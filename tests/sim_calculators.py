# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Calculators and Tools: one page with four sub-headings, every entry in its place, the old
Electronics and RF & LoRa bookmarks landing on theirs, Meshtastic without the LoRa Frame
Visualiser, Flashing tools with the serial terminal at the end, and one tile for the three old
folders. Against a hub it starts. python3 tests/sim_calculators.py"""

import html
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import adapt_tools

state = tempfile.mkdtemp(prefix="calculators-")
port = free_port()
SECRET = "f" * 64
env = dict(
    os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1"
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0

CALCULATORS = [
    (
        "general",
        "General calculators & misc tools",
        [
            "Unit Converter and Calculator",
            "Discord Timestamp Generator",
            "URL Cleaner",
        ],
    ),
    (
        "computing-data",
        "Computing & Data",
        [
            "Computing Units",
            "Datatype Byte Converter",
            "Protobuf Encoding Demonstrator",
            "ASCII and Unicode Converter",
            "Unicode Explorer",
            "Emoji Version Explorer",
            "Integer Arithmetic Calculator",
        ],
    ),
    (
        "electronics",
        "Electronics",
        [
            "Solar Panel Sense Checker",
            "Voltage Divider Calculator",
            "Ohm's Law Calculator",
            "Capacitor Discharge Calculator",
            "MLCC Derating Lookup",
            "Wire Voltage Drop Calculator",
            "Battery Runtime Calculator",
            "IC Pinout ASCII Generator",
            "Buck and Boost Duty Cycle Calculator",
        ],
    ),
    (
        "rf-lora",
        "RF & LoRa",
        [
            "Serial terminal and log analyser",
            "LoRa Airtime Calculator",
            "LoRa Frame Structure Visualiser",
            "USB RF Power Meter V3 Reader",
        ],
    ),
]
MESHTASTIC = [
    "Serial terminal and log analyser",
    "Download serial terminal (for live USB)",
    "ESP32 web flasher",
    "Rockchip boards",
    "Meshtastic documentation (offline)",
    "Meshtastic Node ID Tool",
    "Meshtastic URL & QR Tool",
    "meshtasticd Pinout Map",
    "LoRa Airtime Calculator",
]
FLASHING = [
    "ESP32 web flasher",
    "FreeOCD ARM debugger and flasher",
    "Rockchip boards",
    "Download serial terminal (for live USB)",
    "Serial terminal and log analyser",
]


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def groups(page):
    """[(heading id, heading, [entry name])] on a menu page; one unnamed group when it has no headings."""
    out = []
    for m in re.finditer(r'<h2 id="([^"]+)">([^<]*)</h2>|<span class="name">([^<]*)</span>', page):
        if m.group(1):
            out.append((m.group(1), html.unescape(m.group(2)), []))
        elif out:
            out[-1][2].append(html.unescape(m.group(3)))
        else:
            out.append(("", "", [html.unescape(m.group(3))]))
    return out


try:
    for _ in range(300):  # up to 30 s: a busy CI runner can take longer than 5 s to start the hub
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    # Seen at /admin, as its owner: the Rockchip entry is administrator-only.
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/admin/", headers={"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET})
    r = c.getresponse()
    r.read()
    seen = (r.getheader("Set-Cookie") or "").split(";")[0]

    def go(path):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET, "X-Irate-Admin": "1"}
        if seen:
            headers["Cookie"] = seen
        c.request("GET", path, headers=headers)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode()

    st, _, page = go("/calculators.html")
    got = groups(page)
    check("Calculators and Tools answers", st == 200 and "Calculators and Tools" in page, st)
    check(
        "four sub-headings, in Tom's order",
        [g[0] for g in got] == [g[0] for g in CALCULATORS] and [g[1] for g in got] == [g[1] for g in CALCULATORS],
        [g[:2] for g in got],
    )
    for (sid, title, names), (gsid, gtitle, gnames) in zip(CALCULATORS, got, strict=True):
        check(f"  {title}: every entry, in its place", gsid == sid and gtitle == title and gnames == names, gnames)

    for old, anchor in (
        ("/tools-general.html", "#general"),
        ("/tools-electronics.html", "#electronics"),
        ("/tools-rf.html", "#rf-lora"),
    ):
        st, headers, _ = go(old)
        check(
            f"{old} lands on its sub-heading",
            st in (301, 302) and headers.get("Location") == f"/calculators.html{anchor}",
            (st, headers.get("Location")),
        )

    st, _, page = go("/meshtastic.html")
    check(
        "Meshtastic: as it was, less the LoRa Frame Visualiser",
        st == 200 and groups(page)[0][2] == MESHTASTIC,
        groups(page),
    )
    st, _, page = go("/flashing.html")
    check(
        "Flashing tools: the serial terminal and log analyser last",
        st == 200 and groups(page)[0][2] == FLASHING,
        groups(page),
    )

    st, _, home = go("/")
    check(
        "one tile, named Calculators and Tools",
        st == 200 and '<span class="name">Calculators and Tools</span>' in home,
        st,
    )
    check("  and no Electronics tile", '<span class="name">Electronics</span>' not in home)
    check(
        "  and no RF & LoRa tile",
        '<span class="name">RF &amp; LoRa</span>' not in home and '<span class="name">RF & LoRa</span>' not in home,
    )
    check(
        "  the tile opens the page",
        'href="/calculators.html"' in home[home.find('<span class="name">Calculators and Tools</span>') - 400 :][:400],
    )

    st, _, body = go("/menus.json")
    menus = json.loads(body)
    check(
        "/menus.json has the one list, not the old three",
        st == 200
        and menus.get("calculators", {}).get("href") == "/calculators.html"
        and not ({"tools-general", "tools-electronics", "tools-rf"} & set(menus)),
        menus,
    )
    st, _, body = go("/admin/folders")
    folders = {f["id"] for f in json.loads(body)["folders"]}
    check(
        "/admin/folders: one folder, not the old three",
        st == 200 and "calculators" in folders and not ({"tools-general", "tools-electronics", "tools-rf"} & folders),
        folders,
    )

    manifest = json.loads((REPO / "apps.d/45-tools.json").read_text())
    by_name = {e["name"]: e for e in manifest["entries"]}
    check(
        "the two discovered calculators have entries of their own",
        "Emoji Version Explorer" in by_name and "Buck and Boost Duty Cycle Calculator" in by_name,
    )
    check(
        "  and discovery no longer places anything",
        "discover" not in manifest and all("section" in e for e in manifest["entries"] if "calculators" in e["menus"]),
    )
    menu = json.loads((REPO / "apps.d/51-calculators.json").read_text())["menu"]
    section_ids = [s["id"] for s in menu["sections"]]
    check(
        "the page's sections are the four the sub-headings name",
        section_ids == [s[0] for s in CALCULATORS],
        section_ids,
    )
    check(
        "  every entry naming a section names one of them",
        all(e.get("section") in section_ids for e in manifest["entries"] if "calculators" in e["menus"]),
    )
    back = adapt_tools.homes()
    check(
        "Back to index leads each calculator page to the hub list that names it",
        back.get("lora-airtime-calculator.html") == "/calculators.html"
        and back.get("unit-conversion.html") == "/calculators.html"
        and back.get("meshtastic-nodeid-tool.html") == "/meshtastic.html",
        back,
    )
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
