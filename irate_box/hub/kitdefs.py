# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The shipped toolkits (toolkits/<id>.json), read the same way by the hub (library/toolkits.py)
and by root (root/kits.py). Here, not in root/, because the hub cannot read root's code."""
import json
import os
import re
from pathlib import Path

DEFS = Path(os.environ.get("HUB_KITS_DEFS", Path(__file__).resolve().parents[2] / "toolkits"))
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,62}$")


def definitions():
    """{id: kit}, from the shipped toolkit files; anything malformed is left out."""
    out = {}
    for f in sorted(DEFS.glob("*.json")):
        try:
            k = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if (isinstance(k, dict) and ID_RE.match(str(k.get("id", ""))) and k["id"] == f.stem
                and isinstance(k.get("packages"), list) and k["packages"] and all(isinstance(p, str) and PKG_RE.match(p) for p in k["packages"])):
            out[k["id"]] = k
    return out
