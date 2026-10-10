# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Hotspot profiles that are not irate-box's (root/otherap.py), offline with nmcli stood in: removed with
the profile kept and brought back by undo; left and no longer listed; irate-box's own and client profiles
refused; and the inventory's finding for each choice (hub/netinv.py).
python3 tests/sim_otherap.py"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="otherap-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"), HUB_NM_CONNECTIONS=str(T / "nm"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import netinv  # noqa: E402
from irate_box.root import otherap as O  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


SETUP, OURS, HOME = "33564e5b-5cb3-4b45-ac5f-a092c4f7ddba", "11111111-1111-1111-1111-111111111111", "0c3bb1fa-0f6a-3707-8c8d-4e23598253db"
(T / "nm").mkdir()
(T / "nm/Hotspot.nmconnection").write_text("[connection]\nid=Hotspot\nuuid=" + SETUP + "\n[wifi]\nmode=ap\nssid=armbiansetup-lyra\n")
profiles = {SETUP: ("Hotspot", "ap", "armbiansetup-lyra", T / "nm/Hotspot.nmconnection"),
            OURS: ("irate-box-ap", "ap", "Irate-Box", T / "nm/irate-box-ap.nmconnection"),
            HOME: ("Home", "infrastructure", "Home", T / "nm/home.nmconnection")}
loaded = set(profiles)


def fake_run(*cmd, timeout=60):
    if cmd[:3] == ("nmcli", "-t", "-f") and cmd[4:7] == ("con", "show", "uuid"):
        u = cmd[7]
        if u not in loaded:
            return 10, "no such connection"
        name, mode, ssid, _f = profiles[u]
        return 0, f"connection.id:{name}\n802-11-wireless.mode:{mode}\n802-11-wireless.ssid:{ssid}"
    if cmd[:6] == ("nmcli", "-t", "-f", "UUID,FILENAME", "con", "show"):
        return 0, "\n".join(f"{u}:{profiles[u][3]}" for u in loaded)
    if cmd[:3] == ("nmcli", "con", "delete"):
        loaded.discard(cmd[4])
        profiles[cmd[4]][3].unlink(missing_ok=True)
        return 0, ""
    if cmd[:3] == ("nmcli", "con", "load"):
        loaded.add(SETUP)
        return 0, ""
    return 0, ""


O.run = fake_run
said = O.remove(SETUP)
rec = O.record()
check("remove: gone from NetworkManager, its profile kept", SETUP not in loaded and not (T / "nm/Hotspot.nmconnection").exists()
      and Path(rec[SETUP]["kept"]).read_text().startswith("[connection]\nid=Hotspot"), said)
check("  the record: names and the choice, nothing else", set(rec[SETUP]) == {"name", "ssid", "choice", "kept", "file", "at"} and rec[SETUP]["choice"] == "removed")
said = O.undo(SETUP)
check("undo: brought back and loaded", SETUP in loaded and (T / "nm/Hotspot.nmconnection").exists() and O.record() == {}, said)
said = O.leave(SETUP)
check("leave: kept as it is, recorded as left", SETUP in loaded and O.record()[SETUP]["choice"] == "left", said)
for bad, why in ((OURS, "irate-box's own"), (HOME, "only another hotspot"), ("x", "not a connection")):
    try:
        O.remove(bad)
        check(f"refused: {why}", False)
    except ValueError as exc:
        check(f"refused: {why}", why in str(exc), str(exc))


def found(autoconnect, chosen):
    p = {"uuid": SETUP, "name": "Hotspot", "ssid": "armbiansetup-lyra", "mode": "ap", "autoconnect": autoconnect, "iface": "wlan0"}
    inv = {"radios": [], "country": "GB", "other_ap": chosen, "stacks": {"networkmanager": {"running": True, "wifi_profiles": [p]}}}
    return [h for h in netinv.hazards(inv) if h["id"] == f"other-ap:{SETUP}"]


check("inventory: one not chosen for, listed with what to do", [h["status"] for h in found(False, {})] == ["warn"]
      and "remove it" in found(False, {})[0]["fix"])
check("  left: no longer listed", found(False, {SETUP: {"choice": "left"}}) == [])
check("  set to start by itself: warned, left or not", [h["status"] for h in found(True, {SETUP: {"choice": "left"}})] == ["warn"]
      and "start by itself" in found(True, {})[0]["title"])

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
