# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Housecall doctor's shape (notes: housecall-doctor-plan): no function defined twice (the second silently
replaces the first), the commands the plan names, the report where /admin looks, and the fourth row in the
doctors' table. python3 tests/housecall_guard.py"""

import ast
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
src = (REPO / "irate_box" / "root" / "housecall.py").read_text()
tree = ast.parse(src)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


names = Counter(n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef)))
check("no function or class defined twice", all(c == 1 for c in names.values()), [n for n, c in names.items() if c > 1])
for flag in ("--image", "--device", "--root", "--this-card", "--reference", "--dpkg", "--git-tree"):
    check(f"the command line takes {flag}", f'"{flag}"' in src)
check("the report is control/housecall.json", 'CONTROL / "housecall.json"' in src)
check(
    "from inside: the hub's large folders left out whole (box 181: 14 GB of build caches in 231 280 files)",
    "skip=large" in src and '"ci"' in src and '"zim"' in src,
)
check(
    "  and /etc/systemd read once, with /etc, not listed again",
    '"/etc/systemd"' not in src,
)
check(
    "from inside: the settings, code, hub state, root's records and systemd's folders",
    all(
        f'"{folder}"' in src
        for folder in (
            "/etc",
            "/opt/irate-box",
            "/var/lib/hub",
            "/var/lib/irate-box-root",
            "/lib/systemd/system",
            "/usr/lib/systemd/system",
        )
    ),
)
check("a whole-disk filesystem is read without a partition table", "_scan_ext" in src and "blkid(path, 0)" in src)
check("debugfs is never asked to write (no -w)", '"-w"' not in src)
check("e2fsck runs read-only (-n)", '"e2fsck", "-fn"' in src)
check("blkid probes without writing (-p)", '"blkid", "-p"' in src)
check("a reference's digest is checked", "re.fullmatch" in src and "sha256" in src and "md5" in src and "git" in src)
check("the package and git references exist", "references_from_dpkg" in src and "references_from_git" in src)

doctors = (REPO / "docs" / "dev" / "doctors.md").read_text()
check("the doctors' page counts four", "# The four doctors" in doctors)
check("  and has the Housecall doctor's row", "| Housecall doctor |" in doctors and "housecall.py" in doctors)
check(
    "  and no longer says Box doctor is the only doctor that repairs",
    "only doctor that repairs:" not in doctors,
)
check(
    "documents how to run it on an image",
    "irate-box housecall --image" in doctors,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
