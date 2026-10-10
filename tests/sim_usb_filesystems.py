#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which filesystems a USB stick may have (usbstick.readable): what the kernel knows, a driver it can load, ntfs-3g for
NTFS; a stick it cannot read said so, not mounted; the box doctor names both lists."""
import os, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="usbfs-"))
os.environ.update(HUB_PROC=str(T), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.root import usbstick, health  # noqa: E402

fails = 0
def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok

# A Lyra's kernel: ext2-4 and btrfs known; vfat, exfat, ntfs3 loadable; no isofs, xfs or f2fs; no ntfs-3g.
(T / "filesystems").write_text("nodev\tsysfs\nnodev\ttmpfs\n\text3\n\text2\n\text4\n\tsquashfs\n\tbtrfs\n")
loadable = {"vfat", "exfat", "ntfs3"}
usbstick.run = lambda *cmd, timeout=60: subprocess.CompletedProcess(cmd, 0 if cmd[:3] == ("modprobe", "-n", "-q") and cmd[3] in loadable else 1, "", "")
usbstick.shutil = type("W", (), {"which": staticmethod(lambda name: None)})
can = usbstick.readable()
check("what the kernel knows or can load: readable; the rest not", all(can[f] for f in ("vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext4", "btrfs"))
      and not any(can[f] for f in ("xfs", "f2fs", "iso9660")), can)
f = health.check_usb_filesystems()[0]
check("the doctor names both, NTFS once, and what reads anywhere", f["detail"] == "FAT, exFAT, NTFS, ext2, ext3, ext4, btrfs. Not XFS, F2FS, ISO 9660 "
      "(a disc image): this kernel has no driver for them." and "FAT or exFAT" in f["fix"], f)
loadable.discard("ntfs3")
usbstick.shutil = type("W", (), {"which": staticmethod(lambda name: "/usr/bin/ntfs-3g" if name == "ntfs-3g" else None)})
can = usbstick.readable()
check("NTFS through ntfs-3g where the kernel has no ntfs3", can["ntfs"] and not can["ntfs3"])
check("  said once, as readable", "NTFS" in health.check_usb_filesystems()[0]["detail"].split(". Not")[0]
      and "NTFS" not in health.check_usb_filesystems()[0]["detail"].split(". Not")[-1])
# A stick in a filesystem this box cannot read: said, and never mounted.
mounted = []
usbstick.devices = lambda: [{"name": "sda1", "path": "/dev/sda1", "label": "DISC", "size": 1, "fstype": "iso9660", "mountpoint": None}]
class Never:
    def __init__(self, dev): mounted.append(dev)
    def __enter__(self): raise AssertionError("mounted")
    def __exit__(self, *a): return False
usbstick.Mounted = Never
s = usbstick.scan()["devices"][0]
check("a stick it cannot read: why, in words, and not mounted", not mounted and "cannot read ISO 9660" in s["error"] and s["zims"] == [], s)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
