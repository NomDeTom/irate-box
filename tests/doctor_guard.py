# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's steps: unique ids, and no step function defined twice (the second
silently replaces the first, and its findings vanish from the report: it happened, 2026-10-06).
python3 tests/doctor_guard.py"""
import ast
import re, sys
from collections import Counter
from pathlib import Path
src = (Path(__file__).resolve().parents[1] / "irate_box" / "root" / "secdoctor.py").read_text()
tree = ast.parse(src)
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
names = Counter(n.name for n in tree.body if isinstance(n, ast.FunctionDef))
check("no function defined twice", all(c == 1 for c in names.values()), [n for n, c in names.items() if c > 1])
steps = next(n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "STEPS" for t in n.targets))
ids = Counter(e.elts[0].value for e in steps.value.elts)
check("no step id used twice", all(c == 1 for c in ids.values()), [i for i, c in ids.items() if c > 1])
fns = [e.elts[3].id for e in steps.value.elts]
check("every step names a function of its own", len(fns) == len(set(fns)), [f for f, c in Counter(fns).items() if c > 1])
# F2's check reads a marker in the hub's code: the hub must still carry it.
server = (Path(__file__).resolve().parents[1] / "irate_box" / "hub" / "server.py").read_text()
check("server.py declares DRAINS_REQUEST_BODIES = True (the doctor's F2 check reads it)",
      re.search(r"(?m)^DRAINS_REQUEST_BODIES\s*=\s*True\b", server) is not None)
# The doctor runs daily from a timer (stance review 2026-10-08 §2), and says what is new since the last run.
inst = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
uninst = (Path(__file__).resolve().parents[1] / "uninstall.sh").read_text()
check("install.sh writes and enables irate-box-secdoctor.timer, running `secdoctor run` as a hardened oneshot",
      "irate-box-secdoctor.timer" in inst and "ExecStart=$CODE/irate-box secdoctor run" in inst
      and "irate-box-secdoctor.timer irate-box-control.path" in inst and "ProtectSystem=strict\nReadWritePaths=$STATE/control" in inst)
check("uninstall.sh takes the timer and its unit away", "irate-box-secdoctor.timer irate-box-secdoctor.service" in uninst and 'irate-box-secdoctor.{service,timer}' in uninst)
check("secdoctor.py has the `run` command the timer calls", '"run"' in src and "write_report(rep)" in src)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.root import secdoctor as sd  # noqa: E402
prev = {"at": 1, "steps": [{"findings": [{"id": "a", "status": "ok"}, {"id": "b", "status": "warn"}]}]}
now = [{"id": "a", "status": "problem"}, {"id": "b", "status": "problem"}, {"id": "c", "status": "warn"}, {"id": "d", "status": "ok"}]
check("new_since: what was fine or absent and is not now; not what was already wrong, nor what is fine",
      sd.new_since(prev, now) == ["a", "c"] and sd.new_since(None, now) == [], sd.new_since(prev, now))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
