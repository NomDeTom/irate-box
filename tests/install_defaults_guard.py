# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""install.sh runs with set -u: a variable its option loop sets only when an option is given must have a default
before the loop, or every install without that option stops at its first read ("unbound variable"; an update on box
181 stopped so). Each NAME=value the loop assigns is checked against the assignments above it.
python3 tests/install_defaults_guard.py"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
lines = (REPO / "install.sh").read_text().splitlines()
loop = next(n for n, line in enumerate(lines) if line.startswith("while [ $# -gt 0 ]; do"))
end = next(n for n in range(loop, len(lines)) if lines[n] == "done")
assign = re.compile(r"(?:^|[\s;])([A-Z][A-Z0-9_]*)\+?=")
# In the loop, only a statement's assignments: a line that starts with them (one or several), or after a case
# pattern's ")" or a ";" "&&" "||". Not words in an option's help text ("--uplink takes KEY=VALUE pairs").
statement = re.compile(r"(?:^\s*|[;)]\s*|&&\s*|\|\|\s*)([A-Z][A-Z0-9_]*)\+?=")
before = {m.group(1) for line in lines[:loop] if not line.lstrip().startswith("#") for m in assign.finditer(line)}
set_in_loop = set()
for line in lines[loop:end]:
    if line.lstrip().startswith("#"):
        continue
    if re.match(r"^\s*[A-Z][A-Z0-9_]*\+?=", line):
        set_in_loop |= {m.group(1) for m in assign.finditer(line)}
    set_in_loop |= {m.group(1) for m in statement.finditer(line)}
missing = sorted(set_in_loop - before)
for name in missing:
    print(f"FAIL {name} is set by an option but has no default before the option loop")
print("ok" if not missing else f"{len(missing)} failure(s)")
sys.exit(1 if missing else 0)
