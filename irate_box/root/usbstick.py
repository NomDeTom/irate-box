# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Books to and from a USB stick, for /admin's Books page. Run as root by hub_control.py.

scan()         every removable or USB partition with a filesystem: mounted read-only (nosuid,
               nodev, noexec) just long enough to list its .zim files, then unmounted.
import_zim()   one of those books copied into the hub's ZIM folder as the hub user, checked
               (zimcheck: whole, and readable by Kiwix) on the stick and again as copied, and
               the Kiwix library rebuilt; refused over an existing
               book of the same name, or when it would leave less free than the librarian's
               min_free_mb.
export_zim()   a book from the hub onto the stick, under irate-box/, mounted read-write only
               for the copy.
export_kit(), import_kit()   a toolkit to or from the stick's irate-box/kits/ (kits.py: the
               import checks every .deb against Debian's signatures first).

A stick the desktop has mounted already is used where it is and left mounted. Stdlib only.
"""

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

from irate_box.library import zimcheck
from irate_box.msg import M, Refused, render
from irate_box.root import safeio

MOUNT_ROOT = Path(os.environ.get("HUB_USB_MOUNTS", "/run/irate-box/usb"))
FILESYSTEMS = {"vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "iso9660"}
# Each one's kernel driver (the module's name, for modprobe), and its name in words.
DRIVER = {
    "vfat": "vfat",
    "exfat": "exfat",
    "ntfs": "ntfs3",
    "ntfs3": "ntfs3",
    "ext2": "ext4",
    "ext3": "ext4",
    "ext4": "ext4",
    "btrfs": "btrfs",
    "xfs": "xfs",
    "f2fs": "f2fs",
    "iso9660": "isofs",
}
FS_WORDS = {
    "vfat": "FAT",
    "exfat": "exFAT",
    "ntfs": "NTFS",
    "ntfs3": "NTFS",
    "ext2": "ext2",
    "ext3": "ext3",
    "ext4": "ext4",
    "btrfs": "btrfs",
    "xfs": "XFS",
    "f2fs": "F2FS",
    "iso9660": "ISO 9660 (a disc image)",
}
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
CHUNK = 1 << 20
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# For tests only: count loop devices as sticks (a container has no USB to plug in).
INCLUDE_LOOP = os.environ.get("HUB_USB_INCLUDE_LOOP") == "1"


def run(*cmd, timeout=60):
    """A command's result; one not installed, or that cannot start, answers 127 as a shell would."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def readable():
    """{fstype: True if this box can mount it}: its kernel knows it already (/proc/filesystems), or can load its driver
    (modprobe's dry run, which a built-in driver passes too), or, for NTFS, ntfs-3g is installed."""
    try:
        known = {line.split()[-1] for line in (PROC / "filesystems").read_text().splitlines() if line.strip()}
    except OSError:
        known = set()
    drivers = {}
    for fs in sorted(FILESYSTEMS):
        mod = DRIVER[fs]
        if mod not in drivers:
            drivers[mod] = fs in known or mod in known or run("modprobe", "-n", "-q", mod, timeout=20).returncode == 0
    return {fs: drivers[DRIVER[fs]] or (fs == "ntfs" and bool(shutil.which("ntfs-3g"))) for fs in FILESYSTEMS}


def _walk(devs, parent_usb=False):
    for d in devs:
        usb = (
            parent_usb
            or d.get("tran") == "usb"
            or str(d.get("rm")) in ("1", "True", "true")
            or (INCLUDE_LOOP and d.get("type") == "loop")
        )
        yield d, usb
        yield from _walk(d.get("children") or [], usb)


def _probe(path):
    """(fstype, label) read off the device by blkid, for when lsblk has no udev data to say."""
    out = run("blkid", "-o", "export", path)
    fields = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    return fields.get("TYPE"), fields.get("LABEL")


