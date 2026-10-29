# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The supply: undervoltage and a failing power, watched by crash watch (crashwatch.py; notes:
box-doctor-hardening-plan Part 3, step 2), offline: a Pi's rpi_volt and get_throttled, an INA2xx's bus channel, no
sensor, NUT on battery and low battery, the hook file, the owner's log/sync/shut-down action, the shutdown owner's
deferral, the threshold setting, the events across boots, and Box doctor's finding. python3 tests/sim_powerwatch.py"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="powerwatch-"))
etc, state, root, sysfs, proc, run = (T / n for n in ("etc", "state", "root", "sys", "proc", "run"))
for p in (etc, state, root, sysfs, proc, run):
    p.mkdir(parents=True)
(proc / "sys/kernel/random").mkdir(parents=True)
(proc / "sys/kernel/random/boot_id").write_text("boot-one\n")
hwmon = sysfs / "class/hwmon"
hwmon.mkdir(parents=True)
rpi = hwmon / "hwmon0"
rpi.mkdir()
(rpi / "name").write_text("rpi_volt\n")
(rpi / "in0_lcrit_alarm").write_text("0\n")
fail_file = T / "power-fail"
os.environ.update(
    HUB_ETC_DIR=str(etc),
    HUB_STATE_DIR=str(state),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_RUN_DIR=str(run),
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
active = {"nut": False, "apcupsd": False}


def fake_run(*cmd, **kw):
    calls.append(cmd)
    out = ""
    if cmd[:2] == ("vcgencmd", "get_throttled"):
        out = throttled["out"]
    elif cmd[:2] == ("upsc", "-l"):
        out = "ups\n"
    elif cmd[:1] == ("upsc",):
        out = f"ups.status: {nut['status']}\n"
    elif cmd[:2] == ("systemctl", "is-active"):
        unit = cmd[-1]
        yes = (active["nut"] and unit in ("nut-monitor.service", "upsmon.service")) or (
            active["apcupsd"] and unit == "apcupsd.service"
        )
        return subprocess.CompletedProcess(cmd, 0 if yes else 3, "", "")
    return subprocess.CompletedProcess(cmd, 0, out, "")


cw._run = fake_run
cw.shutil.which = lambda name: "/usr/bin/upsc" if name == "upsc" else None


def reset():
    for f in (cw.POWER, cw.POWER_STATE):
        f.unlink(missing_ok=True)
    cw._OWNER.update(at=0.0, name="")


reading = cw.power_now()
check(
    "a Raspberry Pi's rpi_volt: measured, above its threshold",
    reading["measured"] and reading["source"] == "rpi_volt" and not reading["undervoltage"],
    reading,
)
found = {f["id"]: f for f in health.check_power(now=time.time())}
check(
    "  a sensor that only says low or not low: no invented voltage",
    found["power"]["status"] == "ok" and "power.ok-rpi" in message_ids(found["power"]),
    found.get("power"),
)

(rpi / "in0_lcrit_alarm").write_text("1\n")
throttled["out"] = "throttled=0x50000"  # under-voltage now, and occurred since boot
now = time.time()
events = cw.power_tick(now)
check(
    "one sag on a Pi is one event, not two",
    [e["kind"] for e in events] == ["undervoltage"] and events[0]["source"] == "rpi_volt",
    events,
)
check("  a second look with no change notes nothing", cw.power_tick(now + 30) == [])
(rpi / "in0_lcrit_alarm").write_text("0\n")
throttled["out"] = "throttled=0x0"
events = cw.power_tick(now + 60)
check("  the supply back: a recovery is noted", [e["kind"] for e in events] == ["recovered"], events)

# The INA2xx's in0 is the shunt; the bus is in1.
ina = hwmon / "hwmon1"
ina.mkdir()
(ina / "name").write_text("ina219\n")
(ina / "in0_input").write_text("20\n")  # the shunt: 20 mV
(ina / "in1_input").write_text("5000\n")  # the bus: 5 V
reset()
reading = cw.power_now()
check(
    "an INA219 reads its bus (in1), not its shunt (in0)",
    reading["source"] == "ina219" and reading["mv"] == 5000 and not reading["undervoltage"],
    reading,
)
(ina / "in1_input").write_text("4600\n")
reading = cw.power_now()
check("  and a low bus is the undervoltage", reading["mv"] == 4600 and reading["undervoltage"], reading)
events = cw.power_tick(now + 90)
check("  and it is noted", [e["kind"] for e in events] == ["undervoltage"], events)

# The owner may set the low point for the rail.
cw.set_option("undervoltage-mv", "3000")
(ina / "in1_input").write_text("3100\n")
check("the low point is the owner's: 3.1 V is fine on a 3.3 V rail", not cw.power_now(3000)["undervoltage"])
(ina / "in1_input").write_text("2900\n")
check("  and 2.9 V is low", cw.power_now(3000)["undervoltage"])
cw.set_option("undervoltage-mv", "4630")

# The state is in RAM and written only when it changes.
writes = []
real_write = cw._write


def counting(p, d):
    if Path(p) == cw.POWER_STATE:
        writes.append(Path(p))
    return real_write(p, d)


cw._write = counting
reset()
cw.power_tick(now + 120)
first = len(writes)
cw.power_tick(now + 150)
check("a look with no change writes no state", first == 1 and len(writes) == 1, writes)
cw._write = real_write

# No sensor, then the setting off.
for d in (rpi, ina):
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
reading = cw.power_now()
reset()
check("no sensor: not measured", not reading["measured"] and reading["why"] == "no sensor", reading)
found = {f["id"]: f for f in health.check_power(now=now + 200)}
check(
    "  Box doctor: says so, and what would measure it",
    found["power"]["status"] == "ok" and "power.not-measured" in message_ids(found["power"]),
    found.get("power"),
)
cw.set_option("undervoltage", "off")
reset()
found = {f["id"]: f for f in health.check_power(now=now + 210)}
check("switched off: no supply reading and no undervoltage finding", "power.off" in message_ids(found["power"]))
cw.set_option("undervoltage", "on")

# NUT: on battery is not a reason to shut down; a low battery is.
nut["status"] = "OB"
check("NUT on battery: failing, but not low", cw.power_fail() == (True, "the box is on battery", False))
cw.set_option("power-action", "shutdown")
settings = cw.load_settings()
reset()
calls.clear()
events = cw.power_tick(now + 220, settings)
check(
    "  on battery alone: logged and synced, never powered off",
    [e["kind"] for e in events] == ["power-fail"] and ("systemctl", "poweroff") not in calls and cw.os.sync,
    calls,
)
calls.clear()
check("  and noted once", cw.power_tick(now + 240, settings) == [])

# A low battery, and no shutdown owner: the box powers off.
nut["status"] = "OB LB"
check("NUT with a low battery: failing and low", cw.power_fail() == (True, "the battery is low", True))
reset()
calls.clear()
cw.power_tick(now + 250, settings)
check("  a low battery: the box shuts down", ("systemctl", "poweroff") in calls, calls)

# A shutdown owner is asked, never raced.
active["nut"] = True
reset()
calls.clear()
cw.power_tick(now + 260, settings)
check(
    "NUT's upsmon manages the shutdown: upsmon -c fsd, not a second poweroff",
    ("upsmon", "-c", "fsd") in calls and ("systemctl", "poweroff") not in calls,
    calls,
)
found = {f["id"]: f for f in health.check_power(now=now + 270)}
check("  Box doctor says who shuts it down", "power.manager" in message_ids(found["power"]), found.get("power"))
active["nut"] = False
active["apcupsd"] = True
reset()
calls.clear()
cw.power_tick(now + 280, settings)
check(
    "apcupsd manages the shutdown: no poweroff and no upsmon",
    ("systemctl", "poweroff") not in calls and ("upsmon", "-c", "fsd") not in calls,
    calls,
)
active["apcupsd"] = False
cw._OWNER.update(at=0.0, name="")

# The hook file: its words say low (a shutdown) or not.
fail_file.write_text("low: the mains is gone\n")
check("a hook file marked low: failing and low", cw.power_fail() == (True, "low: the mains is gone", True))
fail_file.write_text("on battery\n")
check("  a hook file not marked low: failing, not low", cw.power_fail()[2] is False)
fail_file.unlink()
nut["status"] = "OL"
reset()
cw.power_tick(now + 290, settings)
check("the power back: a recovery is noted", [e["kind"] for e in cw.power_tick(now + 300, settings)] == [])

# Events stay across boots.
cw.POWER.write_text(json.dumps([{"at": now, "boot": "boot-one", "kind": "power-fail", "text": "x"}]))
(proc / "sys/kernel/random/boot_id").write_text("boot-two\n")
check("the events stay across boots", cw.power_events()[0]["boot"] == "boot-one")

check(
    "the settings: undervoltage on or off, its low point, and the power action",
    cw.set_option("undervoltage", "off")["id"] == "crashwatch-set.undervoltage.off"
    and cw.set_option("power-action", "sync")["id"] == "crashwatch-set.power-action.sync"
    and cw.set_option("undervoltage-mv", "11000")["id"] == "crashwatch-set.undervoltage-mv",
)
try:
    cw.set_option("power-action", "explode")
    refused = False
except (cw.Refused, ValueError):
    refused = True
check("  an unknown action is refused", refused)
groups = {g["key"]: g for g in health.setting_groups(cw.load_settings(), "hardware")}
check(
    "  the doctor's settings carry all three",
    groups["undervoltage"]["current"] == "off"
    and groups["undervoltage-mv"]["current"] == "11000"
    and [o["choice"] for o in groups["power-action"]["options"]]
    == ["crashwatch-power-action:log", "crashwatch-power-action:sync", "crashwatch-power-action:shutdown"],
    groups.keys(),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
