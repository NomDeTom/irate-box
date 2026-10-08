#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root helper for the admin page: the few things the unprivileged hub cannot do itself.

The hub (server.py, user hub) writes one JSON request per file into
$HUB_STATE_DIR/control/requests/. irate-box-control.path notices the folder is not empty
and runs this as root, once per batch. Each request is checked against an allow-list,
carried out, answered in control/results/<id>.json, and deleted. Nothing else is accepted:

  {"id": ..., "action": "service", "unit": "<allowed unit>", "op": "start|stop|restart|enable|disable"}
      Optional services get every op; the web server and the hub itself only restart.
  {"id": ..., "action": "password", "password": "<new admin password>"[, "setup": true]}
      The one admin login: the web server's (nginx's login file, or Caddy's basic_auth
      hashes; /admin, /sync, /term), Syncthing's GUI login, and
      /etc/hub/admin-password. With "setup", this is
      the first-use form on an unclaimed box: accepted only while UNCLAIMED exists, which
      it then deletes, so the first password chosen is the only one set this way.
  {"id": ..., "action": "update-check"}
      Clone or fast-forward irate-box into a root-owned cache, from the repository and branch
      install.sh recorded in /etc/hub/install-options, and say what is new. The summary goes
      to control/update.json. The hub never writes the code that root will run.
  {"id": ..., "action": "update-fetch"}
      Check first if the cache does not hold what the last check found, then verify it
      (verify_update) and prefetch the release downloads its install.sh will want into the
      download cache. Every check goes into control/update.json.
  {"id": ..., "action": "update-install"}
      Run that checkout's install.sh with the recorded options and the download cache;
      output in control/update.log. Refused unless the fetched commit passed verification.
  While any of these three runs, control/update-progress.json says which step it is on, of
  how many, and how far a download has got (Progress), for the bars on /admin.
  {"id": ..., "action": "update-doctor"}
      For when a check fails: look at everything an update needs (doctor) and write a plain
      report, each finding with what to do, to control/doctor.json.
  {"id": ..., "action": "update-clear-cache"}
      Remove the cached clone and downloads, so the next check starts afresh.
  {"id": ..., "action": "addon", "addon": "<an apps.d add-on>", "on": true|false}
      Rerun install.sh from a copy of the installed code with that add-on's --with-* option
      added, or taken out with --remove; output and progress as for update-install.
  {"id": ..., "action": "usb-scan"} / "usb-import" (device, file) / "usb-export" (device, book)
      Books to and from a USB stick (usbstick.py): what is on each stick to control/usb.json;
      a book copied in (then the library rebuilt) or out, with progress in usb-progress.json.
  {"id": ..., "action": "security-scan"}
      What the box exposes and how it is set up (security.py): to control/security.json.
  {"id": ..., "action": "security-audit"}
      The security doctor (secdoctor.py): a read-only audit of how the system is set up, to
      control/security-audit.json. Changes nothing but that file.
  {"id": ..., "action": "security-fix", "choice": "<one of the page's offers>"}
      Carry out one fix the Security page offered (security.fix: a drop-in or a unit switched
      off, each recorded with how to undo it), then scan again.
  {"id": ..., "action": "health-scan"}
      The box doctor (health.py): the last install, the root helper, every unit, Kiwix, the
      watchdog, the web server's config; to control/health.json.
  {"id": ..., "action": "health-fix", "choice": "<one of its offers>"}
      One repair the doctor offered (restart a unit, rebuild or tidy the Kiwix library, …), or
      "rerun-install": install.sh again from a copy of the installed code, recorded options.
      Then the doctor looks again.
  {"id": ..., "action": "net-scan"[, "iface": "<interface or phy>"]}
      What the box has for networking (netinv.py): radios, who runs them, what each can do,
      what is in the way; to control/netinv.json. With "iface", that device alone.
  {"id": ..., "action": "uplink-set", "settings": {eagerness, forgiveness, iface, overrides}}
      How hard the watchdog (uplink.py) works to keep the box on its network: checked by
      uplink.validate, written to /etc/hub/uplink.json, which irate-box-uplink picks up.
  {"id": ..., "action": "uplink-hold", "minutes": 0-1440}
      No repairs for that long (0 ends a hold), for an owner working on the network.
  {"id": ..., "action": "uplink-profile", "on": true|false}
      The by-consent change to the owner's WiFi profile (keep retrying), or its undo.
  {"id": ..., "action": "app-install", "app": "draw|mermaid|serial|room", "zip": "<staged bundle>"}
      Check a bundle the librarian staged in $STATE/library/apps/ and swap it in under
      /usr/share/hub (the previous copy kept). {"action": "app-rollback", "app": ...} swaps back.
  {"id": ..., "action": "kit-fetch", "kit": "<a shipped toolkit>", "budget_mb": 500}
  {"id": ..., "action": "kit-install", "kit": ..., "hours": 24|null}
  {"id": ..., "action": "kit-remove" | "kit-keep" | "kit-rollback", "kit": ...[, "hours": ...]}   {"action": "kit-expire"}
      Toolkits (kits.py): fetch a shipped kit's packages into the local repository (online),
      install it from there with no internet (removed again after "hours"; null: never),
      remove what it added, change when it goes, or remove every kit whose time is up. A
      request names a kit, never packages. control/kits.json says what is cached and installed.
  {"action": "kit-define", "kit": {id, title, summary, packages, remove_after_hours}}
  {"action": "kit-undefine", "kit": ...}   {"action": "kit-extra", "kit": ..., "packages": [...]}
      The owner's own kits, and extra tools in a shipped kit (step 38): every package name
      checked against this box's package lists before root keeps the definition.

From a root shell, the same file also resets the login, for an owner who has lost it:

    sudo /opt/irate-box/irate-box hub_control reset-password

which puts the box back to unclaimed: /admin asks the next visitor to choose a password, and
install.sh --apps-from-actions uses  hub_control.py install-app APP ZIP  for a first install.

Stdlib only.
"""

import hashlib
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from irate_box.hub import access
from irate_box.root import health
from irate_box.hub import manifests
from irate_box.hub import netinv
from irate_box.root import secdoctor
from irate_box.root import security
from irate_box.hub import uplink
from irate_box.root import ap, kits, safeio, usbstick

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
HUB_USER = os.environ.get("HUB_USER", "hub")
CONTROL = STATE / "control"
REQUESTS = CONTROL / "requests"
RESULTS = CONTROL / "results"


def _web_server():
    """The web server in front: nginx, or Caddy, the fallback (install.sh --web). The unit
    sets it; from a root shell (reset-password) it comes from the recorded install options.
    A box installed before the choice existed has Caddy."""
    if os.environ.get("HUB_WEB_SERVER") in ("nginx", "caddy"):
        return os.environ["HUB_WEB_SERVER"]
    try:
        opts = (ETC / "install-options").read_text().split()
    except OSError:
        return "caddy"
    web = opts[opts.index("--web") + 1] if "--web" in opts[:-1] else "caddy"
    return web if web in ("nginx", "caddy") else "caddy"


WEB_SERVER = _web_server()
CADDYFILE = Path(os.environ.get("HUB_CADDYFILE", "/etc/caddy/Caddyfile"))
# On a box whose Caddy is the owner's, the hub's site (and so its login) is this file, imported
# by their Caddyfile (install.sh); otherwise the Caddyfile is wholly the hub's.
CADDY_SITE = CADDYFILE.parent / "irate-box.caddy"
# nginx: the hub's login file (SHA-512-crypt; nginx reads it on every request).
NGINX_LOGINS = Path(os.environ.get("HUB_NGINX_LOGINS", "/etc/nginx/irate-box.htpasswd"))
# Present while no admin password has been chosen (install.sh creates it, beside the web
# server's config). The server then lets /admin through without a login, and the hub serves
# only the set-the-password page.
UNCLAIMED = Path(os.environ.get("HUB_UNCLAIMED_FILE", f"/etc/{WEB_SERVER}/irate-box-unclaimed"))


def _login_file():
    return CADDY_SITE if CADDY_SITE.exists() else CADDYFILE
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
UPDATE_SRC = Path(os.environ.get("HUB_UPDATE_DIR", "/var/cache/irate-box/src"))
UPDATE_STATE = CONTROL / "update.json"
UPDATE_LOG = CONTROL / "update.log"
UPDATE_PROGRESS = CONTROL / "update-progress.json"
DOCTOR_STATE = CONTROL / "doctor.json"
SECURITY_STATE = CONTROL / "security.json"
AUDIT_STATE = CONTROL / "security-audit.json"
USB_STATE = CONTROL / "usb.json"
USB_PROGRESS = CONTROL / "usb-progress.json"
ZIM_DIR = STATE / "zim"
SECURITY_LOG = CONTROL / security.UPDATES_LOG_NAME
# Release downloads install.sh would otherwise make itself (install.sh --download-cache).
DOWNLOADS = Path(os.environ.get("HUB_DOWNLOAD_CACHE", "/var/cache/irate-box/downloads"))
# The offline kit /admin offers (Backup): root-owned, readable by the hub, which serves it.
KITS = STATE / "kits"
KIT_PROGRESS = CONTROL / "kit-progress.json"
INSTALL_TIMEOUT = 45 * 60
# How many "==> " steps an install prints, until one has run here and been counted.
INSTALL_STEPS_GUESS = 12

OPS_ALL = ("start", "stop", "restart", "enable", "disable")
# The units each app's manifest offers for control (apps.d/, beside this file and as
# root-owned as it), and the two that may only be restarted.
MANIFESTS = manifests.load()
UNITS = {unit.replace("@hub.", f"@{HUB_USER}."): OPS_ALL for unit in manifests.controllable_units(MANIFESTS)}
UNITS.update({f"{WEB_SERVER}.service": ("restart",), "irate-box.service": ("restart",)})
# Who may open each app (access.py): root's choice, the web server's snippet made from it,
# and the copy the hub reads for its tiles.
ACCESS_FILE = ETC / "access.json"
ACCESS_STATE = CONTROL / "access.json"
# The box's own admin login switched off (accounts step 16, stage 3): its hash kept here, root's,
# and the login file emptied. The hub's copy of whether it is on, for /admin → Accounts.
ADMIN_LOGIN_OFF = ETC / "admin-login.off"
ADMIN_LOGIN_STATE = CONTROL / "admin-login.json"
ACCESS_STOPPED = ETC / "access-stopped.json"  # the services "off" stopped, to start again
NGINX_ACCESS = Path(os.environ.get("HUB_NGINX_ACCESS", ETC / "nginx-access.conf"))
# The add-on server's maps (http level), and Caddy's add-on routes: from access.json and the
# local add-ons' manifests, re-checked here (the hub writes those).
NGINX_ADDON_ACCESS = Path(os.environ.get("HUB_NGINX_ADDON_ACCESS", ETC / "nginx-addons.conf"))
# A local add-on in users mode (item 6, current-and-next-actions): one location per such add-on,
# ahead of the add-on server's shared one (access.addon_gates).
NGINX_ADDON_GATES = Path(os.environ.get("HUB_NGINX_ADDON_GATES", ETC / "nginx-addon-gates.conf.d"))
CADDY_ACCESS = Path(os.environ.get("HUB_ACCESS_DIR", "/etc/caddy/irate-box-access"))
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HASH_LINE = re.compile(r"^(\s*admin\s+)\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}\s*$", re.M)
MIN_PASSWORD = 8


# systemd-run's timers default to AccuracySec=1min: "--on-active=1" fired 22 s late in a
# test, and the old Caddy login kept working until then.
TIMER_EXACT = ("--timer-property=AccuracySec=100ms",)


def run(*cmd, timeout=120, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


# Root's files in control/ (F3, F13): safeio.write, a new file renamed into place, never through
# a link the hub planted; left root's, 0644, which the hub reads. Nothing is chowned to the hub.


def service(req):
    unit, op = str(req.get("unit", "")), str(req.get("op", ""))
    if unit not in UNITS:
        raise ValueError(f"{unit} is not a unit the admin page controls")
    if op not in UNITS[unit]:
        raise ValueError(f"{op} is not allowed for {unit}")
    if run("systemctl", "cat", unit).returncode != 0:
        raise ValueError(f"{unit} is not installed")
    args = {"enable": ("enable", "--now"), "disable": ("disable", "--now")}.get(op, (op,))
    if unit == "irate-box.service":
        # Restarting the hub from a request the hub made: answer first, then go.
        subprocess.Popen(["systemd-run", "--on-active=2", *TIMER_EXACT, "systemctl", "restart", unit])
        return f"{unit} restarting"
    out = run("systemctl", *args, unit)
    if out.returncode != 0:
        raise ValueError((out.stderr or out.stdout).strip().splitlines()[-1] if (out.stderr or out.stdout).strip() else f"systemctl {op} failed")
    return f"{unit}: {op} done, now {run('systemctl', 'is-active', unit).stdout.strip()}"


def password(req):
    pw = str(req.get("password", ""))
    if len(pw) < MIN_PASSWORD or len(pw) > 128 or any(c in pw for c in "\n\r\0"):
        raise ValueError(f"the password must be {MIN_PASSWORD}-128 characters, on one line")
    if req.get("setup") and not UNCLAIMED.exists():
        raise ValueError("the admin password has already been set; log in to change it")
    done = set_login(pw)
    if req.get("setup"):
        UNCLAIMED.unlink(missing_ok=True)
        return "admin password set: " + done
    return "admin password changed: " + done


def reset_password():
    """Back to unclaimed: an unknown random login everywhere, and the first-use form on
    /admin. For the console, when the owner has lost the password."""
    set_login(secrets.token_urlsafe(24), keep=False)
    UNCLAIMED.write_text("no admin password chosen yet (hub_control.py reset-password)\n")
    UNCLAIMED.chmod(0o644)
    return "admin password reset: /admin now asks the next visitor to choose one"


def _set_nginx_login(pw):
    """The login file nginx checks. SHA-512-crypt, not bcrypt: nginx checks the hash on every
    request and caches nothing, and bcrypt at Caddy's cost takes ~5.8 s a check on the Lyra
    (this: 38 ms). nginx reads the file per request, so nothing needs reloading."""
    if not NGINX_LOGINS.exists():
        raise ValueError(f"no admin login file at {NGINX_LOGINS}")
    out = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=pw + "\n",
                         capture_output=True, text=True, timeout=30)
    hashed = out.stdout.strip()
    if out.returncode != 0 or not hashed.startswith("$6$"):
        raise ValueError("openssl passwd failed")
    gid = NGINX_LOGINS.stat().st_gid  # nginx's group, as install.sh set it
    tmp = NGINX_LOGINS.with_name(NGINX_LOGINS.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"admin:{hashed}\n")
    if os.geteuid() == 0:  # always, but in the tests
        os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, NGINX_LOGINS)
    return "nginx (1 login)"


def _set_caddy_login(pw):
    """The bcrypt hashes in the hub's Caddy config, validated before they replace it."""
    # On stdin, not the command line, which every process can read in /proc (F9).
    hashed = run("caddy", "hash-password", input=pw + "\n")
    if hashed.returncode != 0:
        raise ValueError("caddy hash-password failed")
    count = _caddy_login_swap(hashed.stdout.strip())
    # The private apps' snippets carry the hash too.
    if CADDY_ACCESS.is_dir():
        _access_files(access.read(ACCESS_FILE))
    # Restart, not reload: the Caddyfile turns Caddy's admin API off, which reload needs.
    subprocess.Popen(["systemd-run", "--on-active=1", *TIMER_EXACT, "systemctl", "restart", "caddy"])
    return f"Caddy ({count} logins)"


