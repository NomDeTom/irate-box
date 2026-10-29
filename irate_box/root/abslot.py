# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Patch the inactive A/B system slot, offline: a chroot of the slot, apt in it, the running /etc brought over,
and a check of what changed. Nothing is written to the running system.

The binds are made --rbind and each made --make-rslave, so an unmount in the slot cannot take the host's own /dev,
/sys or /proc with it (the incident on box 181). They are unmounted one by one, never with umount -R, and a folder
with anything mounted under it is never removed. Stdlib only.
"""

import shutil
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import tempfile
from pathlib import Path

from irate_box.msg import M, Refused

POLICY = "#!/bin/sh\nexit 101\n"
BINDS = ("proc", "sys", "dev")


def run(*cmd, timeout=3600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _check(result, what):
    if result.returncode:
        raise Refused(
            M("abslot.failed", "{what} failed: {why}", what=what, why=(result.stderr or result.stdout).strip()[-300:])
        )
    return result


def _dir(path):
    if path.is_symlink():
        raise Refused(M("abslot.symlink", "The slot has a link where {path} should be a folder.", path=str(path)))
    path.mkdir(parents=True, exist_ok=True)


def _mount(target, run=run):
    mounted = []
    try:
        for name in BINDS:
            source = Path("/") / name
            dest = target / name
            _dir(dest)
            _check(run("mount", "--rbind", str(source), str(dest)), f"bind {name}")
            _check(run("mount", "--make-rslave", str(dest)), f"make {name} private")
            mounted.append(dest)
    except Exception:
        _unmount(mounted, run)
        raise
    return mounted


def _under(target, run=run):
    """Every mount at or below target, deepest first; findmnt only reads, it unmounts nothing."""
    said = run("findmnt", "-R", "-n", "-o", "TARGET", str(target))
    found = [line.strip() for line in (said.stdout or "").splitlines() if line.strip()]
    return sorted(set(found or [str(target)]), key=len, reverse=True)


def _unmount(mounts, run=run):
    left = []
    for target in reversed(mounts):
        for point in _under(target, run):
            result = run("umount", point)
            if result.returncode:
                left.append(point)
    return left


def _policy(slot):
    """A policy-rc.d that starts nothing, with the slot's own put back afterwards."""
    path = slot / "usr" / "sbin" / "policy-rc.d"
    _dir(path.parent)
    if path.is_symlink():
        raise Refused(M("abslot.symlink", "The slot has a link where {path} should be a folder.", path=str(path)))
    before = path.read_bytes() if path.exists() else None
    path.write_text(POLICY)
    path.chmod(0o755)
    return path, before


def patch(slot, running="/", *, apt_lists=None, run=run):
    """Patch one inactive slot from the running system. Returns the report: commands and the check's differences."""
    slot, running = Path(slot).resolve(), Path(running).resolve()
    if not slot.is_dir():
        raise Refused(M("abslot.no-slot", "{path} is not a folder.", path=str(slot)))
    if slot == running or slot in running.parents:
        raise Refused(M("abslot.running", "That is the running system, not an inactive slot."))
    lists = Path(apt_lists) if apt_lists else Path(tempfile.mkdtemp(prefix="abslot-lists-"))
    lists.mkdir(parents=True, exist_ok=True)
    mounts = []
    policy, before = _policy(slot)
    try:
        mounts = _mount(slot, run)
        lists_dest = slot / "var" / "lib" / "apt" / "lists"
        _dir(lists_dest)
        _check(run("mount", "--rbind", str(lists), str(lists_dest)), "bind apt lists")
        _check(run("mount", "--make-rslave", str(lists_dest)), "make apt lists private")
        mounts.append(lists_dest)
        # The running slot's settings come over before apt and irate-box run in the slot. Its state is outside /etc.
        _check(run("rsync", "-a", "--delete", f"{running}/etc/", f"{slot}/etc/"), "copy /etc")
        _check(
            run("chroot", str(slot), "apt-get", "update", "-o", "Dir::State::Lists=/var/lib/apt/lists"),
            "apt-get update",
        )
        _check(
            run("chroot", str(slot), "apt-get", "-y", "full-upgrade", "-o", "Dir::State::Lists=/var/lib/apt/lists"),
            "apt-get full-upgrade",
        )
        updater = slot / "opt" / "irate-box" / "irate-box"
        if updater.is_file():
            _check(run("chroot", str(slot), "/opt/irate-box/irate-box", "update"), "irate-box update in the slot")
        verify = run("chroot", str(slot), "dpkg", "--verify")
        changes = _check(
            run(
                "rsync",
                "-a",
                "--checksum",
                "--dry-run",
                "--itemize-changes",
                "--exclude=/proc",
                "--exclude=/sys",
                "--exclude=/dev",
                "--exclude=/run",
                "--exclude=/tmp",
                "--exclude=/var/lib/hub",
                "--exclude=/var/lib/apt/lists",
                str(running) + "/",
                str(slot) + "/",
            ),
            "rsync check",
        )
        return {
            "slot": str(slot),
            "verify": (verify.stdout or "").strip().splitlines(),
            "changes": (changes.stdout or "").strip().splitlines(),
            "mounts": [str(m) for m in mounts],
        }
    finally:
        left = _unmount(mounts, run)
        if before is None:
            policy.unlink(missing_ok=True)
        else:
            policy.write_bytes(before)
            policy.chmod(0o755)
        if left:
            raise Refused(
                M("abslot.left-mounted", "These mounts could not be unmounted: {list}.", list=", ".join(left))
            )
        if apt_lists is None:
            shutil.rmtree(lists, ignore_errors=True)
