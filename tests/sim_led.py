# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The status light (root/led.py) and the box doctor's side of it, offline, on stand-in /sys trees: which LED
each board gives (a vendor board's with no triggers, a Raspberry Pi's ACT left to its SD card, a status-named
one, none but a power LED and virtual ones), the state shown for each choice, the light driven and given back
as it was, a stop leaving it off, and the doctor's settings, findings and choices. python3 tests/sim_led.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="led-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_SYSFS=str(T / "sys"), HUB_PROC=str(T / "proc"),
                  HUB_DEVICE_TREE=str(T / "dt"), HUB_LED_RUN=str(T / "run-led"), HUB_UPLINK_RUN=str(T / "run-uplink"))
sys.path.insert(0, str(REPO))
from irate_box.root import led, health  # noqa: E402
from irate_box.finding import message_ids as msg_ids  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
def board(compatible, model, lights):
    """A fresh stand-in: lights {name: (trigger list or None, device kind)}."""
    for d in ("sys", "dt", "etc", "run-led", "state"):
        if (T / d).exists():
            import shutil; shutil.rmtree(T / d)
    (T / "etc").mkdir()
    (T / "dt").mkdir(); (T / "dt" / "compatible").write_bytes("\0".join(compatible).encode() + b"\0")
    (T / "dt" / "model").write_bytes(model.encode() + b"\0")
    for name, (trig, kind) in lights.items():
        d = T / "sys/class/leds" / name
        put(d / "brightness", "0\n"); put(d / "max_brightness", "1\n")
        dev = T / "sys/devices" / ("virtual/leds" if kind == "virtual" else "platform/leds") / name
        dev.mkdir(parents=True, exist_ok=True); (d / "device").symlink_to(dev)
        if trig is not None:
            put(d / "trigger", " ".join(f"[{t}]" if i == 0 else t for i, t in enumerate(trig)) + "\n")
put(T / "proc/uptime", "5000.0 1.0\n")

# --- which LED, on which board ---
board(["rockchip,rk3506b-lyra-zero", "rockchip,rk3506"], "Luckfox Lyra Zero W", {"work-led": (None, "platform")})
f = led.leds()
check("a vendor board's LED with no trigger file: found, its triggers missing, known by the board table",
      f == {"work-led": {"triggers": False, "trigger": None, "max": 1, "kind": "board"}}, f)
check("  picked, and shows the link by default", led.effective() == ("work-led", "network"), led.effective())

board(["raspberrypi,3-model-b-plus", "brcm,bcm2837"], "Raspberry Pi 3 Model B Plus Rev 1.3",
      {"ACT": (["mmc0", "none", "timer", "heartbeat"], "platform"), "PWR": (["default-on", "none"], "platform"),
       "mmc0": (["none"], "virtual"), "default-on": (["default-on"], "virtual")})
f = led.leds()
check("a Pi: ACT found, PWR and the virtual entries never", list(f) == ["ACT"] and f["ACT"]["kind"] == "pi-act" and f["ACT"]["trigger"] == "mmc0", f)
check("  ACT picked, but left to its SD card unless the owner chooses", led.effective() == ("ACT", "leave"), led.effective())
board(["vendor,thing"], "Some Board", {"green:status": (["none", "timer"], "platform"), "red:power": (["default-on"], "platform"),
                                        "input0::capslock": (["kbd-capslock"], "platform")})
check("a mainline status LED: picked and shows the link; a power LED and a keyboard's never",
      list(led.leds()) == ["green:status"] and led.effective() == ("green:status", "network"), led.leds())
board(["vendor,other"], "Other", {"pwr_led": (None, "platform")})
check("only a power LED: nothing to use", led.leds() == {} and led.effective() == (None, "leave"))

# --- the owner's choices ---
board(["raspberrypi,3-model-b-plus"], "Raspberry Pi 3 Model B Plus Rev 1.3", {"ACT": (["mmc0", "none"], "platform"),
                                                                           "led-user": (["none"], "platform")})
said = led.set_option("show", "all")
check("set show all: kept, said", said["id"] == "led-set.show.all" and led.effective()[1] == "all", said)
said = led.set_option("led", "led-user")
check("set led: one of those found only", said["id"] == "led-set.led" and said["args"] == {"led": "led-user"} and led.effective()[0] == "led-user")
try:
    led.set_option("led", "PWR"); check("a power LED or one not there refused", False)
except ValueError as exc:
    check("a power LED or one not there refused", exc.message["id"] == "led-set.not-a-light")
