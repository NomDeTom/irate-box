# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards plugged into the box's USB, prepared from /admin: their onboard flash erased so that they start
from their SD card (the Luckfox Lyra Zero W, Lyra Plus and others mPWRD-OS supports). Root's half; the hub lists
the boards from sysfs itself (boards()) and asks the root helper to act.

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

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from irate_box.msg import M, Refused

CODE = Path(os.environ.get("HUB_CODE_DIR", Path(__file__).resolve().parents[2]))
LOADERS = CODE / "rockchip" / "loaders"
SYS_USB = Path(os.environ.get("HUB_SYS_USB", "/sys/bus/usb/devices"))
VENDOR = "2207"
ROCKUSB = ("ff", "06", "05")
ADB = ("ff", "42", "01")
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WAIT = 25  # seconds for a board to come back after a reboot into Loader mode, or after a loader is sent


def run(*cmd, timeout=600):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


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


def _one(modes):
    """The one board on USB, which must be in one of these modes; refused for none, or more than one."""
    found = boards()
    if not found:
        raise Refused(M("rkboard.none", "No Rockchip board is connected to the box's USB."))
    if len(found) > 1:
        raise Refused(M("rkboard.several", "{n} Rockchip boards are connected. Connect one at a time.", n=len(found)))
    if found[0]["mode"] not in modes:
        raise Refused(
            M("rkboard.wrong-mode", "The board is not in a mode for this step ({mode}).", mode=found[0]["mode"])
        )
    return found[0]


def _need(tool):
    if not tools()[tool]:
        raise Refused(
            M(
                "rkboard.no-tool",
                "{tool} is not installed. Install the Rockchip boards toolkit on Tools, Toolkits.",
                tool=tool,
            )
        )


def _wait_for(modes):
    for _ in range(WAIT):
        found = [b for b in boards() if b["mode"] in modes]
        if found:
            return found[0]
        time.sleep(1)
    return None


def to_loader():
    """A board running its own system restarted into Loader mode (adb reboot loader)."""
    board = _one(("system",))
    _need("adb")
    serial = board["serial"]
    r = run("adb", *(["-s", serial] if SERIAL_RE.match(serial) else []), "reboot", "loader", timeout=60)
    if r.returncode:
        raise Refused(
            M(
                "rkboard.adb-failed",
                "The board did not take the request: {detail}",
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )
    if not _wait_for(("loader",)):
        raise Refused(
            M(
                "rkboard.no-loader",
                "The board restarted but did not come back in Loader mode. Unplug it, hold its boot button, "
                "and connect it again (Maskrom mode).",
            )
        )
    return M("rkboard.in-loader", "The board is in Loader mode, ready to erase.")


def _send_loader(loader_id):
    loader = next((x for x in loaders() if x["id"] == loader_id), None)
    if not loader:
        raise Refused(M("rkboard.loader-unknown", "Select the board's chip, so that the box sends the right loader."))
    path = LOADERS / loader["file"]
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    if digest != loader["sha256"]:
        raise Refused(M("rkboard.loader-damaged", "The loader for {chip} is damaged or missing.", chip=loader["chip"]))
    r = run("rkdeveloptool", "db", str(path), timeout=120)
    if r.returncode or not _wait_for(("loader",)):
        raise Refused(
            M(
                "rkboard.loader-failed",
                "The board did not start the {chip} loader. Check that the board has that chip. {detail}",
                chip=loader["chip"],
                detail=(r.stderr or r.stdout).strip()[-160:],
            )
        )


def erase(loader_id=None):
    """The board's onboard flash erased (rkdeveloptool ef), then the board reset. A board in Maskrom mode is sent
    the loader for its chip first."""
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    r = run("rkdeveloptool", "ef", timeout=900)
    if r.returncode:
        raise Refused(
            M("rkboard.erase-failed", "The erase failed: {detail}", detail=(r.stderr or r.stdout).strip()[-200:])
        )
    run("rkdeveloptool", "rd", timeout=60)
    return M(
        "rkboard.erased",
        "The board's onboard flash is erased. Unplug the board, put its SD card in, and power it: it starts from the card.",
    )
