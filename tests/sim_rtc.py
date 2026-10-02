# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""rtc.py against simulated chips (HUB_RTC_FAKE), with systemctl, timedatectl and date stood in
for; then the doctor's set_clock. Changes nothing on the machine. python3 tests/sim_rtc.py"""
import json, os, sys, time, calendar, tempfile, subprocess
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="rtcsim-"))
for d in ("etc", "state/control", "run", "units", "bin"):
    (T / d).mkdir(parents=True)
# Stand-ins: systemctl does nothing; timedatectl says what NTP file says; date records.
(T / "bin/systemctl").write_text("#!/bin/sh\necho \"$*\" >> %s/systemctl.log\nexit 0\n" % T)
(T / "bin/timedatectl").write_text("#!/bin/sh\ncat %s/ntp 2>/dev/null || echo no\n" % T)
(T / "bin/date").write_text("#!/bin/sh\necho \"$*\" >> %s/date.log\nexit 0\n" % T)
for f in ("systemctl", "timedatectl", "date"):
    os.chmod(T / "bin" / f, 0o755)
os.environ.update(PATH=f"{T}/bin:" + os.environ["PATH"], HUB_RTC_FAKE=str(T / "fake.json"), HUB_ETC_DIR=str(T / "etc"),
                  HUB_STATE_DIR=str(T / "state"), HUB_RUN_DIR=str(T / "run"), HUB_UNIT_DIR=str(T / "units"))
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
from irate_box.root import rtc  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond

WHEN = calendar.timegm((2026, 10, 2, 8, 30, 15, 0, 0, 0))  # Friday
def regs(n=256): return [0] * n
ds = regs(); ds[0:7] = [0x15, 0x30, 0x08, 0x06, 0x02, 0x10, 0x26]; ds[0x0E] = 0x1C; ds[0x11] = 0x19; ds[0x12] = 0x40
rv = regs(); rv[0:7] = [0x15, 0x30, 0x08, 0x20, 0x02, 0x10, 0x26]
rx = regs(); rx[0x10:0x17] = [0x15, 0x30, 0x08, 0x20, 0x02, 0x10, 0x26]; rx[0x1F] = 0x20  # CHGEN on, INIEN off
pcf = regs(); pcf[2:9] = [0x95, 0x30, 0x08, 0x02, 0x05, 0x10, 0x26]  # VL set (bit 7 of seconds)
mpu = regs(); mpu[0x75] = 0x68
both = regs(); both[0:7] = [0x15, 0x30, 0x08, 0x20, 0x02, 0x10, 0x26]; both[0x10:0x17] = [0x15, 0x30, 0x08, 0x20, 0x02, 0x10, 0x26]
def fake(buses, drivers=()):
    (T / "fake.json").write_text(json.dumps({"buses": buses, "busy": ["2:0x14"], "drivers": list(drivers)}))

# 1. find
fake({"1": {"0x68": ds, "0x32": rv}, "2": {"0x32": rx, "0x51": pcf}, "3": {"0x68": mpu}})
f = rtc.find()
got = sorted((x["bus"], x["addr"], x["chip"], x["ambiguous"]) for x in f["found"])
check("find: DS3231, RV-8803, RX8130, PCF8563; the MPU-6050 left out",
      got == [(1, "0x32", "rv8803", False), (1, "0x68", "ds3231", False), (2, "0x32", "rx8130", False), (2, "0x51", "pcf8563", False)], got)
check("find: all supported (own driver)", all(x["supported"] and not x["kernel"] for x in f["found"]))
fake({"4": {"0x32": both}})
f = rtc.find()
check("find: a 0x32 pattern that fits both is offered as both", sorted(x["chip"] for x in f["found"]) == ["rv8803", "rx8130"]
      and all(x["ambiguous"] for x in f["found"]), f["found"])

