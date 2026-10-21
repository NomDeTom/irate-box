# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""No browser pop-ups on the hub's pages (the admin pages plan, phase 5): a question is drawn on the page
(AW.confirmHere on /admin, askHere from web/ask.js elsewhere), an error is a line on the page, and a new name is a
text field. Read as text, nothing run.

  web/*.js calls no confirm(), prompt() or alert(), but for the two that guard leaving unsaved changes: closing a
    card (admin-widgets.js) and leaving a page (admin-layout.js)

python3 tests/popup_guard.py"""

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
POPUP = re.compile(r"(?<![\w.])(?:window\.)?(confirm|prompt|alert)\(")
KEPT = {("admin-widgets.js", "confirm"), ("admin-layout.js", "confirm")}

found = []
for path in sorted(WEB.glob("*.js")):
    for n, line in enumerate(path.read_text().splitlines(), 1):
        for m in POPUP.finditer(line):
            if (path.name, m.group(1)) not in KEPT:
                found.append(f"{path.name}:{n} {m.group(1)}()")
kept = sum(
    1
    for path in WEB.glob("*.js")
    for line in path.read_text().splitlines()
    for m in POPUP.finditer(line)
    if (path.name, m.group(1)) in KEPT
)
ok = not found and kept == 2
print(
    ("PASS " if ok else "FAIL ")
    + "no browser pop-up but the two that guard unsaved changes"
    + ("" if ok else f"  {found}, kept {kept}")
)
print(f"failures: {int(not ok)}")
sys.exit(0 if ok else 1)
