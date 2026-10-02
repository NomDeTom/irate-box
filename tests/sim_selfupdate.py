# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's automatic updates (library/selfupdate.py) against a throwaway state folder: the
root helper's answers written by hand, a stand-in hub for "anyone on it?", the clock and the
window chosen. python3 tests/sim_selfupdate.py"""
import json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="selfupdate-"))
ONLINE = {"n": 0}
class Hub(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"online": ONLINE["n"]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = HTTPServer(("127.0.0.1", 0), Hub); threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update(HUB_STATE_DIR=str(T), HUB_STATUS_URL=f"http://127.0.0.1:{srv.server_port}/status")
REPO = str(Path(__file__).resolve().parents[1]); sys.path.insert(0, REPO)
from irate_box.library import selfupdate as U  # noqa: E402
(T / "control" / "requests").mkdir(parents=True); (T / "control" / "results").mkdir()
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def queued():
    return [json.loads(p.read_text()) for p in (T / "control" / "requests").glob("*.json")]
def answer(ok=True, message="done", update=None):
    for r in queued():
        (T / "control" / "results" / f"{r['id']}.json").write_text(json.dumps({"id": r["id"], "ok": ok, "message": message}))
        (T / "control" / "requests" / f"{r['id']}.json").unlink()
    if update is not None:
        (T / "control" / "update.json").write_text(json.dumps(update))
H = 3600
def pol(**kw):
    p = {"hub_check_every_hours": 24, "hub_auto": 0, "hub_window_start": 2, "hub_window_end": 5}; p.update(kw); return p
t0 = 1_800_000_000
quiet = {"log": lambda *a: None}

r = U.step(pol(), now=t0, **quiet)
check("first round: asks the root helper to check", r == "asked to check" and [q["action"] for q in queued()] == ["update-check"], (r, queued()))
check("a second round waits for the answer", U.step(pol(), now=t0 + H, **quiet).startswith("waiting for the root helper"))
answer(message="update available: 1 new commit, abc1234", update={"up_to_date": False, "available": "abc1234", "verified": None})
r = U.step(pol(), now=t0 + 2 * H, **quiet)
check("check only (hub_auto 0): reports it, asks nothing more", "abc1234" in r and not queued(), (r, queued()))
check("  the answer is kept for /admin", U.load_state()["last"]["step"] == "check")
r = U.step(pol(hub_auto=1), now=t0 + 3 * H, **quiet)
check("hub_auto 1: asks to fetch", r == "asked to fetch" and queued()[0]["action"] == "update-fetch", r)
answer(message="update fetched, verified", update={"up_to_date": False, "available": "abc1234", "verified": "abc1234"})
r = U.step(pol(hub_auto=1), now=t0 + 4 * H, **quiet)
check("hub_auto 1, verified: reports the fetch, no install", "update fetched, verified" in r and not queued(), r)
r = U.step(pol(hub_auto=1), now=t0 + 4 * H + 60, **quiet)
check("  then: ready to install on /admin", "ready to install" in r and not queued(), r)

real_in_window = U.in_window
U.in_window = lambda policy, hour=None: False
r = U.step(pol(hub_auto=2), now=t0 + 5 * H, **quiet)
check("hub_auto 2, outside the window: waits, says when", "installs between 02:00 and 05:00" in r and not queued(), r)
U.in_window = lambda policy, hour=None: True
ONLINE["n"] = 3
r = U.step(pol(hub_auto=2), now=t0 + 6 * H, **quiet)
check("inside the window, 3 on the hub: waits for nobody", "waiting for nobody" in r and "(3 now)" in r and not queued(), r)
ONLINE["n"] = 0
(T / "control" / "requests" / "other.json").write_text("{}")
r = U.step(pol(hub_auto=2), now=t0 + 7 * H, **quiet)
check("something else queued for the root helper: waits", "requests waiting" in r and len(queued()) == 1, r)
(T / "control" / "requests" / "other.json").unlink()
(T / "control" / "update-progress.json").write_text(json.dumps({"pid": os.getpid(), "action": "addon"}))
r = U.step(pol(hub_auto=2), now=t0 + 7 * H, **quiet)
check("the root helper running an add-on: waits", "busy (addon)" in r and not queued(), r)
(T / "control" / "update-progress.json").unlink()
r = U.step(pol(hub_auto=2), now=t0 + 8 * H, **quiet)
check("nobody on, in the window, idle: asks to install", r == "asked to install" and queued()[0]["action"] == "update-install", r)
answer(message="updated to abc1234", update={"up_to_date": True, "available": "abc1234", "verified": "abc1234"})
r = U.step(pol(hub_auto=2), now=t0 + 9 * H, **quiet)
check("after the install: up to date", "updated to abc1234" in r and not queued(), r)
r = U.step(pol(hub_auto=2), now=t0 + 9 * H + 60, **quiet)
check("and then simply up to date", r == "up to date", r)
# A new version that fails verification: fetched once, never again, never installed.
r = U.step(pol(hub_auto=2), now=t0 + 26 * H, **quiet)
check("a day later: checks again", r == "asked to check", r)
answer(update={"up_to_date": False, "available": "def5678", "verified": None})
r = U.step(pol(hub_auto=2), now=t0 + 27 * H, **quiet)
check("new version: asks to fetch it", r == "asked to fetch", r)
answer(ok=False, message="did not pass verification", update={"up_to_date": False, "available": "def5678", "verified": None})
r = U.step(pol(hub_auto=2), now=t0 + 28 * H, **quiet)
check("failed verification: says so", "did not pass verification" in r and not queued(), r)
r = U.step(pol(hub_auto=2), now=t0 + 29 * H, **quiet)
check("and does not fetch it again, nor install it", "Updates doctor" in r and not queued(), r)
# Off entirely
r = U.step(pol(hub_check_every_hours=0), now=t0 + 60 * H, **quiet)
check("checks off: asks nothing", not queued(), r)
# No answer for hours: gives up waiting
U.step(pol(), now=t0 + 80 * H, **quiet); assert queued()
for p in (T / "control" / "requests").glob("*.json"): p.unlink()
r = U.step(pol(), now=t0 + 80 * H + 4 * H, **quiet)
check("no answer in 3 hours: stops waiting", "no answer from the root helper" in r, r)
# The window, wrapping midnight
U.in_window = real_in_window
w = {"hub_window_start": 23, "hub_window_end": 4}
check("window 23-04: 23, 0 and 3 in; 4 and 12 out", all(U.in_window(w, h) for h in (23, 0, 3)) and not any(U.in_window(w, h) for h in (4, 12)))
check("window 02-05: 2 and 4 in; 5 and 1 out", all(U.in_window(pol(), h) for h in (2, 4)) and not any(U.in_window(pol(), h) for h in (5, 1)))
check("an empty window never installs", not any(U.in_window({"hub_window_start": 3, "hub_window_end": 3}, h) for h in range(24)))
srv.shutdown()
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
