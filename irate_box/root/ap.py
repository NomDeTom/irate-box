# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot, applied (item 2 of the current and next actions): what the root helper writes and
runs for a plan from hub/apmode.py. As the prototype on the Lyra did it (2026-10-01, plan
archive): a second interface `ap0` when the hotspot shares the radio with the box's WiFi link
(`iw dev <link> interface add ap0 type __ap`), the radio's own interface when it has one to itself;
a NetworkManager connection on it with the address 192.168.4.1/24 (which the box's certificate
authority permits); and the box's own dnsmasq bound to that interface alone, handing out
.10 to .200 and answering every name with 192.168.4.1, so whatever a guest types reaches the hub.
Forwarding stays off: nothing passes between the guests and the box's other networks (guests'
onward internet is a separate, owner-consented choice, not made here).

Pure functions here (the files and the steps, tested offline); the root helper runs them.
"""

import ipaddress

from irate_box.hub import apmode, hotspot

CONNECTION = "irate-box-ap"
SHARED_IFACE = "ap0"
KEYFILE = "/etc/NetworkManager/system-connections/irate-box-ap.nmconnection"
DNSMASQ_CONF = "/etc/hub/ap-dnsmasq.conf"
DNSMASQ_UNIT = "irate-box-ap-dns.service"
LEASES = "/var/lib/misc/dnsmasq.leases"     # the hub counts guests from it (server.py LEASES)
NETWORK = ipaddress.ip_network(f"{apmode.ADDRESS}/24", strict=False)
DHCP_FIRST, DHCP_LAST = 10, 200
DEFAULT_SSID = "Irate-Box"


def ap_iface(plan):
    """The interface the hotspot runs on: its own when the radio is its alone, else ap0 beside the link."""
    return plan["iface"] if plan["kind"] == "own-radio" or plan.get("drops_uplink") else SHARED_IFACE


def _keyfile_value(v):
    return str(v).replace("\n", "")


def keyfile(plan, settings, ssid=DEFAULT_SSID):
    """The NetworkManager connection, as a keyfile (root's, 600). One network: the "two networks"
    choice needs a second access point interface, which comes later."""
    nets = hotspot.networks(settings, ssid)
    role, net = nets[0]
    sec = hotspot.nm_properties(settings, net["kind"])
    band = "a" if plan.get("band") == "5 GHz" else "bg"
    lines = ["# Written by irate-box (root/ap.py): the hotspot. Edits are overwritten.",
             "[connection]", f"id={CONNECTION}", "type=wifi", f"interface-name={ap_iface(plan)}",
             "autoconnect=true", "autoconnect-priority=-10", "",
             "[wifi]", "mode=ap", f"ssid={_keyfile_value(net['ssid'])}", f"band={band}", f"channel={int(plan['channel'])}", ""]
    if sec:
        lines.append("[wifi-security]")
        for k, v in sec.items():
            lines.append(f"{k.split('.', 1)[1]}={_keyfile_value(v)}")
        lines.append("")
    lines += ["[ipv4]", "method=manual", f"address1={apmode.ADDRESS}/{NETWORK.prefixlen}", "never-default=true", "",
              "[ipv6]", "method=disabled", ""]
    return "\n".join(lines)


def dnsmasq_conf(plan):
    """The box's dnsmasq for the hotspot alone: DHCP for guests, and every name answered with the hub."""
    first, last = NETWORK.network_address + DHCP_FIRST, NETWORK.network_address + DHCP_LAST
    return "\n".join([
        "# Written by irate-box (root/ap.py): DHCP and DNS on the hotspot only. Edits are overwritten.",
        f"interface={ap_iface(plan)}", "bind-dynamic", "except-interface=lo", "no-resolv", "no-hosts",
        f"dhcp-range={first},{last},{NETWORK.netmask},12h",
        f"dhcp-option=option:router,{apmode.ADDRESS}", f"dhcp-option=option:dns-server,{apmode.ADDRESS}",
        f"address=/#/{apmode.ADDRESS}", f"dhcp-leasefile={LEASES}", "dhcp-authoritative", "",
    ])


def up_steps(plan):
    """The commands that bring the hotspot up, in order (the files written first). Refuses a plan
    that waits on the owner, or has no channel."""
    if plan.get("needs_choice") or plan["kind"] == "none" or not plan.get("channel"):
        raise ValueError("this plan doesn't start a hotspot: " + plan.get("text", ""))
    steps = []
    if ap_iface(plan) == SHARED_IFACE:
        steps.append(["iw", "dev", plan["iface"], "interface", "add", SHARED_IFACE, "type", "__ap"])
        steps.append(["nmcli", "device", "set", SHARED_IFACE, "managed", "yes"])
    if plan.get("drops_uplink") and plan.get("uplink"):
        steps.append(["nmcli", "device", "disconnect", plan["uplink"]])
    steps += [["nmcli", "connection", "reload"], ["nmcli", "connection", "up", CONNECTION],
              ["systemctl", "restart", DNSMASQ_UNIT]]
    return steps


def down_steps(plan):
    """The commands that take it down again, leaving the box's own links as they were."""
    steps = [["systemctl", "stop", DNSMASQ_UNIT], ["nmcli", "connection", "down", CONNECTION]]
    if ap_iface(plan) == SHARED_IFACE:
        steps.append(["iw", "dev", SHARED_IFACE, "del"])
    if plan.get("drops_uplink") and plan.get("uplink"):
        steps.append(["nmcli", "device", "connect", plan["uplink"]])
    return steps


def dnsmasq_unit():
    """The dnsmasq unit for the hotspot (dnsmasq-base's binary; NetworkManager's shared mode isn't
    used, so the DNS answers are the hub's)."""
    return "\n".join([
        "[Unit]", "Description=Irate-Box: DHCP and DNS on the hotspot (root/ap.py)",
        "After=NetworkManager.service", "",
        "[Service]", f"ExecStart=/usr/sbin/dnsmasq --keep-in-foreground --conf-file={DNSMASQ_CONF} --pid-file=",
        "Restart=on-failure", "",
        "[Install]", "WantedBy=multi-user.target", "",
    ])
