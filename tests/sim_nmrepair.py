# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager on an image that does not run it (root/nmrepair.py): the netplan renderer, the install
that starts nothing, the handover, the two-minute check that keeps a connected box and puts back one that is
not, the boot-time check, and a failed install that leaves the earlier state. No NetworkManager or netplan
is run. python3 tests/sim_nmrepair.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="nmrepair-"))
(T / "etc").mkdir()
(T / "netplan").mkdir()
(T / "units").mkdir()
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_NETPLAN_DIR=str(T / "netplan"),
    HUB_UNIT_DIR=str(T / "units"),
    HUB_CODE_DIR=str(REPO),
)
sys.path.insert(0, str(REPO))
from irate_box.root import nmhandover, nmrepair

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


class R:
    def __init__(self, out="", rc=0, err=""):
        self.stdout, self.stderr, self.returncode = out, err, rc


WIFI = """network:
  version: 2
  wifis:
    wlan0:
      dhcp4: yes
      access-points:
        "Home":
          password: "secret"
"""
orig = WIFI
state = {
    "installed": False,
    "nm_wifi": False,
    "address": False,
    "route": False,
    "apt_rc": 0,
    "calls": [],
}


def fake(cmd, timeout=0, **kw):
    state["calls"].append(list(cmd))
    name = Path(cmd[0]).name
    if name == "dpkg-query":
        return R("install ok installed" if state["installed"] else "unknown ok not-installed")
    if name == "apt-get":
        if state["apt_rc"] == 0:
            state["installed"] = True
        return R("", state["apt_rc"], "apt failed" if state["apt_rc"] else "")
    if name == "nmcli":
        return R("wlan0:wifi:connected\n" if state["nm_wifi"] else "wlan0:wifi:disconnected\n")
    if name == "ip" and cmd[1:3] == ["-o", "-4"]:
        return R("2: wlan0    inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0\n" if state["address"] else "")
    if name == "ip" and cmd[1:4] == ["-4", "route", "show"]:
        return R("default via 192.168.1.1 dev wlan0\n" if state["route"] else "")
    return R()


def reset():
    nmrepair.NETPLAN.joinpath("30-wifis-dhcp.yaml").write_text(orig)
    nmrepair.RECORD.unlink(missing_ok=True)
    (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).unlink(missing_ok=True)
    state.update(installed=False, nm_wifi=False, address=False, route=False, apt_rc=0, calls=[])


handed = []
nmhandover.handover = lambda name: handed.append(name) or {"id": "nm-handover.done", "en": f"handed {name}"}
nmhandover.undo = lambda name: f"undid {name}"

check(
    "the netplan renderer: put under network, and an earlier one replaced",
    "network:\n  renderer: NetworkManager\n  version: 2\n" in nmrepair._renderer(WIFI)
    and "  renderer: NetworkManager\n"
    in nmrepair._renderer(WIFI.replace("  version: 2\n", "  renderer: networkd\n  version: 2\n")),
)
check("  a file with no network block: refused", nmrepair._renderer("foo: bar\n") is None)
check("  the WiFi device read from the file", nmrepair._wifi_device(WIFI) == "wlan0")

reset()
msg = nmrepair.install(run=fake)
rec = json.loads(nmrepair.RECORD.read_text())
check(
    "install: NetworkManager installed and masked at run time, the WiFi given to it, the profile handed over, both checks armed",
    rec["phase"] == "checking"
    and rec["device"] == "wlan0"
    and rec["handover"] == "30-wifis-dhcp.yaml"
    and rec["installed_before"] is False
    and "renderer: NetworkManager" in (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text()
    and handed == ["30-wifis-dhcp.yaml"]
    and ["systemctl", "mask", "--runtime", "NetworkManager"] in state["calls"]
    and ["systemctl", "unmask", "--runtime", "NetworkManager"] in state["calls"]
    and ["systemctl", "enable", nmrepair.CHECK_SERVICE] in state["calls"]
    and (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists()
    and any(c[:2] == ["systemd-run", "--quiet"] for c in state["calls"])
    and "NetworkManager is installed" in str(msg),
    (rec, state["calls"]),
)
state.update(nm_wifi=True, address=True, route=True)
said = nmrepair.check(run=fake)
rec = json.loads(nmrepair.RECORD.read_text())
check(
    "  a WiFi connection with an address and a default route two minutes later: the change stays, the netplan copy dropped",
    rec["phase"] == "kept"
    and "files" not in rec
    and not (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists()
    and "stays" in said,
    (rec, said),
)

check(
    "  the boot check asks for the two-minute grace, with enough start time for it",
    "nmrepair check boot" in nmrepair._boot_unit_text() and "TimeoutStartSec=180" in nmrepair._boot_unit_text(),
)
reset()
nmrepair.install(run=fake)
state.update(address=True, route=True)


armed = {"during": False}


def nap(_seconds):
    armed["during"] = (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists()
    state["nm_wifi"] = True


said = nmrepair.check(run=fake, grace=10, sleep=nap)
check(
    "  at boot: a WiFi that joins during the grace keeps the change, the unit armed until the decision",
    state["nm_wifi"]
    and armed["during"]
    and not (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists()
    and json.loads(nmrepair.RECORD.read_text())["phase"] == "kept"
    and "stays" in said,
    (said, nmrepair.RECORD.read_text()),
)

reset()
state.update(nm_wifi=True, address=True, route=False)
nmrepair.install(run=fake)
state["route"] = False
said = nmrepair.check(run=fake)
check(
    "  a connected WiFi without a default route: put back",
    (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text() == orig
    and not nmrepair.RECORD.exists()
    and "restored" in said,
    (said, state["calls"]),
)

reset()
nmrepair.install(run=fake)
said = nmrepair.check(run=fake)
check(
    "  not on a WiFi connection: the netplan file as it was, NetworkManager stopped, nothing waiting",
    (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text() == orig
    and not nmrepair.RECORD.exists()
    and ["systemctl", "disable", "--now", "NetworkManager"] in state["calls"]
    and not (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists()
    and "restored" in said,
    (said, state["calls"]),
)

reset()
state["apt_rc"] = 100
try:
    nmrepair.install(run=fake)
    ok = False
except nmrepair.Refused as exc:
    ok = "could not update its package lists" in str(exc)
check(
    "a failed install: refused, NetworkManager unmasked again, netplan as it was, nothing recorded or armed",
    ok
    and ["systemctl", "unmask", "--runtime", "NetworkManager"] in state["calls"]
    and (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text() == orig
    and not nmrepair.RECORD.exists()
    and not (nmrepair.UNIT_DIR / nmrepair.CHECK_SERVICE).exists(),
    (ok, nmrepair.RECORD.exists()),
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
