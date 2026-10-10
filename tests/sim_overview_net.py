# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Overview's network heatmap (server.net_uptime_72h): each link recorded in the last 72 hours, the box's link to
the network first; a link never up in that time (a port with no cable) left off, as it would be red throughout.
python3 tests/sim_overview_net.py"""
import json, os, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="overview-net-"))
os.environ.update(HUB_STATE_DIR=str(T))
(T / "control").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import server  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
now = int(time.time() // 300)
first = now - 600
hist = {"slot": 300, "ifaces": {
    "wlan0": {"first": first, "s": "u" * 590 + "d" * 5 + "u" * 5, "kind": "uplink"},
    "eth0": {"first": first, "s": "d" * 600, "kind": "link"},
    "eth1": {"first": first, "s": "d" * 300 + "u" * 300, "kind": "link"}}}
(T / "control" / "uplink-history.json").write_text(json.dumps(hist))
server.uplink.HISTORY = T / "control" / "uplink-history.json"
out = server.net_uptime_72h()
check("the uplink first, a link up some of the time kept, a port never up left off", list(out) == ["wlan0", "eth1"], list(out))
check("  each by hour, 72 of them", all(len(v["hours"]) == 72 for v in out.values()))
hist["ifaces"]["wlan0"]["s"] = "d" * 600
(T / "control" / "uplink-history.json").write_text(json.dumps(hist))
check("the uplink down throughout: still shown (that is the news)", "wlan0" in server.net_uptime_72h())
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