def candidates(lsblk_json, probe=_probe):
    """Partitions (or whole unpartitioned sticks) on removable or USB devices with a
    filesystem the box can read: [{name, path, label, size, fstype, mountpoint}]."""
    out = []
    for d, usb in _walk(json.loads(lsblk_json).get("blockdevices", [])):
        if usb and not d.get("fstype") and not d.get("children"):
            d["fstype"], d["label"] = probe(d.get("path") or f"/dev/{d['name']}")
        if usb and (d.get("fstype") or "").lower() in FILESYSTEMS:
            out.append(
                {
                    "name": d["name"],
                    "path": d.get("path") or f"/dev/{d['name']}",
                    "label": d.get("label") or "",
                    "size": d.get("size") or "",
                    "fstype": d["fstype"],
                    "mountpoint": d.get("mountpoint"),
                }
            )
    return out


def devices():
    out = run("lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,RM,TRAN,FSTYPE,SIZE,LABEL,MOUNTPOINT")
    if out.returncode != 0:
        raise Refused(
            M(
                "usb.lsblk-failed",
                "The box could not list the USB drives. lsblk said: {detail}",
                detail=out.stderr.strip() or "no output",
            )
        )
    return candidates(out.stdout)


class Mounted:
    """The device's files, at a mount point of ours (removed after), or, to read only, at its existing one.
    To write (every export), only a mount of the box's own (audit M7): one made elsewhere has neither nosymfollow
    nor the others, and a folder on it could be a link that sends root's writes anywhere. The owner unmounts it,
    and the box mounts it itself."""

    def __init__(self, dev, writable=False):
        self.dev, self.writable, self.ours = dev, writable, False

    def __enter__(self):
        if self.dev.get("mountpoint"):
            if self.writable:
                raise Refused(
                    M(
                        "usb.device.mounted-elsewhere",
                        "The box cannot write to {name}: it is already mounted at {mountpoint}. Unmount it, then try "
                        "again. The box then mounts it itself, so that nothing on the drive can send the box's files "
                        "somewhere else.",
                        name=self.dev["name"],
                        mountpoint=self.dev["mountpoint"],
                    )
                )
            return Path(self.dev["mountpoint"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.dev["name"]):
            raise Refused(M("usb.device.bad-name", "The device name {name} is not valid.", name=self.dev["name"]))
        self.path = MOUNT_ROOT / self.dev["name"]
        # Root's own place for its mounts: a link at it, or in place of it, is not one.
        if MOUNT_ROOT.is_symlink() or self.path.is_symlink():
            raise Refused(
                M(
                    "usb.device.mount-link",
                    "The box's mount point {path} is a link. Mount refused.",
                    path=str(self.path),
                )
            )
        self.path.mkdir(parents=True, exist_ok=True)
        # nosymfollow: a stick's own links (ext4 can hold them) lead nowhere root follows.
        opts = ("rw" if self.writable else "ro") + ",nosuid,nodev,noexec,nosymfollow"
        out = run("mount", "-o", opts, self.dev["path"], str(self.path))
        if out.returncode != 0:
            self.path.rmdir()
            raise Refused(
                M(
                    "usb.device.mount-failed",
                    "The box could not mount {name}. mount said: {detail}",
                    name=self.dev["name"],
                    detail=out.stderr.strip()[:200],
                )
            )
        self.ours = True
        return self.path

    def __exit__(self, *exc):
        if self.ours:
            run("sync")
            run("umount", str(self.path))
            try:
                self.path.rmdir()
            except OSError:
                pass


def _zims(root, depth=3):
    found = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) >= depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for f in filenames:
            if f.lower().endswith(".zim") and not f.startswith("."):
                p = Path(dirpath) / f
                try:
                    why = zimcheck.header_problem(p)  # 80 bytes: cheap enough for a scan
                    found.append(
                        {
                            "file": str(rel / f),
                            "size": p.stat().st_size,
                            "zim": why is None,
                            **({"problem": why} if why else {}),
                        }
                    )
                except OSError:
                    continue
    return sorted(found, key=lambda z: z["file"])


