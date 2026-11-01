# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Presets written to an Armbian card (root/rkpresets.py), offline: the release and its first login read from a
recorded image's files; every preset line for the choices; values Armbian's first login cannot hold refused; a
release and a firstlogin the box does not know refused; the presets and the box's key written into a mounted root
filesystem, and root's key not written through a link; a card's root partition found from a recorded lsblk and
mounted, written and unmounted. python3 tests/sim_rkpresets.py"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkpresets-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ROOT_STATE_DIR=str(T / "root-state"),
    HUB_RK_MOUNT=str(T / "mount"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import rkcard, rkpresets

FIX = REPO / "tests" / "fixtures"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Refused as exc:
        return exc.message["id"]
    return None


rockchip = (FIX / "armbian-release-rockchip.txt").read_text()
allwinner = (FIX / "armbian-release-allwinner.txt").read_text()
firstlogin = (FIX / "armbian-firstlogin-26.05.txt").read_text()
check(
    "an Armbian release read: its family and version, for a Rockchip and an Allwinner image",
    rkpresets.release(rockchip) == ("rockchip", "26.05.0-trunk")
    and rkpresets.release(allwinner) == ("sunxi", "26.11.0"),
)
check(
    "  an image that is not Armbian, and a release this box has not read, refused",
    refused(rkpresets.release, "PRETTY_NAME=Debian\n") == "rkpresets.not-armbian"
    and refused(rkpresets.release, rockchip.replace("26.05", "27.02")) == "rkpresets.release-unknown",
)
check("a first login that names every preset the box writes is known", rkpresets.check_firstlogin(firstlogin) is None)
check(
    "  one that does not is refused",
    refused(rkpresets.check_firstlogin, "PRESET_ROOT_PASSWORD=1\n") == "rkpresets.firstlogin-unknown",
)

choices = {
    "root_password": "Root16charsAbcd1",
    "user": "salty-parrot",
    "user_password": "User16charsAbcd2",
    "real_name": "Salty Parrot",
    "locale": "en_GB.UTF-8",
    "timezone": "Europe/London",
    "ssid": "GOODNET",
    "wifi_password": "eight888",
    "country": "GB",
}
text = rkpresets.preset_text(choices)
check(
    "the presets: each setting on its own line, the WiFi set from them and no locale menu",
    'PRESET_ROOT_PASSWORD="Root16charsAbcd1"' in text
    and 'PRESET_USER_NAME="salty-parrot"' in text
    and 'PRESET_USER_PASSWORD="User16charsAbcd2"' in text
    and 'PRESET_NET_WIFI_SSID="GOODNET"' in text
    and 'PRESET_NET_WIFI_KEY="eight888"' in text
    and 'PRESET_CONNECT_WIRELESS="yes"' in text
    and 'SET_LANG_BASED_ON_LOCATION="no"' in text,
    text,
)
no_wifi = rkpresets.preset_text({**choices, "ssid": "", "wifi_password": ""})
check(
    "  no WiFi: no SSID or key line, and the first boot is not told to connect",
    "PRESET_NET_WIFI_SSID" not in no_wifi
    and "PRESET_NET_WIFI_KEY" not in no_wifi
    and 'PRESET_CONNECT_WIRELESS="no"' in no_wifi,
    no_wifi,
)
check(
    "a value with a quote, a dollar sign or a line break refused before anything is written",
    refused(rkpresets.preset_text, {**choices, "wifi_password": 'eight"888'}) == "rkpresets.value"
    and refused(rkpresets.preset_text, {**choices, "ssid": "GOOD$NET"}) == "rkpresets.value"
    and refused(rkpresets.preset_text, {**choices, "timezone": "Europe/London\nrm -rf /"}) == "rkpresets.value",
)

# A root filesystem standing in for a mounted card.
root = T / "card-root"
(root / "etc").mkdir(parents=True)
(root / "usr" / "lib" / "armbian").mkdir(parents=True)
(root / "root").mkdir()
(root / "home" / "salty-parrot").mkdir(parents=True)
(root / "etc" / "armbian-release").write_text(rockchip)
(root / "usr" / "lib" / "armbian" / "armbian-firstlogin").write_text(firstlogin)
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOw7 box@irate"
wrote = rkpresets.write_at(root, choices, KEY)
presets = root / "root" / ".not_logged_in_yet"
check(
    "the presets and the box's key written into the mounted root filesystem, root's alone",
    "settings for rockchip 26.05.0-trunk" in wrote
    and presets.read_text() == text
    and oct(presets.stat().st_mode & 0o777) == "0o600"
    and (root / "root" / ".ssh" / "authorized_keys").read_text() == KEY + "\n"
    and (root / "home" / "salty-parrot" / ".ssh" / "authorized_keys").read_text() == KEY + "\n"
    and oct((root / "root" / ".ssh").stat().st_mode & 0o777) == "0o700",
    wrote,
)
# A link where root's .ssh should be: the write must not follow it.
link_root = T / "linked-root"
shutil.copytree(root, link_root)
outside = T / "outside"
outside.mkdir()
for folder in ("home", "root"):
    target = link_root / folder / ".ssh"
    if target.exists():
        shutil.rmtree(target)
(link_root / "root" / ".ssh").symlink_to(outside)
check(
    "a link where root's .ssh should be refused, and nothing written through it",
    refused(rkpresets.write_at, link_root, choices, KEY) != None and not list(outside.iterdir()),
)

# A card whose root partition is found from a recorded lsblk, mounted, written and unmounted.
recorded = (FIX / "rk-image-lsblk.json").read_text()
seen = {}
ran = []


def fake_run(*cmd, timeout=120):
    ran.append(cmd)
    if cmd[0] == "lsblk":
        return subprocess.CompletedProcess(cmd, 0, recorded, "")
    if cmd[0] == "mount":
        target = Path(cmd[-1])
        shutil.copytree(root, target, dirs_exist_ok=True)
        seen["mounted"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")
    if cmd[0] == "umount":
        path = Path(cmd[-1])
        seen["presets"] = (path / "root" / ".not_logged_in_yet").read_text()
        seen["key"] = (path / "root" / ".ssh" / "authorized_keys").read_text()
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


rkpresets.run = fake_run
rkcard._card = lambda *a, **k: {"path": "/dev/sda", "size": 15634268160, "mounted": False}
wrote = rkpresets.write_card("sda", 15634268160, "S1", choices, KEY)
check(
    "a card: its armbi_root partition mounted read-write and no-exec, the presets and key written, unmounted",
    seen.get("mounted") == ("mount", "-o", "rw,noexec,nosuid,nodev", "/dev/sda2", str(seen["mounted"][-1]))
    and seen.get("presets") == text
    and seen.get("key") == KEY + "\n"
    and any(c[0] == "umount" for c in ran)
    and "settings for rockchip 26.05.0-trunk" in wrote,
    (seen, wrote),
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
