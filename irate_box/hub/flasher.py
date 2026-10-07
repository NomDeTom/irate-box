# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Meshtastic web flasher's side of the hub (plan §5, web-flasher stage 3).

The flasher is a download, not a page served in place: it flashes over Web Serial, which needs a
secure context, and a page opened from the guest's own disk is one where the hub's plain HTTP is
not. The fork's bundle (NomDeTom/web-flasher, irate-box-bundle.yml) is flasher.html, the whole
app in one file, plus public/: event artwork, device photos and the bundled data files. Every
address in flasher.html is __IRATE_BOX_ORIGIN__/flasher/..., filled in here with the address the
guest downloaded it from, so one file is right on whichever network it was fetched.

Opened from disk, the page has origin "null", so everything it fetches from the hub is
cross-origin: every answer carries Access-Control-Allow-Origin: *, and the API answers the
preflight (OPTIONS) that two of its requests send. The web server serves public/ and the firmware
mirror itself, with the same headers (irate-box.nginx, the Caddyfile); this module does the
page and the API:

  GET /flasher/flasher.html                  the page, as an attachment, address filled in
  GET /flasher/api/resource/deviceHardware   the bundle's data/hardware-list.json
  GET /flasher/api/resource/eventFirmware    the bundle's data/event_firmware.json
  GET /flasher/api/github/firmware/list      what the librarian keeps (firmware.py writes
                                             index.json), and first, what the owner published
                                             from the Firmware Factory (factory.py publish:
                                             <version>-built/ folders), as alphas; an empty list
                                             until there is any
  OPTIONS /flasher/api/...                   the CORS preflight

Stdlib only.
"""

import json
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("HUB_FLASHER_ROOT", "/usr/share/hub/apps/flasher"))
FIRMWARE = Path(os.environ.get("HUB_FIRMWARE_ROOT", "/var/lib/hub/firmware"))
PLACEHOLDER = b"__IRATE_BOX_ORIGIN__"
# A Host header fit to go into a URL: a name or IPv4 address, or a bracketed IPv6 one, and a port.
HOST_RE = re.compile(r"^(?:[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?$")
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    # Chrome asks before a page from a less private place (one opened from disk counts) reaches
    # a private address such as the hub's.
    "Access-Control-Allow-Private-Network": "true",
    "Access-Control-Max-Age": "86400",
}
EMPTY_LIST = {"releases": {"stable": [], "alpha": []}, "pullRequests": []}
API_FILES = {
    "/flasher/api/resource/deviceHardware": "public/data/hardware-list.json",
    "/flasher/api/resource/eventFirmware": "public/data/event_firmware.json",
}


BUILT = "-built"   # factory.py's folders: <version>-built/firmware-<version>-built.json


def _built():
    """The releases built here, newest first: [{version, targets}] (factory.published, read here
    without the factory's imports)."""
    out = []
    for folder in FIRMWARE.glob("*" + BUILT) if FIRMWARE.is_dir() else []:
        try:
            targets = json.loads((folder / f"firmware-{folder.name}.json").read_text()).get("targets") or []
        except (OSError, ValueError):
            continue
        if targets and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,40}" + BUILT, folder.name):
            out.append((folder.stat().st_mtime, {"version": folder.name, "targets": targets}))
    return [r for _, r in sorted(out, key=lambda x: x[0], reverse=True)]


def installed():
    return (ROOT / "flasher.html").is_file()


def page(host):
    """flasher.html with the hub's address filled in, or None (not installed, or a Host that
    cannot go into a URL)."""
    if not installed() or not HOST_RE.match(host or ""):
        return None
    return (ROOT / "flasher.html").read_bytes().replace(PLACEHOLDER, f"http://{host}".encode())


def api(path):
    """(status, body bytes) for a GET under /flasher/api/."""
    if path in API_FILES:
        f = ROOT / API_FILES[path]
        return (200, f.read_bytes()) if f.is_file() else (404, b'{"error": "the flasher is not installed"}')
    if path == "/flasher/api/github/firmware/list":
        try:
            out = json.loads((FIRMWARE / "index.json").read_text())
        except (OSError, ValueError):
            out = json.loads(json.dumps(EMPTY_LIST))
        built = [{"id": "v" + r["version"], "release_notes": "",
                  "title": f"Meshtastic Firmware {r['version'][:-len(BUILT)]} built on this box "
                           f"({', '.join(t['board'] for t in r['targets'][:4])}{' …' if len(r['targets']) > 4 else ''})"}
                 for r in _built()]
        if built:
            out.setdefault("releases", {}).setdefault("alpha", [])[:0] = built
        return 200, json.dumps(out).encode()
    # Pull-request builds and anything else the hosted API has: not offline.
    return 404, b'{"error": "not available on this hub"}'
