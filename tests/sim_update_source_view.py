# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What /admin/update gives the Update source form and the update doctor's date (item 148), from the files root
leaves: the recorded source (install.sh's world-readable copy of install-options), what the repository said of its
branches (control/update-branches.json), and whether the doctor's report is older than the last check or a change of
source. Read in the hub's own process, nothing started. python3 tests/sim_update_source_view.py"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATE = Path(tempfile.mkdtemp(prefix="update-view-"))
os.environ.update(HUB_STATE_DIR=str(STATE), HUB_ETC_DIR=str(STATE))
(STATE / "control").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import server as S

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


snap = S.update_snapshot()
check(
    "nothing recorded: no source, no branches, the doctor not stale",
    snap["source"] is None and snap["branches"] is None and snap["doctor_stale"] is False,
    snap,
)

(STATE / "install-options").write_text(
    "--web\nnginx\n--repo\nhttps://github.com/NomDeTom/irate-box\n--branch\nfix-tls\n"
)
kept = {
    "repo": "https://github.com/NomDeTom/irate-box",
    "default": "main",
    "branches": ["main", "fix-tls"],
    "branch": "fix-tls",
    "installed": "b913cf4",
    "standing": "elsewhere",
}
(STATE / "control" / "update-branches.json").write_text(json.dumps(kept))
now = time.time()
(STATE / "control" / "doctor.json").write_text(json.dumps({"at": now - 3600, "findings": []}))
os.utime(STATE / "install-options", (now - 7200, now - 7200))
snap = S.update_snapshot()
check(
    "the recorded source: the repository and the branch, the other options left out",
    snap["source"] == {"repo": "https://github.com/NomDeTom/irate-box", "branch": "fix-tls"},
    snap["source"],
)
check("  what the repository said of its branches, as root kept it", snap["branches"] == kept, snap["branches"])
check("  a doctor's report newer than the source: not stale", snap["doctor_stale"] is False)

(STATE / "control" / "update.json").write_text(json.dumps({"available": "80c97e7", "relation": "diverged"}))
check("a check after the report: the report is stale", S.update_snapshot()["doctor_stale"] is True)
(STATE / "control" / "doctor.json").write_text(json.dumps({"at": time.time() + 5, "findings": []}))
check("  the doctor run again: not stale", S.update_snapshot()["doctor_stale"] is False)

(STATE / "install-options").write_text("--repo\nhttps://github.com/NomDeTom/irate-box\n")
check(
    "no branch recorded: main, as the installer takes it",
    S.update_snapshot()["source"] == {"repo": "https://github.com/NomDeTom/irate-box", "branch": "main"},
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
