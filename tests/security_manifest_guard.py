# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security manifest (irate_box/root/secmanifest.py, security.json) stays the code's own word:
the guard passes on this tree, and fails when the code gains an action, a wider secret mode, a
dropped sandbox line or an undeclared host; the comparison sorts real differences into widenings.
python3 tests/security_manifest_guard.py"""

import copy
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import secmanifest

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


declared = secmanifest.load(REPO)
code = secmanifest.build(REPO, generated=None)
check("the tree has a security manifest", declared is not None)
check(
    "  and the code matches it",
    secmanifest.mismatches(declared, code) == [],
    secmanifest.mismatches(declared, code)[:4],
)
check(
    "  the manifest declares root's actions and the units",
    len(declared["root_actions"]) > 50 and len(declared["units"]) > 20,
    (len(declared["root_actions"]), len(declared["units"])),
)
check(
    "  the secrets, the web servers' surfaces and the outbound hosts",
    declared["secrets"] and declared["served"]["nginx"]["locations"] and declared["outbound"],
    {k: len(v) if hasattr(v, "__len__") else v for k, v in declared.items()},
)


def with_change(change):
    manifest = copy.deepcopy(code)
    change(manifest)
    return secmanifest.mismatches(declared, manifest)


def add_action(manifest):
    manifest["root_actions"]["do-anything"] = {"https_only": False, "policy": False, "reads": [], "writes": []}


def widen_secret(manifest):
    manifest["secrets"]["/etc/hub/admin-password"]["mode"] = "644"


def drop_sandbox(manifest):
    manifest["units"]["irate-box.service"].pop("ProtectSystem", None)


def add_host(manifest):
    manifest["outbound"]["evil.example"] = ""


for name, change, said in (
    ("an action added", add_action, "do-anything"),
    ("a secret widened", widen_secret, "admin-password"),
    ("a sandbox line dropped", drop_sandbox, "irate-box.service"),
    ("a host added", add_host, "evil.example"),
):
    problems = with_change(change)
    check(f"the guard fails on {name}", bool(problems) and any(said in p for p in problems), problems)


def tree_with_install(edit):
    root = Path(tempfile.mkdtemp(prefix="secmanifest-"))
    (root / "irate_box").symlink_to(REPO / "irate_box")
    (root / "config").symlink_to(REPO / "config")
    text = (REPO / "install.sh").read_text()
    (root / "install.sh").write_text(edit(text))
    return root


tree = tree_with_install(lambda text: text + '\nchmod 644 "$ETC/admin-password"\n')
problems = secmanifest.mismatches(declared, secmanifest.build(tree))
check("the guard reads install.sh: a wider secret mode fails", any("admin-password" in p for p in problems), problems)
shutil.rmtree(tree, ignore_errors=True)

tree = tree_with_install(lambda text: text.replace("ProtectSystem=strict\n", "", 1))
problems = secmanifest.mismatches(declared, secmanifest.build(tree))
check("  a dropped sandbox line fails", any("ProtectSystem" in p or "directives" in p for p in problems), problems)
shutil.rmtree(tree, ignore_errors=True)

tree = tree_with_install(lambda text: text + "\n# https://evil.example/update\n")
problems = secmanifest.mismatches(declared, secmanifest.build(tree))
check("  an undeclared host fails", any("evil.example" in p for p in problems), problems)
shutil.rmtree(tree, ignore_errors=True)

tree = tree_with_install(lambda text: text + '\nusermod -aG video "$HUB_USER"\n')
problems = secmanifest.mismatches(declared, secmanifest.build(tree))
check("  a group added to the hub user fails", any("video" in p for p in problems), problems)
shutil.rmtree(tree, ignore_errors=True)


old = secmanifest.build(REPO, generated=None)
new = copy.deepcopy(old)
new["root_actions"]["update-install"]["https_only"] = False
new["secrets"]["/etc/hub/admin-password"]["mode"] = "644"
new["units"]["irate-box.service"].pop("ProtectSystem", None)
new["outbound"]["evil.example"] = ""
changes = secmanifest.compare(old, new, ["irate_box/root/hub_control.py"])
toggles = {item["toggle"] for item in changes["widenings"]}
check(
    "the comparison sorts a new capability, a wider secret, a lost sandbox and a new host as widenings",
    {"root_actions", "secrets", "sandboxes", "outbound", "root_code"} <= toggles,
    toggles,
)
check(
    "  and holds the ones that are not only flagged",
    {item["toggle"] for item in secmanifest.held(changes["widenings"], {"outbound": "flag", "root_code": "flag"})}
    == {"root_actions", "secrets", "sandboxes"},
    changes["widenings"],
)
check(
    "  a narrowing is no widening",
    secmanifest.compare(old, new, [])["narrowings"] == [] and secmanifest.compare(new, old, [])["widenings"] == [],
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
