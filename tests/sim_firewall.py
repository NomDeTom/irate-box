# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The network floor (root/firewall.py; stance review §4 item 3): the ruleset's text (the hotspot's
interface alone, the hub's doors, the owner's services, drop last, no forwarding), checked by nft
where nft is installed; the hotspot's interface found from the record or the dnsmasq config; the
Security page's finding and its switches against a stand-in nft and systemctl; the unit's text.
python3 tests/sim_firewall.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="firewall-"))
ETC, BIN = T / "etc", T / "bin"
ETC.mkdir(); BIN.mkdir()
# nft and systemctl stand-ins: nft -c accepts anything with "table inet irate_box" in it; "list table"
# answers by a marker file; systemctl records what it was asked.
(BIN / "nft").write_text(f"""#!/usr/bin/env python3
import os, sys
a = sys.argv[1:]
if a[:2] == ["list", "table"]: sys.exit(0 if os.path.exists("{T}/loaded") else 1)
if a[:2] == ["delete", "table"]: os.path.exists("{T}/loaded") and os.unlink("{T}/loaded"); sys.exit(0)
if a[:2] == ["-c", "-f"]: sys.exit(0 if "table inet irate_box" in open(a[2]).read() else 1)
if a[:1] == ["-f"]: open("{T}/loaded", "w").write(""); sys.exit(0)
sys.exit(2)
""")
(BIN / "systemctl").write_text(f"""#!/usr/bin/env python3
import sys
open("{T}/systemctl-log", "a").write(" ".join(sys.argv[1:]) + "\\n")
if "--now" in sys.argv and "enable" in sys.argv or "restart" in sys.argv: open("{T}/loaded", "w").write("")
if "disable" in sys.argv:
    import os; os.path.exists("{T}/loaded") and os.unlink("{T}/loaded")
""")
for b in (BIN / "nft", BIN / "systemctl"):
    b.chmod(0o755)
os.environ.update(HUB_ETC_DIR=str(ETC), HUB_AP_DNSMASQ=str(T / "ap-dnsmasq.conf"), PATH=f"{BIN}:{os.environ['PATH']}",
                  HUB_PROC_SYS=str(T / "proc"), HUB_GROUP_FILE=str(T / "group"), HUB_PASSWD_FILE=str(T / "passwd"),
                  HUB_SUDOERS=str(T / "sudoers"), HUB_SUDOERS_DIR=str(T / "sudoers.d"), HUB_APT_DIR=str(T / "apt"),
                  HUB_MOSQUITTO_DIR=str(T / "mosquitto"), HUB_UNIT_DIR=str(T / "units"))
sys.path.insert(0, str(REPO))
from irate_box.root import firewall as fw, security  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# The text. The floor as the Security page keeps it: a level (hub, or hub and apps) and the services.
F = lambda services=(), level="apps": {"level": level, "services": list(services)}  # noqa: E731
text = fw.ruleset("ap0", F(["mqtt", "sync"]))
lines = [l.strip() for l in text.splitlines()]
check("one table, inet irate_box, made then deleted first so loading twice is loading once on any kernel", lines[3:5] == ["table inet irate_box", "delete table inet irate_box"] and "table inet irate_box {" in lines)
check("the hotspot's interface alone is sent to the drop chain; the input policy stays accept",
      'iifname "ap0" jump hotspot' in lines and "type filter hook input priority filter; policy accept;" in lines)
hot = text.split("chain hotspot {")[1].split("\n\t}\n")[0]
check("the hub's doors, DNS and DHCP, MQTT and Syncthing when chosen; drop last",
      "tcp dport { 53, 80, 443, 1883, 8090, 8091, 8092, 8093, 8490, 8491, 8492, 8493, 22000 } accept" in hot
      and "udp dport { 53, 67, 21027, 22000 } accept" in hot and hot.strip().endswith("drop") and "established,related accept" in hot, hot)
check("  SSH only by choice", "22," not in fw.ruleset("ap0", F()) and "{ 22, 53" in fw.ruleset("ap0", F(["ssh"])))
check("the floor at hub only: the front, DNS and DHCP; the apps' ports closed", "tcp dport { 53, 80, 443 } accept" in fw.ruleset("ap0", F(level="hub"))
      and "8090" not in fw.ruleset("ap0", F(level="hub")))
check("nothing forwarded to or from the hotspot", 'iifname "ap0" drop' in lines and 'oifname "ap0" drop' in lines)
try:
    fw.ruleset("ap0; flush ruleset", F()); check("an interface name that is not one: refused", False)
except ValueError:
    check("an interface name that is not one: refused", True)
real_nft = shutil.which("nft", path="/usr/sbin:/sbin:/usr/bin")
if real_nft and os.geteuid() == 0:
    (T / "r.nft").write_text(fw.ruleset("wlan1", F(["mqtt", "sync", "ssh"])))
    r = subprocess.run([real_nft, "-c", "-f", str(T / "r.nft")], capture_output=True, text=True)
    check("the real nft accepts the ruleset (nft -c)", r.returncode == 0, r.stderr)
