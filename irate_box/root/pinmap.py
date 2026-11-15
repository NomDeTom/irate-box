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

# Banana Pi BPI-M2 Zero, 40-pin Raspberry Pi-compatible header, from Banana Pi's wiki pinout. The port names are
# the Allwinner H3/H2+ GPIO names; the roles are the pinout's default (and alternate) functions.
BPI_M2_ZERO = {
    1: ("3V3", None, None, ""),
    2: ("5V", None, None, ""),
    3: ("PA12", 12, None, "TWI0-SDA"),
    4: ("5V", None, None, ""),
    5: ("PA11", 11, None, "TWI0-SCK"),
    6: ("GND", None, None, ""),
    7: ("PA6", 6, None, "PWM1"),
    8: ("PA13", 13, None, "UART3-TX / SPI1-CS"),
    9: ("GND", None, None, ""),
    10: ("PA14", 14, None, "UART3-RX / SPI1-CLK"),
    11: ("PA1", 1, None, "UART2-RX"),
    12: ("PA16", 16, None, "UART3-CTS / SPI1-MISO"),
    13: ("PA0", 0, None, "UART2-TX"),
    14: ("GND", None, None, ""),
    15: ("PA3", 3, None, "UART2-CTS"),
    16: ("PA15", 15, None, "UART3-RTS / SPI1-MOSI"),
    17: ("3V3", None, None, ""),
    18: ("PC4", 68, None, ""),
    19: ("PC0", 64, None, "SPI0-MOSI"),
    20: ("GND", None, None, ""),
    21: ("PC1", 65, None, "SPI0-MISO"),
    22: ("PA2", 2, None, "UART2-RTS"),
    23: ("PC2", 66, None, "SPI0-CLK"),
    24: ("PC3", 67, None, "SPI0-CS"),
    25: ("GND", None, None, ""),
    26: ("PC7", 71, None, ""),
    27: ("PA19", 19, None, "TWI1-SDA"),
    28: ("PA18", 18, None, "TWI1-SCK"),
    29: ("PA7", 7, None, ""),
    30: ("GND", None, None, ""),
    31: ("PA8", 8, None, ""),
    32: ("PL2", 354, None, ""),
    33: ("PA9", 9, None, ""),
    34: ("GND", None, None, ""),
    35: ("PA10", 10, None, ""),
    36: ("PL4", 356, None, ""),
    37: ("PA17", 17, None, "SPDIF-OUT"),
    38: ("PA21", 21, None, ""),
    39: ("GND", None, None, ""),
    40: ("PA20", 20, None, ""),
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
# The Allwinner H3/H2+ controllers: the main PIO (PA-PG) and the R_PIO (PL), which the BPI-M2 Zero's header uses.
SUNXI_GPIO_CHIP = {
    "1c20800": "pio",
    "1f02c00": "rpio",
}
GPIO_SYS = Path(os.environ.get("HUB_GPIO_SYS", "/sys/bus/gpio/devices"))
SPI_MASTERS = Path(os.environ.get("HUB_SPI_MASTERS", "/sys/class/spi_master"))
SPI_DEVICES = Path(os.environ.get("HUB_SPI_SYS", "/sys/bus/spi/devices"))
I2C_DEVICES = Path(os.environ.get("HUB_I2C_SYS", "/sys/bus/i2c/devices"))
CHIP_RE = re.compile(r"^gpiochip(\d+)\s+-\s+\d+\s+lines:")
LINE_COUNT_RE = re.compile(r"^\s*line\s+\d+:")
# libgpiod v2: name, direction, then an optional active-low and an optional consumer=… (quoted on the Pi, bare on the
# Lyra). There is no [used] marker.
V2_RE = re.compile(r'^\s*line\s+(\d+):\s+(?:"([^"]*)"|(\S+))\s+(input|output)\b(.*)$')
# libgpiod v1: name, consumer, direction, bias, and [used] when a consumer holds it.
V1_RE = re.compile(r'^\s*line\s+(\d+):\s+(?:"([^"]*)"|(\S+))\s+(?:"([^"]*)"|(\S+))\s+(input|output)\b(.*)$')
CONSUMER_RE = re.compile(r"consumer=(?:\"([^\"]*)\"|(\S+))")
PINMUX_RE = re.compile(r"^pin\s+(\d+)\s+\(([^)]+)\):\s+(.*)$")
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
        found = V2_RE.fullmatch(raw)
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
        found = V1_RE.fullmatch(raw)
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
    """{key: {owner, function, group, name, pin}} for pins a function holds, from pinctrl's pinmux-pins.
    The key is (chip, line) for a gpioX-Y name, ("gpio", N) for gpioN, ("port", "PA12") for a sunxi port, else
    ("pin", N). The function and group are kept, so a claimed pin can be named by what it does."""
    out = {}
    for raw in text.splitlines():
        found = PINMUX_RE.fullmatch(raw)
        if not found:
            continue
        pin_no, name, rest = int(found.group(1)), found.group(2).strip(), found.group(3).split()
        if not rest:
            continue
        function = group = ""
        if " ".join(rest).startswith("(MUX UNCLAIMED)"):
            # A GPIO requested through gpiolib (a hog): pinmux-pins names it gpioN:M after the mux owner.
            owner = next((token for token in rest[1:] if PINMUX_GPIO_RE.fullmatch(token)), "")
        else:
            owner = rest[1] if rest[0] == "device" and len(rest) > 1 else rest[0]
            if "function" in rest:
                at = rest.index("function")
                function = rest[at + 1] if at + 1 < len(rest) else ""
            if "group" in rest:
                at = rest.index("group")
                group = rest[at + 1] if at + 1 < len(rest) else ""
        if not owner or owner in ("UNCLAIMED", "GPIO"):
            continue
        banked = re.fullmatch(r"gpio(\d+)-(\d+)", name)
        if banked:
            key = (f"gpiochip{int(banked.group(1))}", int(banked.group(2)))
        elif re.fullmatch(r"gpio\d+", name):
            key = ("gpio", int(name[4:]))
        elif re.fullmatch(r"P[A-GL]\d+", name.upper()):
            key = ("port", name.upper())
        else:
            key = ("pin", pin_no)
        out[key] = {"owner": owner, "function": function, "group": group, "name": name, "pin": pin_no}
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
    """Mark the lines a pinmux function holds, where gpioinfo did not already name a consumer. A claim is matched
    by its chip and line (Rockchip), its GPIO number (the Pi), its port name (sunxi), or its pin number."""
    for key, claim in claims.items():
        if str(key[0]).startswith("gpiochip"):
            line = next((row for row in lines if row["chip"] == key[0] and row["line"] == key[1]), None)
        elif key[0] == "gpio":
            line = next((row for row in lines if row["line"] == key[1]), None)
        elif key[0] == "port":
            line = next((row for row in lines if _port_key(row["name"]) == key[1]), None)
        else:
            line = next((row for row in lines if row["line"] == claim["pin"]), None)
        if line and not line["used"]:
            line["consumer"] = claim["owner"]
            line["used"] = True
            line["function"] = claim["function"]
            line["group"] = claim["group"]
    return lines


def parse_aliases(text):
    """{name: path} from a device tree aliases file, either name=path or name = "path";."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        found = re.fullmatch(r'([A-Za-z0-9_-]+)\s*=\s*"?([^";]+)"?;?$', line)
        if found:
            out[found.group(1)] = found.group(2).strip()
    return out


def aliases():
    """The device tree's aliases, {name: path}: a directory of files on this kernel, a file of name = "path"; on
    others."""
    path = DT / "aliases"
    out = {}
    try:
        if path.is_dir():
            for entry in path.iterdir():
                if entry.name == "name":
                    continue
                try:
                    out[entry.name] = entry.read_bytes().rstrip(b"\0\n").decode(errors="replace")
                except OSError:
                    continue
        else:
            out = parse_aliases(path.read_text(errors="replace"))
    except OSError:
        pass
    return out


def _of_node_name(owner):
    """The device tree node a platform, SPI or I2C device's of_node points at, by its last name (serial@7e201000)."""
    for base in (Path("/sys/bus/platform/devices"), Path("/sys/bus/spi/devices"), Path("/sys/bus/i2c/devices")):
        try:
            return (base / owner / "of_node").resolve(strict=True).name
        except OSError:
            continue
    return ""


def alias_for(owner, table=None, of_node=None):
    """The alias for an owner's address, or through its of_node where the addresses differ (the Pi's 3f…/7e…)."""
    table = aliases() if table is None else table
    address = owner.split(".", 1)[0].lower()
    for name, value in table.items():
        if re.search(rf"@{re.escape(address)}$", value, re.I):
            return name
    node = of_node if of_node is not None else _of_node_name(owner)
    if node:
        for name, value in table.items():
            if value.rstrip("/").endswith("/" + node) or value.rstrip("/") == node:
                return name
    return ""


def _bus_number(base, prefix, owner):
    """The Linux bus number of the controller at owner's address (ff120000.spi -> 0), or None."""
    address = owner.split(".", 1)[0].lower()
    if not address:
        return None
    try:
        entries = sorted(base.glob(f"{prefix}*"))
    except OSError:
        return None
    for entry in entries:
        number = entry.name[len(prefix) :]
        if not number.isdigit():
            continue
        try:
            target = str(entry.resolve(strict=True)).lower()
        except OSError:
            continue
        if f"/{address}." in target:
            return int(number)
    return None


def bus_devices(owner, signal=""):
    """What sits on the bus a pin's controller drives, by the names a user configures: spidev0.0, or an I2C
    device's own name (pcf8563). Only a device with a driver bound counts: a device-tree entry for a panel
    that is not fitted is not on the bus. A chip select names only its own device. "" when nothing is."""
    kind = owner.rsplit(".", 1)[-1].lower() if "." in owner else ""
    names = []
    if kind == "spi":
        bus = _bus_number(SPI_MASTERS, "spi", owner)
        if bus is None:
            return ""
        cs = re.fullmatch(r"cs(\d+)", signal.lower())
        try:
            devices = sorted(SPI_DEVICES.glob(f"spi{bus}.*"))
        except OSError:
            return ""
        for device in devices:
            index = device.name.split(".", 1)[1]
            if cs and index != cs.group(1):
                continue
            if not (device / "driver").exists():
                continue
            try:
                modalias = (device / "modalias").read_text().strip()
            except OSError:
                continue
            driver = modalias[4:] if modalias.startswith("spi:") else modalias
            names.append(f"spidev{bus}.{index}" if driver == "spidev" else driver)
    elif kind == "i2c":
        bus = _bus_number(I2C_DEVICES, "i2c-", owner)
        if bus is None:
            return ""
        try:
            devices = sorted(I2C_DEVICES.glob(f"{bus}-*"))
        except OSError:
            return ""
        for device in devices:
            if not (device / "driver").exists():
                continue
            try:
                names.append((device / "name").read_text().strip())
            except OSError:
                continue
    return ", ".join(name for name in names if name)


def name_for(claim, table=None, of_node=None):
    """A claimed pin's name, best first: the pinmux group (SPI0 MOSI), the owner's alias (SERIAL1), then the owner."""
    group = claim.get("group") or ""
    found = re.fullmatch(r"rm-io\d+-(.+)$", group)
    if found:
        parts = found.group(1).split("-")
        bus = parts[0].upper()
        signal = "-".join(parts[1:]).upper().replace("CSN", "CS")
        return f"{bus} {signal}".strip()
    alias = alias_for(claim.get("owner") or "", table, of_node=of_node)
    if alias:
        return alias.upper()
    return claim.get("owner") or ""


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
            found = re.fullmatch(r"([0-9a-fA-F]{7,8})(?:\.(?:gpio|pinctrl))?", part)
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


def board_kind(board, model_name):
    """lyra, pi or bpi from the board and the device tree's model; any other board stays as it is."""
    if board in ("pi", "lyra", "bpi"):
        return board
    low = model_name.lower()
    if re.search(r"\bbpi[-_ ]?m2[-_ ]?zero\b", low) or "banana pi m2 zero" in low:
        return "bpi"
    return board or "other"


def _port_key(name):
    """PA12, PA12-EINT12 and PL2-S-EINT2 all name the same header port."""
    return re.sub(r"[^A-Z0-9].*$", "", (name or "").strip().upper())


def sunxi_chips(lines):
    """The main PIO and R_PIO controllers in lines, by device address where sysfs has it, else by their size."""
    chips = {}
    for line in lines:
        chips.setdefault(line["chip"], []).append(line)
    if not chips:
        return None, None
    roles = {chip: SUNXI_GPIO_CHIP.get(_chip_address(chip)) for chip in chips}
    pio = next((chip for chip, role in roles.items() if role == "pio"), None)
    rpio = next((chip for chip, role in roles.items() if role == "rpio"), None)
    if pio is None:
        pio = max(chips, key=lambda chip: len(chips[chip]))
    if rpio is None:
        rpio = next((chip for chip in chips if chip != pio and len(chips[chip]) == 12), None)
    return pio, rpio


def sunxi_line(name, lines, pio, rpio):
    """The gpioinfo row for a port such as PA12 or PL2, by bank and line on the controller that holds it."""
    found = re.fullmatch(r"P([A-GL])(\d+)", name.upper())
    if not found:
        return {}
    bank, number = found.group(1), int(found.group(2))
    chip = rpio if bank == "L" else pio
    line = number if bank == "L" else (ord(bank) - ord("A")) * 32 + number
    return next((row for row in lines if row["chip"] == chip and row["line"] == line), {})


def board_lines(board, lines, model_name=""):
    """The header, as [{pin, type, name, gpio, chip, line, rmio, role, consumer, used}], for the Pi, the
    Lyra Zero W and the BPI-M2 Zero; every line, keyed by chip and number, for a board whose header is not known."""
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
                "function": found.get("function", ""),
                "group": found.get("group", ""),
            }
            row["type"] = _pin(row)
            out.append(row)
        return out
    if board == "bpi":
        pio, rpio = sunxi_chips(lines)
        by_name = {}
        for line in lines:
            key = _port_key(line["name"])
            if key and key != "UNNAMED" and key not in by_name:
                by_name[key] = line
        out = []
        for pin, (name, gpio, rmio, role) in sorted(BPI_M2_ZERO.items()):
            found = (by_name.get(name) or sunxi_line(name, lines, pio, rpio)) if gpio is not None else {}
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
                "function": found.get("function", ""),
                "group": found.get("group", ""),
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
                "function": found.get("function", ""),
                "group": found.get("group", ""),
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
            "function": line.get("function", ""),
            "group": line.get("group", ""),
        }
        for line in lines
    ]


def snapshot(board=None):
    """The board, every line gpioinfo reports and the header, for the page; the pin-mux claims on Rockchip too."""
    from irate_box.root import hardening

    model_name = model()
    board = board_kind(board or hardening.board(), model_name)
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
    header = board_lines(board, lines, model_name)
    table = aliases()
    for row in header:
        if row.get("used") and row.get("consumer"):
            row["label"] = name_for(
                {"owner": row["consumer"], "function": row.get("function", ""), "group": row.get("group", "")}, table
            )
            signal = (row["label"].split(" ", 1) + [""])[1] if row["label"] != row["consumer"] else ""
            row["owner"] = bus_devices(row["consumer"], signal)
    return {
        "board": board,
        "model": model_name,
        "lines": lines,
        "header": header,
        "note": note,
    }


def main(argv):
    """irate-box pinmap [BOARD]: the header and what claims each pin, from gpioinfo."""
    print(json.dumps(snapshot(argv[0] if argv else None), indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
