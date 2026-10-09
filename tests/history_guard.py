# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The code says what it does and why, not who asked or when: lines naming people, the plan or the notes, and
comments carrying dates, said with their place. A warning by default; --strict fails (for CI, if wanted).
python3 tests/history_guard.py [--strict]"""
import re, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# A note link is [[name-like]]; "step N" is left out, as the product has steps of its own (setup's "Step 1 of 5").
ANYWHERE = re.compile(r"\bTom\b|next-work.plan|current-and-next|notes-sync|\[\[[a-z][a-z0-9-]*\]\]|\bstance review\b|\b[Ii]tem \d+\b")
DATE = re.compile(r"\b20\d\d-[01]\d-[0-3]\d\b")
COMMENT = re.compile(r"^\s*(#|//|/\*|\*|<!--)")
SKIP = re.compile(r"^(tests/fixtures/|LICENSES/|.*\.(png|jpg|webp|webm|zim|gpg|asc|pub|woff2?)$)")
CODE = re.compile(r"^(irate_box/|web/|scripts/|config/|tools/|install\.sh$|uninstall\.sh$|irate-box$|extras/)")

found = []
for f in subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True).stdout.split():
    if SKIP.match(f) or not CODE.match(f):
        continue
    try:
        lines = (REPO / f).read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        continue
    for n, line in enumerate(lines, 1):
        if "SPDX-" in line:
            continue
        if ANYWHERE.search(line) or (COMMENT.match(line) and DATE.search(line)):
            found.append(f"{f}:{n}: {line.strip()[:120]}")
for line in found[:40]:
    print(line)
print(f"history: {len(found)} line(s) naming people, the plan or dates in the code" if found else "history: none")
sys.exit(1 if found and "--strict" in sys.argv else 0)
