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


# Packages a kit marks "needs_64bit" (the debug kit's bpftrace, bcc and bpftool: BPF programs that
# a 32-bit ARM kernel will not load; tried on the Lyra, 2026-10-06) are left out on other boards.
ARCH_64 = {"amd64", "arm64", "riscv64", "ppc64el", "s390x", "mips64el", "loong64"}


def packages_for(kit, arch):
    """(the kit's packages for a board of this dpkg architecture, those left out and why)."""
    skip = set() if arch in ARCH_64 else set(kit.get("needs_64bit", [])) & set(kit["packages"])
    return [p for p in kit["packages"] if p not in skip], sorted(skip)


def definitions():
    """{id: kit}, from the shipped toolkit files; anything malformed is left out."""
    out = {}
    for f in sorted(DEFS.glob("*.json")):
        try:
            k = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if (isinstance(k, dict) and ID_RE.match(str(k.get("id", ""))) and k["id"] == f.stem
                and isinstance(k.get("packages"), list) and k["packages"] and all(isinstance(p, str) and PKG_RE.match(p) for p in k["packages"])
                and isinstance(k.get("needs_64bit", []), list) and all(p in k["packages"] for p in k.get("needs_64bit", []))):
            out[k["id"]] = k
    return out
