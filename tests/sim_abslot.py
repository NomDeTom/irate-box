# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The inactive A/B slot patcher (root/abslot.py), offline with a recorded run: the binds are made rslave and
unmounted one by one, never with umount -R; the running /etc comes over before apt; the slot's own policy-rc.d is
put back; and the check's differences are kept. python3 tests/sim_abslot.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import abslot

T = Path(tempfile.mkdtemp(prefix="abslot-"))
running, slot, lists = T / "running", T / "slot", T / "lists"
(running / "etc").mkdir(parents=True)
(slot / "etc").mkdir(parents=True)
(slot / "usr" / "sbin").mkdir(parents=True)
(slot / "usr" / "sbin" / "policy-rc.d").write_text("#!/bin/sh\nexit 0\n")
(slot / "etc" / "resolv.conf").symlink_to("/run/systemd/resolve/stub-resolv.conf")
calls = []
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def run(*cmd, timeout=3600):
    calls.append(tuple(cmd))
    if cmd[0] == "chroot" and cmd[-2:] == ("dpkg", "--verify"):
        return subprocess.CompletedProcess(cmd, 1, "??5?????? /etc/x\n", "")
    if cmd[0] == "rsync" and "--dry-run" in cmd:
        return subprocess.CompletedProcess(cmd, 0, ">f+++++++ etc/x\n", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


report = abslot.patch(slot, running, apt_lists=lists, run=run)
check(
    "every bind is rslave, and each is unmounted on its own",
    sum(1 for c in calls if c[:2] == ("mount", "--rbind")) == 4
    and sum(1 for c in calls if c[:2] == ("mount", "--make-rslave")) == 4
    and sum(1 for c in calls if c[0] == "umount") == 5
    and not any(c[:2] == ("umount", "-R") for c in calls),
    calls,
)
copy = next(i for i, c in enumerate(calls) if c[0] == "rsync" and "--delete" in c)
apt = next(i for i, c in enumerate(calls) if c[0] == "chroot" and "apt-get" in c)
resolver = next(
    i for i, c in enumerate(calls) if c[:2] == ("mount", "--bind") and any("resolv.conf" in str(x) for x in c)
)
check(
    "the running /etc comes over before apt and before the resolver is bound, with fstab and resolv.conf left out",
    copy < resolver < apt
    and any("--exclude=fstab" in c for c in calls)
    and any("--exclude=resolv.conf" in c for c in calls),
    (copy, resolver, apt),
)
check(
    "apt works in the slot with the lists outside it",
    any(
        c[0] == "chroot" and "full-upgrade" in c and any("Dir::State::Lists=/var/lib/apt/lists" in a for a in c)
        for c in calls
    ),
    calls,
)
check(
    "the check runs after the unmounts, on one filesystem",
    next(i for i, c in enumerate(calls) if c[0] == "rsync" and "--dry-run" in c)
    > max(i for i, c in enumerate(calls) if c[0] == "umount")
    and any("-x" in c for c in calls if c[0] == "rsync" and "--dry-run" in c),
    calls,
)
check(
    "the check keeps dpkg's differences and rsync's list",
    report["verify"] == ["??5?????? /etc/x"] and report["changes"] == [">f+++++++ etc/x"],
    report,
)
check(
    "the slot's own policy-rc.d is put back",
    (slot / "usr" / "sbin" / "policy-rc.d").read_text() == "#!/bin/sh\nexit 0\n",
)
resolv = slot / "etc" / "resolv.conf"
check(
    "  and its resolver link is put back",
    resolv.is_symlink() and resolv.readlink() == Path("/run/systemd/resolve/stub-resolv.conf"),
    resolv.readlink() if resolv.is_symlink() else None,
)
try:
    abslot.patch(running, running, apt_lists=lists, run=run)
    check("the running system is refused as a slot", False)
except Refused as exc:
    check("the running system is refused as a slot", exc.message["id"] == "abslot.running", exc.message)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
