#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A clock module on I2C: find it, set it up, and keep the box's time with it.

These boards keep no time while switched off (the Lyra has no RTC at all), so an offline box
boots as far behind as it was off (health.py, the clock check). A battery-backed clock module
on I2C fixes that: DS3231, RV-8803, RX8130 and their relatives.

Nothing here changes the device tree, /boot or the kernel. Two ways to drive a module:

  kernel     the running kernel has a driver for the chip (built in, or a module): the
             module is declared to it at boot through sysfs (new_device), and read and written
             as /dev/rtcN. Undone by removing the setup.
  userspace  it has none (the Lyra's vendor kernel only has rtc-hym8563): this file reads
             and writes the chip's registers through /dev/i2c-N itself. No packages needed.
             DS3231, DS1307, MCP7940, RV-8803, RX8130 and PCF8563/HYM8563 only; the register
             maps follow the kernel's drivers (rtc-ds1307.c, rtc-rv8803.c, rtc-pcf8563.c).

At boot (irate-box-rtc.service, before fake-hwclock restores its saved time) the system clock
is set from the module, unless the module lost its time (its own flag says so) or reads
earlier than a time the box has certainly reached. Hourly and at shutdown the module is set
from the system clock, but only when that clock is trustworthy: set from the network, or set by
the owner (health.py's "Set the clock from this browser"). A clock that came from the module
is not written back to it.

Battery charging is never switched on. On the RX8130 the backup switch (INIEN) is turned on, as
the kernel driver does; charging (CHGEN) is left as it is and reported, since charging a coin
cell is a hazard.

    sudo ./irate-box rtc find              look for a module on every I2C bus (reads only)
    sudo ./irate-box rtc setup CHIP BUS ADDR   e.g. setup ds3231 2 0x68
    sudo ./irate-box rtc status            the module's time against the system's
    sudo ./irate-box rtc save              set the module from the system clock (if trusted)
    sudo ./irate-box rtc remove            undo the setup
    sudo ./irate-box rtc auto              for install.sh: set up the one module found, if one
    ./irate-box rtc boot                   (the unit's own)

For tests, HUB_RTC_FAKE=/path/regs.json stands in for the I2C buses. Stdlib only.
"""

import calendar
import fcntl
import json
import os
import re
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

from irate_box.msg import M, render, sentences
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONFIG = ETC / "rtc.json"
FOUND = STATE / "control" / "rtc-find.json"
STATUS = STATE / "control" / "rtc-status.json"
RUN = Path(os.environ.get("HUB_RUN_DIR", "/run/irate-box"))
TRUSTED = RUN / "clock-trusted"  # health.py writes it when the owner sets the clock
FROM_RTC = RUN / "clock-from-rtc"  # this boot's clock came from the module
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
CODE = Path(__file__).resolve().parents[2]  # irate_box/root/rtc.py → the checkout, whose launcher the units run
FAKE = os.environ.get("HUB_RTC_FAKE")
I2C_SLAVE = 0x0703
RTC_SET_TIME = 0x4024700A
ADDRESSES = (0x68, 0x6F, 0x51, 0x52, 0x32)

# chip: (label, the kernel drivers that take it as [(device name, module)], own driver?)
CHIPS = {
    "ds3231": ("DS3231 (temperature-compensated)", [("ds3231", "rtc_ds1307")], True),
    "ds1307": ("DS1307", [("ds1307", "rtc_ds1307")], True),
    "mcp7940x": ("MCP7940N", [("mcp7940x", "rtc_ds1307")], True),
    "rv8803": (
        "RV-8803 / RX-8900 (Epson RX-8803, RX-8804)",
        [("rv8803", "rtc_rv8803"), ("rx8900", "rtc_rv8803")],
        True,
    ),
    "rx8130": ("RX8130CE", [("rx8130", "rtc_ds1307")], True),  # rtc-ds1307 knows it in newer kernels only
    "pcf8563": ("PCF8563 / HYM8563", [("pcf8563", "rtc_pcf8563"), ("hym8563", "rtc_hym8563")], True),
    "pcf8523": ("PCF8523", [("pcf8523", "rtc_pcf8523")], False),
    "pcf85063": ("PCF85063", [("pcf85063", "rtc_pcf85063")], False),
    "rv3028": ("RV-3028", [("rv3028", "rtc_rv3028")], False),
}


class RtcError(Exception):
    """What went wrong, as a message (msg.M): its English is the exception's text, .message the message itself."""

    def __init__(self, message):
        super().__init__(render(message))
        self.message = message


# --- I2C --------------------------------------------------------------------------------------------


class I2C:
    """Register access to one address on one bus: write the register number, then read on."""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        self.fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        try:
            fcntl.ioctl(self.fd, I2C_SLAVE, addr)  # EBUSY: a kernel driver has it
        except OSError:
            os.close(self.fd)
            raise

    def read(self, reg, n):
        os.write(self.fd, bytes([reg]))
        data = os.read(self.fd, n)
        if len(data) != n:
            raise OSError("short read")
        return data

    def write(self, reg, data):
        os.write(self.fd, bytes([reg]) + bytes(data))

    def close(self):
        os.close(self.fd)


class FakeI2C:
    """HUB_RTC_FAKE: {"buses": {"2": {"0x68": [256 register values], ...}}, "busy": ["2:0x14"]}."""

    def __init__(self, bus, addr):
        self.bus, self.addr = bus, addr
        data = json.loads(Path(FAKE).read_text())
        if f"{bus}:{addr:#04x}" in data.get("busy", []):
            raise OSError(16, "Device or resource busy")
        regs = data.get("buses", {}).get(str(bus), {}).get(f"{addr:#04x}")
        if regs is None:
            raise OSError(6, "No such device or address")  # nothing answers there

    def _load(self):
        return json.loads(Path(FAKE).read_text())

    def read(self, reg, n):
        regs = self._load()["buses"][str(self.bus)][f"{self.addr:#04x}"]
        return bytes(regs[(reg + i) % len(regs)] for i in range(n))

    def write(self, reg, data):
        d = self._load()
        regs = d["buses"][str(self.bus)][f"{self.addr:#04x}"]
        for i, b in enumerate(data):
            regs[(reg + i) % len(regs)] = b
        Path(FAKE).write_text(json.dumps(d))

    def close(self):
        pass


def open_i2c(bus, addr):
    return (FakeI2C if FAKE else I2C)(bus, addr)


def buses():
    if FAKE:
        return sorted(int(b) for b in json.loads(Path(FAKE).read_text()).get("buses", {}))
    return sorted(int(p.name.split("-")[1]) for p in Path("/dev").glob("i2c-*") if p.name.split("-")[1].isdigit())


# --- reading and writing the time ---------------------------------------------------------------------


def bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def tobcd(n):
    return ((n // 10) << 4) | (n % 10)


def bcd_ok(b, lo, hi):
    return (b & 0x0F) <= 9 and (b >> 4) <= 9 and lo <= bcd(b) <= hi


def onehot(b):
    return b in (1, 2, 4, 8, 16, 32, 64)


# Where each chip keeps the time: (offset of seconds, weekday style)
LAYOUT = {
    "ds3231": (0x00, "bcd1"),
    "ds1307": (0x00, "bcd1"),
    "mcp7940x": (0x00, "bcd1"),
    "rv8803": (0x00, "onehot"),
    "rx8130": (0x10, "onehot"),
    "pcf8563": (0x02, "bcd0"),
    "pcf8523": (0x03, "bcd0"),
    "pcf85063": (0x04, "bcd0"),
    "rv3028": (0x00, "bcd0"),
}


def _fields(chip, r):
    """The seven time registers in seconds, minutes, hours, day, weekday, month, year order."""
    if chip == "pcf8563" or chip in ("pcf8523", "pcf85063"):
        sec, mn, hr, day, wd, mon, yr = r  # day comes before weekday on the NXP parts
    else:
        sec, mn, hr, wd, day, mon, yr = r
    return sec, mn, hr, day, wd, mon, yr


def decode(chip, r):
    """(epoch, None) or (None, why) from the seven time registers."""
    sec, mn, hr, day, wd, mon, yr = _fields(chip, r)
    sec &= 0x7F
    mn &= 0x7F
    if chip in ("ds3231", "ds1307", "mcp7940x") and hr & 0x40:
        h12 = bcd(hr & 0x1F) % 12
        hour = h12 + (12 if hr & 0x20 else 0)
    else:
        hour = bcd(hr & 0x3F)
    mon_v = bcd(mon & 0x1F)
    year = 2000 + bcd(yr)
    if not (
        bcd_ok(sec, 0, 59)
        and bcd_ok(mn, 0, 59)
        and 0 <= hour <= 23
        and bcd_ok(day & 0x3F, 1, 31)
        and 1 <= mon_v <= 12
        and bcd_ok(yr, 0, 99)
    ):
        return None, "the time registers hold no valid date"
    try:
        return calendar.timegm((year, mon_v, bcd(day & 0x3F), hour, bcd(mn), bcd(sec), 0, 0, 0)), None
    except (ValueError, OverflowError):
        return None, "the time registers hold no valid date"


def encode(chip, epoch):
    t = time.gmtime(epoch)
    wd_sun0 = (t.tm_wday + 1) % 7  # struct_time: Monday = 0; the chips count from Sunday
    style = LAYOUT[chip][1]
    wd = 1 << wd_sun0 if style == "onehot" else tobcd(wd_sun0 + 1) if style == "bcd1" else wd_sun0
    sec, mn, hr, day = tobcd(t.tm_sec), tobcd(t.tm_min), tobcd(t.tm_hour), tobcd(t.tm_mday)
    mon, yr = tobcd(t.tm_mon), tobcd(t.tm_year % 100)
    if chip in ("pcf8563", "pcf8523", "pcf85063"):
        return [sec, mn, hr, day, wd, mon, yr]
    return [sec, mn, hr, wd, day, mon, yr]


def integrity(chip, dev):
    """None if the chip says its time is good, else why not (it lost power, or never started)."""
    if chip == "ds3231":
        if dev.read(0x0F, 1)[0] & 0x80:
            return "its oscillator-stop flag (OSF) is set, so its time is not valid. Causes include a power loss, or a flat or missing backup battery"
    elif chip == "ds1307":
        if dev.read(0x00, 1)[0] & 0x80:
            return "its oscillator-halt flag (CH) is set, so its time is not valid. The time was never set, or the module lost power"
    elif chip == "mcp7940x":
        r = dev.read(0x00, 4)
        if not r[0] & 0x80 or not r[3] & 0x20:
            return "its oscillator is not running (ST/OSCRUN), so its time is not valid. The time was never set, or the module lost power"
    elif chip == "rv8803":
        if dev.read(0x0E, 1)[0] & 0x02:
            return "its low-voltage flag (V2F) is set, so its time is not valid"
    elif chip == "rx8130":
        if dev.read(0x1D, 1)[0] & 0x02:
            return "its low-voltage flag (VLF) is set, so its time is not valid"
    elif chip in ("pcf8563", "pcf8523", "pcf85063"):
        if dev.read(LAYOUT[chip][0], 1)[0] & 0x80:
            return "its low-voltage or oscillator-stop flag is set, so its time is not valid"
    elif chip == "rv3028":
        if dev.read(0x0E, 1)[0] & 0x01:
            return "its power-on-reset flag (PORF) is set, so its time is not valid"
    return None


def notes(chip, dev):
    """Things worth saying about the module's set-up (battery switch, charging)."""
    out = []
    if chip == "rx8130":
        c1 = dev.read(0x1F, 1)[0]
        if not c1 & 0x10:
            out.append(
                "the battery backup switch (INIEN) is off. The module loses the time at every power cut until it is set up"
            )
        if c1 & 0x20:
            out.append(
                "backup charging (CHGEN) is enabled. Charging suits a supercapacitor or a rechargeable cell. "
                "Charging a CR2032 coin cell is a hazard. The box never changes charging"
            )
    elif chip == "ds3231" and dev.read(0x0E, 1)[0] & 0x80:
        out.append(
            "its oscillator is set to stop on battery (EOSC). The module loses the time at every power cut until it is set up"
        )
    return out


def read_user(chip, dev):
    off = LAYOUT[chip][0]
    why = integrity(chip, dev)
    r = dev.read(off, 7)
    if chip == "rv8803" and (r[0] & 0x7F) == 0x59:  # read again across a minute change, as the driver does
        r2 = dev.read(off, 7)
        r = r2 if (r2[0] & 0x7F) != 0x59 else r
    epoch, bad = decode(chip, r)
    return epoch, why or bad


def write_user(chip, dev, epoch):
    off = LAYOUT[chip][0]
    regs = encode(chip, epoch)
    if chip == "mcp7940x":
        regs[0] |= 0x80  # ST: start the oscillator
        regs[3] |= 0x08  # VBATEN: run from the battery when the power goes
    if chip == "rv8803":
        ctrl = dev.read(0x0F, 1)[0]
        dev.write(0x0F, [ctrl | 0x01])  # stop the clock while it is set (RESET)
        dev.write(off, regs)
        dev.write(0x0F, [ctrl & ~0x01])
        dev.write(0x0E, [dev.read(0x0E, 1)[0] & ~0x03])  # clear V1F/V2F: the time is good now
        return
    dev.write(off, regs)
    if chip == "ds3231":
        dev.write(0x0F, [dev.read(0x0F, 1)[0] & ~0x80])  # clear OSF
    elif chip == "rx8130":
        dev.write(0x1D, [dev.read(0x1D, 1)[0] & ~0x02])  # clear VLF
    elif chip == "pcf8563":
        pass  # writing the seconds register cleared VL (bit 7 written as 0)


def prepare_user(chip, dev):
    """One-off at setup: let the module keep time on its battery. Charging is never touched."""
    done = []
    if chip == "rx8130":
        c1 = dev.read(0x1F, 1)[0]
        if not c1 & 0x10:
            dev.write(0x1F, [c1 | 0x10])
            done.append("inien")
    elif chip == "ds3231":
        c = dev.read(0x0E, 1)[0]
        if c & 0x80:
            dev.write(0x0E, [c & ~0x80])
            done.append("eosc")
    return done


# --- finding a module -----------------------------------------------------------------------------------


def _time_ok(regs, off, wd_style):
    sec, mn, hr = regs[off] & 0x7F, regs[off + 1] & 0x7F, regs[off + 2] & 0x3F
    if wd_style.startswith("nxp"):
        day, wd, mon, yr = regs[off + 3] & 0x3F, regs[off + 4] & 0x07, regs[off + 5] & 0x1F, regs[off + 6]
        wd_ok = wd <= 6
    else:
        wd, day, mon, yr = regs[off + 3], regs[off + 4] & 0x3F, regs[off + 5] & 0x1F, regs[off + 6]
        wd_ok = (
            onehot(wd & 0x7F)
            if wd_style == "onehot"
            else (wd & 0x07) <= 6
            if wd_style == "wd0"
            else 1 <= (wd & 0x07) <= 7
        )
    return (
        bcd_ok(sec, 0, 59)
        and bcd_ok(mn, 0, 59)
        and (bcd_ok(hr, 0, 23) or hr & 0x40)
        and bcd_ok(day, 1, 31)
        and bcd_ok(mon, 1, 12)
        and bcd_ok(yr, 0, 99)
        and wd_ok
    )


# What prepare_user did, for setup to say.
PREPARED = {
    "inien": M("rtc-module.prepared.inien", "The box enabled the module's battery backup (INIEN)."),
    "eosc": M("rtc-module.prepared.eosc", "The box set the module's oscillator to run on battery (EOSC cleared)."),
}


def _time_lost_ok(regs, off):
    """A clock's registers after a power loss: the seconds, minutes and hours still well-formed BCD and the weekday
    one-hot (or never set), but the date not valid (day 0, say). Only taken with the chip's own low-voltage flag."""
    sec, mn, hr, wd = regs[off] & 0x7F, regs[off + 1] & 0x7F, regs[off + 2] & 0x3F, regs[off + 3] & 0x7F
    return bcd_ok(sec, 0, 59) and bcd_ok(mn, 0, 59) and bcd_ok(hr, 0, 23) and (wd == 0 or onehot(wd))


def identify(addr, read):
    """Possible chips at addr, given read(reg, n). [] if nothing there looks like a clock."""
    try:
        r = read(0x00, 0x20)
    except OSError:
        return []
    if addr == 0x68:
        if _time_ok(r, 0x00, "bcd1"):
            # The DS3231 first: a temperature register in range and its unused bits clear. Asked
            # for 0x75, a DS3231 can answer anything, so the motion-sensor test comes after.
            temp, frac = struct.unpack("b", bytes([r[0x11]]))[0], r[0x12]
            if -40 <= temp <= 85 and frac & 0x3F == 0 and r[0x0F] & 0x70 == 0:
                return ["ds3231"]
        try:
            who = read(0x75, 1)[0]
        except OSError:
            who = None
        if who in (0x68, 0x70, 0x71, 0x73, 0x98, 0x19):
            return []  # an InvenSense motion sensor (MPU-6050/6500/9250, ICM-20xxx) answers WHO_AM_I
        if _time_ok(r, 0x00, "bcd1") and r[0x07] & 0x6C == 0:
            return ["ds1307"]
        if _time_ok(r, 0x03, "nxp"):
            return ["pcf8523"]
        return []
    if addr == 0x6F:
        return ["mcp7940x"] if _time_ok(r, 0x00, "bcd1") else []
    if addr == 0x51:
        if _time_ok(r, 0x02, "nxp"):
            return ["pcf8563"]
        if _time_ok(r, 0x04, "nxp"):
            return ["pcf85063"]
        return []
    if addr == 0x52:
        return ["rv3028"] if _time_ok(r, 0x00, "wd0") else []
    if addr == 0x32:
        found = []
        if _time_ok(r, 0x00, "onehot") and r[0x0E] & 0xC0 == 0:
            found.append("rv8803")
        elif r[0x0E] & 0x02 and r[0x0E] & 0xC0 == 0 and _time_lost_ok(r, 0x00):
            # RV-8803 / RX-8900 that lost power: its low-voltage flag (V2F/VLF) set and its date not valid. Found all the
            # same: setting it up sets its time.
            found.append("rv8803")
        if _time_ok(r, 0x10, "onehot"):
            found.append("rx8130")
        return found
    return []


def kernel_driver(chip):
    """{"kind": "builtin"|"module", "name", "module"} for a driver this kernel has for the chip,
    or None. Built in (or already loaded): /sys/bus/i2c/drivers/rtc-*. Loadable: the module's
    i2c alias. (Built-in drivers' aliases are not listed anywhere, hence sysfs.)"""
    if FAKE:
        drivers = set(json.loads(Path(FAKE).read_text()).get("drivers", []))
    else:
        drivers = {d.name for d in Path("/sys/bus/i2c/drivers").glob("*")}
    try:
        aliases = (Path("/lib/modules") / os.uname().release / "modules.alias").read_text()
    except OSError:
        aliases = ""
    for name, module in CHIPS[chip][1]:
        if module.replace("_", "-") in drivers:
            return {"kind": "builtin", "name": name, "module": module}
    for name, module in CHIPS[chip][1]:
        if re.search(rf"^alias i2c:{re.escape(name)} {module}$", aliases, re.M):
            return {"kind": "module", "name": name, "module": module}
    return None


def find():
    """Every I2C bus, every clock address: what answers, and what it looks like."""
    if not FAKE:
        subprocess.run(["modprobe", "i2c-dev"], capture_output=True)
    out = {"at": time.time(), "buses": buses(), "found": [], "busy": []}
    for bus in out["buses"]:
        for addr in ADDRESSES:
            try:
                dev = open_i2c(bus, addr)
            except OSError as exc:
                if exc.errno == 16:
                    drv = _bound_driver(bus, addr)
                    out["busy"].append({"bus": bus, "addr": f"{addr:#04x}", "driver": drv})
                continue
            try:
                chips = identify(addr, dev.read)
                lost = {}
                for chip in chips:
                    try:
                        lost[chip] = integrity(chip, dev)  # its time not valid: it lost power, or never started
                    except OSError:
                        lost[chip] = None
            finally:
                dev.close()
            for chip in chips:
                drv = kernel_driver(chip)
                out["found"].append(
                    {
                        "bus": bus,
                        "addr": f"{addr:#04x}",
                        "chip": chip,
                        "label": CHIPS[chip][0],
                        "ambiguous": len(chips) > 1,
                        "kernel": drv and f"{drv['module']} ({drv['kind']})",
                        "supported": bool(drv) or CHIPS[chip][2],
                        "time_lost": lost.get(chip),
                    }
                )
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(FOUND, json.dumps(out, indent=2))  # control/: never through a link
    os.chmod(FOUND, 0o644)
    return out


def _bound_driver(bus, addr):
    p = Path(f"/sys/bus/i2c/devices/{bus}-{addr:04x}")
    try:
        return (p / "name").read_text().strip()
    except OSError:
        return None


# --- the configured module ---------------------------------------------------------------------------------


def load_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return None


def kernel_rtc_dev(cfg):
    """/dev/rtcN of the configured module, declaring it to the kernel first if need be."""
    bus, addr = cfg["bus"], int(cfg["addr"], 16)
    node = Path(f"/sys/bus/i2c/devices/{bus}-{addr:04x}")
    if not node.exists():
        drv = cfg.get("driver") or {}
        if drv.get("kind") == "module":
            subprocess.run(["modprobe", drv["module"]], capture_output=True)
        try:
            Path(f"/sys/bus/i2c/devices/i2c-{bus}/new_device").write_text(f"{drv['name']} {addr:#04x}")
        except OSError as exc:
            raise RtcError(
                M("rtc-module.kernel-refused", "The kernel did not accept the module: {detail}.", detail=str(exc))
            )
    for _ in range(30):
        rtcs = list(node.glob("rtc/rtc*"))
        if rtcs:
            return Path("/dev") / rtcs[0].name
        time.sleep(0.1)
    raise RtcError(
        M(
            "rtc-module.no-kernel-clock",
            "The kernel driver did not register a clock for the module. "
            "Check that the module is wired to that bus and address.",
        )
    )


def read_module(cfg):
    """(epoch or None, why not, notes)."""
    chip = cfg["chip"]
    if cfg["mode"] == "kernel":
        dev = kernel_rtc_dev(cfg)
        try:
            return int((Path("/sys/class/rtc") / dev.name / "since_epoch").read_text()), None, []
        except OSError:
            return (
                None,
                "the kernel driver cannot read a valid time from it (its time was never set, or it lost power)",
                [],
            )
    try:
        dev = open_i2c(cfg["bus"], int(cfg["addr"], 16))
    except OSError as exc:
        raise RtcError(
            M(
                "rtc-module.unreachable",
                "The box cannot reach the module on bus {bus} at {addr}: {detail}.",
                bus=cfg["bus"],
                addr=cfg["addr"],
                detail=str(exc.strerror or exc),
            )
        )
    try:
        epoch, why = read_user(chip, dev)
        return epoch, why, notes(chip, dev)
    finally:
        dev.close()


def write_module(cfg, epoch):
    chip = cfg["chip"]
    if cfg["mode"] == "kernel":
        dev = kernel_rtc_dev(cfg)
        t = time.gmtime(epoch)
        tm = struct.pack(
            "9i",
            t.tm_sec,
            t.tm_min,
            t.tm_hour,
            t.tm_mday,
            t.tm_mon - 1,
            t.tm_year - 1900,
            (t.tm_wday + 1) % 7,
            t.tm_yday - 1,
            0,
        )
        with open(dev, "wb") as fh:
            fcntl.ioctl(fh, RTC_SET_TIME, tm)
        return
    dev = open_i2c(cfg["bus"], int(cfg["addr"], 16))
    try:
        write_user(chip, dev, epoch)
    finally:
        dev.close()


def ntp_synced():
    out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True, text=True)
    return out.stdout.strip() == "yes"


def root_mtime(path):
    """When root last wrote `path`, or None: a plain file owned by this user (root), not a link.
    The hub can touch its own files, or plant links and its own copies, to push the floor
    forward; only root's own marks count."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st.st_mtime if stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() else None


def floor_time():
    """The latest time the box has certainly reached: a module reading before it is wrong."""
    marks = [
        m
        for m in (
            root_mtime(p)
            for p in (
                Path("/etc/fake-hwclock.data"),
                STATE / "control" / "health.json",
                Path("/var/log/irate-box/install-state.json"),
            )
        )
        if m
    ]
    return max(marks) if marks else 0


def _status(data):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    data["at"] = time.time()
    safeio.write(STATUS, json.dumps(data, indent=2))
    os.chmod(STATUS, 0o644)


def set_system(epoch):
    r = subprocess.run(["date", "-u", "-s", f"@{int(epoch)}"], capture_output=True, text=True)
    if r.returncode:
        raise RtcError(M("rtc-module.date-failed", "The command date failed: {detail}", detail=r.stderr.strip()))


# What boot() did (its record's "boot"), as setup says it: boot's own words are a line in the journal.
BOOT_SAID = {
    "network time already set": M("rtc-module.set-up.network", "The network had already set the system clock."),
    "set from the module": M("rtc-module.set-up.set", "The box set the system clock from the module."),
    "not used": M("rtc-module.set-up.not-used", "The box did not use the module's time."),
}


def boot():
    cfg = load_config()
    if not cfg:
        return "no clock module is set up"
    epoch, why, _ = read_module(cfg)
    now = time.time()
    if epoch is None:
        _status({"boot": "not used", "why": why})
        return f"clock module not used: {why}"
    floor = floor_time()
    if epoch < floor - 120:
        why = (
            f"it reads {time.strftime('%Y-%m-%d %H:%M', time.gmtime(epoch))} UTC, earlier than the box has "
            "certainly been: its time is wrong (its time was never set, or it lost power)"
        )
        _status({"boot": "not used", "why": why})
        return f"clock module not used: {why}"
    if ntp_synced():
        _status({"boot": "network time already set", "drift": now - epoch})
        return "the network had already set the clock"
    if abs(epoch - now) > 2:
        set_system(epoch)
    RUN.mkdir(parents=True, exist_ok=True)
    FROM_RTC.write_text(str(int(epoch)))
    _status({"boot": "set from the module", "moved": epoch - now})
    return f"system clock set from the module ({epoch - now:+.0f} s)"


def chrony_reference():
    """chrony's reference now (its refid, e.g. "RTC", "GPS", or a server's address), or None without chrony."""
    try:
        out = subprocess.run(["chronyc", "-n", "tracking"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^Reference ID\s*:\s*\S+\s*\(([^)]*)\)", out, re.M)
    return m.group(1) if m else None


def trusted():
    """Is the system clock worth writing to the module? (network time, or the owner set it). Not while chrony's
    only reference is the module itself (the Clock page's source): that would write the module's own time back."""
    if ntp_synced() and chrony_reference() != "RTC":
        return "network time"
    if TRUSTED.exists():
        return "set by an administrator"
    return None


NO_MODULE = M("rtc-module.none", "No clock module is set up.")
# Why the system clock is worth writing to the module (trusted(), or the box setting its own clock).
SAVED = {
    "network time": M(
        "rtc-module.saved.network",
        "The box set the clock module from the system clock, in UTC. The system clock has network time.",
    ),
    "set by an administrator": M(
        "rtc-module.saved.administrator",
        "The box set the clock module from the system clock, in UTC. An administrator set the system clock.",
    ),
    "forced": M("rtc-module.saved", "The box set the clock module from the system clock, in UTC."),
}


def save(force=False):
    cfg = load_config()
    if not cfg:
        return NO_MODULE
    why = "forced" if force else trusted()
    if not why:
        return M(
            "rtc-module.not-saved",
            "The module did not change. The system clock is not known to be correct: it has no network "
            "time, and no administrator set it.",
        )
    write_module(cfg, time.time())
    _status({**(json.loads(STATUS.read_text()) if STATUS.exists() else {}), "saved": time.time(), "saved_because": why})
    return SAVED[why]


UNITS = {
    "irate-box-rtc.service": """[Unit]
Description=Irate-Box: set the system clock from the clock module (rtc.py), and save it at shutdown
DefaultDependencies=no
After=systemd-modules-load.service local-fs.target
Before=fake-hwclock-load.service time-sync.target sysinit.target shutdown.target
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=HUB_STATE_DIR={state} HUB_ETC_DIR={etc}
ExecStart={code}/irate-box rtc boot
ExecStop={code}/irate-box rtc save

[Install]
WantedBy=sysinit.target
""",
    "irate-box-rtc-save.service": """[Unit]
Description=Irate-Box: set the clock module from the system clock, if that is trustworthy (rtc.py)

[Service]
Type=oneshot
Environment=HUB_STATE_DIR={state} HUB_ETC_DIR={etc}
ExecStart={code}/irate-box rtc save
""",
    "irate-box-rtc-save.timer": """[Unit]
Description=Irate-Box: keep the clock module set, hourly

[Timer]
OnBootSec=15min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
""",
}


def write_units():
    for name, text in UNITS.items():
        (UNIT_DIR / name).write_text(text.format(state=STATE, etc=ETC, code=CODE))
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    subprocess.run(["systemctl", "enable", "irate-box-rtc.service", "irate-box-rtc-save.timer"], capture_output=True)
    subprocess.run(["systemctl", "start", "irate-box-rtc-save.timer"], capture_output=True)


def setup(chip, bus, addr):
    if chip not in CHIPS:
        raise RtcError(
            M(
                "rtc-module.unsupported",
                "{chip} is not a clock module that this box supports. It supports {chips}.",
                chip=chip,
                chips=list(CHIPS),
            )
        )
    a = int(addr, 16) if isinstance(addr, str) else addr
    # Only what the last search found: a request may not bind a driver at any bus and
    # address. The search's result is root's own file, read only if it still is.
    if a not in ADDRESSES:
        raise RtcError(
            M("rtc-module.bad-address", "{addr} is not an address that a clock module uses.", addr=f"{a:#04x}")
        )
    try:
        found = json.loads(safeio.read_own(FOUND)).get("found", [])
    except (OSError, ValueError):
        found = []
    if not any(f.get("chip") == chip and f.get("bus") == int(bus) and f.get("addr") == f"{a:#04x}" for f in found):
        raise RtcError(
            M(
                "rtc-module.not-found",
                "The last search did not find a {chip} on bus {bus} at {addr}. Search again first.",
                chip=CHIPS[chip][0],
                bus=bus,
                addr=f"{a:#04x}",
            )
        )
    drv = kernel_driver(chip)
    mode = "kernel" if drv else "userspace" if CHIPS[chip][2] else None
    if not mode:
        raise RtcError(
            M(
                "rtc-module.no-driver",
                "The {chip} needs a kernel driver that this kernel does not have: {drivers}.",
                chip=CHIPS[chip][0],
                drivers=[m for _, m in CHIPS[chip][1]],
            )
        )
    cfg = {
        "chip": chip,
        "bus": int(bus),
        "addr": f"{a:#04x}",
        "mode": mode,
        "driver": drv,
        "label": CHIPS[chip][0],
        "set_up": time.time(),
    }
    done = []
    if mode == "userspace":
        try:
            dev = open_i2c(cfg["bus"], a)
        except OSError as exc:
            raise RtcError(
                M(
                    "rtc-module.no-answer",
                    "Nothing answers on bus {bus} at {addr}: {detail}.",
                    bus=bus,
                    addr=f"{a:#04x}",
                    detail=str(exc.strerror or exc),
                )
            )
        try:
            if chip not in identify(a, dev.read):
                raise RtcError(
                    M(
                        "rtc-module.not-that-chip",
                        "The device on bus {bus} at {addr} does not look like a {chip}.",
                        bus=bus,
                        addr=f"{a:#04x}",
                        chip=CHIPS[chip][0],
                    )
                )
            done = prepare_user(chip, dev)
        finally:
            dev.close()
    ETC.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG, 0o644)
    if mode == "kernel":
        kernel_rtc_dev(cfg)  # declared now, so it can be read below; the unit does it at each boot
    write_units()
    epoch, why, _ = read_module(cfg)
    at = {"chip": CHIPS[chip][0], "bus": bus, "addr": f"{a:#04x}"}
    said = [
        M(
            "rtc-module.set-up.kernel",
            "The box set up the {chip} on bus {bus} at {addr}, through the kernel driver.",
            **at,
        )
        if mode == "kernel"
        else M(
            "rtc-module.set-up.own", "The box set up the {chip} on bus {bus} at {addr}, through its own driver.", **at
        )
    ]
    said += [PREPARED[d] for d in done]
    t = trusted()
    if t:
        write_module(cfg, time.time())
        said.append(
            M(
                "rtc-module.set-up.saved",
                "The box set the module from the system clock. The module now keeps the time while the box is off.",
            )
        )
    elif why:
        said.append(
            M(
                "rtc-module.set-up.no-time",
                "The module has no valid time yet ({why}). Set the box clock on Box, Clock. This sets the module too.",
                why=why,
            )
        )
    else:
        # The same rule as at boot: a module with a valid time beats a clock restored from a file.
        boot()
        said.append(
            BOOT_SAID.get(
                (json.loads(STATUS.read_text()) if STATUS.exists() else {}).get("boot"), BOOT_SAID["not used"]
            )
        )
    msg = sentences(*said)
    if not FAKE:
        subprocess.run(["systemctl", "start", "irate-box-rtc.service"], capture_output=True)  # records it as having run
    _status({**(json.loads(STATUS.read_text()) if STATUS.exists() else {}), "message": render(msg)})
    return msg


def remove():
    cfg = load_config()
    for unit in ("irate-box-rtc-save.timer", "irate-box-rtc.service"):
        subprocess.run(["systemctl", "disable", "--now", unit], capture_output=True)
    for name in UNITS:
        (UNIT_DIR / name).unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    if cfg and cfg.get("mode") == "kernel":
        try:
            Path(f"/sys/bus/i2c/devices/i2c-{cfg['bus']}/delete_device").write_text(cfg["addr"])
        except OSError:
            pass
    CONFIG.unlink(missing_ok=True)
    STATUS.unlink(missing_ok=True)
    return (
        M("rtc-module.removed", "The box no longer uses the {chip}. The module keeps its own time.", chip=cfg["label"])
        if cfg
        else M("rtc-module.removed.none", "No clock module was set up.")
    )


def status():
    """For the doctor: the configured module now, or None."""
    cfg = load_config()
    if not cfg:
        return None
    out = {"config": cfg, "units": {}, "last": None}
    for unit in ("irate-box-rtc.service", "irate-box-rtc-save.timer"):
        out["units"][unit] = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True
        ).stdout.strip()
    try:
        out["last"] = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        pass
    try:
        epoch, why, nts = read_module(cfg)
        out.update(epoch=epoch, why=why, notes=nts, drift=(time.time() - epoch) if epoch else None)
    except RtcError as exc:
        out.update(epoch=None, why=str(exc), notes=[], drift=None, unreachable=True)
    out["trusted"] = trusted()
    return out


def auto():
    """install.sh: refresh an existing set-up; otherwise set up the one module found, if one."""
    cfg = load_config()
    if cfg:
        write_units()
        return f"clock module: {cfg['label']} on bus {cfg['bus']} at {cfg['addr']} (kept)"
    if list(Path("/sys/class/rtc").glob("rtc*")) and not FAKE:
        return "clock: the kernel already has a hardware clock. The box does not set up a clock module."
    found = find()
    usable = [f for f in found["found"] if f["supported"]]
    if not found["found"]:
        return "clock module: none found on I2C" + ("" if found["buses"] else " (no I2C bus is enabled)")
    if len({(f["bus"], f["addr"]) for f in usable}) == 1 and len(usable) == 1:
        f = usable[0]
        return f"clock module: {setup(f['chip'], f['bus'], f['addr'])}"
    names = ", ".join(f"{f['label']} (bus {f['bus']}, {f['addr']})" for f in found["found"])
    return f"clock module: found {names}. Select which one to use on Box, Clock."


def main(argv):
    if os.geteuid() != 0 and not FAKE:
        sys.exit("run as root")
    try:
        if argv == ["find"]:
            f = find()
            for x in f["found"]:
                print(
                    f"bus {x['bus']} {x['addr']}: {x['label']}"
                    + (" (or the other chip at this address)" if x["ambiguous"] else "")
                    + (
                        f", kernel driver {x['kernel']}"
                        if x["kernel"]
                        else ", own driver"
                        if x["supported"]
                        else ", not supported here"
                    )
                )
            for b in f["busy"]:
                print(f"bus {b['bus']} {b['addr']}: in use by the kernel ({b['driver']})")
            if not f["found"]:
                print("no clock module found" + ("" if f["buses"] else " (no I2C bus enabled)"))
        elif len(argv) == 4 and argv[0] == "setup":
            print(setup(argv[1], argv[2], argv[3]))
        elif argv == ["status"]:
            print(json.dumps(status(), indent=2))
        elif argv == ["save"]:
            print(save())
        elif argv == ["boot"]:
            print(boot())
        elif argv == ["remove"]:
            print(remove())
        elif argv == ["auto"]:
            print(auto())
        else:
            sys.exit(__doc__.split("\n\n")[-2])
    except RtcError as exc:
        sys.exit(f"rtc.py: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
