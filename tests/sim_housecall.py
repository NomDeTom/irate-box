# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Housecall doctor on images and folders (root/housecall.py, notes: housecall-doctor-plan steps 1 and 3),
offline: a small ext4 image read without a loop device or a mount, its files given the plan's verdicts (0xFF
damaged, zero without a reference suspect, a difference from a reference damaged, a partial 0xFF block unknown);
a partitioned image; a reference manifest; the image unchanged; a folder scanned past the cache; the report in
control/housecall.json. python3 tests/sim_housecall.py"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="housecall-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import housecall

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def build_image(path, files, size="16M", offset=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        subprocess.run(["truncate", "-s", size, str(path)], check=True)
    args = ["mke2fs", "-q", "-t", "ext4"]
    if offset:
        args += ["-E", f"offset={offset}"]
    subprocess.run([*args, str(path)], check=True, stderr=subprocess.DEVNULL)
    source = f"{path}?offset={offset}" if offset else str(path)
    for name, data in files.items():
        local = T / "to-write"
        local.write_bytes(data)
        subprocess.run(
            ["debugfs", "-w", "-R", f"write {local} /{name}", source],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


good = b"hello\n"
changed_ref = b"what the reference says\n"
ff = b"\xff" * 4096
zero = b"\0" * 4096
partial = b"\xff" * 2048 + b"data"

image = T / "card.img"
build_image(image, {"good": good, "ff": ff, "zero": zero, "partial": partial, "changed": b"edited\n"})
before = hashlib.sha256(image.read_bytes()).hexdigest()
references = {
    "/good": hashlib.sha256(good).hexdigest(),
    "/changed": hashlib.sha256(changed_ref).hexdigest(),
}
report = housecall.examine_image(image, references)
by_path = {r["path"]: r for r in report["files"]}
check(
    "an all-0xFF file: damaged",
    by_path.get("/ff", {}).get("verdict") == "damaged" and by_path["/ff"]["ff_blocks"] == 1,
    report["files"],
)
check("a file that matches its reference: genuine", by_path.get("/good", {}).get("verdict") == "genuine")
check("a file that differs from its reference: damaged", by_path.get("/changed", {}).get("verdict") == "damaged")
check(
    "a file with a partial 0xFF block and no reference: unknown (a genuine padding can do this)",
    "/partial" not in by_path and report["counts"]["unknown"] == 1,
    report["counts"],
)
check("an all-zero file with no reference: suspect", report["counts"]["suspect"] == 1)
check(
    "the finding names the damaged files, and says how",
    any(f["id"].startswith("housecall-files:") and f["status"] == "problem" for f in report["findings"])
    and any("/ff: damaged" in e for f in report["findings"] for e in f["evidence"]),
    report["findings"],
)
check(
    "the image is unchanged (its SHA-256 before and after)",
    hashlib.sha256(image.read_bytes()).hexdigest() == before,
)
card_report = report
check(
    "the image's partition table is absent but its whole-disk filesystem is found",
    any(f["id"] == "housecall-fs:whole disk" and f["status"] == "ok" for f in report["findings"]),
    report["findings"],
)

# A partitioned image: the table is named and the ext4 partition at its offset is read.
parts = T / "parts.img"
subprocess.run(["truncate", "-s", "32M", str(parts)], check=True)
subprocess.run(
    ["sfdisk", "-q", str(parts)], input="label: dos\nstart=2048, size=40960, type=83\n", text=True, check=True
)
build_image(parts, {"good": good}, size="32M", offset=2048 * 512)
report = housecall.examine_image(parts)
check(
    "a partitioned image: its table and its filesystem are named",
    any(f["id"] == "housecall-partitions" and f["status"] == "ok" for f in report["findings"])
    and any(f["id"].startswith("housecall-fs:") and f["status"] == "ok" for f in report["findings"]),
    report["findings"],
)
check("  and the file in it is read", report["counts"]["files"] == 1, report["counts"])

# A folder, read directly (this box's own files): the same verdicts, past the cache.
folder = T / "folder"
(folder / "sub").mkdir(parents=True)
(folder / "good").write_bytes(good)
(folder / "sub" / "ff").write_bytes(ff)
(folder / "sub" / "zero").write_bytes(zero)
records = housecall.scan_dir(folder, {"/good": hashlib.sha256(good).hexdigest()})
by_path = {r["path"]: r for r in records}
check(
    "a folder: the same per-file verdicts, with paths from its root",
    by_path["/good"]["verdict"] == "genuine"
    and by_path["/sub/ff"]["verdict"] == "damaged"
    and by_path["/sub/zero"]["verdict"] == "suspect",
    records,
)

# The reference manifest as a file, and its refusal of a bad digest.
refs = T / "refs.json"
refs.write_text(json.dumps({"version": 1, "files": {"/good": {"sha256": hashlib.sha256(good).hexdigest()}}}))
check("a reference manifest is read", housecall.load_references(refs) == {"/good": hashlib.sha256(good).hexdigest()})
refs.write_text(json.dumps({"files": {"/bad": "not-a-digest"}}))
check("  a digest that is not SHA-256 is left out", housecall.load_references(refs) == {})

# The report the page reads.
housecall.write_report(card_report)
saved = json.loads((T / "state" / "control" / "housecall.json").read_text())
check(
    "the report is control/housecall.json, in the doctors' shape",
    saved["counts"]["damaged"] == card_report["counts"]["damaged"]
    and all("summary" in f and "status" in f for f in saved["findings"]),
)
check(
    "  its damaged-file finding says the plan's words",
    any("housecall.files.damaged" in message_ids(f) for f in saved["findings"]),
)

# A file read short, or not at all, is damage, never left out (a truncated debugfs read, an I/O error).
short = housecall._record("/short", 8192, iter([b"x" * 4096]), {})
check("a file that reads back short is damaged", short["verdict"] == "damaged", short)
real_chunks = housecall._direct_chunks


def failing_chunks(path):
    raise OSError(5, "Input/output error")
    yield b""  # a generator, as the real one


(bad_dir := T / "unreadable").mkdir()
(bad_dir / "f").write_bytes(b"data")
housecall._direct_chunks = failing_chunks
records = housecall.scan_dir(bad_dir)
housecall._direct_chunks = real_chunks
check(
    "  a file that cannot be read at all is in the count, damaged",
    len(records) == 1 and records[0]["verdict"] == "damaged" and records[0]["sha256"] is None,
    records,
)

# From inside, the hub's large folders are left out whole.
(big := T / "inside" / "zim").mkdir(parents=True)
(big / "book.zim").write_bytes(b"\xff" * 4096)
(T / "inside" / "settings.json").write_bytes(b"{}")
records = housecall.scan_dir(T / "inside", skip=[big])
check(
    "a skipped folder is not read (the large content, from inside)",
    [r["path"] for r in records] == ["/settings.json"],
    records,
)

# The box's own disk, refused: the root partition's own disk, or another partition of it.
real_run = housecall.run


def fake_run(*cmd, timeout=None):
    out = ""
    if cmd[0] == "findmnt":
        out = "/dev/null\n"  # stands for the root partition: a device that exists
    elif cmd[0] == "lsblk":
        out = {"/dev/null": "mmcblk0", str(T / "mmcblk0p1"): "mmcblk0"}.get(cmd[-1], "") + "\n"
    return subprocess.CompletedProcess(cmd, 0, out, "")


for name in ("mmcblk0", "mmcblk0p1", "sda"):
    (T / name).write_bytes(b"")
housecall.run = fake_run
check(
    "the box's own disk is refused, and its other partitions; another disk is not",
    housecall._own_disk(T / "mmcblk0") and housecall._own_disk(T / "mmcblk0p1") and not housecall._own_disk(T / "sda"),
)
housecall.run = real_run

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
