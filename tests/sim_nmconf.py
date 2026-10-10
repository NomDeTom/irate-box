# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager's box defaults (hub/nmconf.py) and what the doctor makes of retries (hub/netinv.py),
offline: the files read in NetworkManager's order, the later one winning, /etc shadowing /run and
/usr/lib; irate-box's drop-in written with only the owner's choices, taken out again by "default";
the root action (hub_control.nm_defaults) writing it and reloading NetworkManager; the retry findings
reading the value in use, from the profile, the box default or NetworkManager's own.
python3 tests/sim_nmconf.py"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="nmconf-"))
os.environ.update(HUB_NM_ETC=str(T / "etc"), HUB_NM_RUN=str(T / "run"), HUB_NM_LIB=str(T / "lib"),
                  HUB_NM_INTERN=str(T / "intern.conf"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import netinv, nmconf  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# The Lyra's files, as the image ships them.
put(T / "etc/NetworkManager.conf", "[main]\nplugins=ifupdown,keyfile\n")
put(T / "lib/conf.d/no-mac-addr-change.conf", "[device-31-mac-addr-change]\nmatch-device=driver:eagle_sdio,driver:wl\nwifi.scan-rand-mac-address=no\n")
put(T / "run/conf.d/10-globally-managed-devices.conf", "")
put(T / "etc/conf.d/00-armbian-readme.conf", "# nothing\n")
put(T / "etc/conf.d/zz-10-override-wifi-random-mac-disable.conf",
    "[connection]\nwifi.mac-address-randomization=1\n[device]\nwifi.scan-rand-mac-address=no\n")
put(T / "etc/conf.d/zz-20-override-wifi-powersave-disable.conf", "[connection]\nwifi.powersave = 2\n")

names = [f.name for f in nmconf.files()]
check("read in NetworkManager's order: the main file, then conf.d by name across the three places",
      names == ["NetworkManager.conf", "00-armbian-readme.conf", "10-globally-managed-devices.conf",
                "no-mac-addr-change.conf", "zz-10-override-wifi-random-mac-disable.conf",
                "zz-20-override-wifi-powersave-disable.conf"], names)
e = nmconf.effective()
st = e["settings"]
check("power save: off, from Armbian's file (a key with spaces round its '=')",
      st["powersave"]["value"] == "2" and st["powersave"]["file"].endswith("zz-20-override-wifi-powersave-disable.conf"), st["powersave"])
check("scan MAC: the board's own, from Armbian's file", st["scan_mac"]["value"] == "no" and "zz-10" in st["scan_mac"]["file"])
check("retries: nothing sets them, so NetworkManager's own",
      st["auth_retries"]["value"] is None and st["autoconnect_retries"]["value"] is None and st["auth_retries"]["mine"] is None)
check("the older MAC key shown with what it means", e["legacy"] and e["legacy"][0]["means"].startswith("the board's own"), e["legacy"])
check("a section for some devices only, shown as such", e["scoped"] and e["scoped"][0]["match"].startswith("driver:eagle_sdio"), e["scoped"])

# /etc shadows /run, which shadows /usr/lib, for files of the same name.
put(T / "run/conf.d/50-x.conf", "[connection]\nconnection.auth-retries=7\n")
put(T / "etc/conf.d/50-x.conf", "[connection]\nconnection.auth-retries=5\n")
check("a file in /etc shadows one of the same name in /run", nmconf.effective()["settings"]["auth_retries"]["value"] == "5")
(T / "etc/conf.d/50-x.conf").unlink(); (T / "run/conf.d/50-x.conf").unlink()

# The owner's choices: validated, written, read back, and taken out.
try:
    nmconf.validate({"mac": "sometimes"}); check("validate refuses a value not offered", False)
except ValueError:
    check("validate refuses a value not offered", True)
try:
    nmconf.validate({"bogus": "1"}); check("validate refuses a setting not offered", False)
except ValueError:
    check("validate refuses a setting not offered", True)
check("validate: counts as numbers, 'default' as taking it out",
      nmconf.validate({"auth_retries": "0", "autoconnect_retries": "default"}) == {"auth_retries": "0", "autoconnect_retries": None})
text = nmconf.dropin_text({"autoconnect_retries": "0", "auth_retries": "0", "mac": None})
check("the drop-in holds only what was chosen, each in its section",
      "[main]\nautoconnect-retries-default=0" in text and "[connection]\nconnection.auth-retries=0" in text and "cloned" not in text, text)
check("nothing chosen: no file text", nmconf.dropin_text({"mac": None}) == "")

# The root action: writes the drop-in, reloads NetworkManager, says what it did.
os.environ.update(HUB_ETC_DIR=str(T / "hub-etc"), HUB_STATE_DIR=str(T / "state"))
(T / "hub-etc").mkdir(); (T / "state").mkdir()
from irate_box.root import hub_control  # noqa: E402
ran = []
hub_control.run = lambda *c, **k: ran.append(c) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
hub_control.netinv.scan = lambda *a, **k: {}
hub_control.netinv.write = lambda *a, **k: None
said = hub_control.nm_defaults({"changes": {"auth_retries": "0", "autoconnect_retries": "0"}})
e = nmconf.effective()["settings"]
check("the action: irate-box's file written, sorting after Armbian's, and NetworkManager reloaded",
      nmconf.DROPIN.is_file() and nmconf.files()[-1] == nmconf.DROPIN and ("systemctl", "reload", "NetworkManager") in ran, (said, ran))
check("  both retries now forever, from irate-box's file", e["auth_retries"]["value"] == "0" and e["auth_retries"]["mine"] == "0"
      and e["autoconnect_retries"]["file"] == str(nmconf.DROPIN), e)
check("  the drop-in is world-readable (NetworkManager's files are)", oct(nmconf.DROPIN.stat().st_mode & 0o777) == "0o644")
hub_control.nm_defaults({"changes": {"powersave": "3"}})
check("a later change keeps the earlier ones, and wins over Armbian's", nmconf.current_mine() == {"autoconnect_retries": "0", "auth_retries": "0", "powersave": "3"}
      and nmconf.effective()["settings"]["powersave"]["value"] == "3")
hub_control.nm_defaults({"changes": {"powersave": "default"}})
check("'default' takes irate-box's line out: Armbian's value applies again",
      nmconf.effective()["settings"]["powersave"]["value"] == "2" and "powersave" not in nmconf.current_mine())
hub_control.nm_defaults({"changes": {"auth_retries": "default", "autoconnect_retries": "default"}})
check("nothing of irate-box's left: the file goes", not nmconf.DROPIN.exists())

# Per interface: sections in irate-box's drop-in, before the plain ones; the value each interface gets.
try:
    nmconf.validate({"autoconnect_retries": "0"}, "wlan0"); check("per interface: the connect retries refused (box-wide only)", False)
except ValueError as exc:
    check("per interface: the connect retries refused (box-wide only)", "whole box" in str(exc), str(exc))
hub_control.nm_defaults({"changes": {"auth_retries": "0"}})
hub_control.nm_defaults({"changes": {"mac": "random", "scan_mac": "yes"}, "iface": "wlan1"})
text = nmconf.DROPIN.read_text()
check("an interface's sections come first, each matching it by name, the box's kept",
      text.index("[connection-irate-box-wlan1]") < text.index("[connection]") and "match-device=interface-name:wlan1" in text
      and "[device-irate-box-wlan1]" in text and "connection.auth-retries=0" in text, text)
check("  read back per interface", nmconf.current_ifaces() == {"wlan1": {"mac": "random", "scan_mac": "yes"}}, nmconf.current_ifaces())
e1, e0 = nmconf.effective_for("wlan1"), nmconf.effective_for("wlan0", driver="aic8800_fdrv")
check("  wlan1 gets its own MAC and scan settings, from irate-box's section",
      e1["mac"]["value"] == "random" and e1["mac"]["section"] == "connection-irate-box-wlan1"
      and e1["scan_mac"]["value"] == "yes", e1)
check("  wlan0 doesn't: Armbian's scan setting, and the box's retries", e0["scan_mac"]["value"] == "no"
      and e0["mac"]["value"] is None and e0["auth_retries"]["value"] == "0", e0)
check("  power save for wlan1: Armbian's, as nothing of irate-box's sets it there", e1["powersave"]["value"] == "2")
check("  a section for another driver doesn't apply (Armbian's eagle_sdio one)",
      nmconf.effective_for("wlan9", driver="other")["scan_mac"]["section"] != "device-31-mac-addr-change")
check("  that section applies to its driver", nmconf._matches("driver:eagle_sdio,driver:wl", "wlan9", "wl") is True
      and nmconf._matches("except:interface-name:wlan0", "wlan0") is False and nmconf._matches("mac:aa:bb", "wlan0") is None)
hub_control.nm_defaults({"changes": {"mac": "default", "scan_mac": "default"}, "iface": "wlan1"})
check("  'as the box default' takes the interface's lines out, the box's kept",
      nmconf.current_ifaces() == {} and nmconf.current_mine() == {"auth_retries": "0"} and "irate-box-wlan1" not in nmconf.DROPIN.read_text())
hub_control.nm_defaults({"changes": {"auth_retries": "default"}})

# The doctor's retry findings: the value in use, and where it is set.
prof = {"uuid": "u1", "name": "netplan-wlan0-Home", "ssid": "Home", "mode": "infrastructure", "iface": "wlan0",
        "auth_retries": -1, "autoconnect_retries": -1, "autoconnect": True}
check("effective: the profile's own value first", netinv.effective_retries({**prof, "auth_retries": 2}, "auth_retries", None) == (2, "this network's own profile"))
dflt = {"settings": {"auth_retries": {"value": "0", "file": "/etc/NetworkManager/conf.d/zz-90-irate-box-network.conf"}}}
v, where = netinv.effective_retries(prof, "auth_retries", dflt)
check("  then the box default", v == 0 and "box default" in where, (v, where))
check("  then NetworkManager's own", netinv.effective_retries(prof, "auth_retries", None) == (3, "NetworkManager's own default"))


def findings(defaults):
    i = {"radios": [], "stacks": {"networkmanager": {"running": True, "wifi_profiles": [prof], "defaults": defaults}}, "country": "GB"}
    return [h for h in netinv.hazards(i) if h["id"].endswith(":u1")]


h = findings(None)
check("no box default: both retries found, saying where they are set",
      sorted(x["id"].split(":")[0] for x in h) == ["auth-retries", "autoconnect-retries"]
      and all("NetworkManager's own default" in x["detail"] for x in h), h)
both = {"settings": {"auth_retries": {"value": "0", "file": "x"}, "autoconnect_retries": {"value": "0", "file": "x"}}}
check("the box default forever: nothing found", findings(both) == [], findings(both))

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
