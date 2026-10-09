# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Services the add-ons declare: an apps.d manifest's "network" block, for a service that answers the
network itself rather than through the web server. The firewall's floor, the Security page and both
doctors read these, so a new service needs a manifest, not an edit in each of them (SERVICES.md)."""

import os
from pathlib import Path

from irate_box.hub import manifests


def declared(found=None):
    """[service]: each manifest's network block with its name and unit, ready to use. found: manifests
    already loaded (else apps.d is read; a broken one leaves the list empty rather than failing a check)."""
    try:
        found = manifests.load() if found is None else found
    except (manifests.ManifestError, OSError):
        return []
    out = []
    for m in found:
        net, st = m.get("network"), m.get("status") or {}
        if not net:
            continue
        out.append({"id": m["id"], "name": st.get("name") or (m.get("tile") or {}).get("name") or m["id"],
                    "unit": st.get("unit"), "listen": [(l["proto"], l["port"]) for l in net["listen"]],
                    "present": net["present"], "hotspot": net.get("hotspot", "closed"),
                    "risk": net.get("risk", "warn"), "says": net["says"], "option": (m.get("addon") or {}).get("option")})
    return out


PRESENT_ROOT = Path(os.environ.get("HUB_PRESENT_ROOT", "/"))   # where present paths are looked for (tests move it)


def installed(s):
    """Is the service on this box: its present file is there."""
    return (PRESENT_ROOT / s["present"].lstrip("/")).exists()


def by_id(sid, found=None):
    return next((s for s in declared(found) if s["id"] == sid), None)


def by_port(proto, port, found=None):
    return next((s for s in declared(found) if (proto, port) in s["listen"]), None)


def by_unit(unit, found=None):
    return next((s for s in declared(found) if unit and s["unit"] == unit), None)
