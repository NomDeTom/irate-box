# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A sysfs or USB read with a deadline, for the doctors.

A read under /sys can wait for ever when the bus behind it has wedged (the Lyra's USB host bus,
2026-11-04): udev, lsusb and a reader all wait together. Python's own file read has no timeout, so
the read runs through the system's timeout command; one that is late returns the caller's default
instead of holding the doctor open. Stdlib only.
"""

import subprocess

TIMEOUT = 5  # seconds a sysfs read may take
KILL_AFTER = 1  # seconds between timeout's TERM and KILL


def read(path, default="", timeout=TIMEOUT, run=subprocess.run):  # noqa: TID251 - this is the one wrapper
    """The file's text, or default when it is missing or does not answer within `timeout` seconds."""
    try:
        done = run(
            ["timeout", "-k", str(KILL_AFTER), str(timeout), "cat", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout + KILL_AFTER + 1,
        )
    except (OSError, subprocess.SubprocessError):
        return default
    return done.stdout if done.returncode == 0 and done.stdout is not None else default
