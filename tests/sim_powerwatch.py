# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The supply: undervoltage and a failing power, watched by crash watch (crashwatch.py; notes:
box-doctor-hardening-plan Part 3, step 2), offline: a Pi's rpi_volt and get_throttled, an INA219, no sensor, NUT's
on-battery and low-battery, the owner's log/sync/shut-down action, the events kept across boots, and Box doctor's
finding. python3 tests/sim_powerwatch.py"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="powerwatch-"))
etc, state, root, sysfs, proc = (T / n for n in ("etc", "state", "root", "sys", "proc"))
for p in (etc, state, root, sysfs, proc):
    p.mkdir(parents=True)
(proc / "sys/kernel/random").mkdir(parents=True)
(proc / "sys/kernel/random/boot_id").write_text("boot-one\n")
hwmon = sysfs / "class/hwmon"
hwmon.mkdir(parents=True)
rpi = hwmon / "hwmon0"
rpi.mkdir()
(rpi / "name").write_text("rpi_volt\n")
(rpi / "in_voltage0_lcrit_alarm").write_text("0\n")
fail_file = T / "power-fail"
os.environ.update(
    HUB_ETC_DIR=str(etc),
    HUB_STATE_DIR=str(state),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_SYSFS=str(sysfs),
    HUB_PROC=str(proc),
    HUB_POWER_FAIL=str(fail_file),
    HUB_KMSG=str(T / "kmsg"),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import crashwatch as cw
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


calls = []
nut = {"status": "OL"}
throttled = {"out": "throttled=0x0"}


def fake_run(*cmd, **kw):
    calls.append(cmd)
    out = ""
    if cmd[:2] == ("vcgencmd", "get_throttled"):
        out = throttled["out"]
    elif cmd[:2] == ("upsc", "-l"):
        out = "ups\n"
    elif cmd[:1] == ("upsc",):
        out = f"ups.status: {nut['status']}\n"
    return subprocess.CompletedProcess(cmd, 0, out, "")


cw._run = fake_run
cw.shutil.which = lambda name: "/usr/bin/upsc" if name == "upsc" else None

reading = cw.power_now()
check(
    "a Raspberry Pi's rpi_volt: measured, above its threshold",
    reading["measured"] and reading["source"] == "rpi_volt" and not reading["undervoltage"],
    reading,
)

(rpi / "in_voltage0_lcrit_alarm").write_text("1\n")
throttled["out"] = "throttled=0x50000"  # under-voltage now, and occurred since boot
reading = cw.power_now()
check(
    "  an alarm and the firmware's occurred bit: undervoltage now, and since boot",
    reading["undervoltage"] and reading["occurred"],
    reading,
)
now = time.time()
events = cw.power_tick(now)
check(
    "  the first look notes both, with the time and the source",
    [e["kind"] for e in events] == ["undervoltage", "undervoltage"]
    and all(e["at"] == now and e["source"] == "rpi_volt" for e in events),
    events,
)
check("  a second look with no change notes nothing", cw.power_tick(now + 30) == [])

(rpi / "in_voltage0_lcrit_alarm").write_text("0\n")
throttled["out"] = "throttled=0x0"
events = cw.power_tick(now + 60)
check("  the supply back: a recovery is noted", [e["kind"] for e in events] == ["recovered"], events)

ina = hwmon / "hwmon1"
ina.mkdir()
(ina / "name").write_text("ina219\n")
(ina / "in0_input").write_text("4600\n")
reading = cw.power_now()
check(
    "an INA219 below the threshold is the reading used",
    reading["source"] == "ina219" and reading["mv"] == 4600 and reading["undervoltage"],
    reading,
)
events = cw.power_tick(now + 90)
check("  and it is noted", [e["kind"] for e in events] == ["undervoltage"], events)

for d in (rpi, ina):
    (d / "name").unlink()
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
reading = cw.power_now()
cw.POWER.unlink(missing_ok=True)
check("no sensor: not measured", not reading["measured"] and reading["why"] == "no sensor", reading)
found = {f["id"]: f for f in health.check_power(now=now + 120)}
check(
    "  Box doctor: says so, and what would measure it",
    found["power"]["status"] == "ok" and "power.not-measured" in message_ids(found["power"]),
    found.get("power"),
)

nut["status"] = "OB LB"
check("NUT on battery with a low battery: the power is failing", cw.power_fail() == (True, "the battery is low"))
calls.clear()
fail_file.write_text("a power-fail line\n")
check("a power-fail hook file: the power is failing", cw.power_fail()[0])
synced = []
cw.os.sync = lambda: synced.append(True)
cw.set_option("power-action", "shutdown")
settings = cw.load_settings()
events = cw.power_tick(now + 150, settings)
check(
    "a new power failure: logged, synced, and the box shuts down",
    [e["kind"] for e in events] == ["power-fail"] and synced and ("systemctl", "poweroff") in calls,
    (events, synced, calls),
)
check("  noted once", cw.power_tick(now + 180, settings) == [])
found = {f["id"]: f for f in health.check_power(now=now + 200)}
check(
    "  Box doctor: a problem naming the times",
    found["power"]["status"] == "problem" and "power.failed" in message_ids(found["power"]),
    found.get("power"),
)

fail_file.unlink()
nut["status"] = "OL"
events = cw.power_tick(now + 210, settings)
check("the power back: a recovery is noted", [e["kind"] for e in events] == ["power-restored"], events)

kept = cw.power_events()
(proc / "sys/kernel/random/boot_id").write_text("boot-two\n")
check("the events stay across boots", cw.power_events() == kept and any(e["boot"] == "boot-one" for e in kept))

check(
    "the settings: undervoltage on or off, and the power action",
    cw.set_option("undervoltage", "off")["id"] == "crashwatch-set.undervoltage.off"
    and cw.load_settings()["undervoltage"] is False
    and cw.set_option("power-action", "sync")["id"] == "crashwatch-set.power-action.sync",
)
try:
    cw.set_option("power-action", "explode")
    refused = False
except (cw.Refused, ValueError):
    refused = True
check("  an unknown action is refused", refused)
groups = {g["key"]: g for g in health.setting_groups(cw.load_settings(), "hardware")}
check(
    "  the doctor's settings carry both",
    groups["undervoltage"]["current"] == "off"
    and [o["choice"] for o in groups["power-action"]["options"]]
    == ["crashwatch-power-action:log", "crashwatch-power-action:sync", "crashwatch-power-action:shutdown"],
    groups.keys(),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
