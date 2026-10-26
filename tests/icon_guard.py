# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One icon per tile: every default tile's icon (apps.d/, the catalogue's add-ons and their
template) is one emoji, or svg:<name> of a picture in web/icons/. A picture also needs its line in style.css
(.svg-icon[data-icon="<name>"]), or it draws as a blank square. Tiles that share an icon are listed as a warning:
MQTT and the MQTT explorer both have 📡 until each has one of its own.

python3 tests/icon_guard.py"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests

# One emoji: a flag's two regional letters, or one character with its variation selector, skin tone and any
# zero-width-joined parts (server.py's FIRST_EMOJI, matched whole).
ONE_EMOJI = re.compile("[\U0001f1e6-\U0001f1ff]{2}|.[️\U0001f3fb-\U0001f3ff]*(?:‍.[️]*)*")
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


files = (
    sorted(REPO.glob("apps.d/*.json"))
    + sorted(REPO.glob("addons/*.json"))
    + sorted(REPO.glob("addons/templates/*.json"))
)
seen = {}
css = (REPO / "web" / "style.css").read_text()
for path in files:
    tile = json.loads(path.read_text()).get("tile") or {}
    icon = tile.get("icon")
    if icon is None:
        continue  # a widget (the live tiles) draws its own
    where = str(path.relative_to(REPO))
    name = manifests.svg_icon(icon)
    if icon.startswith("svg:"):
        check(f"{where}: {icon} is a picture in web/icons/", bool(name))
        check(f"  {where}: style.css draws it", f'.svg-icon[data-icon="{name}"]' in css)
    else:
        check(
            f"{where}: one emoji ({icon})",
            bool(ONE_EMOJI.fullmatch(icon)) and ord(icon[0]) > 0x7F,
            [hex(ord(c)) for c in icon],
        )
    seen.setdefault(icon.replace("️", ""), []).append(where)
shared = {icon: where for icon, where in seen.items() if len(where) > 1}
for icon, where in shared.items():
    print(f"WARN tiles sharing {icon}: {', '.join(where)}")
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
