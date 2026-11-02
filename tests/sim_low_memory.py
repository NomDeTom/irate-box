# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The memory guard and the build bounds (notes: low-memory-guards-plan Parts 1-3): the slice's
limits, a build outside it, the box's headroom, and the guard's one action — kill only build.slice.
Offline: /proc, /sys/fs/cgroup and systemd stood in.
python3 tests/sim_low_memory.py"""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="memguard-"))
(T / "proc").mkdir()
(T / "etc").mkdir()
(T / "cgroup").mkdir()
(T / "units" / "build.slice.d").mkdir(parents=True)
(T / "run").mkdir()
(T / "logs").mkdir()
(T / "sysrq").write_text("")
(T / "cgroup" / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
(T / "cgroup" / "build.slice").mkdir()
os.environ.update(
    HUB_PROC=str(T / "proc"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_CGROUP_ROOT=str(T / "cgroup"),
    HUB_SYSTEMD_UNIT_DIR=str(T / "units"),
    HUB_BUILD_WRAPPER=str(T / "units" / "bounded-build"),
    HUB_MEMGUARD_STATUS=str(T / "run" / "status.json"),
    HUB_LOW_MEMORY_DIR=str(T / "logs"),
    HUB_SYSRQ=str(T / "sysrq"),
    HUB_MEMGUARD_NO_SYSTEMCTL="1",
    HUB_MEMGUARD_WARN_MB="1000",
    HUB_MEMGUARD_ACT_MB="1000",
    HUB_MEMGUARD_WARN_AFTER="0",
    HUB_MEMGUARD_ACT_AFTER="0",
    HUB_MEMGUARD_STATUS_EVERY="0",
    HUB_MEMGUARD_KILL_EVERY="0",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box import notify
from irate_box.root import memguard

fails = 0


def check(name, cond, info=""):
    global fails
    print(f"{'PASS' if cond else 'FAIL'} {name}{'' if cond else f'  {info}'}")
    fails += not cond


def mem(available=200, total=475, swap_total=237, swap_free=150):
    (T / "proc" / "meminfo").write_text(
        f"MemTotal: {total * 1024} kB\nMemAvailable: {available * 1024} kB\n"
        f"SwapTotal: {swap_total * 1024} kB\nSwapFree: {swap_free * 1024} kB\n"
    )


def proc(pid, comm, cgroup, state="S"):
    p = T / "proc" / str(pid)
    p.mkdir(exist_ok=True)
    (p / "comm").write_text(comm + "\n")
    (p / "cgroup").write_text(f"0::{cgroup}\n")
    (p / "stat").write_text(f"{pid} ({comm}) {state} 1 1 1 0 -1\n")
    (p / "wchan").write_text("0\n")


mem()
proc(100, "make", "/system.slice/apt.service", "D")
proc(101, "gcc", "/build.slice/dkms-build.scope", "S")
proc(102, "python3", "/system.slice/hub.service", "S")
(T / "cgroup" / "build.slice" / "memory.events").write_text("low 0\nhigh 2\nmax 1\noom 1\noom_kill 1\n")
(T / "cgroup" / "build.slice" / "cgroup.procs").write_text("101\n")
(T / "cgroup" / "build.slice" / "cgroup.kill").write_text("")

check("no NOTIFY_SOCKET: notify is a no-op", notify.ready() is False)
sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
sock.bind(str(T / "notify.sock"))
os.environ["NOTIFY_SOCKET"] = str(T / "notify.sock")
check(
    "the guard can tell systemd it is ready",
    notify.ready() and sock.recv(64) == b"READY=1",
)
check(
    "the watchdog round is a datagram too",
    notify.watchdog() and sock.recv(64) == b"WATCHDOG=1",
)
del os.environ["NOTIFY_SOCKET"]

b = memguard.bounds()
check(
    "the slice is detected but its files are missing",
    b["supported"] and not b["ok"] and b["missing"],
)
memguard.install_bounds()
b = memguard.bounds()
check(
    "install_bounds writes every limit and the wrapper",
    b["ok"] and (T / "units" / "bounded-build").stat().st_mode & 0o111,
)
check(
    "the drop-in is the shape systemd reads",
    "[Slice]" in (T / "units" / "build.slice.d" / "limits.conf").read_text(),
)
check(
    "the default preset is the one installed",
    memguard.bounds()["preset"] == "default",
    memguard.bounds(),
)
memguard.set_option("limits", "large")
check(
    "choosing a larger preset writes its hard limit",
    "MemoryMax=320M" in (T / "units" / "build.slice.d" / "limits.conf").read_text() and memguard.bounds()["ok"],
    memguard.bounds(),
)
memguard.set_option("limits", "default")

u = memguard.unbounded_builds()
check("only the build outside the slice is named", [x["pid"] for x in u] == [100], u)

h = memguard.headroom()
check(
    "the headroom carries the slice's own OOM counts",
    h["slice_events"]["oom_kill"] == 1,
    h,
)
check("the low-memory action is read from MemAvailable", h["level"] == "act", h)
check(
    "a build that replaces the one the guard stopped is acted on again",
    memguard.new_build(True, [101], [102])
    and not memguard.new_build(True, [101], [101])
    and not memguard.new_build(True, [101], [])
    and not memguard.new_build(False, [101], [102]),
)

real_run = memguard._run
memguard._run = lambda *cmd, **kw: (
    subprocess.CompletedProcess(cmd, 0, "fake dmesg\n", "") if cmd and cmd[0] == "dmesg" else real_run(*cmd, **kw)
)
st = memguard.run(once=True)
check(
    "a low-memory round records an event and kills only build.slice",
    st["action"] and (T / "cgroup" / "build.slice" / "cgroup.kill").read_text() == "1",
    st,
)
events = list((T / "logs").glob("*/event.json"))
check(
    "the event is written before the action",
    len(events) == 1 and (events[0].parent / "meminfo").is_file(),
    events,
)
dmesg_files = list((T / "logs").glob("*/dmesg"))
check("the event keeps dmesg too", bool(dmesg_files), dmesg_files)
check(
    "the status file is written for the Box doctor",
    (T / "run" / "status.json").is_file(),
)
check(
    "a D-state task is named in the status",
    any(t["pid"] == 100 for t in st["d_tasks"]),
    st["d_tasks"],
)

# Pressure with no build in the slice is recorded once and never touches the empty slice.
(T / "cgroup" / "build.slice" / "cgroup.procs").write_text("")
(T / "cgroup" / "build.slice" / "cgroup.kill").write_text("")
st = memguard.run(once=True)
check(
    "pressure with no bounded build is recorded, not killed",
    st["action"].startswith("pressure") and (T / "cgroup" / "build.slice" / "cgroup.kill").read_text() == "",
    st,
)

# As on a real box (a Banana Pi M2 Zero, 2026-11-02): bounded-build's systemd-run --scope puts the build in a scope
# below the slice, and the slice's own cgroup.procs stays empty. The guard still sees it.
scope = T / "cgroup" / "build.slice" / "run-u42.scope"
scope.mkdir(parents=True, exist_ok=True)
(scope / "cgroup.procs").write_text("201\n202\n")
check(
    "a build in a scope under the slice is seen, though the slice's own cgroup.procs is empty",
    memguard.build_pids() == [201, 202],
    memguard.build_pids(),
)
(scope / "cgroup.procs").write_text("")

mem(available=300, swap_free=200)
memguard.LEVELS["standard"] = {
    "warn_mb": 96,
    "act_mb": 64,
    "warn_swap_pct": 80,
    "act_swap_pct": 90,
}
h = memguard.headroom()
check("with enough memory the guard is quiet", h["level"] == "ok", h)

check(
    "undo removes the slice and wrapper",
    memguard.main(["undo"]) == 0 and not (T / "units" / "bounded-build").exists(),
)

# The settings model: each choice changes the guard or its drop-ins.
mem(available=50, swap_free=1)
(T / "cgroup" / "build.slice" / "cgroup.procs").write_text("101\n")
(T / "cgroup" / "build.slice" / "cgroup.kill").write_text("")
memguard.set_option("memory_action", "record")
st = memguard.run(once=True)
check("record only leaves the build slice alone", st["action"].startswith("recorded"), st)
memguard.set_option("memory_action", "stop")
memguard.set_option("levels", "low")
check(
    "the selected level is the low one",
    memguard.levels()["act_mb"] == 32,
    memguard.levels(),
)
memguard.set_option("apt_builds", "bound")
apt_drop = T / "units" / "apt-daily-upgrade.service.d" / "irate-box-memory.conf"
check(
    "bounded apt builds get a hard limit",
    "MemoryMax=" in apt_drop.read_text(),
    apt_drop,
)
memguard.set_option("apt_builds", "leave")
check("leaving apt alone removes its drop-in", not apt_drop.exists(), apt_drop)
memguard.set_option("watchdog_failure", "reboot")
live = {unit: T / "units" / f"{unit}.d" / "irate-box-memory.conf" for unit in memguard.LIVENESS_UNITS}
liveness_ok = True
for unit, period in memguard.LIVENESS_UNITS.items():
    text = live[unit].read_text()
    liveness_ok = liveness_ok and (
        "StartLimitAction=reboot-force" in text
        and f"StartLimitIntervalSec={memguard.liveness_interval(*period)}" in text
        and "StartLimitBurst=3" in text
    )
check(
    "the reboot choice writes each unit's own start limit, so a hang reaches it",
    liveness_ok
    and memguard.liveness_interval(30, 5) == 165
    and memguard.liveness_interval(120, 10) == 450
    and memguard.liveness_interval(360, 10) == 1170,
    {unit: path.read_text() for unit, path in live.items()},
)
memguard.set_option("watchdog_failure", "restart")
check("restart removes them again", not any(path.exists() for path in live.values()), live)
memguard.set_option("details", "all")
check("every-task detail is saved", memguard.load_settings()["event_details"] == "all")
check(
    "the doctor's groups include the guard's choices",
    {"memguard-limits", "memguard-action"} <= {g["key"] for g in memguard.groups()},
)

# Every memguard command the installer and uninstaller run is one memguard knows: install.sh once called two that did
# not exist (liveness-apply, apt-apply), and only a real install on a Banana Pi M2 Zero showed it (2 problems).
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
called = sorted(
    {
        m
        for f in ("install.sh", "uninstall.sh")
        for m in re.findall(r"irate-box\"? memguard ([a-z][a-z-]+)", (ROOT / f).read_text())
    }
)
unknown = []
for name in called:
    if name in ("run", "kill-builds"):  # the guard itself, and a kill: not to be started from a test
        continue
    try:
        with open(os.devnull, "w") as quiet:
            out, sys.stdout = sys.stdout, quiet
            try:
                rc = memguard.main([name])
            finally:
                sys.stdout = out
    except Exception as exc:  # noqa: BLE001 - any failure is the finding
        rc = f"{type(exc).__name__}: {exc}"
    if rc not in (0, None):
        unknown.append((name, rc))
check(
    "every memguard command install.sh and uninstall.sh run is one memguard knows",
    called and not unknown,
    (called, unknown),
)

print("all passed" if not fails else f"{fails} failures")
sys.exit(1 if fails else 0)
