# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's file I/O in folders the hub can change.

The hub owns $STATE, so wherever root writes under it, the hub may have put a link first: a
symlink at the name, a hardlink to a file of root's, the folder itself swapped for a link. A
plain open(), write_text(), touch() or chown() follows them, and root then writes (or hands to
the hub) a file of the hub's choosing. Here, nothing is followed:

  write(path, data)       a new file under a random name, made with O_EXCL|O_NOFOLLOW in the
                          folder opened with O_NOFOLLOW, then renamed over `path`: a link
                          planted at `path` is replaced, never written through, and a hardlink
                          is never written to (the file is always a new one)
  open_new(path)          the same, for a log written as it goes: the new file is in place at
                          once, and returned open for writing
  create(path, uid, gid)  a new file at exactly that name, owned through its fd, open to write
  read_request(path)      a small regular file, opened with O_NOFOLLOW|O_NONBLOCK, so neither a
                          link nor a FIFO (which would hang root) is read
  read_own(path)          a file only if it and its folder are this user's (root's), no links
  mkdir(path, uid, gid)   a folder made, or one already there, refusing a link, and owned and
                          moded through its fd
  root_dir(name)          a folder of root's own beside $STATE (0700), for what root keeps: the
                          hub can rename anything inside $STATE, so nothing of root's lives there
  own_dir(path)           an fd for a folder of this user's that is not a link, in a folder of
                          this user's that is not a link
  write_checked(path, data)  the write above, then the card asked to prove it: fsync, evict, read
                          back and compare; written once more on a mismatch, and a mismatch that
                          survives both is kept in ROOT_STATE/write-check.json for Box doctor
  write_batch(entries)    several files the same way, one syncfs, then each read back

Root's files are left root's: mode 0644 by default, which the hub reads. Nothing is chowned to
the hub by path. Stdlib only.
"""

import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
ROOT_STATE = Path(
    os.environ.get("HUB_ROOT_STATE_DIR")
    or Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")).parent / "irate-box-root"
)


class LinkRefused(OSError):
    pass


def _dir_fd(folder):
    try:
        return os.open(folder, DIR_FLAGS)
    except OSError as exc:
        if Path(folder).is_symlink():
            raise LinkRefused(
                f"Refused: {folder} is a symbolic link, not a folder. The box does not follow it"
            ) from exc
        raise


def _new(dfd, name, mode):
    tmp = f".{name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
    os.fchmod(fd, mode)  # the umask does not decide what the hub can read
    return tmp, fd


def write(path, data, mode=0o644):
    """`data` (str or bytes) to `path`, atomically, never through a link."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode()
    dfd = _dir_fd(path.parent)
    try:
        tmp, fd = _new(dfd, path.name, mode)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def open_new(path, mode=0o644):
    """A new, empty file at `path` (replacing whatever was there, link or not), open for
    writing as text."""
    path = Path(path)
    dfd = _dir_fd(path.parent)
    try:
        tmp, fd = _new(dfd, path.name, mode)
        try:
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            os.close(fd)
            os.unlink(tmp, dir_fd=dfd)
            raise
        return os.fdopen(fd, "w")
    finally:
        os.close(dfd)


def create(path, mode=0o644, uid=None, gid=None):
    """A new file at exactly `path` (an error if anything is there, link or not), owned and
    moded through its fd, open for writing as bytes."""
    path = Path(path)
    dfd = _dir_fd(path.parent)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
    finally:
        os.close(dfd)
    if uid is not None or gid is not None:
        os.fchown(fd, -1 if uid is None else uid, -1 if gid is None else gid)
    os.fchmod(fd, mode)
    return os.fdopen(fd, "wb")


def read_request(path, limit=1 << 20):
    """The text of a small regular file the hub wrote, or OSError (a link, a FIFO, a device,
    or larger than `limit`)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{Path(path).name} is not a regular file. The box did not read it")
        if st.st_size > limit:
            raise OSError(f"{Path(path).name} is too large. The box did not read it")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(limit + 1).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


class NotOurs(OSError):
    pass


def read_own(path, limit=4 << 20):
    """The text of a file this process's user wrote, root's for the helpers: the file and its
    folder both owned by us and neither a link, checked through their fds. The hub owns
    $STATE, so it could swap control/ for a folder of its own holding a forged update.json or
    rtc-find.json; that one is refused."""
    path = Path(path)
    me = os.geteuid()
    dfd = _dir_fd(path.parent)
    try:
        if os.fstat(dfd).st_uid != me:
            raise NotOurs(f"{path.parent} is not owned by the user that the box expects. The box did not read it")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != me or st.st_size > limit:
            raise NotOurs(f"{path.name} was not written by the user that the box expects. The box did not read it")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(limit + 1).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def mkdir(path, uid=None, gid=None, mode=0o755):
    """`path` as a real folder (made if missing), never a link; then owned and moded through
    its own fd, so a link swapped in after the check is not what changes."""
    path = Path(path)
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        pass
    fd = _dir_fd(path)
    try:
        if uid is not None or gid is not None:
            os.fchown(fd, -1 if uid is None else uid, -1 if gid is None else gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def root_dir(name):
    """ROOT_STATE/name, made 0700 if missing, never a link. Its parents are root's, so its path is safe to use."""
    mkdir(ROOT_STATE, mode=0o700)
    mkdir(ROOT_STATE / name, mode=0o700)
    return ROOT_STATE / name


