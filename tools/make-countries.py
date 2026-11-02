#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Write irate_box/hub/countries.json, the small country list the hub ships, from Debian's iso-codes.

Each ISO 3166-1 country keeps its two-letter code and one English name: iso-codes' common name where it has one
("South Korea", "Bolivia"), else its name ("United Kingdom"). Nothing else: the hub needs a name to show and a
code to set. Run it again when iso-codes has a newer release, on a machine that has the package:

  python3 tools/make-countries.py [/usr/share/iso-codes/json/iso_3166-1.json]
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/iso-codes/json/iso_3166-1.json")
OUT = REPO / "irate_box" / "hub" / "countries.json"

entries = json.loads(SOURCE.read_text(encoding="utf-8"))["3166-1"]
countries = {e["alpha_2"]: e.get("common_name") or e["name"] for e in entries}
try:
    version = subprocess.run(
        ["dpkg-query", "-W", "-f", "${Version}", "iso-codes"], capture_output=True, text=True, check=True
    ).stdout.strip()
except (OSError, subprocess.CalledProcessError):
    version = "unknown"
data = {
    "source": f"Debian iso-codes {version}, iso_3166-1.json: the common name where there is one, else the name",
    "countries": dict(sorted(countries.items())),
}
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"{len(countries)} countries written to {OUT.relative_to(REPO)}")
