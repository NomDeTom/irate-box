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

# Running it (root/ap.py's executor), with the commands stood in and the files in a temp folder.
import json, os, subprocess, tempfile  # noqa: E401,E402
T = Path(tempfile.mkdtemp(prefix="ap-"))
ap.RECORD, ap.TRIED, ap.DISPATCHER, ap.UNITS = T / "ap.json", T / "ap-tried.json", T / "90-irate-box-ap", T / "units"
ap.KEYFILE, ap.DNSMASQ_CONF = T / "kf", T / "dns.conf"
ap.LINKS = T / "network"
calls, iw = [], {"wlan0": 11, "ap0": None}
def fake_run(*cmd, **kw):
    calls.append(list(cmd))
    if cmd[:2] == ("iw", "dev"):
        text = "phy#0\n" + "".join(f"\tInterface {i}\n\t\ttype {'AP' if i == 'ap0' else 'managed'}\n" + (f"\t\tchannel {c} (2462 MHz), width: 20 MHz\n" if c else "")
                                   for i, c in iw.items())
        return subprocess.CompletedProcess(cmd, 0, text, "")
    if cmd[:3] == ("nmcli", "connection", "up"):
        kf = (T / "kf").read_text()
        iw["ap0"] = int(next(l for l in kf.splitlines() if l.startswith("channel=")).split("=")[1])
    return subprocess.CompletedProcess(cmd, 0, "", "")
settings = {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"}
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up); inv["ap"] = v
plan = ap.start(fake_run, inv, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
rec = json.loads(ap.RECORD.read_text())
check("start: the files written (the keyfile 600), the unit installed, the hook in place, the steps run, recorded",
      (T / "kf").stat().st_mode & 0o777 == 0o600 and (ap.UNITS / ap.DNSMASQ_UNIT).exists() and os.access(ap.DISPATCHER, os.X_OK)
      and ["nmcli", "connection", "up", "irate-box-ap"] in calls and rec["up"] and rec["confirmed"] and rec["plan"]["channel"] == 11, rec)
link = ap.LINKS / ap.LINK_FILE
check("  udev told to keep ap0's name (a USB radio's ap0 became wlx… on the Lyra), before ap0 is made",
      link.exists() and "OriginalName=ap0" in link.read_text() and "Name=ap0" in link.read_text()
      and calls.index(["udevadm", "control", "--reload"]) < calls.index(["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"]))
calls.clear()
worked, p2 = ap.try_own_channel(fake_run, inv, settings, wait=0, sleep=lambda s: None)
check("the try: the hotspot on a channel other than the link's (1), held, so it works; recorded; then back to the plan",
      worked is True and json.loads(ap.TRIED.read_text()) == {"phy0": True} and p2["kind"] == "own-channel", (worked, p2))
iw_force = {"wlan0": 11}
def forced_run(*cmd, **kw):
    out = fake_run(*cmd, **kw)
    if cmd[:3] == ("nmcli", "connection", "up"):
        iw["ap0"] = iw["wlan0"]   # a driver that drags the AP onto the link's channel
    return out
worked, p3 = ap.try_own_channel(forced_run, inv, settings, wait=0, sleep=lambda s: None)
check("  a driver that drags the hotspot onto the link's channel: the try fails, recorded, the plan becomes following",
      worked is False and json.loads(ap.TRIED.read_text()) == {"phy0": False} and p3["kind"] == "follow", (worked, p3))
ap.TRIED.unlink()
def refusing_run(*cmd, **kw):
    if cmd[:3] == ("nmcli", "connection", "up") and "channel=11" not in (T / "kf").read_text():
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 4, "", "Error: Connection activation failed")
    return fake_run(*cmd, **kw)
calls.clear()
worked, p3b = ap.try_own_channel(refusing_run, inv, settings, wait=0, sleep=lambda s: None)
check("  a driver that won't start the hotspot off the link's channel: the try fails (no crash), undone, then following",
      worked is False and json.loads(ap.TRIED.read_text()) == {"phy0": False} and p3b["kind"] == "follow"
      and ["iw", "dev", "ap0", "del"] in calls and json.loads(ap.RECORD.read_text())["up"], (worked, p3b))
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
ap.TRIED.write_text(json.dumps({"phy0": False}))
inv2, v2 = box([radio(lyra, "wlan0", "phy0", 6)], wifi_up); inv2["ap"] = v2
calls.clear()
out = ap.follow(fake_run, inv2, settings, "wlan0")
check("follow: the link came up on channel 6, the hotspot moves there", "moved to channel 6" in out and json.loads(ap.RECORD.read_text())["plan"]["channel"] == 6, out)
check("  the same channel again: nothing done", "still on channel 6" in ap.follow(fake_run, inv2, settings, "wlan0"))
inv3, v3 = box([radio(lyra, "wlan0", "phy0", 11, ap_beside_client=False)], wifi_up); inv3["ap"] = v3
calls.clear()
p4 = ap.start(fake_run, inv3, settings, {"take_radio": True}, keyfile_path=T / "kf", conf_path=T / "dns.conf")
check("the owner gave up the link: a dead-man timer set, not confirmed until the owner says so from the hotspot",
      any(c[0] == "systemd-run" and "ap-revert" in c for c in calls) and json.loads(ap.RECORD.read_text())["confirmed"] is False)
ap.confirm(fake_run)
check("  confirmed: the timer stopped, kept", ["systemctl", "stop", "irate-box-ap-deadman.timer"] in calls and json.loads(ap.RECORD.read_text())["confirmed"])
calls.clear()
ap.stop(fake_run)
check("stop: down, the link reconnected, the hook removed, recorded off", ["nmcli", "device", "connect", "wlan0"] in calls
      and not ap.DISPATCHER.exists() and json.loads(ap.RECORD.read_text())["up"] is False)
