#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Irate-Box hub server: shoutbox, board, blob store, status, captive-portal target.

Serves web/ itself so a bare `./irate-box server` works; behind the web server
(nginx, or Caddy) the assets come off disk and only `/` and the API reach this process."""

import io
import ipaddress
import json
import os
import re
import hashlib
import hmac
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import html
from irate_box import confine
from irate_box.hub import access
from irate_box.hub import accounts
from irate_box.hub import board
from irate_box.hub import ci
from irate_box.library import firmware
from irate_box.hub import factory
from irate_box.hub import flasher
from irate_box.hub import gitrepos
from irate_box.hub import hotspot
from irate_box.hub import hubclock
from irate_box.hub import linkhistory
from irate_box.library import librarian
from irate_box.hub import manifests
from irate_box.hub import meshbridge
from irate_box.hub import store
from irate_box.hub import svchistory
from irate_box.hub import uplink
from irate_box.library import zimcheck

CHECKOUT = Path(__file__).resolve().parents[2]  # irate_box/hub/server.py → the checkout
STATIC = CHECKOUT / "web"
# Mutable state lives outside the code directory so packaging can point it at
# /var/lib/hub and `apt purge` cannot eat anyone's messages.
STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", CHECKOUT))
DATA_FILE = STATE_DIR / "messages.json"
MAX_MESSAGES = 200
# Shoutbox entries expire against powered-on time, not the wall clock, which does not
# exist on this hardware. See hubclock.py.
SHOUT_TTL = 24 * 3600
PORT = int(os.environ.get("PORT", 8000))
BIND = os.environ.get("HUB_BIND", "0.0.0.0")  # 127.0.0.1 behind the web server
# Where captive-portal probes are sent. Unset, the redirect is relative and lands on
# whatever address the probe arrived at -- fine on localhost, and on the AP every name
# resolves to the hub anyway. The Pi's unit sets the real origin, http://192.168.4.1/,
# so the sign-in sheet shows an address a guest can type again later.
HUB_URL = os.environ.get("HUB_URL", "/")
# The hotspot's own subnet (the AP add-on sets it). A guest from it who arrives under any other
# name -- a site they typed, which every name resolves to there -- is sent to HUB_URL too, so
# the address bar shows the hub's address and the browser keeps one site's storage (names,
# theme) rather than one per name typed. Guests on the owner's LAN are never redirected.
try:
    AP_NET = ipaddress.ip_network(os.environ["HUB_AP_NET"]) if os.environ.get("HUB_AP_NET") else None
except ValueError:
    AP_NET = None
HUB_HOST = urlparse(HUB_URL).hostname if HUB_URL.startswith(("http://", "https://")) else None
# Without HUB_AP_NET and an absolute HUB_URL set, the hotspot's own (root/ap.py: 192.168.4.1/24) are
# used while the root helper says it is up (control/ap.json), and never otherwise: a home network
# that happens to be 192.168.4.0/24 is not the hotspot.
HOTSPOT_NET = ipaddress.ip_network("192.168.4.0/24")
HOTSPOT_URL = "http://192.168.4.1/"
_ap_up = {"mtime": None, "up": False}


def hotspot_up():
    path = STATE_DIR / "control" / "ap.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    if _ap_up["mtime"] != mtime:
        try:
            _ap_up["up"] = bool(json.loads(path.read_text()).get("up"))
        except (OSError, ValueError, AttributeError):
            _ap_up["up"] = False
        _ap_up["mtime"] = mtime
    return _ap_up["up"]


def ap_net():
    return AP_NET or (HOTSPOT_NET if hotspot_up() else None)


def hub_host():
    return HUB_HOST or (urlparse(HOTSPOT_URL).hostname if hotspot_up() else None)

CLOCK = hubclock.HubClock(STATE_DIR / "clock.json")
BOARD = board.Board(STATE_DIR / "board.json", CLOCK)
# Excalidraw's storage API and the saved-work gallery. See store.py.
STORE = store.Store(STATE_DIR / "store", CLOCK)
# The file drop: guests' files for each other, apart from the store's budget. See store.Drop.
DROP = store.Drop(STATE_DIR / "drop", CLOCK)

# Hosts and paths used by OSes to detect captive portals.
# We redirect them to the hub, which triggers the "sign in to network" popup.
CAPTIVE_HOSTS = {
    "captive.apple.com",
    "www.apple.com",
    "connectivitycheck.gstatic.com",
    "clients3.google.com",
    "connectivitycheck.android.com",
    "www.msftconnecttest.com",
    "www.msftncsi.com",
    "detectportal.firefox.com",
    "nmcheck.gnome.org",
}

CAPTIVE_PATHS = {
    "/hotspot-detect.html",       # iOS / macOS
    "/library/test/success.html", # older iOS
    "/generate_204",              # Android
    "/gen_204",                   # Android's fallback probe (www.google.com); a 404 there reads as "no internet"
    "/connecttest.txt",           # Windows
    "/ncsi.txt",                  # Windows (legacy)
    "/canonical.html",            # Firefox
    "/check_network_status.txt",  # GNOME
}

lock = threading.Lock()


def load_messages():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except ValueError:
            return []  # torn write from a power cut
    return []


def live_messages(now):
    """Messages that have not aged out. Reads do not rewrite the file."""
    return [m for m in load_messages() if now - m.get("created", now) <= SHOUT_TTL]


# What sits behind the web server: each app's "status" part in apps.d/ (manifests.py), then the box's
# own pieces, which are not apps. Keyed by the path the tile links to.
#   port   a loopback listener to probe: that is what "running" means to a guest
#   unit   its systemd unit, which says whether it is installed at all
#   active running means the unit is active, for a daemon with no loopback port to probe
#   root   (static apps) the env var naming the directory the web server serves, as
#          install.sh writes it into both configs. Unset -- a dev checkout -- counts as installed.
#   note   what the dashboard says in place of a path
# path None: on the service dashboard only, with no tile of its own.
TAILSCALE_NAME = "Remote access (Tailscale)"
# The web server in front (install.sh --web): nginx, or Caddy, the fallback.
WEB_SERVER = os.environ.get("HUB_WEB_SERVER", "nginx")
WEB_SERVER_NAME = {"nginx": "nginx", "caddy": "Caddy"}.get(WEB_SERVER, WEB_SERVER)
# The built-in manifests and the local add-ons' (manifests.py), which the owner adds and removes
# from /admin while the hub runs: refresh_manifests() reads them again when their folder changes.
MANIFESTS = manifests.load_all()


def _service_entry(status):
    entry = {"path": status.get("path"), "name": status["name"]}
    if "root_env" in status:
        entry["root"] = status["root_env"]
    for key in ("port", "socket", "unit", "note", "local_dir"):
        if key in status:
            entry[key] = status[key]
    return entry


def _services():
    return [_service_entry(m["status"]) for m in MANIFESTS if "status" in m] + [
        {"path": None, "name": TAILSCALE_NAME, "unit": "tailscaled.service",
         "active": True, "note": "switched on and off from /admin"},
        {"path": None, "name": f"Web server ({WEB_SERVER_NAME})", "unit": f"{WEB_SERVER}.service", "proxy": True},
        {"path": None, "name": "Hub server", "unit": "irate-box.service", "self": True},
    ]


SERVICES = _services()
_local_stamp = {"at": None}


def refresh_manifests():
    """Read the manifests again when the local add-ons' folder has changed (the hub writes it
    by renaming into place, which changes the folder's time): the tiles, /status, the list
    pages and the librarian's apps follow."""
    global MANIFESTS, SERVICES, MENU_PAGES
    stamp = []
    for p in [manifests.LOCAL_D, manifests.CATALOGUE, *sorted(manifests.CATALOGUE.glob("*.json"))]:
        try:
            stamp.append(p.stat().st_mtime_ns)
        except OSError:
            stamp.append(None)
    if stamp == _local_stamp["at"]:
        return
    # The catalogue may have changed under added add-ons (a hub update): bring them along first.
    # A copy it rewrites changes the folder again, and the next call finds nothing more to do.
    try:
        catalogue_sync()
    except (OSError, ValueError, manifests.ManifestError) as exc:
        print(f"catalogue sync: {exc}", file=sys.stderr)
    _local_stamp["at"] = [p.stat().st_mtime_ns if p.exists() else None
                          for p in [manifests.LOCAL_D, manifests.CATALOGUE, *sorted(manifests.CATALOGUE.glob("*.json"))]]
    MANIFESTS = manifests.load_all()
    SERVICES = _services()
    MENU_PAGES = {m["tile"]["href"]: m for m in manifests.menus(MANIFESTS).values()}
    librarian.reload_apps()
    _home_page["mtime"] = None


# The hub's live tiles (a manifest names one with "widget"); hub.js and home.js fill them in.
WIDGET_HTML = {
    "people": """      <div class="service-card people-card" id="people-card">
        <span class="icon">👥</span>
        <span class="name" id="people-count">—</span>
        <span class="desc" id="people-desc">Users online</span>
      </div>""",
    "qr": """      <div class="service-card qr-card" id="qr-card">
        <span class="qr" id="hub-qr" aria-label="QR code for this hub's address"></span>
        <span class="name">Join</span>
        <span class="desc" id="hub-qr-url">Scan to open this hub</span>
      </div>""",
    "factory": """      <div class="service-card factory-card" id="factory-card">
        <span class="icon">🏭</span>
        <span class="name">Firmware Factory</span>
        <span class="desc" id="factory-now">Built on this box</span>
        <span class="bar" id="factory-bar" hidden><span id="factory-progress"></span></span>
        <span class="factory-downloads" id="factory-downloads"></span>
      </div>""",
    "system": """      <div class="service-card system-card" id="system-card">
        <span class="icon">💽💾</span>
        <span class="meter" id="mem-meter" hidden><span class="meter-label">Memory <b id="mem-text"></b></span><span class="bar"><span id="mem-bar"></span></span></span>
        <span class="meter" id="disk-meter" hidden><span class="meter-label">Disk <b id="disk-text"></b></span><span class="bar"><span id="disk-bar"></span></span></span>
      </div>""",
}


# Who sees an app's tile, apart from who may open it (menu overhaul M5; checklist 4a: "access and
# visibility are not the same"). The hub's own choice, not root's: it changes which tiles the hub
# draws, not the web server's gates. auto: as its access (public: everyone; users: those logged
# in; private or off: nobody). An app that is off is never shown, whatever is chosen here.
VISIBILITY_FILE = STATE_DIR / "visibility.json"
VISIBLE = ("auto", "guests", "users", "hidden")


def visibility():
    try:
        data = json.loads(VISIBILITY_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and v in VISIBLE} if isinstance(data, dict) else {}