led.set_option("led", "auto")

# --- the box's state, for each choice ---
up = {"at": time.time(), "state": "up", "settings": {"check": 60}, "outage": None}
def report(**kw):
    put(T / "run-uplink/uplink.json", json.dumps({**up, **kw}))
def yes(unit):
    return True
report()
check("alive: the hub runs", led.box_state("alive", active=yes) == "ok")
check("network: the link up", led.box_state("network", active=yes) == "ok")
report(state="checking")
check("  one missed check is not down yet", led.box_state("network", active=yes) == "ok")
report(state="down", outage={"done": []})
check("  down: off", led.box_state("network", active=yes) == "down")
report(state="down", outage={"done": ["reconnect"]})
check("  being repaired: quick flashes", led.box_state("network", active=yes) == "repairing")
report(links={"eth0": {"watched": True, "state": "up"}, "wlan0": {"watched": True, "state": "down"}}, iface="eth0")
check("  the route's link up, another watched link down: something to read", led.box_state("network", active=yes) == "problem")
report(links={"eth0": {"watched": True, "state": "up"}, "wlan0": {"watched": False, "state": "down"}}, iface="eth0")
check("  one not watched down: not counted", led.box_state("network", active=yes) == "ok")
report(at=time.time() - 3600)
check("  a report an hour old: not well (the watchdog stopped)", led.box_state("network", active=yes) == "down")
report()
check("  the watchdog's unit not running: not well", led.box_state("network", active=lambda u: u != "irate-box-uplink.service") == "down")
check("the hub not running: not well (past the start)", led.box_state("alive", active=lambda u: False) == "down")
put(T / "proc/uptime", "30.0 1.0\n")
check("  in the first minutes after boot: starting", led.box_state("alive", active=lambda u: False) == "starting")
put(T / "proc/uptime", "5000.0 1.0\n")
put(T / "state/control/health.json", json.dumps({"findings": [{"status": "ok"}, {"status": "problem"}]}))
check("all: a problem in the doctor's last look", led.box_state("all", active=yes) == "problem")
check("  network doesn't read the doctor", led.box_state("network", active=yes) == "ok")
put(T / "state/control/update-progress.json", json.dumps({"action": "install", "pid": 4242}))
put(T / "proc/4242/stat", "x")
check("an update installing: steady on", led.box_state("network", active=yes) == "starting")
put(T / "state/control/update-progress.json", json.dumps({"action": "check", "pid": 4242}))
check("  a check is not an install", led.box_state("network", active=yes) == "ok")
(T / "state/control/update-progress.json").unlink()

# --- the light: driven, given back ---
board(["raspberrypi,3-model-b-plus"], "Raspberry Pi 3 Model B Plus Rev 1.3", {"ACT": (["mmc0", "none", "timer"], "platform")})
act = T / "sys/class/leds/ACT"
put(act / "brightness", "1\n")
led.set_option("show", "network")
report()
led.box_state = lambda show, now=None, active=None: "ok"
writes, clock = [], [1000.0]
def fake_sleep(s):
    writes.append(((act / "brightness").read_text().strip(), s)); clock[0] += s
led.run(sleep=fake_sleep, clock=lambda: clock[0], rounds=2)
check("taken: its trigger set to none (so brightness writes hold), and what it had kept",
      (act / "trigger").read_text().strip() == "none" and json.loads((T / "etc/led-saved.json").read_text())
      == {"ACT": {"trigger": "mmc0", "brightness": "1"}}, ((act / "trigger").read_text(), (T / "etc/led-saved.json").read_text()))
check("  well: 1 s on, 1 s off", writes[:4] == [("1", 1.0), ("0", 1.0), ("1", 1.0), ("0", 1.0)], writes[:4])
st = json.loads((T / "run-led/status.json").read_text())
check("  its status said", st["led"] == "ACT" and st["state"] == "ok" and st["words"].startswith("1 s on"), st)
led.set_option("show", "leave")
writes.clear()
led.run(sleep=fake_sleep, clock=lambda: clock[0], rounds=1)
check("left: given back as it was (its trigger), the record cleared", (act / "trigger").read_text().strip() == "mmc0"
      and not (T / "etc/led-saved.json").exists() and [s for _, s in writes] == [10], ((act / "trigger").read_text(), writes))
put(act / "trigger", "[none] mmc0 timer\n")
(T / "etc/led-saved.json").write_text(json.dumps({"ACT": {"trigger": "mmc0", "brightness": "0"}}))
led.run(sleep=fake_sleep, clock=lambda: clock[0], rounds=1)
check("  an LED an earlier run left taken: given back at the next start", (act / "trigger").read_text().strip() == "mmc0"
      and not (T / "etc/led-saved.json").exists())
