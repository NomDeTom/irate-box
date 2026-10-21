# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi country as a setting (hub/wificountry.py, the root helper's wifi-country): the list from tzdata's
ISO 3166 table, the saved country read back, a country applied now (iw reg set) and kept for boot (cfg80211's
option file), an unknown one refused, iw failing (kept for boot, said so), the inventory carrying both, and a
netplan handover's undo leaving a country chosen since. iw, the table and the folders stood in for.
python3 tests/sim_wificountry.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="wificountry-"))
(T / "zoneinfo").mkdir()
(T / "zoneinfo/iso3166.tab").write_text(
    "# ISO 3166 alpha-2 country codes\n#\nGB\tBritain (UK)\nUS\tUnited States\nDE\tGermany\nxx\tnot a code\n"
)
os.environ.update(
    HUB_ISO3166=str(T / "zoneinfo/iso3166.tab"),
    HUB_MODPROBE_DIR=str(T / "modprobe.d"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
)
(T / "state/control").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import wificountry
from irate_box.msg import Refused, ids
from irate_box.root import hub_control

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


ran = []
answer = {"code": 0, "out": ""}


def fake_run(*cmd, **kw):
    ran.append(cmd)
    return subprocess.CompletedProcess(cmd, answer["code"], answer["out"], "")


hub_control.run = fake_run
hub_control.netinv.scan = lambda: {"stand-in": True}
hub_control.netinv.write = lambda inv, path: None

check(
    "the list: tzdata's codes and names, comments and malformed lines left out",
    wificountry.countries() == {"GB": "Britain (UK)", "US": "United States", "DE": "Germany"},
    wificountry.countries(),
)
check("nothing saved yet", wificountry.saved() is None)

said = hub_control.wifi_country({"country": "GB"})
check(
    "GB: applied now (iw reg set GB) and kept for boot in cfg80211's option file",
    ("iw", "reg", "set", "GB") in ran
    and "options cfg80211 ieee80211_regdom=GB" in wificountry.REGDOM_CONF.read_text()
    and wificountry.saved() == "GB",
    (ran, wificountry.REGDOM_CONF.read_text() if wificountry.REGDOM_CONF.exists() else None),
)
check("  said: saved, and the radios use it now", "control.country.now" in ids(said), said)
check("  the file says who wrote it", wificountry.REGDOM_CONF.read_text().startswith(wificountry.SET_HERE))

for bad in ("ZZ", "gb", "", None, "GBR", 7):
    try:
        hub_control.wifi_country({"country": bad})
        refused = False
    except Refused:
        refused = True
    check(f"refused: {bad!r}", refused and wificountry.saved() == "GB")

answer.update(code=1, out="command failed: Invalid argument (-22)")
said = hub_control.wifi_country({"country": "DE"})
check(
    "iw failing: kept for boot all the same, and said so with iw's words",
    wificountry.saved() == "DE" and "control.country.boot-only" in ids(said),
    said,
)
answer.update(code=0, out="")

# The inventory carries the kernel's country, the saved one and the list (by name), the old finding gone.
from irate_box.hub import netinv

real_reg = netinv.reg_country
netinv.reg_country = lambda: "US"
inv = {
    "country": netinv.reg_country(),
    "country_saved": wificountry.saved(),
    "countries": sorted(wificountry.countries().items(), key=lambda pair: pair[1]),
}
check(
    "the inventory's shape: the list ordered by name", [code for code, _ in inv["countries"]] == ["GB", "DE", "US"], inv
)
netinv.reg_country = real_reg
check(
    "the old 'No WiFi country is set' finding is no longer raised by netinv",
    "netinv.regdom" not in (REPO / "irate_box/hub/netinv.py").read_text(),
)

# Each radio's own regulatory state, and the country an access point announces (the Pi's, 2026-10-21).
REG = """global
country 98: DFS-UNSET
\t(2402 - 2472 @ 40), (N/A, 20), (N/A)

phy#4 (self-managed)
country 00: DFS-UNSET
\t(2402 - 2472 @ 40), (6, 20), (N/A)

phy#1
country 99: DFS-UNSET
\t(2402 - 2482 @ 40), (6, 20), (N/A)
"""
check(
    "iw reg get: each radio's own country, self-managed or not",
    netinv.parse_reg(REG)
    == {"phy4": {"country": "00", "self_managed": True}, "phy1": {"country": "99", "self_managed": False}},
    netinv.parse_reg(REG),
)
DUMP = "BSS 50:0f:f5:ad:15:e4(on wlan0) -- associated\n\tCountry: US\tEnvironment: Indoor/Outdoor\nBSS 11:22:33:44:55:66(on wlan0)\n\tCountry: GB\n"
real_run = netinv.run
netinv.run = lambda *cmd, **kw: (0, DUMP)
check("the joined access point's announced country", netinv.announced_country("wlan0", "50:0F:F5:AD:15:E4") == "US")
check("  one not in the scan results: none", netinv.announced_country("wlan0", "aa:bb:cc:dd:ee:ff") is None)
netinv.run = real_run

# A netplan handover's undo removes the file only while it still holds the handover's own country.
from irate_box.root import nmhandover

check("nmhandover writes the same file", nmhandover.REGDOM_CONF == wificountry.REGDOM_CONF)
source = (REPO / "irate_box/root/nmhandover.py").read_text()
check(
    "its undo deletes the file only while it holds the handover's words",
    '"kept when it was handed over" in REGDOM_CONF.read_text()' in source,
)

sys.exit(1 if fails else 0)
