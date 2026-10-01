#!/usr/bin/env python3
"""Irate-Box hub server: shoutbox, board, blob store, status, captive-portal target.

Serves static/ itself so a bare `python3 server.py` works; behind Caddy the assets are
served by file_server and only `/` and the API reach this process."""

import io
import json
import os
import secrets
import socket
import subprocess
import tarfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import html
import board
import hubclock
import librarian
import manifests
import store

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
BIND = os.environ.get("HUB_BIND", "0.0.0.0")  # 127.0.0.1 when Caddy is in front
# Where captive-portal probes are sent. Unset, the redirect is relative and lands on
# whatever address the probe arrived at -- fine on localhost, and on the AP every name
# resolves to the hub anyway. The Pi's unit sets the real origin, http://192.168.4.1/,
# so the sign-in sheet shows an address a guest can type again later.
HUB_URL = os.environ.get("HUB_URL", "/")

CLOCK = hubclock.HubClock(STATE_DIR / "clock.json")
BOARD = board.Board(STATE_DIR / "board.json", CLOCK)
# Excalidraw's storage API and the saved-work gallery. See store.py.
STORE = store.Store(STATE_DIR / "store", CLOCK)

# Hosts and paths used by OSes to detect captive portals.
# We redirect them to the hub, which triggers the "sign in to network" popup.
CAPTIVE_HOSTS = {
    "captive.apple.com",
    "www.apple.com",
    "connectivitycheck.gstatic.com",
    "clients3.google.com",
    "www.msftconnecttest.com",
    "www.msftncsi.com",
    "detectportal.firefox.com",
    "nmcheck.gnome.org",
}

