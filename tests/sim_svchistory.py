# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The services' uptime (svchistory.py), offline: five-minute samples into
hourly buckets (up of taken, starts by a new InvocationID), gaps as no data, the clock going back,
72 days kept; the last 72 hours by hour and 72 days by day the page draws (githubstatus.com's
format); the sampler writing only while the clock is trusted. python3 tests/sim_svchistory.py"""
import json, os, sys, tempfile, time
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="svchist-"))
os.environ.update(HUB_STATE_DIR=str(T), HUB_RUN_DIR=str(T / "run"), TZ="UTC")
time.tzset()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import svchistory as S  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
D0 = 1791331200  # 2026-10-07 00:00 UTC
h = {}
for k in range(12):                       # 00:00-01:00: up, down at 00:30-00:40, started again at 00:40
    S.record(h, {"kiwix.service": (not 6 <= k < 8, "a" if k < 8 else "b"), "mosquitto.service": (True, "m")}, D0 + k * 300 + 5)
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
check("  72 days kept", len(e["s"]) == 3 * S.KEEP and e["first"] + S.KEEP - 1 == (D0 + 4 * 3600 + (S.KEEP + 4) * 3600) // 3600)
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
check("summary: 72 hours, 72 days, the days' labels from the hub", len(u["hours"]) == 72 and len(u["month"]) == 72
      and len(out["hour_cols"]) == 72 and len(out["hour_full"]) == 72 and len(out["month_days"]) == 72, u)
check("  the outage's hour: 10 of 12 up, the start marked", u["hours"][10] == {"up": round(10 / 12, 3), "n": 12, "restarts": 1}, u["hours"][10])
check("  before the record began: no data", u["hours"][0] is None and u["month"][0] is None)
check("  the current (partial) hour: fewer samples", u["hours"][71] is not None and u["hours"][71]["n"] == 6, u["hours"][71])
check("  in words' worth: the share up, the starts", u["summary"]["restarts"] == 1 and 0.9 < u["summary"]["up"] < 1, u["summary"])
# The sampler: only while the clock is trusted, one `systemctl show`, the file written whole.
calls = []
S.look = lambda units: (calls.append(list(units)), {"kiwix.service": (True, "z")})[1]
trust = {"ok": False}
smp = S.Sampler(lambda: ["kiwix.service", "gone.service"], trusted=lambda: trust["ok"])
check("sampler: nothing while the clock is not trusted", smp.tick(D0) is False and not S.FILE.exists() and not calls)
trust["ok"] = True
check("  then a sample, of every unit at once", smp.tick(D0) is True and calls == [["kiwix.service", "gone.service"]]
      and json.loads(S.FILE.read_text())["units"]["kiwix.service"]["s"] == "110")
(T / "run").mkdir(); (T / "run" / "clock-trusted").write_text("")
check("the owner's mark counts as a trusted clock", S.clock_trusted())
check("the server samples and sends the summary", "svchistory.Sampler(" in (Path(__file__).resolve().parents[1] / "irate_box/hub/server.py").read_text())
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
