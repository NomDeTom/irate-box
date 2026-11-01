# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The board's GPIO pins, read from gpioinfo: which header pin is which line, what claims it, and which are free.

The Pi's and the Luckfox Lyra Zero W's 40-pin headers are mapped here; a board whose header map is not known
still lists every line gpioinfo reports, so a claim is not missed. On Rockchip, gpioinfo leaves a pin taken by a
function (SPI, I2C, the SD card) without a consumer, so pinmux-pins from debugfs is read as well, where root can
read it."""

import json
import os
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
# Power and ground pins have no GPIO. The Pi's usual alternate names, for the header's role column.
PI_POWER = {
    1: "3V3",
    2: "5V",
    4: "5V",
    6: "GND",
    9: "GND",
    14: "GND",
    17: "3V3",
    20: "GND",
    25: "GND",
    30: "GND",
    34: "GND",
    39: "GND",
}
PI_ALT = {
    0: "ID_SD",
    1: "ID_SC",
    2: "SDA1",
    3: "SCL1",
    4: "GPCLK0",
    7: "CE1",
    8: "CE0",
    9: "MISO",
    10: "MOSI",
    11: "SCLK",
    14: "TXD",
    15: "RXD",
    18: "PCM_CLK",
    19: "MISO1",
    20: "MOSI1",
    21: "SCLK1",
}

# Luckfox Lyra Zero W, 40-pin header (Pi layout), from Luckfox's pinout as the nomdetom.github.io pinout map holds it.
# pin: (name, global GPIO, RM_IO, fixed role). Power and ground pins have no GPIO.
LYRA_ZERO = {
    1: ("3V3_OUT", None, None, ""),
    2: ("VSYS", None, None, ""),
    3: ("GPIO0_A0", 0, 0, "TP+RTC"),
    4: ("VSYS", None, None, ""),
    5: ("GPIO0_A1", 1, 1, "TP+RTC"),
    6: ("GND", None, None, ""),
    7: ("GPIO0_A2", 2, 2, ""),
    8: ("GPIO0_C6", 22, 22, "UART0 TX"),
    9: ("GND", None, None, ""),
    10: ("GPIO0_C7", 23, 23, "UART0 RX"),
    11: ("GPIO0_A3", 3, 3, ""),
    12: ("GPIO0_B6", 14, 14, ""),
    13: ("GPIO0_A4", 4, 4, ""),
    14: ("GND", None, None, ""),
    15: ("GPIO0_A5", 5, 5, ""),
    16: ("GPIO0_B5", 13, 13, ""),
    17: ("3V3_OUT", None, None, ""),
    18: ("GPIO0_B4", 12, 12, ""),
    19: ("GPIO0_A6", 6, 6, ""),
    20: ("GND", None, None, ""),
    21: ("GPIO0_A7", 7, 7, ""),
    22: ("GPIO0_B3", 11, 11, ""),
    23: ("GPIO0_B0", 8, 8, ""),
    24: ("GPIO0_B2", 10, 10, ""),
    25: ("GND", None, None, ""),
    26: ("GPIO0_B1", 9, 9, ""),
    27: ("GPIO1_B1", 41, 24, ""),
    28: ("GPIO1_D3", 59, 31, ""),
    29: ("GPIO1_B2", 42, 25, ""),
    30: ("GND", None, None, ""),
    31: ("GPIO1_B3", 43, 26, ""),
    32: ("GPIO1_D2", 58, 30, ""),
    33: ("GPIO1_C2", 50, 27, ""),
    34: ("GND", None, None, ""),
    35: ("GPIO0_C0", 16, 16, ""),
    36: ("GPIO1_D1", 57, 29, ""),
    37: ("GPIO1_C3", 51, 28, ""),
    38: ("GPIO0_C1", 17, 17, ""),
    39: ("GND", None, None, ""),
    40: ("GPIO0_C2", 18, 18, ""),
}
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
# The RK3506's GPIO controllers by device address (Firefly's RK3506 and Luckfox's Lyra manuals): GPIO0..GPIO4.
# gpiochipN is probe order, so a PMIC's or an expander's controller added before them shifts every number.
RK3506_GPIO_BANK = {
    "ff940000": 0,
    "ff870000": 1,
    "ff1c0000": 2,
    "ff1d0000": 3,
    "ff1e0000": 4,
}
GPIO_SYS = Path(os.environ.get("HUB_GPIO_SYS", "/sys/bus/gpio/devices"))
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


def model():
    """The board's model, from the device tree: "Luckfox Lyra Zero W", "Raspberry Pi 3 Model B Plus", …"""
    try:
        return (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace")
    except OSError:
        return ""


def _pin(row):
    """A header row's type, from its name when it has no GPIO."""
    name = row["name"]
    if row["gpio"] is not None:
        return "gpio"
    return "gnd" if name == "GND" else "5v" if name in ("5V", "VSYS") else "3v3"


def _chip_address(chip):
    """The controller's memory address, from /sys/bus/gpio/devices/gpiochipN (or its device link), or ""."""
    for base in (GPIO_SYS / chip, GPIO_SYS / chip / "device"):
        try:
            target = base.resolve(strict=True)
        except OSError:
            continue
        for part in reversed(target.parts):
            found = re.fullmatch(r"([0-9a-fA-F]{8})(?:\.gpio)?", part)
            if found:
                return found.group(1).lower()
    return ""


def chip_banks(lines):
    """{chip: GPIO bank} for the Rockchip controllers in lines, by device address. Only when sysfs names none
    (an offline snapshot) does it fall back to gpiochipN, as before."""
    chips = sorted({line["chip"] for line in lines})
    banks = {}
    for chip in chips:
        bank = RK3506_GPIO_BANK.get(_chip_address(chip))
        if bank is not None:
            banks[chip] = bank
    if banks:
        return banks
    return {chip: int(found.group(1)) for chip in chips if (found := re.fullmatch(r"gpiochip(\d+)", chip)) is not None}


def board_lines(board, lines, model_name=""):
    """The header, as [{pin, type, name, gpio, chip, line, rmio, role, consumer, used}], for the Pi and the
    Lyra Zero W; every line, keyed by chip and number, for a board whose header is not known."""
    if board == "pi":
        # The header is on the chip that names GPIO2 (gpiochip0 on a Pi 3/4, another chip on a Pi 5).
        chip = next((line["chip"] for line in lines if line["name"] == "GPIO2"), "gpiochip0")
        by_gpio = {line["line"]: line for line in lines if line["chip"] == chip}
        out = []
        for pin, gpio in sorted(PI.items()):
            found = by_gpio.get(gpio, {}) if gpio is not None else {}
            row = {
                "pin": pin,
                "name": f"GPIO{gpio}" if gpio is not None else PI_POWER[pin],
                "gpio": gpio,
                "chip": chip if gpio is not None else None,
                "line": gpio if gpio is not None else None,
                "rmio": None,
                "role": PI_ALT.get(gpio, "") if gpio is not None else "",
                "consumer": found.get("consumer", ""),
                "used": found.get("used", False),
            }
            row["type"] = _pin(row)
            out.append(row)
        return out
    if board == "lyra" and "zero" in model_name.lower():
        banks = chip_banks(lines)
        by_gpio = {banks[line["chip"]] * 32 + line["line"]: line for line in lines if line["chip"] in banks}
        out = []
        for pin, (name, gpio, rmio, role) in sorted(LYRA_ZERO.items()):
            found = by_gpio.get(gpio, {}) if gpio is not None else {}
            row = {
                "pin": pin,
                "name": name,
                "gpio": gpio,
                "chip": found.get("chip") if gpio is not None else None,
                "line": found.get("line") if gpio is not None else None,
                "rmio": rmio,
                "role": role,
                "consumer": found.get("consumer", ""),
                "used": found.get("used", False),
            }
            row["type"] = _pin(row)
            out.append(row)
        return out
    return [
        {
            "pin": f"{line['chip']}:{line['line']}",
            "type": "gpio",
            "name": line["name"],
            "gpio": line["line"],
            "chip": line["chip"],
            "line": line["line"],
            "rmio": None,
            "role": "",
            "consumer": line["consumer"],
            "used": line["used"],
        }
        for line in lines
    ]


def snapshot(board=None):
    """The board, every line gpioinfo reports and the header, for the page; the pin-mux claims on Rockchip too."""
    from irate_box.root import hardening

    board = board or hardening.board()
    model_name = model()
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
            pinmux = (
                "The pin-mux view under /sys/kernel/debug/pinctrl is not available (root and debugfs are needed), "
                "so a pin that a function such as SPI or I2C holds may show as free."
            )
            note = f"{note} {pinmux}".strip()
    return {
        "board": board,
        "model": model_name,
        "lines": lines,
        "header": board_lines(board, lines, model_name),
        "note": note,
    }


def main(argv):
    """irate-box pinmap [BOARD]: the header and what claims each pin, from gpioinfo."""
    print(json.dumps(snapshot(argv[0] if argv else None), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
