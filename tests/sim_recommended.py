# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The recommended settings for writes and recovery (root/recommended.py, Box doctor's Recommended settings),
offline, against stood-in settings: what differs listed, the owner's own choices never offered back (recorded by
health.fix for card-set and crash watch), the journal on the flash only where a free one is found, applying them
recorded as nobody's choice, the SD card writes level pointed to and not set, the finding and its one repair.
python3 tests/sim_recommended.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="recommended-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.msg import render
from irate_box.root import crashwatch, health, recommended

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


box = {"apt_cache": "card", "apt_languages": "download", "journal": "card", "writeback": "standard"}
cw = {"snapshots": True, "panic": False, "watchdog": False}
units = {"health-timer": True, "lost-writes": True, "canary": False}
nand = {"state": None}
level = {"now": "thrifty"}
applied = []


def groups():
    opts = {"apt_cache": ["card", "memory"], "apt_languages": ["download", "none"], "writeback": ["standard", "longer"]}
    opts["journal"] = ["card", "memory"] + (["flash"] if nand["state"] else [])
    return [{"key": k, "current": box[k], "options": [{"value": v} for v in vs]} for k, vs in opts.items()]


def card_set(key, value):
    applied.append((key, value))
    box[key] = value
    return {"id": "set", "en": f"{key} {value}"}


def set_option(key, value):
    applied.append((key, value))
    cw[key] = value == "on"
    return {"id": "cw", "en": f"{key} {value}"}


def set_unit(key, on):
    applied.append((key, "on" if on else "off"))
    units[key] = on
    return {"id": "unit", "en": f"{key} {'on' if on else 'off'}"}


health.card_groups = groups
health.card_set = card_set
health._nand = lambda: (nand["state"], None)
health.timer_on = lambda: units["health-timer"]
health.lostwrites_on = lambda: units["lost-writes"]
health.set_timer = lambda on: set_unit("health-timer", on)
health.set_lostwrites = lambda on: set_unit("lost-writes", on)
health.set_canary = lambda on: set_unit("canary", on)
health.records_on_card = lambda: []
crashwatch.load_settings = lambda: dict(cw)
crashwatch.set_option = set_option
crashwatch.card_writes = lambda: level["now"]

diffs = {k: (want, now) for k, _, want, now in recommended.differences()}
check(
    "differences: apt's cache, descriptions, the journal (in memory: no flash), the write-back delay, panic, watchdog",
    diffs
    == {
        "card-set:apt_cache": ("memory", "card"),
        "card-set:apt_languages": ("none", "download"),
        "card-set:journal": ("memory", "card"),
        "card-set:writeback": ("longer", "standard"),
        "crashwatch-panic": ("on", "off"),
        "crashwatch-watchdog": ("on", "off"),
    },
    diffs,
)
nand["state"] = "free"
check(
    "  a free onboard flash: the journal on the flash",
    dict((k, w) for k, _, w, _ in recommended.differences())["card-set:journal"] == "flash",
)
nand["state"] = None
check("  the SD card writes level differs (thrifty, not gentle): said, not set by root", recommended.level_differs())

# The owner's own choice, made through the doctor: recorded, never offered back.
health.fix("card-set:apt_cache:card")
health.fix("crashwatch-watchdog:off")
check(
    "the owner's choices (a card-set, a crash watch switch) recorded by health.fix",
    json.loads(recommended.CHOSEN.read_text()) == {"card-set:apt_cache": "card", "crashwatch-watchdog": "off"},
    recommended.CHOSEN.read_text(),
)
left = {k for k, *_ in recommended.differences()}
check(
    "  and not offered back",
    "card-set:apt_cache" not in left and "crashwatch-watchdog" not in left and "crashwatch-panic" in left,
    left,
)

f = {x["id"]: x for x in health._recommended_findings()}.get("recommended", {})
check(
    "the finding: each difference listed with what it is now, the level pointed to its page, one repair",
    f.get("status") == "warn"
    and any("package descriptions not downloaded (now: download)" in e for e in f["evidence"])
    and any("SD card writes level gentle" in e for e in f["evidence"])
    and [a["choice"] for a in f["actions"]] == ["recommended-apply"]
    and "apt's cache" not in render(f["actions"][0]["confirm"]),
    f,
)
applied.clear()
before = recommended.CHOSEN.read_text()
said = health.fix("recommended-apply")
check(
    "Use the recommended settings: the rest applied, none recorded as the owner's",
    sorted(applied)
    == sorted([("apt_languages", "none"), ("journal", "memory"), ("writeback", "longer"), ("panic", "on")])
    and recommended.CHOSEN.read_text() == before
    and render(said).startswith("4 recommended settings applied"),
    (applied, render(said)),
)
level["now"] = "gentle"
check("  nothing left to offer: no finding", not health._recommended_findings())
check("the choice passes the doctor's check", health.CHOICE_RE.match("recommended-apply") is not None)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
