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
import subprocess
import time
from pathlib import Path

from irate_box.library import zimcheck
from irate_box.root import safeio

MOUNT_ROOT = Path(os.environ.get("HUB_USB_MOUNTS", "/run/irate-box/usb"))
FILESYSTEMS = {"vfat", "exfat", "ntfs", "ntfs3", "ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "iso9660"}
CHUNK = 1 << 20
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# For tests only: count loop devices as sticks (a container has no USB to plug in).
INCLUDE_LOOP = os.environ.get("HUB_USB_INCLUDE_LOOP") == "1"


def run(*cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _walk(devs, parent_usb=False):
    for d in devs:
        usb = parent_usb or d.get("tran") == "usb" or str(d.get("rm")) in ("1", "True", "true") or \
            (INCLUDE_LOOP and d.get("type") == "loop")
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
            out.append({"name": d["name"], "path": d.get("path") or f"/dev/{d['name']}",
                        "label": d.get("label") or "", "size": d.get("size") or "",
                        "fstype": d["fstype"], "mountpoint": d.get("mountpoint")})
    return out


def devices():
    out = run("lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,RM,TRAN,FSTYPE,SIZE,LABEL,MOUNTPOINT")
    if out.returncode != 0:
        raise ValueError("lsblk failed: " + (out.stderr.strip() or "no output"))
    return candidates(out.stdout)


class Mounted:
    """The device's files, at its existing mount point or one of ours (removed after)."""

    def __init__(self, dev, writable=False):
        self.dev, self.writable, self.ours = dev, writable, False

    def __enter__(self):
        if self.dev.get("mountpoint"):
            return Path(self.dev["mountpoint"])
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.dev["name"]):
            raise ValueError("unexpected device name")
        self.path = MOUNT_ROOT / self.dev["name"]
        self.path.mkdir(parents=True, exist_ok=True)
        # nosymfollow: a stick's own links (ext4 can hold them) lead nowhere root follows.
        opts = ("rw" if self.writable else "ro") + ",nosuid,nodev,noexec,nosymfollow"
        out = run("mount", "-o", opts, self.dev["path"], str(self.path))
        if out.returncode != 0:
            self.path.rmdir()
            raise ValueError(f"could not mount {self.dev['name']}: {out.stderr.strip()[:200]}")
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
                    found.append({"file": str(rel / f), "size": p.stat().st_size, "zim": why is None,
                                  **({"problem": why} if why else {})})
                except OSError:
                    continue
    return sorted(found, key=lambda z: z["file"])


def scan():
    out = []
    for dev in devices():
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
    raise ValueError(f"{name} is not plugged in now: scan again")


def _copy(src, dest, report=None, out=None):
    """src to dest, or to `out` (a file already open for it)."""
    size = os.path.getsize(src)
    done = 0
    # O_NOFOLLOW: neither the hub's zim/ nor a stick may hand root a link to read through (F14).
    with os.fdopen(os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), "rb") as fi, (out or open(dest, "wb")) as fo:
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
        raise ValueError("not a book on that stick")
    name = rel.stem
    if not NAME_RE.match(name):
        raise ValueError(f"{rel.name}: rename it on the stick to letters, digits, '-', '_' or '.'")
    dest = Path(zim_dir) / f"{name}.zim"
    if dest.exists():
        raise ValueError(f"the hub already has a book called {name}; it was left as it is")
    with Mounted(dev) as root:
        src = root / rel
        if not src.is_file() or src.is_symlink():
            raise ValueError("that book is not on the stick now: scan again")
        why = zimcheck.header_problem(src)
        if why:
            raise ValueError(f"{rel.name} was not copied: it is {why}")
        size = src.stat().st_size
        free = shutil.disk_usage(zim_dir).free
        if free - size < min_free:
            raise ValueError(f"not enough room: {size >> 20} MB needed, {free >> 20} MB free, "
                             f"and {min_free >> 20} MB must stay free (Schedule and token)")
        tmp = Path(zim_dir) / f".{name}.zim.usb"
        try:
            # In the hub's folder: a new file, never through a link, the hub's through its fd (F3).
            tmp.unlink(missing_ok=True)
            hub = pwd.getpwnam(hub_user)
            _copy(src, tmp, report, out=safeio.create(tmp, 0o644, hub.pw_uid, hub.pw_gid))
            why = zimcheck.problem(tmp, user=hub_user)  # libzim as the hub, not root (F12)
            if why:
                raise ValueError(f"{rel.name} was not added: the copy is {why}. It looked whole on the stick, "
                                 "so the stick may be failing, or was pulled out; nothing on the hub changed.")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
    out = subprocess.run(["runuser", "-u", hub_user, "--", "env", f"HUB_STATE_DIR={state_dir}",
                          *librarian_cmd, "rebuild-library"],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise ValueError(f"{name} is copied, but the library was not rebuilt: {out.stderr.strip()[-200:]}")
    return name


def export_kit(device, kit, budget_report=None):
    """A toolkit onto the stick's irate-box/kits/ (kits.export_usb), mounted read-write for it."""
    from irate_box.root import kits
    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise ValueError("that is a read-only disc")
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box" / "kits"
        folder.mkdir(parents=True, exist_ok=True)
        return kits.export_usb(kit, folder, budget_report)


def export_with(device, write):
    """write(folder) called with the stick's irate-box/ folder, mounted read-write (a real folder, never a link)."""
    from irate_box.root import safeio
    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise ValueError("that is a read-only disc")
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box"
        safeio.mkdir(folder, mode=0o755)
        return write(folder)


def import_kit(device, kit, budget_mb, report=None):
    """A toolkit from the stick into the local repository, every .deb checked against Debian's
    signed indexes (kits.import_usb); the stick read-only."""
    from irate_box.root import kits
    dev = _find(None, device)
    with Mounted(dev) as root:
        return kits.import_usb(root / "irate-box" / "kits", kit, budget_mb, report)


def export_zim(device, book, zim_dir, report=None):
    """Copy the hub's <book>.zim to the stick's irate-box/ folder; returns where it went."""
    if not NAME_RE.match(book):
        raise ValueError("not a book name")
    src = Path(zim_dir) / f"{book}.zim"
    # A plain file, not a link (F14): zim/ is the hub's, and a link there would have root copy
    # any file it can read onto the stick.
    if src.is_symlink() or not src.is_file():
        raise ValueError(f"the hub has no book called {book}")
    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise ValueError("that is a read-only disc")
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box"
        folder.mkdir(exist_ok=True)
        if shutil.disk_usage(folder).free < src.stat().st_size:
            raise ValueError("not enough room on the stick")
        tmp = folder / f".{book}.zim.part"
        try:
            tmp.unlink(missing_ok=True)
            # Never through a link on the stick (one mounted elsewhere has no nosymfollow).
            _copy(src, tmp, report, out=safeio.create(tmp, 0o644))
            os.replace(tmp, folder / f"{book}.zim")
        finally:
            tmp.unlink(missing_ok=True)
    return f"irate-box/{book}.zim"


# --- a full image of the box's card (item 34: "settings only, settings and data, full image") -------------
IMAGE_PART = 3900 << 20       # a FAT stick holds no file over 4 GB
FAT = {"vfat"}


def root_disk(run_=None):
    """The whole disk holding / (/dev/mmcblk1, /dev/sda …), or ValueError."""
    run_ = run_ or run
    src = run_("findmnt", "-no", "SOURCE", "/").stdout.strip()
    if not src.startswith("/dev/"):
        raise ValueError(f"the root filesystem is on {src or 'nothing known'}, not a disk to image")
    parent = run_("lsblk", "-no", "PKNAME", src).stdout.strip()
    disk = f"/dev/{parent}" if parent else src
    if not re.fullmatch(r"/dev/[A-Za-z0-9]+", disk):
        raise ValueError("unexpected disk name")
    return disk


def export_image(device, report=None, disk=None, stamp=None):
    """The card, gzipped, onto the stick's irate-box/images/ (parts of 3.9 GB on FAT). Returns where."""
    import zlib
    dev = _find(None, device)
    if dev["fstype"] == "iso9660":
        raise ValueError("that is a read-only disc")
    disk = disk or root_disk()
    if dev["path"].startswith(disk):
        raise ValueError("that stick is the box's own disk")
    total = int(run("blockdev", "--getsize64", disk).stdout.strip() or 0) if not Path(disk).is_file() else Path(disk).stat().st_size
    used = shutil.disk_usage("/").used
    stamp = stamp or time.strftime("%Y%m%d-%H%M")
    base = f"irate-box-image-{stamp}.img.gz"
    run("sync")
    with Mounted(dev, writable=True) as root:
        folder = root / "irate-box" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < used:
            raise ValueError(f"not enough room on the stick: the image needs at least about {used >> 20} MB "
                             f"(what the card holds), and {shutil.disk_usage(folder).free >> 20} MB is free")
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
            raise ValueError(f"the image was not finished ({exc.strerror or exc}): the parts written are removed") from None
        (folder / f"{base}.README.txt").write_text(
            f"A full image of {disk} ({total >> 20} MB), taken {stamp} while the box ran: as after a power cut.\n"
            + (f"In {len(parts)} parts: join them first: cat {base}.part* > {base}\n" if split else "")
            + f"Write it back to a card of at least {total >> 20} MB: gunzip -c {base} | sudo dd of=/dev/YOURCARD bs=4M\n"
              "(or give the .img.gz to an image writer such as Etcher).\n")
    return f"irate-box/images/{base}" + (f" in {len(parts)} parts" if split else "") + f", from {disk} ({total >> 20} MB)"
