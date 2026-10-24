# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The store's edges: a thumbnail seen only by whoever may see its save, a ceiling on the number
of files, no .tmp left by a failed write, no id with a trailing newline, and the expiry sweep never removing a
save written over since. python3 tests/sim_store_edges.py"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import store

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Clock:
    t = 0

    def ticks(self):
        Clock.t += 1
        return Clock.t


class Handler:
    """Enough of a request handler for store.handle: the headers it reads, the answer it sends."""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.command = "GET"
        self.code = None
        self.body = b""

    def send_response(self, code):
        self.code = code

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body += data


T = Path(tempfile.mkdtemp(prefix="store-edges-"))
s = store.Store(T / "store", Clock())

# A thumbnail, only for whoever may see the save.
ann = {"name": "ann"}
store.VIEWER = lambda h: h.headers.get("me")
store.ADMIN = lambda h: h.headers.get("admin") == "yes"
mine = s.save("t", "ann's", b"{}", thumb=b"PNG", marks={"account": "ann", "seen_by": "me"})
open_ = s.save("t", "anyone's", b"{}", thumb=b"PNG")


def thumb(key, **headers):
    h = Handler(headers)
    store.handle(h, "GET", f"/api/saves/{key}/thumb", s)
    return h.code


check("a guest's thumbnail of a save seen by everyone: shown", thumb(open_["id"]) == 200)
check("a thumbnail of a save seen only by its account: not shown to a guest", thumb(mine["id"]) == 404)
check("... shown to its account", thumb(mine["id"], me=ann) == 200)
check("... shown to the administrator's Store pane", thumb(mine["id"], admin="yes") == 200)

# An id with a trailing newline is refused, as one no address can reach.
check("an id with a trailing newline: refused", not store.ID_RE.match("abc\n"))
check("an ordinary id: accepted", store.ID_RE.match("abc-1_2"))

# A write that fails leaves no .tmp behind.
target = T / "store" / "scenes" / "x"
real_fsync = store.os.fsync


def full(_fd):
    raise OSError(28, "No space left on device")


store.os.fsync = full
try:
    s._write(target, b"data")
    raised = False
except OSError:
    raised = True
store.os.fsync = real_fsync
check("a write that fails: the error raised, no .tmp left", raised and not list(target.parent.glob("*.tmp")))

# No more than MAX_FILES files, whatever their size.
store.MAX_FILES = 10
for i in range(30):
    s.put("files", f"f{i}", b"1")
count = sum(1 for ns in store.BLOB_NAMESPACES + ("saves",) for _ in (T / "store" / ns).iterdir())
check("thousands of one-byte files: the oldest go to keep the count under the ceiling", count <= 10, count)
check("... the newest kept", (T / "store" / "files" / "f29").exists())

d = store.Drop(T / "drop", Clock())
store.DROP_MAX_FILES = 5
for i in range(12):
    key = f"d{i}"
    (d.dir / f"{key}.data").parent.mkdir(parents=True, exist_ok=True)
    (d.dir / f"{key}.data").write_bytes(b"1")
    (d.dir / f"{key}.meta").write_text(f'{{"id": "{key}", "size": 1, "created": {i}}}')
with d._lock:
    left = d._prune(keep="d11")
check("the file drop: as many files as its ceiling, the newest kept", len(left) == 5 and left[-1]["id"] == "d11", left)

# The expiry sweep reads a save again under the lock before removing it.
store.MAX_FILES = 4096
store.SAVE_TTL = 5
s2 = store.Store(T / "store2", Clock())
old = s2.save("t", "old", b"{}")
Clock.t += 100
real_read = s2._read_meta
calls = {"n": 0}


def rewritten(path):
    # The first read (outside the lock) sees the old save; by the time the lock is held it was saved over.
    calls["n"] += 1
    meta = real_read(path)
    if calls["n"] == 1 or meta is None:
        return meta
    return dict(meta, created=Clock.t)


s2._read_meta = rewritten
listed = s2.list_saves()
check("a save written over during the sweep: kept", s2.get_save(old["id"]) is not None and len(listed) == 1, listed)
s2._read_meta = real_read
Clock.t += 100
check("a save past its time: removed by the sweep", s2.list_saves() == [] and s2.get_save(old["id"]) is None)

shutil.rmtree(T, ignore_errors=True)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
