# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A pending app install's root result is folded back into the librarian's status: the entry stops
waiting, keeps what happened, and its current is the build on the disk. The status entry is
lyra-tft's real one after the quick installer installed SVG-Edit (#740), 2026-11-07T02:30Z.
python3 tests/sim_install_pending.py"""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="install-pending-"))
os.environ["HUB_STATE_DIR"] = str(T)
os.environ["HUB_ETC_DIR"] = str(T)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import librarian as L

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


# /var/lib/hub/library/status.json on lyra-tft (192.168.1.197), svg, 2026-11-07T02:30Z: the root
# helper had answered 40c1e23e81878540, but nothing folded it in, so current said not installed.
REAL = {
    "last_check": "2026-11-07T02:29:42Z",
    "latest": {
        "version": "run-42276959141/13532309697",
        "label": "run 42276959141 (master, 2026-11-06) via nightly.link",
        "size": 811708,
    },
    "current": {"version": None, "label": "not installed"},
    "pending": "40c1e23e81878540",
    "last_ok": "2026-11-07T02:29:45Z",
    "outcome": "installing c44f061 (run 42276959141 (master, 2026-11-06) via nightly.link)",
}
BUNDLE = {
    "app": "svg",
    "repository": "NomDeTom/svgedit",
    "ref": "pinned",
    "commit": "c44f061d2f9a626d2771cc931298af5a45522d87",
    "built": "2026-11-06T19:08:29Z",
    "run": 42276959141,
}
RESULT = {
    "id": "40c1e23e81878540",
    "ok": True,
    "message": "The app svg is installed: version c44f061 from pinned, built on 2026-11-06.",
    "at": 1794018589.2108178,
}

# What installed_app reads from the unpacked folder's irate-box-bundle.json.
L.installed_app = lambda name: dict(BUNDLE) if name == "svg" else None
check("the svg app is one the librarian knows", "svg" in L.APPS, list(L.APPS))

L.save_status({"svg": copy.deepcopy(REAL)})
status = L.settle_pending({RESULT["id"]: RESULT})
svg = status["svg"]
check("the pending install stops waiting", "pending" not in svg, svg)
check("its result is kept on the entry", svg.get("install_result") == RESULT, svg.get("install_result"))
check(
    "current is the build now on the disk",
    (svg.get("current") or {}).get("version") == "run-42276959141",
    svg.get("current"),
)
check(
    "the current label names the installed commit",
    "c44f061" in ((svg.get("current") or {}).get("label") or ""),
    svg.get("current"),
)
check("the outcome says it installed", "installed" in (svg.get("outcome") or ""), svg.get("outcome"))
check("the fold is saved, not only returned", "pending" not in L.load_status()["svg"], L.load_status()["svg"])

L.save_status({"svg": copy.deepcopy(REAL)})
status = L.settle_pending({})
check(
    "a result not written yet leaves the entry waiting",
    status["svg"].get("pending") == REAL["pending"] and "install_result" not in status["svg"],
    status["svg"],
)

L.save_status({"svg": copy.deepcopy(REAL)})
FAILED = {**RESULT, "ok": False, "message": "The bundle is not a svg app."}
svg = L.settle_pending({RESULT["id"]: FAILED})["svg"]
check(
    "a failed install stops waiting and says so",
    "pending" not in svg and svg.get("install_result") == FAILED and svg.get("outcome", "").startswith("error:"),
    svg,
)

L.save_status({"svg": copy.deepcopy(REAL)})
snap = L.snapshot({RESULT["id"]: RESULT})
check(
    "the snapshot's status is the folded one",
    snap["status"]["svg"].get("pending") is None and snap["status"]["svg"].get("install_result") == RESULT,
    snap["status"]["svg"],
)

# The hub looks a pending id up by its own file, not only among the newest results: an install
# whose answer is older than that window is still folded in rather than waiting for ever.
from irate_box.hub import server

server.CONTROL_RESULTS.mkdir(parents=True, exist_ok=True)
(server.CONTROL_RESULTS / f"{RESULT['id']}.json").write_text(json.dumps(RESULT))
check("a result is found by its id, beyond the newest window", server.control_result(RESULT["id"]) == RESULT)
check("an id that is not a result file's name is refused", server.control_result("../status") is None)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
