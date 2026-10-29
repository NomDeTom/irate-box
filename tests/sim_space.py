# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Box doctor's space check (root/health.py check_space), offline: the hub's state on a small volume of its own
(the Lyra's NAND, 223 MB) judged by its size, not the card's minimum, with advice for that volume; the card's own
minimum where the state is on the card. python3 tests/sim_space.py"""

import os
import shutil
import sys
import tempfile
from collections import namedtuple
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="space-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "state").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


Usage = namedtuple("Usage", "total used free")
MB = 1 << 20
disks = {}
health.shutil.disk_usage = lambda path: disks[str(path)]
own = {"state": True}
health._own_volume = lambda path: own["state"]


def state_findings():
    return [f for f in health.check_space() if f["id"] == f"space:{health.STATE}"]


disks["/"] = Usage(58_000 * MB, 23_000 * MB, 35_000 * MB)
disks[str(health.STATE)] = Usage(223 * MB, 44 * MB, 174 * MB)
check("the state on the NAND (223 MB, 174 MB free): no finding, though under the card's 200 MB", state_findings() == [])

disks[str(health.STATE)] = Usage(223 * MB, 180 * MB, 43 * MB)
found = state_findings()
check(
    "  nearly full (43 MB free, under a quarter of it): a problem, with the volume's own advice",
    len(found) == 1 and found[0]["status"] == "problem" and "space.low.fix.own" in message_ids(found[0]),
    found,
)

own["state"] = False
disks[str(health.STATE)] = Usage(58_000 * MB, 57_850 * MB, 150 * MB)
found = state_findings()
check(
    "the state on the card's own filesystem: the card's 200 MB minimum, and the card's advice",
    len(found) == 1 and "space.low.fix" in message_ids(found[0]) and "space.low.fix.own" not in message_ids(found[0]),
    found,
)
disks[str(health.STATE)] = Usage(58_000 * MB, 23_000 * MB, 35_000 * MB)
check("  with room on the card: no finding", state_findings() == [])

shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
