# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Setting up a new board, the hub's half (hub/rksetup.py, /admin/rkboard's "setup"), offline: setup hotspots found
by both forms of their name (armbian-web-config's and the first login's banner's) and nothing else, strongest first,
two boards sharing a name counted; the box's time zone, locale and network read; every value checked against what
Armbian's first-boot presets cannot hold (sourced by bash inside double quotes), and the page's check the same as
this one. python3 tests/sim_rksetup.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rksetup-"))
(T / "etc" / "default").mkdir(parents=True)
os.environ.update(
    HUB_SYSETC_DIR=str(T / "etc"),
    HUB_MODPROBE_DIR=str(T / "modprobe.d"),
    HUB_SERIAL_BY_ID=str(T / "by-id"),
    HUB_STATE_DIR=str(T / "state"),
)
(T / "state" / "control").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv, rksetup, rkusb

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


nets = [
    {"ssid": "SKYTB1H1", "signal": 90, "aps": 4},
    {"ssid": "armbiansetup-luckfox-lyra-zero-w", "signal": 40, "aps": 2},
    {"ssid": "lyra-ultra-armbiansetup", "signal": 70, "aps": 1},
    {"ssid": "armbiansetup", "signal": 30, "aps": 1},
    {"ssid": "not-armbiansetup-really-x", "signal": 99, "aps": 1},
]
got = rksetup.hotspots(nets)
check(
    "setup hotspots: both forms of the name, the plain one, strongest first; other networks left out",
    [(x["ssid"], x["host"]) for x in got]
    == [
        ("lyra-ultra-armbiansetup", "lyra-ultra"),
        ("armbiansetup-luckfox-lyra-zero-w", "luckfox-lyra-zero-w"),
        ("armbiansetup", ""),
    ],
    got,
)
check(
    "  two boards raising the same name counted",
    next(x for x in got if x["host"] == "luckfox-lyra-zero-w")["boards"] == 2,
)
check(
    "the names in sight, lower-cased: the boards seen and the setup hotspots, once each, blanks left out",
    rksetup.taken_names([{"host": "Lyra-4"}, {"host": ""}, {"host": "lyra-4"}], [{"host": "LYRA-ULTRA"}, {"host": ""}])
    == ["lyra-4", "lyra-ultra"],
    rksetup.taken_names([{"host": "Lyra-4"}, {"host": ""}], [{"host": "LYRA-ULTRA"}]),
)
asked = []
real_run = netinv.run
netinv.run = lambda *cmd, **kw: (asked.append(cmd), (0, ""))[1]
rksetup._scan["at"] = 0
rksetup.hotspots()
rksetup.hotspots()
netinv.run = real_run
check(
    "  read from NetworkManager's last scan, never starting one, and reused for a while",
    len(asked) == 1 and asked[0][-2:] == ("--rescan", "no"),
    asked,
)

(T / "etc" / "timezone").write_text("Europe/London\n")
(T / "etc" / "default" / "locale").write_text('LANG="en_GB.UTF-8"\n')
check(
    "the box's time zone and locale read", rksetup.timezone() == "Europe/London" and rksetup.locale() == "en_GB.UTF-8"
)
(T / "etc" / "timezone").unlink()
os.symlink("/usr/share/zoneinfo/Europe/Paris", T / "etc" / "localtime")
check("  the time zone from where /etc/localtime points, without /etc/timezone", rksetup.timezone() == "Europe/Paris")
asked = []
netinv.run = lambda *cmd, **kw: (asked.append(cmd), (0, "no:OtherNet\nyes:SKYTB1H1\n"))[1]
check(
    "the network the box is on, from nmcli's last scan",
    rksetup.uplink_ssid() == "SKYTB1H1" and asked[-1][-2:] == ("--rescan", "no"),
    asked,
)
asked.clear()
rksetup._defaults["at"] = 0
first, second = rksetup.defaults(), rksetup.defaults()
check(
    "  the page's defaults read once, then reused for a while",
    first == second and first["ssid"] == "SKYTB1H1" and len(asked) == 1,
    (first, asked),
)
netinv.run = lambda *cmd, **kw: (10, "")
check("  none when nmcli fails", rksetup.uplink_ssid() == "")
netinv.run = real_run

P = rksetup.problem
check(
    "values Armbian's presets cannot hold: a quote, a dollar sign, a backtick, a backslash, a line break",
    all(P(f"pass{ch}word", "password") for ch in ['"', "$", "`", "\\", "\n", "\x7f"]),
)
check(
    "  a fair password, a name, a user and a WiFi password pass",
    not any(
        [P("Fair-pass-123", "password"), P("lyra-3", "hostname"), P("armbian", "user"), P("eight888", "wifi-password")]
    ),
)
check(
    "  a short password, a bad name, a bad user, a short WiFi password refused",
    all(
        [
            P("short", "password"),
            P("-lyra", "hostname"),
            P("Lyra", "hostname"),
            P("9user", "user"),
            P("seven77", "wifi-password"),
        ]
    ),
)
check("  an empty WiFi password (an open network) passes", P("", "wifi-password") is None)

# The page's check (admin-rockchip.js) refuses the same characters with the same words.
js = (REPO / "web" / "admin-rockchip.js").read_text()
unsafe = js.split("const UNSAFE = ", 1)[1].split("};", 1)[0]
as_js = {'"': """'"'""", "$": "$", "`": "'`'", "\\": "'\\\\'"}
check(
    "the page refuses the same characters, in the same words",
    unsafe.count(":") == len(rksetup.UNSAFE)
    and all(f"{as_js[ch]}: '{name}'" in unsafe for ch, name in rksetup.UNSAFE.items()),
    unsafe,
)
for words in [
    "which Armbian's setup cannot take.",
    "A WiFi password is 8 to 63 characters.",
    "A password here is at least 8 characters.",
]:
    check(f"  the page says: {words}", words in js)

# What the page can offer now: an adapter and a card in a reader, and the routes they make possible.
(T / "by-id").mkdir()
(T / "by-id" / "usb-1a86_USB2.0-Serial-if00-port0").write_text("")
(T / "by-id" / "not a console").write_text("")
check(
    "serial adapters: by-id names only", rksetup.consoles() == ["usb-1a86_USB2.0-Serial-if00-port0"], rksetup.consoles()
)
rkusb.cards = lambda: [{"name": "sda", "size": 1, "serial": "S1", "mounted": False}]
check(
    "routes: the hotspot always, SSH when a board is seen, serial with an adapter, card with a card",
    rksetup.routes(["usb-1a86_USB2.0-Serial-if00-port0"], [{"name": "sda"}], [{"address": "192.168.4.2"}])
    == ["hotspot", "ssh", "serial", "card"]
    and rksetup.routes([], [], []) == ["hotspot"],
    rksetup.routes([], [], []),
)
# The box's own pins: the root helper's copy of the status, then the serial route without an adapter.
real_model, real_device = rksetup.board_model, rksetup.OWN_DEVICE
rksetup.board_model, rksetup.OWN_DEVICE = (lambda: "pi"), {"pi": "/dev/null"}
own_file = T / "state" / "control" / "ownserial.json"
own_file.write_text(json.dumps({"on": True, "board": "pi"}))
own = rksetup.own_serial()
check(
    "own pins: the root helper's status read without root's module, the device there, so not pending",
    own["supported"] and own["on"] and not own["pending"] and own["device"] == "/dev/null" and own["warning"],
    own,
)
check(
    "  the serial route offered with its own pins and no adapter",
    rksetup.routes([], [], [], own) == ["hotspot", "serial"],
    rksetup.routes([], [], [], own),
)
own_file.write_text(json.dumps({"on": False, "board": "pi"}))
check("  off: no serial route without an adapter", rksetup.routes([], [], [], rksetup.own_serial()) == ["hotspot"])
rksetup.board_model, rksetup.OWN_DEVICE = real_model, real_device
snap = rksetup.snapshot()
check(
    "the page's snapshot: the adapters, the cards, the boards seen, the routes and ready",
    snap["consoles"] == ["usb-1a86_USB2.0-Serial-if00-port0"]
    and snap["cards"]
    and snap["ready"] is True
    and "serial" in snap["routes"]
    and "card" in snap["routes"]
    and isinstance(snap["taken"], list),
    snap,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
