# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Accounts on the box: guests (no
login), users (an account), and admins (an account with the admin role, or the box's own basic-auth
`admin`, which the web server checks and the hub never sees).

What the admin sets (/admin → Accounts):
  signup   off (no accounts: the default; any made before can't log in, and their sessions
           don't count, until it is on again) | open (anyone makes one, usable at once) |
           apply (anyone asks; an admin accepts) | assigned (an admin makes them, each with a
           one-time code to set its password)
  http     permitted | warning (the default: the pages say a password can be read off the air
           without HTTPS) | prevented (no password is taken over plain HTTP)

Stored in $HUB_STATE_DIR/accounts.json (600, the hub's):
  accounts  {lower-case name: {name, hash, state (asked | user | disabled), role (user | admin),
            created, seen, by}}; hash is scrypt (n 2^14, r 8, p 1, a 16-byte salt), or "" until
            a one-time code sets it
  sessions  {sha256 of the cookie: {name, expires}}: the cookie itself is never stored
  codes     {sha256 of the code: {name, expires, why}}: one-time codes to set a password
Logins are limited: 10 failures in a quarter of an hour, per name and per address, then a wait.

Stdlib only.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from irate_box.hub.refused import Refused
from irate_box.msg import M

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "accounts.json"
SIGNUP = ("off", "open", "apply", "assigned")
HTTP = ("permitted", "warning", "prevented")
DEFAULTS = {"signup": "off", "http": "warning"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
RESERVED = {"admin", "root", "hub", "guest", "everyone"}
MIN_PASSWORD, MAX_PASSWORD = 8, 128
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SESSION_DAYS = 30
CODE_HOURS = 24
FAILS, FAIL_WINDOW = 10, 15 * 60
# Wrong passwords earn a short wait, never a long lockout: on an open hotspot a device's address can be copied and
# a new one had at will, so a lockout could be turned on the person it protects. Past a preset's free tries the
# wait doubles with each failure, to its caps: one for a device (and for a name from that device, counted over
# FAIL_WINDOW), and a longer one for a name tried from devices it does not know (counted over NAME_WINDOW: new
# devices cost nothing to have, so they share one count). A device the account knows (KNOWN_BY: by its device
# cookie, its address, either or both) is not held back by that name's wait.
NAME_WINDOW = 24 * 3600
PRESETS = {
    # lenient and siege are adaptive's steps beyond the three the owner chooses from.
    "lenient": {"free": 15, "device_max": 15, "name_free": 15, "name_max": 300},
    "relaxed": {"free": 10, "device_max": 30, "name_free": 10, "name_max": 600},
    "standard": {"free": 5, "device_max": 60, "name_free": 5, "name_max": 1800},
    "strict": {"free": 3, "device_max": 300, "name_free": 3, "name_max": 3600},
    "siege": {"free": 2, "device_max": 600, "name_free": 2, "name_max": 7200},
}
LEVELS = ("relaxed", "standard", "strict")  # the owner's choices
LADDER = ("lenient", "relaxed", "standard", "strict", "siege")
KNOWN_BY = ("cookie", "device", "either", "both")
KNOWN_DAYS, KNOWN_MAX = 90, 10
# Adaptive, one step either side of the owner's level, for ADAPT_HOURS. Tighter for what is clearly under attack:
# an account tried NAME_ATTACK times by unknown devices in an hour (spraying), or a device that failed DEVICE_ATTACK
# different names in an hour (stuffing). Looser for the whole box when people keep needing their free tries: at
# least RELAX_AFTER sign-ins in a day that came right only after nearly all of them, and nothing under attack.
NAME_ATTACK, DEVICE_ATTACK, RELAX_AFTER, ADAPT_HOURS = 20, 5, 3, 24
COMMON_WEIGHT = 3  # a wrong password that is one of the commonest counts as this many failures
COMMON_FILE = Path(__file__).with_name("common-passwords.txt")
# The hub's choices (server.py sets it from its settings): level, adaptive, common, known_by.
SIGNIN = None
SIGNIN_DEFAULTS = {"level": "standard", "adaptive": False, "common": True, "known_by": "cookie"}
SIGNUPS_PER_HOUR = 5  # per address: a box at an event shouldn't fill with one stranger's accounts
MAX_ASKED = 100  # applications waiting at once
MAX_ACCOUNTS = 1000

# Refusals said in more than one place.
SIGN_IN_FIRST = M("accounts.sign-in-first", "Sign in first.")
NAME_TAKEN = M("accounts.name.taken", "Username unavailable.")
NOT_FOUND = M("accounts.not-found", "Account not found.")
NO_USERS = M("accounts.signin.off", "Registration closed. This box has no user accounts.")


def _limit_reached():
    return M(
        "accounts.limit",
        {
            "one": "Account limit reached. This box keeps at most {n} account.",
            "other": "Account limit reached. This box keeps at most {n} accounts.",
        },
        n=MAX_ACCOUNTS,
    )


_lock = threading.Lock()
_fails = {}  # ("name" | "addr" | "pair", key) -> [times]; addr is the device (server.py who), else the address
_signups = {}  # addr -> [times]


class AccountError(Refused):
    """Refused, with a reason fit to show the person: a message (msg.M) with a stable ID, so the page can say it in
    the reader's language and a test can read which refusal it was. Its English is the exception's text."""


class Wait(AccountError):
    """Too many failures: try again later."""


# --- the file ----------------------------------------------------------------------------------


def _load():
    try:
        data = json.loads(FILE.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("settings", {})
    for key in ("accounts", "sessions", "codes"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def _save(data):
    STATE.mkdir(parents=True, exist_ok=True)
    now = time.time()
    data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("expires", 0) > now}
    data["codes"] = {k: v for k, v in data["codes"].items() if v.get("expires", 0) > now}
    tmp = FILE.with_name(FILE.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, FILE)


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


# --- passwords ---------------------------------------------------------------------------------


def hash_password(password):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password, stored):
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got.hex(), h)


# A check that costs what a real one does, for a name that doesn't exist: no timing tells which do.
_DUMMY = hash_password(secrets.token_hex(8))


def _valid_password(pw):
    if not isinstance(pw, str) or not MIN_PASSWORD <= len(pw) <= MAX_PASSWORD or "\n" in pw:
        raise AccountError(
            M(
                "accounts.password.length",
                "Enter a password of {least} to {most} characters.",
                least=MIN_PASSWORD,
                most=MAX_PASSWORD,
            )
        )


def _valid_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise AccountError(
            M("accounts.name.invalid", "Invalid username. Use 3 to 32 letters, digits, dots, dashes or underscores.")
        )
    if name.lower() in RESERVED:
        raise AccountError(
            M("accounts.name.reserved", "Username reserved. The box keeps {name} for its own use.", name=name)
        )
    return name.lower()


# --- the limits --------------------------------------------------------------------------------


def _recent(times, window, now):
    return [t for t in times if now - t < window]


def _signin():
    got = SIGNIN() if SIGNIN else {}
    return {k: got.get(k, v) for k, v in SIGNIN_DEFAULTS.items()}


_tight = {}  # ("name" | "addr", key) -> until: a step tighter, by the adaptive choice
_tried = {}  # device -> {name: last failure}: how many names one device has failed lately
_honest = []  # when sign-ins came right only after nearly all their free tries
_relaxed = [0.0]  # until when the whole box is a step looser


def _level(key, now):
    """The preset's name for this key now: the owner's, or a step either side of it (adaptive)."""
    st = _signin()
    i = LADDER.index(st["level"] if st["level"] in LEVELS else "standard")
    if st["adaptive"]:
        if key is not None and _tight.get(key, 0) > now:
            i += 1
        elif _relaxed[0] > now:
            i -= 1
    return LADDER[i]


def _preset(key, now):
    return PRESETS[_level(key, now)]


def _came_right(name, addr, now):
    """A sign-in that worked: if it needed nearly all its free tries, a sign the box is too strict for its people."""
    st = _signin()
    n = (name or "").lower()
    p = _preset(("addr", addr), now)
    if len(_recent(_fails.get(("pair", f"{n}\0{addr or ''}"), []), FAIL_WINDOW, now)) < max(1, p["free"] - 1):
        return
    _honest[:] = [t for t in _honest if now - t < 86400] + [now]
    if st["adaptive"] and len(_honest) >= RELAX_AFTER and not any(u > now for u in _tight.values()):
        _relaxed[0] = now + ADAPT_HOURS * 3600


_common = []


def common(password):
    """Whether a password is one of the commonest (common-passwords.txt: SecLists' 10,000, MIT)."""
    if not _common:
        try:
            _common.append(frozenset(COMMON_FILE.read_text().split()))
        except OSError:
            _common.append(frozenset())
    return isinstance(password, str) and password.lower() in _common[0]


def _device_key():
    f = STATE / "device-cookie.key"
    try:
        key = f.read_bytes()
    except OSError:
        key = b""
    if len(key) != 32:
        key = secrets.token_bytes(32)
        STATE.mkdir(parents=True, exist_ok=True)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(key)
    return key


def _mark_sig(acc):
    # Signed over the name and the password hash's salt: a changed or reset password ends every device cookie.
    salt = (acc.get("hash") or "").split("$")
    return hmac.new(
        _device_key(), f"{acc['name'].lower()}\0{salt[4] if len(salt) > 4 else ''}".encode(), "sha256"
    ).hexdigest()[:32]


def device_mark(name):
    """The device cookie's value for a browser just signed in to this account (OWASP's device cookies)."""
    acc = _load()["accounts"].get((name or "").lower())
    return f"{acc['name'].lower()}.{_mark_sig(acc)}" if acc and acc.get("hash") else None


def _mark_ok(name, mark):
    n = (name or "").lower()
    if not isinstance(mark, str) or not mark.startswith(n + "."):
        return False
    acc = _load()["accounts"].get(n)
    return bool(acc and acc.get("hash")) and hmac.compare_digest(mark[len(n) + 1 :], _mark_sig(acc))


def _known(name, addr, mark, now):
    """Whether the account knows this browser or device, as KNOWN_BY says: by its device cookie (mark), by its
    address (addr: the hardware address on the hotspot, else the address, signed in from in KNOWN_DAYS), either,
    or both."""
    acc = _load()["accounts"].get((name or "").lower())
    seen = (acc or {}).get("devices") or {}
    by_device = bool(addr) and isinstance(seen, dict) and now - seen.get(addr, 0) < KNOWN_DAYS * 86400
    by_cookie = _mark_ok(name, mark)
    how = _signin()["known_by"]
    return {"cookie": by_cookie, "device": by_device, "either": by_cookie or by_device}.get(
        how, by_cookie and by_device
    )


def _wait_left(key, free, cap, window, now):
    times = _recent(_fails.get(key, []), window, now)
    if len(times) < free:
        return 0
    return max(0, min(cap, 2 ** (len(times) - free)) - (now - times[-1]))


def _limited(name, addr, now, known=False):
    """Wait (refused for some seconds) while this device, this name from this device, or (from a device the account
    does not know) this name has failed too often lately, as the preset says."""
    n = (name or "").lower()
    left = 0
    if addr:  # no address: a call from the box itself
        p = _preset(("addr", addr), now)
        left = _wait_left(("addr", addr), p["free"], p["device_max"], FAIL_WINDOW, now)
    if n:
        p = _preset(("addr", addr), now)
        left = max(left, _wait_left(("pair", f"{n}\0{addr or ''}"), p["free"], p["device_max"], FAIL_WINDOW, now))
        if not known:
            p = _preset(("name", n), now)
            left = max(left, _wait_left(("name", n), p["name_free"], p["name_max"], NAME_WINDOW, now))
    if left > 0:
        secs = int(left) + 1
        raise Wait(
            M(
                "accounts.wait-seconds",
                {"one": "Wait {n} second before you try again.", "other": "Wait {n} seconds before you try again."},
                n=secs,
            )
        )


def _failed(name, addr, now, known=False, password=None):
    st = _signin()
    weight = COMMON_WEIGHT if st["common"] and common(password) else 1
    n = (name or "").lower()
    keys = [("addr", addr, FAIL_WINDOW)] if addr else []
    if n:
        keys.append(("pair", f"{n}\0{addr or ''}", FAIL_WINDOW))
        if not known:
            keys.append(("name", n, NAME_WINDOW))  # the name's own count: failures from devices it does not know
    for kind, key, window in keys:
        _fails[(kind, key)] = _recent(_fails.get((kind, key), []), window, now) + [now] * weight
    if not st["adaptive"]:
        return
    if n and not known and len(_recent(_fails.get(("name", n), []), 3600, now)) >= NAME_ATTACK:
        _tight[("name", n)] = now + ADAPT_HOURS * 3600
        _relaxed[0] = 0.0  # under attack: no looser for anyone
    if addr and n:
        names = {k: t for k, t in _tried.get(addr, {}).items() if now - t < 3600}
        names[n] = now
        _tried[addr] = names
        if len(names) >= DEVICE_ATTACK:
            _tight[("addr", addr)] = now + ADAPT_HOURS * 3600
            _relaxed[0] = 0.0


def adaptive_now(now=None):
    """What the adaptive choice is doing now: the accounts held a step tighter, by name; how many devices; and
    whether the whole box is a step looser (until when)."""
    now = now or time.time()
    live = [k for k, until in _tight.items() if until > now]
    return {
        "tight_names": sorted(k[1] for k in live if k[0] == "name"),
        "tight_devices": sum(1 for k in live if k[0] == "addr"),
        "relaxed_until": _relaxed[0] if _relaxed[0] > now else None,
    }


def preset_numbers(level, guess_seconds=1.0):
    """What a preset buys, worked out by playing it: the most guesses a day at one account by someone with any number
    of new devices (a guess every guess_seconds, the next the moment its wait ends), and the longest a known
    device waits after its own wrong passwords."""
    p = PRESETS[level]
    times, t, n = [], 0.0, 0
    while t < 86400:
        recent = [x for x in times if t - x < NAME_WINDOW]
        left = (
            max(0, min(p["name_max"], 2 ** (len(recent) - p["name_free"])) - (t - recent[-1]))
            if len(recent) >= p["name_free"]
            else 0
        )
        if left > 0:
            t += left
            continue
        times.append(t)
        n += 1
        t += guess_seconds
    return {"guesses_a_day": n, "known_wait": p["device_max"], "free": p["free"]}


def _remember_device(acc, addr, now):
    """The device just signed in to acc is known to it (the newest KNOWN_MAX within KNOWN_DAYS kept)."""
    if not addr:
        return
    seen = {k: v for k, v in (acc.get("devices") or {}).items() if now - v < KNOWN_DAYS * 86400}
    seen[addr] = round(now)
    acc["devices"] = dict(sorted(seen.items(), key=lambda kv: kv[1])[-KNOWN_MAX:])


# --- what the pages and the admin see ----------------------------------------------------------


def settings():
    s = dict(DEFAULTS)
    stored = _load()["settings"]
    if stored.get("signup") in SIGNUP:
        s["signup"] = stored["signup"]
    if stored.get("http") in HTTP:
        s["http"] = stored["http"]
    return s


def _admins(data):
    return [
        k
        for k, a in data["accounts"].items()
        if a.get("role") == "admin" and a.get("state") == "user" and a.get("hash")
    ]


def set_settings(signup=None, http=None, keep_admin=False):
    """keep_admin: the box's own admin login is off, so the admin accounts are the only way into
    /admin: sign-up may not go off (no account would log in)."""
    if keep_admin and signup == "off":
        raise AccountError(
            M(
                "accounts.settings.keep-admin",
                "The box's own administrator sign-in is off. Switch it on in Accounts first. "
                "Otherwise no one can open /admin.",
            )
        )
    with _lock:
        data = _load()
        if signup is not None:
            if signup not in SIGNUP:
                raise AccountError(
                    M("accounts.settings.signup", "Sign-up must be one of: {values}.", values=", ".join(SIGNUP))
                )
            data["settings"]["signup"] = signup
        if http is not None:
            if http not in HTTP:
                raise AccountError(
                    M("accounts.settings.http", "HTTP must be one of: {values}.", values=", ".join(HTTP))
                )
            data["settings"]["http"] = http
        _save(data)
    return settings()


def _public(acc):
    return {k: acc.get(k) for k in ("name", "state", "role", "created", "seen", "by", "https_login")} | {
        "password_set": bool(acc.get("hash")),
        # The admin sees each person's choice to be shown online, and can't change it.
        "shown_online": bool((acc.get("prefs") or {}).get("show_online")),
    }


# --- a person's own settings: online visibility, locking, who sees their posts per app, colour.
# Theirs alone, through their own session; the safe choice is each default: not shown by name, no
# lock, no email.
PREFS = {
    "show_online": False,
    "hue": None,
    "lock_default": False,
    "email": "",
    # Who sees what they post, per app: everyone here, signed-in people, or only them.
    # Never wider than the app itself (its access); Notes, SilverBullet's one shared notebook,
    # has no posts of anyone's own to hide.
    "posts": {"shoutbox": "everyone", "board": "everyone", "saves": "everyone", "drop": "everyone"},
}
POST_SEEN = ("everyone", "users", "me")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")


def prefs(name):
    """One account's settings, with the defaults for any not chosen."""
    acc = _load()["accounts"].get(str(name or "").lower())
    out = dict(PREFS, **{k: v for k, v in ((acc or {}).get("prefs") or {}).items() if k in PREFS})
    out["posts"] = dict(
        PREFS["posts"], **{k: v for k, v in (out.get("posts") or {}).items() if k in PREFS["posts"] and v in POST_SEEN}
    )
    return out


def set_prefs(token, changes):
    """Change the settings of the account this session is: only those, only as each allows."""
    me = session(token)
    if not me:
        raise AccountError(SIGN_IN_FIRST)
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(PREFS):
        raise AccountError(M("accounts.prefs.keys", "The settings must be some of: {keys}.", keys=", ".join(PREFS)))
    for k in ("show_online", "lock_default"):
        if k in changes and type(changes[k]) is not bool:
            raise AccountError(M("accounts.prefs.bool", "{key} must be true or false.", key=k))
    if (
        "hue" in changes
        and changes["hue"] is not None
        and not (type(changes["hue"]) is int and 0 <= changes["hue"] < 360)
    ):
        raise AccountError(M("accounts.prefs.hue", "The hue must be from 0 to 359."))
    if "posts" in changes and not (
        isinstance(changes["posts"], dict)
        and changes["posts"]
        and all(k in PREFS["posts"] and v in POST_SEEN for k, v in changes["posts"].items())
    ):
        raise AccountError(
            M(
                "accounts.prefs.posts",
                "Each entry of posts must name an app ({apps}) and a value ({values}).",
                apps=", ".join(PREFS["posts"]),
                values=", ".join(POST_SEEN),
            )
        )
    if "email" in changes and not (
        changes["email"] == "" or (isinstance(changes["email"], str) and EMAIL_RE.match(changes["email"]))
    ):
        raise AccountError(M("accounts.prefs.email", "The email must be an address, or empty."))
    with _lock:
        data = _load()
        acc = data["accounts"][me["name"].lower()]
        merged = dict(acc.get("prefs") or {}, **changes)
        if "posts" in changes:
            merged["posts"] = dict((acc.get("prefs") or {}).get("posts") or {}, **changes["posts"])
        acc["prefs"] = merged
        _save(data)
    return prefs(me["name"])


def exists(name):
    """Whether an account has this name (any case): a guest may not post under it."""
    return isinstance(name, str) and name.strip().lower() in _load()["accounts"]


def listing():
    data = _load()
    return sorted(
        (_public(a) for a in data["accounts"].values()), key=lambda a: (a["state"] != "asked", a["name"].lower())
    )


def counts():
    accs = _load()["accounts"].values()
    return {s: sum(1 for a in accs if a.get("state") == s) for s in ("asked", "user", "disabled")} | {
        "admins": sum(1 for a in accs if a.get("role") == "admin" and a.get("state") == "user")
    }


# --- a person's own actions --------------------------------------------------------------------


def signup(name, password, addr=""):
    """A new account: usable at once (open) or waiting for an admin (apply). Returns its state."""
    level = settings()["signup"]
    if level == "assigned":
        raise AccountError(M("accounts.signup.assigned", "Registration closed. An administrator creates the accounts."))
    if level not in ("open", "apply"):
        raise AccountError(M("accounts.signup.closed", "Registration closed."))
    key = _valid_name(name)
    _valid_password(password)
    now = time.time()
    with _lock:
        recent = _recent(_signups.get(addr, []), 3600, now)
        if len(recent) >= SIGNUPS_PER_HOUR:
            raise Wait(
                M(
                    "accounts.signup.wait",
                    {
                        "one": "Too many sign-ups from this address. Wait {n} hour before you try again.",
                        "other": "Too many sign-ups from this address. Wait {n} hours before you try again.",
                    },
                    n=1,
                )
            )
        data = _load()
        if key in data["accounts"]:
            raise AccountError(NAME_TAKEN)
        if len(data["accounts"]) >= MAX_ACCOUNTS:
            raise AccountError(_limit_reached())
        if level == "apply" and sum(1 for a in data["accounts"].values() if a.get("state") == "asked") >= MAX_ASKED:
            raise AccountError(
                M(
                    "accounts.signup.queue-full",
                    {
                        "one": "Too many accounts wait for approval. The limit is {n}. Try again later.",
                        "other": "Too many accounts wait for approval. The limit is {n}. Try again later.",
                    },
                    n=MAX_ASKED,
                )
            )
        state = "user" if level == "open" else "asked"
        data["accounts"][key] = {
            "name": name,
            "hash": hash_password(password),
            "state": state,
            "role": "user",
            "created": round(now),
            "seen": None,
            "by": "signed up",
        }
        _signups[addr] = recent + [now]
        _save(data)
    return state


def login(name, password, addr="", https=False, device=None, mark=None):
    """A session for a user whose password is right: (cookie value, account). Over HTTPS it is
    recorded (https_login): an admin account that has can stand in for the box's own login. device: the
    hardware address of the hotspot device signing in, kept with the session (session_device). mark: the browser's
    device cookie, if it sent one (whether the account knows it: _known)."""
    now = time.time()
    known = _known(name, addr, mark, now)
    with _lock:
        _limited(name, addr, now, known)
    acc = _load()["accounts"].get((name or "").lower()) if isinstance(name, str) else None
    ok = check_password(
        password if isinstance(password, str) else "", acc["hash"] if acc and acc.get("hash") else _DUMMY
    )
    # With sign-up off the box has no users' accounts, but its admins still sign in here: the one login.
    # Judged after the password check, so the answer takes as long either way.
    if settings()["signup"] == "off" and not (acc and acc.get("role") == "admin"):
        with _lock:
            _failed(name, addr, now, known, password)
        raise AccountError(NO_USERS)
    with _lock:
        if not ok or not acc:
            _failed(name, addr, now, known, password)
            raise AccountError(
                M("accounts.signin.failed", "Sign-in failed. The username and password do not match an account.")
            )
        if acc.get("state") == "asked":
            raise AccountError(
                M("accounts.signin.pending", "Account approval pending. An administrator must accept this account.")
            )
        if acc.get("state") != "user":
            raise AccountError(M("accounts.signin.disabled", "Account disabled. An administrator can enable it again."))
        token = secrets.token_urlsafe(32)
        data = _load()
        data["sessions"][_digest(token)] = {
            "name": acc["name"].lower(),
            "expires": round(now + SESSION_DAYS * 86400),
            **({"device": device} if device else {}),
        }
        data["accounts"][acc["name"].lower()]["seen"] = round(now)
        _remember_device(data["accounts"][acc["name"].lower()], addr, now)
        _came_right(name, addr, now)
        if https:
            data["accounts"][acc["name"].lower()]["https_login"] = round(now)
        _save(data)
    return token, _public(acc)


_basic_ok = {}  # (name, keyed digest of the password) -> when it was last right: a push is several requests
# The digest's key, new each start: what the cache holds says nothing about a password without it.
_BASIC_KEY = secrets.token_bytes(32)


def _forget_basic(name):
    """A changed or reset password is not right any more, this minute included."""
    for k in [k for k in _basic_ok if k[0] == (name or "").lower()]:
        _basic_ok.pop(k, None)


def check_basic(header, addr=""):
    """An HTTP Basic Authorization header that is an account's name and password (git over HTTP:
    a client sends no cookie): {name, role}, or None. Limited as logins are; a right one is
    remembered for a minute, as git asks several times a push and a check takes half a second on
    a small board."""
    import base64

    if not isinstance(header, str) or not header.startswith("Basic ") or settings()["signup"] == "off":
        return None
    try:
        name, _, password = base64.b64decode(header[6:].strip(), validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    if not name or name.lower() in RESERVED:
        return None  # "admin" is the box's own login, for the web server to check
    now = time.time()
    key = (name.lower(), hmac.new(_BASIC_KEY, password.encode(), "sha256").hexdigest())
    acc = _load()["accounts"].get(name.lower())
    eligible = bool(acc and acc.get("state") == "user" and acc.get("hash"))
    if not (eligible and now - _basic_ok.get(key, 0) <= 60):
        # One password check for every name, a real hash or the dummy, and one limit for every name: no timing
        # or lockout tells an account that exists from one that does not. A rejection is never remembered.
        with _lock:
            try:
                _limited(name, addr, now, _known(name, addr, None, now))
            except Wait:
                return None
        if not check_password(password, acc["hash"] if eligible else _DUMMY) or not eligible:
            with _lock:
                _failed(name, addr, now, _known(name, addr, None, now), password)
            return None
        _basic_ok[key] = now
    return {"name": acc["name"], "role": acc.get("role", "user")}


def session(token):
    """The account a session cookie belongs to, or None: only while the session is current and
    the account a user (not waiting, not switched off). Renewed once a day while in use."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    now = time.time()
    data = _load()
    s = data["sessions"].get(_digest(token))
    if not s or s.get("expires", 0) <= now:
        return None
    acc = data["accounts"].get(s.get("name"))
    if not acc or acc.get("state") != "user":
        return None
    # Sign-up off: no users' accounts, but the admins' still (one login, as login() above).
    if data["settings"].get("signup", DEFAULTS["signup"]) == "off" and acc.get("role") != "admin":
        return None
    if s["expires"] - now < (SESSION_DAYS - 1) * 86400:
        with _lock:
            data = _load()
            if _digest(token) in data["sessions"]:
                data["sessions"][_digest(token)]["expires"] = round(now + SESSION_DAYS * 86400)
                data["accounts"][s["name"]]["seen"] = round(now)
                _save(data)
    return _public(acc)


def session_device(token):
    """The hardware address of the hotspot device a session was made on, or None (made elsewhere, or before
    sessions kept one)."""
    if not isinstance(token, str) or not 20 <= len(token) <= 100:
        return None
    s = _load()["sessions"].get(_digest(token))
    return s.get("device") if isinstance(s, dict) and isinstance(s.get("device"), str) else None


def has_session(name):
    """Whether this account has a current session anywhere (its notebook runs only while it does)."""
    k, now = str(name or "").lower(), time.time()
    return any(s.get("name") == k and s.get("expires", 0) > now for s in _load()["sessions"].values())


def logout(token):
    with _lock:
        data = _load()
        if data["sessions"].pop(_digest(token or ""), None) is not None:
            _save(data)


ADMIN_OVER_HTTP = M(
    "accounts.password.https-only",
    "An administrator password changes only over HTTPS, or at the box's console over SSH.",
)


def change_password(token, old, new, addr="", https=True):
    acc = session(token)
    if not acc:
        raise AccountError(SIGN_IN_FIRST)
    if acc.get("role") == "admin" and not https:
        raise AccountError(ADMIN_OVER_HTTP)
    _valid_password(new)
    now = time.time()
    with _lock:
        _limited(acc["name"], addr, now, True)  # signed in: their own account, only the device's waits
        data = _load()
        stored = data["accounts"][acc["name"].lower()]
        if not check_password(old if isinstance(old, str) else "", stored.get("hash") or _DUMMY):
            _failed(acc["name"], addr, now, True)
            raise AccountError(M("accounts.password.wrong", "The current password is not correct."))
        stored["hash"] = hash_password(new)
        _forget_basic(acc["name"])
        # Every other session of the account ends: a password changed because it leaked.
        keep = _digest(token)
        data["sessions"] = {
            k: v for k, v in data["sessions"].items() if v.get("name") != acc["name"].lower() or k == keep
        }
        _save(data)


def use_code(code, password, addr="", https=True):
    """Set an account's password with a one-time code (an admin's new account, or a reset); an admin
    account's only over HTTPS (the code is kept for that). Returns the account's name."""
    _valid_password(password)
    now = time.time()
    with _lock:
        _limited("", addr, now)
        data = _load()
        key = _digest(_normal(code).upper()) if isinstance(code, str) else ""
        held = data["codes"].get(key)
        if not https and held and data["accounts"].get(held.get("name"), {}).get("role") == "admin":
            raise AccountError(ADMIN_OVER_HTTP)
        c = data["codes"].pop(key, None)
        if not c or c.get("expires", 0) <= now or c.get("name") not in data["accounts"]:
            _failed("", addr, now)
            # One answer for every way a code fails: it does not tell which codes or accounts exist.
            raise AccountError(M("accounts.code.invalid", "This code is invalid, used or expired."))
        acc = data["accounts"][c["name"]]
        # Sign-up off: an admin's code still sets an admin account's password (one login); a user's waits.
        if data["settings"].get("signup", DEFAULTS["signup"]) == "off" and acc.get("role") != "admin":
            raise AccountError(NO_USERS)
        acc["hash"] = hash_password(password)
        _forget_basic(c["name"])
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("name") != c["name"]}
        _save(data)
    return acc["name"]


# --- the admin's actions -----------------------------------------------------------------------


def _normal(code):
    return code.replace("-", "").replace(" ", "").strip()


def _code(data, key, why):
    # 16 characters of an unambiguous alphabet, in fours: easy to read out or type from a phone.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    data["codes"] = {k: v for k, v in data["codes"].items() if v.get("name") != key}
    data["codes"][_digest(raw)] = {"name": key, "expires": round(time.time() + CODE_HOURS * 3600), "why": why}
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


def make(name, role="user"):
    """An admin's new account, with a one-time code (a day) for its person to set the password."""
    key = _valid_name(name)
    if role not in ("user", "admin"):
        raise AccountError(M("accounts.make.role", "The role must be one of: {values}.", values="user, admin"))
    with _lock:
        data = _load()
        if key in data["accounts"]:
            raise AccountError(NAME_TAKEN)
        if len(data["accounts"]) >= MAX_ACCOUNTS:
            raise AccountError(_limit_reached())
        data["accounts"][key] = {
            "name": name,
            "hash": "",
            "state": "user",
            "role": role,
            "created": round(time.time()),
            "seen": None,
            "by": "admin",
        }
        code = _code(data, key, "new")
        _save(data)
    return code


def admin_ready(data=None):
    """Whether an admin account can sign in (switched on, a password set): then /admin asks for no
    login of its own and sends anyone else to sign in (access.py's gates)."""
    return bool(_admins(data or _load()))


def set_admin(name, password, by="the box's first use"):
    """An admin account with this password: made, or, when the name is an account already, made an
    admin with this password (the console's or the first use's say-so); its sessions end."""
    key = _valid_name(name)
    _valid_password(password)
    with _lock:
        data = _load()
        acc = data["accounts"].get(key) or {"name": name, "created": round(time.time()), "seen": None, "by": by}
        acc.update(hash=hash_password(password), state="user", role="admin")
        data["accounts"][key] = acc
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("name") != key}
        _save(data)


def claim_admin(name, password, addr="", device=None):
    """The box's first use (or after reset-password at the console): its owner's admin account, then
    signed in: (cookie value, account)."""
    set_admin(name, password)
    return login(name, password, addr, device=device)


def reset(name):
    """A one-time code to set a new password; the account's sessions end."""
    key = (name or "").lower()
    with _lock:
        data = _load()
        if key not in data["accounts"]:
            raise AccountError(NOT_FOUND)
        code = _code(data, key, "reset")
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("name") != key}
        _save(data)
    return code


