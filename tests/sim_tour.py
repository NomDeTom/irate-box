# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The setup tour's record, against a hub it starts: setup_decided holds only
the decisions there are, each once at most; nothing decided to begin with, so the box runs on the
defaults. python3 tests/sim_tour.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="tour-")
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/admin/settings", body=json.dumps(body).encode(), headers={"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET,
                  "X-Irate-Admin": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        return json.loads(c.getresponse().read())
    check("nothing decided to begin with", post({})["setup_decided"] == [])
    check("decisions recorded", post({"setup_decided": ["visitors", "https"]})["setup_decided"] == ["visitors", "https"])
    for bad in (["visitors", "nonsense"], "visitors", ["https"] * 18):
        check(f"refused: {str(bad)[:40]}", post({"setup_decided": bad})["setup_decided"] == ["visitors", "https"])
    check("nothing muted to begin with", post({})["setup_muted"] == [])
    check("a step muted", post({"setup_muted": ["backup"]})["setup_muted"] == ["backup"])
    check("refused: an unknown step", post({"setup_muted": ["backup", "nonsense"]})["setup_muted"] == ["backup"])
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
