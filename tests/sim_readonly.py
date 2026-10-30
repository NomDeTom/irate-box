# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The read-only system (root/readonly.py), offline: overlayroot installed or not on the Lyra, the Pi's
raspi-config and its package check, the setting's choices, every failure leaving the board as it was, and the
lower root written when the overlay is already live.
python3 tests/sim_readonly.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="readonly-"))
(T / "dt").mkdir()
MOUNTS = T / "mounts"
MOUNTS.write_text("/dev/root / ext4 rw,relatime 0 0\n")
NAND = T / "nand-logs.conf"
NAND.write_text("state=1\n")
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_OVERLAYROOT_CONF=str(T / "overlayroot.conf"),
    HUB_READONLY_HOOK=str(T / "apt.conf"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_READONLY_MOUNTS=str(MOUNTS),
    HUB_NAND_STATE=str(NAND),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import readonly

calls = []
fails = 0
state = {"installed": True, "apt_ok": True, "initramfs_ok": True, "raspi_ok": True, "chroot_ok": True}


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def run(*cmd, timeout=600):
    calls.append(tuple(cmd))
    ok = 0
    if cmd[:2] == ("dpkg", "-s"):
        # installed: True (every package), False (none), or the set of package names installed.
        have = state["installed"]
        ok = 0 if have is True or (isinstance(have, set) and cmd[2] in have) else 1
    elif cmd[:2] == ("apt-get", "update"):
        ok = 0 if state["apt_ok"] else 100
    elif cmd[:1] == ("env",) and "apt-get" in cmd:
        ok = 0 if state["apt_ok"] else 100
        if state["apt_ok"] and state["installed"] is not True:
            have = set() if state["installed"] is False else set(state["installed"])
            state["installed"] = have | {name for name in cmd if name in readonly.LYRA_PACKAGES}
    elif cmd[:1] == ("update-initramfs",):
        ok = 0 if state["initramfs_ok"] else 1
    elif cmd[:2] == ("raspi-config", "nonint"):
        ok = 0 if state["raspi_ok"] else 1
    elif cmd[:1] == ("overlayroot-chroot",):
        # The real one ends in chroot "$@": the first argument must be a program, not -c.
        ok = 0 if state["chroot_ok"] and len(cmd) > 1 and cmd[1] != "-c" else 1
    return subprocess.CompletedProcess(cmd, ok, "", "" if ok else "it failed")


def reset(**changes):
    calls.clear()
    readonly.HOOK.unlink(missing_ok=True)
    readonly.MARK.unlink(missing_ok=True)
    readonly.CONF.unlink(missing_ok=True)
    state.update({"installed": True, "apt_ok": True, "initramfs_ok": True, "raspi_ok": True, "chroot_ok": True})
    state.update(changes)
    MOUNTS.write_text("/dev/root / ext4 rw,relatime 0 0\n")
    NAND.write_text("state=1\n")


def refused(fn, *args):
    try:
        fn(*args)
    except Refused as exc:
        return exc.message["id"]
    return None


readonly.run = run
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")

reset()
said = readonly.enable(True)
check(
    "the Lyra: overlayroot's tmpfs config, a new initramfs, the mark, and no apt hook",
    readonly.enabled()
    and 'overlayroot="tmpfs"' in readonly.CONF.read_text()
    and ("update-initramfs", "-u") in calls
    and not readonly.HOOK.exists()
    and said["id"] == "readonly.on"
    and "after the next restart" in said["en"],
    (said, calls),
)
reset()
NAND.write_text("state=0\n")
said = readonly.enable(True)
check(
    "state on the card: the message says plainly that settings and accounts changed later are lost",
    said["id"] == "readonly.on-card" and "settings and accounts" in said["en"] and "do not survive" in said["en"],
    said,
)
reset()
said = readonly.enable(True)
setting = readonly.groups()[0]
check(
    "  the setting is on, the confirmation is on Read-only, and its words say what is lost",
    setting["current"] == "on"
    and "confirm" in setting["options"][0]
    and "confirm" not in setting["options"][1]
    and "RAM" in setting["desc"]["en"]
    and "/boot" in setting["desc"]["en"]
    and "updates" in setting["desc"]["en"],
    setting,
)
said = readonly.enable(False)
check(
    "  off removes the config and mark, and says it takes the next restart",
    not readonly.enabled()
    and not readonly.CONF.exists()
    and not readonly.HOOK.exists()
    and said["id"] == "readonly.off"
    and "after the next restart" in said["en"],
    (said, calls),
)

reset(installed=False, apt_ok=True)
said = readonly.enable(True)
check(
    "no overlayroot installed: it is installed first, then the config and mark",
    any(c[:2] == ("apt-get", "update") for c in calls)
    and any(c[:1] == ("env",) and "DEBIAN_FRONTEND=noninteractive" in c and "overlayroot" in c for c in calls)
    and readonly.enabled()
    and 'overlayroot="tmpfs"' in readonly.CONF.read_text(),
    calls,
)

# Found on a Lyra (2026-10-30): with overlayroot but no BusyBox, the initramfs had no grep, and overlayroot.log said
# "loaded 'overlay' module but no 'overlay' in /proc/filesystems"; the card stayed read-write.
reset(installed={"overlayroot"}, apt_ok=True)
readonly.enable(True)
installs = [c for c in calls if c[:1] == ("env",) and "apt-get" in c]
check(
    "  overlayroot there but no BusyBox (mPWRD-OS ships none): BusyBox is installed, and only it",
    len(installs) == 1 and "busybox" in installs[0] and "overlayroot" not in installs[0] and readonly.enabled(),
    installs,
)
reset(installed=False, apt_ok=True)
readonly.enable(True)
installs = [c for c in calls if c[:1] == ("env",) and "apt-get" in c]
check(
    "  neither there: both installed in one run",
    len(installs) == 1 and "busybox" in installs[0] and "overlayroot" in installs[0],
    installs,
)

reset(installed=False, apt_ok=False)
check(
    "  and when it cannot be installed: refused, with no mark and no config",
    refused(readonly.enable, True) == "readonly.lyra-update-failed"
    and not readonly.enabled()
    and not readonly.CONF.exists(),
    calls,
)

reset(initramfs_ok=False)
check(
    "update-initramfs fails: the config it wrote is removed, and no mark is written",
    refused(readonly.enable, True) == "readonly.failed" and not readonly.CONF.exists() and not readonly.enabled(),
    calls,
)

reset()
readonly.enable(True)
calls.clear()
state["initramfs_ok"] = False
check(
    "off fails: the config and mark stay, so the setting still shows what the next boot will do",
    refused(readonly.enable, False) == "readonly.off-failed" and readonly.CONF.exists() and readonly.enabled(),
    calls,
)

reset()
(T / "dt" / "model").write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
said = readonly.enable(True)
check(
    "the Pi: raspi-config's switch, the package check, the mark, and no apt hook",
    ("raspi-config", "nonint", "enable_overlayfs") in calls
    and ("dpkg", "-s", "overlayroot") in calls
    and readonly.enabled()
    and not readonly.HOOK.exists()
    and said["id"] == "readonly.on",
    calls,
)
reset(raspi_ok=False)
check(
    "  raspi-config fails: refused, and nothing is recorded",
    refused(readonly.enable, True) == "readonly.failed" and not readonly.enabled(),
    calls,
)
reset(installed=False)
check(
    "  raspi-config succeeds without overlayroot: refused, the switch put back, no mark",
    refused(readonly.enable, True) == "readonly.pi-not-installed"
    and ("raspi-config", "nonint", "disable_overlayfs") in calls
    and not readonly.enabled(),
    calls,
)
reset()
readonly.enable(True)
calls.clear()
state["raspi_ok"] = False
check(
    "  off fails: the mark stays",
    refused(readonly.enable, False) == "readonly.off-failed" and readonly.enabled(),
    calls,
)

(T / "dt" / "model").write_bytes(b"Some Other Board\0")
reset()
check(
    "another board: no setting, and switching it on is refused",
    readonly.groups() == [] and refused(readonly.enable, True) == "readonly.unsupported",
)

(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
reset()
readonly.enable(True)
MOUNTS.write_text("/dev/root / ext4 rw,relatime 0 0\noverlay / overlay rw,relatime 0 0\n")
calls.clear()
readonly.enable(False)
check(
    "the overlay already live: off removes the files from the lower root through overlayroot-chroot too",
    not readonly.enabled() and not readonly.CONF.exists() and any(c[:1] == ("overlayroot-chroot",) for c in calls),
    calls,
)
reset()
readonly.enable(True)
MOUNTS.write_text("/dev/root / ext4 rw,relatime 0 0\noverlay / overlay rw,relatime 0 0\n")
calls.clear()
state["chroot_ok"] = False
check(
    "  and when that fails, nothing in the live root is removed",
    refused(readonly.enable, False) == "readonly.off-failed" and readonly.enabled(),
    calls,
)
(T / "dt" / "model").write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
reset()
readonly.enable(True)
MOUNTS.write_text("/dev/root / ext4 rw,relatime 0 0\noverlay / overlay rw,relatime 0 0\n")
calls.clear()
readonly.enable(False)
check(
    "the Pi with the overlay live: off removes the mark from the lower root through overlayroot-chroot",
    not readonly.enabled() and any(c[:1] == ("overlayroot-chroot",) and c[1] == "sh" for c in calls),
    calls,
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
