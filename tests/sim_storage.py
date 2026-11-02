# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Where the box's storage is (hub/storage.py), from a mount table and statvfs shaped like box
181's: `/` on the SD card, `/var/lib/hub` on the SPI NAND, the state's subfolders bind-mounted back.
The card is the card's figures; the state is reported as flash only when it differs; with both on
one filesystem there is one place. Then the status fields, the disk history and the presets' facts.
python3 tests/sim_storage.py"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="storage-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import memhistory, server, storage

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


CARD_BYTES = 58 * 1000**3
CARD_FREE = 33 * 1000**3
NAND_BYTES = 223 * 1000**2
NAND_FREE = 145 * 1000**2
CARD = "/dev/mmcblk0p1"
NAND = "ubi9:logs"
USB = "/dev/sda1"
USB_BYTES = 64 * 1000**3
USB_FREE = 60 * 1000**3


def mountinfo(path, state_source=NAND, state_mount="/var/lib/hub", usb=True):
    text = (
        f"36 25 179:1 / / rw,relatime shared:1 - ext4 {CARD} rw\n"
        f"37 36 0:30 / {state_mount} rw - ubifs {state_source} rw\n"
        f"38 36 179:1 /var/lib/hub/ci /var/lib/hub/ci rw - ext4 {CARD} rw\n"
        f"39 36 179:1 /var/lib/hub/drop /var/lib/hub/drop rw - ext4 {CARD} rw\n"
        f"40 36 179:2 /boot /boot rw - vfat /dev/mmcblk0p2 rw\n"
    )
    if usb:
        text += f"41 36 8:1 / /media/usb rw - vfat {USB} rw\n"
    path.write_text(text)


def statvfs(path):
    real = os.path.realpath(path)
    total, free = (
        (CARD_BYTES, CARD_FREE)
        if real == "/"
        else (USB_BYTES, USB_FREE)
        if real == "/media/usb"
        else (NAND_BYTES, NAND_FREE)
    )
    return SimpleNamespace(f_blocks=total, f_frsize=1, f_bavail=free)


mounts = T / "mountinfo"
mountinfo(mounts)
with (
    mock.patch.object(storage, "MOUNTINFO", mounts),
    mock.patch.object(storage, "STATE", Path("/var/lib/hub")),
    mock.patch.object(storage, "_statvfs", statvfs),
):
    places = storage.places()
    check(
        "two devices: the card is `/`, named by its device, with its own size",
        places["card"]["device"] == CARD
        and places["card"]["kind"] == "card"
        and places["card"]["total"] == CARD_BYTES
        and places["card"]["free"] == CARD_FREE,
        places.get("card"),
    )
    check(
        "  the state is the flash, named `ubi9:logs`, with the NAND's size",
        places["state"]["device"] == NAND
        and places["state"]["kind"] == "flash"
        and places["state"]["total"] == NAND_BYTES
        and places["state"]["free"] == NAND_FREE,
        places.get("state"),
    )
    drives = {d["kind"]: d for d in places["drives"]}
    check(
        "  a mounted USB disk is a drive of its own; /boot on the card's disk is not another drive",
        drives["usb"]["device"] == USB
        and drives["usb"]["free"] == USB_FREE
        and drives["flash"]["device"] == NAND
        and len(places["drives"]) == 2,
        places.get("drives"),
    )
    # A bind of the card under the state does not make the state the card's.
    check(
        "  a bind of the card inside the state does not change the state's place",
        storage.place("/var/lib/hub/ci", storage._mounts())["device"] == CARD,
        storage.place("/var/lib/hub/ci", storage._mounts()),
    )

    # The status: disk_* stay the card; state_* appears only when the state is elsewhere.
    status = server.system_status(admin=True)
    check(
        "the status: the card's figures under the names every page reads as the card",
        status["disk_total"] == CARD_BYTES and status["disk_free"] == CARD_FREE and status["disk_kind"] == "card",
        status,
    )
    check(
        "  and the state's as their own, with their kind",
        status["state_total"] == NAND_BYTES
        and status["state_free"] == NAND_FREE
        and status["state_kind"] == "flash"
        and status["state_device"] == NAND,
        status,
    )
    check(
        "the admin status lists every extra drive, the state and the USB disk",
        [(d["kind"], d["device"]) for d in status["drives"]] == [("flash", NAND), ("usb", USB)],
        status.get("drives"),
    )
    public = server.system_status()
    check(
        "the public status: the card only, no state and no other drive sent to guests",
        "state_total" not in public and "drives" not in public,
        public,
    )
    # The presets judge the room where their things go: the card.
    check(
        "the presets' free space is the card's, not the NAND's",
        server._preset_facts()["free_mb"] == CARD_FREE // (1 << 20),
        server._preset_facts().get("free_mb"),
    )
    # The disk history: the card's series, and the state's as its own.
    with mock.patch.object(memhistory, "storage", storage):
        check(
            "the disk history reads the card",
            memhistory.disk() == {"disk_total": CARD_BYTES, "disk_free": CARD_FREE},
            memhistory.disk(),
        )
        check(
            "  and the state as its own series",
            memhistory.state() == {"state_total": NAND_BYTES, "state_free": NAND_FREE, "state_kind": "flash"},
            memhistory.state(),
        )

    # Both on the card: one place, no state fields.
    mountinfo(mounts, state_source=CARD, usb=False)
    with mock.patch.object(storage, "MOUNTINFO", mounts):
        one = storage.places()
        status = server.system_status(admin=True)
    check("state on the card: the card only", set(one) == {"card"} and "state_total" not in status, (one, status))

# A state folder on a USB disk is named as such, not as flash.
mountinfo(mounts, state_source="/dev/sda1")
with (
    mock.patch.object(storage, "MOUNTINFO", mounts),
    mock.patch.object(storage, "STATE", Path("/var/lib/hub")),
    mock.patch.object(storage, "_statvfs", statvfs),
):
    check("state on a USB disk: kind usb", storage.places()["state"]["kind"] == "usb")

# A sample's state fields are kept only when they are sound; an older sample keeps its memory.
mem = {"total": 1024, "available": 512, "swap_total": 0, "swap_free": 0}
good = dict(mem, at=1, state_total=223, state_free=145, state_kind="flash")
bad = dict(mem, at=2, state_total=223, state_free=999, state_kind="flash")
check(
    "a sound state series is kept, an unsound one is left out, the memory stays",
    memhistory._clean(good).get("state_free") == 145
    and "state_total" not in memhistory._clean(bad)
    and memhistory._clean(bad)["available"] == 512,
    (memhistory._clean(good), memhistory._clean(bad)),
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
