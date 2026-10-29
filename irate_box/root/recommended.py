# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The recommended settings for writes and recovery (notes: recommended-defaults): what a box is given at its first
install, and what Box doctor offers an existing box, one change at a time listed, never an owner's own choice.

  apt's cache            in memory          (Debian rewrites ~90 MB of it on nearly every apt run)
  package descriptions   not downloaded     (every apt cache and list smaller)
  system journal         on the onboard flash where a free (blank) one is found, else in memory
  write-back delay       longer             (repeated rewrites of a block merge in memory)
  reboot after a panic   on                 (an unattended box comes back)
  hardware watchdog      on                 (a frozen box restarts itself)
  Box doctor's timer     on                 (a down service is found without anyone on /admin)
  lost writes at boot    on                 (a card that drops its newest writes is named at once)
  UBIFS data checks      on                 (a damaged read from the onboard flash is an error)

The SD card writes level (gentle) is the hub's own setting: its default for new installs is in hub/server.py, and an
existing box is pointed to it. HTTPS stays as the installer sets it: on, administration over HTTPS only.

An owner's choice, made on the Recovery page or as a Box doctor repair (health.fix), is recorded in
/etc/hub/chosen.json and is never offered back. Stdlib only.
"""

import json
import os
import sys
from pathlib import Path

from irate_box.msg import M, render
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
CHOSEN = ETC / "chosen.json"
LEVEL = "gentle"


def chosen():
    try:
        got = json.loads(CHOSEN.read_text())
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def record(key, value):
    """An owner's own choice, kept so it is not offered back (health.fix, for card-set and crash watch)."""
    mine = chosen()
    mine[key] = value
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(CHOSEN, json.dumps(mine, indent=1, sort_keys=True) + "\n")


def _items():
    """[(key, label, recommended, current, apply)] for the settings root keeps."""
    from irate_box.root import crashwatch, health

    groups = {g["key"]: g for g in health.card_groups()}
    flash_free = health._nand()[0] in ("free", "on")
    out = []
    for key, value, label in (
        ("apt_cache", "memory", M("rec.apt-cache", "apt's cache in memory")),
        ("apt_languages", "none", M("rec.apt-languages", "package descriptions not downloaded")),
        (
            "journal",
            "flash" if flash_free else "memory",
            M("rec.journal-flash", "the system journal on the onboard flash")
            if flash_free
            else M("rec.journal-memory", "the system journal in memory"),
        ),
        ("writeback", "longer", M("rec.writeback", "the longer write-back delay")),
        ("flash_crc", "on", M("rec.flash-crc", "the onboard flash's data checked on read")),
    ):
        g = groups.get(key)
        if g is None or value not in [o["value"] for o in g["options"]]:
            continue
        out.append((f"card-set:{key}", label, value, g["current"], lambda k=key, v=value: health.card_set(k, v)))
    s = crashwatch.load_settings()
    for key, label in (
        ("panic", M("rec.panic", "a reboot after a kernel panic")),
        ("watchdog", M("rec.watchdog", "the hardware watchdog")),
    ):
        out.append(
            (
                f"crashwatch-{key}",
                label,
                "on",
                "on" if s.get(key) else "off",
                lambda k=key: crashwatch.set_option(k, "on"),
            )
        )
    for key, label, now, apply in (
        (
            "health-timer",
            M("rec.health-timer", "the Box doctor's own checks, every 6 hours"),
            health.timer_on(),
            lambda: health.set_timer(True),
        ),
        (
            "lost-writes",
            M("rec.lost-writes", "the check for lost writes at every boot"),
            health.lostwrites_on(),
            lambda: health.set_lostwrites(True),
        ),
    ):
        out.append((key, label, "on", "on" if now else "off", apply))
    if not health.records_on_card():
        out.append(
            (
                "canary",
                M("rec.canary", "a test file for lost writes"),
                "off",
                "on" if health.canary_installed() else "off",
                lambda: health.set_canary(False),
            )
        )
    return out


def differences():
    """[(key, label, recommended, current)] where the box differs and the owner has not chosen otherwise."""
    mine = chosen()
    return [(k, label, want, now) for k, label, want, now, _ in _items() if now != want and k not in mine]


def apply_all():
    """Every difference applied (not recorded as the owner's choice). The messages of what was done."""
    mine = chosen()
    said = []
    for key, _, want, now, apply in _items():
        if now == want or key in mine:
            continue
        try:
            said.append(apply())
        except Exception as exc:  # noqa: BLE001 - one setting that cannot be applied leaves the others
            said.append(M("rec.failed", "{key} not set: {why}", key=key, why=str(getattr(exc, "message", exc))))
    return said


def level_differs():
    """Whether the hub's SD card writes level is not the recommended one (and not an owner's choice)."""
    from irate_box.root import crashwatch

    return crashwatch.card_writes() != LEVEL and "card_writes" not in chosen()


if __name__ == "__main__" and sys.argv[1:] == ["fresh"]:
    # The installer, at a first install: the recommended settings, said a line each.
    for m in apply_all():
        print(render(m) if isinstance(m, dict) else m)
