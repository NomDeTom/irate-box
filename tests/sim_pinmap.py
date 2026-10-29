# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The given pin map (root/pinmap.py), offline: gpioinfo parsed into lines and claims, the Pi's 40-pin header
resolved to GPIO numbers, a claimed pin refused before a use, and a free one allowed.
python3 tests/sim_pinmap.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import pinmap

PI = """gpiochip0 - 32 lines:
\tline   0:     unnamed       unused   input  active-high
\tline  17:     "GPIO17"      "irate-box" output active-high [used]
\tline   4:     "GPIO4"       "unused"   input  active-high
gpiochip1 - 8 lines:
\tline   0:     "GPIO0"       "unused"   input  active-high
"""
LYRA = """gpiochip0 - 32 lines:
\tline   0:     "GPIO1_A0"    "unused"   input  active-high
\tline   1:     "GPIO1_A1"    "irate-box-key" input active-low [used]
"""
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


lines = pinmap.parse(PI)
check(
    "gpioinfo parsed: its chips, names and claims",
    len(lines) == 4
    and next(line for line in lines if line["line"] == 17)["consumer"] == "irate-box"
    and next(line for line in lines if line["line"] == 17)["used"]
    and not next(line for line in lines if line["line"] == 4)["used"],
    lines,
)
header = pinmap.board_lines("pi", lines)
check(
    "the Pi's header: pin 11 is GPIO17, claimed by irate-box; pin 7 is GPIO4 and free",
    next(row for row in header if row["pin"] == 11)["gpio"] == 17
    and next(row for row in header if row["pin"] == 11)["used"]
    and next(row for row in header if row["pin"] == 7)["used"] is False,
    header,
)
try:
    pinmap.require("pi", 11, lines)
    check("a claimed pin is refused before a use", False)
except Refused as exc:
    check("a claimed pin is refused before a use", exc.message["id"] == "pinmap.claimed", exc.message)
check("a free pin is allowed", pinmap.require("pi", 7, lines)["line"] == 4)
lyra = pinmap.parse(LYRA)
check(
    "a board without a header map lists its lines, and a claimed one is refused",
    pinmap.board_lines("lyra", lyra)[1]["consumer"] == "irate-box-key",
    lyra,
)
try:
    pinmap.require("lyra", 1, lyra)
    check("  its claimed line is refused", False)
except Refused as exc:
    check("  its claimed line is refused", exc.message["id"] == "pinmap.claimed", exc.message)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
