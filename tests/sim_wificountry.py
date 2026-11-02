# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi country as a setting (hub/wificountry.py, the root helper's wifi-country): the list from the box's
iso-codes, or else the list the hub ships, the saved country read back, a country applied now (iw reg set) and kept for boot (cfg80211's
option file), an unknown one refused, iw failing (kept for boot, said so), the inventory carrying both, and a
netplan handover's undo leaving a country chosen since. iw, iso-codes and the folders stood in for.
python3 tests/sim_wificountry.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="wificountry-"))
ISO_CODES = T / "iso-codes" / "iso_3166-1.json"  # none at first: a minimal image, as the Lyra's
os.environ.update(
    HUB_ISO_CODES=str(ISO_CODES),
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

shipped = wificountry.countries()
check(
    "no iso-codes on the box: the shipped list, the United Kingdom by that name",
    len(shipped) == 249 and shipped["GB"] == "United Kingdom" and shipped["KR"] == "South Korea",
    {k: shipped.get(k) for k in ("GB", "KR")},
)
check("  in code order", list(shipped) == sorted(shipped), list(shipped)[:5])
ISO_CODES.parent.mkdir()
ISO_CODES.write_text(
    json.dumps(
        {
            "3166-1": [
                {"alpha_2": "GB", "name": "United Kingdom", "official_name": "United Kingdom of Great Britain"},
                {"alpha_2": "KR", "name": "Korea, Republic of", "common_name": "South Korea"},
                {"alpha_2": "ZZ", "name": "Newland"},  # a newer iso-codes than the shipped list
                {"alpha_2": "xx", "name": "not a code"},
                {"name": "no code"},
            ]
        }
    )
)
check(
    "iso-codes on the box: its own list, the common name where there is one, malformed entries left out",
    wificountry.countries() == {"GB": "United Kingdom", "KR": "South Korea", "ZZ": "Newland"},
    wificountry.countries(),
)
ISO_CODES.write_text("{not json")
check("  an unreadable iso-codes: the shipped list again", wificountry.countries() == shipped)
ISO_CODES.unlink()
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
    "countries": wificountry.by_name(),
}
names = [name for _, name in inv["countries"]]
check(
    "the inventory's list by name, accents aside: Åland with the As, Côte d'Ivoire with the Cs, Türkiye with the Ts",
    names[:4] == ["Afghanistan", "Åland Islands", "Albania", "Algeria"]
    and names.index("Côte d'Ivoire") < names.index("Croatia")
    and names.index("Türkiye") < names.index("Turkmenistan")
    and names.index("Réunion") < names.index("Romania")
    and names.index("United Kingdom") < names.index("United States"),
    names[:6],
)
check(
    "  netinv lists them so",
    "wificountry.by_name()" in (REPO / "irate_box/hub/netinv.py").read_text(),
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
