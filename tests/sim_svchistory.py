# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The services' uptime (svchistory.py), offline: five-minute samples into
hourly buckets (up of taken, starts by a new InvocationID), gaps as no data, the clock going back,
72 days kept; the last 72 hours by hour and 72 days by day the page draws (githubstatus.com's
format); the sampler writing only while the clock is trusted. python3 tests/sim_svchistory.py"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="svchist-"))
os.environ.update(HUB_STATE_DIR=str(T), HUB_RUN_DIR=str(T / "run"), TZ="UTC")
time.tzset()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import svchistory as S  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


D0 = 1791331200  # 2026-10-07 00:00 UTC
h = {}
for k in range(12):  # 00:00-01:00: up, down at 00:30-00:40, started again at 00:40
    S.record(
        h,
        {"kiwix.service": (not 6 <= k < 8, "a" if k < 8 else "b"), "mosquitto.service": (True, "m")},
        D0 + k * 300 + 5,
    )
e = h["units"]["kiwix.service"]
check("an hour: 10 of 12 samples up, one start (a new InvocationID)", e["s"] == "ac1", e)
check("  the first sight of a unit is not a start", h["units"]["mosquitto.service"]["s"] == "cc0")
S.record(h, {"kiwix.service": (True, "b")}, D0 + 3 * 3600 + 10)
check("  hours with no samples: no data", e["s"] == "ac1000000" + "110", e["s"])
before = e["s"]
S.record(h, {"kiwix.service": (False, "b")}, D0 + 3600)
check("  the clock gone back: the sample dropped", e["s"] == before)
for k in range(S.KEEP + 5):
    S.record(h, {"kiwix.service": (True, "b")}, D0 + 4 * 3600 + k * 3600)
check(
    "  72 days kept",
    len(e["s"]) == 3 * S.KEEP and e["first"] + S.KEEP - 1 == (D0 + 4 * 3600 + (S.KEEP + 4) * 3600) // 3600,
)
S.record(h, {"kiwix.service": (True, "b")}, D0 + 200 * 86400)
check("  a jump past 72 days starts again", e["s"] == "110")
# The summary: the last 72 hours by hour, nothing for the first two, a short outage with a
# restart in hour 10, and the current (72nd) hour stopping 30 minutes in.
WIN = D0 - 71 * 3600
h = {}
for k in range(24, 71 * 12 + 6):
    t = WIN + k * 300 + 5
    hr = k // 12
    down = hr == 10 and 2 <= k % 12 < 4
    inv = "y" if (hr > 10 or (hr == 10 and k % 12 >= 4)) else "x"
    S.record(h, {"kiwix.service": (not down, inv)}, t)
now = WIN + 71 * 3600 + 1800
out = S.summarize(h, now)
u = out["units"]["kiwix.service"]
check(
    "summary: 72 hours, 72 days, the days' labels from the hub",
    len(u["hours"]) == 72
    and len(u["month"]) == 72
    and len(out["hour_cols"]) == 72
    and len(out["hour_full"]) == 72
    and len(out["month_days"]) == 72,
    u,
)
check(
    "  the outage's hour: 10 of 12 up, the start marked",
    u["hours"][10] == {"up": round(10 / 12, 3), "n": 12, "restarts": 1},
    u["hours"][10],
)
check("  before the record began: no data", u["hours"][0] is None and u["month"][0] is None)
check(
    "  the current (partial) hour: fewer samples",
    u["hours"][71] is not None and u["hours"][71]["n"] == 6,
    u["hours"][71],
)
check(
    "  in words' worth: the share up, the starts",
    u["summary"]["restarts"] == 1 and 0.9 < u["summary"]["up"] < 1,
    u["summary"],
)
# The sampler: only while the clock is trusted, one `systemctl show`, the file written whole.
calls = []
S.look = lambda units: (calls.append(list(units)), {"kiwix.service": (True, "z")})[1]
trust = {"ok": False}
smp = S.Sampler(lambda: ["kiwix.service", "gone.service"], trusted=lambda: trust["ok"])
check("sampler: nothing while the clock is not trusted", smp.tick(D0) is False and not S.FILE.exists() and not calls)
trust["ok"] = True
check(
    "  then a sample, of every unit at once",
    smp.tick(D0) is True
    and calls == [["kiwix.service", "gone.service"]]
    and json.loads(S.FILE.read_text())["units"]["kiwix.service"]["s"] == "110",
)
(T / "run").mkdir()
(T / "run" / "clock-trusted").write_text("")
check("the owner's mark counts as a trusted clock", S.clock_trusted())
check(
    "the server samples and sends the summary",
    "svchistory.Sampler(" in (Path(__file__).resolve().parents[1] / "irate_box/hub/server.py").read_text(),
)
from irate_box.hub import memhistory as M

mem = {"total": 1024, "available": 512, "swap_total": 256, "swap_free": 128}
hist = {"samples": []}
check("memory: first sample", M.record(hist, mem, D0) and hist["samples"][0]["available"] == 512)
check(
    "memory: duplicate samples and backward clock jumps ignored",
    not M.record(hist, mem, D0 + 10) and not M.record(hist, mem, D0 - 300),
)
for sample in range(1, M.LIMIT + 10):
    M.record(hist, mem, D0 + sample * M.EVERY)
check(
    "memory: seven days retained with a fixed size bound",
    len(hist["samples"]) == M.LIMIT and hist["samples"][-1]["at"] - hist["samples"][0]["at"] == M.KEEP,
)
M.FILE.write_text(json.dumps(hist))
check("memory: history survives reload", M.load() == hist)
check(
    "memory: expired and future samples not served",
    not M.snapshot(D0 - 1)["samples"] and not M.snapshot(D0 + 30 * 86400)["samples"],
)
M.FILE.write_text("not json")
check("memory: torn history starts empty", M.load() == {"samples": []})
M.FILE.write_text(json.dumps({"samples": [None, {}, dict(mem, at=D0, total=-1)]}))
check("memory: malformed samples are ignored", M.load()["samples"] == [])
M.FILE.unlink()
from unittest import mock

