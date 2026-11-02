# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Boards the box can see on its own network: the leases it keeps (NetworkManager's dnsmasq, dhcpd, dhclient), and
what the kernel's neighbours saw on the wire. A board is one whose MAC is a board maker's, or whose name looks like
a board's. The half the hub reads itself (it cannot import irate_box/root); root/rkssh.py acts on it. Stdlib only.
"""

import re
import subprocess
import time
from pathlib import Path

LEASE_FILES = (
    Path("/var/lib/NetworkManager"),
    Path("/var/lib/misc"),
    Path("/var/lib/dhcp"),
)
# A board's maker from its MAC's first three octets (the local-administered bit clear).
VENDORS = {
    "b8:41:d9": "Allwinner",
    "54:01:4a": "Rockchip",
    "b8:27:eb": "Raspberry Pi",
    "dc:a6:32": "Raspberry Pi",
    "e4:5f:01": "Raspberry Pi",
    "d8:3a:dd": "Raspberry Pi",
    "2c:cf:67": "Raspberry Pi",
}
NAMES = ("bpi", "banana", "lyra", "luckfox", "orangepi", "armbian", "rock", "sunxi")
ADDRESS_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
SCAN_KEEP = 15  # seconds the list is reused: the page asks every few seconds
_cache = {"at": 0.0, "found": []}


def run(*cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _mac(value):
    return bool(re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value or ""))


def leases():
    """{address: {mac, host}} from the leases this box keeps."""
    found = {}
    for folder in LEASE_FILES:
        try:
            files = list(folder.glob("*.leases")) + list(folder.glob("dhcp*.lease"))
        except OSError:
            continue
        for path in files:
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                fields = line.split()
                if len(fields) >= 4 and ADDRESS_RE.match(fields[2]) and _mac(fields[1]):
                    found[fields[2]] = {"mac": fields[1].lower(), "host": fields[3] if fields[3] != "*" else ""}
            for block in re.findall(r"lease\s*\{([^}]*)\}", text):
                ip = re.search(r"(?:fixed-address|option\s+ip-address)\s+([\d.]+)", block)
                mac = re.search(r"hardware\s+ethernet\s+([0-9a-fA-F:]{17})", block)
                host = re.search(r'client-hostname\s+"([^"]*)"', block)
                if ip and ADDRESS_RE.match(ip.group(1)):
                    found[ip.group(1)] = {
                        "mac": (mac.group(1).lower() if mac else ""),
                        "host": host.group(1) if host else "",
                    }
    return found


def neighbours():
    """{address: mac} from the kernel's neighbours (a board that answered ARP but kept no lease)."""
    got = run("ip", "neigh", "show")
    found = {}
    for line in (got.stdout or "").splitlines():
        fields = line.split()
        if fields and ADDRESS_RE.match(fields[0]) and "lladdr" in fields:
            mac = fields[fields.index("lladdr") + 1]
            if _mac(mac):
                found[fields[0]] = mac.lower()
    return found


def candidates(lease_data=None, neighbour_data=None, fresh=False):
    """The boards the box can see: [{address, mac, host, vendor, board}], a board maker's MAC or a board-like name
    first. Every lease is listed: on its own hotspot or cable, the box's lease is the board's. The list is reused
    for a few seconds when the page asks again."""
    if lease_data is None and neighbour_data is None and not fresh and time.time() - _cache["at"] <= SCAN_KEEP:
        return list(_cache["found"])
    found = {}
    for address, one in (leases() if lease_data is None else lease_data).items():
        found[address] = dict(one, vendor=VENDORS.get(one["mac"][:8].lower(), ""))
    for address, mac in (neighbours() if neighbour_data is None else neighbour_data).items():
        found.setdefault(address, {"mac": mac, "host": "", "vendor": VENDORS.get(mac[:8].lower(), "")})
    out = []
    for address, one in sorted(found.items(), key=lambda x: tuple(int(p) for p in x[0].split("."))):
        name = (one.get("host") or "").lower()
        board = bool(one.get("vendor")) or any(word in name for word in NAMES)
        out.append({**one, "address": address, "board": board})
    out = sorted(out, key=lambda x: (not x["board"], tuple(int(p) for p in x["address"].split("."))))
    if lease_data is None and neighbour_data is None:
        _cache.update(at=time.time(), found=out)
    return list(out)
