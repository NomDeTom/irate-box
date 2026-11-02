# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Quick presets (presets.d/, irate_box/presets.py, irate_box/root/presets.py), offline: every
entry is data the box would accept; the board-fit checks and the journal's fallbacks; root applies
a list of choices and puts them back, leaving one the administrator changed since. The hub's own
page is a data file too, so nothing here needs a box.
python3 tests/sim_presets.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="presets-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box import choices, presets
from irate_box.hub import manifests, server
from irate_box.root import kits
from irate_box.root import presets as root_presets

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


all_presets = presets.load()
check(
    "three purpose presets and three durability presets",
    len(all_presets) == 6
    and {p["id"] for p in presets.load("purpose")} == {"pirate", "curated", "power"}
    and {p["id"] for p in presets.load("durability")} == {"longevity", "balanced", "full"},
)

known = {
    "hub_keys": set(server.DEFAULT_SETTINGS),
    "addons": set(manifests.addons(server.MANIFESTS)),
    "kits": set(kits.definitions()),
}
errors = [e for p in all_presets for e in presets.preset_errors(p, **known)]
check("every entry is a setting, choice, add-on or toolkit the box accepts", not errors, errors)

check(
    "every purpose suggests a durability, and every entry says why",
    all(p.get("suggested_durability") in {x["id"] for x in presets.load("durability")} for p in presets.load("purpose"))
    and all(entry.get("why") for p in all_presets for entry in p["entries"]),
)

small = {"arch": "armv6l", "memory_mb": 512, "free_mb": 900, "flash_free": False}
fitted, left = presets.resolve(presets.get("power")["entries"], small)
names = {e.get("addon") for e in fitted}
check(
    "power on a 512 MB ARMv6 board leaves out SilverBullet and live collaboration, with a reason",
    "notes" not in names and "room" not in names and len(left) >= 2 and all(item["why"] for item in left),
    (names, left),
)
large = {"arch": "aarch64", "memory_mb": 4096, "free_mb": 20000, "flash_free": True}
large_card = {
    "arch": "armv7",
    "memory_mb": 4096,
    "free_mb": 20000,
    "flash_free": False,
}  # a small state on the NAND, room on the card
fitted, left = presets.resolve(presets.get("power")["entries"], large)
check(
    "  and on a Pi 4 with a free flash, everything fits",
    not left and {"notes", "room", "debug"} <= {e.get("addon") or e.get("kit") for e in fitted},
)
fitted, left = presets.resolve(presets.get("power")["entries"], large_card)
check(
    "  the room a preset needs is the card's, whatever the state folder's own size",
    "room" in {e.get("addon") for e in fitted} and not any(item.get("addon") == "room" for item in left),
    left,
)


def journal_entry(name, facts):
    fitted, left = presets.resolve(presets.get(name)["entries"], facts)
    return [e.get("choice") for e in fitted if e.get("choice", "").startswith("card-set:journal:")][0]


check("journal: on the flash where one is free", journal_entry("longevity", large) == "card-set:journal:flash")
check("  else in memory for longevity", journal_entry("longevity", small) == "card-set:journal:memory")
check(
    "  else on the card, synced every 5 minutes, for balanced",
    journal_entry("balanced", small) == "card-set:journal:card5",
)
check("  and persistence on the card for full logging", journal_entry("full", small) == "card-set:journal:card")

state = {"card-set:apt_cache": "card", "crashwatch-panic": "off", "autoupdate-set": "24-2"}


def key_of(choice):
    kind, _, arg = choice.partition(":")
    if kind == "card-set":
        return f"card-set:{arg.partition(':')[0]}"
    if kind.startswith("crashwatch-"):
        return f"crashwatch-{kind[len('crashwatch-') :].partition(':')[0]}"
    return kind


def fake_current(choice):
    return state.get(key_of(choice))


def fake_apply(choice):
    kind, _, arg = choice.partition(":")
    value = arg.partition(":")[2] if kind == "card-set" else arg
    state[key_of(choice)] = value
    return {"id": "fake", "en": choice}


root_presets.current = fake_current
root_presets._apply = fake_apply