# 2. read
fake({"1": {"0x68": ds, "0x32": rv}, "2": {"0x32": rx, "0x51": pcf}})
for chip, bus, addr in (("ds3231", 1, 0x68), ("rv8803", 1, 0x32), ("rx8130", 2, 0x32)):
    d = rtc.open_i2c(bus, addr); e, why = rtc.read_user(chip, d)
    check(f"read {chip}: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(e)) if e else why}", e == WHEN and why is None, (e, why))
d = rtc.open_i2c(2, 0x51); e, why = rtc.read_user("pcf8563", d)
check(f"read pcf8563 with VL set: refused ({why})", why and "lost power" in why, why)
d = rtc.open_i2c(2, 0x32)
check("rx8130 notes: INIEN off and CHGEN on reported", any("INIEN" in n for n in rtc.notes("rx8130", d)) and any("CHGEN" in n for n in rtc.notes("rx8130", d)))

# 3. write, then read back; flags cleared; charging untouched
NEW = calendar.timegm((2031, 2, 28, 23, 59, 50, 0, 0, 0))
for chip, bus, addr in (("ds3231", 1, 0x68), ("rv8803", 1, 0x32), ("rx8130", 2, 0x32), ("pcf8563", 2, 0x51)):
    d = rtc.open_i2c(bus, addr)
    if chip == "ds3231": d.write(0x0F, [0x80])   # OSF set
    if chip == "rv8803": d.write(0x0E, [0x03])   # V1F|V2F
    if chip == "rx8130": d.write(0x1D, [0x02])   # VLF
    rtc.write_user(chip, d, NEW)
    e, why = rtc.read_user(chip, d)
    check(f"write+read {chip}", e == NEW and why is None, (e, NEW, why))
d = rtc.open_i2c(1, 0x32); check("rv8803: clock running again after the write (RESET clear)", d.read(0x0F, 1)[0] & 1 == 0)
d = rtc.open_i2c(2, 0x32); check("rx8130: CHGEN left as it was", d.read(0x1F, 1)[0] & 0x20 == 0x20)
check("rx8130 weekday is one-hot (Friday 2031-02-28 → bit 5)", rtc.open_i2c(2, 0x32).read(0x13, 1)[0] == 0x20)

# 4. prepare: battery switch on, charging untouched; DS3231 EOSC cleared
d = rtc.open_i2c(2, 0x32); done = rtc.prepare_user("rx8130", d)
check("prepare rx8130: INIEN on, CHGEN kept", d.read(0x1F, 1)[0] == 0x30 and done, (hex(d.read(0x1F, 1)[0]), done))
d = rtc.open_i2c(1, 0x68); d.write(0x0E, [0x9C]); rtc.prepare_user("ds3231", d)
check("prepare ds3231: EOSC cleared", d.read(0x0E, 1)[0] == 0x1C)

# 5. kernel driver choice
fake({"2": {"0x51": pcf}}, drivers=["rtc-hym8563"])
k = rtc.kernel_driver("pcf8563")
check("kernel: a PCF8563 goes to the built-in rtc-hym8563 (as on the Lyra)", k == {"kind": "builtin", "name": "hym8563", "module": "rtc_hym8563"}, k)
check("kernel: no DS3231 driver → own driver", rtc.kernel_driver("ds3231") is None)

