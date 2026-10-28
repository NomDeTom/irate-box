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

import hashlib
import json
import os
import re
import stat
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys
import time
from pathlib import Path

from irate_box.finding import finding
from irate_box.msg import M, Refused, render
from irate_box.root import safeio

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
REPORT = CONTROL / "housecall.json"
CHUNK = 4096
SKIP_DIRS = {"proc", "sys", "dev", "run"}
# The hub's large folders: content fetched again, or to be checked against its release's checksums, never read
# whole from inside (on box 181, 2026-10-28, the build caches alone were 14 GB in 231 280 files). As nand-logs.sh's
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
    sha, md5, git = hashlib.sha256(), hashlib.md5(), hashlib.sha1()
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
    if wanted and any(digest.get(algo) == value for algo, value in wanted.items()):
        return "genuine", None
    if blocks and ff_blocks == blocks:
        return "damaged", M("housecall.why-ff", "It reads back whole as 0xFF.")
    if wanted:
        return "damaged", M("housecall.why-reference", "It differs from its reference.")
    if blocks and zero_blocks == blocks:
        return "suspect", M("housecall.why-zero", "It reads back whole as zero, and there is no reference.")
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
        return {k: v for k, v in value.items() if k in ("sha256", "md5", "git") and isinstance(v, str)}
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
    genuine = [r for r in records if r["verdict"] == "genuine"]
    counts = {
        "damaged": len(damaged),
        "suspect": len(suspect),
        "genuine": len(genuine),
        "unknown": len([r for r in records if r["verdict"] == "unknown"]),
    }
    evidence = [
        f"{r['path']}: {r['verdict']}" + (f" ({render(r['reason'])})" if r.get("reason") else "")
        for r in (damaged + suspect)[:60]
    ]
    if len(damaged) + len(suspect) > 60:
        evidence.append(f"… and {len(damaged) + len(suspect) - 60} more")
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
            notes=[] if not suspect else [M("housecall.files.suspect", "{n} more read as all zero.", n=len(suspect))],
            evidence=evidence,
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
            notes=[M("housecall.files.suspect.why", "An empty or zeroed file can be genuine.")],
            evidence=evidence,
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


def examine_root(root, references=None):
    """A folder the owner points at, read past the cache: the same per-file verdicts, paths from that folder."""
    root = Path(root)
    if not root.is_dir():
        raise Refused(M("housecall.not-folder", "{path} is not a folder.", path=str(root)))
    records = scan_dir(root, references)
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
    return _report(root, findings, records, None)


def examine_this_card(references=None, roots=None):
    """This box's own files on the SD card (the plan's step 4): the folders the box writes and systemd's, card
    files only, the hub's large folders left out. A file on the NAND (nand-logs' state) is left out and said to be
    the NAND's, whose UBIFS checks it."""
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
    return _report(None, findings, records, None)


def _report(target, findings, records, before):
    damaged = [r for r in records if r["verdict"] == "damaged"]
    suspect = [r for r in records if r["verdict"] == "suspect"]
    return {
        "at": time.time(),
        "target": str(target) if target else "this box's card",
        "findings": findings,
        "files": [r for r in records if r["verdict"] in ("damaged", "suspect", "genuine")],
        "counts": {
            "files": len(records),
            "damaged": len(damaged),
            "suspect": len(suspect),
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
    """The commit in /opt/irate-box/VERSION, or None."""
    try:
        first = (Path(root) / "opt/irate-box/VERSION").read_text().split()[0]
    except (OSError, IndexError):
        return None
    return first if re.fullmatch(r"[0-9a-f]{40}", first) else None


def references_from_git(root="/", repo=None, commit=None):
    """irate-box's own tree at the installed commit, by the git blob SHA-1 in the updater's checkout:
    {/opt/irate-box/path: {"git": …}}."""
    repo = Path(repo or "/var/cache/irate-box/src")
    commit = commit or installed_commit(root)
    if not commit:
        raise Refused(
            M("housecall.git.no-commit", "The installed commit is not recorded, so the tree cannot be checked.")
        )
    said = run("git", "-C", str(repo), "ls-tree", "-r", "-z", commit, "--", "irate_box", binary=True)
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


def write_report(report, path=REPORT):
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    safeio.mkdir(parent, mode=0o755)
    safeio.write(path, json.dumps(report, indent=2))
    return report


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
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--image", "--device", "--root", "--reference") and i + 1 < len(argv):
            i += 1
            if arg == "--reference":
                references = load_references(argv[i])
            else:
                kind, target = arg[2:], argv[i]
        elif arg == "--dpkg":
            use_dpkg = True
        elif arg == "--git-tree":
            use_tree = True
        elif arg in ("--json", "--summary"):
            as_json = arg == "--json"
            summary = arg == "--summary"
        elif arg == "--this-card":
            kind = "this-card"
        else:
            sys.exit(
                "usage: housecall.py [--image PATH | --device PATH | --root PATH | --this-card] "
                "[--reference PATH] [--dpkg] [--git-tree] [--json | --summary]"
            )
        i += 1
    try:
        if use_dpkg or use_tree:
            if kind not in ("root", "this-card"):
                sys.exit("--dpkg and --git-tree read a box's own filesystem: use them with --root or --this-card")
            base = target if kind == "root" else "/"
            if use_dpkg:
                references = merge_references(references, references_from_dpkg(base))
            if use_tree:
                references = merge_references(references, references_from_git(base))
        if kind == "image":
            report = examine_image(target, references)
        elif kind == "device":
            report = examine_device(target, references)
        elif kind == "root":
            report = examine_root(target, references)
        elif kind == "this-card":
            report = examine_this_card(references)
        else:
            sys.exit("choose --image, --device, --root or --this-card")
    except Refused as exc:
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
