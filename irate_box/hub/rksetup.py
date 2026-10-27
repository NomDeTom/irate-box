# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Setting up a new board (Rockchip boards, /admin/rockchip.html): the half the hub reads itself. The boards to set up
that the box can see (a setup hotspot in range), the box's own values the page offers as defaults, and the check every
value sent to a board passes.

A new board reaches the owner's network through Armbian's first-boot presets: `/root/.not_logged_in_yet`, `key="value"`
lines that `armbian-firstlogin` sources as root (docs.armbian.com, "first boot configuration"). On stock Armbian they
are written before first boot, or over SSH as root with Armbian's default password; on mPWRD-OS, armbian-web-config's
setup hotspot (`armbiansetup-<hostname>`, password `armbian1234`, http://10.42.0.1:8080, POST /api/setup) writes the
same file. Neither escapes what it writes, so a value with a quote, a dollar sign, a backtick, a backslash or a line
break would break the first login, or run as root on the board: every value is checked here first (`problem`).
Stdlib only.
"""

import os
import re
import time
from pathlib import Path

from irate_box.hub import netinv, wificountry

ETC = Path(os.environ.get("HUB_SYSETC_DIR", "/etc"))
# armbian-web-config's name for its hotspot, and the first login's banner's (which has it the other way round).
HOTSPOT_RE = re.compile(r"^(?:armbiansetup(?:-(?P<host>.+))?|(?P<host2>.+)-armbiansetup)$")
# What Armbian's preset file cannot hold: it is sourced by bash inside double quotes.
UNSAFE = {'"': "a double quote", "$": "a dollar sign", "`": "a backtick", "\\": "a backslash"}
HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SCAN_KEEP = 15  # seconds the list is reused: the page asks every few seconds
_scan = {"at": 0.0, "nets": []}


def problem(value, kind="text"):
    """Why this value cannot be sent to a new board, in words for the page; None when it can. kind: text, password,
    wifi-password, hostname or user."""
    value = "" if value is None else str(value)
    for ch, name in UNSAFE.items():
        if ch in value:
            return f"It has {name}, which Armbian's setup cannot take."
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "It has a line break or another control character, which Armbian's setup cannot take."
    if kind == "wifi-password" and value and not 8 <= len(value) <= 63:
        return "A WiFi password is 8 to 63 characters."
    if kind == "password" and len(value) < 8:
        return "A password here is at least 8 characters."
    if kind == "hostname" and not HOSTNAME_RE.match(value):
        return "A name is lower-case letters, digits and hyphens, up to 63, not starting or ending with a hyphen."
    if kind == "user" and not USER_RE.match(value):
        return "A user name is lower-case letters, digits, hyphens and underscores, starting with a letter."
    return None


def hotspots(nets=None):
    """The setup hotspots in range: [{ssid, host, signal, boards}], strongest first. `boards` is how many access
    points share the name: two boards with the same hostname raise the same one."""
    if nets is None:
        if time.time() - _scan["at"] > SCAN_KEEP:
            # NetworkManager's last scan only: a list older than 30 s otherwise starts a scan, and on a one-radio
            # box the hotspot shares that radio.
            nets = netinv.visible_networks(run_=lambda *cmd: netinv.run(*cmd, "--rescan", "no"))
            _scan.update(at=time.time(), nets=nets)
        nets = _scan["nets"]
    out = []
    for n in nets:
        found = HOTSPOT_RE.match(n.get("ssid") or "")
        if found:
            host = found.group("host") or found.group("host2") or ""
            out.append({"ssid": n["ssid"], "host": host, "signal": n.get("signal", 0), "boards": n.get("aps", 1)})
    return sorted(out, key=lambda x: -x["signal"])


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def timezone():
    """The box's time zone: /etc/timezone, else where /etc/localtime points (…/zoneinfo/Europe/London)."""
    zone = _read(ETC / "timezone")
    if zone:
        return zone
    try:
        target = os.readlink(ETC / "localtime")
    except OSError:
        return ""
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else ""


def locale():
    """The box's LANG from /etc/default/locale (en_GB.UTF-8), or ""."""
    for line in _read(ETC / "default" / "locale").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "LANG":
            return value.strip().strip("\"'")
    return ""


def uplink_ssid():
    """The WiFi network the box is on now, or ""."""
    code, out = netinv.run("nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi", "list", "--rescan", "no")
    if code:
        return ""
    for f in netinv.nm_fields(out):
        if len(f) >= 2 and f[0] == "yes" and f[1]:
            return f[1]
    return ""


_defaults = {"at": 0.0, "values": {}}


def defaults():
    """What the page fills in: the box's own network, WiFi country, time zone and locale (read again after SCAN_KEEP)."""
    if time.time() - _defaults["at"] > SCAN_KEEP:
        values = {
            "ssid": uplink_ssid(),
            "country": wificountry.saved() or "",
            "timezone": timezone(),
            "locale": locale(),
        }
        _defaults.update(at=time.time(), values=values)
    return dict(_defaults["values"])


def snapshot():
    """/admin/rkboard's "setup": the hotspots in range, the defaults, and whether the box can set a board up yet (the
    root helper's half, joining the hotspot and sending the presets, is not built: the page says so)."""
    return {"hotspots": hotspots(), "defaults": defaults(), "ready": False}
