# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One pattern for every update, the librarian's half: each surface its own pair, how often
(hours, 0 by hand) and what to do; a policy saved in the old form carried over as apps and books had it;
fetching-only surfaces capped at Fetch; Manual keeps the toolkits' cache as it is.
python3 tests/sim_update_pattern.py"""
import json, os, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="pattern-"))
os.environ.update(HUB_STATE_DIR=str(T), HUB_LIB_DIR=str(T / "lib"))
sys.path.insert(0, str(REPO))
from irate_box.library import librarian as L  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
L.SOURCES_FILE.parent.mkdir(parents=True, exist_ok=True)
p = L.load_config()["policy"]
check("a new box: 24 hours everywhere; the mirrors and firmware fetch", (p["mirrors_every_hours"], p["mirrors_auto"], p["firmware_every_hours"],
      p["firmware_auto"], p["kits_every_hours"]) == (24, 1, 24, 1, 24), p)
L.SOURCES_FILE.write_text(json.dumps({"policy": {"check_every_hours": 168, "auto_install": 2}, "sources": []}))
p = L.load_config()["policy"]
check("saved in the old form (weekly, auto-install): each surface as apps and books were, Install read as Fetch where fetching is the update",
      (p["mirrors_every_hours"], p["mirrors_auto"], p["firmware_every_hours"], p["firmware_auto"], p["kits_every_hours"]) == (168, 1, 168, 1, 168), p)
L.SOURCES_FILE.write_text(json.dumps({"policy": {"check_every_hours": 0, "auto_install": 0}, "sources": []}))
p = L.load_config()["policy"]
check("  manual, flag: the same", (p["mirrors_every_hours"], p["mirrors_auto"], p["kits_every_hours"]) == (0, 0, 0), p)
L.set_policy(mirrors_every_hours=6, mirrors_auto=1, check_every_hours=24)
p = L.load_config()["policy"]
check("then each its own: the mirrors every 6 hours, apps and books daily, the firmware still as it was", (p["mirrors_every_hours"], p["mirrors_auto"],
      p["check_every_hours"], p["firmware_every_hours"]) == (6, 1, 24, 0), p)
for bad in ({"mirrors_auto": 2}, {"firmware_auto": 2}, {"kits_every_hours": -1}):
    try:
        L.set_policy(**bad); check(f"  {bad} refused", False)
    except L.LibrarianError:
        check(f"  {bad} refused", True)
srv = (REPO / "irate_box/hub/server.py").read_text()
check("the hub passes every policy key the librarian knows", "for k in librarian.DEFAULT_POLICY" in srv)
from irate_box.library import toolkits  # noqa: E402
queued = []
L._queue_root = lambda req: queued.append(req) or "r1"
toolkits.settings = lambda: {"kits": {k: {"keep_current": True} for k in toolkits.definitions()}, "budget_mb": 100, "deep_audit_days": 0}
toolkits.step({"check_every_hours": 24, "kits_every_hours": 0}, now=time.time() + 400 * 86400)
check("the toolkits' cache by hand only: nothing fetched by the round", not [q for q in queued if q["action"] == "kit-fetch"], queued)
toolkits.step({"check_every_hours": 0, "kits_every_hours": 6}, now=time.time() + 400 * 86400)
check("  every 6 hours: a kit due is fetched, whatever apps and books do", [q for q in queued if q["action"] == "kit-fetch"], queued)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
