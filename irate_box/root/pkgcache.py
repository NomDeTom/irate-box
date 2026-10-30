# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The .debs of the packages the box boots and serves with, kept for a reinstall; the rest of apt's
archive removed after each run.

Debian's mirrors drop superseded versions, and unattended-upgrades leaves every .deb in
/var/cache/apt/archives (hundreds of MB on a box that has been updated for a while). The startup
packages' files are the ones the boot check puts back with `apt-get install --reinstall`; their
installed versions are moved into a small cache before the rest is removed. An apt hook runs the
tidy after every dpkg run, so an unattended upgrade is tidied too, not only an install.

  irate-box pkgcache tidy      move the startup packages' installed .debs, remove the rest
  irate-box pkgcache refresh   fetch what the startup cache lacks (online; best effort)
  irate-box pkgcache status    what the cache holds, and what is missing

Stdlib only.
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from irate_box.msg import M
from irate_box.root import aptram

ARCHIVES = Path(os.environ.get("HUB_APT_ARCHIVES", "/var/cache/apt/archives"))
CACHE = Path(os.environ.get("HUB_PKG_CACHE", "/var/cache/irate-box/reinstall"))
LIMIT_MB = int(os.environ.get("HUB_PKG_CACHE_MB", "64"))
DEB = re.compile(r"^([a-z0-9][a-z0-9+.-]+)_([^_]+)_([a-z0-9]+)\.deb$")


def run(cmd, timeout=1800, cwd=None):
    with aptram.argv(cmd) as full:
        return subprocess.run(full, capture_output=True, text=True, timeout=timeout, cwd=cwd)  # noqa: TID251


def installed():
    out = run(["dpkg-query", "-W", "-f", "${Package}\\t${Version}\\n"], timeout=120)
    if out.returncode:
        raise OSError((out.stderr or out.stdout or "dpkg-query failed").strip()[-200:])
    versions = {}
    for line in out.stdout.splitlines():
        name, _, version = line.partition("\\t")
        if name:
            versions[name.split(":")[0]] = version
    return versions


def startup_packages():
    from irate_box.root import health

    try:
        return set(health.startup_packages())
    except OSError:
        return set(health.STARTUP_PACKAGES)


def parse(name):
    found = DEB.match(name)
    return (found.group(1), found.group(2), found.group(3)) if found else (None, None, None)


def _cache_name(name, version, arch):
    return f"{name}_{version}_{arch}.deb"


def _installed_in_cache(versions):
    out = {}
    if not CACHE.is_dir():
        return out
    for path in CACHE.glob("*.deb"):
        name, version, arch = parse(path.name)
        if name in versions and versions[name] == version:
            out[path.name] = path
    return out


def tidy(log=print):
    """Move the startup packages' installed versions into CACHE, remove every other .deb and the
    partial downloads, then keep CACHE under its limit (oldest first). Returns what it did."""
    versions = installed()
    startup = startup_packages()
    ARCHIVES.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o755)
    moved = removed = 0
    for path in sorted(ARCHIVES.glob("*.deb")):
        name, version, arch = parse(path.name)
        if name in startup and versions.get(name) == version:
            dest = CACHE / _cache_name(name, version, arch)
            if not dest.exists():
                os.replace(path, dest)
                moved += 1
            else:
                path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        removed += 1
    partial = ARCHIVES / "partial"
    if partial.is_dir():
        for path in partial.iterdir():
            path.unlink(missing_ok=True)
    # A package upgraded since it was cached: its old version is no longer the one to reinstall.
    for path in CACHE.glob("*.deb"):
        name, version, _arch = parse(path.name)
        if versions.get(name) != version:
            path.unlink(missing_ok=True)
            removed += 1
    cached = sorted(CACHE.glob("*.deb"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in cached)
    while cached and total > LIMIT_MB << 20:
        path = cached.pop(0)
        total -= path.stat().st_size
        path.unlink(missing_ok=True)
        removed += 1
    log(
        M(
            "pkgcache.tidy",
            "Toolkit .debs: moved {moved}, removed {removed}, cache {mb} MB.",
            moved=moved,
            removed=removed,
            mb=total >> 20,
        )
    )
    return {"moved": moved, "removed": removed, "bytes": total}


def refresh(log=print):
    """Fetch the installed version of each startup package the cache lacks. Needs the network and
    the version still in apt's lists; a version Debian has dropped is left out, with the count."""
    versions, startup = installed(), startup_packages()
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o755)
    have = _installed_in_cache(versions)
    fetched = failed = 0
    for name in sorted(startup):
        version = versions.get(name)
        if not version:
            continue
        if any(parse(n)[0] == name and parse(n)[1] == version for n in have):
            continue
        out = run(["apt-get", "download", f"{name}={version}"], timeout=600, cwd=CACHE)
        if out.returncode == 0:
            fetched += 1
        else:
            failed += 1
    log(
        M(
            "pkgcache.refresh",
            "Startup packages: fetched {fetched}. Not available: {failed}.",
            fetched=fetched,
            failed=failed,
        )
    )
    return {"fetched": fetched, "failed": failed}


def status():
    versions, startup = installed(), startup_packages()
    have = _installed_in_cache(versions)
    missing = sorted(n for n in startup if not any(parse(k)[0] == n and parse(k)[1] == versions.get(n) for k in have))
    return {
        "installed": len(have),
        "startup": len(startup),
        "missing": missing,
        "bytes": sum(p.stat().st_size for p in have.values()),
        "archives": sum(p.stat().st_size for p in ARCHIVES.glob("*.deb")) if ARCHIVES.is_dir() else 0,
        "at": time.time(),
    }


if __name__ == "__main__":
    if os.geteuid() != 0:
        sys.exit("run as root")
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "tidy":
        tidy()
    elif command == "refresh":
        refresh()
    elif command == "status":
        print(status())
    else:
        sys.exit("usage: irate-box pkgcache tidy | refresh | status")