def scan():
    out = []
    can = readable()
    for dev in devices():
        fs = (dev.get("fstype") or "").lower()
        if not can.get(fs, True):
            dev["zims"], dev["kits"] = [], []
            # Only what this box can read is suggested: no filesystem is readable on every box. error, the English, for
            # a reader of control/usb.json that expects text; error_message, the message, for the page.
            ok = list(dict.fromkeys(FS_WORDS[n] for n in FS_WORDS if can.get(n)))
            said = M(
                "usb.fs.unsupported",
                "This box cannot read {fs}: its kernel has no driver for it. "
                "This box can read {names}. Format the USB drive with one of them to use it here.",
                fs=FS_WORDS.get(fs, fs),
                names=ok,
            )
            dev["error"], dev["error_message"] = render(said), said
            out.append(dev)
            continue
        try:
            with Mounted(dev) as root:
                dev["zims"] = _zims(root)
                from irate_box.root import kits

                dev["kits"] = kits.stick_kits(root)
        except (ValueError, OSError) as exc:
            dev["zims"], dev["kits"], dev["error"] = [], [], str(exc)
        out.append(dev)
    return {"at": time.time(), "devices": out}


def _find(scan_state, name):
    for dev in devices():  # as the box sees it now, not as the last scan did
        if dev["name"] == name:
            return dev
    raise Refused(M("usb.device.gone", "USB device unavailable: {name} is not connected now. Scan again.", name=name))


def _copy(src, dest, report=None, out=None):
    """src to dest, or to `out` (a file already open for it)."""
    done = 0
    # O_NOFOLLOW: neither the hub's zim/ nor a stick may hand root a link to read through. Nor a hard link: one
    # name of a file that has another could be root's own file under a name the hub chose (the kernel's
    # fs.protected_hardlinks, which the box sets, already refuses to make one; this holds where it is off).
    fd = os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        if out:
            out.close()
        raise Refused(
            M(
                "usb.copy.not-plain",
                "The box did not copy {file}. It is not a plain file: it has another name, or it is not a file.",
                file=Path(src).name,
            )
        )
    size = st.st_size
    with os.fdopen(fd, "rb") as fi, out or open(dest, "wb") as fo:
        while chunk := fi.read(CHUNK):
            fo.write(chunk)
            done += len(chunk)
            if report:
                report(done, size)
        fo.flush()
        os.fsync(fo.fileno())


