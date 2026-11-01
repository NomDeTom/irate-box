# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's findings for new boards (root/health.py, check_boards), offline: a setup hotspot nobody has
answered, and a board the box can see on its own network, each with what it means and where to set it up; nothing
for a box that sees neither. python3 tests/sim_boarddoctor.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="boarddoctor-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ROOT_STATE_DIR=str(T / "root-state"))
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


spots = [{"ssid": "armbiansetup-luckfox-lyra-zero-w", "host": "luckfox-lyra-zero-w", "signal": 60, "boards": 1}]
boards = [
    {"address": "192.168.4.12", "host": "bananapi", "mac": "b8:41:d9:4c:01:ad", "vendor": "Allwinner", "board": True}
]
found = health.check_boards(spots=spots, seen=boards)
by_id = {f["id"]: f for f in found}
check(
    "a setup hotspot nobody answered, and a board on the box's own network, each said",
    set(by_id) == {"boards-setup-hotspot", "boards-default-password"}
    and by_id["boards-setup-hotspot"]["status"] == "warn"
    and by_id["boards-setup-hotspot"]["go"]["section"] == "rockchip"
    and by_id["boards-default-password"]["status"] == "warn",
    found,
)
check(
    "  each says what it means and what to do, in the doctor's message IDs",
    {"boards.hotspot.found", "boards.hotspot.why", "boards.hotspot.fix"} <= message_ids(by_id["boards-setup-hotspot"])
    and {"boards.default.found", "boards.default.why", "boards.default.fix"}
    <= message_ids(by_id["boards-default-password"]),
    {f["id"]: message_ids(f) for f in found},
)
check(
    "  the evidence names the hotspot and the board",
    "armbiansetup-luckfox-lyra-zero-w" in by_id["boards-setup-hotspot"]["evidence"]
    and "192.168.4.12" in by_id["boards-default-password"]["evidence"],
)
check("a box that sees neither has no finding", health.check_boards(spots=[], seen=[]) == [])

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
