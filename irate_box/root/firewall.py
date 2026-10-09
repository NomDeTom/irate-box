# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The network floor (security stance review 2026-10-08 §4 item 3; plan row 27): what a guest on
the hotspot can reach, said once as an nftables ruleset and nothing else.

One table, `inet irate_box`, for everything the hotspot's guests can reach (Tom, 2026-10-08: the
floor and guests' internet one policy, applied as a whole). The floor, at the level the owner
chooses (Tom: "Give me options!"):

  hub    traffic arriving on the hotspot's interface may reach the hub's front (80, 443), DNS and
         DHCP (the box's own dnsmasq) and nothing else of the box;
  apps   the same, and the apps' origins (the add-on, notes, books and cgit ports, and their TLS twins);

and in both the services the owner opens to guests by choice (MQTT for the mesh's nodes, Syncthing,
SSH); everything else from there is dropped. Off, nothing filters what arrives. Nothing is forwarded
between the hotspot and the box's other networks unless the owner shares the connection
(share.py), and then only as contained as share.py's parts say. The box's other interfaces are
untouched: the LAN side stays as the owner has it, so this never locks anyone out.

The ruleset is written to /etc/hub/firewall.nft, checked with nft -c, and loaded in one nft -f (one
transaction: no moment with half of it), and by irate-box-firewall.service at boot; with neither the
floor nor sharing on there is no table. The Security page sets the floor and the containment; the
hotspot's card the sharing level; uninstall.sh takes it all away. Pure functions here (the text, the ports), tested offline; the
root helper writes and loads it. Needs the nftables package (install.sh's list since #119).
"""

import json
import os
import re
import subprocess
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RULES = ETC / "firewall.nft"
RECORD_AP = ETC / "ap.json"
DNSMASQ_CONF = Path(os.environ.get("HUB_AP_DNSMASQ", "/etc/hub/ap-dnsmasq.conf"))
UNIT = "irate-box-firewall.service"
TABLE = "inet irate_box"
DEFAULT_IFACE = "ap0"
MOSQUITTO = Path(os.environ.get("HUB_MOSQUITTO_DIR", "/etc/mosquitto"))
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
# Where a package's unit may be, besides: only on a real box (a test's HUB_UNIT_DIR stands alone).
UNIT_DIRS = (UNIT_DIR,) if "HUB_UNIT_DIR" in os.environ else (UNIT_DIR, Path("/lib/systemd/system"), Path("/usr/lib/systemd/system"))
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

# The hub's own doors on the hotspot: the front, the apps' origins and their TLS twins, DNS, DHCP.
HUB_TCP = (53, 80, 443, 8090, 8091, 8092, 8093, 8490, 8491, 8492, 8493)
HUB_UDP = (53, 67)
# The floor at "hub": the front, DNS and DHCP only.
HUB_ONLY_TCP = (53, 80, 443)
FLOOR_LEVELS = ("hub", "apps")
# Never forwarded to while contained (share.py's lan): this host, private and CGNAT (the tailnet's)
# ranges, link-local, the special-purpose blocks, multicast and above.
PRIVATE = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
           "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/3")
# Interfaces never forwarded into while contained (share.py's tunnels).
TUNNELS = ("tailscale*", "wg*", "tun*", "tap*", "zt*", "docker*", "br-*", "veth*")
DNS_MARK = "0x00000153"       # a guest's DNS sent to share.GUEST_DNS_PORT: the only way in there
# What the owner may open to guests besides, by name: {name: (tcp ports, udp ports)}. Also: a service an
# add-on declares (its manifest's network block, services.py), by its id; and a port the owner opened
# for a service of their own, as "port:tcp/8123".
PORT_SERVICE = re.compile(r"^port:(tcp|udp)/([0-9]{1,5})$")
SERVICES = {
    "mqtt": ((1883,), ()),             # mosquitto, for Meshtastic nodes and the phone app's proxy
    "sync": ((22000,), (22000, 21027)),  # Syncthing's protocol and local discovery
    "ssh": ((22,), ()),
}


def hotspot_iface():
    """The interface the hotspot runs on: from the hotspot's record (root/ap.py) when it is up,
    else from its dnsmasq config, else ap0."""
    try:
        rec = json.loads(RECORD_AP.read_text())
        if rec.get("up") and isinstance(rec.get("plan"), dict):
            from irate_box.root import ap
            iface = ap.ap_iface(rec["plan"])
            if IFACE_RE.match(iface):
                return iface
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        for line in DNSMASQ_CONF.read_text().splitlines():
            if line.startswith("interface=") and IFACE_RE.match(line[len("interface="):].strip()):
                return line[len("interface="):].strip()
    except OSError:
        pass
    return DEFAULT_IFACE


def ports(services=(), level="apps"):
    """(tcp, udp): the hub's own doors (at the floor's level) plus the named services', sorted, each once."""
    tcp, udp = set(HUB_TCP if level == "apps" else HUB_ONLY_TCP), set(HUB_UDP)
    for s in services:
        t, u = service_ports(s)
        tcp |= set(t)
        udp |= set(u)
    return sorted(tcp), sorted(udp)


def ruleset(iface, floor=None, sharing=None):
    """The nftables text. floor: None (off), or {"level": "hub"|"apps", "services": [...]} with the
    services already those on the box (services_here); sharing: share.load()'s dict, or None (off)."""
    from irate_box.root import share
    if not IFACE_RE.match(iface):
        raise ValueError(f"{iface!r} is not an interface name")
    q = f'iifname "{iface}"'
    sharing = sharing if sharing and sharing.get("level") in share.LEVELS[1:] else None
    lv = sharing["level"] if sharing else "off"
    c = sharing["contain"] if sharing else {}
    who = "ether saddr" if c.get("by_mac") else "ip saddr"
    fmt = lambda xs: "{ " + ", ".join(str(x) for x in xs) + " }"  # noqa: E731
    out = [
        "# Written by irate-box (root/firewall.py): what a guest on the hotspot can reach, the floor and",
        "# guests' internet as one. Set on /admin (Security; the hotspot's card); edits are overwritten.",
        "# The box's other interfaces are not filtered here.",
        # Made if missing, then deleted: loading twice is loading once, on any kernel (6.1 has no "destroy").
        f"table {TABLE}",
        f"delete table {TABLE}",
        f"table {TABLE} {{",
    ]
    if sharing:
        out += ["\tset out {", f"\t\ttype {'ether_addr' if c.get('by_mac') else 'ipv4_addr'}", "\t\tflags timeout",
                f"\t\ttimeout {share.OUT_HOURS}h", "\t}"]
    out += ["\tchain input {", "\t\ttype filter hook input priority filter; policy accept;"]
    if sharing:
        # The guests' resolver answers only what was sent there (the redirect marks it), never a direct ask.
        out += [f"\t\t{q} udp dport {share.GUEST_DNS_PORT} ct mark != {DNS_MARK} drop",
                f"\t\t{q} tcp dport {share.GUEST_DNS_PORT} ct mark != {DNS_MARK} drop"]
    if floor:
        out.append(f"\t\t{q} jump hotspot")
    out.append("\t}")
    if floor:
        tcp, udp = ports(floor.get("services", ()), floor.get("level", "apps"))
        if sharing:
            tcp, udp = sorted(set(tcp) | {share.GUEST_DNS_PORT}), sorted(set(udp) | {share.GUEST_DNS_PORT})
        out += ["\tchain hotspot {",
                "\t\tct state established,related accept",
                "\t\tct state invalid drop",
                "\t\ticmp type { echo-request, destination-unreachable, time-exceeded, parameter-problem } accept",
                "\t\ticmpv6 type { echo-request, destination-unreachable, packet-too-big, time-exceeded, parameter-problem, nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept",
                f"\t\tudp dport {fmt(udp)} accept",
                f"\t\ttcp dport {fmt(tcp)} accept",
                "\t\tdrop",
                "\t}"]
    out += ["\tchain forward {", "\t\ttype filter hook forward priority filter; policy accept;"]
    if sharing:
        out.append(f'\t\toifname "{iface}" ct state established,related accept')
        if c.get("lan"):
            out.append(f"\t\t{q} ip daddr {fmt(PRIVATE)} drop")
        if c.get("tunnels"):
            out += [f'\t\t{q} oifname "{t}" drop' for t in TUNNELS]
        if lv == "open":
            out.append(f"\t\t{q} accept")
        elif lv in share.WEB_ONLY:
            out += [f"\t\t{q} {who} @out tcp dport {{ 80, 443 }} accept", f"\t\t{q} {who} @out udp dport 443 accept"]
        else:
            out.append(f"\t\t{q} {who} @out accept")
    out += [f"\t\t{q} drop", f'\t\toifname "{iface}" drop', "\t}"]
    if sharing:
        out += ["\tchain pre {", "\t\ttype nat hook prerouting priority dstnat; policy accept;"]
        if lv in share.LET_OUT:
            # Not let out yet: its plain web requests go to the hub, which answers with the sheet.
            out.append(f"\t\t{q} {who} != @out ip daddr != {share.HUB} tcp dport 80 dnat ip to {share.HUB}")
        # DNS: the guests' resolver for those let out (everyone at open, or with dns_hold off); the
        # rest keep the hotspot's catch-all, where every name is the hub.
        sel = f"{q} {who} @out" if lv in share.LET_OUT and c.get("dns_hold") else q
        out += [f"\t\t{sel} {p} dport 53 ct mark set {DNS_MARK} redirect to :{share.GUEST_DNS_PORT}" for p in ("udp", "tcp")]
        out += ["\t}", "\tchain post {", "\t\ttype nat hook postrouting priority srcnat; policy accept;",
                f'\t\tip saddr {share.NETWORK} oifname != "{iface}" masquerade', "\t}"]
    out += ["}", ""]
    return "\n".join(out)


def service_ports(name):
    """(tcp ports, udp ports) for a name the floor keeps: a built-in, an owner's port, or a declared service.
    A name nothing knows any more (its add-on's manifest gone) opens nothing."""
    if name in SERVICES:
        return SERVICES[name]
    m = PORT_SERVICE.match(name)
    if m and 0 < int(m.group(2)) < 65536:
        return ((int(m.group(2)),), ()) if m.group(1) == "tcp" else ((), (int(m.group(2)),))
    from irate_box.hub import services
    s = services.by_id(name)
    return (tuple(p for pr, p in s["listen"] if pr == "tcp"), tuple(p for pr, p in s["listen"] if pr == "udp")) if s else ((), ())


def services_here(wanted):
    """The services to open, of those the owner wants, that are on the box at all: MQTT only with
    the broker's config, Syncthing only with its unit, a declared service only where installed; SSH and
    the owner's own ports as wanted."""
    have = []
    if "mqtt" in wanted and (MOSQUITTO / "conf.d" / "irate-box.conf").exists():
        have.append("mqtt")
    if "sync" in wanted and any((d / "syncthing@.service").exists() for d in UNIT_DIRS):
        have.append("sync")
    if "ssh" in wanted:
        have.append("ssh")
    from irate_box.hub import services
    for s in services.declared():
        if s["id"] in wanted and services.installed(s):
            have.append(s["id"])
    have += [w for w in wanted if PORT_SERVICE.match(w)]
    return have


def run(*cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def loaded():
    """True when the table is in the kernel, False when not, None when nft is not there."""
    try:
        r = run("nft", "list", "table", *TABLE.split())
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


def apply_all(floor, sharing, iface=None):
    """The floor and guests' internet together: written, checked with nft, loaded in one transaction
    now and at every boot; with neither on, the table and its file gone. Raises ValueError, having
    changed nothing, when nft refuses the ruleset."""
    from irate_box.root import share
    if not floor and not share.on(sharing or {"level": "off"}):
        remove()
        return
    text = ruleset(iface or hotspot_iface(), floor, sharing)
    RULES.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES.with_name(f".{RULES.name}.new")
    tmp.write_text(text)
    try:
        r = run("nft", "-c", "-f", str(tmp))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"nft is not installed ({exc}): apt-get install nftables, or update the box (install.sh adds it)")
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise ValueError("nft rejected the ruleset, so nothing changed: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    os.replace(tmp, RULES)
    r = run("nft", "-f", str(RULES))
    if r.returncode != 0:
        raise ValueError("the ruleset is written but did not load: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    r = run("systemctl", "enable", UNIT)   # loads the file at every boot
    if r.returncode != 0:
        raise ValueError(f"the ruleset is loaded, but {UNIT} is not enabled for the next boot: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])


def remove():
    """Take the table out of the kernel, the unit off at boot, the file away."""
    run("systemctl", "disable", UNIT)
    try:
        run("nft", "delete", "table", *TABLE.split())
    except OSError:
        pass
    RULES.unlink(missing_ok=True)


def share_sysctl():
    from irate_box.root import share
    return share.SYSCTL


def unit_text(code_dir="/opt/irate-box"):
    """The systemd unit install.sh writes: loads the file when there is one, removes the table on stop."""
    return "\n".join([
        "[Unit]", "Description=Irate-Box: what a guest on the hotspot can reach (root/firewall.py)",
        "Documentation=file://" + code_dir + "/irate_box/root/firewall.py",
        "DefaultDependencies=no", "Before=network-pre.target", "Wants=network-pre.target", "",
        "[Service]", "Type=oneshot", "RemainAfterExit=yes",
        f"ExecStart=/bin/sh -c 'if [ -f {RULES} ]; then nft -f {RULES}; fi'",
        f"ExecReload=/bin/sh -c 'if [ -f {RULES} ]; then nft -f {RULES}; fi'",
        # Stopped while guests share the connection: forwarding off too, so nothing passes unfiltered.
        f"ExecStop=/bin/sh -c 'nft delete table {TABLE} 2>/dev/null; if [ -f {share_sysctl()} ]; then sysctl -q -w net.ipv4.ip_forward=0; fi; true'", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ])
