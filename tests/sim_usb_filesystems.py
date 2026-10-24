#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which filesystems a USB stick may have (usbstick.readable): what the kernel knows, a driver it can load, ntfs-3g for
NTFS; a stick it cannot read said so, not mounted; the box doctor names both lists."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="usbfs-"))
os.environ.update(HUB_PROC=str(T), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.root import health, usbstick

fails = 0


def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok


# A Lyra's kernel: ext2-4 and btrfs known; vfat, exfat, ntfs3 loadable; no isofs, xfs or f2fs; no ntfs-3g.
(T / "filesystems").write_text("nodev\tsysfs\nnodev\ttmpfs\n\text3\n\text2\n\text4\n\tsquashfs\n\tbtrfs\n")
loadable = {"vfat", "exfat", "ntfs3"}
real_run = usbstick.run
usbstick.run = lambda *cmd, timeout=60: subprocess.CompletedProcess(
    cmd, 0 if cmd[:3] == ("modprobe", "-n", "-q") and cmd[3] in loadable else 1, "", ""
)
usbstick.shutil = type("W", (), {"which": staticmethod(lambda name: None)})
can = usbstick.readable()
check(
    "what the kernel knows or can load: readable; the rest not",
    all(can[f] for f in ("vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext4", "btrfs"))
    and not any(can[f] for f in ("xfs", "f2fs", "iso9660")),
    can,
)
f = health.check_usb_filesystems()[0]
check(
    "the doctor names both, NTFS once, and what reads anywhere",
    f["summary"]["args"]["names"] == ["FAT", "exFAT", "NTFS", "ext2", "ext3", "ext4", "btrfs"]
    and f["notes"][0]["args"]["names"] == ["XFS", "F2FS", "ISO 9660 (a disc image)"]
    and f["fix"]["id"] == "usb-fs.fix",
    f,
)
loadable.discard("ntfs3")
usbstick.shutil = type("W", (), {"which": staticmethod(lambda name: "/usr/bin/ntfs-3g" if name == "ntfs-3g" else None)})
can = usbstick.readable()
check("NTFS through ntfs-3g where the kernel has no ntfs3", can["ntfs"] and not can["ntfs3"])
u = health.check_usb_filesystems()[0]
check(
    "  said once, as readable",
    "NTFS" in u["summary"]["args"]["names"] and not any("NTFS" in n for x in u["notes"] for n in x["args"]["names"]),
    u,
)
# No modprobe on the box: what the kernel knows is readable, the rest not, and the doctor still says so.
stubbed, path = usbstick.run, os.environ.get("PATH", "")
usbstick.run, os.environ["PATH"] = real_run, str(T / "no-bin")
can = usbstick.readable()
check(
    "no modprobe: the kernel's own filesystems readable, the rest not, no error",
    all(can[f] for f in ("ext2", "ext4", "btrfs")) and not any(can[f] for f in ("vfat", "exfat", "xfs")),
    can,
)
f = health.check_usb_filesystems()[0]
check("  the doctor's finding as usual", f["id"] == "usb-filesystems" and f["status"] == "ok", f)
usbstick.run, os.environ["PATH"] = stubbed, path
# A stick in a filesystem this box cannot read: said, and never mounted.
mounted = []
usbstick.devices = lambda: [
    {"name": "sda1", "path": "/dev/sda1", "label": "DISC", "size": 1, "fstype": "iso9660", "mountpoint": None}
]


class Never:
    def __init__(self, dev):
        mounted.append(dev)

    def __enter__(self):
        raise AssertionError("mounted")

    def __exit__(self, *a):
        return False


usbstick.Mounted = Never
s = usbstick.scan()["devices"][0]
check(
    "a stick it cannot read: why, in words, and not mounted",
    not mounted
    and s["error_message"]["id"] == "usb.fs.unsupported"
    and s["error_message"]["args"]["fs"].startswith("ISO 9660")
    and "FAT" in s["error_message"]["args"]["names"]
    and s["zims"] == [],
    s,
)
# The box writes only to a mount of its own. A drive mounted elsewhere has none of its options
# (nosymfollow, nosuid, nodev, noexec), and a folder on it could be a link that sends root's writes anywhere.
import importlib

os.environ["HUB_USB_MOUNTS"] = str(T / "mounts")
U = importlib.reload(usbstick)
ran = []
mount_rc = {"rc": 0}


def fake(*cmd, timeout=60):
    ran.append(list(cmd))
    return subprocess.CompletedProcess(
        cmd, mount_rc["rc"] if cmd[0] == "mount" else 0, "", "no such option" if cmd[0] == "mount" else ""
    )


U.run = fake
elsewhere = T / "media-owner"
elsewhere.mkdir()
dev = {"name": "sdb1", "path": "/dev/sdb1", "label": "STICK", "size": 1, "fstype": "vfat", "mountpoint": str(elsewhere)}
try:
    with U.Mounted(dev, writable=True):
        pass
    why = None
except U.Refused as exc:
    why = exc.message["id"]
check(
    "writing to a drive mounted elsewhere: refused, said to unmount it, nothing mounted",
    why == "usb.device.mounted-elsewhere" and not ran,
    (why, ran),
)
with U.Mounted(dev) as root:
    check("  reading it there: as before", root == elsewhere and not ran)
for name, call in (
    ("a book", lambda: U.export_zim("sdb1", "book", T / "zims")),
    ("a toolkit", lambda: U.export_kit("sdb1", "small")),
):
    U._find = lambda _a, _d: dev
    (T / "zims").mkdir(exist_ok=True)
    (T / "zims" / "book.zim").write_bytes(b"ZIM")
    try:
        call()
        why = None
    except (U.Refused, ValueError) as exc:
        why = getattr(exc, "message", {}).get("id")
    except Exception as exc:  # noqa: BLE001 - a refusal of another kind is reported, not raised
        why = type(exc).__name__
    check(
        f"  exporting {name} onto it: refused before anything is written there",
        why == "usb.device.mounted-elsewhere" and list(elsewhere.iterdir()) == [],
        (why, list(elsewhere.iterdir())),
    )
# Every export writes through the same boundary (the image's too, which refuses earlier here: no card to read).
src = (REPO / "irate_box" / "root" / "usbstick.py").read_text()
check(
    "  every export mounts the drive writable through Mounted, nowhere else",
    src.count("Mounted(dev, writable=True)") == 4
    and '"rw"' not in src.replace('("rw" if self.writable else "ro")', ""),
    src.count("Mounted(dev, writable=True)"),
)
own = dict(dev, mountpoint=None)
ran.clear()
with U.Mounted(own, writable=True) as root:
    pass
check(
    "a drive of no mount: the box mounts it itself, read-write with nosymfollow, nosuid, nodev and noexec, and unmounts it",
    ran[0][:3] == ["mount", "-o", "rw,nosuid,nodev,noexec,nosymfollow"]
    and ["umount", str(root)] in ran
    and not root.exists(),
    ran,
)
mount_rc["rc"] = 32
ran.clear()
try:
    with U.Mounted(own, writable=True):
        pass
    why = None
except U.Refused as exc:
    why = exc.message["id"]
check(
    "  the options refused (an old kernel): no mount at all, not one without them",
    why == "usb.device.mount-failed" and [c[0] for c in ran] == ["mount"] and not (T / "mounts" / "sdb1").exists(),
    (why, ran),
)
mount_rc["rc"] = 0
(T / "mounts").mkdir(exist_ok=True)
(T / "mounts" / "sdb1").symlink_to(T / "elsewhere-again")
ran.clear()
try:
    with U.Mounted(own, writable=True):
        pass
    why = None
except U.Refused as exc:
    why = exc.message["id"]
check(
    "  a link at the box's own mount point: refused, nothing mounted",
    why == "usb.device.mount-link" and not ran,
    (why, ran),
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
