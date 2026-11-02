#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's status light: one of the board's LEDs blinking while the box is well, so a person
across the room can tell.

irate-box-led.service runs this as root (`led.py run`), always: one small process.

  What it shows   the owner's choice (SHOW), from the least to the most:
                    leave    the LED is left as the image set it
                    alive    the hub runs
                    network  the hub runs and the uplink watchdog says the link is up (another
                           link it watches being down shows as something to read)
                    all      that, and the box doctor's last look found no problem
  How             one pattern per state (PATTERNS), the same on every board: 1 s on, 1 s off while
                  well; off when not; quick flashes while the watchdog repairs the link; two short
                  flashes then a pause when the box doctor has a problem to read; steady on while
                  the box starts or an irate-box update installs. A box that freezes leaves the
                  light stuck on or off.
  Off             drive (the line high) or float (input, high-impedance; less leakage), where the
                  board's line is known and /sys/class/gpio is there.
  The light       driven by writing its brightness, on every board. A kernel trigger (heartbeat,
                  timer) would go on blinking with the hub dead and say "well"; the pattern
                  trigger stops after a set number of cycles but only starts again from its
                  beginning, so it can't be kept going without a jump. Many vendor kernels have no
                  triggers at all, and brightness is all there is.
  Which LED       the owner's, or one found on the board (pick): a Raspberry Pi's ACT (led0), left
                  to its SD card activity unless the owner gives it over; an LED named for status or
                  a heartbeat; a known board's own (BOARD_LEDS). Never a power LED (unless the board's
                  own table names it, as the Banana Pi M2 Zero's only LED is), a keyboard's, or a
                  virtual one. The LED's trigger and brightness from before are kept (led-saved.json)
                  and put back when it is left, given up or irate-box removed.

    led.py run                 the light
    led.py status              what it knows, as JSON
    led.py list                the LEDs found, as JSON
    led.py set KEY VALUE       show leave|alive|network|all; led auto|NAME; off drive|float
    led.py flash NAME [SECS]   blink an LED fast for a few seconds, to find it on the board
    led.py undo-all            for uninstall.sh: every LED put back

