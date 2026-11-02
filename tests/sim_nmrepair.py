# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager on an image that does not run it (root/nmrepair.py): the netplan renderer, the install that
starts nothing, the handover, the two-minute check that keeps a connected box and puts back one that is not,
and a failed install that leaves the earlier state. No NetworkManager or netplan is run.
python3 tests/sim_nmrepair.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="nmrepair-"))
(T / "etc").mkdir()
(T / "netplan").mkdir()
(T / "policy-rc.d").write_text("#!/bin/sh\nexit 0\n")
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_NETPLAN_DIR=str(T / "netplan"),
    HUB_POLICY_RC=str(T / "policy-rc.d"),
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
state = {"installed": False, "nm_wifi": False, "apt_rc": 0, "calls": []}


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
    return R()


def reset():
    nmrepair.NETPLAN.joinpath("30-wifis-dhcp.yaml").write_text(orig)
    nmrepair.RECORD.unlink(missing_ok=True)
    state.update(installed=False, nm_wifi=False, apt_rc=0, calls=[])
    nmrepair.POLICY_RC.write_text("#!/bin/sh\nexit 0\n")


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

reset()
msg = nmrepair.install(run=fake)
rec = json.loads(nmrepair.RECORD.read_text())
check(
    "install: NetworkManager installed, the WiFi given to it in netplan, the profile handed over, the check scheduled",
    rec["phase"] == "checking"
    and rec["handover"] == "30-wifis-dhcp.yaml"
    and rec["installed_before"] is False
    and "renderer: NetworkManager" in (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text()
    and handed == ["30-wifis-dhcp.yaml"]
    and any(c[:2] == ["systemd-run", "--quiet"] for c in state["calls"])
    and nmrepair.POLICY_RC.read_text() == "#!/bin/sh\nexit 0\n"
    and "NetworkManager is installed" in str(msg),
    (rec, state["calls"]),
)
state["nm_wifi"] = True
said = nmrepair.check(run=fake)
rec = json.loads(nmrepair.RECORD.read_text())
check(
    "  a WiFi connection up two minutes later: the change stays",
    rec["phase"] == "kept" and "stays" in said,
    (rec, said),
)

reset()
nmrepair.install(run=fake)
state["nm_wifi"] = False
said = nmrepair.check(run=fake)
check(
    "  not on a WiFi connection: the netplan file as it was, NetworkManager stopped, nothing waiting",
    (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text() == orig
    and not nmrepair.RECORD.exists()
    and ["systemctl", "disable", "--now", "NetworkManager"] in state["calls"]
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
    "a failed install: refused, the policy file and netplan as they were, nothing recorded",
    ok
    and nmrepair.POLICY_RC.read_text() == "#!/bin/sh\nexit 0\n"
    and (nmrepair.NETPLAN / "30-wifis-dhcp.yaml").read_text() == orig
    and not nmrepair.RECORD.exists(),
    (ok, nmrepair.RECORD.exists()),
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
