# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Housecall doctor: is a card, a card image or this box's own files healthy?

Box 190's card passed e2fsck, f3probe and badblocks and still lost 55 files, each read back whole as 0xFF.
This doctor is the examination the case did by hand, on a target the owner points it at: a disk image, a card
device, or this box's own files. It reads only: the image's own filesystem, through e2fsprogs without mounting,
or the folders on the card when it runs on the box.

  irate-box housecall --image card.img [--reference refs.json]
  irate-box housecall --device /dev/sda [--reference refs.json]
  irate-box housecall --root /some/folder [--reference refs.json] [--dpkg] [--git-tree]
  irate-box housecall --this-card [--reference refs.json] [--dpkg] [--git-tree]

Each file gets a verdict (notes: housecall-doctor-plan, "Identifying the damage") from a reference of SHA-256,
MD5 (a package's .md5sums) or a git blob SHA-1 (irate-box's own tree):
  genuine   its SHA-256 matches a reference that should match
  damaged   it is entirely 0xFF, or it differs from its reference
  suspect   it is entirely zero and there is no reference
  unknown   no reference, and nothing wrong on its face (a partial 0xFF block can be genuine padding)

The report is control/housecall.json, in the doctors' finding shape. Stdlib only.

The image is examined without loop devices or mounts, so it works on the Lyra and needs no root for the reading
itself: sfdisk names the partitions, blkid the filesystem at each offset, e2fsck -n checks the structure, and
debugfs lists and streams each file (a whole-disk filesystem works too). A card device needs no loop either:
blkid and debugfs read it read-only, and it is refused while mounted or if it is the box's own disk.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys
import threading
import time
from pathlib import Path

from irate_box.finding import action, finding
from irate_box.msg import M, Refused, render
from irate_box.root import restore, safeio

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
REPORT = CONTROL / "housecall.json"
CHUNK = 4096
SKIP_DIRS = {"proc", "sys", "dev", "run"}
# The hub's large folders: content fetched again, or to be checked against its release's checksums, never read
# whole from inside (a Lyra's build caches alone were 14 GB in 231 280 files). As nand-logs.sh's
# HUB_LARGE.
LARGE = ("zim", "firmware", "kits", "git", "library", "source", "drop", "ci", "factory-targets")
# This box's own files, as the plan's "from inside" lists them: the settings, the code, the hub's state, root's
# records, and systemd's units (its drop-ins in /etc/systemd are read with /etc). Only the files whose mount is the
# SD card are read, and not the hub's large folders.
CARD_FOLDERS = (
    "/etc",
    "/opt/irate-box",
    "/var/lib/hub",
    "/var/lib/irate-box-root",
    "/lib/systemd/system",
    "/usr/lib/systemd/system",
)
FF = b"\xff"
ZERO = b"\x00"


def run(*cmd, timeout=1800, binary=False):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, b"" if binary else "", b"" if binary else str(exc))


def _text(result):
    return ((result.stdout or "") + (result.stderr or "")).strip()


def file_facts(chunks, size=0):
    """(blocks, ff_blocks, zero_blocks, sha256, md5, git, bytes): the facts a verdict needs, from the file's chunks.
    `git` is the blob SHA-1 that a git tree names the file by (the header needs the size before the data)."""
    # MD5 and SHA-1 only to match what dpkg and git name files by, never for security.
    sha, md5, git = hashlib.sha256(), hashlib.md5(usedforsecurity=False), hashlib.sha1(usedforsecurity=False)
    git.update(f"blob {size}\0".encode())
    blocks, ff_blocks, zero_blocks, nbytes = 0, 0, 0, 0
    for chunk in chunks:
        blocks += 1
        nbytes += len(chunk)
        sha.update(chunk)
        md5.update(chunk)
        git.update(chunk)
        if chunk == FF * len(chunk):
            ff_blocks += 1
        elif chunk == ZERO * len(chunk):
            zero_blocks += 1
    return blocks, ff_blocks, zero_blocks, sha.hexdigest(), md5.hexdigest(), git.hexdigest(), nbytes


def _verdict(path, size, blocks, ff_blocks, zero_blocks, digest, references, nbytes=None):
    """The verdict for one file, and why, as the plan's four kinds. A reference is one algorithm and digest, or
    a dict of them ({"sha256": …}, {"md5": …}, {"git": …})."""
    if size is not None and nbytes is not None and nbytes < size:
        return "damaged", M(
            "housecall.why-short", "It read back short: {got} of its {size} bytes.", got=nbytes, size=size
        )
    wanted = _normalise_reference(references.get(path))
    if wanted and any(digest.get(algo) == value for algo, value in wanted.items() if algo != "manifest"):
        return "genuine", None
    if blocks and ff_blocks == blocks:
        return "damaged", M("housecall.why-ff", "It reads back whole as 0xFF.")
    if blocks and zero_blocks == blocks:
        if wanted:
            return "damaged", M("housecall.why-zero-damaged", "It reads back whole as zero.")
        return "suspect", M("housecall.why-zero", "It reads back whole as zero, and there is no reference.")
    if wanted:
        if wanted.get("manifest"):
            return "changed", M(
                "housecall.why-changed", "It changed since Box doctor last copied it. That is a change, not damage."
            )
        return "damaged", M("housecall.why-reference", "It differs from its reference.")
    return "unknown", None


def _unreadable(path, size, exc):
    """A file that could not be read at all: the strongest sign of damage, never left out of the count."""
    return {
        "path": path,
        "verdict": "damaged",
        "size": size,
        "sha256": None,
        "blocks": 0,
        "ff_blocks": 0,
        "reason": M("housecall.why-unreadable", "It could not be read: {why}", why=str(exc)[:160]),
    }


def _normalise_reference(value):
    """A reference as {algorithm: digest}: a bare string is a SHA-256, a {"sha256": …} or {"md5": …} is itself."""
    if isinstance(value, str):
        return {"sha256": value}
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in ("sha256", "md5", "git", "manifest") and isinstance(v, str)}
    return {}


def _record(path, size, chunks, references):
    blocks, ff_blocks, zero_blocks, sha256, md5, git, nbytes = file_facts(chunks, size)
    digest = {"sha256": sha256, "md5": md5, "git": git}
    verdict, why = _verdict(path, size, blocks, ff_blocks, zero_blocks, digest, references, nbytes)
    return {
        "path": path,
        "verdict": verdict,
        "size": size,
        "sha256": sha256,
        "md5": md5,
        "git": git,
        "blocks": blocks,
        "ff_blocks": ff_blocks,
        "reason": why,
    }