Settings in /etc/hub/led.json (root's). Stdlib only.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from irate_box.msg import M, Refused, duration

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
LEDS = SYS / "class" / "leds"
SETTINGS = ETC / "led.json"
SAVED = ETC / "led-saved.json"  # each LED's trigger and brightness before the box took it
RUN_DIR = Path(os.environ.get("HUB_LED_RUN", "/run/irate-box-led"))
CONTROL = STATE / "control"
UPLINK_RUN = Path(os.environ.get("HUB_UPLINK_RUN", "/run/irate-box-uplink")) / "uplink.json"
FLASH = RUN_DIR / "flash.json"  # an LED to blink fast for a while, to find it

SHOW = ("leave", "alive", "network", "all")
# What a choice of what the light shows does, said when it is made.
SHOW_SAID = {
    "alive": lambda led: M("led-set.show.alive", "{led} now blinks while the hub server runs.", led=led),
    "network": lambda led: M(
        "led-set.show.network", "{led} now blinks while the hub server runs and its link is up.", led=led
    ),
    "all": lambda led: M(
        "led-set.show.all",
        "{led} now blinks while the hub server runs, its link is up and the Box doctor finds no problem.",
        led=led,
    ),
}
# (on?, seconds) steps, repeated. Each state the same on every board.
PATTERNS = {
    "ok": [(1, 1.0), (0, 1.0)],
    "down": [(0, 2.0)],
    "repairing": [(1, 0.2), (0, 0.2)],
    "problem": [(1, 0.15), (0, 0.25), (1, 0.15), (0, 1.45)],
    "starting": [(1, 2.0)],
    "flash": [(1, 0.1), (0, 0.1)],
}
STATE_WORDS = {
    "ok": "1 s on, 1 s off: every check that this light shows passes",
    "down": "off: the hub server is not running, the link is down, or the box has no current status data",
    "repairing": "quick flashes: the uplink watchdog is repairing the link",
    "problem": "two short flashes: Box doctor found a problem",
    "starting": "steady on: the box is starting, or an update is installing",
    "leave": "not changed by the box: the image set it",
}
LOOK = 10  # seconds between looks at the box
STARTING = 180  # seconds after boot that count as starting, until the box is first well
# Boards whose LED is known: a device-tree "compatible" prefix, the LED, and what it shows by default.
BOARD_LEDS = [
    ("rockchip,rk3506", "work-led", "network"),
    # Banana Pi M2 Zero: its one LED is the red power/status LED on PL10 (the device tree's
    # active-low pwr_led). The board has no other, so this one is its status light. "pwr" is
    # the same LED as an older kernel names it.
    ("sinovoip,bpi-m2-zero", "bananapi-m2-zero:red:pwr", "network"),
    ("sinovoip,bpi-m2-zero", "pwr", "network"),
]
# The line behind a known board's LED, when the owner chooses high-impedance for its off state:
# (device-tree compatible, LED) -> (the controller's address, its line). PL10 on bpi0's r_pio
# is "PWR-LED"; its GPIO is taken from the controller that address names, not a hard-coded number.
RAW_GPIO = {
    ("sinovoip,bpi-m2-zero", "bananapi-m2-zero:red:pwr"): ("1f02c00", 10),
    ("sinovoip,bpi-m2-zero", "pwr"): ("1f02c00", 10),
}
GPIO_CLASS = Path(os.environ.get("HUB_GPIO_CLASS", "/sys/class/gpio"))
PLATFORM_DRIVERS = Path(os.environ.get("HUB_PLATFORM_DRIVERS", "/sys/bus/platform/drivers"))
# A Raspberry Pi's: the green activity LED (ACT, led0 on older kernels), left to the SD card by default.
PI_ACT = ("ACT", "led0")
# Never taken: a power LED (often under-voltage too), a keyboard's, a camera's, a network port's.
# A known board's own entry above may name one, as bpi0's only LED is its pwr_led.
NEVER = re.compile(
    r"(^|[:_-])(pwr|power)($|[:_-])|^led1$|::(capslock|numlock|scrolllock|kana|compose)|camera|^mmc\d|:(lan|wan|link)",
    re.I,
)
STATUS_NAME = re.compile(r":(status|heartbeat|indicator)$|^(status|sys|work|user)[-_]?led\d*$|^heartbeat$", re.I)
DEFAULT = {
    "show": None,
    "led": "auto",
    "off": "drive",
}  # show None: what the board suggests (suggest()); off: "drive" or "float"


def read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def write(p, value):
    try:
        Path(p).write_text(f"{value}\n")
        return True
    except OSError:
        return False


# --- the LEDs ----------------------------------------------------------------------------------


def leds():
    """The board's LEDs a status light could be: {name: {triggers: bool, trigger, max, kind}}, kind
    "pi-act", "status", "board" or "other"; none virtual, none NEVER."""
    out = {}
    try:
        names = sorted(p.name for p in LEDS.iterdir())
    except OSError:
        return out
    compat = compatible()
    for name in names:
        d = LEDS / name
        dev = d / "device"
        known = any(c.startswith(pre) and name == led for pre, led, _ in BOARD_LEDS for c in compat)
        if (NEVER.search(name) and not known) or "/virtual/" in str(dev.resolve() if dev.exists() else "/virtual/"):
            continue
        trig = read(d / "trigger")
        cur = re.search(r"\[([^\]]+)\]", trig or "")
        kind = (
            "pi-act"
            if name in PI_ACT and model().startswith("Raspberry Pi")
            else "board"
            if known
            else "status"
            if STATUS_NAME.search(name)
            else "other"
        )
        out[name] = {
            "triggers": trig is not None,
            "trigger": cur.group(1) if cur else None,
            "max": int(read(d / "max_brightness") or 1),
            "kind": kind,
        }
    # An LED the box left high-impedance has no LED class entry (its driver is unbound); keep it
    # in the list from its saved record, so the light keeps running and can be given back.
    for name, was in _saved().items():
        if was.get("raw") and name not in out:
            out[name] = {"triggers": False, "trigger": None, "max": 1, "kind": "board"}
    return out


def compatible():
    raw = read(DT / "compatible") if (DT / "compatible").exists() else None
    if raw is None:
        try:
            raw = (DT / "compatible").read_bytes().decode(errors="replace")
        except OSError:
            return []
    return [c for c in raw.replace("\0", "\n").splitlines() if c]


def model():
    try:
        return (DT / "model").read_bytes().decode(errors="replace").strip("\0 \n")
    except OSError:
        return ""


def pick(found, chosen="auto"):
    """The LED to use: the owner's if it is there; else a known board's, a status-named one, then a Pi's ACT."""
    if chosen != "auto":
        return chosen if chosen in found else None
    for kind in ("board", "status", "pi-act"):
        for name, info in found.items():
            if info["kind"] == kind:
                return name
    return None


def suggest(found, name):
    """What the light shows when the owner hasn't chosen: on a Pi's ACT, nothing (it shows the SD card);
    on a known board's or a status LED, the link."""
    info = found.get(name) or {}
    if info.get("kind") == "board":
        compat = compatible()
        return next(
            (show for pre, led, show in BOARD_LEDS if led == name and any(c.startswith(pre) for c in compat)), "network"
        )
    return "network" if info.get("kind") == "status" else "leave"


# --- settings ------------------------------------------------------------------------------------


def load_settings():
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULT)
    if raw.get("show") in SHOW:
        out["show"] = raw["show"]
    if isinstance(raw.get("led"), str) and re.fullmatch(r"auto|[A-Za-z0-9:._@-]{1,64}", raw["led"]):
        out["led"] = raw["led"]
    if raw.get("off") in ("drive", "float"):
        out["off"] = raw["off"]
    return out


