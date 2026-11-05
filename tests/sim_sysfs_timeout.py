# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The doctors' USB sysfs reads: text when the file answers, the caller's default when it is cut off,
no fork for the files that never touch USB, and no second attempt at a path that timed out (the
Lyra's wedged USB bus, 2026-11-04). python3 tests/sim_sysfs_timeout.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="sysfs-timeout-"))
SYS = T / "sys"
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_SYSFS=str(SYS))
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv
from irate_box.root import crashwatch as cw
from irate_box.root import health, sysread

fails = 0
USB = "/sys/bus/usb/devices/1-1.1/uevent"


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def CP(cmd, code=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, code, out, err)


# The USB tree is recognised; thermal and other sysfs are not.
check(
    "the USB tree is the bounded one, and thermal is not",
    sysread._usb_path(USB)
    and sysread._usb_path("/sys/kernel/debug/usb/devices")
    and not sysread._usb_path("/sys/class/thermal/thermal_zone0/temp")
    and not sysread._usb_path("/proc/uptime"),
)

# A file outside USB is read directly, with no process at all.
put = SYS / "class/net/wlan0/operstate"
put.parent.mkdir(parents=True)
put.write_text("up\n")


def never(cmd, **kw):
    raise AssertionError("a non-USB read started a process")


check("a non-USB file: read directly, no fork", sysread.read(put, "?", run=never) == "up\n")
check("  a file that is not there: the caller's default", sysread.read(SYS / "gone", "?", run=never) == "?")

# A USB read that answers.
sysread.forget()
seen = []


def answers(cmd, **kw):
    seen.append((cmd, kw.get("timeout")))
    return CP(cmd, 0, "usb\n", "")


check("a USB read that answers: its text", sysread.read(USB, "?", run=answers) == "usb\n")
check(
    "  through timeout and cat, with the caller's deadline and a fallback kill",
    seen and seen[0][0][:4] == ["timeout", "-k", "1", "5"] and seen[0][0][4:] == ["cat", "--", USB],
    seen,
)

# A cut-off USB read is remembered, and not tried again in this process.
sysread.forget()
count = []


def late(cmd, **kw):
    count.append(cmd)
    return CP(cmd, 124, "", "timed out")


check("a USB read cut off at its deadline: the default", sysread.read(USB, "?", run=late) == "?")
check("  the same path again: the default, with no second process", sysread.read(USB, "?", run=late) == "?")
check("  one process for the two reads", len(count) == 1, count)
sysread.forget()


def raises(cmd, **kw):
    raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))


check("  a read that has to be abandoned: the default too", sysread.read(USB, "?", run=raises) == "?")
check(
    "  and it is remembered like a timeout",
    sysread.read(USB, "?", run=late) == "?" and len(count) == 1,
    count,
)
sysread.forget()

# The Box doctor's sysfs helper takes the same read.
real = sysread.read
sysread.read = lambda path, default="", timeout=5, run=None: "42\n"
check("the Box doctor's sysfs helper: the text, stripped", health._sysfs(put) == "42")
sysread.read = lambda path, default="", timeout=5, run=None: None
check("  a read with nothing: its default", health._sysfs(put, "-1") == "-1")

# Crash watch's _read sends /sys through the same reader, and leaves /proc as it was.
asked = []


def bounded(path, default="", timeout=5, run=None):
    asked.append(str(path))
    return "bounded\n"


sysread.read = bounded
cw.SYS = SYS
check(
    "crash watch reads /sys through the reader",
    cw._read(SYS / "x") == "bounded\n" and asked == [str(SYS / "x")],
    asked,
)
asked.clear()
(T / "proc").mkdir()
(T / "proc/uptime").write_text("12.0 1.0\n")
cw.PROC = T / "proc"
check("  and /proc directly, not through it", cw._read(T / "proc/uptime") == "12.0 1.0\n" and asked == [])
sysread.read = real

# netinv's /sys read goes through the same reader; a file outside /sys is read directly.
real_read = sysread.read
sysread.read = lambda path, default="", timeout=5, run=None: "up\n"
check("netinv's /sys read: through the reader, stripped", netinv.read("/sys/class/net/wlan0/operstate") == "up")
sysread.read = real_read
check("  a file outside /sys still read directly", netinv.read(T / "etc" / "plain", "?") == "?")

print("\n" + ("OK" if not fails else f"{fails} FAILURES"))
sys.exit(1 if fails else 0)