board(["rockchip,rk3506"], "Luckfox Lyra Zero W", {"work-led": (None, "platform")})
wl = T / "sys/class/leds/work-led"
put(wl / "brightness", "0\n")
writes.clear()
def wl_sleep(s):
    writes.append(((wl / "brightness").read_text().strip(), s)); clock[0] += s
led.box_state = lambda show, now=None, active=None: "repairing"
led.run(sleep=wl_sleep, clock=lambda: clock[0], rounds=1)
check("no trigger file: driven by brightness alone; repairing, quick flashes", writes[:2] == [("1", 0.2), ("0", 0.2)], writes[:4])
check("  undo-all gives it back", led.undo_all() == ["work-led: restored to brightness 0"] and (wl / "brightness").read_text().strip() == "0")
led.flash("work-led", 5)
led.set_option("show", "leave")
writes.clear()
led.run(sleep=wl_sleep, clock=lambda: clock[0], rounds=1)
check("flash: an LED blinked fast to find it, even while left", writes[:2] == [("1", 0.1), ("0", 0.1)]
      and json.loads((T / "run-led/status.json").read_text())["state"] == "flash", writes[:2])
clock[0] += 60
led._flash_asked = lambda now: None
led.run(sleep=wl_sleep, clock=lambda: clock[0] + 100, rounds=1)
check("  then given back", not (T / "etc/led-saved.json").exists())

# --- the box doctor ---
board(["raspberrypi,3-model-b-plus"], "Raspberry Pi 3 Model B Plus Rev 1.3", {"ACT": (["mmc0", "none"], "platform"),
                                                                           "led-user": (["none"], "platform")})
ls = led.status()
g = {x["key"]: x for x in health.led_groups(ls)}
check("the doctor's settings: what the light shows (left on a Pi, said why), the safest first; which light, when there's a choice",
      list(g) == ["led-show", "led-which"] and g["led-show"]["current"] == "leave" and g["led-show"]["desc"]["id"] == "hs.led-show.desc-leave"
      and [o["value"] for o in g["led-show"]["options"]] == ["leave", "alive", "network", "all"]
      and g["led-which"]["options"][0]["choice"] == "led-led:auto" and g["led-which"]["options"][2]["choice"] == "led-led:led-user")
check("  the image's failing LED service: a choice to switch it off, and on again",
      [o["choice"] for o in health.led_groups(ls, "failed")[-1]["options"]] == ["armbian-led:on", "armbian-led:off"]
      and health.led_groups(ls, "masked")[-1]["current"] == "off")
check("  no LED: no settings", health.led_groups({"found": {}, "settings": {}}) == [])
f = health.check_led(ls)
check("  left: said, with a way to change it and to find each light", f[0]["status"] == "ok" and "led.leave" in msg_ids(f[0])
      and [a["choice"] for a in f[0]["actions"]] == ["led-flash:ACT", "led-flash:led-user"] and f[0]["go"]["mark"] == "hs-led-show", f)
led.set_option("show", "network")
ls = led.status()
f = health.check_led(ls, active=lambda u: {"ActiveState": "inactive"})
check("  chosen but its service not running: a problem, with a restart", f[0]["status"] == "problem"
      and f[0]["actions"][0]["choice"] == "unit-restart:irate-box-led.service", f)
board(["rockchip,rk3506"], "Luckfox Lyra Zero W", {"work-led": (None, "platform")})
f = health.check_led(led.status(), active=lambda u: {"ActiveState": "active"})
check("  a kernel with no triggers: said that the box drives it", "led.driven-no-triggers" in msg_ids(f[0]), f)
for good in ("led-show:network", "led-led:auto", "led-led:green:status", "led-flash:work-led", "armbian-led:off"):
    check(f"the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("led-show:blink", "led-led:../x", "led-flash:a b", "armbian-led:remove"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))
said = health.fix("led-show:alive")
check("  a choice saved: the setting changed, and said", led.load_settings()["show"] == "alive" and said["id"] == "led-set.show.alive", said)
from irate_box.hub import server  # noqa: E402
check("the hub passes the same choices on", all(server.HEALTH_CHOICE_RE.match(c) for c in ("led-show:all", "led-flash:ACT", "armbian-led:on"))
      and not server.HEALTH_CHOICE_RE.match("led-show:x"))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
