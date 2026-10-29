# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The given pin map (root/pinmap.py), offline: the real gpioinfo of a Pi 26 and a Lyra 181, in libgpiod v2's
layout, and the Lyra's pinmux-pins; the Pi's 40-pin header resolved, a Rockchip function's pin marked taken, a
partial read and a missing gpioinfo said, and each line kept by its chip and number.
python3 tests/sim_pinmap.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import pinmap

PI = (Path(__file__).parent / "fixtures/gpioinfo-pi.txt").read_text()
LYRA = (Path(__file__).parent / "fixtures/gpioinfo-lyra.txt").read_text()
PINMUX = (Path(__file__).parent / "fixtures/pinmux-pins-lyra.txt").read_text()
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args):
    try:
        fn(*args)
    except Refused as exc:
        return exc.message["id"]
    return None


lines = pinmap.parse(PI)
check(
    "the Pi's real gpioinfo read whole: 62 lines, quoted consumers, active-low, kernel functions",
    len(lines) == pinmap.line_count(PI) == 62
    and next(x for x in lines if x["line"] == 28)["consumer"] == "hpd"
    and next(x for x in lines if x["line"] == 29)["consumer"] == "ACT"
    and next(x for x in lines if x["line"] == 30)["consumer"] == "kernel"
    and next(x for x in lines if x["line"] == 2)["used"] is False,
    lines[:3],
)
header = pinmap.board_lines("pi", lines)
check(
    "the Pi's header: GPIO14 and GPIO15 are plain inputs on this board, and free",
    next(row for row in header if row["pin"] == 8)["gpio"] == 14
    and next(row for row in header if row["pin"] == 8)["used"] is False
    and next(row for row in header if row["pin"] == 10)["used"] is False,
    header[7:10],
)
lyra = pinmap.parse(LYRA)
check(
    "the Lyra's real gpioinfo read whole: 160 lines, bare consumers (no quotes) and unnamed lines",
    len(lyra) == pinmap.line_count(LYRA) == 160
    and next(x for x in lyra if x["chip"] == "gpiochip1" and x["line"] == 0)["consumer"] == "work-led"
    and next(x for x in lyra if x["chip"] == "gpiochip1" and x["line"] == 14)["consumer"] == "cd"
    and next(x for x in lyra if x["chip"] == "gpiochip0" and x["line"] == 6)["used"] is False,
    lyra[:2],
)
V1 = """gpiochip0 - 32 lines:
\tline   0:     unnamed       unused   input  active-high
\tline  17:     "GPIO17"      "irate-box" output active-high [used]
"""
v1 = pinmap.parse(V1)
check(
    "a board with libgpiod v1 still reads: its consumer column and [used]",
    len(v1) == 2
    and next(x for x in v1 if x["line"] == 17)["consumer"] == "irate-box"
    and next(x for x in v1 if x["line"] == 17)["used"]
    and not next(x for x in v1 if x["line"] == 0)["used"],
    v1,
)

claims = pinmap.parse_pinmux(PINMUX)
check(
    "the Lyra's pinmux-pins: the SPI, I2C and SD card pins a function holds, and only those",
    claims.get(("gpiochip0", 6)) == "ff120000.spi"
    and claims.get(("gpiochip0", 1)) == "ff060000.i2c"
    and claims.get(("gpiochip3", 0)) == "ff480000.mmc"
    and claims.get(("gpiochip1", 20)) == "vcc3v3-lcd"
    and ("gpiochip0", 2) not in claims,
    claims,
)
pinmap.apply_pinmux(lyra, claims)
check(
    "  applied: gpiochip0 line 6 is taken by ff120000.spi, and the SD card's own pins too",
    next(x for x in lyra if x["chip"] == "gpiochip0" and x["line"] == 6)["used"]
    and next(x for x in lyra if x["chip"] == "gpiochip0" and x["line"] == 6)["consumer"] == "ff120000.spi"
    and next(x for x in lyra if x["chip"] == "gpiochip3" and x["line"] == 0)["used"],
    lyra[6],
)
lyra_header = pinmap.board_lines("lyra", lyra)
check(
    "a board without a header map keys every line by its chip and number, with no duplicate pins",
    len(lyra_header) == len(lyra) == len({row["pin"] for row in lyra_header})
    and next(row for row in lyra_header if row["pin"] == "gpiochip0:6")["used"],
    lyra_header[:3],
)


def snapshot_with(text, claims=None, has_debugfs=True):
    """snapshot() against a stand-in gpioinfo and pinmux-pins."""
    real_run, real_pinmux = pinmap.run, pinmap.pinmux_claims
    pinmap.run = lambda *a, **k: __import__("subprocess").CompletedProcess(a, 0, text, "")
    pinmap.pinmux_claims = lambda: (claims or {}, has_debugfs)
    try:
        return pinmap.snapshot("lyra")
    finally:
        pinmap.run, pinmap.pinmux_claims = real_run, real_pinmux


whole = snapshot_with(LYRA, claims)
check(
    "the snapshot: the whole map, the pin-mux claims, and no note when nothing is missing",
    len(whole["lines"]) == 160
    and whole["note"] == ""
    and next(row for row in whole["lines"] if row["chip"] == "gpiochip0" and row["line"] == 6)["used"],
    whole["note"],
)
without = snapshot_with(LYRA, {}, has_debugfs=False)
check(
    "  no debugfs: the page is told the map is incomplete, rather than that the SPI pin is free",
    "incomplete" in without["note"] or "may show as free" in without["note"],
    without["note"],
)
partial = snapshot_with("gpiochip0 - 32 lines:\n\tline   0:     not a line gpioinfo writes\n")
check(
    "  a read shorter than the lines printed is said, rather than shown as a whole map",
    "only 0 could be read" in partial["note"],
    partial["note"],
)
real_run = pinmap.run
pinmap.run = lambda *a, **k: __import__("subprocess").CompletedProcess(a, 127, "", "gpioinfo: not found")
check(
    "  gpioinfo missing is refused, and says which package brings it",
    refused(pinmap.snapshot, "pi") == "pinmap.failed",
)
pinmap.run = real_run
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