CAPTIVE_PATHS = {
    "/hotspot-detect.html",       # iOS / macOS
    "/library/test/success.html", # older iOS
    "/generate_204",              # Android
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


# What sits behind Caddy: each app's "status" part in apps.d/ (manifests.py), then the box's
# own pieces, which are not apps. Keyed by the path the tile links to.
#   port   a loopback listener to probe: that is what "running" means to a guest
#   unit   its systemd unit, which says whether it is installed at all
#   active running means the unit is active, for a daemon with no loopback port to probe
#   root   (static apps) the env var naming the directory Caddy serves, the same
#          variable the Caddyfile reads. Unset -- a dev checkout -- counts as installed.
#   note   what the dashboard says in place of a path
# path None: on the service dashboard only, with no tile of its own.
TAILSCALE_NAME = "Remote access (Tailscale)"
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
    {"path": None, "name": "Web server (Caddy)", "unit": "caddy.service", "proxy": True},
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
    """Record that this address is around. Behind Caddy every request arrives from
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
# The gate is Caddy's basic_auth on /admin/*, the same treatment /term/ gets -- run
# the hub bare, with no Caddy in front, and these are as open as every other hub API.
SETTINGS_FILE = STATE_DIR / "settings.json"
DEFAULT_SETTINGS = {
    # The ttyd card is shown like any other service -- greyed while its unit is off --
    # unless the operator would rather guests were not shown the escape hatch at all.
    "show_term_card": True,
    # The saved-work store (store.py): its size cap, and how long a gallery save lives
    # (0 = until the cap evicts it). Defaults from HUB_STORE_* in hub.env.
    "store_max_total_mb": max(1, store.MAX_TOTAL >> 20),
    "store_save_ttl_hours": int(store.SAVE_TTL // 3600),
}
_settings_lock = threading.Lock()


def valid_setting(key, value):
    want = type(DEFAULT_SETTINGS[key])
    return type(value) is want and (want is not int or value >= 0)


def apply_settings(data):
    store.MAX_TOTAL = max(1, data["store_max_total_mb"]) << 20
    store.SAVE_TTL = data["store_save_ttl_hours"] * 3600


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


def service_status(proxied):
    """Per-service state: "running", "stopped" (installed, not answering) or "missing".
    `up` stays for the tiles: running, and reachable because Caddy is in front. Probes
    are cached so a page full of pollers costs one sweep per STATUS_CACHE_S."""
    now = time.monotonic()
    if now - _status_cache["at"] > STATUS_CACHE_S:
        _status_cache["ports"] = {
            svc["port"]: port_listening(svc["port"]) for svc in SERVICES if "port" in svc
        }
        _status_cache["units"] = unit_states([s["unit"] for s in SERVICES if "unit" in s])
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
            have = {s["name"] for s in librarian.load_config()["sources"]}
            for app in librarian.default_apps():
                if app not in have:
                    librarian.add_source(librarian.default_app_source(app))
        elif action in ("check", "update"):
            download = action == "update"
            if not library_start(action, lambda: librarian.update(names or None, download=download, **quiet)):
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
CONTROL_OPS.update({"caddy.service": ["restart"], "irate-box.service": ["restart"]})
MIN_PASSWORD = 8
# First use (hub_control.py, the Caddyfile): while this root-owned file exists no admin
# password has been chosen, Caddy lets /admin through with no login, and the hub serves
# only the set-the-password page there.
UNCLAIMED_FILE = Path(os.environ.get("HUB_UNCLAIMED_FILE", "/etc/caddy/irate-box-unclaimed"))
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
DOCTOR_STATE = CONTROL_DIR / "doctor.json"
UPDATE_ACTIONS = {"fetch": "update-fetch", "install": "update-install",
                  "doctor": "update-doctor", "clear-cache": "update-clear-cache"}


def update_snapshot():
    """What /admin's update buttons show: the last fetch (from the root helper), the tail
    of the last install's output, and whether a request is still being worked on."""
    try:
        state = json.loads(UPDATE_STATE.read_text())
    except (OSError, ValueError):
        state = None
    try:
        log = UPDATE_LOG.read_text(errors="replace").splitlines()[-40:]
    except OSError:
        log = []
    try:
        doctor = json.loads(DOCTOR_STATE.read_text())
    except (OSError, ValueError):
        doctor = None
    pending = len(list(CONTROL_REQUESTS.glob("*.json"))) if CONTROL_REQUESTS.exists() else 0
    return {"version": hub_version(), "state": state, "log": log, "pending": pending,
            "doctor": doctor, "results": control_results(5)}


def moderation_snapshot():
    now = CLOCK.ticks()
    with lock:
        msgs = live_messages(now)
    return {"now": now, "messages": list(reversed(msgs)), "board": BOARD.all_threads()}


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
    else:
        return 400, {"error": "unknown action"}
    return (200 if ok else 404), moderation_snapshot()


def store_snapshot():
    return {"now": CLOCK.ticks(), "saves": STORE.list_saves(), "usage": STORE.usage()}


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


class Handler(BaseHTTPRequestHandler):
    # Socket timeout per request. A phone that stalls mid-upload releases its thread
    # instead of pinning it; Caddy in front already shields the listener itself.
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
        self.send_response(302)
        self.send_header("Location", HUB_URL)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _admin_locked(self, path):
        """On an unclaimed box, every admin path but the setup page answers 403."""
        if path.startswith("/admin") and unclaimed() and path not in SETUP_PATHS:
            self.send_json(403, {"error": "no admin password has been chosen yet: open /admin/"})
            return True
        return False

    def do_GET(self):
        note_client(self)
        if self._is_captive_probe():
            self._redirect_to_hub()
            return

        path = self.path.split("?")[0]
        if self._admin_locked(path):
            return

        if path == "/admin/setup":
            query = dict(p.partition("=")[::2] for p in self.path.partition("?")[2].split("&") if p)
            self.send_json(200, setup_status(query.get("id", "")[:40]))
            return

        if store.handle(self, "GET", path, STORE):
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

        if path in ("/admin", "/admin/"):
            path = "/admin-setup.html" if unclaimed() else "/admin.html"

        if path == "/status":
            # Caddy's reverse_proxy adds X-Forwarded-For; a direct hit has none.
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
        if store.handle(self, "POST", path, STORE):
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
        if not store.handle(self, "PUT", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_DELETE(self):
        if not store.handle(self, "DELETE", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_PATCH(self):
        if not store.handle(self, "PATCH", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_OPTIONS(self):
        if not store.handle(self, "OPTIONS", self.path.split("?")[0], STORE):
            self.send_empty(404)

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


if __name__ == "__main__":
    CLOCK.start()
    # One thread per request: a 50 MB paste into the blob store must not freeze
    # everyone else's shoutbox poll. State is guarded by `lock` and the store's own.
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"Hub running at http://{BIND}:{PORT}")
    print(f"Uptime clock at {hubclock.format_age(CLOCK.ticks())} cumulative")
    try:
        server.serve_forever()
    finally:
        CLOCK.stop()
