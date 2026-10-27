# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards plugged into the box's USB, as sysfs shows them: the half the hub reads itself (it cannot import
from irate_box/root, which is root's alone), and root's rkboard.py builds on to act (switch a board to Loader mode,
erase its flash).

A board shows itself on USB in one of three ways (vendor 0x2207):
  system    running its own system with Rockchip's USB gadget (Luckfox's factory image: RNDIS and ADB, 2207:0013):
            `adb reboot loader` brings it back in Loader mode, no button needed
  loader    Rockchip's download mode from a loader on the board (a U-Boot's rockusb, "USB download gadget",
            2207:350f on the RK3506): ready to erase
  maskrom   only the chip's boot ROM (the boot button held while plugging in): a loader is sent first
            (rockchip/loaders/, by the board's chip), then it is in Loader mode
The two download modes share the RockUSB interface (ff/06/05); rkdeveloptool tells them apart by the low bit of
the device's USB version (bcdUSB): odd for Loader (2.01), even for Maskrom (2.00). rkdeveloptool and adb come from
the Rockchip boards toolkit.
"""

import json
import os
import shutil
import time
from pathlib import Path

CODE = Path(os.environ.get("HUB_CODE_DIR", Path(__file__).resolve().parents[2]))
LOADERS = CODE / "rockchip" / "loaders"
SYS_USB = Path(os.environ.get("HUB_SYS_USB", "/sys/bus/usb/devices"))
VENDOR = "2207"
ROCKUSB = ("ff", "06", "05")
ADB = ("ff", "42", "01")


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def loaders():
    """[{id, chip, file, sha256, boards}] from rockchip/loaders/loaders.json; [] when it cannot be read."""
    try:
        return json.loads((LOADERS / "loaders.json").read_text()).get("loaders", [])
    except (OSError, ValueError):
        return []


def boards():
    """Every Rockchip device on the box's USB: {path, id, product, serial, mode}, mode as in this module's notes
    ("other" for a Rockchip device that is none of them)."""
    out = []
    for dev in sorted(SYS_USB.glob("*")):
        if ":" in dev.name or _read(dev / "idVendor") != VENDOR:
            continue
        ifaces = {
            (_read(i / "bInterfaceClass"), _read(i / "bInterfaceSubClass"), _read(i / "bInterfaceProtocol"))
            for i in dev.glob(f"{dev.name}:*")
        }
        if ROCKUSB in ifaces:
            version = _read(dev / "version").replace(" ", "")
            mode = "loader" if version.endswith(("1", "3", "5", "7", "9")) else "maskrom"
        elif ADB in ifaces:
            mode = "system"
        else:
            mode = "other"
        out.append(
            {
                "path": dev.name,
                "id": f"{VENDOR}:{_read(dev / 'idProduct')}",
                "product": _read(dev / "product"),
                "serial": _read(dev / "serial"),
                "mode": mode,
            }
        )
    return out


def tools():
    return {"rkdeveloptool": bool(shutil.which("rkdeveloptool")), "adb": bool(shutil.which("adb"))}


def status():
    return {"at": time.time(), "boards": boards(), "tools": tools(), "loaders": loaders()}
