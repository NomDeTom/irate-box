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
  The light       driven by writing its brightness, on every board. A kernel trigger (heartbeat,
                  timer) would go on blinking with the hub dead and say "well"; the pattern
                  trigger stops after a set number of cycles but only starts again from its
                  beginning, so it can't be kept going without a jump. Many vendor kernels have no
                  triggers at all, and brightness is all there is.
  Which LED       the owner's, or one found on the board (pick): a Raspberry Pi's ACT (led0), left
                  to its SD card activity unless the owner gives it over; an LED named for status or
                  a heartbeat; a known board's own (BOARD_LEDS). Never a power LED, a keyboard's,
                  or a virtual one. The LED's trigger and brightness from before are kept
                  (led-saved.json) and put back when it is left, given up or irate-box removed.

    led.py run                 the light
    led.py status              what it knows, as JSON
    led.py list                the LEDs found, as JSON
    led.py set KEY VALUE       show leave|alive|network|all; led auto|NAME
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
SAVED = ETC / "led-saved.json"     # each LED's trigger and brightness before the box took it
RUN_DIR = Path(os.environ.get("HUB_LED_RUN", "/run/irate-box-led"))
CONTROL = STATE / "control"
UPLINK_RUN = Path(os.environ.get("HUB_UPLINK_RUN", "/run/irate-box-uplink")) / "uplink.json"
FLASH = RUN_DIR / "flash.json"     # an LED to blink fast for a while, to find it

SHOW = ("leave", "alive", "network", "all")
# What a choice of what the light shows does, said when it is made.
SHOW_SAID = {"alive": lambda led: M("led-set.show.alive", "{led} now blinks while the hub server runs.", led=led),
             "network": lambda led: M("led-set.show.network", "{led} now blinks while the hub server runs and its link is up.", led=led),
             "all": lambda led: M("led-set.show.all", "{led} now blinks while the hub server runs, its link is up and the Box doctor "
                                                      "finds no problem.", led=led)}
# (on?, seconds) steps, repeated. Each state the same on every board.
PATTERNS = {
    "ok": [(1, 1.0), (0, 1.0)],
    "down": [(0, 2.0)],
    "repairing": [(1, 0.2), (0, 0.2)],
    "problem": [(1, 0.15), (0, 0.25), (1, 0.15), (0, 1.45)],
    "starting": [(1, 2.0)],
    "flash": [(1, 0.1), (0, 0.1)],
}
STATE_WORDS = {"ok": "1 s on, 1 s off: every check that this light shows passes",
               "down": "off: the hub server is not running, the link is down, or the box has no current status data",
               "repairing": "quick flashes: the uplink watchdog is repairing the link",
               "problem": "two short flashes: Box doctor found a problem",
               "starting": "steady on: the box is starting, or an update is installing", "leave": "not changed by the box: the image set it"}
LOOK = 10           # seconds between looks at the box
STARTING = 180      # seconds after boot that count as starting, until the box is first well
# Boards whose LED is known: a device-tree "compatible" prefix, the LED, and what it shows by default.
BOARD_LEDS = [("rockchip,rk3506", "work-led", "network")]
# A Raspberry Pi's: the green activity LED (ACT, led0 on older kernels), left to the SD card by default.
PI_ACT = ("ACT", "led0")
# Never taken: a power LED (often under-voltage too), a keyboard's, a camera's, a network port's.
NEVER = re.compile(r"(^|[:_-])(pwr|power)($|[:_-])|^led1$|::(capslock|numlock|scrolllock|kana|compose)|camera|^mmc\d|:(lan|wan|link)",
                   re.I)
