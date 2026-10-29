# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Writing a protected file and making the card prove it (durable.write_checked; notes: box-doctor-hardening-plan
Part 2, step 2), offline: an identical file is left alone; a write is fsynced, evicted and read back; a mismatch is
written once more; one that survives both is kept for Box doctor, which gives a problem naming each file. The front's
files go together with one syncfs. tls._write and hub_control._write_root_file use it. python3 tests/sim_write_checked.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="write-checked-"))
state, root = T / "state", T / "root"
for p in (state, root):
    p.mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(state), HUB_ROOT_STATE_DIR=str(root))
sys.path.insert(0, str(REPO))
from irate_box import durable
from irate_box.finding import message_ids
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


path = T / "etc" / "protected.conf"
path.parent.mkdir()
check("a protected file is written", durable.write_checked(path, "first\n", 0o600) and path.read_text() == "first\n")
check("  with its mode", path.stat().st_mode & 0o777 == 0o600, oct(path.stat().st_mode & 0o777))

before = path.stat()
check("the same words: no write at all (same inode and mtime)", durable.write_checked(path, "first\n", 0o600))
after = path.stat()
check("  (still the same inode and mtime)", (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns))

real_read, real_write = durable._read_back, durable._write_synced
read_again = {"n": 0}
writes = []


def write_counting(*args, **kw):
    writes.append(args[0])
    return real_write(*args, **kw)


durable._write_synced = write_counting
read_again["n"] = 1


def read_wrong_once(*args, **kw):
    if read_again["n"]:
        read_again["n"] -= 1
        return b"not what was written"
    return real_read(*args, **kw)


durable._read_back = read_wrong_once
check("a read-back that differs once: written again, then right", durable.write_checked(path, "second\n", 0o600))
check("  (two writes, and no finding kept)", len(writes) == 2 and not (root / "write-check.json").exists(), writes)

durable._read_back = lambda *a, **kw: b"still not what was written"
check(
    "a read-back that always differs: the write is not claimed", durable.write_checked(path, "third\n", 0o600) is False
)
report = durable.report_path()
check("  and it is kept for Box doctor", report.is_file() and str(path) in report.read_text(), report.read_text())
found = {f["id"]: f for f in health.check_writes_kept()}
check(
    "  Box doctor: a problem naming the file",
    found["card-writes-kept"]["status"] == "problem"
    and found["card-writes-kept"]["evidence"] == [str(path)]
    and "writes-kept.found" in message_ids(found["card-writes-kept"]),
    found.get("card-writes-kept"),
)
durable._read_back = real_read
durable._write_synced = real_write

a, b = T / "etc" / "a.conf", T / "etc" / "b.conf"
a.write_text("a\n")
b.write_text("b\n")
synced = []
real_sync = durable._syncfs_all
durable._syncfs_all = lambda paths: synced.append(list(paths))
check("a batch with one changed file reads back", durable.write_batch([(a, "a\n", 0o644), (b, "b2\n", 0o644)]))
check(
    "  one syncfs, and the changed file written", len(synced) == 1 and b.read_text() == "b2\n", (synced, b.read_text())
)
synced.clear()
check(
    "a batch with nothing changed writes and syncs nothing",
    durable.write_batch([(a, "a\n"), (b, "b2\n")]) and not synced,
)
durable._syncfs_all = real_sync

tls_src = (REPO / "irate_box/root/tls.py").read_text()
hc_src = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "tls._write goes through safeio.write_checked",
    "def _write(path, text, mode, group=None):" in tls_src
    and "safeio.write_checked(path, text, mode, group=group)" in tls_src,
)
check(
    "hub_control's root file writer goes through safeio.write_checked",
    "def _write_root_file(path, text, mode=0o644, group=None):" in hc_src
    and "safeio.write_checked(path, text, mode, group=group)" in hc_src,
)
source = (REPO / "irate_box/root/health.py").read_text()
scan = source[source.index("def scan(quick=False):") : source.index("def _previous_report")]
check("the Box doctor runs the check", "check_writes_kept" in scan)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