def save_settings(s):
    from irate_box.root import safeio

    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(SETTINGS, json.dumps(s, indent=1))


def set_option(key, value):
    s = load_settings()
    found = leds()
    if key == "show" and value in SHOW:
        s["show"] = value
        save_settings(s)
        name = pick(found, s["led"])
        if value == "leave":
            return (
                M("led-set.leave", "The box no longer drives {led}. It stays as the image set it.", led=name)
                if name
                else M(
                    "led-set.leave.none", "The box does not drive a status light. Each LED stays as the image set it."
                )
            )
        if not name:
            return M(
                "led-set.show.no-led",
                "The setting is saved. This board has no LED that the box can use, so it applies once there is one.",
            )
        return SHOW_SAID[value](name)
    if key == "led" and (value == "auto" or value in found):
        s["led"] = value
        save_settings(s)
        name = pick(found, value)
        if not name:
            return M("led-set.led.none", "The box found no LED that it can use as the status light.")
        return (
            M("led-set.led.auto", "The box selected {led} as the status light.", led=name)
            if value == "auto"
            else M("led-set.led", "The status light is now {led}.", led=name)
        )
    if key == "led":
        raise Refused(M("led-set.not-a-light", "No LED called {led} can be a status light.", led=value))
    if key == "off" and value in ("drive", "float"):
        name = pick(found, s["led"])
        if value == "float" and not (name and _raw_ready(name)):
            raise Refused(
                M(
                    "led-set.off.no-raw",
                    "This board's light cannot be left high-impedance. It is driven off.",
                )
            )
        s["off"] = value
        save_settings(s)
        if name:
            _RAW_FAILED.discard(name)
        led_said = name or M("led.the-light", "the light")
        return (
            M(
                "led-set.off.float",
                "When {led} is off, it is now left high-impedance, which saves its leakage.",
                led=led_said,
            )
            if value == "float"
            else M("led-set.off.drive", "When {led} is off, it is now driven off.", led=led_said)
        )
    if key == "off":
        raise Refused(M("led-set.off.invalid", "The off state is driven off or high-impedance."))
    raise Refused(M("led-set.unknown", "The status light has no setting {key} {value}.", key=key, value=value))


def effective(s=None, found=None):
    """(the LED, what it shows) as they stand: the owner's choice, or what the board suggests."""
    s = s or load_settings()
    found = leds() if found is None else found
    name = pick(found, s["led"])
    show = s["show"] or (suggest(found, name) if name else "leave")
    return name, (show if name else "leave")


