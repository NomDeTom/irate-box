# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box doctor on what adds nothing here (root/health.py check_idle), offline: meshtasticd failing at each
boot with no LoRa radio, ModemManager with no modem, Bluetooth with nothing paired; each offered to be
switched off, never done for the owner; switched off, kept as it was and offered back; the failed-unit
finding not doubled. Every command is stood in. python3 tests/sim_idle.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="idle-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"))
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import health  # noqa: E402
fails = 0
def check(name, cond, info=""):
    """info: what a failure looked at, for a debugger; not printed (the doctor's records can hold a unit's own words)."""
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += not cond

units = {"meshtasticd.service": {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "enabled", "Result": "start-limit-hit"},
         "ModemManager.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"},
         "bluetooth.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled"}}
said = {"journal": "INFO | Start meshradio init\nWARN | 19:59:22 10.289 No sx1262 radio\n", "mmcli": "No modems were found\n", "paired": ""}
ran = []
def run(*cmd, timeout=60, **kw):
    ran.append(cmd)
    out, code = "", 0
    if cmd[0] == "journalctl":
        out = said["journal"]
    elif cmd[0] == "mmcli":
        out = said["mmcli"]
    elif cmd[0] == "bluetoothctl":
        out = said["paired"]
    elif cmd[0] == "systemctl" and cmd[1] == "--failed":
        out = "meshtasticd.service loaded failed failed Meshtastic Daemon\nother.service loaded failed failed Other\n"
    elif cmd[0] == "systemctl" and cmd[1] in ("disable", "enable", "start", "reset-failed"):
        u = cmd[-1]
        if cmd[1] == "disable":
            units[u].update(UnitFileState="disabled", ActiveState="inactive")
        elif cmd[1] == "enable":
            units[u]["UnitFileState"] = "enabled"
        elif cmd[1] == "start":
            units[u]["ActiveState"] = "active"
    return subprocess.CompletedProcess(cmd, code, out, "")
health.run = run
health.unit_props = lambda u: dict(units.get(u, {"LoadState": "not-found"}))
health.watched_units = lambda: {}

f = {x["id"]: x for x in health.check_idle()}
check("meshtasticd with no LoRa radio: said, with why (its own words) and that systemd gave up",
      "idle:meshtasticd.service" in f and "No sx1262 radio" in f["idle:meshtasticd.service"]["detail"]
      and "gives up" in f["idle:meshtasticd.service"]["detail"], f.get("idle:meshtasticd.service"))
check("  ModemManager with no modem, Bluetooth with nothing paired: said", "idle:ModemManager.service" in f and "idle:bluetooth.service" in f)
check("  each offered to be switched off, asked first; nothing done by looking",
      all(x["actions"][0]["choice"].startswith("idle-off:") and x["actions"][0].get("confirm") for x in f.values())
      and not any(c[1] in ("disable", "stop") for c in ran if c[0] == "systemctl"))
said["journal"] = "INFO | radio ready\n"
said["mmcli"] = "/org/freedesktop/ModemManager1/Modem/0 [Quectel] EG25\n"
said["paired"] = "Device AA:BB:CC:DD:EE:FF Phone\n"
check("  a radio found, a modem there, something paired: none of them said", not health.check_idle())
said.update(journal="WARN | No sx1262 radio\n", mmcli="No modems were found\n", paired="")
fu = [x["id"] for x in health.check_units() if x["id"].startswith(("other-failed", "watched-failed"))]
check("the failed-unit finding not doubled: meshtasticd left to this one, another failed unit still said",
      "other-failed:meshtasticd.service" not in fu and "other-failed:other.service" in fu, fu)
check("the doctor takes idle-off and idle-on for these only", health.CHOICE_RE.match("idle-off:meshtasticd.service")
      and health.CHOICE_RE.match("idle-on:bluetooth.service") and not health.CHOICE_RE.match("idle-off:ssh.service"))
msg = health.fix("idle-off:meshtasticd.service")
rec = json.loads(health.IDLE_OFF.read_text())
check("switched off: disabled and stopped, what it was kept", units["meshtasticd.service"]["UnitFileState"] == "disabled"
      and rec["meshtasticd.service"]["enabled"] is True and rec["meshtasticd.service"]["active"] is False and "switched off" in msg, (msg, rec))
f = {x["id"]: x for x in health.check_idle()}
check("  then: listed as switched off here, with a button to switch it back on; no longer offered off",
      "idle-off:meshtasticd.service" in f and f["idle-off:meshtasticd.service"]["actions"][0]["choice"] == "idle-on:meshtasticd.service"
      and "idle:meshtasticd.service" not in f, list(f))
msg = health.fix("idle-on:meshtasticd.service")
check("switched back on as it was: enabled again, not started (it wasn't running)", units["meshtasticd.service"]["UnitFileState"] == "enabled"
      and units["meshtasticd.service"]["ActiveState"] == "inactive" and "meshtasticd.service" not in json.loads(health.IDLE_OFF.read_text()), msg)
health.fix("idle-off:bluetooth.service"); health.fix("idle-on:bluetooth.service")
check("  one that was running: started again", units["bluetooth.service"]["ActiveState"] == "active")
for bad in ("idle-on:meshtasticd.service",):
    try:
        health.fix(bad); check(f"{bad} when it wasn't switched off here: refused", False)
    except ValueError:
        check(f"{bad} when it wasn't switched off here: refused", True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