def set_login(pw, keep=True):
    """Put pw everywhere the admin login is used. keep=False leaves /etc/hub/admin-password
    out (removed): a random placeholder nobody is meant to know. A new password turns the box's
    own login back on if it was off (reset-password at the console is the way back in)."""
    # The web server first: if its new login cannot be written, nothing else has changed.
    web = _set_nginx_login(pw) if WEB_SERVER == "nginx" else _set_caddy_login(pw)
    if ADMIN_LOGIN_OFF.exists():
        ADMIN_LOGIN_OFF.unlink()
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
        web += ", the box's own login on again"
    _admin_login_record()

    secret = ETC / "admin-password"
    if keep:
        # Root's alone from the moment it exists (a write then a chmod left it readable by every
        # account for that moment), into a new file renamed over the old.
        tmp = secret.with_name(f".{secret.name}.new")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(pw + "\n")
        os.replace(tmp, secret)
    else:
        secret.unlink(missing_ok=True)
    # ttyd has no credential of its own any more (F9); a box installed before then has one, the
    # old password, which is no use to anyone once it changes: it goes.
    (ETC / "ttyd.env").unlink(missing_ok=True)
    done = [web]
    if run("systemctl", "is-active", "--quiet", f"syncthing@{HUB_USER}").returncode == 0:
        done.append("Syncthing" if syncthing_gui_password(pw) else "Syncthing (failed)")
    return ", ".join(d for d in done if d)


SYNCTHING_GUI = "http://127.0.0.1:8384"