def change(name, action, keep_admin=False):
    """accept (an application), disable, enable, delete, admin (the role), user (the role).
    keep_admin: the box's own admin login is off; the last admin account stays one."""
    key = (name or "").lower()
    with _lock:
        data = _load()
        acc = data["accounts"].get(key)
        if not acc:
            raise AccountError(NOT_FOUND)
        if keep_admin and action in ("disable", "delete", "user") and _admins(data) == [key]:
            raise AccountError(
                M(
                    "accounts.last-admin",
                    "This is the last administrator account. The box's own administrator sign-in is off. "
                    "Switch it on first. Otherwise no one can open /admin.",
                )
            )
        if action == "accept":
            if acc["state"] != "asked":
                raise AccountError(
                    M("accounts.accept.not-asked", "You can accept only an account that waits for approval.")
                )
            acc["state"], acc["by"] = "user", "accepted by an administrator"
        elif action == "disable":
            acc["state"] = "disabled"
        elif action == "enable":
            acc["state"] = "user"
        elif action in ("admin", "user"):
            acc["role"] = action
        elif action == "delete":
            del data["accounts"][key]
            data["codes"] = {k: v for k, v in data["codes"].items() if v.get("name") != key}
        else:
            raise AccountError(
                M(
                    "accounts.change.action",
                    "The action must be one of: {values}.",
                    values="accept, disable, enable, delete, admin, user",
                )
            )
        if action in ("disable", "delete"):
            data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("name") != key}
        _save(data)


def main(argv):
    """The console's way to an admin account (hub_control set-admin runs this as the hub's user):
    `accounts set-admin NAME`, the password on stdin."""
    import sys

    if len(argv) == 2 and argv[0] == "set-admin":
        try:
            set_admin(argv[1], sys.stdin.readline().rstrip("\n"), by="the console")
        except AccountError as exc:
            sys.exit(f"accounts: {exc}")
        print(f"admin account {argv[1]}: password set")
        return
    sys.exit("usage: accounts set-admin NAME   (the password on stdin)")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
