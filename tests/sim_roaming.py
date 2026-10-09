# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Roaming's two choices that change the owner's WiFi (hub/roaming.py): a lock to one access
point, recorded, applied at once and taken off again if that access point does not answer; background
scans off while the hotspot shares the radio, kept off by the watchdog and put back; both undone by
choosing again and by undo-all. nmcli and wpa_cli stood in. python3 tests/sim_roaming.py"""
import json, os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="roaming-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.hub import roaming as R, uplink as U  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
PROF = {"uuid": "1111aaaa-0000-4000-8000-000000000001", "name": "HomeNet"}
U._uplink_profile = lambda: PROF
calls, state = [], {"bssid": "", "bgscan": '"simple:30:-65:300"', "fail_up": False, "shared": True}
def fake(*cmd, timeout=60):
    calls.append(cmd)
    if cmd[:3] == ("nmcli", "-g", "802-11-wireless.bssid"):
        return 0, state["bssid"].replace(":", "\\\\:")
    if cmd[:3] == ("nmcli", "con", "modify"):
        state["bssid"] = cmd[-1]
        return 0, ""
    if "up" in cmd and cmd[0] == "nmcli":
        return (4, "Error: no network with BSSID") if state["fail_up"] and state["bssid"] else (0, "")
    if cmd[:2] == ("wpa_cli", "-i") and cmd[-1] == "status":
        return 0, "id=0\nwpa_state=COMPLETED\n"
    if "get_network" in cmd:
        return 0, state["bgscan"]
    if "set_network" in cmd:
        state["bgscan"] = cmd[-1]
        return 0, "OK"
    return 0, ""
U.shared_radio = lambda iface: state["shared"]
msg = R.lock("AA:BB:CC:00:00:02", fake)
rec = R.record()["lock"]
check("lock: the profile's BSSID set and the link brought up on it; the old (none) kept", state["bssid"] == "aa:bb:cc:00:00:02"
      and any(c[:3] == ("nmcli", "-w", "45") for c in calls) and rec["old"] == "" and "locked to aa:bb:cc:00:00:02" in msg, msg)
calls.clear()
check("  the same again: nothing done", "already" in R.lock("aa:bb:cc:00:00:02", fake) and not [c for c in calls if "modify" in c])
state["fail_up"] = True
try:
    R.lock("aa:bb:cc:00:00:03", fake); check("a lock whose access point does not answer: refused", False)
except ValueError as exc:
    check("a new lock whose access point does not answer: refused, the lock before back in force, the link up again", "as before" in str(exc)
          and state["bssid"] == "aa:bb:cc:00:00:02", (str(exc), state["bssid"]))
state["fail_up"] = False
msg = R.unlock(fake)
check("unlock: the BSSID as it was (none), off the record", state["bssid"] == "" and "lock" not in R.record() and "unlocked" in msg, msg)
state["fail_up"] = True
try:
    R.lock("aa:bb:cc:00:00:04", fake); check("a first lock that does not answer: refused", False)
except ValueError as exc:
    check("a first lock that does not answer: refused, taken off again, nothing recorded", "taken off again" in str(exc)
          and state["bssid"] == "" and "lock" not in R.record())
state["fail_up"] = False
try:
    R.lock("aa:bb", fake); check("not a BSSID: refused", False)
except ValueError:
    check("not a BSSID: refused", True)
msg = R.no_scan(True, "wlan0", fake)
check("no-scan: the background scan cleared live while the hotspot shares the radio, the old one kept", state["bgscan"] == '""'
      and R.record()["no-scan"]["was"] == "simple:30:-65:300" and "background scans off" in msg, msg)
state["bgscan"] = '"simple:30:-65:300"'   # NetworkManager connected again and set it
check("  the watchdog clears it again", "background scans off" in R.clear_scan("wlan0", fake) and state["bgscan"] == '""')
check("  and does nothing when it is off already", R.clear_scan("wlan0", fake) == "")
state["shared"] = False; state["bgscan"] = '"simple:30:-65:300"'
check("  nor while the hotspot does not share the radio", R.clear_scan("wlan0", fake) == "" and state["bgscan"] == '"simple:30:-65:300"')
state["shared"] = True; R.clear_scan("wlan0", fake)
msg = R.no_scan(False, "wlan0", fake)
check("  off: the scan back as NetworkManager had it", state["bgscan"] == '"simple:30:-65:300"' and "no-scan" not in R.record(), msg)
said = R.apply({"roaming": "lock", "lock_bssid": "aa:bb:cc:00:00:01"}, {"roaming": "roam"}, "wlan0", fake)
check("apply: roam to lock locks", "locked to aa:bb:cc:00:00:01" in said and state["bssid"] == "aa:bb:cc:00:00:01", said)
said = R.apply({"roaming": "no-scan"}, {"roaming": "lock", "lock_bssid": "aa:bb:cc:00:00:01"}, "wlan0", fake)
check("  lock to no-scan: unlocked, scans off", "unlocked" in said and "background scans off" in said and state["bssid"] == "", said)
check("  roam to roam: nothing", R.apply({"roaming": "roam"}, {"roaming": "roam"}, "wlan0", fake) == "")
said = R.undo_all("wlan0", fake)
check("undo-all puts back what is in force", any("background scans back" in x for x in said) and not R.record(), said)
check("uninstall.sh runs undo-all when roaming changed anything", "uplink-roaming.json" in (REPO / "uninstall.sh").read_text())
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check("saving settings applies roaming first, and keeps the old if it fails", "roaming.apply(s, old, iface)" in hc and "not saved:" in hc)
# The box doctor: roaming often with the hotspot on the same radio, said with the choices.
from irate_box.root import health  # noqa: E402
inv = {"radios": [{"iface": "wlan0", "roaming": {"ssid": "HomeNet", "aps": [{}, {}, {}], "channels": [6, 11], "hotspot_shares": True}}]}
st = {"iface": "wlan0", "roams_hour": 9, "chosen": {"roaming": "roam"}}
f = health.roaming_findings(st, inv)
check("the doctor: 9 roams an hour with the hotspot on the radio, a warning naming the choices", len(f) == 1 and f[0]["status"] == "warn"
      and "9 times" in f[0]["detail"] and "lock to one access point" in f[0]["fix"] and "Network → Staying" in f[0]["fix"], f)
check("  not for a few roams, nor with no hotspot on the radio, nor once a choice is made", health.roaming_findings(dict(st, roams_hour=4), inv) == []
      and health.roaming_findings(st, {"radios": [{"iface": "wlan0", "roaming": dict(inv["radios"][0]["roaming"], hotspot_shares=False)}]}) == []
      and health.roaming_findings(dict(st, chosen={"roaming": "no-scan"}), inv) == [])
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
