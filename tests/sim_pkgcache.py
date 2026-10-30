# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The startup packages' .debs kept for a reinstall and the rest of apt's archive removed
(root/pkgcache.py); the reinstall tries the cache before the network; the installer's apt hook.
dpkg and apt are stood in. python3 tests/sim_pkgcache.py"""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="pkgcache-"))
archives, cache = T / "archives", T / "cache"
archives.mkdir()
cache.mkdir()
os.environ.update(HUB_APT_ARCHIVES=str(archives), HUB_PKG_CACHE=str(cache))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import pkgcache, restore

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def deb(directory, name):
    path = directory / name
    path.write_bytes(b"x" * 10)
    return path


versions = {"systemd": "252-1", "nginx": "1.26-1"}
startup = {"systemd", "nginx"}
with (
    mock.patch.object(pkgcache, "installed", lambda: versions),
    mock.patch.object(pkgcache, "startup_packages", lambda: startup),
):
    deb(archives, "systemd_252-1_armhf.deb")
    deb(archives, "nginx_1.24-1_armhf.deb")
    deb(archives, "hello_1-1_all.deb")
    deb(cache, "systemd_250-1_armhf.deb")
    (archives / "partial").mkdir()
    deb(archives / "partial", "systemd_252-1_armhf.deb.part")
    said = pkgcache.tidy(log=lambda *_: None)
    check(
        "tidy: the startup package at its installed version moves to the cache",
        (cache / "systemd_252-1_armhf.deb").is_file() and said["moved"] == 1,
        (said, sorted(p.name for p in cache.iterdir())),
    )
    check(
        "  another version of a startup package, a package that is not one, and a partial file are removed",
        not (archives / "nginx_1.24-1_armhf.deb").exists()
        and not (archives / "hello_1-1_all.deb").exists()
        and not list((archives / "partial").iterdir())
        and not (cache / "systemd_250-1_armhf.deb").exists(),
        sorted(p.name for p in archives.iterdir()),
    )
    state = pkgcache.status()
    check(
        "status: the startup package held, nginx missing, the archive size",
        state["installed"] == 1 and state["missing"] == ["nginx"] and state["bytes"] == 10,
        state,
    )
    check("  a second tidy changes nothing", pkgcache.tidy(log=lambda *_: None)["moved"] == 0)

# apt's epoch encoding in a .deb name: compare the version apt records, keep the file name.
epoch = archives / "bind9-host_1%3a9.20.30-1~deb13u1_armhf.deb"
epoch.write_bytes(b"x" * 10)
with (
    mock.patch.object(pkgcache, "installed", lambda: {"bind9-host": "1:9.20.30-1~deb13u1"}),
    mock.patch.object(pkgcache, "startup_packages", lambda: {"bind9-host"}),
):
    pkgcache.tidy(log=lambda *_: None)
check(
    "an epoch in the file name is decoded for the version, and the file keeps apt's name",
    (cache / "bind9-host_1%3a9.20.30-1~deb13u1_armhf.deb").is_file()
    and pkgcache.parse("bind9-host_1%3a9.20.30-1~deb13u1_armhf.deb")[1] == "1:9.20.30-1~deb13u1",
    sorted(p.name for p in cache.iterdir()),
)

calls = []


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def fake_run(*cmd, timeout=0):
    calls.append(cmd)
    return R(1 if len(calls) == 1 else 0)


target = T / "etc-file"
target.write_text("x")
with (
    mock.patch.object(restore, "run", fake_run),
    mock.patch.object(restore, "is_conffile", lambda path, package: False),
):
    said = restore.from_package(target, "systemd")
check(
    "a reinstall tries the cached .debs first, then the network",
    len(calls) == 2
    and "--no-download" in calls[0]
    and any(str(pkgcache.CACHE) in str(part) for part in calls[0])
    and "--no-download" not in calls[1]
    and said["id"] == "restore.package.done",
    (calls, said),
)
check(
    "the installer writes the apt hook and runs a tidy",
    "pkgcache tidy" in (REPO / "install.sh").read_text()
    and "/etc/apt/apt.conf.d/99-irate-box" in (REPO / "install.sh").read_text(),
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
