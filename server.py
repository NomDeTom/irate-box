#!/usr/bin/env python3
"""Irate-Box hub server: shoutbox, board, blob store, status, captive-portal target.

Serves static/ itself so a bare `python3 server.py` works; behind the web server
(nginx, or Caddy) the assets come off disk and only `/` and the API reach this process."""

import io
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import tarfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import html
import board
import ci
import firmware
import flasher
import gitrepos
import hubclock
import librarian
import manifests
import store
import uplink
import zimcheck

STATIC = Path(__file__).parent / "static"
# Mutable state lives outside the code directory so packaging can point it at
# /var/lib/hub and `apt purge` cannot eat anyone's messages.
STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", Path(__file__).parent))
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
MANIFESTS = manifests.load()


def _service_entry(status):
    entry = {"path": status.get("path"), "name": status["name"]}
    if "root_env" in status:
        entry["root"] = status["root_env"]
    for key in ("port", "unit", "note"):
        if key in status:
            entry[key] = status[key]
    return entry


SERVICES = [_service_entry(m["status"]) for m in MANIFESTS if "status" in m] + [
    {"path": None, "name": TAILSCALE_NAME, "unit": "tailscaled.service",
     "active": True, "note": "switched on and off from /admin"},
    {"path": None, "name": f"Web server ({WEB_SERVER_NAME})", "unit": f"{WEB_SERVER}.service", "proxy": True},
    {"path": None, "name": "Hub server", "unit": "irate-box.service", "self": True},
]


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
    "system": """      <div class="service-card system-card" id="system-card">
        <span class="icon">💽</span>
        <span class="meter" id="mem-meter" hidden><span class="meter-label">Memory <b id="mem-text"></b></span><span class="bar"><span id="mem-bar"></span></span></span>
        <span class="meter" id="disk-meter" hidden><span class="meter-label">Disk <b id="disk-text"></b></span><span class="bar"><span id="disk-bar"></span></span></span>
      </div>""",
}


def render_tiles(row="apps"):
    """One row of the home page's tiles, from the manifests' "tile" parts. Rendered here
    rather than in the browser, so the page arrives whole."""
    out = []
    for m in MANIFESTS:
        tile = m.get("tile")
        if not tile or tile.get("row", "apps") != row:
            continue
        if "widget" in tile:
            out.append(WIDGET_HTML[tile["widget"]])
            continue
        attrs = [f'class="service-card"']
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
                   f'      </a>')
    return "\n".join(out)


TILES_MARK = "<!-- apps.d tiles -->"
BOX_MARK = "<!-- apps.d box tiles -->"
_home_page = {"mtime": None, "body": b""}


def home_page():
    """index.html with the tiles in place, re-read when the file changes."""
    path = STATIC / "index.html"
    mtime = path.stat().st_mtime
    if _home_page["mtime"] != mtime:
        text = path.read_text(encoding="utf-8")
        text = text.replace(TILES_MARK, render_tiles("apps")).replace(BOX_MARK, render_tiles("box"))
        _home_page["body"] = text.encode()
        _home_page["mtime"] = mtime
    return _home_page["body"]


# The list pages (a manifest with a "menu"), at their tile's href. Rendered on each request:
# discovered entries come from the installed apps, which an update changes.
MENU_PAGES = {m["tile"]["href"]: m for m in manifests.menus(MANIFESTS).values()}
MENU_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} · Hub</title>
  <link rel="stylesheet" href="style.css">
  <script>try{{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}}catch(e){{}}</script>
</head>
<body>
  <!-- {about}
       Rendered by server.py from the manifests in apps.d/ ("menu", and every manifest's
       "entries" aimed at it). Each entry opens under the hub bar in a new tab, and greys
       out when data-service is down. -->
  <header class="sub-header">
    <nav class="head-nav"><a class="head-btn" href="/" title="Back to the hub" aria-label="Back to the hub">🏠</a><a class="head-btn" href="/help.html" title="Quick help" aria-label="Quick help">🛟</a></nav>
    <h1>{title}</h1>
    <p class="subtitle">{subtitle}</p>
    <div class="theme-picker" role="group" aria-label="Theme">
      <button type="button" data-theme-choice="light" title="Light">☀️</button>
      <button type="button" data-theme-choice="dark" title="Dark">🌙</button>
      <button type="button" data-theme-choice="auto" title="Follow system">Auto</button>
    </div>
  </header>

  <section class="item-section">
    <ul class="item-list">
{items}
    </ul>
  </section>

  <script src="hub.js"></script>
