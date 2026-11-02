# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's side of the low-memory plan (notes: low-memory-guards-plan Part 2): the missing
bounds, a build outside the slice, the headroom levels and the two repairs.
python3 tests/sim_build_bounds.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(f"{'PASS' if cond else 'FAIL'} {name}{'' if cond else f'  {info}'}")
    fails += not cond


def ids(findings):
    return [f["id"] for f in findings]


def choices(findings):
    return [a["choice"] for f in findings for a in f["actions"]]


def bounds(ok=False, missing=None, supported=True):
    return {
        "supported": supported,
        "ok": ok,
        "dropin": "/etc/systemd/system/build.slice.d/limits.conf",
        "wrapper": "/usr/local/sbin/bounded-build",
        "values": {},
        "missing": missing or ["MemoryMax"],
    }


health.memguard.bounds = lambda: bounds()
health.memguard.unbounded_builds = list
f = health.check_build_bounds()
check(
    "a missing bound is a problem, with a repair",
    ids(f) == ["build-bounds"] and f[0]["status"] == "problem" and "build-bounds-install" in choices(f),
    f,
)

health.memguard.bounds = lambda: bounds(ok=True)
f = health.check_build_bounds()
check(
    "a healthy bound is ok and has no button",
    ids(f) == ["build-bounds"] and f[0]["status"] == "ok" and not f[0]["actions"],
    f,
)

health.memguard.unbounded_builds = lambda: [{"pid": 12, "name": "make", "cgroup": "/system.slice/shell"}]
f = health.check_build_bounds()
check(
    "a build outside the slice is named, with its pid",
    "build-unbounded" in ids(f) and "12" in " ".join(f[-1]["evidence"]),
    f,
)
check("an unbounded build has no misleading kill button", not f[-1]["actions"], f)

health.memguard.unbounded_builds = list
health.memguard.bounds = lambda: bounds(supported=False)
f = health.check_build_bounds()
check(
    "a kernel without cgroup v2 is a note, not a fault",
    f[0]["status"] == "note" and not f[0]["actions"],
    f,
)


def head(level):
    return {
        "at": 1,
        "available_mb": 50 if level == "act" else 110,
        "total_mb": 475,
        "swap_used_pct": 95 if level == "act" else 10,
        "level": level,
        "slice_events": {"oom_kill": 1},
    }


health.memguard.headroom = lambda: head("ok")
f = health.check_memory_headroom()
check(
    "headroom is reported when it is fine",
    ids(f) == ["memory-headroom"] and f[0]["status"] == "ok" and not f[0]["actions"],
    f,
)

health.memguard.headroom = lambda: head("warn")
f = health.check_memory_headroom()
check(
    "a warning says the numbers and offers no kill",
    f[0]["status"] == "warn" and not f[0]["actions"],
    f,
)

health.memguard.headroom = lambda: head("act")
f = health.check_memory_headroom()
check(
    "the act level offers the build-only stop",
    f[0]["status"] == "problem" and "memguard-kill-builds" in choices(f),
    f,
)

called = []
health.memguard.install_bounds = lambda: called.append("install")
health.memguard.kill_builds = lambda: "build.slice killed"
check(
    "the repair puts the bounds back",
    health.fix("build-bounds-install") and called == ["install"],
)
check(
    "the repair kills only the build slice",
    health.fix("memguard-kill-builds")["id"] == "repair.memguard.killed",
)

units = [w[0] for w in health._watchdogs()]
check(
    "the memory guard is one of the watchdogs the doctor checks",
    "irate-box-memory-guard.service" in units,
    units,
)

repo = Path(__file__).resolve().parents[1]
inst = (repo / "install.sh").read_text()
uninst = (repo / "uninstall.sh").read_text()
check(
    "install.sh applies the slice, the wrapper and the memory guard",
    "memguard install-bounds" in inst
    and "memguard liveness-apply" in inst
    and "memguard apt-apply" in inst
    and "irate-box-memory-guard.service" in inst
    and "WatchdogSec=30" in inst,
)
check(
    "the watchdogs are tied to systemd (Type=notify, WatchdogSec; no never-firing FailureAction)",
    "Type=notify" in inst and "WatchdogSec=120" in inst and "WatchdogSec=360" in inst and "FailureAction=" not in inst,
)
check(
    "uninstall.sh takes the guard, slice and wrapper away",
    "irate-box-memory-guard.service" in uninst and "bounded-build" in uninst and "build.slice.d" in uninst,
)
check(
    "the Box doctor scans the two new checks",
    "check_build_bounds" in health.scan.__code__.co_names
    or "check_build_bounds" in (repo / "irate_box/root/health.py").read_text(),
)
check(
    "the new actions pass the shared choice pattern",
    all(health.CHOICE_RE.match(c) for c in ("build-bounds-install", "memguard-kill-builds")),
)

print("all passed" if not fails else f"{fails} failures")
sys.exit(1 if fails else 0)
