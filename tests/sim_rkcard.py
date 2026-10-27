# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A board's SD card in a USB reader (hub/rkusb.cards, root/rkcard.py), offline: a reader with a card listed, the
box's own disk and an empty reader left out; each action refusing a card swapped or mounted; a check that only
reads (the partition table, each filesystem read-only); the genuine test's answers (real, fake, not finished);
an image written to the card (a file standing in), read back the same, the card's end cleared; an image that does
not match or is too large refused, nothing written. python3 tests/sim_rkcard.py"""

import hashlib
import json
import lzma
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkcard-"))
(T / "state" / "rk-images").mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.hub import rkusb
from irate_box.msg import Refused
from irate_box.root import rkcard

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args):
    try:
        fn(*args)
    except Refused as exc:
        return exc.message["id"]
    return None


# The listing, from lsblk's JSON.
sample = {
    "blockdevices": [
        {
            "name": "mmcblk0",
            "type": "disk",
            "rm": False,
            "tran": None,
            "size": 63864569856,
            "children": [{"name": "mmcblk0p2", "mountpoint": "/"}],
        },
        {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "rm": True,
            "tran": "usb",
            "size": 15634268160,
            "model": "STORAGE DEVICE ",
            "serial": "000000001206",
            "children": [{"name": "sda1", "mountpoint": None}],
        },
        {"name": "sdb", "type": "disk", "rm": True, "tran": "usb", "size": 0},
        {"name": "sdc", "type": "disk", "rm": False, "tran": "usb", "size": 500107862016},
    ]
}
got = rkusb.cards(json.dumps(sample), "mmcblk0")
check(
    "cards: a removable USB disk with a medium in, its model and serial; the box's disk, an empty reader and a fixed USB disk left out",
    [(c["name"], c["model"], c["serial"], c["mounted"]) for c in got]
    == [("sda", "STORAGE DEVICE", "000000001206", False)],
    got,
)
sample["blockdevices"][1]["children"][0]["mountpoint"] = "/media/raspberry/armbi_root"
check("  one of its partitions mounted: said", rkusb.cards(json.dumps(sample), "mmcblk0")[0]["mounted"] is True)

# The card: a file standing in for /dev/sda.
SIZE = 8 << 20
dev = T / "sda"
dev.write_bytes(b"\xff" * SIZE)
card = {"name": "sda", "path": str(dev), "size": SIZE, "model": "STORAGE DEVICE", "serial": "S1", "mounted": False}
rkusb.cards = lambda *a, **k: [dict(card)]
ran = []
answers = {}


def fake_run(*cmd, timeout=600):
    ran.append(cmd)
    out, rc = answers.get(cmd[0], ("", 0))
    if cmd[0] == "lsblk":
        out = json.dumps(
            {
                "blockdevices": [
                    {
                        "name": "sda",
                        "children": [
                            {"name": "sda1", "path": "/dev/sda1", "fstype": "ext4"},
                            {"name": "sda2", "path": "/dev/sda2", "fstype": "vfat"},
                        ],
                    }
                ]
            }
        )
    return subprocess.CompletedProcess(cmd, rc, out, "")


rkcard.run = fake_run
check("a card not shown: refused", refused(rkcard.check, "sdz", SIZE, "S1") == "rkcard.gone")
check(
    "  another card in the same reader (its size or serial): refused",
    refused(rkcard.check, "sda", SIZE, "S2") == "rkcard.changed"
    and refused(rkcard.check, "sda", SIZE + 1, "S1") == "rkcard.changed",
)
card["mounted"] = True
check("  mounted: refused, saying to unmount it", refused(rkcard.check, "sda", SIZE, "S1") == "rkcard.mounted")
card["mounted"] = False
check("  a name that is not a reader's disk: refused", refused(rkcard.check, "mmcblk0", SIZE, "S1") == "rkcard.name")

answers["sgdisk"] = ("Caution! After loading partitions, the CRC doesn't check out!\nMain partition table: ERROR\n", 0)
answers["e2fsck"] = ("", 0)
answers["fsck.vfat"] = ("", 1)
ran.clear()
said = rkcard.check("sda", SIZE, "S1")
text = said["args"]["said"]
check(
    "check: reads only (sgdisk -v, e2fsck -n, fsck.vfat -n), says the table damaged and each filesystem",
    "damaged" in text
    and "sda1 (ext4): clean" in text
    and "sda2 (vfat): errors found, nothing changed" in text
    and ("e2fsck", "-n", "-f", "/dev/sda1") in ran
    and ("fsck.vfat", "-n", "/dev/sda2") in ran
    and all(c[0] in ("sgdisk", "lsblk", "e2fsck", "fsck.vfat") for c in ran),
    (said, ran),
)
answers["sgdisk"] = ("No problems found. 2014 free sectors\n", 0)
check("  a sound table: said", "partition table: no problems" in rkcard.check("sda", SIZE, "S1")["args"]["said"])

answers["f3probe"] = (
    "Good news: The device `/dev/sda' is the real thing\n\tDevice geometry:\n\t         *Usable* size: 14.56 GB (30535680 blocks)\n",
    0,
)
check(
    "genuine: the real thing, its usable size said",
    rkcard.genuine("sda", SIZE, "S1")["id"] == "rkcard.genuine"
    and ("f3probe", "--destructive", "--time-ops", str(dev)) in ran,
)
answers["f3probe"] = (
    "Bad news: The device `/dev/sda' is a counterfeit of type limbo\n\t         *Usable* size: 7.48 GB (15687680 blocks)\n",
    0,
)
got = rkcard.genuine("sda", SIZE, "S1")
check(
    "  a fake: said, with how much of it is usable",
    got["id"] == "rkcard.fake" and got["args"]["usable"] == "7.48 GB",
    got,
)
answers["f3probe"] = ("", 1)
check("  not finished: refused", refused(rkcard.genuine, "sda", SIZE, "S1") == "rkcard.f3-failed")

raw = os.urandom(300000)
packed = lzma.compress(raw)
(rkusb.IMAGES / "board.img.xz").write_bytes(packed)
digest = hashlib.sha256(packed).hexdigest()
check(
    "flash: an image that does not match: refused, nothing written",
    refused(rkcard.flash, "sda", SIZE, "S1", "board.img.xz", "0" * 64) == "rkboard.image-damaged"
    and dev.read_bytes()[:4] == b"\xff" * 4,
)
said = rkcard.flash("sda", SIZE, "S1", "board.img.xz", digest)
data = dev.read_bytes()
check(
    "  written and read back the same, the card's last 1 MiB cleared, the space between left alone",
    said["id"] == "rkcard.flashed"
    and data[: len(raw)] == raw
    and data[-(1 << 20) :] == bytes(1 << 20)
    and data[len(raw)] == 0xFF,
    said,
)
big = os.urandom(SIZE + 4096)
(rkusb.IMAGES / "big.img").write_bytes(big)
check(
    "  an image larger than the card: refused",
    refused(rkcard.flash, "sda", SIZE, "S1", "big.img", hashlib.sha256(big).hexdigest()) == "rkboard.image-large",
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
