# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot verdicts (hub/netinv.py ap_verdicts) for each kind of radio, offline: the one-line
detail the doctor and install.sh print, and the conditions /admin lists one per line (2026-10-06).
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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
