# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards on the box's USB (root/rkboard.py, the root helper's rk-loader and rk-erase, /admin/rkboard),
offline, against a made sysfs whose board changes mode as the stood-in adb and rkdeveloptool act on it: each mode
found (its own system by its ADB interface, Loader and Maskrom by the RockUSB interface and the low bit of its USB
version); a board running its own system switched to Loader mode; a board in Loader mode erased and reset; one in
Maskrom mode sent the loader for the chip selected first; none, several, the wrong mode, a missing tool, an unknown
chip and a damaged loader refused; the shipped loaders matching their recorded sha256; control/rkboard.json written
after each step; the menu entry listed only to an administrator. python3 tests/sim_rkboard.py"""

import hashlib
import json
import lzma
import os
import re
import shutil as real_shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkboard-"))
USB = T / "usb"
USB.mkdir()
os.environ.update(HUB_SYS_USB=str(USB), HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "state" / "control").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused, render
from irate_box.root import hub_control, rkboard

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def plug(path, mode, serial="b57290249a9b3206"):
    """A Rockchip device at this USB path, in this mode, as sysfs shows it. "spl" is the RK3506B's SPL loader sent
    from Maskrom, as a Lyra Zero W showed it on 2026-10-29: USB 2.00 like the boot ROM, but named. The boot ROM has
    no names at all, so sysfs has no manufacturer, product or serial file for it."""
    dev = USB / path
    real_shutil.rmtree(dev, ignore_errors=True)
    dev.mkdir()
    pid, manufacturer, product, version, iface = {
        "system": ("0013", "rockchip", "rk3xxx", " 2.00", ("ff", "42", "01")),
        "loader": ("350f", "", "USB download gadget", " 2.01", ("ff", "06", "05")),
        "spl": ("350f", "RockChip", "USB-MSC", " 2.00", ("ff", "06", "05")),
        "maskrom": ("350f", None, None, " 2.00", ("ff", "06", "05")),
        "other": ("0006", "", "rk3xxx", " 2.00", ("08", "06", "50")),
    }[mode]
    if mode == "spl":
        serial = "rockchip"
    elif mode == "maskrom":
        serial = None
    for f, v in {
        "idVendor": "2207",
        "idProduct": pid,
        "manufacturer": manufacturer,
        "product": product,
        "version": version,
        "serial": serial,
    }.items():
        if v:
            (dev / f).write_text(v + "\n")
    i = dev / f"{path}:1.0"
    i.mkdir()
    for f, v in zip(("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"), iface, strict=True):
        (i / f).write_text(v + "\n")


def unplug_all():
    for d in USB.iterdir():
        real_shutil.rmtree(d)


ran = []
outcome = {"ef": 0, "corrupt": False}
# What the loader shows (rfi): the Lyra's SPI NAND unless a test puts a card in. A card is kept as bytes, small
# (its sectors), while its MB says gigabytes, as rfi prints both.
NAND = {"mb": 256, "sectors": 524288}
shows = dict(NAND)
card = bytearray()


def fake_run(*cmd, timeout=600):
    ran.append(cmd)
    if cmd[0] == "adb" and cmd[-2:] == ("reboot", "loader"):
        plug("1-1.2", "loader")
    if cmd[:2] == ("rkdeveloptool", "db"):
        plug("1-1.2", "spl")  # as the real RK3506B loader comes up: still USB 2.00, but named
    if cmd[:2] == ("rkdeveloptool", "rfi"):
        out = f"Flash Info:\r\n\tManufacturer: SAMSUNG, value=00\r\n\tFlash Size: {shows['mb']} MB\r\n\tFlash Size: {shows['sectors']} Sectors\r\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")
    if cmd[:2] == ("rkdeveloptool", "wl"):
        data, at = Path(cmd[3]).read_bytes(), int(cmd[2]) * 512
        card[at : at + len(data)] = data
    if cmd[:2] == ("rkdeveloptool", "rl"):
        at, n = int(cmd[2]) * 512, int(cmd[3])  # the first sector, then a length in bytes, as the real tool takes
        back = bytes(card[at : at + n])
        Path(cmd[4]).write_bytes(back[:-1] + b"?" if outcome["corrupt"] else back)
    rc = outcome["ef"] if cmd[:2] == ("rkdeveloptool", "ef") else 0
    return subprocess.CompletedProcess(cmd, rc, "", "erase failed" if rc else "")


rkboard.run = fake_run
rkboard.time.sleep = lambda s: None
rkboard.WAIT = 3
have = {"rkdeveloptool": "/usr/bin/rkdeveloptool", "adb": "/usr/bin/adb"}
rkboard.rkusb.shutil.which = lambda name: have.get(name)

# The shipped loaders: each file present and matching its sha256.
shipped = rkboard.loaders()
check(
    "the shipped loaders: RK3506B and RK3506, each file matching its recorded sha256",
    [x["id"] for x in shipped] == ["rk3506b", "rk3506"]
    and all(
        hashlib.sha256((rkboard.rkusb.LOADERS / x["file"]).read_bytes()).hexdigest() == x["sha256"] for x in shipped
    ),
    shipped,
)
check("  Rockchip's licence beside them", (REPO / "LICENSES" / "LicenseRef-Rockchip-rkbin.txt").is_file())
check(
    "the toolkit brings rkdeveloptool and adb first, and the console and card tools",
    json.loads((REPO / "toolkits/rockchip.json").read_text())["packages"][:2] == ["rkdeveloptool", "adb"]
    and {"tio", "grabserial", "python3-pexpect", "gdisk", "parted", "bmap-tools", "f3", "mmc-utils"}
    <= set(json.loads((REPO / "toolkits/rockchip.json").read_text())["packages"]),
)

# Each mode found.
for mode in ("system", "loader", "maskrom", "other"):
    unplug_all()
    plug("1-1.2", mode)
    found = rkboard.boards()
    check(f"found in {mode} mode", [b["mode"] for b in found] == [mode], found)
unplug_all()
plug("1-1.2", "spl")
found = rkboard.boards()
check(
    "the RK3506B's SPL loader sent from Maskrom (USB 2.00, but named): Loader mode, not Maskrom",
    [b["mode"] for b in found] == ["loader"],
    found,
)
unplug_all()
(USB / "1-1.1").mkdir()
(USB / "1-1.1" / "idVendor").write_text("a69c\n")
check("another vendor's device (the WiFi chip): not listed", rkboard.boards() == [])


def refused(fn, *args):
    try:
        fn(*args)
    except Refused as exc:
        return exc.message["id"]
    return None


check("nothing connected: refused, said", refused(rkboard.erase) == "rkboard.none")
plug("1-1.2", "loader")
plug("1-1.3", "loader", serial="other")
check("two boards: refused, one at a time", refused(rkboard.erase) == "rkboard.several")
real_shutil.rmtree(USB / "1-1.3")

# A board running its own system: switched to Loader mode by adb, through the root helper's action.
unplug_all()
plug("1-1.2", "system")
check("its own system: Erase refused (the wrong mode)", refused(rkboard.erase) == "rkboard.wrong-mode")
ran.clear()
said = hub_control.ACTIONS["rk-loader"]({"action": "rk-loader"})
check(
    "Switch to Loader mode: adb reboot loader for that board, then it is in Loader mode",
    ("adb", "-s", "b57290249a9b3206", "reboot", "loader") in ran and said["id"] == "rkboard.in-loader",
    ran,
)
state = json.loads((T / "state" / "control" / "rkboard.json").read_text())
check("  control/rkboard.json written after it", [b["mode"] for b in state["boards"]] == ["loader"], state)

# In Loader mode: erased and reset, no loader needed. What the flash holds is read before the erase.
card[:] = b"UBI#" + b"\0" * 16 + b"rescue\0logs\0state\0"
ran.clear()
read = hub_control.ACTIONS["rk-read"]({"action": "rk-read", "loader": None})
check(
    "the flash read says what it holds, with the token to confirm",
    read.get("token") == "logs,rescue,state" and "rescue" in render(read),
    read,
)
try:
    rkboard.erase(None)
    check("  a non-blank flash is not erased without the confirmation", False)
except Refused as exc:
    check(
        "  a non-blank flash is not erased without the confirmation", exc.message["id"] == "rkboard.erase.unconfirmed"
    )
ran.clear()
said = hub_control.ACTIONS["rk-erase"]({"action": "rk-erase", "loader": None, "confirm": read["token"]})
check(
    "Loader mode: read, confirmed, erased (ef) and reset (rd), no loader sent",
    ("rkdeveloptool", "ef") in ran
    and ("rkdeveloptool", "rd") in ran
    and any(c[:2] == ("rkdeveloptool", "rl") for c in ran)
    and not any(c[:2] == ("rkdeveloptool", "db") for c in ran)
    and said["id"] == "rkboard.erased"
    and "rescue" in render(said),
    (ran, said),
)
# An erased NAND reads as 0xFF (a Lyra Zero W's, read back with rkdeveloptool rl after ef, 2026-10-30).
card[:] = b"\xff" * (64 << 10)
outcome["ef"] = 1
check("  the erase failing: said, the board not reset", refused(rkboard.erase) == "rkboard.erase-failed")
outcome["ef"] = 0

# In Maskrom mode: the loader for the chip selected first.
unplug_all()
plug("1-1.2", "maskrom")
check("Maskrom with no chip selected: refused", refused(rkboard.erase, None) == "rkboard.loader-unknown")
ran.clear()
rkboard.erase("rk3506b")
db = [c for c in ran if c[:2] == ("rkdeveloptool", "db")]
check(
    "Maskrom: the RK3506B loader sent (db), then erased",
    db and db[0][2].endswith("rk3506b_spl_loader_v1.06.112.bin") and ("rkdeveloptool", "ef") in ran,
    ran,
)
check(
    "  an unknown chip refused by the root helper before anything runs",
    refused(hub_control.ACTIONS["rk-erase"], {"action": "rk-erase", "loader": "rk9999"}) == "rkboard.loader-unknown",
)
unplug_all()
plug("1-1.2", "maskrom")
damaged = T / "loaders"
real_shutil.copytree(rkboard.rkusb.LOADERS, damaged)
(damaged / "rk3506b_spl_loader_v1.06.112.bin").write_bytes(b"not a loader")
real_loaders = rkboard.rkusb.LOADERS
rkboard.rkusb.LOADERS = damaged
check("  a damaged loader: refused, not sent", refused(rkboard.erase, "rk3506b") == "rkboard.loader-damaged")
rkboard.rkusb.LOADERS = real_loaders

# The loader shows the SD card: no erase (ef would empty the card), writing an image to it instead.
unplug_all()
plug("1-1.2", "loader")
shows.update(mb=14910, sectors=8192)
card[:] = b"\xff" * (8192 * 512)
ran.clear()
check(
    "the loader shows a card (gigabytes): the erase refused, saying so, nothing erased",
    refused(rkboard.erase) == "rkboard.card-shown"
    and ("rkdeveloptool", "rfi") in ran
    and ("rkdeveloptool", "ef") not in ran,
    ran,
)
images = T / "state" / "rk-images"
images.mkdir(parents=True)
raw = os.urandom(150000) + b"tail"  # not a whole number of sectors
packed = lzma.compress(raw)
(images / "board.img.xz").write_bytes(packed)
digest = hashlib.sha256(packed).hexdigest()
(images / "board.img.xz.sha256").write_text(f"{digest}  board.img.xz\n")
(images / "linked.img").symlink_to(images / "board.img.xz")
listed = rkboard.rkusb.images()
check(
    "the images on the box listed for the page, with the sha256 beside each; a link left out",
    [(i["name"], i["sha256"]) for i in listed] == [("board.img.xz", digest)],
    listed,
)
rkboard.CHUNK = 64 << 10
rkboard.RUN = T / "run"
ran.clear()
check(
    "  a name with a path in it: refused",
    refused(rkboard.write_card, "../board.img.xz", digest) == "rkboard.image-name",
)
check("  a link: refused, not followed", refused(rkboard.write_card, "linked.img", digest) == "rkboard.image-missing")
check("  no sha256 given: refused", refused(rkboard.write_card, "board.img.xz", "") == "rkboard.image-sha")
check(
    "  a sha256 that does not match: refused, nothing written",
    refused(rkboard.write_card, "board.img.xz", "0" * 64) == "rkboard.image-damaged"
    and not any(c[:2] == ("rkdeveloptool", "wl") for c in ran),
)
shows.update(NAND)
check(
    "  the loader shows the onboard flash: refused, nothing written",
    refused(rkboard.write_card, "board.img.xz", digest) == "rkboard.not-card"
    and not any(c[:2] == ("rkdeveloptool", "wl") for c in ran),
)
shows.update(mb=14910, sectors=8192)
ran.clear()
said = hub_control.ACTIONS["rk-write"](
    {"action": "rk-write", "image": "board.img.xz", "sha256": digest, "loader": None}
)
padded = raw + bytes(-len(raw) % 512)
wl = [c for c in ran if c[:2] == ("rkdeveloptool", "wl")]
rl = [c for c in ran if c[:2] == ("rkdeveloptool", "rl")]
check(
    "the image written to the card, unpacked as it goes, in pieces each read back; the board reset",
    bytes(card[: len(padded)]) == padded
    and len(rl) == -(-len(padded) // rkboard.CHUNK)
    and ("rkdeveloptool", "rd") in ran
    and said["id"] == "rkboard.card-written",
    (said, len(wl), len(rl)),
)
check(
    "  the card's last sectors cleared (an older image's backup partition table), the space between left alone",
    bytes(card[-2048 * 512 :]) == bytes(2048 * 512) and card[len(padded)] == 0xFF,
)
check("  the pieces held in RAM removed after", not list((T / "run").iterdir()))
outcome["corrupt"] = True
check(
    "  a piece that does not read back the same: refused, saying where",
    refused(rkboard.write_card, "board.img.xz", digest) == "rkboard.verify-failed",
)
outcome["corrupt"] = False
shows.update(sectors=200)
card[:] = bytes(200 * 512)
check(
    "  an image larger than the card: refused",
    refused(rkboard.write_card, "board.img.xz", digest) == "rkboard.image-large",
)
shows.update(NAND)

# A missing tool.
del have["rkdeveloptool"]
unplug_all()
plug("1-1.2", "loader")
check("rkdeveloptool missing: refused, naming the toolkit", refused(rkboard.erase) == "rkboard.no-tool")
have["rkdeveloptool"] = "/usr/bin/rkdeveloptool"

# The hub's route and the menu entry.
src = (REPO / "irate_box/hub/server.py").read_text()
check(
    "the hub: /admin/rkboard sends loader and erase on, the loader id checked by pattern",
    '"action": "rk-loader"' in src
    and '"action": "rk-erase", "loader": loader' in src
    and '"action": "rk-write",' in src
    and '"image": payload["image"],' in src
    and '"action": "rk-serial-setup"' in src
    and 'CONTROL_DIR / "rk-secrets"' in src
    and "secret.unlink()" in src,
)
# The hub runs as an unprivileged user and cannot read irate_box/root (root's alone, 0700 on a box): what it shows
# comes from hub/rkusb.py. Found on a box: the page showed a blank card, the hub failing to import root's module.
route = src.split("def rkboard_snapshot")[1].split("\ndef ")[0]
check(
    "the hub's /admin/rkboard reads hub/rkusb.py and hub/rksetup.py, never irate_box.root (unreadable to the hub)",
    re.search(r"from irate_box\.hub import [\w, ]*\brkusb\b", route) is not None
    and re.search(r"from irate_box\.hub import [\w, ]*\brksetup\b", route) is not None
    and "irate_box.root" not in route.replace("not irate_box.root", "")
    and "irate_box.root" not in (REPO / "irate_box/hub/rksetup.py").read_text(),
    route,
)
check(
    "the menu entry is for administrators only",
    'if e.get("admin") and signed_in != "admin":' in src
    and json.loads((REPO / "apps.d/57-rockchip.json").read_text())["entries"][0]["admin"] is True
    and json.loads((REPO / "apps.d/57-rockchip.json").read_text())["entries"][0]["href"] == "/admin/rockchip.html"
    and '"/admin/rockchip.html"' in src
    and 'path = "/admin-rockchip.html"' in src,
)

# The real tool (the kit's rkdeveloptool 1.0.0) takes rl's length in bytes: "rl 0 512" read 512 bytes, "rl 64 4096"
# read back the 4 KiB written at sector 64, and a length that is not a multiple of 512 is refused. Found on a Lyra
# Zero W (2026-10-30), where an erased NAND then read as "data this box does not recognise".
card[:] = b"\xff" * (32 << 20)
ran.clear()
seen = rkboard.contents({"sectors": 523264})
rl = [c for c in ran if c[:2] == ("rkdeveloptool", "rl")]
check(
    "the flash read asks for 16 MiB in bytes, and an erased NAND (0xFF) is blank",
    rl and rl[0][3] == str(16 << 20) and seen["read"] and seen["blank"],
    (rl, seen),
)
card[:] = b"\xff" * 4096 + b"UBI#" + b"\xff" * 4096
check("  a flash with any data on it is not blank", not rkboard.contents({"sectors": 523264})["blank"])

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
