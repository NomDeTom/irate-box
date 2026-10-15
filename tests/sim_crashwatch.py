# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Crash watch (root/crashwatch.py) and the box doctor's findings on it, offline: the
kernel's flood counted not copied, a snapshot from a stand-in /proc and /sys, a crash filed at the next
boot (and not after a clean shutdown), a failing radio told from a link going down and noted (never
acted on), its old setting carried over to the uplink watchdog, the hang settings, and the doctor's switches. Every command that would
change the system is stood in. python3 tests/sim_crashwatch.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="crashwatch-"))
PROC, SYS = T / "proc", T / "sys"
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_PROC=str(PROC), HUB_SYSFS=str(SYS),
                  HUB_KMSG=str(T / "no-kmsg"), HUB_SYSCTL_DIR=str(T / "sysctl.d"), HUB_MODULES_LOAD=str(T / "modules-load.d"),
                  HUB_SYSTEMD_CONF_DIR=str(T / "system.conf.d"), HUB_MODULES_DIR=str(T / "lib-modules"), HUB_DEV_USB=str(T / "dev-usb"))
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import crashwatch as cw, health  # noqa: E402
from irate_box.finding import message_args as msg_args, message_ids as msg_ids  # noqa: E402
from irate_box.msg import render  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)

# A stand-in /proc and /sys: a small ARM board.
put(PROC / "sys/kernel/random/boot_id", "163672e1-5e3e-44f8-94a4-994042b8f0fc\n")
put(PROC / "uptime", "5209.53 10000.00\n")
put(PROC / "loadavg", "0.10 0.17 0.31 1/161 4242\n")
put(PROC / "meminfo", "MemTotal: 487000 kB\nMemFree: 35840 kB\nMemAvailable: 296960 kB\nSlab: 83968 kB\nSwapFree: 228352 kB\nCommitted_AS: 563200 kB\n")
put(PROC / "net/wireless", "Inter-| sta-|\n face | tus |\n wlan0: 0000    0.  -31.  -256.\n")
for pid, comm, pages in (("100", "node", "0 11758"), ("200", "syncthing", "0 5874")):
    put(PROC / pid / "comm", comm + "\n"); put(PROC / pid / "statm", pages + " 0 0 0 0 0\n")
put(SYS / "class/thermal/thermal_zone0/temp", "42000\n")
put(SYS / "class/net/wlan0/operstate", "up\n"); (SYS / "class/net/wlan0/wireless").mkdir(parents=True)
(SYS / "bus/usb/devices/1-1.1").mkdir(parents=True); (SYS / "bus/usb/devices/1-1").mkdir(parents=True)

# --- the kernel's lines ---
dump = ["[20916.1] usb 1-1.1: 4addr Frame received (0), skb->len:115"] + ["[20916.1] 4addr 37 00 00 00 21 c8 f1 dd 04 00 00 00 80 01 ec 3b"] * 7 \
    + ["[20916.1] 4addr 00 00 78"]
lines = dump * 4 + ["[21000.0] usb 1-1.2: USB disconnect, device number 5"]
keep, flood = cw.floods(lines)
check("a driver's hex dump: one kind, counted not kept, its header lines with it", flood == {"4addr #": 36} and not any("4addr" in l for l in keep), (flood, keep))
check("  the rest kept", "[21000.0] usb 1-1.2: USB disconnect, device number 5" in keep)
check("  under the flood line, kept as they are", cw.floods(lines[:20])[1] == {})

# --- a snapshot ---
snap = cw.snapshot(keep, flood)
check("a snapshot: time, uptime, load and boot; memory; temperature; the heaviest; links; wifi; the flood counted",
      "up 5210s load 0.10 0.17 0.31 boot 163672e1" in snap and "MemAvailable 290 MB" in snap and "temp 42C" in snap
      and "47032 KB node" in snap and "links: wlan0 up" in snap and "wifi wlan0:" in snap and 'kernel: 36 lines like "4addr #"' in snap, snap)
cw.SNAP_MAX = 2000
for _ in range(10):
    cw.append_snapshot(snap)
check("  appended to the box's storage, private, rotated past its size", cw.SNAP.exists() and cw.SNAP.with_name("snap.log.1").exists()
      and oct(cw.DIR.stat().st_mode & 0o777) == "0o700" and cw.SNAP.stat().st_size <= 2000 + len(snap))

