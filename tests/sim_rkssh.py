# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Stock Armbian's default password over a cable (hub/boardfind.py, root/rkssh.py), offline: the boards found from
recorded leases and neighbours, a board maker's or a board-like name first; an address the box did not find
refused; a link the box does not control refused; then, over a stand-in SSH board, the default password once, the
same first-boot questions as the serial route answered, and the name, keys, WiFi and address set at its shell.
python3 tests/sim_rkssh.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkssh-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ROOT_STATE_DIR=str(T / "root-state"))
sys.path.insert(0, str(REPO))
from irate_box.hub import boardfind
from irate_box.msg import Refused
from irate_box.root import rkssh

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Refused as exc:
        return exc.message["id"]
    return None


leases = {
    "192.168.4.12": {"mac": "b8:41:d9:4c:01:ad", "host": "bananapi"},
    "192.168.1.30": {"mac": "aa:bb:cc:dd:ee:ff", "host": "laptop"},
    "192.168.4.15": {"mac": "54:01:4a:4c:01:ad", "host": "lyra-zero-w"},
}
neigh = {"192.168.1.60": "b8:27:eb:11:22:33", "192.168.1.61": "aa:bb:cc:dd:ee:01"}
found = boardfind.candidates(leases, neigh)
check(
    "the boards found: a maker's MAC or a board-like name first, a plain lease after",
    [b["address"] for b in found] == ["192.168.1.60", "192.168.4.12", "192.168.4.15", "192.168.1.30", "192.168.1.61"]
    and next(b for b in found if b["address"] == "192.168.4.12")["vendor"] == "Allwinner"
    and next(b for b in found if b["address"] == "192.168.4.15")["vendor"] == "Rockchip"
    and next(b for b in found if b["address"] == "192.168.1.60")["vendor"] == "Raspberry Pi",
    found,
)
check("  a lease with no maker and no board-like name is still listed, not a board", found[-1]["board"] is False)

boardfind.candidates = lambda: [
    {"address": "192.168.1.60", "mac": "b8:27:eb:11:22:33", "host": "bpi", "vendor": "Raspberry Pi", "board": True}
]
check("an address root did not find refused", refused(rkssh.find, "192.168.1.99") == "rkssh.not-found")
check("  the board root did find", rkssh.find("192.168.1.60")["address"] == "192.168.1.60")


def route(dev):
    def run(*cmd, timeout=30):
        return subprocess.CompletedProcess(cmd, 0, f"192.168.1.60 via 192.168.1.1 dev {dev} src 192.168.1.2", "")

    return run


rkssh.run = route("eth0")
check("a cable is a link the box controls", rkssh.controlled("192.168.1.60") is True)
rkssh.run = route("wlan0")
check("  the WiFi the box joins is not", rkssh.controlled("192.168.1.60") is False)
rkssh.run = route("ap0")
check("  the box's own hotspot is", rkssh.controlled("192.168.1.60") is True)

choices = {
    "user": "salty-parrot",
    "real_name": "Salty Parrot",
    "root_password": "Root16charsAbcd1",
    "user_password": "User16charsAbcd2",
    "hostname": "lyra-3",
    "timezone": "Europe/London",
    "locale": "en_GB.UTF-8",
    "country": "GB",
    "ssid": "GOODNET",
    "wifi_password": "eight888",
    "keys": ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOw7 tom@laptop"],
    "lock_root": True,
    "listen": 5,
}
record = T / "record.json"
os.environ.update(FAKE_RECORD=str(record), FAKE_START="firstlogin", FAKE_LOCALES="1", FAKE_NM="1")
rkssh.controlled = lambda address: True
rkssh.SSH = [sys.executable, str(REPO / "tests/fixtures/fake_ssh_board.py")]
got = rkssh.setup("192.168.1.60", choices)
answers = json.loads(record.read_text())
check(
    "the default password once, then the first-boot questions answered as the serial route answers them",
    answers["answers"]["root1"] == ["Root16charsAbcd1"]
    and answers["answers"]["root2"] == ["Root16charsAbcd1"]
    and answers["answers"]["user"] == ["salty-parrot"]
    and answers["answers"]["user1"] == ["User16charsAbcd2"]
    and answers["answers"]["wifi"] == ["n"],
    answers["answers"],
)
cmds = "\n".join(answers["commands"])
check(
    "  then the name, time zone, keys, setup page, WiFi and root's lock set at its shell",
    'hostnamectl set-hostname "lyra-3"' in cmds
    and 'timedatectl set-timezone "Europe/London"' in cmds
    and 'nmcli dev wifi connect "GOODNET" password "eight888"' in cmds
    and "systemctl disable --now armbian-web-config" in cmds
    and "passwd -l root" in cmds,
    cmds,
)
check("  the address read back", got["address"] == "192.168.1.77" and got["root_password"] == "Root16charsAbcd1", got)

# A link the box does not control: refused before an SSH session is made.
rkssh.controlled = lambda address: False
check(
    "the route refused on a network the box does not control",
    refused(rkssh.setup, "192.168.1.60", choices) == "rkssh.not-controlled",
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
