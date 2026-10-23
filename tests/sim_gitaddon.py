# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Removing the Git add-on (root/gitaddon.py), against a throwaway box: its units and cgit's files go, the
marker is left, the repositories stay, Git access is set to off, --purge removes the packages; the storage
still works afterwards. python3 tests/sim_gitaddon.py"""

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="gitaddon-"))
os.environ.update(HUB_GIT_ROOT=str(T / "git"), HUB_STATE_DIR=str(T / "state"), HUB_UNIT_DIR=str(T / "units"))
for d in ("git/public", "git/private", "units", "state"):
    (T / d).mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import gitrepos
from irate_box.root import gitaddon

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


for u in gitaddon.UNITS:
    (T / "units" / u).write_text("[Unit]\n")
for a in ("public", "private"):
    (T / "git" / f"cgitrc-{a}").write_text("x\n")
code, _ = gitrepos.action({"action": "create", "area": "private", "name": "keep"})
check("a repository to keep", code == 200)
check(
    "before: served, units and cgitrc present",
    gitaddon.status() == {"served": True, "units": list(gitaddon.UNITS), "cgitrc": ["public", "private"]},
    gitaddon.status(),
)

calls, asked = [], []
fake = types.SimpleNamespace(access_set=lambda req: asked.append(req))
with (
    mock.patch.object(
        gitaddon, "run", lambda *cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0, stderr="")
    ),
    mock.patch.dict(sys.modules, {"irate_box.root.hub_control": fake}),
    mock.patch.object(sys.modules["irate_box.root"], "hub_control", fake, create=True),
):
    lines = gitaddon.remove(say=lambda _: None)
check("the servers are stopped and disabled", any(c[:3] == ("systemctl", "disable", "--now") for c in calls), calls)
check(
    "after: not served, no units, no cgitrc",
    gitaddon.status() == {"served": False, "units": [], "cgitrc": []},
    gitaddon.status(),
)
check("Git access set to off", asked == [{"app": "git", "mode": "off"}], asked)
check("no package removed without --purge", not any(c[0] == "apt-get" for c in calls), calls)
check("the repository is still there", (T / "git" / "private" / "keep.git" / "HEAD").exists())
check(
    "the storage still works: a repository can be made",
    gitrepos.action({"action": "create", "area": "public", "name": "more"})[0] == 200,
)
check("the snapshot says it is not served", gitrepos.snapshot()["served"] is False)

with (
    mock.patch.object(
        gitaddon, "run", lambda *cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=0, stderr="")
    ),
    mock.patch.dict(sys.modules, {"irate_box.root.hub_control": fake}),
    mock.patch.object(sys.modules["irate_box.root"], "hub_control", fake, create=True),
):
    gitaddon.remove(purge=True, say=lambda _: None)
check(
    "--purge removes the packages",
    any(c[:3] == ("apt-get", "remove", "-y") and "cgit" in c and "fcgiwrap" in c for c in calls),
    calls,
)
check("the git program is not one of them", not any(c[0] == "apt-get" and "git" in c for c in calls))
check("a bad command line is refused", gitaddon.main(["remove", "--everything"]) == 2 and gitaddon.main([]) == 2)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
