# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Where the box's storage is: the card, and the state when it lives elsewhere.

The hub's state folder can move: on a Lyra it sits on the SPI NAND (`ubi9:logs`, 223 MB)
while the card holds the rest (`/dev/mmcblk0p1`, tens of GB). A page that says "the card" must
read the card, and a page that says "the flash" must read the state. `places()` answers both from
the mount table and `statvfs`, in one place, so a reading that means the card cannot quietly
become the NAND's.

  card   the filesystem holding `/`
  state  the filesystem holding $HUB_STATE_DIR, only when it is not the card's

Each place: path, mount, device, fstype, kind (card, flash, usb, other), total and free bytes.
Stdlib only.
"""

import os
import re
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
MOUNTINFO = Path(os.environ.get("HUB_MOUNTINFO", "/proc/self/mountinfo"))


def _statvfs(path):
    return os.statvfs(path)


def _mounts():
    """The mount table: [{mount, fstype, source}], the fields after the '-' separator."""
    try:
        lines = MOUNTINFO.read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        fields = line.split()
        try:
            sep = fields.index("-")
            out.append({"mount": fields[4], "fstype": fields[sep + 1], "source": fields[sep + 2]})
        except (ValueError, IndexError):
            continue
    return out


def _mount_for(path, mounts=None):
    """The mount holding path: the longest mount point that is a prefix of it."""
    target = os.path.realpath(str(path))
    best = None
    for mount in mounts if mounts is not None else _mounts():
        point = mount["mount"]
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) > len(best["mount"]):
                best = mount
    return best


PSEUDO = {
    "proc",
    "sysfs",
    "devtmpfs",
    "devpts",
    "tmpfs",
    "ramfs",
    "cgroup",
    "cgroup2",
    "securityfs",
    "pstore",
    "debugfs",
    "tracefs",
    "configfs",
    "fusectl",
    "mqueue",
    "hugetlbfs",
    "bpf",
    "autofs",
    "overlay",
    "nsfs",
    "binfmt_misc",
}


def _drive(mount):
    """A mounted device a person thinks of as a drive, not a kernel or container filesystem."""
    source = str(mount.get("source") or "")
    name = source.rsplit("/", 1)[-1]
    if mount.get("fstype") in PSEUDO:
        return False
    if name.startswith(("loop", "zram")):
        return False
    return source.startswith("/dev/") or source.startswith(("ubi", "mtd"))


def _base(source):
    """The disk a partition is on: mmcblk0p1 -> mmcblk0, nvme0n1p1 -> nvme0n1, sda1 -> sda."""
    name = str(source or "").rsplit("/", 1)[-1]
    return re.sub(r"p?\d+$", "", name) if name.startswith(("mmcblk", "nvme")) else re.sub(r"\d+$", "", name)


def _kind(source):
    name = str(source or "").rsplit("/", 1)[-1]
    if name.startswith(("ubi", "mtd")) or "nand" in name.lower():
        return "flash"
    if name.startswith(("mmcblk", "nvme")):
        return "card"
    if name.startswith(("sd", "usb")):
        return "usb"
    return "other"


def place(path, mounts=None):
    """One path's filesystem, or {} when it cannot be read."""
    try:
        st = _statvfs(str(path))
    except OSError:
        return {}
    mount = _mount_for(path, mounts) or {}
    total = st.f_blocks * st.f_frsize
    free = min(st.f_bavail * st.f_frsize, total)
    return {
        "path": str(path),
        "mount": mount.get("mount"),
        "device": mount.get("source"),
        "fstype": mount.get("fstype"),
        "kind": _kind(mount.get("source")),
        "total": total,
        "free": max(0, free),
    }


def places(state=None):
    """{card, state?, drives?}: the card always; the state when its filesystem is not the card's;
    drives for every other mounted device (a USB disk, a second card), each once."""
    mounts = _mounts()
    card = place("/", mounts)
    state_path = STATE if state is None else state
    state = place(state_path, mounts)
    if state and card and (state.get("mount") == card.get("mount") or state.get("device") == card.get("device")):
        state = None
    out = {"card": card}
    seen = {card.get("device")}
    card_disk = _base(card.get("device"))
    drives = []
    if state:
        out["state"] = state
        seen.add(state.get("device"))
        drives.append(state)
    for mount in mounts:
        if not _drive(mount) or mount.get("source") in seen or _base(mount.get("source")) == card_disk:
            continue
        found = place(mount["mount"], mounts)
        if not found or not found.get("total"):
            continue
        seen.add(mount["source"])
        drives.append(found)
    if drives:
        out["drives"] = drives
    return out
