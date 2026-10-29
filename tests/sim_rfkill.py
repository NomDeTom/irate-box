# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The radios switched on at every start (root/rfkill.py) and the box doctor's side of it, offline, on stand-in /sys
trees: the Luckfox Lyra Zero W's WiFi chip held off by bt_default (Armbian's image), unblocked and its own switches
set once the chip reaches the bus; an admin's off, leave and Bluetooth-only choices; a board with no power line; a
hard block reported, not touched; other types left alone; and the doctor's settings, findings and choices.
python3 tests/sim_rfkill.py"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rfkill-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_SYSFS=str(T / "sys"),
    HUB_DEVICE_TREE=str(T / "dt"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import render
from irate_box.root import health, rfkill

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def put(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def switch(index, name, kind, soft=0, hard=0):
    d = T / "sys/class/rfkill" / f"rfkill{index}"
    put(d / "name", f"{name}\n")
    put(d / "type", f"{kind}\n")
    put(d / "soft", f"{soft}\n")
    put(d / "hard", f"{hard}\n")


def soft(index):
    return (T / "sys/class/rfkill" / f"rfkill{index}" / "soft").read_text().strip()


def board(model, switches):
    """A fresh stand-in: the board's model and its switches [(index, name, type, soft, hard)]; no settings."""
    for d in ("sys", "dt", "etc"):
        shutil.rmtree(T / d, ignore_errors=True)
    put(T / "dt" / "model", "")
    (T / "dt" / "model").write_bytes(model.encode() + b"\0")
    (T / "etc").mkdir()
    for sw in switches:
        switch(*sw)


def chip_arrives(*switches):
    """A sleep that brings the chip's own switches on its first call, as the bus would once its power is on."""
    calls = []

    def sleep(_):
        if not calls:
            for sw in switches:
                switch(*sw)
        calls.append(1)

    return sleep, calls


LYRA = "Luckfox Lyra Zero W"

# --- the Lyra, as Armbian's image starts it: bt_default blocked, the chip off the bus ---------------------------
board(LYRA, [(0, "bt_default", "bluetooth", 1)])
sws = {sw["name"]: sw for sw in rfkill.switches()}
check(
    "the Lyra's bt_default is the WiFi chip's power line, counted as WiFi",
    sws["bt_default"]["radio"] == "wifi" and sws["bt_default"]["power_line"],
    sws,
)
found = health.check_rfkill()
check(
    "  the doctor: a problem, the chip's power named, one action to set it again",
    len(found) == 1
    and found[0]["status"] == "problem"
    and "bt_default (the WiFi chip's power) is blocked" in render(found[0]["summary"])
    and [a["choice"] for a in found[0]["actions"]] == ["rfkill-apply"],
    found,
)
# The chip's switches come back with what systemd-rfkill saved: WiFi's blocked, Bluetooth's not.
sleep, calls = chip_arrives((1, "phy0", "wlan", 1), (2, "hci0", "bluetooth", 0))
changed = rfkill.apply(sleep=sleep)
check(
    "  no admin choice: the power line unblocked, then the chip's WiFi switch once it is on the bus",
    soft(0) == "0"
    and soft(1) == "0"
    and soft(2) == "0"
    and [sw["name"] for sw, *_ in changed] == ["bt_default", "phy0"],
    rfkill.said(changed),
)
check("  and the wait ends as soon as the chip is there", 0 < len(calls) <= 2, calls)
found = health.check_rfkill()
check(
    "  the doctor: as set, and says no admin has chosen yet",
    len(found) == 1
    and found[0]["status"] == "ok"
    and render(found[0]["summary"])
    == "As set: WiFi switched on at every start and Bluetooth switched on at every start."
    and any("An administrator has not selected" in render(n) for n in found[0]["notes"]),
    found,
)
check("  nothing at odds: a second boot changes nothing", rfkill.apply(sleep=lambda _: None) == [])

# --- an admin's choices --------------------------------------------------------------------------------------
said = health.fix("rfkill:bluetooth:off")
check(
    "Bluetooth off: hci0 blocked, the chip's power left on (WiFi stays)",
    soft(0) == "0" and soft(1) == "0" and soft(2) == "1" and said["id"] == "rfkill-set.off",
    said,
)
check(
    "  saved, and counted as an admin's choice",
    rfkill.load_settings()["bluetooth"] == "off" and rfkill.chosen() == {"bluetooth"},
)
switch(2, "hci0", "bluetooth", 0)  # unblocked by something else since
found = health.check_rfkill()
check(
    "  unblocked since: a warning (not a problem: WiFi is fine)",
    found and found[0]["status"] == "warn" and "hci0 (Bluetooth) is unblocked" in render(found[0]["summary"]),
    found,
)
said = health.fix("rfkill-apply")
check("  set again", soft(2) == "1" and said["id"] == "repair.rfkill.done", said)

groups = {g["key"]: g for g in health.rfkill_groups(rfkill.status())}
check(
    "the settings card: one group per radio, three choices each",
    set(groups) == {"rfkill-wifi", "rfkill-bluetooth"}
    and [o["choice"] for o in groups["rfkill-wifi"]["options"]]
    == ["rfkill:wifi:on", "rfkill:wifi:off", "rfkill:wifi:leave"]
    and groups["rfkill-bluetooth"]["current"] == "off",
    groups,
)
check(
    "  WiFi's says that off takes Bluetooth off too on this board",
    "Off takes Bluetooth off too" in render(groups["rfkill-wifi"]["desc"]),
    groups["rfkill-wifi"]["desc"],
)

health.fix("rfkill:wifi:off")
check("WiFi off: the chip's power and phy0 blocked", soft(0) == "1" and soft(1) == "1")
groups = {g["key"]: g for g in health.rfkill_groups(rfkill.status())}
check(
    "  Bluetooth's setting says it stays off with the chip",
    "Bluetooth stays off whatever is set here" in render(groups["rfkill-bluetooth"]["desc"]),
    groups["rfkill-bluetooth"]["desc"],
)

health.fix("rfkill:wifi:leave")
switch(0, "bt_default", "bluetooth", 0)
said = health.fix("rfkill:wifi:leave")
check(
    "WiFi left as the image has it: nothing changed, either way",
    soft(0) == "0" and soft(1) == "1" and rfkill.apply(sleep=lambda _: None) == [] and said["id"] == "rfkill-set.leave",
)

# --- other boards ---------------------------------------------------------------------------------------------
board(
    "Raspberry Pi 3 Model B Plus Rev 1.3", [(0, "phy0", "wlan", 1), (1, "hci0", "bluetooth", 1), (2, "gps0", "gps", 1)]
)
changed = rfkill.apply(sleep=lambda _: check("  no power line: no wait", False))
check(
    "a Pi: WiFi and Bluetooth unblocked, a GPS switch left alone",
    soft(0) == "0" and soft(1) == "0" and soft(2) == "1" and len(changed) == 2,
    rfkill.said(changed),
)
board(LYRA, [(0, "bt_default", "bluetooth", 0), (1, "phy0", "wlan", 1, 1)])
found = {f["id"]: f for f in health.check_rfkill()}
check(
    "a hard block: reported as the board's switch",
    "rfkill:hard" in found and "phy0" in render(found["rfkill:hard"]["summary"]),
    found,
)
board("Some board", [])
check(
    "no switches: no finding and no settings",
    health.check_rfkill() == [] and health.rfkill_groups(rfkill.status()) == [],
)
board("Some board", [(0, "nfc0", "nfc", 1)])
check("only other types: no finding", health.check_rfkill() == [])

# --- the choices, as the doctor and the hub take them -----------------------------------------------------------
for good in ("rfkill:wifi:on", "rfkill:bluetooth:leave", "rfkill-apply"):
    check(f"the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("rfkill:wwan:on", "rfkill:wifi:maybe", "rfkill-apply:x"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))
from irate_box.hub import server

check(
    "the hub passes the same choices on",
    all(server.HEALTH_CHOICE_RE.match(c) for c in ("rfkill:wifi:off", "rfkill-apply"))
    and not server.HEALTH_CHOICE_RE.match("rfkill:wifi:x"),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
