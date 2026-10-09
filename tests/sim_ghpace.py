# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapting to GitHub's rate limit (Tom, 2026-10-09), offline: Adapt asks GitHub what it allows and sets an
hourly budget just under it; requests are counted over a rolling hour and the budget runs out; three
rate-limit refusals in a row halve it (never below the floor); a clean scheduled run gives some back.
The network is stood in for. python3 tests/sim_ghpace.py"""
import io, json, os, sys, tempfile, time, urllib.error
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="ghpace-"))
os.environ.update(HUB_STATE_DIR=str(T), GITHUB_TOKEN="")
sys.path.insert(0, str(REPO))
from irate_box.library import librarian as L  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

class Resp(io.BytesIO):
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): return False
limit = {"n": 60}
mode = {"refuse": False}
def fake_open(req, timeout=None):
    if req.full_url.endswith("/rate_limit"):
        return Resp(json.dumps({"resources": {"core": {"limit": limit["n"]}}}).encode())
    if mode["refuse"]:
        raise urllib.error.HTTPError(req.full_url, 403, "rate", {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "60", "X-RateLimit-Reset": str(int(time.time()) + 600)}, None)
    return Resp(b"{}")
L.urllib.request.urlopen = fake_open

check("not adapted: no budget, nothing counted", L.pace_left() is None)
L._open(f"{L.API}/repos/a/b/releases").close()
check("  and no hits kept", not L.pace().get("hits"))
p = L.adapt_rate()
check("Adapt without a token: the budget is 50 of 60", p["budget"] == 50 and p["limit"] == 60 and L.pace_left() == 50, p)
for _ in range(5):
    L._open(f"{L.API}/repos/a/b/releases").close()
check("requests are counted: 45 left", L.pace_left() == 45, L.pace_left())
L._open(f"{L.API}/rate_limit").close()
check("  the free /rate_limit call is not", L.pace_left() == 45)
old = L.pace(); old["hits"] = [int(time.time()) - 4000] * 3 + old["hits"]; L._write_json(L.PACE_FILE, old)
check("  hits older than an hour fall away", L.pace_left() == 45)
p = L.pace(); p["hits"] = [int(time.time())] * 50; L._write_json(L.PACE_FILE, p)
check("a spent budget: 0 left", L.pace_left() == 0)
p["hits"] = []; L._write_json(L.PACE_FILE, p)
mode["refuse"] = True
for i in range(2):
    try: L._open(f"{L.API}/x")
    except L.LibrarianError: pass
check("two refusals: no back-off yet", L.pace()["budget"] == 50 and L.pace()["failures"] == 2, L.pace())
try: L._open(f"{L.API}/x")
except L.LibrarianError: pass
check("the third halves the budget to 25 and starts counting again", L.pace()["budget"] == 25 and L.pace()["failures"] == 0, L.pace())
for _ in range(9):
    try: L._open(f"{L.API}/x")
    except L.LibrarianError: pass
check("more refusals keep halving, never below the floor", L.pace()["budget"] == L.PACE_FLOOR, L.pace()["budget"])
mode["refuse"] = False
L._pace_clean_run()
check("a clean run gives some back, up to what GitHub allows", L.PACE_FLOOR < L.pace()["budget"] <= 50, L.pace()["budget"])
limit["n"] = 5000
check("with a token's allowance: 4990", L.adapt_rate()["budget"] == 4990)
L.clear_pace()
check("Stop adapting: back to nothing", L.pace_left() is None)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
