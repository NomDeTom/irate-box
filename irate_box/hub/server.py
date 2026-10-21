#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Irate-Box hub server: shoutbox, board, blob store, status, captive-portal target.

Serves web/ itself so a bare `./irate-box server` works; behind the web server
(nginx, or Caddy) the assets come off disk and only `/` and the API reach this process.
"""

import base64
import binascii
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from irate_box import confine, finding
from irate_box.hub import (
    access,
    accounts,
    backup,
    board,
    ci,
    factory,
    flasher,
    gitrepos,
    hotspot,
    hubclock,
    jsonlog,
    kitauto,
    linkhistory,
    manifests,
    memhistory,
    meshbridge,
    notebooks,
    pagehistory,
    reach,
    refused,
    store,
    svchistory,
    uplink,
    watchlog,
)
from irate_box.library import firmware, librarian, zimcheck
from irate_box.msg import M, render

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
BIND = os.environ.get(
    "HUB_BIND", "127.0.0.1"
)  # the web server fronts it; set another address only with a protected front
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
    "/hotspot-detect.html",  # iOS / macOS
    "/library/test/success.html",  # older iOS
    "/generate_204",  # Android
    "/gen_204",  # Android's fallback probe (www.google.com); a 404 there reads as "no internet"
    "/connecttest.txt",  # Windows
    "/ncsi.txt",  # Windows (legacy)
    "/canonical.html",  # Firefox
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
    return [dict(_service_entry(m["status"]), app=m["id"]) for m in MANIFESTS if "status" in m] + [
        {
            "path": None,
            "name": TAILSCALE_NAME,
            "unit": "tailscaled.service",
            "active": True,
            "note": "switched on and off from /admin",
        },
        {
            "path": None,
            "name": f"Web server ({WEB_SERVER_NAME})",
            "unit": f"{WEB_SERVER}.service",
            "proxy": True,
        },
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
    for p in [
        manifests.LOCAL_D,
        manifests.CATALOGUE,
        *sorted(manifests.CATALOGUE.glob("*.json")),
    ]:
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
    _local_stamp["at"] = [
        p.stat().st_mtime_ns if p.exists() else None
        for p in [
            manifests.LOCAL_D,
            manifests.CATALOGUE,
            *sorted(manifests.CATALOGUE.glob("*.json")),
        ]
    ]
    MANIFESTS = manifests.load_all()
    SERVICES = _services()
    MENU_PAGES = {m["tile"]["href"]: m for m in manifests.menus(MANIFESTS).values()}
    librarian.reload_apps()
    _home_page.clear()  # both copies (guests, signed in): a new add-on's tile shows at once


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


# Who sees an app's tile, apart from who may open it (access and visibility are not the same).
# The hub's own choice, not root's: it changes which tiles the hub
# draws, not the web server's gates. auto: as its access (public: everyone; users: those logged
# in; private or off: nobody). An app that is off is never shown, whatever is chosen here.
VISIBILITY_FILE = STATE_DIR / "visibility.json"
# admin: only to an admin account signed in on the hub (four levels as a consistent set of
# chips: guests, users, admin and hidden). signed_in, in what follows,
# is the visitor's level: False (a guest), True (an account) or "admin" (an admin account).
VISIBLE = ("auto", "guests", "users", "admin", "hidden")


# The admin, seen at /admin, so admin-only apps show even with accounts off. The box's own login is the web server's, on /admin only, so the home
# page never sees it: /admin's page, which only the admin reaches, leaves a signed cookie that the
# home page reads as "the admin is here". It changes which tiles show, never who may open them.
ADMIN_SEEN_COOKIE = "irate_admin_seen"
ADMIN_SEEN_HOURS = 12
_admin_seen_key = []


def admin_seen_key():
    if not _admin_seen_key:
        f = STATE_DIR / "admin-seen.key"
        try:
            key = f.read_bytes()
        except OSError:
            key = b""
        if len(key) != 32:
            key = secrets.token_bytes(32)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(key)
        _admin_seen_key.append(key)
    return _admin_seen_key[0]


def admin_seen_value(now=None):
    until = int((now or time.time()) + ADMIN_SEEN_HOURS * 3600)
    return f"{until}.{hmac.new(admin_seen_key(), str(until).encode(), 'sha256').hexdigest()}"


def admin_seen_ok(value, now=None):
    until, _, sig = (value or "").partition(".")
    if not until.isdigit() or int(until) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, hmac.new(admin_seen_key(), until.encode(), "sha256").hexdigest())


def _unseen(v, signed_in):
    """Is a tile at visibility v hidden from this visitor?"""
    return v == "hidden" or (v == "users" and not signed_in) or (v == "admin" and signed_in != "admin")


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


# What a tile does for a visitor who sees it but may not open it: offer the
# sign-in page, offer sign-up (while accounts are open or by application), a padlock that does
# nothing, or greyed out. Per app; sign-in unless chosen.
LOCKED_AS_FILE = STATE_DIR / "locked_as.json"
LOCKED_AS = ("signin", "signup", "padlock", "grey")


def locked_as():
    try:
        data = json.loads(LOCKED_AS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and v in LOCKED_AS} if isinstance(data, dict) else {}


def save_locked_as(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LOCKED_AS_FILE.parent / (LOCKED_AS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, LOCKED_AS_FILE)


def _switched():
    """The apps with a switch: the built-ins the web server gates, and the local add-ons."""
    return set(access.ROUTED) | {m["id"] for m in MANIFESTS if m.get("local")}


# The apps drawn on the hub page itself, not as tiles (the shoutbox and the board): who sees
# them is a visibility of their own (guests, users or hidden; auto is guests); who may post is
# their shout_who / board_who. id -> the tab they are in index.html.
PAGE_APPS = {"shoutbox": "shout", "board": "board"}


def page_app_hidden(i, signed_in):
    return _unseen(visibility().get(i, "auto"), signed_in)


def hidden_apps(signed_in=False):
    """The apps not on the home page or the list pages: off; and otherwise as their visibility
    says, or (auto) as their access does: private, and for users unless the visitor is logged in
    (access.py; the root helper leaves its copy of the choices in the control folder). A built-in
    with no switch (a list page, the box row, About) is never hidden."""
    state, vis = access.read(ACCESS_STATE), visibility()
    offer = settings_snapshot()["sign_in_offer"]
    out = set()
    for i in _switched():
        mode, v = access.mode_of(state, i), vis.get(i, "auto")
        if mode == "off" or (v != "auto" and _unseen(v, signed_in)):
            out.add(i)
        elif (
            v == "auto"
            and mode != "public"
            and not (signed_in and mode == "users")
            and not (mode == "users" and offer)
            and not (mode == "private" and signed_in == "admin")
        ):
            out.add(i)
    return out


def seen_only():
    """The tiles with no switch of their own: the folders, About. Who opens them
    is no one's to set (their pages are the hub's, open to all), but who sees the tile is.
    """
    sw = _switched()
    return {
        m["id"]
        for m in MANIFESTS
        if m["id"] not in sw
        and m["id"] not in PAGE_APPS
        and (m.get("tile") or {}).get("row", "apps") == "apps"
        and not (m.get("tile") or {}).get("widget")
        and (m.get("tile") or m.get("menu"))
    }


def hidden_tiles(signed_in=False):
    """The tiles left off this visitor's home page: hidden_apps, and the seen-only tiles hidden
    from them (whose addresses still work, as for any app whose tile is hidden)."""
    vis = visibility()
    out = set(hidden_apps(signed_in))
    for i in seen_only():
        if _unseen(vis.get(i, "auto"), signed_in):
            out.add(i)
    return out


def admin_only_tiles():
    """The tiles only an admin sees: shown to the admin, by choice or (as its access) for a private
    app, and not switched off."""
    state, vis, out = access.read(ACCESS_STATE), visibility(), set()
    for i in _switched():
        mode, v = access.mode_of(state, i), vis.get(i, "auto")
        if mode != "off" and (v == "admin" or (v == "auto" and mode == "private")):
            out.add(i)
    return out | {i for i in seen_only() if vis.get(i) == "admin"}


def locked_apps(signed_in=False):
    """The apps whose tile shows but which this visitor can't open yet: for users, to someone not
    logged in; private, to anyone (the admin login is asked for at its door)."""
    state = access.read(ACCESS_STATE)
    return {
        i
        for i in _switched()
        if access.mode_of(state, i) == "private" or (access.mode_of(state, i) == "users" and not signed_in)
    }


def render_tiles(
    row="apps",
    hidden=frozenset(),
    factory_tile=False,
    locked=frozenset(),
    admin_only=frozenset(),
):
    """One row of the home page's tiles, from the manifests' "tile" parts. Rendered here
    rather than in the browser, so the page arrives whole. hidden: apps left out, and so a
    list page left with nothing on it. factory_tile: the owner's choice to show the factory's.
    locked: apps shown to this visitor that ask for a login at their door. admin_only: tiles
    only an admin sees, marked so as special to the admin."""
    out = []
    ms = MANIFESTS
    st = status_tiles_state() if row == "box" else None
    if st:
        pos = {i: n for n, i in enumerate(st["order"])}
        ms = sorted(ms, key=lambda m: pos.get(m["id"], len(pos)))
        hidden = set(hidden) | set(st["hidden"])
    own_icons = {}
    lock_ways, access_state = locked_as(), access.read(ACCESS_STATE)
    lock_all = settings_snapshot()["locked_all"]
    sign_up_open = accounts.settings()["signup"] in ("open", "apply")
    if row == "apps":
        st = tiles_state()
        own_icons = st["icon"]
        pos = {i: n for n, i in enumerate(st["order"])}
        ms = sorted(ms, key=lambda m: pos.get(m["id"], len(pos)))
    for m in ms:
        tile = m.get("tile")
        if not tile or tile.get("row", "apps") != row or m["id"] in hidden:
            continue
        size = (st or {}).get("size", {}).get(m["id"])
        if tile.get("widget") == "factory" and not factory_tile:
            continue
        if m.get("menu") and not menu_entries(m["id"], hidden):
            continue
        if "widget" in tile:
            w = WIDGET_HTML[tile["widget"]]
            out.append(
                w.replace('class="service-card ', f'class="service-card {size} ', 1).replace(
                    "<div ", f'<div data-size="{size}" ', 1
                )
                if size
                else w
            )
            continue
        # A locked tile (seen, not to be opened by this visitor): as the owner chose for it.
        how = (lock_all or lock_ways.get(m["id"], "signin")) if m["id"] in locked else None
        if how == "signup" and not (sign_up_open and access.mode_of(access_state, m["id"]) == "users"):
            how = "signin"  # no sign-up to offer, or an app for the admin only
        dead = how in ("padlock", "grey")
        attrs = [
            (
                f'class="service-card{" locked" if how else ""}{" greyed" if how == "grey" else ""}{" padlock" if how == "padlock" else ""}'
                f'{" admin-only" if m["id"] in admin_only else ""}{" " + size if size else ""}"'
            )
        ]  # one attribute, two lines
        if size:
            attrs.append(f'data-size="{size}"')
        if tile.get("element_id"):
            attrs.append(f'id="{html.escape(tile["element_id"])}"')
        if dead:
            attrs.append('aria-disabled="true"')
        elif how == "signup":
            attrs.append(f'href="/account.html?next={quote(tile["href"], safe="")}#signup"')
        elif how == "signin" and access.mode_of(access_state, m["id"]) == "users":
            attrs.append(f'href="/account.html?next={quote(tile["href"], safe="")}"')
        else:
            attrs.append(f'href="{html.escape(tile["href"])}"')
        path = m.get("status", {}).get("path")
        if path:
            attrs.append(f'data-service="{html.escape(path)}"')
        if tile.get("new_tab") and not dead:
            attrs.append('target="_blank"')
        tag = "div" if dead else "a"
        lock = {
            "signin": "🔒 sign in to open",
            "signup": "🔒 sign up to open",
            "padlock": "🔒",
        }.get(how)
        out.append(
            f"      <{tag} {' '.join(attrs)}>\n"
            + icon_html(own_icons.get(m["id"]) or tile["icon"])
            + f'        <span class="name">{html.escape(tile["name"])}</span>\n'
            f'        <span class="desc">{html.escape(tile["desc"])}</span>\n'
            + (f'        <span class="lock">{lock}</span>\n' if lock else "")
            + (
                '        <span class="tile-pill admin-pill" title="Only administrators see this tile">admin only</span>\n'
                if m["id"] in admin_only
                else ""
            )
            + f"      </{tag}>"
        )
    return "\n".join(out)


# --- folders -----------------------------------------------------------------------------------
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
    return {
        f: {k: [h for h in v.get(k, []) if isinstance(h, str)][:500] for k in FOLDER_PARTS}
        for f, v in data.items()
        if isinstance(f, str) and isinstance(v, dict)
    }


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
    in from other folders, in its order (the rest after, as they were), less the hidden ones.
    """
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
            entries.append(
                {
                    "href": href,
                    "name": e["name"],
                    "desc": e.get("desc", ""),
                    "kind": entry_kind(href),
                    "from": pool.get(href, (0, 0, 0, mid))[3],
                    "hidden": href in hidden,
                    "extra": href in extra,
                }
            )
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
            if (
                not isinstance(hrefs, list)
                or len(hrefs) > 500
                or not all(isinstance(h, str) and h in pool for h in hrefs)
            ):
                return None
            part[k] = list(dict.fromkeys(hrefs))
        out[f] = part
    return out


# --- status tiles ------------------------------------------------------------------------------
# The row of readouts under the apps (the box row: people, the join QR, memory and disk, the
# Firmware Factory, services) is one folder of tiles the owner arranges: which show, in what order,
# and which take two cells to say more. The hub's own (state/status_tiles.json).
STATUS_TILES_FILE = STATE_DIR / "status_tiles.json"


# A tile's size: one cell, wide (two across) or large (two across and two down). An older "double"
# list reads as wide.
SIZES = ("wide", "large")


def _sizes(data):
    size = {i: "wide" for i in data.get("double", []) if isinstance(i, str)}
    got = data.get("size", {})
    if isinstance(got, dict):
        size.update({k: v for k, v in got.items() if isinstance(k, str) and v in SIZES})
    return dict(list(size.items())[:100])


