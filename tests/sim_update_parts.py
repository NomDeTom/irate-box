# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The image's parts after an update (root/partswatch.py), as the Updates doctor reads them: the
snapshot after each dpkg run, a part removed, half-configured packages, a failed automatic update,
a new kernel without its DKMS driver, a service that failed after the update, a held part, and a
toolkit's offline copy behind what apt would install. dpkg, apt, dkms, systemctl and the paths are
stood in. python3 tests/sim_update_parts.py"""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="partswatch-"))
state = T / "state"
(state / "control").mkdir(parents=True)
log = T / "dpkg.log"
modules = T / "modules"
modules.mkdir()
unattended = T / "unattended.log"
os.environ.update(
    HUB_STATE_DIR=str(state),
    HUB_DPKG_LOG=str(log),
    HUB_KERNEL_MODULES=str(modules),
    HUB_UNATTENDED_LOG=str(unattended),
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import partswatch

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def ids(found):
    return {f["id"]: f for f in found}


partswatch._go()
log.write_text("2026-11-02 03:10:00 upgrade chrony:armhf 4.6.1-1 4.6.1-1\n")

# The snapshot keeps the watched parts, and the previous one, for the next doctor run.
partswatch.snapshot(versions={"chrony": "4.6.1", "systemd-timesyncd": "255", "vim": "9.1"})
first = partswatch._read(partswatch.PARTS, {})
partswatch.snapshot(versions={"systemd-timesyncd": "255", "vim": "9.1"})
second = partswatch._read(partswatch.PARTS, {})
check(
    "snapshot: the watched parts, the last one kept, the last dpkg run's time",
    first["current"]["installed"] == {"chrony": "4.6.1", "systemd-timesyncd": "255"}
    and second["previous"]["installed"] == {"chrony": "4.6.1", "systemd-timesyncd": "255"}
    and second["current"]["installed"] == {"systemd-timesyncd": "255"}
    and second["current"]["dpkg_at"] is not None,
    (first, second),
)
found = ids(partswatch._removed())
check(
    "a part removed by the update: named, with the time, the clock's own repair and page",
    found["parts-removed:chrony"]["summary"]["id"] == "update.parts.clock"
    and found["parts-removed:chrony"]["summary"]["args"]["package"] == "chrony"
    and found["parts-removed:chrony"]["go"]["section"] == "clock",
    found,
)


# Half-configured packages: dpkg --audit and apt-get check.
def broken_run(*cmd, timeout=120, **kw):
    if cmd[:2] == ("dpkg", "--audit"):
        return done(1, "Package libfoo is half configured\n")
    if cmd[0] == "apt-get":
        return done(1, "", "E: Unmet dependencies\n")
    return done()


with mock.patch.object(partswatch, "run", broken_run):
    found = ids(partswatch._broken())
check(
    "half-configured packages: a problem, with the two commands that finish them",
    found["packages-broken"]["status"] == "problem"
    and found["packages-broken"]["commands"] == ["dpkg --configure -a", "apt-get -f install"]
    and "libfoo" in " ".join(found["packages-broken"]["evidence"]),
    found,
)

# A failed automatic update: the log's error, and the unit's result.
unattended.write_text(
    "2026-11-02 06:00:01 INFO Starting unattended upgrades script\n"
    "2026-11-02 06:00:09 ERROR could not install security update\n"
)


def unattended_run(*cmd, timeout=120, **kw):
    if cmd[0] == "systemctl":
        return done(0, "Result=success\nExecMainStatus=0\nActiveState=inactive\n")
    return done()


with mock.patch.object(partswatch, "run", unattended_run):
    found = ids(partswatch._unattended())
check(
    "a failed automatic run: the log's last error, and the page",
    found["unattended-failed"]["status"] == "warn"
    and "could not install" in found["unattended-failed"]["summary"]["args"]["line"]
    and found["unattended-failed"]["go"]["section"] == "updates",
    found,
)

# The newest kernel, and a DKMS module that built only for the one before it.
(modules / "6.1.110").mkdir()
(modules / "6.1.120").mkdir()


def dkms_run(*cmd, timeout=120, **kw):
    if cmd[:2] == ("dkms", "status"):
        return done(0, "aic8800/1.0, 6.1.110, armv7l: installed\n")
    return done()


with mock.patch.object(partswatch, "run", dkms_run):
    found = ids(partswatch._kernel())
check(
    "a new kernel without its DKMS driver: a problem before any reboot",
    found["kernel-dkms"]["status"] == "problem"
    and found["kernel-dkms"]["summary"]["args"] == {"kernel": "6.1.120", "modules": "aic8800"},
    found,
)

# A service that failed after the last dpkg run.
log.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} upgrade chrony:armhf 4.6.1-1 4.6.1-1\n")
started = time.strftime("%a %Y-%m-%d %H:%M:%S", time.localtime(time.time() - 10))


def service_run(*cmd, timeout=120, **kw):
    if cmd[0] == "systemctl":
        return done(0, f"ActiveState=failed\nResult=exit-code\nActiveEnterTimestamp={started} UTC\n")
    return done()


with (
    mock.patch.object(partswatch, "run", service_run),
    mock.patch.object(partswatch, "_installed_versions", lambda: {"chrony": "4.6.1"}),
):
    found = ids(partswatch._services())
check(
    "a service failing since the update: the unit and the update's time",
    found["parts-service:chrony.service"]["summary"]["args"]["unit"] == "chrony.service",
    found,
)

# A held part with a newer candidate.


def held_run(*cmd, timeout=120, **kw):
    if cmd[0] == "apt-mark":
        return done(0, "chrony\n")
    if cmd[:2] == ("apt-cache", "policy"):
        return done(0, "chrony:\n  Installed: 4.6.1\n  Candidate: 4.7.0\n")
    return done()


with mock.patch.object(partswatch, "run", held_run):
    found = ids(partswatch._held())
check(
    "a held part with a newer candidate: a note, not a fault",
    found["parts-held:chrony"]["status"] == "note"
    and found["parts-held:chrony"]["summary"]["args"]
    == {"package": "chrony", "installed": "4.6.1", "candidate": "4.7.0"},
    found,
)

# A toolkit's offline copy behind the installed packages.
with (
    mock.patch.object(partswatch, "_kit_packages", lambda: {"chrony": "gps-time"}),
    mock.patch("irate_box.root.kits.installed_state", lambda: {"gps-time": {}}),
    mock.patch(
        "irate_box.root.kits.check_result",
        lambda kit: {"state": "newer", "changes": [{"name": "chrony", "from": "4.6.1", "to": "4.7.0"}]},
    ),
):
    found = ids(partswatch._kits_behind())
check(
    "a toolkit behind the installed packages: named, with the version it would go back to",
    found["parts-kit:gps-time"]["status"] == "warn"
    and found["parts-kit:gps-time"]["summary"]["args"]["kit"] == "gps-time"
    and "4.6.1" in " ".join(found["parts-kit:gps-time"]["evidence"]),
    found,
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
