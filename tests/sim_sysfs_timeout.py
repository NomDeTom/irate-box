# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The doctors' bounded sysfs reads: text when the file answers, the caller's default when it does
not or when the read is cut off (the Lyra's wedged USB bus, 2026-11-04). python3 tests/sim_sysfs_timeout.py"""

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


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def CP(cmd, code=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, code, out, err)


# A file that answers: its text, unchanged for the caller to strip.
put = SYS / "class/net/wlan0/operstate"
put.parent.mkdir(parents=True)
put.write_text("up\n")
check("a sysfs read that answers: its text", sysread.read(put, "?") == "up\n")
check("  a file that is not there: the caller's default", sysread.read(SYS / "gone", "?") == "?")

seen = []


def late(cmd, **kw):
    seen.append((cmd, kw.get("timeout")))
    return CP(cmd, 124, "", "timed out")


check("a read cut off at its deadline: the default", sysread.read(put, "?", run=late) == "?")
check(
    "  through timeout and cat, with the caller's deadline and a fallback kill",
    seen
    and seen[0][0][:4] == ["timeout", "-k", "1", "5"]
    and seen[0][0][4:] == ["cat", "--", str(put)]
    and seen[0][1] == 7,
    seen,
)


def raises(cmd, **kw):
    raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))


check("  a read that has to be abandoned: the default too", sysread.read(put, "?", run=raises) == "?")

# The Box doctor's sysfs helper takes the same bounded read.
real = sysread.read
sysread.read = lambda path, default="", timeout=5, run=None: "42\n"
check("the Box doctor's sysfs helper: the text, stripped", health._sysfs(put) == "42")
sysread.read = lambda path, default="", timeout=5, run=None: None
check("  a read with nothing: its default", health._sysfs(put, "-1") == "-1")

# Crash watch's _read sends /sys through the same deadline, and leaves /proc as it was.
asked = []


def bounded(path, default="", timeout=5, run=None):
    asked.append(str(path))
    return "bounded\n"


sysread.read = bounded
cw.SYS = SYS
check(
    "crash watch reads /sys through the deadline",
    cw._read(SYS / "x") == "bounded\n" and asked == [str(SYS / "x")],
    asked,
)
asked.clear()
(T / "proc").mkdir()
(T / "proc/uptime").write_text("12.0 1.0\n")
cw.PROC = T / "proc"
check("  and /proc directly, not through it", cw._read(T / "proc/uptime") == "12.0 1.0\n" and asked == [])
sysread.read = real

# netinv's read under /sys has its own deadline; other files are read as before.
real_run = netinv.subprocess.run
netinv.subprocess.run = lambda cmd, **kw: CP(cmd, 0, "up\n", "")
check("netinv's /sys read: bounded, stripped", netinv.read("/sys/class/net/wlan0/operstate") == "up")
netinv.subprocess.run = lambda cmd, **kw: CP(cmd, 124, "", "timed out")
check("  cut off: its default", netinv.read("/sys/class/net/wlan0/operstate", "?") == "?")
netinv.subprocess.run = real_run
check("  a file outside /sys still read directly", netinv.read(T / "etc" / "plain", "?") == "?")

print("\n" + ("OK" if not fails else f"{fails} FAILURES"))
sys.exit(1 if fails else 0)
