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
service's watchdog restarts it, never the box. Stdlib only.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from irate_box import notify
from irate_box.msg import M, Refused
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

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
SETTINGS = ETC / "memguard.json"
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
    {
        "dkms",
        "make",
        "gmake",
        "gcc",
        "cc1",
        "cc1plus",
        "ld",
        "collect2",
        "dpkg-buildpackage",
        "dpkg-deb",
    }
)
LIMITS = {
    "MemoryHigh": "180M",
    "MemoryMax": "220M",
    "MemorySwapMax": "64M",
    "CPUQuota": "80%",
    "IOWeight": "20",
    "TasksMax": "64",
}
PRESETS = {
    "small": {
        "MemoryHigh": "120M",
        "MemoryMax": "160M",
        "MemorySwapMax": "32M",
        "CPUQuota": "60%",
        "IOWeight": "10",
        "TasksMax": "48",
    },
    "default": LIMITS,
    "large": {
        "MemoryHigh": "260M",
        "MemoryMax": "320M",
        "MemorySwapMax": "96M",
        "CPUQuota": "100%",
        "IOWeight": "20",
        "TasksMax": "64",
    },
}
LEVELS = {
    "standard": {
        "warn_mb": WARN_MB,
        "act_mb": ACT_MB,
        "warn_swap_pct": WARN_SWAP_PCT,
        "act_swap_pct": ACT_SWAP_PCT,
    },
    "sensitive": {
        "warn_mb": 128,
        "act_mb": 96,
        "warn_swap_pct": 75,
        "act_swap_pct": 85,
    },
    "low": {"warn_mb": 64, "act_mb": 32, "warn_swap_pct": 85, "act_swap_pct": 95},
}
DEFAULT = {
    "limits": "default",
    "levels": "standard",
    "memory_action": "stop",
    "apt_builds": "slow",
    "event_details": "blocked",
    "watchdog_failure": "restart",
    "fallback": "off",
}
APT_UNITS = ("apt-daily-upgrade.service", "unattended-upgrades.service")
LIVENESS_UNITS = {
    "irate-box-crashwatch.service": "120",
    "irate-box-uplink.service": "360",
    "irate-box-memory-guard.service": "30",
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


def load_settings():
    """The administrator's choices, with the safe defaults for anything not set or no longer valid."""
    out = dict(DEFAULT)
    try:
        data = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if data.get("limits") in PRESETS:
        out["limits"] = data["limits"]
    if data.get("levels") in LEVELS:
        out["levels"] = data["levels"]
    for key, values in (
        ("memory_action", ("stop", "record")),
        ("apt_builds", ("leave", "slow", "bound")),
        ("event_details", ("blocked", "all")),
        ("watchdog_failure", ("restart", "reboot")),
        ("fallback", ("off", "earlyoom")),
    ):
        if data.get(key) in values:
            out[key] = data[key]
    return out


def save_settings(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(SETTINGS, json.dumps(data, indent=1, sort_keys=True), mode=0o644)


def limits():
    """The build slice's limits: the administrator's preset, or the box's default."""
    return PRESETS[load_settings()["limits"]]


def levels():
    """The warn and act thresholds: the administrator's level, or the standard one."""
    return LEVELS[load_settings()["levels"]]


SETTING_OF = {
    "limits": "limits",
    "levels": "levels",
    "action": "memory_action",
    "apt": "apt_builds",
    "details": "event_details",
    "watchdog": "watchdog_failure",
    "fallback": "fallback",
}


def set_option(name, value):
    """Change one setting, apply what it changes, and say what happened."""
    key = SETTING_OF.get(name, name)
    choices = {
        "limits": PRESETS,
        "levels": LEVELS,
        "memory_action": {"stop": 1, "record": 1},
        "apt_builds": {"leave": 1, "slow": 1, "bound": 1},
        "event_details": {"blocked": 1, "all": 1},
        "watchdog_failure": {"restart": 1, "reboot": 1},
        "fallback": {"off": 1, "earlyoom": 1},
    }
    if key not in choices or value not in choices[key]:
        raise Refused(
            M(
                "memguard.set.unknown",
                "{key} {value} is not a setting of the memory guard.",
                key=key,
                value=value,
            )
        )
    data = load_settings()
    data[key] = value
    if key == "fallback" and value == "earlyoom" and not shutil.which("earlyoom"):
        raise Refused(
            M(
                "memguard.fallback.missing",
                "earlyoom is not installed. Install it first, then select it here.",
            )
        )
    save_settings(data)
    if key == "limits":
        install_bounds()
        return M("memguard.set.limits", "The build limits are now {name}.", name=value)
    if key == "watchdog_failure":
        apply_liveness()
        return M(
            "memguard.set.watchdog",
            "A stopped watchdog is now handled by {value}.",
            value=value,
        )
    if key == "apt_builds":
        apply_apt()
        return M("memguard.set.apt", "Package updates now {value} their builds.", value=value)
    if key == "fallback":
        apply_fallback()
        return M(
            "memguard.set.fallback",
            "The no-cgroup fallback is now {value}.",
            value=value,
        )
    return M("memguard.set.saved", "The memory guard's setting is saved.")


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


def headroom(settings=None):
    s = settings or load_settings()
    level = LEVELS[s["levels"]]
    m = meminfo()
    events = slice_events()
    out = "ok"
    if m["available_mb"] < level["warn_mb"] or m["swap_used_pct"] >= level["warn_swap_pct"]:
        out = "warn"
    if m["available_mb"] < level["act_mb"] or m["swap_used_pct"] >= level["act_swap_pct"]:
        out = "act"
    return {"at": time.time(), **m, "slice_events": events, "level": out}


def build_pids():
    """The pids in build.slice, from its own cgroup file (the kernel's answer, not a process walk)."""
    try:
        return [int(pid) for pid in (BUILD_SLICE / "cgroup.procs").read_text().split() if pid.isdigit()]
    except OSError:
        return []


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
    want = limits()
    values = {}
    try:
        for line in DROPIN.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() in want:
                values[key.strip()] = value.strip()
    except OSError:
        pass
    missing = [k for k, v in want.items() if values.get(k) != v]
    return {
        "slice": str(BUILD_SLICE),
        "supported": slice_supported(),
        "dropin": str(DROPIN),
        "wrapper": str(WRAPPER),
        "values": values,
        "wanted": want,
        "preset": load_settings()["limits"],
        "missing": missing,
        "ok": slice_supported() and not missing and WRAPPER.is_file() and os.access(WRAPPER, os.X_OK),
    }


def install_bounds():
    """Write the slice and wrapper, then ask systemd to take the slice. The owner's build settings are kept."""
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(
        DROPIN,
        "[Slice]\n" + "".join(f"{k}={v}\n" for k, v in limits().items()),
        mode=0o644,
    )
    WRAPPER.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(WRAPPER, WRAPPER_TEXT, mode=0o755)
    os.chmod(WRAPPER, 0o755)  # nosec B103 - the wrapper is meant to be run
    if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
        _run("systemctl", "daemon-reload")
        _run("systemctl", "start", "build.slice")
    return f"build.slice and {WRAPPER} are in place"


def _dropin(unit, text):
    folder = UNIT_DIR / f"{unit}.d"
    path = folder / "irate-box-memory.conf"
    if text is None:
        try:
            path.unlink()
        except OSError:
            pass
        try:
            folder.rmdir()
        except OSError:
            pass
        return
    folder.mkdir(parents=True, exist_ok=True)
    safeio.write(path, text, mode=0o644)


def apply_liveness():
    """Write the administrator's choice for a watchdog that stops answering."""
    reboot = load_settings()["watchdog_failure"] == "reboot"
    text = (
        "[Unit]\n# Written by irate-box: this service has failed too often and is not answering, so restart the "
        "box.\nStartLimitIntervalSec=60\nStartLimitBurst=3\nStartLimitAction=reboot-force\n"
        if reboot
        else None
    )
    for unit in LIVENESS_UNITS:
        _dropin(unit, text)
    if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
        _run("systemctl", "daemon-reload")


def apply_apt():
    """Bound apt's own builds: slowed, bounded, or left as they are."""
    choice = load_settings()["apt_builds"]
    if choice == "leave":
        text = None
    elif choice == "slow":
        text = f"[Service]\n# Written by irate-box: throttle apt's build without killing it.\nMemoryHigh={limits()['MemoryHigh']}\n"
    else:
        text = (
            "[Service]\n# Written by irate-box: bound apt's build like any other build.\n"
            f"MemoryHigh={limits()['MemoryHigh']}\nMemoryMax={limits()['MemoryMax']}\n"
            f"MemorySwapMax={limits()['MemorySwapMax']}\n"
        )
    for unit in APT_UNITS:
        _dropin(unit, text)
    if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
        _run("systemctl", "daemon-reload")


def apply_fallback():
    """On a kernel without cgroup v2, offer earlyoom as the administrator's fallback."""
    on = load_settings()["fallback"] == "earlyoom" and not slice_supported()
    if os.environ.get("HUB_MEMGUARD_NO_SYSTEMCTL") != "1":
        _run("systemctl", "enable" if on else "disable", "--now", "earlyoom")


def groups():
    """The memory guard's choices for Recovery and monitoring, with the board's defaults and warnings."""
    s = load_settings()
    limits_now = limits()
    groups = [
        {
            "key": "memguard-watchdog",
            "label": M("memguard.watchdog", "When a watched service stops answering"),
            "desc": M(
                "memguard.watchdog.desc",
                "Crash watch, the uplink watchdog and the memory guard each tell systemd they are alive. "
                "A service that stops is restarted. This selects whether a box that keeps failing restarts too.",
            ),
            "current": s["watchdog_failure"],
            "options": [
                {
                    "value": "restart",
                    "label": M("memguard.watchdog.restart", "Restart the service"),
                    "choice": "memguard-watchdog:restart",
                },
                {
                    "value": "reboot",
                    "label": M(
                        "memguard.watchdog.reboot",
                        "Restart the box if it keeps failing",
                    ),
                    "choice": "memguard-watchdog:reboot",
                    "confirm": M(
                        "memguard.watchdog.confirm",
                        "A service that fails three times in a minute restarts the box. A forced restart can lose "
                        "unsaved data, and a card that loses power mid-write can be damaged.",
                    ),
                },
            ],
        },
        {
            "key": "memguard-limits",
            "label": M("memguard.limits", "Limits for builds"),
            "desc": M(
                "memguard.limits.desc",
                "A build runs in build.slice with these limits: {high} soft, {max} hard, {swap} swap, {cpu} CPU. "
                "A hard limit kills the build, not the box.",
                high=limits_now["MemoryHigh"],
                max=limits_now["MemoryMax"],
                swap=limits_now["MemorySwapMax"],
                cpu=limits_now["CPUQuota"],
            ),
            "current": s["limits"],
            "options": [
                {
                    "value": "small",
                    "label": M("memguard.limits.small", "Small (160 MB)"),
                    "choice": "memguard-limits:small",
                },
                {
                    "value": "default",
                    "label": M("memguard.limits.default", "Box default (220 MB)"),
                    "choice": "memguard-limits:default",
                },
                {
                    "value": "large",
                    "label": M("memguard.limits.large", "Large (320 MB)"),
                    "choice": "memguard-limits:large",
                },
            ],
        },
        {
            "key": "memguard-levels",
            "label": M("memguard.levels", "When memory is low"),
            "desc": M(
                "memguard.levels.desc",
                "The available memory and swap at which the guard warns, and at which it acts. "
                "Standard is {warn} MB and {act} MB on this box.",
                warn=LEVELS[s["levels"]]["warn_mb"],
                act=LEVELS[s["levels"]]["act_mb"],
            ),
            "current": s["levels"],
            "options": [
                {
                    "value": "standard",
                    "label": M("memguard.levels.standard", "Standard"),
                    "choice": "memguard-levels:standard",
                },
                {
                    "value": "sensitive",
                    "label": M("memguard.levels.sensitive", "Earlier"),
                    "choice": "memguard-levels:sensitive",
                },
                {
                    "value": "low",
                    "label": M("memguard.levels.low", "Only when very low"),
                    "choice": "memguard-levels:low",
                },
            ],
        },
        {
            "key": "memguard-action",
            "label": M("memguard.action", "When memory is nearly gone"),
            "desc": M(
                "memguard.action.desc",
                "Stop the build in build.slice, or only record what the box was doing. "
                "The guard never acts on a slice with no build in it.",
            ),
            "current": s["memory_action"],
            "options": [
                {
                    "value": "stop",
                    "label": M("memguard.action.stop", "Stop builds"),
                    "choice": "memguard-action:stop",
                },
                {
                    "value": "record",
                    "label": M("memguard.action.record", "Record only"),
                    "choice": "memguard-action:record",
                },
            ],
        },
        {
            "key": "memguard-apt",
            "label": M("memguard.apt", "Builds started by package updates"),
            "desc": M(
                "memguard.apt.desc",
                "A kernel or driver update can build through DKMS, outside build.slice. Slow throttles it without "
                "killing it. Bounded gives it the same limits as other builds.",
            ),
            "current": s["apt_builds"],
            "options": [
                {
                    "value": "leave",
                    "label": M("memguard.apt.leave", "Left as they are"),
                    "choice": "memguard-apt:leave",
                },
                {
                    "value": "slow",
                    "label": M("memguard.apt.slow", "Slowed"),
                    "choice": "memguard-apt:slow",
                },
                {
                    "value": "bound",
                    "label": M("memguard.apt.bound", "Bounded like other builds"),
                    "choice": "memguard-apt:bound",
                    "confirm": M(
                        "memguard.apt.confirm",
                        "A package update stopped halfway can leave packages half set up.",
                    ),
                },
            ],
        },
        {
            "key": "memguard-details",
            "label": M("memguard.details", "Details kept on a memory event"),
            "desc": M(
                "memguard.details.desc",
                "Memory and blocked tasks are written with the event. Every task adds the kernel's full task list, "
                "which can stall a serial console for seconds.",
            ),
            "current": s["event_details"],
            "options": [
                {
                    "value": "blocked",
                    "label": M("memguard.details.blocked", "Memory and blocked tasks"),
                    "choice": "memguard-details:blocked",
                },
                {
                    "value": "all",
                    "label": M("memguard.details.all", "Every task"),
                    "choice": "memguard-details:all",
                    "confirm": M(
                        "memguard.details.confirm",
                        "The kernel prints every task to the log. On a board whose console is a serial line, that can "
                        "stall the box for seconds.",
                    ),
                },
            ],
        },
    ]
    if not slice_supported():
        groups.append(
            {
                "key": "memguard-fallback",
                "label": M("memguard.fallback", "This kernel has no cgroup v2"),
                "desc": M(
                    "memguard.fallback.desc",
                    "Without cgroup v2 the build slice cannot hold a build. earlyoom can kill the largest process "
                    "when memory runs out, but it cannot limit only builds.",
                ),
                "current": s["fallback"],
                "options": [
                    {
                        "value": "off",
                        "label": M("memguard.fallback.off", "Leave it"),
                        "choice": "memguard-fallback:off",
                    },
                    {
                        "value": "earlyoom",
                        "label": M("memguard.fallback.earlyoom", "Use earlyoom"),
                        "choice": "memguard-fallback:earlyoom",
                    },
                ],
            }
        )
    return groups


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
    out = _run("dmesg", timeout=5)
    if out.stdout:
        safeio.write(folder / "dmesg", out.stdout, mode=0o600)
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
            name, rest = (
                stat[stat.find("(") + 1 : stat.rfind(")")],
                stat[stat.rfind(")") + 2 :].split(),
            )
            if rest and rest[0] == "D":
                out.append({"pid": int(pid), "name": name})
    except OSError:
        pass
    return out


def kill_builds():
    """Kill only build.slice, through the kernel's cgroup file; systemd is the fallback."""
    if not build_pids():
        return "build.slice is empty"
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
    h = headroom(load_settings())
    return {
        **h,
        "bounds": bounds(),
        "settings": load_settings(),
        "unbounded": unbounded_builds(),
        "d_tasks": d_tasks(),
    }


def _write_status(data):
    try:
        safeio.write(STATUS, json.dumps(data, indent=1, sort_keys=True), mode=0o644)
    except OSError:
        pass


def new_build(acted, acted_pids, pids):
    """Whether the slice holds a build other than the one the guard stopped: a new one is acted on again."""
    return acted and bool(pids) and sorted(pids) != sorted(acted_pids)


def run(once=False):
    notify.ready()
    warn_since = act_since = None
    warned = acted = critical = noted = False
    acted_at = last_status = 0.0
    acted_pids = []
    while True:
        chosen = load_settings()
        h = headroom(chosen)
        now = h["at"]
        level = h["level"]
        if level == "ok":
            warn_since = act_since = None
            warned = acted = critical = noted = False
            acted_at = 0.0
            acted_pids = []
        elif level == "warn":
            warn_since = warn_since or now
            act_since = None
        else:
            act_since = act_since or now
            warn_since = None
        action = None
        # Walk /proc only when there is pressure, or when the act state needs the build list.
        extra = {}
        if level != "ok":
            extra = {"unbounded": unbounded_builds(), "d_tasks": d_tasks()}
        pids = build_pids() if level == "act" else []
        if level == "act" and new_build(acted, acted_pids, pids):
            # A build started after the one stopped: this episode now has something new to act on.
            acted = critical = False
            acted_at = 0.0
        if level == "warn" and now - (warn_since or now) >= WARN_AFTER and not warned:
            warned = True
            folder = record("warn", {**h, **extra, "build_pids": pids})
            action = f"warned: {folder}"
        if level == "act":
            if not acted and pids and now - (act_since or now) >= ACT_AFTER:
                folder = record("act", {**h, **extra, "build_pids": pids})
                _sysrq("m")
                _sysrq("w")
                if chosen["event_details"] == "all":
                    _sysrq("t")
                action = f"{kill_builds()}: {folder}" if chosen["memory_action"] == "stop" else f"recorded: {folder}"
                acted, acted_at, acted_pids = True, now, sorted(pids)
            elif not acted and not noted and not pids and now - (act_since or now) >= ACT_AFTER:
                # Nothing bounded to stop: record the pressure once, but never touch an empty slice.
                folder = record("pressure", {**h, **extra, "build_pids": []})
                action = f"pressure without a bounded build: {folder}"
                noted = True
            elif acted and not critical and now - acted_at >= 60:
                folder = record("critical", {**h, **extra, "build_pids": pids})
                action = f"still low after the kill: {folder}"
                critical = True
        st = {**h, **extra, "action": action, "build_pids": pids, "bounds": bounds()}
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
