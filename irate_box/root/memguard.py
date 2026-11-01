# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Bounded builds, and a small guard for a box that runs out of memory.

A build on a 475 MB Lyra can starve sshd, systemd and the hub before anything notices: there is no
PSI and no hung-task detector on the vendor kernel. This module gives builds a cgroup of their own
and watches the box's headroom from a process that is itself protected:

  build.slice      MemoryHigh/MemoryMax, swap, CPU and I/O limits, installed by install.sh
  bounded-build    the wrapper that starts a command inside build.slice
  memguard run     the guard: writes low-memory events, and kills only build.slice when it must

The guard never reboots on memory pressure. It records the state first, then stops the builder; the
watchdog liveness rule (WatchdogSec/FailureAction) is what may restart the box, and only through
systemd. Stdlib only.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from irate_box import notify
from irate_box.root import safeio

INTERVAL = float(os.environ.get("HUB_MEMGUARD_INTERVAL", "5"))
WARN_MB = int(os.environ.get("HUB_MEMGUARD_WARN_MB", "96"))
ACT_MB = int(os.environ.get("HUB_MEMGUARD_ACT_MB", "64"))
WARN_SWAP_PCT = int(os.environ.get("HUB_MEMGUARD_WARN_SWAP", "80"))
ACT_SWAP_PCT = int(os.environ.get("HUB_MEMGUARD_ACT_SWAP", "90"))
WARN_AFTER = float(os.environ.get("HUB_MEMGUARD_WARN_AFTER", "15"))
ACT_AFTER = float(os.environ.get("HUB_MEMGUARD_ACT_AFTER", "30"))
KILL_EVERY = float(os.environ.get("HUB_MEMGUARD_KILL_EVERY", "60"))
STATUS_EVERY = float(os.environ.get("HUB_MEMGUARD_STATUS_EVERY", "30"))

CGROUP_ROOT = Path(os.environ.get("HUB_CGROUP_ROOT", "/sys/fs/cgroup"))
BUILD_SLICE = Path(os.environ.get("HUB_BUILD_SLICE", str(CGROUP_ROOT / "build.slice")))
UNIT_DIR = Path(os.environ.get("HUB_SYSTEMD_UNIT_DIR", "/etc/systemd/system"))
WRAPPER = Path(os.environ.get("HUB_BUILD_WRAPPER", "/usr/local/sbin/bounded-build"))
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
SRQ = Path(os.environ.get("HUB_SYSRQ", "/proc/sysrq-trigger"))
STATUS = Path(os.environ.get("HUB_MEMGUARD_STATUS", "/run/irate-box-memguard/status.json"))
EVENTS = Path(os.environ.get("HUB_LOW_MEMORY_DIR", "/var/log.nand/low-memory"))
NAND_LOGS = EVENTS.parent

BUILD_COMMS = frozenset(
    {"dkms", "make", "gmake", "gcc", "cc1", "cc1plus", "ld", "collect2", "dpkg-buildpackage", "dpkg-deb"}
)
LIMITS = {
    "MemoryHigh": "180M",
    "MemoryMax": "220M",
    "MemorySwapMax": "64M",
    "CPUQuota": "80%",
    "IOWeight": "20",
    "TasksMax": "64",
}
DROPIN = UNIT_DIR / "build.slice.d" / "limits.conf"
WRAPPER_TEXT = """#!/bin/sh
# Written by irate-box: a build inside build.slice, with the box's memory, CPU and I/O kept for it.
exec systemd-run --quiet --scope --slice=build.slice --setenv=MAKEFLAGS=-j1 -- "$@"
"""


