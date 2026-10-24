# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Checks an add-on's manifest declares ("checks", manifests.py), run by the Security doctor in a process of their
own (declared.py, secdoctor.run_declared): a file's owner and mode, a config file's live lines. Each that fails is a
point of its own, with the manifest's words; one that cannot read is a warning, never a pass; a pattern that
backtracks for ever is stopped after the time limit and said once. A manifest whose checks are not valid is refused
at load, and the doctor runs none of them. python3 tests/sim_declared_checks.py"""

import grp
import os
import pwd
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="declared-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "state").mkdir(parents=True)
(T / "etc").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests
from irate_box.msg import render
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


ME = pwd.getpwuid(os.getuid()).pw_name
GROUP = grp.getgrgid(os.getgid()).gr_name
conf = T / "irc.conf"
conf.write_text(
    "[Global]\n"
    "; [Operator]  (an operator left as a comment)\n"
    "MaxConnections = 64\n"
    "MaxConnectionsIP = 0\n"
    "DNS = No\n"
    "# CloakHost = %x.box\n"
)
os.chmod(conf, 0o640)


def c(cid, kind, **kw):
    return {"id": cid, "kind": kind, "status": kw.pop("status", "warn"), "title": cid, "says": f"{cid} says.", **kw}


CHECKS = [
    c("owner-ok", "file", path=str(conf), owner=ME, group=GROUP, max_mode="0640"),
    c("too-open", "file", path=str(conf), max_mode="0600", status="problem"),
    c("not-there", "file", path=str(T / "absent"), exists=False),
    c("should-be-there", "file", path=str(T / "absent")),
    c("operator", "config", path=str(conf), comments=[";", "#"], absent=r"^\s*\[Operator\]"),
    c("max-conn", "config", path=str(conf), comments=[";"], value={"key": r"^\s*MaxConnections\s*=\s*(\d+)", "min": 1}),
    c("max-conn-ip", "config", path=str(conf), value={"key": r"^\s*MaxConnectionsIP\s*=\s*(\d+)", "min": 1}),
    c("cloak", "config", path=str(conf), comments=["#"], present=r"^\s*CloakHost\s*=\s*\S", why="Why.", fix="Fix."),
    c("dns", "config", path=str(conf), ignore_case=True, value={"key": r"^\s*DNS\s*=\s*(\S+)", "equals": "no"}),
    c("unreadable", "config", path=str(T / "absent.conf"), present="x"),
]

# Valid as a manifest, beside a status with a unit.
m = {"id": "made-up", "order": 1, "status": {"name": "x", "port": 1, "unit": "made-up.service"}, "checks": CHECKS}
try:
    manifests._check(m, "test")
    valid = True
except manifests.ManifestError as exc:
    valid = str(exc)
check("a manifest with checks of each kind is valid", valid is True, valid)

got = {i["key"]: i for i in sd.run_declared({"id": "made-up", "checks": CHECKS})}
check(
    "the failing checks, and only those, are points",
    sorted(got) == ["cloak", "max-conn-ip", "should-be-there", "too-open", "unreadable"],
    sorted(got),
)
check("  a point with the check's status", got["too-open"]["status"] == "problem", got["too-open"])
check("  its words from the manifest", render(got["cloak"]["summary"]) == "cloak says.", got["cloak"])
check(
    "  why and fix too, when the check gives them",
    render(got["cloak"]["why"]) == "Why." and render(got["cloak"]["fix"]) == "Fix.",
)
check("  the file and what was found as evidence", got["too-open"]["evidence"] == [str(conf), "mode 0640"], got)
check(
    "a commented-out line does not count: no operator, no cloak",
    "operator" not in got and "cloak" in got,
)
check("a value below its minimum fails; one at it passes", "max-conn-ip" in got and "max-conn" not in got)
check("equals, ignoring case when asked", "dns" not in got)
check(
    "a file it cannot read: a warning, not a pass",
    got["unreadable"]["status"] == "warn" and "could not read" in render(got["unreadable"]["summary"]),
    got["unreadable"],
)

# A pattern that backtracks for ever: stopped, said once, and the doctor goes on.
slow = [c("slow", "config", path=str(T / "slow.conf"), present=r"^(a+)+$")]
(T / "slow.conf").write_text("a" * 40 + "b\n")
got = sd.run_declared({"id": "made-up", "checks": slow}, timeout=1)
check(
    "a pattern that runs too long: one warning, results said missing",
    [(i["key"], i["status"]) for i in got] == [("checks", "warn")] and "too long" in render(got[0]["summary"]),
    got,
)


# Refused at load.
def refused(bad):
    try:
        manifests._check({**m, "checks": bad}, "test")
        return False
    except manifests.ManifestError:
        return True


check("refused: a relative path", refused([c("x", "file", path="etc/x")]))
check("refused: a path through ..", refused([c("x", "file", path="/etc/../root/x")]))
check("refused: an unknown kind", refused([c("x", "exec", path="/bin/sh")]))
check("refused: a check id used twice", refused([c("x", "file", path="/a"), c("x", "file", path="/b")]))
check("refused: a pattern that does not compile", refused([c("x", "config", path="/a", present="(")]))
check("refused: a pattern over 200 characters", refused([c("x", "config", path="/a", present="a" * 201)]))
check(
    "refused: a value key without one group",
    refused([c("x", "config", path="/a", value={"key": "a", "min": 1})]),
)
check("refused: both present and absent", refused([c("x", "config", path="/a", present="a", absent="b")]))
check("refused: more than 20 checks", refused([c(f"x{n}", "file", path="/a") for n in range(21)]))
try:
    manifests._check({"id": "y", "order": 1, "checks": CHECKS}, "test")
    no_unit = False
except manifests.ManifestError:
    no_unit = True
check("refused: checks on an add-on with no status.unit", no_unit)

# The doctor checks validity itself: it reads the manifests' JSON, not through the hub's loader.
got = sd.run_declared({"id": "made-up", "checks": [c("x", "file", path="relative")]})
check(
    "invalid checks given to the doctor: one warning, none run",
    [(i["key"], i["status"]) for i in got] == [("checks", "warn")] and "not valid" in render(got[0]["summary"]),
    got,
)

sys.exit(1 if fails else 0)
