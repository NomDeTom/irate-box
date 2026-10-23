# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box doctor's check that its own watchdogs are alive (health.check_watchdogs_alive, item 162): crash watch and the
uplink watchdog each write their time; one whose service is active but which has been silent for three of its rounds
is stuck, and a process of it waiting in the kernel (state D) is named with where it waits. Offline: systemctl,
/proc and the status files stood in.
python3 tests/sim_watchdogs_alive.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="alive-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_PROC=str(T / "proc"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import uplink
from irate_box.root import crashwatch, health

fails = 0


def check(name, cond, info=""):
    global fails
    print(f"{'PASS' if cond else 'FAIL'} {name}{'' if cond else f'  {info}'}")
    fails += not cond


NOW = 1_800_000_000.0
crash_status, up_status = T / "crash-status.json", T / "uplink.json"
crashwatch.STATUS = crash_status
uplink.status_path = lambda: up_status
units = {
    "irate-box-crashwatch.service": {"active": True, "pid": "100", "since": 0},
    "irate-box-uplink.service": {"active": True, "pid": "200", "since": 0},
}
UPTIME = 5000.0


def fake_run(*cmd, timeout=60, **kw):
    unit = cmd[-1]
    u = units.get(unit, {})
    if cmd[1] == "is-active":
        return subprocess.CompletedProcess(cmd, 0 if u.get("active") else 3, "", "")
    prop = cmd[cmd.index("-p") + 1]
    value = {"MainPID": u.get("pid", "0"), "ActiveEnterTimestampMonotonic": str(int(u.get("since", 0) * 1e6))}[prop]
    return subprocess.CompletedProcess(cmd, 0, value + "\n", "")


health.run = fake_run
proc = T / "proc"


def process(pid, name, state, ppid, wchan="0"):
    (proc / str(pid)).mkdir(parents=True, exist_ok=True)
    (proc / str(pid) / "stat").write_text(f"{pid} ({name}) {state} {ppid} 1 1 0 -1\n")
    (proc / str(pid) / "wchan").write_text(wchan)


proc.mkdir(parents=True)
(proc / "uptime").write_text(f"{UPTIME} 100.0\n")
process(100, "python3", "S", 1)
process(200, "python3", "S", 1)


def doctor():
    return {f["id"]: f for f in health.check_watchdogs_alive(now=NOW)}


crash_status.write_text(json.dumps({"at": NOW - 120}))
up_status.write_text(json.dumps({"at": NOW - 40, "settings": {"check": 60}}))
check("both reporting in time: nothing said", doctor() == {}, doctor())

crash_status.write_text(json.dumps({"at": NOW - 1200}))
f = doctor().get("alive:irate-box-crashwatch")
check(
    "crash watch active but silent 20 min (3 rounds are 15): a problem, saying since when",
    f and f["status"] == "problem" and "has not reported since" in str(f["summary"]) and not f["notes"],
    f,
)

process(101, "ip", "D", 100, "rtnl_lock")
f = doctor().get("alive:irate-box-crashwatch")
check(
    "  a child of it waiting in the kernel: named, with where it waits",
    f and f["evidence"] == ["ip (101) waiting in the kernel at rtnl_lock"] and f["notes"],
    f and f["evidence"],
)
process(300, "sshd", "D", 1, "rtnl_lock")
check(
    "  another program waiting is not counted as the watchdog's",
    doctor()["alive:irate-box-crashwatch"]["evidence"] == ["ip (101) waiting in the kernel at rtnl_lock"],
)

up_status.write_text(json.dumps({"at": NOW - 250, "settings": {"check": 120}}))
check(
    "the uplink watchdog checking every 2 min, last 250 s ago: within its rounds",
    "alive:irate-box-uplink" not in doctor(),
)
up_status.write_text(json.dumps({"at": NOW - 400, "settings": {"check": 120}}))
check("  400 s ago (3 rounds are 360, at least 300): silent", "alive:irate-box-uplink" in doctor())

crash_status.unlink()
units["irate-box-crashwatch.service"]["since"] = UPTIME - 60
check("no report yet, started a minute ago: nothing said yet", "alive:irate-box-crashwatch" not in doctor())
units["irate-box-crashwatch.service"]["since"] = UPTIME - 3600
f = doctor().get("alive:irate-box-crashwatch")
check("  no report after an hour: a problem, since it started", f and "since it started" in str(f["summary"]), f)

units["irate-box-crashwatch.service"]["active"] = False
check("a stopped watchdog: not this check's (the units check says so)", "alive:irate-box-crashwatch" not in doctor())

print(f"\n{fails} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
