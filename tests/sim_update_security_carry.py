# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""An update's security approval outlives a newer fetch with the same changes
(hub_control.security_report, update_fetch): approve a widening at one commit, fetch a later
commit with that same widening and it is still approved, said as carried; one with an extra
widening is held for that one alone; after an install the old approval no longer counts.
python3 tests/sim_update_security_carry.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="update-carry-"))
for d in ("state/control/results", "state/control/requests", "etc", "code", "cache"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_RUN_DIR=str(T / "run"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_UPDATE_DIR=str(T / "cache" / "src"),
    HUB_DOWNLOAD_CACHE=str(T / "cache" / "downloads"),
    HUB_WEB_SERVER="nginx",
)
sys.path.insert(0, str(REPO))
from irate_box import msg
from irate_box.root import hub_control as H
from irate_box.root import secmanifest

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def code_tree(*actions):
    """A fetched tree that matches its own security manifest, with each named root action added."""
    root = Path(tempfile.mkdtemp(prefix="carry-tree-"))
    (root / "config").symlink_to(REPO / "config")
    (root / "install.sh").write_text((REPO / "install.sh").read_text())
    (root / "irate_box").mkdir()
    (root / "irate_box" / "__init__.py").symlink_to(REPO / "irate_box" / "__init__.py")
    (root / "irate_box" / "hub").symlink_to(REPO / "irate_box" / "hub")
    (root / "irate_box" / "root").mkdir()
    for path in (REPO / "irate_box" / "root").iterdir():
        if path.name in ("__pycache__", "hub_control.py"):
            continue
        (root / "irate_box" / "root" / path.name).symlink_to(path)
    text = (REPO / "irate_box" / "root" / "hub_control.py").read_text()
    for action in actions:
        text = text.replace('"pins": pins,', f'"pins": pins,\n    "{action}": pins,', 1)
    (root / "irate_box" / "root" / "hub_control.py").write_text(text)
    secmanifest.save(root, secmanifest.build(root))
    return root


def said(check):
    return msg.render(check["detail"])


# The installed code, with its manifest and the checker that reads a fetched tree.
(T / "code" / "security.json").write_text((REPO / "security.json").read_text())
(T / "code" / "irate_box" / "root").mkdir(parents=True)
(T / "code" / "irate_box" / "root" / "secmanifest.py").write_text(
    (REPO / "irate_box" / "root" / "secmanifest.py").read_text()
)

installed = "b" * 40
tree_a = code_tree("evil")
report_a = H.security_report(tree_a, installed, sha="a" * 40)
check(
    "a new root action is held at first",
    [item["text"] for item in report_a["held"]] == ["a new root action, evil"] and not report_a["approved"],
    report_a,
)
H.secchanges.approve(
    "a" * 40,
    "aaaaaaa",
    [item["text"] for item in report_a["held"]],
    "https",
    "an administrator over HTTPS",
    report_a["levels"],
    items=report_a["held"],
    installed=installed,
)

tree_b = code_tree("evil")
report_b = H.security_report(tree_b, installed, sha="c" * 40)
check(
    "a later fetch with the same change carries the approval",
    report_b["approved"]
    and not report_b["held"]
    and [item["text"] for item in report_b["covered"]] == ["a new root action, evil"],
    report_b,
)
check(
    "  and says which release it was approved for",
    (report_b.get("approval") or {}).get("commit") == "aaaaaaa",
    report_b.get("approval"),
)
check(
    "  the security check says so, and names the same change",
    H._security_check(tree_b, installed)["detail"]["id"] == "update.verify.security.carried"
    and "approved for aaaaaaa. the same change" in said(H._security_check(tree_b, installed)).lower(),
    H._security_check(tree_b, installed),
)
check(
    "  but an install by another route no longer carries it",
    H.security_report(tree_b, "e" * 40, sha="c" * 40)["approved"] is False
    and [item["text"] for item in H.security_report(tree_b, "e" * 40, sha="c" * 40)["held"]]
    == ["a new root action, evil"],
    H.security_report(tree_b, "e" * 40, sha="c" * 40),
)

tree_c = code_tree("evil", "evil2")
report_c = H.security_report(tree_c, installed, sha="d" * 40)
check(
    "a later fetch with one more change is held for that one alone",
    [item["text"] for item in report_c["held"]] == ["a new root action, evil2"]
    and [item["text"] for item in report_c["covered"]] == ["a new root action, evil"]
    and not report_c["approved"],
    report_c,
)
said_c = H._security_check(tree_c, installed)
check(
    "  the held check names only the new one, and its note the approved one",
    "evil2" in said(said_c) and "evil," not in said(said_c) and "approved for aaaaaaa" in msg.render(said_c["note"]),
    said_c,
)

# Fetching the newer commit with the same change: the approval is kept for what was downloaded.
src = T / "cache" / "src"
shutil.copytree(tree_b, src, symlinks=True)
subprocess.run(["git", "init", "-q"], cwd=src, check=True)
subprocess.run(["git", "add", "-A"], cwd=src, check=True)
subprocess.run(
    ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "an update"],
    cwd=src,
    check=True,
)
full = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
H._write_update_state(
    {
        "checked_sha": None,
        "available": full[:7],
        "available_sha": full,
        "installed": installed,
        "up_to_date": False,
        "changes": [],
        "changes_known": True,
        "checks": [],
    }
)
carried_check = H._security_check(src, installed)
with (
    mock.patch.object(H, "verify_update", lambda *args, **kw: [carried_check]),
    mock.patch.object(H, "_install_options", lambda: []),
):
    result = H.update_fetch({})
state = json.loads(H.UPDATE_STATE.read_text())
check(
    "fetching it keeps the approval for the new commit, and says it was carried",
    result["id"] == "update.fetch.approved"
    and state["security_approved_sha"] == full
    and state["security_approved_commit"] == "aaaaaaa",
    state,
)
check(
    "  the page's Install is allowed, and the doctor offers neither approval nor Install anyway",
    H._security_approved(state)
    and H._security_approval(state, full) is not None
    and H._approve_actions(state) == []
    and H._force_actions(state) == [],
    (H._approve_actions(state), H._force_actions(state)),
)

H.secchanges.record_install(
    "ccccccc",
    None,
    "github",
    report_b["levels"],
    report_b["changes"],
    "https",
    False,
    (),
)
report_after = H.security_report(tree_b, installed, sha="c" * 40)
check(
    "after an install the old approval no longer counts",
    not report_after["approved"] and [item["text"] for item in report_after["held"]] == ["a new root action, evil"],
    report_after,
)

sys.exit(1 if fails else 0)