STATUS_NAME = re.compile(r":(status|heartbeat|indicator)$|^(status|sys|work|user)[-_]?led\d*$|^heartbeat$", re.I)
DEFAULT = {"show": None, "led": "auto"}   # show None: what the board suggests (suggest())


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
        if NEVER.search(name) or "/virtual/" in str(dev.resolve() if dev.exists() else "/virtual/"):
            continue
        trig = read(d / "trigger")
        cur = re.search(r"\[([^\]]+)\]", trig or "")
        kind = ("pi-act" if name in PI_ACT and model().startswith("Raspberry Pi")
                else "board" if any(c.startswith(pre) and name == led for pre, led, _ in BOARD_LEDS for c in compat)
                else "status" if STATUS_NAME.search(name) else "other")
        out[name] = {"triggers": trig is not None, "trigger": cur.group(1) if cur else None,
                     "max": int(read(d / "max_brightness") or 1), "kind": kind}
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
        return next((show for pre, led, show in BOARD_LEDS if led == name and any(c.startswith(pre) for c in compat)), "network")
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
            return (M("led-set.leave", "The box no longer drives {led}. It stays as the image set it.", led=name) if name else
                    M("led-set.leave.none", "The box does not drive a status light. Each LED stays as the image set it."))
        if not name:
            return M("led-set.show.no-led", "The setting is saved. This board has no LED that the box can use, so it applies "
                                            "once there is one.")
        return SHOW_SAID[value](name)
    if key == "led" and (value == "auto" or value in found):
        s["led"] = value
        save_settings(s)
        name = pick(found, value)
        if not name:
            return M("led-set.led.none", "The box found no LED that it can use as the status light.")
        return (M("led-set.led.auto", "The box selected {led} as the status light.", led=name) if value == "auto" else
                M("led-set.led", "The status light is now {led}.", led=name))
    if key == "led":
        raise Refused(M("led-set.not-a-light", "No LED called {led} can be a status light.", led=value))
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


def take(name):
    """The LED's trigger and brightness kept (once), its trigger set to none, so writes to its brightness hold."""
    d = LEDS / name
    saved = _saved()
    if name not in saved:
        trig = re.search(r"\[([^\]]+)\]", read(d / "trigger") or "")
        saved[name] = {"trigger": trig.group(1) if trig else None, "brightness": read(d / "brightness")}
        from irate_box.root import safeio
        ETC.mkdir(parents=True, exist_ok=True)
        safeio.write(SAVED, json.dumps(saved, indent=1))
    if (d / "trigger").exists():
        write(d / "trigger", "none")


def give_back(name):
    """The LED as it was before the box took it; said in words, or None if it was never taken."""
    saved = _saved()
    was = saved.pop(name, None)
    if was is None:
        return None
    d = LEDS / name
    if was.get("trigger") and (d / "trigger").exists():
        write(d / "trigger", was["trigger"])
    if was.get("trigger") in (None, "none") and was.get("brightness") is not None:
        write(d / "brightness", was["brightness"])
    from irate_box.root import safeio
    if saved:
        safeio.write(SAVED, json.dumps(saved, indent=1))
    else:
        SAVED.unlink(missing_ok=True)
    return f"{name}: restored to {was.get('trigger') or 'brightness ' + str(was.get('brightness'))}"


def undo_all():
    return [g for g in (give_back(n) for n in list(_saved())) if g]


# --- what the box is ------------------------------------------------------------------------------

def _active(unit):
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=10).returncode == 0
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
    if st not in ("up", "checking"):     # one missed check is not down yet
        return "starting" if uptime() < STARTING else "down"
    # The box is on its network; another link it watches being down is something to read, not the box down.
    if any(lk.get("watched") and lk.get("state") in ("down", "stalled") for i, lk in (rep.get("links") or {}).items()
           if i != rep.get("iface")):
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
    return {"at": time.time(), "led": name, "show": show, "state": state, "words": STATE_WORDS.get(state, state),
            "triggers": (found.get(name) or {}).get("triggers"), "found": found}


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
    held = {"name": None}

    def stop(*_):
        if held["name"]:
            write(LEDS / held["name"] / "brightness", 0)
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
            if want and held["name"] != want:
                take(want)
            held["name"] = want
            write_status(_status(new, show, state, found))
            step = 0
        if not held["name"]:
            sleep(LOOK)
            continue
        pattern = PATTERNS[state]
        on, secs = pattern[step % len(pattern)]
        write(LEDS / held["name"] / "brightness", (found.get(held["name"]) or {}).get("max", 1) if on else 0)
        step += 1
        sleep(secs)


def flash(name, secs=8):
    if name not in leds():
        raise Refused(M("led-set.not-a-light", "No LED called {led} can be a status light.", led=name))
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    FLASH.write_text(json.dumps({"led": name, "until": time.time() + max(2, min(int(secs), 30))}))
    return M("led-set.flash", "{led} starts to flash fast within {wait}, for {time}.", led=name, wait=duration(LOOK),
             time=duration(max(2, min(int(secs), 30))))


def status():
    s = load_settings()
    found = leds()
    name, show = effective(s, found)
    return {"settings": s, "led": name, "show": show, "suggested": suggest(found, name) if name else None,
            "found": found, "running": read_status(), "saved": _saved()}


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
