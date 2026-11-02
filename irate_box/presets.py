# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Quick presets: what the box is for, and how hard it writes to its card.

A preset is data in presets.d/<name>.json, never a mode. It lists changes to settings the box
already has, an add-on or a toolkit to install, and each entry may say what it needs (memory,
architecture, free space, a free onboard flash) and why. The page previews every line, applies
them through the same paths a hand change uses, and records what it changed so that Undo can put
each setting back, unless the administrator has changed it since.

Two independent dials:
  purpose     pirate, curated, power    what the box is for
  durability  longevity, balanced, full  what a power cut may lose, against the card's life

The entries are checked twice: this module's entry_errors() is called by the guard test, and root
checks every choice again before it acts (choices.py's pattern). Stdlib only.
"""

import json
import os
from pathlib import Path

from irate_box import choices
from irate_box.msg import M

DIR = Path(os.environ.get("HUB_PRESETS_D", Path(__file__).resolve().parents[1] / "presets.d"))
KINDS = ("purpose", "durability")
TYPES = ("hub", "choice", "accounts", "hotspot", "access", "addon", "kit")


def _load_file(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") and data.get("kind") in KINDS else None


def load(kind=None):
    """Every preset, purpose first, then durability, each in a stable order. A file that is not one is left out."""
    out = []
    try:
        paths = sorted(DIR.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        data = _load_file(path)
        if data is None or (kind and data["kind"] != kind):
            continue
        data.setdefault("entries", [])
        out.append(data)
    return sorted(out, key=lambda p: (KINDS.index(p["kind"]), p.get("order", 100), p["id"]))


def get(name):
    for preset in load():
        if preset["id"] == name:
            return preset
    return None


def _arch(value):
    value = str(value or "").lower()
    if value.startswith("armv7"):
        return "armv7"
    if value.startswith("aarch64") or value.startswith("arm64"):
        return "aarch64"
    if value.startswith("x86_64") or value.startswith("amd64"):
        return "x86_64"
    if value.startswith("armv6"):
        return "armv6"
    return value


def _missing(needs, facts):
    """Why an entry does not fit, as a sentence; None when it does."""
    if not isinstance(needs, dict):
        return None
    arch = needs.get("arch")
    if arch and _arch(facts.get("arch")) not in {_arch(a) for a in arch}:
        return M(
            "preset.need.arch",
            "the box's processor is {arch}",
            arch=facts.get("arch") or "not known",
        )
    memory = needs.get("memory_mb")
    if isinstance(memory, int) and memory > 0 and int(facts.get("memory_mb") or 0) < memory:
        return M(
            "preset.need.memory",
            "it needs about {want} MB of memory and the box has {have}",
            want=memory,
            have=int(facts.get("memory_mb") or 0),
        )
    free = needs.get("free_mb")
    if isinstance(free, int) and free > 0 and int(facts.get("free_mb") or 0) < free:
        return M(
            "preset.need.space",
            "it needs about {want} MB free and the box has {have}",
            want=free,
            have=int(facts.get("free_mb") or 0),
        )
    if needs.get("flash_free") is True and not facts.get("flash_free"):
        return M("preset.need.flash", "the box has no free onboard flash")
    return None


def resolve(entries, facts):
    """(the entries that fit, the ones left out). An entry with an `otherwise` is replaced by it when it does not fit."""
    fitted, left = [], []
    for entry in entries or []:
        entry = dict(entry)
        why = _missing(entry.get("needs"), facts or {})
        if why is None:
            entry.pop("otherwise", None)
            fitted.append(entry)
            continue
        otherwise = entry.get("otherwise")
        if isinstance(otherwise, dict):
            otherwise = dict(otherwise)
            otherwise.pop("needs", None)
            fitted.append(otherwise)
        else:
            left.append({"entry": entry, "why": why})
    return fitted, left


def label(entry):
    return entry.get("label") or entry.get("why") or entry.get("choice") or entry.get("addon") or entry.get("kit") or ""


def entry_errors(entry, hub_keys=None, addons=None, kits=None):
    """Every reason an entry is not one the box would accept. Empty for a good entry."""
    out = []
    if not isinstance(entry, dict):
        return ["an entry must be an object"]
    kind = entry.get("type")
    if kind not in TYPES:
        return [f"unknown type {kind!r}"]
    if kind == "hub":
        key = entry.get("key")
        if hub_keys is not None and key not in hub_keys:
            out.append(f"hub setting {key!r} is not one the hub keeps")
        if "value" not in entry:
            out.append("a hub entry needs a value")
    elif kind == "choice":
        choice = entry.get("choice")
        if not isinstance(choice, str) or not choices.CHOICE_RE.match(choice):
            out.append(f"choice {choice!r} is not one the box accepts")
    elif kind == "accounts":
        if entry.get("key") not in ("signup", "http"):
            out.append("an accounts entry changes signup or http")
        if not isinstance(entry.get("value"), str):
            out.append("an accounts entry needs a value")
    elif kind == "hotspot":
        if entry.get("value") not in ("open", "owe", "sae", "two"):
            out.append(f"hotspot mode {entry.get('value')!r} is unknown")
    elif kind == "access":
        if not isinstance(entry.get("app"), str) or not entry["app"]:
            out.append("an access entry needs an app")
        if entry.get("mode") not in ("public", "users", "private", "off"):
            out.append(f"access mode {entry.get('mode')!r} is unknown")
    elif kind == "addon":
        aid = entry.get("addon")
        if not isinstance(aid, str) or (addons is not None and aid not in addons):
            out.append(f"{aid!r} is not an add-on install.sh adds")
    elif kind == "kit":
        kid = entry.get("kit")
        if not isinstance(kid, str) or (kits is not None and kid not in kits):
            out.append(f"{kid!r} is not a toolkit this box ships")
    needs = entry.get("needs")
    if needs is not None:
        if not isinstance(needs, dict) or set(needs) - {"arch", "memory_mb", "free_mb", "flash_free"}:
            out.append("needs may name arch, memory_mb, free_mb and flash_free")
        elif needs.get("arch") is not None and not (
            isinstance(needs["arch"], list) and all(isinstance(a, str) for a in needs["arch"])
        ):
            out.append("needs.arch must be a list of architecture names")
        elif any(isinstance(needs.get(k), (int, float)) and needs[k] < 0 for k in ("memory_mb", "free_mb")):
            out.append("needs' sizes must be positive")
    otherwise = entry.get("otherwise")
    if otherwise is not None:
        if not isinstance(otherwise, dict):
            out.append("otherwise must be an entry")
        else:
            out.extend(f"otherwise: {reason}" for reason in entry_errors(otherwise, hub_keys, addons, kits))
    return out


def preset_errors(preset, **known):
    out = []
    for n, entry in enumerate(preset.get("entries") or []):
        for reason in entry_errors(entry, **known):
            out.append(f"{preset.get('id')} entry {n + 1}: {reason}")
    return out
