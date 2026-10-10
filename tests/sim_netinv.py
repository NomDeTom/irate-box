# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot verdicts (hub/netinv.py ap_verdicts) for each kind of radio, offline: the one-line
detail the doctor and install.sh print, and the conditions /admin lists one per line.
python3 tests/sim_netinv.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import netinv  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

def inv(radio, uplink_iface="wlan0", nm=True, hostapd=True):
    r = {"phy": "phy0", "iface": "wlan0", "type": "managed", "ap": True, "owner": "networkmanager", "driver": "aic8800_fdrv"}
    r.update(radio)
    return {"radios": [r], "uplink": {"iface": uplink_iface, "kind": "wifi"},
            "stacks": {"networkmanager": {"running": nm}, "hostapd": {"installed": hostapd}}}
def kinds(v):
    return [c["kind"] for c in v["conditions"]]

v = netinv.ap_verdicts(inv({}, uplink_iface="eth0"))[0]
check("its own radio: how, nothing else", v["possible"] and v["mode"] == "radio-alone" and kinds(v) == ["how"], v)
v = netinv.ap_verdicts(inv({"ap_beside_client": True, "channels_at_once": 3}))[0]
check("beside the client, 3 channels: how, limit, untested", v["mode"] == "beside-client" and kinds(v) == ["how", "limit", "untested"], kinds(v))
check("…and the detail line is as before", v["detail"].startswith("A second interface beside the client link") and "untested" in v["detail"], v["detail"])
v = netinv.ap_verdicts(inv({"ap_beside_client": True, "channels_at_once": 1}))[0]
check("beside the client, 1 channel: the hotspot follows", kinds(v) == ["how", "limit"] and "follows your WiFi's channel" in v["conditions"][1]["text"], v["conditions"])
v = netinv.ap_verdicts(inv({"ap_beside_client": False}))[0]
check("takes the radio: a limit, saying the box leaves", kinds(v) == ["limit"] and "leave your network" in v["conditions"][0]["text"], v["conditions"])
v = netinv.ap_verdicts(inv({"owner": "wpa_supplicant"}, uplink_iface="eth0", nm=False, hostapd=False))[0]
check("hostapd needed and missing: a needs line", v["backend"] == "hostapd" and kinds(v)[-1] == "needs" and "apt install hostapd" in v["detail"], v)
v = netinv.ap_verdicts(inv({"owner": "iwd"}, uplink_iface="eth0"))[0]
check("a manager irate-box can't drive: not possible, a limit saying so", not v["possible"] and kinds(v)[-1] == "limit" and "iwd" in v["conditions"][-1]["text"], v)
v = netinv.ap_verdicts(inv({"ap": False}))[0]
check("no AP mode at all: not possible, one limit", not v["possible"] and kinds(v) == ["limit"], v)
h = netinv._h("x", "warn", "t", "d", iface="wlan0")
check("a finding can name its device", h["iface"] == "wlan0" and "iface" not in netinv._h("y", "ok", "t", "d"))

# Roaming: the access points sharing the network, the background scan, the choices that work.
# nmcli's and wpa_cli's own output from a real board, the addresses and names changed.
LIST = ("04\\:95\\:E6\\:00\\:00\\:01:6:2437 MHz:100:HomeNet\n50\\:0F\\:F5\\:00\\:00\\:02:6:2437 MHz:87:HomeNet\n"
        "50\\:0F\\:F5\\:00\\:00\\:03:11:2462 MHz:80:HomeNet\nB2\\:41\\:D9\\:00\\:00\\:04:6:2437 MHz:0:Irate-Box\n")
aps = netinv.parse_wifi_list(LIST, "HomeNet")
check("three access points for the network, strongest first, the hotspot's own not among them", [a["bssid"] for a in aps]
      == ["04:95:e6:00:00:01", "50:0f:f5:00:00:02", "50:0f:f5:00:00:03"] and aps[2]["channel"] == 11 and aps[0]["freq"] == 2437, aps)
def fake(*cmd, **_):
    if cmd[:2] == ("nmcli", "-t"):
        return 0, LIST
    if cmd[-1] == "status":
        return 0, "bssid=04:95:e6:00:00:01\nfreq=2437\nssid=HomeNet\nid=0\nwpa_state=COMPLETED\n"
    if cmd[-1] == "bgscan":
        return 0, '"simple:30:-65:300"'
    return 1, ""
netinv.shutil.which = lambda c: "/usr/sbin/" + c
r = {"iface": "wlan0", "owner": "networkmanager", "link": {"ssid": "HomeNet"}}
f = netinv.roaming_facts(r, {"version": "1.52.1"}, fake)
check("NetworkManager with wpa_supplicant's socket: two channels, NM's background scan read, all four choices",
      f["channels"] == [6, 11] and f["bgscan"] == "simple:30:-65:300" and f["choices"] == ["roam", "no-scan", "lock"] and f["nm_version"] == "1.52.1", f)
f = netinv.roaming_facts(dict(r, owner="wpa_supplicant"), None, fake)
check("  wpa_supplicant alone: no lock (NetworkManager's), said why", "lock" not in f["choices"] and "NetworkManager" in f["why"]["lock"] and f["aps"] == [], f)
f = netinv.roaming_facts(r, {}, lambda *c, **_: (1, ""))
check("  no control socket: no background-scan choice, said why", "no-scan" not in f["choices"] and "control socket" in f["why"]["no-scan"], f)
# iw dev on a board with a P2P device between the hotspot and the link, with no interface of its own.
IW = ("phy#3\n\tInterface ap0\n\t\tifindex 9\n\t\taddr b2:00:00:00:00:01\n\t\tssid Irate-Box\n\t\ttype AP\n"
      "\t\tchannel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz\n\tUnnamed/non-netdev interface\n\t\twdev 0x300000002\n"
      "\t\taddr be:00:00:00:00:02\n\t\ttype P2P-device\n\tInterface wlan0\n\t\tifindex 8\n\t\taddr b8:00:00:00:00:03\n"
      "\t\tssid HomeNet\n\t\ttype managed\n\t\tchannel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz\n")
