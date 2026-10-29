# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The board's GPIO pins, read from gpioinfo: which header pin is which line, what claims it, and which are free.

The Pi's 40-pin header is mapped here; a board whose header map is not known still lists every line gpioinfo
reports, so a claim is not missed. On Rockchip, gpioinfo leaves a pin taken by a function (SPI, I2C, the SD card)
without a consumer, so pinmux-pins from debugfs is read as well, where root can read it."""

import json
import re
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys
from pathlib import Path

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
LINE_COUNT_RE = re.compile(r"^\s*line\s+\d+:")
# libgpiod v2: name, direction, then an optional active-low and an optional consumer=… (quoted on the Pi, bare on the
# Lyra). There is no [used] marker.
V2_RE = re.compile(r'^\s*line\s+(\d+):\s+(?:"([^"]*)"|(\S+))\s+(input|output)\b(.*)$')
# libgpiod v1: name, consumer, direction, bias, and [used] when a consumer holds it.
V1_RE = re.compile(r'^\s*line\s+(\d+):\s+(?:"([^"]*)"|(\S+))\s+(?:"([^"]*)"|(\S+))\s+(input|output)\b(.*)$')
CONSUMER_RE = re.compile(r"consumer=(?:\"([^\"]*)\"|(\S+))")
PINMUX_RE = re.compile(r"^pin\s+\d+\s+\(gpio(\d+)-(\d+)\):\s+(.*)$")
PINMUX_GPIO_RE = re.compile(r"gpio\d+:\d+")


def run(*cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def parse(text):
    """[{chip, line, name, consumer, direction, used}] from gpioinfo's text, v1 or v2."""
    out, chip = [], ""
    for raw in text.splitlines():
        found = CHIP_RE.match(raw)
        if found:
            chip = f"gpiochip{found.group(1)}"
            continue
        if not chip:
            continue
        found = V2_RE.match(raw)
        if found:
            name = found.group(2) if found.group(2) is not None else found.group(3)
            consumer = CONSUMER_RE.search(found.group(5))
            who = (consumer.group(1) if consumer.group(1) is not None else consumer.group(2)) if consumer else ""
            out.append(
                {
                    "chip": chip,
                    "line": int(found.group(1)),
                    "name": name,
                    "consumer": who,
                    "direction": found.group(4),
                    "used": bool(who) or "[used]" in found.group(5),
                }
            )
            continue
        found = V1_RE.match(raw)
        if not found:
            continue
        name = found.group(2) if found.group(2) is not None else found.group(3)
        consumer = found.group(4) if found.group(4) is not None else found.group(5)
        used = "[used]" in found.group(7) or consumer != "unused"
        out.append(
            {
                "chip": chip,
                "line": int(found.group(1)),
                "name": name,
                "consumer": "" if consumer == "unused" else consumer,
                "direction": found.group(6),
                "used": used,
            }
        )
    return out


def line_count(text):
    """The number of line rows gpioinfo printed, whether or not they could be read."""
    return sum(1 for raw in text.splitlines() if LINE_COUNT_RE.match(raw))


def parse_pinmux(text):
    """{(chip, line): owner} for pins a function holds, from pinctrl's pinmux-pins."""
    out = {}
    for raw in text.splitlines():
        found = PINMUX_RE.match(raw)
        if not found:
            continue
        chip, line, rest = f"gpiochip{int(found.group(1))}", int(found.group(2)), found.group(3).split()
        if not rest:
            continue
        if not " ".join(rest).startswith("(MUX UNCLAIMED)"):
            out[(chip, line)] = rest[0]
            continue
        # A GPIO requested through gpiolib (a hog): pinmux-pins names it gpioN:M after the mux owner.
        for token in rest[1:]:
            if PINMUX_GPIO_RE.fullmatch(token):
                out[(chip, line)] = token
                break
    return out


def pinmux_claims():
    """{(chip, line): owner} from every controller's pinmux-pins, and whether debugfs had any to read."""
    base = Path("/sys/kernel/debug/pinctrl")
    files = sorted(base.glob("*/pinmux-pins"))
    claims = {}
    for path in files:
        try:
            claims.update(parse_pinmux(path.read_text()))
        except OSError:
            continue
    return claims, bool(files)


def apply_pinmux(lines, claims):
    """Mark the lines a pinmux function holds, where gpioinfo did not already name a consumer."""
    for line in lines:
        who = claims.get((line["chip"], line["line"]))
        if who and not line["used"]:
            line["consumer"] = who
            line["used"] = True
    return lines


def board_lines(board, lines):
    """The header, as [{pin, gpio, name, consumer, used}], for the Pi; every line for an unknown header."""
    if board == "pi":
        # The header is on the chip that names GPIO2 (gpiochip0 on a Pi 3/4, another chip on a Pi 5).
        chip = next((line["chip"] for line in lines if line["name"] == "GPIO2"), "gpiochip0")
        by_gpio = {line["line"]: line for line in lines if line["chip"] == chip}
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
            "pin": f"{line['chip']}:{line['line']}",
            "gpio": line["line"],
            "name": line["name"],
            "consumer": line["consumer"],
            "used": line["used"],
        }
        for line in lines
    ]


def snapshot(board=None):
    """The board, every line gpioinfo reports and the header, for the page; the pin-mux claims on Rockchip too."""
    from irate_box.root import hardening

    board = board or hardening.board()
    said = run("gpioinfo")
    if said.returncode:
        why = said.stderr.strip()[-200:] or "gpioinfo failed"
        if said.returncode == 127:
            why = "gpioinfo is not installed (it comes with the gpiod package)."
        raise Refused(M("pinmap.failed", "The GPIO lines could not be read: {why}", why=why))
    lines = parse(said.stdout)
    expected = line_count(said.stdout)
    note = ""
    if len(lines) < expected:
        note = f"gpioinfo printed {expected} lines, but only {len(lines)} could be read: some pins may be missing."
    if board != "pi":
        claims, had_debugfs = pinmux_claims()
        if had_debugfs:
            apply_pinmux(lines, claims)
        else:
            note = (
                "The pin-mux view under /sys/kernel/debug/pinctrl is not available (root and debugfs are needed), "
                "so a pin that a function such as SPI or I2C holds may show as free."
            )
    return {"board": board, "lines": lines, "header": board_lines(board, lines), "note": note}


def main(argv):
    """irate-box pinmap [BOARD]: the header and what claims each pin, from gpioinfo."""
    print(json.dumps(snapshot(argv[0] if argv else None), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
