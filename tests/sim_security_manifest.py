# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security check of a fetched update (hub_control._security_check): a tree that matches its own
manifest and widens nothing passes; a manifest that disagrees with its code is held; a new root action
is held and named; a new outbound host warns and installs; a changed root file warns. The fetched tree
is read, never imported. python3 tests/sim_security_manifest.py"""

import json
import os
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

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
