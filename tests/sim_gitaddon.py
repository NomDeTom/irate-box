# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Removing the Git add-on (root/gitaddon.py), against a throwaway box: its units and cgit's files go, the
marker is left, the repositories stay, Git access is set to off, --purge removes the packages; the storage
still works afterwards. With it removed: the hotspot firewall closes cgit's ports, the root helper will not turn
Git's access on, and the Box doctor neither expects its socket nor misses a removal cut short. Adding it back
(install.sh --with-git's half) takes the marker away and turns its access on. python3 tests/sim_gitaddon.py"""

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="gitaddon-"))
# The repositories in the state folder, as install.sh puts them (the Box doctor finds the marker there).
os.environ.update(
    HUB_GIT_ROOT=str(T / "state" / "git"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_UNIT_DIR=str(T / "units"),
)
for d in ("state/git/public", "state/git/private", "units", "etc"):
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
    (T / "state" / "git" / f"cgitrc-{a}").write_text("x\n")
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
check("the repository is still there", (T / "state" / "git" / "private" / "keep.git" / "HEAD").exists())
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
check(
    "a bad command line is refused",
    gitaddon.main(["remove", "--everything"]) == 2
    and gitaddon.main([]) == 2
    and gitaddon.main(["add-back", "--yes"]) == 2,
)

# What reads the marker, with Git removed.
from irate_box.root import firewall, health, hub_control  # noqa: E402
from irate_box.msg import Refused  # noqa: E402

tcp, _ = firewall.ports((), "apps")
check(
    "the hotspot firewall closes cgit's port and its TLS twin", 8093 not in tcp and 8493 not in tcp and 8092 in tcp, tcp
)
try:
    hub_control.access_set({"app": "git", "mode": "public"})
    refused = None
except Refused as exc:
    refused = exc.message["id"]
check(
    "the root helper will not turn Git's access on (its routes would have no servers)",
    refused == "control.access.git-removed",
    refused,
)
health.UNIT_DIR = T / "units"
check(
    "the Box doctor does not expect the git servers' socket",
    not any(u == "irate-box-git.socket" for u, _ in health.expected_units()),
)
f = health.check_git_addon()
check("  and says Git is removed, all of it", [x["status"] for x in f] == ["ok"], f)
(T / "units" / "irate-box-git.socket").write_text("[Unit]\n")
f = health.check_git_addon()
check(
    "  a removal cut short (a unit left): a warning, with the command that finishes it",
    f[0]["status"] == "warn"
    and f[0]["commands"][0].endswith("gitaddon remove --yes")
    and any(e.endswith("irate-box-git.socket") for e in f[0]["evidence"]),
    f,
)
(T / "units" / "irate-box-git.socket").unlink()

# Adding it back: the marker goes, and Git's access, which the removal turned off, is on again.
(T / "access.json").write_text('{"git": "off"}')
asked.clear()
fake_back = types.SimpleNamespace(access_set=lambda req: asked.append(req), ACCESS_FILE=T / "access.json")
with (
    mock.patch.dict(sys.modules, {"irate_box.root.hub_control": fake_back}),
    mock.patch.object(sys.modules["irate_box.root"], "hub_control", fake_back, create=True),
):
    gitaddon.add_back(say=lambda _: None)
check("adding Git back takes the marker away", gitrepos.served() and not gitrepos.REMOVED.exists())
check("  and turns its access on again (public, its default)", asked == [{"app": "git", "mode": "public"}], asked)
check("  the Box doctor expects its socket again", any(u == "irate-box-git.socket" for u, _ in health.expected_units()))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