def _run(*cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _int_file(path):
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0


def meminfo():
    """MemTotal, MemAvailable, SwapTotal and SwapFree in kB."""
    out = {}
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                out[key] = int(rest.split()[0])
    except (OSError, ValueError):
        pass
    total = out.get("MemTotal", 0)
    avail = out.get("MemAvailable", 0)
    sw_total = out.get("SwapTotal", 0)
    sw_free = out.get("SwapFree", 0)
    return {
        "total_mb": round(total / 1024),
        "available_mb": round(avail / 1024),
        "swap_total_mb": round(sw_total / 1024),
        "swap_used_pct": round(100 * (sw_total - sw_free) / sw_total) if sw_total else 0,
    }


def slice_events():
    """The cgroup's own counters: oom_kill, max and high."""
    out = {}
    try:
        for line in (BUILD_SLICE / "memory.events").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in ("oom", "oom_kill", "max", "high"):
                out[key] = int(value)
    except (OSError, ValueError):
        pass
    return out


def headroom():
    m = meminfo()
    events = slice_events()
    level = "ok"
    if m["available_mb"] < WARN_MB or m["swap_used_pct"] >= WARN_SWAP_PCT:
        level = "warn"
    if m["available_mb"] < ACT_MB or m["swap_used_pct"] >= ACT_SWAP_PCT:
        level = "act"
    return {"at": time.time(), **m, "slice_events": events, "level": level}


def _cgroup(pid):
    try:
        for line in (PROC / str(pid) / "cgroup").read_text().splitlines():
            if line.startswith("0::"):
                return line[3:] or "/"
    except OSError:
        pass
    return ""


def unbounded_builds():
    """Known build processes not in build.slice, as name and pid."""
    out = []
    try:
        pids = [d for d in os.listdir(PROC) if d.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            comm = (PROC / pid / "comm").read_text().strip()
        except OSError:
            continue
        if comm not in BUILD_COMMS:
            continue
        cg = _cgroup(pid)
        if cg == "/build.slice" or cg.startswith("/build.slice/"):
            continue
        out.append({"pid": int(pid), "name": comm, "cgroup": cg or "?"})
    return sorted(out, key=lambda b: b["pid"])


def slice_supported():
    try:
        controllers = (CGROUP_ROOT / "cgroup.controllers").read_text().split()
    except OSError:
        return False
    return {"memory", "cpu", "io"}.issubset(controllers)


def bounds():
    """What is installed, and whether every limit this plan asks for is there."""
    values = {}
    try:
        for line in DROPIN.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() in LIMITS:
                values[key.strip()] = value.strip()
    except OSError:
        pass
    missing = [k for k, v in LIMITS.items() if values.get(k) != v]
    return {
        "slice": str(BUILD_SLICE),
        "supported": slice_supported(),
        "dropin": str(DROPIN),
        "wrapper": str(WRAPPER),
        "values": values,
        "missing": missing,
        "ok": slice_supported() and not missing and WRAPPER.is_file() and os.access(WRAPPER, os.X_OK),
    }


def install_bounds():
    """Write the slice and wrapper, then ask systemd to take the slice. The owner's build settings are kept."""
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(DROPIN, "[Slice]\n" + "".join(f"{k}={v}\n" for k, v in LIMITS.items()), mode=0o644)
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(WRAPPER, WRAPPER_TEXT, mode=0o755)
    os.chmod(WRAPPER, 0o755)  # nosec B103 - the wrapper is meant to be run
    if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
        _run("systemctl", "daemon-reload")
        _run("systemctl", "start", "build.slice")
    return f"build.slice and {WRAPPER} are in place"


def _event_dir(now):
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime(now))
    base = EVENTS if os.access(NAND_LOGS, os.W_OK) else safeio.ROOT_STATE / "low-memory"
    try:
        base.mkdir(parents=True, exist_ok=True)
        out = base / stamp
        out.mkdir(exist_ok=True)
        return out
    except OSError:
        out = safeio.root_dir("low-memory") / stamp
        out.mkdir(exist_ok=True)
        return out


def _copy(path, folder, name):
    try:
        safeio.write(folder / name, path.read_text(), mode=0o600)
    except OSError:
        pass


def record(kind, data, now=None):
    """The state before an action, in the low-memory record; the JSON is what the Box doctor reads."""
    now = now or time.time()
    folder = _event_dir(now)
    data = {"at": now, "kind": kind, **data}
    safeio.write(folder / "event.json", json.dumps(data, indent=1, sort_keys=True), mode=0o600)
    for path, name in (
        (PROC / "meminfo", "meminfo"),
        (PROC / "swaps", "swaps"),
        (PROC / "loadavg", "loadavg"),
        (BUILD_SLICE / "memory.events", "memory.events"),
        (BUILD_SLICE / "cgroup.procs", "cgroup.procs"),
    ):
        _copy(path, folder, name)
    return folder


def _sysrq(letter):
    try:
        SRQ.write_text(letter)
    except OSError:
        pass


def d_tasks():
    out = []
    try:
        for pid in os.listdir(PROC):
            if not pid.isdigit():
                continue
            try:
                stat = (PROC / pid / "stat").read_text()
            except OSError:
                continue
            name, rest = stat[stat.find("(") + 1 : stat.rfind(")")], stat[stat.rfind(")") + 2 :].split()
            if rest and rest[0] == "D":
                out.append({"pid": int(pid), "name": name})
    except OSError:
        pass
    return out


def kill_builds():
    """Kill only build.slice, through the kernel's cgroup file; systemd is the fallback."""
    try:
        (BUILD_SLICE / "cgroup.kill").write_text("1")
        return "build.slice killed"
    except OSError:
        r = _run("systemctl", "kill", "--signal=SIGKILL", "build.slice", timeout=10)
        return (
            "build.slice killed with systemctl"
            if r.returncode == 0
            else f"build.slice not killed: {r.stderr.strip()[-120:]}"
        )


def status():
    h = headroom()
    return {
        **h,
        "bounds": bounds(),
        "unbounded": unbounded_builds(),
        "d_tasks": d_tasks(),
    }


def _write_status(data):
    try:
        safeio.write(STATUS, json.dumps(data, indent=1, sort_keys=True), mode=0o644)
    except OSError:
        pass


def run(once=False):
    notify.ready()
    warn_since = act_since = None
    warned = False
    last_kill = last_status = 0.0
    while True:
        h = headroom()
        now = h["at"]
        level = h["level"]
        if level == "ok":
            warn_since = act_since = None
            warned = False
        elif level == "warn":
            warn_since = warn_since or now
            act_since = None
        else:
            act_since = act_since or now
            warn_since = None
        action = None
        if level == "warn" and now - (warn_since or now) >= WARN_AFTER and not warned:
            warned = True
            folder = record("warn", h)
            action = f"warned: {folder}"
        if level == "act" and now - (act_since or now) >= ACT_AFTER and now - last_kill >= KILL_EVERY:
            folder = record("act", {**h, "d_tasks": d_tasks(), "unbounded": unbounded_builds()})
            _sysrq("m")
            _sysrq("w")
            if d_tasks():
                _sysrq("t")
            action = f"{kill_builds()}: {folder}"
            last_kill = now
        st = {**h, "action": action, "bounds": bounds(), "unbounded": unbounded_builds(), "d_tasks": d_tasks()}
        if action or now - last_status >= STATUS_EVERY:
            _write_status(st)
            last_status = now
        notify.watchdog()
        if once:
            return st
        time.sleep(INTERVAL)


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        run()
    elif cmd == "status":
        print(json.dumps(status(), indent=1, sort_keys=True))
    elif cmd == "install-bounds":
        print(install_bounds())
    elif cmd == "kill-builds":
        print(kill_builds())
    elif cmd == "undo":
        for path in (DROPIN, WRAPPER):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            DROPIN.parent.rmdir()
        except OSError:
            pass
        if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
            _run("systemctl", "daemon-reload")
        print("the build bounds are gone")
    else:
        print("usage: memguard.py run | status | install-bounds | kill-builds | undo")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
