# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Put one file back, from a copy, irate-box's git checkout, or its package.

The Housecall doctor's recovery step, and later Box doctor's boot check, both restore files; this is the one
place that writes them. Every restore:

  - takes the source only when it checks out (a SHA-256 the caller gives, or the git blob it asked for);
  - writes the new file beside the old one (the plan's A/B for files), fsyncs it, evicts it from the page cache
    and reads it back from the card, and compares its SHA-256, before anything else changes;
  - keeps the old file as name.damaged (a hard link, made before the switch), then renames the new one into place
    and fsyncs the folder: the target is never missing, even at a power cut;
  - gives the new file the old one's owner, group and mode (server.key is root:<front group> 0640, and Caddy
    reads it), or git's mode for irate-box's own files;
  - changes nothing if the write or the read-back fails.

  from_copy(target, source, sha256=None)       a file beside it, a USB drive, or a copy
  from_bytes(target, data, expect=None)        data the caller made (a regenerated key or front file)
  from_git(target, repo, commit, relative)     irate-box's own file, from the updater's checkout
  from_package(target, package=None)           a package's file, by apt-get install --reinstall
  is_conffile(target, package)                 whether dpkg lists it as a package conffile

Stdlib only.
"""

import hashlib
import os
import secrets
import stat
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
from pathlib import Path

from irate_box.msg import M, Refused

CHUNK = 1 << 20


def run(*cmd, timeout=600, binary=False):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, b"" if binary else "", b"" if binary else str(exc))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _damaged(path):
    """The name the old file is kept under: name.damaged, then name.damaged.2, .3 …"""
    base = Path(str(path) + ".damaged")
    if not base.exists():
        return base
    for n in range(2, 1000):
        candidate = Path(f"{path}.damaged.{n}")
        if not candidate.exists():
            return candidate
    raise Refused(M("restore.damaged-name", "There are too many kept copies beside {path}.", path=str(path)))


def _read_back_sha(dfd, name):
    """The SHA-256 of a file as the card returns it (its pages evicted before: _write_beside)."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dfd)
    try:
        digest = hashlib.sha256()
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _write_beside(path, data, mode, owner):
    """A new file beside path, fsynced, evicted from the page cache and read back from the card: its name, once the
    read-back matches. Nothing at path changes."""
    path = Path(path)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    tmp = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            if owner is not None and os.geteuid() == 0:
                os.fchown(fd, *owner)
            os.fchmod(fd, mode)
            os.fsync(fd)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
        if _read_back_sha(dfd, tmp) != _sha(data):
            raise Refused(
                M(
                    "restore.read-back",
                    "The new {name} did not read back the same. Nothing was changed.",
                    name=path.name,
                )
            )
        return tmp, dfd
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        os.close(dfd)
        raise


def from_bytes(target, data, mode=None, expect=None, keep=True, owner=None):
    """`data` at target, checked, with the old file kept beside it. mode and owner (uid, gid) default to the old
    file's, else 0644 and root's. The message says what was done."""
    target = Path(target)
    digest = _sha(data)
    if expect and expect != digest:
        raise Refused(
            M(
                "restore.source-mismatch",
                "The copy for {name} does not match its SHA-256. Nothing was written.",
                name=target.name,
            )
        )
    if not target.parent.is_dir():
        raise Refused(M("restore.no-folder", "The folder {folder} is not there.", folder=str(target.parent)))
    try:
        old = target.lstat()
    except OSError:
        old = None
    if old is not None and stat.S_ISREG(old.st_mode):
        mode = stat.S_IMODE(old.st_mode) if mode is None else mode
        owner = (old.st_uid, old.st_gid) if owner is None else owner
    mode = 0o644 if mode is None else mode
    tmp, dfd = _write_beside(target, data, mode, owner)
    try:
        kept = None
        if old is not None and keep:
            kept = _damaged(target)
            os.link(target.name, kept.name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        os.replace(tmp, target.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        os.fsync(dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    finally:
        os.close(dfd)
    if kept:
        return M(
            "restore.done.kept",
            "The old {name} is kept as {kept}, and the new one reads back the same.",
            name=target.name,
            kept=kept.name,
        )
    return M("restore.done", "The new {name} reads back the same.", name=target.name)


def from_copy(target, source, sha256=None, mode=None):
    """A file from a copy beside it, on a USB drive or in Box doctor's second copy."""
    source = Path(source)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise Refused(M("restore.no-source", "The copy could not be read: {why}", why=str(exc))) from None
    if mode is None and not Path(target).exists():
        try:
            mode = stat.S_IMODE(source.stat().st_mode)
        except OSError:
            mode = None
    return from_bytes(target, data, mode=mode, expect=sha256)  # mode None: the old file's, else the copy's


def from_git(target, repo, commit, relative, mode=None):
    """irate-box's own file, from the updater's git checkout at the installed commit (binary-safe), with git's mode:
    an executable (100755) comes back executable."""
    if mode is None:
        listed = run("git", "-C", str(repo), "ls-tree", commit, "--", relative)
        mode = 0o755 if listed.returncode == 0 and listed.stdout.startswith("100755") else 0o644
    said = run("git", "-C", str(repo), "show", f"{commit}:{relative}", binary=True)
    if said.returncode:
        raise Refused(
            M(
                "restore.git",
                "The file is not in the git checkout at {commit}: {why}",
                commit=commit,
                why=(said.stderr or b"").decode(errors="replace").strip()[-200:],
            )
        )
    return from_bytes(target, said.stdout, mode=mode)


def is_conffile(target, package):
    """Whether dpkg lists target among the package's conffiles (the path whole, not /etc/foo in /etc/foo.conf)."""
    said = run("dpkg-query", "-W", "-f=${Conffiles}\n", package, timeout=60)
    return said.returncode == 0 and any(line.split()[:1] == [str(target)] for line in said.stdout.splitlines())


def from_package(target, package=None):
    """The package's own file back, by apt-get install --reinstall. A conffile is moved aside first, so dpkg
    cannot keep the damaged one as an edit (--force-confmiss)."""
    target = Path(target)
    if package is None:
        said = run("dpkg", "-S", str(target), timeout=60)
        if said.returncode:
            raise Refused(M("restore.no-package", "No package owns {path}.", path=str(target)))
        package = said.stdout.split(":", 1)[0].split(",")[0].strip()
    conffile = is_conffile(target, package)
    kept = None
    if conffile and target.exists():
        kept = _damaged(target)
        os.replace(target, kept)
    args = ["apt-get", "install", "--reinstall", "-y", package]
    if conffile:
        args.insert(3, "-o")
        args.insert(4, "Dpkg::Options::=--force-confmiss")
    said = run(*args, timeout=3600)
    if said.returncode:
        if kept is not None and not target.exists():
            os.replace(kept, target)  # offline, say: the damaged conffile back, rather than none at all
        raise Refused(
            M(
                "restore.package",
                "The package {package} did not reinstall: {why}",
                package=package,
                why=(said.stderr or said.stdout).strip()[-300:],
            )
        )
    return M("restore.package.done", "The package {package} was installed again.", package=package)
