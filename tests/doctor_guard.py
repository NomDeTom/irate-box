# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's steps: unique ids, and no step function defined twice (the second
silently replaces the first, and its findings vanish from the report: it happened, 2026-10-06).
python3 tests/doctor_guard.py"""
import ast, sys
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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