def _direct_chunks(path):
    """A file's 4 KB chunks, read past the cache: a box in use can hold the copy it believes it wrote."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except (AttributeError, OSError):
            pass
        while chunk := os.read(fd, CHUNK):
            yield chunk
    finally:
        os.close(fd)


def scan_dir(root, references=None, on_card=False, skipped=None, prefix="", skip=()):
    """Every regular file under root, read past the cache. on_card leaves out files not on the SD card (for
    --this-card: the Lyra's NAND state is the NAND's to check), and names them in skipped when it is a list.
    prefix is put before the path (this box's folders, so a report names /etc/… not /hostname); skip leaves out
    those folders whole."""
    root = Path(root)
    references = references or {}
    skip = {Path(p) for p in skip}
    out = []
    for directory, subdirs, files in os.walk(root):
        if Path(directory) == root:
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
        subdirs[:] = [d for d in subdirs if Path(directory) / d not in skip]
        for name in sorted(files):
            path = Path(directory) / name
            try:
                metadata = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
                continue
            if on_card and not _on_card(path):
                if skipped is not None:
                    skipped.append(str(path))
                continue
            relative = "/" + str(path.relative_to(root))
            if prefix:
                relative = prefix.rstrip("/") + relative
            try:
                record = _record(relative, metadata.st_size, _direct_chunks(path), references)
            except OSError as exc:
                record = _unreadable(relative, metadata.st_size, exc)
            out.append(record)
    return out


def _on_card(path):
    """Whether path is on the SD card (its mount's source is the card), by Box doctor's own mount lookup; True where
    the card cannot be named."""
    from irate_box.hub import cardhistory
    from irate_box.root import health

    found_card = cardhistory.card()
    if not found_card:
        return True
    return health._on_card(path, found_card[0])


LS_RE = re.compile(r"^/(\d+)/([0-7]+)/\d+/\d+/(.*)$")


def _entries(source, inode=2):
    """(name, inode, mode, size) in a directory of an ext filesystem, from debugfs's machine-readable ls."""
    said = run("debugfs", "-R", f"ls -p <{inode}>", source)
    if said.returncode:
        raise Refused(
            M(
                "housecall.debugfs",
                "The filesystem could not be read: {why}",
                why=_text(said)[-200:] or "debugfs failed",
            )
        )
    for line in said.stdout.splitlines():
        match = LS_RE.match(line)
        if not match:
            continue
        child, mode, rest = int(match[1]), int(match[2], 8), match[3]
        if stat.S_ISDIR(mode):
            name = rest.rstrip("/")
            if name in ("", ".", ".."):
                continue
            yield name, child, mode, None
        elif stat.S_ISREG(mode):
            rest = rest.rstrip("/")
            name, _, size = rest.rpartition("/")
            yield name, child, mode, int(size or 0)


def _ext_chunks(source, inode):
    """A file's chunks from debugfs, streamed rather than held in memory."""
    proc = subprocess.Popen(  # noqa: TID251 - streamed: a book-sized file is never held in memory
        ["debugfs", "-R", f"cat <{inode}>", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        while chunk := proc.stdout.read(CHUNK):
            yield chunk
        if proc.wait() != 0:
            raise OSError(f"debugfs could not read inode {inode}")
    finally:
        proc.stdout.close()
        proc.wait()


def _scan_ext(source, references=None, prefix=""):
    """Every regular file in an ext filesystem, through debugfs (no mount, no write)."""
    references = references or {}
    out = []

    def walk(inode, base):
        for name, child, mode, size in _entries(source, inode):
            path = f"{base}/{name}"
            if stat.S_ISDIR(mode):
                walk(child, path)
            else:
                try:
                    record = _record(path, size, _ext_chunks(source, child), references)
                except OSError as exc:
                    record = _unreadable(path, size, exc)
                out.append(record)

    walk(2, prefix.rstrip("/"))
    return out


def partitions_table(path):
    """The image's partition table, from sfdisk: (label, [{node, start, size, type}], why)."""
    said = run("sfdisk", "-d", "--json", str(path), timeout=120)
    try:
        data = json.loads(said.stdout)
        table = data["partitiontable"]
    except (ValueError, KeyError, TypeError):
        return None, [], _text(said)[-200:] or "no partition table"
    parts = []
    for part in table.get("partitions") or []:
        parts.append(
            {
                "node": part.get("node") or "",
                "start": int(part.get("start") or 0),
                "size": int(part.get("size") or 0),
                "type": str(part.get("type") or ""),
            }
        )
    return table.get("label") or "unknown", parts, None


def blkid(path, offset=0):
    """The filesystem at an image offset (or on a device), from blkid, or None."""
    args = ["blkid", "-p"]
    if offset:
        args += ["-O", str(offset)]
    said = run(*args, str(path), timeout=120)
    if said.returncode:
        return None
    found = re.search(r'\bTYPE="([^"]+)"', said.stdout)
    return found[1] if found else None


def _filesystem(source, name, fstype, references):
    """One filesystem's structure check and file scan; (findings, file records)."""
    out, records = [], []
    if fstype.startswith("ext"):
        checked = run("e2fsck", "-fn", source, timeout=7200)
        out.append(
            finding(
                f"housecall-fs:{name}",
                M("housecall.fs.title", "Filesystem {name}", name=name),
                "ok" if checked.returncode == 0 else "warn",
                M("housecall.fs.clean", "The filesystem is clean.")
                if checked.returncode == 0
                else M("housecall.fs.errors", "Its structure has errors. Nothing was changed."),
                notes=[M("housecall.fs.structure", "A filesystem check reads its structure, not its files' contents.")],
                evidence=[M("housecall.fs.type", "Type: {type}", type=fstype)]
                if checked.returncode == 0
                else [_text(checked)[-300:]],
            )
        )
        try:
            records = _scan_ext(source, references)
            out.append(_files_finding(records, name))
        except Refused as exc:
            out.append(
                finding(
                    f"housecall-files:{name}",
                    M("housecall.files.title", "Files on {name}", name=name),
                    "warn",
                    M("housecall.files.unreadable", "The files could not be read: {why}", why=str(exc)),
                )
            )
    else:
        out.append(
            finding(
                f"housecall-fs:{name}",
                M("housecall.fs.title", "Filesystem {name}", name=name),
                "warn",
                M("housecall.fs.not-checked", "{type} is not a filesystem this doctor reads yet.", type=fstype),
                notes=[M("housecall.fs.not-checked.note", "Only ext2, ext3 and ext4 are examined now.")],
            )
        )
    return out, records


def _files_finding(records, where):
    """One finding for a filesystem's files: the damaged and suspect ones named, the rest counted."""
    damaged = [r for r in records if r["verdict"] == "damaged"]
    suspect = [r for r in records if r["verdict"] == "suspect"]
    changed = [r for r in records if r["verdict"] == "changed"]
    genuine = [r for r in records if r["verdict"] == "genuine"]
    counts = {
        "damaged": len(damaged),
        "suspect": len(suspect),
        "changed": len(changed),
        "genuine": len(genuine),
        "unknown": len([r for r in records if r["verdict"] == "unknown"]),
    }
    notes = []
    if suspect:
        notes.append(M("housecall.files.suspect", "{n} more read as all zero.", n=len(suspect)))
    if changed:
        notes.append(
            M(
                "housecall.files.changed",
                {
                    "one": "{n} changed since Box doctor last copied it: a change, not damage.",
                    "other": "{n} changed since Box doctor last copied it: changes, not damage.",
                },
                n=len(changed),
            )
        )
    evidence = [
        f"{r['path']}: {r['verdict']}" + (f" ({render(r['reason'])})" if r.get("reason") else "")
        for r in (damaged + suspect)[:60]
    ]
    if len(damaged) + len(suspect) > 60:
        evidence.append(f"… and {len(damaged) + len(suspect) - 60} more")
    acts = []
    if damaged:
        labels = {
            "copy": M("housecall.restore.copy", "Restore from Box doctor's copy"),
            "git": M("housecall.restore.git-label", "Restore from irate-box's tree"),
            "package": M("housecall.restore.package-label", "Reinstall its package"),
        }
        acts = [
            action(
                "housecall-restore",
                labels.get(
                    r["restore"]["kind"], M("housecall.restore.action", "Restore {name}", name=Path(r["path"]).name)
                ),
                M(
                    "housecall.restore.confirm",
                    "Put {path} back? The damaged file is kept beside it as name.damaged.",
                    path=r["path"],
                ),
                path=r["path"],
                kind=r["restore"]["kind"],
            )
            for r in damaged
            if r.get("restore")
        ]
    if damaged:
        return finding(
            f"housecall-files:{where}",
            M("housecall.files.title", "Files on {name}", name=where),
            "problem",
            M(
                "housecall.files.damaged",
                {"one": "{n} file is damaged.", "other": "{n} files are damaged."},
                n=len(damaged),
            ),
            M(
                "housecall.files.damaged.why",
                "A file that reads back as 0xFF, or differs from its reference, was not kept by the card.",
            ),
            M(
                "housecall.files.damaged.fix",
                "Replace the card, or restore the named files from a copy. Box doctor's boot check mends the "
                "settings and keys it knows.",
            ),
            notes=notes,
            evidence=evidence,
            actions=acts,
            counts=counts,
        )
    if suspect:
        return finding(
            f"housecall-files:{where}",
            M("housecall.files.title", "Files on {name}", name=where),
            "warn",
            M(
                "housecall.files.suspect.only",
                {
                    "one": "{n} file reads back as all zero and has no reference.",
                    "other": "{n} files read back as all zero and have no reference.",
                },
                n=len(suspect),
            ),
            notes=[M("housecall.files.suspect.why", "An empty or zeroed file can be genuine."), *notes[1:]],
            evidence=evidence,
            counts=counts,
        )
    if changed:
        return finding(
            f"housecall-files:{where}",
            M("housecall.files.title", "Files on {name}", name=where),
            "warn",
            M(
                "housecall.files.changed.only",
                {
                    "one": "{n} file changed since Box doctor last copied it. It is not damage.",
                    "other": "{n} files changed since Box doctor last copied it. They are not damage.",
                },
                n=len(changed),
            ),
            notes=[
                M("housecall.files.changed.why", "The next copy takes the new contents. Nothing is restored over it.")
            ],
            evidence=[f"{r['path']}: changed" for r in changed[:60]],
            counts=counts,
        )
    return finding(
        f"housecall-files:{where}",
        M("housecall.files.title", "Files on {name}", name=where),
        "ok",
        M(
            "housecall.files.ok",
            {
                "one": "{n} file was read whole. It does not read as 0xFF. {genuine} match a reference.",
                "other": "{n} files were read whole. None reads as 0xFF. {genuine} match a reference.",
            },
            n=len(records),
            genuine=len(genuine),
        ),
        counts=counts,
    )


def examine_image(path, references=None):
    """An image or card, read only; the plan's step 1 and per-file verdict on an image."""
    path = Path(path).resolve()
    if not path.is_file():
        raise Refused(M("housecall.not-image", "{path} is not a disk image file.", path=str(path)))
    before = path.stat()
    label, parts, why = partitions_table(path)
    findings = [
        finding(
            "housecall-target",
            M("housecall.target.title", "Target"),
            "ok",
            M(
                "housecall.target.image",
                "The image {name} ({mb} MB) is being read. Nothing is written to it.",
                name=path.name,
                mb=before.st_size >> 20,
            ),
            evidence=[str(path), "sha256 not read: the image is never opened for writing"],
        )
    ]
    if parts:
        findings.append(
            finding(
                "housecall-partitions",
                M("housecall.partitions.title", "Partition table"),
                "ok",
                M(
                    "housecall.partitions.ok",
                    {
                        "one": "The {label} table lists {n} partition.",
                        "other": "The {label} table lists {n} partitions.",
                    },
                    label=label,
                    n=len(parts),
                ),
                evidence=[f"{p['node'] or p['start']}: {p['type']}" for p in parts],
            )
        )
        targets = [
            (
                f"{path}?offset={p['start'] * 512}" if p["start"] else str(path),
                Path(p["node"]).name if p["node"] else f"partition {p['start']}",
                p,
            )
            for p in parts
        ]
    else:
        fstype = blkid(path, 0)
        if not fstype:
            findings.append(
                finding(
                    "housecall-partitions",
                    M("housecall.partitions.title", "Partition table"),
                    "problem",
                    M("housecall.partitions.none", "There is no partition table and no filesystem at the start."),
                    notes=[M("housecall.partitions.why", "The image can be damaged, or not a card image.")],
                    evidence=[why or ""],
                )
            )
            return _report(path, findings, [], before)
        targets = [(str(path), "whole disk", {"type": fstype})]
    records = []
    for source, name, part in targets:
        fstype = blkid(path, part.get("start", 0) * 512) if part.get("start") else blkid(path, 0)
        if not fstype:
            findings.append(
                finding(
                    f"housecall-partition:{name}",
                    M("housecall.partition.title", "Partition {name}", name=str(name)),
                    "warn",
                    M("housecall.partition.no-fs", "No filesystem the box can name is on it."),
                )
            )
            continue
        said, records_here = _filesystem(source, str(name), fstype, references)
        findings += said
        records += records_here
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        findings.append(
            finding(
                "housecall-changed",
                M("housecall.changed.title", "The image changed"),
                "problem",
                M("housecall.changed.said", "The image's size or modification time changed while it was read."),
            )
        )
    return _report(path, findings, records, before)


def examine_device(path, references=None):
    """A card device in a reader, read only. Refused while mounted or if it is the box's own disk."""
    path = Path(path).resolve()
    if not path.exists():
        raise Refused(M("housecall.not-device", "{path} is not a device.", path=str(path)))
    said = run("lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,START,MOUNTPOINTS,PKNAME", str(path), timeout=60)
    try:
        node = (json.loads(said.stdout).get("blockdevices") or [])[0]
    except (ValueError, IndexError):
        raise Refused(M("housecall.lsblk", "The device could not be read: {why}", why=_text(said)[-200:])) from None
    own = _own_disk(path)
    if own:
        raise Refused(M("housecall.own-disk", "That is the box's own disk. Point the doctor at a card in a reader."))
    children = node.get("children") or []
    for child in [node, *children]:
        mounts = child.get("mountpoints") or []
        if any(mounts):
            raise Refused(M("housecall.mounted", "The card is mounted. Unmount it first, then examine it."))
    findings = [
        finding(
            "housecall-target",
            M("housecall.target.title", "Target"),
            "ok",
            M("housecall.target.device", "The device {name} is being read. Nothing is written to it.", name=path.name),
            evidence=[str(path)],
        )
    ]
    records = []
    for child in children or [node]:
        name, part_path, fstype = (
            child.get("name") or path.name,
            child.get("path") or str(path),
            child.get("fstype") or "",
        )
        fstype = fstype or blkid(part_path)
        if not fstype:
            continue
        said, records_here = _filesystem(part_path, name, fstype, references)
        findings += said
        records += records_here
    return _report(path, findings, records, None)


MIN_IMAGE = 1 << 20


def _source_size(device):
    """The card's size in bytes, from blockdev (its whole size, whatever is on it) or the file's own size."""
    said = run("blockdev", "--getsize64", str(device), timeout=60)
    if said.returncode == 0 and said.stdout.strip().isdigit():
        return int(said.stdout.strip())
    try:
        return os.stat(device).st_size
    except OSError as exc:
        raise Refused(M("housecall.image-first.no-source", "The card cannot be read: {why}", why=str(exc))) from None


def _destination_on_card(image):
    """Whether image would be written on this box's own SD card (by the card's mount, as Box doctor finds it)."""
    try:
        from irate_box.hub import cardhistory
        from irate_box.root import health

        found = cardhistory.card()
        return bool(found) and health._on_card(Path(image).parent, found[0])
    except (OSError, ValueError):
        return False


def _free_bytes(folder):
    """The space free on folder's filesystem, or None where it cannot be read."""
    try:
        stats = os.statvfs(folder)
        return stats.f_bavail * stats.f_frsize
    except OSError:
        return None


def copy_device(device, image, on_own_card=False, progress=None):
    """The card imaged first, read only. dd reads 4 KiB at a time with noerror and sync (a bad sector is zero-filled,
    not the whole 4 MiB block) and the kernel's read-ahead, and the image is trimmed to the card's exact size: a card
    is rarely a whole number of blocks. It is written as <image>.part and renamed only when whole, so a stopped or failed copy never looks like a
    card. It is refused on this box's own card unless the owner says so."""
    device, image = Path(device).resolve(), Path(image).resolve()
    if not image.parent.is_dir():
        raise Refused(
            M("housecall.image-first.no-folder", "The folder {folder} is not there.", folder=str(image.parent))
        )
    if image.exists():
        raise Refused(M("housecall.image-first.exists", "{path} is already there. Select a new name.", path=str(image)))
    if _destination_on_card(image) and not on_own_card:
        raise Refused(
            M(
                "housecall.image-first.own-card",
                "That image would be written on this box's own card. Tick that it can be, or select a USB drive.",
            )
        )
    size = _source_size(device)
    free = _free_bytes(image.parent)
    if free is not None and free < size + MIN_IMAGE:
        raise Refused(
            M(
                "housecall.image-first.space",
                "The image needs {need} MB, and {free} MB is free there. Select another folder.",
                need=size >> 20,
                free=max(free, 0) >> 20,
            )
        )
    part = image.with_name(image.name + ".part")
    part.unlink(missing_ok=True)
    args = ["dd", f"if={device}", f"of={part}", "bs=4096", "conv=noerror,sync,fsync", "status=none"]
    if progress:
        progress(0, size)
    stop = threading.Event()

    def watch():
        while not stop.wait(1):
            try:
                done = part.stat().st_size
            except OSError:
                continue
            progress(min(done, size), size)

    watcher = threading.Thread(target=watch, daemon=True) if progress else None
    if watcher:
        watcher.start()
    said = run(*args, timeout=6 * 3600)
    stop.set()
    if watcher:
        watcher.join(timeout=2)
    errors = (said.stderr or "").count("error reading")
    if (said.returncode and not errors) or not part.exists() or part.stat().st_size < size:
        part.unlink(missing_ok=True)
        raise Refused(
            M(
                "housecall.image-first.failed",
                "The card was not imaged: {why}",
                why=(_text(said)[-200:] or "the copy stopped or wrote less than the card"),
            )
        )
    if part.stat().st_size > size:
        os.truncate(part, size)
    if part.stat().st_size != size:
        part.unlink(missing_ok=True)
        raise Refused(M("housecall.image-first.failed", "The card was not imaged: {why}", why="the size is wrong"))
    os.replace(part, image)
    if progress:
        progress(size, size)
    return M(
        "housecall.image-first.done",
        "The card was imaged to {image} ({mb} MB){errors} before it was read.",
        image=image.name,
        mb=size >> 20,
        errors="" if not errors else f", with {errors} read error(s) zero-filled",
    )


def image_first_finding(device, image, said):
    """The finding that says the card was imaged first, and that the examination ran on the image."""
    return finding(
        "housecall-image-first",
        M("housecall.image-first.title", "Image first"),
        "ok",
        said,
        notes=[
            M(
                "housecall.image-first.read-only",
                "The card itself was only read. The examination ran on the image, so the card was not opened again.",
            )
        ],
        evidence=[f"{device} -> {image}"],
    )


def examine_image_first(device, image, references=None, on_own_card=False, progress=None):
    """Image the card first, then examine the image: everything after the copy runs on the image, never the card."""
    said = copy_device(device, image, on_own_card=on_own_card, progress=progress)
    report = examine_image(image, references)
    report["source_device"] = str(Path(device).resolve())
    report["image"] = str(Path(image).resolve())
    report["findings"].insert(0, image_first_finding(device, image, said))
    return report


def _parent_disk(path):
    """The disk a partition is on (lsblk's PKNAME: mmcblk0 for /dev/mmcblk0p2), or '' for a whole disk."""
    return run("lsblk", "-n", "-d", "-o", "PKNAME", str(path), timeout=30).stdout.strip()


def _own_disk(path):
    """Whether path is the box's own root disk, or a partition of it."""
    said = run("findmnt", "-n", "-o", "SOURCE", "/", timeout=30).stdout.strip()
    if not said or not Path(said).exists():
        return False
    disk = _parent_disk(said) or Path(said).name
    try:
        root_st, path_st = os.stat(said), os.stat(path)
    except OSError:
        return False
    if stat.S_ISBLK(root_st.st_mode) and stat.S_ISBLK(path_st.st_mode) and root_st.st_rdev == path_st.st_rdev:
        return True
    return Path(path).name == disk or _parent_disk(path) == disk


def examine_root(root, references=None, manifest=None):
    """A folder the owner points at, read past the cache: the same per-file verdicts, paths from that folder."""
    root = Path(root)
    if not root.is_dir():
        raise Refused(M("housecall.not-folder", "{path} is not a folder.", path=str(root)))
    manifests = manifest_entries(manifest) if manifest else None
    if manifest:
        references = merge_references(references, load_manifest(manifest))
    records = plan_restore(scan_dir(root, references), root, manifests=manifests)
    findings = [
        finding(
            "housecall-target",
            M("housecall.target.title", "Target"),
            "ok",
            M("housecall.target.root", "The folder {path} is being read. Nothing is written.", path=str(root)),
            evidence=[str(root)],
        ),
        _files_finding(records, str(root)),
    ]
    report = _report(root, findings, records, None)
    report["root"] = str(root)
    return report


def examine_this_card(references=None, roots=None, manifest=None):
    """This box's own files on the SD card (the plan's step 4): the folders the box writes and systemd's, card
    files only, the hub's large folders left out. A file on the NAND (nand-logs' state) is left out and said to be
    the NAND's, whose UBIFS checks it."""
    if manifest:
        references = merge_references(references, load_manifest(manifest))
    folders = [Path(root) for root in (roots or CARD_FOLDERS)]
    large = [STATE / name for name in LARGE if (STATE / name).is_dir()]
    records, skipped = [], []
    for root in folders:
        if root.is_dir():
            records += scan_dir(root, references, on_card=True, skipped=skipped, prefix=str(root), skip=large)
    notes = []
    if large:
        notes.append(
            M(
                "housecall.this-card.large",
                "Left out: the hub's large folders ({names}). They are fetched again, or checked against their "
                "release's checksums, rather than read whole from inside.",
                names=", ".join(p.name for p in large),
            )
        )
    records = plan_restore(records, "/", manifests=manifest_entries(manifest) if manifest else None)
    findings = [
        finding(
            "housecall-target",
            M("housecall.target.title", "Target"),
            "ok",
            M("housecall.target.this-card", "The box's own files on the SD card are being read. Nothing is written."),
            notes=notes,
            evidence=[str(r) for r in folders],
        ),
        _files_finding(records, "this card"),
    ]
    if skipped:
        findings.append(
            finding(
                "housecall-scope",
                M("housecall.scope.title", "Files left out"),
                "ok",
                M(
                    "housecall.scope.nand",
                    {
                        "one": "{n} file was left out: it is not on the SD card.",
                        "other": "{n} files were left out: they are not on the SD card.",
                    },
                    n=len(skipped),
                ),
                notes=[
                    M(
                        "housecall.scope.ubifs",
                        "The onboard flash's own UBIFS checks its data, including the Lyra's state when it is kept there.",
                    )
                ],
                evidence=skipped[:40] + ([f"… and {len(skipped) - 40} more"] if len(skipped) > 40 else []),
            )
        )
    report = _report(None, findings, records, None)
    report["root"] = "/"
    return report


def running_report(target, note=None):
    """What the page shows while an examination runs (the root helper writes it as it starts the unit). A note
    replaces the usual line while the card is being imaged first."""
    now = time.time()
    said = note or M(
        "housecall.running.said",
        "The doctor is examining {target}. Nothing is written to it.",
        target=target,
    )
    return {
        "at": now,
        "running": True,
        "started": now,
        "target": target,
        "findings": [
            finding(
                "housecall-running",
                M("housecall.running.title", "Examining"),
                "ok",
                said,
            )
        ],
        "files": [],
        "counts": {"files": 0, "damaged": 0, "suspect": 0, "genuine": 0, "unknown": 0},
    }


def ended_report(target, why):
    """An examination that ended without a report: refused, failed, or stopped."""
    report = running_report(target or "the target")
    report["running"] = False
    report["findings"] = [
        finding(
            "housecall-ended",
            M("housecall.ended.title", "Not examined"),
            "warn",
            M("housecall.ended.said", "The examination ended without a report: {why}", why=str(why)[:300]),
        )
    ]
    return report


def _report(target, findings, records, before):
    damaged = [r for r in records if r["verdict"] == "damaged"]
    suspect = [r for r in records if r["verdict"] == "suspect"]
    return {
        "at": time.time(),
        "target": str(target) if target else "this box's card",
        "findings": findings,
        "files": [r for r in records if r["verdict"] in ("damaged", "suspect", "changed", "genuine")],
        "counts": {
            "files": len(records),
            "damaged": len(damaged),
            "suspect": len(suspect),
            "changed": len([r for r in records if r["verdict"] == "changed"]),
            "genuine": len([r for r in records if r["verdict"] == "genuine"]),
            "unknown": len([r for r in records if r["verdict"] == "unknown"]),
        },
        "size": before.st_size if before else None,
    }


def load_references(path):
    """A reference manifest: {"files": {"/path": "sha256"}} or {"/path": "sha256"}, path→sha."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(M("housecall.references", "The reference file could not be read: {why}", why=str(exc))) from None
    files = data.get("files", data) if isinstance(data, dict) else {}
    out = {}
    for key, value in files.items():
        if not isinstance(key, str):
            continue
        reference = _normalise_reference(value)
        if reference and all(
            re.fullmatch(r"[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64}", digest) for digest in reference.values()
        ):
            out["/" + key.lstrip("/")] = reference
    return out


def merge_references(*groups):
    """Several reference maps as one; a later one adds to a path's algorithms rather than replacing them."""
    out = {}
    for group in groups:
        for path, value in (group or {}).items():
            out.setdefault(path, {}).update(_normalise_reference(value))
    return out


def references_from_dpkg(root="/"):
    """Every installed package's files, by the MD5 in /var/lib/dpkg/info/*.md5sums: {path: {"md5": …}}."""
    info = Path(root) / "var/lib/dpkg/info"
    out = {}
    for sums in sorted(info.glob("*.md5sums")) if info.is_dir() else []:
        try:
            lines = sums.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            digest, _, relative = line.strip().partition("  ")
            if not re.fullmatch(r"[0-9a-f]{32}", digest) or not relative:
                continue
            path = "/" + relative.lstrip("/")
            if (Path(root) / relative).exists():
                out[path] = {"md5": digest}
    return out


def installed_commit(root="/"):
    """The commit in /opt/irate-box/VERSION, or None. The installer writes it short ("4ccfedc (installed …)"), so
    7 to 40 hex digits; references_from_git resolves it in the checkout."""
    try:
        first = (Path(root) / "opt/irate-box/VERSION").read_text().split()[0]
    except (OSError, IndexError):
        return None
    return first if re.fullmatch(r"[0-9a-f]{7,40}", first) else None


def _references_from_git_commit(repo, commit):
    """irate-box's whole tree at commit, by the git blob SHA-1 in the updater's checkout."""
    repo = Path(repo)
    full = run("git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    if full.returncode == 0 and full.stdout.strip():
        commit = full.stdout.strip()
    # The whole tree: /opt/irate-box holds all of it (web/, install.sh, tools/ …), not only irate_box/.
    said = run("git", "-C", str(repo), "ls-tree", "-r", "-z", commit, binary=True)
    if said.returncode:
        raise Refused(
            M(
                "housecall.git.no-tree",
                "The git checkout does not have {commit}: {why}",
                commit=commit,
                why=(said.stderr or b"").decode(errors="replace").strip()[-200:],
            )
        )
    out = {}
    for entry in said.stdout.split(b"\0"):
        if not entry:
            continue
        meta, _, relative = entry.partition(b"\t")
        fields = meta.split()
        if len(fields) < 3 or fields[1] != b"blob":
            continue
        out["/opt/irate-box/" + relative.decode(errors="surrogateescape")] = {"git": fields[2].decode()}
    return out


def references_from_git(root="/", repo=None, commit=None):
    """irate-box's own tree at the installed commit, by the git blob SHA-1 in the updater's checkout:
    {/opt/irate-box/path: {"git": …}}."""
    commit = commit or installed_commit(root)
    if not commit:
        raise Refused(
            M("housecall.git.no-commit", "The installed commit is not recorded, so the tree cannot be checked.")
        )
    return _references_from_git_commit(repo or "/var/cache/irate-box/src", commit)


ROOT_STATE = Path(os.environ.get("HUB_ROOT_STATE_DIR") or STATE.parent / "irate-box-root")
MANIFEST = ROOT_STATE / "protected" / "manifest.json"


def manifest_entries(path=None):
    """Box doctor's protected-files manifest (retain.py, Part 2 step 4): {path: entry}, entry with its sha256,
    copy name, owner and mode."""
    path = Path(path or MANIFEST)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {
        "/" + key.lstrip("/"): value
        for key, value in entries.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def load_manifest(path=None):
    """Box doctor's protected-files manifest as references: {path: {"sha256": …}}."""
    out = {}
    for key, value in manifest_entries(path).items():
        digest = value.get("sha256")
        if isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest):
            out[key] = {"sha256": digest, "manifest": "1"}
    return out


def _ext_lookup(source, path):
    """(inode, mode, size) for a path in an ext filesystem, or None; debugfs's directory listing, no mount."""
    inode = 2
    parts = [part for part in path.strip("/").split("/") if part]
    for index, part in enumerate(parts):
        found = next((entry for entry in _entries(source, inode) if entry[0] == part), None)
        if not found:
            return None
        _, inode, mode, size = found
        if index < len(parts) - 1 and not stat.S_ISDIR(mode):
            return None
    return inode, mode, size


def _ext_read(source, inode):
    return b"".join(_ext_chunks(source, inode))


def references_from_dpkg_ext(source):
    """The packages' md5sums inside an ext image or card, read through debugfs: {path: {"md5": …}}."""
    info = _ext_lookup(source, "var/lib/dpkg/info")
    if not info or not stat.S_ISDIR(info[1]):
        return {}
    out = {}
    for name, inode, mode, _size in _entries(source, info[0]):
        if not name.endswith(".md5sums") or not stat.S_ISREG(mode):
            continue
        try:
            text = _ext_read(source, inode).decode(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            digest, _, relative = line.strip().partition("  ")
            if re.fullmatch(r"[0-9a-f]{32}", digest) and relative:
                out["/" + relative.lstrip("/")] = {"md5": digest}
    return out


def installed_commit_ext(source):
    """The commit in /opt/irate-box/VERSION inside an ext image or card, or None."""
    found = _ext_lookup(source, "opt/irate-box/VERSION")
    if not found or not stat.S_ISREG(found[1]):
        return None
    try:
        first = _ext_read(source, found[0]).decode(errors="replace").split()[0]
    except (OSError, IndexError):
        return None
    return first if re.fullmatch(r"[0-9a-f]{7,40}", first) else None


def references_from_git_ext(source, repo=None):
    """irate-box's tree for the commit recorded inside an ext image or card, by the updater's checkout."""
    commit = installed_commit_ext(source)
    if not commit:
        raise Refused(
            M("housecall.git.no-commit", "The installed commit is not recorded, so the tree cannot be checked.")
        )
    return _references_from_git_commit(repo or "/var/cache/irate-box/src", commit)


def _ext_sources_image(path):
    """The ext filesystems in a disk image, as debugfs sources (image?offset=N, or the image itself)."""
    path = Path(path)
    _label, parts, _why = partitions_table(path)
    if not parts:
        return [str(path)] if (blkid(path, 0) or "").startswith("ext") else []
    out = []
    for part in parts:
        offset = part.get("start", 0) * 512
        fstype = blkid(path, offset) if offset else blkid(path, 0)
        if (fstype or "").startswith("ext"):
            out.append(f"{path}?offset={offset}" if offset else str(path))
    return out


def _ext_sources_device(path):
    """The ext filesystems on a card device, from lsblk (a whole-disk filesystem works too)."""
    path = Path(path)
    said = run("lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE", str(path), timeout=60)
    try:
        node = (json.loads(said.stdout).get("blockdevices") or [])[0]
    except (ValueError, IndexError):
        return []
    out = []
    for child in node.get("children") or [node]:
        fstype = child.get("fstype") or blkid(child.get("path") or str(path))
        if (fstype or "").startswith("ext"):
            out.append(child.get("path") or str(path))
    return out


def references_from_target(path, device=False, use_dpkg=False, use_tree=False, repo=None):
    """The references a card image or device already holds: its packages' md5sums, and irate-box's git tree."""
    sources = _ext_sources_device(path) if device else _ext_sources_image(path)
    out = {}
    found_commit = False
    for source in sources:
        if use_dpkg:
            out = merge_references(out, references_from_dpkg_ext(source))
        if use_tree:
            try:
                out = merge_references(out, references_from_git_ext(source, repo))
                found_commit = True
            except Refused:
                continue
    if use_tree and not found_commit:
        raise Refused(
            M("housecall.git.no-commit", "The installed commit is not recorded, so the tree cannot be checked.")
        )
    return out


def _target_for(path, root):
    """A report path as the file to write, under root (/ for this box's own files). Another card is untrusted: a
    folder on the way may be a symlink out of it, so the parent is resolved and must stay inside the root."""
    base = Path(root or "/")
    relative = str(path).lstrip("/")
    if ".." in Path(relative).parts:
        raise Refused(M("housecall.restore.path", "That path is not inside what was examined."))
    if str(base) == "/":
        return Path(path)
    root_resolved = base.resolve()
    target = root_resolved / relative
    parent = target.parent.resolve()
    if parent != root_resolved and root_resolved not in parent.parents:
        raise Refused(M("housecall.restore.outside", "That path leads outside what was examined."))
    return target


def _copy_source(entry):
    """Box doctor's good copy of a protected file, or its .prev, when its SHA-256 still matches the manifest."""
    name = entry.get("copy")
    expected = entry.get("sha256")
    if not name or not expected:
        return None
    for candidate in (ROOT_STATE / "protected" / name, ROOT_STATE / "protected" / (name + ".prev")):
        try:
            if candidate.is_file() and restore.sha_file(candidate) == expected:
                return candidate
        except OSError:
            continue
    return None


def _candidate(path, root, manifests, allow_package, repo=None):
    """Where one damaged file can come back from: Box doctor's copy, irate-box's git tree, or its package."""
    entry = manifests.get(path)
    if entry:
        source = _copy_source(entry)
        if source:
            return {
                "kind": "copy",
                "source": str(source),
                "sha256": entry["sha256"],
                "mode": entry.get("mode"),
                "owner": [entry.get("uid"), entry.get("gid")],
            }
    if path.startswith("/opt/irate-box/"):
        commit = installed_commit(root)
        if commit:
            relative = path[len("/opt/irate-box/") :]
            try:
                expected = _references_from_git_commit(repo or "/var/cache/irate-box/src", commit).get(path, {})
            except Refused:
                expected = {}
            if expected.get("git"):
                return {
                    "kind": "git",
                    "repo": str(repo or "/var/cache/irate-box/src"),
                    "commit": commit,
                    "relative": relative,
                }
    if allow_package:
        said = restore.run("dpkg", "-S", str(_target_for(path, root)), timeout=60)
        if said.returncode == 0:
            return {"kind": "package"}
    return None


def plan_restore(records, root, manifests=None, repo=None):
    """Add a way back to every damaged file that has one, so the finding can offer it and the page can ask. Box
    doctor's default manifest is this box's, so it is used only for this box's own files (/): another card's must
    give its own (--manifest)."""
    if manifests is None:
        manifests = manifest_entries() if str(Path(root or "/")) == "/" else {}
    allow_package = str(Path(root or "/")) == "/"
    for record in records:
        if record.get("verdict") != "damaged":
            continue
        found = _candidate(record["path"], root, manifests, allow_package, repo)
        if found:
            record["restore"] = found
    return records


def restore_file(path, root, restore_plan):
    """Put one file back from the source the report named. The old file is kept as name.damaged."""
    from irate_box.root import restore as restore_mod

    target = _target_for(path, root)
    kind = (restore_plan or {}).get("kind")
    if kind == "copy":
        source = Path(str(restore_plan.get("source") or ""))
        protected = (ROOT_STATE / "protected").resolve()
        try:
            source.resolve().relative_to(protected)
        except (OSError, ValueError):
            raise Refused(M("housecall.restore.source", "That copy is not one of Box doctor's.")) from None
        owner = restore_plan.get("owner") or [None, None]
        return restore_mod.from_copy(
            target,
            source,
            sha256=restore_plan.get("sha256"),
            mode=restore_plan.get("mode"),
            owner=(owner[0], owner[1]) if owner[0] is not None and owner[1] is not None else None,
        )
    if kind == "git":
        relative = str(restore_plan.get("relative") or "")
        if not relative or ".." in Path(relative).parts:
            raise Refused(M("housecall.restore.git", "That file is not one irate-box's git tree can put back."))
        return restore_mod.from_git(
            target,
            Path(str(restore_plan.get("repo") or "/var/cache/irate-box/src")),
            str(restore_plan.get("commit") or ""),
            relative,
        )
    if kind == "package":
        return restore_mod.from_package(target)
    raise Refused(M("housecall.restore.kind", "Select how the file comes back."))


def restore_from_report(path, kind=None):
    """One recovery action, asked from the page, for a report this doctor wrote: its own plan for the file."""
    try:
        report = json.loads(REPORT.read_text())
    except (OSError, ValueError):
        raise Refused(M("housecall.restore.no-report", "There is no examination report to restore from.")) from None
    root = report.get("root") or "/"
    record = next((r for r in report.get("files") or [] if r.get("path") == path), None)
    plan = (record or {}).get("restore")
    if not record or not plan:
        raise Refused(M("housecall.restore.no-plan", "That file has no way back in the current report."))
    if kind and kind != plan.get("kind"):
        raise Refused(
            M("housecall.restore.changed", "Ask for the examination again first: what can restore it changed.")
        )
    said = restore_file(path, root, plan)
    record["verdict"] = "restored"
    record["reason"] = said
    remaining = [r for r in report.get("files") or [] if r.get("verdict") == "damaged"]
    restored = [r for r in report.get("files") or [] if r.get("verdict") == "restored"]
    report.setdefault("counts", {})["damaged"] = len(remaining)
    report["counts"]["restored"] = len(restored)
    report.setdefault("findings", []).append(
        finding(
            f"housecall-restored:{path}",
            M("housecall.restored.title", "Restored"),
            "ok",
            said,
            evidence=[path],
        )
    )
    write_report(report)
    return said


PEERS = ROOT_STATE / "peers.json"
PEER_DIR = ROOT_STATE / "peers"
PEER_KEY = Path(os.environ.get("HUB_PEER_KEY") or PEER_DIR / "housecall-peer")
PEER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PEER_COMMAND = (
    r"for f in /var/lib/hub/control/health.json /var/lib/hub/control/security-audit.json "
    r"/var/lib/hub/control/doctor.json /opt/irate-box/VERSION; do printf '\n=== %s\n' $f; "
    r"cat $f 2>/dev/null; done"
)
PEER_DOCTORS = (
    ("box-doctor", "/var/lib/hub/control/health.json"),
    ("security-doctor", "/var/lib/hub/control/security-audit.json"),
    ("updates-doctor", "/var/lib/hub/control/doctor.json"),
)
LOCAL_VERSION = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box")) / "VERSION"


def load_peers():
    try:
        data = json.loads(PEERS.read_text())
    except (OSError, ValueError):
        return {}
    peers = data.get("peers") if isinstance(data, dict) else None
    return peers if isinstance(peers, dict) else {}


def _valid_peer(name, host, user, port):
    if not PEER_RE.match(name or ""):
        raise Refused(M("housecall.peer.name", "Give the other box a short name: letters, digits, dots, dashes."))
    if not HOST_RE.match(host or "") or host.startswith("-"):
        raise Refused(M("housecall.peer.host", "Give its address: a host name or an IP address."))
    if not USER_RE.match(user or "") or user.startswith("-"):
        raise Refused(
            M("housecall.peer.user", "Give the user this box signs in as: lower-case letters, digits, _ and -.")
        )
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise Refused(M("housecall.peer.port", "Give its SSH port, 1 to 65535."))


def pair_peer(name, host, user="root", port=22):
    """One pairing, made once: remember the box, and its SSH host key as ssh-keyscan gives it now. The owner then
    puts this box's public key on the other box. No password is stored."""
    _valid_peer(name, host, user, port)
    PEER_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    peers = load_peers()
    if name in peers and (peers[name].get("host"), peers[name].get("user"), peers[name].get("port")) != (
        host,
        user,
        port,
    ):
        raise Refused(
            M(
                "housecall.peer.exists",
                "{name} is already paired with {host}. Select another name.",
                name=name,
                host=peers[name].get("host"),
            )
        )
    if not PEER_KEY.is_file():
        made = run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(PEER_KEY), timeout=120)
        if made.returncode or not PEER_KEY.is_file():
            raise Refused(
                M(
                    "housecall.peer.no-key",
                    "This box has no SSH key, and one could not be made: {why}",
                    why=_text(made)[-200:],
                )
            )
    known = PEER_DIR / f"{name}.known_hosts"
    scan = run("ssh-keyscan", "-p", str(port), host, timeout=60)
    if scan.returncode or "ssh-" not in (scan.stdout or ""):
        raise Refused(
            M("housecall.peer.scan", "The other box's SSH host key could not be read: {why}", why=_text(scan)[-200:])
        )
    safeio.write_checked(known, scan.stdout, 0o600)
    peers[name] = {"host": host, "user": user, "port": port, "known_hosts": str(known), "key": str(PEER_KEY)}
    safeio.write_checked(PEERS, json.dumps({"version": 1, "peers": peers}, indent=1, sort_keys=True), 0o600)
    public = run("ssh-keygen", "-y", "-f", str(PEER_KEY), timeout=60)
    pub = (public.stdout or "").strip()
    # Inside command="...", sshd turns only \" back into a quote and keeps every other backslash, so the \n the
    # remote shell reads must reach it as written: only quotes are escaped.
    escaped = PEER_COMMAND.replace('"', '\\"')
    line = f'restrict,command="{escaped}" {pub}' if pub else ""
    auth = PEER_DIR / f"{name}.authorized_key"
    safeio.write_checked(auth, line + "\n", 0o600)
    shown = run("ssh-keygen", "-lf", str(known), timeout=60).stdout.strip()
    fingerprint = " ".join(shown.split()[1:2]) or "not read"
    return M(
        "housecall.peer.paired",
        "{name} is paired at {user}@{host}. Its host key fingerprint is {fingerprint}. Paste the line in {file} into "
        "{user}'s authorized_keys on the other box. That line lets one command only, not a shell.",
        name=name,
        user=user,
        host=host,
        fingerprint=fingerprint,
        file=str(auth),
    )


def _unpair_action(name):
    return action(
        f"housecall-unpair:{name}",
        M("housecall.peer.unpair", "Remove this pairing"),
        M(
            "housecall.peer.unpair.confirm",
            "Forget the pairing with {name}? Its host key and this box's key line for it are deleted here. "
            "Remove its line from the other box's authorized_keys by hand.",
            name=name,
        ),
    )


def unpair_peer(name):
    """Forget a pairing: the entry, its known_hosts and the authorized-key line made for it. The other box's own
    authorized_keys is the owner's to edit; this cannot reach it."""
    peers = load_peers()
    peer = peers.get(name)
    if not peer:
        raise Refused(M("housecall.peer.unknown", "No box is paired as {name}. Pair it first.", name=name))
    peers.pop(name)
    safeio.write_checked(PEERS, json.dumps({"version": 1, "peers": peers}, indent=1, sort_keys=True), 0o600)
    known = Path(peer.get("known_hosts") or PEER_DIR / f"{name}.known_hosts")
    known.unlink(missing_ok=True)
    (PEER_DIR / f"{name}.authorized_key").unlink(missing_ok=True)
    return M(
        "housecall.peer.unpaired",
        "The pairing with {name} is removed. Remove its line from the other box's authorized_keys by hand.",
        name=name,
    )


def _peer_reports(text):
    """The other box's reports, split by the fixed command's === path lines: {path: text}."""
    out, current, lines = {}, None, []
    for line in (text or "").splitlines():
        if line.startswith("=== "):
            if current is not None:
                out[current] = "\n".join(lines).strip()
            current, lines = line[4:].strip(), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = "\n".join(lines).strip()
    return out


def _peer_doctor(label, path, reports):
    """One doctor on the other box, from its report: its problems and warnings, not its whole report."""
    raw = reports.get(path) or ""
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return finding(
            f"housecall-peer:{label}",
            M("housecall.peer.doctor", "{doctor} on the other box", doctor=label),
            "warn",
            M("housecall.peer.no-report", "There is no report from its {doctor}.", doctor=label),
        )
    found = data["findings"]
    problems = [f for f in found if f.get("status") == "problem"]
    warns = [f for f in found if f.get("status") == "warn"]
    status = "problem" if problems else ("warn" if warns else "ok")
    if problems:
        summary = M(
            "housecall.peer.problems",
            {"one": "{n} problem, and {w} warning(s).", "other": "{n} problems, and {w} warning(s)."},
            n=len(problems),
            w=len(warns),
        )
    elif warns:
        summary = M(
            "housecall.peer.warnings",
            {"one": "No problems, {w} warning.", "other": "No problems, {w} warnings."},
            n=len(warns),
            w=len(warns),
        )
    else:
        summary = M("housecall.peer.clean", "No problems, no warnings.")
    return finding(
        f"housecall-peer:{label}",
        M("housecall.peer.doctor", "{doctor} on the other box", doctor=label),
        status,
        summary,
        evidence=[f"{f.get('id')}: {f.get('status')}" for f in (problems + warns)[:20]],
    )


def peer_examine(name):
    """Another paired box, read only over SSH: its three doctors' reports and its version, as findings. An
    unreachable box is a finding, not an error."""
    peers = load_peers()
    peer = peers.get(name)
    if not peer:
        raise Refused(M("housecall.peer.unknown", "No box is paired as {name}. Pair it first.", name=name))
    said = run(
        "ssh",
        "-i",
        str(peer.get("key") or PEER_KEY),
        "-p",
        str(peer.get("port") or 22),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={peer.get('known_hosts') or PEER_DIR / (name + '.known_hosts')}",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "IdentitiesOnly=yes",
        f"{peer.get('user') or 'root'}@{peer.get('host')}",
        PEER_COMMAND,
        timeout=120,
    )
    if said.returncode:
        report = _report(
            None,
            [
                finding(
                    "housecall-peer",
                    M("housecall.peer.title", "Another box"),
                    "problem",
                    M(
                        "housecall.peer.unreachable",
                        "{name} could not be read: {why}",
                        name=name,
                        why=_text(said)[-200:],
                    ),
                    actions=[_unpair_action(name)],
                )
            ],
            [],
            None,
        )
        report["target"] = f"the box {name}"
        return report
    reports = _peer_reports(said.stdout)
    remote_version = (reports.get("/opt/irate-box/VERSION") or "").split()
    try:
        local_version = LOCAL_VERSION.read_text().split()[0]
    except (OSError, IndexError):
        local_version = ""
    found = [
        finding(
            "housecall-peer",
            M("housecall.peer.title", "Another box"),
            "ok",
            M(
                "housecall.peer.reached",
                "{name} was read, read only: its three doctors and its version.",
                name=name,
            ),
            evidence=[f"{peer.get('user') or 'root'}@{peer.get('host')}"],
            actions=[_unpair_action(name)],
        )
    ]
    if remote_version:
        status = "ok" if remote_version[0] == local_version else "warn"
        found.append(
            finding(
                "housecall-peer-version",
                M("housecall.peer.version", "Its irate-box"),
                status,
                M(
                    "housecall.peer.version.said",
                    "It runs {remote}. This box runs {local}.",
                    remote=remote_version[0],
                    local=local_version or "an unknown version",
                ),
            )
        )
    else:
        found.append(
            finding(
                "housecall-peer-version",
                M("housecall.peer.version", "Its irate-box"),
                "warn",
                M("housecall.peer.version.missing", "Its irate-box version was not read."),
            )
        )
    found += [_peer_doctor(label, path, reports) for label, path in PEER_DOCTORS]
    report = _report(None, found, [], None)
    report["target"] = f"the box {name}"
    return report


def record_card(card, action, said):
    """A result from the Rockchip boards page (rkcard.py) as a finding here, so a card checked there is in this
    doctor's report too. The report's files and counts, if it had any, are kept."""
    with _report_lock(REPORT):
        try:
            report = json.loads(REPORT.read_text())
        except (OSError, ValueError):
            report = {}
        if not isinstance(report, dict) or not isinstance(report.get("findings"), list):
            report = {"at": time.time(), "target": "cards in a reader", "findings": [], "files": [], "counts": {}}
        fid = f"housecall-card:{card}:{action}"
        report["findings"] = [f for f in report["findings"] if f.get("id") != fid]
        status = said.get("status") if isinstance(said, dict) else None
        if action == "genuine" and isinstance(said, dict) and said.get("id") == "rkcard.fake":
            status = "problem"
        status = status if status in ("ok", "warn", "problem") else "ok"
        report["findings"].append(
            finding(
                fid,
                M("housecall.card.title", "Card {name}", name=card),
                status,
                said,
                notes=[
                    M(
                        "housecall.card.rockchip",
                        "From the Rockchip boards page, where the card in the reader is handled.",
                    )
                ],
                evidence=[f"The {action} of the card {card}"],
            )
        )
        report["at"] = time.time()
        _write_report(report)
        return report


@contextlib.contextmanager
def _report_lock(path):
    """One writer at a time for the report: an examination ending and a card result must not overwrite each
    other. A lock that cannot be taken is not fatal: the write still happens."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError:
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_report(report, path=REPORT):
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    safeio.mkdir(parent, mode=0o755)
    safeio.write(path, json.dumps(report, indent=2))
    return report


def write_report(report, path=REPORT):
    """Write the report, keeping any Rockchip card result already in it: a card checked while an examination runs
    is not lost when that examination ends."""
    with _report_lock(path):
        try:
            old = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            old = {}
        if isinstance(old, dict) and isinstance(report, dict) and isinstance(report.get("findings"), list):
            cards = [
                f
                for f in old.get("findings") or []
                if isinstance(f, dict) and str(f.get("id", "")).startswith("housecall-card:")
            ]
            have = {f.get("id") for f in report["findings"] if isinstance(f, dict)}
            report["findings"] += [f for f in cards if f.get("id") not in have]
        return _write_report(report, path)


def main(argv):
    if os.geteuid() != 0:
        sys.exit("run as root: sudo ./irate-box housecall [--image PATH | --device PATH | --root PATH | --this-card]")
    target = None
    kind = ""
    references = {}
    use_dpkg = False
    use_tree = False
    as_json = False
    summary = False
    image_first = ""
    on_own_card = False
    manifest = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in (
            "--image",
            "--device",
            "--root",
            "--reference",
            "--image-first",
            "--manifest",
            "--peer",
        ) and i + 1 < len(argv):
            i += 1
            if arg == "--reference":
                references = load_references(argv[i])
            elif arg == "--manifest":
                manifest = argv[i]
            elif arg == "--image-first":
                image_first = argv[i]
            elif arg == "--peer":
                kind, target = "peer", argv[i]
            else:
                kind, target = arg[2:], argv[i]
        elif arg == "--dpkg":
            use_dpkg = True
        elif arg == "--git-tree":
            use_tree = True
        elif arg == "--on-own-card":
            on_own_card = True
        elif arg in ("--json", "--summary"):
            as_json = arg == "--json"
            summary = arg == "--summary"
        elif arg == "--this-card":
            kind = "this-card"
        else:
            sys.exit(
                "usage: housecall.py [--image PATH | --device PATH [--image-first PATH [--on-own-card]] | "
                "--root PATH | --this-card | --peer NAME] [--reference PATH] [--manifest PATH] [--dpkg] "
                "[--git-tree] [--json | --summary]"
            )
        i += 1
    if image_first and kind != "device":
        sys.exit("--image-first images a card: use it with --device")
    try:
        prepared = None
        if kind == "device" and image_first:
            write_report(
                running_report(
                    f"the card {target}",
                    M(
                        "housecall.image-first.running",
                        "The card is being imaged to {image} first. It is only read. The examination runs on the image.",
                        image=image_first,
                    ),
                )
            )
            prepared = (target, image_first, copy_device(target, image_first, on_own_card=on_own_card))
            kind, target = "image", image_first
        if manifest:
            references = merge_references(references, load_manifest(manifest))
        elif kind == "this-card":
            references = merge_references(references, load_manifest())
        if use_dpkg or use_tree:
            if kind in ("root", "this-card"):
                base = target if kind == "root" else "/"
                if use_dpkg:
                    references = merge_references(references, references_from_dpkg(base))
                if use_tree:
                    references = merge_references(references, references_from_git(base))
            elif kind in ("image", "device"):
                references = merge_references(
                    references,
                    references_from_target(target, device=kind == "device", use_dpkg=use_dpkg, use_tree=use_tree),
                )
            else:
                sys.exit("--dpkg and --git-tree read a card, an image, a folder or this box's own files")
        if kind == "image":
            report = examine_image(target, references)
        elif kind == "device":
            report = examine_device(target, references)
        elif kind == "root":
            report = examine_root(target, references, manifest=manifest or None)
        elif kind == "this-card":
            report = examine_this_card(references, manifest=manifest or None)
        elif kind == "peer":
            report = peer_examine(target)
        else:
            sys.exit("choose --image, --device, --root, --this-card or --peer")
        if prepared:
            report["source_device"] = str(Path(prepared[0]).resolve())
            report["image"] = str(Path(prepared[1]).resolve())
            report["findings"].insert(0, image_first_finding(*prepared))
    except (Refused, OSError, ValueError, subprocess.SubprocessError) as exc:
        write_report(ended_report(target or kind, exc))  # the page's running report must not stay running
        sys.exit(f"housecall: {exc}")
    write_report(report)
    if as_json:
        print(json.dumps(report, indent=2))
        return
    for f in report["findings"]:
        if summary and f["status"] == "ok":
            continue
        print(f"{f['status']:7} {render(f['title'])}: {render(f['summary'])}")
    c = report["counts"]
    print(f"{c['damaged']} damaged, {c['suspect']} suspect, {c['genuine']} genuine, {c['unknown']} unknown")


if __name__ == "__main__":
    main(sys.argv[1:])
