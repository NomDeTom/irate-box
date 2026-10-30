# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Stage 6's card layout (root/layout.py), offline with a recorded run: the plan's starts and labels, the GPT at
the card's first 16 MiB, the chosen filesystems, the loop device and its partitions, and the read-back.
python3 tests/sim_layout.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import layout

T = Path(tempfile.mkdtemp(prefix="layout-"))
image = T / "card.img"
with open(image, "wb") as fh:
    fh.truncate(20 << 30)
calls = []
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def run(*cmd, timeout=3600):
    calls.append(tuple(cmd))
    if cmd[:2] == ("losetup", "--find"):
        return subprocess.CompletedProcess(cmd, 0, "/dev/loop7\n", "")
    if cmd[0] == "blkid":
        return subprocess.CompletedProcess(cmd, 0, "\n".join(f"{d}: LABEL=x" for d in cmd[1:]), "")
    if cmd[:2] == ("sgdisk", "-p"):
        rows = "\n".join(
            f"{i} {p['start'] // 512} {p['start'] // 512 + p['size'] // 512} {p['size'] // (1 << 20)} MiB 8300 {p['name']}"
            for i, p in enumerate(parts, 1)
        )
        return subprocess.CompletedProcess(cmd, 0, "Number Start End Size Code Name\n" + rows + "\n", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


parts = layout.plan(20 << 30, "lyra", "btrfs+f2fs", "uboot")
check(
    "the layout: bootstate at 16 MiB, two 4 GiB slots, state and content to the end",
    parts[0]["name"] == "irate-bootstate"
    and parts[0]["start"] == 16 << 20
    and [p["name"] for p in parts]
    == ["irate-bootstate", "irate-system-a", "irate-system-b", "irate-state", "irate-content"]
    and parts[3]["filesystem"] == "btrfs"
    and parts[4]["filesystem"] == "f2fs",
    parts,
)
try:
    layout.plan(1 << 30)
    check("a card too small for the plan is refused", False)
except Refused as exc:
    check("a card too small for the plan is refused", exc.message["id"] == "layout.small", exc.message)
report = layout.write(image, "lyra", "btrfs+f2fs", "uboot", source=T / "system", run=run)
check(
    "the GPT: the first partition starts at 32768 (16 MiB) and every name is set",
    any(c[:2] == ("sgdisk", "--clear") for c in calls)
    and any("--new=1:32768:+32768" in c for c in calls)
    and all(
        any(f"--change-name={i}:{p['name']}" in c for c in calls if c[0] == "sgdisk") for i, p in enumerate(parts, 1)
    ),
    calls,
)
check(
    "the loop device's partitions get their filesystems, and the loop is detached",
    any(c[0] == "mkfs.ext4" and c[-1] == "/dev/loop7p2" for c in calls)
    and any(c[0] == "mkfs.btrfs" and c[-1] == "/dev/loop7p4" for c in calls)
    and any(c[0] == "mkfs.f2fs" and c[-1] == "/dev/loop7p5" for c in calls)
    and ("losetup", "-d", "/dev/loop7") in calls,
    calls,
)
# Found on the spare card (2026-10-29/30): the raw bootstate kept the old image's ext4 signature and root UUID, and a
# Lyra's U-Boot scanned nothing, as no partition was bootable and the first one is raw.
check(
    "the raw bootstate is wiped of any old signature, and nothing else is",
    ("wipefs", "-a", "/dev/loop7p1") in calls and sum(1 for c in calls if c[0] == "wipefs") == 1,
    [c for c in calls if c[0] == "wipefs"],
)
check(
    "  slot A, and only slot A, is marked bootable for U-Boot's scan",
    any("--change-name=2:irate-system-a" in c and "--attributes=2:set:2" in c for c in calls)
    and sum(1 for c in calls if any(a.startswith("--attributes=") for a in c)) == 1,
    [c for c in calls if c[0] == "sgdisk"],
)
check(
    "the system is copied into each slot, and the read-back matches the plan",
    sum(1 for c in calls if c[0] == "rsync" and "--delete" in c) == 2
    and report["table"].startswith("Number")
    and report["matches"] is True
    and len(report["blkid"].splitlines()) == len(parts),
    report,
)


def empty_run(*cmd, timeout=3600):
    if cmd[:2] == ("sgdisk", "-p"):
        return subprocess.CompletedProcess(cmd, 0, "Number Start End Size Code Name\n", "")
    return run(*cmd, timeout=timeout)


image2 = T / "card2.img"
with open(image2, "wb") as fh:
    fh.truncate(20 << 30)
try:
    layout.write(image2, run=empty_run)
    check("a read-back that parses nothing is refused", False)
except Refused as exc:
    check("a read-back that parses nothing is refused", exc.message["id"] == "layout.mismatch", exc.message)

real_stat = layout.os.stat
layout._root_disk = lambda: "mmcblk0"
layout.run = lambda *c, **k: subprocess.CompletedProcess(c, 0, "mmcblk0\n", "")
layout.os.stat = lambda path: type("S", (), {"st_mode": 0, "st_rdev": 7})()
layout.stat.S_ISBLK = lambda mode: True
check(
    "the running card is refused even with no own_device", layout._own("/dev/mmcblk0") and layout._own("/dev/mmcblk0p1")
)
layout.os.stat = real_stat
layout.stat.S_ISBLK = __import__("stat").S_ISBLK
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
