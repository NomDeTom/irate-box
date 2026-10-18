# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's steps: unique ids, and no step function defined twice (the second
silently replaces the first, and its findings vanish from the report).
python3 tests/doctor_guard.py"""

import ast
import re
import sys
from collections import Counter
from pathlib import Path

src = (Path(__file__).resolve().parents[1] / "irate_box" / "root" / "secdoctor.py").read_text()
tree = ast.parse(src)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


names = Counter(n.name for n in tree.body if isinstance(n, ast.FunctionDef))
check(
    "no function defined twice",
    all(c == 1 for c in names.values()),
    [n for n, c in names.items() if c > 1],
)
steps = next(
    n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "STEPS" for t in n.targets)
)
ids = Counter(e.elts[0].value for e in steps.value.elts)
check(
    "no step id used twice",
    all(c == 1 for c in ids.values()),
    [i for i, c in ids.items() if c > 1],
)
fns = [e.elts[3].id for e in steps.value.elts]
check(
    "every step names a function of its own",
    len(fns) == len(set(fns)),
    [f for f, c in Counter(fns).items() if c > 1],
)
# The doctor's drain check reads a marker in the hub's code: the hub must still carry it.
server = (Path(__file__).resolve().parents[1] / "irate_box" / "hub" / "server.py").read_text()
check(
    "server.py declares DRAINS_REQUEST_BODIES = True (the doctor's drain check reads it)",
    re.search(r"(?m)^DRAINS_REQUEST_BODIES\s*=\s*True\b", server) is not None,
)
# The doctor runs daily from a timer, and says what is new since the last run.
inst = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
uninst = (Path(__file__).resolve().parents[1] / "uninstall.sh").read_text()
check(
    "install.sh writes and enables irate-box-secdoctor.timer, running `secdoctor run` as a hardened oneshot",
    "irate-box-secdoctor.timer" in inst
    and "ExecStart=$CODE/irate-box secdoctor run" in inst
    and "irate-box-secdoctor.timer irate-box-control.path" in inst
    and "ProtectSystem=strict\nReadWritePaths=$STATE/control" in inst,
)
check(
    "install.sh keeps silverbullet.env root's alone (it can hold the notes' sign-in), an existing one too (item 133)",
    re.search(
        r'(?m)^EOF\n\t# .*\n\tchown root:root "\$ETC/silverbullet.env"\n\tchmod 600 "\$ETC/silverbullet.env"$',
        inst,
    )
    is not None,
)
check(
    "uninstall.sh takes the timer and its unit away",
    "irate-box-secdoctor.timer irate-box-secdoctor.service" in uninst
    and "irate-box-secdoctor.{service,timer}" in uninst,
)
check(
    "secdoctor.py has the `run` command the timer calls",
    '"run"' in src and "write_report(rep)" in src,
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.root import secdoctor as sd

prev = {
    "at": 1,
    "steps": [{"findings": [{"id": "a", "status": "ok"}, {"id": "b", "status": "warn"}]}],
}
now = [
    {"id": "a", "status": "problem"},
    {"id": "b", "status": "problem"},
    {"id": "c", "status": "warn"},
    {"id": "d", "status": "ok"},
]
check(
    "new_since: what was fine or absent and is not now; not what was already wrong, nor what is fine",
    sd.new_since(prev, now) == ["a", "c"] and sd.new_since(None, now) == [],
    sd.new_since(prev, now),
)

# The update doctor's links (go, as health.py's): each place it names is an id on the admin page, and only what is not
# fine has one.
hc = (Path(__file__).resolve().parents[1] / "irate_box" / "root" / "hub_control.py").read_text()
html = (Path(__file__).resolve().parents[1] / "web" / "admin.html").read_text()
places = [
    [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    for n in ast.walk(ast.parse(hc))
    if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_go"
]
check("the update doctor names its places to go (_go)", len(places) >= 4, places)
check(
    "every place the update doctor names (and the setting it marks) is an id on the admin page",
    all(f'id="{i}"' in html for p in places for i in p),
    [i for p in places for i in p if f'id="{i}"' not in html],
)
fine_go = [
    n.lineno
    for n in ast.walk(ast.parse(hc))
    if isinstance(n, ast.Call)
    and getattr(n.func, "id", "") == "finding"
    and len(n.args) > 2
    and isinstance(n.args[2], ast.Constant)
    and n.args[2].value == "ok"
    and any(k.arg == "go" for k in n.keywords)
]
check("a finding that is fine sends nobody anywhere (no go)", not fine_go, fine_go)

# Who owns what: a doctor diagnoses and repairs; an ongoing choice is changed on its own page. The Box doctor builds no
# setting chips, and the Security doctor's rows link a policy choice to Security or Updates rather than changing it.
health = (Path(__file__).resolve().parents[1] / "irate_box" / "root" / "health.py").read_text()
check(
    "the Box doctor builds no setting chips (a choice is changed on its own page)",
    "_chip(" not in health and "group=" not in health,
    re.findall(r".*(?:_chip\(|group=).*", health)[:3],
)
adminjs = (Path(__file__).resolve().parents[1] / "web" / "admin.js").read_text()
joint = adminjs[adminjs.index("function jointDo(") : adminjs.index("function jointRow(")]
check(
    "the Security doctor: a policy choice is a link (Go to …), only a cure is a button that acts",
    re.search(r"""secKind\(f\)\s*!==\s*['"]cure['"]""", joint) is not None
    and "Go to ${where}" in joint
    and "secChoose(" not in joint,
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