def import_zim(device, file, zim_dir, hub_user, min_free, librarian_cmd, state_dir, report=None):
    """Copy device:file into zim_dir; returns the book's name."""
    dev = _find(None, device)
    rel = Path(file)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".zim":
        raise Refused(M("usb.import.not-a-book", "{file} is not a book on the USB drive.", file=file))
    name = rel.stem
    if not NAME_RE.match(name):
        raise Refused(
            M(
                "usb.import.bad-name",
                "The box cannot use the name {file}. On the USB drive, rename the file with only letters, digits, '-', '_' and '.'.",
                file=rel.name,
            )
        )
    dest = Path(zim_dir) / f"{name}.zim"
    if dest.exists():
        raise Refused(
            M(
                "usb.import.exists",
                "Book already exists: the box already has a book called {name}. The box did not change it.",
                name=name,
            )
        )
    with Mounted(dev) as root:
        src = root / rel
        if not src.is_file() or src.is_symlink():
            raise Refused(
                M("usb.import.book-gone", "USB device unavailable: the book is not on the USB drive now. Scan again.")
            )
        why = zimcheck.header_problem(src)
        if why:
            raise Refused(
                M(
                    "usb.import.check-failed",
                    "Book failed integrity check: the box did not copy {file}. {why}",
                    file=rel.name,
                    why=why,
                )
            )
        size = src.stat().st_size
        free = shutil.disk_usage(zim_dir).free
        if free - size < min_free:
            raise Refused(
                M(
                    "usb.import.no-space",
                    "Not enough space. The book needs {need} MB. The box has {free} MB free. "
                    "The box must keep {keep} MB free (set in the librarian).",
                    need=size >> 20,
                    free=free >> 20,
                    keep=min_free >> 20,
                )
            )
        tmp = Path(zim_dir) / f".{name}.zim.usb"
        try:
            # In the hub's folder: a new file, never through a link, the hub's through its fd.
            tmp.unlink(missing_ok=True)
            hub = pwd.getpwnam(hub_user)
            _copy(src, tmp, report, out=safeio.create(tmp, 0o644, hub.pw_uid, hub.pw_gid))
            why = zimcheck.problem(tmp, user=hub_user)  # libzim as the hub, not root
            if why:
                raise Refused(
                    M(
                        "usb.import.copy-failed",
                        "Book failed integrity check: the copy of {file} failed the check. {why} The file looked complete "
                        "on the USB drive. The cause can be a failing USB drive, a removed USB drive or a box storage fault. The box added nothing.",
                        file=rel.name,
                        why=why,
                    )
                )
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    out = subprocess.run(  # noqa: TID251
        ["runuser", "-u", hub_user, "--", "env", f"HUB_STATE_DIR={state_dir}", *librarian_cmd, "rebuild-library"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if out.returncode != 0:
        raise Refused(
            M(
                "usb.import.index-failed",
                "Book copied. The library index rebuild failed for {name}: {detail}",
                name=name,
                detail=out.stderr.strip()[-200:],
            )
        )
    return name


def export_kit(device, kit, budget_report=None):
    """A toolkit onto the stick's irate-box/kits/ (kits.export_usb), mounted read-write for it. Returns kits.export_usb's
    line, in English."""
    from irate_box.root import kits

    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise Refused(M("usb.device.disc", "USB device read-only: this device is a disc. The box cannot write to it."))
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box" / "kits"
        folder.mkdir(parents=True, exist_ok=True)
        return kits.export_usb(kit, folder, budget_report)


def export_with(device, write):
    """write(folder) called with the stick's irate-box/ folder, mounted read-write (a real folder, never a link)."""
    from irate_box.root import safeio

    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise Refused(M("usb.device.disc", "USB device read-only: this device is a disc. The box cannot write to it."))
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box"
        safeio.mkdir(folder, mode=0o755)
        return write(folder)


def import_kit(device, kit, budget_mb, report=None, freshness=None):
    """A toolkit from the stick into the local repository, every .deb checked against Debian's
    signed indexes (kits.import_usb); the stick read-only. freshness: as kits.import_usb's."""
    from irate_box.root import kits

    dev = _find(None, device)
    with Mounted(dev) as root:
        return kits.import_usb(root / "irate-box" / "kits", kit, budget_mb, report, freshness=freshness)


def export_zim(device, book, zim_dir, report=None):
    """Copy the hub's <book>.zim to the stick's irate-box/ folder; returns where it went."""
    if not NAME_RE.match(book):
        raise Refused(M("usb.export.bad-book", "The request is not valid: {book} is not a book name.", book=book))
    src = Path(zim_dir) / f"{book}.zim"
    # A plain file, not a link: zim/ is the hub's, and a link there would have root copy
    # any file it can read onto the stick.
    if src.is_symlink() or not src.is_file():
        raise Refused(M("usb.export.no-book", "The box has no book called {book}.", book=book))
    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise Refused(M("usb.device.disc", "USB device read-only: this device is a disc. The box cannot write to it."))
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box"
        folder.mkdir(exist_ok=True)
        if shutil.disk_usage(folder).free < src.stat().st_size:
            raise Refused(
                M(
                    "usb.export.no-space",
                    "Not enough space on the USB drive for {book}. The book needs {need} MB. The USB drive has {free} MB free.",
                    book=book,
                    need=src.stat().st_size >> 20,
                    free=shutil.disk_usage(folder).free >> 20,
                )
            )
        tmp = folder / f".{book}.zim.part"
        try:
            tmp.unlink(missing_ok=True)
            # Never through a link on the stick (one mounted elsewhere has no nosymfollow).
            _copy(src, tmp, report, out=safeio.create(tmp, 0o644))
            os.replace(tmp, folder / f"{book}.zim")
        finally:
            tmp.unlink(missing_ok=True)
    return f"irate-box/{book}.zim"


# --- a full image of the box's card ---------------------------------------------------------------
IMAGE_PART = 3900 << 20  # a FAT stick holds no file over 4 GB
FAT = {"vfat"}


def root_disk(run_=None):
    """The whole disk holding / (/dev/mmcblk1, /dev/sda …), or ValueError."""
    run_ = run_ or run
    src = run_("findmnt", "-no", "SOURCE", "/").stdout.strip()
    if not src:
        raise Refused(
            M(
                "usb.image.no-source",
                "The box cannot make an image: it cannot find the device that holds its root filesystem.",
            )
        )
    if not src.startswith("/dev/"):
        raise Refused(
            M(
                "usb.image.not-disk",
                "The box cannot make an image: its root filesystem is on {source}, not on a disk.",
                source=src,
            )
        )
    parent = run_("lsblk", "-no", "PKNAME", src).stdout.strip()
    disk = f"/dev/{parent}" if parent else src
    if not re.fullmatch(r"/dev/[A-Za-z0-9]+", disk):
        raise Refused(
            M("usb.image.bad-disk", "The box cannot make an image: the disk name {disk} is not valid.", disk=disk)
        )
    return disk


def export_image(device, report=None, disk=None, stamp=None):
    """The card, gzipped, onto the stick's irate-box/images/ (parts of 3.9 GB on FAT). Returns where, as a message."""
    import zlib

    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise Refused(M("usb.device.disc", "USB device read-only: this device is a disc. The box cannot write to it."))
    disk = disk or root_disk()
    if dev["path"].startswith(disk):
        raise Refused(
            M("usb.image.own-disk", "This USB device is the disk that the box runs from. Select a different USB drive.")
        )
    total = (
        int(run("blockdev", "--getsize64", disk).stdout.strip() or 0)
        if not Path(disk).is_file()
        else Path(disk).stat().st_size
    )
    used = shutil.disk_usage("/").used
    stamp = stamp or time.strftime("%Y%m%d-%H%M")
    base = f"irate-box-image-{stamp}.img.gz"
    run("sync")
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < used:
            raise Refused(
                M(
                    "usb.image.no-space",
                    "Not enough space on the USB drive. The image needs at least about {need} MB (the used space on the box). "
                    "The USB drive has {free} MB free.",
                    need=used >> 20,
                    free=shutil.disk_usage(folder).free >> 20,
                )
            )
        split = dev["fstype"] in FAT
        parts, out, written, n = [], None, 0, 0
        gz = zlib.compressobj(1, zlib.DEFLATED, 31)

        def emit(data):
            nonlocal out, written, n
            while data:
                if out is None or (split and written >= IMAGE_PART):
                    if out:
                        out.close()
                    n += 1
                    name = f"{base}.part{n:02d}" if split else base
                    parts.append(name)
                    out, written = safeio.create(folder / name, 0o644), 0
                room = IMAGE_PART - written if split else len(data)
                out.write(data[:room])
                written += min(room, len(data))
                data = data[room:]

        try:
            done = 0
            with open(disk, "rb") as src:
                for chunk in iter(lambda: src.read(CHUNK * 4), b""):
                    emit(gz.compress(chunk))
                    done += len(chunk)
                    if report:
                        report(done, total)
            emit(gz.flush())
            if out:
                out.close()
        except OSError as exc:
            if out:
                out.close()
            for p in parts:
                (folder / p).unlink(missing_ok=True)
            raise Refused(
                M(
                    "usb.image.incomplete",
                    "Full image incomplete ({detail}). The box removed the partial files.",
                    detail=str(exc.strerror or exc),
                )
            ) from None
        (folder / f"{base}.README.txt").write_text(
            f"A full image of {disk} ({total >> 20} MB), taken {stamp} while the box ran. It is like the state after a power cut.\n"
            + (
                f"Step 1. Join the {len(parts)} parts: cat {base}.part* > {base}\n"
                if split
                else "Step 1. The image is one file. Do not join it.\n"
            )
            + f"Step 2. Check that the destination card is at least {total >> 20} MB.\n"
            "Step 3. Check the device name. The next command erases everything on that device.\n"
            + f"Step 4. Write the image: gunzip -c {base} | sudo dd of=/dev/YOURCARD bs=4M\n"
            "Or open the .img.gz file in an image writer such as Etcher.\n"
        )
    if split:
        return M(
            "usb.image.done-parts",
            {
                "one": "The box wrote a full image of {disk} ({mb} MB) to irate-box/images/{file} on the USB drive, in {n} part.",
                "other": "The box wrote a full image of {disk} ({mb} MB) to irate-box/images/{file} on the USB drive, in {n} parts.",
            },
            disk=disk,
            mb=total >> 20,
            file=base,
            n=len(parts),
        )
    return M(
        "usb.image.done",
        "The box wrote a full image of {disk} ({mb} MB) to irate-box/images/{file} on the USB drive.",
        disk=disk,
        mb=total >> 20,
        file=base,
    )
