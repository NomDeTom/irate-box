# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Visits from the internet, for the security doctor (if someone forwards a router port to the box, its pages and login forms are open to anyone).

The box is for its own networks: its hotspot, the LAN it joins, and Tailscale's (100.64.0.0/10,
which is not a public address). A request from a public address means the box can be reached from
the internet. The hub notes the network it came from (a /24, or a /48 for IPv6: never the whole
address), when it was first and last seen, and how many requests, in $STATE/public-visits.json,
written at most once a minute per network. The web servers keep no access logs, so this sees what
reaches the hub (every page it serves, the home page included), not static files alone.
Stdlib only."""

import ipaddress
import json
import os
import threading
import time
from pathlib import Path

STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE_DIR / "public-visits.json"
KEEP = 20            # networks kept, the most recent
WRITE_EVERY = 60     # seconds between writes for one network
_lock = threading.Lock()
_written = {}        # network -> when last written
_seen = None         # the file's contents, once read


def public_net(addr):
    """The /24 (IPv4) or /48 (IPv6) of a public address, or None for anything else (private,
    loopback, link-local, Tailscale's shared range, or not an address at all)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        return None
    return str(ipaddress.ip_network(f"{ip}/{24 if ip.version == 4 else 48}", strict=False))


def _load():
    try:
        d = json.loads(FILE.read_text())
        return d if isinstance(d, dict) and isinstance(d.get("nets"), dict) else {"nets": {}}
    except (OSError, ValueError):
        return {"nets": {}}


def note(addr, now=None):
    """A request from addr: noted if it is public. Never raises (a visit is never refused for this)."""
    net = public_net(addr)
    if net is None:
        return
    global _seen
    now = time.time() if now is None else now
    try:
        with _lock:
            if _seen is None:
                _seen = _load()
            e = _seen["nets"].setdefault(net, {"first": now, "last": now, "count": 0})
            e["last"], e["count"] = now, e.get("count", 0) + 1
            if now - _written.get(net, 0) < WRITE_EVERY:
                return
            _written[net] = now
            nets = dict(sorted(_seen["nets"].items(), key=lambda kv: kv[1]["last"], reverse=True)[:KEEP])
            _seen["nets"] = nets
            tmp = FILE.with_name(f".{FILE.name}.tmp")
            tmp.write_text(json.dumps(_seen))
            os.replace(tmp, FILE)
    except OSError:
        pass