</body>
</html>
"""


def menu_page(m):
    items = []
    for _, e, service in manifests.entries_for(m["id"], MANIFESTS):
        attrs = f'href="{html.escape(e["href"])}"'
        if service:
            attrs += f' data-service="{html.escape(service)}"'
        if e.get("new_tab", True):
            attrs += ' target="_blank"'
        desc = f'<span class="desc">{html.escape(e["desc"])}</span>' if e.get("desc") else ""
        items.append(f'      <li><a {attrs}><span class="name">{html.escape(e["name"])}</span>{desc}</a></li>')
    menu = m["menu"]
    return MENU_TEMPLATE.format(title=html.escape(menu["title"]), subtitle=html.escape(menu["subtitle"]),
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
    # The ttyd card is shown like any other service -- greyed while its unit is off --
    # unless the operator would rather guests were not shown the escape hatch at all.
    "show_term_card": True,
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
}
_settings_lock = threading.Lock()


def valid_setting(key, value):
    want = type(DEFAULT_SETTINGS[key])
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
        _status_cache["units"] = unit_states([s["unit"] for s in SERVICES if "unit" in s])
        _status_cache["why"] = {}
        _status_cache["at"] = now
    units = _status_cache["units"]
    out = []
    for svc in SERVICES:
        installed, active, _ = units.get(svc["unit"], (True, False, False)) if "unit" in svc else (True, False, False)
        if "root" in svc:
            root = os.environ.get(svc["root"])
            installed = root is None or Path(root).is_dir()
            running = installed and proxied
        elif svc.get("self"):
            running = True  # it is answering this request
        elif svc.get("proxy"):
            running = proxied
        elif svc.get("active"):
            running = installed and active
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


def firmware_action(payload):
    """(status, body) for POST /admin/firmware: the settings, or a run (firmware.py)."""
    action = payload.get("action")
    try:
        if action == "settings":
            firmware.set_settings(**{k: payload[k] for k in ("enabled", "boards", "keep_alpha", "keep_beta", "cache")
                                     if k in payload})
        elif action in ("check", "update"):
            def run():
                with librarian.Lock():
                    return {"firmware": firmware.sync(check_only=(action == "check"), log=lambda *_: None)}
            if not library_start(f"firmware-{action}", run):
                return 409, {"error": "the librarian is already running"}
        else:
            return 400, {"error": "action must be settings, check or update"}
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
VERSION_FILE = Path(__file__).parent / "VERSION"
_ALL_OPS = ["start", "stop", "restart", "enable", "disable"]
CONTROL_OPS = {unit: _ALL_OPS for unit in manifests.controllable_units(MANIFESTS)}
CONTROL_OPS.update({f"{WEB_SERVER}.service": ["restart"], "irate-box.service": ["restart"]})
MIN_PASSWORD = 8
# First use (hub_control.py, the web server's config): while this root-owned file exists no
# admin password has been chosen, the web server lets /admin through with no login, and the
# hub serves only the set-the-password page there. It sits beside that server's config.
UNCLAIMED_FILE = Path(os.environ.get("HUB_UNCLAIMED_FILE", f"/etc/{WEB_SERVER}/irate-box-unclaimed"))
SETUP_PATHS = ("/admin", "/admin/", "/admin/setup")


def unclaimed():
    return UNCLAIMED_FILE.exists()


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


def hub_version():
    try:
        return VERSION_FILE.read_text().strip()
    except OSError:
        return "a development checkout"


def control_request(req):
    """Queue a request for hub_control.py; returns its id, which its answer will carry."""
    CONTROL_REQUESTS.mkdir(parents=True, exist_ok=True)
    rid = secrets.token_hex(8)
    tmp = CONTROL_DIR / f".{rid}.tmp"  # written beside, renamed in: never read half-written
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
            "results": control_results(),
            "pending": len(list(CONTROL_REQUESTS.glob("*.json"))) if CONTROL_REQUESTS.exists() else 0}


UPDATE_STATE = CONTROL_DIR / "update.json"
UPDATE_LOG = CONTROL_DIR / "update.log"
UPDATE_PROGRESS = CONTROL_DIR / "update-progress.json"
DOCTOR_STATE = CONTROL_DIR / "doctor.json"
UPDATE_ACTIONS = {"check": "update-check", "fetch": "update-fetch", "install": "update-install",
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
    return {"version": hub_version(), "state": state, "log": log, "pending": pending,
            "progress": update_progress(), "doctor": doctor, "results": control_results(5)}


SECURITY_STATE = CONTROL_DIR / "security.json"
AUDIT_STATE = CONTROL_DIR / "security-audit.json"
SECURITY_LOG = CONTROL_DIR / "security-updates.log"
IFACE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
SECURITY_CHOICE_RE = re.compile(r"^[a-z-]+(:[A-Za-z0-9@._-]+)?$")


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
    return {"hub": hub, "scan": scan, "audit": audit, "log": log, "pending": _pending_actions("security-"),
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
                         "sudo python3 /opt/irate-box/health.py"]}


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
    return {"inventory": load(NETINV_STATE), "uplink": status,
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
    return {"now": now, "messages": list(reversed(msgs)), "board": BOARD.all_threads(), "drops": DROP.list()}


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
    if action == "delete_message":
        ok = delete_message(payload.get("created"), payload.get("name"))
    elif action == "delete_thread" and type(payload.get("id")) is int:
        ok = BOARD.delete_thread(payload["id"])
    elif action == "delete_post" and type(payload.get("id")) is int and type(payload.get("index")) is int:
        ok = BOARD.delete_post(payload["id"], payload["index"])
    elif action == "delete_drop" and isinstance(payload.get("id"), str):
        ok = DROP.delete(payload["id"])
    else:
        return 400, {"error": "unknown action"}
    return (200 if ok else 404), moderation_snapshot()


def store_snapshot():
    return {"now": CLOCK.ticks(), "saves": STORE.list_saves(), "usage": STORE.usage(), "drop": DROP.usage()}


def store_action(payload):
    action, key = payload.get("action"), str(payload.get("id", ""))
    if action in ("rename", "delete") and not store.ID_RE.match(key):
        return 400, {"error": "bad id"}
    try:
        if action == "rename":
            name = str(payload.get("name", "")).strip()[:store.MAX_NAME]
            if not name or STORE.rename_save(key, name) is None:
                return 404, {"error": "no such save, or no name"}
        elif action == "delete":
            STORE.delete_save(key)
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
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class HubServer(ThreadingHTTPServer):
    # socketserver listens with a queue of 5. A burst of new connections -- a page load from
    # several guests, or a proxy that does not reuse them -- overflowed it on the Lyra and
    # waited out SYN retries: p99 over 2 s (notes: 2026-10-02-caddy-vs-nginx-benchmark).
    request_queue_size = 64


class Handler(BaseHTTPRequestHandler):
    # Socket timeout per request. A phone that stalls mid-upload releases its thread
    # instead of pinning it; the web server in front already shields the listener itself.
    timeout = 30
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
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
        url = html.escape(HUB_URL, quote=True)
        body = (f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>Irate-Box</title>'
                f'<meta http-equiv="refresh" content="0; url={url}"></head>'
                f'<body><p><a href="{url}">Open the hub</a></p></body></html>').encode()
        self.send_response(302)
        self.send_header("Location", HUB_URL)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _admin_locked(self, path):
        """On an unclaimed box, every admin path but the setup page answers 403."""
        if path.startswith("/admin") and unclaimed() and path not in SETUP_PATHS:
            self.send_json(403, {"error": "no admin password has been chosen yet: open /admin/"})
            return True
        return False

    def _off_hub_name(self):
        """A hotspot guest asking under a name that is not the hub's own (see AP_NET)."""
        if not (AP_NET and HUB_HOST) or self.headers.get("Host", "").split(":")[0] == HUB_HOST:
            return False
        fwd = self.headers.get("X-Forwarded-For", "")
        addr = fwd.split(",")[0].strip() if fwd.strip() else self.client_address[0]
        try:
            return ipaddress.ip_address(addr) in AP_NET
        except ValueError:
            return False

    def do_GET(self):
        note_client(self)
        if self._is_captive_probe() or self._off_hub_name():
            self._redirect_to_hub()
            return

        path = self.path.split("?")[0]
        if self._admin_locked(path):
            return

        if path.startswith("/flasher/") or path == "/flasher":
            self._flasher(path)
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
            self.send_json(200, {"now": now, "ttl": SHOUT_TTL, "messages": msgs})
            return

        if path == "/board/threads":
            self.send_json(200, BOARD.list_threads())
            return

        if path == "/admin/settings":
            self.send_json(200, settings_snapshot())
            return

        if path == "/admin/tailscale":
            self._send_tailscale()
            return

        if path == "/admin/library":
            self.send_json(200, library_snapshot())
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

        if path == "/admin/network":
            self.send_json(200, network_snapshot())
            return

        if path == "/admin/health":
            self.send_json(200, health_snapshot())
            return

        if path == "/admin/addons":
            self.send_json(200, addons_snapshot())
            return

        if path == "/admin/usb":
            self.send_json(200, usb_snapshot())
            return

        if path == "/admin/git":
            self.send_json(200, gitrepos.snapshot())
            return

        if path == "/admin/ci":
            self.send_json(200, ci.snapshot())
            return

        if path == "/admin/firmware":
            snap = firmware.snapshot()
            snap.update(running=librarian.is_running(), progress=librarian.progress())
            self.send_json(200, snap)
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
            }
            joined = joined_count()
            if joined is not None:
                payload["joined"] = joined
            self.send_json(200, payload)
            return

        tid = thread_id(path)
        if tid is not None:
            result = BOARD.get_thread(tid)
            if result is None:
                self.send_json(404, {"error": "no such thread"})
            else:
                self.send_json(200, result)
            return

        if path in MENU_PAGES:
            body = menu_page(MENU_PAGES[path])
            self.send_response(200)
            self.send_header("Content-Type", MIME[".html"])
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/index.html"):
            body = home_page()
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
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def do_POST(self):
        note_client(self)
        path = self.path.split("?")[0]

        # Delegated before the body is read: the store takes raw bytes, and the
        # Excalidraw frontend sends no Content-Type for JSON to be parsed from.
        if store.handle(self, "POST", path, STORE, DROP):
            return

        if self._admin_locked(path):
            return
        payload = self._read_payload()
        if payload is None:
            self.send_json(400, {"error": "bad request"})
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
                for key in DEFAULT_SETTINGS:
                    if valid_setting(key, payload.get(key)):
                        _settings[key] = payload[key]
                current = dict(_settings)
                save_settings(current)
                apply_settings(current)
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
            self.send_json(*gitrepos.action(payload))
            return

        if path == "/admin/firmware":
            self.send_json(*firmware_action(payload))
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
            else:
                self.send_json(400, {"error": "action must be scan, import (device, file) or export (device, book)"})
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

        if path == "/admin/security":
            if payload.get("action") == "scan":
                self.send_json(202, {"id": control_request({"action": "security-scan"})})
            elif payload.get("action") == "audit":
                self.send_json(202, {"id": control_request({"action": "security-audit"})})
            elif payload.get("action") == "fix" and SECURITY_CHOICE_RE.match(str(payload.get("choice", ""))):
                self.send_json(202, {"id": control_request({"action": "security-fix", "choice": payload["choice"]})})
            else:
                self.send_json(400, {"error": "action must be scan, audit, or fix with a choice"})
            return

        if path == "/messages":
            self._post_message(payload)
            return

        if path == "/board/threads":
            try:
                result = BOARD.create_thread(payload.get("name"),
                                             payload.get("title"),
                                             payload.get("text"),
                                             payload.get("hue"))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(201, result)
            return

        tid = thread_id(path)
        if tid is not None:
            try:
                result = BOARD.reply(tid, payload.get("name"), payload.get("text"),
                                     payload.get("hue"))
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

    def _post_message(self, payload):
        name = str(payload.get("name", "")).strip()[:32]
        text = str(payload.get("text", "")).strip()[:200]
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
    # One thread per request: a 50 MB paste into the blob store must not freeze
    # everyone else's shoutbox poll. State is guarded by `lock` and the store's own.
    server = HubServer((BIND, PORT), Handler)
    print(f"Hub running at http://{BIND}:{PORT}")
    print(f"Uptime clock at {hubclock.format_age(CLOCK.ticks())} cumulative")
    try:
        server.serve_forever()
    finally:
        CLOCK.stop()
