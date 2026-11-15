# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's plan (apmode.py): the ladder over whatever radios a box has, the channel an
access point may use, and the address the box's CA allows. An AIC8800DC radio from its `iw list`
(tests/fixtures/iw-list-aic8800dc.txt); the rest are boxes it isn't: Ethernet
only, a USB dongle beside the WiFi link, a one-channel radio, a radio that can't share, none at all.
python3 tests/sim_apmode.py"""

import copy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import apmode, netinv

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


phys = netinv.parse_phys((REPO / "tests" / "fixtures" / "iw-list-aic8800dc.txt").read_text())
lyra = phys["phy0"]
check(
    "the AIC8800DC radio: an AP beside the client, up to 3 channels, 2.4 GHz, channels 1-14 listed",
    lyra["ap"]
    and lyra["ap_beside_client"]
    and lyra["channels_at_once"] == 3
    and lyra["bands"] == ["2.4 GHz"]
    and [c["channel"] for c in lyra["channels"]] == list(range(1, 15)),
    {k: lyra[k] for k in ("ap", "ap_beside_client", "channels_at_once", "bands")},
)
check(
    "  channels for an AP: 1-13 in GB (the driver's 14 isn't allowed there), 1-11 in the US",
    [c["channel"] for c in apmode.allowed(lyra, "GB")] == list(range(1, 14))
    and [c["channel"] for c in apmode.allowed(lyra, "US")] == list(range(1, 12)),
)


def radio(base, iface, phy, link_ch=None, **kw):
    r = dict(copy.deepcopy(base), iface=iface, phy=phy, type="managed", owner="networkmanager")
    if link_ch:
        r["link"] = {"channel": link_ch, "freq": 2407 + 5 * link_ch if link_ch < 14 else 5000 + 5 * link_ch}
    r.update(kw)
    return r


def box(radios, uplink=None, country="GB"):
    inv = {
        "radios": radios,
        "uplink": uplink or {},
        "country": country,
        "stacks": {"networkmanager": {"running": True}, "hostapd": {"installed": False}},
    }
    return inv, netinv.ap_verdicts(inv)


wifi_up = {"iface": "wlan0", "kind": "wifi"}
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
p = apmode.plan(inv, v)
check(
    "a radio offering two channels at once (the Lyra's, its chip not looked up) on a home WiFi: rung 2, beside the link on a channel of its own, starting on the link's (11), to be tried",
    (p["rung"], p["kind"], p["channel"], p["to_try"], p["follows_uplink"], p["address"])
    == (2, "own-channel", 11, True, False, "192.168.4.1"),
    p,
)
# The same radio known as the AIC8800DC (known_radios.json "hotspot_channel": "link"): its firmware stalls with the
# hotspot on a channel of its own while the link joins, so it follows the link's, and there is nothing to try.
known_dc = radio(lyra, "wlan0", "phy0", 11, known={"chip": "AIC8800DC", "hotspot_channel": "link"})
inv_dc, v_dc = box([known_dc], wifi_up)
p = apmode.plan(inv_dc, v_dc)
check(
    "  known as the AIC8800DC: rung 3, following the link's channel (11), not tried on a channel of its own",
    (p["rung"], p["kind"], p["channel"], p["to_try"], p["follows_uplink"]) == (3, "follow", 11, False, True),
    p,
)
check(
    "  the table says so for the AIC8800DC, and netinv passes it on",
    netinv.known_facts({"usb": {"id": "a69c:88dc"}, "driver": "aic8800_fdrv"})["hotspot_channel"] == "link"
    and netinv.known_facts({"usb": {"id": "a69c:8d81"}, "driver": "aic8800_fdrv"}).get("hotspot_channel") is None,
)
p = apmode.plan(inv, v, caps={"phy0": {"own-channel": {"value": "no", "detail": ""}}})
check(
    "  the try failed: rung 3, following the link's channel",
    (p["rung"], p["kind"], p["channel"], p["follows_uplink"]) == (3, "follow", 11, True),
    p,
)
p = apmode.plan(inv, v, caps={"phy0": {"own-channel": {"value": "yes", "detail": ""}}})
check("  the try worked: rung 2, nothing left to try", (p["rung"], p["to_try"]) == (2, False), p)

inv, v = box([radio(lyra, "wlan0", "phy0")], {"iface": "eth0", "kind": "ethernet"})
p = apmode.plan(inv, v)
check(
    "Ethernet uplink (or none): rung 1, the radio to itself, channel 1, 6 or 11",
    (p["rung"], p["kind"], p["channel"]) == (1, "own-radio", 1),
    p,
)
p = apmode.plan(inv, v, owner={"channel": 6})
check("  the owner's channel when it's allowed", p["channel"] == 6, p)
p = apmode.plan(inv, v, owner={"channel": 14})
check("  and not when it isn't (14 in GB)", p["channel"] == 1, p)

inv, v = box([radio(lyra, "wlan0", "phy0", 11), radio(lyra, "wlan1", "phy1")], wifi_up)
p = apmode.plan(inv, v)
check(
    "a USB dongle beside the WiFi link: rung 1 on the dongle, the link's radio left alone",
    (p["rung"], p["iface"]) == (1, "wlan1"),
    p,
)
p = apmode.plan(inv, v, owner={"radio": "wlan0"})
check("  unless the owner chose the link's radio", (p["iface"], p["rung"]) == ("wlan0", 2), p)

inv, v = box([radio(lyra, "wlan0", "phy0", 6, channels_at_once=1)], wifi_up)
p = apmode.plan(inv, v)
check(
    "a radio that runs one channel at a time: rung 3, following the link (6)",
    (p["rung"], p["channel"], p["follows_uplink"]) == (3, 6, True),
    p,
)

inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up)
p = apmode.plan(inv, v)
check(
    "a radio that can't be AP and client at once: rung 4, the owner chooses, nothing taken",
    (p["rung"], p["needs_choice"], p["channel"]) == (4, True, None),
    p,
)
p = apmode.plan(inv, v, owner={"take_radio": True})
check(
    "  the owner chose the hotspot: the link given up, a channel picked",
    (p["drops_uplink"], p["needs_choice"], p["channel"]) == (True, False, 11),
    p,
)

inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap=False)], wifi_up)
p = apmode.plan(inv, v)
check(
    "no radio that can be an AP: rung 5, said, with what would help",
    (p["rung"], p["kind"]) == (5, "none") and "USB WiFi dongle" in p["text"],
    p,
)

five = radio(lyra, "wlan0", "phy0", 52, channels_at_once=1)
five["channels"] = [{"freq": 5260, "channel": 52, "ap_ok": False}, {"freq": 5180, "channel": 36, "ap_ok": True}]
inv, v = box([five], wifi_up)
p = apmode.plan(inv, v)
check(
    "a one-channel radio whose link is on a radar (DFS) channel: can't follow it there, so the owner chooses",
    (p["rung"], p["needs_choice"]) == (4, True) and "52" in p.get("why", ""),
    p,
)

inv, v = box([], {})
check("no radios at all: rung 5", apmode.plan(inv, v)["rung"] == 5)

# Applied (root/ap.py): the NetworkManager keyfile, dnsmasq, and the steps, from a plan.
from irate_box.root import ap

inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
p = apmode.plan(inv, v)
kf = ap.keyfile(p, {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"})
check(
    "applied, sharing the radio: ap0 beside the link, open, channel 11, 192.168.4.1/24, never the default route",
    "interface-name=ap0" in kf
    and "mode=ap" in kf
    and "channel=11" in kf
    and "band=bg" in kf
    and "address1=192.168.4.1/24" in kf
    and "never-default=true" in kf
    and "[wifi-security]" not in kf,
    kf,
)
kf = ap.keyfile(p, {"mode": "sae", "password": "correct horse", "allow_wpa2": False, "second": "owe"})
check(
    "  WPA3: SAE with management frames protected, the password in the keyfile (root's, 600)",
    "[wifi-security]\nkey-mgmt=sae\npsk=correct horse\npmf=3" in kf,
    kf,
)
d = ap.dnsmasq_conf(p)
check(
    "  dnsmasq on the hotspot's address alone (never ap0's other networks): DHCP .10-.200, every name the hub's, "
    "the leases where the hub counts guests",
    "listen-address=192.168.4.1" in d
    and "interface=" not in d.replace("except-interface=", "")
    and "dhcp-range=192.168.4.10,192.168.4.200,255.255.255.0,12h" in d
    and "address=/#/192.168.4.1" in d
    and "dhcp-leasefile=/var/lib/misc/dnsmasq.leases" in d
    and "no-resolv" in d,
    d,
)
steps = ap.up_steps(p)
check(
    "  up: ap0 added on the link's radio, managed, the connection up, dnsmasq restarted; the link untouched",
    steps[0] == ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"]
    and ["nmcli", "connection", "up", "irate-box-ap"] in steps
    and not any("disconnect" in s for s in steps),
    steps,
)
check("  down: dnsmasq, the connection, ap0 removed", ap.down_steps(p)[-1] == ["iw", "dev", "ap0", "del"])
inv, v = box([radio(lyra, "wlan0", "phy0")], {"iface": "eth0", "kind": "ethernet"})
p = apmode.plan(inv, v)
steps = ap.up_steps(p)
check(
    "applied, a radio to itself: still on ap0, added on wlan0, wlan0 held (out of NetworkManager's hands, down) first",
    "interface-name=ap0" in ap.keyfile(p, {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"})
    and steps[:3]
    == [
        ["nmcli", "device", "set", "wlan0", "managed", "no"],
        ["ip", "link", "set", "wlan0", "down"],
        ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"],
    ],
    steps,
)
check(
    "  down: ap0 removed, wlan0 given back to NetworkManager",
    ap.down_steps(p)[-2:] == [["iw", "dev", "ap0", "del"], ["nmcli", "device", "set", "wlan0", "managed", "yes"]],
    ap.down_steps(p),
)
inv, v = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up)
p = apmode.plan(inv, v)
try:
    ap.up_steps(p)
    check("  a plan waiting on the owner: refused", False)
except ValueError:
    check("  a plan waiting on the owner: refused", True)
p = apmode.plan(inv, v, owner={"take_radio": True})
check(
    "  the owner took the radio: the link's wlan0 held on the way up, the hotspot on ap0 beside it; given back and "
    "connected on the way down",
    ap.up_steps(p)[:3]
    == [
        ["nmcli", "device", "set", "wlan0", "managed", "no"],
        ["ip", "link", "set", "wlan0", "down"],
        ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"],
    ]
    and "interface-name=ap0" in ap.keyfile(p, {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"})
    and ap.down_steps(p)[-2:]
    == [["nmcli", "device", "set", "wlan0", "managed", "yes"], ["nmcli", "device", "connect", "wlan0"]],
    (ap.up_steps(p), ap.down_steps(p)),
)

# Running it (root/ap.py's executor), with the commands stood in and the files in a temp folder.
import json
import os
import subprocess
import tempfile

T = Path(tempfile.mkdtemp(prefix="ap-"))
from irate_box.root import radiocaps

radiocaps.STATE = T / "state"
radiocaps.STORE = radiocaps.STATE / "radio-caps.json"
ap.RECORD, ap.TRIED, ap.DISPATCHER, ap.UNITS = T / "ap.json", T / "ap-tried.json", T / "90-irate-box-ap", T / "units"
ap.KEYFILE, ap.DNSMASQ_CONF = T / "kf", T / "dns.conf"
ap.LINKS = T / "network"
ap.NM_RULES = T / "conf.d" / "zz-80-irate-box-ap.conf"
calls, iw = [], {"wlan0": 11, "ap0": None}


def fake_run(*cmd, **kw):
    calls.append(list(cmd))
    if cmd[:2] == ("iw", "dev"):
        text = "phy#0\n" + "".join(
            f"\tInterface {i}\n\t\ttype {'AP' if i == 'ap0' else 'managed'}\n"
            + (f"\t\tchannel {c} (2462 MHz), width: 20 MHz\n" if c else "")
            for i, c in iw.items()
        )
        return subprocess.CompletedProcess(cmd, 0, text, "")
    if cmd[:3] == ("nmcli", "connection", "up"):
        kf = (T / "kf").read_text()
        iw["ap0"] = int(next(ln for ln in kf.splitlines() if ln.startswith("channel=")).split("=")[1])
    if cmd[0] == "wpa_cli" and "chan_switch" in cmd:
        iw["ap0"] = netinv.freq_channel(int(cmd[cmd.index("chan_switch") + 2]))
        return subprocess.CompletedProcess(cmd, 0, "OK", "")
    if cmd[0] == "wpa_cli" and cmd[-1] == "status":
        return subprocess.CompletedProcess(cmd, 0, "wpa_state=COMPLETED\nmode=AP\n", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


settings = {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"}
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
inv["ap"] = v
plan = ap.start(fake_run, inv, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
rec = json.loads(ap.RECORD.read_text())
check(
    "start: the files written (the keyfile 600), the unit installed, the hook in place, the steps run, recorded",
    (T / "kf").stat().st_mode & 0o777 == 0o600
    and (ap.UNITS / ap.DNSMASQ_UNIT).exists()
    and os.access(ap.DISPATCHER, os.X_OK)
    and ["nmcli", "connection", "up", "irate-box-ap"] in calls
    and rec["up"]
    and rec["confirmed"]
    and rec["plan"]["channel"] == 11,
    rec,
)
check(
    "  NetworkManager's rule written and read again (no restart): ap* only the hotspot's connection, wlan* never it",
    "[device-irate-box-hotspot]\nmatch-device=interface-name:ap*\nallowed-connections=id:irate-box-ap"
    in ap.NM_RULES.read_text()
    and "match-device=interface-name:wlan*\nallowed-connections=except:id:irate-box-ap" in ap.NM_RULES.read_text()
    and ["nmcli", "general", "reload", "conf"] in calls
    and calls.index(["nmcli", "general", "reload", "conf"])
    < calls.index(["nmcli", "connection", "up", "irate-box-ap"]),
)
link = ap.LINKS / ap.LINK_FILE
check(
    "  udev told to keep ap0's name (a USB radio's ap0 can become wlx…), before ap0 is made",
    link.exists()
    and "OriginalName=ap0" in link.read_text()
    and "Name=ap0" in link.read_text()
    and calls.index(["udevadm", "control", "--reload"])
    < calls.index(["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"]),
)
calls.clear()
worked, p2 = ap.try_own_channel(fake_run, inv, settings, wait=0, sleep=lambda s: None)
check(
    "the try: the hotspot on a channel other than the link's (1), held, so it works; recorded; then back to the plan",
    worked is True
    and radiocaps.stored(inv["radios"][0])["own-channel"]["value"] == "yes"
    and p2["kind"] == "own-channel",
    (worked, p2),
)
check(
    "  afterwards on the plan's channel (the link's, 11), the try's channel not kept as the owner's choice",
    p2["channel"] == 11 and json.loads(ap.RECORD.read_text())["owner"] == {},
    (p2.get("channel"), json.loads(ap.RECORD.read_text())["owner"]),
)
iw_force = {"wlan0": 11}


def forced_run(*cmd, **kw):
    out = fake_run(*cmd, **kw)
    if cmd[:3] == ("nmcli", "connection", "up"):
        iw["ap0"] = iw["wlan0"]  # a driver that drags the AP onto the link's channel
    return out


worked, p3 = ap.try_own_channel(forced_run, inv, settings, wait=0, sleep=lambda s: None)
check(
    "  a driver that drags the hotspot onto the link's channel: the try fails, recorded, the plan becomes following",
    worked is False and radiocaps.stored(inv["radios"][0])["own-channel"]["value"] == "no" and p3["kind"] == "follow",
    (worked, p3),
)
radiocaps.STORE.unlink(missing_ok=True)


def refusing_run(*cmd, **kw):
    if cmd[:3] == ("nmcli", "connection", "up") and "channel=11" not in (T / "kf").read_text():
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 4, "", "Error: Connection activation failed")
    return fake_run(*cmd, **kw)


calls.clear()
worked, p3b = ap.try_own_channel(refusing_run, inv, settings, wait=0, sleep=lambda s: None)
check(
    "  a driver that won't start the hotspot off the link's channel: the try fails (no crash), undone, then following",
    worked is False
    and radiocaps.stored(inv["radios"][0])["own-channel"]["value"] == "no"
    and p3b["kind"] == "follow"
    and ["iw", "dev", "ap0", "del"] in calls
    and json.loads(ap.RECORD.read_text())["up"],
    (worked, p3b),
)


def broken_run(*cmd, **kw):
    if cmd[:3] == ("nmcli", "connection", "up"):
        return subprocess.CompletedProcess(cmd, 4, "", "Error: no suitable device")
    return fake_run(*cmd, **kw)


try:
    ap.start(broken_run, inv, settings)
    failed = None
except RuntimeError as exc:
    failed = str(exc)
check("a start that fails says why (for the Network page)", failed and "no suitable device" in failed, failed)
radiocaps.record_test(inv["radios"][0], "own-channel", False, "")
inv2, v2 = box([radio(lyra, "wlan0", "phy0", 6)], wifi_up)
inv2["ap"] = v2
calls.clear()
moved, out = ap.follow(fake_run, inv2, settings, "wlan0")
check(
    "follow: the link came up on channel 6, the hotspot moves there in place, and says so",
    moved
    and "(channel switch)" in out
    and any(c[0] == "wpa_cli" and "chan_switch" in c for c in calls)
    and json.loads(ap.RECORD.read_text())["plan"]["channel"] == 6,
    (out, calls),
)
again = ap.follow(fake_run, inv2, settings, "wlan0")
check(
    "  the same channel again: nothing done, and not said as moved",
    again[0] is False and "still on channel 6" in again[1],
    again,
)
# The move interval lives in ap.follow, so both triggers obey it (HotspotFollow has no hold of its own).
inv1, v1 = box([radio(lyra, "wlan0", "phy0", 1)], wifi_up)
inv1["ap"] = v1
held, why = ap.follow(fake_run, inv1, settings, "wlan0")
check("  inside the move interval: held, and said so", held is False and "moves again after" in why, why)
rec = json.loads(ap.RECORD.read_text())
rec["moved_at"] = 0
ap.RECORD.write_text(json.dumps(rec))
calls.clear()
moved, why = ap.follow(fake_run, inv1, settings, "wlan0")
check(
    "  after the interval: moved by channel switch",
    moved and "(channel switch)" in why and json.loads(ap.RECORD.read_text())["plan"]["channel"] == 1,
    why,
)


def no_switch(*cmd, **kw):
    if cmd[0] == "wpa_cli" and "chan_switch" in cmd:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 4, "", "FAIL")
    return fake_run(*cmd, **kw)


inv6, v6 = box([radio(lyra, "wlan0", "phy0", 6)], wifi_up)
inv6["ap"] = v6
rec = json.loads(ap.RECORD.read_text())
rec["moved_at"] = 0
ap.RECORD.write_text(json.dumps(rec))
calls.clear()
moved, why = ap.follow(no_switch, inv6, settings, "wlan0")
check(
    "  a channel switch that fails: learned csa no, and the hotspot restarted",
    moved
    and "restarted: channel switch failed" in why
    and radiocaps.rows(inv6["radios"][0], radiocaps.stored(inv6["radios"][0]))["csa"]["value"] == "no",
    (why, radiocaps.stored(inv6["radios"][0])),
)
inv11, v11 = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
inv11["ap"] = v11
rec = json.loads(ap.RECORD.read_text())
rec["moved_at"] = 0
ap.RECORD.write_text(json.dumps(rec))
calls.clear()
moved, why = ap.follow(no_switch, inv11, settings, "wlan0")
check(
    "  the learned no stands: restarted without trying the switch again",
    moved and not any(c[0] == "wpa_cli" and "chan_switch" in c for c in calls),
    (why, calls),
)
inv3, v3 = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up)
inv3["ap"] = v3
calls.clear()
p4 = ap.start(fake_run, inv3, settings, {"take_radio": True}, keyfile_path=T / "kf", conf_path=T / "dns.conf")
check(
    "the owner gave up the link: a dead-man timer set, not confirmed until the owner says so from the hotspot",
    any(c[0] == "systemd-run" and "ap-revert" in c for c in calls)
    and json.loads(ap.RECORD.read_text())["confirmed"] is False,
)
ap.confirm(fake_run)
check(
    "  confirmed: the timer stopped, kept",
    ["systemctl", "stop", "irate-box-ap-deadman.timer"] in calls and json.loads(ap.RECORD.read_text())["confirmed"],
)
calls.clear()
ap.stop(fake_run)
check(
    "stop: down, the link reconnected, the hook removed, recorded off",
    ["nmcli", "device", "connect", "wlan0"] in calls
    and not ap.DISPATCHER.exists()
    and json.loads(ap.RECORD.read_text())["up"] is False,
)
p5 = ap.start(fake_run, inv3, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
check(
    "a plan that waits on the owner starts nothing",
    p5["needs_choice"] and json.loads(ap.RECORD.read_text())["up"] is False,
)

# After a reboot (the boot unit, ap.boot).
calls.clear()
ap.start(fake_run, inv, settings)
check(
    "start installs and enables the boot unit",
    (ap.UNITS / ap.BOOT_UNIT).exists()
    and ["systemctl", "enable", ap.BOOT_UNIT] in calls
    and "hub_control ap-boot" in (ap.UNITS / ap.BOOT_UNIT).read_text(),
)
from irate_box.root import radio as radio_mod

waits = []
radio_mod.settle_before_hotspot = lambda *a, **k: waits.append(1) or 0  # nothing joining (never the real nmcli here)
calls.clear()
out = ap.boot(fake_run, inv, settings)
check(
    "boot: on before, planned and started again (ap0 made anew)",
    out.startswith("The hotspot started again on ap0")
    and ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"] in calls,
    out,
)
check("  after waiting for any WiFi link still joining", waits == [1] and "finish joining" not in out, out)
radio_mod.settle_before_hotspot = lambda *a, **k: 4
ap.start(fake_run, inv, settings)
out = ap.boot(fake_run, inv, settings)
check("  and says so when it waited", out.endswith("(after 4 s for the WiFi link to finish joining)."), out)
radio_mod.settle_before_hotspot = lambda *a, **k: 0
ap.start(fake_run, inv3, settings, {"take_radio": True})
calls.clear()
out = ap.boot(fake_run, inv3, settings)
check(
    "  a hotspot that took the link and was never confirmed: the link comes back instead",
    "restored the link" in out
    and ["nmcli", "device", "connect", "wlan0"] in calls
    and json.loads(ap.RECORD.read_text())["up"] is False,
    out,
)
calls.clear()
check("  off before: nothing", ap.boot(fake_run, inv, settings) == "The hotspot is off." and not calls)
check("stop disables the boot unit", ["systemctl", "disable", ap.BOOT_UNIT] in (ap.stop(fake_run) and calls))

# The hostapd route: a box whose radio NetworkManager doesn't run (wpa_supplicant, or nothing).
ap.HOSTAPD_CONF = T / "hostapd.conf"
wpa = radio(lyra, "wlan0", "phy0", 6, owner="wpa_supplicant")
inv4 = {
    "radios": [wpa],
    "uplink": wifi_up,
    "country": "GB",
    "stacks": {"networkmanager": {"running": False}, "hostapd": {"installed": True}},
}
inv4["ap"] = netinv.ap_verdicts(inv4)
p6 = apmode.plan(inv4, inv4["ap"])
conf = ap.hostapd_conf(p6, {"mode": "owe", "password": "", "allow_wpa2": False, "second": "owe"}, country="GB")
check(
    "hostapd route: chosen where wpa_supplicant runs the radio; ap0, channel, country, OWE with management frames protected",
    p6["backend"] == "hostapd"
    and "interface=ap0" in conf
    and "channel=6" in conf
    and "ctrl_interface=/run/hostapd" in conf
    and "country_code=GB" in conf
    and "wpa_key_mgmt=OWE" in conf
    and "ieee80211w=2" in conf
    and "ssid2=" in conf,
    (p6, conf),
)
steps = ap.up_steps(p6)
check(
    "  up: ap0, the address by hand, hostapd, dnsmasq; no NetworkManager",
    ["ip", "addr", "replace", "192.168.4.1/24", "dev", "ap0"] in steps
    and ["systemctl", "restart", ap.HOSTAPD_UNIT] in steps
    and not any(s[0] == "nmcli" for s in steps),
    steps,
)
check(
    "  down: hostapd stopped, the address flushed, ap0 removed",
    ["systemctl", "stop", ap.HOSTAPD_UNIT] in ap.down_steps(p6)
    and ap.down_steps(p6)[-1] == ["iw", "dev", "ap0", "del"],
)
calls.clear()
p7 = ap.start(fake_run, inv4, settings)
check(
    "  start: hostapd's configuration (600) and unit written, not a NetworkManager keyfile",
    ap.HOSTAPD_CONF.stat().st_mode & 0o777 == 0o600
    and (ap.UNITS / ap.HOSTAPD_UNIT).exists()
    and ["systemctl", "restart", ap.HOSTAPD_UNIT] in calls,
)
ap.stop(fake_run)

# hostapd missing: the hotspot installs it when switched on (from Debian, or the Network kit's cache).
from irate_box.root import kits

real_bin, real_cache = ap.HOSTAPD_BIN, kits.install_from_cache
ap.HOSTAPD_BIN = T / "no-hostapd"


def no_cache(names, run=None):
    raise kits.Refused(kits.M("kits.install-packages.not-cached", "not cached"))


kits.install_from_cache = no_cache
inv5 = {**inv4, "stacks": {"networkmanager": {"running": False}, "hostapd": {"installed": False}}}
calls.clear()
ap.start(fake_run, inv5, settings)
check(
    "  hostapd missing: installed when the hotspot is switched on",
    ["apt-get", "install", "-y", "-q", "--no-install-recommends", "hostapd"] in calls,
    calls,
)
ap.stop(fake_run)
ap.HOSTAPD_BIN, kits.install_from_cache = real_bin, real_cache

# The box doctor's hotspot checks (health.check_hotspot).
from irate_box.root import health

inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up)
inv["ap"] = v
ap.start(fake_run, inv, settings)
active = {"nm": True, "dns": True}


def doc_run(*cmd, **kw):
    if cmd[:2] == ("iw", "dev"):
        return fake_run(*cmd)
    if cmd[0] == "nmcli":
        return subprocess.CompletedProcess(
            cmd, 0, "irate-box-ap\nnetplan-wlan0\n" if active["nm"] else "netplan-wlan0\n", ""
        )
    if cmd[:2] == ("systemctl", "is-active"):
        return subprocess.CompletedProcess(cmd, 0 if active["dns"] else 3, "", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")


health.run = doc_run
iw["ap0"] = 11
f = {x["id"]: x for x in health.check_hotspot()}
check(
    "doctor: the hotspot on its channel, its connection and dnsmasq up: ok",
    f["hotspot-iface"]["status"] == "ok" and set(f) == {"hotspot-iface"},
    f,
)
active["dns"] = False
iw["ap0"] = 6
f = {x["id"]: x for x in health.check_hotspot()}
check(
    "  dnsmasq down: a problem with a restart offered; on the wrong channel: a warning",
    f["hotspot-dns"]["status"] == "problem" and f["hotspot-dns"]["actions"] and f["hotspot-iface"]["status"] == "warn",
    f,
)
del iw["ap0"]
f = {x["id"]: x for x in health.check_hotspot()}
check("  its interface gone: a problem", f["hotspot-iface"]["status"] == "problem", f)
iw["ap0"] = 11
roles = {"ap0": "AP", "wlan0": "managed"}


def roles_run(*cmd, **kw):
    if cmd[:2] == ("iw", "dev"):
        text = "phy#0\n" + "".join(f"\tInterface {i}\n\t\ttype {t}\n" for i, t in roles.items())
        return subprocess.CompletedProcess(cmd, 0, text, "")
    return doc_run(*cmd, **kw)


health.run = roles_run
f = {x["id"]: x for x in health.check_wifi_roles()}
check("doctor, WiFi roles: the hotspot on ap0, the link on wlan0, the rule there: ok", set(f) == {"wifi-roles"}, f)
roles["ap0"] = "managed"
f = {x["id"]: x for x in health.check_wifi_roles()}
check(
    "  a client on ap0 (the box's link where its hotspot belongs): a problem, Network → Hotspot",
    f["wifi-roles:ap0"]["status"] == "problem" and "wifi-roles" not in f,
    f,
)
roles.update(ap0="AP", wlan0="AP")
f = {x["id"]: x for x in health.check_wifi_roles()}
check("  an access point on wlan0 (not irate-box's): a warning", f["wifi-roles:wlan0"]["status"] == "warn", f)
roles["wlan0"] = "managed"
ap.NM_RULES.write_text("# an older rule\n")
f = {x["id"]: x for x in health.check_wifi_roles()}
check(
    "  NetworkManager's rule out of date while the hotspot runs: a warning",
    f["wifi-roles:rules"]["status"] == "warn",
    f,
)
ap.NM_RULES.write_text(ap.nm_rules())
health.run = doc_run
iw["ap0"] = None
ap.stop(fake_run)
check("  off: nothing to check", health.check_hotspot() == [])
# Uninstalling: the hotspot off and every file of it gone, whatever its record says, before its firewall.
ap.HOSTAPD_CONF = T / "hostapd.conf"
ap.start(fake_run, inv, settings, keyfile_path=ap.KEYFILE, conf_path=ap.DNSMASQ_CONF)
ap.HOSTAPD_CONF.write_text("interface=ap0\n")
mine = [
    ap.KEYFILE,
    ap.DNSMASQ_CONF,
    ap.HOSTAPD_CONF,
    ap.UNITS / ap.BOOT_UNIT,
    ap.UNITS / ap.DNSMASQ_UNIT,
    ap.DISPATCHER,
    ap.LINKS / ap.LINK_FILE,
    ap.RECORD,
]
check("  a hotspot up, its files written", all(f.exists() for f in mine), [str(f) for f in mine if not f.exists()])
state = {"ap0": True, "active": set()}


def remove_run(*cmd, **kw):
    calls.append(list(cmd))
    if cmd[:3] == ("ip", "link", "show"):
        return subprocess.CompletedProcess(cmd, 0 if state["ap0"] else 1, "", "")
    if cmd[:3] == ("iw", "dev", "ap0") and cmd[3:] == ("del",):
        state["ap0"] = False
    if cmd[:2] == ("systemctl", "is-active"):
        return subprocess.CompletedProcess(cmd, 0, "active\n" if cmd[2] in state["active"] else "inactive\n", "")
    if cmd[:3] == ("nmcli", "-t", "-f"):
        return subprocess.CompletedProcess(
            cmd, 0, "irate-box-ap\n" if "irate-box-ap" in state["active"] else "home\n", ""
        )
    return subprocess.CompletedProcess(cmd, 0, "", "")


calls.clear()
off, problems = ap.remove(remove_run)
check(
    "remove: every unit disabled and stopped, the connection deleted, ap0 removed, nothing of it left",
    off
    and not problems
    and all(
        ["systemctl", "disable", "--now", u] in calls
        for u in (*ap.DEADMAN_UNITS, ap.BOOT_UNIT, ap.DNSMASQ_UNIT, ap.HOSTAPD_UNIT)
    )
    and ["nmcli", "connection", "delete", "irate-box-ap"] in calls
    and ["iw", "dev", "ap0", "del"] in calls
    and not any(f.exists() for f in mine),
    (off, problems, [str(f) for f in mine if f.exists()]),
)
check(
    "  only irate-box's own: no other connection touched",
    not any(
        c[:2] == ["nmcli", "connection"] and c[-1] not in ("irate-box-ap",) and c[2] in ("down", "delete")
        for c in calls
    ),
)
calls.clear()
off, problems = ap.remove(remove_run)
check("  again, with nothing left and no record: still fine", off and not problems, problems)
state.update(ap0=True, active={ap.DNSMASQ_UNIT, "irate-box-ap"})


def stuck_run(*cmd, **kw):
    if cmd[:3] == ("iw", "dev", "ap0"):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, "", "busy")
    return remove_run(*cmd, **kw)


off, problems = ap.remove(stuck_run)
check(
    "  what will not go down is said, and off is False (the uninstaller then stops, the firewall kept)",
    not off
    and any(ap.DNSMASQ_UNIT in p for p in problems)
    and any("irate-box-ap" in p for p in problems)
    and any("ap0" in p for p in problems),
    problems,
)
un = (REPO / "uninstall.sh").read_text()
loop = un.index('say "Stopping and disabling services"')
check(
    "uninstall.sh: the hotspot removed first (hub_control ap-remove), stopping with the firewall kept when it is not off",
    un.index("hub_control ap-remove") < loop < un.index("irate-box-firewall.service", loop)
    and "exit 1" in un[un.index("hub_control ap-remove") : loop],
)
fallback = un[un.index("A partial install, its code gone") : loop]
check(
    "  its fallback, for an install whose code is gone, names every unit and file the module does",
    all(
        n in fallback
        for n in (
            ap.BOOT_UNIT,
            ap.DNSMASQ_UNIT,
            ap.HOSTAPD_UNIT,
            *ap.DEADMAN_UNITS,
            ap.CONNECTION,
            ap.LINK_FILE,
            "90-irate-box-ap",
            "irate-box-ap.nmconnection",
            "ap-dnsmasq.conf",
            "ap-hostapd.conf",
            ap.NM_RULES.name,
            "renamed_from",
        )
    ),
)
check("  and it warns that a device on the hotspot loses its connection", "loses its connection" in un[:loop])

# A driver that can't add a second interface: the radio the hotspot holds has its own interface renamed ap0.
ap.RECORD.write_text(json.dumps({"up": False}))
calls.clear()


def no_second_iface(*cmd, **kw):
    if cmd[:2] == ("iw", "dev") and "interface" in cmd and "add" in cmd:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, "", "command failed: Operation not supported (-95)")
    return fake_run(*cmd, **kw)


inv8, v8 = box([radio(lyra, "wlan0", "phy0")], {"iface": "eth0", "kind": "ethernet"})
inv8["ap"] = v8
p8 = ap.start(no_second_iface, inv8, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
check(
    "no second interface: wlan0 held, then renamed ap0 (never the hotspot on wlan0), recorded to give the name back",
    p8.get("renamed_from") == "wlan0"
    and calls.index(["ip", "link", "set", "wlan0", "down"])
    < calls.index(["ip", "link", "set", "wlan0", "name", "ap0"])
    < calls.index(["nmcli", "connection", "up", "irate-box-ap"])
    and json.loads(ap.RECORD.read_text())["plan"]["renamed_from"] == "wlan0"
    and "interface-name=ap0" in (T / "kf").read_text(),
    calls,
)
calls.clear()
ap.stop(fake_run)
check(
    "  off: ap0 named wlan0 again (not deleted), and given back to NetworkManager",
    ["ip", "link", "set", "ap0", "name", "wlan0"] in calls
    and ["iw", "dev", "ap0", "del"] not in calls
    and calls.index(["ip", "link", "set", "ap0", "name", "wlan0"])
    < calls.index(["nmcli", "device", "set", "wlan0", "managed", "yes"]),
    calls,
)
p8 = ap.start(no_second_iface, inv8, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
calls.clear()


def renamed_there(*cmd, **kw):
    if cmd[:3] == ("ip", "link", "show"):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return fake_run(*cmd, **kw)


ap.remove(renamed_there)
check(
    "  remove: a renamed radio's interface named back, never deleted",
    ["ip", "link", "set", "ap0", "name", "wlan0"] in calls and ["iw", "dev", "ap0", "del"] not in calls,
    calls,
)


def shared_no_second(*cmd, **kw):
    return no_second_iface(*cmd, **kw)


ap.RECORD.write_text(json.dumps({"up": False}))
calls.clear()
try:
    ap.start(shared_no_second, inv, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
    refused = False
except RuntimeError:
    refused = True
check(
    "  a radio shared with the link can't be renamed: the start fails, and nothing of wlan0 is touched",
    refused and not any(c[:3] == ["ip", "link", "set"] and "wlan0" in c for c in calls),
    calls,
)

# An update: the running hotspot's dnsmasq file in this version's form, restarted by the install.
ap.RECORD.write_text(json.dumps({"up": True, "plan": p8}))
ap.DNSMASQ_CONF.write_text("interface=ap0\n")
check(
    "update: an older dnsmasq file (interface=ap0) rewritten on the hotspot's address, its unit to restart",
    ap.DNSMASQ_UNIT in ap.refresh_units() and "listen-address=192.168.4.1" in ap.DNSMASQ_CONF.read_text(),
)
check("  a second run changes nothing", ap.DNSMASQ_UNIT not in ap.refresh_units())
ap.NM_RULES.unlink()
ap.KEYFILE.unlink(missing_ok=True)
check("  NetworkManager's rule: not written where no hotspot was set up through it", ap.refresh_nm_rules(True) is False)
ap.KEYFILE.write_text("[connection]\nid=irate-box-ap\n")
check(
    "  written where one was, then unchanged",
    ap.refresh_nm_rules(True) is True and ap.refresh_nm_rules(True) is False and ap.NM_RULES.exists(),
)
# A record from before every hotspot ran on ap0 (no hotspot_iface): it comes down as that version brought it up.
old_nm = {"kind": "own-radio", "iface": "wlan0", "channel": 6, "backend": "networkmanager"}
old_hostapd = dict(old_nm, backend="hostapd")
check(
    "legacy record: its hotspot is where that version put it (wlan0), for the firewall and the doctor",
    ap.ap_iface(old_nm) == "wlan0"
    and ap.ap_iface({**old_nm, "kind": "follow"}) == "ap0"
    and not ap.holds_radio(old_nm),
)
check(
    "  hostapd: down takes 192.168.4.1/24 off wlan0, and deletes no ap0 it never made",
    ["ip", "addr", "del", "192.168.4.1/24", "dev", "wlan0"] in ap.down_steps(old_hostapd)
    and ["iw", "dev", "ap0", "del"] not in ap.down_steps(old_hostapd),
    ap.down_steps(old_hostapd),
)
check(
    "  NetworkManager: the connection down, wlan0 not handed back (it was never held)",
    ["nmcli", "connection", "down", "irate-box-ap"] in ap.down_steps(old_nm)
    and not any("managed" in c for c in ap.down_steps(old_nm)),
    ap.down_steps(old_nm),
)
ap.RECORD.write_text(json.dumps({"up": True, "plan": old_hostapd}))
calls.clear()
ap.remove(renamed_there)
check(
    "  remove: the legacy address taken off wlan0",
    ["ip", "addr", "del", "192.168.4.1/24", "dev", "wlan0"] in calls,
    calls,
)

# The rename back failing: the plan kept, so ap0 (the radio's own interface) is never deleted later.
ap.RECORD.write_text(json.dumps({"up": False}))
p9 = ap.start(no_second_iface, inv8, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")


def name_stuck(*cmd, **kw):
    calls.append(list(cmd))
    if cmd[:3] == ("ip", "link", "show"):
        return subprocess.CompletedProcess(cmd, 0 if cmd[3] == "ap0" else 1, "", "")
    if cmd[:3] == ("ip", "link", "set") and "name" in cmd:
        return subprocess.CompletedProcess(cmd, 2, "", "RTNETLINK answers: Device or resource busy")
    return subprocess.CompletedProcess(cmd, 0, "", "")


calls.clear()
said = ap.stop(name_stuck)
rec9 = json.loads(ap.RECORD.read_text())
check(
    "rename back failed: off, but it says so, and the plan is kept (last_plan) with renamed_from",
    "still named ap0" in said and rec9["last_plan"]["renamed_from"] == "wlan0" and not rec9["up"],
    (said, rec9),
)
calls.clear()
ap.remove(name_stuck)
check("  remove then names it back, never deletes it", ["iw", "dev", "ap0", "del"] not in calls, calls)
inv9 = copy.deepcopy(inv8)
for r in inv9["radios"]:
    r["iface"] = "ap0"
inv9["ap"] = netinv.ap_verdicts(inv9)
ap.RECORD.write_text(json.dumps({"up": False}))
try:
    ap.start(fake_run, inv9, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
    refused_ap0 = False
except ValueError:
    refused_ap0 = True
check("  ap0 the radio's own interface with no record of its name: refused, nothing deleted", refused_ap0)

# The hostapd route: ap0 kept out of NetworkManager's hands, the rule written where NetworkManager is installed.
ap.RECORD.write_text(json.dumps({"up": False}))
ap.NM_RULES.unlink(missing_ok=True)
calls.clear()
ap.start(fake_run, inv4, settings)
check(
    "hostapd route: NetworkManager told to keep off ap0, and the rule written",
    ["nmcli", "device", "set", "ap0", "managed", "no"] in calls and ap.NM_RULES.exists(),
    calls,
)
ap.stop(fake_run)

# The doctor: NetworkManager too old to apply the rule, on a shared radio.
inv["ap"] = v
ap.RECORD.write_text(json.dumps({"up": False}))
ap.start(fake_run, inv, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
roles.update(ap0="AP", wlan0="managed")
nm_version = {"v": "1.30.0"}


def old_nm_run(*cmd, **kw):
    if cmd[:4] == ("nmcli", "-t", "-f", "VERSION"):
        return subprocess.CompletedProcess(cmd, 0, nm_version["v"] + "\n", "")
    return roles_run(*cmd, **kw)


health.run = old_nm_run
f = {x["id"]: x for x in health.check_wifi_roles()}
check(
    "doctor: NetworkManager 1.30 on a shared radio: a warning, not ok",
    f.get("wifi-roles:nm-version", {}).get("status") == "warn" and "wifi-roles" not in f,
    f,
)
nm_version["v"] = "1.42.4"
f = {x["id"]: x for x in health.check_wifi_roles()}
check("  1.42: ok", set(f) == {"wifi-roles"}, f)
health.run = doc_run
ap.stop(fake_run)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
