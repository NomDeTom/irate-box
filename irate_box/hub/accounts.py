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

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "accounts.json"
SIGNUP = ("off", "open", "apply", "assigned")
HTTP = ("permitted", "warning", "prevented")
DEFAULTS = {"signup": "off", "http": "warning"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
RESERVED = {"admin", "root", "hub", "guest", "everyone"}
MIN_PASSWORD, MAX_PASSWORD = 8, 128
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
SESSION_DAYS = 30
CODE_HOURS = 24
FAILS, FAIL_WINDOW = 10, 15 * 60
SIGNUPS_PER_HOUR = 5        # per address: a box at an event shouldn't fill with one stranger's accounts
MAX_ASKED = 100             # applications waiting at once
MAX_ACCOUNTS = 1000

_lock = threading.Lock()
_fails = {}                 # ("name" | "addr", key) -> [times]
_signups = {}               # addr -> [times]


class AccountError(ValueError):
    """Refused, with a reason fit to show the person."""


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
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got.hex(), h)


# A check that costs what a real one does, for a name that doesn't exist: no timing tells which do.
_DUMMY = hash_password(secrets.token_hex(8))


def _valid_password(pw):
    if not isinstance(pw, str) or not MIN_PASSWORD <= len(pw) <= MAX_PASSWORD or "\n" in pw:
        raise AccountError(f"a password is {MIN_PASSWORD}–{MAX_PASSWORD} characters")


def _valid_name(name):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise AccountError("a name is 3–32 letters, digits, dots, dashes or underscores")
    if name.lower() in RESERVED:
        raise AccountError(f"{name} is kept for the box")
    return name.lower()


# --- the limits --------------------------------------------------------------------------------

def _recent(times, window, now):
    return [t for t in times if now - t < window]


def _limited(name, addr, now):
    for key in (("name", (name or "").lower()), ("addr", addr or "")):
        if len(_recent(_fails.get(key, []), FAIL_WINDOW, now)) >= FAILS:
            raise Wait("too many tries: wait a quarter of an hour")


def _failed(name, addr, now):
    for key in (("name", (name or "").lower()), ("addr", addr or "")):
        _fails[key] = _recent(_fails.get(key, []), FAIL_WINDOW, now) + [now]


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
    return [k for k, a in data["accounts"].items() if a.get("role") == "admin" and a.get("state") == "user" and a.get("hash")]


def set_settings(signup=None, http=None, keep_admin=False):
    """keep_admin: the box's own admin login is off, so the admin accounts are the only way into
    /admin: sign-up may not go off (no account would log in)."""
    if keep_admin and signup == "off":
        raise AccountError("the box's own admin login is off: switch it on first (Accounts), or no one could open /admin")
    with _lock:
        data = _load()
        if signup is not None:
            if signup not in SIGNUP:
                raise AccountError(f"signup is one of {', '.join(SIGNUP)}")
            data["settings"]["signup"] = signup
        if http is not None:
            if http not in HTTP:
                raise AccountError(f"http is one of {', '.join(HTTP)}")
            data["settings"]["http"] = http
        _save(data)
    return settings()


def _public(acc):
    return {k: acc.get(k) for k in ("name", "state", "role", "created", "seen", "by", "https_login")} | {
        "password_set": bool(acc.get("hash")),
        # The admin sees each person's choice to be shown online, and can't change it.
        "shown_online": bool((acc.get("prefs") or {}).get("show_online"))}


# --- a person's own settings: online visibility, locking, who sees their posts per app, colour.
# Theirs alone, through their own session; the safe choice is each default: not shown by name, no
# lock, no email.
PREFS = {"show_online": False, "hue": None, "lock_default": False, "email": "",
         # Who sees what they post, per app: everyone here, signed-in people, or only them.
         # Never wider than the app itself (its access); Notes, SilverBullet's one shared notebook,
         # has no posts of anyone's own to hide.
         "posts": {"shoutbox": "everyone", "board": "everyone", "saves": "everyone", "drop": "everyone"}}
POST_SEEN = ("everyone", "users", "me")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}$")