def syncthing_gui_password(pw, tries=15, sleep=time.sleep):
    """Syncthing's GUI password, through its REST API with the API key from its own config: the
    password travels in the request body, never on a command line (F9: `syncthing cli … password
    set` put it in /proc for every process to read). Syncthing hashes it (bcrypt) as it saves.
    Tried again for a while: install.sh's `gui user set` just before restarts the GUI's listener,
    and a request in that gap finds nothing there (the Lyra, 2026-10-07: /sync/ kept its old one)."""
    cfg = next((t for t in (STATE / ".local/state/syncthing/config.xml", STATE / ".config/syncthing/config.xml")
                if t.exists()), None)
    m = re.search(r"<apikey>([^<]+)</apikey>", cfg.read_text()) if cfg else None
    if not m:
        return False
    req = urllib.request.Request(f"{SYNCTHING_GUI}/rest/config/gui", method="PATCH",
                                 data=json.dumps({"password": pw}).encode(),
                                 headers={"X-API-Key": m.group(1), "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status == 200
        except OSError:
            if attempt + 1 < tries:
                sleep(1)
    return False


# --- an offline kit of this box (/admin, Backup) ------------------------------------------

def _du(*paths):
    total = 0
    for p in paths:
        for root, _, files in os.walk(p):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    return total


def offline_kit(req):
    """A kit that sets up another box with no internet, from what this one has: its code (the
    installer's --make-offline-bundle, run from the installed copy), its apps, its download
    cache (anything missing fetched if there is internet), and, if asked, its books. One .tar in
    $STATE/kits, which replaces the last; the hub offers it as a download."""
    books = req.get("books") is True
    zims = sorted(ZIM_DIR.glob("*.zim")) if books else []
    apps = Path("/usr/share/hub/apps")
    arch = platform.machine()
    base = _du(apps, Path("/usr/share/hub/room"), DOWNLOADS) + (4 << 20)
    need = 2 * base + sum(z.stat().st_size for z in zims) + (64 << 20)
    # The hub owns $STATE, so kits/ is made root's through its own fd (never a link the hub
    # planted), and every path below goes through that fd: a kits/ renamed away and replaced by
    # a link while this runs changes nothing, and HOME for the installer is a root-only folder
    # (security stance review 2026-10-08, N1).
    safeio.mkdir(KITS, 0, 0, 0o755)
    kits_fd = safeio._dir_fd(KITS)
    try:
        if os.fstat(kits_fd).st_uid != 0:
            raise ValueError(f"{KITS} is not root's: refused")
        return _offline_kit(Path(f"/proc/self/fd/{kits_fd}"), zims, apps, arch, need)
    finally:
        os.close(kits_fd)


def _offline_kit(kdir, zims, apps, arch, need):
    free = shutil.disk_usage(kdir).free
    if free - need < _min_free():
        raise ValueError(f"not enough space: the kit needs about {need >> 20} MB and {free >> 20} MB is free "
                         f"(keeping {_min_free() >> 20} MB spare)" + (" — try without the books" if zims else ""))
    with Progress("kit", 3, path=KIT_PROGRESS) as progress:
        work = Path(tempfile.mkdtemp(prefix=".kit-", dir=kdir))
        try:
            progress.step("Gathering the code, the apps and the downloads")
            kit = work / "irate-box-kit"
            args = ["bash", str(CODE / "install.sh"), "--make-offline-bundle", str(kit), "--apps", str(apps),
                    "--download-cache", str(DOWNLOADS), "--arch", arch]
            if WEB_SERVER == "caddy":
                args += ["--web", "caddy"]
            out = run(*args, timeout=3600, env=dict(os.environ, HOME=str(work)))
            if out.returncode != 0:
                raise ValueError("the kit could not be made: " + ((out.stderr or out.stdout).strip().splitlines() or ["?"])[-1][:300])
            report = [l.strip() for l in out.stdout.splitlines() if l.startswith("    ")]
            if zims:
                progress.step(f"Checksumming {len(zims)} book{'s' if len(zims) != 1 else ''}")
                with open(kit / "SHA256SUMS", "a") as fh:
                    for z in zims:
                        h = hashlib.sha256()
                        with open(z, "rb") as zf:
                            for chunk in iter(lambda: zf.read(1 << 20), b""):
                                h.update(chunk)
                        fh.write(f"{h.hexdigest()}  ./zim/{z.name}\n")
            progress.step("Packing it into one file")
            ver = re.sub(r"[^A-Za-z0-9._-]", "", ((CODE / "VERSION").read_text().split() or ["unknown"])[0]) or "unknown"
            name = f"irate-box-kit-{ver}-{arch}{'-with-books' if zims else ''}.tar"
            # kits/ may hold entries the hub made before root took the folder: the part file is
            # made fresh at exactly this name (a planted link there is removed, never written through).
            part = kdir / f".{name}.part"
            part.unlink(missing_ok=True)
            safeio.create(part, 0o644).close()
            tar = run("tar", "-C", str(work), "-cf", str(part), "irate-box-kit", timeout=3600)
            if tar.returncode == 0 and zims:
                tar = run("tar", "-C", str(ZIM_DIR), "-rf", str(part), "--transform", "s,^,irate-box-kit/zim/,",
                          *[z.name for z in zims], timeout=7200)
            if tar.returncode != 0:
                part.unlink(missing_ok=True)
                raise ValueError("tar failed: " + (tar.stderr.strip().splitlines() or ["?"])[-1][:300])
            part.chmod(0o644)
            for old in kdir.glob("irate-box-kit-*.tar"):
                old.unlink()
            final = kdir / name
            os.replace(part, final)
            meta = {"name": name, "size": final.stat().st_size, "at": time.time(), "arch": arch,
                    "books": [z.name for z in zims], "contents": report[:40]}
            safeio.write(kdir / "kit.json", json.dumps(meta, indent=2))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return f"offline kit ready: {name} ({meta['size'] >> 20} MB)"


# --- who may open each app ---------------------------------------------------------

def _caddy_hash():
    m = HASH_LINE.search(_login_file().read_text()) if _login_file().exists() else None
    return os.environ.get("HUB_CADDY_HASH") or (m.group(0).split()[-1] if m else None)


def _caddy_directive():
    """basic_auth, or basicauth on Caddy older than 2.8 (as install.sh writes it there)."""
    out = run("caddy", "version")
    m = re.search(r"v?(\d+)\.(\d+)", out.stdout or "")
    return "basicauth" if m and (int(m.group(1)), int(m.group(2))) < (2, 8) else "basic_auth"


def _write_root_file(path, text, mode=0o644, group=None):
    tmp = path.with_name(f".{path.name}.new")  # a leading dot: Caddy's <id>.caddy* imports never see it
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    if group is not None:
        os.chown(tmp, 0, group)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _access_files(state):
    """The web server's part, written; returns what it replaced, to put back on failure."""
    local, _ = manifests.load_local(builtin=MANIFESTS)
    if WEB_SERVER == "nginx":
        gates_dir = NGINX_ACCESS.with_name(NGINX_ACCESS.name + ".d")   # the template's @ACCESS@.d/gate-<id>.conf*
        gates = access.nginx_gates(state, admin_login=not ADMIN_LOGIN_OFF.exists())
        gates_dir.mkdir(mode=0o755, exist_ok=True)
        addon_gates = access.addon_gates(state, local)
        NGINX_ADDON_GATES.mkdir(mode=0o755, exist_ok=True)
        paths = ([NGINX_ACCESS, NGINX_ADDON_ACCESS] + sorted(set(gates_dir.glob("gate-*.conf")) | {gates_dir / n for n in gates})
                 + sorted(set(NGINX_ADDON_GATES.glob("users-*.conf")) | {NGINX_ADDON_GATES / n for n in addon_gates}))
        old = {p: p.read_text() if p.exists() else None for p in paths}
        _write_root_file(NGINX_ACCESS, access.nginx_conf(state))
        _write_root_file(NGINX_ADDON_ACCESS, access.addon_nginx_conf(state, local))
        for p in gates_dir.glob("gate-*.conf"):
            if p.name not in gates:
                p.unlink()
        for name, text in gates.items():
            _write_root_file(gates_dir / name, text)
        for p in NGINX_ADDON_GATES.glob("users-*.conf"):
            if p.name not in addon_gates:
                p.unlink()
        for name, text in addon_gates.items():
            _write_root_file(NGINX_ADDON_GATES / name, text)
        return old
    login = _caddy_hash()
    if not login:
        raise ValueError(f"no admin login found in {_login_file()} for the private apps")
    # Readable by Caddy (it runs as its own user), as the Caddyfile with the same hash is.
    CADDY_ACCESS.mkdir(mode=0o755, exist_ok=True)
    old = {}
    files = dict(access.caddy_snippets(state, login, _caddy_directive(), admin_login=not ADMIN_LOGIN_OFF.exists()))
    files["addons-routes.caddy"] = access.addon_caddy_routes(state, local, login, _caddy_directive())
    for name, text in files.items():
        path = CADDY_ACCESS / name
        old[path] = path.read_text() if path.exists() else None
        _write_root_file(path, text)
    return old


def _access_record(state):
    """root's copy, and the hub's (in its control folder: a fresh name, never following a link)."""
    _write_root_file(ACCESS_FILE, json.dumps(state, indent=2) + "\n", 0o644)
    safeio.write(ACCESS_STATE, json.dumps(state))


def _access_apply(state, reload=True):
    """Write the web server's part and check it with the rest of its config; on a failed
    check, put the old files back. reload: make it live (install.sh does that itself)."""
    old = _access_files(state)
    check = run("nginx", "-t") if WEB_SERVER == "nginx" else \
        run("caddy", "validate", "--adapter", "caddyfile", "--config", str(CADDYFILE))
    if check.returncode != 0:
        for path, text in old.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                _write_root_file(path, text)
        raise ValueError("the web server did not accept the change; nothing was changed: "
                         + ((check.stderr or check.stdout).strip().splitlines() or [""])[-1][:200])
    _access_record(state)
    if reload:
        if WEB_SERVER == "nginx":
            run("systemctl", "reload", "nginx")
        else:
            # The Caddyfile turns Caddy's admin API off, which reload needs.
            subprocess.Popen(["systemd-run", "--on-active=1", *TIMER_EXACT, "systemctl", "restart", "caddy"])


def _access_unit(app):
    m = next((m for m in MANIFESTS if m["id"] == app), {})
    st = m.get("status", {})
    unit = st.get("unit", "").replace("@hub.", f"@{HUB_USER}.")
    return unit if st.get("control") and unit and run("systemctl", "cat", unit).returncode == 0 else None


def access_set(req):
    """One app public, users, private or off. Off also stops (and disables) an add-on's service;
    leaving off starts it again."""
    app, mode = str(req.get("app", "")), str(req.get("mode", ""))
    local = {m["id"] for m in manifests.load_local(builtin=MANIFESTS)[0]}
    # Off is always safe, so a web add-on just removed (its manifest gone) can still be switched
    # off: the hub asks that when one is added or removed.
    if app not in access.ROUTED and app not in local and not (mode == "off" and access.LOCAL_ID_RE.match(app)):
        raise ValueError(f"{app} is not an app whose access can be set")
    if mode not in access.MODES:
        raise ValueError("mode: public, users, private or off")
    if mode == "users" and not access.users_allowed(app):
        raise ValueError(f"{app} can be public, private or off: not for users")
    state = access.read(ACCESS_FILE)
    was = access.mode_of(state, app)
    state[app] = mode
    _access_apply(state)
    done = f"{app}: {mode}"
    unit = _access_unit(app)
    # Only what "off" stopped is started again: an add-on the owner never switched on stays off.
    try:
        stopped = set(json.loads(ACCESS_STOPPED.read_text()))
    except (OSError, ValueError, TypeError):
        stopped = set()
    if unit and mode == "off" and was != "off":
        if run("systemctl", "is-enabled", "--quiet", unit).returncode == 0 or \
                run("systemctl", "is-active", "--quiet", unit).returncode == 0:
            out = run("systemctl", "disable", "--now", unit)
            if out.returncode == 0:
                stopped.add(unit)
                done += f"; {unit} stopped"
            else:
                done += f"; {unit} could not be stopped"
    elif unit and was == "off" and mode != "off" and unit in stopped:
        out = run("systemctl", "enable", "--now", unit)
        stopped.discard(unit)
        done += f"; {unit} started again" if out.returncode == 0 else f"; {unit} could not be started"
    _write_root_file(ACCESS_STOPPED, json.dumps(sorted(stopped)) + "\n", 0o644)
    return done


def access_install():
    """For install.sh: the choices so far (or the defaults, or the old "show the Terminal
    card" setting), written out for the web server it is about to check and start."""
    if ACCESS_FILE.exists():
        state = access.read(ACCESS_FILE)
    else:
        state = access.defaults()
        try:
            if json.loads((STATE / "settings.json").read_text()).get("show_term_card") is False:
                state["term"] = "private"
        except (OSError, ValueError):
            pass
    _access_files(state)
    _access_record(state)
    _admin_login_record()
    return ", ".join(f"{k} {v}" for k, v in state.items() if v != "public") or "every app public"


def _admin_login_record():
    safeio.write(ADMIN_LOGIN_STATE, json.dumps({"on": not ADMIN_LOGIN_OFF.exists()}))


def _https_admins():
    """The admin accounts that could stand in for the box's own login: switched on, with a
    password, and logged in over HTTPS at least once (accounts.py records it), while the box has
    accounts at all. Read here, not taken from the hub's word."""
    try:
        data = json.loads((STATE / "accounts.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or (data.get("settings") or {}).get("signup", "off") == "off":
        return []
    return sorted(a.get("name", "?") for a in (data.get("accounts") or {}).values()
                  if isinstance(a, dict) and a.get("role") == "admin" and a.get("state") == "user" and a.get("hash") and a.get("https_login"))


def _reload_web():
    if WEB_SERVER == "nginx":
        run("systemctl", "reload", "nginx")
    else:
        # The Caddyfile turns Caddy's admin API off, which reload needs.
        subprocess.Popen(["systemd-run", "--on-active=1", *TIMER_EXACT, "systemctl", "restart", "caddy"])


def _caddy_login_swap(new_hash):
    """Every admin hash in the hub's Caddy config replaced by new_hash, checked before it's kept
    (the owner's whole Caddyfile too, when the hub's site is imported into it)."""
    target = _login_file()
    text = target.read_text()
    updated, count = HASH_LINE.subn(lambda m: m.group(1) + new_hash, text)
    if not count:
        raise ValueError(f"no admin login found in {target}")
    candidate = target.with_name(target.name + ".new")
    candidate.write_text(updated)
    if run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate)).returncode != 0:
        candidate.unlink(missing_ok=True)
        raise ValueError("the new Caddy config did not validate; nothing was changed")
    os.replace(candidate, target)
    if target != CADDYFILE and run("caddy", "validate", "--adapter", "caddyfile", "--config", str(CADDYFILE)).returncode != 0:
        target.write_text(text)
        raise ValueError("the Caddy config did not validate with the new login; nothing was changed")
    return count


def _caddy_login_off():
    """Caddy: the login's hashes swapped for one of a password nobody knows (as nginx's login file
    is emptied); the real one kept in ADMIN_LOGIN_OFF to put back."""
    real = _caddy_hash()
    if not real:
        raise ValueError(f"no admin login found in {_login_file()}")
    hashed = run("caddy", "hash-password", input=secrets.token_urlsafe(32) + "\n")
    if hashed.returncode != 0 or not hashed.stdout.strip().startswith("$2"):
        raise ValueError("caddy hash-password failed")
    fd = os.open(ADMIN_LOGIN_OFF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(real + "\n")
    try:
        _caddy_login_swap(hashed.stdout.strip())
    except ValueError:
        ADMIN_LOGIN_OFF.unlink()
        raise


def admin_login(req):
    """The box's own admin login (basic auth) on or off. Off only while an admin account has
    logged in over HTTPS, so the box can't be locked out from /admin; reset-password at the
    console (or a new password) turns it on again. nginx: its login file emptied; Caddy: its
    hashes swapped for an unknown one, and its admin gate made a hard one (access.py)."""
    on = req.get("on") is True
    if on == (not ADMIN_LOGIN_OFF.exists()):
        return f"the box's own admin login is already {'on' if on else 'off'}"
    if not on:
        admins = _https_admins()
        if not admins:
            raise ValueError("first make an admin account, and log in with it over HTTPS: then it can stand in for this login")
    if WEB_SERVER != "nginx":
        if not on:
            _caddy_login_off()
        else:
            _caddy_login_swap(ADMIN_LOGIN_OFF.read_text().strip())
            ADMIN_LOGIN_OFF.unlink()
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
        _admin_login_record()
        return "the box's own admin login is " + ("on again" if on else f"off: admin accounts ({', '.join(admins)}) open /admin")
    if not on:
        fd = os.open(ADMIN_LOGIN_OFF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(NGINX_LOGINS.read_text())
        text = "# The box's own admin login is off (/admin → Accounts); reset-password at the console turns it on.\n"
    else:
        text = ADMIN_LOGIN_OFF.read_text()
    gid = NGINX_LOGINS.stat().st_gid
    tmp = NGINX_LOGINS.with_name(NGINX_LOGINS.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    if os.geteuid() == 0:  # always, but in the tests
        os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, NGINX_LOGINS)
    if on:
        ADMIN_LOGIN_OFF.unlink()
    _access_files(access.read(ACCESS_FILE))
    _reload_web()
    _admin_login_record()
    return "the box's own admin login is " + ("on again" if on else f"off: admin accounts ({', '.join(admins)}) open /admin")


# --- updates ---------------------------------------------------------------------

def _install_options():
    try:
        lines = (ETC / "install-options").read_text().splitlines()
    except OSError:
        raise ValueError("no /etc/hub/install-options: this box was installed before updates "
                         "from /admin existed. Run install.sh once by hand to record them.")
    return [line for line in lines if line]


def _option(opts, name, default=None):
    return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else default


def _git(*args):
    out = run("git", *args, timeout=600)
    if out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        raise ValueError(f"git {args[0] if args[0] != '-C' else args[2]}: {lines[-1] if lines else 'failed'}")
    return out.stdout.strip()


def _installed_commit():
    """The commit in VERSION (git describe output: abc1234, or v1.2-3-gabc1234, maybe -dirty)."""
    try:
        first = (CODE / "VERSION").read_text().split()[0]
    except (OSError, IndexError):
        return None
    first = first.removesuffix("-dirty")
    match = re.search(r"-g([0-9a-f]{7,40})$", first) or re.match(r"^([0-9a-f]{7,40})$", first)
    return match.group(1) if match else None


def _write_update_state(data):
    safeio.write(UPDATE_STATE, json.dumps(data, indent=2))


def _read_update_state():
    # Root's own file, read only if it still is (F14): it says which commit passed verification.
    try:
        return json.loads(safeio.read_own(UPDATE_STATE))
    except (OSError, ValueError):
        return {}


class Progress:
    """control/update-progress.json (or `path`) while a check, fetch or install runs: step `step` of
    `steps` (`estimate` when the count is a guess), what it is doing, and the bytes of a
    download in flight. The hub only reads it, and ignores it once `pid` has gone; it is
    removed when the action ends, however it ends."""

    def __init__(self, action, steps, estimate=False, path=None):
        self.data = {"action": action, "pid": os.getpid(), "step": 0, "steps": steps,
                     "estimate": estimate, "label": "", "done": 0, "total": 0}
        self.path = path or UPDATE_PROGRESS
        self._last = 0.0

    def __enter__(self):
        self._write()
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)

    def add_steps(self, n):
        self.data["steps"] += n
        self._write()

    def step(self, label):
        step = self.data["step"] + 1
        self.data.update(step=step, steps=max(self.data["steps"], step), label=label, done=0, total=0)
        self._write()

    def bytes(self, done, total):
        """Written at most once a second, and once more when a download completes."""
        self.data.update(done=done, total=total)
        if time.monotonic() - self._last >= 1 or (total and done >= total):
            self._write()

    def _write(self):
        self._last = time.monotonic()
        safeio.write(self.path, json.dumps(self.data))


# --- verifying a fetched update -------------------------------------------------------
# Everything that can be checked before install.sh runs is checked here, so a bad push or a
# broken download shows up as a red line on /admin rather than as a half-installed box.
# Each check: {"name", "ok", "detail", "warn"}; warn marks one that does not block.

def _check(name, ok, detail="", warn=False):
    return {"name": name, "ok": bool(ok), "detail": detail, "warn": warn}


def _arch_names():
    m = platform.machine()
    return {"ttyd": {"x86_64": "x86_64", "aarch64": "aarch64", "armv7l": "armhf", "armv8l": "armhf",
                     "armv6l": "arm"}.get(m),
            "sb": {"x86_64": "x86_64", "aarch64": "aarch64", "armv7l": "armv7", "armv8l": "armv7"}.get(m),
            "caddy": {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7", "armv8l": "armv7"}.get(m)}


def _fetch(url, dest, timeout=300, report=None):
    """Download url to dest, atomically, calling report(done, total) as bytes arrive (total
    is 0 when the server does not say). Returns dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-update"})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if report:
                report(done, total)
    os.replace(tmp, dest)
    return dest


def _digest(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sum_for(sums_file, name):
    for line in Path(sums_file).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _var(script, name):
    m = re.search(rf"^{name}=([0-9A-Za-z._-]+)\s*$", script, re.M)
    return m.group(1) if m else None


def _have_version(cmd, version):
    try:
        return version in run(*cmd, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False


def prefetch(src, opts, progress=None):
    """Download into DOWNLOADS the release files the new install.sh will want and the box
    does not already have, each checked against its published checksum. Returns checks.
    The downloads are listed before any starts, so progress knows how many steps they are."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    script = (src / "install.sh").read_text()
    arch = _arch_names()
    report = progress.bytes if progress else None
    jobs = []  # (step label, function returning a check or None)

    ttyd = _var(script, "TTYD_VERSION")
    if ttyd and arch["ttyd"] and not _have_version(("/usr/local/bin/ttyd", "--version"), ttyd):
        def get_ttyd():
            base = f"https://github.com/tsl0922/ttyd/releases/download/{ttyd}"
            name = f"ttyd.{arch['ttyd']}"
            try:
                sums = _fetch(f"{base}/SHA256SUMS", DOWNLOADS / f"ttyd-{ttyd}-SHA256SUMS")
                binary = _fetch(f"{base}/{name}", DOWNLOADS / f"ttyd-{ttyd}-{name}", report=report)
                ok = _digest(binary, "sha256") == _sum_for(sums, name)
                return _check(f"ttyd {ttyd} downloaded", ok, "checksum matches" if ok else "checksum MISMATCH")
            except OSError as exc:
                return _check(f"ttyd {ttyd} downloaded", False, str(exc))
        jobs.append((f"Downloading ttyd {ttyd}", get_ttyd))

    sb = _var(script, "SB_VERSION")
    if sb and "--with-notes" in opts and arch["sb"] and \
            not _have_version(("/usr/local/bin/silverbullet", "--version"), sb):
        def get_sb():
            name = f"silverbullet-server-linux-{arch['sb']}.zip"
            try:
                z = _fetch(f"https://github.com/silverbulletmd/silverbullet/releases/download/{sb}/{name}",
                           DOWNLOADS / f"silverbullet-{sb}-{name}", report=report)
                # The digest pinned beside SB_VERSION (GitHub's record of the release asset: F30).
                want = _var(script, f"SB_SHA256_{arch['sb']}")
                ok = bool(want) and hashlib.sha256(Path(z).read_bytes()).hexdigest() == want
                return _check(f"SilverBullet {sb} downloaded", ok,
                              "checksum matches" if ok else "checksum MISMATCH" if want else f"no SB_SHA256_{arch['sb']} in install.sh")
            except OSError as exc:
                return _check(f"SilverBullet {sb} downloaded", False, str(exc))
        jobs.append((f"Downloading SilverBullet {sb}", get_sb))

    # Caddy from its GitHub release, on a box served by Caddy whose apt repository failed
    # verification.
    if WEB_SERVER == "caddy" and (ETC / "caddy-from-release").exists() and arch["caddy"]:
        def get_caddy():
            try:
                req = urllib.request.Request("https://api.github.com/repos/caddyserver/caddy/releases/latest",
                                             headers={"User-Agent": "irate-box-update"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    tag = json.load(resp)["tag_name"]
                ver = tag.lstrip("v")
                installed = run("dpkg-query", "-W", "-f=${Version}", "caddy").stdout.strip()
                (DOWNLOADS / "caddy-release-tag").write_text(tag + "\n")
                if installed == ver:
                    return None
                deb = f"caddy_{ver}_linux_{arch['caddy']}.deb"
                base = f"https://github.com/caddyserver/caddy/releases/download/{tag}"
                sums = _fetch(f"{base}/caddy_{ver}_checksums.txt", DOWNLOADS / f"caddy_{ver}_checksums.txt")
                pkg = _fetch(f"{base}/{deb}", DOWNLOADS / deb, report=report)
                ok = _digest(pkg, "sha512") == _sum_for(sums, deb)
                return _check(f"Caddy {ver} downloaded", ok, "checksum matches" if ok else "checksum MISMATCH")
            except (OSError, ValueError, KeyError) as exc:
                return _check("Caddy release downloaded", False, str(exc))
        jobs.append(("Downloading Caddy's latest release", get_caddy))

    if progress:
        progress.add_steps(len(jobs))
    checks = []
    for label, job in jobs:
        if progress:
            progress.step(label)
        check = job()
        if check:
            checks.append(check)
    return checks


def verify_update(src, installed, opts, progress=None):
    """Checks on a fetched tree; any failure not marked warn blocks the install."""
    checks = []
    if installed:
        known = run("git", "-C", str(src), "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
        ff = known and run("git", "-C", str(src), "merge-base", "--is-ancestor", installed, "HEAD").returncode == 0
        checks.append(_check("A fast-forward of the installed version", ff,
                             "history continues from the installed commit" if ff else
                             f"{installed} is not an ancestor: the branch was rewritten, or the box was "
                             "installed from elsewhere. Read the changes before installing.", warn=True))
    for script in ("install.sh", "uninstall.sh", "scripts/tailscale-apply.sh"):
        path = src / script
        if path.exists():
            out = run("bash", "-n", str(path))
            checks.append(_check(f"{script} parses", out.returncode == 0,
                                 (out.stderr.strip().splitlines() or [""])[-1]))
    bad = []
    pys = sorted([*src.glob("irate_box/**/*.py"), *src.glob("scripts/*.py")])
    for py in pys:
        try:
            compile(py.read_text(), str(py), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            bad.append(f"{py.relative_to(src)}: {exc}")
    checks.append(_check("Python files compile", bool(pys) and not bad,
                         "; ".join(bad) or (f"{len(pys)} files" if pys else "no Python files under irate_box/: not this layout")))

    if WEB_SERVER == "nginx":
        checks.append(_nginx_check(src, opts))
    caddyfile = src / "config" / "Caddyfile"
    if WEB_SERVER == "caddy" and caddyfile.exists():
        # As install.sh will write it: the box's current login hash in place of the marker.
        current = _login_file().read_text() if _login_file().exists() else ""
        m = HASH_LINE.search(current)
        text = caddyfile.read_text()
        if m:
            text = text.replace("$2a$14$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT", m.group(0).split()[-1])
        with tempfile.NamedTemporaryFile("w", suffix=".Caddyfile", delete=False) as tmp:
            tmp.write(text)
        try:
            out = run("caddy", "validate", "--adapter", "caddyfile", "--config", tmp.name)
            detail = "" if out.returncode == 0 else ((out.stderr or out.stdout).strip().splitlines() or [""])[-1]
            checks.append(_check("The new Caddyfile validates", out.returncode == 0, detail[:300]))
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(_check("The new Caddyfile validates", False, str(exc)))
        finally:
            os.unlink(tmp.name)
    checks += prefetch(src, opts, progress)
    return checks


def _nginx_check(src, opts):
    """The new irate-box.nginx as install.sh will write it, through nginx -t on its own (a
    minimal main config around it, so the box's live config is not touched)."""
    name = "The new nginx site passes nginx -t"
    template = src / "config" / "irate-box.nginx"
    if not template.exists():
        return _check(name, False, "config/irate-box.nginx is missing from the update")
    text = template.read_text()
    # nobody may not bind a port under 1024, even for a test: an unprivileged one stands in, so
    # the listen lines are still parsed (F29).
    port = _option(opts, "--port", "80")
    if os.geteuid() == 0 and port.isdigit() and int(port) < 1024:
        port = "18080"
    for key, value in {"@PORT@": port, "@STATIC@": str(CODE / "web"),
                       "@APPS@": "/usr/share/hub/apps", "@GIT_ROOT@": str(STATE / "git"),
                       "@FIRMWARE@": str(STATE / "firmware"),
                       "@HTPASSWD@": str(NGINX_LOGINS),
                       "@UNCLAIMED@": str(UNCLAIMED)}.items():
        text = text.replace(key, value)
    with tempfile.TemporaryDirectory() as tmp:
        # Who may open each app, as this box has it now (install.sh writes it the same way).
        (Path(tmp) / "access.conf").write_text(access.nginx_conf(access.read(ACCESS_FILE)))
        text = text.replace("@ACCESS@", f"{tmp}/access.conf")
        # The front's /admin secret: its include, with a stand-in (the check only parses it).
        (Path(tmp) / "front.conf").write_text('proxy_set_header X-Irate-Front "check";\n')
        text = text.replace("@FRONT@", f"{tmp}/front.conf")
        (Path(tmp) / "addons.conf").write_text(access.addon_nginx_conf(access.read(ACCESS_FILE), []))
        # The TLS twins' includes: an empty folder (a box with no certificate), their ports as set.
        (Path(tmp) / "tls").mkdir()
        for key, value in {"@ADDON_ACCESS@": f"{tmp}/addons.conf", "@ADDON_GATES@": f"{tmp}/addon-gates.d", "@ADDON_PORT@": "8090", "@NOTES_PORT@": "8091", "@WIKI_PORT@": "8092", "@GIT_PORT@": "8093",
                           "@TLS@": f"{tmp}/tls", "@TLS_PORT@": "18443", "@ADDON_TLS_PORT@": "8490", "@NOTES_TLS_PORT@": "8491",
                           "@WIKI_TLS_PORT@": "8492", "@GIT_TLS_PORT@": "8493", "@ADDONS@": str(STATE / "addons")}.items():
            text = text.replace(key, value)
        # A port placeholder this helper does not know yet (an update adds a server block): a
        # spare port stands in, so a newer site is checked by its syntax, not refused for being
        # newer than its checker (#27's @NOTES_PORT@ was, on 2026-10-06).
        spare = iter(range(18100, 18200))
        text = re.sub(r"@[A-Z_]+_PORT@", lambda m: str(next(spare)), text)
        conf = Path(tmp) / "nginx.conf"
        # nginx's scratch paths in here too, so it needs nothing of the box's.
        temps = "access_log off;\n" + "".join(f"{k}_temp_path {tmp}/{k};\n" for k in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
        conf.write_text(f"pid {tmp}/nginx.pid;\nerror_log stderr;\nevents {{}}\n"
                        f"http {{\ninclude /etc/nginx/mime.types;\n{temps}{text}\n}}\n")
        # As nobody, not root (F29): this is the fetched commit's config, not yet approved, and
        # nginx -t as root would create any file its error_log or access_log names, or echo an
        # include's first token (a root-only file's) into the report.
        os.chmod(tmp, 0o755)
        for f in Path(tmp).iterdir():
            f.chmod(0o644)
        as_nobody = ()
        if os.geteuid() == 0:
            # nobody's own scratch folder: nginx -t writes its pid and temp paths there.
            nobody = pwd.getpwnam("nobody")
            os.chown(tmp, nobody.pw_uid, nobody.pw_gid)
            as_nobody = ("runuser", "-u", "nobody", "--")
        try:
            out = run(*as_nobody, "nginx", "-t", "-q", "-e", "stderr", "-c", str(conf))
        except (OSError, subprocess.SubprocessError) as exc:
            return _check(name, False, str(exc))
    lines = [l for l in (out.stderr or out.stdout).strip().splitlines() if "[emerg]" in l or "[crit]" in l]
    return _check(name, out.returncode == 0, "" if out.returncode == 0 else (lines or ["nginx -t failed"])[-1][:300])


def _check_for_update(progress):
    """Bring the cache up to the recorded branch and work out what is new: two steps.
    Returns (state for update.json, install options)."""
    opts = _install_options()
    repo = _option(opts, "--repo")
    branch = _option(opts, "--branch", "main")
    if not repo:
        raise ValueError("install-options names no repository")
    if repo.startswith(("http://", "git://")):
        # Code that root will run, over a transport anyone on the way can rewrite (F22).
        raise ValueError(f"updates come from {repo}, which is not encrypted: rerun install.sh with --repo https://…")
    progress.step(f"Fetching {branch} from {repo}")
    src = str(UPDATE_SRC)
    if (UPDATE_SRC / ".git").is_dir():
        _git("-C", src, "remote", "set-url", "origin", repo)
        _git("-C", src, "fetch", "--depth", "200", "origin", branch)
        _git("-C", src, "reset", "--hard", "FETCH_HEAD")
        _git("-C", src, "clean", "-fdx")
    else:
        if UPDATE_SRC.exists():
            subprocess.run(["rm", "-rf", src], check=True)
        UPDATE_SRC.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--depth", "200", "--branch", branch, repo, src)
    progress.step("Reading the changes")
    head = _git("-C", src, "rev-parse", "--short=7", "HEAD")
    full = _git("-C", src, "rev-parse", "HEAD")
    installed = _installed_commit()
    known = bool(installed) and run("git", "-C", src, "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
    span = f"{installed}..HEAD" if known else "-10"
    changes = [c for c in _git("-C", src, "log", "--format=%h %cs %s", span).splitlines() if c]
    up_to_date = bool(installed) and head.startswith(installed[:7]) or (known and not changes)
    state = {
        "repo": repo, "branch": branch, "available": head,
        "available_date": _git("-C", src, "log", "-1", "--format=%cs"),
        "installed": installed, "up_to_date": up_to_date,
        "changes": changes[:50], "changes_known": known, "fetched": time.time(),
        "verified": None, "verified_sha": None, "checks": [],
    }
    # The same commit fetched before: its checks and downloads still stand. Matched on the
    # whole hash (F21): a commit sharing the first 7 digits must not inherit "verified".
    old = _read_update_state()
    if not up_to_date and old.get("verified_sha") == full:
        state.update(verified=head, verified_sha=full, checks=old.get("checks", []))
    if old.get("install_steps"):
        state["install_steps"] = old["install_steps"]
    return state, opts


def _change_count(state):
    n = len(state["changes"])
    return f"{n} new commit{'s' if n != 1 else ''}" if state["changes_known"] else "a different version"


def update_check(req):
    with Progress("check", 2) as progress:
        state, _ = _check_for_update(progress)
    _write_update_state(state)
    if state["up_to_date"]:
        return f"irate-box is up to date ({state['available']} on {state['branch']})"
    if state["verified"]:
        return f"update available: {_change_count(state)}, {state['available']} on {state['branch']}, already fetched and verified"
    return f"update available: {_change_count(state)}, {state['available']} on {state['branch']}; fetch it to verify it"


def update_fetch(req):
    # Steps: the verification, plus the check if one is needed, plus each download
    # (prefetch adds those once it knows them).
    with Progress("fetch", 1) as progress:
        state = _read_update_state()
        cached = (UPDATE_SRC / ".git").is_dir() and \
            run("git", "-C", str(UPDATE_SRC), "rev-parse", "--short=7", "HEAD").stdout.strip()
        if state.get("up_to_date") or not cached or cached != state.get("available"):
            progress.add_steps(2)
            state, opts = _check_for_update(progress)
        else:
            opts = _install_options()
        if state["up_to_date"]:
            _write_update_state(state)
            return f"irate-box is up to date ({state['available']} on {state['branch']})"
        progress.step("Checking the new version")
        checks = verify_update(UPDATE_SRC, state["installed"], opts, progress)
    head = state["available"]
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    failed = [c for c in checks if not c["ok"] and not c["warn"]]
    state.update(checks=checks, verified=None if failed else head, verified_sha=None if failed else full)
    _write_update_state(state)
    if failed:
        return f"update {head} found but did not pass verification: {failed[0]['name']} ({failed[0]['detail']})"
    return f"update fetched: {_change_count(state)}, {head} on {state['branch']}, verified"


STEP_LINE = re.compile(rb"==> (.*?)(?:\x1b\[0m)?\s*$")


def _follow_install(proc, progress):
    """Wait for install.sh, making each "==> " heading it prints (its say()) a step.
    Returns (exit code, steps seen)."""
    deadline = time.monotonic() + INSTALL_TIMEOUT
    seen, offset, partial = 0, 0, b""
    while True:
        code = proc.poll()
        with open(UPDATE_LOG, "rb") as fh:
            fh.seek(offset)
            new = fh.read()
            offset = fh.tell()
        *lines, partial = (partial + new).split(b"\n")
        for line in lines:
            m = STEP_LINE.search(line)
            if m:
                seen += 1
                progress.step(m.group(1).decode(errors="replace"))
        if code is not None:
            return code, seen
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            raise ValueError(f"install.sh did not finish within {INSTALL_TIMEOUT // 60} minutes; see update.log")
        time.sleep(1)


def update_install(req):
    if not (UPDATE_SRC / "install.sh").is_file():
        raise ValueError("nothing fetched yet: check for updates first")
    state = _read_update_state()
    # The whole hash of what is checked out, against the whole hash that passed (F21).
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    if not state.get("verified_sha") or state.get("verified_sha") != full:
        raise ValueError("the fetched version has not passed verification: fetch the update again")
    return _install_fetched(state)


def _install_fetched(state):
    seen = _run_install(UPDATE_SRC, _install_options(), "install", state.get("install_steps"))
    state = _read_update_state()
    if state:
        state.update(installed=_installed_commit(), up_to_date=True, changes=[], install_steps=seen)
        _write_update_state(state)
    return f"updated to {_installed_commit() or 'the fetched version'}"


def update_force_install(req):
    """The fetched version, installed although it failed verification (the Updates doctor's
    "Install anyway"): for when the check is what is wrong, as when an update changes the
    code's layout and this box's check cannot read it. Still only the version that was fetched
    and checked, and only if its installer parses; the installer stops on its own errors."""
    if not (UPDATE_SRC / "install.sh").is_file():
        raise ValueError("nothing fetched yet: check for updates and fetch first")
    state = _read_update_state()
    head = _git("-C", str(UPDATE_SRC), "rev-parse", "--short=7", "HEAD")
    if state.get("available") != head or not state.get("checks"):
        raise ValueError("fetch the update first: only a version that was fetched and checked can be installed anyway")
    if run("bash", "-n", str(UPDATE_SRC / "install.sh")).returncode != 0:
        raise ValueError("the fetched install.sh does not parse, so not even a forced install can run it")
    failed = [c["name"] for c in state["checks"] if not c["ok"] and not c["warn"]]
    done = _install_fetched(state)
    return f"{done} (installed anyway, past: {'; '.join(failed)})" if failed else done


def _run_install(src, args, action, steps=None):
    """Run src's install.sh with args, its output in control/update.log and its "==> " steps in
    update-progress.json. steps: what the last install here printed (a guess until one has).
    Returns the steps it printed; raises ValueError if it fails."""
    cmd = ["bash", str(src / "install.sh"), "--src", str(src), *args]
    if "--download-cache" in (src / "install.sh").read_text():
        cmd += ["--download-cache", str(DOWNLOADS)]
    # systemd gives the helper no HOME, and Caddy (the fallback) warns about it on every validate.
    env = dict(os.environ, HOME=os.environ.get("HOME", "/root"))
    with Progress(action, steps or INSTALL_STEPS_GUESS, estimate=not steps) as progress, \
            safeio.open_new(UPDATE_LOG) as log:
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
        code, seen = _follow_install(proc, progress)
    if code != 0:
        raise ValueError(f"install.sh exited with {code}; see the installer output")
    return seen


# --- add-ons ---------------------------------------------------------------------------
# /admin's Add-ons page: install.sh again, from a copy of the installed code (never the code
# it is about to replace), with one --with-* option added or taken out (--remove). install.sh
# then records the new set in install-options, so updates keep it.

ADDON_SRC = UPDATE_SRC.parent / "addon-src"


def addon(req):
    aid = str(req.get("addon", ""))
    spec = manifests.addons(MANIFESTS).get(aid)
    if not spec:
        raise ValueError(f"{aid} is not an add-on")
    on = req.get("on") is True
    option = spec["addon"]["option"]
    opts = _install_options()
    if (option in opts) == on:
        raise ValueError(f"{spec['addon']['title']} is already {'added' if on else 'removed'}")
    args = [o for o in opts if o != option] + ([option] if on else ["--remove", option.removeprefix("--with-")])
    shutil.rmtree(ADDON_SRC, ignore_errors=True)
    ADDON_SRC.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CODE, ADDON_SRC, symlinks=True)
    state = _read_update_state()
    try:
        seen = _run_install(ADDON_SRC, args, "addon", state.get("install_steps"))
    finally:
        shutil.rmtree(ADDON_SRC, ignore_errors=True)
    if state:  # the next run's step count: the same script, near enough the same steps
        state["install_steps"] = seen
        _write_update_state(state)
    return f"{spec['addon']['title']}: {'added' if on else 'removed'}"


# --- the update doctor ---------------------------------------------------------------
# For when "Check for updates" fails: look at everything an update depends on and say, in
# plain words, what is wrong and what to do. Read-only apart from `apt-get update`, which
# only refreshes package lists. Each finding: {"check", "status": ok|warn|problem,
# "detail", "fix"}.

def _finding(check, status, detail, fix=""):
    return {"check": check, "status": status, "detail": detail, "fix": fix}


def _local_addresses():
    out = run("ip", "-o", "addr", "show", timeout=10).stdout
    return set(re.findall(r"inet6? ([0-9a-f.:]+)/", out))


def _tls_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-doctor"})
    return urllib.request.urlopen(req, timeout=timeout)


def doctor():
    import socket
    import ssl
    from email.utils import parsedate_to_datetime
    findings = []

    # 1. How this box updates.
    try:
        opts = _install_options()
        repo, branch = _option(opts, "--repo"), _option(opts, "--branch", "main")
        findings.append(_finding("Install options", "ok", f"updates come from {repo} ({branch})"))
    except ValueError as exc:
        opts, repo, branch = [], None, "main"
        findings.append(_finding("Install options", "problem", str(exc),
                                 "Run install.sh once by hand (sudo ./install.sh …) to record them."))

    # 2. Names: on the hub's own access point every name resolves to the box itself.
    host = "github.com"
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
        mine = addrs & (_local_addresses() | {"127.0.0.1", "::1"})
        if mine:
            findings.append(_finding("DNS", "problem", f"{host} resolves to this box ({', '.join(sorted(mine))})",
                                     "The box is answering every name itself, as it does in access-point mode. "
                                     "Join it to a network with internet, or update from a laptop that has one."))
        else:
            findings.append(_finding("DNS", "ok", f"{host} → {', '.join(sorted(addrs)[:3])}"))
    except OSError as exc:
        addrs = set()
        findings.append(_finding("DNS", "problem", f"cannot resolve {host}: {exc}",
                                 "No working DNS: check the network the box is on (Ethernet, or WiFi it has joined)."))

    # 3. A route out.
    reachable = False
    if addrs:
        try:
            with socket.create_connection((host, 443), timeout=8):
                reachable = True
            findings.append(_finding("Connection", "ok", f"{host}:443 answers"))
        except OSError as exc:
            findings.append(_finding("Connection", "problem", f"cannot connect to {host}:443: {exc}",
                                     "The network has no way out, or blocks HTTPS. Try another network."))

    # 4. TLS, and the clock it depends on. A board with no RTC can boot years in the past,
    #    and then every certificate looks not-yet-valid.
    now = time.time()
    installed_on = None
    try:
        m = re.search(r"installed (\d{4}-\d{2}-\d{2})", (CODE / "VERSION").read_text())
        installed_on = time.mktime(time.strptime(m.group(1), "%Y-%m-%d")) if m else None
    except OSError:
        pass
    if reachable:
        try:
            with _tls_get(f"https://{host}/") as resp:
                server = parsedate_to_datetime(resp.headers["Date"]).timestamp()
            skew = now - server
            if abs(skew) > 600:
                findings.append(_finding("Clock", "problem", f"the box's clock is {abs(skew) / 3600:.1f} h "
                                         f"{'ahead' if skew > 0 else 'behind'}",
                                         "Set the time (timedatectl, or let NTP sync once online); "
                                         "certificates and update checks depend on it."))
            else:
                findings.append(_finding("Clock", "ok", f"within {abs(skew):.0f} s of {host}"))
            findings.append(_finding("TLS", "ok", f"https://{host}/ verifies"))
        except ssl.SSLError as exc:
            findings.append(_finding("TLS", "problem", f"certificate check failed: {exc}",
                                     "Usually the clock (see below) or missing ca-certificates; "
                                     "a network that intercepts HTTPS also does this."))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            findings.append(_finding("TLS", "problem", f"https://{host}/ failed: {exc}", ""))
    if installed_on and now < installed_on - 86400:
        findings.append(_finding("Clock", "problem",
                                 f"the clock says {time.strftime('%Y-%m-%d', time.gmtime(now))}, before this "
                                 f"hub was installed", "Set the time: timedatectl set-time 'YYYY-MM-DD HH:MM'."))

    # 5. GitHub's API, which the librarian and Caddy's release lookup use.
    if reachable:
        try:
            with _tls_get("https://api.github.com/rate_limit") as resp:
                core = json.load(resp)["resources"]["core"]
            if core["remaining"] == 0:
                wait = max(0, core["reset"] - now) / 60
                findings.append(_finding("GitHub API", "problem", "rate limit used up (60 requests an hour "
                                         "without a token)", f"Wait about {wait:.0f} min, or set a GitHub token "
                                         "in Library."))
            else:
                findings.append(_finding("GitHub API", "ok", f"{core['remaining']} of {core['limit']} requests left this hour"))
        except (OSError, ValueError, KeyError) as exc:
            findings.append(_finding("GitHub API", "warn", f"could not ask: {exc}", ""))

    # 6. The repository and branch exist and answer.
    if repo and reachable:
        out = run("git", "ls-remote", "--heads", repo, branch, timeout=60)
        if out.returncode != 0:
            findings.append(_finding("Repository", "problem", (out.stderr.strip().splitlines() or ["git ls-remote failed"])[-1],
                                     "Check the repository still exists and is public."))
        elif not out.stdout.strip():
            findings.append(_finding("Repository", "problem", f"{repo} has no branch {branch}",
                                     "The branch was renamed or deleted; reinstall naming the right one with --branch."))
        else:
            findings.append(_finding("Repository", "ok", f"{branch} is at {out.stdout.split()[0][:7]}"))

    # 7. Space for the clone, downloads and the install.
    for label, path, need in (("Space for the update cache", UPDATE_SRC.parent, 200),
                              ("Space on the system card", Path("/"), 300)):
        path = path if path.exists() else path.parent
        free = shutil.disk_usage(path).free >> 20
        findings.append(_finding(label, "ok" if free >= need else "problem", f"{free} MB free in {path}",
                                 "" if free >= need else f"Free at least {need} MB (old books in Library, saved work)."))

    # 8. The cached clone.
    if (UPDATE_SRC / ".git").is_dir():
        locks = [p.name for p in (UPDATE_SRC / ".git").glob("*.lock")]
        if locks:
            findings.append(_finding("Update cache", "problem", f"stale git lock: {', '.join(locks)}",
                                     "A check was interrupted. Clear the cache below and check again."))
        else:
            status = run("git", "-C", str(UPDATE_SRC), "status", "--porcelain", timeout=60)
            remote = run("git", "-C", str(UPDATE_SRC), "remote", "get-url", "origin").stdout.strip()
            if status.returncode != 0:
                findings.append(_finding("Update cache", "problem", "the cached clone is damaged",
                                         "Clear the cache below and check again."))
            elif repo and remote != repo:
                findings.append(_finding("Update cache", "warn", f"cloned from {remote}, not {repo}",
                                         "The next check switches it over."))
            else:
                findings.append(_finding("Update cache", "ok", "the cached clone is intact"))
    else:
        findings.append(_finding("Update cache", "ok", "empty: the next check clones afresh"))

    # 9. Package sources: a repository that fails verification fails every apt-get update.
    out = run("apt-get", "update", "-q", timeout=300)
    text = out.stdout + out.stderr
    bad = sorted(set(re.findall(r"(?:Err|E|W):\S*\s+(https?://\S+)", text)))
    if out.returncode == 0 and not re.search(r"^(E|Err):", text, re.M):
        findings.append(_finding("Package sources", "ok", "apt-get update succeeds"))
    else:
        lines = [l for l in text.splitlines() if re.match(r"^(E|Err|W):", l)][:3]
        findings.append(_finding("Package sources", "problem", " | ".join(lines)[:400] or "apt-get update failed",
                                 "A repository that fails verification (Caddy's did, on 2026-10-01) blocks "
                                 "every update. install.sh copes with Caddy's; for others, remove the list "
                                 "under /etc/apt/sources.list.d/ from the terminal."))

    # 10. The last fetch, as the page last saw it.
    try:
        last = json.loads(UPDATE_STATE.read_text())
        failed = [c for c in last.get("checks", []) if not c.get("ok") and not c.get("warn")]
        if failed:
            findings.append(_finding("Last update check", "problem",
                                     "; ".join(f"{c['name']}: {c['detail']}" for c in failed)[:400],
                                     "Fix the cause, then check for updates again."))
    except (OSError, ValueError):
        pass
    findings += scheduled_findings()
    findings += kits_findings()
    return findings


def scheduled_findings(now=None):
    """Whether the timer's runs actually reach the hub's own update (library/last-run.json, kept
    by the librarian): a check that the box *can* update says nothing about whether it *does*."""
    now = now or time.time()
    try:
        rec = json.loads((STATE / "library" / "last-run.json").read_text())
    except (OSError, ValueError):
        return [_finding("Scheduled updates", "warn", "no run of the librarian's timer recorded yet",
                         "It runs hourly; if this stays, see: journalctl -u irate-box-librarian -n 80")]
    out = []
    last = rec.get("last_scheduled") or {}
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last.get("started") or 0))
    if last and last.get("finished") is None:
        pass  # running now
    elif last and last.get("error"):
        out.append(_finding("Scheduled updates", "problem", f"the timer's last run ({when}) crashed: {last['error']}"
                            + (f" in {last['where']}" if last.get("where") else ""),
                            "journalctl -u irate-box-librarian -n 80 has the whole of it; until it is fixed, nothing "
                            "after the crash runs (the books', the toolkits', the hub's own update)."))
    hub_at = rec.get("hub_stage_at")
    if not hub_at or now - hub_at > 3 * 3600:
        out.append(_finding("Scheduled updates", "problem",
                            "the timer's runs have not reached the hub's own update stage "
                            + (f"since {time.strftime('%Y-%m-%d %H:%M', time.localtime(hub_at))}" if hub_at else "yet"),
                            "See the librarian's last runs: journalctl -u irate-box-librarian -n 80."))
    if not out:
        out.append(_finding("Scheduled updates", "ok", f"the timer's last run ({when}) finished and reached the hub's update"
                            + ("" if last.get("ok") else f"; some sources had errors: {'; '.join(last.get('errors', [])[:3])}")))
    return out


def kits_findings(now=None):
    """The toolkits (toolkits-plan §8): the cache's files against their manifests, its folder,
    its size against the budget the page set, and kits left installed."""
    now = now or time.time()
    out = []
    problems = kits.verify()
    if problems:
        out.append(_finding("Toolkits' cache", "problem", "; ".join(problems[:5]),
                            "Fetch the kit again while online (Library → Toolkits); nothing is installed from a cache that fails this."))
    elif kits.MANIFESTS.is_dir() and any(kits.MANIFESTS.glob("*.json")):
        out.append(_finding("Toolkits' cache", "ok", f"every file matches its manifest ({kits.pool_bytes() >> 20} MB)"))
    try:
        budget = int(json.loads((STATE / "library" / "toolkits.json").read_text()).get("budget_mb", 500))
    except (OSError, ValueError, TypeError, AttributeError):
        budget = 500
    if kits.pool_bytes() > budget << 20:
        out.append(_finding("Toolkits' cache", "warn", f"{kits.pool_bytes() >> 20} MB, over its {budget} MB budget",
                            "Raise the budget, or stop keeping a kit current."))
    # Kits kept fresher (the security kit: from outside the box's control), and their data:
    # a warning after a week, a problem after a month (toolkits-plan §1a).
    def age(what, when, fix):
        days = (now - when) / 86400
        if days > 30:
            out.append(_finding("Security tools' freshness", "problem", f"{what} is {int(days)} days old", fix))
        elif days > 7:
            out.append(_finding("Security tools' freshness", "warn", f"{what} is {int(days)} days old", fix))
    for kid, k in kits.definitions().items():
        man = kits.manifest(kid)
        if k.get("refresh_hours") and man:
            age(f"the {kid} kit's cache", man["fetched"], "Connect the box to the internet: the librarian refreshes it daily.")
        if "debsecan" in k.get("feeds", []) and man:
            try:
                codename = next(l.split("=", 1)[1].strip().strip('"') for l in open("/etc/os-release") if l.startswith("VERSION_CODENAME="))
                feed = STATE / "library" / "debsecan" / "release" / "1" / codename
                if feed.is_file():
                    age("debsecan's data (Debian's security tracker)", feed.stat().st_mtime,
                        "Connect the box to the internet: the librarian refreshes it daily.")
            except (OSError, StopIteration):
                pass
    for kit, v in kits.installed_state().items():
        days = (now - v.get("at", now)) / 86400
        if kit == "debug" and days > 7:
            out.append(_finding("Toolkits installed", "warn", f"the debug kit has been installed for {int(days)} days: "
                                "it can read any process's memory and every packet", "Remove it under Library → Toolkits."))
        elif v.get("remove_at") is None and kit != "build":
            out.append(_finding("Toolkits installed", "warn", f"{kit} is installed with no removal time",
                                "Set one under Library → Toolkits, or remove it."))
    return out


def update_doctor(req):
    findings = doctor()
    safeio.write(DOCTOR_STATE, json.dumps({"at": time.time(), "findings": findings}, indent=2))
    problems = [f for f in findings if f["status"] == "problem"]
    if not problems:
        return "update doctor: no problems found"
    return f"update doctor: {len(problems)} problem{'s' if len(problems) != 1 else ''}, first: {problems[0]['check']}"


def update_clear_cache(req):
    """Remove the cached clone and downloads; the next check starts afresh."""
    for path in (UPDATE_SRC, DOWNLOADS):
        if path.exists():
            shutil.rmtree(path)
    UPDATE_STATE.unlink(missing_ok=True)
    return "update cache cleared"


# --- the security page -----------------------------------------------------------------

def _write_security(data):
    safeio.write(SECURITY_STATE, json.dumps(data, indent=2))


def security_scan(req):
    data = security.scan()
    _write_security(data)
    bad = [f for f in data["findings"] if f["status"] == "problem"]
    return f"security scan: {len(bad)} to fix" if bad else "security scan: nothing to fix"


def security_audit(req):
    report = secdoctor.audit()
    secdoctor.write_report(report, AUDIT_STATE)
    c = report["counts"]
    return f"security doctor: {c['problem']} problem(s), {c['warn']} warning(s)"


def security_deep_audit(req):
    """debian-cis and Lynis (deepaudit.py), then the regular audit, so the report shows them."""
    from irate_box.root import deepaudit
    line = deepaudit.deep(log=lambda *_: None)
    security_audit(req)
    return line


def security_fix(req):
    choice = str(req.get("choice", ""))
    if not re.fullmatch(r"[a-z-]+(:[A-Za-z0-9@_][A-Za-z0-9@._-]*)?", choice):
        raise ValueError("not a Security page choice")
    # Only what root's own last scan offered (F14): a forged request could otherwise switch off
    # any unit not on the protected list (a firewall, auditd, a getty). The scan is root's file,
    # read only if it still is; every fix ends with a new one, so the page's buttons stay valid.
    try:
        offered = {a["choice"] for f in json.loads(safeio.read_own(SECURITY_STATE)).get("findings", [])
                   for a in f.get("actions", [])}
    except (OSError, ValueError, KeyError, TypeError):
        offered = set()
    if choice not in offered:
        raise ValueError("the Security page did not offer that: scan again, then choose from what it shows")
    if choice == "security-updates":
        safeio.write(SECURITY_LOG, "")
    try:
        msg = security.fix(choice, SECURITY_LOG)
    finally:
        _write_security(security.scan())
    return msg


# --- books on a USB stick -----------------------------------------------------------------

def _write_usb(data):
    safeio.write(USB_STATE, json.dumps(data, indent=2))


def _tls_answer(fn):
    """openssl's and nginx's refusals (RuntimeError in tls.py) as a failed answer, not a crash of
    the helper: only ValueError and OSError are answered as failures by the loop below."""
    def run_it(req):
        try:
            return fn(req)
        except RuntimeError as exc:
            raise ValueError(str(exc))
    run_it.__name__ = fn.__name__
    return run_it


@_tls_answer
def tls_make(req):
    """HTTPS (step 15): the box's own CA and its certificate. A second CA only when asked: every
    device that installed the first would have to install the new one."""
    from irate_box.root import tls
    if tls.status().get("set_up") and req.get("again") is not True:
        return "HTTPS is already set up: making a new CA means every device installs it again (ask for that explicitly)"
    rec = tls.make_ca()
    return f"made the box's CA ({rec['ca']['fingerprint'][:23]}…) and its certificate"


@_tls_answer
def tls_renew(req):
    from irate_box.root import tls
    return tls.renew()


@_tls_answer
def tls_import(req):
    """The owner's own certificate (stage 4): the chain and key the hub staged, read without
    following a link, checked by tls.import_own, and the staged copies removed either way."""
    from irate_box.root import tls
    staged = STATE / "tls-import"
    try:
        chain = safeio.read_request(staged / "chain.pem", 64 << 10)
        key = safeio.read_request(staged / "key.pem", 16 << 10)
    except OSError as exc:
        return f"nothing to import ({exc})"
    finally:
        for f in ("chain.pem", "key.pem"):
            (staged / f).unlink(missing_ok=True)
    try:
        return tls.import_own(chain, key)
    except ValueError as exc:
        raise ValueError(f"not used: {exc}")


@_tls_answer
def tls_box(req):
    from irate_box.root import tls
    return tls.use_box_own()


@_tls_answer
def tls_admin_only(req):
    from irate_box.root import tls
    return tls.admin_only(req.get("on") is True)


@_tls_answer
def tls_switch(req):
    """HTTPS on or off at the front, the CA and certificate kept either way."""
    from irate_box.root import tls
    if not tls.status().get("set_up"):
        return "HTTPS is not set up yet: make the box's certificate first"
    return tls.front(req.get("on") is True)


def usb_scan(req):
    data = usbstick.scan()
    _write_usb(data)
    books = sum(len(d["zims"]) for d in data["devices"])
    return (f"{len(data['devices'])} stick{'s' if len(data['devices']) != 1 else ''}, "
            f"{books} book{'s' if books != 1 else ''}") if data["devices"] else "no USB stick found"


def _min_free():
    try:
        policy = json.loads((STATE / "library" / "sources.json").read_text()).get("policy", {})
        return int(policy.get("min_free_mb", 512)) << 20
    except (OSError, ValueError, TypeError):
        return 512 << 20


def usb_import(req):
    device, file = str(req.get("device", "")), str(req.get("file", ""))
    with Progress("usb-import", 1, path=USB_PROGRESS) as progress:
        progress.step(f"Copying {Path(file).name} from the stick")
        name = usbstick.import_zim(device, file, ZIM_DIR, HUB_USER, _min_free(),
                                   [str(CODE / "irate-box"), "librarian"], STATE, progress.bytes)
    return f"{name}: added to the library"


def usb_export(req):
    device, book = str(req.get("device", "")), str(req.get("book", ""))
    with Progress("usb-export", 1, path=USB_PROGRESS) as progress:
        progress.step(f"Copying {book} to the stick")
        where = usbstick.export_zim(device, book, ZIM_DIR, progress.bytes)
    try:
        _write_usb(usbstick.scan())
    except (ValueError, OSError):
        pass
    return f"{book}: copied to the stick as {where}; it is safe to unplug"


def usb_kit_export(req):
    device, kit = str(req.get("device", "")), str(req.get("kit", ""))
    if not kits.ID_RE.match(kit):
        raise ValueError("kit: a toolkit's id")
    try:
        with Progress("usb-export", 1, path=USB_PROGRESS) as progress:
            progress.step(f"Copying the {kit} kit to the stick")
            line = usbstick.export_kit(device, kit, progress.bytes)
        try:
            _write_usb(usbstick.scan())
        except (ValueError, OSError):
            pass
        return f"{line}; it is safe to unplug"
    finally:
        _kits_status()


def usb_kit_import(req):
    device, kit = str(req.get("device", "")), str(req.get("kit", ""))
    if not kits.ID_RE.match(kit):
        raise ValueError("kit: a toolkit's id")
    try:
        budget = int(json.loads((STATE / "library" / "toolkits.json").read_text()).get("budget_mb", 500))
    except (OSError, ValueError, TypeError, AttributeError):
        budget = 500
    try:
        with Progress("usb-import", 1, path=USB_PROGRESS) as progress:
            progress.step(f"Checking and copying the {kit} kit from the stick")
            return usbstick.import_kit(device, kit, budget, progress.bytes)
    finally:
        _kits_status()


# --- app bundles -------------------------------------------------------------------
# The librarian (as the hub user) downloads a bundle into $STATE/library/apps/ and checks it;
# this checks it again -- the hub wrote that file -- and swaps it in, keeping the previous
# copy as .<app>.prev for app-rollback. Where each app goes, what proves a bundle whole and
# which unit to restart after come from its manifest (apps.d/, "install").

APP_STAGING = STATE / "library" / "apps"
APP_MAX_BYTES = 400 << 20
APPS = manifests.installable(MANIFESTS)


def _app_dir(app):
    return manifests.install_dir(APPS[app])


def _has_needed(names, pattern):
    """The manifest's "needs": a file, or a glob such as *.html, present at the top level."""
    import fnmatch
    return any(fnmatch.fnmatchcase(n, pattern) for n in names) if any(c in pattern for c in "*?[") else pattern in names


def _checked_bundle(app, zip_path):
    """The bundle's metadata, or ValueError: the same rules as librarian.check_bundle."""
    if app not in APPS:
        raise ValueError(f"{app} is not an app the hub installs")
    path = Path(zip_path)  # root's own copy (_take_staged), not the hub's staged file
    if path.is_symlink() or not path.is_file():
        raise ValueError("the bundle must be a file the librarian staged")
    try:
        with zipfile.ZipFile(path) as zf:
            names, total = set(), 0
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in info.filename:
                    raise ValueError(f"unsafe path in the bundle: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"symlink in the bundle: {info.filename}")
                total += info.file_size
                names.add(info.filename)
            if total > APP_MAX_BYTES:
                raise ValueError("the bundle is too large")
            meta = json.loads(zf.read("irate-box-bundle.json"))
            if meta.get("app") != app or not _has_needed(names, APPS[app]["install"]["needs"]):
                raise ValueError(f"not a bundle of {app}")
            return meta
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid bundle: {exc}")


# Root's copy of a bundle while it is checked and extracted (F7): the staged zip is in the hub's
# folder, where the hub could swap it between the check and the extraction.
APP_TAKEN = Path(os.environ.get("HUB_APP_TAKEN", "/var/cache/irate-box/app-install"))


def _staged_dir_fd(zip_path):
    """The staged bundle's folder, opened without following a link, and only if the hub (this
    user, when not root) owns it: a folder swapped for a link, or for one of root's, is refused."""
    path = Path(zip_path)
    staging = APP_STAGING.resolve()
    if not path.resolve().is_relative_to(staging):
        raise ValueError("the bundle must be a file the librarian staged")
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    owner = pwd.getpwnam(HUB_USER).pw_uid if os.geteuid() == 0 else os.geteuid()
    if os.fstat(dfd).st_uid != owner:
        os.close(dfd)
        raise ValueError("the bundle's folder is not the librarian's")
    return dfd


def _take_staged(zip_path):
    """A root-owned copy of the staged bundle, read once through an O_NOFOLLOW fd (F7)."""
    name = Path(zip_path).name
    dfd = _staged_dir_fd(zip_path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except OSError:
        raise ValueError("the bundle must be a file the librarian staged")
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as src:
        st = os.fstat(src.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > APP_MAX_BYTES:
            raise ValueError("the bundle must be a plain file, and not too large")
        APP_TAKEN.mkdir(parents=True, exist_ok=True)
        os.chmod(APP_TAKEN, 0o700)
        copy = APP_TAKEN / f"{secrets.token_hex(8)}.zip"
        with open(copy, "xb") as out:
            # Bounded: the hub could still be writing to the file it staged.
            while chunk := src.read(1 << 20):
                out.write(chunk)
                if out.tell() > APP_MAX_BYTES:
                    break
        if copy.stat().st_size > APP_MAX_BYTES:
            copy.unlink()
            raise ValueError("the bundle is too large")
    return copy


def _drop_staged(zip_path):
    """The hub's staged zip removed, through its folder's fd (never a link's target)."""
    try:
        dfd = _staged_dir_fd(zip_path)
    except (ValueError, OSError):
        return
    try:
        os.unlink(Path(zip_path).name, dir_fd=dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def install_app(app, zip_path):
    taken = _take_staged(zip_path)
    try:
        return _install_taken(app, taken, zip_path)
    finally:
        taken.unlink(missing_ok=True)


def _install_taken(app, zip_path, staged):
    meta = _checked_bundle(app, zip_path)
    target = _app_dir(app)
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.parent / f".{target.name}.new"
    prev = target.parent / f".{target.name}.prev"
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(new)
        # Root-owned and world-readable, whatever modes the zip carried.
        as_root = os.geteuid() == 0
        for root, dirs, files in os.walk(new):
            if as_root:
                os.chown(root, 0, 0)
            os.chmod(root, 0o755)
            for name in files:
                p = os.path.join(root, name)
                if as_root:
                    os.chown(p, 0, 0)
                os.chmod(p, 0o644)
        if target.exists():
            shutil.rmtree(prev, ignore_errors=True)
            os.rename(target, prev)
        os.rename(new, target)
    finally:
        shutil.rmtree(new, ignore_errors=True)
    _drop_staged(staged)
    if APPS[app]["install"].get("restart"):
        run("systemctl", "try-restart", APPS[app]["install"]["restart"])
    return f"{app}: installed {str(meta.get('commit', ''))[:7]} ({meta.get('ref')}, built {str(meta.get('built', ''))[:10]})"


def app_install(req):
    return install_app(str(req.get("app", "")), str(req.get("zip", "")))


def app_rollback(req):
    app = str(req.get("app", ""))
    if app not in APPS:
        raise ValueError(f"{app} is not an app the hub installs")
    target = _app_dir(app)
    prev = target.parent / f".{target.name}.prev"
    if not prev.is_dir():
        raise ValueError(f"no previous {app} to go back to")
    swap = target.parent / f".{target.name}.swap"
    shutil.rmtree(swap, ignore_errors=True)
    if target.exists():
        os.rename(target, swap)
    os.rename(prev, target)
    if swap.exists():
        os.rename(swap, prev)  # so a second roll back goes forward again
    if APPS[app]["install"].get("restart"):
        run("systemctl", "try-restart", APPS[app]["install"]["restart"])
    meta = json.loads((target / "irate-box-bundle.json").read_text()) if (target / "irate-box-bundle.json").exists() else {}
    return f"{app}: rolled back to {str(meta.get('commit', 'the previous build'))[:7]}"


# --- the box doctor ----------------------------------------------------------------------

HEALTH_STATE = CONTROL / "health.json"


def _write_health():
    os.environ["HUB_CONTROL_RUNNING"] = "1"  # the queue it would report is the one being answered
    data = health.scan()
    safeio.write(HEALTH_STATE, json.dumps(data, indent=2))
    return data


def health_scan(req):
    data = _write_health()
    bad = [f for f in data["findings"] if f["status"] == "problem"]
    return f"doctor: {len(bad)} problem{'s' if len(bad) != 1 else ''}" if bad else "doctor: nothing wrong found"


def rerun_install():
    """install.sh again, as it was last run, from a copy of the installed code (as addon())."""
    opts = _install_options()
    shutil.rmtree(ADDON_SRC, ignore_errors=True)
    ADDON_SRC.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CODE, ADDON_SRC, symlinks=True)
    state = _read_update_state()
    try:
        _run_install(ADDON_SRC, opts, "repair", state.get("install_steps"))
    finally:
        shutil.rmtree(ADDON_SRC, ignore_errors=True)
    return "the installer ran again and finished"


def health_fix(req):
    choice = str(req.get("choice", ""))
    try:
        if choice == "rerun-install":
            msg = rerun_install()
        elif choice == "net-scan":
            msg = net_scan({})
        else:
            msg = health.fix(choice)
    finally:
        try:
            _write_health()
        except Exception:  # the repair's own answer matters more than a fresh report
            pass
    return msg


# --- network: inventory and the uplink watchdog ------------------------------------------

IFACE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$")  # no leading "-": it would be an option (F14)


def net_scan(req):
    iface = req.get("iface") or None
    if iface is not None and not IFACE_RE.match(str(iface)):
        raise ValueError("not an interface name")
    inv = netinv.scan(iface)
    netinv.write(inv, CONTROL / "netinv.json")
    radios = len(inv["radios"])
    return (f"{radios} radio{'s' if radios != 1 else ''}"
            + (f", link {inv['uplink']['iface']} run by {inv['uplink']['backend']}" if inv["uplink"].get("iface") else ", no link"))


# --- the hotspot (item 2: root/ap.py does the work, hub/apmode.py the plan) ------------------------

AP_STATUS = CONTROL / "ap.json"      # the hub's copy: the plan, whether it's up, the tries


def _ap_settings():
    """The owner's security choice for the hotspot (the hub's hotspot.json), checked again here."""
    from irate_box.hub import hotspot
    try:
        raw = json.loads((STATE / "hotspot.json").read_text())
    except (OSError, ValueError):
        raw = {}
    try:
        return hotspot.validate(raw, hotspot.capabilities())
    except ValueError:
        return dict(hotspot.DEFAULT)


def _ap_owner(req):
    """The owner's choices for the hotspot, checked: a radio by name, a band, a channel, and whether to
    give the box's WiFi link up for it (only where a radio can't do both)."""
    out = {}
    if req.get("radio"):
        if not IFACE_RE.match(str(req["radio"])):
            raise ValueError("not an interface name")
        out["radio"] = str(req["radio"])
    if req.get("band") in ("2.4 GHz", "5 GHz"):
        out["band"] = req["band"]
    if req.get("channel") is not None:
        if type(req["channel"]) is not int or not 1 <= req["channel"] <= 196:
            raise ValueError("channel: a channel number")
        out["channel"] = req["channel"]
    if req.get("take_radio") is True:
        out["take_radio"] = True
    return out


def _ap_record_status(plan=None, note=""):
    rec = ap._load(ap.RECORD, {})
    safeio.write(AP_STATUS, json.dumps({"up": bool(rec.get("up")), "plan": rec.get("plan") if rec.get("up") else plan,
                                        "confirmed": rec.get("confirmed", True), "since": rec.get("since"),
                                        "owner": rec.get("owner") or {}, "tried": ap._load(ap.TRIED, {}),
                                        "note": note, "at": time.time()}))


def _ap_inventory():
    inv = netinv.scan()
    netinv.write(inv, CONTROL / "netinv.json")
    return inv


def _ap_failed(exc):
    """A step that failed (ap.start has undone what it did): said on the Network page and to the hub."""
    note = f"the hotspot didn't start: {exc}"
    _ap_record_status(None, note)
    return ValueError(note)


def ap_on(req):
    try:
        plan = ap.start(run, _ap_inventory(), _ap_settings(), _ap_owner(req))
    except RuntimeError as exc:
        raise _ap_failed(exc) from exc
    if plan.get("needs_choice") or plan["kind"] == "none":
        _ap_record_status(plan, plan["text"])
        return plan["text"] + (f" ({plan['why']})" if plan.get("why") else "")
    note = f"up on {ap.ap_iface(plan)}, channel {plan['channel']}: {plan['text']}"
    if plan.get("drops_uplink"):
        note += f" Your WiFi link is off: open the hub from the hotspot within {ap.DEADMAN // 60} minutes and confirm, or it comes back by itself."
    _ap_record_status(plan, note)
    return note


def ap_off(req):
    note = ap.stop(run)
    _ap_record_status(None, note)
    return note


def ap_try(req):
    try:
        worked, plan = ap.try_own_channel(run, _ap_inventory(), _ap_settings())
    except RuntimeError as exc:
        raise _ap_failed(exc) from exc
    if worked is None:
        note = "nothing to try: " + plan["text"]
    else:
        note = ("it holds a channel of its own beside your WiFi: guests won't notice your WiFi roam" if worked
                else "it can't hold a channel of its own here, so it follows your WiFi's channel")
    _ap_record_status(plan, note)
    return note


def ap_confirm(req):
    note = ap.confirm(run)
    _ap_record_status(None, note)
    return note


def _uplink_running():
    if run("systemctl", "is-active", "--quiet", "irate-box-uplink.service").returncode:
        run("systemctl", "enable", "--now", "irate-box-uplink.service")


def uplink_set(req):
    s = uplink.load_settings()
    new = dict(req.get("settings") or {})
    new.setdefault("hold_until", s.get("hold_until", 0))
    s = uplink.save_settings(new)
    _uplink_running()
    return (f"Uplink: {s['eagerness']}, {s['forgiveness']}" + (", custom values" if s["overrides"] else "")
            + f", watching {s['iface']}. Acting again in {uplink.COMMON['pause_after_change'] // 60} min at the earliest.")


def uplink_hold(req):
    minutes = req.get("minutes")
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise ValueError("minutes must be 0 to 1440")
    s = uplink.load_settings()
    s["hold_until"] = time.time() + minutes * 60 if minutes else 0
    uplink.save_settings(s)
    return f"Repairs held for {minutes} min." if minutes else "Hold ended: repairs as set."


def uplink_profile(req):
    if type(req.get("on")) is not bool:
        raise ValueError("on must be true or false")
    msg = uplink.profile(req["on"])
    try:
        netinv.write(netinv.scan(), CONTROL / "netinv.json")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return msg


def _kits_status():
    safeio.write(CONTROL / "kits.json", json.dumps(kits.status()))


def _kit_hours(req):
    hours = req.get("hours", 24)
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise ValueError("hours: 1 to 8760, or null for never")
    return hours


def _kit_req(fn):
    """A toolkit action, and control/kits.json written after it, whatever happened."""
    def action(req):
        kit = req.get("kit")
        if fn is kits.define:
            try:
                return kits.define(kit)
            finally:
                _kits_status()
        if fn is not kits.expire and not (isinstance(kit, str) and kits.ID_RE.match(kit)):
            raise ValueError("kit: a toolkit's id")
        try:
            if fn is kits.set_extra:
                return kits.set_extra(kit, req.get("packages"))
            if fn is kits.fetch:
                budget = req.get("budget_mb", 500)
                if type(budget) is not int or not 10 <= budget <= 1 << 16:
                    raise ValueError("budget_mb: 10 to 65536")
                return kits.fetch(kit, budget_mb=budget, log=lambda *_: None)
            if fn is kits.install:
                return kits.install(kit, _kit_hours(req), log=lambda *_: None)
            if fn is kits.set_removal:
                return kits.set_removal(kit, _kit_hours(req))
            if fn is kits.expire:
                return "; ".join(kits.expire()) or "nothing due"
            return fn(kit, log=lambda *_: None)
        finally:
            _kits_status()
    return action


ACTIONS = {"service": service, "password": password,
           "update-check": update_check, "update-fetch": update_fetch, "update-install": update_install,
           "update-force-install": update_force_install,
           "update-doctor": update_doctor, "update-clear-cache": update_clear_cache,
           "security-scan": security_scan, "security-audit": security_audit, "security-deep-audit": security_deep_audit, "security-fix": security_fix, "addon": addon,
           "tls-make": tls_make, "tls-renew": tls_renew, "tls-switch": tls_switch, "tls-import": tls_import, "tls-box": tls_box, "tls-admin-only": tls_admin_only, "usb-scan": usb_scan, "usb-import": usb_import, "usb-export": usb_export,
           "usb-kit-import": usb_kit_import, "usb-kit-export": usb_kit_export,
           "app-install": app_install, "app-rollback": app_rollback,
           "access": access_set, "admin-login": admin_login, "ap-on": ap_on, "ap-off": ap_off, "ap-try": ap_try, "ap-confirm": ap_confirm, "offline-kit": offline_kit, "health-scan": health_scan, "health-fix": health_fix, "net-scan": net_scan, "uplink-set": uplink_set, "uplink-hold": uplink_hold, "uplink-profile": uplink_profile,
           "kit-fetch": _kit_req(kits.fetch), "kit-install": _kit_req(kits.install), "kit-remove": _kit_req(kits.remove),
           "kit-keep": _kit_req(kits.set_removal), "kit-rollback": _kit_req(kits.rollback),
           "kit-define": _kit_req(kits.define), "kit-undefine": _kit_req(kits.undefine), "kit-extra": _kit_req(kits.set_extra), "kit-expire": _kit_req(kits.expire), "kit-status": lambda req: (_kits_status(), "ok")[1]}


def answer(rid, ok, message):
    safeio.mkdir(RESULTS)
    safeio.write(RESULTS / f"{rid}.json", json.dumps({"id": rid, "ok": ok, "message": message, "at": time.time()}))
    # Keep the last 50 answers; the page only ever looks at recent ones.
    old = sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-50]
    for p in old:
        p.unlink(missing_ok=True)


def main():
    # A request that arrives while this runs is picked up by the same loop.
    for _ in range(100):
        pending = sorted(REQUESTS.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not pending:
            return 0
        for path in pending:
            rid = path.stem if ID_RE.match(path.stem) else "invalid"
            what = None
            try:
                text = safeio.read_request(path)  # never a link or a FIFO (F3)
                # Gone before anything else can fail on it: a request this process cannot handle
                # must never be found again by the next one, or the path unit restarts the helper
                # for ever and nothing queued behind it is served (stance review 2026-10-08, N6).
                path.unlink(missing_ok=True)
                req = json.loads(text)
                if not isinstance(req, dict):
                    raise ValueError("not a request")
                what = req.get("action")
                action = ACTIONS.get(what)
                if not action:
                    raise ValueError("unknown action")
                answer(rid, True, action(req))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                path.unlink(missing_ok=True)
                answer(rid, False, str(exc))
            except Exception as exc:  # a handler's own bug: answered, and the queue goes on
                path.unlink(missing_ok=True)
                answer(rid, False, f"{type(exc).__name__}: {exc}")
            if what in UPDATES:
                # The code on disk is new now; this process still has the old in memory. Stop, and
                # the path unit starts a fresh helper for what is still queued (the Lyra,
                # 2026-10-07: a kit fetched just after an update ran with the old kits.py).
                return 0
    return 0


UPDATES = ("update-install", "update-force-install")


if __name__ == "__main__":
    # NetworkManager's dispatcher (root/ap.py's hook): a link came up; a following hotspot moves.
    if len(sys.argv) == 3 and sys.argv[1] == "ap-follow" and IFACE_RE.match(sys.argv[2]):
        if os.geteuid() != 0:
            sys.exit("run as root")
        rec = ap._load(ap.RECORD, {})
        if not rec.get("up") or not (rec.get("plan") or {}).get("follows_uplink"):
            sys.exit(0)   # every link change calls this (ap0's own too): leave the last note alone
        try:
            note = ap.follow(run, _ap_inventory(), _ap_settings(), sys.argv[2])
            if note.startswith("moved"):
                _ap_record_status(None, note)
        except RuntimeError as exc:
            sys.exit(str(_ap_failed(exc)))
        sys.exit(0)
    # The boot unit (root/ap.py): the hotspot as it was before the reboot.
    if sys.argv[1:] == ["ap-boot"]:
        if os.geteuid() != 0:
            sys.exit("run as root")
        try:
            _ap_record_status(None, ap.boot(run, _ap_inventory(), _ap_settings()))
        except RuntimeError as exc:
            sys.exit(str(_ap_failed(exc)))
        sys.exit(0)
    # The dead-man timer: the owner didn't confirm a hotspot that took the box's own link.
    if sys.argv[1:] == ["ap-revert"]:
        if os.geteuid() != 0:
            sys.exit("run as root")
        rec = ap._load(ap.RECORD, {})
        if rec.get("up") and not rec.get("confirmed"):
            _ap_record_status(None, ap.stop(run) + ": not confirmed in time, so the box's WiFi link is back")
        sys.exit(0)
    if sys.argv[1:] == ["reset-password"]:
        if os.geteuid() != 0:
            sys.exit("run as root: sudo ./irate-box hub_control reset-password")
        print(reset_password())
        sys.exit(0)
    if sys.argv[1:] == ["syncthing-gui-password"]:
        # For install.sh: the password on stdin (F9).
        sys.exit(0 if syncthing_gui_password(sys.stdin.readline().rstrip("\n")) else 1)
    if sys.argv[1:] == ["access-off-units"]:
        # For install.sh: the services of the apps switched off, which it leaves stopped.
        state = access.read(ACCESS_FILE)
        for m in MANIFESTS:
            st = m.get("status", {})
            if state.get(m["id"]) == "off" and st.get("control") and st.get("unit"):
                print(st["unit"].replace("@hub.", f"@{HUB_USER}."))
        sys.exit(0)
    if sys.argv[1:] == ["access-install"]:
        if os.geteuid() != 0:
            sys.exit("run as root")
        try:
            print(access_install())
        except (ValueError, OSError) as exc:
            sys.exit(f"access-install: {exc}")
        sys.exit(0)
    if len(sys.argv) == 4 and sys.argv[1] == "install-app":
        if os.geteuid() != 0:
            sys.exit("run as root")
        try:
            print(install_app(sys.argv[2], sys.argv[3]))
        except (ValueError, OSError) as exc:
            sys.exit(f"install-app: {exc}")
        sys.exit(0)
    sys.exit(main())
