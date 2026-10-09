# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The root helper busy, not stuck (a kit's minutes-long refresh is not "not answering"): a request waiting behind a job the helper is still running is said as such; one waiting
with the helper not running, or behind a job running for hours, is still the alarm. systemctl is stood
in. python3 tests/sim_helper_busy.py"""
import os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="helper-busy-"))
os.environ.update(HUB_STATE_DIR=str(T), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.hub import server  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

server.CONTROL_REQUESTS.mkdir(parents=True, exist_ok=True)
req = server.CONTROL_REQUESTS / "aaaa.json"
req.write_text('{"action": "kit-fetch"}')
old = time.time() - 180
os.utime(req, (old, old))
_mono = time.monotonic
time.monotonic = lambda: _mono() + 1e5  # a freshly booted runner's uptime is under the 240 s the test needs
unit = {"state": "activating", "since": time.monotonic() - 240}
def fake_run(cmd, **kw):
    out = f"ActiveState={unit['state']}\nInactiveExitTimestampMonotonic={int(unit['since'] * 1e6)}\n"
    return subprocess.CompletedProcess(cmd, 0, out, "")
server.subprocess.run = fake_run

h = server.helper_state()
check("a request 3 min old behind a job running 4 min: busy, not stuck", h["stuck"] is False and 230 <= h["busy"] <= 250, h)
import json  # noqa: E402
log = json.loads(server.HELPER_BUSY_LOG.read_text())
check("  logged: the job's start, how long, the longest wait, what waited", len(log) == 1 and log[0]["waiting"] == ["kit-fetch"]
      and 175 <= log[0]["longest_wait"] <= 190 and 230 <= log[0]["busy_for"] <= 250, log)
os.utime(req, (old - 120, old - 120))
h = server.helper_state()
log = json.loads(server.HELPER_BUSY_LOG.read_text())
check("  the same job looked at again: one entry, its longest wait grown", len(log) == 1 and log[0]["longest_wait"] >= 295, log)
check("  the week's summary for the page", h["busy_log"]["count"] == 1 and h["busy_log"]["longest_wait"] >= 295, h["busy_log"])
unit["state"] = "inactive"
h = server.helper_state()
check("the helper not running: stuck, the alarm", h["stuck"] is True and h["busy"] is None, h)
unit.update(state="activating", since=time.monotonic() - 5 * 3600)
h = server.helper_state()
check("a job running 5 hours: stuck too", h["stuck"] is True and h["busy"] is None, h)
check("  and not logged as a busy spell", len(json.loads(server.HELPER_BUSY_LOG.read_text())) == 1)
req.unlink()
unit.update(state="activating", since=time.monotonic() - 60)
h = server.helper_state()
check("nothing waiting: neither", h["stuck"] is False and h["busy"] is None, h)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
