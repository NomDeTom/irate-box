# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The offline kit's apps (install.sh --make-offline-bundle): a box making a kit of itself (--apps) uses its installed
copies, with no download (GitHub's API limit is 60 an hour without a token); only an app it lacks is downloaded, and
only a download that fails counts as missing. The loop is taken from install.sh as it is and run with a librarian
that always fails. python3 tests/sim_bundle_apps.py"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


inst = (REPO / "install.sh").read_text()
loop = re.search(r"(?ms)^\tfor app in draw mermaid serial tools room flasher; do\n.*?^\tdone\n", inst).group(0)
T = Path(tempfile.mkdtemp(prefix="bundle-apps-"))
(T / "here").mkdir()
# The librarian, failing as it does when GitHub's API limit is reached.
(T / "here" / "irate-box").write_text("#!/bin/sh\necho 'librarian: GitHub API rate limit reached' >&2\nexit 1\n")
(T / "here" / "irate-box").chmod(0o755)


def run(apps, room):
    for d in apps:
        (T / "apps" / d).mkdir(parents=True, exist_ok=True)
        (T / "apps" / d / "index.html").write_text(d)
    if room:
        (T / "room" / "dist").mkdir(parents=True, exist_ok=True)
        (T / "room" / "dist" / "index.js").write_text("room")
    script = (
        f'set -e\nhere="{T}/here"; kit="{T}/kit"; APPS_SRC="{T}/apps"; ROOM="{T}/room"; MISSING_N=0\n'
        f'tmp="$(mktemp -d)"\n{loop}echo "MISSING=$MISSING_N"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout


out = run(["draw", "mermaid", "serial", "tools", "flasher"], room=True)
check(
    "a box making a kit of itself: every app its installed copy, nothing downloaded, nothing missing",
    "MISSING=0" in out
    and out.count("this box's installed copy") == 6
    and "Missing" not in out
    and all((T / "kit" / "apps" / a).exists() for a in ("draw", "mermaid", "serial", "tools", "flasher", "room")),
    out,
)
import shutil  # noqa: E402

shutil.rmtree(T / "apps", ignore_errors=True)
shutil.rmtree(T / "kit", ignore_errors=True)
out = run(["draw"], room=True)
check(
    "  an app it lacks: downloaded, and missing only when that fails (once each)",
    "MISSING=4" in out and out.count("Missing offline component") == 4 and out.count("this box's installed copy") == 2,
    out,
)
shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
