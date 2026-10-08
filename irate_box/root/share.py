# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Guests' onward internet as a scale the owner chooses (Tom, 2026-10-08: "give options, and a
sliding scale"; principle 3: sharing the connection onward is an option the owner turns on after a
warning; principle 5: options, not automatic decisions).

  off          guests reach the box and nothing else (the default; ap.py's design).
  users-web    a device signed in to an account on the hub reaches the web (80 and 443).
  sheet-web    any device, once through the sign-in sheet, reaches the web.
  sheet-all    any device, once through the sheet, reaches everything.
  open         every device on the hotspot reaches everything; no sheet.

Contained (Tom, 2026-10-08: "Fix the network sharing issue with a containment plan … Give the admin
the option to disable various parts (with security doctor findings against them when they're too
much)"). Each part is on unless the owner turns it off, and the Security doctor says so while it is:

  lan       nothing forwarded to private, CGNAT (the tailnet's 100.64/10), link-local or multicast
            addresses: guests reach the internet, not the owner's network behind the box.
  tunnels   nothing forwarded into a tunnel or a container's network (Tailscale, WireGuard, tun/tap,
            Docker's bridges): the box's own tailnet stays the box's.
  by_mac    a device let out by its hardware address, not its IP: a later device given the same
            address doesn't inherit it.
  dns_hold  a device not let out yet gets only the hub's catch-all DNS (every name is the hub), so
            it can't look outside names up through the box. Those let out are sent to a resolver of
            their own (GUEST_DNS, port 5354), which answers as the box's own resolver does.

The rules are firewall.py's: one table with the floor, written and loaded as one (firewall.apply_all).
Pure functions here, tested offline; the root helper writes and loads."""

import ipaddress
import json
import os
import re
from pathlib import Path

LEVELS = ("off", "users-web", "sheet-web", "sheet-all", "open")
LET_OUT = ("users-web", "sheet-web", "sheet-all")     # the levels where a device is let out one by one
WEB_ONLY = ("users-web", "sheet-web")
CONTAIN = ("lan", "tunnels", "by_mac", "dns_hold")
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "share.json"
PUBLIC = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "share.json"   # what the hub and /admin may see
SYSCTL = Path(os.environ.get("HUB_SYSCTL_DIR", "/etc/sysctl.d")) / "90-irate-box-share.conf"
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
GUEST_DNS_UNIT = "irate-box-guest-dns.service"
GUEST_DNS_PORT = 5354
LEASES = Path(os.environ.get("HUB_AP_LEASES", "/var/lib/misc/dnsmasq.leases"))
HUB = "192.168.4.1"                 # apmode.ADDRESS: the hub on the hotspot
NETWORK = ipaddress.ip_network(f"{HUB}/24", strict=False)
OUT_HOURS = 12                      # how long a device stays let out after tapping through
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def load():
    try:
        data = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        data = {}
    contain = data.get("contain") if isinstance(data.get("contain"), dict) else {}
    return {"level": data.get("level") if data.get("level") in LEVELS else "off",
            "forward_was": data.get("forward_was"),
            "contain": {k: contain.get(k) is not False for k in CONTAIN}}


def save(rec):
    """Root's record, and what the hub may see of it (the level and the containment)."""
    from irate_box.root import safeio
    safeio.write(RECORD, json.dumps(rec))
    safeio.write(PUBLIC, json.dumps({"level": rec["level"], "contain": rec["contain"]}))


def on(state=None):
    return (state or load())["level"] != "off"


def guest_address(addr):
    """An address on the hotspot's network that isn't the hub's, as a string, or None."""
    try:
        ip = ipaddress.ip_address(str(addr))
    except ValueError:
        return None
    return str(ip) if ip in NETWORK and ip != ipaddress.ip_address(HUB) and ip != NETWORK.broadcast_address \
        and ip != NETWORK.network_address else None


def mac_of(addr, neigh=""):
    """A guest's hardware address from the hotspot's DHCP leases (expiry, MAC, IP, …), else from the
    kernel's neighbour table (`ip neigh` output, passed in); None if neither knows it."""
    try:
        for line in LEASES.read_text().splitlines():
            f = line.split()
            if len(f) >= 3 and f[2] == addr and MAC_RE.match(f[1].lower()):
                return f[1].lower()
    except OSError:
        pass
    for line in neigh.splitlines():
        f = line.split()
        if f and f[0] == addr and "lladdr" in f and MAC_RE.match(f[f.index("lladdr") + 1].lower()):
            return f[f.index("lladdr") + 1].lower()
    return None


def allow_command(key, table="inet irate_box"):
    """The nft command that lets one device out (the caller checked the level and the key: a MAC with
    by_mac, else an address)."""
    return ["nft", "add", "element", *table.split(), "out", f"{{ {key} timeout {OUT_HOURS}h }}"]


def guest_dns_unit(iface):
    """The resolver for guests let out (dns_hold): dnsmasq on GUEST_DNS_PORT, the hotspot's interface
    only, no DHCP, answering from the box's own resolv.conf. The hotspot's own dnsmasq keeps its
    catch-all; firewall.py sends only those let out here."""
    if not re.match(r"^[A-Za-z0-9_.-]{1,15}$", iface):
        raise ValueError(f"{iface!r} is not an interface name")
    return "\n".join([
        "[Unit]", "Description=Irate-Box: DNS for guests let out to the internet (root/share.py)",
        "After=network-online.target", "",
        "[Service]",
        f"ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file=/dev/null --pid-file= --port={GUEST_DNS_PORT} "
        f"--interface={iface} --bind-dynamic --except-interface=lo --no-hosts --cache-size=1000 --domain-needed --bogus-priv",
        "Restart=on-failure", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ])


WORDS = {
    "off": "guests reach the box and nothing else",
    "users-web": "a device signed in to an account reaches the web",
    "sheet-web": "any device reaches the web once through the sign-in sheet",
    "sheet-all": "any device reaches everything once through the sign-in sheet",
    "open": "every device on the hotspot reaches everything, with no sheet",
}
CONTAIN_WORDS = {
    "lan": "the owner's own network (private, CGNAT and link-local addresses) kept out of guests' reach",
    "tunnels": "nothing forwarded into Tailscale, WireGuard or a container's network",
    "by_mac": "devices let out by their hardware address, not their IP",
    "dns_hold": "a device not let out can't look outside names up through the box",
}
