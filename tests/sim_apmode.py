# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's plan (apmode.py, item 2): the ladder over whatever radios a box has, the channel an
access point may use, and the address the box's CA allows. The Lyra's own radio from its `iw list`
(tests/fixtures/iw-list-aic8800dc.txt, captured 2026-10-07); the rest are boxes it isn't: Ethernet
only, a USB dongle beside the WiFi link, a one-channel radio, a radio that can't share, none at all.
python3 tests/sim_apmode.py"""
import copy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import apmode, netinv  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


phys = netinv.parse_phys((REPO / "tests" / "fixtures" / "iw-list-aic8800dc.txt").read_text())
lyra = phys["phy0"]
check("the Lyra's radio: an AP beside the client, up to 3 channels, 2.4 GHz, channels 1-14 listed",
      lyra["ap"] and lyra["ap_beside_client"] and lyra["channels_at_once"] == 3 and lyra["bands"] == ["2.4 GHz"]
      and [c["channel"] for c in lyra["channels"]] == list(range(1, 15)), {k: lyra[k] for k in ("ap", "ap_beside_client", "channels_at_once", "bands")})
check("  channels for an AP: 1-13 in GB (the driver's 14 isn't allowed there), 1-11 in the US",
      [c["channel"] for c in apmode.allowed(lyra, "GB")] == list(range(1, 14))
      and [c["channel"] for c in apmode.allowed(lyra, "US")] == list(range(1, 12)))


def radio(base, iface, phy, link_ch=None, **kw):
    r = dict(copy.deepcopy(base), iface=iface, phy=phy, type="managed", owner="networkmanager")
    if link_ch:
        r["link"] = {"channel": link_ch, "freq": 2407 + 5 * link_ch if link_ch < 14 else 5000 + 5 * link_ch}
    r.update(kw)
    return r


def box(radios, uplink=None, country="GB"):
    inv = {"radios": radios, "uplink": uplink or {}, "country": country,
           "stacks": {"networkmanager": {"running": True}, "hostapd": {"installed": False}}}
    return inv, netinv.ap_verdicts(inv)


wifi_up = {"iface": "wlan0", "kind": "wifi"}
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
p = apmode.plan(inv, v)
check("the Lyra on its home WiFi: rung 2, beside the link on a channel of its own, starting on the link's (11), to be tried",
      (p["rung"], p["kind"], p["channel"], p["to_try"], p["follows_uplink"], p["address"]) == (2, "own-channel", 11, True, False, "192.168.4.1"), p)
p = apmode.plan(inv, v, tried={"phy0": False})
check("  the try failed: rung 3, following the link's channel", (p["rung"], p["kind"], p["channel"], p["follows_uplink"]) == (3, "follow", 11, True), p)
p = apmode.plan(inv, v, tried={"phy0": True})
check("  the try worked: rung 2, nothing left to try", (p["rung"], p["to_try"]) == (2, False), p)

inv, v = box([radio(lyra, "wlan0", "phy0")], {"iface": "eth0", "kind": "ethernet"})
p = apmode.plan(inv, v)
check("Ethernet uplink (or none): rung 1, the radio to itself, channel 1, 6 or 11", (p["rung"], p["kind"], p["channel"]) == (1, "own-radio", 1), p)
p = apmode.plan(inv, v, owner={"channel": 6})
check("  the owner's channel when it's allowed", p["channel"] == 6, p)
p = apmode.plan(inv, v, owner={"channel": 14})
check("  and not when it isn't (14 in GB)", p["channel"] == 1, p)

inv, v = box([radio(lyra, "wlan0", "phy0", 11), radio(lyra, "wlan1", "phy1")], wifi_up)
p = apmode.plan(inv, v)
check("a USB dongle beside the WiFi link: rung 1 on the dongle, the link's radio left alone", (p["rung"], p["iface"]) == (1, "wlan1"), p)
p = apmode.plan(inv, v, owner={"radio": "wlan0"})
check("  unless the owner chose the link's radio", (p["iface"], p["rung"]) == ("wlan0", 2), p)

inv, v = box([radio(lyra, "wlan0", "phy0", 6, channels_at_once=1)], wifi_up)
p = apmode.plan(inv, v)
check("a radio that runs one channel at a time: rung 3, following the link (6)", (p["rung"], p["channel"], p["follows_uplink"]) == (3, 6, True), p)

inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up)
p = apmode.plan(inv, v)
check("a radio that can't be AP and client at once: rung 4, the owner chooses, nothing taken",
      (p["rung"], p["needs_choice"], p["channel"]) == (4, True, None), p)
p = apmode.plan(inv, v, owner={"take_radio": True})
check("  the owner chose the hotspot: the link given up, a channel picked", (p["drops_uplink"], p["needs_choice"], p["channel"]) == (True, False, 11), p)

inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap=False)], wifi_up)
p = apmode.plan(inv, v)
check("no radio that can be an AP: rung 5, said, with what would help", (p["rung"], p["kind"]) == (5, "none") and "USB WiFi dongle" in p["text"], p)

five = radio(lyra, "wlan0", "phy0", 52, channels_at_once=1)
five["channels"] = [{"freq": 5260, "channel": 52, "ap_ok": False}, {"freq": 5180, "channel": 36, "ap_ok": True}]
inv, v = box([five], wifi_up)
p = apmode.plan(inv, v)
check("a one-channel radio whose link is on a radar (DFS) channel: can't follow it there, so the owner chooses",
      (p["rung"], p["needs_choice"]) == (4, True) and "52" in p.get("why", ""), p)

inv, v = box([], {})
check("no radios at all: rung 5", apmode.plan(inv, v)["rung"] == 5)

# Applied (root/ap.py): the NetworkManager keyfile, dnsmasq, and the steps, from a plan.
from irate_box.root import ap  # noqa: E402
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
p = apmode.plan(inv, v)
kf = ap.keyfile(p, {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"})
check("applied, sharing the radio: ap0 beside the link, open, channel 11, 192.168.4.1/24, never the default route",
      "interface-name=ap0" in kf and "mode=ap" in kf and "channel=11" in kf and "band=bg" in kf and "address1=192.168.4.1/24" in kf
      and "never-default=true" in kf and "[wifi-security]" not in kf, kf)
kf = ap.keyfile(p, {"mode": "sae", "password": "correct horse", "allow_wpa2": False, "second": "owe"})
check("  WPA3: SAE with management frames protected, the password in the keyfile (root's, 600)",
      "[wifi-security]\nkey-mgmt=sae\npsk=correct horse\npmf=3" in kf, kf)
d = ap.dnsmasq_conf(p)
check("  dnsmasq on ap0 alone: DHCP .10-.200, every name the hub's, the leases where the hub counts guests",
      "interface=ap0" in d and "dhcp-range=192.168.4.10,192.168.4.200,255.255.255.0,12h" in d and "address=/#/192.168.4.1" in d
      and "dhcp-leasefile=/var/lib/misc/dnsmasq.leases" in d and "no-resolv" in d, d)
steps = ap.up_steps(p)
check("  up: ap0 added on the link's radio, managed, the connection up, dnsmasq restarted; the link untouched",
      steps[0] == ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"] and ["nmcli", "connection", "up", "irate-box-ap"] in steps
      and not any("disconnect" in s for s in steps), steps)
check("  down: dnsmasq, the connection, ap0 removed", ap.down_steps(p)[-1] == ["iw", "dev", "ap0", "del"])
inv, v = box([radio(lyra, "wlan0", "phy0")], {"iface": "eth0", "kind": "ethernet"})
p = apmode.plan(inv, v)
check("applied, a radio to itself: on wlan0 directly, no ap0", "interface-name=wlan0" in ap.keyfile(p, {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"})
      and not any("__ap" in " ".join(s) for s in ap.up_steps(p)))
inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up)
p = apmode.plan(inv, v)
try:
    ap.up_steps(p); check("  a plan waiting on the owner: refused", False)
except ValueError:
    check("  a plan waiting on the owner: refused", True)
p = apmode.plan(inv, v, owner={"take_radio": True})
check("  the owner took the radio: the link disconnected on the way up, reconnected on the way down",
      ["nmcli", "device", "disconnect", "wlan0"] in ap.up_steps(p) and ap.down_steps(p)[-1] == ["nmcli", "device", "connect", "wlan0"])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
