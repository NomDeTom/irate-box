# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's image and kernel checks (health.check_image, check_apt_recommends, check_kernel_features), on
stand-in release files, apt configuration, a kernel config and the image's newest release: the image's name, release,
Armbian commit and age; a newer release or an old image; apt's recommends off as a note; cgroup BPF and AppArmor
present or absent; a clock module with no kernel driver. Each case by message id.
python3 tests/sim_image_health.py"""

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="image-health-"))
ETC, SYS, PROC, BOOT = T / "etc", T / "sys", T / "proc", T / "boot"
for d in (ETC, SYS, PROC, BOOT):
    d.mkdir()
os.environ.update(
    HUB_ETC_DIR=str(ETC),
    HUB_STATE_DIR=str(T / "state"),
    HUB_OS_RELEASE=str(T / "os-release"),
    HUB_MPWRD_RELEASE=str(T / "mPWRD-release"),
    HUB_ARMBIAN_RELEASE=str(T / "armbian-release"),
    HUB_ARMBIAN_IMAGE_RELEASE=str(T / "armbian-image-release"),
    HUB_SYSFS=str(SYS),
    HUB_PROC=str(PROC),
    HUB_BOOT_DIR=str(BOOT),
    HUB_KERNEL_RELEASE="6.1.115-vendor-rockchip",
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


NOW = time.time()


def R(rc=0, out="", err=""):
    return mock.Mock(returncode=rc, stdout=out, stderr=err)


def world(*, version="0.2.1", built_days=10, recommends="1", config=None, apparmor=None, journal="", newest=None):
    (T / "os-release").write_text(
        'NAME="Debian GNU/Linux"\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n'
    )
    (T / "mPWRD-release").write_text(f'VERSION="{version}"\nARMBIAN_COMMIT="8de11a0"\n')
    stamp = NOW - built_days * 86400
    os.utime(T / "mPWRD-release", (stamp, stamp))
    if (T / "armbian-release").exists():
        (T / "armbian-release").unlink()
    (T / "armbian-image-release").write_text("ARMBIAN_COMMIT=8de11a0\n")
    os.utime(T / "armbian-image-release", (stamp, stamp))
    conf = "CONFIG_CGROUP_BPF=%s\nCONFIG_SECURITY_APPARMOR=%s\n" % (
        "y" if config is None or config.get("bpf") else "n",
        "y" if config is None or config.get("apparmor") else "n",
    )
    (BOOT / "config-6.1.115-vendor-rockchip").write_text(conf)
    if apparmor is None:
        apparmor = config is None or config.get("apparmor")
    aa = SYS / "module/apparmor"
    if apparmor and not aa.exists():
        aa.mkdir(parents=True)
    elif not apparmor and aa.exists():
        aa.rmdir()

    def run(*cmd, timeout=60, **kw):
        if cmd[0] == "apt-config":
            return R(out=f'APT::Install-Recommends "{recommends}";\n')
        if cmd[0] == "journalctl":
            return R(out=journal)
        return R()

    class Resp(io.BytesIO):
        pass

    def urlopen(request, timeout=8):
        if newest is None:
            raise OSError("offline")
        return Resp(json.dumps({"tag_name": newest, "published_at": "2026-09-01T00:00:00Z"}).encode())

    return [
        mock.patch.object(health, "run", run),
        mock.patch.object(health.shutil, "which", lambda n: "/usr/bin/apt-config" if n == "apt-config" else None),
        mock.patch.object(health.urllib.request, "urlopen", urlopen),
    ]


def with_world(**case):
    patches = world(**case)
    for p in patches:
        p.start()
    return patches


def ids(findings, fid):
    return {f["id"]: f for f in findings}[fid]


# 1. An old mPWRD-OS image with a newer release, recommends off, no cgroup BPF, no AppArmor, a userspace clock.
(ETC / "rtc.json").write_text(json.dumps({"chip": "ds3231", "bus": 2, "addr": "0x68", "mode": "userspace"}))
patches = with_world(
    version="0.2.1", built_days=200, recommends="0", newest="v0.2.3", config={"bpf": False, "apparmor": False}
)
try:
    img = ids(health.check_image(), "os-image")
    check(
        "a newer release: a warning naming it, the image and the commit",
        img["status"] == "warn"
        and "image.newer" in msg_ids(img)
        and msg_args(img, "image.newer")["project"] == "mPWRD-OS/mPWRD-OS"
        and msg_args(img, "image.newer")["tag"] == "v0.2.3"
        and "image.commit" in msg_ids(img),
        img,
    )
    rec = ids(health.check_apt_recommends(), "apt-recommends")
    check("recommends off is said once, as a note", rec["status"] == "note" and "recommends.off" in msg_ids(rec), rec)
    feats = {f["id"]: f for f in health.check_kernel_features()}
    check(
        "no cgroup BPF: a warning saying IPAddressDeny and IPAccounting do nothing",
        feats["kernel-cgroup-bpf"]["status"] == "warn"
        and "kernel-bpf.missing.why" in msg_ids(feats["kernel-cgroup-bpf"]),
        feats["kernel-cgroup-bpf"],
    )
    check(
        "no AppArmor: a warning, no cure on this kernel",
        feats["kernel-apparmor"]["status"] == "warn" and "kernel-apparmor.missing" in msg_ids(feats["kernel-apparmor"]),
        feats["kernel-apparmor"],
    )
    check(
        "a clock module on irate-box's own reader: a note, not a fault",
        feats["kernel-rtc"]["status"] == "note" and "kernel-rtc.userspace" in msg_ids(feats["kernel-rtc"]),
        feats["kernel-rtc"],
    )
finally:
    for p in patches:
        p.stop()

# 2. A current image that matches the newest release, recommends on, the kernel features present, no clock module.
(ETC / "rtc.json").unlink()
patches = with_world(version="0.2.3", built_days=10, recommends="1", newest="v0.2.3")
try:
    img = ids(health.check_image(), "os-image")
    check("the newest release, freshly built: ok", img["status"] == "ok" and "image.newest-same" in msg_ids(img), img)
    rec = ids(health.check_apt_recommends(), "apt-recommends")
    check("recommends on: ok", rec["status"] == "ok" and "recommends.on" in msg_ids(rec), rec)
    feats = {f["id"]: f for f in health.check_kernel_features()}
    check(
        "cgroup BPF and AppArmor present: ok, and no clock-module line without one",
        feats["kernel-cgroup-bpf"]["status"] == "ok"
        and feats["kernel-apparmor"]["status"] == "ok"
        and "kernel-rtc" not in feats,
        feats,
    )
finally:
    for p in patches:
        p.stop()

# 3. Offline, older than six months: the age is the warning; the newest release was not asked.
patches = with_world(version="0.2.1", built_days=200, recommends="1", newest=None)
try:
    img = ids(health.check_image(), "os-image")
    check(
        "offline and more than six months old: a warning, and the lookup said not done",
        img["status"] == "warn" and "image.old" in msg_ids(img) and "image.offline" in msg_ids(img),
        img,
    )
finally:
    for p in patches:
        p.stop()

# 4. No readable release files at all.
for p in (T / "os-release", T / "mPWRD-release", T / "armbian-image-release"):
    if p.exists():
        p.unlink()
patches = with_world()
for p in (T / "os-release", T / "mPWRD-release", T / "armbian-image-release"):
    if p.exists():
        p.unlink()
try:
    img = ids(health.check_image(), "os-image")
    check(
        "no release file: a warning that says it could not read the image",
        img["status"] == "warn" and "image.unreadable" in msg_ids(img),
        img,
    )
finally:
    for p in patches:
        p.stop()

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