# --- a crash, at boot ---
cw.journal_tail = lambda n=300: "the previous boot's journal"
check("the first boot: noted, nothing filed", cw.boot(now=1000.0) is None and json.loads(cw.RUN.read_text())["clean"] is False)
cw.shutdown()
put(PROC / "sys/kernel/random/boot_id", "22222222-0000-0000-0000-000000000000\n")
check("a boot after a clean shutdown: nothing filed", cw.boot(now=2000.0) is None)
cw.append_snapshot(snap)
put(PROC / "sys/kernel/random/boot_id", "33333333-0000-0000-0000-000000000000\n")
f = cw.boot(now=3000.0)
check("a boot after one that never shut down: filed, with its last snapshot and journal", f and f["boot"].startswith("22222222")
      and (cw.CRASHES / f["dir"] / "snapshots.log").read_text().endswith(snap) and "journal" in (cw.CRASHES / f["dir"] / "journal-previous-boot.log").read_text()
      and f["last"].startswith("=== ") and json.loads(cw.INDEX.read_text())[-1]["dir"] == f["dir"], f)
check("  the service restarting in the same boot: nothing filed", cw.boot(now=3100.0) is None)
cw.KEEP_CRASHES = 2
for i, b in enumerate(("44444444", "55555555", "66666666")):
    put(PROC / "sys/kernel/random/boot_id", f"{b}-0000-0000-0000-000000000000\n")
    cw.boot(now=4000.0 + i * 100)
index = json.loads(cw.INDEX.read_text())
check("  the last few kept, the older ones' folders gone", len(index) == 2 and len(list(cw.CRASHES.iterdir())) == 2, index)

# --- the radio: failing, or only its link ---
known = {"wlan0": {"driver": "aic8800_fdrv", "port": "1-1.1", "product": "AIC8800DC"}}
st = cw.radio_state(known, ["[1.0] wlan0: disconnected from 04:95:e6:72:b6:d1"])
check("a link going down is not the radio failing", st == {"wlan0": ([], [])}, st)
st = cw.radio_state(known, ["[1.0] usb 1-1.2: USB disconnect, device number 5"])
check("  nor another USB device leaving", st["wlan0"] == ([], []), st)
st = cw.radio_state(known, ["[1.0] aicwf_usb: cmd tx timeout", "[1.1] usb 1-1.1: USB disconnect, device number 4"])
check("the driver's errors and its own USB device leaving: counted", len(st["wlan0"][1]) == 2, st)
shutil.rmtree(SYS / "bus/usb/devices/1-1.1"); shutil.rmtree(SYS / "class/net/wlan0")
st = cw.radio_state(known, [])
check("  its interface and USB device gone: said", st["wlan0"][0] == ["wlan0 is gone", "its USB device 1-1.1 (AIC8800DC) left the bus"], st)

# --- a failing radio: noticed and noted, never acted on (the uplink watchdog repairs) ---
def drive(ticks=((0, True), (30, True), (210, True), (240, False))):
    log = []
    def lg(now, iface, kind, text, snapshot=False):
        e = {"at": now, "iface": iface, "kind": kind, "text": text}; log.append(e); return e
    p = cw.RadioWatch(lg)
    for t, bad in ticks:
        p.act(t, {"wlan0": (["wlan0 is gone"] if bad else [], [])})
    return [e["kind"] for e in log]
kinds = drive()
check("a failing radio: the failure noted once and its recovery, nothing done", kinds == ["failed", "recovered"], kinds)
check("  crash watch can no longer reset the radio or reboot", not any(hasattr(cw, n) for n in ("Preempt", "reset_radio", "reboot_now", "guards")))
burst = cw.RadioWatch(lambda *a, **k: {"kind": a[2]})
ev = burst.act(0, {"wlan0": ([], ["e"] * 3)}) + burst.act(30, {"wlan0": ([], ["e"] * 3)})
check("a burst of driver errors over two looks: a failure", [e["kind"] for e in ev] == ["failed"], ev)

