# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Stage 6's card layout: the partitions the plan names, written to a card in a reader or an image file.

The GPT's first partition starts at 16 MiB, as Armbian's does, so the bootloader area before it is left alone. The
partitions are bootstate (raw, or FAT for tryboot), the system slot or slots, state and content. A card or image can
be checked without writing; the write refuses the box's own card, a card too small, or a target it cannot lay out.
Stdlib only.
"""

import os
import re
import shutil
import stat
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import tempfile
from pathlib import Path

from irate_box.msg import M, Refused

MIB = 1 << 20
FIRST = 16 * MIB
BOOTSTATE = 16 * MIB
STATE = 1 << 30
SYSTEM = {"lyra": 4 << 30, "pi": 8 << 30}


def run(*cmd, timeout=3600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def plan(size, board="lyra", filesystem="ext4", ab="off"):
    """The partitions, in bytes: bootstate, one or two system slots, state, and content to the end."""
    slots = 2 if ab != "off" else 1
    system = SYSTEM.get(board, SYSTEM["lyra"])
    need = FIRST + BOOTSTATE + slots * system + STATE
    if size < need:
        raise Refused(
            M(
                "layout.small",
                "The card is {gb} GB. This layout needs {need} GB.",
                gb=f"{size / 2**30:.1f}",
                need=f"{need / 2**30:.1f}",
            )
        )
    out = [
        {
            "name": "irate-bootstate",
            "start": FIRST,
            "size": BOOTSTATE,
            "filesystem": "vfat" if ab == "tryboot" else "raw",
        }
    ]
    for i in range(slots):
        out.append(
            {
                "name": f"irate-system-{chr(ord('a') + i)}",
                "start": out[-1]["start"] + out[-1]["size"],
                "size": system,
                "filesystem": "ext4",
            }
        )
    out.append(
        {
            "name": "irate-state",
            "start": out[-1]["start"] + out[-1]["size"],
            "size": STATE,
            "filesystem": "btrfs" if "btrfs" in filesystem else "ext4",
        }
    )
    out.append(
        {
            "name": "irate-content",
            "start": out[-1]["start"] + out[-1]["size"],
            "size": size - (out[-1]["start"] + out[-1]["size"]),
            "filesystem": "f2fs" if "f2fs" in filesystem else "ext4",
        }
    )
    return out


def _disk(path):
    said = run("lsblk", "-n", "-d", "-o", "PKNAME", str(path), timeout=30)
    return said.stdout.strip() or Path(path).name


def _root_disk():
    said = run("findmnt", "-n", "-o", "SOURCE", "/", timeout=30)
    source = said.stdout.strip()
    return _disk(source) if source else ""


def _own(path, own_device=None):
    """The running card's disk and any partition of it; own_device may name it for a test."""
    try:
        if stat.S_ISBLK(os.stat(path).st_mode):
            if own_device and os.path.exists(own_device):
                return _disk(path) == _disk(own_device)
            root = _root_disk()
            return bool(root) and _disk(path) == root
    except OSError:
        return False
    return False


def _mounted(path):
    said = run("lsblk", "-n", "-o", "MOUNTPOINTS", str(path), timeout=30)
    return any(line.strip() for line in said.stdout.splitlines())


def _sgdisk(path, parts):
    commands = [["sgdisk", "--clear", "-a", "1", str(path)]]
    for index, part in enumerate(parts, 1):
        size = f"+{part['size'] // 512}" if part["name"] != "irate-content" else "0"
        code = "0700" if part["filesystem"] == "vfat" else "8300"
        command = [
            "sgdisk",
            f"--new={index}:{part['start'] // 512}:{size}",
            f"--change-name={index}:{part['name']}",
            f"--typecode={index}:{code}",
        ]
        if part["name"] == "irate-system-a":
            # The legacy BIOS bootable attribute (bit 2): U-Boot's boot scan looks only at bootable partitions when
            # one is, and the first partition here is a raw bootstate it cannot read (found on a Lyra, 2026-10-30).
            command.append(f"--attributes={index}:set:2")
        commands.append(command + [str(path)])
    return commands


def _part(device, index):
    """mmcblk0p2 and loop0p2; sda2 has no p."""
    return f"{device}p{index}" if device[-1:].isdigit() else f"{device}{index}"


def _mkfs(device, part):
    label = part["name"]
    fs = part["filesystem"]
    if fs == "raw":
        return None
    if fs == "vfat":
        return ["mkfs.vfat", "-F", "16", "-n", label, device]
    if fs == "btrfs":
        return ["mkfs.btrfs", "-f", "-L", label, device]
    if fs == "f2fs":
        return ["mkfs.f2fs", "-f", "-l", label, device]
    return ["mkfs.ext4", "-F", "-L", label, device]


