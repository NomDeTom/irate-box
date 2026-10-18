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

Root's files are left root's: mode 0644 by default, which the hub reads. Nothing is chowned to
the hub by path. Stdlib only.
"""

import os
import secrets
import stat
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
