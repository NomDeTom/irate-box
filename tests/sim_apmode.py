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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