def own_dir(path):
    """An fd for `path`: a folder of this user's, not a link, in a folder of this user's, not a link."""
    path = Path(path)
    me = os.geteuid()
    pfd = _dir_fd(path.parent)
    try:
        if os.fstat(pfd).st_uid != me:
            raise NotOurs(f"{path.parent} is not owned by the user that the box expects. The box did not use it")
        fd = os.open(path.name, DIR_FLAGS, dir_fd=pfd)
    finally:
        os.close(pfd)
    if os.fstat(fd).st_uid != me:
        os.close(fd)
        raise NotOurs(f"{path} is not owned by the user that the box expects. The box did not use it")
    return fd


WRITE_CHECK = ROOT_STATE / "write-check.json"
WRITE_CHECK_KEEP = 20


def _gid(group):
    if isinstance(group, int):
        return group
    try:
        import grp

        return grp.getgrnam(group).gr_gid
    except (KeyError, ImportError):
        return None


def _write_synced(path, data, mode, group=None):
    """`data` to `path` through a new file, the data and then the folder fsynced before the rename is done."""
    path = Path(path)
    dfd = _dir_fd(path.parent)
    try:
        tmp, fd = _new(dfd, path.name, mode)
        try:
            os.fchmod(fd, mode)
            if group is not None and os.geteuid() == 0:
                gid = _gid(group)
                if gid is not None:
                    os.fchown(fd, -1, gid)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.fsync(dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _read_back(path, sync=True, limit=16 << 20):
    """The file read past the page cache, so a copy the cache believes is written is not mistaken for the card's."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if sync:
            os.fsync(fd)
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except (AttributeError, OSError):
            pass
        out = bytearray()
        while len(out) <= limit:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            out += chunk
        return bytes(out)
    finally:
        os.close(fd)


def _syncfs(path):
    """One file system's writes to the medium (Linux's syncfs), or every file system where it is not there."""
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        fd = os.open(path, DIR_FLAGS)
        try:
            if libc.syncfs(fd) != 0:
                raise OSError(ctypes.get_errno(), "syncfs")
        finally:
            os.close(fd)
    except (AttributeError, OSError):
        os.sync()


def _syncfs_all(paths):
    seen = set()
    for path in paths:
        try:
            device = os.stat(Path(path).parent).st_dev
        except OSError:
            continue
        if device in seen:
            continue
        seen.add(device)
        _syncfs(Path(path).parent)


def _record_check(path, expected, got, why):
    """A write the card did not keep, for Box doctor. The report is a record: a plain write is enough."""
    try:
        data = json.loads(WRITE_CHECK.read_text())
    except (OSError, ValueError):
        data = []
    if not isinstance(data, list):
        data = []
    data.append({"at": time.time(), "path": str(path), "why": why, "expected": expected, "got": got})
    mkdir(ROOT_STATE, mode=0o700)
    write(WRITE_CHECK, json.dumps(data[-WRITE_CHECK_KEEP:], indent=1), 0o600)


def write_checked(path, data, mode=0o644, group=None):
    """Write a protected file, then make the card prove it: fsync, evict from the cache, read it back and compare. A
    mismatch is written once more; one that survives both is kept for Box doctor (ROOT_STATE/write-check.json) and
    this returns False. An identical file is left alone (no write)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    if path.is_file() and not path.is_symlink():
        try:
            if path.read_bytes() == data:
                if stat.S_IMODE(path.stat().st_mode) != mode:
                    os.chmod(path, mode)
                if group is not None and os.geteuid() == 0:
                    gid = _gid(group)
                    if gid is not None:
                        try:
                            os.chown(path, -1, gid)
                        except OSError:
                            pass
                return True
        except OSError:
            pass
    expected = hashlib.sha256(data).hexdigest()
    got, why = None, "not written"
    for _ in range(2):
        _write_synced(path, data, mode, group)
        try:
            got = _read_back(path)
        except OSError as exc:
            got, why = None, f"could not be read back: {exc}"
            continue
        if hashlib.sha256(got).hexdigest() == expected:
            return True
        why = "read back different"
    _record_check(path, expected, hashlib.sha256(got).hexdigest() if got is not None else None, why)
    return False


def write_batch(entries):
    """Several protected files written together: each written only when it differs, one syncfs per file system,
    then each read back; a mismatch is written once more and kept for Box doctor if it survives that.
    `entries` is [(path, data[, mode[, group]])]. True when every file read back right."""
    written = []
    for entry in entries:
        path, data = Path(entry[0]), entry[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        data = data.encode() if isinstance(data, str) else data
        mode = entry[2] if len(entry) > 2 else 0o644
        group = entry[3] if len(entry) > 3 else None
        if path.is_file() and not path.is_symlink():
            try:
                if path.read_bytes() == data:
                    continue
            except OSError:
                pass
        _write_synced(path, data, mode, group)
        written.append((path, data, mode, group))
    if not written:
        return True
    failed = []
    for attempt in (1, 2):
        if attempt == 2:
            for path, data, mode, group in failed:
                _write_synced(path, data, mode, group)
            if not failed:
                break
        _syncfs_all([item[0] for item in written])
        failed = []
        for path, data, mode, group in written:
            try:
                got = _read_back(path, sync=False)
            except OSError:
                got = None
            if got is None or hashlib.sha256(got).hexdigest() != hashlib.sha256(data).hexdigest():
                failed.append((path, data, mode, group))
        if not failed:
            return True
    for path, data, *_ in failed:
        _record_check(path, hashlib.sha256(data).hexdigest(), None, "read back different")
    return False
