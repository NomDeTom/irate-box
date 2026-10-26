# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""apt's binary cache kept off the SD card for irate-box's own apt runs.

apt rebuilds /var/cache/apt/pkgcache.bin and srcpkgcache.bin whole whenever a list changes (~90 MB on a Debian
box, more with a second architecture) and never edits them in place (Debian bug #1004583). A run with lists of
its own (the debug archive, a package channel, the kits' pool) also leaves the system's cache built from those,
so the next ordinary apt run rebuilds it again. Each run here builds pkgcache.bin in a folder of its own in RAM,
removed when the run ends, and no srcpkgcache.bin: apt needs a cache file it can write while it holds its lock,
but not the second one. The cost is the cache's size in RAM for the run, and the seconds to build it.
"""

import contextlib
import os
import shutil
import tempfile
from pathlib import Path

RAM = Path(os.environ.get("HUB_APT_RAM", "/dev/shm"))
TOOLS = ("apt-get", "apt-cache")


@contextlib.contextmanager
def folder():
    """A folder in RAM for one run, removed after it (the system's temporary folder where RAM is not writable)."""
    path = Path(tempfile.mkdtemp(prefix="irate-box-apt-", dir=RAM if os.access(RAM, os.W_OK) else None))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def options(where):
    """apt options for a cache in the folder `where`."""
    return ["-o", f"Dir::Cache::pkgcache={where / 'pkgcache.bin'}", "-o", "Dir::Cache::srcpkgcache="]


# The folder every apt run shares while shared() is open (the root helper runs one job at a time).
_shared = None


@contextlib.contextmanager
def shared():
    """One folder for every apt run in the with block, so apt builds its cache once and reuses it while the
    lists it reads stay the same (a cache built per run costs ~10 s on the Lyra each time)."""
    global _shared
    if _shared:
        yield _shared
        return
    with folder() as where:
        _shared = where
        try:
            yield where
        finally:
            _shared = None


@contextlib.contextmanager
def argv(cmd):
    """cmd with the options after the program's name when it is apt-get or apt-cache, else as it is."""
    if Path(cmd[0]).name not in TOOLS:
        yield list(cmd)
    elif _shared:
        yield [cmd[0], *options(_shared), *cmd[1:]]
    else:
        with folder() as where:
            yield [cmd[0], *options(where), *cmd[1:]]
