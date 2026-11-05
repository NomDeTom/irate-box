# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Meshtastic MUI on an SPI panel: choose the Lyra header pins, build the overlay, write the MUI config.

This is the modular "activate MUI" backend. It does not assume the SPI1/LCD wiring in lcdtouch.py:
the caller passes header-pin numbers for the bus signals and the plain GPIOs, and this module:

  * lists the valid choices from the board's current pinout and the kernel's rm_io*_spi* pinctrl labels;
  * rejects duplicates, power/ground pins, fixed-role pins, an occupied pin, and a display bus that
    collides with the radio's spidev;
  * writes one Armbian user overlay (irate-box-mui.dtbo) for the selected bus/CS and backlight;
  * writes /etc/hub/mui.yaml for the per-host meshtasticd build (display + LGFX XPT2046 touch);
  * writes and enables irate-box-mui.service, with Conflicts=meshtasticd.service;
  * records the selection and paths in /etc/hub/mui.json and reports pending/active state.

The overlay applies at the next restart, like every user overlay. No radio is configured here: the
default Lora section is SimRadio on spidev0.0, deliberately a different bus from the display, so a
radio can be fitted on SPI0 later. Stdlib only.
"""

import json
import os
import re
import subprocess  # Fixed argv, trusted executable, no shell. # nosec B404
import sys
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import hardware, pinmap, safeio

BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = ETC / "mui.json"
CONFIG = ETC / "mui.yaml"
FSDIR = ETC / "mui-fs"
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
UNIT = "irate-box-mui.service"
UNIT_PATH = UNIT_DIR / UNIT
OVERLAY = "irate-box-mui"
DTBO = BOOT / "overlay-user" / f"{OVERLAY}.dtbo"
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
SPI_SYS = Path(os.environ.get("HUB_SPI_SYS", "/sys/bus/spi/devices"))
BOOT_ID = Path(os.environ.get("HUB_BOOT_ID", "/proc/sys/kernel/random/boot_id"))
DEFAULT_BINARY = os.environ.get("HUB_MUI_BINARY", "/usr/local/bin/meshtasticd-per-host-spi")
DEFAULT_RADIO = "spidev0.0"
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 320
TOUCH_MODES = ("lgfx", "none")
SIGNALS = ("clk", "mosi", "miso", "csn0", "csn1")
ROLES = {
    "clk": "SPI clock",
    "mosi": "SPI MOSI",
    "miso": "SPI MISO",
    "display_cs": "display chip select",
    "touch_cs": "touch chip select",
    "dc": "display D/C",
    "rst": "display reset",
    "bl": "display backlight",
}
DEFAULTS = {
    "bus": 1,
    "clk": 27,
    "mosi": 29,
    "miso": 31,
    "display_cs": 35,
    "touch_cs": 37,
    "dc": 26,
    "rst": 28,
    "bl": 36,
    "touch": "lgfx",
    "radio_spidev": DEFAULT_RADIO,
    "radio_module": "sim",
    "replace_radio": False,
    "bus_frequency": 10000000,
}
# Fallback labels for a kernel without __symbols__: the two SPI controllers share these RM_IOs.
FALLBACK_LABELS = {
    (bus, signal, rmio): f"rm_io{rmio}_spi{bus}_{signal}"
    for bus in (0, 1)
    for signal, rmio in (("clk", 24), ("mosi", 25), ("miso", 26), ("csn0", 16), ("csn1", 28))
}
SPI_LABEL_RE = re.compile(r"^rm_io(\d+)_spi(\d+)_(clk|mosi|miso|csn\d+)$")
SPIDEV_RE = re.compile(r"^spidev(\d)\.(\d)$")
# A pin currently held by one of these is ours to replace. Radio, UART, pinhog and other owners are not.
OWN_TOKENS = ("irate-box", "portduino", "spidev", "lcd", "ads7846", "xpt2046", "ff130000.spi")
FOREIGN_TOKENS = ("ff120000.spi", "serial", "uart", "tty", "irate-box-pin")


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _run(*cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _systemctl(*args):
    return _run("systemctl", *args)


def _snapshot():
    """The live header, through pinmap; tests replace this with a fixed snapshot."""
    return pinmap.snapshot()


def supported():
    return "lyra zero" in pinmap.model().lower()


def _symbols():
    path = DT / "__symbols__"
    try:
        if path.is_dir():
            out = {}
            for entry in path.iterdir():
                if entry.name == "name":
                    continue
                try:
                    out[entry.name] = entry.read_bytes().rstrip(b"\0\n").decode(errors="replace")
                except OSError:
                    continue
            return out
        if path.is_file():
            return pinmap.parse_aliases(path.read_text(errors="replace"))
    except OSError:
        pass
    return {}


def _spi_table():
    """{(bus, signal, rmio): pinctrl label} from the live symbols; the known RM_IO fallback otherwise."""
    table = {}
    for label in _symbols():
        found = SPI_LABEL_RE.match(label)
        if found:
            table[(int(found.group(2)), found.group(3), int(found.group(1)))] = label
    return table or dict(FALLBACK_LABELS)


def _header():
    return pinmap.LYRA_ZERO


def _pin(pin):
    """A header row as a dict, or None. pinmap's tuples are (name, global GPIO, RM_IO, fixed role)."""
    if isinstance(pin, bool) or isinstance(pin, float):
        return None
    try:
        pin = int(pin)
    except (TypeError, ValueError):
        return None
    row = _header().get(pin)
    if row is None:
        return None
    name, gpio, rmio, fixed = row
    return {
        "pin": pin,
        "name": name,
        "gpio": gpio,
        "rmio": rmio,
        "fixed": fixed or "",
        "gpiochip": (gpio // 32) if gpio is not None else None,
        "line": (gpio % 32) if gpio is not None else None,
    }


def _spi_candidates(bus):
    table = _spi_table()
    out = {signal: [] for signal in SIGNALS}
    for pin, row in sorted(_header().items()):
        name, gpio, rmio, _fixed = row
        if gpio is None or rmio is None:
            continue
        for signal in SIGNALS:
            label = table.get((bus, signal, rmio))
            if label:
                item = {"pin": pin, "name": name, "gpio": gpio, "rmio": rmio, "signal": signal, "label": label}
                if signal.startswith("csn"):
                    item["cs"] = int(signal[3:])
                out[signal].append(item)
    return out


def _spi_candidate(bus, signal, pin):
    for item in _spi_candidates(bus)[signal]:
        if item["pin"] == pin:
            return item
    return None


def _cs_candidate(bus, pin):
    for signal in ("csn0", "csn1"):
        item = _spi_candidate(bus, signal, pin)
        if item:
            return item
    return None


def _int_value(value, field):
    """A pin or frequency from the request: a plain integer or a decimal string, never a bool or a float."""
    if isinstance(value, bool) or isinstance(value, float):
        raise Refused(M("mui.bad-selection", "{role} is not a pin number.", role=field))
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    raise Refused(M("mui.bad-selection", "{role} is not a pin number.", role=field))


def _normalize(selection):
    sel = dict(DEFAULTS)
    if isinstance(selection, dict):
        for key in DEFAULTS:
            if key in selection:
                sel[key] = selection[key]
    for key in ("bus", "clk", "mosi", "miso", "display_cs", "touch_cs", "dc", "rst", "bl", "bus_frequency"):
        if key in sel and sel[key] is not None:
            sel[key] = _int_value(sel[key], key)
    if type(sel.get("replace_radio")) is not bool:
        raise Refused(M("mui.replace-radio", "replace_radio must be true or false."))
    sel["touch"] = str(sel.get("touch") or "lgfx")
    sel["radio_spidev"] = str(sel.get("radio_spidev") or "")
    sel["radio_module"] = str(sel.get("radio_module") or "sim")
    # A path is root's own: never accept one from a hub request.
    sel["binary"] = DEFAULT_BINARY
    return sel


def _claim_rows():
    """The live header rows by pin. An unreadable snapshot is not a reason to block a saved choice."""
    try:
        return {
            row["pin"]: row
            for row in _snapshot().get("header") or []
            if isinstance(row, dict) and isinstance(row.get("pin"), int)
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _pin_status(pin, rows=None):
    """(usable, why, message_id, detail) for one pin: the one rule shared by validation and the page."""
    row = _pin(pin)
    if row is None:
        return False, "not in this board's header map", "mui.unknown-pin", None
    if row["gpio"] is None:
        return False, "power or ground", "mui.not-gpio", None
    if row["fixed"]:
        return False, f"fixed for {row['fixed']}", "mui.fixed-pin", row["fixed"]
    claim = (rows if rows is not None else _claim_rows()).get(pin) or {}
    if claim.get("used"):
        owner = str(claim.get("consumer") or claim.get("function") or "another function").lower()
        if any(token in owner for token in FOREIGN_TOKENS):
            return False, f"used by {owner}", "mui.pin-busy", owner
        if not any(token in owner for token in OWN_TOKENS):
            return False, f"used by {owner}", "mui.pin-busy", owner
        return True, f"in use by {owner}; this overlay replaces it", None, None
    return True, "free", None, None


def _pin_ok(pin, role, sel, check_claims=True):
    row = _pin(pin)
    ok, why, code, detail = _pin_status(pin)
    if not ok:
        if code == "mui.unknown-pin":
            raise Refused(M(code, "Pin {pin} is not in this board's header map.", pin=pin))
        if code == "mui.not-gpio":
            raise Refused(M(code, "Pin {pin} is power or ground, not a GPIO.", pin=pin))
        if code == "mui.fixed-pin":
            raise Refused(M(code, "Pin {pin} is fixed for {fixed}; choose a free GPIO.", pin=pin, fixed=detail))
        raise Refused(M("mui.pin-busy", "Pin {pin} is used by {owner}.", pin=pin, owner=detail))
    return row


def _validate(selection, check_binary=False):
    sel = _normalize(selection)
    if not supported():
        raise Refused(M("mui.unsupported", "MUI pin selection is for the Luckfox Lyra Zero W only."))
    if sel["bus"] not in (0, 1):
        raise Refused(M("mui.bus", "The display bus must be SPI0 or SPI1."))
    if sel["touch"] not in TOUCH_MODES:
        raise Refused(M("mui.touch-mode", "Touch must be LGFX XPT2046 or none."))
    roles = ("clk", "mosi", "miso", "display_cs", "dc", "rst", "bl") + (("touch_cs",) if sel["touch"] == "lgfx" else ())
    seen = {}
    for role in roles:
        pin = sel.get(role)
        if not isinstance(pin, int):
            raise Refused(M("mui.missing-pin", "Choose a pin for {role}.", role=role))
        if pin in seen:
            raise Refused(
                M("mui.duplicate-pin", "Pin {pin} is chosen for both {a} and {b}.", pin=pin, a=seen[pin], b=role)
            )
        seen[pin] = role
        _pin_ok(pin, role, sel)
    for signal, role in (("clk", "clk"), ("mosi", "mosi"), ("miso", "miso")):
        if _spi_candidate(sel["bus"], signal, sel[role]) is None:
            valid = [item["pin"] for item in _spi_candidates(sel["bus"])[signal]]
            raise Refused(
                M(
                    "mui.not-spi",
                    "Pin {pin} is not SPI{bus} {signal}; valid pins: {valid}.",
                    pin=sel[role],
                    bus=sel["bus"],
                    signal=signal.upper(),
                    valid=", ".join(str(pin) for pin in valid) or "none",
                )
            )
    display = _cs_candidate(sel["bus"], sel["display_cs"])
    if display is None:
        raise Refused(
            M("mui.not-display-cs", "Pin {pin} is not a SPI{bus} chip select.", pin=sel["display_cs"], bus=sel["bus"])
        )
    sel["_display_cs"] = display
    if sel["touch"] == "lgfx":
        touch = _cs_candidate(sel["bus"], sel["touch_cs"])
        if touch is None:
            raise Refused(
                M("mui.not-touch-cs", "Pin {pin} is not a SPI{bus} chip select.", pin=sel["touch_cs"], bus=sel["bus"])
            )
        if touch["cs"] == display["cs"]:
            raise Refused(M("mui.same-cs", "The display and touch cannot share one chip select."))
        sel["_touch_cs"] = touch
    if not (1000000 <= sel["bus_frequency"] <= 40000000):
        raise Refused(M("mui.bus-frequency", "The display bus frequency must be 1-40 MHz."))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", sel["radio_module"]):
        raise Refused(M("mui.radio-module", "The radio module name is not valid."))
    if sel["radio_module"] != "sim":
        raise Refused(M("mui.radio-module.sim", "MUI activation uses SimRadio. Configure a real radio separately."))
    if sel["radio_spidev"]:
        found = SPIDEV_RE.fullmatch(sel["radio_spidev"])
        if not found:
            raise Refused(M("mui.radio-spidev", "{name} is not a spidev path.", name=sel["radio_spidev"]))
        if int(found.group(1)) == sel["bus"]:
            raise Refused(
                M(
                    "mui.radio-bus",
                    "Display SPI{bus} is the radio's bus; choose the other SPI bus or a different radio spidev.",
                    bus=sel["bus"],
                )
            )
    if check_binary and not (Path(sel["binary"]).is_file() and os.access(sel["binary"], os.X_OK)):
        raise Refused(M("mui.no-binary", "The per-host meshtasticd is not at {path}.", path=sel["binary"]))
    return sel


def _gpio_cell(pin):
    row = _pin(pin)
    if row is None or row["gpio"] is None:
        raise Refused(M("mui.not-gpio", "Pin {pin} is power or ground, not a GPIO.", pin=pin))
    return f"<&gpio{row['gpiochip']} {row['line']} 0>"


def dts(selection):
    """The Armbian user overlay for this selection."""
    sel = _validate(selection)
    bus = sel["bus"]
    labels = [sel["_display_cs"]]
    for pin, signal in ((sel["clk"], "clk"), (sel["mosi"], "mosi"), (sel["miso"], "miso")):
        item = _spi_candidate(bus, signal, pin)
        labels.append(item)
    if sel["touch"] == "lgfx":
        labels.append(sel["_touch_cs"])
    pinctrl = " ".join(f"&{item['label']}" for item in labels)
    rmios = sorted({item["rmio"] for item in labels if item["rmio"] is not None})
    exclusive = ", ".join([f'"spi{bus}"'] + [f'"RM_IO{rmio}"' for rmio in rmios])
    nodes = []
    for item, kind in ((sel["_display_cs"], "display"),) + (
        ((sel["_touch_cs"], "touch"),) if sel["touch"] == "lgfx" else ()
    ):
        max_hz = sel["bus_frequency"] if kind == "display" else 2000000
        nodes.append(
            "\t\t\tspi@{cs} {{\n"
            '\t\t\t\tcompatible = "rockchip,spidev";\n'
            "\t\t\t\treg = <{cs}>;\n"
            "\t\t\t\tspi-max-frequency = <{hz}>;\n"
            '\t\t\t\tstatus = "okay";\n'
            "\t\t\t}};".format(cs=item["cs"], hz=max_hz)
        )
    num_cs = max(sel["_display_cs"]["cs"], sel["_touch_cs"]["cs"] if sel["touch"] == "lgfx" else 0) + 1
    title = f"Meshtastic MUI: SPI{bus} ILI9488 + XPT2046 on Luckfox Lyra Zero W"
    description = (
        f"SPI{bus} using {', '.join(item['label'] for item in labels)}; "
        f"D/C header {sel['dc']}, RST {sel['rst']}, BL {sel['bl']}."
    )
    return """/dts-v1/;
/plugin/;
/ {{
\tmetadata {{
\t\ttitle = "{title}";
\t\tcompatible = "rockchip,rk3506b-lyra-zero";
\t\tcategory = "spi";
\t\texclusive = {exclusive};
\t\tdescription = "{description}";
\t}};
\tfragment@0 {{
\t\ttarget = <&spi{bus}>;
\t\t__overlay__ {{
\t\t\tstatus = "okay";
\t\t\tnum-cs = <{num_cs}>;
\t\t\t#address-cells = <1>;
\t\t\t#size-cells = <0>;
\t\t\tpinctrl-names = "default";
\t\t\tpinctrl-0 = <{pinctrl}>;
{nodes}
\t\t}};
\t}};
\tfragment@1 {{
\t\ttarget-path = "/";
\t\t__overlay__ {{
\t\t\tlcd_backlight: lcd-backlight {{
\t\t\t\tcompatible = "gpio-leds";
\t\t\t\tbacklight {{
\t\t\t\t\tlabel = "lcd-backlight";
\t\t\t\t\tgpios = {bl};
\t\t\t\t\tdefault-state = "on";
\t\t\t\t}};
\t\t\t}};
\t\t}};
\t}};
}};
""".format(
        title=title,
        exclusive=exclusive,
        description=description,
        bus=bus,
        num_cs=num_cs,
        pinctrl=pinctrl,
        nodes="\n".join(nodes),
        bl=_gpio_cell(sel["bl"]),
    )


def _spidev(bus, index):
    return f"spidev{bus}.{index}"


def _map_lines(pin, indent):
    row = _pin(pin)
    pad = "  " * indent
    return f"{pad}pin: {pin}\n{pad}gpiochip: {row['gpiochip']}\n{pad}line: {row['line']}\n"


def config(selection):
    """The meshtasticd YAML for the per-host build, with the display and touch on the selected bus."""
    sel = _validate(selection)
    display = _spidev(sel["bus"], sel["_display_cs"]["cs"])
    out = []
    out.append("Lora:")
    out.append(f"  Module: {sel['radio_module']}")
    if sel["radio_spidev"]:
        out.append(f"  spidev: {sel['radio_spidev']}")
    out.extend(
        [
            "",
            "Display:",
            "  Panel: ILI9488",
            f"  spidev: {display}",
            f"  BusFrequency: {sel['bus_frequency']}",
            f"  Width: {DISPLAY_WIDTH}",
            f"  Height: {DISPLAY_HEIGHT}",
            "  Rotate: true",
            "  OffsetRotate: 1",
            "  Invert: true",
            "  RGBOrder: true",
            "  DC:",
        ]
    )
    out.append(_map_lines(sel["dc"], 2).rstrip())
    out.append("  Reset:")
    out.append(_map_lines(sel["rst"], 2).rstrip())
    if sel["touch"] == "lgfx":
        out.extend(
            [
                "",
                "Touchscreen:",
                "  Module: XPT2046",
                f"  spidev: {_spidev(sel['bus'], sel['_touch_cs']['cs'])}",
                "  BusFrequency: 1000000",
                "  Rotate: 0",
            ]
        )
    out.extend(["", "Config:", "  DisplayMode: COLOR", ""])
    return "\n".join(out)


def unit(selection):
    sel = _validate(selection)
    return f"""[Unit]
Description=Meshtastic MUI on the irate-box SPI display
After=network.target
Conflicts=meshtasticd.service

[Service]
Type=simple
ExecStart={sel["binary"]} -c {CONFIG} --fsdir {FSDIR} -p 4404 -v
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def _boot_id():
    return _read(BOOT_ID).strip()


def _devices(sel):
    display = sel.get("_display_cs") or _cs_candidate(sel["bus"], sel.get("display_cs"))
    touch = sel.get("_touch_cs") or (
        _cs_candidate(sel["bus"], sel.get("touch_cs")) if sel.get("touch") == "lgfx" else None
    )
    return {
        "display": bool(display and (SPI_SYS / f"spi{sel['bus']}.{display['cs']}").exists()),
        "touch": bool(touch and (SPI_SYS / f"spi{sel['bus']}.{touch['cs']}").exists())
        if sel.get("touch") == "lgfx"
        else True,
    }


def _state():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(selection, note="", meshtasticd_enabled=None):
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(
        STATE,
        json.dumps(
            {
                "boot_id": _boot_id(),
                "selection": {k: v for k, v in selection.items() if not k.startswith("_")},
                "overlay": OVERLAY,
                "config": str(CONFIG),
                "fsdir": str(FSDIR),
                "unit": UNIT,
                "binary": selection.get("binary", ""),
                "meshtasticd_enabled": bool(meshtasticd_enabled),
                "note": note,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n",
        0o600,
    )


def _status_for(sel):
    devices = _devices(sel) if supported() else {"display": False, "touch": False}
    enabled = OVERLAY in hardware.overlay_names(boot=BOOT)
    installed = DTBO.is_file()
    return {
        "supported": supported(),
        "selection": {k: v for k, v in sel.items() if not k.startswith("_")},
        "pins": [
            {"role": role, "pin": sel.get(role)}
            for role in ("clk", "mosi", "miso", "display_cs", "touch_cs", "dc", "rst", "bl")
            if isinstance(sel.get(role), int) and (role != "touch_cs" or sel.get("touch") == "lgfx")
        ],
        "overlay": OVERLAY,
        "dtbo": str(DTBO),
        "overlay_enabled": enabled,
        "overlay_installed": installed,
        "devices": devices,
        "spidev": {
            "display": f"spidev{sel['bus']}.{(sel.get('_display_cs') or _cs_candidate(sel['bus'], sel.get('display_cs')) or {'cs': '?'})['cs']}",
            "touch": (
                f"spidev{sel['bus']}.{(sel.get('_touch_cs') or _cs_candidate(sel['bus'], sel.get('touch_cs')) or {'cs': '?'})['cs']}"
                if sel.get("touch") == "lgfx"
                else ""
            ),
        },
        "active": bool(enabled and installed and devices["display"] and devices["touch"]),
        "pending": bool(enabled and installed and not (devices["display"] and devices["touch"])),
        "config": str(CONFIG),
        "config_written": CONFIG.is_file(),
        "fsdir": str(FSDIR),
        "unit": UNIT,
        "unit_written": UNIT_PATH.is_file(),
        "unit_enabled": _systemctl("is-enabled", UNIT).returncode == 0,
        "unit_active": _systemctl("is-active", UNIT).returncode == 0,
        "binary": sel.get("binary", ""),
        "binary_present": bool(sel.get("binary") and Path(sel["binary"]).is_file()),
        "meshtasticd_enabled": bool(_state().get("meshtasticd_enabled")),
        "radio": {
            "module": sel.get("radio_module", ""),
            "spidev": sel.get("radio_spidev", ""),
            "configured": _radio_configured(),
            "replaced_meshtasticd": bool(_state().get("meshtasticd_enabled")),
        },
    }


def status(selection=None):
    """The live overlay/unit state, and the saved selection (or one supplied by the caller)."""
    if selection is None:
        saved = _state().get("selection")
        sel = _normalize(saved if isinstance(saved, dict) else {})
    else:
        sel = _normalize(selection)
    return _status_for(sel)


def options():
    """Everything a pin picker needs: valid header pins, SPI signal candidates and the saved choice."""
    claims = _claim_rows()
    buses = []
    for bus in (0, 1):
        signals = _spi_candidates(bus)
        for rows in signals.values():
            for row in rows:
                ok, why, _code, _detail = _pin_status(row["pin"], claims)
                row["usable"] = ok
                row["why"] = why
        buses.append({"bus": bus, "signals": signals})
    gpios = []
    for pin, (name, gpio, rmio, fixed) in sorted(_header().items()):
        if gpio is None:
            continue
        ok, why, _code, _detail = _pin_status(pin, claims)
        gpios.append(
            {
                "pin": pin,
                "name": name,
                "gpio": gpio,
                "rmio": rmio,
                "fixed": fixed or "",
                "usable": ok,
                "why": why,
                "gpiochip": gpio // 32,
                "line": gpio % 32,
            }
        )
    saved = _state().get("selection")
    return {
        "supported": supported(),
        "board": pinmap.model(),
        "roles": ROLES,
        "buses": buses,
        "gpios": gpios,
        "touch_modes": list(TOUCH_MODES),
        "radio_spidevs": [DEFAULT_RADIO, "spidev1.0"],
        "defaults": dict(DEFAULTS),
        "selection": _normalize(saved if isinstance(saved, dict) else {}),
        "config": str(CONFIG),
        "unit": UNIT,
        "binary": DEFAULT_BINARY,
        "binary_present": Path(DEFAULT_BINARY).is_file(),
    }


def write_status():
    """Publish the current status where the pages read it."""
    state = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "mui.json"
    safeio.mkdir(state.parent, mode=0o755)
    safeio.write(state, json.dumps(status(), indent=1, sort_keys=True) + "\n", 0o644)
    write_options()
    return state


def write_options():
    """Publish the pin picker's data (candidates, defaults, saved choice) for the page."""
    path = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "mui-options.json"
    safeio.mkdir(path.parent, mode=0o755)
    safeio.write(path, json.dumps(options(), indent=1, sort_keys=True) + "\n", 0o644)
    return path


MESHTASTIC_CONFIG = Path(os.environ.get("HUB_MESHTASTIC_CONFIG", "/etc/meshtasticd/config.yaml"))
MESHTASTIC_CONFIG_DIR = Path(os.environ.get("HUB_MESHTASTIC_CONFIG_DIR", "/etc/meshtasticd/config.d"))


def _lora_module(text):
    """The last Lora.Module in one meshtasticd YAML file, or None when it has no Lora section."""
    section = re.search(r"(?ms)^Lora:\s*\n(.*?)(?=^\S|\Z)", text)
    if not section:
        return None
    found = re.findall(r"(?m)^\s+Module:\s*(\S+)", section.group(1))
    return found[-1] if found else None


def _radio_configured():
    """Whether meshtasticd has a non-SimRadio Lora module, reading config.yaml then config.d/*.yaml.

    The packaged daemon reads config.yaml first, then each config.d file in directory order; the last
    Lora.Module wins. An unreadable file counts as configured so the safe refusal holds.
    """
    files = [MESHTASTIC_CONFIG]
    try:
        files += sorted(MESHTASTIC_CONFIG_DIR.glob("*.yaml"))
    except OSError:
        return True
    module = None
    for path in files:
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError:
            return True
        found = _lora_module(text)
        if found is not None:
            module = found
    return bool(module and module.lower() != "sim")


def _overlay_error(exc):
    if exc.kind == "dtc-missing":
        raise Refused(M("mui.dtc-missing", "The device tree compiler (dtc) is not installed.")) from None
    raise Refused(M("mui.dtc-failed", "The device tree compiler (dtc) failed: {detail}", detail=exc.detail)) from None


def activate(selection):
    """Write the overlay, config, UI timeout and unit, then enable MUI; a restart applies the overlay."""
    sel = _validate(selection, check_binary=True)
    package_enabled = _systemctl("is-enabled", "meshtasticd.service").returncode == 0 or bool(
        _state().get("meshtasticd_enabled")
    )
    if package_enabled and _radio_configured() and not sel["replace_radio"]:
        raise Refused(
            M(
                "mui.radio-configured",
                "meshtasticd has a real radio configured. MUI uses SimRadio; pass replace_radio to replace it.",
            )
        )
    try:
        hardware.write_dtbo(OVERLAY, dts(sel), boot=BOOT)
    except hardware.OverlayError as exc:
        _overlay_error(exc)
    # Compilation succeeded: only now take the old display overlays out of the boot line.
    for old in ("luckfox-lyra-zero-w-spi1-lcd-touch", "luckfox-lyra-zero-w-spi0-lcd-touch"):
        hardware.set_overlay_names(remove=old, boot=BOOT)
    hardware.set_overlay_names(add=OVERLAY, boot=BOOT)
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(CONFIG, config(sel), 0o644)
    safeio.mkdir(FSDIR, mode=0o755)
    safeio.mkdir(FSDIR / "prefs", mode=0o755)
    # DeviceUIConfig: version=1, brightness=153, screen_timeout=0, alert_enabled=1, banner_enabled=1.
    # The default 30 s timeout blanks the panel because this setup has no LGFX backlight control.
    safeio.write(FSDIR / "prefs" / "uiconfig.proto", bytes.fromhex("0801109901180040014801"), 0o644)
    safeio.mkdir(UNIT_DIR, mode=0o755)
    safeio.write(UNIT_PATH, unit(sel), 0o644)
    _systemctl("daemon-reload")
    _systemctl("enable", UNIT)
    if package_enabled:
        _systemctl("disable", "--now", "meshtasticd.service")
    devices = _devices(sel)
    if devices["display"] and devices["touch"]:
        _systemctl("restart", UNIT)
    _save_state(sel, meshtasticd_enabled=package_enabled)
    radio = f"SimRadio on {sel['radio_spidev']}" if sel["radio_spidev"] else "SimRadio"
    if devices["display"] and devices["touch"]:
        return M("mui.active", "MUI is running with {radio}.", radio=radio)
    return M(
        "mui.saved",
        "MUI is saved with {radio}. Restart the box to apply the selected SPI pins.",
        radio=radio,
    )


def restart():
    """Schedule a reboot after answering, so the pending overlay is applied."""
    said = _run("systemd-run", "--on-active=2", "systemctl", "reboot")
    if said.returncode:
        detail = (said.stderr or said.stdout).strip()[:160] or "systemd-run failed"
        raise Refused(M("mui.restart-failed", "The box could not be restarted: {why}", why=detail))
    return M("mui.restart", "The box is restarting to apply the MUI overlay.")


def deactivate():
    """Stop and remove the MUI unit and the selected overlay; the config is kept for a later restart."""
    _systemctl("disable", "--now", UNIT)
    UNIT_PATH.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    try:
        hardware.remove_overlay(OVERLAY, boot=BOOT)
    except hardware.OverlayError as exc:
        _overlay_error(exc)
    saved = _state()
    selection = saved.get("selection")
    if isinstance(selection, dict):
        _save_state(
            _normalize(selection),
            note="deactivated",
            meshtasticd_enabled=saved.get("meshtasticd_enabled"),
        )
    if saved.get("meshtasticd_enabled"):
        _systemctl("enable", "--now", "meshtasticd.service")
    return M("mui.off", "MUI is stopped. The selected overlay is off after the next restart; the saved config is kept.")


def _parse(argv):
    if not argv:
        return {}
    if argv[0].startswith("{"):
        try:
            return json.loads(argv[0])
        except ValueError:
            raise Refused(M("mui.bad-json", "The selection is not valid JSON.")) from None
    out = {}
    for item in argv:
        key, sep, value = item.partition("=")
        if not sep:
            raise Refused(M("mui.bad-argument", "Use key=value for a pin selection, or one JSON object."))
        out[key] = value
    return out


def main(argv):
    if argv == ["status"]:
        print(json.dumps(status(), indent=1, sort_keys=True))
        return 0
    if argv == ["options"]:
        print(json.dumps(options(), indent=1, sort_keys=True))
        return 0
    if argv and argv[0] == "on":
        print(activate(_parse(argv[1:])))
        return 0
    if argv == ["off"]:
        print(deactivate())
        return 0
    print("Usage: irate-box mui status | options | on JSON | off", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
