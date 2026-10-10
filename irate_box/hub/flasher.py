# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The web flashers' side of the hub: the light one (ESP Web Tools, in place) and the Meshtastic one (a download).

The light one is the page /flasher/ itself, served in place: over HTTPS it flashes an ESP32 from
the browser (Web Serial needs a secure context), with ESP Web Tools (ESPHome's install button,
Apache-2.0; install.sh puts its bundle in $HUB_EWT_ROOT, served at /flasher/esp-web-tools/). It
offers each ESP32 target the owner published from the Firmware Factory (factory.py publish), each
with a manifest made here from the build's own factory.bin: the chip from its image header, the
offsets of the file system and the OTA loader from the partition table inside it. nRF52 and RP2040
boards take a UF2 dragged onto their USB drive: the Factory's downloads.

  GET /flasher/api/esp                                the targets offered: [{version, env, chip, …}]
  GET /flasher/api/esp/<version>/<env>/manifest.json  one target's manifest, for the install button

The Meshtastic one:

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
EWT = Path(os.environ.get("HUB_EWT_ROOT", "/usr/share/hub/apps/esp-web-tools"))
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


# --- the light flasher: ESP Web Tools' manifests --------------------------------------------------
# An ESP image's header: 0xE9 first, the chip's id at byte 12 (little-endian). The classic ESP32's
# bootloader sits at 0x1000 in a merged image, the later chips' at 0.
CHIPS = {0: "ESP32", 2: "ESP32-S2", 5: "ESP32-C3", 9: "ESP32-S3", 12: "ESP32-C2", 13: "ESP32-C6", 16: "ESP32-H2"}
PART_TABLE = 0x8000


def chip(data):
    """ESP Web Tools' chipFamily for a merged image (factory.bin), or None."""
    for at in (0, 0x1000):
        if len(data) > at + 16 and data[at] == 0xE9:
            return CHIPS.get(int.from_bytes(data[at + 12:at + 14], "little"))
    return None


def partitions(data):
    """The partition table inside a merged image: [{type, subtype, offset, size, label}]."""
    out = []
    for i in range(PART_TABLE, min(len(data), PART_TABLE + 0xC00) - 31, 32):
        e = data[i:i + 32]
        if e[:2] != b"\xaa\x50":
            break
        out.append({"type": e[2], "subtype": e[3], "offset": int.from_bytes(e[4:8], "little"),
                    "size": int.from_bytes(e[8:12], "little"), "label": e[12:28].split(b"\0")[0].decode("ascii", "replace")})
    return out


ESP_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,40}" + "-built$")
ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
OTA_RE = re.compile(r"^(mt-esp32[a-z0-9]*-ota|bleota[a-z0-9-]*)\.bin$")


def _target_files(folder, env):
    """A published target's manifest (.mt.json) and the files it names that are there."""
    try:
        mt = json.loads((folder / f"firmware-{env}-{folder.name}.mt.json").read_text())
    except (OSError, ValueError):
        return None, []
    names = [str(f.get("name", "")) for f in mt.get("files") or []]
    return mt, [n for n in names if n and "/" not in n and (folder / n).is_file()]


def esp_manifest(version, env):
    """ESP Web Tools' manifest for one published ESP32 target, or None: factory.bin at 0, then the OTA
    loader at the second app partition and the file system at the data one, where the build has them
    and they fit."""
    if not ESP_VERSION_RE.match(version or "") or not ENV_RE.match(env or ""):
        return None
    folder = FIRMWARE / version
    mt, names = _target_files(folder, env)
    factory = next((n for n in names if n.endswith(".factory.bin")), None)
    if not mt or not factory:
        return None
    data = (folder / factory).read_bytes()
    fam = chip(data)
    if not fam:
        return None
    table = partitions(data)
    parts = [{"path": f"/flasher/firmware/{version}/{factory}", "offset": 0}]
    ota = next((p for p in table if p["type"] == 0 and p["subtype"] == 0x11), None)
    fs = next((p for p in table if p["type"] == 1 and p["subtype"] in (0x82, 0x83)), None)
    for name in names:
        where = ota if OTA_RE.match(name) else fs if name.startswith("littlefs-") and name.endswith(".bin") else None
        if where and (folder / name).stat().st_size <= where["size"]:
            parts.append({"path": f"/flasher/firmware/{version}/{name}", "offset": where["offset"]})
    return {"name": f"Meshtastic for {mt.get('display_name') or env}", "version": version[:-len(BUILT)],
            "new_install_prompt_erase": True, "new_install_improv_wait_time": 0,
            "builds": [{"chipFamily": fam, "parts": parts}]}


def esp_targets():
    """The ESP32 targets offered on the page, newest release first: [{version, env, name, chip, parts}]."""
    out = []
    for r in _built():
        for t in r["targets"]:
            if not str(t.get("platform") or "").startswith("esp32"):
                continue
            m = esp_manifest(r["version"], t.get("board"))
            if m:
                out.append({"version": r["version"], "env": t["board"], "name": m["name"], "chip": m["builds"][0]["chipFamily"],
                            "parts": len(m["builds"][0]["parts"])})
    return out


def esp_ready():
    return (EWT / "install-button.js").is_file()


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
    if path == "/flasher/api/esp":
        return 200, json.dumps({"targets": esp_targets(), "engine": esp_ready(), "download": installed()}).encode()
    m = re.fullmatch(r"/flasher/api/esp/([^/]+)/([^/]+)/manifest\.json", path)
    if m:
        man = esp_manifest(m.group(1), m.group(2))
        return (200, json.dumps(man).encode()) if man else (404, b'{"error": "no such ESP32 build here"}')
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
