# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapting to GitHub's rate limit, offline: it starts from the first answer, using what GitHub says is
left of the shared allowance; requests are counted over a rolling hour and the budget runs out; the gap
between requests grows with the share used; three rate-limit refusals in a row halve it (never below the
floor); a clean scheduled run gives some back; Stop adapting keeps an off marker. The network is stood
in for. python3 tests/sim_ghpace.py"""

import io
import json
import os
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="ghpace-"))
os.environ.update(HUB_STATE_DIR=str(T), GITHUB_TOKEN="")
sys.path.insert(0, str(REPO))
from irate_box.library import librarian as L

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Resp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


limit = {"n": 60}
mode = {"refuse": False}


def fake_open(req, timeout=None):
    if req.full_url.endswith("/rate_limit"):
        return Resp(json.dumps({"resources": {"core": {"limit": limit["n"]}}}).encode())
    if mode["refuse"]:
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "rate",
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "60", "X-RateLimit-Reset": str(int(time.time()) + 600)},
            None,
        )
    return Resp(b"{}")


L.urllib.request.urlopen = fake_open

check("not adapted: no budget, nothing counted", L.pace_left() is None)
L._open(f"{L.API}/repos/a/b/releases").close()
check("  and no hits kept", not L.pace().get("hits"))
L._note_rate(
    {
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "59",
        "X-RateLimit-Reset": str(int(time.time()) + 1800),
    }
)
check(
    "the first answer starts it at the shared remainder: 49",
    L.pace()["budget"] == 49 and L.pace_left() == 49,
    L.pace(),
)
p = L.adapt_rate()
check(
    "Adapt without a token: the budget is 50 of 60", p["budget"] == 50 and p["limit"] == 60 and L.pace_left() == 50, p
)
for _ in range(5):
    L._open(f"{L.API}/repos/a/b/releases").close()
check("requests are counted: 45 left", L.pace_left() == 45, L.pace_left())
L._open(f"{L.API}/rate_limit").close()
check("  the free /rate_limit call is not", L.pace_left() == 45)
L._rate.update(limit=60, remaining=60, reset=int(time.time()) + 1800)
check("stretching: plenty left, no gap", L._pace_gap() == 0)
L._rate.update(remaining=30)
check("  half used, still no gap", L._pace_gap() == 0)
L._rate.update(remaining=15)
check("  three quarters used, 30 seconds", L._pace_gap() == 30)
L._rate.update(remaining=6)
check("  nine tenths used, 48 seconds", L._pace_gap() == 48)
L._rate.update(reset=int(time.time()) - 1)
check("  a past reset, no gap", L._pace_gap() == 0)
old = L.pace()
old["hits"] = [int(time.time()) - 4000] * 3 + old["hits"]
L._write_json(L.PACE_FILE, old)
check("  hits older than an hour fall away", L.pace_left() == 45)
p = L.pace()
p["hits"] = [int(time.time())] * 50
L._write_json(L.PACE_FILE, p)
check("a spent budget: 0 left", L.pace_left() == 0)
p["hits"] = []
L._write_json(L.PACE_FILE, p)
mode["refuse"] = True
for _i in range(2):
    try:
        L._open(f"{L.API}/x")
    except L.LibrarianError:
        pass
check("two refusals: no back-off yet", L.pace()["budget"] == 50 and L.pace()["failures"] == 2, L.pace())
try:
    L._open(f"{L.API}/x")
except L.LibrarianError:
    pass
check(
    "the third halves the budget to 25 and starts counting again",
    L.pace()["budget"] == 25 and L.pace()["failures"] == 0,
    L.pace(),
)
for _ in range(9):
    try:
        L._open(f"{L.API}/x")
    except L.LibrarianError:
        pass
check("more refusals keep halving, never below the floor", L.pace()["budget"] == L.PACE_FLOOR, L.pace()["budget"])
mode["refuse"] = False
L._rate.clear()  # the refusals had said none left; a new hour has no reading yet
L._pace_clean_run()
check(
    "a clean run gives some back, up to what GitHub allows", L.PACE_FLOOR < L.pace()["budget"] <= 50, L.pace()["budget"]
)
# A budget learned near the end of a busy hour is not stuck small after GitHub's window resets.
L._write_json(L.PACE_FILE, {})
L._rate.clear()
reset = int(time.time()) + 600
L._note_rate({"X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "12", "X-RateLimit-Reset": str(reset)})
check("a budget learned with 12 left is 5", L.pace()["budget"] == L.PACE_FLOOR, L.pace())
p = L.pace()
p["reset"] = int(time.time()) - 1
p["hits"] = [int(time.time())]
L._write_json(L.PACE_FILE, p)
L._rate.update(limit=60, remaining=60, reset=int(time.time()) - 1)
L._pace_clean_run()
check(
    "  after its reset, a clean run starts the new hour at 50",
    L.pace()["budget"] == 50 and not L.pace().get("hits"),
    L.pace(),
)
p = L.pace()
p["budget"] = L.PACE_FLOOR
p["reset"] = int(time.time()) - 1
p["hits"] = [int(time.time())]
L._write_json(L.PACE_FILE, p)
L._rate.clear()
check("  and pace_left does it too", L.pace_left() == 50, L.pace_left())
# The gap belongs to the timer's run: one started by hand goes ahead.
L._write_json(L.PACE_FILE, {"budget": 50, "limit": 60, "hits": []})
L._rate.update(limit=60, remaining=15, reset=int(time.time()) + 1800)
slept = []
real_sleep = L.time.sleep
L.time.sleep = lambda seconds: slept.append(seconds)
try:
    L._memo.clear()
    L._api("/repos/a/b/releases?per_page=20&hand=1")
    check("a hand run with three quarters used does not wait", not slept, slept)
    L._memo.clear()
    with L._pace_run(True):
        L._api("/repos/a/b/releases?per_page=20&scheduled=1")
    check("  a scheduled run waits 30 seconds", slept == [30], slept)
finally:
    L.time.sleep = real_sleep
# The allowance follows the token: a budget learned without one is dropped, and the next answer sets it anew.
L._write_json(L.PACE_FILE, {"budget": 50, "limit": 60, "hits": [int(time.time())], "failures": 0})
L.set_token("github_pat_test")
check("saving a token drops the budget learned without it", not L.pace().get("budget"), L.pace())
L._note_rate(
    {
        "X-RateLimit-Limit": "5000",
        "X-RateLimit-Remaining": "5000",
        "X-RateLimit-Reset": str(int(time.time()) + 1800),
    }
)
check("  the next answer starts it from the token's allowance: 4990", L.pace()["budget"] == 4990, L.pace())
L.set_token("")
check("removing a token drops it too", not L.pace().get("budget"), L.pace())
L._note_rate(
    {
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "60",
        "X-RateLimit-Reset": str(int(time.time()) + 1800),
    }
)
check("  and the next answer starts it from 60: 50", L.pace()["budget"] == 50, L.pace())
limit["n"] = 5000
check("with a token's allowance: 4990", L.adapt_rate()["budget"] == 4990)
L.clear_pace()
check("Stop adapting: an off marker, no budget", L.pace().get("off") and L.pace_left() is None, L.pace())
L._note_rate(
    {
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "6",
        "X-RateLimit-Reset": str(int(time.time()) + 1800),
    }
)
check("  a later answer does not start it again, and it does not stretch", L.pace_left() is None and L._pace_gap() == 0)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