sampler = M.Sampler(trusted=lambda: False)
check("memory: no writes before the clock is trusted", not sampler.tick(D0) and not M.FILE.exists())
sampler = M.Sampler(trusted=lambda: True)
with (
    mock.patch.object(M, "memory", return_value=mem),
    mock.patch.object(M, "processes", return_value=[{"pid": 1, "name": "hub", "resident": 128}]),
):
    check(
        "memory: trusted sampler writes memory and largest processes",
        sampler.tick(D0) and M.snapshot(D0)["processes"][0]["name"] == "hub",
    )
    check("memory: restart does not duplicate the last sample", not sampler.tick(D0 + 1))
check("memory: stale process snapshots are not presented as current", not M.snapshot(D0 + 3 * M.EVERY)["processes"])
with mock.patch.object(M, "memory", return_value=mem), mock.patch.object(M, "processes", return_value=[]):
    sampler.tick(D0 + M.EVERY)
check(
    "memory: a sample is a line added, not the file rewritten",
    M.FILE.read_text().count("\n") == 2 and len(M.load()["samples"]) == 2,
)
M.FILE.write_text(json.dumps({"samples": [dict(mem, at=D0)]}))
with mock.patch.object(M, "memory", return_value=mem), mock.patch.object(M, "processes", return_value=[]):
    sampler.tick(D0 + M.EVERY)
check(
    "memory: the older single-object file turned into lines at the next sample",
    M.FILE.read_text().count("\n") == 2 and [x["at"] for x in M.load()["samples"]] == [D0, D0 + M.EVERY],
)
# The disk with each sample: the state folder's filesystem, as the disk meter reads it; an older sample without it, or
# with unsound values, keeps its memory and says nothing of the disk (never a zero).
M.FILE.unlink()
disk = {"disk_total": 8000, "disk_free": 3000}
with (
    mock.patch.object(M, "memory", return_value=mem),
    mock.patch.object(M, "disk", return_value=disk),
    mock.patch.object(M, "processes", return_value=[]),
):
    sampler.tick(D0 + 5 * M.EVERY)
got = M.load()["samples"][-1]
check(
    "disk: kept with the memory in each sample",
    got["disk_total"] == 8000 and got["disk_free"] == 3000 and got["available"] == 512,
    got,
)
M.FILE.write_text(
    json.dumps(
        {
            "samples": [
                dict(mem, at=D0),
                dict(mem, at=D0 + M.EVERY, disk_total=8000, disk_free=9000),
                dict(mem, at=D0 + 2 * M.EVERY, disk_total=8000, disk_free="x"),
                dict(mem, at=D0 + 3 * M.EVERY, **disk),
            ]
        }
    )
)
got = M.load()["samples"]
check(
    "disk: an older sample, or one with free over total or not a number: its memory kept, no disk said",
    len(got) == 4
    and all("disk_total" not in s and "disk_free" not in s for s in got[:3])
    and got[3]["disk_free"] == 3000,
    got,
)
with mock.patch.object(M, "STATE", T / "no-such-folder"):
    check("disk: no filesystem to read: nothing said, the memory sample still taken", M.disk() == {})
check(
    "disk: read from a real folder: total over zero, free within it",
    0 <= M.disk()["disk_free"] <= M.disk()["disk_total"],
)
M.FILE.unlink()

from irate_box.hub import jsonlog as J

jl = T / "log.jsonl"
check("jsonlog: nothing to append to: the caller writes it whole", not J.append(jl, [{"a": 1}]))
J.rewrite(jl, [{"a": 1}])
check(
    "jsonlog: appended a line, read back in order",
    J.append(jl, [{"a": 2}]) and J.read(jl) == [{"a": 1}, {"a": 2}] and J.count(jl) == 2,
)
jl.write_text(jl.read_text() + '{"a": 3')
check("jsonlog: a line cut short by a power cut is left out", J.read(jl) == [{"a": 1}, {"a": 2}])
old = T / "old.json"
old.write_text(json.dumps([{"a": 1}]))
check(
    "jsonlog: the older JSON array is read, and not appended to",
    J.read(old) == [{"a": 1}] and not J.append(old, [{"a": 2}]) and J.count(old) > 1000,
)
link = T / "link.jsonl"
link.symlink_to(jl)
check("jsonlog: never appended through a link", not J.append(link, [{"a": 9}]) and J.read(jl)[-1] != {"a": 9})
proc = T / "proc"
proc.mkdir()
(proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 600 kB\nSwapTotal: 200 kB\nSwapFree: 150 kB\n")
for pid in range(1, 8):
    folder = proc / str(pid)
    folder.mkdir()
    (folder / "statm").write_text(f"100 {pid}\n")
    (folder / "comm").write_text(f"process-{pid}\n")
with mock.patch.object(M, "PROC", proc):
    check(
        "memory: uses available memory and swap in bytes",
        M.memory() == {"total": 1024000, "available": 614400, "swap_total": 204800, "swap_free": 153600},
    )
    check(
        "memory: only the five largest processes, ranked by resident bytes",
        [entry["pid"] for entry in M.processes()] == [7, 6, 5, 4, 3],
    )
    (proc / "meminfo").write_text("MemTotal: 1000 kB\nMemAvailable: 600 kB\n")
    check("memory: a system without swap still records", M.memory()["swap_total"] == 0 and M.memory()["swap_free"] == 0)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
