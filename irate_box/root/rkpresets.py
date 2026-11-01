# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A new Armbian board's first-boot settings written onto its SD card, root's half (the card route). The card has
an image on it already (rkcard.flash, or rkboard.write_card through the loader). Root finds the image's root
filesystem, mounts it read-write and no-exec in a folder of its own, checks that it is Armbian and that its
armbian-firstlogin reads the presets this box writes, then writes `/root/.not_logged_in_yet`: one `NAME="value"`
line per setting. `armbian-firstlogin` sources that file as root at the first start, so no setup hotspot is raised
and Armbian's default password is never in use. The box's own key is put in root's authorized_keys on the card,
so the setup can finish over SSH without a password.

Every value has passed hub/rksetup.problem (no quote, dollar sign, backtick, backslash or control character):
the card's first login reads the file as shell. The preset names, and the firstlogin that reads them, change
between Armbian releases, so the releases tested are pinned and an unknown firstlogin is refused. Stdlib only.
"""

import json
import os
import tempfile
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import rkcard, safeio

# The labels and fields of the presets this box writes. PRESET_ROOT_KEY and PRESET_USER_KEY are not among them:
# armbian-firstlogin fetches those from a URL, so the box puts its key on the card itself (below).
PRESETS = (
    ("PRESET_ROOT_PASSWORD", "root_password", "password"),
    ("PRESET_USER_NAME", "user", "user"),
    ("PRESET_USER_PASSWORD", "user_password", "password"),
    ("PRESET_DEFAULT_REALNAME", "real_name", "text"),
    ("PRESET_USER_SHELL", "shell", "text"),
    ("PRESET_LOCALE", "locale", "text"),
    ("PRESET_TIMEZONE", "timezone", "text"),
    ("PRESET_NET_WIFI_SSID", "ssid", "text"),
    ("PRESET_NET_WIFI_KEY", "wifi_password", "wifi-password"),
    ("PRESET_NET_WIFI_COUNTRYCODE", "country", "text"),
)
# How the first login gets there: the network is set from the presets, not asked, and the locale menu is not shown.
FIXED = (
    ("PRESET_USER_SHELL", "/bin/bash"),
    ("SET_LANG_BASED_ON_LOCATION", "no"),
)
# The firstlogin this box has read: a release whose file names every preset here is known; one that does not is
# refused rather than written half understood.
FIRSTLOGIN = Path("usr/lib/armbian/armbian-firstlogin")
RELEASE = Path("etc/armbian-release")
KNOWN_FIRSTLOGIN = (
    "PRESET_ROOT_PASSWORD",
    "PRESET_USER_NAME",
    "PRESET_USER_PASSWORD",
    "PRESET_NET_WIFI_SSID",
    "PRESET_NET_WIFI_KEY",
    "PRESET_CONNECT_WIRELESS",
)
# The releases whose firstlogin and preset names were read (mPWRD-OS 26.05, Armbian 26.11). A release outside
# these is refused: the names change between releases.
SUPPORTED = (
    "24.05",
    "24.08",
    "24.11",
    "25.02",
    "25.05",
    "25.08",
    "25.11",
    "26.02",
    "26.05",
    "26.08",
    "26.11",
)
MOUNT_ROOT = Path(os.environ.get("HUB_RK_MOUNT", "/run/irate-box-board"))


def run(*cmd, timeout=120):
    """Every command goes through here (the tests stand it in)."""
    import subprocess

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def release(text):
    """(linux family, release) from an /etc/armbian-release, or Refused when the file is not Armbian's."""
    fields = {}
    for line in (text or "").splitlines():
        key, _, value = line.partition("=")
        if key.strip():
            fields[key.strip()] = value.strip().strip('"')
    family = fields.get("LINUXFAMILY") or fields.get("BOARD") or ""
    version = fields.get("VERSION") or fields.get("VERSION_ID") or ""
    if not family or not version or ("ARMBIAN" not in (text or "").upper() and "BOARD" not in fields):
        raise Refused(M("rkpresets.not-armbian", "The image on the card is not an Armbian image."))
    if not any(version.startswith(x) for x in SUPPORTED):
        raise Refused(
            M(
                "rkpresets.release-unknown",
                "The image's Armbian release {version} has not been read by this box: its first-boot settings "
                "can have different names. Use a release the box knows.",
                version=version,
            )
        )
    return family, version


def check_firstlogin(text):
    """Why this firstlogin cannot be trusted with these presets, or None when it names every one."""
    missing = [name for name in KNOWN_FIRSTLOGIN if name not in (text or "")]
    if missing:
        raise Refused(
            M(
                "rkpresets.firstlogin-unknown",
                "The image's first boot does not read the settings this box writes ({names}).",
                names=", ".join(missing),
            )
        )


