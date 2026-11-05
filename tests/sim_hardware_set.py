# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The one hardware action gate (root/hardware.py's set): the shared pattern, the hardware choices only, and
each one routed to the module that owns it; the Box doctor's health-fix reaches the same gate.
python3 tests/sim_hardware_set.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="hardware-set-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_SYSFS=str(T / "sys"))
(T / "state" / "control").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.msg import M, Refused
from irate_box.root import clock, crashwatch, hardware, health, led, recommended, rfkill, rtc, usbgadget

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(choice):
    try:
        hardware.set(choice)
    except Refused as exc:
        return exc.message["id"]
    return None


check("a choice outside the shared pattern is refused", refused("usb-gadget:maybe") == "hardware.set.unknown")
check(
    "a pattern choice that is not hardware is refused",
    refused("card-set:journal:memory") == "hardware.set.not-hardware",
)
check(
    "only the hardware kinds are handled",
    hardware.handles("usb-gadget:on")
    and hardware.handles("crashwatch-panic:on")
    and hardware.handles("led-show:alive")
    and hardware.handles("rtc-find")
    and not hardware.handles("card-set:journal:memory")
    and not hardware.handles("pins-overlay"),
)

calls = []


def said(where, *args):
    calls.append((where, args))
    return M(f"test.{where}", "done")


usbgadget.set_option = lambda value: said("usb", value)
usbgadget.set_drive = lambda value: said("drive", value)
usbgadget.set_drive_write = lambda value: said("drive-write", value)
led.set_option = lambda key, value: said("led", key, value)
led.flash = lambda name: said("flash", name)
rfkill.set_option = lambda radio, value: said("rfkill", radio, value)
rfkill.apply = lambda: [({"name": "wifi"}, None, None)]
clock.apply = lambda choice: said("clock", choice)
clock.undo = lambda: said("clock-undo")
crashwatch.set_option = lambda key, value: said("crashwatch", key, value)
recommended.record = lambda key, value: calls.append(("record", (key, value)))
health.set_clock = lambda epoch: said("clock-set", epoch)
rtc.find = lambda: {"found": [{"label": "DS3231", "bus": 1, "addr": "0x68"}], "buses": ["1"]}
rtc.look = lambda asked: said("rtc-look", asked)
rtc.setup = lambda chip, bus, addr: said("rtc-setup", chip, bus, addr)
rtc.save = lambda: said("rtc-save")
rtc.remove = lambda: said("rtc-remove")

for choice, expected in (
    ("usb-gadget:on", ("usb", ("on",))),
    ("usb-gadget-drive:off", ("drive", ("off",))),
    ("usb-gadget-drive-write:on", ("drive-write", ("on",))),
    ("led-show:alive", ("led", ("show", "alive"))),
    ("led-flash:ACT", ("flash", ("ACT",))),
    ("rfkill:wifi:off", ("rfkill", ("wifi", "off"))),
    ("rfkill-apply", ("rfkill-apply", ())),
    ("clock-sources:chrony:1:0:1:1:192.168.1.1", ("clock", ("clock-sources:chrony:1:0:1:1:192.168.1.1",))),
    ("clock-undo", ("clock-undo", ())),
    ("clock-set:1791580000", ("clock-set", (1791580000,))),
    ("crashwatch-panic:on", ("crashwatch", ("panic", "on"))),
    ("rtc-find", ("rtc-find", ())),
    ("rtc-setup:ds3231:1:0x68", ("rtc-setup", ("ds3231", 1, "0x68"))),
    ("rtc-save", ("rtc-save", ())),
    ("rtc-remove", ("rtc-remove", ())),
):
    calls.clear()
    reply = hardware.set(choice)
    ok = True
    if expected[0] == "rfkill-apply":
        ok = ok and reply["id"] == "repair.rfkill.done"
    elif expected[0] == "rtc-find":
        ok = ok and reply["id"] == "repair.rtc.found"
    else:
        ok = ok and calls[0] == expected
    check(f"{choice}: routed to its module", ok, (reply, calls))

calls.clear()
reply = hardware.set("crashwatch-panic:on")
check(
    "the owner's own choice is recorded as theirs",
    ("record", ("crashwatch-panic", "on")) in calls,
    calls,
)

# The Box doctor's alias goes through the same gate.
real_set, real_handles = hardware.set, hardware.handles
hardware.set = lambda choice: said("via-health", choice)
hardware.handles = lambda choice: choice.startswith("usb-gadget")
check(
    "health.fix hands a hardware choice to hardware.set",
    health.fix("usb-gadget:off").get("id") == "test.via-health" and calls[-1] == ("via-health", ("usb-gadget:off",)),
    calls[-1:],
)
calls.clear()
check(
    "  and leaves a choice that is not hardware to itself",
    not hardware.handles("card-set:journal:memory"),
)
hardware.set, hardware.handles = real_set, real_handles

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
