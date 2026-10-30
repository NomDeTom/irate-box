# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security check of a fetched update (hub_control._security_check): a tree that matches its own
manifest and widens nothing passes; a manifest that disagrees with its code is held; a new root action
is held and named; a new outbound host warns and installs; a changed root file warns. The fetched tree
is read, never imported. python3 tests/sim_security_manifest.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secmanifest-sim-"))
for d in ("state/control/results", "etc", "code", "cache"):
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
from irate_box.root import hub_control as H
from irate_box.root import secmanifest

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def code_tree(install_edit=None, control_edit=None):
    root = Path(tempfile.mkdtemp(prefix="secmanifest-tree-"))
    (root / "config").symlink_to(REPO / "config")
    (root / "install.sh").write_text(
        install_edit((REPO / "install.sh").read_text()) if install_edit else (REPO / "install.sh").read_text()
    )
    (root / "irate_box").mkdir()
    (root / "irate_box" / "__init__.py").symlink_to(REPO / "irate_box" / "__init__.py")
    (root / "irate_box" / "hub").symlink_to(REPO / "irate_box" / "hub")
    (root / "irate_box" / "root").mkdir()
    for path in (REPO / "irate_box" / "root").iterdir():
        if path.name in ("__pycache__", "hub_control.py"):
            continue
        (root / "irate_box" / "root" / path.name).symlink_to(path)
    text = (REPO / "irate_box" / "root" / "hub_control.py").read_text()
    (root / "irate_box" / "root" / "hub_control.py").write_text(control_edit(text) if control_edit else text)
    return root


def written(tree):
    manifest = secmanifest.build(tree)
    secmanifest.save(tree, manifest)
    return manifest


def with_install(tree):
    (T / "code" / "security.json").write_text((REPO / "security.json").read_text())
    return tree


said = H._security_check(with_install(REPO), None)
check("a tree that matches its manifest and widens nothing: passed", said["ok"], said)
check("  said as such", said["detail"]["id"] == "update.verify.security.ok", said["detail"])

tree = code_tree(control_edit=lambda text: text.replace('"pins": pins,', '"pins": pins,\n    "evil": pins,', 1))
written(tree)
said = H._security_check(with_install(tree), None)
check(
    "a new root action is held, and named",
    not said["ok"] and not said["warn"] and said["detail"]["id"] == "update.verify.security.widens",
    said,
)
check("  in its evidence and its fix", any("evil" in e for e in said["evidence"]) and said["fix"], said)

tree = code_tree()
written(tree)
broken = json.loads((tree / "security.json").read_text())
broken["root_actions"].pop("pins")
(tree / "security.json").write_text(json.dumps(broken))
said = H._security_check(with_install(tree), None)
check(
    "a manifest that does not match its own code: held",
    not said["ok"] and said["detail"]["id"] == "update.verify.security.mismatch",
    said,
)

tree = code_tree(install_edit=lambda text: text + "\n# https://evil.example/update\n")
written(tree)
said = H._security_check(with_install(tree), None)
check(
    "a new outbound host: a warning that installs",
    not said["ok"] and said["warn"] and said["detail"]["id"] == "update.verify.security.flagged",
    said,
)

tree = code_tree()
written(tree)
with_install(tree)
with mock.patch.object(H, "_changed_files", lambda src, installed: ["install.sh"]):
    said = H._security_check(tree, "abc1234")
check(
    "a privileged file changed with the manifest unchanged: a warning",
    not said["ok"] and said["warn"] and said["detail"]["id"] == "update.verify.security.flagged",
    said,
)
check("  naming the file", any("install.sh" in e for e in said["evidence"]), said["evidence"])

check(
    "the fetched code was not imported: the tree has no __pycache__ under its copied root",
    not (tree / "irate_box" / "root" / "__pycache__").exists(),
)

with_install(REPO)
update_src = Path(os.environ["HUB_UPDATE_DIR"])
shutil.rmtree(update_src, ignore_errors=True)
tree = code_tree(control_edit=lambda text: text.replace('"pins": pins,', '"pins": pins,\n    "evil": pins,', 1))
written(tree)
shutil.copytree(tree, update_src)
subprocess.run(["git", "init", "-q"], cwd=update_src, check=True)
subprocess.run(["git", "add", "-A"], cwd=update_src, check=True)
subprocess.run(
    ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "an update"],
    cwd=update_src,
    check=True,
)
full = subprocess.run(
    ["git", "-C", str(update_src), "rev-parse", "HEAD"], capture_output=True, text=True
).stdout.strip()
check_now = H._security_check(update_src, None)
H._write_update_state(
    {
        "checked_sha": full,
        "available": full[:7],
        "available_sha": full,
        "installed": None,
        "branch": "main",
        "verified": None,
        "verified_sha": None,
        "checks": [check_now],
        "security": H.security_report(update_src, None),
        "changes": [],
        "changes_known": True,
    }
)
report = {
    "manifest": True,
    "problems": [],
    "held": [{"toggle": "root_actions", "text": "a new root action, evil"}],
    "levels": {"root_actions": "authorise"},
}
check(
    "the doctor offers an approval for a held widening",
    [a["choice"] for a in H._approve_actions({"security": report})] == ["approve-security"],
    H._approve_actions({"security": report}),
)
check(
    "  but not when the level needs the console",
    H._approve_actions({"security": {**report, "levels": {"root_actions": "console"}}}) == [],
)
said = H.update_approve_security({})
check(
    "an administrator approves the held widenings over HTTPS",
    said["id"] == "update.security.approved" and "evil" in " ".join(said["args"]["changes"]),
    said,
)
(T / "etc/install-options").write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")
with (
    mock.patch.object(H, "_run_install", lambda *args, **kwargs: 3),
    mock.patch.object(H, "_installed_commit", lambda: "abcdef0"),
):
    said = H.update_install({})
check("  and the approved update installs", said["id"] == "update.install.done", said)

record = H._security_record_finding()
check(
    "the Updates doctor shows the change as not yet reviewed",
    record[0]["status"] == "note" and record[0]["actions"][0]["choice"] == "security-record-read",
    record,
)
said = H.ACTIONS["security-record-read"]({})
check("  marking the record read", said["id"] == "security.record.read", said)
check("  leaves nothing unread", H._security_record_finding()[0]["status"] == "ok", H._security_record_finding())

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