p5 = ap.start(fake_run, inv3, settings, keyfile_path=T / "kf", conf_path=T / "dns.conf")
check("a plan that waits on the owner starts nothing", p5["needs_choice"] and json.loads(ap.RECORD.read_text())["up"] is False)

# After a reboot (the boot unit, ap.boot).
calls.clear()
ap.start(fake_run, inv, settings)
check("start installs and enables the boot unit", (ap.UNITS / ap.BOOT_UNIT).exists() and ["systemctl", "enable", ap.BOOT_UNIT] in calls
      and "hub_control ap-boot" in (ap.UNITS / ap.BOOT_UNIT).read_text())
calls.clear()
out = ap.boot(fake_run, inv, settings)
check("boot: on before, planned and started again (ap0 made anew)", out.startswith("started again on ap0") and
      ["iw", "dev", "wlan0", "interface", "add", "ap0", "type", "__ap"] in calls, out)
ap.start(fake_run, inv3, settings, {"take_radio": True})
calls.clear()
out = ap.boot(fake_run, inv3, settings)
check("  a hotspot that took the link and was never confirmed: the link comes back instead",
      "the link is back" in out and ["nmcli", "device", "connect", "wlan0"] in calls and json.loads(ap.RECORD.read_text())["up"] is False, out)
calls.clear()
check("  off before: nothing", ap.boot(fake_run, inv, settings) == "the hotspot is off" and not calls)
check("stop disables the boot unit", ["systemctl", "disable", ap.BOOT_UNIT] in (ap.stop(fake_run) and calls))

# The hostapd route: a box whose radio NetworkManager doesn't run (wpa_supplicant, or nothing).
ap.HOSTAPD_CONF = T / "hostapd.conf"
wpa = radio(lyra, "wlan0", "phy0", 6, owner="wpa_supplicant")
inv4 = {"radios": [wpa], "uplink": wifi_up, "country": "GB", "stacks": {"networkmanager": {"running": False}, "hostapd": {"installed": True}}}
inv4["ap"] = netinv.ap_verdicts(inv4)
p6 = apmode.plan(inv4, inv4["ap"])
conf = ap.hostapd_conf(p6, {"mode": "owe", "password": "", "allow_wpa2": False, "second": "owe"}, country="GB")
check("hostapd route: chosen where wpa_supplicant runs the radio; ap0, channel, country, OWE with management frames protected",
      p6["backend"] == "hostapd" and "interface=ap0" in conf and "channel=6" in conf and "country_code=GB" in conf
      and "wpa_key_mgmt=OWE" in conf and "ieee80211w=2" in conf and "ssid2=" in conf, (p6, conf))
steps = ap.up_steps(p6)
check("  up: ap0, the address by hand, hostapd, dnsmasq; no NetworkManager",
      ["ip", "addr", "replace", "192.168.4.1/24", "dev", "ap0"] in steps and ["systemctl", "restart", ap.HOSTAPD_UNIT] in steps
      and not any(s[0] == "nmcli" for s in steps), steps)
check("  down: hostapd stopped, the address flushed, ap0 removed", ["systemctl", "stop", ap.HOSTAPD_UNIT] in ap.down_steps(p6)
      and ap.down_steps(p6)[-1] == ["iw", "dev", "ap0", "del"])
calls.clear()
p7 = ap.start(fake_run, inv4, settings)
check("  start: hostapd's configuration (600) and unit written, not a NetworkManager keyfile",
      ap.HOSTAPD_CONF.stat().st_mode & 0o777 == 0o600 and (ap.UNITS / ap.HOSTAPD_UNIT).exists() and ["systemctl", "restart", ap.HOSTAPD_UNIT] in calls)
ap.stop(fake_run)

# The box doctor's hotspot checks (health.check_hotspot).
from irate_box.root import health  # noqa: E402
inv, v = box([radio(lyra, "wlan0", "phy0", 11)], wifi_up); inv["ap"] = v
ap.start(fake_run, inv, settings)
active = {"nm": True, "dns": True}
def doc_run(*cmd, **kw):
    if cmd[:2] == ("iw", "dev"):
        return fake_run(*cmd)
    if cmd[0] == "nmcli":
        return subprocess.CompletedProcess(cmd, 0, "irate-box-ap\nnetplan-wlan0\n" if active["nm"] else "netplan-wlan0\n", "")
    if cmd[:2] == ("systemctl", "is-active"):
        return subprocess.CompletedProcess(cmd, 0 if active["dns"] else 3, "", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")
health.run = doc_run
iw["ap0"] = 11
f = {x["id"]: x for x in health.check_hotspot()}
check("doctor: the hotspot on its channel, its connection and dnsmasq up: ok", f["hotspot-iface"]["status"] == "ok" and set(f) == {"hotspot-iface"}, f)
active["dns"] = False; iw["ap0"] = 6
f = {x["id"]: x for x in health.check_hotspot()}
check("  dnsmasq down: a problem with a restart offered; on the wrong channel: a warning",
      f["hotspot-dns"]["status"] == "problem" and f["hotspot-dns"]["actions"] and f["hotspot-iface"]["status"] == "warn", f)
del iw["ap0"]
f = {x["id"]: x for x in health.check_hotspot()}
check("  its interface gone: a problem", f["hotspot-iface"]["status"] == "problem", f)
iw["ap0"] = None; ap.stop(fake_run)
check("  off: nothing to check", health.check_hotspot() == [])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
