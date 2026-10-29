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
The two download modes share the RockUSB interface (ff/06/05). rkdeveloptool tells them apart by the low bit of
the device's USB version (bcdUSB): odd for Loader (2.01), even for Maskrom (2.00). That is not enough on its own: the
RK3506B's SPL loader, once sent from Maskrom, keeps 2.00 (and rkdeveloptool still says Maskrom), but it names itself
("RockChip", "USB-MSC", "rockchip"), and the boot ROM gives no names at all. So a RockUSB device that names itself
is in Loader mode too (seen on a Lyra Zero W, 2026-10-29). rkdeveloptool and adb come from the Rockchip boards
toolkit.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CODE = Path(os.environ.get("HUB_CODE_DIR", Path(__file__).resolve().parents[2]))
LOADERS = CODE / "rockchip" / "loaders"
# Images for a board's SD card, written through its loader (root's rkboard.write_card): <name>.img or .img.xz, and
# beside each its publisher's sha256 in <name>.sha256 when known.
IMAGES = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "rk-images"
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.img(?:\.xz)?$")
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
            named = any(_read(dev / name) for name in ("manufacturer", "product", "serial"))
            mode = "loader" if named or version.endswith(("1", "3", "5", "7", "9")) else "maskrom"
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


def images():
    """The images on the box for a board's card: [{name, size, sha256}], sha256 from its sidecar or ""."""
    out = []
    try:
        names = sorted(p.name for p in IMAGES.iterdir() if p.is_file() and not p.is_symlink())
    except OSError:
        return out
    for name in names:
        if not IMAGE_RE.match(name):
            continue
        side = _read(IMAGES / f"{name}.sha256").split()
        sha = side[0].lower() if side and re.fullmatch(r"[0-9a-fA-F]{64}", side[0]) else ""
        out.append({"name": name, "size": (IMAGES / name).stat().st_size, "sha256": sha})
    return out


def _root_disk():
    """The disk the box runs from (its root filesystem's), never offered as a card."""
    try:
        out = subprocess.run(  # noqa: TID251
            ["findmnt", "-n", "-o", "SOURCE", "/"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        parent = subprocess.run(  # noqa: TID251
            ["lsblk", "-no", "PKNAME", out], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return parent or out.rsplit("/", 1)[-1] or None


def cards(lsblk_json=None, root_disk=None):
    """SD cards in a USB reader on the box: removable USB disks with a medium in, never the box's own disk.
    [{name, path, size, model, serial, mounted}]; mounted when any of its partitions is."""
    if lsblk_json is None:
        try:
            lsblk_json = subprocess.run(  # noqa: TID251
                ["lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,RM,TRAN,SIZE,MODEL,SERIAL,MOUNTPOINT"],
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        root_disk = _root_disk()
    try:
        devices = json.loads(lsblk_json or "{}").get("blockdevices", [])
    except ValueError:
        return []

    def mounted(d):
        return bool(d.get("mountpoint")) or any(mounted(c) for c in d.get("children") or [])

    out = []
    for d in devices:
        removable = str(d.get("rm")).lower() in ("1", "true")
        if d.get("type") != "disk" or d.get("tran") != "usb" or not removable or d.get("name") == root_disk:
            continue
        size = int(d.get("size") or 0)
        if size <= 0:
            continue  # a reader with no card in
        out.append(
            {
                "name": d["name"],
                "path": d.get("path") or f"/dev/{d['name']}",
                "size": size,
                "model": (d.get("model") or "").strip(),
                "serial": (d.get("serial") or "").strip(),
                "mounted": mounted(d),
            }
        )
    return out


def tools():
    return {"rkdeveloptool": bool(shutil.which("rkdeveloptool")), "adb": bool(shutil.which("adb"))}


def status():
    return {"at": time.time(), "boards": boards(), "tools": tools(), "loaders": loaders()}