def prefs(name):
    """One account's settings, with the defaults for any not chosen."""
    acc = _load()["accounts"].get(str(name or "").lower())
    out = dict(PREFS, **{k: v for k, v in ((acc or {}).get("prefs") or {}).items() if k in PREFS})
    out["posts"] = dict(PREFS["posts"], **{k: v for k, v in (out.get("posts") or {}).items() if k in PREFS["posts"] and v in POST_SEEN})
    return out


def set_prefs(token, changes):
    """Change the settings of the account this session is: only those, only as each allows."""
    me = session(token)
    if not me:
        raise AccountError("log in first")
    if not isinstance(changes, dict) or not changes or not set(changes) <= set(PREFS):
        raise AccountError("settings: show_online, hue, lock_default, email")
    for k in ("show_online", "lock_default"):
        if k in changes and type(changes[k]) is not bool:
            raise AccountError(f"{k}: true or false")
    if "hue" in changes and changes["hue"] is not None and not (type(changes["hue"]) is int and 0 <= changes["hue"] < 360):
        raise AccountError("hue: 0 to 359")
    if "posts" in changes and not (isinstance(changes["posts"], dict) and changes["posts"]
                                   and all(k in PREFS["posts"] and v in POST_SEEN for k, v in changes["posts"].items())):
        raise AccountError("posts: per app (shoutbox, board, saves, drop), everyone, users or me")
    if "email" in changes and not (changes["email"] == "" or (isinstance(changes["email"], str) and EMAIL_RE.match(changes["email"]))):
        raise AccountError("email: an address, or nothing")
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
    return sorted((_public(a) for a in data["accounts"].values()), key=lambda a: (a["state"] != "asked", a["name"].lower()))


def counts():
    accs = _load()["accounts"].values()
    return {s: sum(1 for a in accs if a.get("state") == s) for s in ("asked", "user", "disabled")} | \
        {"admins": sum(1 for a in accs if a.get("role") == "admin" and a.get("state") == "user")}


# --- a person's own actions --------------------------------------------------------------------

def signup(name, password, addr=""):
    """A new account: usable at once (open) or waiting for an admin (apply). Returns its state."""
    level = settings()["signup"]
    if level not in ("open", "apply"):
        raise AccountError("this box takes no sign-ups" + (": its admin makes the accounts" if level == "assigned" else ""))
    key = _valid_name(name)
    _valid_password(password)
    now = time.time()
    with _lock:
        recent = _recent(_signups.get(addr, []), 3600, now)
        if len(recent) >= SIGNUPS_PER_HOUR:
            raise Wait("too many sign-ups from here: wait an hour")
        data = _load()
        if key in data["accounts"]:
            raise AccountError("that name is taken")
        if len(data["accounts"]) >= MAX_ACCOUNTS:
            raise AccountError("this box has as many accounts as it holds")
        if level == "apply" and sum(1 for a in data["accounts"].values() if a.get("state") == "asked") >= MAX_ASKED:
            raise AccountError("too many waiting for the admin already: try later")
        state = "user" if level == "open" else "asked"
        data["accounts"][key] = {"name": name, "hash": hash_password(password), "state": state, "role": "user",
                                 "created": round(now), "seen": None, "by": "signed up"}
        _signups[addr] = recent + [now]
        _save(data)
    return state


def login(name, password, addr="", https=False):
    """A session for a user whose password is right: (cookie value, account). Over HTTPS it is
    recorded (https_login): an admin account that has can stand in for the box's own login."""
    now = time.time()
    with _lock:
        _limited(name, addr, now)
    acc = _load()["accounts"].get((name or "").lower()) if isinstance(name, str) else None
    ok = check_password(password if isinstance(password, str) else "", acc["hash"] if acc and acc.get("hash") else _DUMMY)
    # With sign-up off the box has no users' accounts, but its admins still sign in here: the one login.
    # Judged after the password check, so the answer takes as long either way.
    if settings()["signup"] == "off" and not (acc and acc.get("role") == "admin"):
        with _lock:
            _failed(name, addr, now)
        raise AccountError("this box has no accounts")
    with _lock:
        if not ok or not acc:
            _failed(name, addr, now)
            raise AccountError("that name and password don't match an account here")
        if acc.get("state") == "asked":
            raise AccountError("this account waits for the admin to accept it")
        if acc.get("state") != "user":
            raise AccountError("this account is switched off")
        token = secrets.token_urlsafe(32)
        data = _load()
        data["sessions"][_digest(token)] = {"name": acc["name"].lower(), "expires": round(now + SESSION_DAYS * 86400)}
        data["accounts"][acc["name"].lower()]["seen"] = round(now)
        if https:
            data["accounts"][acc["name"].lower()]["https_login"] = round(now)
        _save(data)
    return token, _public(acc)


