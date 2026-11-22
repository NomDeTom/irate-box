# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The pin watch: SilverBullet 2.11.1 against its real 2.11.1-2.12.1 release notes (2.12.1 is flagged), ttyd 1.7.7
as the latest (nothing), and one issue per flagged pin, updated rather than duplicated and closed when it catches
up. python3 tests/sim_pin_advisories.py"""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests/fixtures/pin-advisories"
spec = importlib.util.spec_from_file_location("pin_advisories", REPO / "scripts/pin-advisories.py")
pins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pins)

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def releases(name):
    return json.loads((FIX / f"{name}-releases.json").read_text())


read = {pin["name"]: pin for pin in pins.read_pins(REPO)}
check(
    "the pins are read from the files that hold them",
    read["SilverBullet"]["version"] == "2.11.1"
    and read["ttyd"]["version"] == "1.7.7"
    and read["ESP Web Tools"]["version"] == "10.4.0"
    and read["jsdom"]["version"] == "22.1.0",
    {name: pin["version"] for name, pin in read.items()},
)

silverbullet = pins.assessment(read["SilverBullet"], releases("silverbullet"))
check(
    "SilverBullet 2.11.1: behind 2.12.1, flagged by its release notes",
    silverbullet["behind"]
    and silverbullet["latest"] == "2.12.1"
    and silverbullet["flagged"]
    and [r["tag"] for r in silverbullet["releases"]] == ["2.12.1"],
    silverbullet,
)
check(
    "  2.12.1 is the release that says 'Security', and 2.12.0 changes nothing",
    any("dot-prefixed" in line for line in silverbullet["releases"][0]["lines"])
    and "2.12.0" not in {r["tag"] for r in silverbullet["releases"]},
    silverbullet["releases"],
)

ttyd = pins.assessment(read["ttyd"], releases("ttyd"))
check(
    "ttyd 1.7.7: the latest, so nothing to flag",
    not ttyd["behind"] and not ttyd["flagged"] and ttyd["latest"] == "1.7.7",
    ttyd,
)
check(
    "  a pin that is behind with no security wording is not flagged",
    not pins.assessment(dict(read["ttyd"], version="1.7.6"), releases("ttyd"))["flagged"],
)

body = pins.issue_body(read["SilverBullet"], silverbullet)
check(
    "the issue names the tool and both versions, with the matching line and its link",
    "SilverBullet 2.11.1" in pins.issue_title(read["SilverBullet"], silverbullet)
    and "2.12.1" in pins.issue_title(read["SilverBullet"], silverbullet)
    and "dot-prefixed" in body
    and "https://github.com/silverbulletmd/silverbullet/releases/tag/2.12.1" in body,
    body,
)

# One issue per pin: a second run updates it; a run whose pin has caught up closes it.
calls = []


def fake_github(path, token=None, body=None, method=None):
    calls.append((method or "GET", path, body))
    if path.endswith("/issues?state=open&labels=pin-advisory&per_page=100"):
        return [
            {"number": 7, "body": pins.marker(read["SilverBullet"]) + "\nold body\n"},
            {"number": 9, "body": pins.marker(read["ttyd"]) + "\nold body\n"},
        ]
    if method == "POST":
        return {"number": 8}
    return body or {}


original = pins.github
pins.github = fake_github
try:
    pins.sync("o/r", "t", [read["SilverBullet"]], lambda pin, token: silverbullet)
    check(
        "a later run updates the open issue, it does not open another",
        any(method == "PATCH" and path.endswith("/issues/7") for method, path, _ in calls)
        and not any(method == "POST" for method, _, _ in calls),
        calls,
    )
    calls.clear()
    pins.sync("o/r", "t", [read["ttyd"]], lambda pin, token: ttyd)
    closed = [body for method, _, body in calls if method == "PATCH" and (body or {}).get("state") == "closed"]
    check("a pin that is no longer flagged closes its issue", bool(closed), calls)
finally:
    pins.github = original

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
