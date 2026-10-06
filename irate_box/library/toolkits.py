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
import json
import time

from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError
from irate_box.hub import kitdefs

SETTINGS = librarian.LIB_DIR / "toolkits.json"
STATE = librarian.LIB_DIR / "toolkits-state.json"  # when each kit's fetch was last asked for
STATUS = librarian.STATE_DIR / "control" / "kits.json"  # root's: what is cached and installed
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
    return {"kits": definitions(), "settings": settings(), "status": status()}


def action(payload):
    """A request from the page: queued for root, its id returned."""
    what = payload.get("action")
    if what == "settings":
        set_settings(payload)
        return None
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
    if what == "remove":
        return librarian._queue_root({"action": "kit-remove", "kit": kid})
    if what == "status":
        return librarian._queue_root({"action": "kit-status"})
    raise LibrarianError("action: settings, fetch, install, keep, remove or status")


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
