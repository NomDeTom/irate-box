# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's accepted list (server.health_accept): a review finding accepted by its id, kept in the hub's own
file, sent with the doctor's report and the Network page's; taken back; ids it refuses. python3 tests/sim_health_accept.py"""
import os
import sys
import tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="health-accept-"))
os.environ["HUB_STATE_DIR"] = str(T)
sys.path.insert(0, str(REPO))
from irate_box.hub import server  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

check("nothing accepted at first", server.health_accepted() == {})
server.health_accept("other-failed:armbian-led-state.service", "The image's LED service failed", True)
server.health_accept("net:other-ap:33564e5b-5cb3-4b45-ac5f-a092c4f7ddba", "Another hotspot profile: Hotspot", True)
acc = server.health_accepted()
check("accepted: by the finding's id, with its title and when", set(acc) == {"other-failed:armbian-led-state.service", "net:other-ap:33564e5b-5cb3-4b45-ac5f-a092c4f7ddba"}
      and acc["other-failed:armbian-led-state.service"]["title"] == "The image's LED service failed" and acc["other-failed:armbian-led-state.service"]["at"] > 0, acc)
check("  kept in the hub's own file", (T / "health-accepted.json").is_file())
check("  sent with the doctor's report and the Network page's", server.health_snapshot()["accepted"] == acc and server.network_snapshot()["accepted"] == acc)
server.health_accept("other-failed:armbian-led-state.service", "", False)
check("taken back: gone from the list, the other kept", set(server.health_accepted()) == {"net:other-ap:33564e5b-5cb3-4b45-ac5f-a092c4f7ddba"})
for bad in ("", "../x", " spaced", None, "x" * 200):
    try:
        server.health_accept(bad, "", True); check(f"refused: {bad!r}", False)
    except ValueError:
        check(f"refused: {str(bad)[:20]!r}", True)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
