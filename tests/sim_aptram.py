# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""apt's cache in RAM for irate-box's own apt runs (root/aptram.py): apt-get and apt-cache get options putting
pkgcache.bin in a folder of their own and no srcpkgcache.bin, other commands are left as they are, the folder
goes when the run ends, and runs inside shared() use one folder. Then each runner that uses it: kits, pkgwatch,
security, and the updates doctor's apt-get update, with subprocess stood in. python3 tests/sim_aptram.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RAM = Path(tempfile.mkdtemp(prefix="aptram-"))
os.environ["HUB_APT_RAM"] = str(RAM)
sys.path.insert(0, str(REPO))

from irate_box.root import aptram

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def opt(cmd, key):
    """The value of `-o key=value` in cmd, or None."""
    for a, b in zip(cmd, cmd[1:], strict=False):
        if a == "-o" and b.startswith(key + "="):
            return b.split("=", 1)[1]
    return None


with aptram.argv(["apt-get", "update", "-q"]) as cmd:
    cache = opt(cmd, "Dir::Cache::pkgcache")
    check(
        "apt-get: the cache in a folder of its own in RAM, no srcpkgcache.bin, the options after the program",
        cmd[0] == "apt-get"
        and cmd[1] == "-o"
        and cmd[-2:] == ["update", "-q"]
        and cache
        and Path(cache).parent.parent == RAM
        and Path(cache).name == "pkgcache.bin"
        and opt(cmd, "Dir::Cache::srcpkgcache") == "",
        cmd,
    )
    check("  the folder is there while the run is", Path(cache).parent.is_dir())
check("  and gone after it", not Path(cache).parent.exists())
with aptram.argv(["/usr/bin/apt-cache", "show", "gdb"]) as cmd:
    check("apt-cache by its full path too", opt(cmd, "Dir::Cache::pkgcache") is not None, cmd)
with aptram.argv(["dpkg", "-S", "/lib/modules"]) as cmd:
    check("any other command as it is", cmd == ["dpkg", "-S", "/lib/modules"], cmd)
with aptram.shared() as where:
    with aptram.argv(["apt-get", "check"]) as a, aptram.argv(["apt-cache", "show", "gdb"]) as b:
        same = opt(a, "Dir::Cache::pkgcache") == opt(b, "Dir::Cache::pkgcache") == str(where / "pkgcache.bin")
    with aptram.shared() as inner:
        nested = inner == where
    check("inside shared(): every run uses the one folder (nested, the same)", same and nested)
check("  gone when shared() ends", not where.exists() and not list(RAM.iterdir()))
os.environ["HUB_APT_RAM"] = str(RAM / "missing")
aptram.RAM = RAM / "missing"
with aptram.folder() as f:
    check("RAM not writable: the system's temporary folder instead", f.is_dir() and f.parent != aptram.RAM)
aptram.RAM = RAM

# The runners: each passes apt-get with the options to subprocess.
seen = []
real_run = subprocess.run


def fake_run(cmd, *a, **kw):
    seen.append(list(cmd))
    return subprocess.CompletedProcess(cmd, 0, "", "")


subprocess.run = fake_run
from irate_box.root import kits, pkgwatch, security

kits.run(["apt-get", "update", "-q"])
pkgwatch.run(["apt-get", "update", "-q"])
security._waiting()
with open(os.devnull, "w") as log:
    security._apt_logged(log, ["apt-get", "update"], 60)
subprocess.run = real_run
check(
    "kits, pkgwatch and security run apt with the cache in RAM",
    len(seen) == 4 and all(opt(c, "Dir::Cache::pkgcache") for c in seen),
    seen,
)
src = (REPO / "irate_box" / "root" / "hub_control.py").read_text()
check(
    "the updates doctor's apt-get update too",
    'with aptram.argv(["apt-get", "update", "-q"]) as cmd:\n        out = run(*cmd, timeout=300)' in src,
)
check("nothing left in RAM", not list(RAM.iterdir()))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
