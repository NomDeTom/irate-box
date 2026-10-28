<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The tests

| Tier | Where | Run it with | In CI |
|---|---|---|---|
| Python simulations and guards | `tests/sim_*.py`, `tests/*_guard.py`, `tests/api_*.py` | `tests/run.sh` (all), or `python3 tests/<name>.py` (one) | Yes |
| Pages | `tests/jsdom/dom-*.cjs` | `tests/jsdom/run.sh` (all), or `tests/jsdom/run.sh <name>` (those whose names contain it) | Yes |
| Browser | `tests/browser/` | `tests/local-ci.sh` (needs Docker), or `tests/browser/run.sh USER@BOX` against a box | No |
| Container | `tests/container/ct-*.sh` | `tests/container/run.sh [scenario]` (needs Docker with systemd) | No |

**Before you push:** `tests/local-ci.sh --quick` runs what CI runs (REUSE, `tests/run.sh`, the shell scripts' parse, the pages in jsdom), and the browser pages. Without `--quick` it also builds the `.deb`.

## Notes

- Run the tests with umask 022. `tests/local-ci.sh` sets it. A shell with umask 0002 makes some file-permission checks fail.
- Each test makes its own temporary files and starts its own hub on a free port, if it needs one.
- The page tests run in jsdom, which is not in the repository. `tests/jsdom/run.sh` installs the version CI uses the first time, in `~/.cache/irate-box/` (npm, a few seconds; no Docker, no root). To use another, set `JSDOM=/path/to/node_modules/jsdom`.
- Some page tests need a running hub. `tests/jsdom/run.sh` starts one for them on a free port.
- Every module run through `./irate-box` has `faulthandler` on: a crash in C prints each thread's stack to stderr (the journal, for a service), and so does SIGUSR1, without stopping it. To see what a hung service is doing: `sudo systemctl kill -s USR1 irate-box-uplink.service`, then `journalctl -u irate-box-uplink -n 60`.
- `tests/guide-pending` holds back two guide checks until the guide is updated after the language review. The file says why. Delete it in the commit that updates the guide.

## Adding a test

Copy a test of the same kind and keep its shape: one `check(name, condition, detail)` per thing checked, and a last line `failures: N`. Name each check after what a person would see, not after the code.