# --- taking an LED and giving it back -------------------------------------------------------------


def _saved():
    try:
        return json.loads(SAVED.read_text())
    except (OSError, ValueError):
        return {}


def _save_saved(saved):
    from irate_box.root import safeio

    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(SAVED, json.dumps(saved, indent=1))


def _raw_spec(name):
    """(controller address, line) for a known board's LED that may be left high-impedance, or None."""
    compat = compatible()
    for (prefix, led), spec in RAW_GPIO.items():
        if led == name and any(c.startswith(prefix) for c in compat):
            return spec
    return None


def _raw_ready(name):
    return bool(_raw_spec(name)) and GPIO_CLASS.is_dir()


def _raw_wanted(name):
    return load_settings().get("off") == "float" and _raw_ready(name) and name not in _RAW_FAILED


_RAW_FAILED = set()


def _platform_device(name):
    """The platform device holding a gpio-led, e.g. "leds" for the device tree's leds node."""
    link = LEDS / name / "device"
    try:
        return link.resolve(strict=True).name
    except OSError:
        return None


def _gpio_number(address, line):
    """The GPIO's global number, from the gpiochip whose label or device address names the controller."""
    try:
        chips = sorted(GPIO_CLASS.glob("gpiochip*"))
    except OSError:
        return None
    for chip in chips:
        label = read(chip / "label") or ""
        try:
            dev = str((chip / "device").resolve())
        except OSError:
            dev = ""
        if address in label or address in dev:
            try:
                return int(read(chip / "base")) + line
            except (TypeError, ValueError):
                return None
    return None


class _RawLed:
    """One LED driven through /sys/class/gpio, so its off state can be input (high-impedance)."""

    def __init__(self, name, address, line):
        self.name, self.address, self.line = name, address, line
        self.device = None
        self.gpio = None
        self.unbound = self.exported = False

    def take(self):
        self.device = _platform_device(self.name)
        self.gpio = _gpio_number(self.address, self.line)
        if not self.device or self.gpio is None:
            return False
        if not write(PLATFORM_DRIVERS / "leds-gpio" / "unbind", self.device):
            return False
        self.unbound = True
        if not write(GPIO_CLASS / "export", self.gpio):
            write(PLATFORM_DRIVERS / "leds-gpio" / "bind", self.device)
            self.unbound = False
            return False
        self.exported = True
        return True

    def set(self, on):
        return write(GPIO_CLASS / f"gpio{self.gpio}" / "direction", "low" if on else "in")

    def give_back(self):
        if self.exported:
            write(GPIO_CLASS / "unexport", self.gpio)
            self.exported = False
        if self.unbound:
            write(PLATFORM_DRIVERS / "leds-gpio" / "bind", self.device)
            self.unbound = False


def _class_led(name):
    """The LED class's trigger and brightness kept, its trigger set to none, so brightness writes hold."""
    d = LEDS / name
    saved = _saved()
    if name not in saved:
        trig = re.search(r"\[([^\]]+)\]", read(d / "trigger") or "")
        saved[name] = {"trigger": trig.group(1) if trig else None, "brightness": read(d / "brightness")}
        _save_saved(saved)
    if (d / "trigger").exists():
        write(d / "trigger", "none")
    try:
        top = int(read(d / "max_brightness") or 1)
    except (TypeError, ValueError):
        top = 1
    return lambda on: write(d / "brightness", top if on else 0)