else:
    print("skip the real nft -c: nftables is not installed here, or not root (nft -c opens netlink)")

# The interface: the hotspot's record when up, else dnsmasq's config, else ap0.
check("no record, no config: ap0", fw.hotspot_iface() == "ap0")
(T / "ap-dnsmasq.conf").write_text("interface=wlan1\nbind-dynamic\n")
check("  from the dnsmasq config", fw.hotspot_iface() == "wlan1")
(ETC / "ap.json").write_text(json.dumps({"up": True, "plan": {"kind": "own-radio", "iface": "wlx001122", "channel": 6}}))
check("  from the hotspot's record when it is up", fw.hotspot_iface() == "wlx001122")
(ETC / "ap.json").write_text(json.dumps({"up": False, "plan": {"kind": "own-radio", "iface": "wlx001122"}}))
check("  not when it is down", fw.hotspot_iface() == "wlan1")
(ETC / "ap.json").unlink()

# The Security page: off, on, SSH opened and closed, off again; undo_all.
def finding():
    return security.firewall_findings(security.load_record())[0]
f = finding()
check("no floor: a warning saying what it would allow, with a switch for each level", f["status"] == "warn"
      and [a["choice"] for a in f["actions"]] == ["firewall-apps", "firewall-hub"]
      and "TCP 53, 80, 443, 8090" in f["detail"] and "wlan1" in f["detail"], f)
print(security.fix("firewall-on", None))
f = finding()
check("on: the file written for wlan1, checked, the unit enabled and loaded; ok, SSH closed, with the SSH switch and Undo",
      (ETC / "firewall.nft").read_text().count('iifname "wlan1"') == 2 and "enable irate-box-firewall.service" in (T / "systemctl-log").read_text()
      and (T / "loaded").exists() and f["status"] == "ok" and "SSH from the hotspot closed" in f["detail"]
      and [a["choice"] for a in f["actions"]] == ["firewall-hub", "firewall-ssh-on", "firewall-off"], f)
check("  no MQTT or Syncthing on this box: not opened", "1883" not in (ETC / "firewall.nft").read_text())
(T / "mosquitto" / "conf.d").mkdir(parents=True); (T / "mosquitto" / "conf.d" / "irate-box.conf").write_text("listener 1883\n")
security.fix("firewall-on", None)
check("  MQTT installed later: opened at the next switch, Syncthing still not", "1883" in (ETC / "firewall.nft").read_text() and "22000" not in (ETC / "firewall.nft").read_text())
print(security.fix("firewall-ssh-on", None))
f = finding()
check("SSH opened: in the ruleset, said, the switch now closes it", "{ 22, 53" in (ETC / "firewall.nft").read_text() and "open, by your choice" in f["detail"]
      and "firewall-ssh-off" in [a["choice"] for a in f["actions"]])
print(security.fix("firewall-ssh-off", None))
check("  closed again", "{ 22, 53" not in (ETC / "firewall.nft").read_text())
print(security.fix("firewall-hub", None))
f = finding()
check("hub only: the apps' ports closed, said, the switch back to hub and apps", "8090" not in (ETC / "firewall.nft").read_text()
      and security.load_record()["firewall"]["level"] == "hub" and f["detail"].startswith("Hub only") and f["actions"][0]["choice"] == "firewall-apps", f)
print(security.fix("firewall-apps", None))
check("  and back", "8090" in (ETC / "firewall.nft").read_text() and security.load_record()["firewall"]["level"] == "apps")
(T / "loaded").unlink()
check("on but not loaded: a problem", finding()["status"] == "problem")
print(security.fix("firewall-on", None))
print(security.fix("firewall-off", None))
check("off: the file gone, the table gone, the unit disabled, the warning back", not (ETC / "firewall.nft").exists() and not (T / "loaded").exists()
      and "disable irate-box-firewall.service" in (T / "systemctl-log").read_text()
      and finding()["status"] == "warn" and "firewall" not in security.load_record())
security.fix("firewall-on", None)
done = security.undo_all()
check("undo_all takes the floor away", not (ETC / "firewall.nft").exists() and "firewall" not in security.load_record(), done)
bad = Path(BIN / "nft"); bad.write_text("#!/bin/sh\nexit 1\n")
try:
    security.fix("firewall-on", None); check("nft refusing the file: nothing changed, said", False)
except ValueError as exc:
    check("nft refusing the file: nothing changed, said", "nothing changed" in str(exc) and not (ETC / "firewall.nft").exists(), str(exc))

# The unit.
u = fw.unit_text("/opt/irate-box")
check("the unit loads the file only when it is there, deletes the table on stop, runs before the network",
      f"if [ -f {fw.RULES} ]; then nft -f {fw.RULES}; fi" in u and "nft delete table inet irate_box" in u
      and "Before=network-pre.target" in u and "RemainAfterExit=yes" in u)
inst = (REPO / "install.sh").read_text()
check("install.sh writes it from the module; uninstall.sh takes it away", "firewall.unit_text" in inst and "irate-box-firewall.service" in (REPO / "uninstall.sh").read_text())
shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
