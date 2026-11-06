# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's SVG-Edit check: a dev checkout without HUB_SVG_ROOT is not a fault, a missing
folder or a build without its page or Editor.js is a problem with the installer's repair, and a
whole build is fine. python3 tests/sim_svg_root.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import finding
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


old = os.environ.pop("HUB_SVG_ROOT", None)
try:
    result = health.check_svg_root()
    check(
        "a checkout with no HUB_SVG_ROOT: not a fault",
        result[0]["id"] == "svg-root"
        and result[0]["status"] == "ok"
        and "svg-root.unset" in finding.message_ids(result[0]),
        result,
    )

    T = Path(tempfile.mkdtemp(prefix="svg-root-"))
    missing = T / "absent"
    os.environ["HUB_SVG_ROOT"] = str(missing)
    result = health.check_svg_root()
    check(
        "a missing folder: a problem, with the installer's repair",
        result[0]["status"] == "problem"
        and "svg-root.missing" in finding.message_ids(result[0])
        and any(action["choice"] == "rerun-install" for action in result[0]["actions"]),
        result,
    )

    half = T / "half"
    half.mkdir()
    (half / "index.html").write_text("<html></html>")
    os.environ["HUB_SVG_ROOT"] = str(half)
    result = health.check_svg_root()
    check(
        "a build without Editor.js: a problem naming it",
        result[0]["status"] == "problem"
        and "svg-root.incomplete" in finding.message_ids(result[0])
        and any("Editor.js" in item for item in result[0]["evidence"]),
        result,
    )

    (half / "Editor.js").write_text("// editor\n")
    result = health.check_svg_root()
    check(
        "a whole build: fine",
        result[0]["status"] == "ok" and "svg-root.ok" in finding.message_ids(result[0]),
        result,
    )
finally:
    os.environ.pop("HUB_SVG_ROOT", None)
    if old is not None:
        os.environ["HUB_SVG_ROOT"] = old

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
