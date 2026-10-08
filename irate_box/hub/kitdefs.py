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


# Packages a kit gives "only_where" a condition (the System kit, os-image-plan §2: each package only
# where it fits) are left out where the board doesn't meet it. Read from the board itself; the paths
# can be moved for the tests (HUB_SYSFS, HUB_ROOTFS).
SYSFS = Path(os.environ.get("HUB_SYSFS", "/sys"))
ROOTFS = Path(os.environ.get("HUB_ROOTFS", "/"))
CONDITIONS = {
    # PCI devices to list (pciutils): the Lyra has none.
    "pci": lambda: any((SYSFS / "bus/pci/devices").glob("*")),
    # A disk that may report SMART (smartmontools): SATA, SCSI, USB disks and NVMe, not SD cards.
    "smart-disk": lambda: any((SYSFS / "block").glob("sd*")) or any((SYSFS / "block").glob("nvme*")),
    # A kernel with AppArmor (apparmor and its tools do nothing without it).
    "apparmor": lambda: (SYSFS / "kernel/security/apparmor").is_dir(),
    # Debian's kernel defaults not already in place: newer Armbian board packages ship the same file
    # and conflict with linux-sysctl-defaults.
    "no-sysctl-defaults": lambda: not (ROOTFS / "usr/lib/sysctl.d/50-default.conf").exists(),
}


def packages_for(kit, arch, fetching=False):
    """(the kit's packages for a board of this dpkg architecture, those left out and why). Fetching,
    its "alternatives" too: cached with it, installed only by the owner's choice, never with the kit."""
    names = kit["packages"] + (kit.get("alternatives", []) if fetching else [])
    skip = set() if arch in ARCH_64 else set(kit.get("needs_64bit", [])) & set(names)
    skip |= {p for p, cond in kit.get("only_where", {}).items() if p in names and not CONDITIONS[cond]()}
    return [p for p in names if p not in skip], sorted(skip)


WHY = {"pci": "no PCI", "smart-disk": "no disk that reports SMART", "apparmor": "no AppArmor in the kernel",
       "no-sysctl-defaults": "already in place"}


def why_left_out(kit, left):
    """The left-out packages grouped by why, as one phrase: "gdb (64-bit only); pciutils (no PCI)"."""
    by = {}
    for p in left:
        cond = kit.get("only_where", {}).get(p)
        by.setdefault(WHY[cond] if cond else "64-bit only", []).append(p)
    return "; ".join(f"{', '.join(ps)} ({why})" for why, ps in by.items())


def _ok(k, stem):
    return (isinstance(k, dict) and ID_RE.match(str(k.get("id", ""))) and k["id"] == stem
            and isinstance(k.get("packages"), list) and k["packages"] and all(isinstance(p, str) and PKG_RE.match(p) for p in k["packages"])
            and isinstance(k.get("needs_64bit", []), list) and all(p in k["packages"] for p in k.get("needs_64bit", []))
            and isinstance(k.get("pip", []), list) and all(isinstance(p, str) and PIP_RE.match(p) for p in k.get("pip", []))
            and isinstance(k.get("alternatives", []), list) and all(isinstance(p, str) and PKG_RE.match(p) and p not in k["packages"] for p in k.get("alternatives", []))
            and isinstance(k.get("only_where", {}), dict) and all(p in k["packages"] and c in CONDITIONS for p, c in k.get("only_where", {}).items()))


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