def preset_text(choices):
    """The preset file's text: one NAME="value" line per setting, every value checked here again."""
    from irate_box.hub import rksetup

    lines = ["# Written by irate-box before the board's first start."]
    for name, field, kind in PRESETS:
        value = str(choices.get(field) or "")
        if (why := rksetup.problem(value, kind)) is not None:
            raise Refused(M("rkpresets.value", "{field}: {why}", field=name, why=why))
        if not value and field in ("root_password", "user", "user_password"):
            raise Refused(M("rkpresets.value-missing", "The card's setup needs {field}.", field=field))
        if field in ("ssid", "wifi_password", "country") and not value:
            continue
        lines.append(f'{name}="{value}"')
    wifi = bool(str(choices.get("ssid") or ""))
    lines.append(f'PRESET_NET_WIFI_ENABLED="{"yes" if wifi else "no"}"')
    lines.append(f'PRESET_CONNECT_WIRELESS="{"yes" if wifi else "no"}"')
    lines.append('PRESET_NET_CHANGE_DEFAULTS="yes"')
    for name, value in FIXED:
        lines.append(f'{name}="{value}"')
    return "\n".join(lines) + "\n"


def _dir(root, *parts):
    """A folder under the mounted image, made if needed, never through a link."""
    path = Path(root, *parts)
    for parent in reversed(path.parents):
        if parent.is_symlink():
            raise Refused(
                M("rkpresets.no-follow", "The image has a link where a folder should be: nothing was written.")
            )
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_at(root, choices, key=None):
    """The presets written into an already-mounted Armbian root filesystem (root is its folder). The box's public
    key, when given, is put in root's authorized_keys so the finish can sign in without a password. Returns what
    was written, for the page."""
    root = Path(root)
    try:
        text = (root / RELEASE).read_text()
    except OSError:
        raise Refused(M("rkpresets.not-armbian", "The image on the card is not an Armbian image.")) from None
    family, version = release(text)
    try:
        firstlogin = (root / FIRSTLOGIN).read_text()
    except OSError:
        raise Refused(
            M("rkpresets.no-firstlogin", "The image has no armbian-firstlogin to read the settings.")
        ) from None
    check_firstlogin(firstlogin)
    body = preset_text(choices)
    try:
        safeio.write(root / "root" / ".not_logged_in_yet", body, 0o600)
        wrote = [f"settings for {family} {version}"]
        if key:
            folder = _dir(root, "root", ".ssh")
            safeio.write(folder / "authorized_keys", key.strip() + "\n", 0o600)
            wrote.append("this box's key")
            user = str(choices.get("user") or "")
            home = root / "home" / user
            if user and home.is_dir():
                folder = _dir(home, ".ssh")
                safeio.write(folder / "authorized_keys", key.strip() + "\n", 0o600)
                wrote.append(f"{user}'s key")
    except OSError:
        raise Refused(
            M("rkpresets.link", "The image has a link where a file should be: nothing was followed.")
        ) from None
    return ", ".join(wrote)


def _partition(card):
    """The card's root filesystem: the one labelled armbi_root or rootfs, else its largest ext4 partition."""
    got = run("lsblk", "-J", "-b", "-o", "NAME,PATH,FSTYPE,LABEL,SIZE", card)
    try:
        parts = (json.loads(got.stdout).get("blockdevices") or [{}])[0].get("children") or []
    except (ValueError, IndexError):
        parts = []
    usable = [p for p in parts if p.get("fstype") in ("ext4", "ext3", "btrfs", "f2fs", "xfs")]
    named = [p for p in usable if (p.get("label") or "") in ("armbi_root", "rootfs", "armbian")]
    picked = (named or sorted(usable, key=lambda p: -int(p.get("size") or 0)) or [None])[0]
    if not picked:
        raise Refused(
            M("rkpresets.no-root", "The card has no root filesystem the box can find: write an image to it first.")
        )
    return picked.get("path") or f"/dev/{picked['name']}"


def write_card(name, size, serial, choices, key=None):
    """A card already written with an Armbian image: its root filesystem found, mounted, given the presets and the
    box's key, and unmounted again."""
    card = rkcard._card(name, size, serial)
    probed = run("partprobe", "-s", card["path"], timeout=60)
    if probed.returncode != 0:
        run("blockdev", "--rereadpt", card["path"], timeout=60)
    part = _partition(card["path"])
    MOUNT_ROOT.mkdir(parents=True, exist_ok=True)
    mount = Path(tempfile.mkdtemp(prefix="card-", dir=MOUNT_ROOT))
    try:
        got = run("mount", "-o", "rw,noexec,nosuid,nodev", part, str(mount), timeout=120)
        if got.returncode != 0:
            raise Refused(
                M(
                    "rkpresets.mount",
                    "The card's root filesystem could not be mounted: {detail}",
                    detail=(got.stderr or got.stdout).strip()[-160:],
                )
            )
        wrote = write_at(mount, choices, key)
    finally:
        run("umount", str(mount), timeout=120)
        try:
            mount.rmdir()
        except OSError:
            pass
    return wrote