# 6. setup (own driver), boot, save, remove
fake({"1": {"0x68": ds}})
d = rtc.open_i2c(1, 0x68); d.write(0x0F, [0]); rtc.write_user("ds3231", d, int(time.time()) - 3600)  # an hour slow
(T / "ntp").write_text("no\n")
msg = rtc.setup("ds3231", 1, "0x68")
print("   setup:", msg)
check("setup: config and units written", (T / "etc/rtc.json").exists() and (T / "units/irate-box-rtc.service").exists())
check("setup: untrusted clock, valid module → the system clock is set from the module (as at boot)", "System clock set from the module" in msg, msg)
unit = (T / "units/irate-box-rtc.service").read_text()
check("unit: before fake-hwclock, no default dependencies", "Before=fake-hwclock-load.service" in unit and "DefaultDependencies=no" in unit)
check("unit: runs the module through the launcher", f"ExecStart={REPO}/irate-box rtc boot" in unit, unit)
(T / "date.log").unlink(missing_ok=True)
print("   boot:", rtc.boot())
dl = (T / "date.log").read_text() if (T / "date.log").exists() else ""
check("boot: system clock set from the module", "-u -s @" in dl, dl)
check("save: refused while the clock is not trusted", "left alone" in rtc.save())
(T / "ntp").write_text("yes\n")
print("   save:", rtc.save())
e, _ = rtc.read_user("ds3231", rtc.open_i2c(1, 0x68))
check("save with network time: module now matches", abs(e - time.time()) < 3, e - time.time())
(T / "ntp").write_text("no\n")
# a module behind the floor (a time the box certainly reached) is not used
os.utime(T / "state/control/rtc-status.json")
cl = T / "state/clock.json"; cl.write_text("{}"); os.utime(cl, (time.time() + 86400 * 30, time.time() + 86400 * 30))
(T / "date.log").unlink(missing_ok=True)
r = rtc.boot(); print("   boot behind floor:", r)
check("boot: a module earlier than the box has been is not used", "not used" in r and not (T / "date.log").exists(), r)
cl.unlink()
st = rtc.status()
check("status: reading and drift", st["epoch"] and st["drift"] is not None and st["units"], st)
print("   remove:", rtc.remove())
check("remove: config and units gone", not (T / "etc/rtc.json").exists() and not (T / "units/irate-box-rtc.service").exists())

# 7. auto
fake({"1": {"0x68": ds}})
print("   auto (one module):", rtc.auto())
check("auto: one module → set up", (T / "etc/rtc.json").exists())
rtc.remove()
fake({"4": {"0x32": both}})
r = rtc.auto(); print("   auto (ambiguous):", r)
check("auto: two possibilities → nothing set up, owner asked", not (T / "etc/rtc.json").exists() and "choose" in r)

# 8. The doctor: the browser's time sets the system clock, marks it trusted, and writes the module
from irate_box.root import health  # noqa: E402
fake({"1": {"0x68": ds}})
rtc.setup("ds3231", 1, "0x68")
(T / "date.log").unlink(missing_ok=True)
target = int(time.time()) + 7200
msg = health.set_clock(target); print("   set_clock:", msg)
check("set_clock: date called with the browser's time", f"@{target}" in (T / "date.log").read_text())
check("set_clock: clock marked trusted for this boot", rtc.TRUSTED.exists())
e, _ = rtc.read_user("ds3231", rtc.open_i2c(1, 0x68))
check("set_clock: the module written too", "clock module is set too" in msg and abs(e - time.time()) < 3, (msg, e - time.time()))
check("save: allowed now (set by the owner)", "set by the owner" in rtc.save())
try:
    health.set_clock(1600000000); check("set_clock refuses a time before what the box has written", False)
except ValueError as exc:
    check(f"set_clock refuses an implausible time ({exc})", True)
f = health.check_module(); print("   doctor:", [(x["status"], x["detail"][:70]) for x in f])
check("doctor: module ok", f and f[0]["status"] == "ok")
rtc.remove(); rtc.FOUND.unlink(missing_ok=True)
fake({"1": {"0x68": ds}, "4": {"0x32": both}})
rtc.find()
f = health.check_module()
check("doctor: three set-up offers after a search (one DS3231, the ambiguous pair)",
      sorted(a["choice"] for x in f for a in x["actions"]) == ["rtc-setup:ds3231:1:0x68", "rtc-setup:rv8803:4:0x32", "rtc-setup:rx8130:4:0x32"],
      [a["choice"] for x in f for a in x["actions"]])
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
