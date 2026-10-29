# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The board's GPIO pins, read from gpioinfo: which header pin is which line, what claims it, and which are free.

A use of a claimed pin is refused before an overlay is written. The Pi's 40-pin header is mapped here; a board whose
header map is not known still lists every line gpioinfo reports, so a claim is not missed."""

import json
import re
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys

from irate_box.msg import M, Refused

# pin: GPIO number (BCM) on the Pi's 40-pin header. Power and ground pins have no GPIO.
PI = {
    1: None,
    2: None,
    3: 2,
    4: None,
    5: 3,
    6: None,
    7: 4,
    8: 14,
    9: None,
    10: 15,
    11: 17,
    12: 18,
    13: 27,
    14: None,
    15: 22,
    16: 23,
    17: None,
    18: 24,
    19: 10,
    20: None,
    21: 9,
    22: 25,
    23: 11,
    24: 8,
    25: None,
    26: 7,
    27: 0,
    28: 1,
    29: 5,
    30: None,
    31: 6,
    32: 12,
    33: 13,
    34: None,
    35: 19,
    36: 16,
    37: 26,
    38: 20,
    39: None,
    40: 21,
}
CHIP_RE = re.compile(r"^gpiochip(\d+)\s+-\s+\d+\s+lines:")
LINE_RE = re.compile(r'^\s*line\s+(\d+):\s+(?:"([^"]*)"|(\S+))\s+(?:"([^"]*)"|(\S+))\s+(\S+)\s+(\S+)(.*)$')


def run(*cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def parse(text):
    """[{chip, line, name, consumer, direction, used}] from gpioinfo's text."""
    out, chip = [], ""
    for raw in text.splitlines():
        found = CHIP_RE.match(raw)
        if found:
            chip = f"gpiochip{found.group(1)}"
            continue
        found = LINE_RE.match(raw)
        if not found or not chip:
            continue
        name = found.group(2) if found.group(2) is not None else found.group(3)
        consumer = found.group(4) if found.group(4) is not None else found.group(5)
        used = "[used]" in found.group(8) or consumer != "unused"
        out.append(
            {
                "chip": chip,
                "line": int(found.group(1)),
                "name": name,
                "consumer": consumer,
                "direction": found.group(6),
                "used": used,
            }
        )
    return out


def board_lines(board, lines):
    """The header, as [{pin, gpio, name, consumer, used}], for the Pi; every line for an unknown header."""
    if board == "pi":
        by_gpio = {line["line"]: line for line in lines if line["chip"] == "gpiochip0"}
        return [
            {
                "pin": pin,
                "gpio": gpio,
                "name": by_gpio.get(gpio, {}).get("name", ""),
                "consumer": by_gpio.get(gpio, {}).get("consumer", ""),
                "used": by_gpio.get(gpio, {}).get("used", False),
            }
            for pin, gpio in sorted(PI.items())
        ]
    return [
        {
            "pin": line["line"],
            "gpio": line["line"],
            "name": line["name"],
            "consumer": line["consumer"],
            "used": line["used"],
        }
        for line in lines
    ]


def require(board, pin, lines):
    """Refuse a header pin already claimed, before an overlay is written; return the line used."""
    if board == "pi":
        if pin not in PI:
            raise Refused(M("pinmap.pin", "Pin {pin} is not on the 40-pin header.", pin=pin))
        gpio = PI[pin]
        if gpio is None:
            raise Refused(M("pinmap.power", "Pin {pin} is power or ground, not a GPIO.", pin=pin))
        line = next((x for x in lines if x["chip"] == "gpiochip0" and x["line"] == gpio), None)
    else:
        line = next((x for x in lines if x["line"] == pin), None)
    if line and line["used"]:
        raise Refused(
            M(
                "pinmap.claimed",
                "GPIO {gpio} is already claimed by {who}.",
                gpio=pin if board != "pi" else gpio,
                who=line["consumer"] or line["name"] or "another use",
            )
        )
    return line


def snapshot(board=None):
    """The board, every line gpioinfo reports and the header, for the page."""
    from irate_box.root import hardening

    board = board or hardening.board()
    said = run("gpioinfo")
    if said.returncode:
        raise SystemExit(said.stderr.strip()[-200:] or "gpioinfo failed")
    lines = parse(said.stdout)
    return {"board": board, "lines": lines, "header": board_lines(board, lines)}


def main(argv):
    """irate-box pinmap [BOARD]: the header and what claims each pin, from gpioinfo."""
    print(json.dumps(snapshot(argv[0] if argv else None), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
