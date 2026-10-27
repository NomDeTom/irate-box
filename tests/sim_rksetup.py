# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Setting up a new board, the hub's half (hub/rksetup.py, /admin/rkboard's "setup"), offline: setup hotspots found
by both forms of their name (armbian-web-config's and the first login's banner's) and nothing else, strongest first,
two boards sharing a name counted; the box's time zone, locale and network read; every value checked against what
Armbian's first-boot presets cannot hold (sourced by bash inside double quotes), and the page's check the same as
this one. python3 tests/sim_rksetup.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rksetup-"))
(T / "etc" / "default").mkdir(parents=True)
os.environ.update(HUB_SYSETC_DIR=str(T / "etc"), HUB_MODPROBE_DIR=str(T / "modprobe.d"))
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv, rksetup

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

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
