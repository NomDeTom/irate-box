# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hardware status (root/hardware.py), offline: every section built from a stand-in module, the one
document written, an unsupported board, the overlay and display states, the USB gadget, and one part
that cannot be read named in errors while the others stay. No hardware is touched.
python3 tests/sim_hardware.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="hardware-"))
for d in ("boot", "state/control", "dt", "spi"):
    (T / d).mkdir(parents=True, exist_ok=True)
(T / "boot" / "armbianEnv.txt").write_text(
    "verbosity=1\nuser_overlays=irate-box-watchdog luckfox-lyra-zero-w-spi1-lcd-touch\n"
)
os.environ.update(
    HUB_BOOT_DIR=str(T / "boot"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_BOOT_ID=str(T / "boot_id"),
)
(T / "boot_id").write_text("boot-one\n")
sys.path.insert(0, str(REPO))
from irate_box.root import (
    clock,
    crashwatch,
    hardening,
    hardware,
    lcdtouch,
    led,
    ownserial,
    pinmap,
    rfkill,
    rtc,
    usbgadget,
)

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


# Stand-ins: the sections read only these status functions, so no /sys, /proc or device is touched.
pinmap.model = lambda: "Luckfox Lyra Zero W"
hardening.board = lambda: "lyra"
lcdtouch.status = lambda: {
    "supported": True,
    "on": True,
    "enabled": True,
    "installed": True,
    "active": True,
    "pending": True,
    "display_on": True,
}
ownserial.status = lambda: {
    "board": "lyra",
    "supported": True,
    "on": True,
    "pending": False,
    "device": "/dev/ttyS1",
    "warning": "",
}
crashwatch.status = lambda: {
    "settings": {"watchdog": True},
    "watchdog_device": "hardware",
    "watchdog_pending": False,
}
usbgadget.status = lambda: {
    "peripheral": True,
    "settings": {"on": True, "drive": True, "drive_write": False},
    "drive": {"on": True},
    "up": True,
    "pending": False,
}
led.status = lambda: {"settings": {}, "found": ["ACT"], "led": "ACT", "show": "alive", "off": False}
rfkill.status = lambda: {"settings": {"wifi": "on", "bluetooth": "leave"}}
clock.load = lambda: {"service": "chrony", "pool": "pool.ntp.org", "local": ""}
rtc.load_config = lambda: None

pins = {
    "board": "lyra",
    "model": "Luckfox Lyra Zero W",
    "header": [
        {"pin": 1, "type": "3v3", "name": "3V3_OUT", "used": False},
        {"pin": 8, "type": "gpio", "name": "GPIO0_C6", "used": True, "consumer": "serial1", "function": "uart"},
        {"pin": 10, "type": "gpio", "name": "GPIO0_C7", "used": False},
        {"pin": 26, "type": "gpio", "name": "GPIO0_B1", "used": False},
        {"pin": 28, "type": "gpio", "name": "GPIO1_D3", "used": True, "consumer": "irate-box-lcd"},
        {"pin": 27, "type": "gpio", "name": "GPIO1_B1", "used": True, "consumer": "spidev", "function": "spi1"},
        {"pin": 35, "type": "gpio", "name": "GPIO0_C0", "used": True, "consumer": "other-driver", "function": "spi1"},
    ],
    "lines": [{"chip": "gpiochip0", "line": 6, "used": True, "consumer": "ff120000.spi"}],
    "note": "",
}
(T / "state" / "control" / "pins.json").write_text(json.dumps(pins))

data = hardware.write_status()
doc = json.loads(hardware.STATUS.read_text())
check(
    "the one document is written where the hub reads it",
    hardware.STATUS == T / "state" / "control" / "hardware.json" and doc == data,
)
check(
    "the board: the model, the kind and that its header is known",
    data["board"] == {"model": "Luckfox Lyra Zero W", "kind": "lyra", "supported": True},
    data["board"],
)
check("  with a boot id and a time", data["boot_id"] == "boot-one" and isinstance(data["at"], float))
check("the pins are the last read, not a new gpioinfo run", data["pins"] == pins, data["pins"])
check(
    "the overlays: the boot line, the serial, the watchdog and the LCD",
    data["overlays"]["user_overlays"] == ["irate-box-watchdog", "luckfox-lyra-zero-w-spi1-lcd-touch"]
    and data["overlays"]["items"]["serial"]["enabled"] is True
    and data["overlays"]["items"]["watchdog"]["enabled"] is True
    and data["overlays"]["items"]["watchdog"]["active"] is True
    and data["overlays"]["items"]["lcd"]["on"] is True
    and data["overlays"]["items"]["lcd"]["pending"] is True,
    data["overlays"],
)
bypin = {item["pin"]: item for item in data["assignments"]}
check(
    "assignments: the display's pins stay assigned while D/C and RST are released",
    bypin[26]["role"] == "Display D/C"
    and bypin[26]["state"] == "idle"
    and bypin[28]["state"] == "active"
    and bypin[28]["holding"] is True
    and bypin[28]["active_owner"] == "irate-box-lcd"
    and bypin[27]["state"] == "active"
    and bypin[27]["owner"] == "display"
    and bypin[27]["active_owner"] == "spidev"
    and bypin[27]["holding"] is False
    and bypin[35]["state"] == "conflict"
    and bypin[35]["source"] == "lcdtouch",
    [bypin.get(n) for n in (26, 28, 27, 35)],
)
check(
    "  own serial: its TX taken and active, RX assigned but free",
    bypin[8]["role"] == "UART TX" and bypin[8]["state"] == "active" and bypin[10]["state"] == "idle",
    [bypin.get(n) for n in (8, 10)],
)

# MUI's pins, as the lyra-tft read them with MUI running (2026-11-11): the SPI1 lines by the controller's address,
# D/C, reset and backlight by meshtasticd (portduino). The fixed SPI1 overlay is not in the boot files.
from irate_box.root import mui

real_lcd_status, real_claims = lcdtouch.status, mui.claims
lcdtouch.status = lambda: {"supported": True, "on": False, "enabled": False, "installed": True, "active": False}
mui.claims = lambda: [
    {"pin": 27, "role": "clk", "name": "SPI1 CLK", "bus": 1},
    {"pin": 26, "role": "dc", "name": "Display D/C", "bus": 1},
    {"pin": 36, "role": "bl", "name": "Backlight", "bus": 1},
    {"pin": 37, "role": "touch_cs", "name": "Touch CS", "bus": 1},
]
pins_mui = dict(pins)
pins_mui["header"] = [
    {
        "pin": 27,
        "type": "gpio",
        "name": "GPIO1_B1",
        "used": True,
        "consumer": "ff130000.spi",
        "group": "rm-io24-spi1-clk",
    },
    {"pin": 26, "type": "gpio", "name": "GPIO0_B1", "used": True, "consumer": "portduino"},
    {"pin": 36, "type": "gpio", "name": "GPIO1_D1", "used": True, "consumer": "portduino"},
    {"pin": 37, "type": "gpio", "name": "GPIO1_C3", "used": True, "consumer": "other-driver"},
]
(T / "state" / "control" / "pins.json").write_text(json.dumps(pins_mui))
mui_pins = {item["pin"]: item for item in hardware.status()["assignments"]}
check(
    "assignments: the fixed SPI1 overlay out of the boot files assigns nothing",
    not [item for item in mui_pins.values() if item["source"] == "lcdtouch"],
    mui_pins,
)
check(
    "  MUI's pins are MUI's, by name, whether the SPI controller or meshtasticd holds them",
    mui_pins[27]["state"] == "active"
    and mui_pins[27]["by"] == "MUI"
    and mui_pins[27]["role"] == "SPI1 CLK"
    and mui_pins[26]["state"] == "active"
    and mui_pins[26]["holding"] is True
    and mui_pins[26]["role"] == "Display D/C"
    and mui_pins[36]["state"] == "active"
    and mui_pins[36]["role"] == "Backlight",
    [mui_pins.get(n) for n in (27, 26, 36)],
)
check(
    "  a pin MUI wants that another driver holds is a conflict", mui_pins[37]["state"] == "conflict", mui_pins.get(37)
)
lcdtouch.status, mui.claims = real_lcd_status, real_claims
(T / "state" / "control" / "pins.json").write_text(json.dumps(pins))
check(
    "the display: on, waiting for a restart, its pixels and driver",
    data["display"] == {"supported": True, "on": True, "pending": True, "pixels": [480, 320], "driver": "ili9488"},
    data["display"],
)
check(
    "the USB gadget: on and up, the drive on and read-only, its address",
    data["usb_gadget"]
    == {
        "supported": True,
        "on": True,
        "up": True,
        "drive": True,
        "drive_write": False,
        "pending": False,
        "address": "192.168.7.1",
    },
    data["usb_gadget"],
)
check(
    "the light, the radios, the clock, the module, the serial pins and the watchdog",
    data["led"] == {"supported": True, "on": True, "show": "alive", "led": "ACT"}
    and data["radios"] == {"wifi": "on", "bluetooth": "leave"}
    and data["clock"] == {"source": "chrony", "servers": "pool.ntp.org"}
    and data["rtc"] == {"found": False}
    and data["serial"] == {"supported": True, "on": True, "pending": False, "device": "/dev/ttyS1"}
    and data["watchdog"] == {"supported": True, "mode": "hardware", "enabled": True, "pending": False}
    and data["errors"] == [],
    {k: data[k] for k in ("led", "radios", "clock", "rtc", "serial", "watchdog", "errors")},
)

# A board without a known header, and a module that cannot read its part: the rest still arrives.
hardening.board = lambda: "other"
pinmap.model = lambda: "Acme Widget"
lcdtouch.status = lambda: {"supported": False}
rtc.load_config = lambda: {"chip": "ds3231"}
data = hardware.status()
check(
    "an unknown board: its kind and that the header is not known",
    data["board"] == {"model": "Acme Widget", "kind": "other", "supported": False},
    data["board"],
)
check("  a clock module found", data["rtc"] == {"found": True})
check(
    "  a board without the panel: no display mode and no display pins",
    data["display"] == {"supported": False, "on": False, "pending": False, "pixels": [], "driver": ""}
    and not [item for item in data["assignments"] if item["source"] == "lcdtouch"],
    (data["display"], data["assignments"]),
)
real_led = led.status
led.status = lambda: (_ for _ in ()).throw(OSError("no LED class"))
data = hardware.status()
check(
    "one part that fails is named in errors, and the others are still there",
    data["errors"]
    and data["errors"][0]["section"] == "led"
    and "no LED class" in data["errors"][0]["error"]
    and data["usb_gadget"]["supported"] is True,
    data["errors"],
)
check("  the failed section says so too", data["led"] == {"supported": False, "error": "OSError: no LED class"})
led.status = real_led
data = hardware.status()
check("  and it is back when the module answers again", data["errors"] == [] and data["led"]["supported"] is True)

# The published answer says when it could not read everything.
M = hardware.publish()
check("publish: one message, and the file written again", M["id"] == "control.hardware.published", M)
real_status = hardware.status
hardware.status = lambda: (_ for _ in ()).throw(AssertionError("read again"))
check("this boot's file is not read again on a later helper start", hardware.write_status_on_boot() is None)
hardware.status = real_status
# An update without a restart: the file is this boot's, but the old code wrote it (no digest, or another one).
stale = json.loads(hardware.STATUS.read_text())
stale.pop("code")
hardware.STATUS.write_text(json.dumps(stale))
check("  a file the code before an update wrote is read again", hardware.write_status_on_boot() is not None)
check("  and carries this code's digest", json.loads(hardware.STATUS.read_text())["code"] == hardware.code_digest())

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