def status_tiles_state():
    try:
        data = json.loads(STATUS_TILES_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    out = {k: [x for x in data.get(k, []) if isinstance(x, str)][:50] for k in ("order", "hidden")}
    out["size"] = _sizes(data)
    return out


def box_tiles():
    """The box row's tiles, in the manifests' order: [(id, name)]."""
    names = {
        "people": "People here",
        "qr": "Join QR",
        "system": "Memory and disk",
        "factory": "Firmware Factory",
    }
    return [
        (m["id"], m["tile"].get("name") or names.get(m["tile"].get("widget"), m["id"]))
        for m in MANIFESTS
        if (m.get("tile") or {}).get("row") == "box"
    ]


def status_tiles_snapshot():
    st = status_tiles_state()
    pos = {i: n for n, i in enumerate(st["order"])}
    tiles = sorted(box_tiles(), key=lambda t: pos.get(t[0], len(pos)))
    return {
        "tiles": [
            {
                "id": i,
                "name": n,
                "hidden": i in st["hidden"],
                "size": st["size"].get(i, "single"),
            }
            for i, n in tiles
        ],
        "state": st,
    }


# The apps row's order (on /admin's "All apps"): the hub's own, like the box row's.
TILES_FILE = STATE_DIR / "tiles.json"


# A tile's own icon: emoji, or up to four letters and digits drawn as text.
ICON_TEXT = re.compile(r"^[A-Za-z0-9]{1,4}$")


def valid_icon(s):
    """Up to four letters and digits; or emoji (up to 16 code points, none of them ASCII, so no
    letters, markup or spaces among them)."""
    return isinstance(s, str) and (bool(ICON_TEXT.match(s)) or (0 < len(s) <= 16 and all(ord(c) > 0x7F for c in s)))


def tiles_state():
    try:
        data = json.loads(TILES_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    icon = data.get("icon", {})
    icon = {k: v for k, v in icon.items() if isinstance(k, str) and valid_icon(v)} if isinstance(icon, dict) else {}
    return {
        "order": [x for x in data.get("order", []) if isinstance(x, str)][:100],
        "size": _sizes(data),
        "icon": icon,
    }


def app_tiles():
    """The apps row's tiles, in the manifests' order: [(id, name, icon)]."""
    return [
        (m["id"], m["tile"].get("name", m["id"]), m["tile"].get("icon", ""))
        for m in MANIFESTS
        if m.get("tile") and m["tile"].get("row", "apps") == "apps" and not m["tile"].get("widget")
    ]


def tiles_snapshot():
    st = tiles_state()
    pos = {i: n for n, i in enumerate(st["order"])}
    tiles = sorted(app_tiles(), key=lambda t: pos.get(t[0], len(pos)))
    return {
        "tiles": [
            {
                "id": i,
                "name": n,
                "icon": c,
                "own_icon": st["icon"].get(i),
                "size": st["size"].get(i, "single"),
            }
            for i, n, c in tiles
        ],
        "state": st,
    }


def icon_html(icon):
    """A tile's icon: emoji as they are, letters and digits as a word of their own (F-icons)."""
    cls = "icon icon-text" if ICON_TEXT.match(icon) else "icon"
    return f'        <span class="{cls}">{html.escape(icon)}</span>\n'


TILES_MARK = "<!-- apps.d tiles -->"
BOX_MARK = "<!-- apps.d box tiles -->"
ACCOUNT_MARK = "<!-- account nav -->"
FIRST_USE_MARK = "<!-- first use -->"


def _first_use_note():
    """The home page's word while the box is in its first use: whoever on the network makes the first admin account
    becomes the administrator (the set-up page is the only admin page until then), and how to make it safely."""
    if not unclaimed():
        return ""
    return (
        '<section class="first-use-note" role="note"><h2>⚙️ This box is not set up yet</h2>'
        "<p>Until an administrator account is made, any device on this network can make it and become the administrator. Make it now "
        "over HTTPS or at the box's console over SSH (<code>sudo /opt/irate-box/irate-box hub_control set-admin NAME</code>). "
        "Over plain HTTP, others on the network can read the password.</p>"
        '<p><a class="action-btn" href="/admin/setup">Set up this box</a> '
        '<a href="/guide/first-use.html">How: Setting up a new box</a></p></section>'
    )


_home_page = {}  # signed in or not -> {"mtime", "body"}


def _account_nav(signed_in):
    """The header's account buttons. One login for everyone, admins included: "Sign in" for a guest
    (admins sign in there too, so it shows with sign-up off), "My account" for anyone in; "Admin" for a
    signed-in admin only (a box in its first use says so on the home page instead: FIRST_USE_MARK).
    """
    signup = accounts.settings()["signup"]
    title, label = (
        ("Your account", "My account")
        if signed_in
        else ("Sign in" if signup == "off" else "Sign in or sign up", "Sign in")
    )
    out = f'<a class="head-btn labelled" href="/account.html" title="{title}"><span class="head-emoji" aria-hidden="true">👤</span> {label}</a>'
    if signed_in == "admin":
        out += (
            '<a class="head-btn labelled" href="/admin/" title="Administration pages">'
            '<span class="head-emoji" aria-hidden="true">⚙️</span> Admin</a>'
        )
    return out


def home_page(signed_in=False):
    """index.html with the tiles in place, re-read when the file or the access choices change;
    one for guests and one for anyone logged in (who also sees the apps for users)."""
    path = STATIC / "index.html"
    try:
        chosen = ACCESS_STATE.stat().st_mtime
    except OSError:
        chosen = None
    for f in (
        VISIBILITY_FILE,
        FOLDERS_FILE,
        STATUS_TILES_FILE,
        TILES_FILE,
        SETTINGS_FILE,
        LOCKED_AS_FILE,
        PICTURES["hub-art"]["file"],
    ):
        try:
            chosen = (chosen, f.stat().st_mtime)
        except OSError:
            chosen = (chosen, None)
    show_factory = settings_snapshot()["factory_tile"]
    signup = accounts.settings()["signup"]
    words = settings_snapshot()
    notice_over = bool(words["notice_until"]) and time.time() >= words["notice_until"]
    mtime = (
        path.stat().st_mtime,
        chosen,
        show_factory,
        signup,
        accounts.admin_ready(),
        unclaimed(),
        notice_over,
    )
    cached = _home_page.setdefault(signed_in, {"mtime": None, "body": b""})
    if cached["mtime"] != mtime:
        text = path.read_text(encoding="utf-8")
        hidden = hidden_tiles(signed_in)
        locked = locked_apps(signed_in)
        admin_only = admin_only_tiles() if signed_in == "admin" else set()
        # A tab its visitor isn't to see: its link and its pane left out of the page.
        for app, tab in PAGE_APPS.items():
            if page_app_hidden(app, signed_in):
                text = re.sub(rf'\s*<a href="#{tab}" data-tab="{tab}"[^>]*>[^<]*</a>', "", text)
                text = text.replace(f'<div id="tab-{tab}">', f'<div id="tab-{tab}" data-off hidden>').replace(
                    f'<div id="tab-{tab}" hidden>',
                    f'<div id="tab-{tab}" data-off hidden>',
                )
        text = text.replace(
            TILES_MARK,
            render_tiles("apps", hidden, locked=locked, admin_only=admin_only),
        ).replace(BOX_MARK, render_tiles("box", hidden, show_factory, locked))
        text = text.replace(ACCOUNT_MARK, _account_nav(signed_in)).replace(FIRST_USE_MARK, _first_use_note())
        title, sub = html.escape(words["hub_title"].strip()), html.escape(words["hub_subtitle"].strip())
        # The keeper's own name for the hub is theirs, not the page's words: no message ID on it.
        if title != html.escape(DEFAULT_SETTINGS["hub_title"]):
            text = re.sub(r"<title[^>]*>Hub</title>", f"<title>{title}</title>", text, count=1)
            text = re.sub(r"<h1[^>]*>Hub</h1>", f"<h1>{title}</h1>", text, count=1)
        notice, keeper = words["notice_text"].strip(), words["keeper_contact"].strip()
        if notice and not notice_over:
            text = text.replace(
                "</header>",
                f'</header>\n  <p class="hub-notice" role="status">{html.escape(notice)}</p>',
                1,
            )
        if keeper:
            text = text.replace(
                '  <script src="qrcode.js">',
                f'  <footer class="hub-keeper">Looked after by: {html.escape(keeper)}</footer>\n'
                '  <script src="qrcode.js">',
                1,
            )
        art = picture_mtime("hub-art")
        if art:
            text = re.sub(
                r"<h1[^>]*>",
                lambda m: f'<img class="hub-art" src="/hub-art?{int(art)}" alt="">{m.group(0)}',
                text,
                count=1,
            )
        if sub != html.escape(DEFAULT_SETTINGS["hub_subtitle"]):
            text = re.sub(
                r'<p class="subtitle"[^>]*>[^<]*</p>',
                (f'<p class="subtitle">{sub}</p>' if sub else ""),
                text,
                count=1,
            )
        cached["body"] = text.encode()
        cached["mtime"] = mtime
    return cached["body"]


# The list pages (a manifest with a "menu"), at their tile's href. Rendered on each request:
# discovered entries come from the installed apps, which an update changes.
MENU_PAGES = {m["tile"]["href"]: m for m in manifests.menus(MANIFESTS).values()}
# A page's tab icon: its emoji drawn as an SVG in the page itself (no request). The first emoji of a tile's icon
# ("📡📻" is 📡), with what joins to it (U+FE0F, a skin tone, a ZWJ sequence, a flag's pair).
FIRST_EMOJI = re.compile("[\U0001f1e6-\U0001f1ff]{2}|.[\ufe0f\U0001f3fb-\U0001f3ff]*(?:\u200d.[\ufe0f]*)*")


def emoji_icon(icon, fallback="🏴\u200d☠️"):
    m = FIRST_EMOJI.match(icon or "")
    e = html.escape(m.group(0) if m else fallback)
    return (
        '<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22>'
        f'<text y=%22.9em%22 font-size=%2290%22>{e}</text></svg>">'
    )


MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="art-page">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Hub</title>
  {icon}
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
    <nav class="head-nav"><a class="head-btn labelled" href="/help.html" title="Quick help"><span class="head-emoji" aria-hidden="true">🛟</span> Help</a><a class="head-btn labelled" href="/" title="Back to the hub"><span class="head-emoji" aria-hidden="true">🏠</span> Hub</a></nav>
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
            href = f"/app.html?from={m['id']}#" + href[len("/app.html#") :]
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
    return MENU_TEMPLATE.format(
        art_attrs=art_attrs,
        art=art_html,
        banner=banner,
        title=html.escape(menu["title"]),
        subtitle=html.escape(menu["subtitle"]),
        icon=emoji_icon((m.get("tile") or {}).get("icon")),
        about=html.escape(menu.get("about", "")).replace("--", "-"),
        items="\n".join(items),
    ).encode()


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


_online_names = {}  # account name -> when last seen (ticks), in memory only


def note_account(name):
    with _seen_lock:
        _online_names[name] = CLOCK.ticks()


def online_names(now):
    """The accounts seen in the last ONLINE_WINDOW: every one, whatever they chose to show."""
    with _seen_lock:
        return sorted(n for n, t in _online_names.items() if now - t <= ONLINE_WINDOW)


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
# The owner's pictures (Appearance), each a file in $STATE served at its own path, its type told by its
# first bytes: the favicon (a PNG or an icon file) and the hub page's header picture (PNG, JPEG or WebP).
# Never SVG: one served from the hub's own origin could carry script.
PNG, ICO, JPEG = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
PICTURES = {
    "favicon": {
        "file": STATE_DIR / "favicon",
        "url": "/favicon.ico",
        "max": 64 * 1024,
        "kinds": (PNG, ICO),
        "says": "a PNG or .ico file",
    },
    "hub-art": {
        "file": STATE_DIR / "hub-art",
        "url": "/hub-art",
        "max": 180 * 1024,
        "kinds": (PNG, JPEG, "webp"),
        "says": "a PNG, JPEG or WebP picture",
    },
}
PICTURE_URLS = {p["url"]: name for name, p in PICTURES.items()}


def picture_kind(name, data):
    for k in PICTURES[name]["kinds"]:
        if k == "webp":
            if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
                return "image/webp"
        elif data.startswith(k[0]):
            return k[1]
    return None


def picture_mtime(name):
    try:
        return PICTURES[name]["file"].stat().st_mtime
    except OSError:
        return None


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
    # The Firmware Factory's tile on the front page: its queue for anyone to follow, and
    # what it built to download. Off until the owner shows it.
    "factory_tile": False,
    # The shoutbox and the forum: who may post, guests (and
    # users), users only, or off; and whether a user's name carries a check mark.
    "shout_who": "guests",
    "shout_marks": True,
    "board_who": "guests",
    "board_marks": True,
    # Page widths in rem (80 by default, with the owner's choice of 45, 60, 80, 90 or 100): the hub's pages, the admin
    # pages (as the hub's until chosen apart), and the shoutbox and the forum their own, 45 as they always were.
    # Every page reads them from /layout.css.
    "page_width": 80,
    "admin_width": 80,
    "shout_width": 45,
    "board_width": 45,
    # The emoji pickers' set: Unicode's emoji up to a release the guests' phones
    # can draw (a newer emoji shows as an empty box on an older phone). 13.1 for iOS 14.5 and
    # Android 12; 15.0 for iOS 16.4 (every iPhone from the 8 on) and Android 14; 16.0 for iOS 18.4
    # (iPhone XS on) and Android 16. Read by emoji.js from /layout.css.
    "emoji_set": "13.1",
    # The hub's own words and colour (Appearance): its name (the page's heading and the browser
    # tab's), the line under it, and an accent colour for the main light and dark themes ("" keeps
    # theirs). The other themes keep their own colours.
    "hub_title": "Hub",
    "hub_subtitle": "You are connected to the network of the box.",
    "accent": "",
    # The light and dark palettes the owner chose (Appearance): per mode, any of PALETTE_ROLES as #rrggbb; a role
    # left out keeps the theme's own (or, for the accent, the one accent above). {} is the standard theme.
    "palette": {},
    # The Krab art at the foot of the admin pages and a few others: on, or off for a plainer look.
    "krab_art": True,
    # What guests see: a notice at the top of the hub page ("Back online at 6 pm"), until a time
    # (epoch seconds; 0 for no end), and how to reach whoever looks after the box, at its foot.
    "notice_text": "",
    "notice_until": 0,
    "keeper_contact": "",
    # Unique visitors counted by a helper of their own, from salted hashes it keeps in memory
    # (irate_box/root/visitors.py). On by default, as one of the setup's
    # decisions; off, the helper doesn't run at all.
    "visitor_counts": True,
    # Reports (anyone can report a post; the admin decides what counts): the
    # reasons offered, how many reports put a post in the queue, and whether it is hidden from
    # everyone else until looked at.
    "report_reasons": ["spam", "unkind", "personal details", "other"],
    "report_threshold": 1,
    "report_hide": False,
    # A save tied to its account, so its person may open and change it from another device once
    # logged in: off by default (the
    # admin's choice), a guest's saves stay exactly as they are either way.
    "saves_cross_device": False,
    # Who sees the names of those signed in and around: users, or the admin only. Guests
    # only ever get the count; and each person decides whether their own name shows at all.
    "names_to": "users",
    # The setup tour: which of the decisions that touch security the owner has made,
    # by keeping the default or changing it where it lives. The box runs on the defaults until then.
    "setup_decided": [],
    # Setup steps the owner has muted: still listed, greyed,
    # but not counted in Overview's "needs attention" or the tour's "still to do". Hiding is the whole page.
    "setup_muted": [],
    # The Network page's changes made here that the owner has hidden as old news (handover:<file>,
    # otherap:<uuid>, wifisync): still there, one button away.
    "net_changes_hidden": [],
    # A tile a guest can't open (the setup decision "sign-in-offer"): for an app
    # open to users and left at "as its access", show its tile to guests too, with a lock that leads
    # to sign-in. Off (the default) shows it only to those who may open it, as before.
    "sign_in_offer": False,
    # What every tile does for one who sees it but may not open it, over the per-app choices (a
    # global override). "" (the default) leaves each app to its own choice.
    "locked_all": "",
    # A notebook per login (notebooks.py): "" decides by sign-up (private wherever people may sign
    # in), "private", or "communal" (the shared notes only). At most so many running at once, and
    # none started while the box has less than so much memory free.
    "notebooks": "",
    "notebooks_max": 5,
    "notebooks_keep_free_mb": 96,
    # How hard the box writes to its card (Recovery and monitoring): what may be lost in a crash or a
    # power cut in exchange for writing less. Owner data is written as it changes at every stop.
    "card_writes": "thrifty",
}
POSTERS = ("guests", "users", "off")
WIDTHS = (45, 60, 80, 90, 100)
EMOJI_SETS = ("13.1", "15.0", "16.0")
TEXT_LIMITS = {
    "hub_title": 40,
    "hub_subtitle": 120,
    "notice_text": 200,
    "keeper_contact": 200,
}


# A palette role and the colour variable it sets.
PALETTE_ROLES = {
    "bg": "--bg",
    "surface": "--surface",
    "border": "--border",
    "text": "--text",
    "muted": "--muted",
    "accent": "--accent",
}
PALETTE_MODES = ("light", "dark")


def valid_palette(value):
    return (
        isinstance(value, dict)
        and set(value) <= set(PALETTE_MODES)
        and all(
            isinstance(m, dict)
            and set(m) <= set(PALETTE_ROLES)
            and all(isinstance(c, str) and re.fullmatch(r"#[0-9a-f]{6}", c) for c in m.values())
            for m in value.values()
        )
    )


def palette_css(palette):
    """The owner's palettes as rules for the main themes only: dark where the page is dark (chosen, or Auto on a device
    not asking for light), light where it is light; the other themes keep their colours, and so does a visitor who
    chose a palette of their own (themes.js: data-palette, standard or more contrast) over the box's.
    """

    def block(colours):
        decl = "".join(f" {PALETTE_ROLES[r]}: {c};" for r, c in sorted(colours.items()))
        if "accent" in colours:
            decl += f" --accent-ink: {accent_ink(colours['accent'])};"
        return "{" + decl + " }"

    out = ""
    dark, light = palette.get("dark") or {}, palette.get("light") or {}
    if dark:
        out += (
            'html:root:not([data-palette])[data-theme="dark"] %s\n'
            "@media not all and (prefers-color-scheme: light) { html:root:not([data-palette]):not([data-theme]) %s }\n"
            % (block(dark), block(dark))
        )
    if light:
        out += (
            'html:root:not([data-palette])[data-theme="light"] %s\n'
            "@media (prefers-color-scheme: light) { html:root:not([data-palette]):not([data-theme]) %s }\n"
            % (block(light), block(light))
        )
    return out


def accent_ink(accent):
    """The text colour on the accent: white or near-black, whichever stands out more from it."""

    def lum(hexc):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = (int(hexc[i : i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)

    a = lum(accent)
    return "#fff" if (1.05) / (a + 0.05) >= (a + 0.05) / (lum("#0f1117") + 0.05) else "#0f1117"


# The setup decisions (anything that has an impact on user or box security):
# web/admin-tour.js has each one's words and where it lives.
# With the setup's own steps and the backup: one list, one tour.
SETUP_DECISIONS = (
    "visitors",
    "names",
    "signup",
    "sign-in-offer",
    "https",
    "root-policy",
    "hotspot",
    "guest-net",
    "ssh",
    "tailscale",
    "cockpit",
    "terminal",
    "password",
    "connection",
    "security",
    "addons",
    "books",
    "backup",
)
REPORT_REASONS = ("spam", "unkind", "personal details", "illegal", "other")
_settings_lock = threading.Lock()


def valid_setting(key, value):
    want = type(DEFAULT_SETTINGS[key])
    if key in ("shout_who", "board_who"):
        return value in POSTERS
    if key in ("page_width", "admin_width", "shout_width", "board_width"):
        return type(value) is int and value in WIDTHS
    if key == "report_reasons":
        return (
            isinstance(value, list)
            and 0 < len(value) <= len(REPORT_REASONS)
            and all(v in REPORT_REASONS for v in value)
        )
    if key == "locked_all":
        return value == "" or value in LOCKED_AS
    if key == "net_changes_hidden":
        return (
            isinstance(value, list)
            and len(value) <= 32
            and all(
                isinstance(v, str) and re.fullmatch(r"(handover|otherap|wifisync)(:[A-Za-z0-9._-]{1,80})?", v)
                for v in value
            )
        )
    if key in ("setup_decided", "setup_muted"):
        return (
            isinstance(value, list) and len(value) <= len(SETUP_DECISIONS) and all(v in SETUP_DECISIONS for v in value)
        )
    if key == "emoji_set":
        return value in EMOJI_SETS
    if key in TEXT_LIMITS:
        return (
            isinstance(value, str)
            and len(value.strip()) <= TEXT_LIMITS[key]
            and (key != "hub_title" or value.strip())
            and not any(ord(c) < 32 for c in value)
        )
    if key == "accent":
        return value == "" or (isinstance(value, str) and re.fullmatch(r"#[0-9a-f]{6}", value) is not None)
    if key == "palette":
        return valid_palette(value)
    if key == "names_to":
        return value in ("users", "admin")
    if key == "report_threshold":
        return type(value) is int and 1 <= value <= 20
    if key == "notebooks":
        return value in notebooks.MODES
    if key == "card_writes":
        return value in CARD_WRITES
    if key == "notebooks_max":
        return type(value) is int and value in notebooks.MAX_CHOICES
    if key == "notebooks_keep_free_mb":
        return type(value) is int and value in notebooks.FREE_CHOICES
    return type(value) is want and (want is not int or value >= 0)


# The write-intensity ladder: seconds between the hub clock's saves and the mesh nodes' saves, per stop.
# (Crash watch reads the same setting for its snapshots: crashwatch.card_writes.)
CARD_WRITES = {
    "everything": (60, 60),
    "thrifty": (600, 300),
    "gentle": (1800, 900),
    "saver": (3600, 1800),
}


def apply_settings(data):
    clock_every, mesh_every = CARD_WRITES.get(data.get("card_writes"), CARD_WRITES["thrifty"])
    if "CLOCK" in globals():
        CLOCK.flush_interval = clock_every
    meshbridge.SAVE_EVERY = mesh_every
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
        if (
            "admin_width" not in stored
        ):  # chosen before the admin pages had a width of their own: the one width for both
            data["admin_width"] = data["page_width"]
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
# The notebooks per login: the list of those running starts empty with the hub (a notebook left
# running from before is stopped, and opens again on its owner's next visit).
NOTEBOOKS = notebooks.Notebooks(
    lambda path: socket_listening(path),
    lambda k: accounts.has_session(k),
    lambda: settings_snapshot(),
    lambda: accounts.settings()["signup"],
    WEB_SERVER,
)


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
    """A service on a UNIX socket (SilverBullet's: no TCP port at all; ttyd's)."""
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
    is no systemd to ask (a dev machine): the caller then treats every unit as installed.
    """
    try:
        out = subprocess.run(  # noqa: TID251
            [
                "systemctl",
                "show",
                "--property=Id,LoadState,ActiveState,UnitFileState",
                *units,
            ],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    states = {}
    for block in out.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if "Id" in fields:
            states[fields["Id"]] = (
                fields.get("LoadState") not in ("not-found", ""),
                fields.get("ActiveState") == "active",
                fields.get("UnitFileState") == "enabled",
            )
    # A missing unit still gets a block, under the name it was asked for.
    return states


def kiwix_why():
    """Why /wiki/ is not running, in words a guest or the owner can act on. Cheap enough for
    /status (cached with it): the ZIM headers only, never kiwix-manage."""
    books = sorted(librarian.ZIM_DIR.glob("*.zim")) if librarian.ZIM_DIR.is_dir() else []
    if not books:
        return "No books yet. Add one on the Books page, or from a USB drive."
    bad = [b for b in books if zimcheck.header_problem(b)]
    if len(bad) == len(books):
        return (
            f"None of the {len(books)} book{'s' if len(books) != 1 else ''} can be read (damaged or unfinished "
            "copies). Health says which, and can set them aside."
        )
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
        _status_cache["ports"] = {svc["port"]: port_listening(svc["port"]) for svc in SERVICES if "port" in svc}
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
        entry = {
            "path": svc["path"],
            "name": svc["name"],
            "state": state,
            "up": proxied and running,
        }
        if "note" in svc:
            entry["note"] = svc["note"]
        if svc["name"] == TAILSCALE_NAME and running:
            # Running is not reachable: a box never logged in to a Tailscale network can't be reached through it.
            ts = tailscale_backend()
            if ts and ts["state"] == "needs-login":
                entry["note"] = (
                    "running, but not signed in to a Tailscale network, so nothing reaches the box through it (System → Access)"
                )
        if state == "stopped" and svc.get("unit") == "kiwix.service":
            why = _status_cache.setdefault("why", {})
            if "kiwix" not in why:
                why["kiwix"] = kiwix_why()
            entry["why"] = why["kiwix"]
        out.append(entry)
    return out


# --- unique visitors ----------------------------------------------------------------
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
    return {
        "day": data["day"],
        "week": data["week"],
        "date": str(data.get("date", ""))[:10],
    }


# --- remote access -------------------------------------------------------------
# The hub runs unprivileged, so it cannot start tailscaled itself. It records what the
# operator asked for in TAILSCALE_WANT, and a root path unit (irate-box-tailscale.path,
# set up by install.sh) applies it with tailscale-apply.sh. One line:
#   off | on | on <seconds>   -- a timed session ends by itself, and at the next boot.
TAILSCALE_WANT = STATE_DIR / "tailscale.want"
TAILSCALE_MAX_HOURS = 72


_tailscale_cache = {"at": -1e9, "val": None}


def tailscale_backend(run=subprocess.run):  # noqa: TID251
    """Tailscale's own state while tailscaled runs, from `tailscale status --json` (the hub may ask: it is only read):
    {"state": "connected" | "needs-login" | another of its states, lowercased, "name", "tailnet", "ip"}; None when it
    can't be asked (not running, no CLI). Cached as the services' states are."""
    now = time.monotonic()
    if now - _tailscale_cache["at"] < STATUS_CACHE_S:
        return _tailscale_cache["val"]
    val = None
    try:
        r = run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        d = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        d = None
    if isinstance(d, dict) and isinstance(d.get("BackendState"), str):
        me, net = d.get("Self") or {}, d.get("CurrentTailnet") or {}
        bs = d["BackendState"]
        val = {
            "state": {"Running": "connected", "NeedsLogin": "needs-login"}.get(bs, bs.lower()),
            "name": me.get("DNSName", "").rstrip(".") or me.get("HostName"),
            "tailnet": net.get("Name"),
            "ip": next(iter(me.get("TailscaleIPs") or []), None),
        }
    _tailscale_cache.update(at=now, val=val)
    return val


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
        snap["job"] = {
            "action": _library_job["action"],
            "result": _library_job["result"],
            "busy": bool(thread and thread.is_alive()),
        }
    snap["running"] = snap["running"] or snap["job"]["busy"]
    return snap


def library_start(action, fn):
    """Run fn() in the background unless something is already running; False if busy."""

    def work():
        try:
            result = fn()
        except librarian.LibrarianError as exc:
            result = refused.answer(exc)
        with _library_lock:
            _library_job["result"] = result

    with _library_lock:
        thread = _library_job["thread"]
        if (thread and thread.is_alive()) or librarian.is_running():
            return False
        _library_job.update(
            action=action,
            result=None,
            thread=threading.Thread(target=work, daemon=True),
        )
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
            librarian.set_policy(**{k: payload[k] for k in librarian.DEFAULT_POLICY if type(payload.get(k)) is int})
        elif action == "token":
            librarian.set_token(str(payload.get("value") or ""))
        elif action == "adapt-rate":
            librarian.adapt_rate()
        elif action == "adapt-rate-off":
            librarian.clear_pace()
        elif action == "add-apps":
            # The named apps (an app row's "Keep current"), or every default one.
            have = {s["name"] for s in librarian.load_config()["sources"]}
            for app in names or librarian.default_apps():
                if not librarian.APPS.get(app, {}).get("source"):
                    raise librarian.LibrarianError(
                        M(
                            "library.app.not-kept",
                            "The hub does not keep {app} current.",
                            app=app,
                        )
                    )
                if app not in have:
                    librarian.add_source(librarian.default_app_source(app))
        elif action in ("check", "fetch", "update"):
            if not library_start(action, lambda: librarian.update(names or None, mode=action, **quiet)):
                return 409, refused.say(librarian.BUSY)
        elif action == "rollback":
            name, version = str(payload.get("name", "")), payload.get("version") or None
            if not library_start(action, lambda: {name: librarian.rollback(name, version)}):
                return 409, refused.say(librarian.BUSY)
        else:
            return 400, refused.say(UNKNOWN_ACTION)
    except librarian.LibrarianError as exc:
        return _busy_or(400, exc)
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
        last.setdefault(
            r["run"].split("/")[0],
            {k: r.get(k) for k in ("run", "state", "started", "duration", "branch", "commit")},
        )
    parent = {}
    for m in mirror_list:
        for sub in (m.get("status") or {}).get("submodules", {}).values():
            parent[(m["area"], sub["repo"])] = m["name"]
    for r in snap["repos"]:
        r["last_build"] = last.get(r["name"]) if r["area"] == "private" else None
        r["submodule_of"] = parent.get((r["area"], r["name"]))
    snap.update(
        mirrors=mirror_list,
        running=librarian.is_running(),
        progress=librarian.progress(),
    )
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
                    return {
                        "mirrors": mirrors.sync_all(
                            names,
                            check_only=(action == "mirror-check"),
                            log=lambda *_: None,
                        )
                    }

            if not library_start(action, run):
                return 409, refused.say(librarian.BUSY)
        else:
            return 400, refused.say(UNKNOWN_ACTION)
    except librarian.LibrarianError as exc:
        return _busy_or(400, exc)
    return 200, git_snapshot()


def firmware_action(payload):
    """(status, body) for POST /admin/firmware: the settings, or a run (firmware.py)."""
    action = payload.get("action")
    try:
        if action == "settings":
            firmware.set_settings(
                **{
                    k: payload[k]
                    for k in (
                        "enabled",
                        "configs",
                        "boards",
                        "keep_alpha",
                        "keep_beta",
                        "cache",
                    )
                    if k in payload
                }
            )
        elif action in ("check", "update"):

            def run():
                with librarian.Lock():
                    return {"firmware": firmware.sync(check_only=(action == "check"), log=lambda *_: None)}

            if not library_start(f"firmware-{action}", run):
                return 409, refused.say(librarian.BUSY)
        elif action == "flush-cache":
            firmware.flush_cache(log=lambda *_: None)
        else:
            return 400, refused.say(UNKNOWN_ACTION)
    except librarian.LibrarianError as exc:
        return _busy_or(400, exc)
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


# The front's word that a request came through its /admin route, where it asked for the
# login: a secret it adds there and nowhere else (install.sh makes it, root-only, and hands it
# to the web server and to this unit). Without it, anything that can send a request to the
# hub's loopback port is the admin: a build, a proxy like SilverBullet's, a path
# the front did not normalise. Unset, as when the hub runs bare in development, /admin
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


def admin_login_state():
    """The box's own login as the root helper last wrote it: {on, reach (off | local | network), password_set}."""
    try:
        d = json.loads((CONTROL_DIR / "admin-login.json").read_text())
    except (OSError, ValueError):
        d = {}
    on = d.get("on") is not False
    return {
        "on": on,
        "reach": d.get("reach") or ("network" if on else "off"),
        "password_set": d.get("password_set", on),
    }


def accounts_view():
    listing = accounts.listing()
    return {
        "settings": accounts.settings(),
        "accounts": listing,
        "counts": accounts.counts(),
        "admin_login": {
            **admin_login_state(),
            "results": control_results(10),
            "https_admins": [
                a["name"]
                for a in listing
                if a["role"] == "admin" and a["state"] == "user" and a["password_set"] and a.get("https_login")
            ],
        },
    }


def setup_status(rid):
    out = {"unclaimed": unclaimed()}
    if rid:
        out["result"] = next((r for r in control_results(20) if r.get("id") == rid), None)
    return out


# What a backup holds is hub/backup.py's: settings only, or settings and data; never what is
# fetched again (books, mirrors, builds, caches), nor the GitHub token. Syncthing's identity (its private
# keys and config.xml: restored, the box keeps its device ID, but whoever holds the file can pose as the box
# to its peers) only when asked for with ?syncthing=1, which the page labels as such.


def _str_list(v, limit=500):
    return isinstance(v, list) and len(v) <= limit and all(isinstance(x, str) and 0 < len(x) <= 80 for x in v)


def backup_plan():
    """Everything the Backup page offers, with its size (hub/backup.py plan)."""
    from irate_box.hub import kitdefs

    try:
        kits_status = json.loads((CONTROL_DIR / "kits.json").read_text())
    except (OSError, ValueError):
        kits_status = {}
    try:
        titles = {k: d.get("title", k) for k, d in kitdefs.definitions().items()}
    except (OSError, ValueError, AttributeError):
        titles = {}
    out = backup.plan(
        STATE_DIR,
        STATE_DIR / "zim",
        kits_status,
        apps_dir=Path("/usr/share/hub/apps"),
        kit_titles=titles,
    )
    out["image_pending"] = _pending_actions("backup-image")
    return out


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
    # which is root's, nor in requests/, whose path unit would start the helper for it.
    tmp = STATE_DIR / f".request-{rid}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(dict(req, id=rid), fh)
    os.replace(tmp, CONTROL_REQUESTS / f"{rid}.json")
    return rid


def control_results(limit=10):
    out = []
    for path in sorted(CONTROL_RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
    return out


MESHTASTICD = "meshtasticd.service"  # sampled for its trends where installed; not one of the hub's services


def meshtasticd_state():
    """meshtasticd on Overview, or None where it is not installed: running or not, how it last
    ended, its restarts since the box started, and systemd's start limit, which ends a crash loop
    (the unit restarts it always, up to the limit, then leaves it failed until it is started again).
    """
    try:
        out = subprocess.run(  # noqa: TID251
            [
                "systemctl",
                "show",
                "--property=LoadState,ActiveState,SubState,Result,NRestarts,StartLimitBurst,StartLimitIntervalUSec",
                MESHTASTICD,
            ],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    f = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if f.get("LoadState") in (None, "", "not-found"):
        return None
    num = lambda k: int(f[k]) if f.get(k, "").isdigit() else None  # noqa: E731
    return {
        "active": f.get("ActiveState"),
        "sub": f.get("SubState"),
        "result": f.get("Result"),
        "restarts": num("NRestarts"),
        "burst": num("StartLimitBurst"),
        "interval": f.get("StartLimitIntervalUSec"),
    }


def net_uptime_72h():
    """The Overview's network heatmap: each link recorded in the last 72 hours, by hour (the Network
    page's own summary, without the days), the box's link to the network first; a link never up in that
    time (a port with no cable) left off."""
    try:
        hist = json.loads(uplink.HISTORY.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for iface, u in sorted(
        linkhistory.summarize(hist).items(),
        key=lambda kv: (kv[1]["kind"] != "uplink", kv[0]),
    ):
        # A link never up in the 72 hours (a port with no cable) is left off: drawn, it would be red throughout.
        if u["summary"]["hours_seen"] and (u["kind"] == "uplink" or u["summary"]["up"]):
            out[iface] = {k: u[k] for k in ("kind", "hours", "hour_cols", "hour_full", "summary")}
    return out


def admin_box(proxied):
    units = [s["unit"] for s in SERVICES if "unit" in s]
    states = unit_states(units)
    by_name = {s["name"]: s for s in SERVICES}
    services = []
    for entry in service_status(proxied):
        unit = by_name.get(entry["name"], {}).get("unit")
        _, active, enabled = states.get(unit, (True, False, False))
        services.append(
            dict(
                entry,
                unit=unit,
                active=active,
                enabled=enabled,
                ops=CONTROL_OPS.get(unit, []),
            )
        )
    now = CLOCK.ticks()
    return {
        "system": system_status(),
        "uptime": now,
        "online": online_count(now),
        "memory_history": memhistory.snapshot(),
        "joined": joined_count(),
        "services": services,
        "version": hub_version(),
        "service_uptime": svchistory.summarize(svchistory.load()),
        "page_use": pagehistory.summarize(pagehistory.load()),
        "net_uptime": net_uptime_72h(),
        "meshtasticd": meshtasticd_state(),
        "first_use": unclaimed(),
        # The hub's own origin, which the administration pages (on a port of their own) name in addresses to copy.
        "hub_port": int(os.environ.get("HUB_PUBLIC_PORT", "80")),
        "hub_tls_port": int(os.environ.get("HUB_PUBLIC_TLS_PORT", "443")),
        "results": control_results(),
        "pending": (len(list(CONTROL_REQUESTS.glob("*.json"))) if CONTROL_REQUESTS.exists() else 0),
    }


# A refusal, as the pages read it (refused.py): error, its English, as every page expects; message, the message
# itself (its ID and arguments), for a page that says it in the reader's language.
refusal = refused.answer

# Refusals said in more than one place. A request no page of this version makes (an unknown action, a malformed
# body) is still refused in a sentence: an older page open in a tab can make one.
UNKNOWN_ACTION = M("hub.action.unknown", "Unknown action. Reload the page and try again.")
ACCEPTED = M("hub.accept.done", "Accepted. The finding is no longer in the review list.")
REOPENED = M("hub.accept.reopened", "Reopened. The finding is in the review list again.")
BAD_REQUEST = M("hub.request.invalid", "Invalid request. Reload the page and try again.")
NOT_SHOWN = M("hub.not-shown", "Not found. The administrator does not show this page.")
NO_THREAD = M("board.thread.not-found", "Thread not found. It was deleted, or you cannot see it.")
CONSENT_FIRST = M("addons.consent.first", "Read the consent text of the add-on and agree to it first.")
CROSS_SITE = M("hub.cross-site", "Refused. A change must come from a page of this box.")
SETUP_FIRST = M(
    "setup.first",
    "The box is in its first use. Select Set up this box and make the first administrator account.",
)


def password_length():
    return M(
        "hub.password.length",
        "The password must be {min} to {max} characters, on one line.",
        min=MIN_PASSWORD,
        max=128,
    )


def _already_added(addon):
    return M("addons.added", "The add-on {addon} is already added.", addon=addon)


def _busy_or(code, exc):
    """A librarian's failure: 409 when the librarian is already running (or the add-on is already added), else code."""
    m = getattr(exc, "message", None)
    return (
        409 if isinstance(m, dict) and m.get("id") in (librarian.BUSY["id"], "addons.added") else code
    ), refused.answer(exc)


UPDATE_STATE = CONTROL_DIR / "update.json"
UPDATE_LOG = CONTROL_DIR / "update.log"
UPDATE_PROGRESS = CONTROL_DIR / "update-progress.json"
DOCTOR_STATE = CONTROL_DIR / "doctor.json"
BRANCHES_STATE = CONTROL_DIR / "update-branches.json"  # root's: what the repository said of its branches
SIGNING_STATE = CONTROL_DIR / "update-signing.json"  # root's: the level, and the keys' names
# Guests' onward internet (root/share.py): the level root keeps, and the devices this hub has asked
# root to let out (for the captive API's answer; root's table holds the real list, each for 12 h).
SHARE_STATE = CONTROL_DIR / "share.json"
SHARE_LEVELS = ("off", "users-web", "sheet-web", "sheet-all", "open")
SHARE_LET_OUT = ("users-web", "sheet-web", "sheet-all")
SHARE_HOURS = 12
_let_out = {}  # address -> until (time.time())
_let_out_lock = threading.Lock()


def share_level():
    try:
        level = json.loads(SHARE_STATE.read_text()).get("level")
    except (OSError, ValueError, AttributeError):
        return "off"
    return level if level in SHARE_LEVELS else "off"


def on_hotspot(addr):
    try:
        return ipaddress.ip_address(addr) in ipaddress.ip_network("192.168.4.0/24") and addr != "192.168.4.1"
    except ValueError:
        return False


def let_out(addr):
    with _let_out_lock:
        return _let_out.get(addr, 0) > time.time()


UPDATE_ACTIONS = {
    "check": "update-check",
    "reach": "update-reach",
    "fetch": "update-fetch",
    "install": "update-install",
    "force-install": "update-force-install",
    "doctor": "update-doctor",
    "clear-cache": "update-clear-cache",
    "poll-target": "update-poll-target",
    "poll-source": "update-poll-target",
    "set-source": "update-set-source",
}
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


def _metered_view():
    """For the page: whether the box is on a metered network now, and since when scheduled downloads wait."""
    from irate_box.hub import netpolicy

    try:
        now = netpolicy.metered_now()
    except (OSError, ValueError):
        now = None
    return {"now": now, "waiting": netpolicy.waiting()}


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
        doctor = finding.current(json.loads(DOCTOR_STATE.read_text()))
    except (OSError, ValueError):
        doctor = None
    # The doctor's report is stale once a check, a download or a new source came after it.
    stale = False
    try:
        ran = json.loads(DOCTOR_STATE.read_text()).get("at") or 0
        since = max((p.stat().st_mtime for p in (UPDATE_STATE, INSTALL_OPTIONS) if p.exists()), default=0)
        stale = bool(ran) and since > ran + 1
    except (OSError, ValueError, AttributeError):
        pass
    # Where updates come from now (install.sh's world-readable copy), and what the repository said of its branches.
    try:
        opts = INSTALL_OPTIONS.read_text().split()
        source = {k: opts[opts.index(f"--{k}") + 1] for k in ("repo", "branch") if f"--{k}" in opts[:-1]}
        source.setdefault("branch", "main")
    except OSError:
        source = None
    try:
        branches = json.loads(BRANCHES_STATE.read_text())
    except (OSError, ValueError):
        branches = None
    # Only update requests: a queued app install or service change is not the update's.
    pending = _pending_actions("update-")
    # Automatic updates: the librarian's policy for them, and what its last step did.
    policy = librarian.load_config()["policy"]
    auto = {
        k: policy[k]
        for k in (
            "hub_check_every_hours",
            "hub_auto",
            "hub_window_start",
            "hub_window_end",
        )
    }
    auto["state"] = librarian._hub_update_state()
    try:
        sig = json.loads(SIGNING_STATE.read_text())
    except (OSError, ValueError):
        sig = {"level": "off", "keys": []}
    return {
        "version": hub_version(),
        "state": state,
        "log": log,
        "pending": pending,
        "progress": update_progress(),
        "doctor": doctor,
        "doctor_stale": stale,
        "source": source,
        "branches": branches,
        "results": control_results(5),
        "auto": auto,
        "signing": sig,
        "metered": _metered_view(),
    }


SECURITY_STATE = CONTROL_DIR / "security.json"


def setup_blocked():
    """Why "Finish setup" is refused (a message): the Security page's last scan says a passwordless sudo rule
    names an account sshd lets in by password, the one state an image leaves that makes a guessed
    password root. None when it does not, or no scan has run."""
    try:
        # Any scan, an older version's too: this refusal reads only a finding's id and status.
        scan = json.loads(SECURITY_STATE.read_text())
    except (OSError, ValueError):
        return None
    for f in scan.get("findings", []) if isinstance(scan, dict) else []:
        if isinstance(f, dict) and f.get("id") == "sudo-nopasswd" and f.get("status") == "problem":
            return M(
                "setup.blocked",
                "Not finished yet. {reason} The Security page offers the fixes. Then finish here.",
                reason=f.get("summary") or f.get("detail", ""),
            )
    return None


AUDIT_STATE = CONTROL_DIR / "security-audit.json"
SECURITY_LOG = CONTROL_DIR / "security-updates.log"
IFACE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
SECURITY_CHOICE_RE = re.compile(
    r"^[a-z-]+(:[A-Za-z0-9@_][A-Za-z0-9@._/-]*)?$"
)  # tcp/8123: a port the hotspot firewall opens


SECURITY_ACCEPTED = STATE_DIR / "security-accepted.json"
ACCEPT_KEY_RE = re.compile(r"^(setting|service|finding|compound|kit|package|hub):[A-Za-z0-9:/._@+-]{1,180}$")


def security_accepted():
    """{item key: {at, title}}: what the owner has looked at and accepted as it is.
    The page lists them apart and leaves them out of its counts; Undo takes one back."""
    try:
        data = json.loads(SECURITY_ACCEPTED.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def security_accept(key, title, yes):
    if not isinstance(key, str) or not ACCEPT_KEY_RE.match(key):
        raise refused.Refused(M("accept.key.invalid", "Invalid item. Reload the page and try again."))
    return _accept(SECURITY_ACCEPTED, security_accepted(), key, title, yes)


def _accept(path, data, key, title, yes):
    """One doctor's accepted list, a key accepted (with when and its title) or taken back; written whole."""
    if yes:
        if len(data) >= 500 and key not in data:
            raise refused.Refused(
                M(
                    "accept.full",
                    "The list already has 500 accepted items. Reopen some items first.",
                )
            )
        data[key] = {"at": time.time(), "title": str(title or "")[:200]}
    else:
        data.pop(key, None)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
    return data


# The Box doctor's own accepted list: review findings (warn) the owner has looked at and leaves as they are, by the
# finding's id. The page lists them apart and leaves them out of its counts; a finding that becomes a problem shows again.
HEALTH_ACCEPTED = STATE_DIR / "health-accepted.json"
HEALTH_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:/._@+-]{0,180}$")


def health_accepted():
    try:
        data = json.loads(HEALTH_ACCEPTED.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def health_accept(key, title, yes):
    if not isinstance(key, str) or not HEALTH_KEY_RE.match(key):
        raise refused.Refused(M("accept.key.invalid", "Invalid item. Reload the page and try again."))
    return _accept(HEALTH_ACCEPTED, health_accepted(), key, title, yes)


def _tls_status():
    try:
        return json.loads((CONTROL_DIR / "tls" / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def security_snapshot():
    """What the Security page shows: the hub's own lines (which only the hub knows), then the
    root helper's last scan of the box. The lines follow HTTPS as it is."""
    tls = _tls_status()
    transport = tls.get("transport", "both" if tls.get("on") else "http")
    https, admin_only = bool(tls.get("on")), bool(tls.get("admin_only")) or transport in ("discovery", "https")
    to_https = {
        "go": "security-https",
        "where": M("go.security-https", "Security → HTTPS"),
    }
    pw_t = M("hub.password.title", "Administrator password")
    if unclaimed():
        pw = finding.finding(
            "admin-password",
            pw_t,
            "problem",
            M(
                "hub.password.unset",
                "No administrator account is set yet. The box is in its first use.",
            ),
            M(
                "hub.password.unset.why",
                "Until an administrator account is made, any device on the network can use the administration pages.",
            ),
            M(
                "hub.password.unset.fix",
                "Select Set up this box, and make the administrator account over HTTPS. Or make it at "
                "the console, over SSH.",
            ),
            commands=["sudo /opt/irate-box/irate-box hub_control set-admin NAME"],
        )
    elif https and admin_only:
        pw = finding.finding(
            "admin-password",
            pw_t,
            "ok",
            M(
                "hub.password.https-only",
                "It is set, and the administration pages answer over "
                "HTTPS only. The password never crosses the network "
                "unencrypted.",
            ),
        )
    elif https:
        pw = finding.finding(
            "admin-password",
            pw_t,
            "warn",
            M(
                "hub.password.both",
                "It is set. HTTPS is on, but the administration pages also answer over plain HTTP.",
            ),
            M(
                "hub.password.both.why",
                "A browser that opens them as http:// sends the password unencrypted. Anyone who "
                "listens on an open hotspot can read it.",
            ),
            M(
                "hub.password.both.fix",
                "Make the administration pages HTTPS only, after this device trusts the box's certificate.",
            ),
            do=to_https,
        )
    else:
        pw = finding.finding(
            "admin-password",
            pw_t,
            "warn",
            M("hub.password.plain", "It is set. It travels over plain HTTP."),
            M(
                "hub.password.plain.why",
                "On an open hotspot, anyone who listens can read it the first time a browser sends it.",
            ),
            M(
                "hub.password.plain.fix",
                "Turn HTTPS on, and install the box's certificate on your devices. Until then, sign in "
                "from the LAN or over Tailscale.",
            ),
            do=to_https,
        )
    https_t = M("hub.https.title", "HTTPS")
    hub = [
        pw,
        (
            finding.finding(
                "plain-http",
                https_t,
                "ok",
                M(
                    "hub.https.on",
                    "HTTPS is on. Devices that installed the box's certificate (from /certificate) use HTTPS when "
                    "they connect.",
                ),
                notes=[
                    (
                        M(
                            "hub.https.http-disabled",
                            "HTTP is disabled, including HTTP captive-portal discovery.",
                        )
                        if transport == "https"
                        else (
                            M(
                                "hub.https.http-discovery",
                                "HTTP serves only captive discovery probes and redirects other requests to HTTPS.",
                            )
                            if transport == "discovery"
                            else M(
                                "hub.https.plain-too",
                                "Plain HTTP still answers, for the sign-in sheet of the hotspot and for devices "
                                "without the certificate.",
                            )
                        )
                    )
                ],
            )
            if https
            else finding.finding(
                "plain-http",
                https_t,
                "warn",
                M("hub.https.off", "The box has no certificate in use."),
                M(
                    "hub.https.off.why",
                    "Other people on the same network can read and change what a browser and the box send to "
                    "each other: pages, messages and uploads.",
                ),
                M(
                    "hub.https.off.fix",
                    "Make the box's certificate. Then install it on your own devices from /certificate.",
                ),
                do=to_https,
            )
        ),
        finding.finding(
            "one-origin",
            M("hub.origin.title", "Apps on the address of the administration pages"),
            "warn",
            M(
                "hub.origin.shared",
                "The drawing editors (Excalidraw, Mermaid) and the hub's other apps run on the address of the "
                "administration pages.",
            ),
            M(
                "hub.origin.why",
                "A hostile page among them can act with your sign-in while you are signed in.",
            ),
            M(
                "hub.origin.fix",
                "Select Sign out after administration work. Signing out does not remove this risk.",
            ),
            notes=[
                M(
                    "hub.origin.own",
                    "Notes, the books, Git and the add-ons have addresses of their own. Giving the editors an "
                    "address of their own is planned.",
                )
            ],
            do={
                "hub": M(
                    "hub.origin.design",
                    "This is the design of the hub. There is nothing to set here. Accept the risk, or "
                    "sign out after administration work.",
                )
            },
        ),
    ]
    try:
        scan = finding.current(json.loads(SECURITY_STATE.read_text()))
    except (OSError, ValueError):
        scan = None
    try:
        audit = finding.current(json.loads(AUDIT_STATE.read_text()))
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
            imports[kind] = {
                "name": rep.get("name"),
                "ran": rep.get("ran"),
                "imported": rep.get("imported"),
                "results": len(rep.get("results", [])),
            }
        except (OSError, ValueError):
            pass
    return {
        "hub": hub,
        "scan": scan,
        "audit": audit,
        "deep": deep,
        "imports": imports,
        "log": log,
        "pending": _pending_actions("security-"),
        "results": control_results(5),
        "accepted": security_accepted(),
    }


HEALTH_STATE = CONTROL_DIR / "health.json"
INSTALL_LOG_DIR = Path(os.environ.get("HUB_LOG_DIR", "/var/log/irate-box"))
HEALTH_CHOICE_RE = re.compile(
    r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]{1,80}$"
    r"|^other-restart:[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]{0,200}$"
    r"|^(kiwix-rebuild|kiwix-off|rerun-install|net-scan|rtc-find|rtc-save|rtc-remove)$"
    r"|^crashwatch-(snapshots|panic|watchdog):(on|off)$"
    r"|^clock-set:\d{10}$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$"
    r"|^clock-sources:(image|chrony):[01]:[01]:[01]:[01]:(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})$|^clock-undo$"
    r"|^uplink-skip:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}:(reconnect|restart|radio|reboot)$"
    r"|^idle-(off|on):(meshtasticd|ModemManager|bluetooth)\.service$"
    r"|^led-show:(leave|alive|network|all)$|^led-(led|flash):[A-Za-z0-9:._@-]{1,64}$|^armbian-led:(on|off)$"
    r"|^uplink-reconnect:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$"
)
HELPER_STUCK_AFTER = 90  # seconds a request may wait before the page says the helper is not answering
HELPER_BUSY_UP_TO = 3 * 3600  # a job running longer than this counts as stuck too
# Busy spells logged, so that patterns in false alarms can be seen:
# one entry per job of the helper's that kept a request waiting past HELPER_STUCK_AFTER.
HELPER_BUSY_LOG = STATE_DIR / "helper-busy.json"
_busy_lock = threading.Lock()


def _helper_doing():
    """What the root helper is running now: the request it says it is on (control/helper-doing.json, e.g.
    "kit-fetch build"), else its child's command line where /proc shows root's processes (e.g.
    "apt-get install --download-only … stage-debug"). None when it can't be told."""
    try:
        d = json.loads((CONTROL_DIR / "helper-doing.json").read_text())
        said = " ".join(str(d.get(k) or "") for k in ("action", "detail")).strip()[:160]
        if said:
            return said
    except (OSError, ValueError, AttributeError):
        pass
    try:
        r = subprocess.run(  # noqa: TID251
            ["systemctl", "show", "irate-box-control.service", "-p", "MainPID"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        pid = r.stdout.strip().partition("=")[2]
        kids = Path(f"/proc/{pid}/task/{pid}/children").read_text().split() if pid.isdigit() and pid != "0" else []
        cmd = Path(f"/proc/{kids[0]}/cmdline").read_bytes().split(b"\0") if kids else []
    except (OSError, subprocess.SubprocessError):
        return None
    words = [w.decode(errors="replace") for w in cmd if w]
    # The command and its plain words; options' values (paths) kept short.
    return (
        " ".join(Path(w).name if w.startswith("/") else w for w in words if not w.startswith("Dir::") and w != "-o")[
            :160
        ]
        or None
    )


def _log_busy(started, busy, waited, now=None):
    """One entry per job: when it started, how long it ran, the longest any request waited behind it,
    what waited, and what the helper was doing. Updated as the job goes on; the last 200 kept.
    """
    now = now or time.time()
    waiting = []
    for path in CONTROL_REQUESTS.glob("*.json") if CONTROL_REQUESTS.exists() else ():
        try:
            waiting.append(str(json.loads(path.read_text()).get("action", "?"))[:40])
        except (OSError, ValueError, AttributeError):
            pass
    with _busy_lock:
        try:
            log = json.loads(HELPER_BUSY_LOG.read_text())
        except (OSError, ValueError):
            log = []
        entry = next((e for e in log if abs(e.get("job_started", 0) - started) < 10), None)
        if entry is None:
            entry = {
                "job_started": round(started),
                "doing": _helper_doing(),
                "waiting": [],
            }
            log.append(entry)
        entry.update(
            seen=round(now),
            busy_for=round(busy),
            longest_wait=max(entry.get("longest_wait", 0), round(waited)),
            waiting=sorted(set(entry["waiting"]) | set(waiting)),
        )
        if not entry.get("doing"):
            entry["doing"] = _helper_doing()
        try:
            _write_atomic(HELPER_BUSY_LOG, json.dumps(log[-200:]))
        except OSError:
            pass


def helper_busy_summary(now=None):
    """The last week's busy spells, for the Health page: how many, the longest wait, the commonest job."""
    now = now or time.time()
    try:
        log = [e for e in json.loads(HELPER_BUSY_LOG.read_text()) if e.get("seen", 0) > now - 7 * 86400]
    except (OSError, ValueError):
        return None
    if not log:
        return None
    kinds = {}
    for e in log:
        k = " ".join((e.get("doing") or "?").split()[:2])
        kinds[k] = kinds.get(k, 0) + 1
    top = max(kinds.items(), key=lambda x: x[1])
    return {
        "count": len(log),
        "longest_wait": max(e.get("longest_wait", 0) for e in log),
        "commonest": top[0],
        "commonest_n": top[1],
        "last": log[-1],
    }


def helper_busy():
    """Seconds the root helper has been at its current job, or None when it isn't running one. It
    takes one request at a time, so a long job (a toolkit's download, an update) keeps the rest
    waiting without anything being wrong (a kit's refresh of minutes is not "not answering").
    systemctl show is the hub's to ask."""
    try:
        r = subprocess.run(  # noqa: TID251
            [
                "systemctl",
                "show",
                "irate-box-control.service",
                "-p",
                "ActiveState",
                "-p",
                "InactiveExitTimestampMonotonic",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    f = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    if (
        f.get("ActiveState") not in ("activating", "active")
        or not f.get("InactiveExitTimestampMonotonic", "").isdigit()
    ):
        return None
    since = int(f["InactiveExitTimestampMonotonic"]) / 1e6
    return max(0, time.monotonic() - since) if since else None


QUEUE_DETAIL_KEYS = (
    "kit",
    "app",
    "choice",
    "name",
)  # what a request is for, safe to show: never its other fields


def helper_queue(done=8):
    """The root helper's queue, for the page: what it runs now (it says so in control/helper-doing.json), what
    waits, oldest first, and the last answers. A request shows only its action and what it is for (a kit, an
    app, a doctor's choice, a name): requests can carry passwords and passphrases."""
    now = time.time()

    def age(at):
        return round(max(0, now - at)) if isinstance(at, (int, float)) else None

    def detail(req):
        return next(
            (str(req[k])[:60] for k in QUEUE_DETAIL_KEYS if isinstance(req.get(k), str)),
            "",
        )

    try:
        d = json.loads((CONTROL_DIR / "helper-doing.json").read_text())
        running = {
            "action": str(d.get("action", ""))[:40],
            "detail": str(d.get("detail", ""))[:60],
            "for": age(d.get("started")),
        }
    except (OSError, ValueError, AttributeError):
        running = None
    waiting = []
    for path in CONTROL_REQUESTS.glob("*.json") if CONTROL_REQUESTS.exists() else ():
        try:
            req = json.loads(path.read_text())
            waiting.append(
                {
                    "id": path.stem[:32],
                    "action": str(req.get("action", "?"))[:40],
                    "detail": detail(req),
                    "waited": age(path.stat().st_mtime),
                }
            )
        except (OSError, ValueError, AttributeError):
            continue
    waiting.sort(key=lambda w: -(w["waited"] or 0))
    finished = [
        {
            "id": str(r.get("id", ""))[:32],
            "ok": bool(r.get("ok")),
            "message": str(r.get("message", ""))[:200],
            "ago": age(r.get("at")),
        }
        for r in control_results(done)
    ]
    return {"running": running, "waiting": waiting, "done": finished}


def helper_state():
    """Is the root helper answering? The hub can see that for itself: its requests wait in a
    folder it owns. One left for more than 90 s means everything on /admin that needs root
    goes nowhere, including the doctor, so the page says what to type instead; unless the helper
    is busy with an earlier job, which the page says instead (helper_busy)."""
    oldest = None
    for path in CONTROL_REQUESTS.glob("*.json") if CONTROL_REQUESTS.exists() else ():
        try:
            m = path.stat().st_mtime
        except OSError:
            continue
        oldest = m if oldest is None or m < oldest else oldest
    age = time.time() - oldest if oldest else 0
    busy = helper_busy() if age > HELPER_STUCK_AFTER else None
    working = busy is not None and busy < HELPER_BUSY_UP_TO
    if working:
        _log_busy(time.time() - busy, busy, age)
    return {
        "waiting": _pending_actions(""),
        "oldest": round(age),
        "stuck": age > HELPER_STUCK_AFTER and not working,
        "busy": round(busy) if working else None,
        "busy_log": helper_busy_summary(),
        "commands": [
            "sudo systemctl reset-failed irate-box-control.service irate-box-control.path",
            "sudo systemctl start irate-box-control.path",
            "sudo /opt/irate-box/irate-box health",
        ],
    }


def health_snapshot():
    """The Health page: the doctor's last report (root helper), the last install's record and
    the end of its output (both readable by the hub), and whether the helper is answering.
    """

    def load(path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    try:
        log = [
            ANSI_RE.sub("", line)
            for line in (INSTALL_LOG_DIR / "install.log").read_text(errors="replace").splitlines()[-80:]
        ]
    except OSError:
        log = []
    return {
        "report": finding.current(load(HEALTH_STATE)),
        "install": load(INSTALL_LOG_DIR / "install-state.json"),
        "log": log,
        "ladder": jsonlog.read(uplink.LADDER),
        "ladder_gaps": linkhistory.gaps(load(uplink.HISTORY)),
        "helper": helper_state(),
        "queue": helper_queue(),
        "pending": _pending_actions("health-"),
        "results": control_results(5),
        "progress": update_progress(),
        "watchers": watchers_glance(),
        "accepted": health_accepted(),
    }


def watchers_glance():
    """For the doctor's overview: the box's last start and why, the watchdog's last repair and where the
    link stands now (the watchers' record, the watchdog's own report)."""
    boots = watchlog.read(kinds={"boot"})
    repairs = watchlog.read(kinds={"repair"})
    try:
        u = json.loads(uplink.status_path().read_text())
    except (OSError, ValueError):
        u = {}
    return {
        "last_boot": boots[-1] if boots else None,
        "last_repair": repairs[-1] if repairs else None,
        "link": {k: u.get(k) for k in ("at", "state", "iface", "cause")} if u else None,
    }


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

    status = load(uplink.status_path())
    if status:
        # A report the watchdog stopped writing is old news: say so rather than show it as live.
        status["stale"] = time.time() - status.get("at", 0) > 3 * max(status.get("settings", {}).get("check", 60), 60)
    # Each link's uptime: hours for a week, days for 35, summed here from the watchdog's
    # five-minute slots, so the page gets a few KB rather than the slots.
    hist = load(uplink.HISTORY)
    return {
        "inventory": load(NETINV_STATE),
        "uplink": status,
        "uptime": linkhistory.summarize(hist),
        "ladder": jsonlog.read(uplink.LADDER),  # the escalation chart, on the Status tab
        "ladder_gaps": linkhistory.gaps(hist),  # its spans with no record (a freeze, a reboot)
        "joined": load(CONTROL_DIR / "wifi-joined.json") or [],  # networks added on the access tab (wifijoin.py)
        "conn_changes": load(CONTROL_DIR / "nm-connection-changes.json")
        or {},  # connections' own settings changed here
        "metered": _metered_view(),
        "other_ap": load(CONTROL_DIR / "other-ap.json") or {},  # other hotspot profiles: removed or left
        "accepted": health_accepted(),  # review items accepted (the Box doctor's list: a network one is "net:<id>")
        "wifisync": load(CONTROL_DIR / "wifisync.json")
        or {},  # mPWRD-OS's wifisync: as the image had it, if changed here
        "levels": uplink.levels(),
        "pending": _pending_actions("net-") + _pending_actions("uplink-") + _pending_actions("wifi-"),
        "results": control_results(5),
    }


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
    units = [
        m["status"]["unit"].replace("@hub.", f"@{os.environ.get('HUB_USER', 'hub')}.")
        for m in specs.values()
        if m.get("status", {}).get("unit")
    ]
    states = unit_states(units) if units else {}
    out = []
    for aid, m in specs.items():
        unit = m.get("status", {}).get("unit", "").replace("@hub.", f"@{os.environ.get('HUB_USER', 'hub')}.")
        _, active, _ = states.get(unit, (False, False, False))
        out.append(
            {
                "id": aid,
                **{k: m["addon"].get(k) for k in ("title", "summary", "consent", "needs")},
                "added": None if opts is None else m["addon"]["option"] in opts,
                "active": active,
            }
        )
    snap = update_snapshot()
    return {
        "addons": out,
        "known": opts is not None,
        "progress": snap["progress"],
        "log": snap["log"],
        "pending": _pending_actions("addon"),
        "results": control_results(5),
    }


# --- local add-ons --------------------------------------------------------------
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
    consent allows. The added copy (manifests.LOCAL_D) is what the owner
    agreed to; a newer catalogue entry with the same agreed part (manifests.agreed_part: consent
    text, what it connects to, browser storage, where it comes from) replaces it: the tile, its
    list, a moved pin. One that changes the agreed part waits for the owner to accept it (/admin);
    the agreed copy keeps running. Pasted add-ons have no catalogue entry and are left alone.
    """
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
            # Accepted, or back in the catalogue as it was (gone during an update, say): current again.
            if (state.get(i) or {}).get("status") in ("held", "gone"):
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
        added.append(
            {
                "id": i,
                "title": m["addon"]["title"],
                "summary": m["addon"]["summary"],
                "mode": access.mode_of(state, i),
                "installed": librarian.installed_app(i),
                "status": status.get(i, {}),
                "pin": m["source"].get("pin"),
                "repo": m["source"].get("repo"),
                "capabilities": m["capabilities"],
                "from_catalogue": i in cat,
                "consent": consents.get(i),
                "href": m["tile"]["href"],
                "catalogue": cat_state.get(i),
            }
        )
    have = {m["id"] for m in local}
    offered = [
        {
            "id": i,
            "title": c["addon"]["title"],
            "summary": c["addon"]["summary"],
            "consent": c["addon"]["consent"],
            "repo": c["source"].get("repo"),
            "pin": c["source"].get("pin"),
            "capabilities": c.get("capabilities", {}),
            "added": i in have,
        }
        for i, c in cat.items()
    ]
    job = {k: _library_job.get(k) for k in ("action", "result")}
    return {
        "catalogue": offered,
        "catalogue_error": cat_error,
        "added": added,
        "errors": errors,
        "addon_port": int(os.environ.get("HUB_ADDON_PORT", "8090")),
        "job": job,
        "running": librarian.is_running(),
    }


def _local_add(m, how):
    """Write a checked local manifest, record the consent, and fetch it in the background."""
    i = m["id"]
    path = confine.under(manifests.LOCAL_D, f"{i}.json")
    if path.exists():
        raise refused.Refused(_already_added(i))
    _write_atomic(path, json.dumps(m, indent=2) + "\n")
    consents = _read_consents()
    consents[i] = {"at": int(time.time()), "how": how, "consent": m["addon"]["consent"]}
    _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
    refresh_manifests()
    # Off until the owner switches it on, whatever was chosen for that name before (a removed
    # add-on, or a former built-in such as ELIZA): root's, which also puts it in the web
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
                return 400, refused.say(
                    M(
                        "addons.catalogue.unknown",
                        "Add-on not found in the catalogue. Reload the page and try again.",
                    )
                )
            if payload.get("agree") is not True:
                return 400, refused.say(CONSENT_FIRST)
            return 202, _local_add(cat[i], "catalogue")
        if action == "paste":
            m = payload.get("manifest")
            # Pasted: anyone's manifest, not one this hub's code offers. The page shows the
            # heavy warning; the request must say it was read.
            if payload.get("understood") != "I understand this runs someone else's code on this box's address":
                return 400, refused.say(
                    M(
                        "addons.paste.warning",
                        "Read the warning about pasted add-ons and confirm it first.",
                    )
                )
            manifests.check_local(m, "the pasted manifest", {x["id"] for x in manifests.load()})
            return 202, _local_add(m, "pasted")
        if action == "accept":
            # The catalogue's newer entry, whose agreed part changed: the owner has read the
            # changes on /admin and takes it. It replaces the copy, with a new consent record.
            i = str(payload.get("id", ""))
            cat = manifests.catalogue()
            if not LOCAL_ID_RE.match(i) or i not in cat or not confine.under(manifests.LOCAL_D, f"{i}.json").exists():
                return 400, refused.say(
                    M(
                        "addons.catalogue.not-added",
                        "This add-on was not added from the catalogue. Reload the page and try again.",
                    )
                )
            path = confine.under(manifests.LOCAL_D, f"{i}.json")
            if payload.get("agree") is not True:
                return 400, refused.say(CONSENT_FIRST)
            entry = cat[i]
            _write_atomic(path, json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
            consents = _read_consents()
            consents[i] = {
                "at": int(time.time()),
                "how": "catalogue",
                "consent": entry["addon"]["consent"],
            }
            _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
            refresh_manifests()
            librarian.reload_apps()
            library_start(
                "update",
                lambda: librarian.update([i], mode="update", log=lambda *_: None),
            )
            return 200, {"accepted": i}
        if action == "remove":
            i = str(payload.get("id", ""))
            if not LOCAL_ID_RE.match(i) or not confine.under(manifests.LOCAL_D, f"{i}.json").exists():
                return 400, refused.say(
                    M(
                        "addons.not-added",
                        "Add-on not found. It is not added on this box.",
                    )
                )
            try:
                librarian.remove_source(i)
            except librarian.LibrarianError:
                pass
            for p in (
                confine.under(manifests.ADDONS, i),
                confine.under(manifests.ADDONS, f".{i}.prev"),
                confine.under(manifests.ADDONS, f".{i}.new"),
            ):
                shutil.rmtree(p, ignore_errors=True)
            # Off first (root's, while the manifest is still there to name it), then gone.
            control_request({"action": "access", "app": i, "mode": "off"})
            confine.under(manifests.LOCAL_D, f"{i}.json").unlink(missing_ok=True)
            consents = _read_consents()
            consents.pop(i, None)
            _write_atomic(CONSENTS, json.dumps(consents, indent=2) + "\n")
            refresh_manifests()
            return 200, {"removed": i}
        return 400, refused.say(UNKNOWN_ACTION)
    except manifests.ManifestError as exc:
        return 400, refused.say(M("addons.manifest.invalid", "Invalid manifest: {reason}.", reason=str(exc)))
    except (ValueError, librarian.LibrarianError) as exc:
        return _busy_or(400, exc)


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
    auto = kitauto.load(STATE_DIR)
    return {
        "kit": kit,
        "progress": progress,
        "pending": _pending_actions("offline-kit") + _pending_actions("content-export"),
        "auto": {"when": auto["when"], "can_refresh": bool(auto["request"])},
        "books": {"count": len(books), "bytes": sum(b.stat().st_size for b in books)},
        "results": control_results(5),
    }


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
    return {
        "scan": scan,
        "progress": progress,
        "books": books,
        "pending": _pending_actions("usb-"),
        "results": control_results(5),
    }


def moderation_snapshot():
    now = CLOCK.ticks()
    with lock:
        msgs = live_messages(now)
    st = settings_snapshot()
    return {
        "now": now,
        "messages": list(reversed(msgs)),
        "board": BOARD.all_threads(),
        "drops": [store.public_meta(m) for m in DROP.list()],
        "queue": moderation_queue(),
        "reports": {
            "reasons": st["report_reasons"],
            "all_reasons": list(REPORT_REASONS),
            "threshold": st["report_threshold"],
            "hide": st["report_hide"],
        },
    }


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
        if ok:
            forget_report(f"drop:{payload['id']}")
    else:
        return 400, refused.say(UNKNOWN_ACTION)
    return (200 if ok else 404), moderation_snapshot()


# --- who sees what a person posts --------------------------------------------------------------
# Each signed-in person chooses, per app, whether what they post is seen by everyone here,
# signed-in people, or only them (their account page; accounts.py "posts"). Never wider than the
# app itself. A post carries its account and, if narrower than everyone, the choice; what anyone
# reads leaves out what they may not see (store.can_see). The admin's moderation sees all.
store.VIEWER = lambda handler: accounts.session(handler._session_token())
store.SEEN_BY = lambda me, app: accounts.prefs(me["name"])["posts"].get(app, "everyone")
store.REPORT_REASONS = lambda: settings_snapshot()["report_reasons"]
store.HIDDEN = lambda: hidden_by_reports()
# Saves tied to accounts: the admin's setting, off by default.
store.CROSS_DEVICE = lambda: settings_snapshot()["saves_cross_device"]


def seen_by_of(account, app):
    """The narrower-than-everyone choice an account's new post carries, or None."""
    if not account:
        return None
    seen = accounts.prefs(account)["posts"].get(app, "everyone")
    return seen if seen in ("users", "me") else None


# --- reports ---------------------------------------------------------------------------------
# Anyone may report a shoutbox message or a forum post, for one of the reasons the owner offers;
# the owner decides how many reports put it in the queue on /admin → Moderation, and whether it is
# hidden from everyone else until they have looked. A post is known by where it is and when it was
# written: shoutbox:<created>:<name>, board:<thread>:<created>. One report per visitor per post: the
# visitor is known by a hash of their address with a salt made when the hub starts, in memory only.
REPORTS_FILE = STATE_DIR / "reports.json"
_reports_lock = threading.Lock()
_report_salt = secrets.token_bytes(32)
_reported = set()  # hashes of (visitor, post): who has reported what, this run only
_report_times = {}  # visitor hash -> recent report times (at most REPORTS_PER_HOUR an hour)
REPORTS_PER_HOUR = 20
# A notes page by its path in SilverBullet's space (no leading slash, no "..", no control characters).
REPORT_KEY_RE = re.compile(
    r"^(shoutbox:\d{1,20}:.{1,40}|board:\d{1,12}:\d{1,20}|drop:[A-Za-z0-9_-]{1,64}"
    r"|notes:(?!/)(?!(?:.*/)?\.\.(?:/|$))[^\x00-\x1f]{1,200})$"
)


def reports():
    try:
        data = json.loads(REPORTS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return (
        {k: v for k, v in data.items() if REPORT_KEY_RE.match(k) and isinstance(v, dict)}
        if isinstance(data, dict)
        else {}
    )


def save_reports(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REPORTS_FILE.parent / (REPORTS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, REPORTS_FILE)


def report_key(app, ref):
    """The key of a post from what the page sends: shoutbox {created, name}; board {thread, created};
    drop {id}; notes {page}."""
    if not isinstance(ref, dict):
        return None
    if app == "shoutbox" and type(ref.get("created")) in (int, float) and isinstance(ref.get("name"), str):
        key = f"shoutbox:{int(ref['created'])}:{ref['name'][:40]}"
    elif app == "board" and type(ref.get("thread")) is int and type(ref.get("created")) in (int, float):
        key = f"board:{ref['thread']}:{int(ref['created'])}"
    elif app == "drop" and isinstance(ref.get("id"), str):
        key = f"drop:{ref['id']}"
    elif app == "notes" and isinstance(ref.get("page"), str):
        key = f"notes:{ref['page'].strip('/')}"
    else:
        return None
    return key if REPORT_KEY_RE.match(key) else None


def report(app, ref, reason, addr):
    """One visitor's report. (status, message), the message a msg.M."""
    st = settings_snapshot()
    key = report_key(app, ref)
    if key is None or reason not in st["report_reasons"]:
        return 400, M("report.invalid", "Invalid report. Select one of the reasons offered.")
    who = hmac.new(_report_salt, addr.encode(), hashlib.sha256).digest()[:16]
    mine = hmac.new(_report_salt, who + key.encode(), hashlib.sha256).digest()[:16]
    now = time.monotonic()
    with _reports_lock:
        recent = [t for t in _report_times.get(who, []) if now - t < 3600]
        if len(recent) >= REPORTS_PER_HOUR:
            return 429, M("report.too-many", "Too many reports. Try again later.")
        if mine in _reported:
            return 200, M("report.again", "You reported this already. Thank you.")
        _reported.add(mine)
        _report_times[who] = recent + [now]
        data = reports()
        r = data.setdefault(key, {"app": app, "count": 0, "reasons": {}, "first": CLOCK.ticks()})
        r["count"] += 1
        r["reasons"][reason] = r["reasons"].get(reason, 0) + 1
        r["last"] = CLOCK.ticks()
        save_reports(data)
    return 200, M("report.done", "Reported. Thank you. An administrator will review it.")


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
    files = {m.get("id"): m for m in DROP.list()}
    out = []
    for k, r in reports().items():
        if r.get("kept") or r.get("count", 0) < st["report_threshold"]:
            continue
        kind, _, rest = k.partition(":")
        if kind == "drop" and rest in files:
            out.append(
                dict(
                    r,
                    key=k,
                    by=files[rest].get("by") or "",
                    text=files[rest].get("name", ""),
                    where="File drop",
                    drop=rest,
                )
            )
            continue
        if kind == "notes":
            out.append(dict(r, key=k, by="", text=rest, where="Notes", link="/notes/" + rest))
            continue
        if k in msgs:
            m = msgs[k]
            out.append(
                dict(
                    r,
                    key=k,
                    by=m.get("name", ""),
                    text=m.get("text", ""),
                    where="Shoutbox",
                )
            )
        elif k in posts:
            t, i, p = posts[k]
            out.append(
                dict(
                    r,
                    key=k,
                    by=p.get("author", ""),
                    text=p.get("text", ""),
                    where=f"Board: {t.get('title', '')}",
                    thread=t["id"],
                    index=i,
                )
            )
    out.sort(key=lambda x: (-x["count"], -x.get("last", 0)))
    return out


def store_snapshot():
    return {
        "now": CLOCK.ticks(),
        "saves": [store.public_meta(m) for m in STORE.list_saves()],
        "usage": STORE.usage(),
        "drop": DROP.usage(),
    }


def store_action(payload):
    action, key = payload.get("action"), str(payload.get("id", ""))
    if action in ("rename", "delete") and not store.ID_RE.match(key):
        return 400, refused.say(store.BAD_ID)
    try:
        if action == "rename":
            name = str(payload.get("name", "")).strip()[: store.MAX_NAME]
            if not name:
                return 400, refused.say(store.NAME_REQUIRED)
            if STORE.rename_save(key, name, force=True) is None:
                return 404, refused.say(store.NOT_FOUND)
        elif action == "delete":
            STORE.delete_save(key, force=True)
        elif action == "clear":
            STORE.clear_namespace(str(payload.get("namespace", "")))
        else:
            return 400, refused.say(UNKNOWN_ACTION)
    except ValueError as exc:
        return 400, refused.answer(exc)
    return 200, store_snapshot()


def card_written():
    """Bytes written to the block device under / since boot (its stat's sectors written), or None."""
    try:
        source = next(
            line.split()[-2] for line in Path("/proc/self/mountinfo").read_text().splitlines() if line.split()[4] == "/"
        )
        dev = Path(source).name
        return int((Path("/sys/class/block") / dev / "stat").read_text().split()[6]) * 512
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def system_status():
    """Memory and disk for the box tile, in bytes. MemAvailable, not MemFree: the page
    cache Kiwix leans on counts as available, and is the first thing to go."""
    out = {}
    try:
        out["boot_uptime"] = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    written = card_written()
    if written is not None:
        out["card_written"] = written
    try:
        with open("/proc/meminfo") as fh:
            mem = {k: int(v.split()[0]) * 1024 for k, v in (line.split(":", 1) for line in fh)}
        out["mem_total"], out["mem_available"] = mem["MemTotal"], mem["MemAvailable"]
        out["swap_total"], out["swap_free"] = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
    except (OSError, KeyError, ValueError):
        pass
    try:
        st = os.statvfs(STATE_DIR)
        out["disk_total"], out["disk_free"] = (
            st.f_blocks * st.f_frsize,
            st.f_bavail * st.f_frsize,
        )
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
        return int(path[len(prefix) :].strip("/"))
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
    # several guests, or a proxy that does not reuse them -- overflows it on a small board and
    # waits out SYN retries: p99 over 2 s.
    request_queue_size = 64


# Whatever part of a request's body a route leaves unread is read away (or the connection
# closed) after it answers, so the web server's kept-alive connection never carries it into the
# next request. The security doctor looks for this marker.
DRAINS_REQUEST_BODIES = True
MAX_JSON = 256 * 1024  # the largest JSON body the hub reads; the store and drop have their own
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
            reach.note(self._client_addr())  # from the internet? (the security doctor asks)
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

    def _send_backup(self, with_syncthing=False, level="data"):
        """The hub's state as a .tar.gz, streamed, at the level asked for (hub/backup.py): settings only,
        or settings and data; Syncthing's identity only when asked for. BACKUP-CONTENTS.txt at the top
        says which this is."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        kind = ("settings" if level == "settings" else "settings-and-data") + (
            "-with-syncthing-keys" if with_syncthing else ""
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/gzip")
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="irate-box-{kind}-{stamp}.tar.gz"',
        )
        self.send_header("Connection", "close")  # no length known in advance
        self.end_headers()
        self.close_connection = True

        keep = backup.keep_for(level, with_syncthing, backup.git_mirrors(STATE_DIR))

        if with_syncthing:
            about = (
                "This backup CONTAINS THE SYNCTHING PRIVATE KEYS AND CONFIGURATION\n"
                "(.local/state/syncthing: key.pem, https-key.pem, config.xml).\n"
                "Anyone who has this file can pose as this box to its Syncthing peers.\n"
                "Keep it as you keep a password. Delete copies that you do not need.\n"
            )
        else:
            about = (
                "The Syncthing identity (private keys, config.xml) is NOT in this backup.\n"
                "Restored on a box, Syncthing starts with a new device ID, and its peers\n"
                "must be paired again.\n"
            )
        held = (
            "SETTINGS ONLY: what was chosen (the hub's settings, accounts, the apps' and add-ons'\n"
            "settings, the library's and mirrors' sources), none of what was made on the box.\n"
            if level == "settings"
            else "SETTINGS AND DATA: what was chosen, and what was made on the box (notes, saved work,\n"
            "the board, the shoutbox, dropped files, the box's own git repositories).\n"
        )
        about = (
            f"Irate-Box state backup, {stamp} UTC, from {hub_version()}.\n\n{held}\n{about}\n"
            "Left out: books, the firmware and git mirrors, builds and their caches, crash\n"
            "evidence, the library's archive (all downloaded again), the GitHub token, temporary files.\n"
        )
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
        second or two the path unit takes to act, and permanently if it is not set up.
        """
        proxied = "X-Forwarded-For" in self.headers
        state = next(
            (s["state"] for s in service_status(proxied) if s["name"] == TAILSCALE_NAME),
            "missing",
        )
        self.send_json(
            200,
            {
                "want": read_tailscale_want(),
                "state": state,
                "max_hours": TAILSCALE_MAX_HOURS,
                "backend": tailscale_backend() if state == "running" else None,
            },
        )

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
        body = (
            f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Irate-Box</title>'
            f'<meta http-equiv="refresh" content="0; url={url}"></head>'
            f'<body><p><a href="{url}">Open the hub</a></p></body></html>'
        ).encode()
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
        into the next request (the next request fails, or is another). Runs
        after every request (handle_one_request), and before a refusal that reads nothing.
        """
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
        self.send_json(
            403,
            refused.say(
                M(
                    "hub.admin.front",
                    "Refused. Open /admin through the web server of the box.",
                )
            ),
        )
        return True

    # --- accounts (accounts.py): the session cookie, and whether the request came over HTTPS

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
            # basic=1 (Caddy, the box's own login on and an admin account ready): a request carrying a
            # login of its own (a script's) goes on to Caddy's basic_auth, which checks it; anyone else
            # without an admin session is sent to the standard sign-in rather than asked for that login.
            if (
                admin
                or "soft=1" in query
                or ("basic=1" in query and self.headers.get("Authorization", "").startswith("Basic "))
            ):
                self.send_response(204)
                self.send_header("X-Irate-Session", "admin" if admin else "")  # always: see git-access
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            ok = False
        elif path == "/_irate/admin":
            # Unclaimed: /admin shows only the set-the-password page, so it asks no login; the shell,
            # Syncthing and private apps behind the same gate must not open (nginx says which page).
            # nginx sends the request's URI as typed but routes on the normalised one, so
            # "/admin/../term/" reaches the shell's location: judged here as the browser and
            # nginx see it, after unquoting and normalising.
            import posixpath

            uri = posixpath.normpath(unquote(self.headers.get("X-Original-URI", "").partition("?")[0]) or "/")
            ok = admin or (unclaimed() and (uri == "/admin" or uri.startswith("/admin/")))
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
            self.send_json(
                401,
                refused.say(
                    M(
                        "hub.admin.sign-in",
                        "Administrator access required. Sign in as an administrator.",
                    )
                    if path == "/_irate/admin"
                    else accounts.SIGN_IN_FIRST
                ),
            )

    def _signed_in(self):
        return accounts.session(self._session_token()) is not None if self._session_token() else False

    def _viewer(self):
        """The visitor's level for what the hub shows them: False, True, or "admin" (an admin account,
        or the admin lately at /admin: the box's own login)."""
        if admin_seen_ok(self._cookie(ADMIN_SEEN_COOKIE)):
            return "admin"
        me = accounts.session(self._session_token()) if self._session_token() else None
        return False if me is None else "admin" if me.get("role") == "admin" else True

    def _cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _session_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == SESSION_COOKIE:
                return v
        return ""

    def _session_cookie(self, token, max_age):
        return (
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"
            + ("; Secure" if self._https() else ""),
        )

    def _client_addr(self):
        fwd = self.headers.get("X-Forwarded-For", "")
        return fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]

    def _hue(self, payload, poster):
        """The colour a post is written in: the page's choice, or for someone logged in with none, their
        account's own."""
        hue = payload.get("hue")
        if hue is None and poster and poster[1]:
            hue = accounts.prefs(poster[0])["hue"]
        return hue

    def _note_account(self):
        """A signed-in visitor's name, as around now: in memory only."""
        token = self._session_token()
        if token:
            me = accounts.session(token)
            if me:
                note_account(me["name"])

    def _account_post(self, payload):
        """/api/account: sign up, log in or out, change a password, set one with a code. A page
        elsewhere can't make these (the header, as /admin's), nor is a password taken over plain
        HTTP when the admin has prevented it."""
        site = self.headers.get("Sec-Fetch-Site")
        origin = self.headers.get("Origin")
        if (
            self.headers.get("X-Irate-Account") != "1"
            or (site and site not in ("same-origin", "none"))
            or (origin and urlparse(origin).netloc != self.headers.get("Host", ""))
        ):
            self.send_json(
                403,
                refused.say(
                    M(
                        "accounts.cross-site",
                        "Refused. Use the account page of this box.",
                    )
                ),
            )
            return
        action, addr = payload.get("action"), self._client_addr()
        if action == "logout":
            me = accounts.session(self._session_token())
            accounts.logout(self._session_token())
            if me and not accounts.has_session(me["name"]):
                NOTEBOOKS.close(me["name"])
            self.send_json(200, {"me": None}, [self._session_cookie("", 0)])
            return
        if (
            action in ("signup", "login", "password", "code")
            and not self._https()
            and accounts.settings()["http"] == "prevented"
        ):
            self.send_json(
                403,
                dict(
                    refused.say(
                        M(
                            "accounts.https-only",
                            "This box accepts passwords only over HTTPS. Open this page with https://.",
                        )
                    ),
                    https=False,
                ),
            )
            return
        try:
            if action == "signup":
                state = accounts.signup(payload.get("name"), payload.get("password"), addr)
                if state == "asked":
                    self.send_json(200, {"state": "asked"})
                    return
                action = "login"
            if action == "login":
                token, me = accounts.login(
                    payload.get("name"),
                    payload.get("password"),
                    addr,
                    https=self._https(),
                )
                self.send_json(
                    200,
                    {"me": me},
                    [self._session_cookie(token, accounts.SESSION_DAYS * 86400)],
                )
            elif action == "password":
                accounts.change_password(
                    self._session_token(),
                    payload.get("old"),
                    payload.get("new"),
                    addr,
                    https=self._https(),
                )
                self.send_json(200, {"changed": True})
            elif action == "code":
                before = accounts.admin_ready()
                name = accounts.use_code(
                    payload.get("code"),
                    payload.get("password"),
                    addr,
                    https=self._https(),
                )
                self._admin_gate_follow(before)
                self.send_json(200, {"name": name})
            elif action == "prefs":
                # A person's own settings: theirs alone, through their own session.
                self.send_json(
                    200,
                    {"prefs": accounts.set_prefs(self._session_token(), payload.get("prefs"))},
                )
            else:
                self.send_json(400, refused.say(UNKNOWN_ACTION))
        except accounts.Wait as exc:
            self.send_json(429, refusal(exc))
        except accounts.AccountError as exc:
            self.send_json(400, refusal(exc))

    def _admin_gate_follow(self, before):
        """The admin accounts changed: if one being able to sign in became true or false, the root helper
        rewrites the admin's gates (no login prompt of the box's own while one can; hub_control admin-gate).
        """
        if accounts.admin_ready() != before:
            control_request({"action": "admin-gate"})

    def _forged(self, path):
        """An /admin POST a page elsewhere could have made: it must carry X-Irate-Admin,
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
            self.send_json(
                403,
                refused.say(
                    M(
                        "hub.admin.cross-site",
                        "Refused. An /admin change must come from the /admin page itself.",
                    )
                ),
            )
        return not ok

    # What /admin answers on a box in its first use, to anyone (audit M4): the set-up page, its request's progress
    # (?id=), and making the first administrator account. Nothing else of /admin: until the box has an owner, the
    # first person on its network would otherwise read and change every setting.
    SETUP_PATHS = frozenset(("/admin/setup",))

    def _admin_locked(self, path):
        """A box in its first use: /admin is the set-up page alone (SETUP_PATHS), to anyone on its network, until the
        owner makes the first administrator account there or at the console (set-admin). The pages send a browser
        there; any other /admin request is refused, read or write. An administrator's session passes: the one
        the set-up page signs in with, before the root helper has ended the first use. The box's own login is never
        set from here (/admin/password: script-login at the console)."""
        if not unclaimed() or not (path == "/admin" or path.startswith("/admin/")):
            return False
        me = None if path == "/admin/password" else accounts.session(self._session_token())
        if path in self.SETUP_PATHS or (me is not None and me.get("role") == "admin"):
            return False
        self._discard_body()
        if self.command == "GET" and (path in ("/admin", "/admin/") or path.endswith(".html")):
            self.send_response(302)
            self.send_header("Location", "/admin/setup")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        self.send_json(403, refused.say(SETUP_FIRST))
        return True

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

    def _send_notes(self):
        """The shared notes (SilverBullet's space) as a zip."""
        self._send_zip(
            STATE_DIR / "notes",
            "notes",
            M(
                "notes.download.none",
                "No notes yet. SilverBullet is not installed, or nobody has written a note.",
            ),
        )

    def _send_zip(self, base, stem, missing):
        """A SilverBullet space as a zip: its pages and attachments, without its own hidden files (a
        dot at any level) and never past a link out of the folder. missing: a message, when there is none.
        """
        if not base.is_dir():
            self.send_json(404, refused.say(missing))
            return
        root = base.resolve()
        with tempfile.SpooledTemporaryFile(max_size=32 << 20) as buf:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(
                        d for d in dirnames if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d))
                    )
                    for name in sorted(filenames):
                        full = Path(dirpath) / name
                        if name.startswith(".") or full.is_symlink() or not full.is_file():
                            continue
                        z.write(full, str(full.relative_to(root)))
            size = buf.tell()
            buf.seek(0)
            name = f"{stem}-{time.strftime('%Y-%m-%d')}.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                shutil.copyfileobj(buf, self.wfile, 1 << 20)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_kit(self):
        """The offline kit, streamed (with books it runs to gigabytes)."""
        try:
            kit = json.loads((KITS_DIR / "kit.json").read_text())
            name = kit["name"]
            if not re.fullmatch(r"irate-box-(kit|content)-[A-Za-z0-9._-]{1,120}\.tar", name):
                raise ValueError(name)
            fh = open(KITS_DIR / name, "rb")
        except (OSError, ValueError, KeyError, TypeError):
            self.send_json(
                404,
                refused.say(M("kit.download.none", "No offline kit yet. Make one first.")),
            )
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
            self.send_json(
                404,
                refused.say(
                    M(
                        "hub.source.none",
                        "No source archive. The installer makes it, and this copy of irate-box was not installed.",
                    )
                ),
            )
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
        self._note_account()
        refresh_manifests()
        if self._is_captive_probe() or self._off_hub_name():
            self._redirect_to_hub()
            return

        path = self.path.split("?")[0]
        if path in ("/http-admin-warning", "/http-admin-continue"):
            nxt = self.path.partition("?next=")[2]
            if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or any(ord(char) < 32 for char in nxt):
                nxt = "/admin/"
            if path == "/http-admin-continue":
                self.send_response(303)
                self.send_header("Set-Cookie", "irate_http_ok=1; Path=/; HttpOnly; SameSite=Strict")
                self.send_header("Location", nxt)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            else:
                body = (
                    '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    '<title>Unencrypted administrator access</title><link rel="stylesheet" href="/palette.css">'
                    "<main><h1>Unencrypted administrator access</h1><p>Other people on this network can read your password "
                    'and change traffic sent over HTTP. Use HTTPS when possible.</p><p><a href="/http-admin-continue?next='
                    + html.escape(nxt, quote=True)
                    + '">Continue over HTTP</a></p></main></html>'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
            return
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

        if path == "/_irate/notebook":
            # The notebooks' origin asks on every request: whose notebook (a header nginx routes on).
            code, k = NOTEBOOKS.gate(accounts.session(self._session_token()))
            self.send_response(204 if code == 204 else code)
            if k:
                self.send_header("X-Irate-Notebook", k)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/notebook":
            self.send_json(200, NOTEBOOKS.status(accounts.session(self._session_token())))
            return

        if path == "/api/notebook/download":
            me = accounts.session(self._session_token())
            k = notebooks.key(me["name"]) if me else None
            if k is None:
                self.send_json(
                    401,
                    refused.say(
                        M(
                            "notebook.download.sign-in",
                            "Sign in to download your notebook.",
                        )
                    ),
                )
                return
            self._send_zip(
                NOTEBOOKS.folder(k),
                f"notebook-{k}",
                M(
                    "notebook.download.none",
                    "No notebook yet. Open your notebook once first.",
                ),
            )
            return

        if path == "/admin/notebooks":
            self.send_json(200, NOTEBOOKS.view())
            return

        if path == "/api/account":
            # The account page's view: what the admin allows, who this is, and their own settings.
            me = accounts.session(self._session_token())
            self.send_json(
                200,
                dict(
                    accounts.settings(),
                    https=self._https(),
                    me=me,
                    prefs=accounts.prefs(me["name"]) if me else None,
                    names_to=settings_snapshot()["names_to"],
                ),
            )
            return

        if path == "/api/trends":
            # The memory and disk history behind the hub page's box tile: an admin account's session only (a guest,
            # or a user, is refused here, not merely not shown the triangle), and the samples alone, never the processes.
            me = accounts.session(self._session_token()) if self._session_token() else None
            if not me or me.get("role") != "admin":
                self.send_json(
                    403,
                    refused.say(M("hub.trends.admin", "Only administrators can see the trends.")),
                )
                return
            snap = memhistory.snapshot()
            self.send_json(
                200,
                {
                    "now": snap["now"],
                    "every": snap["every"],
                    "samples": snap["samples"],
                },
            )
            return

        if path == "/admin/accounts":
            self.send_json(200, accounts_view())
            return

        if path == "/admin/setup" and "?" in self.path:
            # The first use's request, followed (?id=); without a query, the page itself (below).
            query = dict(p.partition("=")[::2] for p in self.path.partition("?")[2].split("&") if p)
            self.send_json(200, setup_status(query.get("id", "")[:40]))
            return

        if store.handle(self, "GET", path, STORE, DROP):
            return

        if path == "/api/report":
            # The reasons a report may give, for a page with no listing of its own to carry them (the hub bar).
            self.send_json(200, {"reasons": settings_snapshot()["report_reasons"]})
            return

        if path == "/messages":
            now = CLOCK.ticks()
            with lock:
                msgs = live_messages(now)
            hide = hidden_by_reports()
            if hide:
                msgs = [
                    m for m in msgs if f"shoutbox:{int(m.get('created', 0))}:{str(m.get('name', ''))[:40]}" not in hide
                ]
            me = accounts.session(self._session_token())
            msgs = [m for m in msgs if store.can_see(m, me)]
            self.send_json(
                200,
                {
                    "now": now,
                    "ttl": SHOUT_TTL,
                    "messages": msgs,
                    "posting": self._posting_view("shout"),
                    "report_reasons": settings_snapshot()["report_reasons"],
                },
            )
            return

        if path == "/board/threads":
            listing, me = BOARD.list_threads(), accounts.session(self._session_token())
            listing["threads"] = [t for t in listing["threads"] if store.can_see(t, me)]
            self.send_json(200, dict(listing, posting=self._posting_view("board")))
            return

        if path == "/admin/folders":
            self.send_json(200, folders_snapshot())
            return

        if path == "/admin/status-tiles":
            self.send_json(200, status_tiles_snapshot())
            return

        if path == "/admin/tiles":
            self.send_json(200, tiles_snapshot())
            return

        if path == "/admin/apps":
            # The Apps and Folders groups of /admin: which sections each app owns.
            # switch: whether its access can be set (its page then starts with it).
            seen = seen_only()
            self.send_json(
                200,
                {
                    "apps": [
                        dict(
                            a,
                            switch=a["id"] in access.ROUTED or a["local"],
                            seen=a["id"] in seen,
                        )
                        for a in manifests.admin_apps(MANIFESTS)
                    ]
                },
            )
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
            query = {
                k: unquote(v.replace("+", " "))
                for k, v in (p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            }
            try:
                self.send_json(
                    200,
                    librarian.catalogue_search(
                        q=query.get("q", ""),
                        language=query.get("language", ""),
                        category=query.get("category", ""),
                        start=int(query.get("start") or 0),
                    ),
                )
            except ValueError:
                self.send_json(400, refused.say(BAD_REQUEST))
            except librarian.LibrarianError as exc:
                self.send_json(
                    502,
                    refused.say(
                        M(
                            "library.catalogue.failed",
                            "The box cannot read the Kiwix catalogue: {reason} The catalogue needs the internet.",
                            reason=getattr(exc, "message", None) or str(exc),
                        )
                    ),
                )
            return

        if path == "/admin/books":
            query = {
                k: unquote(v.replace("+", " "))
                for k, v in (p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            }
            try:
                self.send_json(
                    200,
                    librarian.books_page(
                        q=query.get("q", "")[:100],
                        language=query.get("language", "")[:20],
                        state=(query.get("state", "") if query.get("state") in librarian.BOOK_STATES else ""),
                        kept=query.get("kept", ""),
                        sort=query.get("sort", "title"),
                        page=int(query.get("page") or 1),
                        per_page=int(query.get("per_page") or librarian.PER_PAGE),
                        names_only=query.get("names") == "1",
                    ),
                )
            except ValueError:
                self.send_json(400, refused.say(BAD_REQUEST))
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
            q = self.path.partition("?")[2].split("&")
            self._send_backup(
                with_syncthing="syncthing=1" in q,
                level="settings" if "level=settings" in q else "data",
            )
            return

        if path == "/admin/backup/plan":
            self.send_json(200, backup_plan())
            return

        if path == "/admin/update":
            self.send_json(200, update_snapshot())
            return

        if path == "/admin/security":
            self.send_json(200, security_snapshot())
            return

        if path == "/admin/hotspot":
            self.send_json(200, dict(hotspot.snapshot(), share=share_level()))
            return

        if path == "/admin/packages":
            # Packages from their makers, watched (root/pkgwatch.py): what root published, and its answers.
            try:
                pkgs = json.loads((CONTROL_DIR / "pkgwatch.json").read_text())
            except (OSError, ValueError):
                pkgs = {}
            self.send_json(200, {"packages": pkgs, "results": control_results(5)})
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

        if path == "/admin/notes/download":
            self._send_notes()
            return

        if path == "/admin/access":
            state = access.read(ACCESS_STATE)
            vis = visibility()
            self.send_json(
                200,
                {
                    "apps": [
                        dict(
                            a,
                            mode=access.mode_of(state, a["id"]),
                            visible=vis.get(a["id"], "auto"),
                        )
                        for a in access.apps(MANIFESTS)
                    ],
                    "pages": {i: vis.get(i, "auto") for i in PAGE_APPS},
                    "seen": {i: vis.get(i, "auto") for i in sorted(seen_only())},
                    "locked_as": locked_as(),
                    "results": control_results(),
                },
            )
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
            # What the security doctor flags about a kit's cache, shown before an install
            # (an upgrade is fine unless it is a flagged issue).
            try:
                audit = json.loads((CONTROL_DIR / "security-audit.json").read_text())
                snap["flags"] = {
                    i["about"]["key"]: i["titles"]
                    for i in (audit.get("joint") or {}).get("items", [])
                    if i["about"]["kind"] == "kit"
                }
                snap["flag_details"] = {
                    f["about"]["key"]: f.get("summary") or f.get("detail")
                    for st in audit.get("steps", [])
                    for f in st["findings"]
                    if (f.get("about") or {}).get("kind") == "kit" and f["status"] != "ok"
                }
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
            # The decoder bridge: everything it heard, the messages too, and the channels
            # by name (never their keys).
            self.send_json(
                200,
                dict(
                    MESH.heard.view(texts=True),
                    state=MESH.state,
                    error=MESH.error,
                    error_message=MESH.error_message,
                    channels=meshbridge.channels_view(),
                ),
            )
            return

        if path == "/admin/tls":
            # HTTPS: the root helper's copy of what it made (root/tls.py status).
            try:
                st = json.loads((CONTROL_DIR / "tls" / "status.json").read_text())
            except (OSError, ValueError):
                st = {"set_up": False}
            try:
                policy = json.loads((CONTROL_DIR / "security-policy.json").read_text())
            except (OSError, ValueError):
                policy = {"mode": "hub"}
            self.send_json(
                200,
                dict(
                    st,
                    policy=policy,
                    pending=_pending_actions("tls-") + _pending_actions("security-policy"),
                    results=control_results(5),
                ),
            )
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
                self.send_json(400, refused.answer(exc))
            return

        if path == "/admin/ci/run":
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            try:
                offset = int(query.get("from", "0"))
            except ValueError:
                offset = 0
            view = ci.run_view(unquote(query.get("run", "")), offset)
            self.send_json(
                *(
                    (200, view)
                    if view
                    else (
                        404,
                        refused.say(M("ci.run.not-found", "Build not found. It was deleted.")),
                    )
                )
            )
            return

        if path == "/admin/ci/file":
            # A build's log (shown as text) or one of its artifacts (a download, never shown).
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            name = unquote(query.get("name", ""))
            f = ci.run_file(unquote(query.get("run", "")), name)
            if not f:
                self.send_json(
                    404,
                    refused.say(
                        M(
                            "ci.file.not-found",
                            "File not found. The build was deleted, or it did not make this file.",
                        )
                    ),
                )
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

        admin_page = False
        if path in ("/admin", "/admin/"):
            path = "/admin.html"
            admin_page = not unclaimed()
        elif path == "/admin/setup":
            # The first use's page: making the admin account; after it, /admin.
            if not unclaimed():
                self.send_response(302)
                self.send_header("Location", "/admin/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            path = "/admin-setup.html"
        elif path == "/admin/factory.html":
            # The Firmware Factory's own page: behind /admin's login, as /admin is.
            path = "/admin-factory.html"
        elif path == "/admin/mesh.html":
            # Mesh's Heard, the messages' texts too (as the Factory's page): behind /admin's login.
            path = "/admin-mesh.html"

        if path == "/api/guest-net":
            # What the sheet shows a guest: whether the owner shares the connection, at what level,
            # and whether this device is out. (root/share.py; the home page's banner, home.js.)
            level, addr = share_level(), self._client_addr()
            self.send_json(
                200,
                {
                    "level": level,
                    "here": on_hotspot(addr),
                    "out": level == "open" or let_out(addr),
                    "signed_in": bool(self._signed_in()),
                    "hours": SHARE_HOURS,
                },
            )
            return

        if path == "/api/captive":
            # RFC 8908's captive-portal API, named by the hotspot's DHCP (option 114, RFC 8910):
            # tells a phone outright that this network has a sign-in page and where, so it need
            # not guess from probes. Captive unless the owner shares the connection and this device
            # is let out (or everyone is: "open"); root/share.py.
            level, addr = share_level(), self._client_addr()
            out = level == "open" or (level in SHARE_LET_OUT and let_out(addr))
            body = json.dumps({"captive": not out, "user-portal-url": HUB_URL if HUB_HOST else "/"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/captive+json")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/menus.json":
            # The list pages, for the hub bar's ↑ (app.js): {id: {title, href}}, public ones only.
            hidden = hidden_apps(self._viewer())
            self.send_json(
                200,
                {
                    i: {"title": mm["menu"]["title"], "href": mm["tile"]["href"]}
                    for i, mm in manifests.menus(MANIFESTS).items()
                    if i not in hidden
                },
            )
            return

        if path == "/internal/git-access":
            # nginx's auth_request for a push or fetch (gitrepos.decide): 204 if no login is needed
            # or the request's own credentials are an account's that may; 403 if only the box's own
            # login will do (nginx then asks for it). Anything on loopback may ask what a guest may
            # do; an account's answer needs its password.
            mode = access.mode_of(access.read(ACCESS_STATE), "git")
            account = accounts.check_basic(
                self.headers.get("Authorization", ""),
                self.headers.get("X-Forwarded-For", ""),
            )
            ok = gitrepos.decide(
                self.headers.get("X-Original-URI", ""),
                self.headers.get("X-Original-Method", "GET"),
                mode,
                account,
            )
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
                self.send_header(
                    "X-Irate-User",
                    account["name"] if vouched and account is not None else "",
                )
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_empty(204 if ok else 403)
            return

        if path == "/mesh.json":
            # The mesh heard through the box's broker (meshbridge.py): nodes and traffic, never the
            # messages; only while the MQTT app is public (/admin → Access).
            if access.mode_of(access.read(ACCESS_STATE), "mqtt") != "public":
                self.send_json(404, refused.say(NOT_SHOWN))
                return
            self.send_json(200, dict(MESH.heard.view(texts=False), state=MESH.state))
            return

        if path in ("/certificate", "/certificate.json", "/certificate/ca.crt"):
            # HTTPS: the box's CA for guests to install, and what the page says of it.
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
                self.send_json(
                    200,
                    {
                        "set_up": bool(st.get("set_up")),
                        "on": bool(st.get("on")),
                        "port": (st.get("ports") or {}).get("main", 443),
                        "fingerprint": (st.get("ca") or {}).get("fingerprint"),
                        "names": cert.get("names", []),
                        "addresses": cert.get("addresses", []),
                        "expires": cert.get("expires"),
                    },
                )
                return
            try:
                body = (CONTROL_DIR / "tls" / "ca.crt").read_bytes()
            except OSError:
                self.send_json(
                    404,
                    refused.say(
                        M(
                            "tls.ca.none",
                            "No certificate yet. The administrator has not set up HTTPS.",
                        )
                    ),
                )
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
                self.send_json(404, refused.say(NOT_SHOWN))
                return
            if path == "/factory.json":
                self.send_json(200, factory.public_view())
                return
            query = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
            name = unquote(query.get("name", ""))
            f = factory.public_file(unquote(query.get("run", "")), name)
            if not f:
                self.send_json(
                    404,
                    refused.say(
                        M(
                            "factory.file.not-found",
                            "File not found. The firmware was unpublished.",
                        )
                    ),
                )
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

        if path in PICTURE_URLS:
            name = PICTURE_URLS[path]
            try:
                body = PICTURES[name]["file"].read_bytes()
            except OSError:
                body = b""
            kind = picture_kind(name, body)
            if not kind:
                self.send_empty(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", len(body))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/layout.css":
            # The owner's page widths for every page: public, tiny, read after style.css.
            # No file of that name in web/, so both web servers hand it to the hub.
            with _settings_lock:
                cur = dict(_settings)
            body = (
                ":root { --page-width: %drem; --shout-width: %drem; --board-width: %drem; --emoji-set: %s; }\n"
                "body.admin { --page-width: %drem; }\n"
                % (
                    cur["page_width"],
                    cur["shout_width"],
                    cur["board_width"],
                    cur["emoji_set"],
                    cur["admin_width"],
                )
            )
            # An uploaded favicon (Appearance) over the pages' own emoji icons: themes.js reads this and swaps it in.
            fav = picture_mtime("favicon")
            if fav:
                body += ':root { --favicon: "%d"; }\n' % int(fav)
            if not cur["krab_art"]:
                body += (
                    ".admin-art, .art-loop { display: none !important; }\n"
                    '[data-art]:not([data-art=""]) { padding-bottom: 0 !important; }\n'
                )
            if cur["accent"]:
                # The main themes only (light, dark, or as the device): another theme keeps its colours.
                body += (
                    'html:root:not([data-palette]):not([data-theme]), html:root:not([data-palette])[data-theme="light"], '
                    'html:root:not([data-palette])[data-theme="dark"] '
                    "{ --accent: %s; --accent-ink: %s; }\n" % (cur["accent"], accent_ink(cur["accent"]))
                )
            body += palette_css(cur["palette"])  # after the one accent: a mode's own accent wins over it
            body = body.encode()
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
            # Different devices today and this week: the numbers only, while counting is on.
            counted = visitor_counts()
            if counted is not None:
                payload["visitors"] = counted
            # Signed in and around: the count for everyone; the names only to whom the owner
            # allows, and only of those who said yes on their own account page.
            around = online_names(now)
            payload["signed_in"] = len(around)
            me = accounts.session(self._session_token())
            if me and (settings_snapshot()["names_to"] == "users" or me.get("role") == "admin"):
                payload["names"] = [n for n in around if accounts.prefs(n)["show_online"]]
            self.send_json(200, payload)
            return

        tid = thread_id(path)
        if tid is not None:
            result = BOARD.get_thread(tid)
            if result is None:
                self.send_json(404, refused.say(NO_THREAD))
            else:
                me = accounts.session(self._session_token())
                if result["thread"]["posts"] and not store.can_see(result["thread"]["posts"][0], me):
                    self.send_json(404, refused.say(NO_THREAD))
                    return
                result = dict(
                    result,
                    thread=dict(
                        result["thread"],
                        posts=[p for p in result["thread"]["posts"] if store.can_see(p, me)],
                    ),
                )
                hide = hidden_by_reports()
                if hide:
                    t = dict(result["thread"])
                    t["posts"] = [
                        (
                            p
                            if f"board:{t['id']}:{int(p.get('created', 0))}" not in hide
                            else dict(
                                p,
                                text="(hidden while an administrator reviews a report)",
                                hidden=True,
                            )
                        )
                        for p in t["posts"]
                    ]
                    result = dict(result, thread=t)
                self.send_json(
                    200,
                    dict(
                        result,
                        posting=self._posting_view("board"),
                        report_reasons=settings_snapshot()["report_reasons"],
                    ),
                )
            return

        if path in MENU_PAGES and not (
            MENU_PAGES[path].get("local") and MENU_PAGES[path]["id"] in hidden_apps(self._viewer())
        ):
            body = menu_page(MENU_PAGES[path], self._viewer())
            self.send_response(200)
            self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            body = home_page(self._viewer())
            self.send_response(200)
            self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", len(body))
            # Its header buttons follow who is signed in: never a copy from before signing in or out.
            self.send_header("Cache-Control", "no-cache")
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
            if admin_page:  # only the admin reaches /admin: the home page may show them the admin's tiles
                self.send_header(
                    "Set-Cookie",
                    f"{ADMIN_SEEN_COOKIE}={admin_seen_value()}; Path=/; Max-Age={ADMIN_SEEN_HOURS * 3600}; "
                    "HttpOnly; SameSite=Strict" + ("; Secure" if self._https() else ""),
                )
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_empty(404)

    def _read_payload(self):
        """The request's JSON, or None. Larger than MAX_JSON is not read at all: the
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
        self._note_account()
        refresh_manifests()
        path = self.path.split("?")[0]
        if not path.startswith("/_irate/") and self._cross_site():
            return

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
        if not isinstance(payload, dict):  # None, or a JSON array or string
            if self._too_large:
                self.send_json(
                    413,
                    refused.say(
                        M(
                            "hub.request.large",
                            "Request too large. The hub reads at most {kb} KB.",
                            kb=MAX_JSON >> 10,
                        )
                    ),
                )
                return
            self.send_json(400, refused.say(BAD_REQUEST))
            return

        if path == "/api/account":
            self._account_post(payload)
            return

        if path == "/api/notebook":
            # Open (start) this login's own notebook; only from the hub's own pages, as /api/account.
            site = self.headers.get("Sec-Fetch-Site")
            if self.headers.get("X-Irate-Account") != "1" or (site and site not in ("same-origin", "none")):
                self.send_json(403, refused.say(CROSS_SITE))
                return
            me = accounts.session(self._session_token())
            if payload.get("action") == "close":
                if me:
                    NOTEBOOKS.close(me["name"])
                self.send_json(200, NOTEBOOKS.status(me))
            else:
                self.send_json(200, NOTEBOOKS.open(me))
            return

        if path == "/admin/accounts":
            # Accounts (accounts.py): the sign-up level and the HTTP stance; accept, disable,
            # enable, delete, the role; a new account or a reset, each with a one-time code.
            action = payload.get("action")
            before = accounts.admin_ready()
            if unclaimed() and (action == "admin" or (action == "make" and payload.get("role") == "admin")):
                # The first admin is the first use's: made on the set-up page (HTTPS, or HTTP by choice) or at the console.
                self.send_json(403, refused.say(SETUP_FIRST))
                return
            try:
                keep = not admin_login_on()
                if action == "admin-login":
                    # The box's own login: its reach (off, from the box itself, from the network too), the admin's choice;
                    # root's (the web server's login files), checked there too. "on" as before.
                    reach = payload.get("reach")
                    if reach is not None and reach not in ("off", "local", "network"):
                        self.send_json(400, refused.say(BAD_REQUEST))
                        return
                    req = (
                        {"action": "admin-login", "reach": reach}
                        if reach is not None
                        else {"action": "admin-login", "on": payload.get("on") is True}
                    )
                    self.send_json(202, {"id": control_request(req)})
                    return
                if action == "settings":
                    out = {
                        "settings": accounts.set_settings(payload.get("signup"), payload.get("http"), keep_admin=keep)
                    }
                elif action == "make":
                    out = {"code": accounts.make(payload.get("name"), payload.get("role", "user"))}
                elif action == "reset":
                    out = {"code": accounts.reset(payload.get("name"))}
                else:
                    accounts.change(payload.get("name"), action, keep_admin=keep)
                    out = {}
            except accounts.AccountError as exc:
                self.send_json(400, refusal(exc))
                return
            self._admin_gate_follow(before)
            self.send_json(200, dict(out, **accounts_view()))
            return

        if path == "/admin/setup":
            pw, name = payload.get("password"), payload.get("name")
            if not unclaimed():
                self.send_json(
                    403,
                    refused.say(
                        M("setup.done", "This box is already set up. Sign in to change its accounts or passwords.")
                    ),
                )
            elif not self._https() and payload.get("over_http") is not True:
                # The first password over plain HTTP only when the owner chooses it, having been told.
                self.send_json(
                    403,
                    dict(
                        refused.say(
                            M(
                                "setup.http",
                                "Over plain HTTP, others on the network can read the password. "
                                "Use HTTPS or the console, or select the option to set it over HTTP.",
                            )
                        ),
                        https=False,
                    ),
                )
            elif not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= 128) or "\n" in pw:
                self.send_json(400, refused.say(password_length()))
            elif name is None:
                # A script's first use (no name): the box's own login, as before.
                self.send_json(
                    202,
                    {"id": control_request({"action": "password", "password": pw, "setup": True})},
                )
            else:
                # The owner's first use: their admin account, signed in at once. The box's own login goes
                # off (no password made for scripts: script-login at the console makes one).
                try:
                    token, _me = accounts.claim_admin(name, pw, self._client_addr())
                except accounts.Wait as exc:
                    self.send_json(429, refusal(exc))
                    return
                except accounts.AccountError as exc:
                    self.send_json(400, refusal(exc))
                    return
                rid = control_request({"action": "claim"})
                control_request({"action": "admin-gate"})
                self.send_json(
                    202,
                    {"id": rid},
                    [self._session_cookie(token, accounts.SESSION_DAYS * 86400)],
                )
            return

        if path == "/admin/settings":
            if payload.get("setup_done") is True:
                why = setup_blocked()
                if why:
                    self.send_json(409, refused.say(why))
                    return
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

        if path == "/admin/picture":
            name = payload.get("which")
            if name not in PICTURES:
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            pic = PICTURES[name]
            if payload.get("remove") is True:
                pic["file"].unlink(missing_ok=True)
                self.send_json(200, {name: False})
                return
            try:
                data = base64.b64decode(payload.get("data") or "", validate=True)
            except (binascii.Error, TypeError, ValueError):
                data = b""
            if not data or len(data) > pic["max"] or not picture_kind(name, data):
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "hub.picture.favicon",
                            "Select a PNG or .ico file of at most {kb} KB.",
                            kb=pic["max"] >> 10,
                        )
                        if name == "favicon"
                        else M(
                            "hub.picture.art",
                            "Select a PNG, JPEG or WebP picture of at most {kb} KB.",
                            kb=pic["max"] >> 10,
                        )
                    ),
                )
                return
            tmp = pic["file"].with_name(pic["file"].name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, pic["file"])
            self.send_json(200, {name: True})
            return

        if path == "/admin/tailscale":
            mode, hours = payload.get("mode"), payload.get("hours")
            if mode not in ("off", "on", "timed") or (
                mode == "timed" and not (type(hours) is int and 1 <= hours <= TAILSCALE_MAX_HOURS)
            ):  # not bool
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "tailscale.hours",
                            "Select off, on, or on for 1 to {max} hours.",
                            max=TAILSCALE_MAX_HOURS,
                        )
                    ),
                )
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
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "hub.control.not-offered",
                            "The hub does not offer {op} for {unit}.",
                            op=str(op),
                            unit=str(unit),
                        )
                    ),
                )
                return
            self.send_json(
                202,
                {"id": control_request({"action": "service", "unit": unit, "op": op})},
            )
            return

        if path == "/admin/password":
            pw = payload.get("password")
            if not self._https():
                self.send_json(
                    403,
                    dict(
                        refused.say(
                            M(
                                "hub.password.https",
                                "An administrator password can change only over HTTPS or at the console over SSH.",
                            )
                        ),
                        https=False,
                    ),
                )
                return
            if not isinstance(pw, str) or not (MIN_PASSWORD <= len(pw) <= 128) or "\n" in pw:
                self.send_json(400, refused.say(password_length()))
                return
            self.send_json(202, {"id": control_request({"action": "password", "password": pw})})
            return

        if path == "/admin/moderation":
            self.send_json(*moderation_action(payload))
            return

        if path == "/admin/update" and payload.get("action") == "signing":
            # How far an update must be vouched for (root/signing.py): root checks and keeps it.
            level, signers = payload.get("level"), payload.get("signers", "")
            if level not in ("off", "github", "tags") or not isinstance(signers, str) or len(signers) > 20000:
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "update.signing.invalid",
                            "Invalid signing settings. Select a level, and enter the signers as allowed_signers lines.",
                        )
                    ),
                )
                return
            self.send_json(
                202,
                {"id": control_request({"action": "update-signing", "level": level, "signers": signers})},
            )
            return

        if path == "/admin/update":
            action = UPDATE_ACTIONS.get(payload.get("action"))
            if not action:
                self.send_json(400, refused.say(UNKNOWN_ACTION))
                return
            request = {"action": action}
            if action == "update-reach":
                if payload.get("endpoint") not in ("download", "install"):
                    self.send_json(400, refused.say(UNKNOWN_ACTION))
                    return
                request.update(endpoint=payload["endpoint"], candidate=payload.get("candidate"))
            if action in ("update-poll-target", "update-set-source"):
                request.update(repo=str(payload.get("repo") or "")[:300])
            if action == "update-set-source":
                request.update(
                    branch=str(payload.get("branch") or "")[:100], confirmed=payload.get("confirmed") is True
                )
            self.send_json(202, {"id": control_request(request)})
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
                    raise refused.Refused(UNKNOWN_ACTION)
            except ValueError as exc:
                self.send_json(400, refused.answer(exc))
                return
            self.send_json(200, {"channels": meshbridge.channels_view()})
            return

        if path == "/admin/tls":
            action = payload.get("action")
            if action == "policy":
                if (
                    unclaimed()
                    or payload.get("mode") not in ("hub", "console")
                    or (payload["mode"] == "console" and payload.get("confirmed") is not True)
                ):
                    self.send_json(
                        400,
                        refused.say(
                            M(
                                "policy.request.invalid",
                                "Claim the box and confirm the security policy choice first.",
                            )
                        ),
                    )
                    return
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "security-policy",
                                "mode": payload["mode"],
                                "confirmed": payload.get("confirmed") is True,
                            }
                        )
                    },
                )
                return
            if action == "settings":
                transport, admin_http = payload.get("transport"), payload.get("admin_http")
                if transport not in (
                    "http",
                    "both",
                    "discovery",
                    "https",
                ) or admin_http not in ("allow", "warning", "block"):
                    self.send_json(400, refused.say(BAD_REQUEST))
                    return
                if (transport in ("discovery", "https") or admin_http == "block") and self.headers.get(
                    "X-Forwarded-Proto"
                ) != "https":
                    self.send_json(
                        409,
                        refused.say(
                            M(
                                "tls.settings.http",
                                "Open /admin over HTTPS first. These settings cannot be enabled from an HTTP session.",
                            )
                        ),
                    )
                    return
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "tls-settings",
                                "transport": transport,
                                "admin_http": admin_http,
                            }
                        )
                    },
                )
                return
            if action in ("on", "off"):
                self.send_json(
                    202,
                    {"id": control_request({"action": "tls-switch", "on": action == "on"})},
                )
                return
            if action == "import":
                # The owner's own certificate and key, staged for the root helper (hub-only, 0600),
                # which checks and installs them and removes these copies.
                chain, key = payload.get("chain"), payload.get("key")
                if (
                    not isinstance(chain, str)
                    or not isinstance(key, str)
                    or len(chain) > 64 << 10
                    or len(key) > 16 << 10
                ):
                    self.send_json(
                        400,
                        refused.say(
                            M(
                                "tls.import.invalid",
                                "Paste the certificate chain and the key, each as PEM text.",
                            )
                        ),
                    )
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
                    self.send_json(
                        409,
                        refused.say(
                            M(
                                "tls.admin-only.http",
                                "Open /admin over HTTPS first (https://<this box>/admin/). Then make it HTTPS only. "
                                "Otherwise you can lose access to /admin.",
                            )
                        ),
                    )
                    return
                self.send_json(202, {"id": control_request({"action": "tls-admin-only", "on": on})})
                return
            if action not in ("make", "renew"):
                self.send_json(400, refused.say(UNKNOWN_ACTION))
                return
            self.send_json(
                202,
                {
                    "id": control_request(
                        {
                            "action": f"tls-{action}",
                            "again": payload.get("again") is True,
                        }
                    )
                },
            )
            return

        if path == "/admin/factory":
            # The Firmware Factory's queue (factory.py): queue targets, cancel or move one up, pause.
            action = payload.get("action")
            try:
                if action == "queue":
                    out = factory.queue(
                        str(payload.get("source", "")),
                        str(payload.get("ref", "")),
                        payload.get("targets"),
                    )
                    self.send_json(202, dict(out, snapshot=factory.snapshot()))
                    return
                if action in ("tools", "offline"):
                    out = factory.queue_tools(
                        str(payload.get("source", "")),
                        str(payload.get("ref", "")),
                        str(payload.get("family", "")),
                        offline=action == "offline",
                        env=str(payload.get("env") or "") or None,
                    )
                    self.send_json(202, dict(out, snapshot=factory.snapshot()))
                    return
                if action == "again":
                    factory.again(str(payload.get("run", "")))
                elif action == "publish":
                    factory.publish(str(payload.get("run", "")))
                elif action == "unpublish":
                    factory.unpublish(str(payload.get("version", "")), str(payload.get("env", "")))
                elif action in ("cancel", "up"):
                    (factory.cancel if action == "cancel" else factory.move_up)(str(payload.get("id", "")))
                elif action in ("pause", "resume"):
                    factory.pause(action == "pause")
                else:
                    raise refused.Refused(UNKNOWN_ACTION)
            except ValueError as exc:
                self.send_json(400, refused.answer(exc))
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
                        raise refused.Refused(
                            M(
                                "ci.repo.invalid",
                                "Invalid repository name. Select a private repository.",
                            )
                        )
                    branch = payload.get("branch")
                    out = ci.queue_build(
                        confine.under(gitrepos.ROOT, "private", f"{name}.git"),
                        str(branch) if branch else None,
                    )
                    self.send_json(202, dict(out, queued=True, snapshot=ci.snapshot()))
                elif action in ("keep", "unkeep", "delete"):
                    ci.queue_run_change(str(payload.get("run", "")), action)
                    self.send_json(202, {"queued": True})
                elif action == "settings":
                    keep = payload.get("keep_runs")
                    if type(keep) is not int or not 1 <= keep <= 50:
                        raise refused.Refused(
                            M(
                                "ci.keep.range",
                                "Enter a number of builds to keep from 1 to 50.",
                            )
                        )
                    ci.SETTINGS.parent.mkdir(parents=True, exist_ok=True)
                    ci.SETTINGS.write_text(json.dumps({"keep_runs": keep}))
                    self.send_json(200, ci.snapshot())
                else:
                    raise refused.Refused(UNKNOWN_ACTION)
            except ValueError as exc:
                self.send_json(400, refused.answer(exc))
            return

        if path == "/admin/kits":
            from irate_box.library import toolkits

            try:
                rid = toolkits.action(payload)
            except librarian.LibrarianError as exc:
                self.send_json(*_busy_or(400, exc))
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
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "usb-import",
                                "device": device,
                                "file": payload["file"][:512],
                            }
                        )
                    },
                )
            elif action == "export" and USB_DEVICE_RE.match(device) and isinstance(payload.get("book"), str):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "usb-export",
                                "device": device,
                                "book": payload["book"][:64],
                            }
                        )
                    },
                )
            elif (
                action in ("kit-import", "kit-export")
                and USB_DEVICE_RE.match(device)
                and isinstance(payload.get("kit"), str)
            ):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": f"usb-{action}",
                                "device": device,
                                "kit": payload["kit"][:32],
                                # The owner's approval to import a release the clock cannot date; root checks
                                # the security policy before taking it.
                                **({"freshness": "unknown-time"} if payload.get("freshness") == "unknown-time" else {}),
                            }
                        )
                    },
                )
            elif (
                action == "export-many"
                and USB_DEVICE_RE.match(device)
                and _str_list(payload.get("books"))
                and _str_list(payload.get("kits"))
                and _str_list(payload.get("repos", []))
                and payload.get("state", "none") in ("none", "settings", "data")
            ):
                # A content export without the hub program: the layout the other box's USB imports read.
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "usb-export-many",
                                "device": device,
                                "books": payload["books"][:200],
                                "kits": payload["kits"][:50],
                                "repos": payload.get("repos", [])[:200],
                                "state": payload.get("state", "none"),
                            }
                        )
                    },
                )
            elif action == "image" and USB_DEVICE_RE.match(device):
                self.send_json(
                    202,
                    {"id": control_request({"action": "backup-image", "device": device})},
                )
            else:
                self.send_json(400, refused.say(BAD_REQUEST))
            return

        if path == "/admin/kit":
            books, budget = payload.get("books", False), payload.get("budget_mb")
            if (
                payload.get("action") == "make"
                and (type(books) is bool or _str_list(books))
                and _str_list(payload.get("kits", []))
                and _str_list(payload.get("repos", []))
                and payload.get("state", "none") in ("none", "settings", "data")
                and (budget is None or (type(budget) is int and budget > 0))
            ):
                # A new box's kit with what the owner chose; root checks each again (kit_choices). Kept, so Refresh and
                # the schedule (kitauto) make the same kit again.
                req = {
                    "action": ("offline-kit" if payload.get("hub", True) is not False else "content-export"),
                    "books": books,
                    "kits": payload.get("kits", []),
                    "repos": payload.get("repos", []),
                    "state": payload.get("state", "none"),
                    "budget_mb": budget,
                }
                kitauto.remember(STATE_DIR, req)
                self.send_json(202, {"id": control_request(req)})
            elif payload.get("action") == "refresh":
                req = kitauto.load(STATE_DIR)["request"]
                if not req:
                    self.send_json(
                        400,
                        refused.say(
                            M(
                                "kit.refresh.none",
                                "No export was made yet. Create an export first.",
                            )
                        ),
                    )
                else:
                    self.send_json(202, {"id": control_request(req)})
            elif payload.get("action") == "schedule" and payload.get("when") in kitauto.WHEN:
                kitauto.set_when(STATE_DIR, payload["when"])
                self.send_json(200, kit_snapshot())
            else:
                self.send_json(400, refused.say(BAD_REQUEST))
            return

        if path == "/admin/tiles":
            # The apps row's order: a list of its tiles' ids, the rest after them as before.
            data, ids = payload.get("state"), {i for i, _, _ in app_tiles()}
            size = data.get("size", {}) if isinstance(data, dict) else None
            icon = data.get("icon", {}) if isinstance(data, dict) else None
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("order", []), list)
                or not set(data.get("order", [])) <= ids
                or not isinstance(size, dict)
                or not set(size) <= ids
                or not set(size.values()) <= set(SIZES)
                or not isinstance(icon, dict)
                or not set(icon) <= ids
                or not all(valid_icon(v) for v in icon.values())
            ):
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "tiles.invalid",
                            "Invalid tile arrangement. An icon is an emoji, or up to four letters and digits.",
                        )
                    ),
                )
                return
            tmp = TILES_FILE.parent / (TILES_FILE.name + ".tmp")
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(
                    {
                        "order": list(dict.fromkeys(data.get("order", []))),
                        "size": size,
                        "icon": icon,
                    }
                )
            )
            os.replace(tmp, TILES_FILE)
            self.send_json(200, tiles_snapshot())
            return

        if path == "/admin/status-tiles":
            # The box row's arrangement: its tiles only, each part a list of their ids.
            data, ids = payload.get("state"), {i for i, _ in box_tiles()}
            size = data.get("size", {}) if isinstance(data, dict) else None
            if (
                not isinstance(data, dict)
                or not all(
                    isinstance(data.get(k, []), list) and set(data.get(k, [])) <= ids
                    for k in ("order", "hidden", "double")
                )
                or not isinstance(size, dict)
                or not set(size) <= ids
                or not set(size.values()) <= set(SIZES)
            ):
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            tmp = STATUS_TILES_FILE.parent / (STATUS_TILES_FILE.name + ".tmp")
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            out = {k: list(dict.fromkeys(data.get(k, []))) for k in ("order", "hidden")}
            out["size"] = _sizes(data)  # an older page's "double" list, read as wide
            tmp.write_text(json.dumps(out))
            os.replace(tmp, STATUS_TILES_FILE)
            self.send_json(200, status_tiles_snapshot())
            return

        if path == "/admin/folders":
            # The folders' arrangement: the whole of folders.json, checked, at once.
            data = valid_folders(payload.get("state"))
            if data is None:
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            save_folders(data)
            self.send_json(200, folders_snapshot())
            return

        if path == "/admin/visibility" and "locked" in payload:
            # What its tile does for one who sees it but may not open it.
            app, how = str(payload.get("app", "")), payload.get("locked")
            if app not in _switched() or how not in LOCKED_AS:
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            data = locked_as()
            if how == "signin":
                data.pop(app, None)
            else:
                data[app] = how
            save_locked_as(data)
            self.send_json(200, {"locked_as": data})
            return

        if path == "/admin/visibility":
            # Who sees an app's tile: the hub's own, at once; no root, no web server change.
            app, v = str(payload.get("app", "")), payload.get("visible")
            if (app not in _switched() and app not in PAGE_APPS and app not in seen_only()) or v not in VISIBLE:
                self.send_json(400, refused.say(BAD_REQUEST))
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
            if (
                (app not in access.ROUTED and not any(m.get("local") and m["id"] == app for m in MANIFESTS))
                or mode not in access.MODES
                or (mode == "users" and not access.users_allowed(app))
            ):
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "access.invalid",
                            "This app does not offer this access. Reload the page and try again.",
                        )
                    ),
                )
            else:
                self.send_json(
                    202,
                    {"id": control_request({"action": "access", "app": app, "mode": mode})},
                )
            return

        if path == "/admin/local-addons":
            code, body = local_addons_action(payload)
            self.send_json(code, body)
            return

        if path == "/admin/addons":
            aid = str(payload.get("addon", ""))
            if (
                not ADDON_ID_RE.match(aid)
                or aid not in manifests.addons(MANIFESTS)
                or type(payload.get("on")) is not bool
            ):
                self.send_json(400, refused.say(BAD_REQUEST))
            else:
                self.send_json(
                    202,
                    {"id": control_request({"action": "addon", "addon": aid, "on": payload["on"]})},
                )
            return

        if path == "/admin/health":
            if payload.get("action") == "scan":
                self.send_json(202, {"id": control_request({"action": "health-scan"})})
            elif payload.get("action") == "fix" and HEALTH_CHOICE_RE.match(str(payload.get("choice", ""))):
                self.send_json(
                    202,
                    {"id": control_request({"action": "health-fix", "choice": payload["choice"]})},
                )
            elif payload.get("action") in ("accept", "unaccept"):
                # The owner's own: a review finding looked at and left as it is (or taken back). The hub's file; no root.
                try:
                    data = health_accept(
                        payload.get("key"),
                        payload.get("title"),
                        payload["action"] == "accept",
                    )
                except ValueError as exc:
                    self.send_json(400, refused.answer(exc))
                    return
                self.send_json(
                    200,
                    refused.done(
                        ACCEPTED if payload["action"] == "accept" else REOPENED,
                        accepted=data,
                    ),
                )
            else:
                self.send_json(400, refused.say(UNKNOWN_ACTION))
            return

        if path == "/admin/network":
            act = payload.get("action")
            if act == "scan" and (payload.get("iface") in (None, "") or IFACE_NAME_RE.match(str(payload["iface"]))):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "net-scan",
                                "iface": payload.get("iface") or None,
                                "rescan": payload.get("rescan") is True,
                            }
                        )
                    },
                )
            elif act == "settings":
                try:
                    settings = uplink.validate(payload.get("settings"))
                except (ValueError, TypeError) as exc:
                    self.send_json(400, refused.answer(exc))
                    return
                settings.pop("hold_until", None)
                self.send_json(
                    202,
                    {"id": control_request({"action": "uplink-set", "settings": settings})},
                )
            elif act == "do" and payload.get("step") in uplink.STEPS:
                self.send_json(
                    202,
                    {"id": control_request({"action": "uplink-do", "step": payload["step"]})},
                )
            elif act == "hold" and type(payload.get("minutes")) is int and 0 <= payload["minutes"] <= 1440:
                self.send_json(
                    202,
                    {"id": control_request({"action": "uplink-hold", "minutes": payload["minutes"]})},
                )
            elif act == "profile" and type(payload.get("on")) is bool:
                self.send_json(
                    202,
                    {"id": control_request({"action": "uplink-profile", "on": payload["on"]})},
                )
            elif (
                act == "handover"
                and isinstance(payload.get("name"), str)
                and re.fullmatch(r"[A-Za-z0-9_.@+-]{1,128}\.yaml", payload["name"])
                and type(payload.get("undo", False)) is bool
            ):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "nm-handover",
                                "name": payload["name"],
                                "undo": payload.get("undo", False),
                            }
                        )
                    },
                )
            elif act == "defaults" and isinstance(payload.get("changes"), dict):
                from irate_box.hub import nmconf

                iface = payload.get("iface")
                try:
                    changes = nmconf.validate(payload["changes"], iface)
                except ValueError as exc:
                    self.send_json(400, refused.answer(exc))
                    return
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "nm-defaults",
                                "iface": iface,
                                "changes": {k: ("default" if v is None else v) for k, v in changes.items()},
                            }
                        )
                    },
                )
            elif (
                act == "radiotest"
                and payload.get("exp") in ("follows-roam", "radio-reset", "driver-reset")
                and IFACE_NAME_RE.match(str(payload.get("iface") or ""))
            ):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "radio-test",
                                "iface": payload["iface"],
                                "exp": payload["exp"],
                            }
                        )
                    },
                )
            elif act == "wifisync" and payload.get("do") in ("off", "on", "undo"):
                self.send_json(
                    202,
                    {"id": control_request({"action": "wifisync", "do": payload["do"]})},
                )
            elif (
                act == "otherap"
                and isinstance(payload.get("uuid"), str)
                and payload.get("do") in ("remove", "leave", "undo", "forget")
                and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    payload["uuid"],
                )
            ):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "other-ap",
                                "uuid": payload["uuid"],
                                "do": payload["do"],
                            }
                        )
                    },
                )
            elif (
                act == "connection"
                and isinstance(payload.get("uuid"), str)
                and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                    payload["uuid"],
                )
                and (
                    payload.get("undo") is True
                    or (
                        isinstance(payload.get("changes"), dict)
                        and payload["changes"]
                        and all(isinstance(k, str) and isinstance(v, str) for k, v in payload["changes"].items())
                    )
                )
            ):
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "nm-connection",
                                "uuid": payload["uuid"],
                                "changes": payload.get("changes") or {},
                                "undo": payload.get("undo") is True,
                            }
                        )
                    },
                )
            elif (
                act == "join"
                and isinstance(payload.get("ssid"), str)
                and payload.get("security") in ("wpa-psk", "sae", "open")
                and isinstance(payload.get("psk", ""), str)
                and type(payload.get("hidden", False)) is bool
                and type(payload.get("now", False)) is bool
            ):
                # A network for the box to join: checked again by root (wifijoin.validate); the password
                # goes only into the request file (0600, the hub's) and NetworkManager's keyfile (root's).
                self.send_json(
                    202,
                    {
                        "id": control_request(
                            {
                                "action": "wifi-join",
                                "ssid": payload["ssid"],
                                "security": payload["security"],
                                "psk": payload.get("psk", ""),
                                "hidden": payload.get("hidden", False),
                                "now": payload.get("now", False),
                            }
                        )
                    },
                )
            elif act == "forget" and isinstance(payload.get("uuid"), str):
                self.send_json(
                    202,
                    {"id": control_request({"action": "wifi-forget", "uuid": payload["uuid"]})},
                )
            else:
                self.send_json(400, refused.say(BAD_REQUEST))
            return

        if path == "/admin/packages":
            # Root checks each of these again (pkgwatch.py); here only their shapes.
            act, pkg = payload.get("action"), payload.get("package")
            if (
                not isinstance(pkg, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", pkg)
                and not (act == "check" and pkg == "")
            ):
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            if act == "check":
                req = {"action": "pkg-check", **({"package": pkg} if pkg else {})}
            elif act == "fetch" and pkg:
                req = {"action": "pkg-fetch", "package": pkg}
            elif (
                act == "install"
                and isinstance(payload.get("version"), str)
                and re.fullmatch(r"[A-Za-z0-9.+~:-]{1,100}", payload["version"])
            ):
                req = {
                    "action": "pkg-install",
                    "package": pkg,
                    "version": payload["version"],
                }
            elif act == "rollback":
                req = {"action": "pkg-rollback", "package": pkg}
            elif (
                act == "settings"
                and payload.get("channel") in ("beta", "alpha", "daily")
                and payload.get("mode") in ("flag", "watch", "auto", "aged")
                and payload.get("days") in (1, 3, 7, 14, 30)
                and not isinstance(payload.get("days"), bool)
                and payload.get("every", 24) in (0, 6, 24, 168)
                and not isinstance(payload.get("every"), bool)
            ):
                req = {
                    "action": "pkg-settings",
                    "package": pkg,
                    "channel": payload["channel"],
                    "mode": payload["mode"],
                    "days": payload["days"],
                    "every": payload.get("every", 24),
                }
            else:
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            self.send_json(202, {"id": control_request(req)})
            return

        if path == "/admin/hotspot" and payload.get("action") == "share":
            # The owner's level (root/share.py), on Network → The hotspot.
            level = payload.get("level")
            if level not in SHARE_LEVELS:
                self.send_json(400, refused.say(BAD_REQUEST))
                return
            self.send_json(202, {"id": control_request({"action": "share-set", "level": level})})
            return

        if path == "/admin/hotspot" and payload.get("action") in (
            "on",
            "off",
            "try",
            "confirm",
        ):
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
                self.send_json(400, refused.answer(exc))
                return
            self.send_json(
                200,
                refused.done(
                    M(
                        "hotspot.saved",
                        "Saved: {mode}. The setting applies the next time you enable the hotspot (Network).",
                        mode=hotspot.LABEL_SAID[saved["mode"]],
                    ),
                    settings=saved,
                ),
            )
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
                    msg = (
                        secimports.save(payload.get("report"))
                        if payload["action"] == "import"
                        else secimports.remove(str(payload.get("kind", "")))
                    )
                except ValueError as exc:
                    self.send_json(400, refused.answer(exc))
                    return
                self.send_json(
                    202,
                    refused.done(msg, id=control_request({"action": "security-audit"})),
                )
            elif payload.get("action") in ("accept", "unaccept"):
                # The owner's own: looked at, and accepted as it is (or taken back). The hub's file; no root.
                try:
                    data = security_accept(
                        payload.get("key"),
                        payload.get("title"),
                        payload["action"] == "accept",
                    )
                except ValueError as exc:
                    self.send_json(400, refused.answer(exc))
                    return
                self.send_json(
                    200,
                    refused.done(
                        ACCEPTED if payload["action"] == "accept" else REOPENED,
                        accepted=data,
                    ),
                )
            elif payload.get("action") == "fix" and SECURITY_CHOICE_RE.match(str(payload.get("choice", ""))):
                self.send_json(
                    202,
                    {"id": control_request({"action": "security-fix", "choice": payload["choice"]})},
                )
            else:
                self.send_json(400, refused.say(UNKNOWN_ACTION))
            return

        if path == "/messages":
            self._post_message(payload)
            return

        if path == "/api/guest-net":
            # A guest taps through the sheet (or, at users-web, is signed in): root lets the device out.
            level, addr = share_level(), self._client_addr()
            if level not in SHARE_LET_OUT:
                self.send_json(
                    409,
                    refused.say(
                        M(
                            "share.not-per-device",
                            "The administrator does not share the internet connection one device at a time now.",
                        )
                    ),
                )
                return
            if not on_hotspot(addr):
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "share.not-hotspot",
                            "Only a device on the hotspot of the box can use the shared connection.",
                        )
                    ),
                )
                return
            if level == "users-web" and not self._signed_in():
                self.send_json(
                    403,
                    refused.say(
                        M(
                            "share.users-only",
                            "Sign in first. The administrator shares this connection with users only.",
                        )
                    ),
                )
                return
            if payload.get("agree") is not True:
                self.send_json(
                    400,
                    refused.say(
                        M(
                            "share.agree",
                            "Read what sharing means, and agree to it first.",
                        )
                    ),
                )
                return
            with _let_out_lock:
                if _let_out.get(addr, 0) > time.time() + SHARE_HOURS * 3600 - 60:
                    self.send_json(200, {"out": True})  # asked a moment ago
                    return
                _let_out[addr] = time.time() + SHARE_HOURS * 3600
            self.send_json(
                202,
                {
                    "id": control_request({"action": "share-allow", "ip": addr}),
                    "out": True,
                },
            )
            return

        if path == "/api/report":
            fwd = self.headers.get("X-Forwarded-For", "")
            addr = fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]
            code, message = report(
                str(payload.get("app", "")),
                payload.get("ref"),
                payload.get("reason"),
                addr,
            )
            # report.js reads message (the thanks) or error as text: the English, with the message itself as msg.
            self.send_json(
                code,
                {
                    "message" if code == 200 else "error": render(message),
                    "msg": message,
                },
            )
            return

        if path == "/board/threads":
            poster = self._poster("board", str(payload.get("name", "")))
            if poster is None:
                return
            try:
                result = BOARD.create_thread(
                    poster[0],
                    payload.get("title"),
                    payload.get("text"),
                    self._hue(payload, poster),
                    poster[1],
                    seen_by_of(poster[1], "board"),
                )
            except ValueError as exc:
                self.send_json(400, refused.answer(exc))
                return
            self.send_json(201, result)
            return

        tid = thread_id(path)
        if tid is not None:
            poster = self._poster("board", str(payload.get("name", "")))
            if poster is None:
                return
            try:
                result = BOARD.reply(
                    tid,
                    poster[0],
                    payload.get("text"),
                    self._hue(payload, poster),
                    poster[1],
                    seen_by_of(poster[1], "board"),
                )
            except ValueError as exc:
                self.send_json(400, refused.answer(exc))
                return
            if result is None:
                self.send_json(404, refused.say(NO_THREAD))
            else:
                self.send_json(201, result)
            return

        self.send_empty(404)

    def _cross_site(self):
        """A change a page on another site made (for /admin,
        see _forged): when the browser says where the request came from (Sec-Fetch-Site, or
        Origin), it must be this host; a sibling port (same-site) does not count. A client that
        sends neither (curl, the box's own scripts) carries no cached login of a browser's.
        """
        site = self.headers.get("Sec-Fetch-Site")
        origin = self.headers.get("Origin")
        if (site and site not in ("same-origin", "none")) or (
            origin and urlparse(origin).netloc != self.headers.get("Host", "")
        ):
            self._discard_body()
            self.send_json(403, refused.say(CROSS_SITE))
            return True
        return False

    def do_PUT(self):
        if not self._cross_site() and not store.handle(self, "PUT", self.path.split("?")[0], STORE, DROP):
            self.send_empty(404)

    def do_DELETE(self):
        if not self._cross_site() and not store.handle(self, "DELETE", self.path.split("?")[0], STORE, DROP):
            self.send_empty(404)

    def do_PATCH(self):
        if not self._cross_site() and not store.handle(self, "PATCH", self.path.split("?")[0], STORE, DROP):
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
        """The web flashers (flasher.py): /flasher/ the light one's page, in place; the Meshtastic one
        as a download, with this hub's address in it; and the small API both read. Their static files
        come from the web server."""
        if path == "/flasher":
            self.send_response(301)
            self.send_header("Location", "/flasher/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/flasher/":
            try:
                body = (STATIC / "esp-flasher.html").read_bytes()
            except OSError:
                self.send_empty(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        if path == "/flasher/flasher.html":
            body = flasher.page(self.headers.get("Host", ""))
            if body is None:
                self.send_json(
                    404,
                    refused.say(
                        M(
                            "flasher.none",
                            "The web flasher is not installed on this hub.",
                        )
                    ),
                )
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
        self.send_json(404, refused.say(M("hub.not-found", "Not found.")))

    def _poster(self, kind, name):
        """Who is posting to the shoutbox or forum (kind "shout" or "board"), as the admin allows:
        (name, account), or None once the refusal is sent. A logged-in user posts under their
        account's name; a guest (where guests may) under any name but an account's."""
        shout = kind == "shout"
        who = settings_snapshot()[f"{kind}_who"]
        if who == "off":
            self.send_json(
                403,
                refused.say(
                    M("shoutbox.closed", "The shoutbox is closed.")
                    if shout
                    else M("board.closed", "The forum is closed.")
                ),
            )
            return None
        me = accounts.session(self._session_token())
        if me:
            return me["name"], me["name"]
        if who == "users":
            self.send_json(
                403,
                dict(
                    refused.say(
                        M(
                            "shoutbox.sign-in",
                            "Sign in to post on the shoutbox (/account.html).",
                        )
                        if shout
                        else M(
                            "board.sign-in",
                            "Sign in to post on the forum (/account.html).",
                        )
                    ),
                    login=True,
                ),
            )
            return None
        if accounts.exists(name):
            self.send_json(
                403,
                refused.say(
                    M(
                        "posting.name.account",
                        "{name} is the name of an account. Sign in to post with that name.",
                        name=name.strip(),
                    )
                ),
            )
            return None
        return name, None

    def _posting_view(self, kind):
        """What a page needs to show the shoutbox or forum's posting as it is: who may post,
        whether users' names are marked, and who this is."""
        st = settings_snapshot()
        me = accounts.session(self._session_token())
        return {
            "who": st[f"{kind}_who"],
            "marks": st[f"{kind}_marks"],
            "me": me["name"] if me else None,
        }

    def _post_message(self, payload):
        text = str(payload.get("text", "")).strip()[:200]
        poster = self._poster("shout", str(payload.get("name", "")).strip()[:32])
        if poster is None:
            return
        name, account = poster
        if not name or not text:
            self.send_json(400, refused.say(M("shoutbox.empty", "Enter a name and a message.")))
            return

        # Optional author colour, stored as a hue only (0-359) -- the client's palette
        # picks saturation and lightness. Absent means "derive it from the name", so a
        # guest who renames re-colours instead of carrying a stale hue around.
        hue = self._hue(payload, poster)
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
            seen = seen_by_of(account, "shoutbox")
            if seen:
                entry["seen_by"] = seen
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
    # The mesh heard through the box's MQTT broker: decoded with the owner's channel keys.
    MESH.start()
    # The services' uptime: every unit's state every five minutes, for /admin's grid.
    svchistory.Sampler(lambda: [s["unit"] for s in SERVICES if "unit" in s] + [MESHTASTICD]).start()
    memory_sampler = memhistory.Sampler()
    memory_sampler.start()
    notebooks.Reaper(NOTEBOOKS).start()
    # The pages the web server serves itself (no unit to sample): their use, from nginx's log of page loads.
    pagehistory.Sampler(lambda: [s["path"] for s in SERVICES if "root" in s and s.get("path")]).start()
    # One thread per request: a 50 MB paste into the blob store must not freeze
    # everyone else's shoutbox poll. State is guarded by `lock` and the store's own.
    server = HubServer((BIND, PORT), Handler)
    print(f"Hub running at http://{BIND}:{PORT}")
    print(f"Uptime clock at {hubclock.format_age(CLOCK.ticks())} cumulative")
    # Stopped (an update, a restart, a shutdown): out through the finally below, so the clock and the
    # mesh nodes are saved; with the card ladder they are otherwise saved only every few minutes.
    import signal

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    finally:
        memory_sampler.stopping.set()
        try:
            MESH.heard.prune_and_save(force=True)
        except Exception as exc:  # noqa: BLE001 - the clock is saved whatever
            print(f"mesh nodes not saved at stop: {exc}")
        CLOCK.stop()
