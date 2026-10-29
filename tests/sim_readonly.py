# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The read-only system (root/readonly.py), offline: the Lyra's overlayroot config and apt hooks, the Pi's
raspi-config switch, the setting's choices, and switching both off without leaving a mark.
python3 tests/sim_readonly.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="readonly-"))
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_OVERLAYROOT_CONF=str(T / "overlayroot.conf"),
    HUB_READONLY_HOOK=str(T / "apt.conf"),
    HUB_DEVICE_TREE=str(T / "dt"),
)
(T / "dt").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import readonly

calls = []
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def run(*cmd, timeout=600):
    calls.append(tuple(cmd))
    return subprocess.CompletedProcess(cmd, 0, "", "")


readonly.run = run
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
said = readonly.enable(True)
check(
    "the Lyra: overlayroot's tmpfs config, the apt hooks, the mark and a new initramfs",
    readonly.enabled()
    and 'overlayroot="tmpfs"' in readonly.CONF.read_text()
    and "DPkg::Pre-Invoke" in readonly.HOOK.read_text()
    and ("update-initramfs", "-u") in calls
    and said["id"] == "readonly.on",
    (said, calls),
)
check("  the setting shows it on", readonly.groups()[0]["current"] == "on")
said = readonly.enable(False)
check(
    "  off removes the config, hooks and mark",
    not readonly.enabled()
    and not readonly.CONF.exists()
    and not readonly.HOOK.exists()
    and said["id"] == "readonly.off",
    (said, calls),
)
(T / "dt" / "model").write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
calls.clear()
said = readonly.enable(True)
check(
    "the Pi: raspi-config's overlay switch, with the hooks and mark",
    ("raspi-config", "nonint", "enable_overlayfs") in calls
    and readonly.enabled()
    and readonly.HOOK.is_file()
    and said["id"] == "readonly.on",
    calls,
)
readonly.enable(False)
check("  off calls the Pi's switch off", ("raspi-config", "nonint", "disable_overlayfs") in calls, calls)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
