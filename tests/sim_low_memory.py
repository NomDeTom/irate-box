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
(T / "cgroup").mkdir()
(T / "units" / "build.slice.d").mkdir(parents=True)
(T / "run").mkdir()
(T / "logs").mkdir()
(T / "sysrq").write_text("")
(T / "cgroup" / "cgroup.controllers").write_text("cpuset cpu io memory pids\n")
(T / "cgroup" / "build.slice").mkdir()
os.environ.update(
    HUB_PROC=str(T / "proc"),
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
check("the guard can tell systemd it is ready", notify.ready() and sock.recv(64) == b"READY=1")
check("the watchdog round is a datagram too", notify.watchdog() and sock.recv(64) == b"WATCHDOG=1")
del os.environ["NOTIFY_SOCKET"]

b = memguard.bounds()
check("the slice is detected but its files are missing", b["supported"] and not b["ok"] and b["missing"])
memguard.install_bounds()
b = memguard.bounds()
check(
    "install_bounds writes every limit and the wrapper",
    b["ok"] and (T / "units" / "bounded-build").stat().st_mode & 0o111,
)
check(
    "the drop-in is the shape systemd reads", "[Slice]" in (T / "units" / "build.slice.d" / "limits.conf").read_text()
)
dropin = T / "units" / "build.slice.d" / "limits.conf"
dropin.write_text(dropin.read_text().replace("MemoryMax=220M", "MemoryMax=300M"))
check("a raised limit is not called missing", memguard.bounds()["ok"], memguard.bounds())

u = memguard.unbounded_builds()
check("only the build outside the slice is named", [x["pid"] for x in u] == [100], u)

h = memguard.headroom()
check("the headroom carries the slice's own OOM counts", h["slice_events"]["oom_kill"] == 1, h)
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
check("the event is written before the action", len(events) == 1 and (events[0].parent / "meminfo").is_file(), events)
dmesg_files = list((T / "logs").glob("*/dmesg"))
check("the event keeps dmesg too", bool(dmesg_files), dmesg_files)
check("the status file is written for the Box doctor", (T / "run" / "status.json").is_file())
check("a D-state task is named in the status", any(t["pid"] == 100 for t in st["d_tasks"]), st["d_tasks"])

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
memguard.WARN_MB, memguard.ACT_MB = 96, 64
memguard.WARN_SWAP_PCT, memguard.ACT_SWAP_PCT = 80, 90
h = memguard.headroom()
check("with enough memory the guard is quiet", h["level"] == "ok", h)

check(
    "undo removes the slice and wrapper", memguard.main(["undo"]) == 0 and not (T / "units" / "bounded-build").exists()
)

print("all passed" if not fails else f"{fails} failures")
sys.exit(1 if fails else 0)