def save_visibility(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = VISIBILITY_FILE.parent / (VISIBILITY_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, VISIBILITY_FILE)


def _switched():
    """The apps with a switch: the built-ins the web server gates, and the local add-ons."""
    return set(access.ROUTED) | {m["id"] for m in MANIFESTS if m.get("local")}


# The apps drawn on the hub page itself, not as tiles (the shoutbox and the board, M9): who sees
# them is a visibility of their own (guests, users or hidden; auto is guests); who may post is
# their shout_who / board_who. id -> the tab they are in index.html.
PAGE_APPS = {"shoutbox": "shout", "board": "board"}


def page_app_hidden(i, signed_in):
    v = visibility().get(i, "auto")
    return v == "hidden" or (v == "users" and not signed_in)


def hidden_apps(signed_in=False):
    """The apps not on the home page or the list pages: off; and otherwise as their visibility
    says, or (auto) as their access does: private, and for users unless the visitor is logged in
    (access.py; the root helper leaves its copy of the choices in the control folder). A built-in
    with no switch (a list page, the box row, About) is never hidden."""
    state, vis = access.read(ACCESS_STATE), visibility()
    out = set()
    for i in _switched():
        mode, v = access.mode_of(state, i), vis.get(i, "auto")
        if mode == "off" or v == "hidden":
            out.add(i)
        elif v == "users":
            if not signed_in:
                out.add(i)
        elif v == "auto" and mode != "public" and not (signed_in and mode == "users"):
            out.add(i)
    return out


def locked_apps(signed_in=False):
    """The apps whose tile shows but which this visitor can't open yet: for users, to someone not
    logged in; private, to anyone (the admin login is asked for at its door)."""
    state = access.read(ACCESS_STATE)
    return {i for i in _switched() if access.mode_of(state, i) == "private"
            or (access.mode_of(state, i) == "users" and not signed_in)}


def render_tiles(row="apps", hidden=frozenset(), factory_tile=False, locked=frozenset()):
    """One row of the home page's tiles, from the manifests' "tile" parts. Rendered here
    rather than in the browser, so the page arrives whole. hidden: apps left out, and so a
    list page left with nothing on it. factory_tile: the owner's choice to show the factory's.
    locked: apps shown to this visitor that ask for a login at their door (M5)."""
    out = []
    ms = MANIFESTS
    st = status_tiles_state() if row == "box" else None
    if st:
        pos = {i: n for n, i in enumerate(st["order"])}
        ms = sorted(ms, key=lambda m: pos.get(m["id"], len(pos)))
        hidden = set(hidden) | set(st["hidden"])
    for m in ms:
        tile = m.get("tile")
        if not tile or tile.get("row", "apps") != row or m["id"] in hidden:
            continue
        wide = bool(st) and m["id"] in st["double"]
        if tile.get("widget") == "factory" and not factory_tile:
            continue
        if m.get("menu") and not menu_entries(m["id"], hidden):
            continue
        if "widget" in tile:
            w = WIDGET_HTML[tile["widget"]]
            out.append(w.replace('class="service-card ', 'class="service-card wide ', 1).replace('<div ', '<div data-size="double" ', 1) if wide else w)
            continue
        attrs = [f'class="service-card{" locked" if m["id"] in locked else ""}{" wide" if wide else ""}"']
        if tile.get("element_id"):
            attrs.append(f'id="{html.escape(tile["element_id"])}"')
        attrs.append(f'href="{html.escape(tile["href"])}"')
        path = m.get("status", {}).get("path")
        if path:
            attrs.append(f'data-service="{html.escape(path)}"')
        if tile.get("new_tab"):
            attrs.append('target="_blank"')
        out.append(f'      <a {" ".join(attrs)}>\n'
                   f'        <span class="icon">{html.escape(tile["icon"])}</span>\n'
                   f'        <span class="name">{html.escape(tile["name"])}</span>\n'
                   f'        <span class="desc">{html.escape(tile["desc"])}</span>\n'
                   + ('        <span class="lock">🔒 sign in to open</span>\n' if m["id"] in locked else '')
                   + f'      </a>')
    return "\n".join(out)


# --- folders (menu overhaul M7; checklist 5e, accepted by Tom 2026-10-07) ----------------------
# A list page (a manifest with a "menu": Meshtastic, the calculators, ELIZA) is a folder of
# entries: pages, downloads, or one page with starting switches. The owner may hide an entry from
# a folder, put a folder's entries in an order of their own, and put an entry from one folder in
# another as well. The hub's own choice (state/folders.json), not root's: it changes what the hub
# lists, not what the web server serves. {folder: {"hidden": [href], "order": [href], "extra": [href]}}
FOLDERS_FILE = STATE_DIR / "folders.json"
FOLDER_PARTS = ("hidden", "order", "extra")


def folders_state():
    try:
        data = json.loads(FOLDERS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {f: {k: [h for h in v.get(k, []) if isinstance(h, str)][:500] for k in FOLDER_PARTS}
            for f, v in data.items() if isinstance(f, str) and isinstance(v, dict)}


def save_folders(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FOLDERS_FILE.parent / (FOLDERS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, FOLDERS_FILE)


def entry_pool(skip=()):
    """Every entry any folder lists: href -> (order, entry, status path, the folder that names it)."""
    pool = {}
    for mid in manifests.menus(MANIFESTS):
        for o, e, svc in manifests.entries_for(mid, MANIFESTS, skip):
            pool.setdefault(e["href"], (o, e, svc, mid))
    return pool


def menu_entries(menu_id, skip=(), include_hidden=False, state=None):
    """A folder's entries as the owner arranged them: its own (manifests.entries_for), those put
    in from other folders, in its order (the rest after, as they were), less the hidden ones."""
    st = (folders_state() if state is None else state).get(menu_id, {})
    items = list(manifests.entries_for(menu_id, MANIFESTS, skip))
    have = {e["href"] for _, e, _ in items}
    if st.get("extra"):
        pool = entry_pool(skip)
        for href in st["extra"]:
            if href in pool and href not in have:
                o, e, svc, _ = pool[href]
                items.append((o, e, svc))
                have.add(href)
    pos = {h: i for i, h in enumerate(st.get("order", []))}
    items = [x for _, x in sorted(enumerate(items), key=lambda p: (pos.get(p[1][1]["href"], len(pos)), p[0]))]
    if not include_hidden:
        hidden = set(st.get("hidden", []))
        items = [x for x in items if x[1]["href"] not in hidden]
    return items


def entry_kind(href):
    """What an entry is, for /admin: one page with starting switches, a download, or a page."""
    tail = href.split("#", 1)[-1]
    if "?" in tail:
        return "switches"
    return "page" if href.startswith("/app.html#") else "download"


def folders_snapshot():
    """/admin/folders: each folder with all its entries (hidden ones too) and the choices."""
    state = folders_state()
    pool = entry_pool()
    out = []
    for mid, m in manifests.menus(MANIFESTS).items():
        st = state.get(mid, {})
        hidden, extra = set(st.get("hidden", [])), set(st.get("extra", []))
        entries = []
        for _, e, _ in menu_entries(mid, include_hidden=True, state=state):
            href = e["href"]
            entries.append({"href": href, "name": e["name"], "desc": e.get("desc", ""), "kind": entry_kind(href),
                            "from": pool.get(href, (0, 0, 0, mid))[3], "hidden": href in hidden, "extra": href in extra})
        out.append({"id": mid, "title": m["menu"]["title"], "entries": entries})
    return {"folders": out, "state": state}


def valid_folders(data):
    """A whole folders.json from /admin: only real folders, only entries some folder lists."""
    if not isinstance(data, dict) or len(data) > 64:
        return None
    menus, pool = manifests.menus(MANIFESTS), entry_pool()
    out = {}
    for f, v in data.items():
        if f not in menus or not isinstance(v, dict):
            return None
        part = {}
        for k in FOLDER_PARTS:
            hrefs = v.get(k, [])
            if not isinstance(hrefs, list) or len(hrefs) > 500 or not all(isinstance(h, str) and h in pool for h in hrefs):
                return None
            part[k] = list(dict.fromkeys(hrefs))
        out[f] = part
    return out


# --- status tiles (menu overhaul M8; Tom, 2026-10-07/08) ---------------------------------------
# The row of readouts under the apps (the box row: people, the join QR, memory and disk, the
# Firmware Factory, services) is one folder of tiles the owner arranges: which show, in what order,
# and which take two cells to say more. The hub's own (state/status_tiles.json).
STATUS_TILES_FILE = STATE_DIR / "status_tiles.json"


def status_tiles_state():
    try:
        data = json.loads(STATUS_TILES_FILE.read_text())
    except (OSError, ValueError):
        return {"order": [], "hidden": [], "double": []}
    data = data if isinstance(data, dict) else {}
    return {k: [x for x in data.get(k, []) if isinstance(x, str)][:50] for k in ("order", "hidden", "double")}


def box_tiles():
    """The box row's tiles, in the manifests' order: [(id, name)]."""
    names = {"people": "People here", "qr": "Join QR", "system": "Memory and disk", "factory": "Firmware Factory"}
    return [(m["id"], m["tile"].get("name") or names.get(m["tile"].get("widget"), m["id"]))
            for m in MANIFESTS if (m.get("tile") or {}).get("row") == "box"]


def status_tiles_snapshot():
    st = status_tiles_state()
    pos = {i: n for n, i in enumerate(st["order"])}
    tiles = sorted(box_tiles(), key=lambda t: pos.get(t[0], len(pos)))
    return {"tiles": [{"id": i, "name": n, "hidden": i in st["hidden"], "double": i in st["double"]} for i, n in tiles],
            "state": st}


TILES_MARK = "<!-- apps.d tiles -->"
BOX_MARK = "<!-- apps.d box tiles -->"
_home_page = {}   # signed in or not -> {"mtime", "body"}


def home_page(signed_in=False):
    """index.html with the tiles in place, re-read when the file or the access choices change;
    one for guests and one for anyone logged in (who also sees the apps for users)."""
    path = STATIC / "index.html"
    try:
        chosen = ACCESS_STATE.stat().st_mtime
    except OSError:
        chosen = None
    for f in (VISIBILITY_FILE, FOLDERS_FILE, STATUS_TILES_FILE):
        try:
            chosen = (chosen, f.stat().st_mtime)
        except OSError:
            chosen = (chosen, None)
    show_factory = settings_snapshot()["factory_tile"]
    mtime = (path.stat().st_mtime, chosen, show_factory)
    cached = _home_page.setdefault(signed_in, {"mtime": None, "body": b""})
    if cached["mtime"] != mtime:
        text = path.read_text(encoding="utf-8")
        hidden = hidden_apps(signed_in)
        locked = locked_apps(signed_in)
        # A tab its visitor isn't to see (M9): its link and its pane left out of the page.
        for app, tab in PAGE_APPS.items():
            if page_app_hidden(app, signed_in):
                text = re.sub(rf'\s*<a href="#{tab}" data-tab="{tab}">[^<]*</a>', "", text)
                text = text.replace(f'<div id="tab-{tab}">', f'<div id="tab-{tab}" data-off hidden>').replace(f'<div id="tab-{tab}" hidden>', f'<div id="tab-{tab}" data-off hidden>')
        text = text.replace(TILES_MARK, render_tiles("apps", hidden, locked=locked)).replace(BOX_MARK, render_tiles("box", hidden, show_factory, locked))
        cached["body"] = text.encode()
        cached["mtime"] = mtime
    return cached["body"]


# The list pages (a manifest with a "menu"), at their tile's href. Rendered on each request:
# discovered entries come from the installed apps, which an update changes.
MENU_PAGES = {m["tile"]["href"]: m for m in manifests.menus(MANIFESTS).values()}
MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="art-page">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Hub</title>
  <link rel="stylesheet" href="style.css">
  <script src="/themes.js"></script>
  <link rel="stylesheet" href="/layout.css">
</head>
<body{art_attrs}>
  <!-- {about}
       Rendered by server.py from the manifests in apps.d/ ("menu", and every manifest's
       "entries" aimed at it). Each entry opens under the hub bar in a new tab, and greys
       out when data-service is down. -->
  <header class="sub-header">
    <nav class="head-nav"><a class="head-btn labelled" href="/" title="Back to the hub"><span class="head-emoji" aria-hidden="true">🏠</span> Hub</a><a class="head-btn labelled" href="/help.html" title="Quick help"><span class="head-emoji" aria-hidden="true">🛟</span> Help</a></nav>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
    <div class="theme-picker" role="group" aria-label="Theme"></div>
  </header>

  <section class="item-section">
    <ul class="item-list">
{items}
    </ul>
  </section>

{art}{banner}  <script src="hub.js"></script>
</body>
</html>
"""


def menu_page(m, signed_in=False):
    items = []
    for _, e, service in menu_entries(m["id"], hidden_apps(signed_in)):
        href = e["href"]
        if href.startswith("/app.html#"):
            # The hub bar then offers ↑ back to this list (app.js).
            href = f"/app.html?from={m['id']}#" + href[len("/app.html#"):]
        attrs = f'href="{html.escape(href)}"'
        if service:
            attrs += f' data-service="{html.escape(service)}"'
        if e.get("new_tab", True):
            attrs += ' target="_blank"'
        desc = f'<span class="desc">{html.escape(e["desc"])}</span>' if e.get("desc") else ""
        items.append(f'      <li><a {attrs}><span class="name">{html.escape(e["name"])}</span>{desc}</a></li>')
    menu = m["menu"]
    # A krab in the bottom-left corner, if the manifest names one (style.css: data-art).
    art = menu.get("art")
    lines = menu.get("banner")
    # Text art instead, if the manifest has it (e.g. ELIZA's, from its source): the same kind of
    # faint ident in the bottom-right corner, behind the list (style.css: .menu-ident).
    art_attrs = f' data-art="{art}" data-art-side="left"' if art else (' data-art="ident"' if lines else "")
    art_html = '  <div class="admin-art" aria-hidden="true"></div>\n' if art else ""
    banner = f'  <pre class="menu-ident" aria-hidden="true">{html.escape(chr(10).join(lines))}</pre>\n' if lines else ""
    return MENU_TEMPLATE.format(art_attrs=art_attrs, art=art_html, banner=banner, title=html.escape(menu["title"]), subtitle=html.escape(menu["subtitle"]),
                                about=html.escape(menu.get("about", "")).replace("--", "-"),
                                items="\n".join(items)).encode()
STATUS_CACHE_S = 5  # one probe sweep per this many seconds, shared by every client
_status_cache = {"at": 0.0, "ports": {}, "units": {}}

# --- who is here -------------------------------------------------------------
# "Online" is distinct client addresses seen in the last ONLINE_WINDOW seconds of
# powered-on time. Every open page already polls /status and /messages, so this needs
# no heartbeat of its own -- ordinary traffic is the signal. Addresses are held in
# memory only: never written to disk, never served individually, and gone on restart.
# The page is only ever told how many.
ONLINE_WINDOW = 90
# Optional second number: devices that joined the WiFi at all, whether or not anyone
# opened the page. That is the PirateBox station counter, and dnsmasq already keeps it.
LEASES = Path(os.environ.get("HUB_LEASES", "/var/lib/misc/dnsmasq.leases"))
_seen = {}
_seen_lock = threading.Lock()


def note_client(handler):
    """Record that this address is around. Behind the web server every request arrives from
    loopback, so the forwarded address is what distinguishes one guest from another."""
    fwd = handler.headers.get("X-Forwarded-For", "")
    addr = fwd.split(",")[0].strip() if fwd.strip() else handler.client_address[0]
    if not addr:
        return
    with _seen_lock:
        _seen[addr] = CLOCK.ticks()


# --- operator settings -------------------------------------------------------
# Options the person who owns the box sets, not the guests. Only keys listed in DEFAULTS,
# with the default's type (and ints not negative), are accepted, so a malformed or
# hand-edited file cannot introduce keys.
# The gate is the web server's basic auth on /admin/*, the same treatment /term/ gets -- run
# the hub bare, with no web server in front, and these are as open as every other hub API.
SETTINGS_FILE = STATE_DIR / "settings.json"
DEFAULT_SETTINGS = {
    # The saved-work store (store.py): its size cap, and how long a gallery save lives
    # (0 = until the cap evicts it). Defaults from HUB_STORE_* in hub.env.
    "store_max_total_mb": max(1, store.MAX_TOTAL >> 20),
    "store_save_ttl_hours": int(store.SAVE_TTL // 3600),
    # The file drop: its own budget, and how long a file lives (powered-on hours; 0 = until
    # the budget evicts it).
    "drop_max_total_mb": max(1, store.DROP_MAX_TOTAL >> 20),
    "drop_ttl_hours": int(store.DROP_TTL // 3600),
    # The setup steps on /admin (Welcome) until the owner says they are done with them.
    "setup_done": False,
    # An event box starts each day empty: clear the shoutbox / board when the box boots
    # (a real boot, not the hub restarting for an update; see clear_on_new_boot).
    "shout_reset_on_boot": False,
    "board_reset_on_boot": False,
    # The Firmware Factory's tile on the front page (step 36): its queue for anyone to follow, and
    # what it built to download. Off until the owner shows it.
    "factory_tile": False,
    # The shoutbox and the forum (accounts step 16, Tom's answer 6): who may post, guests (and
    # users), users only, or off; and whether a user's name carries a check mark.
    "shout_who": "guests",
    "shout_marks": True,
    "board_who": "guests",
    "board_marks": True,
    # Page widths in rem (menu overhaul M2; Tom, 2026-10-08: 80rem, with the owner's choice of
    # 45, 60, 80, 90 or 100; the shoutbox and the forum their own, 45 as they always were).
    # Every page reads them from /layout.css.
    "page_width": 80,
    "shout_width": 45,
    "board_width": 45,
    # Unique visitors counted by a helper of their own, from salted hashes it keeps in memory
    # (irate_box/root/visitors.py; M11). On by default (Tom, 2026-10-08), as one of the setup's
    # decisions; off, the helper doesn't run at all.
    "visitor_counts": True,
    # Reports (M10; Tom, 2026-10-08: "anyone can report a post - admin decides what counts"): the
    # reasons offered, how many reports put a post in the queue, and whether it is hidden from
    # everyone else until looked at.
    "report_reasons": ["spam", "unkind", "personal details", "other"],
    "report_threshold": 1,
    "report_hide": False,
}
POSTERS = ("guests", "users", "off")
WIDTHS = (45, 60, 80, 90, 100)
REPORT_REASONS = ("spam", "unkind", "personal details", "illegal", "other")
_settings_lock = threading.Lock()


def valid_setting(key, value):
    want = type(DEFAULT_SETTINGS[key])
    if key in ("shout_who", "board_who"):
        return value in POSTERS
    if key in ("page_width", "shout_width", "board_width"):
        return type(value) is int and value in WIDTHS
    if key == "report_reasons":
        return isinstance(value, list) and 0 < len(value) <= len(REPORT_REASONS) and all(v in REPORT_REASONS for v in value)
    if key == "report_threshold":
        return type(value) is int and 1 <= value <= 20
    return type(value) is want and (want is not int or value >= 0)


def apply_settings(data):
    store.MAX_TOTAL = max(1, data["store_max_total_mb"]) << 20
    store.SAVE_TTL = data["store_save_ttl_hours"] * 3600
    store.DROP_MAX_TOTAL = max(1, data["drop_max_total_mb"]) << 20
    store.DROP_TTL = data["drop_ttl_hours"] * 3600


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE) as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data
    if isinstance(stored, dict):
        for key in DEFAULT_SETTINGS:
            if valid_setting(key, stored.get(key)):
                data[key] = stored[key]
    return data


def save_settings(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.parent / (SETTINGS_FILE.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, SETTINGS_FILE)


# Held in memory so a page full of /status pollers costs no disk reads.
_settings = load_settings()
apply_settings(_settings)


def settings_snapshot():
    with _settings_lock:
        return dict(_settings)


def online_count(now):
    with _seen_lock:
        for addr, seen in list(_seen.items()):
            if now - seen > ONLINE_WINDOW:
                del _seen[addr]
        return len(_seen)


def joined_count():
    """Devices with a DHCP lease, or None where there is no dnsmasq (i.e. in dev).
    Lease expiry is a wall-clock timestamp, which is fiction on a board with no RTC,
    so the lines are counted rather than filtered -- dnsmasq prunes the file itself."""
    try:
        with open(LEASES) as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return None


def port_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def socket_listening(path):
    """A service on a UNIX socket (SilverBullet's: no TCP port at all, F31; ttyd's, F9)."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            s.connect(path)
            return True
    except PermissionError:
        # There, but not the hub's to open (ttyd's is root's and the web server's group's).
        # Its folder is the unit's RuntimeDirectory, gone when the unit stops, so it is live.
        return True
    except OSError:
        return False


def unit_states(units):
    """{unit: (installed?, active?, enabled?)} from one `systemctl show`, or {} where there
    is no systemd to ask (a dev machine): the caller then treats every unit as installed."""
    try:
        out = subprocess.run(
            ["systemctl", "show", "--property=Id,LoadState,ActiveState,UnitFileState", *units],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    states = {}
    for block in out.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" in fields:
            states[fields["Id"]] = (fields.get("LoadState") not in ("not-found", ""),
                                    fields.get("ActiveState") == "active",
                                    fields.get("UnitFileState") == "enabled")
    # A missing unit still gets a block, under the name it was asked for.
    return states


def kiwix_why():
    """Why /wiki/ is not running, in words a guest or the owner can act on. Cheap enough for
    /status (cached with it): the ZIM headers only, never kiwix-manage."""
    books = sorted(librarian.ZIM_DIR.glob("*.zim")) if librarian.ZIM_DIR.is_dir() else []
    if not books:
        return "No books yet: add one in Library → Books, or from a USB stick."
    bad = [b for b in books if zimcheck.header_problem(b)]
    if len(bad) == len(books):
        return (f"None of the {len(books)} book{'s' if len(books) != 1 else ''} can be read (damaged or unfinished "
                "copies). Health says which, and can set them aside.")
    try:
        has_books = "<book " in librarian.LIBRARY_XML.read_text(errors="replace")
    except OSError:
        has_books = False
    if not has_books:
        return "The library is empty, though there are books: Health → Rebuild the library."
    return "Kiwix is not running. Health says why, and can start it again."


def service_status(proxied):
    """Per-service state: "running", "stopped" (installed, not answering) or "missing".
    `up` stays for the tiles: running, and reachable because the web server is in front. Probes
    are cached so a page full of pollers costs one sweep per STATUS_CACHE_S."""
    now = time.monotonic()
    if now - _status_cache["at"] > STATUS_CACHE_S:
        _status_cache["ports"] = {
            svc["port"]: port_listening(svc["port"]) for svc in SERVICES if "port" in svc
        }
        _status_cache["sockets"] = {
            svc["socket"]: socket_listening(svc["socket"]) for svc in SERVICES if "socket" in svc
        }
        _status_cache["units"] = unit_states([s["unit"] for s in SERVICES if "unit" in s])
        _status_cache["why"] = {}
        _status_cache["at"] = now
    units = _status_cache["units"]
    out = []
    for svc in SERVICES:
        installed, active, _ = units.get(svc["unit"], (True, False, False)) if "unit" in svc else (True, False, False)
        if "local_dir" in svc:
            installed = Path(svc["local_dir"]).is_dir()
            running = installed and proxied
        elif "root" in svc:
            root = os.environ.get(svc["root"])
            installed = root is None or Path(root).is_dir()
            running = installed and proxied
        elif svc.get("self"):
            running = True  # it is answering this request
        elif svc.get("proxy"):
            running = proxied
        elif svc.get("active"):
            running = installed and active
        elif "socket" in svc:
            running = _status_cache["sockets"].get(svc["socket"], False)
        else:
            running = _status_cache["ports"].get(svc["port"], False)
        state = "running" if running else "stopped" if installed else "missing"
        entry = {"path": svc["path"], "name": svc["name"], "state": state,
                 "up": proxied and running}
        if "note" in svc:
            entry["note"] = svc["note"]
        if state == "stopped" and svc.get("unit") == "kiwix.service":
            why = _status_cache.setdefault("why", {})
            if "kiwix" not in why:
                why["kiwix"] = kiwix_why()
            entry["why"] = why["kiwix"]
        out.append(entry)
    return out


# --- unique visitors (M11) ----------------------------------------------------------
# The hub can't start or stop the counting helper (it runs as root, to read the leases): it records
# the owner's choice in VISITORS_WANT, and a root path unit (irate-box-visitors-switch.path) applies
# it (scripts/visitors-apply.sh). The helper leaves only two numbers in VISITORS_COUNTS.
VISITORS_WANT = STATE_DIR / "visitors.want"
VISITORS_COUNTS = Path(os.environ.get("HUB_VISITORS_COUNTS", "/run/irate-box-visitors/counts.json"))


def write_visitors_want(on):
    tmp = VISITORS_WANT.parent / (VISITORS_WANT.name + ".tmp")
    tmp.write_text("on\n" if on else "off\n")
    os.replace(tmp, VISITORS_WANT)


def visitor_counts():
    """{day, week} from the helper, or None (off, not running yet, or nothing written)."""
    if not _settings.get("visitor_counts"):
        return None
    try:
        data = json.loads(VISITORS_COUNTS.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or type(data.get("day")) is not int or type(data.get("week")) is not int:
        return None
    return {"day": data["day"], "week": data["week"], "date": str(data.get("date", ""))[:10]}


# --- remote access -------------------------------------------------------------
# The hub runs unprivileged, so it cannot start tailscaled itself. It records what the
# operator asked for in TAILSCALE_WANT, and a root path unit (irate-box-tailscale.path,
# set up by install.sh) applies it with tailscale-apply.sh. One line:
#   off | on | on <seconds>   -- a timed session ends by itself, and at the next boot.
TAILSCALE_WANT = STATE_DIR / "tailscale.want"
TAILSCALE_MAX_HOURS = 72


def read_tailscale_want():
    try:
        parts = TAILSCALE_WANT.read_text().split()
    except OSError:
        parts = []
    if parts[:1] == ["on"]:
        if len(parts) == 2 and parts[1].isdigit():
            return {"mode": "timed", "hours": int(parts[1]) // 3600}
        return {"mode": "on"}
    return {"mode": "off"}


def write_tailscale_want(mode, hours=None):
    line = {"off": "off", "on": "on"}.get(mode)
    if mode == "timed":
        line = f"on {hours * 3600}"
    tmp = TAILSCALE_WANT.parent / (TAILSCALE_WANT.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, TAILSCALE_WANT)
    _status_cache["at"] = 0.0  # the next /status should show the change, not a cached sweep


# --- the librarian -------------------------------------------------------------
# Keeps ZIM books current from their sources (librarian.py). The page reads a snapshot and
# posts actions; anything that touches the network or the card runs in one background
# thread, and the librarian's own lock keeps it from overlapping the hourly timer.
_library_job = {"thread": None, "action": None, "result": None}
_library_lock = threading.Lock()


def library_snapshot():
    snap = librarian.snapshot()
    # An app the librarian handed to the root helper: what became of it.
    results = {r.get("id"): r for r in control_results(30)}
    for entry in snap["status"].values():
        if entry.get("pending"):
            entry["install_result"] = results.get(entry["pending"])
    with _library_lock:
        thread = _library_job["thread"]
        snap["job"] = {"action": _library_job["action"], "result": _library_job["result"],
                       "busy": bool(thread and thread.is_alive())}
    snap["running"] = snap["running"] or snap["job"]["busy"]
    return snap


def library_start(action, fn):
    """Run fn() in the background unless something is already running; False if busy."""
    def work():
        try:
            result = fn()
        except librarian.LibrarianError as exc:
            result = {"error": str(exc)}
        with _library_lock:
            _library_job["result"] = result
    with _library_lock:
        thread = _library_job["thread"]
        if (thread and thread.is_alive()) or librarian.is_running():
            return False
        _library_job.update(action=action, result=None,
                            thread=threading.Thread(target=work, daemon=True))
        _library_job["thread"].start()
    return True


def library_action(payload):
    """(status code, body) for one POST /admin/library."""
    action = payload.get("action")
    names = [n for n in payload.get("names") or [] if isinstance(n, str)]
    quiet = {"log": lambda *_: None}
    try:
        if action == "add":
            librarian.add_source(payload.get("source") or {})
        elif action == "remove":
            librarian.remove_source(str(payload.get("name", "")), bool(payload.get("delete_book")))
        elif action == "policy":
            librarian.set_policy(**{k: payload[k] for k in librarian.DEFAULT_POLICY
                                    if type(payload.get(k)) is int})
        elif action == "token":
            librarian.set_token(str(payload.get("value") or ""))
        elif action == "add-apps":
            # The named apps (an app row's "Keep current"), or every default one.
            have = {s["name"] for s in librarian.load_config()["sources"]}
            for app in names or librarian.default_apps():
                if not librarian.APPS.get(app, {}).get("source"):
                    raise librarian.LibrarianError(f"{app} is not an app the hub keeps current")
                if app not in have:
                    librarian.add_source(librarian.default_app_source(app))
        elif action in ("check", "fetch", "update"):
            if not library_start(action, lambda: librarian.update(names or None, mode=action, **quiet)):
                return 409, {"error": "the librarian is already running"}
        elif action == "rollback":
            name, version = str(payload.get("name", "")), payload.get("version") or None
            if not library_start(action, lambda: {name: librarian.rollback(name, version)}):
                return 409, {"error": "the librarian is already running"}
        else:
            return 400, {"error": "unknown action"}
    except librarian.LibrarianError as exc:
        return 400, {"error": str(exc)}
    return 200, library_snapshot()


def git_snapshot():
    """The Git page: the repositories, and the mirrors with what each keeps (mirrors.py)."""
    from irate_box.library import mirrors
    snap = gitrepos.snapshot()
    mirror_list = mirrors.snapshot()
    # Each repository's newest build, and which mirror a submodule mirror belongs to (the Git
    # page leaves those out of its grid; Library → Mirrors lists them under their parent).
    last = {}
    for r in ci.snapshot(limit=500)["runs"]:
        last.setdefault(r["run"].split("/")[0], {k: r.get(k) for k in ("run", "state", "started", "duration", "branch", "commit")})
    parent = {}
    for m in mirror_list:
        for sub in (m.get("status") or {}).get("submodules", {}).values():
            parent[(m["area"], sub["repo"])] = m["name"]
    for r in snap["repos"]:
        r["last_build"] = last.get(r["name"]) if r["area"] == "private" else None
        r["submodule_of"] = parent.get((r["area"], r["name"]))
    snap.update(mirrors=mirror_list, running=librarian.is_running(), progress=librarian.progress())
    return snap


def git_action(payload):
    """(status, body) for POST /admin/git: a repository change (gitrepos.py), or a mirror's."""
    from irate_box.library import mirrors
    action = payload.get("action")
    if not str(action).startswith("mirror-"):
        code, body = gitrepos.action(payload)
        return (code, git_snapshot()) if code == 200 else (code, body)
    try:
        if action == "mirror-add":
            mirrors.add(payload.get("mirror") or {})
        elif action == "mirror-change":
            mirrors.change(payload.get("mirror") or {})
        elif action == "mirror-remove":
            mirrors.remove(str(payload.get("name", "")))
        elif action in ("mirror-check", "mirror-update"):
            names = [str(payload["name"])] if payload.get("name") else None
            def run():
                with librarian.Lock():
                    return {"mirrors": mirrors.sync_all(names, check_only=(action == "mirror-check"), log=lambda *_: None)}
            if not library_start(action, run):
                return 409, {"error": "the librarian is already running"}
        else:
            return 400, {"error": "action must be mirror-add, mirror-change, mirror-remove, mirror-check or mirror-update"}
    except librarian.LibrarianError as exc:
        return 400, {"error": str(exc)}
    return 200, git_snapshot()


def firmware_action(payload):
    """(status, body) for POST /admin/firmware: the settings, or a run (firmware.py)."""
    action = payload.get("action")
    try:
        if action == "settings":
            firmware.set_settings(**{k: payload[k] for k in ("enabled", "configs", "boards", "keep_alpha", "keep_beta", "cache")
                                     if k in payload})
        elif action in ("check", "update"):
            def run():
                with librarian.Lock():
                    return {"firmware": firmware.sync(check_only=(action == "check"), log=lambda *_: None)}
            if not library_start(f"firmware-{action}", run):
                return 409, {"error": "the librarian is already running"}
        elif action == "flush-cache":
            firmware.flush_cache(log=lambda *_: None)
        else:
            return 400, {"error": "action must be settings, check, update or flush-cache"}
    except librarian.LibrarianError as exc:
        return 400, {"error": str(exc)}
    snap = firmware.snapshot()
    snap.update(running=librarian.is_running(), progress=librarian.progress())
    return 200, snap


# --- admin: box and services, moderation, saved work, password, backup -----------
# Starting and stopping services and changing the admin password need root. The hub asks:
# it drops a request in control/requests/, irate-box-control.path runs hub_control.py as
# root, which acts only on its own allow-list and answers in control/results/. CONTROL_OPS
# mirrors that allow-list so the page offers only what will be accepted.
CONTROL_DIR = STATE_DIR / "control"
CONTROL_REQUESTS = CONTROL_DIR / "requests"
CONTROL_RESULTS = CONTROL_DIR / "results"
ACCESS_STATE = CONTROL_DIR / "access.json"
KITS_DIR = STATE_DIR / "kits"  # the offline kit the root helper made (/admin, Backup)
KIT_PROGRESS = CONTROL_DIR / "kit-progress.json"  # the root helper's copy of who may open each app
VERSION_FILE = CHECKOUT / "VERSION"
_ALL_OPS = ["start", "stop", "restart", "enable", "disable"]
CONTROL_OPS = {unit: _ALL_OPS for unit in manifests.controllable_units(MANIFESTS)}
CONTROL_OPS.update({f"{WEB_SERVER}.service": ["restart"], "irate-box.service": ["restart"]})
MIN_PASSWORD = 8
# First use (hub_control.py, the web server's config): while this root-owned file exists no
# admin password has been chosen, the web server lets /admin through with no login, and the
# hub serves only the set-the-password page there. It sits beside that server's config.
UNCLAIMED_FILE = Path(os.environ.get("HUB_UNCLAIMED_FILE", f"/etc/{WEB_SERVER}/irate-box-unclaimed"))
SETUP_PATHS = ("/admin", "/admin/", "/admin/setup")


# The front's word that a request came through its /admin route, where it asked for the
# login: a secret it adds there and nowhere else (install.sh makes it, root-only, and hands it
# to the web server and to this unit). Without it, anything that can send a request to the
# hub's loopback port is the admin: a build (F8), a proxy like SilverBullet's (F31), a path
# the front did not normalise (F27). Unset, as when the hub runs bare in development, /admin
# is as open as it always was.
FRONT_SECRET = os.environ.get("HUB_FRONT_SECRET", "")
FRONT_HEADER = "X-Irate-Front"
SESSION_COOKIE = "irate_session"


def unclaimed():
    return UNCLAIMED_FILE.exists()


def admin_login_on():
    """Whether the box's own admin login (basic auth) is on: the root helper's record (on unless
    it says off)."""
    try:
        return json.loads((CONTROL_DIR / "admin-login.json").read_text()).get("on") is not False
    except (OSError, ValueError, AttributeError):
        return True


def accounts_view():
    listing = accounts.listing()
    return {"settings": accounts.settings(), "accounts": listing, "counts": accounts.counts(),
            "admin_login": {"on": admin_login_on(), "results": control_results(10),
                            "https_admins": [a["name"] for a in listing if a["role"] == "admin" and a["state"] == "user"
                                             and a["password_set"] and a.get("https_login")]}}


def setup_status(rid):
    out = {"unclaimed": unclaimed()}
    if rid:
        out["result"] = next((r for r in control_results(20) if r.get("id") == rid), None)
    return out
# Never in a backup: the big or regenerable (ZIMs, archived versions), the transient, and
# the one secret the page can set (the GitHub token).
BACKUP_SKIP = ("zim", "control", "library/archive", "library/tmp", "library/github-token",
               "library/lock", "library/apps")
# Syncthing's identity: its private keys (key.pem, https-key.pem) and config.xml (device list,
# GUI password hash, API key). Restoring them keeps the box's device ID, so peers need no
# re-pairing -- but whoever holds the file can pose as the box to those peers. Left out
# unless the backup is asked for with ?syncthing=1, which the page labels as such.
SYNCTHING_DIRS = (".local/state/syncthing", ".config/syncthing")


# This box's own source, as installed (install.sh writes it; the AGPL's offer to everyone who
# uses the hub over the network, which on an offline box has to come from the box itself).
SOURCE_TARBALL = STATE_DIR / "source" / "irate-box-source.tar.gz"


def hub_version():
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "a development checkout"


def control_request(req):
    """Queue a request for hub_control.py; returns its id, which its answer will carry."""
    CONTROL_REQUESTS.mkdir(parents=True, exist_ok=True)
    rid = secrets.token_hex(8)
    # Written in the hub's own folder, renamed in: never read half-written. Not in control/,
    # which is root's (F3), nor in requests/, whose path unit would start the helper for it.
    tmp = STATE_DIR / f".request-{rid}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(dict(req, id=rid), fh)
    os.replace(tmp, CONTROL_REQUESTS / f"{rid}.json")
    return rid


def control_results(limit=10):
    out = []
    for path in sorted(CONTROL_RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:limit]:
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
    return out


def admin_box(proxied):
    units = [s["unit"] for s in SERVICES if "unit" in s]
    states = unit_states(units)
    by_name = {s["name"]: s for s in SERVICES}
    services = []
    for entry in service_status(proxied):
        unit = by_name.get(entry["name"], {}).get("unit")
        _, active, enabled = states.get(unit, (True, False, False))
        services.append(dict(entry, unit=unit, active=active, enabled=enabled,
                             ops=CONTROL_OPS.get(unit, [])))
    now = CLOCK.ticks()
    return {"system": system_status(), "uptime": now, "online": online_count(now),
            "joined": joined_count(), "services": services, "version": hub_version(),
            "service_uptime": svchistory.summarize(svchistory.load()),
            "results": control_results(),
            "pending": len(list(CONTROL_REQUESTS.glob("*.json"))) if CONTROL_REQUESTS.exists() else 0}


UPDATE_STATE = CONTROL_DIR / "update.json"
UPDATE_LOG = CONTROL_DIR / "update.log"
UPDATE_PROGRESS = CONTROL_DIR / "update-progress.json"
DOCTOR_STATE = CONTROL_DIR / "doctor.json"
UPDATE_ACTIONS = {"check": "update-check", "fetch": "update-fetch", "install": "update-install",
                  "force-install": "update-force-install",
                  "doctor": "update-doctor", "clear-cache": "update-clear-cache"}
# Terminal colour codes, in logs written before install.sh stopped sending them to files.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def update_progress():
    """The root helper's step and download in flight, or None: nothing running, or a file
    left behind by a helper that died."""
    try:
        data = json.loads(UPDATE_PROGRESS.read_text())
        pid = int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass  # alive, and root's
    except OSError:
        return None
    return data


def _pending_actions(prefix):
    n = 0
    for path in CONTROL_REQUESTS.glob("*.json") if CONTROL_REQUESTS.exists() else ():
        try:
            n += str(json.loads(path.read_text()).get("action", "")).startswith(prefix)
        except (OSError, ValueError, AttributeError):
            pass
    return n


def update_snapshot():
    """What /admin's update buttons show: the last check or fetch (from the root helper),
    the tail of the last install's output, whether a request is still being worked on, and
    the step it is on."""
    try:
        state = json.loads(UPDATE_STATE.read_text())
    except (OSError, ValueError):
        state = None
    try:
        log = [ANSI_RE.sub("", line) for line in UPDATE_LOG.read_text(errors="replace").splitlines()[-40:]]
    except OSError:
        log = []
    try:
        doctor = json.loads(DOCTOR_STATE.read_text())
    except (OSError, ValueError):
        doctor = None
    # Only update requests: a queued app install or service change is not the update's.
    pending = _pending_actions("update-")
    # Automatic updates: the librarian's policy for them, and what its last step did.
    policy = librarian.load_config()["policy"]
    auto = {k: policy[k] for k in ("hub_check_every_hours", "hub_auto", "hub_window_start", "hub_window_end")}
    auto["state"] = librarian._hub_update_state()
    return {"version": hub_version(), "state": state, "log": log, "pending": pending,
            "progress": update_progress(), "doctor": doctor, "results": control_results(5), "auto": auto}


SECURITY_STATE = CONTROL_DIR / "security.json"
AUDIT_STATE = CONTROL_DIR / "security-audit.json"
SECURITY_LOG = CONTROL_DIR / "security-updates.log"
IFACE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
SECURITY_CHOICE_RE = re.compile(r"^[a-z-]+(:[A-Za-z0-9@_][A-Za-z0-9@._-]*)?$")


def security_snapshot():
    """What the Security page shows: the hub's own lines (which only the hub knows), then the
    root helper's last scan of the box."""
    hub = [{
        "id": "admin-password", "title": "Admin password",
        **({"status": "problem", "detail": "Not chosen yet: anyone on the network can open /admin and choose it.",
            "fix": "Choose it now, on this page's Access section."} if unclaimed() else
           {"status": "warn", "detail": "Set. It travels as plain HTTP, so on an open hotspot anyone listening "
            "can read it the first time a browser sends it.",
            "fix": "Log in to /admin from the LAN or over Tailscale rather than over the hotspot; a safer login is planned."}),
    }, {
        "id": "plain-http", "title": "Plain HTTP", "status": "warn",
        "detail": "The hub has no certificate, so everything a browser and the box say to each other — pages, "
                  "messages, uploads — can be read by anyone on the same open network, and changed by anyone "
                  "who sets out to.",
        "fix": "That is the price of working offline with no setup. Keep secrets off the hub.",
    }, {
        "id": "one-origin", "title": "Apps share the admin page's address", "status": "warn",
        "detail": "Kiwix books, the calculators and the drawing apps run on the same origin as /admin, so a "
                  "hostile page among them could act with your login while you are logged in.",
        "fix": "Log out (close the browser) after admin work; giving /admin an address of its own is planned.",
    }]
    for f in hub:
        f.setdefault("actions", [])
    try:
        scan = json.loads(SECURITY_STATE.read_text())
    except (OSError, ValueError):
        scan = None
    try:
        audit = json.loads(AUDIT_STATE.read_text())
    except (OSError, ValueError):
        audit = None
    try:
        log = [ANSI_RE.sub("", line) for line in SECURITY_LOG.read_text(errors="replace").splitlines()[-40:]]
    except OSError:
        log = []
    deep = {}
    try:
        d = json.loads((CONTROL_DIR / "security-deep.json").read_text())
        deep = {"at": d.get("at"), "took": d.get("took")}
    except (OSError, ValueError):
        pass
    try:
        deep["progress"] = json.loads((CONTROL_DIR / "security-deep-progress.json").read_text())
    except (OSError, ValueError):
        pass
    imports = {}
    for kind in ("openvas", "nmap"):
        try:
            rep = json.loads((STATE_DIR / "security-imports" / f"{kind}.json").read_text())
            imports[kind] = {"name": rep.get("name"), "ran": rep.get("ran"), "imported": rep.get("imported"), "results": len(rep.get("results", []))}
        except (OSError, ValueError):
            pass
    return {"hub": hub, "scan": scan, "audit": audit, "deep": deep, "imports": imports, "log": log, "pending": _pending_actions("security-"),
            "results": control_results(5)}


HEALTH_STATE = CONTROL_DIR / "health.json"
INSTALL_LOG_DIR = Path(os.environ.get("HUB_LOG_DIR", "/var/log/irate-box"))
HEALTH_CHOICE_RE = re.compile(r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]{1,80}$"
                              r"|^(kiwix-rebuild|kiwix-off|rerun-install|net-scan|rtc-find|rtc-save|rtc-remove)$"
                              r"|^clock-set:\d{10}$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$")
HELPER_STUCK_AFTER = 90  # seconds a request may wait before the page says the helper is not answering


def helper_state():
    """Is the root helper answering? The hub can see that for itself: its requests wait in a
    folder it owns. One left for more than 90 s means everything on /admin that needs root
    goes nowhere, including the doctor, so the page says what to type instead."""
    oldest = None
    for path in CONTROL_REQUESTS.glob("*.json") if CONTROL_REQUESTS.exists() else ():
        try:
            m = path.stat().st_mtime
        except OSError:
            continue
        oldest = m if oldest is None or m < oldest else oldest
    age = time.time() - oldest if oldest else 0
    return {"waiting": _pending_actions(""), "oldest": round(age), "stuck": age > HELPER_STUCK_AFTER,
            "commands": ["sudo systemctl reset-failed irate-box-control.service irate-box-control.path",
                         "sudo systemctl start irate-box-control.path",
                         "sudo /opt/irate-box/irate-box health"]}


def health_snapshot():
    """The Health page: the doctor's last report (root helper), the last install's record and
    the end of its output (both readable by the hub), and whether the helper is answering."""
    def load(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
    try:
        log = [ANSI_RE.sub("", line) for line in (INSTALL_LOG_DIR / "install.log").read_text(errors="replace").splitlines()[-80:]]
    except OSError:
        log = []
    return {"report": load(HEALTH_STATE), "install": load(INSTALL_LOG_DIR / "install-state.json"), "log": log,
            "helper": helper_state(), "pending": _pending_actions("health-"), "results": control_results(5),
            "progress": update_progress()}


NETINV_STATE = CONTROL_DIR / "netinv.json"
UPLINK_STATE = CONTROL_DIR / "uplink.json"
MESH = meshbridge.Bridge()  # started in main; idle (retrying now and then) where there is no broker


def network_snapshot():
    """The Network page: the root helper's last inventory, the watchdog's own report, and the
    levels it offers (from uplink.py, so the page and the watchdog cannot disagree)."""
    def load(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None
    status = load(UPLINK_STATE)
    if status:
        # A report the watchdog stopped writing is old news: say so rather than show it as live.
        status["stale"] = time.time() - status.get("at", 0) > 3 * max(status.get("settings", {}).get("check", 60), 60)
    # Each link's uptime (step 34): hours for a week, days for 35, summed here from the watchdog's
    # five-minute slots, so the page gets a few KB rather than the slots.
    return {"inventory": load(NETINV_STATE), "uplink": status, "uptime": linkhistory.summarize(load(uplink.HISTORY)),
            "levels": {"eagerness": list(uplink.EAGERNESS), "forgiveness": list(uplink.FORGIVENESS),
                       "describe": uplink.DESCRIBE, "presets": {"eagerness": uplink.EAGERNESS,
                                                                "forgiveness": uplink.FORGIVENESS, "common": uplink.COMMON},
                       "fields": {k: list(v) if isinstance(v, tuple) else {s: list(r) for s, r in v.items()}
                                  for k, v in uplink.FIELDS.items()}},
            "pending": _pending_actions("net-") + _pending_actions("uplink-"), "results": control_results(5)}


# install.sh's copy of /etc/hub/install-options in the state folder (/etc/hub is not readable
# by the hub); for showing what is added only.
INSTALL_OPTIONS = STATE_DIR / "install-options"
ADDON_ID_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def addons_snapshot():
    """The Add-ons page: each add-on from apps.d, whether install.sh has it (its option in
    install-options, which is root-owned and world-readable), and its service's state; plus the
    installer run in flight, which is the same as an update's."""
    try:
        opts = INSTALL_OPTIONS.read_text().split()
    except OSError:
        opts = None  # a dev checkout, or a box installed before install-options existed
    specs = manifests.addons(MANIFESTS)
    units = [m["status"]["unit"].replace("@hub.", f"@{os.environ.get('HUB_USER', 'hub')}.")
             for m in specs.values() if m.get("status", {}).get("unit")]
    states = unit_states(units) if units else {}
    out = []
    for aid, m in specs.items():
        unit = m.get("status", {}).get("unit", "").replace("@hub.", f"@{os.environ.get('HUB_USER', 'hub')}.")
        _, active, _ = states.get(unit, (False, False, False))
        out.append({"id": aid, **{k: m["addon"].get(k) for k in ("title", "summary", "consent", "needs")},
                    "added": None if opts is None else m["addon"]["option"] in opts, "active": active})
    snap = update_snapshot()
    return {"addons": out, "known": opts is not None, "progress": snap["progress"], "log": snap["log"],
            "pending": _pending_actions("addon"), "results": control_results(5)}


# --- local add-ons (plans/no-root-addons-plan) ---------------------------------
# Added, kept current and removed by the hub itself: their manifests in manifests.LOCAL_D, their
# files in manifests.ADDONS (the librarian's), served by the web server's add-on origin. The
# owner's agreement to each is kept in CONSENTS. Switching one on is the access switch (root's).
CONSENTS = STATE_DIR / "addons-consent.json"
# Each added add-on against the catalogue (catalogue_sync): {id: {status, at, changed, held}}.
CATALOGUE_STATE = STATE_DIR / "addons-catalogue.json"
LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def _read_consents():
    try:
        data = json.loads(CONSENTS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _agreed_changes(old, new):
    """What changed in the part the owner agreed to, in words, for /admin."""
    a, b = manifests.agreed_part(old), manifests.agreed_part(new)
    out = []
    if a["consent"] != b["consent"]:
        out.append("its consent text")
    added = [c for c in b["connect"] if c not in a["connect"]]
    gone = [c for c in a["connect"] if c not in b["connect"]]
    if added:
        out.append("now connects to " + ", ".join(added))
    if gone:
        out.append("no longer connects to " + ", ".join(gone))
    if a["storage"] != b["storage"]:
        out.append("now keeps data in the visitor's browser" if b["storage"] else "no longer keeps data in the browser")
    if a["source"] != b["source"]:
        out.append(f"comes from {b['source'].get('repo')} (was {a['source'].get('repo')})")
    return out


def catalogue_sync():
    """Bring each add-on added from the catalogue up to the catalogue's entry, as far as the owner's
    consent allows (next-work-plan step 19). The added copy (manifests.LOCAL_D) is what the owner
    agreed to; a newer catalogue entry with the same agreed part (manifests.agreed_part: consent
    text, what it connects to, browser storage, where it comes from) replaces it: the tile, its
    list, a moved pin. One that changes the agreed part waits for the owner to accept it (/admin);
    the agreed copy keeps running. Pasted add-ons have no catalogue entry and are left alone."""
    try:
        cat = manifests.catalogue()
    except (manifests.ManifestError, OSError, ValueError):
        return {}
    consents = _read_consents()
    try:
        state = json.loads(CATALOGUE_STATE.read_text())
    except (OSError, ValueError):
        state = {}
    now = int(time.time())
    kick = []
    for path in sorted(manifests.LOCAL_D.glob("*.json")) if manifests.LOCAL_D.is_dir() else []:
        i = path.stem
        if (consents.get(i) or {}).get("how") != "catalogue":
            continue
        try:
            copy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entry = cat.get(i)
        if entry is None:
            if (state.get(i) or {}).get("status") != "gone":
                state[i] = {"status": "gone", "at": now}
            continue
        if entry == copy:
            if (state.get(i) or {}).get("status") == "held":
                state[i] = {"status": "current", "at": now}
            continue
        held = _agreed_changes(copy, entry)
        if held:
            state[i] = {"status": "held", "at": now, "held": held}
            continue
        changed = sorted(k for k in set(copy) | set(entry) if copy.get(k) != entry.get(k))
        if (copy.get("source") or {}).get("pin") != (entry.get("source") or {}).get("pin"):
            kick.append(i)
        _write_atomic(path, json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        state[i] = {"status": "updated", "at": now, "changed": changed}
    _write_atomic(CATALOGUE_STATE, json.dumps(state, indent=2) + "\n")
    if kick:
        # A moved pin: fetch it now rather than at the librarian's next turn.
        librarian.reload_apps()
        library_start("update", lambda: librarian.update(kick, mode="update", log=lambda *_: None))
    return state


def local_addons_snapshot():
    """/admin's local add-ons: the catalogue, what is added (and installed, and how it is
    switched), and any local manifest that was left out and why."""
    refresh_manifests()
    try:
        cat = manifests.catalogue()
        cat_error = None
    except (manifests.ManifestError, OSError, ValueError) as exc:
        cat, cat_error = {}, str(exc)
    local, errors = manifests.load_local(builtin=manifests.load())
    state = access.read(ACCESS_STATE)
    consents = _read_consents()
    status = librarian.load_status()
    try:
        cat_state = json.loads(CATALOGUE_STATE.read_text())
    except (OSError, ValueError):
        cat_state = {}
    added = []
    for m in local:
        i = m["id"]
        added.append({"id": i, "title": m["addon"]["title"], "summary": m["addon"]["summary"],
                      "mode": access.mode_of(state, i), "installed": librarian.installed_app(i),
                      "status": status.get(i, {}), "pin": m["source"].get("pin"), "repo": m["source"].get("repo"),
                      "capabilities": m["capabilities"], "from_catalogue": i in cat,
                      "consent": consents.get(i), "href": m["tile"]["href"], "catalogue": cat_state.get(i)})
    have = {m["id"] for m in local}
    offered = [{"id": i, "title": c["addon"]["title"], "summary": c["addon"]["summary"],
                "consent": c["addon"]["consent"], "repo": c["source"].get("repo"), "pin": c["source"].get("pin"),
                "capabilities": c.get("capabilities", {}), "added": i in have} for i, c in cat.items()]
    job = {k: _library_job.get(k) for k in ("action", "result")}
    return {"catalogue": offered, "catalogue_error": cat_error, "added": added, "errors": errors,
            "addon_port": int(os.environ.get("HUB_ADDON_PORT", "8090")), "job": job,
            "running": librarian.is_running()}


def _local_add(m, how):
    """Write a checked local manifest, record the consent, and fetch it in the background."""
    i = m["id"]
    path = confine.under(manifests.LOCAL_D, f"{i}.json")
    if path.exists():
        raise ValueError(f"{i} is already added")
    _write_atomic(path, json.dumps(m, indent=2) + "\n")
    consents = _read_consents()
    consents[i] = {"at": int(time.time()), "how": how, "consent": m["addon"]["consent"]}
    _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
    refresh_manifests()
    # Off until the owner switches it on, whatever was chosen for that name before (a removed
    # add-on, or the built-in ELIZA of 2026-10-05): root's, which also puts it in the web
    # server's add-on maps.
    rid = control_request({"action": "access", "app": i, "mode": "off"})
    librarian.add_source(librarian.default_app_source(i))
    started = library_start("update", lambda: librarian.update([i], mode="update", log=lambda *_: None))
    return {"added": i, "fetching": started, "access": rid}


def local_addons_action(payload):
    """(status code, body) for one POST /admin/local-addons."""
    action = payload.get("action")
    try:
        if action == "add":
            i = str(payload.get("id", ""))
            cat = manifests.catalogue()
            if i not in cat:
                return 400, {"error": "id must name an add-on in the catalogue"}
            if payload.get("agree") is not True:
                return 400, {"error": "agree to its consent text first"}
            return 202, _local_add(cat[i], "catalogue")
        if action == "paste":
            m = payload.get("manifest")
            # Pasted: anyone's manifest, not one this hub's code offers. The page shows the
            # heavy warning; the request must say it was read.
            if payload.get("understood") != "I understand this runs someone else's code on this box's address":
                return 400, {"error": "a pasted add-on needs its warning acknowledged"}
            manifests.check_local(m, "the pasted manifest", {x["id"] for x in manifests.load()})
            return 202, _local_add(m, "pasted")
        if action == "accept":
            # The catalogue's newer entry, whose agreed part changed: the owner has read the
            # changes on /admin and takes it. It replaces the copy, with a new consent record.
            i = str(payload.get("id", ""))
            cat = manifests.catalogue()
            if not LOCAL_ID_RE.match(i) or i not in cat or not confine.under(manifests.LOCAL_D, f"{i}.json").exists():
                return 400, {"error": "id must name an add-on added from the catalogue"}
            path = confine.under(manifests.LOCAL_D, f"{i}.json")
            if payload.get("agree") is not True:
                return 400, {"error": "agree to its consent text first"}
            entry = cat[i]
            _write_atomic(path, json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
            consents = _read_consents()
            consents[i] = {"at": int(time.time()), "how": "catalogue", "consent": entry["addon"]["consent"]}
            _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
            refresh_manifests()
            librarian.reload_apps()
            library_start("update", lambda: librarian.update([i], mode="update", log=lambda *_: None))
            return 200, {"accepted": i}
        if action == "remove":
            i = str(payload.get("id", ""))
            if not LOCAL_ID_RE.match(i) or not confine.under(manifests.LOCAL_D, f"{i}.json").exists():
                return 400, {"error": "id must name an added add-on"}
            try:
                librarian.remove_source(i)
            except librarian.LibrarianError:
                pass
            for p in (confine.under(manifests.ADDONS, i), confine.under(manifests.ADDONS, f".{i}.prev"), confine.under(manifests.ADDONS, f".{i}.new")):
                shutil.rmtree(p, ignore_errors=True)
            # Off first (root's, while the manifest is still there to name it), then gone.
            control_request({"action": "access", "app": i, "mode": "off"})
            confine.under(manifests.LOCAL_D, f"{i}.json").unlink(missing_ok=True)
            consents = _read_consents()
            consents.pop(i, None)
            _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
            refresh_manifests()
            return 200, {"removed": i}
        return 400, {"error": "action: add, paste, accept or remove"}
    except manifests.ManifestError as exc:
        return 400, {"error": str(exc)}
    except (ValueError, librarian.LibrarianError) as exc:
        return 409 if "already" in str(exc) else 400, {"error": str(exc)}


def kit_snapshot():
    """/admin's offline kit: the one made last (if its file is still there), the one being made,
    and how much the books would add."""
    try:
        kit = json.loads((KITS_DIR / "kit.json").read_text())
        if not (KITS_DIR / kit["name"]).is_file():
            kit = None
    except (OSError, ValueError, KeyError, TypeError):
        kit = None
    try:
        progress = json.loads(KIT_PROGRESS.read_text())
        os.kill(int(progress["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        progress = None
    books = sorted((STATE_DIR / "zim").glob("*.zim")) if (STATE_DIR / "zim").is_dir() else []
    return {"kit": kit, "progress": progress, "pending": _pending_actions("offline-kit"),
            "books": {"count": len(books), "bytes": sum(b.stat().st_size for b in books)},
            "results": control_results(5)}


USB_STATE = CONTROL_DIR / "usb.json"
USB_PROGRESS = CONTROL_DIR / "usb-progress.json"
USB_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def usb_snapshot():
    """Books on USB sticks: the root helper's last scan, the copy in flight, the hub's books."""
    try:
        scan = json.loads(USB_STATE.read_text())
    except (OSError, ValueError):
        scan = None
    progress = None
    try:
        progress = json.loads(USB_PROGRESS.read_text())
        os.kill(int(progress["pid"]), 0)
    except PermissionError:
        pass
    except (OSError, ValueError, KeyError, TypeError):
        progress = None
    books = sorted(p.stem for p in (STATE_DIR / "zim").glob("*.zim")) if (STATE_DIR / "zim").is_dir() else []
    return {"scan": scan, "progress": progress, "books": books,
            "pending": _pending_actions("usb-"), "results": control_results(5)}


def moderation_snapshot():
    now = CLOCK.ticks()
    with lock:
        msgs = live_messages(now)
    st = settings_snapshot()
    return {"now": now, "messages": list(reversed(msgs)), "board": BOARD.all_threads(),
            "drops": [store.public_meta(m) for m in DROP.list()], "queue": moderation_queue(),
            "reports": {"reasons": st["report_reasons"], "all_reasons": list(REPORT_REASONS),
                        "threshold": st["report_threshold"], "hide": st["report_hide"]}}


def forget_report(key):
    with _reports_lock:
        data = reports()
        if data.pop(key, None) is not None:
            save_reports(data)


def delete_message(created, name):
    with lock:
        msgs = load_messages()
        kept = [m for m in msgs if not (m.get("created") == created and m.get("name") == name)]
        if len(kept) == len(msgs):
            return False
        save_messages(kept)
        return True


def moderation_action(payload):
    action = payload.get("action")
    if action == "keep" and isinstance(payload.get("key"), str) and REPORT_KEY_RE.match(payload["key"]):
        # Looked at and kept: out of the queue, and shown again if it was hidden.
        with _reports_lock:
            data = reports()
            ok = payload["key"] in data
            if ok:
                data[payload["key"]]["kept"] = True
                save_reports(data)
        return (200 if ok else 404), moderation_snapshot()
    if action == "delete_message":
        ok = delete_message(payload.get("created"), payload.get("name"))
        if ok:
            forget_report(f"shoutbox:{int(payload.get('created') or 0)}:{str(payload.get('name', ''))[:40]}")
    elif action == "delete_thread" and type(payload.get("id")) is int:
        ok = BOARD.delete_thread(payload["id"])
    elif action == "delete_post" and type(payload.get("id")) is int and type(payload.get("index")) is int:
        thread = BOARD.get_thread(payload["id"])
        posts = thread["thread"]["posts"] if thread else []
        ok = BOARD.delete_post(payload["id"], payload["index"])
        if ok and 0 <= payload["index"] < len(posts):
            forget_report(f"board:{payload['id']}:{int(posts[payload['index']].get('created', 0))}")
    elif action == "delete_drop" and isinstance(payload.get("id"), str):
        ok = DROP.delete(payload["id"], force=True)  # moderation removes locked files too
    else:
        return 400, {"error": "unknown action"}
    return (200 if ok else 404), moderation_snapshot()


# --- reports (M10) ---------------------------------------------------------------------------
# Anyone may report a shoutbox message or a forum post, for one of the reasons the owner offers;
# the owner decides how many reports put it in the queue on /admin → Moderation, and whether it is
# hidden from everyone else until they have looked. A post is known by where it is and when it was
# written: shoutbox:<created>:<name>, board:<thread>:<created>. One report per visitor per post: the
# visitor is known by a hash of their address with a salt made when the hub starts, in memory only.
REPORTS_FILE = STATE_DIR / "reports.json"
_reports_lock = threading.Lock()
_report_salt = secrets.token_bytes(32)
_reported = set()       # hashes of (visitor, post): who has reported what, this run only
_report_times = {}      # visitor hash -> recent report times (at most REPORTS_PER_HOUR an hour)
REPORTS_PER_HOUR = 20
REPORT_KEY_RE = re.compile(r"^(shoutbox:\d{1,20}:.{1,40}|board:\d{1,12}:\d{1,20})$")


def reports():
    try:
        data = json.loads(REPORTS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if REPORT_KEY_RE.match(k) and isinstance(v, dict)} if isinstance(data, dict) else {}


def save_reports(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REPORTS_FILE.parent / (REPORTS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, REPORTS_FILE)


def report_key(app, ref):
    """The key of a post from what the page sends: shoutbox {created, name}; board {thread, created}."""
    if not isinstance(ref, dict):
        return None
    if app == "shoutbox" and type(ref.get("created")) in (int, float) and isinstance(ref.get("name"), str):
        key = f"shoutbox:{int(ref['created'])}:{ref['name'][:40]}"
    elif app == "board" and type(ref.get("thread")) is int and type(ref.get("created")) in (int, float):
        key = f"board:{ref['thread']}:{int(ref['created'])}"
    else:
        return None
    return key if REPORT_KEY_RE.match(key) else None


def report(app, ref, reason, addr):
    """One visitor's report. (status, message)."""
    st = settings_snapshot()
    key = report_key(app, ref)
    if key is None or reason not in st["report_reasons"]:
        return 400, "app, the post, and one of the reasons offered"
    who = hmac.new(_report_salt, addr.encode(), hashlib.sha256).digest()[:16]
    mine = hmac.new(_report_salt, who + key.encode(), hashlib.sha256).digest()[:16]
    now = time.monotonic()
    with _reports_lock:
        recent = [t for t in _report_times.get(who, []) if now - t < 3600]
        if len(recent) >= REPORTS_PER_HOUR:
            return 429, "that's a lot of reports: try again later"
        if mine in _reported:
            return 200, "already reported, thank you"
        _reported.add(mine)
        _report_times[who] = recent + [now]
        data = reports()
        r = data.setdefault(key, {"app": app, "count": 0, "reasons": {}, "first": CLOCK.ticks()})
        r["count"] += 1
        r["reasons"][reason] = r["reasons"].get(reason, 0) + 1
        r["last"] = CLOCK.ticks()
        save_reports(data)
    return 200, "reported, thank you: the owner will look"


def hidden_by_reports():
    """The posts hidden from everyone else until the owner looks (if they chose that)."""
    st = settings_snapshot()
    if not st["report_hide"]:
        return set()
    return {k for k, r in reports().items() if r.get("count", 0) >= st["report_threshold"] and not r.get("kept")}


def moderation_queue():
    """The reported posts that count (as many reports as the owner asked), with what they say."""
    st, now = settings_snapshot(), CLOCK.ticks()
    with lock:
        msgs = {f"shoutbox:{int(m.get('created', 0))}:{str(m.get('name', ''))[:40]}": m for m in live_messages(now)}
    posts = {}
    for t in BOARD.all_threads()["threads"]:
        for i, p in enumerate(t["posts"]):
            posts[f"board:{t['id']}:{int(p.get('created', 0))}"] = (t, i, p)
    out = []
    for k, r in reports().items():
        if r.get("kept") or r.get("count", 0) < st["report_threshold"]:
            continue
        if k in msgs:
            m = msgs[k]
            out.append(dict(r, key=k, by=m.get("name", ""), text=m.get("text", ""), where="Shoutbox"))
        elif k in posts:
            t, i, p = posts[k]
            out.append(dict(r, key=k, by=p.get("author", ""), text=p.get("text", ""), where=f"Board: {t.get('title', '')}",
                            thread=t["id"], index=i))
    out.sort(key=lambda x: (-x["count"], -x.get("last", 0)))
    return out


def store_snapshot():
    return {"now": CLOCK.ticks(), "saves": [store.public_meta(m) for m in STORE.list_saves()], "usage": STORE.usage(),
            "drop": DROP.usage()}


def store_action(payload):
    action, key = payload.get("action"), str(payload.get("id", ""))
    if action in ("rename", "delete") and not store.ID_RE.match(key):
        return 400, {"error": "bad id"}
    try:
        if action == "rename":
            name = str(payload.get("name", "")).strip()[:store.MAX_NAME]
            if not name or STORE.rename_save(key, name, force=True) is None:
                return 404, {"error": "no such save, or no name"}
        elif action == "delete":
            STORE.delete_save(key, force=True)
        elif action == "clear":
            STORE.clear_namespace(str(payload.get("namespace", "")))
        else:
            return 400, {"error": "unknown action"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, store_snapshot()


def system_status():
    """Memory and disk for the box tile, in bytes. MemAvailable, not MemFree: the page
    cache Kiwix leans on counts as available, and is the first thing to go (plan §2)."""
    out = {}
    try:
        with open("/proc/meminfo") as fh:
            mem = {k: int(v.split()[0]) * 1024 for k, v in (l.split(":", 1) for l in fh)}
        out["mem_total"], out["mem_available"] = mem["MemTotal"], mem["MemAvailable"]
    except (OSError, KeyError, ValueError):
        pass
    try:
        st = os.statvfs(STATE_DIR)
        out["disk_total"], out["disk_free"] = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    except OSError:
        pass
    return out


def save_messages(msgs):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.parent / (DATA_FILE.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(msgs, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, DATA_FILE)


def thread_id(path):
    """Extract N from /board/thread/N, or None if the path is not one."""
    prefix = "/board/thread/"
    if not path.startswith(prefix):
        return None
    try:
        return int(path[len(prefix):].strip("/"))
    except ValueError:
        return None


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class HubServer(ThreadingHTTPServer):
    # socketserver listens with a queue of 5. A burst of new connections -- a page load from
    # several guests, or a proxy that does not reuse them -- overflowed it on the Lyra and
    # waited out SYN retries: p99 over 2 s (notes: 2026-10-02-caddy-vs-nginx-benchmark).
    request_queue_size = 64


# F2: whatever part of a request's body a route leaves unread is read away (or the connection
# closed) after it answers, so the web server's kept-alive connection never carries it into the
# next request. The doctor's F2 check looks for this marker.
DRAINS_REQUEST_BODIES = True
MAX_JSON = 256 * 1024  # the largest JSON body the hub reads (F15); the store and drop have their own
# More unread than this, and the connection is closed rather than read. Closing early is the
# rougher choice (a client still sending sees a reset, and may lose the answer), so it is kept
# for bodies far over any the hub takes (the drop's are up to 25 MB by default).
DRAIN_MAX = 64 << 20


class _Counted:
    """The connection's reader, counting the bytes read since the request's headers."""

    def __init__(self, f):
        self._f = f
        self.count = 0

    def read(self, n=-1):
        b = self._f.read(n)
        self.count += len(b)
        return b

    def read1(self, n=-1):
        b = self._f.read1(n)
        self.count += len(b)
        return b

    def readline(self, n=-1):
        b = self._f.readline(n)
        self.count += len(b)
        return b

    def readinto(self, b):
        n = self._f.readinto(b)
        self.count += n or 0
        return n

    def __getattr__(self, name):
        return getattr(self._f, name)


class Handler(BaseHTTPRequestHandler):
    # Socket timeout per request. A phone that stalls mid-upload releases its thread
    # instead of pinning it; the web server in front already shields the listener itself.
    timeout = 30
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.rfile = _Counted(self.rfile)

    def parse_request(self):
        ok = super().parse_request()
        self.rfile.count = 0  # the body starts here
        # A body that will not be read away (chunked: the hub reads none; or too large) means
        # closing after the answer, so the answer says so, and the web server does not send its
        # next request down a connection about to close.
        self._close_after = False
        if ok:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            self._close_after = bool(self.headers.get("Transfer-Encoding")) or not 0 <= n <= DRAIN_MAX
            # Asked to close (an nginx location that sets its own headers sends Connection: close):
            # Python closes, but says nothing, and the web server would keep the connection for
            # its next request, which then fails (found with the accounts' session check).
            self._close_after = self._close_after or self.headers.get("Connection", "").strip().lower() == "close"
        return ok

    def send_response(self, code, message=None):
        super().send_response(code, message)
        if getattr(self, "_close_after", False):
            self.send_header("Connection", "close")

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            # The client went (a kept-alive connection reset while idle, or a client that
            # closed without reading the answer): a closed connection, not the hub's error.
            self.close_connection = True
            return
        if not self.close_connection:
            self._discard_body()

    def log_message(self, fmt, *args):
        pass  # quiet

    def send_json(self, code, data, headers=()):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_backup(self, with_syncthing=False):
        """The hub's state as a .tar.gz, streamed: notes, saves, board, shoutbox, settings,
        the library's sources and the clock -- everything but BACKUP_SKIP, and Syncthing's
        identity only when asked for. BACKUP-CONTENTS.txt at the top says which this is."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        kind = "with-syncthing-keys" if with_syncthing else "state"
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Disposition", f'attachment; filename="irate-box-{kind}-{stamp}.tar.gz"')
        self.send_header("Connection", "close")  # no length known in advance
        self.end_headers()
        self.close_connection = True

        skip = BACKUP_SKIP if with_syncthing else BACKUP_SKIP + SYNCTHING_DIRS

        def keep(info):
            rel = info.name.split("/", 1)[1] if "/" in info.name else ""
            if any(rel == s or rel.startswith(s + "/") for s in skip) or rel.endswith(".tmp"):
                return None
            return info

        if with_syncthing:
            about = ("This backup CONTAINS SYNCTHING'S PRIVATE KEYS AND CONFIG\n"
                     "(.local/state/syncthing: key.pem, https-key.pem, config.xml).\n"
                     "Anyone holding this file can pose as this box to its Syncthing peers.\n"
                     "Keep it as you would a password; delete copies you do not need.\n")
        else:
            about = ("Syncthing's identity (private keys, config.xml) is NOT in this backup.\n"
                     "Restored on a box, Syncthing starts with a new device ID, and its peers\n"
                     "need to be paired again.\n")
        about = (f"Irate-Box state backup, {stamp} UTC, from {hub_version()}.\n\n{about}\n"
                 "Left out: ZIM books and archived versions (they come from their sources),\n"
                 "the GitHub token, and transient files.\n")
        try:
            with tarfile.open(fileobj=self.wfile, mode="w|gz") as tar:
                data = about.encode()
                info = tarfile.TarInfo("irate-box-state/BACKUP-CONTENTS.txt")
                info.size, info.mtime, info.mode = len(data), int(time.time()), 0o644
                tar.addfile(info, io.BytesIO(data))
                tar.add(STATE_DIR, arcname="irate-box-state", filter=keep)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_tailscale(self):
        """What the operator asked for, and what the unit is doing. They differ for the
        second or two the path unit takes to act, and permanently if it is not set up."""
        proxied = "X-Forwarded-For" in self.headers
        state = next((s["state"] for s in service_status(proxied)
                      if s["name"] == TAILSCALE_NAME), "missing")
        self.send_json(200, {"want": read_tailscale_want(), "state": state,
                             "max_hours": TAILSCALE_MAX_HOURS})

    def send_empty(self, code):
        # Under HTTP/1.1 keep-alive every response needs a length, or the client
        # waits for a body that never comes.
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_captive_probe(self):
        host = self.headers.get("Host", "").split(":")[0]
        path = self.path.split("?")[0]
        return host in CAPTIVE_HOSTS or path in CAPTIVE_PATHS

    def _redirect_to_hub(self):
        # A redirect, and a page as well: some phones act only on a 3xx, others only on a
        # 200 with content where they expected an empty 204, so the probe gets both -- a 302
        # whose body is a page that goes to the hub by itself.
        # A guest on the hotspot goes to the hub's own address (not "/" under whatever name they typed).
        target = HUB_URL if HUB_HOST or not self._from_hotspot() else HOTSPOT_URL
        url = html.escape(target, quote=True)
        body = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Irate-Box</title>'
                f'<meta http-equiv="refresh" content="0; url={url}"></head>'
                f'<body><p><a href="{url}">Open the hub</a></p></body></html>').encode()
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _discard_body(self):
        """Read away what is left unread of this request's body when it is small, or close the
        connection. Left in the stream, the web server's kept-alive connection would carry it
        into the next request (F2's mechanism: the next request fails, or is another). Runs
        after every request (handle_one_request), and before a refusal that reads nothing."""
        headers = getattr(self, "headers", None)
        if headers is None:
            return
        try:
            n = int(headers.get("Content-Length") or 0) - self.rfile.count
        except ValueError:
            n = -1
        if getattr(self, "_close_after", False) or n < 0:
            self.close_connection = True
        else:
            try:
                while n > 0:
                    got = len(self.rfile.read(min(n, 1 << 16)))
                    if not got:
                        break
                    n -= got
            except OSError:  # it never came: the socket timed out
                self.close_connection = True

    def _admin_refused(self, path):
        """An /admin request that did not come through the front's /admin route: 403."""
        if not FRONT_SECRET or not path.startswith("/admin"):
            return False
        if hmac.compare_digest(self.headers.get(FRONT_HEADER, "").encode(), FRONT_SECRET.encode()):
            return False
        self._discard_body()
        self.send_json(403, {"error": "/admin only through the web server in front"})
        return True

    # --- accounts (accounts.py, step 16): the session cookie, and whether the request came over HTTPS

    def _https(self):
        """Over HTTPS: the web server says so (X-Forwarded-Proto, which it always sets, replacing a
        guest's own). Run bare, the hub is plain HTTP."""
        return "X-Forwarded-For" in self.headers and self.headers.get("X-Forwarded-Proto") == "https"

    def _irate_check(self, path):
        """The front's questions (nginx auth_request, Caddy forward_auth), never with a body.
        /_irate/admin: is this an admin account's session? (Asked beside the box's own login for
        /admin, the shell, Syncthing and private apps: either will do.) Yes too while the box is
        unclaimed, for /admin alone (X-Original-URI), which then shows only the set-the-password page
        and asks no login (nginx's satisfy any would otherwise refuse it); never for the shell.
        /_irate/user: is this visitor logged in? (An app in users mode, access.py.) No: 401 for
        nginx (its gate sends them to log in), or for Caddy (?redirect=1) the redirect itself,
        back to where they were going on this origin.
        Caddy's admin gate (access.caddy_admin_gate) asks /_irate/admin?soft=1 (the box's own login
        on: always 204, and X-Irate-Session: admin for an admin account's session, which skips the
        login after it) or ?redirect=1 (the own login off: the same, or the redirect). Not "yes" for
        an unclaimed box there: Caddy's /admin lets that through itself, and the shell and Syncthing
        must not be."""
        me = accounts.session(self._session_token())
        query = self.path.partition("?")[2].split("&")
        admin = me is not None and me.get("role") == "admin"
        if path == "/_irate/admin" and ("soft=1" in query or "redirect=1" in query):
            if admin or "soft=1" in query:
                self.send_response(204)
                self.send_header("X-Irate-Session", "admin" if admin else "")   # always: see git-access
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            ok = False
        elif path == "/_irate/admin":
            # Unclaimed: /admin shows only the set-the-password page, so it asks no login; the shell,
            # Syncthing and private apps behind the same gate must not open (nginx says which page).
            uri = self.headers.get("X-Original-URI", "")
            ok = admin or (unclaimed() and (uri == "/admin" or uri.startswith(("/admin/", "/admin?"))))
        else:
            ok = me is not None
        if ok:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif "redirect=1" in query:
            back = self.headers.get("X-Forwarded-Uri", "/")
            nxt = back if back.startswith("/") and not back.startswith("//") else "/"
            self.send_response(302)
            self.send_header("Location", "/account.html?next=" + quote(nxt, safe="/"))
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_json(401, {"error": "an admin's login" if path == "/_irate/admin" else "log in first"})

    def _signed_in(self):
        return accounts.session(self._session_token()) is not None if self._session_token() else False

    def _session_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE:
                return v
        return ""

    def _session_cookie(self, token, max_age):
        return ("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"
                + ("; Secure" if self._https() else ""))

    def _client_addr(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]

    def _account_post(self, payload):
        """/api/account: sign up, log in or out, change a password, set one with a code. A page
        elsewhere can't make these (the header, as /admin's), nor is a password taken over plain
        HTTP when the admin has prevented it."""
        site = self.headers.get("Sec-Fetch-Site")
        origin = self.headers.get("Origin")
        if self.headers.get("X-Irate-Account") != "1" or (site and site not in ("same-origin", "none")) \
                or (origin and urlparse(origin).netloc != self.headers.get("Host", "")):
            self.send_json(403, {"error": "this must come from the box's own account page"})
            return
        action, addr = payload.get("action"), self._client_addr()
        if action == "logout":
            accounts.logout(self._session_token())
            self.send_json(200, {"me": None}, [self._session_cookie("", 0)])
            return
        if action in ("signup", "login", "password", "code") and not self._https() and accounts.settings()["http"] == "prevented":
            self.send_json(403, {"error": "this box takes passwords only over HTTPS: open this page with https://", "https": False})
            return
        try:
            if action == "signup":
                state = accounts.signup(payload.get("name"), payload.get("password"), addr)
                if state == "asked":
                    self.send_json(200, {"state": "asked"})
                    return
                action = "login"
            if action == "login":
                token, me = accounts.login(payload.get("name"), payload.get("password"), addr, https=self._https())
                self.send_json(200, {"me": me}, [self._session_cookie(token, accounts.SESSION_DAYS * 86400)])
            elif action == "password":
                accounts.change_password(self._session_token(), payload.get("old"), payload.get("new"), addr)
                self.send_json(200, {"changed": True})
            elif action == "code":
                name = accounts.use_code(payload.get("code"), payload.get("password"), addr)
                self.send_json(200, {"name": name})
            else:
                self.send_json(400, {"error": "action is signup, login, logout, password or code"})
        except accounts.Wait as exc:
            self.send_json(429, {"error": str(exc)})
        except accounts.AccountError as exc:
            self.send_json(400, {"error": str(exc)})

    def _forged(self, path):
        """An /admin POST a page elsewhere could have made (S3): it must carry X-Irate-Admin,
        which a cross-site form cannot send and a cross-site fetch cannot without a preflight
        the hub never answers; and when the browser says where it came from (Origin,
        Sec-Fetch-Site), that must be this host. Browsers send a cached login with a forged
        form, so the login alone does not stop it."""
        if not path.startswith("/admin"):
            return False
        ok = self.headers.get("X-Irate-Admin") == "1"
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            ok = False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
            ok = False
        if not ok:
            self._discard_body()
            self.send_json(403, {"error": "an /admin change must come from the /admin page itself"})
        return not ok

    def _admin_locked(self, path):
        """On an unclaimed box, every admin path but the setup page answers 403."""
        if path.startswith("/admin") and unclaimed() and path not in SETUP_PATHS:
            self._discard_body()
            self.send_json(403, {"error": "no admin password has been chosen yet: open /admin/"})
            return True
        return False

    def _off_hub_name(self):
        """A hotspot guest asking under a name that is not the hub's own (see AP_NET)."""
        net, host = ap_net(), hub_host()
        if not (net and host) or self.headers.get("Host", "").split(":")[0] == host:
            return False
        return self._from_hotspot(net)

    def _from_hotspot(self, net=None):
        net = net or ap_net()
        if not net:
            return False
        fwd = self.headers.get("X-Forwarded-For", "")
        addr = fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]
        try:
            return ipaddress.ip_address(addr) in net
        except ValueError:
            return False

    def _send_kit(self):
        """The offline kit, streamed (with books it runs to gigabytes)."""
        try:
            kit = json.loads((KITS_DIR / "kit.json").read_text())
            name = kit["name"]
            if not re.fullmatch(r"irate-box-kit-[A-Za-z0-9._-]{1,120}\.tar", name):
                raise ValueError(name)
            fh = open(KITS_DIR / name, "rb")
        except (OSError, ValueError, KeyError, TypeError):
            self.send_json(404, {"error": "no offline kit yet: make one first"})
            return
        with fh:
            size = os.fstat(fh.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/x-tar")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                shutil.copyfileobj(fh, self.wfile, 1 << 20)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_source(self):
        """The installed code as a tarball, named after its version (/about.html links it)."""
        try:
            data = SOURCE_TARBALL.read_bytes()
        except OSError:
            self.send_json(404, {"error": "the source tarball is made when irate-box is installed; "
                                          "this one was not (a development checkout?)"})
            return
        try:  # the name install.sh gave it, the same as on the git server and in the drop
            name = (SOURCE_TARBALL.parent / "name").read_text().strip()
        except OSError:
            name = ""
        if not re.fullmatch(r"irate-box-source-[A-Za-z0-9._-]{1,80}\.tar\.gz", name):
            ver = re.sub(r"[^A-Za-z0-9._-]", "", hub_version().split(" ")[0]) or "unknown"
            name = f"irate-box-source-{ver}.tar.gz"
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        note_client(self)
        refresh_manifests()
        if self._is_captive_probe() or self._off_hub_name():
            self._redirect_to_hub()
            return

        path = self.path.split("?")[0]
        if self._admin_refused(path) or self._admin_locked(path):
            return

        if path.startswith("/flasher/") or path == "/flasher":
            self._flasher(path)
            return

        if path in ("/source", "/source/", "/source/irate-box-source.tar.gz"):
            self._send_source()
            return

        if path in ("/_irate/admin", "/_irate/user"):
            self._irate_check(path)
            return

        if path == "/api/account":
            # The account page's view: what the admin allows, and who this is (accounts.py).
            self.send_json(200, dict(accounts.settings(), https=self._https(), me=accounts.session(self._session_token())))
            return

        if path == "/admin/accounts":
            self.send_json(200, accounts_view())
            return

        if path == "/admin/setup":
            query = dict(p.partition("=")[::2] for p in self.path.partition("?")[2].split("&") if p)
            self.send_json(200, setup_status(query.get("id", "")[:40]))
            return

        if store.handle(self, "GET", path, STORE, DROP):
            return

        if path == "/messages":
            now = CLOCK.ticks()
            with lock:
                msgs = live_messages(now)
            hide = hidden_by_reports()
            if hide:
                msgs = [m for m in msgs if f"shoutbox:{int(m.get('created', 0))}:{str(m.get('name', ''))[:40]}" not in hide]
            self.send_json(200, {"now": now, "ttl": SHOUT_TTL, "messages": msgs, "posting": self._posting_view("shout"),
                                 "report_reasons": settings_snapshot()["report_reasons"]})
            return

        if path == "/board/threads":
            self.send_json(200, dict(BOARD.list_threads(), posting=self._posting_view("board")))
            return

        if path == "/admin/folders":
            self.send_json(200, folders_snapshot())
            return

        if path == "/admin/status-tiles":
            self.send_json(200, status_tiles_snapshot())
            return

        if path == "/admin/apps":
            # The Apps and Folders groups of /admin (menu overhaul M4): which sections each app owns.
            # switch: whether its access can be set (its page then starts with it, M6).
            self.send_json(200, {"apps": [dict(a, switch=a["id"] in access.ROUTED or a["local"]) for a in manifests.admin_apps(MANIFESTS)]})
            return

        if path == "/admin/settings":
            self.send_json(200, settings_snapshot())
            return

        if path == "/admin/tailscale":
            self._send_tailscale()
            return

        if path == "/admin/library":
            snap = library_snapshot()
            if "books=0" in self.path.partition("?")[2].split("&"):
                # The Books page asks for its books a page at a time (/admin/books): leave them out.
                snap["sources"] = [x for x in snap["sources"] if x.get("kind") == "app"]
                snap["status"] = {k: v for k, v in snap["status"].items() if k in {x["name"] for x in snap["sources"]}}
            self.send_json(200, snap)
            return

        if path == "/admin/catalogue":
            # Kiwix's online catalogue, to pick books from (librarian.catalogue_search): online only.
            query = {k: unquote(v.replace("+", " ")) for k, v in (p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)}
            try:
                self.send_json(200, librarian.catalogue_search(q=query.get("q", ""), language=query.get("language", ""),
                                                               category=query.get("category", ""), start=int(query.get("start") or 0)))
            except ValueError:
                self.send_json(400, {"error": "start is a number"})
            except librarian.LibrarianError as exc:
                self.send_json(502, {"error": f"Kiwix's catalogue could not be read ({exc}). It needs the internet."})
            return

        if path == "/admin/books":
            query = {k: unquote(v.replace("+", " ")) for k, v in (p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)}
            try:
                self.send_json(200, librarian.books_page(q=query.get("q", "")[:100], language=query.get("language", "")[:20],
                                                         state=query.get("state", "") if query.get("state") in librarian.BOOK_STATES else "",
                                                         kept=query.get("kept", ""), sort=query.get("sort", "title"),
                                                         page=int(query.get("page") or 1), per_page=int(query.get("per_page") or librarian.PER_PAGE),
                                                         names_only=query.get("names") == "1"))
            except ValueError:
                self.send_json(400, {"error": "page and per_page are numbers"})
            return

        if path == "/admin/box":
            self.send_json(200, admin_box("X-Forwarded-For" in self.headers))
            return

        if path == "/admin/moderation":
            self.send_json(200, moderation_snapshot())
            return

        if path == "/admin/store":
            self.send_json(200, store_snapshot())
            return

        if path == "/admin/backup":
            self._send_backup(with_syncthing="syncthing=1" in self.path.partition("?")[2].split("&"))
            return

        if path == "/admin/update":
            self.send_json(200, update_snapshot())
            return

        if path == "/admin/security":
            self.send_json(200, security_snapshot())
            return

        if path == "/admin/hotspot":
            self.send_json(200, hotspot.snapshot())
            return

        if path == "/admin/network":
            self.send_json(200, network_snapshot())
            return

        if path == "/admin/health":
            self.send_json(200, health_snapshot())
            return

        if path == "/admin/addons":
            self.send_json(200, addons_snapshot())
            return

        if path == "/admin/local-addons":
            self.send_json(200, local_addons_snapshot())
            return

        if path == "/admin/kit":
            self.send_json(200, kit_snapshot())
            return

        if path == "/admin/kit/download":
            self._send_kit()
            return

        if path == "/admin/access":
            state = access.read(ACCESS_STATE)
            vis = visibility()
            self.send_json(200, {"apps": [dict(a, mode=access.mode_of(state, a["id"]), visible=vis.get(a["id"], "auto"))
                                          for a in access.apps(MANIFESTS)],
                                 "pages": {i: vis.get(i, "auto") for i in PAGE_APPS},
                                 "results": control_results()})
            return

        if path == "/admin/usb":
            self.send_json(200, usb_snapshot())
            return

        if path == "/admin/git":
            self.send_json(200, git_snapshot())
            return

        if path == "/admin/ci":
            self.send_json(200, ci.snapshot())
            return

        if path == "/admin/kits":
            from irate_box.library import toolkits
            snap = toolkits.snapshot()
            snap["results"] = [r for r in control_results(20)]
            # What the security doctor flags about a kit's cache, shown before an install (Tom,
            # 2026-10-06: upgrades are fine "unless it's a flagged issue").
            try:
                audit = json.loads((CONTROL_DIR / "security-audit.json").read_text())
                snap["flags"] = {i["about"]["key"]: i["titles"] for i in (audit.get("joint") or {}).get("items", [])
                                 if i["about"]["kind"] == "kit"}
                snap["flag_details"] = {f["about"]["key"]: f["detail"] for st in audit.get("steps", []) for f in st["findings"]
                                        if (f.get("about") or {}).get("kind") == "kit" and f["status"] != "ok"}
            except (OSError, ValueError, KeyError, TypeError):
                snap["flags"], snap["flag_details"] = {}, {}
            self.send_json(200, snap)
            return

        if path == "/admin/firmware":
            snap = firmware.snapshot()
            snap.update(running=librarian.is_running(), progress=librarian.progress())
            self.send_json(200, snap)
            return

        if path == "/admin/factory":
            self.send_json(200, factory.snapshot())
            return

        if path == "/admin/mesh":
            # The decoder bridge (step 18): everything it heard, the messages too, and the channels
            # by name (never their keys).
            self.send_json(200, dict(MESH.heard.view(texts=True), state=MESH.state, error=MESH.error,
                                     channels=meshbridge.channels_view()))
            return

        if path == "/admin/tls":
            # HTTPS (step 15): the root helper's copy of what it made (root/tls.py status).
            try:
                st = json.loads((CONTROL_DIR / "tls" / "status.json").read_text())
            except (OSError, ValueError):
                st = {"set_up": False}
            self.send_json(200, dict(st, pending=_pending_actions("tls-"), results=control_results(5)))
            return

        if path == "/admin/factory/targets":
            # A source's refs, and its targets at one (factory.py): read from its local copy.
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            try:
                src = factory.source(unquote(query.get("source", "")))
                out = {"source": src["name"], "refs": factory.refs(src)}
                if query.get("ref"):
                    out.update(factory.targets(src, unquote(query["ref"])))
                    out["suggested"] = factory.suggested(out["targets"], factory._flasher_boards())
                self.send_json(200, out)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/admin/ci/run":
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            try:
                offset = int(query.get("from", "0"))
            except ValueError:
                offset = 0
            view = ci.run_view(unquote(query.get("run", "")), offset)
            self.send_json(*((200, view) if view else (404, {"error": "no such build"})))
            return

        if path == "/admin/ci/file":
            # A build's log (shown as text) or one of its artifacts (a download, never shown).
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            name = unquote(query.get("name", ""))
            f = ci.run_file(unquote(query.get("run", "")), name)
            if not f:
                self.send_json(404, {"error": "no such build file"})
                return
            body = f.read_bytes()
            self.send_response(200)
            if name == "log.txt":
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            else:
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/admin", "/admin/"):
            path = "/admin-setup.html" if unclaimed() else "/admin.html"

        if path == "/api/captive":
            # RFC 8908's captive-portal API, named by the hotspot's DHCP (option 114, RFC 8910):
            # tells a phone outright that this network has a sign-in page and where, so it need
            # not guess from probes. captive stays true: the box never gives "internet".
            body = json.dumps({"captive": True, "user-portal-url": HUB_URL if HUB_HOST else "/"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/captive+json")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/menus.json":
            # The list pages, for the hub bar's ↑ (app.js): {id: {title, href}}, public ones only.
            hidden = hidden_apps()
            self.send_json(200, {i: {"title": mm["menu"]["title"], "href": mm["tile"]["href"]}
                                 for i, mm in manifests.menus(MANIFESTS).items() if i not in hidden})
            return

        if path == "/internal/git-access":
            # nginx's auth_request for a push or fetch (gitrepos.decide): 204 if no login is needed
            # or the request's own credentials are an account's that may; 403 if only the box's own
            # login will do (nginx then asks for it). Anything on loopback may ask what a guest may
            # do; an account's answer needs its password.
            mode = access.mode_of(access.read(ACCESS_STATE), "git")
            account = accounts.check_basic(self.headers.get("Authorization", ""), self.headers.get("X-Forwarded-For", ""))
            ok = gitrepos.decide(self.headers.get("X-Original-URI", ""), self.headers.get("X-Original-Method", "GET"), mode, account)
            if "soft=1" in self.path.partition("?")[2].split("&"):
                # Caddy's git routes (no satisfy any): always 204, saying "ok" when the box's own login
                # isn't needed, and the account's name for the push hook's REMOTE_USER (nginx passes
                # $remote_user). Credentials that aren't an account's (the box's own) aren't
                # vouched for here: Caddy's login checks them, so a wrong password isn't let through.
                # Both always sent, empty for nothing: Caddy 2.6's copy_headers otherwise sets the
                # placeholder's own text ({http.reverse_proxy.header.…}) on the request.
                vouched = ok and (account is not None or not self.headers.get("Authorization"))
                self.send_response(204)
                self.send_header("X-Irate-Session", "ok" if vouched else "")
                self.send_header("X-Irate-User", account["name"] if vouched and account is not None else "")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_empty(204 if ok else 403)
            return

        if path == "/mesh.json":
            # The mesh heard through the box's broker (meshbridge.py): nodes and traffic, never the
            # messages; only while the MQTT app is public (/admin → Access).
            if access.mode_of(access.read(ACCESS_STATE), "mqtt") != "public":
                self.send_json(404, {"error": "not shown"})
                return
            self.send_json(200, dict(MESH.heard.view(texts=False), state=MESH.state))
            return

        if path in ("/certificate", "/certificate.json", "/certificate/ca.crt"):
            # HTTPS (step 15): the box's CA for guests to install, and what the page says of it.
            # Public: only the CA's public certificate and facts about it, never a key.
            if path == "/certificate":
                self.send_response(302)
                self.send_header("Location", "/certificate.html")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                st = json.loads((CONTROL_DIR / "tls" / "status.json").read_text())
            except (OSError, ValueError):
                st = {"set_up": False}
            if path == "/certificate.json":
                cert = st.get("cert") or {}
                self.send_json(200, {"set_up": bool(st.get("set_up")), "on": bool(st.get("on")), "port": (st.get("ports") or {}).get("main", 443),
                                     "fingerprint": (st.get("ca") or {}).get("fingerprint"), "names": cert.get("names", []),
                                     "expires": cert.get("expires")})
                return
            try:
                body = (CONTROL_DIR / "tls" / "ca.crt").read_bytes()
            except OSError:
                self.send_json(404, {"error": "no certificate yet"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-x509-ca-cert")
            self.send_header("Content-Disposition", 'attachment; filename="irate-box-ca.crt"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/factory.json", "/factory/file"):
            # The Firmware Factory's tile (factory.py public_view): only while the owner shows it,
            # and only its downloadable files (ESP32 images and zips, UF2s), never logs or sources.
            if not settings_snapshot()["factory_tile"]:
                self.send_json(404, {"error": "not shown"})
                return
            if path == "/factory.json":
                self.send_json(200, factory.public_view())
                return
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            name = unquote(query.get("name", ""))
            f = factory.public_file(unquote(query.get("run", "")), name)
            if not f:
                self.send_json(404, {"error": "no such file"})
                return
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/layout.css":
            # The owner's page widths for every page (M2): public, tiny, read after style.css.
            # No file of that name in web/, so both web servers hand it to the hub.
            with _settings_lock:
                cur = dict(_settings)
            body = (":root { --page-width: %drem; --shout-width: %drem; --board-width: %drem; }\n"
                    % (cur["page_width"], cur["shout_width"], cur["board_width"])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/status":
            # The web server's proxy adds X-Forwarded-For; a direct hit has none.
            proxied = "X-Forwarded-For" in self.headers
            now = CLOCK.ticks()
            payload = {
                "proxied": proxied,
                "services": service_status(proxied),
                "system": system_status(),
                "uptime": now,
                "online": online_count(now),
                "settings": settings_snapshot(),
                # The About page shows it beside the source offer (the tarball is named after it).
                "version": hub_version(),
            }
            joined = joined_count()
            if joined is not None:
                payload["joined"] = joined
            # Different devices today and this week (M11): the numbers only, while counting is on.
            counted = visitor_counts()
            if counted is not None:
                payload["visitors"] = counted
            self.send_json(200, payload)
            return

        tid = thread_id(path)
        if tid is not None:
            result = BOARD.get_thread(tid)
            if result is None:
                self.send_json(404, {"error": "no such thread"})
            else:
                hide = hidden_by_reports()
                if hide:
                    t = dict(result["thread"])
                    t["posts"] = [p if f"board:{t['id']}:{int(p.get('created', 0))}" not in hide
                                  else dict(p, text="(hidden while the owner looks at a report)", hidden=True) for p in t["posts"]]
                    result = dict(result, thread=t)
                self.send_json(200, dict(result, posting=self._posting_view("board"), report_reasons=settings_snapshot()["report_reasons"]))
            return

        if path in MENU_PAGES and not (MENU_PAGES[path].get("local") and MENU_PAGES[path]["id"] in hidden_apps()):
            body = menu_page(MENU_PAGES[path], self._signed_in())
            self.send_response(200)
            self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            body = home_page(self._signed_in())
            self.send_response(200)
            self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        file_path = STATIC / path.lstrip("/")

        if not file_path.resolve().is_relative_to(STATIC.resolve()):
            self.send_empty(403)
            return

        if file_path.is_file():
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(file_path.suffix, "application/octet-stream"))
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_empty(404)

    def _read_payload(self):
        """The request's JSON, or None. Larger than MAX_JSON is not read at all (F15): the
        caller answers 413 and the drain reads it away or closes the connection."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None
        if length > MAX_JSON:
            self._too_large = True
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def do_POST(self):
        note_client(self)
        refresh_manifests()
        path = self.path.split("?")[0]

        # Delegated before the body is read: the store takes raw bytes, and the
        # Excalidraw frontend sends no Content-Type for JSON to be parsed from.
        if store.handle(self, "POST", path, STORE, DROP):
            return

        if path in ("/_irate/admin", "/_irate/user"):
            # The front's checks, whatever the method: never a body to read.
            self._irate_check(path)
            return

        if self._admin_refused(path) or self._forged(path) or self._admin_locked(path):
            return
        self._too_large = False
        payload = self._read_payload()
        if not isinstance(payload, dict):  # None, or a JSON array or string (F26)
            if self._too_large:
                self.send_json(413, {"error": f"a request to the hub is at most {MAX_JSON >> 10} KB"})
                return
            self.send_json(400, {"error": "bad request"})
            return

        if path == "/api/account":
            self._account_post(payload)
            return

        if path == "/admin/accounts":
            # Accounts (accounts.py): the sign-up level and the HTTP stance; accept, disable,
            # enable, delete, the role; a new account or a reset, each with a one-time code.
            action = payload.get("action")
            try:
                keep = not admin_login_on()
                if action == "admin-login":
                    # The box's own login on or off: root's (the web server's login file), checked there too.
                    self.send_json(202, {"id": control_request({"action": "admin-login", "on": payload.get("on") is True})})
                    return
                if action == "settings":
                    out = {"settings": accounts.set_settings(payload.get("signup"), payload.get("http"), keep_admin=keep)}
                elif action == "make":
                    out = {"code": accounts.make(payload.get("name"), payload.get("role", "user"))}
                elif action == "reset":
                    out = {"code": accounts.reset(payload.get("name"))}
                else:
                    accounts.change(payload.get("name"), action, keep_admin=keep)
                    out = {}
            except accounts.AccountError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, dict(out, **accounts_view()))
            return

        if path == "/admin/setup":
            pw = payload.get("password")
            if not unclaimed():
                self.send_json(403, {"error": "the admin password has already been set"})
            elif not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= 128) or "\n" in pw:
                self.send_json(400, {"error": f"the password must be {MIN_PASSWORD}-128 characters"})
            else:
                self.send_json(202, {"id": control_request({"action": "password", "password": pw, "setup": True})})
            return

        if path == "/admin/settings":
            with _settings_lock:
                was_counting = _settings.get("visitor_counts")
                for key in DEFAULT_SETTINGS:
                    if valid_setting(key, payload.get(key)):
                        _settings[key] = payload[key]
                current = dict(_settings)
                save_settings(current)
                apply_settings(current)
                if current["visitor_counts"] != was_counting or not VISITORS_WANT.exists():
                    write_visitors_want(current["visitor_counts"])
            self.send_json(200, current)
            return

        if path == "/admin/tailscale":
            mode, hours = payload.get("mode"), payload.get("hours")
            if mode not in ("off", "on", "timed") or (mode == "timed" and not (
                    type(hours) is int and 1 <= hours <= TAILSCALE_MAX_HOURS)):  # not bool
                self.send_json(400, {"error": f"mode off, on, or timed with hours 1-{TAILSCALE_MAX_HOURS}"})
                return
            write_tailscale_want(mode, hours)
            self._send_tailscale()
            return

        if path == "/admin/library":
            self.send_json(*library_action(payload))
            return

        if path == "/admin/control":
            unit, op = payload.get("unit"), payload.get("op")
            if op not in CONTROL_OPS.get(unit, []):
                self.send_json(400, {"error": f"{op} is not offered for {unit}"})
                return
            self.send_json(202, {"id": control_request({"action": "service", "unit": unit, "op": op})})
            return

        if path == "/admin/password":
            pw = payload.get("password")
            if not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= 128) or "\n" in pw:
                self.send_json(400, {"error": f"the password must be {MIN_PASSWORD}-128 characters"})
                return
            self.send_json(202, {"id": control_request({"action": "password", "password": pw})})
            return

        if path == "/admin/moderation":
            self.send_json(*moderation_action(payload))
            return

        if path == "/admin/update":
            action = UPDATE_ACTIONS.get(payload.get("action"))
            if not action:
                self.send_json(400, {"error": f"action must be one of {', '.join(UPDATE_ACTIONS)}"})
                return
            self.send_json(202, {"id": control_request({"action": action})})
            return

        if path == "/admin/store":
            self.send_json(*store_action(payload))
            return

        if path == "/admin/git":
            self.send_json(*git_action(payload))
            return

        if path == "/admin/firmware":
            self.send_json(*firmware_action(payload))
            return

        if path == "/admin/mesh":
            action = payload.get("action")
            try:
                if action == "add-channel":
                    meshbridge.add_channel(payload.get("name"), payload.get("key"))
                elif action == "remove-channel":
                    meshbridge.remove_channel(str(payload.get("name", "")))
                else:
                    raise ValueError("action must be add-channel or remove-channel")
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, {"channels": meshbridge.channels_view()})
            return

        if path == "/admin/tls":
            action = payload.get("action")
            if action in ("on", "off"):
                self.send_json(202, {"id": control_request({"action": "tls-switch", "on": action == "on"})})
                return
            if action == "import":
                # The owner's own certificate and key, staged for the root helper (hub-only, 0600),
                # which checks and installs them and removes these copies.
                chain, key = payload.get("chain"), payload.get("key")
                if not isinstance(chain, str) or not isinstance(key, str) or len(chain) > 64 << 10 or len(key) > 16 << 10:
                    self.send_json(400, {"error": "chain and key: the PEM text of each"})
                    return
                staged = STATE_DIR / "tls-import"
                staged.mkdir(mode=0o700, exist_ok=True)
                for name, text in (("chain.pem", chain), ("key.pem", key)):
                    f = staged / name
                    f.unlink(missing_ok=True)
                    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as fh:
                        fh.write(text)
                self.send_json(202, {"id": control_request({"action": "tls-import"})})
                return
            if action == "box":
                self.send_json(202, {"id": control_request({"action": "tls-box"})})
                return
            if action == "admin-only":
                on = payload.get("on") is True
                # Only from a page that came over HTTPS: proof the owner's device trusts the box,
                # so turning it on can't lock them out of /admin.
                if on and self.headers.get("X-Forwarded-Proto") != "https":
                    self.send_json(409, {"error": "open /admin over HTTPS first (https://<this box>/admin/): only then is it safe to make it HTTPS only"})
                    return
                self.send_json(202, {"id": control_request({"action": "tls-admin-only", "on": on})})
                return
            if action not in ("make", "renew"):
                self.send_json(400, {"error": "action must be make, renew, on, off, import, box or admin-only"})
                return
            self.send_json(202, {"id": control_request({"action": f"tls-{action}", "again": payload.get("again") is True})})
            return

        if path == "/admin/factory":
            # The Firmware Factory's queue (factory.py): queue targets, cancel or move one up, pause.
            action = payload.get("action")
            try:
                if action == "queue":
                    out = factory.queue(str(payload.get("source", "")), str(payload.get("ref", "")), payload.get("targets"))
                    self.send_json(202, dict(out, snapshot=factory.snapshot()))
                    return
                if action in ("tools", "offline"):
                    out = factory.queue_tools(str(payload.get("source", "")), str(payload.get("ref", "")), str(payload.get("family", "")),
                                              offline=action == "offline", env=str(payload.get("env") or "") or None)
                    self.send_json(202, dict(out, snapshot=factory.snapshot()))
                    return
                if action == "publish":
                    factory.publish(str(payload.get("run", "")))
                elif action == "unpublish":
                    factory.unpublish(str(payload.get("version", "")), str(payload.get("env", "")))
                elif action in ("cancel", "up"):
                    (factory.cancel if action == "cancel" else factory.move_up)(str(payload.get("id", "")))
                elif action in ("pause", "resume"):
                    factory.pause(action == "pause")
                else:
                    raise ValueError("action must be queue, tools, offline, publish, unpublish, cancel, up, pause or resume")
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, factory.snapshot())
            return

        if path == "/admin/ci":
            # Build now, keep or delete a run, how many runs to keep (ci.py).
            action = payload.get("action")
            try:
                if action == "build":
                    name = str(payload.get("repo", ""))
                    if not gitrepos.NAME_RE.match(name):
                        raise ValueError("repo: a private repository's name")
                    branch = payload.get("branch")
                    out = ci.queue_build(confine.under(gitrepos.ROOT, "private", f"{name}.git"), str(branch) if branch else None)
                    self.send_json(202, dict(out, queued=True, snapshot=ci.snapshot()))
                elif action in ("keep", "unkeep", "delete"):
                    ci.queue_run_change(str(payload.get("run", "")), action)
                    self.send_json(202, {"queued": True})
                elif action == "settings":
                    keep = payload.get("keep_runs")
                    if type(keep) is not int or not 1 <= keep <= 50:
                        raise ValueError("keep_runs: 1 to 50")
                    ci.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
                    ci.SETTINGS.write_text(json.dumps({"keep_runs": keep}))
                    self.send_json(200, ci.snapshot())
                else:
                    raise ValueError("action must be build, keep, unkeep, delete or settings")
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/admin/kits":
            from irate_box.library import toolkits
            try:
                rid = toolkits.action(payload)
            except librarian.LibrarianError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            snap = toolkits.snapshot()
            if rid:
                snap["id"] = rid
            self.send_json(202 if rid else 200, snap)
            return

        if path == "/admin/usb":
            action, device = payload.get("action"), str(payload.get("device", ""))
            if action == "scan":
                self.send_json(202, {"id": control_request({"action": "usb-scan"})})
            elif action == "import" and USB_DEVICE_RE.match(device) and isinstance(payload.get("file"), str):
                self.send_json(202, {"id": control_request({"action": "usb-import", "device": device,
                                                            "file": payload["file"][:512]})})
            elif action == "export" and USB_DEVICE_RE.match(device) and isinstance(payload.get("book"), str):
                self.send_json(202, {"id": control_request({"action": "usb-export", "device": device,
                                                            "book": payload["book"][:64]})})
            elif action in ("kit-import", "kit-export") and USB_DEVICE_RE.match(device) and isinstance(payload.get("kit"), str):
                self.send_json(202, {"id": control_request({"action": f"usb-{action}", "device": device, "kit": payload["kit"][:32]})})
            else:
                self.send_json(400, {"error": "action must be scan, import (device, file), export (device, book), kit-import or kit-export (device, kit)"})
            return

        if path == "/admin/kit":
            if payload.get("action") == "make" and type(payload.get("books", False)) is bool:
                self.send_json(202, {"id": control_request({"action": "offline-kit", "books": payload.get("books", False)})})
            else:
                self.send_json(400, {"error": "action must be make (books: true or false)"})
            return

        if path == "/admin/status-tiles":
            # The box row's arrangement (M8): its tiles only, each part a list of their ids.
            data, ids = payload.get("state"), {i for i, _ in box_tiles()}
            if not isinstance(data, dict) or not all(isinstance(data.get(k, []), list) and set(data.get(k, [])) <= ids
                                                     for k in ("order", "hidden", "double")):
                self.send_json(400, {"error": "state: order, hidden and double, each a list of the box row's tiles"})
                return
            tmp = STATUS_TILES_FILE.parent / (STATUS_TILES_FILE.name + ".tmp")
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps({k: list(dict.fromkeys(data.get(k, []))) for k in ("order", "hidden", "double")}))
            os.replace(tmp, STATUS_TILES_FILE)
            self.send_json(200, status_tiles_snapshot())
            return

        if path == "/admin/folders":
            # The folders' arrangement (M7): the whole of folders.json, checked, at once.
            data = valid_folders(payload.get("state"))
            if data is None:
                self.send_json(400, {"error": "state: per folder, hidden, order and extra: entries some folder lists"})
                return
            save_folders(data)
            self.send_json(200, folders_snapshot())
            return

        if path == "/admin/visibility":
            # Who sees an app's tile (M5): the hub's own, at once; no root, no web server change.
            app, v = str(payload.get("app", "")), payload.get("visible")
            if (app not in _switched() and app not in PAGE_APPS) or v not in VISIBLE:
                self.send_json(400, {"error": "app must name an app on /admin, and visible be auto, guests, users or hidden"})
                return
            data = visibility()
            if v == "auto":
                data.pop(app, None)
            else:
                data[app] = v
            save_visibility(data)
            self.send_json(200, {"visibility": data})
            return

        if path == "/admin/access":
            app, mode = str(payload.get("app", "")), payload.get("mode")
            if (app not in access.ROUTED and not any(m.get("local") and m["id"] == app for m in MANIFESTS)) or mode not in access.MODES \
                    or (mode == "users" and not access.users_allowed(app)):
                self.send_json(400, {"error": "app must name an app on /admin, and mode be public, private or off (or users, where it can be)"})
            else:
                self.send_json(202, {"id": control_request({"action": "access", "app": app, "mode": mode})})
            return

        if path == "/admin/local-addons":
            code, body = local_addons_action(payload)
            self.send_json(code, body)
            return

        if path == "/admin/addons":
            aid = str(payload.get("addon", ""))
            if not ADDON_ID_RE.match(aid) or aid not in manifests.addons(MANIFESTS) or type(payload.get("on")) is not bool:
                self.send_json(400, {"error": "addon must name an add-on, and on be true or false"})
            else:
                self.send_json(202, {"id": control_request({"action": "addon", "addon": aid, "on": payload["on"]})})
            return

        if path == "/admin/health":
            if payload.get("action") == "scan":
                self.send_json(202, {"id": control_request({"action": "health-scan"})})
            elif payload.get("action") == "fix" and HEALTH_CHOICE_RE.match(str(payload.get("choice", ""))):
                self.send_json(202, {"id": control_request({"action": "health-fix", "choice": payload["choice"]})})
            else:
                self.send_json(400, {"error": "action must be scan, or fix with a choice the doctor offered"})
            return

        if path == "/admin/network":
            act = payload.get("action")
            if act == "scan" and (payload.get("iface") in (None, "") or IFACE_NAME_RE.match(str(payload["iface"]))):
                self.send_json(202, {"id": control_request({"action": "net-scan", "iface": payload.get("iface") or None})})
            elif act == "settings":
                try:
                    settings = uplink.validate(payload.get("settings"))
                except (ValueError, TypeError) as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                settings.pop("hold_until", None)
                self.send_json(202, {"id": control_request({"action": "uplink-set", "settings": settings})})
            elif act == "hold" and type(payload.get("minutes")) is int and 0 <= payload["minutes"] <= 1440:
                self.send_json(202, {"id": control_request({"action": "uplink-hold", "minutes": payload["minutes"]})})
            elif act == "profile" and type(payload.get("on")) is bool:
                self.send_json(202, {"id": control_request({"action": "uplink-profile", "on": payload["on"]})})
            else:
                self.send_json(400, {"error": "action must be scan, settings, hold or profile"})
            return

        if path == "/admin/hotspot" and payload.get("action") in ("on", "off", "try", "confirm"):
            # The hotspot itself (root/ap.py through the root helper): on with the owner's choices,
            # off, the try of a channel of its own beside the WiFi link, or keeping one that took it.
            req = {"action": "ap-" + payload["action"]}
            if payload["action"] == "on":
                for k in ("radio", "band", "channel", "take_radio"):
                    if payload.get(k) is not None:
                        req[k] = payload[k]
            self.send_json(202, {"id": control_request(req)})
            return

        if path == "/admin/hotspot":
            try:
                saved = hotspot.save(payload.get("settings"))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, {"settings": saved, "message": f"Saved: {hotspot.LABEL[saved['mode']]}. "
                                 "It takes effect the next time the hotspot is switched on (Network)."})
            return

        if path == "/admin/security":
            if payload.get("action") == "scan":
                self.send_json(202, {"id": control_request({"action": "security-scan"})})
            elif payload.get("action") == "audit":
                self.send_json(202, {"id": control_request({"action": "security-audit"})})
            elif payload.get("action") == "deep":
                self.send_json(202, {"id": control_request({"action": "security-deep-audit"})})
            elif payload.get("action") in ("import", "import-remove"):
                # A scan report read in the owner's browser (secimports.py); the doctor runs again.
                from irate_box.hub import secimports
                try:
                    msg = secimports.save(payload.get("report")) if payload["action"] == "import" else \
                        (secimports.remove(str(payload.get("kind", ""))) or "Removed.")
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(202, {"id": control_request({"action": "security-audit"}), "message": msg})
            elif payload.get("action") == "fix" and SECURITY_CHOICE_RE.match(str(payload.get("choice", ""))):
                self.send_json(202, {"id": control_request({"action": "security-fix", "choice": payload["choice"]})})
            else:
                self.send_json(400, {"error": "action must be scan, audit, or fix with a choice"})
            return

        if path == "/messages":
            self._post_message(payload)
            return

        if path == "/api/report":
            fwd = self.headers.get("X-Forwarded-For", "")
            addr = fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]
            code, message = report(str(payload.get("app", "")), payload.get("ref"), payload.get("reason"), addr)
            self.send_json(code, {"message": message} if code == 200 else {"error": message})
            return

        if path == "/board/threads":
            poster = self._poster("board", str(payload.get("name", "")))
            if poster is None:
                return
            try:
                result = BOARD.create_thread(poster[0],
                                             payload.get("title"),
                                             payload.get("text"),
                                             payload.get("hue"), poster[1])
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(201, result)
            return

        tid = thread_id(path)
        if tid is not None:
            poster = self._poster("board", str(payload.get("name", "")))
            if poster is None:
                return
            try:
                result = BOARD.reply(tid, poster[0], payload.get("text"),
                                     payload.get("hue"), poster[1])
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            if result is None:
                self.send_json(404, {"error": "no such thread"})
            else:
                self.send_json(201, result)
            return

        self.send_empty(404)

    def do_PUT(self):
        if not store.handle(self, "PUT", self.path.split("?")[0], STORE, DROP):
            self.send_empty(404)

    def do_DELETE(self):
        if not store.handle(self, "DELETE", self.path.split("?")[0], STORE, DROP):
            self.send_empty(404)

    def do_PATCH(self):
        if not store.handle(self, "PATCH", self.path.split("?")[0], STORE, DROP):
            self.send_empty(404)

    def do_OPTIONS(self):
        path = self.path.split("?")[0]
        if path.startswith("/flasher/api/"):
            # The flasher's CORS preflight: it runs from the guest's disk (origin null).
            self.send_response(204)
            for k, v in flasher.CORS.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not store.handle(self, "OPTIONS", path, STORE, DROP):
            self.send_empty(404)

    def _flasher(self, path):
        """The web flasher (flasher.py): the page as a download, with this hub's address in
        it, and the small API it reads. Its static files come from the web server."""
        if path in ("/flasher", "/flasher/"):
            self.send_response(302)
            self.send_header("Location", "/flasher/flasher.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/flasher/flasher.html":
            body = flasher.page(self.headers.get("Host", ""))
            if body is None:
                self.send_json(404, {"error": "the web flasher is not installed on this hub"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="meshtastic-flasher.html"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        if path.startswith("/flasher/api/"):
            code, body = flasher.api(path)
            self.send_response(code)
            for k, v in flasher.CORS.items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        self.send_json(404, {"error": "not found"})

    def _poster(self, kind, name):
        """Who is posting to the shoutbox or forum (kind "shout" or "board"), as the admin allows:
        (name, account), or None once the refusal is sent. A logged-in user posts under their
        account's name; a guest (where guests may) under any name but an account's."""
        what = "shoutbox" if kind == "shout" else "forum"
        who = settings_snapshot()[f"{kind}_who"]
        if who == "off":
            self.send_json(403, {"error": f"the {what} is closed"})
            return None
        me = accounts.session(self._session_token())
        if me:
            return me["name"], me["name"]
        if who == "users":
            self.send_json(403, {"error": f"log in to post on the {what} (/account.html)", "login": True})
            return None
        if accounts.exists(name):
            self.send_json(403, {"error": f"{name.strip()} is an account's name: log in to post as it"})
            return None
        return name, None

    def _posting_view(self, kind):
        """What a page needs to show the shoutbox or forum's posting as it is: who may post,
        whether users' names are marked, and who this is."""
        st = settings_snapshot()
        me = accounts.session(self._session_token())
        return {"who": st[f"{kind}_who"], "marks": st[f"{kind}_marks"], "me": me["name"] if me else None}

    def _post_message(self, payload):
        text = str(payload.get("text", "")).strip()[:200]
        poster = self._poster("shout", str(payload.get("name", "")).strip()[:32])
        if poster is None:
            return
        name, account = poster
        if not name or not text:
            self.send_json(400, {"error": "name and text required"})
            return

        # Optional author colour, stored as a hue only (0-359) -- the client's palette
        # picks saturation and lightness. Absent means "derive it from the name", so a
        # guest who renames re-colours instead of carrying a stale hue around.
        hue = payload.get("hue")
        if hue is not None:
            try:
                hue = int(hue) % 360
            except (TypeError, ValueError):
                hue = None

        now = CLOCK.ticks()
        entry = {
            "name": name,
            "text": text,
            "created": now,
            # Kept for debugging only. On a box with no RTC this is fiction; nothing
            # reads it, and nothing should.
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if hue is not None:
            entry["hue"] = hue
        if account:
            entry["account"] = account
        with lock:
            msgs = live_messages(now)
            msgs.append(entry)
            if len(msgs) > MAX_MESSAGES:
                msgs = msgs[-MAX_MESSAGES:]
            save_messages(msgs)

        self.send_json(201, entry)


BOOT_ID_FILE = STATE_DIR / "boot_id"


def clear_on_new_boot(boot_id_path=Path("/proc/sys/kernel/random/boot_id")):
    """Clear what the owner chose to clear at boot, once per boot. A boot is the kernel's
    boot_id changing from the one stored last time, so an update or a crash that restarts
    the hub does not wipe the room. Returns what was cleared."""
    try:
        current = boot_id_path.read_text().strip()
    except OSError:
        return []  # no /proc (a dev machine): never clear
    try:
        previous = BOOT_ID_FILE.read_text().strip()
    except OSError:
        previous = None
    if previous == current:
        return []
    cleared = []
    if previous is not None:  # the first start ever is not a reboot
        settings = settings_snapshot()
        if settings["shout_reset_on_boot"]:
            with lock:
                DATA_FILE.write_text("[]")
            cleared.append("shoutbox")
        if settings["board_reset_on_boot"]:
            BOARD.clear()
            cleared.append("board")
    BOOT_ID_FILE.write_text(current + "\n")
    return cleared


if __name__ == "__main__":
    for what in clear_on_new_boot():
        print(f"New boot: cleared the {what}, as set on /admin")
    CLOCK.start()
    # The mesh heard through the box's MQTT broker (step 18): decoded with the owner's channel keys.
    MESH.start()
    # The services' uptime (step 35): every unit's state every five minutes, for /admin's grid.
    svchistory.Sampler(lambda: [s["unit"] for s in SERVICES if "unit" in s]).start()
    # One thread per request: a 50 MB paste into the blob store must not freeze
    # everyone else's shoutbox poll. State is guarded by `lock` and the store's own.
    server = HubServer((BIND, PORT), Handler)
    print(f"Hub running at http://{BIND}:{PORT}")
    print(f"Uptime clock at {hubclock.format_age(CLOCK.ticks())} cumulative")
    try:
        server.serve_forever()
    finally:
        CLOCK.stop()