def take(name):
    """Take an LED, and return (set it on or off, "raw" or "class"). High-impedance is used only
    when the owner chose it and this board's line is known; everything else falls back to LED class."""
    saved = _saved()
    was = saved.get(name)
    kept = None
    if was and was.get("raw"):
        info = was["raw"]
        gpio = info.get("gpio")
        line = GPIO_CLASS / f"gpio{gpio}" if gpio is not None else None
        # A raw record is only live while its exported line is there and the LED class (whose
        # driver was unbound) is not. A restart rebinds the driver and takes the export away; then
        # the record is stale, and its trigger and brightness are still the board's own.
        if _raw_wanted(name) and line is not None and line.exists() and not (LEDS / name).exists():
            spec = _raw_spec(name)
            if spec:
                raw = _RawLed(name, *spec)
                raw.device, raw.gpio = info.get("device"), gpio
                raw.unbound = raw.exported = True
                return raw.set, "raw"
        if _raw_wanted(name):
            kept = {"trigger": info.get("trigger"), "brightness": info.get("brightness")}
            saved.pop(name, None)
            if saved:
                _save_saved(saved)
            else:
                SAVED.unlink(missing_ok=True)
        else:
            give_back(name)
        was = None
    if _raw_wanted(name):
        spec = _raw_spec(name)
        raw = _RawLed(name, *spec)
        d = LEDS / name
        trig = re.search(r"\[([^\]]+)\]", read(d / "trigger") or "")
        if raw.take():
            saved[name] = {
                "raw": {
                    "device": raw.device,
                    "gpio": raw.gpio,
                    "trigger": kept["trigger"] if kept else (trig.group(1) if trig else None),
                    "brightness": kept["brightness"] if kept else read(d / "brightness"),
                }
            }
            _save_saved(saved)
            return raw.set, "raw"
        _RAW_FAILED.add(name)
    return _class_led(name), "class"


def give_back(name):
    """The LED as it was before the box took it; said in words, or None if it was never taken."""
    saved = _saved()
    was = saved.pop(name, None)
    if was is None:
        return None
    d = LEDS / name
    if was.get("raw"):
        info = was["raw"]
        write(GPIO_CLASS / "unexport", info.get("gpio"))
        write(PLATFORM_DRIVERS / "leds-gpio" / "bind", info.get("device"))
        if info.get("trigger") and (d / "trigger").exists():
            write(d / "trigger", info["trigger"])
        if info.get("trigger") in (None, "none") and info.get("brightness") is not None:
            write(d / "brightness", info["brightness"])
    else:
        if was.get("trigger") and (d / "trigger").exists():
            write(d / "trigger", was["trigger"])
        if was.get("trigger") in (None, "none") and was.get("brightness") is not None:
            write(d / "brightness", was["brightness"])
    if saved:
        _save_saved(saved)
    else:
        SAVED.unlink(missing_ok=True)
    if was.get("raw"):
        return f"{name}: restored to its LED class"
    return f"{name}: restored to {was.get('trigger') or 'brightness ' + str(was.get('brightness'))}"


def undo_all():
    return [g for g in (give_back(n) for n in list(_saved())) if g]


# --- what the box is ------------------------------------------------------------------------------


def _active(unit):
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=10).returncode == 0  # noqa: TID251
    except (OSError, subprocess.SubprocessError):
        return False


