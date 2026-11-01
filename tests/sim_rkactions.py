# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The root helper's new-board actions (hub_control.py), offline: the card, the setup hotspot and the default
password routes each check their request again, make the passwords when the page asks for generated ones, keep them
once for the page, and say what happened. The card route writes the presets with this box's key. The SSH route
finishes only when the choices ask (its own test is tests/sim_rkssh.py). python3 tests/sim_rkactions.py"""

import json
import os
import pwd
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkactions-"))
(T / "control").mkdir()
(T / "by-id").mkdir()
os.environ.update(
    HUB_STATE_DIR=str(T),
    HUB_ROOT_STATE_DIR=str(T / "root-state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_SERIAL_BY_ID=str(T / "by-id"),
    HUB_USER=pwd.getpwuid(os.getuid()).pw_name,
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import hub_control

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


page = {
    "route": "card",
    "network": "other",
    "otherSsid": "GOODNET",
    "otherPass": "eight888",
    "rootPass": "generated",
    "userName": "salty-parrot",
    "userPass": "generated",
    "lockRoot": False,
    "key": "",
    "hostname": "bpi-1",
    "place": "box",
    "locale": "en_GB.UTF-8",
    "timezone": "Europe/London",
    "country": "GB",
    "address": "dhcp",
    "after": "skip",
}
hub_control._box_key = lambda: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOw7 box@irate"
written = {}
from irate_box.root import rkpresets

rkpresets.write_card = lambda card, size, serial, choices, key=None: (
    written.update(card=card, size=size, serial=serial, choices=choices, key=key)
    or "settings for rockchip 26.05.0-trunk"
)
got = hub_control.ACTIONS["rk-card-setup"](
    {"id": "rk10", "card": "sda", "size": 15634268160, "serial": "S1", "choices": page}
)
secret = json.loads((T / "control" / "rk-secrets" / "rk10.json").read_text())
check(
    "the card route: its values checked, generated passwords made, the presets written with this box's key",
    got["id"] == "rkpresets.done"
    and written["card"] == "sda"
    and written["choices"]["ssid"] == "GOODNET"
    and written["key"].startswith("ssh-ed25519 ")
    and len(secret["root"]) == 16
    and secret["user"] == "salty-parrot"
    and len(secret["user_password"]) == 16,
    (got, written, secret),
)

from irate_box.root import rkspot

rkspot.setup = lambda hotspot, choices: {"ssid": hotspot, "host": "bpi", "answer": "ok"}
hub_control._wait_for_board = lambda before, hostname="", seconds=60: ""
got = hub_control.ACTIONS["rk-hotspot-setup"]({"id": "rk11", "hotspot": "armbiansetup-bpi", "choices": page})
secret = json.loads((T / "control" / "rk-secrets" / "rk11.json").read_text())
check(
    "the hotspot route: the choices sent, the passwords kept once, and the board not seen yet said",
    got["id"] == "rkspot.done" and "not seen on the network yet" in got["args"]["said"] and len(secret["root"]) == 16,
    got,
)

from irate_box.root import rkssh

rkssh.setup = lambda address, choices: {
    "address": address,
    "said": ["name bpi-1"],
    "root_password": choices["root_password"],
    "user_password": choices["user_password"],
}
got = hub_control.ACTIONS["rk-ssh-setup"]({"id": "rk12", "address": "192.168.1.60", "choices": page})
check(
    "the default-password route: the board's address and the choices sent, and done said",
    got["id"] == "rkssh.done" and got["args"]["address"] == "192.168.1.60",
    got,
)
try:
    hub_control.ACTIONS["rk-ssh-setup"](
        {"id": "rk13", "address": "192.168.1.60", "choices": dict(page, userName="bad$name")}
    )
    check("  a bad user refused", False)
except Refused as exc:
    check("  a bad user refused", exc.message["id"] == "rkserial.value")

# The finish and the install accept only an address root found itself.
from irate_box.hub import boardfind

boardfind.candidates = lambda **kw: []
for action, body in (
    ("rk-finish", {"address": "192.168.1.99", "choices": {"hostname": "bpi-1", "userName": "armbian"}}),
    ("rk-install", {"address": "192.168.1.99"}),
):
    try:
        hub_control.ACTIONS[action]({"id": "rk14", **body})
        check(f"  {action}: an address root did not find refused", False)
    except Refused as exc:
        check(f"  {action}: an address root did not find refused", exc.message["id"] == "rkfinish.not-found")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