got = root_presets.apply_choices(["card-set:apt_cache:memory", "crashwatch-panic:on", "autoupdate-set:24-0"])
check(
    "root applies each choice and records the before and after",
    state == {"card-set:apt_cache": "memory", "crashwatch-panic": "on", "autoupdate-set": "24-0"}
    and [e["before"] for e in got["entries"]] == ["card", "off", "24-2"]
    and all(e["ok"] for e in got["entries"]),
    got,
)
check(
    "  one record of the whole application, in the state folder",
    json.loads(root_presets.RECORD.read_text())["entries"] == got["entries"],
)

state["crashwatch-watchdog"] = "off"
state["crashwatch-panic"] = "on"
undone = root_presets.undo()
check(
    "undo puts back every setting it changed",
    state["card-set:apt_cache"] == "card" and state["autoupdate-set"] == "24-2" and undone["restored"] == 3,
    (state, undone),
)

state.update({"card-set:apt_cache": "card", "crashwatch-panic": "off", "autoupdate-set": "24-2"})
root_presets.apply_choices(["card-set:apt_cache:memory"])
state["card-set:apt_cache"] = "card5"
undone = root_presets.undo()
check(
    "  a setting changed since the preset was applied is not touched",
    state["card-set:apt_cache"] == "card5" and undone["kept"],
    (state, undone),
)

got = root_presets.apply_choices(["card-set:apt_cache:card", "made-up:one"])
check(
    "a choice the box refuses is recorded, and the others still apply",
    [e["ok"] for e in got["entries"]] == [True, False] and state["card-set:apt_cache"] == "card",
    got,
)

check(
    "root's record is in root's own state, not the hub's folder",
    str(root_presets.RECORD).startswith(str(root_presets.safeio.ROOT_STATE)),
)

# Root resolves a preset by name itself, never a list of choices the hub sends.
state.clear()
state.update(
    {"card-set:apt_cache": "card", "card-set:journal": "card", "crashwatch-panic": "off", "autoupdate-set": "24-2"}
)
root_presets._facts = lambda: {"arch": "aarch64", "memory_mb": 4096, "free_mb": 20000, "flash_free": True}
before_record = root_presets.load_record()
root_presets.apply_named(durability="balanced", save=False)
check(
    "a durability preset applied by name changes only its own settings, and saves no record",
    state["card-set:journal"] == "flash"
    and state["card-set:apt_cache"] == "memory"
    and root_presets.load_record() == before_record,
)
try:
    root_presets.apply_named(durability="made-up")
    check("  an unknown preset name is refused", False)
except Exception as exc:  # noqa: BLE001
    check("  an unknown preset name is refused", "preset.unknown" == getattr(exc, "message", {}).get("id"), exc)

check(
    "every choice in the shipped presets passes the shared pattern",
    all(choices.CHOICE_RE.match(e["choice"]) for p in all_presets for e in p["entries"] if e.get("type") == "choice"),
)

# The setup tour: a completed preset marks the steps it answered done.
check(
    "quick setup is a setup decision, and the hub keeps it",
    "quick-setup" in server.SETUP_DECISIONS and server.valid_setting("setup_decided", ["quick-setup"]),
)
setup_changes = [
    {"ok": True, "entry": {"type": "accounts", "key": "signup"}},
    {"ok": True, "entry": {"type": "hotspot", "value": "open"}},
    {"ok": True, "entry": {"type": "access", "app": "term", "mode": "off"}},
    {"ok": True, "entry": {"type": "addon", "addon": "notes", "on": True}},
    {"ok": False, "entry": {"type": "addon", "addon": "room", "on": True}},
    {"ok": True, "entry": {"type": "hub", "key": "shout_who", "value": "users"}},
]
check(
    "a preset's setup steps: quick setup, signup, hotspot, terminal and add-ons",
    server._preset_setup_ids({"changes": setup_changes}) == ["addons", "hotspot", "quick-setup", "signup", "terminal"],
    server._preset_setup_ids({"changes": setup_changes}),
)
check(
    "  an entry that failed is not marked",
    server._preset_setup_ids(
        {"changes": [{"ok": True, "request": "r1", "entry": {"type": "access", "app": "term", "mode": "on"}}]},
        {"r1": {"ok": False}},
    )
    == ["quick-setup"],
)
check(
    "  an answered root request is",
    server._preset_setup_ids(
        {"changes": [{"ok": True, "request": "r1", "entry": {"type": "access", "app": "term", "mode": "on"}}]},
        {"r1": {"ok": True}},
    )
    == ["quick-setup", "terminal"],
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