def uptime():
    try:
        return float((PROC / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 1e9


def installing():
    """An irate-box update installing now (the root helper's progress file, its process still there)."""
    try:
        prog = json.loads((CONTROL / "update-progress.json").read_text())
    except (OSError, ValueError):
        return False
    return "install" in str(prog.get("action")) and (PROC / str(prog.get("pid"))).exists()


def box_state(show, now=None, active=_active):
    """ok, down, repairing, problem or starting, for what the owner chose to be shown."""
    now = now or time.time()
    if installing():
        return "starting"
    if not active("irate-box.service"):
        return "starting" if uptime() < STARTING else "down"
    if show == "alive":
        return "ok"
    try:
        rep = json.loads(UPLINK_RUN.read_text() if UPLINK_RUN.exists() else (CONTROL / "uplink.json").read_text())
    except (OSError, ValueError):
        rep = None
    check = ((rep or {}).get("settings") or {}).get("check", 60)
    fresh = rep and now - float(rep.get("at") or 0) <= 3 * max(check, 60) and active("irate-box-uplink.service")
    if not fresh:
        return "starting" if uptime() < STARTING else "down"
    st = rep.get("state")
    if st in ("down", "stalled") and (rep.get("outage") or {}).get("done"):
        return "repairing"
    if st not in ("up", "checking"):  # one missed check is not down yet
        return "starting" if uptime() < STARTING else "down"
    # The box is on its network; another link it watches being down is something to read, not the box down.
    if any(
        lk.get("watched") and lk.get("state") in ("down", "stalled")
        for i, lk in (rep.get("links") or {}).items()
        if i != rep.get("iface")
    ):
        return "problem"
    if show == "all":
        try:
            doc = json.loads((CONTROL / "health.json").read_text())
            if any(f.get("status") == "problem" for f in doc.get("findings") or []):
                return "problem"
        except (OSError, ValueError, AttributeError):
            pass
    return "ok"


# --- running --------------------------------------------------------------------------------------


def _status(name, show, state, found):
    return {
        "at": time.time(),
        "led": name,
        "show": show,
        "state": state,
        "words": STATE_WORDS.get(state, state),
        "triggers": (found.get(name) or {}).get("triggers"),
        "found": found,
    }


def write_status(st):
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = RUN_DIR / "status.json.tmp"
        tmp.write_text(json.dumps(st, indent=1))
        tmp.replace(RUN_DIR / "status.json")
    except OSError:
        pass


def read_status():
    try:
        return json.loads((RUN_DIR / "status.json").read_text())
    except (OSError, ValueError):
        return None


def _flash_asked(now):
    try:
        f = json.loads(FLASH.read_text())
        return f["led"] if now < f["until"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run(sleep=time.sleep, clock=time.time, rounds=None):
    """The light. rounds: how many looks, for the tests (None: until stopped)."""
    held = {"name": None, "set": None, "mode": None}

    def stop(*_):
        if held["set"]:
            held["set"](False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    looked, state, show, found, step = 0.0, "starting", "leave", {}, 0
    n = 0
    while True:
        now = clock()
        if now - looked >= LOOK:
            if rounds is not None and n >= rounds:
                break
            looked, n = now, n + 1
            found = leds()
            new, show = effective(found=found)
            flashing = _flash_asked(now)
            if flashing and flashing in found:
                new, state = flashing, "flash"
            elif new and show != "leave":
                state = box_state(show, now)
            else:
                state = "leave"
            want = new if state != "leave" else None
            # Any LED taken but no longer wanted goes back, this run's or one an earlier run left taken.
            for other in [x for x in _saved() if x != want]:
                give_back(other)
            want_mode = "raw" if want and _raw_wanted(want) else "class"
            if want != held["name"] or want_mode != held["mode"]:
                if held["name"]:
                    give_back(held["name"])
                held.update(name=None, set=None, mode=None)
                if want:
                    setter, mode = take(want)
                    held.update(name=want, set=setter, mode=mode)
            write_status(_status(new, show, state, found))
            step = 0
        if not held["name"]:
            sleep(LOOK)
            continue
        pattern = PATTERNS[state]
        on, secs = pattern[step % len(pattern)]
        if held["set"]:
            held["set"](on)
        step += 1
        sleep(secs)


def flash(name, secs=8):
    if name not in leds():
        raise Refused(M("led-set.not-a-light", "No LED called {led} can be a status light.", led=name))
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    FLASH.write_text(json.dumps({"led": name, "until": time.time() + max(2, min(int(secs), 30))}))
    return M(
        "led-set.flash",
        "{led} starts to flash fast within {wait}, for {time}.",
        led=name,
        wait=duration(LOOK),
        time=duration(max(2, min(int(secs), 30))),
    )


def status():
    s = load_settings()
    found = leds()
    name, show = effective(s, found)
    return {
        "settings": s,
        "led": name,
        "show": show,
        "off": s["off"],
        "raw": bool(name and _raw_ready(name)),
        "raw_failed": name in _RAW_FAILED,
        "suggested": suggest(found, name) if name else None,
        "found": found,
        "running": read_status(),
        "saved": _saved(),
    }


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        run()
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "list":
        print(json.dumps(leds(), indent=1))
    elif cmd == "set" and len(argv) == 3:
        print(set_option(argv[1], argv[2]))
    elif cmd == "flash" and len(argv) in (2, 3):
        print(flash(argv[1], *(argv[2:] or [8])))
    elif cmd == "undo-all":
        for line in undo_all():
            print(line)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
