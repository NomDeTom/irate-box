# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards on the box's USB, root's half: a board running its own system switched to Loader mode, a board's
onboard flash erased (in Maskrom mode, the loader for its chip sent first). The boards, their modes, the tools and
the loaders are read by hub/rkusb.py, which the hub uses too (see there for the modes).
"""

import hashlib
import re
import subprocess
import time

from irate_box.hub import rkusb
from irate_box.hub.rkusb import boards, loaders, status, tools  # noqa: F401 - the root helper's names for them
from irate_box.msg import M, Refused

SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WAIT = 25  # seconds for a board to come back after a reboot into Loader mode, or after a loader is sent


def run(*cmd, timeout=600):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


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
    path = rkusb.LOADERS / loader["file"]
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
