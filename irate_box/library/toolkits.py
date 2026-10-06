# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits, the hub's half (toolkits-plan §1–3): the owner's settings, what the page shows, and
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
    out = {"budget_mb": raw.get("budget_mb", 500), "kits": {}}
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
            raise LibrarianError("budget_mb: 10 to 65536")
        cfg["budget_mb"] = b
    for kid, ch in (changes.get("kits") or {}).items():
        if kid not in cfg["kits"] or not isinstance(ch, dict):
            raise LibrarianError(f"no toolkit {kid}")
        if "keep_current" in ch:
            if type(ch["keep_current"]) is not bool:
                raise LibrarianError("keep_current: true or false")
            cfg["kits"][kid]["keep_current"] = ch["keep_current"]
        if "remove_after" in ch:
            if not _hours_ok(ch["remove_after"]):
                raise LibrarianError(f"remove_after: 1 to {MAX_HOURS} hours, or null for never")
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
        raise LibrarianError("this box's Debian suite is unknown (/etc/os-release)")
    FEED.mkdir(parents=True, exist_ok=True)
    for name in (s, "GENERIC"):
        with librarian._open(FEED_URL + name, timeout=120) as resp:
            raw = resp.read(64 << 20)
        if not zlib.decompress(raw)[:10] == b"VERSION 1\n":
            raise LibrarianError(f"debsecan's data for {name} is not in the format it reads")
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
                         "groups": [], "history": "shallow", "budget_mb": 64, "submodules": False})
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
            raise LibrarianError("kit: {title, packages, ...}")
        kid = spec.get("id") or re.sub(r"[^a-z0-9]+", "-", str(spec.get("title", "")).lower()).strip("-")[:32]
        if kid in kitdefs.shipped() or kid == "extras":
            kid = f"my-{kid}"[:32]
        pkgs = spec.get("packages")
        if isinstance(pkgs, str):
            pkgs = re.split(r"[\s,]+", pkgs.strip())
        return librarian._queue_root({"action": "kit-define", "kit": {
            "id": kid, "title": str(spec.get("title", "")), "summary": str(spec.get("summary", "")),
            "packages": [p for p in (pkgs or []) if p], "remove_after_hours": spec.get("remove_after_hours", 24)}})
    kid = payload.get("kit")
    if what != "status" and kid not in definitions():
        raise LibrarianError("kit: one of " + ", ".join(definitions()))
    if what == "fetch":
        return librarian._queue_root({"action": "kit-fetch", "kit": kid, "budget_mb": settings()["budget_mb"]})
    if what in ("install", "keep"):
        hours = payload.get("hours", settings()["kits"][kid]["remove_after"]) if what == "install" else payload.get("hours")
        if not _hours_ok(hours):
            raise LibrarianError(f"hours: 1 to {MAX_HOURS}, or null for never")
        return librarian._queue_root({"action": f"kit-{what}", "kit": kid, "hours": hours})
    if what in ("remove", "rollback"):
        return librarian._queue_root({"action": f"kit-{what}", "kit": kid})
    if what == "status":
        return librarian._queue_root({"action": "kit-status"})
    if what == "extra":
        pkgs = payload.get("packages")
        if not isinstance(pkgs, list) or len(pkgs) > 20:
            raise LibrarianError("packages: a list of up to 20 names")
        return librarian._queue_root({"action": "kit-extra", "kit": kid, "packages": [str(p) for p in pkgs]})
    if what == "undefine":
        return librarian._queue_root({"action": "kit-undefine", "kit": kid})
    raise LibrarianError("action: settings, fetch, install, keep, remove, rollback, status, define, undefine or extra")


def step(policy, now=None, log=print):
    """The librarian's part, once a run: an install whose time is up is removed; and one kit
    kept current whose cache is missing or due is fetched (one a run: a fetch holds the root
    helper for minutes). Returns a line, or None when nothing was due."""
    now = now or time.time()
    st = status()
    out = []
    if any(v.get("remove_at") and v["remove_at"] <= now for v in (st.get("installed") or {}).values()):
        librarian._queue_root({"action": "kit-expire"})
        out.append("removing the kits whose time is up")
    cfg = settings()
    asked = librarian._read_json(STATE, {})
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
        every = k.get("refresh_hours") or policy["check_every_hours"]
        cached = ((st.get("kits") or {}).get(kid) or {}).get("cached") or {}
        last = max(cached.get("fetched") or 0, asked.get(kid, 0))
        if now - last >= every * 3600:
            librarian._queue_root({"action": "kit-fetch", "kit": kid, "budget_mb": cfg["budget_mb"]})
            asked[kid] = now
            librarian._write_json(STATE, asked)
            out.append(f"fetching {kid}")
            break
    return "; ".join(out) or None
