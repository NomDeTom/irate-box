# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's half of the quick presets: apply a list of existing choices, and undo the last application.

The hub decides what a preset changes and checks every entry against the shared data files
(irate_box/presets.py); root checks each choice again with choices.CHOICES_RE and applies it
through the same function a hand change uses (health.fix, or security.fix for the update
pattern). One record of the whole application is kept in the box's state folder, with each
setting's value before and after, so Undo can put it back unless the administrator has changed
it since. Stdlib only.
"""

import json
import os
import sys
import time
from pathlib import Path

from irate_box import choices
from irate_box.msg import M, Refused, sentences
from irate_box.root import safeio

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = safeio.ROOT_STATE / "presets" / "last.json"


def load_record():
    try:
        data = json.loads(safeio.read_own(RECORD))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_record(data):
    safeio.root_dir("presets")
    safeio.write(RECORD, json.dumps(data, indent=1, sort_keys=True) + "\n", mode=0o600)


def _group_key(choice):
    """The settings group a choice belongs to, as health.py names it."""
    kind, _, arg = choice.partition(":")
    if kind == "card-set":
        return arg.partition(":")[0]
    if kind.startswith("crashwatch-"):
        return kind[len("crashwatch-") :]
    if kind.startswith("memguard-"):
        return kind
    return kind


def _from_groups(choice, groups):
    key = _group_key(choice)
    for group in groups:
        if group.get("key") == key:
            return group.get("current")
    for group in groups:
        for option in group.get("options") or []:
            if option.get("choice") == choice:
                return group.get("current")
    return None


def current(choice):
    """What the setting behind a choice is now, or None where root cannot tell."""
    from irate_box.root import crashwatch, health, memguard

    kind, _, arg = choice.partition(":")
    try:
        if kind == "card-set":
            return _from_groups(choice, health.card_groups())
        if kind.startswith("crashwatch-"):
            key = kind[len("crashwatch-") :]
            s = crashwatch.load_settings()
            if key in ("snapshots", "panic", "watchdog", "undervoltage"):
                return "on" if s[key] else "off"
            if key == "undervoltage-mv":
                return str(s["undervoltage_mv"])
            if key == "power-action":
                return s["power_action"]
            return None
        if kind in ("health-timer", "lost-writes"):
            return _from_groups(choice, health.setting_groups(crashwatch.load_settings(), None))
        if kind == "canary":
            return "on" if health.canary_installed() else "off"
        if kind == "retain":
            from irate_box.root import retain

            return "on" if retain.installed() else "off"
        if kind == "readonly":
            from irate_box.root import readonly

            return "on" if readonly.enabled() else "off"
        if kind == "bootkey":
            from irate_box.root import bootkey

            return "on" if bootkey.installed() else "off"
        if kind.startswith("memguard-"):
            return _from_groups(choice, memguard.groups())
        if kind == "autoupdate-set":
            from irate_box.root import security

            pattern = security.chosen_pattern(security.load_record())
            return f"{pattern[0]}-{pattern[1]}" if pattern else None
    except Exception:  # noqa: BLE001 - a setting root cannot read is simply not undone
        return None
    return None


def _apply(choice):
    if choice.startswith("autoupdate-set:"):
        from irate_box.root import security

        return security.fix(choice, STATE / "preset-security.log")
    from irate_box.root import health

    return health.fix(choice)


def _inverse(choice, before):
    """A choice with the old value in place of the new one, or None for a setting root cannot make again."""
    if before is None:
        return None
    kind, _, arg = choice.partition(":")
    if kind == "card-set":
        return f"card-set:{arg.partition(':')[0]}:{before}"
    if kind.startswith("crashwatch-"):
        return f"crashwatch-{kind[len('crashwatch-') :].partition(':')[0]}:{before}"
    if kind.startswith("memguard-"):
        return f"{kind.partition(':')[0]}:{before}"
    if kind == "autoupdate-set":
        return f"autoupdate-set:{before}"
    if kind in ("health-timer", "lost-writes", "canary", "retain", "bootkey", "readonly"):
        return f"{kind}:{before}"
    return None


def apply_choices(choices_list, save=True):
    """Apply each choice in turn. One that fails leaves the others to go ahead. Returns the record."""
    entries, said = [], []
    for choice in choices_list or []:
        if not isinstance(choice, str) or not choices.CHOICES_RE.match(choice):
            entries.append(
                {
                    "choice": str(choice)[:120],
                    "before": None,
                    "after": None,
                    "ok": False,
                    "said": M("preset.choice.bad", "The box does not know that setting."),
                }
            )
            continue
        before = current(choice)
        try:
            message = _apply(choice)
            after = current(choice)
            entries.append(
                {
                    "choice": choice,
                    "before": before,
                    "after": after,
                    "ok": True,
                    "said": message,
                }
            )
            if isinstance(message, dict):
                said.append(message)
        except Exception as exc:  # noqa: BLE001 - one setting that cannot be applied leaves the others
            message = getattr(exc, "message", None) or exc
            entries.append(
                {
                    "choice": choice,
                    "before": before,
                    "after": current(choice),
                    "ok": False,
                    "said": message if isinstance(message, dict) else str(message)[:300],
                }
            )
    if save:
        record = load_record()
        record["at"] = time.time()
        record["entries"] = entries
        record.pop("undone_at", None)
        save_record(record)
    return {"entries": entries, "said": said}


def undo():
    """Put back each setting the last application changed, unless it has been changed since."""
    record = load_record()
    if not record.get("entries"):
        return {"restored": 0, "kept": [], "said": []}
    restored, kept, said = 0, [], []
    for entry in reversed(record["entries"]):
        choice = entry.get("choice")
        before, after = entry.get("before"), entry.get("after")
        if not entry.get("ok") or before is None or not isinstance(choice, str):
            continue
        now = current(choice)
        if now != after:
            kept.append({"choice": choice, "now": now, "after": after})
            continue
        inverse = _inverse(choice, before)
        if inverse is None or not choices.CHOICES_RE.match(inverse):
            continue
        try:
            message = _apply(inverse)
            restored += 1
            if isinstance(message, dict):
                said.append(message)
        except Exception as exc:  # noqa: BLE001 - one that cannot be put back leaves the others
            kept.append({"choice": choice, "now": now, "after": after, "problem": str(exc)[:200]})
    record["undone_at"] = time.time()
    record["restored"] = restored
    record["kept"] = kept
    save_record(record)
    return {"restored": restored, "kept": kept, "said": said}


def _preset(name, kind):
    from irate_box import presets

    preset = presets.get(name)
    if not preset or preset.get("kind") != kind:
        raise Refused(M("preset.unknown", "There is no {kind} preset called {name}.", kind=kind, name=name))
    return preset


def apply_named(purpose=None, durability=None, save=True):
    """The preset's root choices, resolved here from presets.d/ with this box's own facts. The hub names a
    preset; it never sends the list of choices root applies."""
    from irate_box import presets

    entries = []
    if purpose:
        entries.extend(_preset(purpose, "purpose").get("entries") or [])
    if durability:
        entries.extend(_preset(durability, "durability").get("entries") or [])
    fitted, _left = presets.resolve(entries, _facts())
    return apply_choices([e["choice"] for e in fitted if e.get("type") == "choice"], save=save)


def _facts():
    """The little the installer can know about this box before the hub runs."""
    from irate_box.root import health

    try:
        memory = 0
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                memory = int(line.split()[1]) // 1024
                break
    except (OSError, ValueError):
        memory = 0
    import platform
    import shutil

    try:
        free = shutil.disk_usage(STATE).free // (1 << 20)
    except OSError:
        free = 0
    return {
        "arch": platform.machine(),
        "memory_mb": memory,
        "free_mb": free,
        "flash_free": health._nand()[0] in ("free", "on"),
    }


def apply_durability(name):
    """A durability preset applied at a first install: root choices, and the hub's card_writes setting."""
    from irate_box import presets

    preset = _preset(name, "durability")
    fitted, _left = presets.resolve(preset.get("entries"), _facts())
    got = apply_named(durability=name)
    # The hub's own setting, written where the hub reads it before it starts.
    patch = {e["key"]: e["value"] for e in fitted if e.get("type") == "hub" and isinstance(e.get("key"), str)}
    if patch:
        path = STATE / "settings.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.update(patch)
        safeio.write(path, json.dumps(data), mode=0o600)
    said = got["said"]
    if patch:
        said.append(M("preset.card-writes", "The card write level is now {value}.", value=patch.get("card_writes")))
    return sentences(*said) if said else M("preset.applied", "The preset is applied.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "durability":
        print(apply_durability(sys.argv[2]))
    elif len(sys.argv) == 2 and sys.argv[1] == "undo":
        print(undo())
    else:
        sys.exit("usage: presets.py durability longevity|balanced|full | undo")