dev = netinv.parse_iw_dev(IW)
check("iw dev: the hotspot stays AP with its own address, the P2P device's lines go nowhere", dev["ap0"]["type"] == "AP"
      and dev["ap0"]["addr"] == "b2:00:00:00:00:01" and dev["wlan0"]["type"] == "managed" and set(dev) == {"ap0", "wlan0"}, dev)
# What lowers the chance of staying on the network (link_findings): from the link's connection and the defaults.
def link_inv(profile, defaults=None, radio=None, route="wlan0", **extra):
    r = {"iface": "wlan0", "type": "managed", "driver": "brcmfmac", "profile": profile, "roaming": {"aps": [{}, {}, {}]}}
    r.update(radio or {})
    return {"radios": [r], "default_route": {"iface": route} if route else None,
            "stacks": {"networkmanager": {"running": True, "defaults": defaults or {}}, "netplan": extra.pop("netplan", None)}, **extra}
base = {"uuid": "u1", "name": "Home", "ssid": "Home", "autoconnect": True, "powersave": 0}
ids = lambda i: sorted(h["id"].split(":")[0] for h in netinv.link_findings(i))  # noqa: E731
check("a plain connection: nothing found", ids(link_inv(base)) == [], netinv.link_findings(link_inv(base)))
check("not joining by itself, locked to an access point and a band: both found",
      ids(link_inv({**base, "autoconnect": False, "bssid_lock": "aa:bb:cc:dd:ee:ff", "band_lock": "bg"})) == ["autoconnect-off", "locked"])
h = [x for x in netinv.link_findings(link_inv({**base, "bssid_lock": "aa:bb:cc:dd:ee:ff", "band_lock": "bg"})) if x["id"].startswith("locked")][0]
check("  the lock says what and how many access points share the name", "the access point aa:bb:cc:dd:ee:ff and the 2.4 GHz band" in h["title"]
      and "3 access points" in h["detail"], h)
check("power save on for the connection: found", ids(link_inv({**base, "powersave": 3})) == ["powersave-set"])
check("power save on as the box default, the connection leaving it: found",
      ids(link_inv(base, {"settings": {"powersave": {"value": "3"}}})) == ["powersave-set"])
check("  but off for that interface, which comes first: not found",
      ids(link_inv(base, {"settings": {"powersave": {"value": "3"}}, "ifaces": {"wlan0": {"powersave": {"value": "2"}}}})) == [])
check("a random MAC address, the connection's own or the box default's: found",
      ids(link_inv({**base, "cloned": "random"})) == ["mac-random"] and ids(link_inv(base, {"settings": {"cloned_mac": {"value": "random"}}})) == ["mac-random"])
check("two files for one connection: found", ids(link_inv(base, duplicate_profiles=[{"uuid": "u1", "name": "Home", "files": ["a", "b"]}])) == ["duplicate"])
check("netplan that can't be read: a problem", [h["status"] for h in netinv.link_findings(link_inv(base, netplan={"error": "x.yaml:3:5: Invalid YAML"}))] == ["problem"])
check("the AIC8800 driver without its own reset: found; with it: not",
      ids(link_inv(base, radio={"driver": "aic8800_fdrv", "params": {"ps_on": "Y"}})) == ["driver-wedge"]
      and ids(link_inv(base, radio={"driver": "aic8800_fdrv", "params": {"recovered_at": "0"}})) == [])
import tempfile as _tf  # noqa: E402
d = Path(_tf.mkdtemp())
(d / "a.nmconnection").write_text("[connection]\nid=Home\nuuid=0c3bb1fa-0f6a-3707-8c8d-4e23598253db\n")
(d / "b.nmconnection").write_text("[connection]\nid=Home\nuuid=0c3bb1fa-0f6a-3707-8c8d-4e23598253db\n")
(d / "c.nmconnection").write_text("[connection]\nid=Other\nuuid=11111111-1111-1111-1111-111111111111\n")
dup = netinv.duplicate_profiles((str(d),))
check("  two of three files share one: that pair", len(dup) == 1 and dup[0]["files"] == ["a.nmconnection", "b.nmconnection"], dup)

# The box doctor shows the inventory's warnings and problems, with the look's age when it's old.
import json as _json, os as _os, time as _time  # noqa: E402
_os.environ["HUB_STATE_DIR"] = str(d)
from irate_box.root import health  # noqa: E402
health.CONTROL = d
inv = {"at": _time.time(), "hazards": [{"id": "locked:u1", "status": "warn", "title": "Home: locked", "detail": "Can't roam.", "fix": "Unlock it."},
                                       {"id": "ok-thing", "status": "ok", "title": "fine", "detail": "", "fix": ""}]}
(d / "netinv.json").write_text(_json.dumps(inv))
f = health.check_inventory()
check("doctor: the warnings shown, with their fix; the fine ones not", [x["id"] for x in f] == ["net:locked:u1"] and f[0]["fix"] == "Unlock it.", f)
inv["at"] = _time.time() - 2 * 86400
(d / "netinv.json").write_text(_json.dumps(inv))
f = health.check_inventory()
check("  an old look: its age said, and a finding to look again", "48 h ago" in f[0]["detail"] and f[-1]["id"] == "netinv", f)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
