# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A board's SD card in a USB reader on the box, root's half (Rockchip boards: Cards in a reader). The cards are
listed by hub/rkusb.cards (removable USB disks with a medium in, never the box's own disk). Each action finds its
card again by name and checks its size and serial against what the page showed, so a card swapped meanwhile is
refused, and refuses a card any of whose partitions is mounted (writing under a mount corrupts it; an ext4 mount
writes to it on its own).

  check     reads only: the partition table (sgdisk -v), each filesystem read-only (e2fsck -n, fsck.vfat -n)
  genuine   f3probe --destructive: whether the card holds as much as it says (a fake keeps its first part and
            loses the rest); its contents lost
  flash     an image from rkusb.IMAGES (.img, or .img.xz unpacked as it goes), checked against its sha256 first,
            written, read back and compared, the card's last 1 MiB cleared (an older image's backup partition
            table)

Stdlib only.
"""

import hashlib
import json
import lzma
import os
import re
import subprocess

from irate_box.hub import rkusb
from irate_box.msg import M, Refused
from irate_box.root import rkboard

NAME_RE = re.compile(r"^sd[a-z]{1,2}$")
CHUNK = 4 << 20
TAIL = 1 << 20


def run(*cmd, timeout=600):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _card(name, size, serial):
    """The card the page showed: still there, the same size and serial, nothing on it mounted."""
    if not NAME_RE.match(name or ""):
        raise Refused(M("rkcard.name", "Select a card."))
    found = next((c for c in rkusb.cards() if c["name"] == name), None)
    if not found:
        raise Refused(M("rkcard.gone", "That card is not in a reader on the box any more."))
    if found["size"] != size or found["serial"] != (serial or ""):
        raise Refused(M("rkcard.changed", "The card in that reader is not the one shown: look at the page again."))
    if found["mounted"]:
        raise Refused(M("rkcard.mounted", "The card is mounted (the desktop may have opened it): unmount it first."))
    return found


def check(name, size, serial):
    """The card read, nothing written: its partition table, and each filesystem the kernel found."""
    card = _card(name, size, serial)
    said = []
    table = run("sgdisk", "-v", card["path"], timeout=60)
    text = (table.stdout or "") + (table.stderr or "")
    if table.returncode == 127:
        said.append("partition table: not checked (gdisk is not installed: the Rockchip boards toolkit brings it)")
    elif "No problems found" in text:
        said.append("partition table: no problems")
    elif "CRC" in text or "Problem" in text or "Warning" in text:
        first = next((line.strip() for line in text.splitlines() if "CRC" in line or "Problem" in line), "")
        said.append(f"partition table: damaged ({first[:120]})")
    else:
        said.append("partition table: none the box knows (a card never written, or a whole-disk filesystem)")
    lsblk = run("lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,SIZE", card["path"], timeout=20)
    try:
        parts = (json.loads(lsblk.stdout).get("blockdevices") or [{}])[0].get("children") or []
    except (ValueError, IndexError):
        parts = []
    for part in parts:
        fstype, path = part.get("fstype") or "", part.get("path") or ""
        if fstype.startswith("ext"):
            r = run("e2fsck", "-n", "-f", path, timeout=1800)
            said.append(
                f"{part['name']} ({fstype}): " + ("clean" if r.returncode == 0 else "errors found, nothing changed")
            )
        elif fstype == "vfat":
            r = run("fsck.vfat", "-n", path, timeout=600)
            said.append(
                f"{part['name']} (vfat): " + ("clean" if r.returncode == 0 else "errors found, nothing changed")
            )
        elif fstype:
            said.append(f"{part['name']} ({fstype}): not checked")
    if not parts:
        said.append("no partitions the kernel found")
    return M("rkcard.checked", "The card was read, nothing written: {said}.", said="; ".join(said))


def genuine(name, size, serial):
    """f3probe --destructive on the card: whether it holds as much as it says. Its contents are lost."""
    card = _card(name, size, serial)
    r = run("f3probe", "--destructive", "--time-ops", card["path"], timeout=3600)
    text = r.stdout or ""
    if r.returncode == 127:
        raise Refused(M("rkcard.no-f3", "f3 is not installed. Install the Rockchip boards toolkit on Tools, Toolkits."))
    usable = re.search(r"\*Usable\* size:\s*([\d.]+ \w+)", text)
    if "is the real thing" in text:
        return M(
            "rkcard.genuine",
            "The card is genuine: {usable} usable, as it says. What was on it is gone.",
            usable=usable.group(1) if usable else "all of it",
        )
    if "counterfeit" in text or "damaged" in text or "is limbo" in text:
        return M(
            "rkcard.fake",
            "The card is not what it says: only {usable} of it can be used. Replace it.",
            usable=usable.group(1) if usable else "part",
        )
    raise Refused(M("rkcard.f3-failed", "The test did not finish: {detail}", detail=(r.stderr or text).strip()[-200:]))


def flash(name, size, serial, image, sha256):
    """An image from rkusb.IMAGES written to the card, checked against its sha256 first, read back and compared."""
    if not rkboard.SHA_RE.match(sha256 or ""):
        raise Refused(M("rkboard.image-sha", "Give the image's sha256, as its publisher lists it."))
    with rkboard._image(image) as fh:
        h = hashlib.sha256()
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != sha256:
        raise Refused(
            M("rkboard.image-damaged", "The image {name} does not match its sha256: nothing was written.", name=image)
        )
    card = _card(name, size, serial)
    written, digest = 0, hashlib.sha256()
    fd = os.open(card["path"], os.O_WRONLY | os.O_EXCL | os.O_CLOEXEC)
    try:
        with rkboard._image(image) as fh:
            stream = lzma.open(fh) if image.endswith(".xz") else fh
            while data := stream.read(CHUNK):
                if written + len(data) > card["size"]:
                    raise Refused(
                        M(
                            "rkboard.image-large",
                            "The image is larger than the card ({gb} GB).",
                            gb=f"{card['size'] / 2**30:.1f}",
                        )
                    )
                os.write(fd, data)
                digest.update(data)
                written += len(data)
        os.fsync(fd)
        if card["size"] > written + TAIL:
            os.lseek(fd, card["size"] - TAIL, os.SEEK_SET)
            os.write(fd, bytes(TAIL))
            os.fsync(fd)
    finally:
        os.close(fd)
    # Read back past the reader's cache: the kernel's pages for the card dropped first.
    run("blockdev", "--flushbufs", card["path"], timeout=60)
    back, left = hashlib.sha256(), written
    with open(card["path"], "rb", buffering=0) as dev:
        while left:
            block = dev.read(min(CHUNK, left))
            if not block:
                break
            back.update(block)
            left -= len(block)
    if left or back.hexdigest() != digest.hexdigest():
        raise Refused(
            M("rkcard.verify-failed", "The card did not read back what was written: it is not usable as it is.")
        )
    return M(
        "rkcard.flashed",
        "The image is on the card: {mb} MB written and read back the same. Take the card out and put it in the board.",
        mb=written >> 20,
    )
