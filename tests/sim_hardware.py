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
    "on": False,
    "pending": False,
    "device": "",
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
clock.status = lambda: {"service": "chrony", "daemon": "chrony", "pool": "pool.ntp.org", "local": ""}
rtc.status = lambda: None

pins = {
    "board": "lyra",
    "model": "Luckfox Lyra Zero W",
    "header": [{"pin": 1, "type": "3v3", "name": "3V3_OUT", "used": False}],
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
    and data["overlays"]["items"]["serial"]["enabled"] is False
    and data["overlays"]["items"]["watchdog"]["enabled"] is True
    and data["overlays"]["items"]["watchdog"]["active"] is True
    and data["overlays"]["items"]["lcd"]["on"] is True
    and data["overlays"]["items"]["lcd"]["pending"] is True,
    data["overlays"],
)
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
    and data["serial"] == {"supported": True, "on": False, "pending": False, "device": ""}
    and data["watchdog"] == {"supported": True, "mode": "hardware", "enabled": True, "pending": False}
    and data["errors"] == [],
    {k: data[k] for k in ("led", "radios", "clock", "rtc", "serial", "watchdog", "errors")},
)

# A board without a known header, and a module that cannot read its part: the rest still arrives.
hardening.board = lambda: "other"
pinmap.model = lambda: "Acme Widget"
rtc.status = lambda: {"config": {"chip": "ds3231"}}
data = hardware.status()
check(
    "an unknown board: its kind and that the header is not known",
    data["board"] == {"model": "Acme Widget", "kind": "other", "supported": False},
    data["board"],
)
check("  a clock module found", data["rtc"] == {"found": True})
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

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
