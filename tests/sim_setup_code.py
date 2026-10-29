# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The first-boot setup code (irate_box/setup_code.py): 16 characters made while the box is unclaimed, the plain code
root's alone, the hash the hub reads, a wrong code refused, and both files gone once the box is claimed.
python3 tests/sim_setup_code.py"""

import os
import stat
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="setup-code-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_UNCLAIMED_FILE=str(T / "unclaimed"))
sys.path.insert(0, str(REPO))
from irate_box import setup_code

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


Path(os.environ["HUB_UNCLAIMED_FILE"]).write_text("no admin account yet\n")
code = setup_code.ensure()
check("a 16-character code is made while the box is unclaimed", isinstance(code, str) and len(code) == 16, code)
check(
    "  the plain code is root's alone; the hash is root:hub and readable by the hub",
    stat.S_IMODE(setup_code.CODE_FILE.stat().st_mode) == 0o400
    and stat.S_IMODE(setup_code.HASH_FILE.stat().st_mode) == 0o640
    and setup_code.CODE_FILE.read_text().strip() == code
    and setup_code.HASH_FILE.read_text().strip() != code,
    (setup_code.CODE_FILE, setup_code.HASH_FILE),
)
check(
    "  the right code verifies; a wrong one does not",
    setup_code.verify(code) and not setup_code.verify(code[:-1] + "X"),
)
check("  making it again keeps the same code", setup_code.ensure() == code)
setup_code.HASH_FILE.write_text("a wrong hash\n")
check("  a missing or wrong hash is made again from the code", setup_code.ensure() == code and setup_code.verify(code))
setup_code.clear()
check("  clearing removes both files", not setup_code.CODE_FILE.exists() and not setup_code.HASH_FILE.exists())
Path(os.environ["HUB_UNCLAIMED_FILE"]).unlink()
check(
    "  once the box is claimed, no code is made, and any old one is removed",
    setup_code.ensure() is None and not setup_code.CODE_FILE.exists() and not setup_code.HASH_FILE.exists(),
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
