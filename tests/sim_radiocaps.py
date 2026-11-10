# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The radio capability record (root/radiocaps.py): the two-part key from a USB radio and a built-in one, the
driver read live and the chip table read live, a test and a learned answer above them, and the one-time
migration from radio-tests.json and ap-tried.json. Real `iw list` captures from the plan's evidence folder.
python3 tests/sim_radiocaps.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="radiocaps-"))
SYS = T / "sys"
for module, version in (("aic8800_fdrv", "6.4.3.0"), ("brcmfmac", None)):
    folder = SYS / "module" / module
    folder.mkdir(parents=True)
    if version:
        (folder / "version").write_text(version + "\n")
    (folder / "srcversion").write_text("5BC8B259A1C2D3E4F506172\n")
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_SYSFS=str(SYS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import netinv
from irate_box.root import radiocaps

FIX = Path(__file__).resolve().parent / "fixtures"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# The driver's own answers, from the captures the plan cites (2026-11-10).
lyra = netinv.parse_phys((FIX / "iw-list-aic8800dc-lyra-bench.txt").read_text())["phy5"]
pi = netinv.parse_phys((FIX / "iw-list-brcmfmac-pi.txt").read_text())["phy0"]
check(
    "the Lyra's AIC8800DC says channel_switch and set_channel, an AP, 2.4 GHz, up to 3 channels",
    {"channel_switch", "set_channel"} <= set(lyra["commands"])
    and lyra["ap"]
    and lyra["bands"] == ["2.4 GHz"]
    and lyra["channels_at_once"] == 3,
    {k: lyra[k] for k in ("commands", "ap", "bands", "channels_at_once")},
)
check(
    "the Pi's brcmfmac says set_channel but not channel_switch, and both bands",
    "set_channel" in pi["commands"]
    and "channel_switch" not in pi["commands"]
    and pi["bands"] == ["2.4 GHz", "5 GHz"]
    and pi["sched_scan"],
    {k: pi[k] for k in ("commands", "bands", "sched_scan")},
)

usb = {
    "iface": "wlan0",
    "phy": "phy5",
    "type": "managed",
    "driver": "aic8800_fdrv",
    "usb": {"id": "a69c:88dc", "port": "1-1.1", "product": "AIC8800DC"},
    **lyra,
    "known": netinv.known_facts({"usb": {"id": "a69c:88dc"}, "driver": "aic8800_fdrv"}),
}
builtin = {
    "iface": "wlan0",
    "phy": "phy0",
    "type": "managed",
    "driver": "brcmfmac",
    "addr": "dc:a6:32:12:34:56",
    **pi,
    "known": netinv.known_facts({"driver": "brcmfmac"}),
}
check(
    "the key names the radio (USB id and port; a built-in one's MAC) and the driver (its version, else srcversion and kernel)",
    radiocaps.key(usb) == "usb:a69c:88dc@1-1.1|aic8800_fdrv 6.4.3.0"
    and radiocaps.key(builtin) == f"mac:dc:a6:32:12:34:56|brcmfmac 5BC8B259A1C2D3E4F506172+{os.uname().release}",
    (radiocaps.key(usb), radiocaps.key(builtin)),
)
check(
    "two identical dongles on two ports get two records",
    radiocaps.key(dict(usb, usb={"id": "a69c:88dc", "port": "1-1.2"})) != radiocaps.key(usb),
)
check(
    "the chip's firmware is passed on, with a one-line summary and the rest as evidence",
    usb["known"]["firmware"].startswith("the pinned driver's fw/aic8800DC")
    and usb["known"]["summary"].startswith("Built into the Luckfox Lyra Zero W.")
    and any("16 stalls" in note for note in usb["known"]["evidence"]),
    usb["known"],
)

rows = radiocaps.rows(usb)
check(
    "the empty record: the driver's rows, the chip's own-channel answer, and the driver's three channels below it",
    rows["channels-at-once"]["value"] == 1
    and rows["channels-at-once"]["source"] == "known"
    and rows["channels-at-once"]["below"][0]["value"] == 3
    and rows["own-channel"]["value"] == "no"
    and rows["follows-by-itself"]["value"] == "yes"
    and rows["ap-beside-link"]["value"] == "yes",
    {k: rows[k] for k in ("channels-at-once", "own-channel", "follows-by-itself")},
)
check(
    "an unanswered row with a test is offered it; a chip-list row is not (no button)",
    rows["reset-recovery"]["value"] is None
    and rows["reset-recovery"]["testable"] is False
    and rows["own-channel"]["test"] == "own-channel"
    and rows["follows-by-itself"]["test"] == "follows-roam",
    {k: rows[k] for k in ("reset-recovery", "own-channel", "follows-by-itself")},
)
check(
    "the Pi's brcmfmac has no channel_switch row answer (the csa row is PR 2's): its AP and SAE are the driver's",
    rows["ap"]["value"] == "yes" and rows["sae"]["value"] == "yes",
)

radiocaps.record_test(usb, "own-channel", True, "It held a channel of its own.")
radiocaps.record_test(usb, "radio-reset", "works", "The link was back in 4 s.")
rows = radiocaps.rows(usb, radiocaps.stored(usb), can=("own-channel", "radio-reset"))
check(
    "a test on a row the chip list answers is not stored: the chip list stays the answer",
    "own-channel" not in radiocaps.stored(usb)
    and rows["own-channel"]["value"] == "no"
    and rows["own-channel"]["source"] == "known"
    and rows["reset-recovery"]["value"] == "yes"
    and rows["reset-recovery"]["source"] == "test",
    {k: rows[k] for k in ("own-channel", "reset-recovery")},
)
radiocaps.record_learned(usb, "reset-recovery", "no", "The link was not back in 180 s.")
rows = radiocaps.rows(usb, radiocaps.stored(usb))
check(
    "a learned row cannot override a test (test > learned), and stays the answer when there is none",
    rows["reset-recovery"]["value"] == "yes"
    and rows["reset-recovery"]["source"] == "test"
    and radiocaps.stored(usb)["reset-recovery"]["source"] == "test",
    rows["reset-recovery"],
)
radiocaps.record_learned(builtin, "reset-recovery", "no", "The link was not back in 180 s.")
check(
    "  with no test, what the box saw is the row",
    radiocaps.rows(builtin, radiocaps.stored(builtin))["reset-recovery"]["value"] == "no"
    and radiocaps.stored(builtin)["reset-recovery"]["source"] == "learned",
    radiocaps.stored(builtin),
)

# A change of driver starts a fresh record, and the old one is still named.
old_key = radiocaps.key(usb)
changed = dict(usb, driver="aic8800_fdrv", usb={"id": "a69c:88dc", "port": "1-1.1"})
(SYS / "module" / "aic8800_fdrv" / "version").write_text("6.5.0.0\n")
check(
    "a driver update changes the key; the old record is kept as the previous driver's",
    radiocaps.key(changed) != old_key
    and radiocaps.stored(changed) == {}
    and radiocaps.previous(changed)["radio"] == "usb:a69c:88dc@1-1.1",
    (radiocaps.key(changed), radiocaps.stored(changed), radiocaps.previous(changed)),
)
(SYS / "module" / "aic8800_fdrv" / "version").write_text("6.4.3.0\n")
radiocaps.STORE.unlink(missing_ok=True)

# The migration: radio-tests.json by model and driver version; ap-tried.json only where one radio can be an AP.
inv = {
    "radios": [
        dict(usb, type="managed"),
        dict(usb, iface="ap0", type="AP"),
    ]
}
(T / "state").mkdir(parents=True, exist_ok=True)
(T / "state" / "radio-tests.json").write_text(
    json.dumps(
        {
            "a69c:88dc aic8800_fdrv 6.4.3.0": {
                "follows-roam": {"answer": "follows", "detail": "It followed the link.", "at": 100},
                "radio-reset": {"answer": "works", "detail": "It came back in 4 s.", "at": 200},
            }
        }
    )
)
(T / "etc").mkdir(parents=True, exist_ok=True)
(T / "etc" / "ap-tried.json").write_text(json.dumps({"phy5": True}))
migrated = radiocaps.stored(usb) if radiocaps.migrate(inv) else {}
migrated_rows = radiocaps.rows(usb, migrated)
check(
    "the migration reads both old stores once; the chip list's own rows are left to it",
    migrated.get("reset-recovery", {}).get("value") == "yes"
    and "follows-by-itself" not in migrated
    and "own-channel" not in migrated
    and migrated_rows["follows-by-itself"]["value"] == "yes"
    and migrated_rows["own-channel"]["value"] == "no",
    (migrated, {k: migrated_rows[k]["value"] for k in ("follows-by-itself", "own-channel")}),
)
check(
    "  the old files are left in place, unread from now on",
    (T / "state" / "radio-tests.json").exists() and (T / "etc" / "ap-tried.json").exists(),
)
check("  a second run changes nothing", radiocaps.migrate(inv) is False)

# lyra-tft's real file: {"phy0": true}, while its radio is phy6 (a driver reload renumbers the phy).
radiocaps.STORE.unlink(missing_ok=True)
tft = dict(builtin, iface="wlan0", phy="phy6")
tft_inv = {"radios": [tft, dict(tft, iface="ap0", type="AP")]}
(T / "etc" / "ap-tried.json").write_text(json.dumps({"phy0": True}))
radiocaps.migrate(tft_inv)
check(
    "the stale phy number in ap-tried.json: its one answer goes to the box's one radio that can be an AP",
    radiocaps.stored(tft)["own-channel"]["value"] == "yes",
    radiocaps.stored(tft),
)
radiocaps.STORE.unlink(missing_ok=True)
(T / "etc" / "ap-tried.json").write_text(json.dumps({"phy0": True, "phy6": False}))
radiocaps.migrate(tft_inv)
check(
    "  more than one entry: dropped",
    "own-channel" not in radiocaps.stored(tft),
    radiocaps.stored(tft),
)
radiocaps.STORE.unlink(missing_ok=True)
other = dict(builtin, iface="wlan1", phy="phy7", addr="dc:a6:32:12:34:57")
two = {"radios": [tft, other]}
(T / "etc" / "ap-tried.json").write_text(json.dumps({"phy6": True}))
radiocaps.migrate(two)
check(
    "the phy-only ap-tried answer is dropped where two radios can be an access point",
    all("own-channel" not in radiocaps.stored(r) for r in two["radios"]),
    [radiocaps.stored(r) for r in two["radios"]],
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
