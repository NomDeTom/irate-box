<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The tests

| Tier | Where | Run it with | In CI |
|---|---|---|---|
| Python simulations and guards | `tests/sim_*.py`, `tests/*_guard.py`, `tests/api_*.py` | `tests/run.sh` (all), or `python3 tests/<name>.py` (one) | Yes |
| Pages | `tests/jsdom/dom-*.cjs` | `node tests/jsdom/<name>.cjs`, with jsdom on `NODE_PATH` | No |
| Browser | `tests/browser/` | `tests/local-ci.sh` (needs Docker), or `tests/browser/run.sh USER@BOX` against a box | No |
| Container | `tests/container/ct-*.sh` | `tests/container/run.sh [scenario]` (needs Docker with systemd) | No |

**Before you push:** `tests/local-ci.sh --quick` runs what CI runs (REUSE, `tests/run.sh`, the shell scripts' parse), and the browser pages. Without `--quick` it also builds the `.deb`.

## Notes

- Run the tests with umask 022. `tests/local-ci.sh` sets it. A shell with umask 0002 makes some file-permission checks fail.
- Each test makes its own temporary files and starts its own hub on a free port, if it needs one.
- Four page tests need a running hub: `dom-appbar`, `dom-drop`, `dom-locks` and `dom-themes`. Start one with `HUB_STATE_DIR=$(mktemp -d) PORT=18099 HUB_BIND=127.0.0.1 ./irate-box server`, then run the test with `BASE=http://127.0.0.1:18099`.
- `dom-themes` fails three checks on `main` too: the theme menu list, and "Could not parse CSS stylesheet" twice. This is a known problem.
- `tests/guide-pending` holds back two guide checks until the guide is updated after the language review. The file says why. Delete it in the commit that updates the guide.

## Adding a test

Copy a test of the same kind and keep its shape: one `check(name, condition, detail)` per thing checked, and a last line `failures: N`. Name each check after what a person would see, not after the code.
