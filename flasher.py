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
                                             index.json); an empty list until it keeps any
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
            return 200, (FIRMWARE / "index.json").read_bytes()
        except OSError:
            return 200, json.dumps(EMPTY_LIST).encode()
    # Pull-request builds and anything else the hosted API has: not offline.
    return 404, b'{"error": "not available on this hub"}'