_basic_ok = {}   # (name, sha256 of the password) -> when it was last right: a push is several requests


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
    key = (name.lower(), hashlib.sha256(password.encode()).hexdigest())
    acc = _load()["accounts"].get(name.lower())
    if not acc or acc.get("state") != "user" or not acc.get("hash"):
        return None
    if now - _basic_ok.get(key, 0) > 60:
        with _lock:
            try:
                _limited(name, addr, now)
            except Wait:
                return None
        if not check_password(password, acc["hash"]):
            with _lock:
                _failed(name, addr, now)
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


def logout(token):
    with _lock:
        data = _load()
        if data["sessions"].pop(_digest(token or ""), None) is not None:
            _save(data)


ADMIN_OVER_HTTP = "an admin's password changes only over HTTPS, or at the box's console over SSH"


def change_password(token, old, new, addr="", https=True):
    acc = session(token)
    if not acc:
        raise AccountError("log in first")
    if acc.get("role") == "admin" and not https:
        raise AccountError(ADMIN_OVER_HTTP)
    _valid_password(new)
    now = time.time()
    with _lock:
        _limited(acc["name"], addr, now)
        data = _load()
        stored = data["accounts"][acc["name"].lower()]
        if not check_password(old if isinstance(old, str) else "", stored.get("hash") or _DUMMY):
            _failed(acc["name"], addr, now)
            raise AccountError("the current password is not right")
        stored["hash"] = hash_password(new)
        _forget_basic(acc["name"])
        # Every other session of the account ends: a password changed because it leaked.
        keep = _digest(token)
        data["sessions"] = {k: v for k, v in data["sessions"].items() if v.get("name") != acc["name"].lower() or k == keep}
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
            raise AccountError("that code is not one this box gave, or it has been used or has expired")
        acc = data["accounts"][c["name"]]
        # Sign-up off: an admin's code still sets an admin account's password (one login); a user's waits.
        if data["settings"].get("signup", DEFAULTS["signup"]) == "off" and acc.get("role") != "admin":
            raise AccountError("this box has no accounts")
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
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def make(name, role="user"):
    """An admin's new account, with a one-time code (a day) for its person to set the password."""
    key = _valid_name(name)
    if role not in ("user", "admin"):
        raise AccountError("role is user or admin")
    with _lock:
        data = _load()
        if key in data["accounts"]:
            raise AccountError("that name is taken")
        if len(data["accounts"]) >= MAX_ACCOUNTS:
            raise AccountError("this box has as many accounts as it holds")
        data["accounts"][key] = {"name": name, "hash": "", "state": "user", "role": role,
                                 "created": round(time.time()), "seen": None, "by": "admin"}
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


def claim_admin(name, password, addr=""):
    """The box's first use (or after reset-password at the console): its owner's admin account, then
    signed in: (cookie value, account)."""
    set_admin(name, password)
    return login(name, password, addr)


def reset(name):
    """A one-time code to set a new password; the account's sessions end."""
    key = (name or "").lower()
    with _lock:
        data = _load()
        if key not in data["accounts"]:
            raise AccountError("no such account")
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
            raise AccountError("no such account")
        if keep_admin and action in ("disable", "delete", "user") and _admins(data) == [key]:
            raise AccountError("the last admin account, while the box's own admin login is off: switch that on first, or no one could open /admin")
        if action == "accept":
            if acc["state"] != "asked":
                raise AccountError("only an application is accepted")
            acc["state"], acc["by"] = "user", "accepted by the admin"
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
            raise AccountError("action is accept, disable, enable, delete, admin or user")
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
