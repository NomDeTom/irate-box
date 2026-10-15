# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The offline kit made again (/admin, Updates and backup): the last export's choices are kept, so Refresh makes the
same kit again, and the owner chooses when the librarian makes it again by itself: only when asked, after each hub
update (the kit's hub is older than the installed one), or every week. Stdlib only; the hub's own file."""

import json
import os
import re
import time
from pathlib import Path

WHEN = ("manual", "update", "weekly")
WEEK = 7 * 86400
ASK_GAP = 6 * 3600   # an export takes minutes: asked again only after this, so one slow export is not asked twice


def _file(state_dir):
    return Path(state_dir) / "kit-auto.json"


def load(state_dir):
    """{"when", "request" (the last export's root request, or None), "asked" (when the librarian last asked)}."""
    try:
        d = json.loads(_file(state_dir).read_text())
    except (OSError, ValueError):
        d = {}
    return {"when": d.get("when") if d.get("when") in WHEN else "manual", "request": d.get("request"), "asked": d.get("asked", 0)}


def _save(state_dir, d):
    p = _file(state_dir)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def remember(state_dir, req):
    """The export just asked for: the one Refresh and the schedule make again."""
    d = load(state_dir)
    d["request"] = req
    _save(state_dir, d)


def set_when(state_dir, when):
    if when not in WHEN:
        raise ValueError(when)
    d = load(state_dir)
    d["when"] = when
    _save(state_dir, d)


def asked(state_dir, now=None):
    d = load(state_dir)
    d["asked"] = now or time.time()
    _save(state_dir, d)


def kit_version(kit):
    """The hub commit a kit holds (irate-box-kit-<commit>-<arch>.tar), or None (a content export holds no hub)."""
    m = re.match(r"irate-box-kit-([0-9a-f]{7,40})-", (kit or {}).get("name", ""))
    return m.group(1) if m else None


def due(state_dir, kit, installed, now=None):
    """The request to make again now, or None: per the owner's choice, when there is a last export to repeat and the
    librarian has not just asked. installed: the hub's commit (VERSION's first word)."""
    now = now or time.time()
    d = load(state_dir)
    req = d["request"]
    if d["when"] == "manual" or not req or now - d["asked"] < ASK_GAP:
        return None
    if not kit:
        return req   # the last kit is gone (deleted, or never finished): made again
    if d["when"] == "weekly":
        return req if now - (kit.get("at") or 0) >= WEEK else None
    held = kit_version(kit)
    return req if held and installed and not installed.startswith(held) and not held.startswith(installed) else None
