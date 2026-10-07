# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The toolkits, read the same way by the hub (library/toolkits.py) and by root (root/kits.py).
Here, not in root/, because the hub cannot read root's code.

  shipped              toolkits/<id>.json in the hub's code
  the owner's own      <kits root>/owner/<id>.json, written only by root (kits.define), after
                       checking every package name against the box's package lists
  extra tools          <kits root>/owner/extras.json, {shipped kit: [package, ...]}, likewise:
                       added to that kit's packages (Tom, 2026-10-06: "the toolkit may want an
                       extra tool to be tracked")"""
import json
import os
import re
from pathlib import Path

DEFS = Path(os.environ.get("HUB_KITS_DEFS", Path(__file__).resolve().parents[2] / "toolkits"))
OWNER = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits")) / "owner"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,62}$")
# PyPI's own name rule (PEP 508), for a kit's "pip" list (toolkits-plan §5: the build kit's wheelhouse).
PIP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


# Packages a kit marks "needs_64bit" (the debug kit's bpftrace, bcc and bpftool: BPF programs that
# a 32-bit ARM kernel will not load; tried on the Lyra, 2026-10-06) are left out on other boards.
ARCH_64 = {"amd64", "arm64", "riscv64", "ppc64el", "s390x", "mips64el", "loong64"}


def packages_for(kit, arch):
    """(the kit's packages for a board of this dpkg architecture, those left out and why)."""
    skip = set() if arch in ARCH_64 else set(kit.get("needs_64bit", [])) & set(kit["packages"])
    return [p for p in kit["packages"] if p not in skip], sorted(skip)


def _ok(k, stem):
    return (isinstance(k, dict) and ID_RE.match(str(k.get("id", ""))) and k["id"] == stem
            and isinstance(k.get("packages"), list) and k["packages"] and all(isinstance(p, str) and PKG_RE.match(p) for p in k["packages"])
            and isinstance(k.get("needs_64bit", []), list) and all(p in k["packages"] for p in k.get("needs_64bit", []))
            and isinstance(k.get("pip", []), list) and all(isinstance(p, str) and PIP_RE.match(p) for p in k.get("pip", [])))


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def shipped():
    """{id: kit} from the hub's code; anything malformed is left out."""
    out = {}
    for f in sorted(DEFS.glob("*.json")):
        k = _load(f)
        if _ok(k, f.stem):
            out[k["id"]] = k
    return out


def definitions():
    """Every kit: the shipped ones (with any extra tools added), then the owner's own."""
    out = shipped()
    extras = _load(OWNER / "extras.json") or {}
    for kid, pkgs in extras.items() if isinstance(extras, dict) else ():
        if kid in out and isinstance(pkgs, list) and all(isinstance(p, str) and PKG_RE.match(p) for p in pkgs):
            add = [p for p in pkgs if p not in out[kid]["packages"]]
            out[kid] = dict(out[kid], packages=out[kid]["packages"] + add, extra=add)
    for f in sorted(OWNER.glob("*.json")) if OWNER.is_dir() else []:
        k = _load(f)
        if f.stem != "extras" and f.stem not in out and _ok(k, f.stem):
            out[k["id"]] = dict(k, owner=True)
    return out
