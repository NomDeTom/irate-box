# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's guards against an AIC8800's firmware stall, offline: a hotspot start waits while a WiFi link is still
joining (root/radio.py), and the uplink watchdog starts again a hotspot found down while the link is up, even when the
driver's own reset never showed as an outage (hub/uplink.py), at most every HOTSPOT_RETRY. No network is touched.
python3 tests/sim_hotspot_guards.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv
from irate_box.hub import uplink as U
from irate_box.root import radio

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# --- a WiFi link still joining, as NetworkManager says it ---
NMCLI = {
    "joining": "wlan0:wifi:connecting (configuring)\nap0:wifi:connected\np2p-dev-wlan0:wifi-p2p:disconnected\nlo:loopback:connected (externally)",
    "address": "wlan0:wifi:connecting (getting IP configuration)\nap0:wifi:connected",
    "settled": "wlan0:wifi:connected\nap0:wifi:connected\np2p-dev-wlan0:wifi-p2p:disconnected",
    "cable": "eth0:ethernet:connecting (getting IP configuration)\nwlan0:wifi:connected",
}


def nm(state):
    return lambda *cmd, timeout=60: (0, NMCLI[state]) if cmd[0] == "nmcli" else (0, "")


check("joining: wlan0 is still joining", radio.wifi_joining(nm("joining")) == ["wlan0"])
check("  and while it waits for its address", radio.wifi_joining(nm("address")) == ["wlan0"])
check("  settled: nothing joining", radio.wifi_joining(nm("settled")) == [])
check("  a cable still connecting doesn't count (not on the radio)", radio.wifi_joining(nm("cable")) == [])
check("  nmcli failing: nothing joining", radio.wifi_joining(lambda *c, timeout=60: (1, "")) == [])

# --- the wait before a hotspot start ---
naps = []
states = iter(["joining", "joining", "address", "settled"])


def nm_settling(*cmd, timeout=60):
    return (0, NMCLI[next(states)]) if cmd[0] == "nmcli" else (0, "")


check(
    "settle: waits while joining, 3 s here",
    radio.settle_before_hotspot(nm_settling, naps.append) == 3 and naps == [1, 1, 1],
)
naps.clear()
check(
    "  never longer than its limit",
    radio.settle_before_hotspot(nm("joining"), naps.append, wait=5) == 5 and len(naps) == 5,
)
check("  nothing joining: no wait", radio.settle_before_hotspot(nm("settled"), naps.append) == 0)

# --- restore_hotspot: waits first, then starts ---
started = []


def run_restore(state):
    seq = iter([state, "settled"])

    def run(*cmd, timeout=60):
        if cmd[0] == "nmcli":
            return 0, NMCLI[next(seq, "settled")]
        started.append(cmd)
        return 0, ""

    return run


radio.hotspot_up = lambda: True
said = radio.restore_hotspot(run_restore("joining"), lambda s: None)
check(
    "restore: after the join, and says it waited",
    said == "The hotspot started again (after 1 s for the WiFi link to finish joining)." and started,
    said,
)
started.clear()
said = radio.restore_hotspot(run_restore("settled"), lambda s: None)
check("  nothing joining: started at once", said == "The hotspot started again." and started, said)
radio.hotspot_up = lambda: False
started.clear()
check(
    "  a hotspot that wasn't up: left alone",
    radio.restore_hotspot(run_restore("settled"), lambda s: None) == "" and not started,
)

# --- the watchdog: a hotspot down while the link is up ---
restores = []
radio.restore_hotspot = lambda *a, **k: restores.append(1) or "The hotspot started again."
ap_now = {"ifaces": {"wlan0": {"type": "managed"}}}
netinv.run = lambda *cmd, **k: (0, "")
netinv.parse_iw_dev = lambda out: ap_now["ifaces"]


class Actor:
    def remember(self):
        pass


def link(dry=False):
    lk = U.Link("wlan0", U.validate({}), None, [], dry=dry)
    lk.actor = Actor()
    lk.up, lk.answers = True, True
    return lk


def logged(lk):
    return [e["text"] for e in lk.w.events if e["kind"] == "result"]


radio.hotspot_up = lambda: True
U._hotspot_tried[0] = 0.0
lk = link()
lk.act(1000.0, [], lambda: {})
check("watchdog: the hotspot down with the link up: started again", restores == [1], restores)
check("  and said why", any(t.startswith("The hotspot was down with the link up:") for t in logged(lk)), logged(lk))
lk.act(1060.0, [], lambda: {})
check("  not again within HOTSPOT_RETRY", restores == [1], restores)
lk.act(1000.0 + U.HOTSPOT_RETRY, [], lambda: {})
check("  again once HOTSPOT_RETRY has passed", restores == [1, 1], restores)

restores.clear()
lk.hotspot_due = True
lk.act(1000.0 + U.HOTSPOT_RETRY + 1, [], lambda: {})
check(
    "  after a driver's reset seen in an outage: at once, whatever the retry",
    restores == [1] and not lk.hotspot_due,
    restores,
)

restores.clear()
U._hotspot_tried[0] = 0.0
ap_now["ifaces"] = {"wlan0": {"type": "managed"}, "ap0": {"type": "AP"}}
link().act(5000.0, [], lambda: {})
check("  the hotspot up: nothing done", restores == [])

ap_now["ifaces"] = {"wlan0": {"type": "managed"}}
radio.hotspot_up = lambda: False
link().act(6000.0, [], lambda: {})
check("  the owner's hotspot off: nothing done", restores == [])

radio.hotspot_up = lambda: True
link(dry=True).act(7000.0, [], lambda: {})
check("  a dry run: nothing done", restores == [])

lk = link()
lk.up = False
lk.act(8000.0, [], lambda: {})
check("  the link down: nothing done (it may be joining)", restores == [])

