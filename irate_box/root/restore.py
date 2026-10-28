# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Put one file back, from a copy, irate-box's git checkout, or its package.

The Housecall doctor's recovery step, and later Box doctor's boot check, both restore files; this is the one
place that writes them. Every restore:

  - takes the source only when it checks out (a SHA-256 the caller gives, or the git blob it asked for);
  - moves the damaged file aside as name.damaged, never over it, so nothing is lost;
  - writes through a temporary file, fsyncs it, renames it into place and fsyncs the folder;
  - reads the new file back and compares its SHA-256 before it says yes;
  - puts the old file back if the write or the read-back fails.

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


def _sync_write(path, data, mode=0o644):
    """A new file in place: a temporary file fsynced, renamed, the folder fsynced."""
    path = Path(path)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    tmp = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
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


def from_bytes(target, data, mode=0o644, expect=None, keep=True):
    """`data` at target, checked, with the old file kept beside it. The message says what was done."""
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
    kept = None
    if target.exists() or target.is_symlink():
        kept = _damaged(target) if keep else None
        os.replace(target, kept)
    try:
        _sync_write(target, data, mode)
        if sha_file(target) != digest:
            raise Refused(
                M(
                    "restore.read-back",
                    "The new {name} did not read back the same. The old file is back.",
                    name=target.name,
                )
            )
    except BaseException:
        if kept is not None:
            try:
                os.replace(kept, target)
            except OSError:
                pass
        raise
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
    if mode is None:
        try:
            mode = stat.S_IMODE(source.stat().st_mode)
        except OSError:
            mode = 0o644
    return from_bytes(target, data, mode=mode, expect=sha256)


def from_git(target, repo, commit, relative, mode=0o644):
    """irate-box's own file, from the updater's git checkout at the installed commit (binary-safe)."""
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
    """Whether dpkg lists target among the package's conffiles."""
    said = run("dpkg-query", "-W", "-f=${Conffiles}\n", package, timeout=60)
    return said.returncode == 0 and str(target) in said.stdout


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
    if conffile and target.exists():
        os.replace(target, _damaged(target))
    args = ["apt-get", "install", "--reinstall", "-y", package]
    if conffile:
        args.insert(3, "-o")
        args.insert(4, "Dpkg::Options::=--force-confmiss")
    said = run(*args, timeout=3600)
    if said.returncode:
        raise Refused(
            M(
                "restore.package",
                "The package {package} did not reinstall: {why}",
                package=package,
                why=(said.stderr or said.stdout).strip()[-300:],
            )
        )
    return M("restore.package.done", "The package {package} was installed again.", package=package)