def write(path, board="lyra", filesystem="ext4", ab="off", source=None, own_device=None, run=run):
    """Lay out path (a card device or an image file). Returns the read-back and the commands used."""
    path = Path(path)
    if not path.exists():
        raise Refused(M("layout.missing", "{path} is not there.", path=str(path)))
    block = stat.S_ISBLK(path.stat().st_mode)
    if block:
        size = int(run("blockdev", "--getsize64", str(path)).stdout.strip() or 0)
    else:
        size = path.stat().st_size
    if _own(path, own_device or os.environ.get("HUB_OWN_DEVICE")):
        raise Refused(M("layout.own", "That is the box's own card. Use a card in a reader or an image file."))
    if _mounted(path):
        raise Refused(M("layout.mounted", "That card has a mounted partition. Unmount it, or use another card."))
    parts = plan(size, board, filesystem, ab)
    if block:
        device = str(path)
        loop = None
    else:
        loop = run("losetup", "--find", "--show", "--partscan", str(path))
        if loop.returncode:
            raise Refused(
                M(
                    "layout.loop",
                    "The image could not be opened as a loop device: {why}",
                    why=loop.stderr.strip()[-200:],
                )
            )
        device = loop.stdout.strip()
    used = []
    try:
        for command in _sgdisk(path, parts):
            used.append(command)
            result = run(*command)
            if result.returncode:
                raise Refused(
                    M("layout.sgdisk", "The partition table was not written: {why}", why=result.stderr.strip()[-300:])
                )
        run("partprobe", device)
        run("udevadm", "settle")
        for index, part in enumerate(parts, 1):
            device_part = _part(device if loop else str(path), index)
            mkfs = _mkfs(device_part, part)
            if not mkfs:
                # A partition left raw keeps whatever the card held there: a card laid out over an image read
                # back its old root's ext4 signature and UUID in the bootstate (2026-10-29). Wipe the signatures.
                wipe = ["wipefs", "-a", device_part]
                used.append(wipe)
                result = run(*wipe)
                if result.returncode:
                    raise Refused(
                        M(
                            "layout.wipe",
                            "{name} could not be cleared of an old filesystem's signature: {why}",
                            name=part["name"],
                            why=result.stderr.strip()[-300:],
                        )
                    )
            if mkfs:
                used.append(mkfs)
                result = run(*mkfs)
                if result.returncode:
                    raise Refused(
                        M(
                            "layout.mkfs",
                            "{name} was not made: {why}",
                            name=part["name"],
                            why=result.stderr.strip()[-300:],
                        )
                    )
            if source and part["name"].startswith("irate-system-"):
                mnt = tempfile.mkdtemp(prefix="layout-mnt-")
                try:
                    used.append(["mount", device_part, mnt])
                    result = run("mount", device_part, mnt)
                    if result.returncode:
                        raise Refused(
                            M(
                                "layout.mount",
                                "{name} was not mounted: {why}",
                                name=part["name"],
                                why=result.stderr.strip()[-300:],
                            )
                        )
                    used.append(["rsync", "-aHAX", "-x", "--numeric-ids", "--delete", f"{source}/", f"{mnt}/"])
                    result = run(
                        "rsync",
                        "-aHAX",
                        "-x",
                        "--numeric-ids",
                        "--delete",
                        "--exclude=/proc",
                        "--exclude=/sys",
                        "--exclude=/dev",
                        "--exclude=/run",
                        "--exclude=/tmp",
                        "--exclude=/var/log.nand",
                        "--exclude=/mnt",
                        "--exclude=/media",
                        "--exclude=/var/lib/hub",
                        "--exclude=/etc/fstab",
                        f"{source}/",
                        f"{mnt}/",
                    )
                    if result.returncode:
                        raise Refused(
                            M("layout.copy", "The system was not copied: {why}", why=result.stderr.strip()[-300:])
                        )
                finally:
                    unmounted = run("umount", mnt)
                    if unmounted.returncode == 0:
                        shutil.rmtree(mnt, ignore_errors=True)
                    else:
                        raise Refused(
                            M(
                                "layout.umount",
                                "The copy could not be unmounted: {why}",
                                why=unmounted.stderr.strip()[-200:],
                            )
                        )
        table = run("sgdisk", "-p", str(path))
        read_back = run("blkid", *[_part(device, i) for i in range(1, len(parts) + 1)], timeout=120)
        rows = {}
        for line in table.stdout.splitlines():
            found = re.match(r"^\s*(\d+)\s+(\d+)\s+\d+\s+[\d.]+ [A-Za-z]+\s+\S+\s+(.+?)\s*$", line)
            if found:
                rows[int(found.group(1))] = (int(found.group(2)), found.group(3))
        matches = (
            bool(rows)
            and len(rows) == len(parts)
            and all(rows.get(i) == (part["start"] // 512, part["name"]) for i, part in enumerate(parts, 1))
        )
        if not matches:
            raise Refused(M("layout.mismatch", "The card does not read back as the layout that was written."))
        return {
            "parts": parts,
            "table": table.stdout,
            "blkid": read_back.stdout,
            "matches": matches,
            "commands": used,
            "device": device,
        }
    finally:
        if loop:
            run("losetup", "-d", device)


def check(path, board="lyra", filesystem="ext4", ab="off"):
    """Read only: the layout the card has, against the one this plan would make."""
    path = Path(path)
    table = run("sgdisk", "-p", str(path))
    size = (
        int(run("blockdev", "--getsize64", str(path)).stdout.strip() or 0)
        if stat.S_ISBLK(path.stat().st_mode)
        else path.stat().st_size
    )
    return {"table": table.stdout, "parts": plan(size, board, filesystem, ab)}
