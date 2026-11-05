# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A USB sysfs read with a deadline, for the doctors.

A read under the USB tree can wait for ever when the bus behind it has wedged (the Lyra's host bus,
2026-11-04): udev, lsusb and a reader all wait together. Python's own file read has no timeout, so a
USB read runs through the system's timeout command. Other sysfs files (thermal, power_supply) never
touch that bus and are read directly: forking `cat` for each would cost a process start every pass on
a 512 MB board. A path that timed out once is not tried again in this boot: its `cat` may be stuck in
an uninterruptible wait that no kill can end, and each pass would leave another behind.

Stdlib only.
"""

import os
import re
import subprocess
from pathlib import Path

TIMEOUT = 5  # seconds a USB sysfs read may take
KILL_AFTER = 1  # seconds between timeout's TERM and KILL
USB_ROOTS = ("/sys/bus/usb", "/sys/kernel/debug/usb")
USB_SEGMENT = re.compile(r"/usb\d*(?:/|\Z)")
_TIMED_OUT = set()  # paths whose read was cut off in this process (one boot's worth)


def _usb_path(path):
    """True for the USB tree, and for a path whose resolved sysfs place is under a USB device."""
    text = str(path)
    if text.startswith(USB_ROOTS):
        return True
    if not text.startswith("/sys/"):
        return False
    try:
        real = os.path.realpath(text)
    except OSError:
        return False
    return real.startswith(USB_ROOTS) or USB_SEGMENT.search(real) is not None


def read(path, default="", timeout=TIMEOUT, run=subprocess.run):  # noqa: TID251 - the one wrapper
    """The file's text, or default when it is missing or does not answer within `timeout` seconds.
    Only a path in the USB tree is bounded; everything else is read directly."""
    if not _usb_path(path):
        try:
            return Path(path).read_text()
        except OSError:
            return default
    key = str(path)
    if key in _TIMED_OUT:
        return default
    try:
        done = run(
            ["timeout", "-k", str(KILL_AFTER), str(timeout), "cat", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout + KILL_AFTER + 1,
        )
    except (OSError, subprocess.SubprocessError):
        _TIMED_OUT.add(key)
        return default
    if done.returncode in (124, 137):
        _TIMED_OUT.add(key)
        return default
    return done.stdout if done.returncode == 0 and done.stdout is not None else default


def forget():
    """A new boot (or a test): a path that timed out before may be tried again."""
    _TIMED_OUT.clear()