# --- the hang settings ---
ran = []
cw.subprocess = type("S", (), {"run": staticmethod(lambda cmd, **kw: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")),
                               "Popen": staticmethod(lambda cmd: ran.append(cmd)), "SubprocessError": subprocess.SubprocessError})
print(cw.set_option("panic", "on"))
check("panic on: written for the next boot and set now", "kernel.panic = 10" in cw.SYSCTL.read_text() and ["sysctl", "-q", "-w", "kernel.softlockup_panic=1"] in ran
      and cw.load_settings()["panic"] is True)
print(cw.set_option("watchdog", "on"))
check("watchdog on, the board having none: softdog loaded and kept, systemd feeding it", "softdog" in cw.MODULES.read_text()
      and "RuntimeWatchdogSec=60" in cw.SYSTEMD_CONF.read_text() and ["modprobe", "softdog"] in ran and ["systemctl", "daemon-reexec"] in ran)
done = cw.undo_all()
check("undo-all: both taken away", not cw.SYSCTL.exists() and not cw.SYSTEMD_CONF.exists() and not cw.MODULES.exists() and len(done) == 2, done)
for bad in (("preempt", "radio"), ("snapshots", "maybe"), ("rm", "-rf")):
    try:
        cw.set_option(*bad); ok = False
    except ValueError:
        ok = True
    check(f"set {bad}: refused", ok)

# --- the box doctor ---
cw.set_option("panic", "off"); cw.set_option("watchdog", "off")
now = 4300.0
cw._write(cw.STATUS, {"at": now, "radios": known, "floods": {"4addr #": 4000}, "flood_window": 3600})
cw._write(cw.EVENTS, [{"at": now - 600, "iface": "wlan0", "kind": "failed", "text": "wlan0 is gone"}])
f = {x["id"]: x for x in health.check_crashwatch(now=now)}
check("the doctor: the last crash said, with its last snapshot, and what would have restarted it",
      f["crash-last"]["status"] == "warn" and msg_args(f["crash-last"], "crash-last.count")["n"] == 2
      and any(e.startswith("=== ") for e in f["crash-last"]["evidence"]) and "crash-last.fix" in msg_ids(f["crash-last"]), f["crash-last"])
check("  the kernel flood, with its rate", f["crash-flood"]["status"] == "warn" and msg_args(f["crash-flood"], "crash-flood.lines")["rate"] == "1.1", f["crash-flood"])
check("  the radio watched, its failure in the last day, nothing to choose here: where repairs are set, said",
      f["crash-radio"]["status"] == "warn" and msg_args(f["crash-radio"], "crash-radio.watching")["radios"] == ["wlan0 (AIC8800DC)"]
      and any("wlan0 failed" in e for e in f["crash-radio"]["evidence"])
      and not f["crash-radio"]["actions"] and "crash-radio.fix" in msg_ids(f["crash-radio"])
      and f["crash-radio"]["go"]["mark"] == "up-wedge", f["crash-radio"])
check("  a frozen box after a crash, nothing to restart it: a warning; the choice is the doctor's settings', linked to its row",
      f["crash-hang"]["status"] == "warn" and "crash-hang.none" in msg_ids(f["crash-hang"]) and not f["crash-hang"]["actions"]
      and (f["crash-hang"]["go"]["section"], render(f["crash-hang"]["go"]["where"]), f["crash-hang"]["go"]["mark"])
      == ("health-settings", "Recovery and monitoring", "hs-panic"), f["crash-hang"])
check("  snapshots: said, linked to their row", not f["crash-snapshots"]["actions"] and f["crash-snapshots"]["go"]["mark"] == "hs-snapshots", f["crash-snapshots"])
g = {x["key"]: x for x in health.doctor_settings()["groups"]}
check("the doctor's settings: snapshots, a panic, and the watchdog where there is one, each with its current choice, the safest first",
      [k for k in g] == ["snapshots", "panic", "watchdog"] and g["snapshots"]["current"] == "on" and g["panic"]["current"] == "off"
      and [o["value"] for o in g["panic"]["options"]] == ["off", "on"] and g["panic"]["options"][1]["confirm"]
      and g["watchdog"]["options"][1]["choice"] == "crashwatch-watchdog:on", g)
check("  in the report too", health.scan()["settings"]["groups"][0]["key"] == "snapshots")
for good in ("crashwatch-panic:on", "crashwatch-snapshots:off"):
    check(f"  the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("crashwatch-preempt:reboot", "crashwatch-rm:on", "crashwatch-panic:on;reboot"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))
said = health.fix("crashwatch-snapshots:off")
check("  a switch pressed: the setting changed, and said", cw.load_settings()["snapshots"] is False and said.startswith("Crash snapshots disabled"))
cw.set_option("snapshots", "on")
# Crash watch's old radio setting, carried over to the uplink watchdog once and dropped.
from irate_box.hub import uplink
uplink.ETC, uplink.SETTINGS = cw.ETC, cw.ETC / "uplink.json"
cw.SETTINGS.write_text(json.dumps({"snapshots": True, "preempt": "reboot", "panic": False, "watchdog": False}))
said = cw.carry_over()
check("carried over: preempt reboot becomes the watchdog's on_wedge reboot, and is dropped",
      uplink.load_settings()["on_wedge"] == "reboot" and "preempt" not in json.loads(cw.SETTINGS.read_text()) and "on_wedge reboot" in said, said)
check("  once only", cw.carry_over() is None)
uplink.save_settings(dict(uplink.load_settings(), on_wedge="reboot"))
cw.SETTINGS.write_text(json.dumps({"preempt": "radio"}))
cw.carry_over()
check("  never lowered: radio leaves the watchdog's reboot as it was", uplink.load_settings()["on_wedge"] == "reboot")
cw.SETTINGS.write_text(json.dumps({"preempt": "warn"}))
check("  warn: nothing to carry, the setting dropped", cw.carry_over() and "preempt" not in json.loads(cw.SETTINGS.read_text()))

# --- a board whose watchdog its device tree leaves switched off (the Lyra's RK3506) ---
DTD, BOOTD = T / "dt", T / "boot"
for n, st in (("watchdog@ff260000", b"disabled\0"), ("watchdog@ff268000", b"disabled\0")):
    (DTD / n).mkdir(parents=True); (DTD / n / "status").write_bytes(st)
BOOTD.mkdir(); (BOOTD / "armbianEnv.txt").write_text("verbosity=1\noverlays=luckfox-lyra-zero-w-spi0-1cs-spidev\n")
cw.DT, cw.BOOT = DTD, BOOTD
dtc_in = []
def fake_run(cmd, **kw):
    ran.append(cmd)
    if cmd[0] == "modinfo":
        return subprocess.CompletedProcess(cmd, 1, "", "modinfo: ERROR: Module softdog not found.")
    if cmd[0] == "dtc":
        dtc_in.append(kw.get("input", "")); Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\xd0\x0d\xfe\xed")
    return subprocess.CompletedProcess(cmd, 0, "", "")
cw.subprocess = type("S", (), {"run": staticmethod(fake_run), "Popen": staticmethod(lambda cmd: ran.append(cmd)), "SubprocessError": subprocess.SubprocessError})
cw.shutil = type("W", (), {"which": staticmethod(lambda name: "/usr/bin/dtc" if name == "dtc" else None)})
check("a switched-off watchdog in the device tree, on Armbian with dtc: offered as one to switch on", cw.watchdog_device() == "switched-off")
f = {x["id"]: x for x in health.check_crashwatch(now=now)}
wdg = {x["key"]: x for x in health.doctor_settings()["groups"]}["watchdog"]
check("  the doctor says so, and its setting asks with a confirm that says a restart is needed",
      "crash-hang.switched-off" in msg_ids(f["crash-hang"]) and "next restart" in render(wdg["desc"])
      and wdg["options"][1]["confirm"]["id"] == "hs.watchdog.confirm-overlay", (f["crash-hang"], wdg))
said = cw.set_option("watchdog", "on")
env = (BOOTD / "armbianEnv.txt").read_text()
check("  on: the overlay compiled for the first switched-off node, added to user_overlays, systemd set to feed it",
      'target-path = "/watchdog@ff260000"' in dtc_in[-1] and 'status = "okay"' in dtc_in[-1]
      and (BOOTD / "overlay-user/irate-box-watchdog.dtbo").exists() and "user_overlays=irate-box-watchdog\n" in env
      and "overlays=luckfox-lyra-zero-w-spi0-1cs-spidev\n" in env and "RuntimeWatchdogSec=60" in cw.SYSTEMD_CONF.read_text()
      and "next restart" in said, (said, env))
check("  until the restart, it waits, and the doctor says so", cw.board_watchdog_pending()
      and "crash-hang.pending" in msg_ids({x["id"]: x for x in health.check_crashwatch(now=now)}["crash-hang"]))
(BOOTD / "armbianEnv.txt").write_text(env.replace("user_overlays=irate-box-watchdog", "user_overlays=mine irate-box-watchdog"))
cw.set_option("watchdog", "off")
env = (BOOTD / "armbianEnv.txt").read_text()
check("  off: the overlay and its name gone, the owner's own user overlay kept", not (BOOTD / "overlay-user/irate-box-watchdog.dtbo").exists()
      and "user_overlays=mine\n" in env and "irate-box-watchdog" not in env and not cw.SYSTEMD_CONF.exists(), env)
cw.set_option("watchdog", "on"); cw.undo_all()
check("  undo-all takes it away too", "irate-box-watchdog" not in (BOOTD / "armbianEnv.txt").read_text()
      and not (BOOTD / "overlay-user/irate-box-watchdog.dtbo").exists())
(DTD / "watchdog@ff260000" / "status").write_bytes(b"okay\0"); (DTD / "watchdog@ff268000" / "status").write_bytes(b"okay\0")
check("  no switched-off node: no watchdog to offer", cw.watchdog_device() is None)
cw.shutil = shutil  # the stub above stood in for which() only

# --- the AIC8800's own words as its firmware wedged (a recorded kernel log) ---
FX = REPO / "tests/fixtures/aic8800-wedge-2026-10-09"
wedge = FX.joinpath("kern.log").read_text().splitlines()
aic = {"wlan0": {"driver": "aic8800_fdrv", "port": "1-1.1", "product": "AIC8800DC"}}
errs = cw.radio_state(aic, wedge)["wlan0"][1]
check("the wedge's lines now counted: the cmdqueue burst, the timeout, cmd queue crashed (none were before)",
      len(errs) == 57 and any("cmd queue crashed" in l for l in errs) and any("cmd_mgr_queue cmd timed-out" in l for l in errs), len(errs))
check("  for an aic radio only: another driver's radio is not blamed for them",
      cw.radio_state({"wlan1": {"driver": "rtl8xxxu", "port": "1-1.2"}}, wedge)["wlan1"][1] == [])
kinds = []
pre = cw.RadioWatch(lambda now, iface, kind, text, snapshot=False: kinds.append(kind) or {"kind": kind})
pre.act(100, cw.radio_state(aic, wedge))
check("  so a failure is noted at once", kinds == ["failed"], kinds)
# Filed as it happens, the box running on: the ten minutes before, and the look's kernel lines.
cw.SNAP.with_name("snap.log.1").unlink(missing_ok=True)
put(cw.SNAP, "".join(f"=== 2026-10-09 03:{m:02d}:00 up 1s load 0\n  x\n" for m in range(40, 60)) + "=== 2026-10-09 04:00:12 up 1s load 0\n  !! wedge\n")
os.environ["TZ"] = "Europe/London"; __import__("time").tzset()
at = __import__("time").mktime((2026, 10, 9, 4, 0, 16, 0, 0, -1))
rec = cw.file_radio(at, "wlan0", "cmd queue crashed", wedge)
d = cw.CRASHES / rec["dir"]
snaps = (d / "snapshots.log").read_text()
check("a radio failure filed when it happens: its own folder, the index apart from the crashes",
      rec["dir"].endswith("-radio-wlan0") and json.loads(cw.INCIDENTS.read_text())[-1]["dir"] == rec["dir"]
      and all(i.get("kind") != "radio" for i in json.loads(cw.INDEX.read_text())), rec)
check("  with the ten minutes before it and the kernel's lines", snaps.startswith("=== 2026-10-09 03:50:00") and "!! wedge" in snaps
      and "cmd queue crashed" in (d / "kernel.log").read_text(), snaps[:40])
check("  a crash filed later reaches back to that boot's first radio failure (not only the last 200 KB)",
      cw.snapshots_tail(10, since=at - 600).startswith("=== 2026-10-09 03:50:00"))
check("  and the doctor says where it is kept", msg_args(next(f for f in health.check_crashwatch(now=at + 60) if f["id"] == "crash-radio"), "crash-radio.kept")["n"] == 1)
# One radio, two interfaces (the uplink and the hotspot on one AIC8800): one incident, both named.
n0 = len(json.loads(cw.INCIDENTS.read_text()))
a = cw.file_radio(at + 200, "wlan0", "wlan0 is gone", ["k1"], "1-1.1")
b = cw.file_radio(at + 200, "ap0", "ap0 is gone", ["k2"], "1-1.1")
idx = json.loads(cw.INCIDENTS.read_text())
check("a second interface of the same radio failing with it: the same incident, both interfaces named",
      len(idx) == n0 + 1 and b["dir"] == a["dir"] and idx[-1]["ifaces"] == ["wlan0", "ap0"] and "ap0: ap0 is gone" in idx[-1]["text"]
      and "k2" in (cw.CRASHES / a["dir"] / "kernel.log").read_text(), idx[-1])
c = cw.file_radio(at + 200 + cw.RADIO_JOIN + 1, "ap0", "ap0 is gone", [], "1-1.1")
o = cw.file_radio(at + 200 + cw.RADIO_JOIN + 2, "wlan1", "wlan1 is gone", [], "2-1")
dirs = [i["dir"] for i in json.loads(cw.INCIDENTS.read_text())]
check("  later, or another radio: an incident of its own", len({a["dir"], c["dir"], o["dir"]}) == 3
      and dirs[-2:] == [c["dir"], o["dir"]], dirs[-3:])

# --- the one radio reset (root/radio.py), shared with the uplink watchdog ---
from irate_box.root import radio  # noqa: E402
def fake_sys(gone_after=None):
    shutil.rmtree(SYS / "bus/usb", ignore_errors=True)
    (SYS / "bus/usb/drivers/usb").mkdir(parents=True); (SYS / "bus/usb/devices/1-1.1").mkdir(parents=True)
    (SYS / "bus/usb/devices/1-1").mkdir(parents=True)
    (SYS / "class/net/wlan0").mkdir(parents=True, exist_ok=True)
ran = []
def frun(*cmd, timeout=60):
    ran.append(" ".join(cmd))
    if cmd[:3] == ("systemctl", "is-active", "--quiet"):
        return 0, ""
    if cmd[0] == "rmmod":
        return 1, "rmmod: ERROR: Module aic8800_fdrv is in use"
    return 0, ""
fake_sys()
put(radio.AP_RECORD, json.dumps({"up": True}))
said = radio.reset("wlan0", "1-1.1", "aic8800_fdrv", "AIC8800DC", run=frun, sleep=lambda s: None, wait=1)
check("reset: NetworkManager stopped around it, the USB device unbound and bound, the hotspot started again",
      ran[1:3] == ["systemctl stop NetworkManager", "systemctl start NetworkManager"] and ran[-1] == "systemctl start irate-box-ap.service"
      and (SYS / "bus/usb/drivers/usb/bind").read_text() == "1-1.1" and "unbound and bound again. wlan0 is back. The hotspot started again." in said, (ran, said))
check("  first the port reset, which needs the device's node (none here: said, and on to the next)",
      "USB 1-1.1 (AIC8800DC): port reset failed" in said and said.index("port reset") < said.index("unbound"), said)
ran.clear(); fake_sys(); shutil.rmtree(SYS / "class/net/wlan0"); (SYS / "module/aic8800_fdrv").mkdir(parents=True, exist_ok=True)
said = radio.reset("wlan0", "1-1.1", "aic8800_fdrv", "AIC8800DC", run=frun, sleep=lambda s: None, wait=1)
check("  not back: then authorized 0/1, then the driver unloaded by its loaded name, which may refuse",
      (SYS / "bus/usb/devices/1-1.1/authorized").read_text() == "1" and "de-authorised and authorised again" in said
      and "would not unload" in said and said.endswith("wlan0 did not return. The hotspot started again."), said)
ran.clear(); fake_sys(); shutil.rmtree(SYS / "bus/usb/devices/1-1.1")
said = radio.reset("wlan0", "1-1.1", None, None, run=frun, sleep=lambda s: None, wait=1)
check("  its USB device gone: the hub it hangs off", "USB 1-1, the hub 1-1.1 hangs off unbound" in said, said)
put(radio.AP_RECORD, json.dumps({"up": False})); ran.clear()
radio.reset("wlan0", "1-1.1", None, None, run=frun, sleep=lambda s: None, wait=1)
check("  a hotspot that was not up is not started", "systemctl start irate-box-ap.service" not in ran, ran)
check("  nothing to reset by: said", radio.reset("wlan0", None, "../x", None, run=frun, sleep=lambda s: None) == "This box has no way to reset this radio.")

# A radio on the board's SDIO bus (a Raspberry Pi's brcmfmac): the function rebound, then its SD host, then the driver
# reloaded with the module that holds it (brcmfmac_cyw) unloaded first and loaded again after.
def fake_sdio():
    for d in ("bus", "class", "devices", "module"):
        shutil.rmtree(SYS / d, ignore_errors=True)
    host = SYS / "devices/platform/soc/3f300000.mmcnr"
    func = host / "mmc_host/mmc1/mmc1:0001/mmc1:0001:1"
    func.mkdir(parents=True)
    for drv in ("bus/sdio/drivers/brcmfmac", "bus/platform/drivers/mmc-bcm2835", "bus/platform/devices"):
        (SYS / drv).mkdir(parents=True)
    (func / "driver").symlink_to(SYS / "bus/sdio/drivers/brcmfmac")
    (host / "driver").symlink_to(SYS / "bus/platform/drivers/mmc-bcm2835")
    (SYS / "bus/platform/devices/3f300000.mmcnr").symlink_to(host)
    (SYS / "class/net/wlan0").mkdir(parents=True)
    (SYS / "class/net/wlan0/device").symlink_to(func)
    (SYS / "module/brcmfmac/holders/brcmfmac_cyw").mkdir(parents=True)
check("SDIO: the radio found on the board's bus, with its driver and its SD host", (fake_sdio() or True)
      and radio._sdio_target("wlan0") == {"func": "mmc1:0001:1", "driver": "brcmfmac", "host": "3f300000.mmcnr", "host_driver": "mmc-bcm2835"},
      radio._sdio_target("wlan0"))
sran = []
def srun(*cmd, timeout=60):
    sran.append(" ".join(cmd))
    if cmd[:3] == ("systemctl", "is-active", "--quiet"):
        shutil.rmtree(SYS / "class/net/wlan0", ignore_errors=True)   # gone, and not coming back by itself
        return 0, ""
    return 0, ""
said = radio.reset("wlan0", None, "brcmfmac", None, run=srun, sleep=lambda s: None, wait=1)
check("  not back: the function rebound, then the host, then the driver reloaded with its holder",
      (SYS / "bus/sdio/drivers/brcmfmac/bind").read_text() == "mmc1:0001:1"
      and (SYS / "bus/platform/drivers/mmc-bcm2835/bind").read_text() == "3f300000.mmcnr"
      and said.index("SDIO mmc1:0001:1 (brcmfmac) unbound and bound again") < said.index("SD host 3f300000.mmcnr unbound")
      < said.index("brcmfmac reloaded (with brcmfmac_cyw)"), said)
rm = [c for c in sran if c.startswith(("rmmod", "modprobe"))]
check("  the holder unloaded first, the driver loaded, the holder again", rm == ["rmmod brcmfmac_cyw", "rmmod brcmfmac", "modprobe brcmfmac",
                                                                               "modprobe brcmfmac_cyw"], rm)
fake_sdio()
said = radio.reset("wlan0", None, "brcmfmac", None, run=lambda *c, timeout=60: (0, ""), sleep=lambda s: None, wait=1)
check("  back after the first rebind: nothing heavier", said.startswith("SDIO mmc1:0001:1 (brcmfmac) unbound and bound again. wlan0 is back.")
      and not (SYS / "bus/platform/drivers/mmc-bcm2835/bind").exists(), said)
for d in ("class", "devices", "module/brcmfmac"):
    shutil.rmtree(SYS / d, ignore_errors=True)
(SYS / "module/aic8800_fdrv").mkdir(parents=True, exist_ok=True)   # as the USB radio's checks below had it

# The port reset (what usbreset does), when the device's node answers it.
resets = []
real_port_reset = radio._port_reset
radio._port_reset = lambda target: resets.append(target)
ran.clear(); fake_sys(); put(radio.AP_RECORD, json.dumps({"up": True}))
said = radio.reset("wlan0", "1-1.1", "aic8800_fdrv", "AIC8800DC", run=frun, sleep=lambda s: None, wait=1)
check("  the port reset first; back, so nothing heavier", resets == ["1-1.1"] and "reset at its port. wlan0 is back." in said
      and "unbound" not in said, said)
# For the hotspot's interface: the radio is back when any of the USB device's interfaces is (the hotspot's own is started again after).
ran.clear(); resets.clear(); fake_sys(); (SYS / "bus/usb/devices/1-1.1/1-1.1:1.2/net/wlan0").mkdir(parents=True)
said = radio.reset("ap0", "1-1.1", "aic8800_fdrv", "AIC8800DC", run=frun, sleep=lambda s: None, wait=1)
check("  reset for ap0: back once the device's wlan0 is, not left to try every way", "reset at its port. ap0 is back. The hotspot started again." in said, said)
radio._port_reset = real_port_reset
# The driver loaded again by its module file's name: DKMS installs aic8800_fdrv as aic8800_fdrv_usb.ko.
rel = os.uname().release
put(radio.MODULES / rel / "modules.dep", "updates/dkms/aic8800_fdrv_usb.ko: updates/dkms/aic_load_fw_usb.ko\nupdates/dkms/aic_load_fw_usb.ko:\nkernel/net/x.ko:\n")
check("  the module file for a driver loaded as aic8800_fdrv: aic8800_fdrv_usb", radio._module_file("aic8800_fdrv") == "aic8800_fdrv_usb")
check("  one whose file has its own name: that name", radio._module_file("x") == "x")
def frun2(*cmd, timeout=60):
    ran.append(" ".join(cmd))
    return (0, "") if cmd[:3] != ("systemctl", "is-active", "--quiet") else (0, "")
ran.clear(); fake_sys(); shutil.rmtree(SYS / "class/net/wlan0")
said = radio.reset("wlan0", "1-1.1", "aic8800_fdrv", "AIC8800DC", run=frun2, sleep=lambda s: None, wait=1)
check("  unloaded with rmmod, loaded with modprobe by the file's name", "rmmod aic8800_fdrv" in ran and "modprobe aic8800_fdrv_usb" in ran
      and "aic8800_fdrv reloaded" in said, (ran, said))

# The driver's own resets, as it counts them in sysfs; else from the kernel's journal.
put(radio.PROC / "uptime", "500.00 900.00\n")
par = SYS / "module/aic8800_fdrv/parameters"
put(par / "recoveries", "0\n"); put(par / "recovered_at", "0\n")
check("driver_reset: none counted, nothing (and the journal not asked)", radio.driver_reset(run=lambda *c, **k: (_ for _ in ()).throw(AssertionError(c))) == (None, None))
put(par / "recoveries", "2\n"); put(par / "recovered_at", "470\n")
m, age = radio.driver_reset()
check("  counted: a marker and its age from the uptime", m == "aic8800_fdrv:2:470" and age == 30.0, (m, age))
shutil.rmtree(SYS / "module/aic8800_fdrv/parameters")
jr = lambda *c, **k: (0, "[  431.500000] host kernel: aic8800: firmware not answering: resetting the device to load it afresh")  # noqa: E731
m, age = radio.driver_reset(run=jr)
check("  no count in sysfs: the journal's line", m == "journal:431.5" and age == 68.5, (m, age))
check("  nor there: nothing", radio.driver_reset(run=lambda *c, **k: (1, "")) == (None, None))

# The driver mending itself (aic8800_fdrv recover_on_timeout): its line seen, the radio's interfaces gone for a moment
# are that reset at work, not a failure.
logged = []
dr = cw.RadioWatch(lambda now, iface, kind, text, snapshot=False: logged.append((kind, text)) or {"kind": kind})
k = ["[93.27] cmd_mgr_queue cmd timed-out cmd_mgr->queue_sz:1", "[93.27] aic8800: firmware not answering: resetting the device to load it afresh"]
dr.act(1000, {"wlan0": (["wlan0 is gone"], []), "ap0": (["ap0 is gone"], [])}, k)
check("the driver's own reset: said, not a failure while it works", [x[0] for x in logged] == ["driver-reset"], logged)
dr.act(1060, {"wlan0": ([], []), "ap0": (["ap0 is gone"], [])})
check("  within the grace: nothing more", [x[0] for x in logged] == ["driver-reset"], logged)
dr.act(1200, {"wlan0": (["wlan0 is gone"], []), "ap0": ([], [])})
check("  after the grace, a radio still gone: a failure", [x[0] for x in logged] == ["driver-reset", "failed"], logged)
# The driver's line overwritten before the look read it (a flood in a small kernel log): its count in sysfs says it.
logged.clear()
dr3 = cw.RadioWatch(lambda now, iface, kind, text, snapshot=False: logged.append((kind, text)) or {"kind": kind})
dr3.act(2000, {"wlan0": ([], []), "ap0": ([], [])}, [], ("aic8800_fdrv:0:0", None))
dr3.act(2030, {"wlan0": ([], []), "ap0": (["ap0 is gone"], [])}, [], ("aic8800_fdrv:1:1990", 18.0))
check("the driver's reset known from its count alone: said", [x[0] for x in logged] == ["driver-reset"], logged)
dr3.act(2060, {"wlan0": ([], []), "ap0": (["ap0 is gone"], [])}, [], ("aic8800_fdrv:1:1990", 48.0))
check("  the same count again: not said again", [x[0] for x in logged] == ["driver-reset"], logged)
dr3.act(2200, {"wlan0": ([], []), "ap0": (["ap0 is gone"], [])}, [], ("aic8800_fdrv:1:1990", 188.0))
check("  after the grace, a radio still gone is a failure", ("failed", "ap0 is gone") in logged, logged)

# The write-intensity ladder: how snapshots reach the card at each stop.
written = []
now = [1000.0]
k = cw.SnapshotKeeper(write=lambda text, sync=True: written.append((text, sync)), clock=lambda: now[0])
k.add("a", "everything"); k.add("b", "thrifty")
check("ladder: everything and thrifty: each snapshot written and synced", written == [("a", True), ("b", True)], written)
written.clear(); k.add("c", "gentle")
check("  gentle: written, not synced", written == [("c", False)], written)
written.clear(); k.add("d", "saver"); now[0] += 60; k.add("e", "saver")
check("  saver: held in memory", written == [], written)
k.add("f", "saver", trouble=True)
check("  saver: written at once, all held, when something goes wrong", written == [("def", True)], written)
written.clear(); k.add("g", "saver"); now[0] += cw.SAVER_EVERY; k.add("h", "saver")
check("  saver: and every quarter of an hour", written == [("gh", True)], written)
written.clear(); k.add("i", "saver"); k.flush()
check("  saver: what is held goes to the card when crash watch stops", written == [("i", True)], written)
written.clear(); k.add("j", "saver"); k.add("k", "thrifty")
check("  moving off saver: what was held first, then the new one", written == [("j", True), ("k", True)], written)
(T / "settings.json").write_text(json.dumps({"card_writes": "gentle"}))
cw.STATE = T
check("ladder: crash watch reads the stop from the hub's settings", cw.card_writes() == "gentle")
(T / "settings.json").write_text(json.dumps({"card_writes": "everything!"}))
check("  an unknown stop is thrifty", cw.card_writes() == "thrifty")
(T / "settings.json").unlink(); (T / "settings.json").symlink_to(T / "elsewhere.json")
(T / "elsewhere.json").write_text(json.dumps({"card_writes": "saver"}))
check("  never through a link: thrifty", cw.card_writes() == "thrifty")

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
