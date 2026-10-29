# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The card the box runs from, read only (housecall.py): the identity in a stand-in /sys, a wrap-around found in an
image that repeats its start, a genuine image, and the furthest ext4 backup superblock that reads back. Nothing is
written. python3 tests/sim_card_genuine.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import housecall

T = Path(tempfile.mkdtemp(prefix="card-genuine-"))
sysdir = T / "sys"
disk = "mmcblk0"
device = sysdir / disk / "device"
device.mkdir(parents=True)
M = 1 << 20
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def csd_for(size):
    """An SD v2.0 CSD naming size bytes."""
    c_size = size // (512 * 1024) - 1
    return f"{((1 << 126) | (c_size << 62)):032x}"


def stand_in(manfid, name, date, serial, size, csd=True):
    (device / "manfid").write_text(f"0x{manfid:06x}\n")
    (device / "oemid").write_text("0x5344\n")
    (device / "name").write_text(name + "\n")
    (device / "date").write_text(date + "\n")
    (device / "serial").write_text(serial + "\n")
    (device / "csd").write_text(csd_for(size) if csd else "not-a-csd\n")
    (sysdir / disk / "size").write_text(f"{size // 512}\n")


def image(path, size, wrap_at=None):
    with open(path, "wb") as f:
        f.truncate(size)
        f.seek(0)
        start = b"BOOT" * (housecall.CARD_BYTES // 4)
        f.write(start)
        if wrap_at is not None:
            f.seek(wrap_at)
            f.write(start)
    return path


size = 2 << 30
stand_in(0x03, "SH64G", "04/2024", "0x1234", size)
ident = housecall.card_identity(disk, sysdir)
check(
    "the card's identity: maker, name, date, serial, and the size its CSD names against the system's",
    ident
    and ident["manfid"] == 0x03
    and ident["name"] == "SH64G"
    and ident["date"] == "04/2024"
    and ident["seen"] == size
    and ident["csd"] == size,
    ident,
)

fake = image(T / "fake.img", size, wrap_at=1 << 30)
wrap = housecall.card_wrap(fake, size)
check(
    "a card that repeats its start at 1 GiB is found there, and named",
    wrap.get("fake") is True and wrap.get("at") == 1 << 30,
    wrap,
)
said = housecall.card_finding(disk=disk, sys_dir=sysdir, device=fake, size=size)
check(
    "  the finding is a problem, with the real size in words",
    said["status"] == "problem" and "1.0 GB" in housecall.render(said["summary"]),
    said,
)

genuine = image(T / "genuine.img", size)
check("a genuine image does not wrap", housecall.card_wrap(genuine, size).get("fake") is False)

stand_in(0x99, "MYSTERY", "01/2025", "0x99", size)
unknown = housecall.card_finding(disk=disk, sys_dir=sysdir, device=genuine, size=size)
check(
    "an unknown maker is a note, not a verdict",
    unknown["status"] == "ok"
    and any(n.get("id") == "housecall.card.unknown-maker" for n in unknown["notes"])
    and "not in the short known list" in " ".join(housecall.render(n) for n in unknown["notes"]),
    unknown,
)

ext = T / "ext4.img"
with open(ext, "wb") as f:
    f.truncate(256 << 20)
subprocess.run(["mke2fs", "-q", "-t", "ext4", "-F", str(ext)], check=True)
extent = housecall.card_extent(ext)
check(
    "the furthest ext4 backup superblock reads back with its magic and UUID",
    extent and extent["far"] > 0 and extent["read"] > 0,
    extent,
)
stand_in(0x03, "SH64G", "04/2024", "0x1234", 256 << 20)
said = housecall.card_finding(disk=disk, sys_dir=sysdir, device=ext, part=ext, size=256 << 20)
check(
    "  the verdict says how far the card is shown to keep data",
    said["status"] == "ok" and "Genuine as far as" in housecall.render(said["summary"]),
    said,
)

report = housecall.examine_card(disk=disk, sys_dir=sysdir)
check(
    "the card check on its own is a report with its target and the card finding",
    [f["id"] for f in report["findings"]] == ["housecall-target", "housecall-card"],
    [f["id"] for f in report["findings"]],
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
