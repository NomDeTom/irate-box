# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Where the librarian keeps podcasts and books: the card, or the library drive root mounted.

Root writes the record (irate_box/root/librarydrive.py) into the control folder. The hub only reads
it, and asks root to choose a drive or eject it; it never chooses a path. When a kind is set to the
drive and the drive is not there, place() answers None: the librarian skips what is bound for it
rather than writing to the card instead. Stdlib only.
"""

import json
import os
from pathlib import Path

from irate_box.msg import M

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = STATE / "control" / "library-drive.json"
KINDS = ("podcasts", "books")
DEFAULT_MOUNT = Path(os.environ.get("HUB_LIBRARY_MOUNT", "/media/irate-box/library"))
FOLDERS = {"podcasts": "podcasts", "books": "zim"}


def record():
    try:
        data = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    kinds = data.get("kinds") if isinstance(data.get("kinds"), dict) else {}
    out = {"uuid": str(data.get("uuid") or ""), "mount": str(data.get("mount") or DEFAULT_MOUNT)}
    out["kinds"] = {k: kinds.get(k) if kinds.get(k) in ("card", "drive") else "card" for k in KINDS}
    out["mounted"] = bool(out["uuid"]) and bool(data.get("mounted")) and os.path.ismount(out["mount"])
    return out


def status():
    rec = record()
    out = dict(rec)
    if rec["mounted"]:
        try:
            st = os.statvfs(rec["mount"])
            out["total"], out["free"] = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        except OSError:
            out["mounted"] = False
    return out


def place(kind, card):
    """Where this kind is kept now: the card path, the drive's folder, or None when the drive is
    chosen and not plugged in."""
    rec = record()
    if rec["kinds"].get(kind) != "drive":
        return Path(card)
    if not rec["mounted"]:
        return None
    return Path(rec["mount"]) / FOLDERS[kind]


def on_drive(kind):
    return record()["kinds"].get(kind) == "drive"


def drive_missing(kind):
    rec = record()
    return rec["kinds"].get(kind) == "drive" and not rec["mounted"]


def _control_request(request):
    # Imported here: the hub's control queue is server.py's own, and a checkout may run without it.
    from irate_box.hub import server

    return server.control_request(request)


def choose(uuid, kinds):
    return _control_request({"action": "library-drive", "uuid": str(uuid or ""), "kinds": kinds})


def eject():
    return _control_request({"action": "library-drive-eject"})


def said_drive_missing(kind):
    return M(
        "library-drive.missing",
        "The library drive is not plugged in. The librarian skips every download for {kind} rather than "
        "writing to the card instead.",
        kind=kind,
    )
