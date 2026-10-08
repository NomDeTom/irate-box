# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Crash watch (root/crashwatch.py; Tom, 2026-10-08) and the box doctor's findings on it, offline: the
kernel's flood counted not copied, a snapshot from a stand-in /proc and /sys, a crash filed at the next
boot (and not after a clean shutdown), a failing radio told from a link going down, pre-emption at
each level with its guards, the hang settings, and the doctor's switches. Every command that would
change the system is stood in. python3 tests/sim_crashwatch.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="crashwatch-"))
PROC, SYS = T / "proc", T / "sys"
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_PROC=str(PROC), HUB_SYSFS=str(SYS),
                  HUB_KMSG=str(T / "no-kmsg"), HUB_SYSCTL_DIR=str(T / "sysctl.d"), HUB_MODULES_LOAD=str(T / "modules-load.d"),
                  HUB_SYSTEMD_CONF_DIR=str(T / "system.conf.d"))
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import crashwatch as cw, health  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)

# A stand-in /proc and /sys: a Lyra-ish board.
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

# --- pre-emption, at each level ---
def drive(level, guard=None, ticks=((0, True), (30, True), (210, True), (240, False))):
    did, log = [], []
    def lg(now, iface, kind, text, snapshot=False):
        e = {"at": now, "iface": iface, "kind": kind, "text": text}; log.append(e); return e
    p = cw.Preempt(level, lambda i: did.append(("reset", i)) or "reset", lambda: did.append(("reboot",)), lambda step, now: guard and guard(step), lg)
    for t, bad in ticks:
        p.act(t, {"wlan0": (["wlan0 is gone"] if bad else [], [])})
    return did, [e["kind"] for e in log]
did, kinds = drive("warn")
check("warn: the failure noted and its recovery, nothing done", did == [] and kinds == ["failed", "recovered"], (did, kinds))
did, kinds = drive("radio")
check("radio: reset once in the failure, never a reboot", did == [("reset", "wlan0")] and kinds == ["failed", "reset", "recovered"], (did, kinds))
did, kinds = drive("reboot")
check("reboot: reset, then restarted when it isn't back after 3 minutes", did == [("reset", "wlan0"), ("reboot",)] and "reboot" in kinds, (did, kinds))
did, kinds = drive("reboot", guard=lambda step: "2 guests are on the hotspot")
check("  held by a guard: said once, nothing done", did == [] and kinds.count("held") <= 2 and kinds[0] == "failed", (did, kinds))
did, kinds = drive("reboot", guard=lambda step: "a build is running" if step == "reboot" else None)
check("  the radio reset, the reboot held while a build runs", did == [("reset", "wlan0")] and "held" in kinds, (did, kinds))
did, kinds = drive("off")
check("off: nothing, not even noted", did == [] and kinds == ["failed", "recovered"] or kinds == [], (did, kinds))
burst = cw.Preempt("radio", lambda i: "reset", lambda: None, lambda s, n: None, lambda *a, **k: {"kind": a[2]})
ev = burst.act(0, {"wlan0": ([], ["e"] * 3)}) + burst.act(30, {"wlan0": ([], ["e"] * 3)})
check("a burst of driver errors over two looks: a failure", [e["kind"] for e in ev] == ["failed", "reset"], ev)

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
for bad in (("preempt", "moon"), ("snapshots", "maybe"), ("rm", "-rf")):
    try:
        cw.set_option(*bad); ok = False
    except ValueError:
        ok = True
    check(f"set {bad}: refused", ok)

# --- the box doctor ---
cw.set_option("preempt", "warn"); cw.set_option("panic", "off"); cw.set_option("watchdog", "off")
now = 4300.0
cw._write(cw.STATUS, {"at": now, "radios": known, "floods": {"4addr #": 4000}, "flood_window": 3600})
cw._write(cw.EVENTS, [{"at": now - 600, "iface": "wlan0", "kind": "failed", "text": "wlan0 is gone"}])
f = {x["id"]: x for x in health.check_crashwatch(now=now)}
check("the doctor: the last crash said, with its last snapshot, and what would have restarted it",
      f["crash-last"]["status"] == "warn" and "2 times this week" in f["crash-last"]["detail"] and "=== " in f["crash-last"]["detail"]
      and "hang settings below" in f["crash-last"]["fix"], f["crash-last"])
check("  the kernel flood, with its rate", f["crash-flood"]["status"] == "warn" and "about 1.1 a second" in f["crash-flood"]["detail"], f["crash-flood"])
check("  the radio watched, its failure in the last day, pre-emption's other levels to choose, a confirm before restarting",
      f["crash-radio"]["status"] == "warn" and "wlan0 (AIC8800DC)" in f["crash-radio"]["detail"] and "wlan0 failed" in f["crash-radio"]["detail"]
      and [a["choice"] for a in f["crash-radio"]["actions"]] == ["crashwatch-preempt:off", "crashwatch-preempt:radio", "crashwatch-preempt:reboot"]
      and f["crash-radio"]["actions"][-1].get("confirm"), f["crash-radio"])
check("  a frozen box: nothing restarts it, the two switches offered", "pull the plug" in f["crash-hang"]["detail"]
      and [a["choice"] for a in f["crash-hang"]["actions"]] == ["crashwatch-panic:on", "crashwatch-watchdog:on"], f["crash-hang"])
for good in ("crashwatch-preempt:reboot", "crashwatch-panic:on", "crashwatch-snapshots:off"):
    check(f"  the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("crashwatch-preempt:moon", "crashwatch-rm:on", "crashwatch-panic:on;reboot"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))
print(health.fix("crashwatch-preempt:radio"))
check("  a switch pressed: the setting changed", cw.load_settings()["preempt"] == "radio")

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
