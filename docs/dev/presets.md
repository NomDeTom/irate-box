<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Quick presets

Two independent dials, both starting points rather than modes:

| Dial | Values | Chooses |
| --- | --- | --- |
| Purpose | `pirate`, `curated`, `power` | apps, accounts, access, building limits and the update pattern |
| Durability | `longevity`, `balanced`, `full` | how hard the box writes to its card, and what a power cut can lose |

The page is **System → Quick setup** (`/admin/#presets`). It previews every line, applies each
change through the same path a hand change uses, and keeps one record so that **Undo preset** can
put each setting back unless the administrator has changed it since.

## The data files

`presets.d/<name>.json` is the whole preset. Each entry has a `type` and a `why`; optional
`label` and `needs` fields make the preview and the board-fit check better.

| `type` | Fields | Applied by |
| --- | --- | --- |
| `hub` | `key`, `value` | the hub's own settings (`server.DEFAULT_SETTINGS`) |
| `choice` | `choice` | root, through `health.fix` (`autoupdate-set` uses `security.fix`) |
| `accounts` | `key` (`signup` or `http`), `value` | `accounts.set_settings` |
| `hotspot` | `value` (a mode) | `hotspot.save` |
| `access` | `app`, `mode` | root's `access` request |
| `addon` | `addon`, `on` | root's `addon` request (install.sh with the box's options) |
| `kit` | `kit` | root's `kit-install` request |

`needs` may name `arch` (a list), `memory_mb`, `free_mb` or `flash_free`. An entry that does not
fit is left out with its `why`; an entry may carry an `otherwise` entry to use instead (the
journal is on the onboard flash where one is free, else in memory or on the card synced every five
minutes).

A guard, `tests/sim_presets.py`, checks that every entry is a hub setting the server keeps, a
choice `choices.CHOICE_RE` accepts, an add-on `install.sh` can add, or a toolkit this box ships.
The hub names the preset to root; root resolves it from `presets.d/` with its own facts, and
checks every choice again before it acts.

## The record and Undo

Root writes `$ROOT_STATE/presets/last.json` with each choice's before and after. The hub writes
`$HUB_STATE_DIR/preset-applied.json` with the whole application, the requests root is running, and
the selected purpose and durability. Undo puts back a setting only while it still has the value
the preset gave it; a setting the administrator changed since is named and left alone. Removing an
add-on is never part of a preset: moving down switches its access off.

`full` logging writes `returns_at` (24 hours), and the hub's background loop applies only the
durability the box had before it, so a debug level is not left on by mistake and the day's purpose
choices and add-ons are left alone.

## Install and first use

`install.sh --profile pirate|curated|power --durability longevity|balanced|full` records both in
`install-options`. At a first install, `--durability` is applied through
`python3 -m irate_box.root.presets durability NAME`; the purpose is offered on Quick setup after
the administrator account exists, preselected from the recorded `--profile`.
