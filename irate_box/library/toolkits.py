# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits, the hub's half: the owner's settings, what the page shows, and
the librarian's part, which keeps each kit's cache current and has each install removed when
its time is up. Root does the work (root/kits.py, through hub_control's kit-* actions); the hub
only asks, naming a shipped kit.

Settings ($HUB_STATE_DIR/library/toolkits.json):
  budget_mb                 the whole cache's limit (500)
  kits.<id>.keep_current    fetched and refreshed on the librarian's schedule (on)
  kits.<id>.remove_after    hours an install stays by default (the kit's own: 24, the build kit
                            never), or null for never
"""
import os
import re
import time
import zlib

from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M
from irate_box.hub import kitdefs

SETTINGS = librarian.LIB_DIR / "toolkits.json"
STATE = librarian.LIB_DIR / "toolkits-state.json"  # when each kit's fetch was last asked for
STATUS = librarian.STATE_DIR / "control" / "kits.json"  # root's: what is cached and installed
# debsecan's data (Debian's security tracker), kept so debsecan works offline from the last copy:
#   debsecan --source file://<FEED>/ --suite <codename>
FEED = librarian.LIB_DIR / "debsecan" / "release" / "1"
FEED_URL = "https://security-tracker.debian.org/tracker/debsecan/release/1/"
OS_RELEASE = "/etc/os-release"
MAX_HOURS = 24 * 365


def definitions():
    return kitdefs.definitions()


def settings():
    raw = librarian._read_json(SETTINGS, {})
    # previous_days: how long an older set, kept for Roll back, stays after a newer set replaced it (None: until the next
    # refresh replaces it in turn).
    out = {"budget_mb": raw.get("budget_mb", 500), "deep_audit_days": raw.get("deep_audit_days", 7),
           "previous_days": raw.get("previous_days"), "kits": {}}
    for kid, k in definitions().items():
        mine = (raw.get("kits") or {}).get(kid, {})
        out["kits"][kid] = {"keep_current": mine.get("keep_current", True),
                            "remove_after": mine.get("remove_after", k.get("remove_after_hours", 24))}
    return out


def _hours_ok(h):
    return h is None or (type(h) is int and 1 <= h <= MAX_HOURS)


def set_settings(changes):
    cfg = settings()
    if "budget_mb" in changes:
        b = changes["budget_mb"]
        if type(b) is not int or not 10 <= b <= 1 << 16:
            raise LibrarianError(M("toolkits.budget.invalid", "Invalid storage limit. Enter a number of MB from 10 to 65536."))
        cfg["budget_mb"] = b
    if "deep_audit_days" in changes:
        d = changes["deep_audit_days"]
        if type(d) is not int or not 0 <= d <= 90:
            raise LibrarianError(M("toolkits.deep-audit.invalid", "Invalid deep audit interval. Enter a number of days from 0 to 90. 0 means never on its own."))
        cfg["deep_audit_days"] = d
    if "previous_days" in changes:
        d = changes["previous_days"]
        if d is not None and (type(d) is not int or not 1 <= d <= 365):
            raise LibrarianError(M("toolkits.previous-days.invalid", "Invalid setting. Enter a number of days from 1 to 365 for older "
                                   "sets, or null to keep them until a refresh brings newer versions."))
        cfg["previous_days"] = d
    for kid, ch in (changes.get("kits") or {}).items():
        if kid not in cfg["kits"] or not isinstance(ch, dict):
            raise LibrarianError(M("toolkits.kit.not-found", "Source not found: toolkit {kit}.", kit=str(kid)))
        if "keep_current" in ch:
            if type(ch["keep_current"]) is not bool:
                raise LibrarianError(M("toolkits.keep-current.invalid", "Invalid setting. keep_current must be true or false."))
            cfg["kits"][kid]["keep_current"] = ch["keep_current"]
        if "remove_after" in ch:
            if not _hours_ok(ch["remove_after"]):
                raise LibrarianError(M("toolkits.remove-after.invalid", "Invalid setting. remove_after must be from 1 to {max} hours, or null for never.",
                                         max=MAX_HOURS))
            cfg["kits"][kid]["remove_after"] = ch["remove_after"]
    librarian._write_json(SETTINGS, cfg)
    return cfg


def status():
    return librarian._read_json(STATUS, {})


def snapshot():
    return {"kits": definitions(), "settings": settings(), "status": status(), "feeds": feeds()}


def suite():
    try:
        for line in open(OS_RELEASE):
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip().strip('"') or None
    except OSError:
        pass
    return None


def feeds():
    """Each data feed a kit keeps, with its age."""
    s = suite()
    f = FEED / s if s else None
    return {"debsecan": {"suite": s, "fetched": f.stat().st_mtime if f and f.is_file() else None,
                         "source": f"file://{FEED}/"}}


def refresh_feed(log=print):
    """debsecan's data for this box's suite (and GENERIC), checked before it replaces the last."""
    s = suite()
    if not s or not re.match(r"^[a-z]{2,20}$", s):
        raise LibrarianError(M("toolkits.suite.unknown", "Download failed. The Debian suite of this box is unknown (/etc/os-release)."))
    FEED.mkdir(parents=True, exist_ok=True)
    for name in (s, "GENERIC"):
        with librarian._open(FEED_URL + name, timeout=120) as resp:
            raw = resp.read(64 << 20)
        if not zlib.decompress(raw)[:10] == b"VERSION 1\n":
            raise LibrarianError(M("toolkits.debsecan.unreadable", "Download failed. The debsecan data for {name} is not in a format that the box can read.",
                                     name=name))
        tmp = FEED / f".{name}.tmp"
        tmp.write_bytes(raw)
        os.replace(tmp, FEED / name)
    return f"debsecan's data for {s}: {(FEED / s).stat().st_size >> 10} KB"


def ensure_git_sources(log=print):
    """The git sources a kit kept current names (the security kit's debian-cis): each a mirror,
    added once if there is none of its upstream; the mirrors' own stage keeps it."""
    from irate_box.library import mirrors
    have = {m["upstream"].rstrip("/").lower() for m in mirrors.load()}
    added = []
    cfg = settings()
    for kid, k in definitions().items():
        if not cfg["kits"][kid]["keep_current"]:
            continue
        for g in k.get("git", []):
            if g["upstream"].rstrip("/").lower() in have:
                continue
            mirrors.add({"name": g["name"], "area": "public", "upstream": g["upstream"], "branches": [g.get("branch", "main")],
                         "groups": [], "history": "shallow", "budget_mb": 64, "submodules": False,
                         # Pinned where the kit pins it: root runs that commit alone (deepaudit.py).
                         **({"follow": "pinned", "pin": g["pin"]} if g.get("pin") else {})})
            have.add(g["upstream"].rstrip("/").lower())
            added.append(g["name"])
    return added


def action(payload):
    """A request from the page: queued for root, its id returned."""
    what = payload.get("action")
    if what == "settings":
        set_settings(payload)
        return None
    if what == "define":
        spec = payload.get("kit")
        if not isinstance(spec, dict):
            raise LibrarianError(M("toolkits.kit.invalid", "Invalid kit. Enter an object with a title and a list of packages."))
        kid = spec.get("id") or re.sub(r"[^a-z0-9]+", "-", str(spec.get("title", "")).lower()).strip("-")[:32]
        if kid in kitdefs.shipped() or kid == "extras":
            kid = f"my-{kid}"[:32]
        pkgs = spec.get("packages")
        if isinstance(pkgs, str):
            pkgs = re.split(r"[\s,]+", pkgs.strip())
        return librarian._queue_root({"action": "kit-define", "kit": {
            "id": kid, "title": str(spec.get("title", "")), "summary": str(spec.get("summary", "")),
            "packages": [p for p in (pkgs or []) if p], "remove_after_hours": spec.get("remove_after_hours", 24)}})
    if what == "drop-all-previous":
        return librarian._queue_root({"action": "kit-drop-all-previous"})
    kid = payload.get("kit")
    if what != "status" and kid not in definitions():
        raise LibrarianError(M("toolkits.kit.unknown", "Unknown kit. Select one of these kits: {kits}.", kits=list(definitions())))
    if what == "fetch":
        return librarian._queue_root({"action": "kit-fetch", "kit": kid, "budget_mb": settings()["budget_mb"]})
    if what in ("install", "keep"):
        hours = payload.get("hours", settings()["kits"][kid]["remove_after"]) if what == "install" else payload.get("hours")
        if not _hours_ok(hours):
            raise LibrarianError(M("toolkits.hours.invalid", "Invalid hours. Enter 1 to {max}, or null for never.", max=MAX_HOURS))
        return librarian._queue_root({"action": f"kit-{what}", "kit": kid, "hours": hours})
    if what in ("remove", "rollback", "drop-previous"):
        return librarian._queue_root({"action": f"kit-{what}", "kit": kid})
    if what == "status":
        return librarian._queue_root({"action": "kit-status"})
    if what == "extra":
        pkgs = payload.get("packages")
        if not isinstance(pkgs, list) or len(pkgs) > 20:
            raise LibrarianError(M("toolkits.extra.invalid", "Invalid package list. Enter up to 20 package names."))
        return librarian._queue_root({"action": "kit-extra", "kit": kid, "packages": [str(p) for p in pkgs]})
    if what == "undefine":
        return librarian._queue_root({"action": "kit-undefine", "kit": kid})
    raise LibrarianError(M("toolkits.action.invalid", "Invalid action. Use settings, fetch, install, keep, remove, rollback, drop-previous, drop-all-previous, status, "
                         "define, undefine or extra."))


def step(policy, now=None, log=print):
    """The librarian's part, once a run: an install whose time is up is removed; and one kit
    kept current whose cache is missing or due is fetched (one a run: a fetch holds the root
    helper for minutes). Returns a line, or None when nothing was due."""
    now = now or time.time()
    st = status()
    out = []
    if any(v.get("remove_at") and v["remove_at"] <= now for v in (st.get("installed") or {}).values()):
        librarian._queue_root({"action": "kit-expire"})
        out.append("removal period elapsed: removing the kits")
    cfg = settings()
    asked = librarian._read_json(STATE, {})
    # Older sets past their days (previous_days), swept: counted from when the newer set replaced them.
    days = cfg.get("previous_days")
    if days and now - asked.get("sweep", 0) >= 3600:
        due = [kid for kid, v in (st.get("kits") or {}).items()
               if v.get("previous") and now - ((v.get("cached") or {}).get("fetched") or now) >= days * 86400]
        if due:
            librarian._queue_root({"action": "kit-drop-old-previous", "days": days})
            asked["sweep"] = now
            librarian._write_json(STATE, asked)
            out.append(f"removing older sets past {days} days: {', '.join(due)}")
    feeding = [kid for kid, k in definitions().items() if cfg["kits"][kid]["keep_current"] and "debsecan" in k.get("feeds", [])]
    if feeding:
        try:
            added = ensure_git_sources(log)
            if added:
                out.append(f"mirroring {', '.join(added)}")
        except (LibrarianError, OSError) as exc:
            out.append(f"git sources: {exc}")
        f = feeds()["debsecan"]
        if now - max(f["fetched"] or 0, asked.get("feed:debsecan", 0)) >= 24 * 3600:
            asked["feed:debsecan"] = now
            librarian._write_json(STATE, asked)
            try:
                out.append(refresh_feed(log))
            except (LibrarianError, OSError, zlib.error) as exc:
                out.append(f"debsecan's data: {exc}")
    for kid, k in definitions().items():
        if not cfg["kits"][kid]["keep_current"]:
            continue
        chosen = policy.get("kits_every_hours", policy["check_every_hours"])
        if not chosen:   # Manual: refreshed only when asked
            continue
        every = k.get("refresh_hours") or chosen
        cached = ((st.get("kits") or {}).get(kid) or {}).get("cached") or {}
        last = max(cached.get("fetched") or 0, asked.get(kid, 0))
        if now - last >= every * 3600:
            librarian._queue_root({"action": "kit-fetch", "kit": kid, "budget_mb": cfg["budget_mb"]})
            asked[kid] = now
            librarian._write_json(STATE, asked)
            out.append(f"downloading {kid}")
            break
    # The security doctor's deep audit (debian-cis and Lynis), weekly by default, when the
    # security kit is kept current: its tools come from its cache.
    days = cfg.get("deep_audit_days", 7)
    if days and cfg["kits"].get("security", {}).get("keep_current"):
        last = (librarian._read_json(librarian.STATE_DIR / "control" / "security-deep.json", {}) or {}).get("at") or 0
        if now - max(last, asked.get("deep-audit", 0)) >= days * 86400:
            librarian._queue_root({"action": "security-deep-audit"})
            asked["deep-audit"] = now
            librarian._write_json(STATE, asked)
            out.append("deep audit scheduled")
    return ". ".join(out) or None
