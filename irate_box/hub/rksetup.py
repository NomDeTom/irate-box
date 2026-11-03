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

import json
import os
import re
import time
from pathlib import Path

from irate_box.hub import boardfind, netinv, rkusb, wificountry

ETC = Path(os.environ.get("HUB_SYSETC_DIR", "/etc"))
# armbian-web-config's name for its hotspot, and the first login's banner's (which has it the other way round).
HOTSPOT_RE = re.compile(r"^(?:armbiansetup(?:-(?P<host>.+))?|(?P<host2>.+)-armbiansetup)$")
# What Armbian's preset file cannot hold: it is sourced by bash inside double quotes.
UNSAFE = {'"': "a double quote", "$": "a dollar sign", "`": "a backtick", "\\": "a backslash"}
HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SCAN_KEEP = 15  # seconds the list is reused: the page asks every few seconds
SERIAL_BY_ID = Path(os.environ.get("HUB_SERIAL_BY_ID", "/dev/serial/by-id"))
CONSOLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
OWN = STATE / "control" / "ownserial.json"
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
OWN_DEVICE = {"pi": "/dev/ttyAMA0", "lyra": "/dev/ttyS1"}
OWN_WARNING = {
    "pi": "Bluetooth moves to the smaller UART: slower, and it can drop connections. The pins are not for 1 500 000 baud.",
    "lyra": "UART1 comes out on header pins 8 (TX) and 10 (RX); UART2 stays the box's own console.",
}
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


def taken_names(candidates=None, spots=None):
    """The names already in sight, lower-cased: the boards the box can see and the setup hotspots in range. The page
    picks a made-up name that is not one of them."""
    seen = boardfind.candidates() if candidates is None else candidates
    here = hotspots() if spots is None else spots
    return sorted({str(x.get("host") or "").lower() for x in [*(seen or []), *(here or [])] if x.get("host")})


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


def consoles():
    """The USB serial adapters on the box, by the /dev/serial/by-id name the root helper opens."""
    try:
        names = sorted(p.name for p in SERIAL_BY_ID.iterdir() if p.is_file() and not p.is_symlink())
    except OSError:
        return []
    return [name for name in names if CONSOLE_RE.match(name)]


def board_model():
    """lyra, pi or other, from the device tree's model: what the hub may read for itself."""
    try:
        low = (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace").lower()
    except OSError:
        return "other"
    if "lyra" in low or "luckfox" in low:
        return "lyra"
    if "raspberry pi" in low:
        return "pi"
    return "other"


def own_serial():
    """The box's own pins as a console, from the root helper's copy of the status (control/ownserial.json):
    {board, supported, on, pending, device, warning}. The hub reads it without importing root's module."""
    board = board_model()
    try:
        data = json.loads(OWN.read_text())
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    on = data.get("on") is True and data.get("board") == board
    device = OWN_DEVICE.get(board) if on else None
    return {
        "board": board,
        "supported": board in OWN_DEVICE,
        "on": on,
        "pending": bool(device and not Path(device).exists()),
        "device": device or "",
        "warning": OWN_WARNING.get(board, ""),
    }


def routes(consoles_in_range=None, cards_in_reader=None, boards_on_network=None, own_serial_here=None):
    """The ways in this box can set a new board up now. A card needs one in a reader; a console needs an adapter or
    the box's own pins (own_serial, root/ownserial.py); the SSH route needs a board the box can see on its own
    network; the hotspot route needs nothing, as the hotspot itself is in range."""
    found = consoles() if consoles_in_range is None else consoles_in_range
    cards = rkusb.cards() if cards_in_reader is None else cards_in_reader
    seen = boardfind.candidates() if boards_on_network is None else boards_on_network
    if own_serial_here is None:
        own_serial_here = own_serial()
    out = ["hotspot"]
    if seen:
        out.append("ssh")
    if found or (own_serial_here.get("on") and not own_serial_here.get("pending")):
        out.append("serial")
    if cards:
        out.append("card")
    return out


def snapshot():
    """/admin/rkboard's "setup": the hotspots in range, the box's own values, the serial adapters, the box's own
    pins and cards it can use, the routes built for them, and the names already in sight."""
    found, cards, seen = consoles(), rkusb.cards(), boardfind.candidates()
    spots, own = hotspots(), own_serial()
    return {
        "hotspots": spots,
        "defaults": defaults(),
        "consoles": found,
        "cards": cards,
        "boards": seen,
        "own_serial": own,
        "routes": routes(found, cards, seen, own),
        "taken": taken_names(seen, spots),
        "ready": True,
    }
