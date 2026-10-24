# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor on a box without a program a check runs: never a crash or a bare "check failed". A program
its own runner does not find is said once, with the checks it held back (tools-<name>); a step that stops because
another module's program is missing says which (<step>-missing). Both are warnings the owner can accept. A file that
is not there is still a failed check, not a missing program. The Security page's scan and the deep audit, which
the doctor reads, the same: no ss is "cannot list", not "nothing listens". python3 tests/sim_secdoctor_missing.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-missing-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "state").mkdir(parents=True)
(T / "etc").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.msg import render
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


NOPE = "irate-box-no-such-program"

# The doctor's runner: None for a program that is not installed, noted with the step that wanted it.
sd.MISSING.clear()
sd._STEP[0] = "front"
check("the runner: None for a program not installed", sd._cmd(NOPE, "--version") is None)
check("  noted with the step that wanted it", sd.MISSING.get(NOPE) == {"front"}, sd.MISSING)
check("  a program that is there still answers", sd._cmd(sys.executable, "-c", "print(1)").stdout.strip() == "1")

# A step whose program, run by another module, is not installed.
got = sd.run_step("kits-trust", lambda ctx: subprocess.run([NOPE], capture_output=True), {})
f = got[0] if len(got) == 1 else {}
check("a step stopped by a missing program: one finding, <step>-missing", f.get("id") == "kits-trust-missing", got)
check("  a warning, naming the program", f.get("status") == "warn" and NOPE in render(f["summary"]), f)
check("  its fix: install it, or accept", "Accept risk" in render(f["fix"]), f)
check("  not named again under the tools step", NOPE not in sd.MISSING or "kits-trust" not in sd.MISSING[NOPE])


# A file that is not there is a failed check, not a missing program.
def reads_nothing(ctx):
    return Path(T / "not-there.conf").read_text()


got = sd.run_step("secrets", reads_nothing, {})
check("a missing file: <step>-error, not -missing", [x["id"] for x in got] == ["secrets-error"], got)

# The tools step: one finding a program, with the checks it held back; ok when nothing is missing.
sd.MISSING.clear()
sd.MISSING.update({NOPE: {"front", "kernel"}, "ss": {None}, "journalctl": set()})
out = sd.step_tools({})
ids = sorted(x["id"] for x in out)
check(
    "the tools step: one finding a program that held a check back, or that the doctor looked for before its steps",
    ids == [f"tools-{NOPE}", "tools-ss"],
    ids,
)
ss = next(x for x in out if x["id"] == "tools-ss")
check("  before its steps: said without a list", "ss is not installed" in render(ss["summary"]), ss)
nope = next(x for x in out if x["id"] == f"tools-{NOPE}")
said = render(nope["summary"])
check("  a warning naming the program and its checks", nope["status"] == "warn" and NOPE in said, said)
check("  the checks by their titles", "Web server in front" in said and "Kernel protections" in said, said)
sd.MISSING.clear()
out = sd.step_tools({})
check("nothing missing: one ok finding", [(x["id"], x["status"]) for x in out] == [("tools", "ok")], out)
check("the tools step is the doctor's last", sd.STEPS[-1][0] == "tools")

# The Security page's scan and the deep audit, which the doctor reads, on a box without their programs.
from irate_box.root import deepaudit, security

check("security.run: 127 for a program not installed", security.run(NOPE).returncode == 127)
security._have = lambda cmd: cmd != "ss"
ids = [x["id"] for x in security.listener_part([], {})]
check("no ss: the scan says it cannot list listeners, not that nothing listens", ids == ["listeners-unread"], ids)
deepaudit.REPORT = T / "state" / "deep.json"
deepaudit.PROGRESS = T / "state" / "deep-progress.json"


def no_lynis(work):
    subprocess.run([NOPE, "audit", "system"], capture_output=True)


deepaudit.run_cis = lambda work: []
deepaudit.run_lynis = no_lynis
deepaudit.deep(log=lambda *a: None)
lyn = deepaudit.load()["sources"]["lynis"]["findings"]
check(
    "the deep audit without its program: lynis-missing, naming it",
    [x["id"] for x in lyn] == ["lynis-missing"] and NOPE in render(lyn[0]["summary"]),
    lyn,
)

sys.exit(1 if fails else 0)
