# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Security doctor's kernel features (secdoctor.step_kernel): AppArmor present or absent, and units that rely
on systemd's IPAddressDeny or IPAccounting when the kernel cannot attach its cgroup programs. A stand-in kernel
config, sysfs and unit properties; each case by message id. python3 tests/sim_secdoctor_kernel_features.py"""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="sec-kernel-"))
SYS, BOOT, PROC = T / "sys", T / "boot", T / "proc"
for d in (SYS, BOOT, PROC, T / "sysctl"):
    d.mkdir()
os.environ.update(
    HUB_SYSFS=str(SYS),
    HUB_BOOT_DIR=str(BOOT),
    HUB_PROC_DIR=str(PROC),
    HUB_SYSCTL_DIR=str(T / "sysctl"),
    HUB_KERNEL_RELEASE="6.1.115-vendor-rockchip",
    HUB_STATE_DIR=str(T / "state"),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def R(rc=0, out=""):
    return mock.Mock(returncode=rc, stdout=out, stderr="")


def boot(*, bpf, apparmor, journal="", units=None):
    (BOOT / "config-6.1.115-vendor-rockchip").write_text(
        "CONFIG_CGROUP_BPF={}\nCONFIG_SECURITY_APPARMOR={}\n".format("y" if bpf else "n", "y" if apparmor else "n")
    )
    aa = SYS / "module/apparmor"
    if apparmor and not aa.exists():
        aa.mkdir(parents=True)
    elif not apparmor and aa.exists():
        aa.rmdir()

    def cmd(*argv, timeout=60, **kw):
        if argv[0] == "journalctl":
            return R(out=journal)
        return None

    patches = [
        mock.patch.object(sd, "_cmd", cmd),
        mock.patch.object(sd, "_sysctl", lambda key: None),
    ]
    for p in patches:
        p.start()
    return patches, {"units": units or {}}


# A vendor kernel without cgroup BPF or AppArmor, and a unit that relies on both.
patches, ctx = boot(
    bpf=False,
    apparmor=False,
    journal="systemd[1]: bpf-firewall: Failed to load BPF program: Operation not permitted (error 524)\n",
    units={
        "irate-box-ci.service": {"IPAddressDeny": "localhost", "IPAccounting": "yes", "RestrictAddressFamilies": ""},
        "silverbullet.service": {"IPAddressDeny": "any", "RestrictAddressFamilies": "AF_UNIX"},
    },
)
try:
    got = {f["id"]: f for f in sd.step_kernel(ctx)}
    check(
        "no AppArmor: a warning with no cure on the kernel",
        got["kernel-apparmor"]["status"] == "warn"
        and "kernel-apparmor.missing.no-cure" in msg_ids(got["kernel-apparmor"]),
        got["kernel-apparmor"],
    )
    net = got["kernel-cgroup-net"]
    check(
        "units relying on IPAddressDeny/IPAccounting while the kernel ignores them: a problem naming them, with systemd's error",
        net["status"] == "problem"
        and msg_args(net, "kernel-cgroup-net.ignored") == {"n": 2}
        and any("irate-box-ci.service: IPAddressDeny=localhost" == e for e in net["evidence"])
        and any("error 524" in e for e in net["evidence"]),
        net,
    )
    check(
        "a unit that also restricts its address families is said to be kept from the network anyway",
        msg_args(net, "kernel-cgroup-net.elsewhere") == {"units": "silverbullet.service"},
        net,
    )
finally:
    for p in patches:
        p.stop()

# A kernel with both, and a unit that relies on them.
patches, ctx = boot(
    bpf=True,
    apparmor=True,
    units={"irate-box-ci.service": {"IPAddressDeny": "localhost", "IPAccounting": "yes"}},
)
try:
    got = {f["id"]: f for f in sd.step_kernel(ctx)}
    check(
        "cgroup BPF and AppArmor present: ok, with nothing said of a unit that relies on them",
        got["kernel-apparmor"]["status"] == "ok"
        and got["kernel-cgroup-net"]["status"] == "ok"
        and "kernel-cgroup-net.ignored" not in msg_ids(got["kernel-cgroup-net"]),
        {k: v["status"] for k, v in got.items()},
    )
finally:
    for p in patches:
        p.stop()

# The kernel's config is not published and the log says nothing.
patches, ctx = boot(bpf=True, apparmor=True, units={})
for p in patches:
    p.stop()
(BOOT / "config-6.1.115-vendor-rockchip").unlink()
if (SYS / "module/apparmor").exists():
    (SYS / "module/apparmor").rmdir()
patches = [
    mock.patch.object(sd, "_cmd", lambda *a, **k: R()),
    mock.patch.object(sd, "_sysctl", lambda key: None),
]
for p in patches:
    p.start()
try:
    got = {f["id"]: f for f in sd.step_kernel(ctx)}
    check(
        "no published config and no log: said as not known, never as ok",
        got["kernel-apparmor"]["status"] == "note"
        and got["kernel-cgroup-net"]["status"] == "note"
        and "kernel-cgroup-net.unknown" in msg_ids(got["kernel-cgroup-net"]),
        got,
    )
finally:
    for p in patches:
        p.stop()

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