# --- the box doctor: the stall known, its reset visible, test options left ---
import tempfile
from types import SimpleNamespace

from irate_box.root import health

T = Path(tempfile.mkdtemp(prefix="wifistall-"))
radio.SYS = T / "sys"
health.MODPROBE_D = T / "modprobe.d"
health.MODPROBE_D.mkdir(parents=True)
journal = {"out": "Oct 22 kernel: something"}
powersave = {"wlan0": "off"}


def fake_run(*cmd, **k):
    if cmd[:2] == ("iw", "dev") and cmd[-2:] == ("get", "power_save"):
        return SimpleNamespace(returncode=0, stdout=f"Power save: {powersave[cmd[2]]}\n", stderr="")
    if cmd[:2] == ("iw", "dev"):
        return SimpleNamespace(returncode=0, stdout="(parsed by the stub above)", stderr="")
    return SimpleNamespace(returncode=0, stdout=journal["out"], stderr="")


health.run = fake_run
ap_now["ifaces"] = {"wlan0": {"type": "managed"}, "ap0": {"type": "AP"}, "wlan1": {"type": "managed"}}
(T / "drivers/aic8800_fdrv").mkdir(parents=True)
(T / "drivers/rtl8xxxu").mkdir(parents=True)
for iface, drv in (("wlan0", "aic8800_fdrv"), ("wlan1", "rtl8xxxu")):
    (radio.SYS / "class/net" / iface / "device").mkdir(parents=True)
    (radio.SYS / "class/net" / iface / "device/driver").symlink_to(T / "drivers" / drv)


def doctor():
    return {f["id"]: f for f in health.check_wifi_stalls()}


check("doctor: no AIC8800 driver loaded: nothing said", doctor() == {})
params = radio.SYS / "module/aic8800_fdrv/parameters"
params.mkdir(parents=True)
f = doctor()["wifi-stall"]
check(
    "  a driver that doesn't report its resets, the kernel's log in the journal: fine, and says how it learns",
    f["status"] == "ok" and "kernel's log" in str(f["summary"]),
    f,
)
check("  the general fixes, without power saving", "Power saving" not in str(f["fix"]), f["fix"])
notes = [str(n) for n in f["notes"]]
check(
    "  power saving's state for the AIC client only, its latency, its power unconfirmed",
    notes[0] == "Power saving is off for wlan0."
    and len(notes) == 2
    and "6 ms slower" in notes[1]
    and "has not been confirmed" in notes[1],
    notes,
)
powersave["wlan0"] = "on"
check("  and when it is on, says so", str(doctor()["wifi-stall"]["notes"][0]) == "Power saving is on for wlan0.")
journal["out"] = ""
f = doctor()["wifi-stall"]
check("  no kernel lines in the journal: a warning", f["status"] == "warn" and "cannot tell" in str(f["summary"]), f)
(params / "recoveries").write_text("3\n")
(params / "recovered_at").write_text("1388\n")
f = doctor()["wifi-stall"]
check(
    "  a driver that reports its resets: fine, with how many", f["status"] == "ok" and "3 times" in str(f["summary"]), f
)
(health.MODPROBE_D / "aic-test.conf").write_text("# a note\noptions aic8800_fdrv timeout_reads=1\noptions other x=1\n")
f = doctor().get("wifi-stall:test-options")
check(
    "  test options left on the driver: a warning naming the line",
    f and f["status"] == "warn" and any("timeout_reads=1" in e for e in f["evidence"]),
    f,
)
(health.MODPROBE_D / "aic-test.conf").write_text(
    "# options aic8800_fdrv timeout_reads=1\noptions aic8800_fdrv ps_on=1\n"
)
check("  a commented-out or ordinary option: no warning", "wifi-stall:test-options" not in doctor())

# The stall watch's mode, said; a log level raised at run time, beyond the driver's default (515).
(params / "stall_watch").write_text("1\n")
f = doctor()["wifi-stall"]
check("  the stall watch's mode in the evidence", "stall_watch 1 (log only)" in f["evidence"], f["evidence"])
(params / "aicwf_dbg_level").write_text("515\n")
check("  the driver's default log level (515): no warning", "wifi-stall:test-options" not in doctor())
(params / "aicwf_dbg_level").write_text("527\n")
f = doctor().get("wifi-stall:test-options")
check(
    "  debug logging turned on at run time (527): a warning naming it",
    f and any("aicwf_dbg_level 527" in e for e in f["evidence"]),
    f,
)
(params / "aicwf_dbg_level").write_text("515\n")

# The hotspot on another channel than the link on the same radio: the condition of every stall measured.
from irate_box.root import ap  # noqa: E402

real_load, real_chans = ap._load, ap.channels_now
record = {"up": True, "plan": {"kind": "own-channel", "iface": "wlan0", "uplink": "wlan0", "channel": 6}}
ap._load = lambda path, default: record if path == ap.RECORD else real_load(path, default)
chans = {"ap0": 6, "wlan0": 11}
ap.channels_now = lambda run: dict(chans)
f = doctor().get("wifi-stall:channel")
check(
    "  the hotspot on channel 6, the link on 11, one radio: a warning, with the way to Network, Hotspot",
    f
    and f["status"] == "warn"
    and "channel 6" in str(f["summary"])
    and "channel 11" in str(f["summary"])
    and f.get("go", {}).get("section") == "network/hotspot",
    f,
)
chans["ap0"] = 11
check("  both on 11: nothing said", "wifi-stall:channel" not in doctor())
record["plan"]["kind"] = "own-radio"
chans["ap0"] = 6
check("  a hotspot on a radio of its own: nothing said", "wifi-stall:channel" not in doctor())
ap._load, ap.channels_now = real_load, real_chans

print(f"\n{fails} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
