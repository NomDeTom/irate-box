# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The network floor (security stance review 2026-10-08 §4 item 3; plan row 27): what a guest on
the hotspot can reach, said once as an nftables ruleset and nothing else.

One table, `inet irate_box`: traffic arriving on the hotspot's interface may reach the hub's
front (80, 443), the apps' origins (the add-on, notes, books and cgit ports, and their TLS twins),
DNS and DHCP (the box's own dnsmasq), and the services the owner has on the hotspot by choice
(MQTT for the mesh's nodes, Syncthing, SSH); everything else from there is dropped, and nothing
is forwarded between the hotspot and the box's other networks. The box's other interfaces are
untouched: the LAN side stays as the owner has it, so this never locks anyone out.

The ruleset is written to /etc/hub/firewall.nft and loaded by irate-box-firewall.service at boot
(the unit only runs when the file exists); the Security page switches it on and off, undoable,
and uninstall.sh takes it away. Pure functions here (the text, the ports), tested offline; the
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
# What the owner may open to guests besides, by name: {name: (tcp ports, udp ports)}.
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


def ports(services=()):
    """(tcp, udp): the hub's own doors plus the named services', sorted, each once."""
    tcp, udp = set(HUB_TCP), set(HUB_UDP)
    for s in services:
        t, u = SERVICES[s]
        tcp |= set(t)
        udp |= set(u)
    return sorted(tcp), sorted(udp)


def ruleset(iface, services=()):
    """The nftables text: a default-drop chain for what arrives on the hotspot's interface, the
    box's other interfaces untouched, and no forwarding to or from the hotspot."""
    if not IFACE_RE.match(iface):
        raise ValueError(f"{iface!r} is not an interface name")
    tcp, udp = ports(services)
    fmt = lambda xs: "{ " + ", ".join(str(x) for x in xs) + " }"  # noqa: E731
    return "\n".join([
        "# Written by irate-box (root/firewall.py): what a guest on the hotspot can reach. Switched",
        "# on and off from the Security page (/admin); edits are overwritten. The box's other",
        "# interfaces are not filtered here.",
        # Made if missing, then deleted: loading twice is loading once, on any kernel (6.1 has no "destroy").
        f"table {TABLE}",
        f"delete table {TABLE}",
        f"table {TABLE} {{",
        "\tchain input {",
        "\t\ttype filter hook input priority filter; policy accept;",
        f"\t\tiifname \"{iface}\" jump hotspot",
        "\t}",
        "\tchain hotspot {",
        "\t\tct state established,related accept",
        "\t\tct state invalid drop",
        "\t\ticmp type { echo-request, destination-unreachable, time-exceeded, parameter-problem } accept",
        "\t\ticmpv6 type { echo-request, destination-unreachable, packet-too-big, time-exceeded, parameter-problem, nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept",
        f"\t\tudp dport {fmt(udp)} accept",
        f"\t\ttcp dport {fmt(tcp)} accept",
        "\t\tdrop",
        "\t}",
        "\tchain forward {",
        "\t\ttype filter hook forward priority filter; policy accept;",
        f"\t\tiifname \"{iface}\" drop",
        f"\t\toifname \"{iface}\" drop",
        "\t}",
        "}",
        "",
    ])


def services_here(wanted):
    """The services to open, of those the owner wants, that are on the box at all: MQTT only with
    the broker's config, Syncthing only with its unit; SSH as wanted."""
    have = []
    if "mqtt" in wanted and (MOSQUITTO / "conf.d" / "irate-box.conf").exists():
        have.append("mqtt")
    if "sync" in wanted and any((d / "syncthing@.service").exists() for d in UNIT_DIRS):
        have.append("sync")
    if "ssh" in wanted:
        have.append("ssh")
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


def apply(services, iface=None):
    """Write the ruleset, check it with nft, load it now and at every boot. Raises ValueError."""
    text = ruleset(iface or hotspot_iface(), services)
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
    r = run("systemctl", "enable", "--now", UNIT)
    if r.returncode != 0:
        raise ValueError(f"the ruleset is written, but {UNIT} did not start: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    r = run("systemctl", "restart", UNIT)  # already enabled and active: load the new file
    if r.returncode != 0:
        raise ValueError(f"the ruleset is written, but {UNIT} did not reload it: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200])


def remove():
    """Take the table out of the kernel, the unit off at boot, the file away."""
    run("systemctl", "disable", "--now", UNIT)
    try:
        run("nft", "delete", "table", *TABLE.split())
    except OSError:
        pass
    RULES.unlink(missing_ok=True)


def unit_text(code_dir="/opt/irate-box"):
    """The systemd unit install.sh writes: loads the file when there is one, removes the table on stop."""
    return "\n".join([
        "[Unit]", "Description=Irate-Box: what a guest on the hotspot can reach (root/firewall.py)",
        "Documentation=file://" + code_dir + "/irate_box/root/firewall.py",
        "DefaultDependencies=no", "Before=network-pre.target", "Wants=network-pre.target", "",
        "[Service]", "Type=oneshot", "RemainAfterExit=yes",
        f"ExecStart=/bin/sh -c 'if [ -f {RULES} ]; then nft -f {RULES}; fi'",
        f"ExecReload=/bin/sh -c 'if [ -f {RULES} ]; then nft -f {RULES}; fi'",
        f"ExecStop=/bin/sh -c 'nft delete table {TABLE} 2>/dev/null || true'", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ])
