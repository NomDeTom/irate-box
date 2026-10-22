#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root helper for the admin page: the few things the unprivileged hub cannot do itself.

Of the three doctors (docs/dev/doctors.md), the Updates doctor lives in this file: its checks are
updates_doctor_findings(), under "the Updates doctor" below, and its action is update_doctor. The Box
doctor is health.py and the Security doctor is secdoctor.py; this file only runs them and writes their reports.

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
      The Updates doctor (updates_doctor_findings, in this file), for when a check fails: look at
      everything an update needs and write a plain report, each finding with what to do, to control/doctor.json.
  {"id": ..., "action": "update-clear-cache"}
      Remove the cached clone and downloads, so the next check starts afresh.
  {"id": ..., "action": "update-poll-target"[, "repo": "<https url>"]}
      Ask the recorded repository (or the one named) which branch it serves by default
      (git ls-remote --symref), and say whether that is the branch this box follows. Changes nothing.
  {"id": ..., "action": "update-set-source", "repo": "<https url>", "branch": "<name>", "confirmed": true}
      Point the box's updates at another repository or branch (a fork): checked as readable, https
      only, the branch listed by the remote; recorded in install-options; the update cache cleared.
      A security-policy action: refused when policy is console-only.
  {"id": ..., "action": "share-set", "level": "off"|"users-web"|"sheet-web"|"sheet-all"|"open"}
      Guests' onward internet (share.py): the one ruleset with the hotspot firewall (firewall.apply_all), IPv4
      forwarding and the guests' resolver made to agree; off takes it all back. The level and the
      containment in control/share.json.
  {"id": ..., "action": "pkg-check", "package": "meshtasticd"?} | "pkg-fetch" | "pkg-install" (+ "version") | "pkg-rollback"
      | "pkg-settings" (+ "channel", "mode": watch|auto|aged, "days", "every": 0|6|24|168 hours)
      Packages from their makers' channels (pkgwatch.py): checked, cached, installed, rolled back; the
      owner's channel and mode (on an image with its own tool, mpwrd-menu, its channel written its way).
  {"id": ..., "action": "share-allow", "ip": "192.168.4.x"}
      Let one device out, at a level that lets devices out one by one (the hub asks).
  {"id": ..., "action": "update-signing", "level": "off"|"github"|"tags", "signers": "<allowed_signers>"}
      How far an update must be vouched for (signing.py): kept in /etc/hub/update-signing.json,
      root's; what /admin may show (the level, each key's name and type) in control/.
  {"id": ..., "action": "addon", "addon": "<an apps.d add-on>", "on": true|false}
      Rerun install.sh from a copy of the installed code with that add-on's --with-* option
      added, or taken out with --remove; output and progress as for update-install.
  {"id": ..., "action": "usb-scan"} / "usb-import" (device, file) / "usb-export" (device, book)
      Books to and from a USB stick (usbstick.py): what is on each stick to control/usb.json;
      a book copied in (then the library rebuilt) or out, with progress in usb-progress.json.
  {"id": ..., "action": "security-scan"}
      What the box exposes and how it is set up (security.py): to control/security.json.
  {"id": ..., "action": "security-audit"}
      The Security doctor (secdoctor.py): a read-only audit of how the system is set up, to
      control/security-audit.json. Changes nothing but that file.
  {"id": ..., "action": "security-fix", "choice": "<one of the page's offers>"}
      Carry out one fix the Security page offered (security.fix: a drop-in or a unit switched
      off, each recorded with how to undo it), then scan again.
  {"id": ..., "action": "health-scan"}
      The Box doctor (health.py): the last install, the root helper, every unit, Kiwix, the
      watchdog, the web server's config; to control/health.json.
  {"id": ..., "action": "health-fix", "choice": "<one of its offers>"}
      One repair the Box doctor offered (restart a unit, rebuild or tidy the Kiwix library, …), or
      "rerun-install": install.sh again from a copy of the installed code, recorded options.
      Then the Box doctor looks again.
  {"id": ..., "action": "net-scan"[, "iface": "<interface or phy>"]}
      What the box has for networking (netinv.py): radios, who runs them, what each can do,
      what is in the way; to control/netinv.json. With "iface", that device alone.
  {"id": ..., "action": "uplink-set", "settings": {pace, reach, guests, sensitivity, on_wedge, iface, overrides}}
      How hard the watchdog (uplink.py) works to keep the box on its network: checked by
      uplink.validate, written to /etc/hub/uplink.json, which irate-box-uplink picks up.
  {"id": ..., "action": "uplink-do", "step": "reconnect"|"restart"|"radio"|"reboot"}
      One step now, asked on /admin (a stalled watchdog's "Reset the radio now"): handed to the
      running watchdog (uplink.request_step), whose guards still apply and whose log says what it did.
  {"id": ..., "action": "uplink-hold", "minutes": 0-1440}
      No repairs for that long (0 ends a hold), for an owner working on the network.
  {"id": ..., "action": "uplink-profile", "on": true|false}
      The by-consent change to the owner's WiFi profile (keep retrying), or its undo.
  {"id": ..., "action": "nm-defaults", "changes": {name: value | "default"}, "iface": null | "wlan0"}
      NetworkManager's box defaults (hub/nmconf.py): the owner's choices written to irate-box's own
      drop-in in /etc/NetworkManager/conf.d, NetworkManager told to read it again. "default" takes
      irate-box's line out; with none left, the file goes.
  {"id": ..., "action": "wifi-country", "country": "GB"}
      The WiFi country (hub/wificountry.py): applied at once (iw reg set) and kept for boot in cfg80211's
      option file, which a netplan handover also writes. Only a code from tzdata's ISO 3166 table.
  {"id": ..., "action": "nm-handover", "name": "<netplan file>.yaml", "undo": false|true}
      A netplan file's connections handed over to NetworkManager (root/nmhandover.py), checked first
      and put back at once if NetworkManager's view of them changes; undo puts the file back.
  {"id": ..., "action": "nm-connection", "uuid": "...", "changes": {name: value | "default"}, "undo": false|true}
      One WiFi connection's own settings (root/nmconnection.py), where a change lasts; the values
      from before recorded for undo and uninstall.
  {"id": ..., "action": "wifisync", "do": "off"|"on"|"undo"}
      mPWRD-OS's wifisync (it rfkills the WiFi radio while meshtasticd's WiFi setting is off): switched
      off or on (systemctl disable/enable --now), the state from before the first change kept for undo
      and uninstall.
  {"id": ..., "action": "other-ap", "uuid": "...", "do": "remove"|"leave"|"undo"|"forget"}
      A hotspot profile that is not irate-box's (root/otherap.py): removed (kept for undo), or left
      and no longer listed.
  {"id": ..., "action": "radio-test", "iface": "wlan0", "exp": "follows-roam"|"radio-reset"|"driver-reset"}
      An experiment on one of the box's radios (root/radiotests.py), refused while guests are on the
      hotspot; its answer kept per radio and driver version.
  {"id": ..., "action": "app-install", "app": "draw|mermaid|serial|room", "zip": "<staged bundle>"}
      Check a bundle the librarian staged in $STATE/library/apps/ and swap it in under
      /usr/share/hub (the previous copy kept). {"action": "app-rollback", "app": ...} swaps back.
  {"id": ..., "action": "kit-fetch", "kit": "<a shipped toolkit>", "budget_mb": 500}
  {"id": ..., "action": "kit-install", "kit": ..., "hours": 24|null}
  {"id": ..., "action": "kit-remove" | "kit-keep" | "kit-rollback" | "kit-drop-previous", "kit": ...[, "hours": ...]}   {"action": "kit-expire" | "kit-drop-all-previous"}
      Toolkits (kits.py): fetch a shipped kit's packages into the local repository (online),
      install it from there with no internet (removed again after "hours"; null: never),
      remove what it added, change when it goes, or remove every kit whose time is up. A
      request names a kit, never packages. control/kits.json says what is cached and installed.
  {"action": "kit-define", "kit": {id, title, summary, packages, remove_after_hours}}
  {"action": "kit-undefine", "kit": ...}   {"action": "kit-extra", "kit": ..., "packages": [...]}
      The owner's own kits, and extra tools in a shipped kit: every package name
      checked against this box's package lists before root keeps the definition.

From a root shell, the same file also resets the login, for an owner who has lost it:

    sudo /opt/irate-box/irate-box hub_control reset-password

which puts the box back to unclaimed: /admin asks the next visitor to choose a password, and
install.sh --apps-from-actions uses  hub_control.py install-app APP ZIP  for a first install.

Stdlib only.
"""

import errno
import hashlib
import json
import os
import platform
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from irate_box.finding import action as finding_action
from irate_box.finding import finding
from irate_box.hub import access, manifests, netinv, uplink
from irate_box.msg import (
    M,
    Refused,
    duration,
    is_message,
    render,
    when,
)
from irate_box.msg import text as paragraph
from irate_box.root import (
    ap,
    health,
    kits,
    safeio,
    secdoctor,
    security,
    share,
    signing,
    usbstick,
)
from irate_box.root.nginx_front import locked

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
HUB_USER = os.environ.get("HUB_USER", "hub")
CONTROL = STATE / "control"
REQUESTS = CONTROL / "requests"
RESULTS = CONTROL / "results"
HELPER_DOING = CONTROL / "helper-doing.json"  # the request being run, for the hub (it cannot see root's processes)


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
SIGNING_PUBLIC = CONTROL / "update-signing.json"
DOCTOR_STATE = CONTROL / "doctor.json"
# What the last "Ask the repository" found: its default branch, its branches, and how the branch this box follows
# stands against the installed version (update_poll_target); the page offers them and the doctor says them.
BRANCHES_STATE = CONTROL / "update-branches.json"
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
# The box's own admin login switched off: its hash kept here, root's,
# and the login file emptied. The hub's copy of whether it is on, for /admin → Accounts.
ADMIN_LOGIN_OFF = ETC / "admin-login.off"
ADMIN_LOGIN_STATE = CONTROL / "admin-login.json"
# How far the box's own login (for scripts) reaches, while it is on: "local" (from the box itself only) or
# "network" (from other devices too); the admin's choice on Accounts. Absent: "network", as it always was.
ADMIN_LOGIN_REACH = ETC / "admin-login.reach"
REACHES = ("off", "local", "network")
ACCESS_STOPPED = ETC / "access-stopped.json"  # the services "off" stopped, to start again
NGINX_ACCESS = Path(os.environ.get("HUB_NGINX_ACCESS", ETC / "nginx-access.conf"))
# The add-on server's maps (http level), and Caddy's add-on routes: from access.json and the
# local add-ons' manifests, re-checked here (the hub writes those).
NGINX_ADDON_ACCESS = Path(os.environ.get("HUB_NGINX_ADDON_ACCESS", ETC / "nginx-addons.conf"))
# A local add-on in users mode: one location per such add-on,
# ahead of the add-on server's shared one (access.addon_gates).
NGINX_ADDON_GATES = Path(os.environ.get("HUB_NGINX_ADDON_GATES", ETC / "nginx-addon-gates.conf.d"))
CADDY_ACCESS = Path(os.environ.get("HUB_ACCESS_DIR", "/etc/caddy/irate-box-access"))
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HASH_LINE = re.compile(r"^(\s*admin\s+)\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}\s*$", re.M)
MIN_PASSWORD = 8

POLICY_FILE = ETC / "security-policy.json"
POLICY_STATE = CONTROL / "security-policy.json"
POLICY_ACTIONS = frozenset(
    (
        "security-policy",
        "security-fix",
        "update-signing",
        "update-force-install",
        "update-set-source",
        "tls-make",
        "tls-switch",
        "tls-import",
        "tls-box",
        "tls-admin-only",
        "tls-settings",
        "access",
        "admin-login",
        "admin-gate",
        "share-set",
        "share-allow",
    )
)


def policy_mode():
    try:
        mode = json.loads(safeio.read_own(POLICY_FILE)).get("mode")
    except FileNotFoundError:
        return "console" if POLICY_FILE.is_symlink() else "hub"
    except (ValueError, OSError, AttributeError):
        return "console"
    return mode if mode in ("hub", "console") else "console"


def policy_publish():
    mode = policy_mode()
    safeio.write(POLICY_STATE, json.dumps({"mode": mode}))
    return mode


def policy_set(req):
    mode = req.get("mode")
    if mode not in ("hub", "console"):
        raise Refused(
            M(
                "policy.invalid",
                "Select hub administration or console/SSH administration.",
            )
        )
    if mode == "console" and req.get("confirmed") is not True:
        raise Refused(
            M(
                "policy.confirm",
                "Confirm that restoring hub policy changes requires a root-capable console or SSH session.",
            )
        )
    safeio.write(POLICY_FILE, json.dumps({"mode": mode}))
    policy_publish()
    return M(
        "policy.saved",
        "Security policy changes are accepted from {source}.",
        source=("the hub administrator" if mode == "hub" else "the root console or SSH only"),
    )


def policy_check(action):
    if action in POLICY_ACTIONS and policy_mode() != "hub":
        raise Refused(
            M(
                "policy.locked",
                "The root helper accepts security policy changes only from the console or SSH. "
                "To restore hub changes, run sudo /opt/irate-box/irate-box hub_control security-policy hub.",
            )
        )


# systemd-run's timers default to AccuracySec=1min: "--on-active=1" fired 22 s late in a
# test, and the old Caddy login kept working until then.
TIMER_EXACT = ("--timer-property=AccuracySec=100ms",)


def run(*cmd, timeout=120, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)  # noqa: TID251


# What every action answers, and why it refuses, are messages (msg.py): whole sentences with stable IDs. Names,
# paths, commands and what a program printed are their arguments, never part of the English. The result keeps
# the English for every reader that expects text (answer).


def _why(exc):
    """What an exception says, as an argument: its message where it has one (Refused, or another module's own),
    else its text."""
    said = getattr(exc, "message", None)
    return said if is_message(said) else str(exc)


def _last_line(text, limit=300):
    """The last line a program printed, as evidence for a message, or "?"."""
    return ((text or "").strip().splitlines() or ["?"])[-1][:limit]


# Root's files in control/: safeio.write, a new file renamed into place, never through
# a link the hub planted; left root's, 0644, which the hub reads. Nothing is chowned to the hub.


def service(req):
    unit, op = str(req.get("unit", "")), str(req.get("op", ""))
    if unit not in UNITS:
        raise Refused(
            M(
                "control.service.unknown",
                "The administration pages do not control {unit}.",
                unit=unit,
            )
        )
    if op not in UNITS[unit]:
        raise Refused(
            M(
                "control.service.op-unavailable",
                "The action {op} is not available for {unit}.",
                op=op,
                unit=unit,
            )
        )
    if run("systemctl", "cat", unit).returncode != 0:
        raise Refused(
            M(
                "control.service.not-installed",
                "The service {unit} is not installed.",
                unit=unit,
            )
        )
    args = {"enable": ("enable", "--now"), "disable": ("disable", "--now")}.get(op, (op,))
    if unit == "irate-box.service":
        # Restarting the hub from a request the hub made: answer first, then go.
        subprocess.Popen(["systemd-run", "--on-active=2", *TIMER_EXACT, "systemctl", "restart", unit])  # noqa: TID251
        return M("control.service.restarting", "The service {unit} is restarting.", unit=unit)
    out = run("systemctl", *args, unit)
    if out.returncode != 0:
        said = (out.stderr or out.stdout).strip()
        if said:
            raise Refused(
                M(
                    "control.service.failed",
                    "The action {op} failed for {unit}. systemctl said: {output}",
                    op=op,
                    unit=unit,
                    output=_last_line(said),
                )
            )
        raise Refused(
            M(
                "control.service.failed-silent",
                "The action {op} failed for {unit}.",
                op=op,
                unit=unit,
            )
        )
    # The state is systemd's own word (active, inactive, failed …): data.
    return M(
        "control.service.done",
        "The action {op} is complete for {unit}. The service state is now {state}.",
        op=op,
        unit=unit,
        state=run("systemctl", "is-active", unit).stdout.strip(),
    )


MAX_PASSWORD = 128
PASSWORD_LENGTH = M(
    "control.password.length",
    "The password must have {min} to {max} characters on one line.",
    min=MIN_PASSWORD,
    max=MAX_PASSWORD,
)
# The box's own sign-in for scripts (HTTP basic auth, user admin): not a hub account, and not an account of the system.
SCRIPT_USER = "admin"


def _password_ok(pw):
    return MIN_PASSWORD <= len(pw) <= MAX_PASSWORD and not any(c in pw for c in "\n\r\0")


def password(req):
    pw = str(req.get("password", ""))
    if not _password_ok(pw):
        raise Refused(PASSWORD_LENGTH)
    if req.get("setup") and not UNCLAIMED.exists():
        raise Refused(
            M(
                "control.password.already-set",
                "The password of the user {user} is already set. Sign in to change it.",
                user=SCRIPT_USER,
            )
        )
    done = set_login(pw)
    if req.get("setup"):
        UNCLAIMED.unlink(missing_ok=True)
    return _password_saved(done)


def _password_saved(done):
    """set_login's record said: where the password went, and the two things that can come with it."""
    places, on_again, failed = done["places"], done["on_again"], done["failed"]
    if on_again and failed:
        return M(
            "control.password.saved.on-again.failed",
            "The new password of the user {user} is saved for {places}. "
            "Script sign-in is enabled again. {failed} did not accept the new password.",
            user=SCRIPT_USER,
            places=places,
            failed=failed,
        )
    if on_again:
        return M(
            "control.password.saved.on-again",
            "The new password of the user {user} is saved for {places}. Script sign-in is enabled again.",
            user=SCRIPT_USER,
            places=places,
        )
    if failed:
        return M(
            "control.password.saved.failed",
            "The new password of the user {user} is saved for {places}. {failed} did not accept the new password.",
            user=SCRIPT_USER,
            places=places,
            failed=failed,
        )
    return M(
        "control.password.saved",
        "The new password of the user {user} is saved for {places}.",
        user=SCRIPT_USER,
        places=places,
    )


def reset_password():
    """Back to unclaimed: an unknown random login everywhere, and the first-use form on
    /admin. For the console, when the owner has lost the password."""
    set_login(secrets.token_urlsafe(24), keep=False)
    UNCLAIMED.write_text("no admin password chosen yet (hub_control.py reset-password)\n")
    UNCLAIMED.chmod(0o644)
    return M(
        "control.password.reset",
        "The password of the user {user} is reset, and initial setup is open again. "
        "Until someone sets a new password, any person who can reach the box can open {page}, "
        "set the password and become the administrator.",
        user=SCRIPT_USER,
        page="/admin",
    )


def _set_nginx_login(pw):
    """The login file nginx checks. SHA-512-crypt, not bcrypt: nginx checks the hash on every
    request and caches nothing, and bcrypt at Caddy's cost takes seconds a check on a small
    board (this: milliseconds). nginx reads the file per request, so nothing needs reloading.
    """
    if not NGINX_LOGINS.exists():
        raise Refused(
            M(
                "control.password.no-file",
                "The script sign-in file {path} does not exist.",
                path=str(NGINX_LOGINS),
            )
        )
    out = subprocess.run(  # noqa: TID251
        ["openssl", "passwd", "-6", "-stdin"],
        input=pw + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    hashed = out.stdout.strip()
    if out.returncode != 0 or not hashed.startswith("$6$"):
        raise Refused(_hash_failed("openssl passwd"))
    gid = NGINX_LOGINS.stat().st_gid  # nginx's group, as install.sh set it
    tmp = NGINX_LOGINS.with_name(NGINX_LOGINS.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"admin:{hashed}\n")
    if os.geteuid() == 0:  # always, but in the tests
        os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, NGINX_LOGINS)
    return "nginx"


def _set_caddy_login(pw):
    """The bcrypt hashes in the hub's Caddy config, validated before they replace it."""
    # On stdin, not the command line, which every process can read in /proc.
    hashed = run("caddy", "hash-password", input=pw + "\n")
    if hashed.returncode != 0:
        raise Refused(_hash_failed("caddy hash-password"))
    _caddy_login_swap(hashed.stdout.strip())
    # The private apps' snippets carry the hash too.
    if CADDY_ACCESS.is_dir():
        _access_files(access.read(ACCESS_FILE))
    # Restart, not reload: the Caddyfile turns Caddy's admin API off, which reload needs.
    subprocess.Popen(["systemd-run", "--on-active=1", *TIMER_EXACT, "systemctl", "restart", "caddy"])  # noqa: TID251
    return "Caddy"


def _hash_failed(command):
    return M(
        "control.password.hash-failed",
        "The command {command} failed. Nothing changed.",
        command=command,
    )


def set_login(pw, keep=True):
    """Put pw everywhere the admin login is used. keep=False leaves /etc/hub/admin-password
    out (removed): a random placeholder nobody is meant to know. A new password turns the box's
    own login back on if it was off (reset-password at the console is the way back in).
    Returns {"places": the programs that took it, "failed": those that did not, "on_again": whether it was off}.
    """
    # The web server first: if its new login cannot be written, nothing else has changed.
    web = _set_nginx_login(pw) if WEB_SERVER == "nginx" else _set_caddy_login(pw)
    on_again = ADMIN_LOGIN_OFF.exists()
    if on_again:
        ADMIN_LOGIN_OFF.unlink()
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
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
    # ttyd has no credential of its own; an older install may still have one, the old password,
    # which is no use to anyone once it changes: it goes.
    (ETC / "ttyd.env").unlink(missing_ok=True)
    done = {"places": [web], "failed": [], "on_again": on_again}
    if run("systemctl", "is-active", "--quiet", f"syncthing@{HUB_USER}").returncode == 0:
        done["places" if syncthing_gui_password(pw) else "failed"].append("Syncthing")
    return done


SYNCTHING_GUI = "http://127.0.0.1:8384"


def _web_open(request, timeout, local_origin=None):
    def validate(url):
        parsed = urllib.parse.urlsplit(url)
        if local_origin is None:
            allowed = parsed.scheme == "https" and bool(parsed.hostname)
        else:
            origin = urllib.parse.urlsplit(local_origin)
            allowed = (
                origin.scheme == "http"
                and origin.hostname in ("127.0.0.1", "::1")
                and (parsed.scheme, parsed.hostname, parsed.port) == (origin.scheme, origin.hostname, origin.port)
            )
        if not allowed or parsed.username is not None or parsed.password is not None:
            raise ValueError("URL must use HTTPS or the configured loopback API origin")

    class WebRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    validate(request.full_url)
    handlers = [WebRedirect()]
    if local_origin is not None:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def syncthing_gui_password(pw, tries=15, sleep=time.sleep):
    """Syncthing's GUI password, through its REST API with the API key from its own config: the
    password travels in the request body, never on a command line (`syncthing cli … password
    set` would put it in /proc for every process to read). Syncthing hashes it (bcrypt) as it saves.
    Tried again for a while: install.sh's `gui user set` just before restarts the GUI's listener,
    and a request in that gap finds nothing there."""
    cfg = next(
        (
            t
            for t in (
                STATE / ".local/state/syncthing/config.xml",
                STATE / ".config/syncthing/config.xml",
            )
            if t.exists()
        ),
        None,
    )
    m = re.search(r"<apikey>([^<]+)</apikey>", cfg.read_text()) if cfg else None
    if not m:
        return False
    req = urllib.request.Request(
        f"{SYNCTHING_GUI}/rest/config/gui",
        method="PATCH",
        data=json.dumps({"password": pw}).encode(),
        headers={"X-API-Key": m.group(1), "Content-Type": "application/json"},
    )
    for attempt in range(tries):
        try:
            with _web_open(req, timeout=20, local_origin=SYNCTHING_GUI) as resp:
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


REPO_RE = re.compile(r"^(public|private)/[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def kit_choices(req):
    """What a new box's kit is to hold: books (true for all, or names), toolkits (ids),
    git repositories (area/name), this box's state (none, settings, data), and a budget in MB (or none).
    Checked here, whatever the hub sent."""
    books = req.get("books", False)
    if books is True:
        zims = sorted(ZIM_DIR.glob("*.zim"))
    elif isinstance(books, list) and all(isinstance(b, str) and usbstick.NAME_RE.match(b) for b in books):
        zims = [ZIM_DIR / f"{b}.zim" for b in books]
        missing = [z.stem for z in zims if not z.is_file() or z.is_symlink()]
        if missing:
            raise Refused(
                M(
                    "control.kit.no-book",
                    {
                        "one": "This box has no book named {names}.",
                        "other": "This box has no books named {names}.",
                    },
                    n=len(missing),
                    names=missing[:3],
                )
            )
    elif books is False or books is None:
        zims = []
    else:
        raise Refused(
            M(
                "control.kit.books-bad",
                "The request must give the books as true, false or a list of book names.",
            )
        )
    kit_ids = req.get("kits") or []
    if not isinstance(kit_ids, list) or not all(isinstance(k, str) and kits.ID_RE.match(k) for k in kit_ids):
        raise Refused(KITS_BAD)
    repos = req.get("repos") or []
    if not isinstance(repos, list) or not all(isinstance(r, str) and REPO_RE.match(r) for r in repos):
        raise Refused(
            M(
                "control.kit.repos-bad",
                "The request must give the repositories as a list of names in the form {form}.",
                form="area/name",
            )
        )
    repo_dirs = [STATE / "git" / f"{r}.git" for r in repos]
    gone = [
        repo
        for repo, directory in zip(repos, repo_dirs, strict=True)
        if not directory.is_dir() or directory.is_symlink()
    ]
    if gone:
        raise Refused(
            M(
                "control.kit.no-repo",
                {
                    "one": "This box has no repository named {names}.",
                    "other": "This box has no repositories named {names}.",
                },
                n=len(gone),
                names=gone[:3],
            )
        )
    state = req.get("state", "none")
    if state not in ("none", "settings", "data"):
        raise Refused(
            M(
                "control.kit.state-bad",
                "The request must give the box state as one of these values: {values}.",
                values=["none", "settings", "data"],
            )
        )
    budget = req.get("budget_mb")
    if budget is not None and (type(budget) is not int or budget <= 0):
        raise Refused(
            M(
                "control.kit.budget-bad",
                "The request must give the size limit as a whole number of MB, or no limit.",
            )
        )
    return zims, kit_ids, repo_dirs, state, budget


KITS_BAD = M(
    "control.kit.kits-bad",
    "The request must give the toolkits as a list of toolkit IDs.",
)
EXPORT_EMPTY = M("control.export.empty", "The export selection is empty. Select at least one item.")


def _download_ready(meta):
    return M(
        "control.kit.ready",
        "The download is ready: {name} ({size} MB).",
        name=meta["name"],
        size=meta["size"] >> 20,
    )


def _tar_failed(tar):
    return Refused(
        M(
            "control.kit.tar-failed",
            "The tar command failed: {output}",
            output=_last_line(tar.stderr),
        )
    )


def offline_kit(req):
    """A kit that sets up another box with no internet, from what this one has: its code (the
    installer's --make-offline-bundle, run from the installed copy), its apps, its download
    cache (anything missing fetched if there is internet), and what the owner chose (kit_choices):
    books, toolkits (in the stick layout the new box's Toolkits import reads), git repositories,
    and this box's settings, or settings and data. One .tar in $STATE/kits, which replaces the
    last; the hub offers it as a download. Refused over the budget, before anything is made.
    """
    zims, kit_ids, repo_dirs, state, budget = kit_choices(req)
    apps = Path("/usr/share/hub/apps")
    arch = platform.machine()
    from irate_box.hub import backup

    base = backup.apps_size(apps) + _du(Path("/usr/share/hub/room"), DOWNLOADS) + (4 << 20)
    chosen = (
        sum(z.stat().st_size for z in zims)
        + _du(*repo_dirs)
        + sum((kits.manifest(k) or {}).get("bytes", 0) for k in kit_ids)
        + (_state_size(state) if state != "none" else 0)
    )
    if budget is not None and base + chosen > budget << 20:
        raise Refused(
            M(
                "control.kit.too-large",
                "The estimated kit size is {size} MB. The size limit is {limit} MB. "
                "Select fewer items or increase the size limit.",
                size=(base + chosen) >> 20,
                limit=budget,
            )
        )
    need = 2 * base + chosen + (64 << 20)
    with KitRoom() as room:
        return _offline_kit(room, zims, apps, arch, need, kit_ids, repo_dirs, state)


KIT_WORK = Path(os.environ.get("HUB_KIT_WORK", "/var/lib/irate-box-kit-work"))
ROOT_UID = 0  # who must own kits/ and the work folder (the tests stand in their own)


class KitRoom:
    """Where a kit or export is made, and how it lands. The hub owns $STATE, so nothing is made inside it:
    the work folder is root's alone beside it (KIT_WORK, the same filesystem), where the installer and tar
    are given real paths that no link the hub plants can redirect. The finished .tar and kit.json then move
    into kits/ through kits/'s own fd (kits/ made root's first), so a kits/ renamed away and replaced by a
    link while this runs changes nothing."""

    def __enter__(self):
        safeio.mkdir(KITS, ROOT_UID, ROOT_UID, 0o755)
        self.fd = safeio._dir_fd(KITS)
        if os.fstat(self.fd).st_uid != ROOT_UID:
            os.close(self.fd)
            raise Refused(
                M(
                    "control.kit.not-root",
                    "Root does not own the folder {path}, so the box did not use it.",
                    path=str(KITS),
                )
            )
        safeio.mkdir(KIT_WORK, ROOT_UID, ROOT_UID, 0o700)
        self.work = Path(tempfile.mkdtemp(prefix="kit-", dir=KIT_WORK))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.work, ignore_errors=True)
        os.close(self.fd)

    def free(self):
        return shutil.disk_usage(self.work).free

    def publish(self, src, name, meta):
        """src (a finished .tar in the work folder) into kits/ as name, the last kit or export there removed,
        and its record as kit.json; meta gains the size. Returns meta."""
        os.chmod(src, 0o644)
        for old in os.listdir(self.fd):
            if re.fullmatch(r"irate-box-(kit|content)-[A-Za-z0-9._-]+\.tar", old) and old != name:
                os.unlink(old, dir_fd=self.fd)
        try:
            os.rename(src, name, dst_dir_fd=self.fd)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            part = f".{name}.part"  # another filesystem: copied in, new and never through a link, then renamed
            try:
                os.unlink(part, dir_fd=self.fd)
            except FileNotFoundError:
                pass
            fd = os.open(
                part,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o644,
                dir_fd=self.fd,
            )
            with open(fd, "wb") as out, open(src, "rb") as inp:
                shutil.copyfileobj(inp, out, 1 << 20)
            os.rename(part, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.unlink(src)
        meta["size"] = os.stat(name, dir_fd=self.fd, follow_symlinks=False).st_size
        rec = self.work / "kit.json"
        rec.write_text(json.dumps(meta, indent=2))
        os.chmod(rec, 0o644)
        try:
            os.rename(rec, "kit.json", dst_dir_fd=self.fd)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            safeio.write(KITS / "kit.json", rec.read_text())
        return meta


CONTENT_IMPORT = Path(__file__).with_name("content-import.sh")


def _as_hub():
    """subprocess arguments that run a reader as the hub user: a link the hub swapped in then reads only what the hub
    could read already. Not root (the tests): already no more than this user."""
    return {"user": HUB_USER, "group": HUB_USER, "extra_groups": []} if os.geteuid() == 0 else {}


class _Capped:
    """A stream that refuses to give more than `left` bytes: the hub writes it, root stores it."""

    def __init__(self, fh, left, path):
        self.fh, self.left, self.path = fh, left, path

    def read(self, n=-1):
        data = self.fh.read(n if n is not None and n >= 0 else self.left + 1)
        self.left -= len(data)
        if self.left < 0:
            raise Refused(
                M(
                    "control.kit.hub-too-large",
                    "{path} holds more than the free storage. The box stopped copying it.",
                    path=str(self.path),
                )
            )
        return data


class _Hashing:
    def __init__(self, fh):
        self.fh, self.h = fh, hashlib.sha256()

    def read(self, n=-1):
        data = self.fh.read(n)
        self.h.update(data)
        return data


class _HubTar:
    """`tar -cf -` of `names` in `folder`, run as the hub user, read as a tarfile stream."""

    def __init__(self, folder, names, limit):
        self.folder = folder
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(  # noqa: TID251
            ["tar", "-C", str(folder), "-cf", "-", "--", *names],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=self.err,
            **_as_hub(),
        )
        self.tar = tarfile.open(fileobj=_Capped(self.proc.stdout, limit, folder), mode="r|")

    def __enter__(self):
        return self.tar

    def __exit__(self, kind, *_):
        self.proc.stdout.close()
        code = self.proc.wait()
        self.err.seek(0)
        said = self.err.read().decode(errors="replace")
        self.err.close()
        if kind is None and code != 0:
            raise _hub_read_failed(self.folder, said)


def _hub_read_failed(path, said):
    return Refused(
        M(
            "control.kit.hub-read-failed",
            "The box could not read {path} as the hub user: {output}",
            path=str(path),
            output=_last_line(said),
        )
    )


def _copy_as_hub(src, dest):
    """A folder the hub owns (a git repository) copied into dest, a new folder of root's: read by the hub user,
    unpacked by tarfile's data filter (no absolute paths, no .., no links out, no devices, no setuid).
    """
    os.mkdir(dest, 0o755)
    with _HubTar(src, ["."], shutil.disk_usage(dest).free - _min_free()) as tar:
        tar.extractall(dest, filter="data")


def _hash_as_hub(zims):
    """{name: sha256} of the books, hashed by the hub user."""
    out = subprocess.run(  # noqa: TID251
        ["sha256sum", "-z", "--", *map(str, zims)],
        capture_output=True,
        timeout=7200,
        **_as_hub(),
    )
    if out.returncode != 0:
        raise _hub_read_failed(ZIM_DIR, out.stderr.decode(errors="replace"))
    return {Path(rec[66:]).name: rec[:64] for rec in out.stdout.decode(errors="replace").split("\0") if len(rec) > 66}


def _append_books(part, zims, prefix, sums=None):
    """The books appended to the tar `part` as prefix + name, read by the hub user. Only those names, only regular
    files; with `sums`, each must still have the hash it was listed with."""
    want = {z.name for z in zims}
    with (
        _HubTar(ZIM_DIR, sorted(want), shutil.disk_usage(part.parent).free - _min_free()) as books,
        tarfile.open(part, "a") as out,
    ):
        for m in books:
            if m.name not in want or not m.isreg():
                raise Refused(
                    M(
                        "control.kit.book-unexpected",
                        "The book folder gave an entry that the box did not ask for: {name}. The box did not use it.",
                        name=m.name,
                    )
                )
            want.discard(m.name)
            info = tarfile.TarInfo(prefix + m.name)
            info.size, info.mtime, info.mode = m.size, m.mtime, 0o644
            src = _Hashing(books.extractfile(m))
            out.addfile(info, src)
            if sums is not None and sums.get(m.name) != src.h.hexdigest():
                raise Refused(
                    M(
                        "control.kit.book-changed",
                        "The book {name} changed while the box made the kit. Make the kit again.",
                        name=m.name,
                    )
                )


def content_extras(top, repo_dirs, state):
    """Git repositories (git/<area>/<name>.git) and this box's state (state-backup.tar.gz) into `top`, an
    export's irate-box/ folder, with import.sh to bring them in on a box that has the hub. Folders and
    files made new, never through a link; a repository already there is replaced whole.
    """
    said = []
    if not repo_dirs and state == "none":
        return said
    for d in repo_dirs:
        area = top / "git" / d.parent.name
        safeio.mkdir(top / "git", mode=0o755)
        safeio.mkdir(area, mode=0o755)
        part, dest = area / f".{d.name}.part", area / d.name
        for old in (part, dest):
            if old.is_symlink():
                old.unlink()
            elif old.exists():
                shutil.rmtree(old)
        _copy_as_hub(d, part)
        os.replace(part, dest)
        said.append(f"git: {d.parent.name}/{d.name}")
    if state != "none":
        import tarfile

        from irate_box.hub import backup

        name = top / "state-backup.tar.gz"
        name.unlink(missing_ok=True)
        with tarfile.open(fileobj=safeio.create(name, 0o644), mode="w:gz") as tar:
            tar.add(
                STATE,
                arcname="irate-box-state",
                filter=backup.keep_for(state, False, backup.git_mirrors(STATE)),
            )
        said.append(f"this box's {'settings' if state == 'settings' else 'settings and data'}")
    script = top / "import.sh"
    script.unlink(missing_ok=True)
    with safeio.create(script, 0o755) as fh:
        fh.write(CONTENT_IMPORT.read_bytes())
    return said


def content_export(req):
    """A content export as one download, without the hub program: the stick layout (books at the top of
    irate-box/, toolkits in kits/, repositories and state with import.sh), packed as a .tar in $STATE/kits,
    which replaces the last kit or export. Unpacked onto a stick, the other box reads it as a stick.
    """
    zims, kit_ids, repo_dirs, state, budget = kit_choices(req)
    size = (
        sum(z.stat().st_size for z in zims)
        + _du(*repo_dirs)
        + sum((kits.manifest(k) or {}).get("bytes", 0) for k in kit_ids)
        + (_state_size(state) if state != "none" else 0)
    )
    if not size:
        raise Refused(EXPORT_EMPTY)
    if budget is not None and size > budget << 20:
        raise Refused(
            M(
                "control.export.too-large",
                "The estimated export size is {size} MB. The size limit is {limit} MB. "
                "Select fewer items or increase the size limit.",
                size=size >> 20,
                limit=budget,
            )
        )
    with KitRoom() as room:
        books = sum(z.stat().st_size for z in zims)
        need = 2 * (size - books) + books + (16 << 20)  # the work folder (no books), then the tar (all)
        if room.free() - need < _min_free():
            raise Refused(
                M(
                    "control.export.no-space",
                    "There is not enough free storage. The export needs {need} MB of free space. "
                    "{free} MB is free, and the box keeps {spare} MB spare.",
                    need=need >> 20,
                    free=room.free() >> 20,
                    spare=_min_free() >> 20,
                )
            )
        with Progress("kit", 2 + bool(kit_ids), path=KIT_PROGRESS) as progress:
            work = room.work
            top = work / "irate-box"
            top.mkdir()
            report = []
            if kit_ids:
                progress.step(_copying_kits(kit_ids))
                (top / "kits").mkdir()
                for k in kit_ids:
                    report.append(kits.export_usb(k, top / "kits", progress.bytes))
            progress.step(
                M(
                    "control.progress.extras",
                    "Adding the repositories and the state of this box",
                )
            )
            report += content_extras(top, repo_dirs, state)
            progress.step(PACKING)
            ver = (
                re.sub(
                    r"[^A-Za-z0-9._-]",
                    "",
                    ((CODE / "VERSION").read_text().split() or ["unknown"])[0],
                )
                or "unknown"
            )
            name = f"irate-box-content-{ver}.tar"
            part = work / name
            tar = run("tar", "-C", str(work), "-cf", str(part), "irate-box", timeout=3600)
            if tar.returncode != 0:
                raise _tar_failed(tar)
            if zims:
                _append_books(part, zims, "irate-box/")
            meta = room.publish(
                part,
                name,
                {
                    "name": name,
                    "kind": "content",
                    "at": time.time(),
                    "arch": platform.machine(),
                    "books": [z.name for z in zims],
                    "kits": list(kit_ids),
                    "repos": [f"{d.parent.name}/{d.name}" for d in repo_dirs],
                    "state": state,
                    "contents": ([f"book: {z.stem}" for z in zims] + report)[:60],
                },
            )
    return _download_ready(meta)


def _state_size(level):
    from irate_box.hub import backup

    sums = backup.walk(STATE)
    return sums["settings"] + (sums["data"] if level == "data" else 0)


# What a kit or an export is doing, for its progress bar: labels, in the words the page shows.
PACKING = M("control.progress.pack", "Packing everything into one file")


def _copying_kits(kit_ids):
    return M(
        "control.progress.kits",
        {"one": "Copying {n} toolkit", "other": "Copying {n} toolkits"},
        n=len(kit_ids),
    )


def _offline_kit(room, zims, apps, arch, need, kit_ids=(), repo_dirs=(), state="none"):
    free = room.free()
    if free - need < _min_free():
        args = {"need": need >> 20, "free": free >> 20, "spare": _min_free() >> 20}
        if zims:
            raise Refused(
                M(
                    "control.kit.no-space.books",
                    "There is not enough free storage. The kit needs {need} MB of free space. "
                    "{free} MB is free, and the box keeps {spare} MB spare. Select no books to make the kit smaller.",
                    **args,
                )
            )
        raise Refused(
            M(
                "control.kit.no-space",
                "There is not enough free storage. The kit needs {need} MB of free space. "
                "{free} MB is free, and the box keeps {spare} MB spare.",
                **args,
            )
        )
    with Progress(
        "kit",
        3 + bool(kit_ids) + bool(repo_dirs) + (state != "none"),
        path=KIT_PROGRESS,
    ) as progress:
        work = room.work
        progress.step(
            M(
                "control.progress.gather",
                "Gathering the code, the apps and the downloads",
            )
        )
        kit = work / "irate-box-kit"
        args = [
            "bash",
            str(CODE / "install.sh"),
            "--make-offline-bundle",
            str(kit),
            "--apps",
            str(apps),
            "--download-cache",
            str(DOWNLOADS),
            "--arch",
            arch,
        ]
        if WEB_SERVER == "caddy":
            args += ["--web", "caddy"]
        out = run(*args, timeout=3600, env=dict(os.environ, HOME=str(work)))
        if out.returncode != 0:
            raise Refused(
                M(
                    "control.kit.make-failed",
                    "The box could not make the kit. The installer said: {output}",
                    output=_last_line(out.stderr or out.stdout),
                )
            )
        report = [line.strip() for line in out.stdout.splitlines() if line.startswith("    ")]
        if kit_ids:
            # The toolkits in the stick layout (kits.export_usb) beside the kit: unpacked on a stick,
            # the new box's setup.sh imports them, each .deb checked against Debian's signatures.
            progress.step(_copying_kits(kit_ids))
            for k in kit_ids:
                report.append(kits.export_usb(k, kit / "kits", progress.bytes))
        if repo_dirs:
            progress.step(
                M(
                    "control.progress.repos",
                    {
                        "one": "Copying {n} git repository",
                        "other": "Copying {n} git repositories",
                    },
                    n=len(repo_dirs),
                )
            )
            for d in repo_dirs:
                dest = kit / "git" / d.parent.name / d.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                _copy_as_hub(d, dest)
                report.append(f"git: {d.parent.name}/{d.name}")
        if state != "none":
            import tarfile

            from irate_box.hub import backup

            progress.step(
                M("control.progress.data", "Adding the settings and data of this box")
                if state == "data"
                else M("control.progress.settings", "Adding the settings of this box")
            )
            with tarfile.open(kit / "state-backup.tar.gz", "w:gz") as tar:
                tar.add(
                    STATE,
                    arcname="irate-box-state",
                    filter=backup.keep_for(state, False, backup.git_mirrors(STATE)),
                )
            report.append(
                f"this box's {'settings' if state == 'settings' else 'settings and data'} (restored by setup.sh)"
            )
        if zims:
            progress.step(
                M(
                    "control.progress.checksums",
                    {
                        "one": "Calculating the checksum of {n} book",
                        "other": "Calculating the checksums of {n} books",
                    },
                    n=len(zims),
                )
            )
            sums = _hash_as_hub(zims)
            with open(kit / "SHA256SUMS", "a") as fh:
                for z in zims:
                    fh.write(f"{sums[z.name]}  ./zim/{z.name}\n")
        progress.step(PACKING)
        ver = (
            re.sub(
                r"[^A-Za-z0-9._-]",
                "",
                ((CODE / "VERSION").read_text().split() or ["unknown"])[0],
            )
            or "unknown"
        )
        name = f"irate-box-kit-{ver}-{arch}{'-with-books' if zims else ''}.tar"
        part = work / name
        tar = run("tar", "-C", str(work), "-cf", str(part), "irate-box-kit", timeout=3600)
        if tar.returncode != 0:
            raise _tar_failed(tar)
        if zims:
            _append_books(part, zims, "irate-box-kit/zim/", sums)
        meta = room.publish(
            part,
            name,
            {
                "name": name,
                "at": time.time(),
                "arch": arch,
                "books": [z.name for z in zims],
                "kits": list(kit_ids),
                "repos": [f"{d.parent.name}/{d.name}" for d in repo_dirs],
                "state": state,
                "contents": report[:60],
            },
        )
    return _download_ready(meta)


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


@locked
def _access_files(state):
    """The web server's part, written; returns what it replaced, to put back on failure."""
    from irate_box.root import tls

    policies = {
        path: text
        for path, text in tls._plain_files(**tls.settings(), access_state=state).items()
        if "-policy-" in path.name
    }
    tls.FRONT.mkdir(parents=True, exist_ok=True)
    previous = {path: path.read_text() if path.exists() else None for path in policies}
    for path, text in policies.items():
        _write_root_file(path, text)
    local, _ = manifests.load_local(builtin=MANIFESTS)
    if WEB_SERVER == "nginx":
        gates_dir = NGINX_ACCESS.with_name(NGINX_ACCESS.name + ".d")  # the template's @ACCESS@.d/gate-<id>.conf*
        gates = access.nginx_gates(
            state,
            admin_login=not ADMIN_LOGIN_OFF.exists(),
            accounts_ready=_admin_accounts_ready(),
        )
        gates_dir.mkdir(mode=0o755, exist_ok=True)
        addon_gates = access.addon_gates(state, local)
        NGINX_ADDON_GATES.mkdir(mode=0o755, exist_ok=True)
        paths = (
            [NGINX_ACCESS, NGINX_ADDON_ACCESS]
            + sorted(set(gates_dir.glob("gate-*.conf")) | {gates_dir / n for n in gates})
            + sorted(set(NGINX_ADDON_GATES.glob("users-*.conf")) | {NGINX_ADDON_GATES / n for n in addon_gates})
        )
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
        return {**old, **previous}
    login = _caddy_hash()
    if not login:
        raise Refused(
            M(
                "control.access.no-hash",
                "The file {path} has no script sign-in password. The apps that require sign-in need it.",
                path=str(_login_file()),
            )
        )
    # Readable by Caddy (it runs as its own user), as the Caddyfile with the same hash is.
    CADDY_ACCESS.mkdir(mode=0o755, exist_ok=True)
    old = {}
    files = dict(
        access.caddy_snippets(
            state,
            login,
            _caddy_directive(),
            admin_login=not ADMIN_LOGIN_OFF.exists(),
            local_only=_login_reach() == "local",
            accounts_ready=_admin_accounts_ready(),
        )
    )
    files["addons-routes.caddy"] = access.addon_caddy_routes(state, local, login, _caddy_directive())
    for name, text in files.items():
        path = CADDY_ACCESS / name
        old[path] = path.read_text() if path.exists() else None
        _write_root_file(path, text)
    return {**old, **previous}


def _access_record(state):
    """root's copy, and the hub's (in its control folder: a fresh name, never following a link)."""
    _write_root_file(ACCESS_FILE, json.dumps(state, indent=2) + "\n", 0o644)
    safeio.write(ACCESS_STATE, json.dumps(state))


@locked
def _access_apply(state, reload=True):
    """Write the web server's part and check it with the rest of its config; on a failed
    check, put the old files back. reload: make it live (install.sh does that itself).
    """
    old = _access_files(state)
    check = (
        run("nginx", "-t")
        if WEB_SERVER == "nginx"
        else run("caddy", "validate", "--adapter", "caddyfile", "--config", str(CADDYFILE))
    )
    if check.returncode != 0:
        for path, text in old.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                _write_root_file(path, text)
        raise Refused(
            M(
                "control.access.web-refused",
                "The web server did not accept the change, so nothing changed. It said: {output}",
                output=_last_line(check.stderr or check.stdout, 200),
            )
        )
    _access_record(state)
    if reload:
        if WEB_SERVER == "nginx":
            run("systemctl", "reload", "nginx")
        else:
            # The Caddyfile turns Caddy's admin API off, which reload needs.
            subprocess.Popen(  # noqa: TID251
                [
                    "systemd-run",
                    "--on-active=1",
                    *TIMER_EXACT,
                    "systemctl",
                    "restart",
                    "caddy",
                ]
            )


def _access_unit(app):
    m = next((m for m in MANIFESTS if m["id"] == app), {})
    st = m.get("status", {})
    unit = st.get("unit", "").replace("@hub.", f"@{HUB_USER}.")
    return unit if st.get("control") and unit and run("systemctl", "cat", unit).returncode == 0 else None


@locked
def access_set(req):
    """One app public, users, private or off. Off also stops (and disables) an add-on's service;
    leaving off starts it again."""
    app, mode = str(req.get("app", "")), str(req.get("mode", ""))
    local = {m["id"] for m in manifests.load_local(builtin=MANIFESTS)[0]}
    # Off is always safe, so a web add-on just removed (its manifest gone) can still be switched
    # off: the hub asks that when one is added or removed.
    if app not in access.ROUTED and app not in local and not (mode == "off" and access.LOCAL_ID_RE.match(app)):
        raise Refused(
            M(
                "control.access.unknown-app",
                "The box cannot set access for {app}.",
                app=app,
            )
        )
    if mode not in access.MODES:
        raise Refused(
            M(
                "control.access.mode-bad",
                "The access mode must be one of these values: {modes}.",
                modes=list(access.MODES),
            )
        )
    if mode == "users" and not access.users_allowed(app):
        raise Refused(
            M(
                "control.access.no-users",
                "The access of {app} cannot be {mode}. Select one of these modes: {modes}.",
                app=app,
                mode=mode,
                modes=[m for m in access.MODES if m != "users"],
            )
        )
    state = access.read(ACCESS_FILE)
    was = access.mode_of(state, app)
    state[app] = mode
    _access_apply(state)
    done = _access_saved(app, mode)
    unit = _access_unit(app)
    # Only what "off" stopped is started again: an add-on the owner never switched on stays off.
    try:
        stopped = set(json.loads(ACCESS_STOPPED.read_text()))
    except (OSError, ValueError, TypeError):
        stopped = set()
    if unit and mode == "off" and was != "off":
        if (
            run("systemctl", "is-enabled", "--quiet", unit).returncode == 0
            or run("systemctl", "is-active", "--quiet", unit).returncode == 0
        ):
            out = run("systemctl", "disable", "--now", unit)
            if out.returncode == 0:
                stopped.add(unit)
                done = M(
                    "control.access.saved.off.stopped",
                    "App access is saved. {app} is disabled, and the box stopped its service {unit}.",
                    app=app,
                    unit=unit,
                )
            else:
                done = M(
                    "control.access.saved.off.stop-failed",
                    "App access is saved. {app} is disabled. The box could not stop its service {unit}.",
                    app=app,
                    unit=unit,
                )
    elif unit and was == "off" and mode != "off" and unit in stopped:
        out = run("systemctl", "enable", "--now", unit)
        stopped.discard(unit)
        # The access sentence, then what became of the service: two whole sentences.
        done = (
            M(
                "control.access.saved.started",
                "{access} The box started the service {unit} again.",
                access=done,
                unit=unit,
            )
            if out.returncode == 0
            else M(
                "control.access.saved.start-failed",
                "{access} The service {unit} did not start.",
                access=done,
                unit=unit,
            )
        )
    _write_root_file(ACCESS_STOPPED, json.dumps(sorted(stopped)) + "\n", 0o644)
    return done


def _access_saved(app, mode):
    """Who can open the app now, by its mode. Its listener and the firewalls still decide who can reach it."""
    if mode == "public":
        return M(
            "control.access.saved.public",
            "App access is saved. Guests can open {app}.",
            app=app,
        )
    if mode == "users":
        return M(
            "control.access.saved.users",
            "App access is saved. Signed-in users can open {app}.",
            app=app,
        )
    if mode == "private":
        return M(
            "control.access.saved.private",
            "App access is saved. Only administrators can open {app}.",
            app=app,
        )
    return M("control.access.saved.off", "App access is saved. {app} is disabled.", app=app)


@locked
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
    limited = [f"{k} ({v})" for k, v in state.items() if v != "public"]
    if limited:
        return M(
            "control.access.installed",
            "App access is written. These apps are not public: {apps}.",
            apps=limited,
        )
    return M(
        "control.access.installed.public",
        "App access is written. Every app has public access. "
        "The firewalls and the address that each app listens on still apply.",
    )


def _login_reach():
    """off | local | network: the box's own login, as the admin chose its reach."""
    if ADMIN_LOGIN_OFF.exists():
        return "off"
    try:
        r = ADMIN_LOGIN_REACH.read_text().strip()
    except OSError:
        return "network"
    return r if r in ("local", "network") else "network"


def _sync_net_logins():
    """nginx: the login file for requests from other devices (the config's map sends the box's own to
    NGINX_LOGINS): a copy of it when the reach is the network, else none in it."""
    if WEB_SERVER != "nginx" or not NGINX_LOGINS.exists():
        return
    net = NGINX_LOGINS.with_name(NGINX_LOGINS.name + ".net")
    text = (
        NGINX_LOGINS.read_text()
        if _login_reach() == "network"
        else "# The box's own login answers only from the box itself (/admin → Accounts).\n"
    )
    tmp = net.with_name(net.name + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    if os.geteuid() == 0:  # always, but in the tests
        os.chown(tmp, 0, NGINX_LOGINS.stat().st_gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, net)


def _admin_login_record():
    _sync_net_logins()
    safeio.write(
        ADMIN_LOGIN_STATE,
        json.dumps(
            {
                "on": not ADMIN_LOGIN_OFF.exists(),
                "reach": _login_reach(),
                "password_set": (ETC / "admin-password").is_file(),
            }
        ),
    )


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
    return sorted(
        a.get("name", "?")
        for a in (data.get("accounts") or {}).values()
        if isinstance(a, dict)
        and a.get("role") == "admin"
        and a.get("state") == "user"
        and a.get("hash")
        and a.get("https_login")
    )


def _admin_accounts_ready():
    """An admin account that can sign in (accounts.admin_ready, read here from the hub's file): then the
    admin's routes send a browser to the standard sign-in, never the box's own login. The hub's file could
    say otherwise only to bring back the box's own login prompt, which still asks for its password.
    """
    try:
        data = json.loads((STATE / "accounts.json").read_text())
        return any(
            isinstance(a, dict) and a.get("role") == "admin" and a.get("state") == "user" and a.get("hash")
            for a in (data.get("accounts") or {}).values()
        )
    except (OSError, ValueError, AttributeError):
        return False


def admin_gate(req):
    """The hub's word that the admin accounts changed: the gates written afresh, if they would differ."""
    old = {p: p.read_text() if p.exists() else None for p in _gate_paths()}
    _access_files(access.read(ACCESS_FILE))
    if any((p.read_text() if p.exists() else None) != t for p, t in old.items()):
        _reload_web()
        if _admin_accounts_ready():
            return M(
                "control.admin-gate.accounts",
                "Administrators now sign in with their accounts on the sign-in page of the box.",
            )
        return M(
            "control.admin-gate.script",
            "No administrator account can sign in yet. The administration pages now ask for script sign-in.",
        )
    return M("control.admin-gate.unchanged", "The administrator sign-in did not change.")


def _gate_paths():
    if WEB_SERVER == "nginx":
        return sorted(NGINX_ACCESS.with_name(NGINX_ACCESS.name + ".d").glob("gate-*.conf"))
    return [CADDY_ACCESS / "admin-gate.caddy"]


def _reload_web():
    if WEB_SERVER == "nginx":
        run("systemctl", "reload", "nginx")
    else:
        # The Caddyfile turns Caddy's admin API off, which reload needs.
        subprocess.Popen(  # noqa: TID251
            [
                "systemd-run",
                "--on-active=1",
                *TIMER_EXACT,
                "systemctl",
                "restart",
                "caddy",
            ]
        )


def _caddy_login_swap(new_hash):
    """Every admin hash in the hub's Caddy config replaced by new_hash, checked before it's kept
    (the owner's whole Caddyfile too, when the hub's site is imported into it)."""
    target = _login_file()
    text = target.read_text()
    updated, count = HASH_LINE.subn(lambda m: m.group(1) + new_hash, text)
    if not count:
        raise Refused(_no_hash(target))
    candidate = target.with_name(target.name + ".new")
    candidate.write_text(updated)
    if run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate)).returncode != 0:
        candidate.unlink(missing_ok=True)
        raise Refused(
            M(
                "control.password.caddy-invalid",
                "Caddy did not accept the new configuration. Nothing changed.",
            )
        )
    os.replace(candidate, target)
    if (
        target != CADDYFILE
        and run("caddy", "validate", "--adapter", "caddyfile", "--config", str(CADDYFILE)).returncode != 0
    ):
        target.write_text(text)
        raise Refused(
            M(
                "control.password.caddy-invalid-whole",
                "Caddy did not accept its whole configuration with the new password. Nothing changed.",
            )
        )
    return count


def _no_hash(path):
    return M(
        "control.password.no-hash",
        "The file {path} has no script sign-in password.",
        path=str(path),
    )


def _caddy_login_off():
    """Caddy: the login's hashes swapped for one of a password nobody knows (as nginx's login file
    is emptied); the real one kept in ADMIN_LOGIN_OFF to put back."""
    real = _caddy_hash()
    if not real:
        raise Refused(_no_hash(_login_file()))
    hashed = run("caddy", "hash-password", input=secrets.token_urlsafe(32) + "\n")
    if hashed.returncode != 0 or not hashed.stdout.strip().startswith("$2"):
        raise Refused(_hash_failed("caddy hash-password"))
    fd = os.open(ADMIN_LOGIN_OFF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(real + "\n")
    try:
        _caddy_login_swap(hashed.stdout.strip())
    except ValueError:
        ADMIN_LOGIN_OFF.unlink()
        raise


def admin_login(req):
    """The box's own admin login (basic auth, for scripts): its reach, the admin's choice ("reach": off, local
    (from the box itself only), network (from other devices too)), or on or off ("on", as before: on keeps the
    reach). Off only while an admin account has logged in over HTTPS, so the box can't be locked out from
    /admin. On only with a password set at the console (script-login): none is made here. nginx: its login file
    emptied when off, the network's copy kept or emptied by the reach; Caddy: its hashes swapped for an unknown one,
    its admin gate made a hard one (access.py), and a login from elsewhere dropped when the reach is local.
    """
    reach = req.get("reach")
    if reach is not None:
        if reach not in REACHES:
            raise Refused(
                M(
                    "control.script-sign-in.reach-bad",
                    "The reach must be one of these values: {values}.",
                    values=list(REACHES),
                )
            )
        if reach == "off":
            return admin_login({"on": False})
        if not (ETC / "admin-password").is_file():
            raise Refused(
                M(
                    "control.script-sign-in.no-password",
                    "Script sign-in has no password yet. "
                    "Set one at the console of the box over SSH with this command: {command}",
                    command=SCRIPT_LOGIN_CMD,
                )
            )
        # From the box itself only, with no administrator account that has signed in over HTTPS, nobody on another
        # device can open /admin at all: as much a lockout as turning the login off, so the same rule (_https_admins).
        if reach == "local" and not _https_admins():
            raise Refused(
                M(
                    "control.script-sign-in.local-refused",
                    "Nothing changed. No administrator account of this box has signed in "
                    "over HTTPS. If script sign-in works only from the box itself, no other device can open the "
                    "administration pages. Create an administrator account on Accounts, and sign in with it over HTTPS. "
                    "At the console, this command creates one: {command}",
                    command=SET_ADMIN_CMD,
                )
            )
        _write_reach(reach)
        if ADMIN_LOGIN_OFF.exists():
            _switch_login(True)
        else:
            _access_files(access.read(ACCESS_FILE))
            _reload_web()
            _admin_login_record()
        return _reach_said(reach)
    on = req.get("on") is True
    if on == (not ADMIN_LOGIN_OFF.exists()):
        return ALREADY_ON if on else ALREADY_OFF
    admins = []
    if not on:
        admins = _https_admins()
        if not admins:
            raise Refused(
                M(
                    "control.script-sign-in.off-refused",
                    "Create an administrator account and sign in with it over HTTPS first. "
                    "Then the account can replace script sign-in.",
                )
            )
    _switch_login(on)
    if on:
        return M("control.script-sign-in.on-again", "Script sign-in is enabled again.")
    return M(
        "control.script-sign-in.off.accounts",
        "Script sign-in is disabled. Administrators open {page} with their accounts: {names}.",
        page="/admin",
        names=admins,
    )


SCRIPT_LOGIN_CMD = "sudo /opt/irate-box/irate-box hub_control script-login"
SET_ADMIN_CMD = "sudo /opt/irate-box/irate-box hub_control set-admin NAME"
ALREADY_ON = M("control.script-sign-in.already-on", "Script sign-in is already enabled.")
ALREADY_OFF = M("control.script-sign-in.already-off", "Script sign-in is already disabled.")


def _reach_said(reach):
    if reach == "local":
        return M(
            "control.script-sign-in.reach.local",
            "Script sign-in now accepts requests only from the box itself.",
        )
    return M(
        "control.script-sign-in.reach.network",
        "Script sign-in now accepts requests from every device on the network of the box.",
    )


def _switch_login(on):
    """The box's own login on or off, with no question asked (admin_login asks first; the first use and
    the console need none)."""
    if WEB_SERVER != "nginx":
        if not on:
            _caddy_login_off()
        else:
            _caddy_login_swap(ADMIN_LOGIN_OFF.read_text().strip())
            ADMIN_LOGIN_OFF.unlink()
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
        _admin_login_record()
        return
    if not on:
        fd = os.open(
            ADMIN_LOGIN_OFF,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
        )
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


def _write_reach(reach):
    fd = os.open(ADMIN_LOGIN_REACH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as fh:
        fh.write(reach + "\n")


def claim(req):
    """The first use ended by the owner's admin account (the hub made it): no longer free, and no
    login of the box's own (no password made for scripts: script-login at the console makes one).
    """
    if not UNCLAIMED.exists():
        raise Refused(M("control.setup.already", "Initial setup is already complete."))
    UNCLAIMED.unlink()
    (ETC / "admin-password").unlink(missing_ok=True)
    if not ADMIN_LOGIN_OFF.exists():
        _switch_login(False)
    else:
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
    return M(
        "control.setup.done",
        "Initial setup is complete. Administrators sign in on the sign-in page of the box. "
        "Script sign-in is disabled, so scripts have no password.",
    )


def _ask_password(prompt="Password: "):
    """Asked twice at a terminal, without echo; from a script, two lines on stdin."""
    import getpass

    ask = getpass.getpass if sys.stdin.isatty() else (lambda _p: sys.stdin.readline().rstrip("\n"))
    pw = ask(prompt)
    if not _password_ok(pw):
        sys.exit(render(PASSWORD_LENGTH))
    if ask("Again: ") != pw:
        sys.exit(
            render(
                M(
                    "control.password.mismatch",
                    "The passwords do not match. Nothing changed.",
                )
            )
        )
    return pw


def console_set_admin(name):
    """sudo irate-box hub_control set-admin NAME: an admin account from the console (over SSH, so the
    password never crosses the network in the clear); ends the first use if it is still on.
    """
    pw = _ask_password(f"Password for the administrator account {name}: ")
    out = subprocess.run(  # noqa: TID251
        [
            "runuser",
            "-u",
            HUB_USER,
            "--",
            "env",
            f"HUB_STATE_DIR={STATE}",
            str(Path(__file__).resolve().parents[2] / "irate-box"),
            "accounts",
            "set-admin",
            name,
        ],
        input=pw + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or out.stdout.strip() or "accounts: failed")
    # The accounts command's own line, then what the helper did: for the terminal, in English.
    said = [out.stdout.strip()]
    if UNCLAIMED.exists():
        said.append(claim({}))
    else:
        _access_files(access.read(ACCESS_FILE))
        _reload_web()
    return paragraph(*said)


def console_script_login(on):
    """sudo irate-box hub_control script-login [--off]: the box's own login (user admin, for scripts:
    curl -u admin:… with the password in /etc/hub/admin-password, root's alone), on with a new
    password or off."""
    if not on:
        (ETC / "admin-password").unlink(missing_ok=True)
        if ADMIN_LOGIN_OFF.exists():
            return render(ALREADY_OFF)
        if not _admin_accounts_ready():
            sys.exit(
                render(
                    M(
                        "control.script-sign-in.off-lockout",
                        "No administrator account can sign in yet. "
                        "Create one with this command first: {command}. Otherwise nobody can open {page}.",
                        command=SET_ADMIN_CMD,
                        page="/admin",
                    )
                )
            )
        _switch_login(False)
        return render(M("control.script-sign-in.off", "Script sign-in is disabled."))
    if not ADMIN_LOGIN_REACH.exists():
        _write_reach("local")  # the safer reach to start; widened on Accounts if wanted
    said = set_login(_ask_password(f"Password for script sign-in (user {SCRIPT_USER}): "))
    return paragraph(
        _reach_said(_login_reach()),
        M("control.script-sign-in.change-reach", "To change this, open Accounts."),
        _password_saved(said),
    )


# --- updates ---------------------------------------------------------------------
# The checks a downloaded version passes are messages too (msg.py).

NO_REPO = M(
    "update.options.no-repo",
    "The install options name no repository. Run the installer once by hand to record one.",
)
OPTIONS_MISSING = M(
    "update.options.missing",
    "This box has no record of its install options. It was installed before updates "
    "from the administration pages existed. Run the installer once by hand to record them.",
)


def _install_options():
    try:
        lines = (ETC / "install-options").read_text().splitlines()
    except OSError as exc:
        raise Refused(OPTIONS_MISSING) from exc
    return [line for line in lines if line]


def _option(opts, name, default=None):
    return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else default


def _subcommand(args):
    """The git subcommand in an argument list that may start with -c NAME=VALUE and -C DIR pairs."""
    args = list(args)
    while len(args) > 2 and args[0] in ("-c", "-C"):
        args = args[2:]
    return args[0]


def _git(*args):
    out = run("git", *args, timeout=600)
    if out.returncode != 0:
        # What git printed is evidence, never translated: an argument of the sentence around it.
        raise Refused(
            M(
                "update.git.failed",
                "The command {command} failed: {output}",
                command=f"git {_subcommand(args)}",
                output=_last_line(out.stderr or out.stdout),
            )
        )
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


def _relation(gitdir, installed, head="HEAD"):
    """How a commit stands against the installed one, in a git directory that has both: "same", "ahead" (it continues
    from the installed commit), "behind" (the installed commit already contains it: installing it goes back),
    "diverged" (neither: a branch merged elsewhere, or a history rewritten), or "unknown" (the installed commit is not
    in that history)."""
    if not installed:
        return "unknown"
    git = ("git", "-C", str(gitdir))
    if run(*git, "cat-file", "-e", f"{installed}^{{commit}}").returncode != 0:
        return "unknown"
    if run(*git, "rev-parse", "--verify", "-q", f"{head}^{{commit}}").returncode != 0:
        return "unknown"
    a = _git(*git[1:], "rev-parse", f"{installed}^{{commit}}")
    b = _git(*git[1:], "rev-parse", f"{head}^{{commit}}")
    if a == b:
        return "same"
    if run(*git, "merge-base", "--is-ancestor", a, b).returncode == 0:
        return "ahead"
    if run(*git, "merge-base", "--is-ancestor", b, a).returncode == 0:
        return "behind"
    return "diverged"


def _write_update_state(data):
    safeio.write(UPDATE_STATE, json.dumps(data, indent=2))


def _read_update_state():
    # Root's own file, read only if it still is: it says which commit passed verification.
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
        self.data = {
            "action": action,
            "pid": os.getpid(),
            "step": 0,
            "steps": steps,
            "estimate": estimate,
            "label": "",
            "done": 0,
            "total": 0,
        }
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
# Each check: {"id", "name", "ok", "warn", "detail", "fix", "evidence"}. name (what was checked), detail (what it
# found) and fix are messages; evidence is what it saw (an error line, a file), as data. warn marks one that does not
# block. A state kept from before the messages has name and detail as English text: the page and Install anyway read both.


def _check(cid, ok, name, detail, warn=False, fix=None, evidence=(), note=None):
    return {
        "id": cid,
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "warn": warn,
        "fix": fix,
        "evidence": [str(e) for e in evidence if e],
        **({"note": note} if note else {}),
    }


# What a check of the code's form does not show: said beside the checks that read only the form.
LIMITED_CHECK = M(
    "update.verify.limited",
    "This check reads only the form of the code or of the web server's configuration. "
    "It does not prove that the update is safe.",
)
DOWNLOAD_OK = M("update.download.ok", "The checksum matches.")
DOWNLOAD_MISMATCH = M(
    "update.download.mismatch",
    "The checksum does not match. The file is not the one that was published.",
)
DOWNLOAD_FAILED = M("update.download.failed", "The download failed.")


def _download_title(name, version):
    return M(
        "update.download.title",
        "Download of {name} {version}",
        name=name,
        version=version,
    )


def _arch_names():
    m = platform.machine()
    return {
        "ttyd": {
            "x86_64": "x86_64",
            "aarch64": "aarch64",
            "armv7l": "armhf",
            "armv8l": "armhf",
            "armv6l": "arm",
        }.get(m),
        "sb": {
            "x86_64": "x86_64",
            "aarch64": "aarch64",
            "armv7l": "armv7",
            "armv8l": "armv7",
        }.get(m),
        "caddy": {
            "x86_64": "amd64",
            "aarch64": "arm64",
            "armv7l": "armv7",
            "armv8l": "armv7",
        }.get(m),
    }


def _fetch(url, dest, timeout=300, report=None):
    """Download url to dest, atomically, calling report(done, total) as bytes arrive (total
    is 0 when the server does not say). Returns dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-update"})
    tmp = dest.with_name(dest.name + ".part")
    with _web_open(req, timeout=timeout) as resp, open(tmp, "wb") as out:
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


def _downloading(name, version):
    return M(
        "update.progress.download",
        "Downloading {name} {version}",
        name=name,
        version=version,
    )


def prefetch(src, opts, progress=None):
    """Download into DOWNLOADS the release files the new install.sh will want and the box
    does not already have, each checked against its published checksum. Returns checks.
    The downloads are listed before any starts, so progress knows how many steps they are.
    """
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
                return _check(
                    "download:ttyd",
                    ok,
                    _download_title("ttyd", ttyd),
                    DOWNLOAD_OK if ok else DOWNLOAD_MISMATCH,
                    evidence=[name],
                )
            except OSError as exc:
                return _check(
                    "download:ttyd",
                    False,
                    _download_title("ttyd", ttyd),
                    DOWNLOAD_FAILED,
                    evidence=[exc],
                )

        jobs.append((_downloading("ttyd", ttyd), get_ttyd))

    sb = _var(script, "SB_VERSION")
    if (
        sb
        and "--with-notes" in opts
        and arch["sb"]
        and not _have_version(("/usr/local/bin/silverbullet", "--version"), sb)
    ):

        def get_sb():
            name = f"silverbullet-server-linux-{arch['sb']}.zip"
            try:
                z = _fetch(
                    f"https://github.com/silverbulletmd/silverbullet/releases/download/{sb}/{name}",
                    DOWNLOADS / f"silverbullet-{sb}-{name}",
                    report=report,
                )
                # The digest pinned beside SB_VERSION (GitHub's record of the release asset).
                want = _var(script, f"SB_SHA256_{arch['sb']}")
                ok = bool(want) and hashlib.sha256(Path(z).read_bytes()).hexdigest() == want
                if not want:
                    return _check(
                        "download:silverbullet",
                        False,
                        _download_title("SilverBullet", sb),
                        M(
                            "update.download.no-digest",
                            "The new installer pins no checksum for this file.",
                        ),
                        evidence=[f"SB_SHA256_{arch['sb']}", "install.sh"],
                    )
                return _check(
                    "download:silverbullet",
                    ok,
                    _download_title("SilverBullet", sb),
                    DOWNLOAD_OK if ok else DOWNLOAD_MISMATCH,
                    evidence=[name],
                )
            except OSError as exc:
                return _check(
                    "download:silverbullet",
                    False,
                    _download_title("SilverBullet", sb),
                    DOWNLOAD_FAILED,
                    evidence=[exc],
                )

        jobs.append((_downloading("SilverBullet", sb), get_sb))

    # Caddy from its GitHub release, on a box served by Caddy whose apt repository failed
    # verification.
    if WEB_SERVER == "caddy" and (ETC / "caddy-from-release").exists() and arch["caddy"]:

        def get_caddy():
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/caddyserver/caddy/releases/latest",
                    headers={"User-Agent": "irate-box-update"},
                )
                with _web_open(req, timeout=60) as resp:
                    tag = json.load(resp)["tag_name"]
                ver = tag.lstrip("v")
                installed = run("dpkg-query", "-W", "-f=${Version}", "caddy").stdout.strip()
                (DOWNLOADS / "caddy-release-tag").write_text(tag + "\n")
                if installed == ver:
                    return None
                deb = f"caddy_{ver}_linux_{arch['caddy']}.deb"
                base = f"https://github.com/caddyserver/caddy/releases/download/{tag}"
                sums = _fetch(
                    f"{base}/caddy_{ver}_checksums.txt",
                    DOWNLOADS / f"caddy_{ver}_checksums.txt",
                )
                pkg = _fetch(f"{base}/{deb}", DOWNLOADS / deb, report=report)
                ok = _digest(pkg, "sha512") == _sum_for(sums, deb)
                return _check(
                    "download:caddy",
                    ok,
                    _download_title("Caddy", ver),
                    DOWNLOAD_OK if ok else DOWNLOAD_MISMATCH,
                    evidence=[deb],
                )
            except (OSError, ValueError, KeyError) as exc:
                return _check(
                    "download:caddy",
                    False,
                    M(
                        "update.download.caddy.title",
                        "Download of Caddy's latest release",
                    ),
                    DOWNLOAD_FAILED,
                    evidence=[exc],
                )

        jobs.append(
            (
                M("update.progress.caddy", "Downloading the latest release of Caddy"),
                get_caddy,
            )
        )

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


SIGNED_CHECK = "signed"
SIGNED_OLD = "Signed as this box requires"  # its name in a state kept before the checks had IDs


def _signature_check(src, installed, tag=None):
    """The level chosen on /admin's Updates (signing.py): nothing more at off; GitHub's signature
    on every new commit; or the release tag signed by one of the owner's keys."""
    sig = signing.load(ETC)
    if sig["level"] == "off":
        return []
    if sig["level"] == "github":
        known = (
            bool(installed) and run("git", "-C", str(src), "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
        )
        span = f"{installed}..HEAD" if known else "-1"
        revs = (
            [r for r in run("git", "-C", str(src), "rev-list", span).stdout.split() if r]
            if known
            else [run("git", "-C", str(src), "rev-parse", "HEAD").stdout.strip()]
        )
        ok, detail, seen = signing.check_commits(src, revs, CODE / signing.GITHUB_KEY)
    else:
        # The tag the check fetched; failing that, the newest release tag on what is checked out.
        tag = tag or next(
            iter(
                run(
                    "git",
                    "-C",
                    str(src),
                    "tag",
                    "--points-at",
                    "HEAD",
                    "-l",
                    signing.TAG_GLOB,
                    "--sort=-v:refname",
                ).stdout.split()
            ),
            "",
        )
        ok, detail, seen = (
            signing.check_tag(src, tag, sig["signers"])
            if tag
            else (
                False,
                M(
                    "update.signed.no-tag",
                    "The downloaded version is not a release tag.",
                ),
                [],
            )
        )
    return [
        _check(
            SIGNED_CHECK,
            ok,
            M("update.verify.signed.title", "Signed as this box requires"),
            detail,
            evidence=seen,
            fix=(
                None
                if ok
                else M(
                    "update.verify.signed.fix",
                    "Install anyway cannot skip this check. "
                    "To change the signing level, open Updates, then Update verification.",
                )
            ),
        )
    ]


def update_signing(req):
    data = signing.save(
        ETC,
        str(req.get("level", "")),
        str(req.get("signers", "")),
        lambda path, text: safeio.write(path, text, mode=0o600),
    )
    safeio.write(SIGNING_PUBLIC, json.dumps(signing.public(data)))
    # Not a block: the setting is saved. But an owner who lowers signing under a source that then stops working is told.
    try:
        recorded = _option(_install_options(), "--repo")
    except ValueError:
        recorded = None
    try:
        if recorded:
            _source_check(recorded, data["level"])
    except Refused:
        return M(
            "update.signing.source-refused",
            "Signing is now {level}, and the recorded update source {repo} is refused at this level. "
            "The next check for updates fails until you give an https:// repository under Update source "
            "or select signed updates again.",
            level=data["level"],
            repo=recorded,
        )
    if data["level"] == "off":
        return M(
            "update.signing.off",
            "Updates now accept any version from the branch. The next check uses this setting.",
        )
    if data["level"] == "github":
        return M(
            "update.signing.github",
            "Updates now accept only commits signed by GitHub's key. The next check uses this setting.",
        )
    return M(
        "update.signing.tags",
        {
            "one": "Updates now accept only release tags signed by the trusted key. The next check uses this setting.",
            "other": "Updates now accept only release tags signed by one of {n} trusted keys. "
            "The next check uses this setting.",
        },
        n=len(data["signers"]),
    )


def verify_update(src, installed, opts, progress=None, tag=None):
    """Checks on a fetched tree; any failure not marked warn blocks the install."""
    checks = []
    if installed:
        known = run("git", "-C", str(src), "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
        ff = known and run("git", "-C", str(src), "merge-base", "--is-ancestor", installed, "HEAD").returncode == 0
        # A rewritten branch (the installed commit is known, and not behind HEAD) blocks: the
        # update path is root, and a history that no longer contains what runs here is the
        # sign of a source taken over. A commit the fetched
        # history does not know at all (a box installed from a copy) is only a warning.
        title = M("update.verify.history.title", "Continues from the installed version")
        if ff:
            checks.append(
                _check(
                    "history",
                    True,
                    title,
                    M(
                        "update.verify.history.ok",
                        "The new history continues from the installed commit.",
                    ),
                )
            )
        elif known and _relation(src, installed) == "behind":
            checks.append(
                _check(
                    "history",
                    False,
                    title,
                    M(
                        "update.verify.history.behind",
                        "The downloaded version is older than the installed commit {commit}, which already "
                        "contains it. Installing it would go back.",
                        commit=installed,
                    ),
                    fix=M(
                        "update.verify.history.behind.fix",
                        "Change the branch under Update source in the update doctor, usually to the repository's "
                        "default branch.",
                    ),
                )
            )
        elif known:
            checks.append(
                _check(
                    "history",
                    False,
                    title,
                    M(
                        "update.verify.history.rewritten",
                        "The new version does not continue from the installed commit "
                        "{commit}. The branch history changed.",
                        commit=installed,
                    ),
                    fix=M(
                        "update.verify.history.diverged.fix",
                        "If the branch was made for one change and merged into another, change the branch under "
                        "Update source in the update doctor, usually to the repository's default branch. Use "
                        "Install anyway only for a history you changed yourself.",
                    ),
                )
            )
        else:
            checks.append(
                _check(
                    "history",
                    False,
                    title,
                    M(
                        "update.verify.history.unknown",
                        "The installed commit {commit} is not in the downloaded history. "
                        "The box was installed from another source.",
                        commit=installed,
                    ),
                    warn=True,
                    fix=M(
                        "update.verify.history.unknown.fix",
                        "Read the changes before you install.",
                    ),
                )
            )
    checks += _signature_check(src, installed, tag)
    for script in ("install.sh", "uninstall.sh", "scripts/tailscale-apply.sh"):
        path = src / script
        if path.exists():
            out = run("bash", "-n", str(path))
            checks.append(
                _check(
                    f"parses:{script}",
                    out.returncode == 0,
                    M(
                        "update.verify.parses.title",
                        "Shell syntax of {script}",
                        script=script,
                    ),
                    (
                        M(
                            "update.verify.parses.ok",
                            "The shell finds no syntax error in it.",
                        )
                        if out.returncode == 0
                        else M(
                            "update.verify.parses.failed",
                            "The shell finds a syntax error in it.",
                        )
                    ),
                    evidence=([] if out.returncode == 0 else (out.stderr.strip().splitlines() or [""])[-1:]),
                )
            )
    bad = []
    pys = sorted([*src.glob("irate_box/**/*.py"), *src.glob("scripts/*.py")])
    for py in pys:
        try:
            compile(py.read_text(), str(py), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            bad.append(f"{py.relative_to(src)}: {exc}")
    title = M("update.verify.python.title", "Python files compile")
    if bad:
        checks.append(
            _check(
                "python",
                False,
                title,
                M(
                    "update.verify.python.failed",
                    {
                        "one": "{n} Python file does not compile.",
                        "other": "{n} Python files do not compile.",
                    },
                    n=len(bad),
                ),
                evidence=bad[:5],
            )
        )
    elif pys:
        checks.append(
            _check(
                "python",
                True,
                title,
                M(
                    "update.verify.python.ok",
                    {"one": "{n} file compiles.", "other": "{n} files compile."},
                    n=len(pys),
                ),
                note=LIMITED_CHECK,
            )
        )
    else:
        checks.append(
            _check(
                "python",
                False,
                title,
                M(
                    "update.verify.python.none",
                    "The update has no Python files where the box expects them. This is not the expected layout.",
                ),
                evidence=["irate_box/"],
            )
        )

    if WEB_SERVER == "nginx":
        checks.append(_nginx_check(src, opts))
    caddyfile = src / "config" / "Caddyfile"
    if WEB_SERVER == "caddy" and caddyfile.exists():
        # As install.sh will write it: the box's current login hash in place of the marker.
        current = _login_file().read_text() if _login_file().exists() else ""
        m = HASH_LINE.search(current)
        text = caddyfile.read_text()
        if m:
            text = text.replace(
                "$2a$14$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT",
                m.group(0).split()[-1],
            )
        with tempfile.NamedTemporaryFile("w", suffix=".Caddyfile", delete=False) as tmp:
            tmp.write(text)
        title = M("update.verify.caddy.title", "Syntax of the new Caddyfile")
        try:
            out = run("caddy", "validate", "--adapter", "caddyfile", "--config", tmp.name)
            detail = "" if out.returncode == 0 else ((out.stderr or out.stdout).strip().splitlines() or [""])[-1]
            if out.returncode == 0:
                checks.append(
                    _check(
                        "caddyfile",
                        True,
                        title,
                        M(
                            "update.verify.caddy.ok",
                            "Caddy accepts the new configuration.",
                        ),
                        note=LIMITED_CHECK,
                    )
                )
            else:
                checks.append(
                    _check(
                        "caddyfile",
                        False,
                        title,
                        M(
                            "update.verify.caddy.failed",
                            "Caddy refuses the new configuration.",
                        ),
                        evidence=[detail[:300]],
                    )
                )
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(
                _check(
                    "caddyfile",
                    False,
                    title,
                    M(
                        "update.verify.caddy.failed",
                        "Caddy refuses the new configuration.",
                    ),
                    evidence=[exc],
                )
            )
        finally:
            os.unlink(tmp.name)
    checks += prefetch(src, opts, progress)
    return checks


def _nginx_check(src, opts):
    """The new irate-box.nginx as install.sh will write it, through nginx -t on its own (a
    minimal main config around it, so the box's live config is not touched)."""
    name = M("update.verify.nginx.title", "Syntax of the new nginx site")
    failed = M("update.verify.nginx.failed", "nginx refuses the new site.")
    template = src / "config" / "irate-box.nginx"
    if not template.exists():
        return _check(
            "nginx",
            False,
            name,
            M("update.verify.nginx.missing", "The update has no nginx site file."),
            evidence=["config/irate-box.nginx"],
        )
    text = template.read_text()
    # nobody may not bind a port under 1024, even for a test: an unprivileged one stands in, so
    # the listen lines are still parsed.
    port = _option(opts, "--port", "80")
    if os.geteuid() == 0 and port.isdigit() and int(port) < 1024:
        port = "18080"
    for key, value in {
        "@PORT@": port,
        "@STATIC@": str(CODE / "web"),
        "@APPS@": "/usr/share/hub/apps",
        "@GIT_ROOT@": str(STATE / "git"),
        "@FIRMWARE@": str(STATE / "firmware"),
        "@HTPASSWD@": str(NGINX_LOGINS),
        "@UNCLAIMED@": str(UNCLAIMED),
    }.items():
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
        from irate_box.root import tls

        for path, content in tls._plain_files("http", "allow").items():
            (Path(tmp) / "tls" / path.name).write_text(
                content.replace("listen 80 ", f"listen {port} ").replace("[::]:80 ", f"[::]:{port} ")
            )
        for key, value in {
            "@ADDON_ACCESS@": f"{tmp}/addons.conf",
            "@ADDON_GATES@": f"{tmp}/addon-gates.d",
            "@ADDON_PORT@": "8090",
            "@NOTES_PORT@": "8091",
            "@NOTEBOOK_PORT@": "8094",
            "@ADMIN_PORT@": "8095",
            "@DRAW_PORT@": "8096",
            "@MERMAID_PORT@": "8097",
            "@SYNC_PORT@": "8098",
            "@WIKI_PORT@": "8092",
            "@GIT_PORT@": "8093",
            "@TLS@": f"{tmp}/tls",
            "@TLS_PORT@": "18443",
            "@ADDON_TLS_PORT@": "8490",
            "@NOTES_TLS_PORT@": "8491",
            "@NOTEBOOK_TLS_PORT@": "8494",
            "@ADMIN_TLS_PORT@": "8495",
            "@DRAW_TLS_PORT@": "8496",
            "@MERMAID_TLS_PORT@": "8497",
            "@SYNC_TLS_PORT@": "8498",
            "@WIKI_TLS_PORT@": "8492",
            "@GIT_TLS_PORT@": "8493",
            "@ADDONS@": str(STATE / "addons"),
            "@PAGELOG@": f"{tmp}/pages.log",
        }.items():
            text = text.replace(key, value)
        # A port placeholder this helper does not know yet (an update adds a server block): a
        # spare port stands in, so a newer site is checked by its syntax, not refused for being
        # newer than its checker.
        spare = iter(range(18100, 18200))
        text = re.sub(r"@[A-Z_]+_PORT@", lambda m: str(next(spare)), text)
        conf = Path(tmp) / "nginx.conf"
        # nginx's scratch paths in here too, so it needs nothing of the box's.
        temps = "access_log off;\n" + "".join(
            f"{k}_temp_path {tmp}/{k};\n" for k in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
        )
        conf.write_text(
            f"pid {tmp}/nginx.pid;\nerror_log stderr;\nevents {{}}\n"
            f"http {{\ninclude /etc/nginx/mime.types;\n{temps}{text}\n}}\n"
        )
        # As nobody, not root: this is the fetched commit's config, not yet approved, and
        # nginx -t as root would create any file its error_log or access_log names, or echo an
        # include's first token (a root-only file's) into the report.
        safeio.mkdir(tmp, mode=0o755)
        for f in Path(tmp).rglob("*"):
            f.chmod(0o755 if f.is_dir() else 0o644)
        as_nobody = ()
        if os.geteuid() == 0:
            # nobody's own scratch folder: nginx -t writes its pid and temp paths there.
            nobody = pwd.getpwnam("nobody")
            os.chown(tmp, nobody.pw_uid, nobody.pw_gid)
            as_nobody = ("runuser", "-u", "nobody", "--")
        try:
            out = run(*as_nobody, "nginx", "-t", "-q", "-e", "stderr", "-c", str(conf))
        except (OSError, subprocess.SubprocessError) as exc:
            return _check(
                "nginx",
                False,
                name,
                M(
                    "update.verify.nginx.unavailable",
                    "The compatibility check could not run. Installation still checks the actual nginx configuration.",
                ),
                warn=True,
                evidence=[exc],
            ) | {"outcome": "inconclusive"}
    lines = [line for line in (out.stderr or out.stdout).strip().splitlines() if "[emerg]" in line or "[crit]" in line]
    if out.returncode == 0:
        return _check(
            "nginx",
            True,
            name,
            M("update.verify.nginx.ok", "nginx accepts the new site."),
            note=LIMITED_CHECK,
        )
    return _check(
        "nginx",
        False,
        name,
        failed,
        warn=True,
        fix=M(
            "update.verify.nginx.advisory",
            "This is an isolated compatibility check, not the installed configuration. Review the evidence in the Updates doctor. Installation must still pass its full nginx check.",
        ),
        evidence=[(lines or ["nginx -t failed"])[-1][:300]],
    ) | {
        "outcome": "inconclusive"
        if any(f'"{tmp}/tls/' in line and "Permission denied" in line for line in lines)
        else "rejected"
    }


# What may be an update source (update-source-policy): the transport protects the code only when signing does not.
# Never, at any level: a sign-in or query in the address, a leading "-", a "<transport>::" remote helper (ext::
# runs commands), ssh and scp-style addresses. Signing off: https. Signing github or tags (the code is vouched for
# by its signatures): also http, git, and a local path or file://.
SOURCE_SCHEMES = {
    "off": ("https",),
    "github": ("https", "http", "git", "file"),
    "tags": ("https", "http", "git", "file"),
}


def _source_level():
    """The signing level, read from root's own file each time a source is set or used."""
    return signing.load(ETC)["level"]


def _source_form(value):
    """(address, scheme) for an address of a form that is allowed at some level, or None. The scheme is git's own
    name for the transport: https, http, git or file (a local path too)."""
    if not value or len(value) > 300 or not value.isprintable() or value.startswith("-"):
        return None
    if any(c in value for c in " \\?#") or re.match(r"^[A-Za-z0-9+.-]*::", value):
        return None
    if value.startswith("/"):
        return value, "file"
    try:
        parts = urllib.parse.urlsplit(value)
        host, _ = parts.hostname, parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc or parts.query or parts.fragment:
        return None
    if parts.scheme in ("https", "http", "git") and host and "://" in value:
        return value, parts.scheme
    if parts.scheme == "file" and not parts.netloc and parts.path.startswith("/"):
        return value, "file"
    return None


def _source_check(value, level=None):
    """The address, if it may be an update source at this signing level (the current one when not given); a
    Refused that says what is wrong with it otherwise. The one rule: every place that sets or uses a source asks."""
    level = level or _source_level()
    value = str(value).strip()
    form = _source_form(value)
    if not form:
        raise Refused(
            M(
                "update.source.repo.bad",
                "That repository address cannot be used. Give an https:// address. A local path, file://, http:// "
                "or git:// address works with signed updates. Never accepted: a user name, password, "
                "query or fragment, ssh and scp-style addresses, and git's remote helpers such as ext::.",
            )
        )
    if form[1] not in SOURCE_SCHEMES[level]:
        raise Refused(
            M(
                "update.source.level",
                "Updates are not signed, so the repository must be an https:// address. {repo} works once "
                "updates are signed: select GitHub's signatures or signed releases under Updates, then "
                "Update verification.",
                repo=value,
            )
        )
    return value


def _source_protocols(level=None):
    """git's own allow-list for a source, as the arguments before the subcommand: no transport but the ones this
    level allows, so that a redirect or a submodule cannot reach another."""
    level = level or _source_level()
    return [
        "-c",
        "protocol.allow=never",
        *(a for s in SOURCE_SCHEMES[level] for a in ("-c", f"protocol.{s}.allow=always")),
    ]


def _recorded_source(repo):
    """The recorded update repository, if the rule allows it at the current signing level, checked every time it is
    used: lowering signing later refuses the next check. Refused with what to do, before git or the cache is
    touched."""
    try:
        return _source_check(repo)
    except Refused as exc:
        if exc.message["id"] == "update.source.level":
            raise Refused(
                M(
                    "update.check.source-level",
                    "Updates come from {repo}, which the box accepts only when updates are signed. "
                    "Select GitHub's signatures or signed releases under Updates, then Update verification. "
                    "Or give an https:// repository under Update source.",
                    repo=repo,
                )
            ) from None
        raise Refused(
            M(
                "update.check.source-bad",
                "Updates come from {repo}, which is not an address the box accepts. "
                "Run the installer again with --repo and a repository address, "
                "without a user name, password, query or fragment.",
                repo=repo,
            )
        ) from None


def _check_for_update(progress):
    """Bring the cache up to the recorded branch and work out what is new: two steps.
    Returns (state for update.json, install options)."""
    opts = _install_options()
    repo = _option(opts, "--repo")
    branch = _option(opts, "--branch", "main")
    if not repo:
        raise Refused(NO_REPO)
    repo = _recorded_source(repo)
    net = _source_protocols()
    progress.step(
        M(
            "update.progress.branch",
            "Downloading the branch {branch} from {repo}",
            branch=branch,
            repo=repo,
        )
    )
    src = str(UPDATE_SRC)
    if (UPDATE_SRC / ".git").is_dir():
        _git("-C", src, "remote", "set-url", "origin", repo)
        _git(*net, "-C", src, "fetch", "--depth", "200", "origin", branch)
        _git("-C", src, "reset", "--hard", "FETCH_HEAD")
        _git("-C", src, "clean", "-fdx")
    else:
        if UPDATE_SRC.exists():
            subprocess.run(["rm", "-rf", src], check=True)  # noqa: TID251
        UPDATE_SRC.parent.mkdir(parents=True, exist_ok=True)
        _git(*net, "clone", "--depth", "200", "--branch", branch, repo, src)
    sig = signing.load(ETC)
    tag = None
    # The release tags (v*), fetched whatever the signing level, so the box holds them: needed when
    # signed releases are chosen, kept otherwise (their failing then only noted).
    tags_said = None
    got = run(
        "git",
        *net,
        "-C",
        src,
        "fetch",
        "--depth",
        "200",
        "--force",
        "origin",
        f"+refs/tags/{signing.TAG_GLOB}:refs/tags/{signing.TAG_GLOB}",
    )
    if got.returncode != 0:
        # git says nothing when no tag matches: tell that apart from a fetch that failed.
        remote = run(
            "git",
            *net,
            "-C",
            src,
            "ls-remote",
            "--tags",
            "origin",
            f"refs/tags/{signing.TAG_GLOB}",
        )
        if remote.returncode == 0 and not remote.stdout.strip():
            tags_said = (
                M(
                    "update.check.no-tags",
                    "Updates require signed releases, and {repo} has no release tag yet.",
                    repo=repo,
                )
                if sig["level"] == "tags"
                else M(
                    "update.check.no-tags-yet",
                    "The repository {repo} has no release tag yet.",
                    repo=repo,
                )
            )
        else:
            tags_said = M(
                "update.check.tags-failed",
                "Downloading the release tags failed: {error}",
                error=((got.stderr or remote.stderr).strip().splitlines() or ["failed"])[-1],
            )
        if sig["level"] == "tags":
            raise Refused(tags_said)
    newest = signing.newest_tag(src)
    if sig["level"] == "tags":
        # Signed releases (signing.py): the newest v* tag, not the branch's tip.
        tag = newest
        if not tag:
            raise Refused(
                M(
                    "update.check.no-tags",
                    "Updates require signed releases, and {repo} has no release tag yet.",
                    repo=repo,
                )
            )
        _git("-C", src, "checkout", "-q", "--detach", f"refs/tags/{tag}")
    progress.step(M("update.progress.changes", "Reading the changes"))
    head = _git("-C", src, "rev-parse", "--short=7", "HEAD")
    full = _git("-C", src, "rev-parse", "HEAD")
    installed = _installed_commit()
    known = bool(installed) and run("git", "-C", src, "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
    span = f"{installed}..HEAD" if known else "-10"
    changes = [c for c in _git("-C", src, "log", "--format=%h %cs %s", span).splitlines() if c]
    relation = _relation(src, installed)
    up_to_date = bool(installed) and head.startswith(installed[:7]) or (known and not changes)
    if relation == "diverged":
        # The commits counted are the branch's own, not new ones: nothing here continues from what runs.
        changes = []
    state = {
        "repo": repo,
        "branch": branch,
        "available": head,
        "available_sha": full,
        "available_date": _git("-C", src, "log", "-1", "--format=%cs"),
        "installed": installed,
        "relation": relation,
        "up_to_date": up_to_date,
        "changes": changes[:50],
        "changes_known": known,
        "fetched": time.time(),
        "verified": None,
        "verified_sha": None,
        "checks": [],
        "signing": sig["level"],
        "tag": tag,
        "release_tag": newest,
        "tags_problem": tags_said,
    }
    # The same commit fetched before: its checks and downloads still stand. Matched on the
    # whole hash: a commit sharing the first 7 digits must not inherit "verified".
    old = _read_update_state()
    if not up_to_date and old.get("verified_sha") == full:
        state.update(verified=head, verified_sha=full, checked_sha=full, checks=old.get("checks", []))
    if old.get("install_steps"):
        state["install_steps"] = old["install_steps"]
    return state, opts


def _up_to_date(state):
    return M(
        "update.check.none",
        "No newer version detected. The box is up to date with {commit} on {branch}.",
        commit=state["available"],
        branch=state["branch"],
    )


def update_check(req):
    with Progress("check", 2) as progress:
        state, _ = _check_for_update(progress)
    _write_update_state(state)
    commit, branch, n = state["available"], state["branch"], len(state["changes"])
    if state.get("relation") == "behind":
        return M(
            "update.check.behind",
            "The branch {branch} is behind the installed version {installed}: it holds nothing newer, so this box "
            "receives no updates from it. Change the branch under Update source in the update doctor, usually to "
            "the repository's default branch.",
            branch=branch,
            installed=state["installed"],
        )
    if state.get("relation") == "diverged":
        return M(
            "update.check.diverged",
            "The branch {branch} ({commit}) does not continue from the installed version {installed}. A branch made "
            "for one change is merged into another and then left behind. Change the branch under Update source in "
            "the update doctor, usually to the repository's default branch.",
            branch=branch,
            commit=commit,
            installed=state["installed"],
        )
    if state["up_to_date"]:
        return _up_to_date(state)
    # The changes are counted only when the history continues from the installed commit.
    if state["verified"]:
        if state["changes_known"]:
            return M(
                "update.check.new-verified",
                {
                    "one": "Update available: {commit} on {branch}, with {n} new commit. It is downloaded, "
                    "and verification passed.",
                    "other": "Update available: {commit} on {branch}, with {n} new commits. It is downloaded, "
                    "and verification passed.",
                },
                commit=commit,
                branch=branch,
                n=n,
            )
        return M(
            "update.check.other-verified",
            "Update available: a different version, {commit} on {branch}. It is downloaded, and verification passed.",
            commit=commit,
            branch=branch,
        )
    if state["changes_known"]:
        return M(
            "update.check.new",
            {
                "one": "Update available: {commit} on {branch}, with {n} new commit. Download it to verify it.",
                "other": "Update available: {commit} on {branch}, with {n} new commits. Download it to verify it.",
            },
            commit=commit,
            branch=branch,
            n=n,
        )
    return M(
        "update.check.other",
        "Update available: a different version, {commit} on {branch}. Download it to verify it.",
        commit=commit,
        branch=branch,
    )


def update_fetch(req):
    # Steps: the verification, plus the check if one is needed, plus each download
    # (prefetch adds those once it knows them).
    with Progress("fetch", 1) as progress:
        state = _read_update_state()
        cached = (UPDATE_SRC / ".git").is_dir() and run(
            "git", "-C", str(UPDATE_SRC), "rev-parse", "--short=7", "HEAD"
        ).stdout.strip()
        if state.get("up_to_date") or not cached or cached != state.get("available"):
            progress.add_steps(2)
            state, opts = _check_for_update(progress)
        else:
            opts = _install_options()
        if state["up_to_date"]:
            _write_update_state(state)
            return _up_to_date(state)
        progress.step(M("update.progress.verify", "Checking the new version"))
        checks = verify_update(UPDATE_SRC, state["installed"], opts, progress, tag=state.get("tag"))
    head = state["available"]
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    failed = [c for c in checks if not c["ok"] and not c["warn"]]
    state.update(
        checks=checks,
        checked_sha=full,
        verified=None if failed else head,
        verified_sha=None if failed else full,
    )
    _write_update_state(state)
    if failed:
        _doctor_again()
        return M(
            "update.fetch.failed",
            {
                "one": "Verification failed. The downloaded version {commit} did not pass {n} check: {checks}.",
                "other": "Verification failed. The downloaded version {commit} did not pass {n} checks: {checks}.",
            },
            commit=head,
            n=len(failed),
            checks=[c["name"] for c in failed],
        )
    if state["changes_known"]:
        return M(
            "update.fetch.done",
            {
                "one": "Download complete. Verification passed: {commit} on {branch}, with {n} new commit.",
                "other": "Download complete. Verification passed: {commit} on {branch}, with {n} new commits.",
            },
            commit=head,
            branch=state["branch"],
            n=len(state["changes"]),
        )
    return M(
        "update.fetch.done-other",
        "Download complete. Verification passed: a different version, {commit} on {branch}.",
        commit=head,
        branch=state["branch"],
    )


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
                # The installer's own heading: its words, as data.
                progress.step(m.group(1).decode(errors="replace"))
        if code is not None:
            return code, seen
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            raise Refused(
                M(
                    "update.install.timeout",
                    {
                        "one": "The installer did not finish within {n} minute. Read the installer output on Updates.",
                        "other": "The installer did not finish within {n} minutes. Read the installer output on Updates.",
                    },
                    n=INSTALL_TIMEOUT // 60,
                )
            )
        time.sleep(1)


def update_reach(req):
    endpoint = req.get("endpoint")
    if endpoint not in ("download", "install"):
        raise Refused(M("update.reach.endpoint", "Unknown update endpoint."))
    state = _read_update_state()
    expected = req.get("candidate")
    if expected and expected != state.get("available_sha"):
        raise Refused(M("update.reach.changed", "The update changed. Review it before continuing."))
    if not state or state.get("up_to_date") or not state.get("available_sha"):
        update_check(req)
        state = _read_update_state()
    if state.get("up_to_date"):
        return _up_to_date(state)
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    if state.get("available_sha") != full:
        raise Refused(M("update.reach.source", "The downloaded source changed. Check for updates again."))
    result = None
    if state.get("checked_sha") != full:
        result = update_fetch(req)
        state = _read_update_state()
    if state.get("verified_sha") != full:
        raise Refused(
            M("update.reach.blocked", "Verification did not pass. Open the Updates doctor to review the failure.")
        )
    if endpoint == "install":
        return update_install(req)
    return result or M("update.download.ready", "The update is downloaded and verified.")


def update_install(req):
    if not (UPDATE_SRC / "install.sh").is_file():
        raise Refused(
            M(
                "update.install.none",
                "No update is downloaded yet. Check for updates first.",
            )
        )
    state = _read_update_state()
    # The whole hash of what is checked out, against the whole hash that passed.
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    if not state.get("verified_sha") or state.get("verified_sha") != full:
        raise Refused(
            M(
                "update.install.not-verified",
                "The downloaded version has not passed verification. Download the update again.",
            )
        )
    return _install_fetched(state)


def _install_fetched(state):
    seen = _run_install(UPDATE_SRC, _install_options(), "install", state.get("install_steps"))
    state = _read_update_state()
    if state:
        state.update(
            installed=_installed_commit(),
            up_to_date=True,
            changes=[],
            install_steps=seen,
        )
        _write_update_state(state)
    return _installed_now()


def _installed_now():
    commit = _installed_commit()
    return (
        M("update.install.done", "Updated to {commit}.", commit=commit)
        if commit
        else M("update.install.done-unknown", "Updated to the downloaded version.")
    )


def _force_failed(state):
    """The failed checks that Install anyway skips: those that failed and are not only warnings."""
    return [c for c in state.get("checks") or [] if not c.get("ok") and not c.get("warn")]


def _force_refusal(state):
    """Why Install anyway cannot install the fetched version now, as a message, or None when it can. The one rule:
    the update doctor offers the button by it, and update_force_install refuses by it."""
    if not (UPDATE_SRC / "install.sh").is_file():
        return M(
            "update.force.none",
            "No update is downloaded yet. Check for updates, then download the update.",
        )
    full = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")
    if state.get("checked_sha") != full or not state.get("checks"):
        return M(
            "update.force.not-checked",
            "Install anyway works only on a downloaded version that the box checked. Download the update first.",
        )
    if _relation(UPDATE_SRC, _installed_commit()) == "behind":
        # Install anyway is for a check that is wrong, never for going back to an older version.
        return M(
            "update.force.older",
            "The downloaded version is older than the installed one, which already contains it. Install anyway "
            "does not go back. Change the branch under Update source instead.",
        )
    if run("bash", "-n", str(UPDATE_SRC / "install.sh")).returncode != 0:
        return M(
            "update.force.parse",
            "The downloaded installer does not pass the shell syntax check. Install anyway cannot skip this check.",
        )
    if any(c.get("id") == SIGNED_CHECK or c.get("name") == SIGNED_OLD for c in _force_failed(state)):
        # The point of a signing level (signing.py): not even Install anyway passes it.
        return M(
            "update.force.unsigned",
            "Verification failed. The downloaded version does not meet the signing level of this box. "
            "Install anyway cannot skip this check. To change the signing level, open Updates, "
            "then Update verification.",
        )
    return None


def update_force_install(req):
    """The fetched version, installed although it failed verification (the Updates doctor's
    "Install anyway"): for when the check is what is wrong, as when an update changes the
    code's layout and this box's check cannot read it. Still only the version that was fetched
    and checked, and only if its installer parses; the installer stops on its own errors.
    """
    state = _read_update_state()
    why = _force_refusal(state)
    if why:
        raise Refused(why)
    failed = _force_failed(state)
    head = _git("-C", str(UPDATE_SRC), "rev-parse", "HEAD")[:7]
    _install_fetched(state)
    commit = _installed_commit() or head
    if failed:
        # Exactly what was skipped, by name.
        return M(
            "update.force.done",
            {
                "one": "Updated to {commit}. Install anyway skipped {n} failed check: {checks}.",
                "other": "Updated to {commit}. Install anyway skipped {n} failed checks: {checks}.",
            },
            commit=commit,
            n=len(failed),
            checks=[c["name"] for c in failed],
        )
    return _installed_now()


def _run_install(src, args, action, steps=None):
    """Run src's install.sh with args, its output in control/update.log and its "==> " steps in
    update-progress.json. steps: what the last install here printed (a guess until one has).
    Returns the steps it printed; raises ValueError if it fails."""
    cmd = ["bash", str(src / "install.sh"), "--src", str(src), *args]
    if "--download-cache" in (src / "install.sh").read_text():
        cmd += ["--download-cache", str(DOWNLOADS)]
    # systemd gives the helper no HOME, and Caddy (the fallback) warns about it on every validate.
    env = dict(os.environ, HOME=os.environ.get("HOME", "/root"))
    with (
        Progress(action, steps or INSTALL_STEPS_GUESS, estimate=not steps) as progress,
        safeio.open_new(UPDATE_LOG) as log,
    ):
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)  # noqa: TID251
        code, seen = _follow_install(proc, progress)
    if code != 0:
        raise Refused(
            M(
                "update.install.failed",
                "The installer stopped with exit code {code}. Read the installer output on Updates.",
                code=code,
            )
        )
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
        raise Refused(M("control.addon.unknown", "{addon} is not an add-on.", addon=aid))
    on = req.get("on") is True
    option = spec["addon"]["option"]
    title = spec["addon"]["title"]
    opts = _install_options()
    if (option in opts) == on:
        raise Refused(
            M(
                "control.addon.already-added",
                "The add-on {title} is already added.",
                title=title,
            )
            if on
            else M(
                "control.addon.already-removed",
                "The add-on {title} is already removed.",
                title=title,
            )
        )
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
    if on:
        return M("control.addon.added", "The add-on {title} is added.", title=title)
    return M("control.addon.removed", "The add-on {title} is removed.", title=title)


# --- the Updates doctor (one of three: docs/dev/doctors.md) --------------------------
# For when "Check for updates" fails: look at everything an update depends on and say, in
# plain words, what is wrong and what to do. Read-only apart from `apt-get update`, which
# only refreshes package lists. Each finding in finding.py's shape: whole sentences (msg.M) with
# stable IDs, commands and what the check saw as data, and the place on the admin page where its cure
# is chosen (go), for what is not fine.

_go = health._go
GO_NETWORK, GO_CLOCK, GO_BOOKS, GO_UPDATES = (
    health.GO_NETWORK,
    health.GO_CLOCK,
    health.GO_BOOKS,
    health.GO_UPDATES,
)
GO_LIBRARIAN = _go("sources", M("go.librarian", "The librarian"))
GO_TOKEN = _go("sources", M("go.source-access", "The librarian → Source access"), "library-token")
# The Update source form's two fields: the link opens the fold, scrolls to the field and marks it.
GO_SOURCE_REPO = _go("updoctor", M("go.update-source", "Update source"), "update-source-repo")
GO_SOURCE_BRANCH = _go("updoctor", M("go.update-source", "Update source"), "update-source-branch")
GO_TOOLKITS = _go("toolkits", M("go.toolkits", "Toolkits"))

# One failed attempt says what happened, not why.
PROBE_NOTE = M(
    "update.probe.note",
    "One failed attempt does not show the cause. The server or the network can fail for a short time. "
    "Run the doctor again to see if it stays.",
)
CLOCK_WHY = M(
    "update.clock.why",
    "Certificate checks need the correct time. With a wrong clock, every HTTPS connection can fail.",
)
CLOCK_FIX = M(
    "update.clock.fix",
    "Set the time on Clock. Or connect the box to a network with internet access, so that it can set its clock.",
)
CLOCK_SET = "timedatectl set-time 'YYYY-MM-DD HH:MM'"
LIBRARIAN_LOG = "journalctl -u irate-box-librarian -n 80"
CACHE_CLEAR = M(
    "update.cache.clear.fix",
    "Clear the update cache in this doctor. Then check for updates again.",
)
CACHE_CLEAR_ACTION = finding_action(
    "clear-cache",
    M("update.cache.clear.button", "Clear the update cache"),
    M(
        "update.cache.clear.confirm",
        "Delete the cached copy of the repository and its downloads? The next check starts again from the beginning.",
    ),
)


def _local_addresses():
    out = run("ip", "-o", "addr", "show", timeout=10).stdout
    return set(re.findall(r"inet6? ([0-9a-f.:]+)/", out))


def _tls_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-doctor"})
    return _web_open(req, timeout=timeout)


def updates_doctor_findings():
    """The Updates doctor: what an update needs from outside the box, as findings (docs/dev/doctors.md)."""
    import socket
    import ssl
    from email.utils import parsedate_to_datetime

    findings = []

    # 1. How this box updates.
    title = M("update.options.title", "Install options")
    try:
        opts = _install_options()
        repo, branch = _option(opts, "--repo"), _option(opts, "--branch", "main")
        if repo:
            try:
                _recorded_source(repo)
                bad_source = None
            except Refused as exc:
                bad_source = exc.message
            if bad_source:
                # git is not run against it: not here, not in a check, not in an install
                findings.append(
                    finding(
                        "options",
                        title,
                        "problem",
                        bad_source,
                        M(
                            "update.options.bad-source.why",
                            "Updates run as root. Without signed updates, only an https:// repository is trusted "
                            "as their source.",
                        ),
                        M(
                            "update.options.bad-source.fix",
                            "Give an https:// repository under Update source in this doctor's recovery actions. "
                            "Or select GitHub's signatures or signed releases under Updates, then Update verification.",
                        ),
                        evidence=[ETC / "install-options"],
                        go=GO_SOURCE_REPO,
                    )
                )
                repo = None
            else:
                findings.append(
                    finding(
                        "options",
                        title,
                        "ok",
                        M(
                            "update.options.ok",
                            "Updates come from the branch {branch} of {repo}.",
                            branch=branch,
                            repo=repo,
                        ),
                    )
                )
        else:
            findings.append(
                finding(
                    "options",
                    title,
                    "problem",
                    NO_REPO,
                    evidence=[ETC / "install-options"],
                )
            )
    except ValueError:
        opts, repo, branch = [], None, "main"
        findings.append(
            finding(
                "options",
                title,
                "problem",
                M(
                    "update.options.none",
                    "The box has no record of where its updates come from.",
                ),
                M(
                    "update.options.none.why",
                    "This box was installed before updates from the administration pages existed. "
                    "A check for updates cannot start without the record.",
                ),
                M(
                    "update.options.none.fix",
                    "Run the installer once by hand. It records the install options.",
                ),
                evidence=[ETC / "install-options"],
            )
        )

    # 1b. A target the installer picked up by accident. The installer records the branch of the
    # checkout it ran from, so an install from a topic branch leaves the box following that branch,
    # which is merged and deleted soon after. Said as a problem when the repository (asked last) says the branch is
    # gone, behind the installed version, or merged into the default; else a warning when it is not main.
    try:
        asked = json.loads(BRANCHES_STATE.read_text())
    except (OSError, ValueError):
        asked = {}
    standing = asked.get("standing") if asked.get("branch") == branch and asked.get("repo") == repo else None
    left = {
        "gone": M(
            "update.target.gone",
            "Updates follow the branch {branch}, which the repository no longer has. The box finds no updates.",
            branch=branch,
        ),
        "behind": M(
            "update.target.behind",
            "Updates follow the branch {branch}, which is behind the installed version. The box finds no updates.",
            branch=branch,
        ),
        "elsewhere": M(
            "update.target.elsewhere",
            "Updates follow the branch {branch}, which does not contain the installed version. The default branch "
            "{default} does. Every update would fail the check that it continues from the installed version.",
            branch=branch,
            default=asked.get("default") or "main",
        ),
        "merged": M(
            "update.target.merged",
            "Updates follow the branch {branch}, which is already merged into the default branch {default}.",
            branch=branch,
            default=asked.get("default") or "main",
        ),
    }.get(standing)
    if repo and left:
        findings.append(
            finding(
                "target",
                M("update.target.title", "Update branch"),
                "problem",
                left,
                M(
                    "update.target.left.why",
                    "A branch made for one change is merged and then left behind. Install anyway would go back, or "
                    "skip the check that guards against a history taken over.",
                ),
                M(
                    "update.target.left.fix",
                    "Select {default} under Update source in this doctor's recovery actions.",
                    default=asked.get("default") or "main",
                ),
                evidence=[ETC / "install-options", BRANCHES_STATE],
                go=GO_SOURCE_BRANCH,
            )
        )
    elif repo and branch != "main":
        findings.append(
            finding(
                "target",
                M("update.target.title", "Update branch"),
                "warn",
                M(
                    "update.target.branch",
                    "Updates follow the branch {branch}, not main. The installer records the branch of the "
                    "checkout it was run from.",
                    branch=branch,
                ),
                M(
                    "update.target.branch.why",
                    "A branch made for one change is merged and then removed. The box would then stop finding "
                    "updates. Until then it receives only that branch, not the releases on main.",
                ),
                M(
                    "update.target.branch.fix",
                    "If this is not intended, change the branch under Update source in this doctor's recovery "
                    "actions. Or run the installer once with --branch main.",
                ),
                evidence=[ETC / "install-options"],
                go=GO_SOURCE_BRANCH,
                actions=[
                    finding_action(
                        "poll-target",
                        M(
                            "update.target.poll",
                            "Ask the repository for its default branch",
                        ),
                    )
                ],
            )
        )

    # 2. Names: on the hub's own access point every name resolves to the box itself.
    host = "github.com"
    title = M("update.dns.title", "Name lookup (DNS)")
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
        mine = addrs & (_local_addresses() | {"127.0.0.1", "::1"})
        if mine:
            findings.append(
                finding(
                    "dns",
                    title,
                    "problem",
                    M(
                        "update.dns.self",
                        "The DNS lookup of {host} failed. The name leads to this box itself.",
                        host=host,
                    ),
                    M(
                        "update.dns.self.why",
                        "In hotspot mode, the box answers every name lookup itself.",
                    ),
                    M(
                        "update.dns.self.fix",
                        "Connect the box to a network with internet access. "
                        "Or update from a laptop that has internet access.",
                    ),
                    evidence=sorted(mine),
                    go=GO_NETWORK,
                )
            )
        else:
            findings.append(
                finding(
                    "dns",
                    title,
                    "ok",
                    M("update.dns.ok", "The name {host} resolves.", host=host),
                    evidence=sorted(addrs)[:3],
                )
            )
    except OSError as exc:
        addrs = set()
        findings.append(
            finding(
                "dns",
                title,
                "problem",
                M("update.dns.failed", "The DNS lookup of {host} failed.", host=host),
                M(
                    "update.dns.failed.why",
                    "Without names, the box cannot reach the repository or the package sources.",
                ),
                M(
                    "update.dns.failed.fix",
                    "Check the network that the box uses: Ethernet, or the WiFi network that it joined.",
                ),
                notes=[PROBE_NOTE],
                evidence=[exc],
                go=GO_NETWORK,
            )
        )

    # 3. A route out.
    reachable = False
    title = M("update.connection.title", "Connection to GitHub")
    if addrs:
        try:
            with socket.create_connection((host, 443), timeout=8):
                reachable = True
            findings.append(
                finding(
                    "connection",
                    title,
                    "ok",
                    M("update.connection.ok", "{host} answers on port 443.", host=host),
                )
            )
        except OSError as exc:
            findings.append(
                finding(
                    "connection",
                    title,
                    "problem",
                    M(
                        "update.connection.failed",
                        "The HTTPS connection to {host} failed.",
                        host=host,
                    ),
                    fix=M(
                        "update.connection.failed.fix",
                        "Check that the network has internet access and lets HTTPS through. "
                        "If it does, try another network.",
                    ),
                    notes=[PROBE_NOTE],
                    evidence=[exc],
                    go=GO_NETWORK,
                )
            )

    # 4. TLS, and the clock it depends on. A board with no RTC can boot years in the past,
    #    and then every certificate looks not-yet-valid.
    now = time.time()
    installed_on = None
    try:
        m = re.search(r"installed (\d{4}-\d{2}-\d{2})", (CODE / "VERSION").read_text())
        installed_on = time.mktime(time.strptime(m.group(1), "%Y-%m-%d")) if m else None
    except OSError:
        pass
    clock = M("update.clock.title", "Clock")
    tls = M("update.tls.title", "HTTPS certificate")
    url = f"https://{host}/"
    if reachable:
        try:
            with _tls_get(url) as resp:
                server = parsedate_to_datetime(resp.headers["Date"]).timestamp()
            skew = now - server
            if abs(skew) > 600:
                gap = duration(round(abs(skew)))
                findings.append(
                    finding(
                        "clock",
                        clock,
                        "problem",
                        (
                            M(
                                "update.clock.ahead",
                                "The box's clock is {gap} ahead of {host}.",
                                gap=gap,
                                host=host,
                            )
                            if skew > 0
                            else M(
                                "update.clock.behind",
                                "The box's clock is {gap} behind {host}.",
                                gap=gap,
                                host=host,
                            )
                        ),
                        CLOCK_WHY,
                        CLOCK_FIX,
                        commands=[CLOCK_SET],
                        go=GO_CLOCK,
                    )
                )
            else:
                findings.append(
                    finding(
                        "clock",
                        clock,
                        "ok",
                        M(
                            "update.clock.ok",
                            "The clock is within {gap} of {host}.",
                            gap=duration(round(abs(skew))),
                            host=host,
                        ),
                    )
                )
            findings.append(
                finding(
                    "tls",
                    tls,
                    "ok",
                    M("update.tls.ok", "The certificate of {url} is valid.", url=url),
                )
            )
        except ssl.SSLError as exc:
            findings.append(
                finding(
                    "tls",
                    tls,
                    "problem",
                    M(
                        "update.tls.failed",
                        "Certificate validation failed for {url}.",
                        url=url,
                    ),
                    fix=M("update.tls.failed.fix", "Check the clock first."),
                    notes=[
                        M(
                            "update.tls.failed.causes",
                            "Possible causes are a wrong clock, missing CA certificates, "
                            "or a network that intercepts HTTPS.",
                        )
                    ],
                    evidence=[exc],
                    go=GO_CLOCK,
                )
            )
        except (OSError, KeyError, TypeError, ValueError) as exc:
            findings.append(
                finding(
                    "tls",
                    tls,
                    "problem",
                    M(
                        "update.tls.error",
                        "The HTTPS request to {url} failed.",
                        url=url,
                    ),
                    notes=[PROBE_NOTE],
                    evidence=[exc],
                    go=GO_CLOCK,
                )
            )
    if installed_on and now < installed_on - 86400:
        findings.append(
            finding(
                "clock-installed",
                clock,
                "problem",
                M(
                    "update.clock.before-install",
                    "The clock says {date}. That is before the box was installed.",
                    date=time.strftime("%Y-%m-%d", time.gmtime(now)),
                ),
                CLOCK_WHY,
                CLOCK_FIX,
                commands=[CLOCK_SET],
                go=GO_CLOCK,
            )
        )

    # 5. GitHub's API, which the librarian and Caddy's release lookup use.
    title = M("update.github-api.title", "GitHub API")
    if reachable:
        try:
            with _tls_get("https://api.github.com/rate_limit") as resp:
                core = json.load(resp)["resources"]["core"]
            if core["remaining"] == 0:
                wait = round(max(0, core["reset"] - now) / 60)
                findings.append(
                    finding(
                        "github-api",
                        title,
                        "problem",
                        M(
                            "update.github-api.used-up",
                            "The GitHub API rate limit is used up. Without a token, the limit is 60 requests an hour.",
                        ),
                        M(
                            "update.github-api.why",
                            "The librarian and the lookup of Caddy's latest release use the GitHub API.",
                        ),
                        M(
                            "update.github-api.used-up.fix",
                            {
                                "one": "Wait about {n} minute. Or enter a GitHub token on "
                                "The librarian → Source access.",
                                "other": "Wait about {n} minutes. Or enter a GitHub token on "
                                "The librarian → Source access.",
                            },
                            n=wait,
                        ),
                        go=GO_TOKEN,
                    )
                )
            else:
                findings.append(
                    finding(
                        "github-api",
                        title,
                        "ok",
                        M(
                            "update.github-api.ok",
                            "{left} of {limit} requests are left this hour.",
                            left=core["remaining"],
                            limit=core["limit"],
                        ),
                    )
                )
        except (OSError, ValueError, KeyError) as exc:
            findings.append(
                finding(
                    "github-api",
                    title,
                    "warn",
                    M(
                        "update.github-api.failed",
                        "The box could not ask GitHub for its rate limit.",
                    ),
                    notes=[PROBE_NOTE],
                    evidence=[exc],
                    go=GO_TOKEN,
                )
            )

    # 6. The repository and branch exist and answer.
    title = M("update.repo.title", "Repository")
    if repo and reachable:
        out = run("git", *_source_protocols(), "ls-remote", "--heads", repo, branch, timeout=60)
        if out.returncode != 0:
            findings.append(
                finding(
                    "repo",
                    title,
                    "problem",
                    M(
                        "update.repo.failed",
                        "The box could not read the repository {repo}.",
                        repo=repo,
                    ),
                    fix=M(
                        "update.repo.failed.fix",
                        "Check that the repository exists and that the box can read it without a sign-in.",
                    ),
                    notes=[PROBE_NOTE],
                    evidence=(out.stderr.strip().splitlines() or ["git ls-remote failed"])[-1:],
                    go=GO_SOURCE_REPO,
                )
            )
        elif not out.stdout.strip():
            # The branch is not there now: renamed, removed, or never there. The doctor cannot tell which.
            findings.append(
                finding(
                    "repo",
                    title,
                    "problem",
                    M(
                        "update.repo.no-branch",
                        "Branch not found. The repository {repo} has no branch named {branch}.",
                        repo=repo,
                        branch=branch,
                    ),
                    fix=M(
                        "update.repo.no-branch.fix",
                        "Check the branch name. To change it, use Update source in this doctor's "
                        "recovery actions, or run the installer again with the option --branch and "
                        "the correct name.",
                    ),
                    go=GO_SOURCE_BRANCH,
                )
            )
        else:
            findings.append(
                finding(
                    "repo",
                    title,
                    "ok",
                    M(
                        "update.repo.ok",
                        "The branch {branch} is at {commit}.",
                        branch=branch,
                        commit=out.stdout.split()[0][:7],
                    ),
                )
            )

    # 7. Space for the clone, downloads and the install.
    for fid, title, path, need in (
        (
            "space:cache",
            M("update.space.cache.title", "Space for the update cache"),
            UPDATE_SRC.parent,
            200,
        ),
        (
            "space:system",
            M("update.space.system.title", "Space on the system card"),
            Path("/"),
            300,
        ),
    ):
        path = path if path.exists() else path.parent
        free = shutil.disk_usage(path).free >> 20
        if free >= need:
            findings.append(
                finding(
                    fid,
                    title,
                    "ok",
                    M("update.space.ok", "{mb} MB is free.", mb=free),
                    evidence=[path],
                )
            )
        else:
            findings.append(
                finding(
                    fid,
                    title,
                    "problem",
                    M(
                        "update.space.low",
                        "Insufficient free storage. Only {mb} MB is free, and an update needs {need} MB.",
                        mb=free,
                        need=need,
                    ),
                    fix=M(
                        "update.space.low.fix",
                        "Delete what is no longer needed, for example old books on Books.",
                    ),
                    evidence=[path],
                    go=GO_BOOKS,
                )
            )

    # 8. The cached clone.
    title = M("update.cache.title", "Update cache")
    if (UPDATE_SRC / ".git").is_dir():
        locks = [p.name for p in (UPDATE_SRC / ".git").glob("*.lock")]
        if locks:
            findings.append(
                finding(
                    "cache",
                    title,
                    "problem",
                    M(
                        "update.cache.lock",
                        {
                            "one": "Stale cache lock. The update cache has {n} lock file that a git command left.",
                            "other": "Stale cache lock. The update cache has {n} lock files that git commands left.",
                        },
                        n=len(locks),
                    ),
                    M(
                        "update.cache.lock.why",
                        "Git cannot work in the cache while a lock file is there.",
                    ),
                    CACHE_CLEAR,
                    evidence=locks,
                    actions=[CACHE_CLEAR_ACTION],
                )
            )
        else:
            status = run("git", "-C", str(UPDATE_SRC), "status", "--porcelain", timeout=60)
            remote = run("git", "-C", str(UPDATE_SRC), "remote", "get-url", "origin").stdout.strip()
            if status.returncode != 0:
                findings.append(
                    finding(
                        "cache",
                        title,
                        "problem",
                        M(
                            "update.cache.damaged",
                            "The cached copy of the repository is damaged.",
                        ),
                        fix=CACHE_CLEAR,
                        evidence=(status.stderr.strip().splitlines() or [])[-1:],
                        actions=[CACHE_CLEAR_ACTION],
                    )
                )
            elif repo and remote != repo:
                findings.append(
                    finding(
                        "cache",
                        title,
                        "warn",
                        M(
                            "update.cache.other-repo",
                            "The cache was copied from {remote}, not from {repo}.",
                            remote=remote,
                            repo=repo,
                        ),
                        notes=[
                            M(
                                "update.cache.other-repo.note",
                                "The next check for updates switches the cache to {repo}.",
                                repo=repo,
                            )
                        ],
                        actions=[CACHE_CLEAR_ACTION],
                    )
                )
            else:
                findings.append(
                    finding(
                        "cache",
                        title,
                        "ok",
                        M(
                            "update.cache.ok",
                            "The cached copy of the repository is intact.",
                        ),
                    )
                )
    else:
        findings.append(
            finding(
                "cache",
                title,
                "ok",
                M(
                    "update.cache.empty",
                    "The cache is empty. The next check downloads the repository again.",
                ),
            )
        )

    # 9. Package sources: a repository that fails verification fails every apt-get update.
    title = M("update.sources.title", "Package sources")
    out = run("apt-get", "update", "-q", timeout=300)
    text = out.stdout + out.stderr
    if out.returncode == 0 and not re.search(r"^(E|Err):", text, re.M):
        findings.append(
            finding(
                "sources",
                title,
                "ok",
                M("update.sources.ok", "The package lists refresh without errors."),
            )
        )
    else:
        # Which sources fail, so that the file named is the one that fails: a command finds its list file.
        bad = sorted(set(re.findall(r"(?:Err|E|W):\S*\s+(https?://\S+)", text)))
        hosts = sorted({re.sub(r"^https?://([^/]+).*", r"\1", u) for u in bad})
        lines = [line for line in text.splitlines() if re.match(r"^(E|Err|W):", line)][:3]
        findings.append(
            finding(
                "sources",
                title,
                "problem",
                M("update.sources.failed", "The package-index refresh failed."),
                M(
                    "update.sources.failed.why",
                    "A package source that fails verification blocks every package update.",
                ),
                (
                    M(
                        "update.sources.failed.fix",
                        "Find the list file of the failing source with the command. "
                        "Copy that file to a safe place. Then remove it, and run the doctor again.",
                    )
                    if hosts
                    else None
                ),
                notes=[
                    M(
                        "update.sources.caddy",
                        "The installer repairs the Caddy package source itself.",
                    )
                ],
                commands=[
                    f"grep -rlF {shlex.quote(h)} /etc/apt/sources.list /etc/apt/sources.list.d/" for h in hosts[:2]
                ],
                evidence=[*bad[:3], *[line[:200] for line in lines]] or ["apt-get update failed"],
            )
        )

    # 10. The last fetch, as the page last saw it.
    try:
        last = json.loads(UPDATE_STATE.read_text())
        failed = [
            c for c in last.get("checks", []) if not c.get("ok") and (not c.get("warn") or c.get("id") == "nginx")
        ]
        if failed:
            findings.append(
                finding(
                    "last-check",
                    M("update.last-check.title", "Last update check"),
                    "problem" if any(not c.get("warn") for c in failed) else "warn",
                    M(
                        "update.last-check.failed",
                        {
                            "one": "The downloaded version failed {n} check.",
                            "other": "The downloaded version failed {n} checks.",
                        },
                        n=len(failed),
                    ),
                    fix=(
                        M(
                            "update.last-check.fix.branch",
                            "The branch this box follows does not lead on from the installed version. Change the "
                            "branch under Update source, usually to the repository's default branch, then check "
                            "for updates again.",
                        )
                        if any(c.get("id") == "history" for c in failed)
                        else M(
                            "update.last-check.fix",
                            "Review the evidence here. Clear a broken cache or, for a trusted version with a known checker failure, select Install anyway in this doctor. Signing and installer syntax checks cannot be skipped.",
                        )
                    ),
                    go=GO_SOURCE_BRANCH if any(c.get("id") == "history" for c in failed) else None,
                    notes=[
                        *(_said_check(c) for c in failed),
                        *(c["fix"] for c in failed if is_message(c.get("fix"))),
                    ],
                    evidence=[e for c in failed for e in c.get("evidence", [])][:6],
                    actions=_force_actions(last),
                )
            )
    except (OSError, ValueError, AttributeError):
        pass
    findings += scheduled_findings()
    findings += kits_findings()
    return findings


def _force_actions(state):
    """Install anyway, for the last download's failed checks, only when update_force_install would run: the button's
    question lists exactly the checks it skips."""
    skipped = _force_failed(state)
    try:
        if not skipped or _force_refusal(state):
            return []
    except Refused:
        return []
    return [
        finding_action(
            "force-install",
            M("update.last-check.force", "Install anyway"),
            M(
                "update.last-check.force.confirm",
                {
                    "one": "Install the downloaded version although it failed this check: {checks}? "
                    "The installer still stops on its own errors. The hub restarts during the install.",
                    "other": "Install the downloaded version although it failed these {n} checks: {checks}? "
                    "The installer still stops on its own errors. The hub restarts during the install.",
                },
                n=len(skipped),
                checks=[c["name"] for c in skipped],
            ),
        )
    ]


def _said_check(c):
    """A failed check as the doctor says it: its own sentence (with its fix) now, or its name and words in a state
    kept from before the checks were messages."""
    if is_message(c.get("detail")):
        return M(
            "update.last-check.item",
            "{check}: {detail}",
            check=c["name"],
            detail=c["detail"],
        )
    return f"{c.get('name', '')}: {c.get('detail', '')}"


def scheduled_findings(now=None):
    """Whether the timer's runs actually reach the hub's own update (library/last-run.json, kept
    by the librarian): a check that the box *can* update says nothing about whether it *does*.
    """
    now = now or time.time()
    title = M("update.scheduled.title", "Scheduled updates")
    log_fix = M("update.scheduled.log.fix", "Read the librarian's log.")
    try:
        rec = json.loads((STATE / "library" / "last-run.json").read_text())
    except (OSError, ValueError):
        return [
            finding(
                "scheduled",
                title,
                "warn",
                M("update.scheduled.none", "No scheduled run is recorded yet."),
                fix=M(
                    "update.scheduled.none.fix",
                    "If this stays, read the librarian's log.",
                ),
                notes=[M("update.scheduled.none.note", "The librarian runs every hour.")],
                commands=[LIBRARIAN_LOG],
                go=GO_LIBRARIAN,
            )
        ]
    out = []
    last = rec.get("last_scheduled") or {}
    started = when(last.get("started") or 0)
    if last and last.get("finished") is None:
        pass  # running now
    elif last and last.get("error"):
        out.append(
            finding(
                "scheduled",
                title,
                "problem",
                M(
                    "update.scheduled.crashed",
                    "Scheduled run failed. The run of {time} stopped with an error.",
                    time=started,
                ),
                M(
                    "update.scheduled.crashed.why",
                    "Until the cause is repaired, the steps after the error do not run: books, "
                    "toolkits and the update of the hub server.",
                ),
                log_fix,
                commands=[LIBRARIAN_LOG],
                evidence=[
                    last["error"],
                    *([last["where"]] if last.get("where") else []),
                ],
                go=GO_LIBRARIAN,
            )
        )
    hub_at = rec.get("hub_stage_at")
    if not hub_at or now - hub_at > 3 * 3600:
        out.append(
            finding(
                "scheduled-stage",
                title,
                "problem",
                (
                    M(
                        "update.scheduled.stage.since",
                        "Update stage not reached. Scheduled runs have not reached the update of "
                        "the hub server since {time}.",
                        time=when(hub_at),
                    )
                    if hub_at
                    else M(
                        "update.scheduled.stage.never",
                        "Update stage not reached. Scheduled runs have not reached the update of the hub server yet.",
                    )
                ),
                fix=log_fix,
                commands=[LIBRARIAN_LOG],
                go=GO_LIBRARIAN,
            )
        )
    if not out:
        errors = last.get("errors", [])[:3] if not last.get("ok") else []
        out.append(
            finding(
                "scheduled",
                title,
                "ok",
                (
                    M(
                        "update.scheduled.ok",
                        "The scheduled run of {time} finished and reached the update of the hub server.",
                        time=started,
                    )
                    if last
                    else M(
                        "update.scheduled.ok-reached",
                        "Scheduled runs reach the update of the hub server.",
                    )
                ),
                notes=([M("update.scheduled.ok.errors", "Some sources had errors.")] if errors else []),
                evidence=errors,
            )
        )
    return out


def kits_findings(now=None):
    """The toolkits: the cache's files against their manifests, its folder,
    its size against the budget the page set, and kits left installed."""
    now = now or time.time()
    out = []
    title = M("update.kits-cache.title", "Toolkit cache")
    problems = kits.verify()
    if problems:
        out.append(
            finding(
                "kits-cache",
                title,
                "problem",
                M(
                    "update.kits-cache.failed",
                    "The toolkit cache failed its integrity check.",
                ),
                M(
                    "update.kits-cache.failed.why",
                    "The box installs nothing from a cache that fails this check.",
                ),
                M(
                    "update.kits-cache.failed.fix",
                    "On Toolkits, select Refresh cache for the toolkit while the box has internet access.",
                ),
                evidence=problems[:5],
                go=GO_TOOLKITS,
            )
        )
    elif kits.MANIFESTS.is_dir() and any(kits.MANIFESTS.glob("*.json")):
        out.append(
            finding(
                "kits-cache",
                title,
                "ok",
                M(
                    "update.kits-cache.ok",
                    "Cached files verified. Every file in the toolkit cache matches its manifest. "
                    "The cache uses {mb} MB.",
                    mb=kits.pool_bytes() >> 20,
                ),
            )
        )
    try:
        budget = int(json.loads((STATE / "library" / "toolkits.json").read_text()).get("budget_mb", 500))
    except (OSError, ValueError, TypeError, AttributeError):
        budget = 500
    if kits.pool_bytes() > budget << 20:
        out.append(
            finding(
                "kits-limit",
                M("update.kits-limit.title", "Toolkit cache storage limit"),
                "warn",
                M(
                    "update.kits-limit.over",
                    "Toolkit cache storage limit exceeded. The cache uses {mb} MB, and its limit is {limit} MB.",
                    mb=kits.pool_bytes() >> 20,
                    limit=budget,
                ),
                fix=M(
                    "update.kits-limit.fix",
                    "Increase the cache storage limit on Toolkits. Or stop keeping a toolkit up to date.",
                ),
                go=GO_TOOLKITS,
            )
        )
    # Kits kept up to date (the security kit: from outside the box's control), and their data, by when the box
    # downloaded them: a warning after a week, a problem after a month.
    age_title = M("update.kits-age.title", "Vulnerability data age")
    age_fix = M(
        "update.kits-age.fix",
        "Connect the box to the internet. The librarian downloads new data every day.",
    )

    def age(fid, at, said):
        days = (now - at) / 86400
        if days > 7:
            out.append(
                finding(
                    fid,
                    age_title,
                    "problem" if days > 30 else "warn",
                    said(int(days)),
                    fix=age_fix,
                    go=GO_TOOLKITS,
                )
            )

    for kid, k in kits.definitions().items():
        man = kits.manifest(kid)
        if k.get("refresh_hours") and man:
            age(
                f"kits-age:{kid}",
                man["fetched"],
                lambda n, kid=kid, at=man["fetched"]: M(
                    "update.kits-age.kit",
                    {
                        "one": "The box downloaded the cache of the {kit} toolkit on {time}, {n} day ago.",
                        "other": "The box downloaded the cache of the {kit} toolkit on {time}, {n} days ago.",
                    },
                    kit=kid,
                    time=when(at),
                    n=n,
                ),
            )
        if "debsecan" in k.get("feeds", []) and man:
            try:
                codename = next(
                    line.split("=", 1)[1].strip().strip('"')
                    for line in open("/etc/os-release")
                    if line.startswith("VERSION_CODENAME=")
                )
                feed = STATE / "library" / "debsecan" / "release" / "1" / codename
                if feed.is_file():
                    at = feed.stat().st_mtime
                    age(
                        "kits-age:debsecan",
                        at,
                        lambda n, at=at: M(
                            "update.kits-age.debsecan",
                            {
                                "one": "The box downloaded debsecan's data from Debian's security tracker on {time}, "
                                "{n} day ago.",
                                "other": "The box downloaded debsecan's data from Debian's security tracker on {time}, "
                                "{n} days ago.",
                            },
                            time=when(at),
                            n=n,
                        ),
                    )
            except (OSError, StopIteration):
                pass
    title = M("update.kits-installed.title", "Installed toolkits")
    for kit, v in kits.installed_state().items():
        days = (now - v.get("at", now)) / 86400
        if kit == "debug" and days > 7:
            out.append(
                finding(
                    f"kits-installed:{kit}",
                    title,
                    "warn",
                    M(
                        "update.kits-installed.debug",
                        {
                            "one": "The debug toolkit has been installed for {n} day.",
                            "other": "The debug toolkit has been installed for {n} days.",
                        },
                        n=int(days),
                    ),
                    M(
                        "update.kits-installed.debug.why",
                        "Run as root, its tools can read the memory of processes and the network traffic on this box.",
                    ),
                    M(
                        "update.kits-installed.debug.fix",
                        "On Toolkits, select Remove toolkit.",
                    ),
                    go=GO_TOOLKITS,
                )
            )
        elif v.get("remove_at") is None and kit != "build":
            out.append(
                finding(
                    f"kits-installed:{kit}",
                    title,
                    "warn",
                    M(
                        "update.kits-installed.no-removal",
                        "Toolkit installed without removal time. The {kit} toolkit stays "
                        "installed until someone removes it.",
                        kit=kit,
                    ),
                    fix=M(
                        "update.kits-installed.no-removal.fix",
                        "On Toolkits, set a removal time, or select Remove toolkit.",
                    ),
                    go=GO_TOOLKITS,
                )
            )
    return out


def _doctor_again():
    """The update doctor run again, so its report is not older than what just changed (a failed verification, a new
    source). Its own failure leaves the old report, said as stale on the page."""
    try:
        safeio.write(DOCTOR_STATE, json.dumps({"at": time.time(), "findings": updates_doctor_findings()}, indent=2))
    except Exception:  # noqa: BLE001 - the action that asked is done; the report is a courtesy
        return  # the old report stays, and the page says it is out of date


def update_doctor(req):
    findings = updates_doctor_findings()
    safeio.write(DOCTOR_STATE, json.dumps({"at": time.time(), "findings": findings}, indent=2))
    problems = [f for f in findings if f["status"] == "problem"]
    if not problems:
        return M("update.doctor.none", "The update doctor found no problems.")
    return M(
        "update.doctor.problems",
        {
            "one": "The update doctor found {n} problem: {first}.",
            "other": "The update doctor found {n} problems. The first is {first}.",
        },
        n=len(problems),
        first=problems[0]["title"],
    )


def update_clear_cache(req):
    """Remove the cached clone and downloads; the next check starts afresh."""
    for path in (UPDATE_SRC, DOWNLOADS):
        if path.exists():
            shutil.rmtree(path)
    UPDATE_STATE.unlink(missing_ok=True)
    return M(
        "update.cache.cleared",
        "The update cache is cleared. The next check starts again from the beginning.",
    )


SOURCE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,99}$")


def _source_repo(value):
    """A repository address root may fetch and run code from, at the current signing level (_source_check). Anything
    else is refused before git runs."""
    return _source_check(value)


def update_set_source(req):
    """Point updates at another repository or branch (a fork). The address must be one the signing level allows
    (_source_check) and the remote must list the branch; only then is the root-owned record changed, and the cache that held the old source
    cleared. Whoever can do this chooses the code root installs next, so it is a security-policy action."""
    repo = _source_repo(req.get("repo", ""))
    branch = str(req.get("branch", "")).strip()
    if not SOURCE_BRANCH.match(branch) or ".." in branch or branch.endswith(("/", ".lock")):
        raise Refused(M("update.source.branch.bad", "Give the branch name as git writes it, for example main."))
    if req.get("confirmed") is not True:
        raise Refused(
            M(
                "update.source.confirm",
                "Confirm that this box can install code from {repo} on {branch}.",
                repo=repo,
                branch=branch,
            )
        )
    opts = _install_options()
    out = run("git", *_source_protocols(), "ls-remote", "--heads", repo, branch, timeout=60)
    if out.returncode != 0:
        raise Refused(M("update.target.poll.failed", "The box could not read {repo}.", repo=repo))
    if not any(line.endswith(f"\trefs/heads/{branch}") for line in out.stdout.splitlines()):
        raise Refused(
            M(
                "update.repo.no-branch",
                "Branch not found. The repository {repo} has no branch named {branch}.",
                repo=repo,
                branch=branch,
            )
        )
    for name, value in (("--repo", repo), ("--branch", branch)):
        if name in opts and opts.index(name) + 1 < len(opts):
            opts[opts.index(name) + 1] = value
        else:
            opts += [name, value]
    text = "\n".join(opts) + "\n"
    safeio.write(ETC / "install-options", text, mode=0o644)
    safeio.write(STATE / "install-options", text, mode=0o644)
    update_clear_cache({})
    # The new branch asked about, and the doctor run again: neither says what was true of the old one.
    try:
        update_poll_target({})
    except Refused:
        BRANCHES_STATE.unlink(missing_ok=True)
    _doctor_again()
    return M(
        "update.source.saved",
        "Updates now come from the branch {branch} of {repo}. The next check starts from there.",
        repo=repo,
        branch=branch,
    )


def _branch_standing(repo, default, branch, installed):
    """How the branch this box follows stands, from the repository's own history: the default branch and that branch
    fetched without their files (commits only) into a scratch repository. "ahead"/"same" are fine; "behind" (the
    installed version already contains it), "elsewhere" (the default branch contains the installed version, the
    followed branch does not: merged into it and left behind), "merged" (the default branch contains the followed
    one); "unknown" when the history could not be read."""
    with tempfile.TemporaryDirectory(prefix="update-branches-") as tmp:
        git = ("git", *_source_protocols(), "-C", tmp)
        if run("git", "init", "-q", "--bare", tmp).returncode != 0:
            return "unknown"
        refs = [f"+refs/heads/{n}:refs/heads/{n}" for n in dict.fromkeys((default, branch)) if n]
        if run(*git, "fetch", "-q", "--no-tags", "--filter=tree:0", repo, *refs, timeout=180).returncode != 0:
            return "unknown"
        standing = _relation(tmp, installed, f"refs/heads/{branch}")
        if standing in ("ahead", "same") or not default or default == branch:
            return standing
        if standing == "behind":
            return "behind"
        on_default = _relation(tmp, installed, f"refs/heads/{default}") in ("ahead", "same")
        if standing == "diverged" and on_default:
            return "elsewhere"
        if (
            run(
                "git", "-C", tmp, "merge-base", "--is-ancestor", f"refs/heads/{branch}", f"refs/heads/{default}"
            ).returncode
            == 0
        ):
            return "merged"
        return standing


def update_poll_target(req):
    """Ask the repository which branch it serves by default and which branches it has, and how the branch this box
    follows stands against the installed version. Kept for the page (BRANCHES_STATE): the Update source form offers
    the branches, the default first, and the doctor says a branch left behind. Changes nothing else."""
    try:
        opts = _install_options()
    except ValueError:
        raise Refused(NO_REPO) from None
    recorded, branch = _option(opts, "--repo"), _option(opts, "--branch", "main")
    if not (req.get("repo") or recorded):
        raise Refused(NO_REPO)
    repo = _source_repo(req["repo"]) if req.get("repo") else _recorded_source(recorded)
    out = run("git", *_source_protocols(), "ls-remote", "--symref", repo, "HEAD", "refs/heads/*", timeout=60)
    if out.returncode != 0:
        raise Refused(M("update.target.poll.failed", "The box could not read {repo}.", repo=repo))
    found = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", out.stdout, re.M)
    default = found.group(1) if found else None
    branches = sorted(
        {m.group(1) for m in re.finditer(r"\trefs/heads/(\S+)$", out.stdout, re.M)},
        key=lambda n: (n != default, n),
    )[:100]
    # How the followed branch stands: only for the repository this box follows (another one is asked about first).
    same_repo = not req.get("repo") or repo == recorded
    installed = _installed_commit()
    # Gone only when the repository listed its branches and this one is not among them.
    gone = same_repo and bool(branches) and branch not in branches
    standing = _branch_standing(repo, default, branch, installed) if same_repo and branch in branches else None
    safeio.write(
        BRANCHES_STATE,
        json.dumps(
            {
                "at": time.time(),
                "repo": repo,
                "default": default,
                "branches": branches,
                "branch": branch if same_repo else None,
                "installed": installed,
                "standing": "gone" if gone else standing,
            },
            indent=2,
        ),
    )
    if gone:
        return M(
            "update.target.poll.gone",
            "{repo} no longer has the branch {branch} that this box follows. Select {default} under Update source.",
            repo=repo,
            branch=branch,
            default=default or "main",
        )
    said = {"repo": repo, "default": default or "main", "branch": branch, "installed": installed or "?"}
    if standing == "behind":
        return M(
            "update.target.poll.behind",
            "{repo} serves {default} by default. This box follows {branch}, which is behind the installed version "
            "{installed}. Select {default} under Update source.",
            **said,
        )
    if standing == "elsewhere":
        return M(
            "update.target.poll.elsewhere",
            "{repo} serves {default} by default, which contains the installed version {installed}. This box follows "
            "{branch}, which does not. Select {default} under Update source.",
            **said,
        )
    if standing == "merged":
        return M(
            "update.target.poll.merged",
            "{repo} serves {default} by default, which already contains {branch}, the branch this box follows. "
            "Select {default} under Update source.",
            **{k: v for k, v in said.items() if k != "installed"},
        )
    if not default:
        return M(
            "update.target.poll.unknown",
            "{repo} did not say which branch is its default. This box follows {branch}.",
            repo=repo,
            branch=branch,
        )
    if default == branch:
        return M(
            "update.target.poll.same",
            "{repo} serves {branch} by default, which is the branch this box follows.",
            repo=repo,
            branch=branch,
        )
    return M(
        "update.target.poll.differs",
        "{repo} serves {default} by default. This box follows {branch}.",
        repo=repo,
        default=default,
        branch=branch,
    )


# --- the security page -----------------------------------------------------------------


def _write_security(data):
    safeio.write(SECURITY_STATE, json.dumps(data, indent=2))


def security_scan(req):
    data = security.scan()
    _write_security(data)
    bad = sum(1 for f in data["findings"] if f["status"] == "problem")
    look = sum(1 for f in data["findings"] if f["status"] == "warn")
    # "nothing to fix" with warnings reads as all clear: both counts, always.
    return M(
        "control.security.scanned",
        "The security scan is complete. Findings that require repair: {repair}. "
        "Findings that require review: {review}.",
        repair=bad,
        review=look,
    )


def security_audit(req):
    """The Security doctor (secdoctor.py; one of three: docs/dev/doctors.md), run and its report written."""
    report = secdoctor.audit()
    secdoctor.write_report(report, AUDIT_STATE)
    c = report["counts"]
    return M(
        "control.security.audited",
        "The Security doctor is complete. Findings that require repair: {repair}. "
        "Findings that require review: {review}.",
        repair=c["problem"],
        review=c["warn"],
    )


def security_deep_audit(req):
    """debian-cis and Lynis (deepaudit.py), then the regular audit, so the report shows them."""
    from irate_box.root import deepaudit

    line = deepaudit.deep(log=lambda *_: None)
    security_audit(req)
    return line


def security_fix(req):
    choice = str(req.get("choice", ""))
    if not re.fullmatch(r"[a-z-]+(:[A-Za-z0-9@_][A-Za-z0-9@._-]*)?", choice):
        raise Refused(
            M(
                "control.security.not-action",
                "This is not an action of the Security page.",
            )
        )
    # Only what root's own last scan offered: a forged request could otherwise switch off
    # any unit not on the protected list (a firewall, auditd, a getty). The scan is root's file,
    # read only if it still is; every fix ends with a new one, so the page's buttons stay valid.
    try:
        offered = {
            a["choice"]
            for f in json.loads(safeio.read_own(SECURITY_STATE)).get("findings", [])
            for a in f.get("actions", [])
        }
    except (OSError, ValueError, KeyError, TypeError):
        offered = set()
    # The update pattern's chips and buttons are a closed set of their own, always there on Updates.
    pattern = choice in (
        "security-check",
        "security-fetch",
        "security-updates",
        "security-reach-fetch",
        "security-reach-install",
    ) or choice.startswith("autoupdate-set:")
    if pattern and choice.startswith("autoupdate-set:"):
        security.parse_pattern(choice.split(":", 1)[1])
    if choice not in offered and not pattern:
        raise Refused(
            M(
                "control.security.stale",
                "This action is no longer available. Run the checks again. Then select an action from the new results.",
            )
        )
    if choice in ("security-updates", "security-fetch", "security-check"):
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
    the helper: only ValueError and OSError are answered as failures by the loop below.
    """

    def run_it(req):
        try:
            return fn(req)
        except RuntimeError as exc:
            said = _why(exc)
            raise (Refused(said) if is_message(said) else ValueError(said)) from exc

    run_it.__name__ = fn.__name__
    return run_it


@_tls_answer
def tls_make(req):
    """HTTPS: the box's own CA and its certificate. A second CA only when asked: every
    device that installed the first would have to install the new one."""
    from irate_box.root import tls

    if tls.status().get("set_up") and req.get("again") is not True:
        return M(
            "control.https.already",
            "HTTPS is already set up. Nothing changed. To replace the certificate authority (CA), "
            "select Make a new CA and confirm. Then every device that trusts the old CA must install the new CA.",
        )
    rec = tls.make_ca()
    return M(
        "control.https.made",
        "The box made its certificate authority (CA) and its certificate. "
        "The CA fingerprint starts with {fingerprint}.",
        fingerprint=rec["ca"]["fingerprint"][:23],
    )


@_tls_answer
def tls_renew(req):
    from irate_box.root import tls

    return tls.renew()


@_tls_answer
def tls_import(req):
    """The owner's own certificate: the chain and key the hub staged, read without
    following a link, checked by tls.import_own, and the staged copies removed either way.
    """
    from irate_box.root import tls

    staged = STATE / "tls-import"
    try:
        chain = safeio.read_request(staged / "chain.pem", 64 << 10)
        key = safeio.read_request(staged / "key.pem", 16 << 10)
    except OSError as exc:
        raise Refused(
            M(
                "control.https.import.none",
                "No certificate is available for import. Nothing changed. "
                "Paste the certificate and the key again: {reason}",
                reason=str(exc),
            )
        ) from exc
    finally:
        for f in ("chain.pem", "key.pem"):
            (staged / f).unlink(missing_ok=True)
    try:
        return tls.import_own(chain, key)
    except ValueError as exc:
        raise Refused(
            M(
                "control.https.import.refused",
                "The box did not import the certificate: {reason}",
                reason=_why(exc),
            )
        ) from exc


@_tls_answer
def tls_box(req):
    from irate_box.root import tls

    return tls.use_box_own()


@_tls_answer
def tls_admin_only(req):
    from irate_box.root import tls

    return tls.admin_only(req.get("on") is True)


@_tls_answer
def tls_settings(req):
    from irate_box.root import tls

    return tls.configure(req.get("transport"), req.get("admin_http"))


@_tls_answer
def tls_switch(req):
    """HTTPS on or off at the front, the CA and certificate kept either way."""
    from irate_box.root import tls

    if not tls.status().get("set_up"):
        raise Refused(
            M(
                "control.https.not-set-up",
                "HTTPS is not set up yet. Nothing changed. Select Make the box's certificate first.",
            )
        )
    return tls.front(req.get("on") is True)


def usb_scan(req):
    data = usbstick.scan()
    _write_usb(data)
    books = sum(len(d["zims"]) for d in data["devices"])
    if not data["devices"]:
        return M("control.usb.scan.none", "No USB drive found.")
    return M(
        "control.usb.scan.found",
        "USB drives found: {drives}. Books on them: {books}.",
        drives=len(data["devices"]),
        books=books,
    )


def _min_free():
    try:
        policy = json.loads((STATE / "library" / "sources.json").read_text()).get("policy", {})
        return int(policy.get("min_free_mb", 512)) << 20
    except (OSError, ValueError, TypeError):
        return 512 << 20


def usb_import(req):
    device, file = str(req.get("device", "")), str(req.get("file", ""))
    with Progress("usb-import", 1, path=USB_PROGRESS) as progress:
        progress.step(
            M(
                "control.progress.usb-import",
                "Copying {file} from the USB drive",
                file=Path(file).name,
            )
        )
        name = usbstick.import_zim(
            device,
            file,
            ZIM_DIR,
            HUB_USER,
            _min_free(),
            [str(CODE / "irate-box"), "librarian"],
            STATE,
            progress.bytes,
        )
    return M("control.usb.import.done", "The book {name} is added to the library.", name=name)


def usb_export(req):
    device, book = str(req.get("device", "")), str(req.get("book", ""))
    with Progress("usb-export", 1, path=USB_PROGRESS) as progress:
        progress.step(_copying_to_usb(book))
        where = usbstick.export_zim(device, book, ZIM_DIR, progress.bytes)
    try:
        _write_usb(usbstick.scan())
    except (ValueError, OSError):
        pass
    # The drive is synced and unmounted when the box mounted it, but not one mounted already: hence the eject.
    return M(
        "control.usb.export.done",
        "The USB transfer is complete. The book {book} is on the USB drive as {path}. "
        "Eject the drive before you remove it.",
        book=book,
        path=where,
    )


def _copying_to_usb(book):
    return M("control.progress.usb-book", "Copying {book} to the USB drive", book=book)


def _copying_kit_to_usb(kit):
    return M(
        "control.progress.usb-kit",
        "Copying the toolkit {kit} to the USB drive",
        kit=kit,
    )


KIT_BAD = M("control.kit.kit-bad", "The request must give a toolkit ID.")


def usb_kit_export(req):
    device, kit = str(req.get("device", "")), str(req.get("kit", ""))
    if not kits.ID_RE.match(kit):
        raise Refused(KIT_BAD)
    try:
        with Progress("usb-export", 1, path=USB_PROGRESS) as progress:
            progress.step(_copying_kit_to_usb(kit))
            line = usbstick.export_kit(device, kit, progress.bytes)
        try:
            _write_usb(usbstick.scan())
        except (ValueError, OSError):
            pass
        return M(
            "control.usb.kit-export.done",
            "The USB transfer of the toolkit {kit} is complete: {details}. Eject the drive before you remove it.",
            kit=kit,
            details=line,
        )
    finally:
        _kits_status()


def usb_export_many(req):
    """A content export onto a stick, without the hub program: books and toolkits in the layout the other
    box's own USB imports read (the existing exports, one after another), and git repositories and this
    box's state beside them with import.sh, which brings those two in on a box that has the hub.
    """
    device = str(req.get("device", ""))
    books, kit_ids = req.get("books") or [], req.get("kits") or []
    if not isinstance(books, list) or not all(isinstance(b, str) and usbstick.NAME_RE.match(b) for b in books):
        raise Refused(
            M(
                "control.kit.book-list-bad",
                "The request must give the books as a list of book names.",
            )
        )
    if not isinstance(kit_ids, list) or not all(isinstance(k, str) and kits.ID_RE.match(k) for k in kit_ids):
        raise Refused(KITS_BAD)
    _, _, repo_dirs, state, _ = kit_choices({"repos": req.get("repos") or [], "state": req.get("state", "none")})
    if not books and not kit_ids and not repo_dirs and state == "none":
        raise Refused(EXPORT_EMPTY)
    said = []
    try:
        extras = bool(repo_dirs) or state != "none"
        with Progress("usb-export", len(books) + len(kit_ids) + extras, path=USB_PROGRESS) as progress:
            for b in books:
                progress.step(_copying_to_usb(b))
                said.append(usbstick.export_zim(device, b, ZIM_DIR, progress.bytes))
            for k in kit_ids:
                progress.step(_copying_kit_to_usb(k))
                said.append(usbstick.export_kit(device, k, progress.bytes))
            if extras:
                progress.step(
                    M(
                        "control.progress.usb-extras",
                        "Copying the repositories and the state of this box to the USB drive",
                    )
                )
                usbstick.export_with(
                    device,
                    lambda top: said.extend(content_extras(top, repo_dirs, state)),
                )
    finally:
        _kits_status()
        try:
            _write_usb(usbstick.scan())
        except (ValueError, OSError):
            pass
    # What is on the drive now, by name: the books, the toolkits and the repositories; the box's state said apart.
    items = [
        *books,
        *kit_ids,
        *(f"{d.parent.name}/{d.name.removesuffix('.git')}" for d in repo_dirs),
    ]
    if state == "none":
        return M(
            "control.usb.many.done",
            "The USB transfer is complete. The USB drive now holds {items}. Eject the drive before you remove it.",
            items=items,
        )
    if items:
        return M(
            "control.usb.many.done-state",
            "The USB transfer is complete. The USB drive now holds {items}, "
            "and the state of this box with import.sh. Eject the drive before you remove it.",
            items=items,
        )
    return M(
        "control.usb.many.done-state-only",
        "The USB transfer is complete. The USB drive now holds the state of this box "
        "with import.sh. Eject the drive before you remove it.",
    )


def backup_image(req):
    """A full image of the box's card onto a stick, compressed, in 3.9 GB parts
    on a FAT stick. Taken while the box runs, so as after a power cut: what was being written may be
    half-written. Restored by writing it back with any image writer (gunzip, then dd or Etcher).
    """
    device = str(req.get("device", ""))
    with Progress("usb-export", 1, path=USB_PROGRESS) as progress:
        progress.step(
            M(
                "control.progress.usb-image",
                "Writing an image of the memory card to the USB drive",
            )
        )
        return usbstick.export_image(device, progress.bytes)


def usb_kit_import(req):
    device, kit = str(req.get("device", "")), str(req.get("kit", ""))
    if not kits.ID_RE.match(kit):
        raise Refused(KIT_BAD)
    # Importing a release the clock cannot date is a security decision: the owner's, from the hub only while the
    # security policy accepts the hub's; otherwise root's, at the console (kits import-dir --unknown-time).
    freshness = kits.UNKNOWN_TIME if req.get("freshness") == kits.UNKNOWN_TIME else None
    if freshness and policy_mode() != "hub":
        raise Refused(
            M(
                "kits.usb.unknown-time.console",
                "The security policy accepts changes only from the console or SSH, so the hub cannot approve an "
                "import without a freshness check. At the console, mount the USB drive and run sudo "
                "/opt/irate-box/irate-box kits import-dir <mount point>/irate-box/kits --unknown-time.",
            )
        )
    try:
        budget = int(json.loads((STATE / "library" / "toolkits.json").read_text()).get("budget_mb", 500))
    except (OSError, ValueError, TypeError, AttributeError):
        budget = 500
    try:
        with Progress("usb-import", 1, path=USB_PROGRESS) as progress:
            progress.step(
                M(
                    "control.progress.usb-kit-import",
                    "Checking and copying the toolkit {kit} from the USB drive",
                    kit=kit,
                )
            )
            return usbstick.import_kit(device, kit, budget, progress.bytes, freshness=freshness)
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
        raise Refused(_not_an_app(app))
    path = Path(zip_path)  # root's own copy (_take_staged), not the hub's staged file
    if path.is_symlink() or not path.is_file():
        raise Refused(NOT_STAGED)
    try:
        with zipfile.ZipFile(path) as zf:
            names, total = set(), 0
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in info.filename:
                    raise Refused(
                        M(
                            "control.app.unsafe-path",
                            "The bundle has an unsafe path: {path}",
                            path=info.filename,
                        )
                    )
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise Refused(
                        M(
                            "control.app.symlink",
                            "The bundle has a symbolic link: {path}",
                            path=info.filename,
                        )
                    )
                total += info.file_size
                names.add(info.filename)
            if total > APP_MAX_BYTES:
                raise Refused(TOO_LARGE)
            meta = json.loads(zf.read("irate-box-bundle.json"))
            if meta.get("app") != app or not _has_needed(names, APPS[app]["install"]["needs"]):
                raise Refused(
                    M(
                        "control.app.wrong-app",
                        "This is not a bundle of {app}.",
                        app=app,
                    )
                )
            return meta
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise Refused(
            M(
                "control.app.invalid",
                "This is not a valid bundle: {reason}",
                reason=str(exc),
            )
        ) from exc


def _not_an_app(app):
    return M("control.app.unknown", "{app} is not an app that the hub installs.", app=app)


NOT_STAGED = M("control.app.not-staged", "The bundle must be a file that the librarian staged.")
TOO_LARGE = M(
    "control.app.too-large",
    "The bundle is larger than {max} MB.",
    max=APP_MAX_BYTES >> 20,
)


# Root's copy of a bundle while it is checked and extracted: the staged zip is in the hub's
# folder, where the hub could swap it between the check and the extraction.
APP_TAKEN = Path(os.environ.get("HUB_APP_TAKEN", "/var/cache/irate-box/app-install"))


def _staged_dir_fd(zip_path):
    """The staged bundle's folder, opened without following a link, and only if the hub (this
    user, when not root) owns it: a folder swapped for a link, or for one of root's, is refused.
    """
    path = Path(zip_path)
    staging = APP_STAGING.resolve()
    if not path.resolve().is_relative_to(staging):
        raise Refused(NOT_STAGED)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    owner = pwd.getpwnam(HUB_USER).pw_uid if os.geteuid() == 0 else os.geteuid()
    if os.fstat(dfd).st_uid != owner:
        os.close(dfd)
        raise Refused(
            M(
                "control.app.folder-owner",
                "The folder of the bundle does not belong to the librarian.",
            )
        )
    return dfd


def _take_staged(zip_path):
    """A root-owned copy of the staged bundle, read once through an O_NOFOLLOW fd."""
    name = Path(zip_path).name
    dfd = _staged_dir_fd(zip_path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except OSError as exc:
        raise Refused(NOT_STAGED) from exc
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as src:
        st = os.fstat(src.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > APP_MAX_BYTES:
            raise Refused(
                M(
                    "control.app.not-plain",
                    "The bundle must be a plain file of at most {max} MB.",
                    max=APP_MAX_BYTES >> 20,
                )
            )
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
            raise Refused(TOO_LARGE)
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
            destination = new.resolve()
            for member in zf.infolist():
                path = (destination / member.filename).resolve()
                if not path.is_relative_to(destination):
                    raise Refused(
                        M(
                            "control.app.unsafe-path",
                            "The bundle has an unsafe path: {path}",
                            path=member.filename,
                        )
                    )
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, path.open("wb") as output:
                        shutil.copyfileobj(source, output)
        # Root-owned and world-readable, whatever modes the zip carried.
        as_root = os.geteuid() == 0
        for root, _dirs, files in os.walk(new):
            safeio.mkdir(root, uid=0 if as_root else None, gid=0 if as_root else None, mode=0o755)
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
    return M(
        "control.app.installed",
        "The app {app} is installed: version {commit} from {ref}, built on {built}.",
        app=app,
        commit=str(meta.get("commit", ""))[:7],
        ref=str(meta.get("ref")),
        built=str(meta.get("built", ""))[:10],
    )


def app_install(req):
    return install_app(str(req.get("app", "")), str(req.get("zip", "")))


def app_rollback(req):
    app = str(req.get("app", ""))
    if app not in APPS:
        raise Refused(_not_an_app(app))
    target = _app_dir(app)
    prev = target.parent / f".{target.name}.prev"
    if not prev.is_dir():
        raise Refused(
            M(
                "control.app.no-previous",
                "There is no earlier version of {app} to roll back to.",
                app=app,
            )
        )
    swap = target.parent / f".{target.name}.swap"
    shutil.rmtree(swap, ignore_errors=True)
    if target.exists():
        os.rename(target, swap)
    os.rename(prev, target)
    if swap.exists():
        os.rename(swap, prev)  # so a second roll back goes forward again
    if APPS[app]["install"].get("restart"):
        run("systemctl", "try-restart", APPS[app]["install"]["restart"])
    meta = (
        json.loads((target / "irate-box-bundle.json").read_text())
        if (target / "irate-box-bundle.json").exists()
        else {}
    )
    if meta.get("commit"):
        return M(
            "control.app.rolled-back",
            "The app {app} is rolled back to version {commit}.",
            app=app,
            commit=str(meta["commit"])[:7],
        )
    return M(
        "control.app.rolled-back-unknown",
        "The app {app} is rolled back to its earlier version.",
        app=app,
    )


# --- the Box doctor (health.py; one of three: docs/dev/doctors.md) -----------------------

HEALTH_STATE = CONTROL / "health.json"


def _write_health():
    os.environ["HUB_CONTROL_RUNNING"] = "1"  # the queue it would report is the one being answered
    data = health.scan()
    safeio.write(HEALTH_STATE, json.dumps(data, indent=2))
    return data


def health_scan(req):
    data = _write_health()
    bad = [f for f in data["findings"] if f["status"] == "problem"]
    if not bad:
        return M("control.health.scanned.none", "The Box doctor found no problems.")
    return M(
        "control.health.scanned",
        {
            "one": "The Box doctor found {n} problem.",
            "other": "The Box doctor found {n} problems.",
        },
        n=len(bad),
    )


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
    return M("control.health.rerun.done", "The installer ran again and finished.")


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
        except Exception:  # noqa: BLE001 - the repair's own answer matters more than a fresh report
            pass
    return msg


# --- network: inventory and the uplink watchdog ------------------------------------------

IFACE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$")  # no leading "-": it would be an option


def net_scan(req):
    iface = req.get("iface") or None
    if iface is not None and not IFACE_RE.match(str(iface)):
        raise Refused(NOT_IFACE)
    if req.get("rescan") is True:
        # A fresh WiFi scan first (NetworkManager's list is otherwise its last one); a moment's work for the radio.
        run("nmcli", "dev", "wifi", "rescan", timeout=30)
        time.sleep(5)
    inv = netinv.scan(iface)
    netinv.write(inv, CONTROL / "netinv.json")
    radios, up = len(inv["radios"]), inv["uplink"]
    if up.get("iface"):
        return M(
            "control.net.scanned",
            {
                "one": "The network scan found {n} radio. {backend} runs the network link {iface}.",
                "other": "The network scan found {n} radios. {backend} runs the network link {iface}.",
            },
            n=radios,
            backend=up.get("backend"),
            iface=up["iface"],
        )
    return M(
        "control.net.scanned.no-link",
        {
            "one": "The network scan found {n} radio. The box has no network link.",
            "other": "The network scan found {n} radios. The box has no network link.",
        },
        n=radios,
    )


NOT_IFACE = M("control.net.iface-bad", "This is not an interface name.")


# --- the hotspot (root/ap.py does the work, hub/apmode.py the plan) -------------------------------

AP_STATUS = CONTROL / "ap.json"  # the hub's copy: the plan, whether it's up, the tries


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
            raise Refused(NOT_IFACE)
        out["radio"] = str(req["radio"])
    if req.get("band") in ("2.4 GHz", "5 GHz"):
        out["band"] = req["band"]
    if req.get("channel") is not None:
        if type(req["channel"]) is not int or not 1 <= req["channel"] <= 196:
            raise Refused(
                M(
                    "control.hotspot.channel-bad",
                    "The channel must be a channel number from {min} to {max}.",
                    min=1,
                    max=196,
                )
            )
        out["channel"] = req["channel"]
    if req.get("take_radio") is True:
        out["take_radio"] = True
    return out


def _ap_record_status(plan=None, note=""):
    """ap.json, for the Network page: the note in English (a message said there as it is)."""
    note = render(note)
    rec = ap._load(ap.RECORD, {})
    safeio.write(
        AP_STATUS,
        json.dumps(
            {
                "up": bool(rec.get("up")),
                "plan": rec.get("plan") if rec.get("up") else plan,
                "confirmed": rec.get("confirmed", True),
                "since": rec.get("since"),
                "owner": rec.get("owner") or {},
                "tried": ap._load(ap.TRIED, {}),
                "note": note,
                "at": time.time(),
            }
        ),
    )


def _ap_inventory():
    inv = netinv.scan()
    netinv.write(inv, CONTROL / "netinv.json")
    return inv


def _ap_failed(exc):
    """A step that failed (ap.start has undone what it did): said on the Network page and to the hub."""
    note = M(
        "control.hotspot.failed",
        "The hotspot did not start: {reason}",
        reason=_why(exc),
    )
    _ap_record_status(None, note)
    return Refused(note)


# --- guests' onward internet (share.py) ------------------------------------------------------------


def _forward(on, rec):
    """IPv4 forwarding: on while sharing (a file in sysctl.d, so a reboot keeps it), and back to
    what the box had before when it stops."""
    rec = dict(rec)
    if on:
        if rec.get("forward_was") is None:
            try:
                rec["forward_was"] = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
            except OSError:
                rec["forward_was"] = "0"
        share.SYSCTL.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(
            share.SYSCTL,
            "# Written by irate-box (root/share.py) while guests share the box's connection.\n"
            "net.ipv4.ip_forward = 1\n",
        )
        run("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
    else:
        share.SYSCTL.unlink(missing_ok=True)
        was = rec.get("forward_was")
        if was in ("0", "1"):
            run("sysctl", "-q", "-w", f"net.ipv4.ip_forward={was}")
        rec["forward_was"] = None
    return rec


def _guest_dns(on, iface=None):
    """The guests' resolver (share.py's dns_hold): its unit written and running while sharing, gone after."""
    unit = share.UNIT_DIR / share.GUEST_DNS_UNIT
    if on:
        text = share.guest_dns_unit(iface)
        if not unit.exists() or unit.read_text() != text:
            safeio.write(unit, text)
            run("systemctl", "daemon-reload")
        run("systemctl", "enable", share.GUEST_DNS_UNIT)
        run("systemctl", "restart", share.GUEST_DNS_UNIT)
    else:
        run("systemctl", "disable", "--now", share.GUEST_DNS_UNIT)
        unit.unlink(missing_ok=True)


def share_apply(rec):
    """Guests' internet as rec says (share.load()'s shape), with the hotspot firewall as it is: one ruleset
    (firewall.apply_all). Turning on: the rules first, then forwarding, then the guests' resolver;
    off: forwarding first, then the rest, so at no moment does anything pass unfiltered.
    """
    from irate_box.root import firewall, security

    fw = security.load_record().get("firewall")
    fw = dict(fw, services=firewall.services_here(fw.get("services", []))) if fw else None
    iface = firewall.hotspot_iface()
    if share.on(rec):
        firewall.apply_all(fw, rec, iface)
        rec = _forward(True, rec)
        _guest_dns(True, iface)
    else:
        rec = _forward(False, rec)
        _guest_dns(False)
        firewall.apply_all(fw, rec, iface)
    share.save(rec)
    return rec


def share_set(req):
    """{"level": one of share.LEVELS}: the rules, forwarding and the guests' resolver made to agree
    with it; off takes all of it back. The containment stays as the owner set it (Security page).
    """
    level = str(req.get("level", ""))
    if level not in share.LEVELS:
        raise Refused(
            M(
                "control.share.level-bad",
                "The level must be one of these values: {levels}.",
                levels=list(share.LEVELS),
            )
        )
    rec = share.load()
    rec["level"] = level
    share_apply(rec)
    return M(
        "control.share.set",
        "Guest internet sharing is saved: {level}.",
        level=share.WORDS[level],
    )


def share_allow(req):
    """{"ip": a guest's address}: let that device out (by its hardware address, while by_mac is on),
    at a level that lets devices out one by one. The hub asks when a guest taps through the sheet (or
    signs in, at users-web)."""
    rec = share.load()
    if rec["level"] not in share.LET_OUT:
        raise Refused(
            M(
                "control.share.not-one-by-one",
                "At the level {level}, the box does not let devices through one at a time.",
                level=rec["level"],
            )
        )
    addr = share.guest_address(req.get("ip"))
    if not addr:
        raise Refused(M("control.share.not-hotspot", "This is not an address on the hotspot."))
    key = addr
    if rec["contain"]["by_mac"]:
        r = run("ip", "neigh", "show", addr)
        key = share.mac_of(addr, r.stdout if r.returncode == 0 else "")
        if not key:
            raise Refused(
                M(
                    "control.share.no-mac",
                    "The box has no hardware address for {address}. Check that the device is still on the hotspot.",
                    address=addr,
                )
            )
    r = run(*share.allow_command(key))
    if r.returncode != 0:
        raise Refused(
            M(
                "control.share.let-failed",
                "The box could not let the device through: {output}",
                output=_last_line(r.stderr, 200),
            )
        )
    if key != addr:
        return M(
            "control.share.let.mac",
            {
                "one": "{address} ({mac}) can reach the internet through the box for {n} hour.",
                "other": "{address} ({mac}) can reach the internet through the box for {n} hours.",
            },
            address=addr,
            mac=key,
            n=share.OUT_HOURS,
        )
    return M(
        "control.share.let",
        {
            "one": "{address} can reach the internet through the box for {n} hour.",
            "other": "{address} can reach the internet through the box for {n} hours.",
        },
        address=addr,
        n=share.OUT_HOURS,
    )


def _guest_policy_follows(note):
    """After the hotspot comes up (its interface may be ap0 or a radio of its own): the hotspot firewall and
    guests' internet rewritten for it, when either is on. Refused, not raised: the hotspot is up. Returns note, or note
    with why the rules were not rewritten."""
    from irate_box.root import security

    if not security.load_record().get("firewall") and not share.on():
        return note
    try:
        share_apply(share.load())
        return note
    except (ValueError, OSError) as exc:
        # The hotspot's sentence, then this one: two whole sentences.
        return M(
            "control.hotspot.rules-failed",
            "{said} The box could not rewrite the guest rules for the hotspot: {reason}",
            said=note,
            reason=_why(exc),
        )


# --- packages from their makers, watched (pkgwatch.py) ----------------------------------------------


def pkg_check(req):
    """{"package": a watched package, or none for every one on the box}: the channel read, a new build
    cached, installed if the owner's mode says so."""
    from irate_box.root import pkgwatch

    ids = [str(req["package"])] if req.get("package") else pkgwatch.watched()
    said = [pkgwatch.check(i, log=lambda *a: None) for i in ids]
    if not said:
        return M("control.pkg.none", "No watched package is on this box.")
    return said[0] if len(said) == 1 else said


def pkg_fetch(req):
    """The channel's newest build downloaded into the cache, whatever the mode; nothing installed."""
    from irate_box.root import pkgwatch

    return pkgwatch.check(str(req.get("package", "")), log=lambda *a: None, fetch=True)


def pkg_install(req):
    from irate_box.root import pkgwatch

    return pkgwatch.install(str(req.get("package", "")), str(req.get("version", "")), log=lambda *a: None)


def pkg_rollback(req):
    from irate_box.root import pkgwatch

    return pkgwatch.rollback(str(req.get("package", "")), log=lambda *a: None)


def pkg_settings(req):
    from irate_box.root import pkgwatch

    days, every = req.get("days"), req.get("every")

    def num(v, bad):
        return v if isinstance(v, int) and not isinstance(v, bool) else bad

    return pkgwatch.set_settings(
        str(req.get("package", "")),
        str(req.get("channel", "")),
        str(req.get("mode", "")),
        num(days, -1),
        None if every is None else num(every, -1),
    )


def ap_on(req):
    try:
        plan = ap.start(run, _ap_inventory(), _ap_settings(), _ap_owner(req))
    except RuntimeError as exc:
        raise _ap_failed(exc) from exc
    # The plan's own words (hub/apmode.py) are its sentences, said inside these.
    if plan.get("needs_choice") or plan["kind"] == "none":
        _ap_record_status(plan, plan["text"])
        if plan.get("why"):
            return M(
                "control.hotspot.not-started",
                "{plan} Reason: {why}",
                plan=plan["text"],
                why=plan["why"],
            )
        return plan["text"]
    where = {
        "iface": ap.ap_iface(plan),
        "channel": plan["channel"],
        "plan": plan["text"],
    }
    if plan.get("drops_uplink"):
        note = M(
            "control.hotspot.on.drops-uplink",
            "The hotspot is enabled on {iface}, channel {channel}. {plan} "
            "Your WiFi link is off. Open the hub from the hotspot within {minutes} minutes and confirm. "
            "Otherwise the box restores the WiFi link.",
            minutes=ap.DEADMAN // 60,
            **where,
        )
    else:
        note = M(
            "control.hotspot.on",
            "The hotspot is enabled on {iface}, channel {channel}. {plan}",
            **where,
        )
    note = _guest_policy_follows(note)
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
        note = M(
            "control.hotspot.try.nothing",
            "There is nothing to try. {plan}",
            plan=plan["text"],
        )
    else:
        note = _guest_policy_follows(
            M(
                "control.hotspot.try.own",
                "The hotspot holds a channel of its own beside your WiFi. Guests do not notice when your WiFi roams.",
            )
            if worked
            else M(
                "control.hotspot.try.follows",
                "The hotspot cannot hold a channel of its own here, so it follows the channel of your WiFi.",
            )
        )
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
    from irate_box.hub import roaming

    s = uplink.load_settings()
    new = dict(req.get("settings") or {})
    new.setdefault("hold_until", s.get("hold_until", 0))
    old, s = s, uplink.validate(new)
    # Each link the settings name: its hardware as it is now, so its settings follow it under another name later;
    # one not here now keeps what was recorded for it.
    named = set(s["watch"]) | set(s.get("per_iface") or {}) | set(s.get("steps_off") or {})
    s["watch_hw"] = {
        n: (uplink.link_hw(n) if (uplink.SYS_NET / n).exists() else (old.get("watch_hw") or {}).get(n))
        for n in sorted(named)
        if (uplink.SYS_NET / n).exists() or (old.get("watch_hw") or {}).get(n)
    }
    s = uplink.validate(s)
    # Roaming's lock and its no-scan change the owner's WiFi: done first, and the setting kept as
    # it was if they fail, so the page never says one is in force that is not.
    iface = uplink.main_link(s)
    try:
        moved = roaming.apply(s, old, iface)
    except ValueError as exc:
        raise Refused(
            M(
                "control.uplink.not-saved",
                "The watchdog settings are not saved: {reason}",
                reason=_why(exc),
            )
        ) from None
    s = uplink.save_settings(s)
    _uplink_running()
    # The settings and the links in uplink.py's own words, as arguments.
    args = {
        "settings": uplink.words(s),
        "links": uplink.watching_words(s),
        "minutes": uplink.COMMON["pause_after_change"] // 60,
    }
    if s["overrides"]:
        said = M(
            "control.uplink.saved.custom",
            "The watchdog settings are saved, with custom values: {settings}. The watchdog watches {links}. "
            "Its next repair comes after {minutes} minutes at the earliest.",
            **args,
        )
    else:
        said = M(
            "control.uplink.saved",
            "The watchdog settings are saved: {settings}. The watchdog watches {links}. "
            "Its next repair comes after {minutes} minutes at the earliest.",
            **args,
        )
    if moved:
        return M(
            "control.uplink.saved.roaming",
            "{said} Changes to the WiFi connection: {changes}.",
            said=said,
            changes=moved,
        )
    return said


def uplink_do(req):
    if req.get("step") not in uplink.STEPS:
        raise Refused(
            M(
                "control.uplink.step-bad",
                "The step must be one of these values: {steps}.",
                steps=list(uplink.STEPS),
            )
        )
    _uplink_running()
    return uplink.request_step(req["step"])


def uplink_hold(req):
    minutes = req.get("minutes")
    if type(minutes) is not int or not 0 <= minutes <= 1440:
        raise Refused(
            M(
                "control.uplink.hold-bad",
                "The hold must be from {min} to {max} minutes.",
                min=0,
                max=1440,
            )
        )
    s = uplink.load_settings()
    s["hold_until"] = time.time() + minutes * 60 if minutes else 0
    uplink.save_settings(s)
    if not minutes:
        return M(
            "control.uplink.hold-ended",
            "The hold has ended. The watchdog repairs as its settings say.",
        )
    return M(
        "control.uplink.held",
        {
            "one": "The watchdog makes no repairs for {n} minute.",
            "other": "The watchdog makes no repairs for {n} minutes.",
        },
        n=minutes,
    )


def uplink_profile(req):
    if type(req.get("on")) is not bool:
        raise Refused(M("control.request.on-bad", "The request must give on as true or false."))
    msg = uplink.profile(req["on"])
    try:
        netinv.write(netinv.scan(), CONTROL / "netinv.json")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return msg


def nm_defaults(req):
    from irate_box.hub import nmconf

    iface = req.get("iface")
    clean = nmconf.validate(req.get("changes"), iface)
    mine, ifaces = nmconf.current_mine(), nmconf.current_ifaces()
    target = mine if iface is None else ifaces.setdefault(iface, {})
    for k, v in clean.items():
        if v is None:
            target.pop(k, None)
        else:
            target[k] = v
    ifaces = {i: v for i, v in ifaces.items() if v}
    text = nmconf.dropin_text(mine, ifaces)
    if text:
        nmconf.DROPIN.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(nmconf.DROPIN, text, 0o644)
    else:
        nmconf.DROPIN.unlink(missing_ok=True)
    try:
        r = run("systemctl", "reload", "NetworkManager", timeout=60)
        code, out = r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        code, out = 1, str(exc)
    try:
        netinv.write(netinv.scan(), CONTROL / "netinv.json")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # Each change: the setting's label and value in nmconf.py's words, or back to a default.
    changes = [
        (
            M(
                "control.nm.change.box-default",
                "{setting}: the box default",
                setting=nmconf.SETTINGS[k]["label"],
            )
            if v is None and iface
            else (
                M(
                    "control.nm.change.nm-default",
                    "{setting}: the NetworkManager default",
                    setting=nmconf.SETTINGS[k]["label"],
                )
                if v is None
                else M(
                    "control.nm.change",
                    "{setting}: {value}",
                    setting=nmconf.SETTINGS[k]["label"],
                    value=nmconf.SETTINGS[k]["values"].get(v, v),
                )
            )
        )
        for k, v in clean.items()
    ]
    said = (
        M(
            "control.nm.saved.iface",
            "The NetworkManager settings for {iface} are saved: {changes}.",
            iface=iface,
            changes=changes,
        )
        if iface
        else M(
            "control.nm.saved",
            "The NetworkManager settings of the box are saved: {changes}.",
            changes=changes,
        )
    )
    if code == 0:
        return said
    return M(
        "control.nm.not-reloaded",
        "{said} NetworkManager did not reload, so it reads the settings at its next start. It said: {output}",
        said=said,
        output=out[-160:],
    )


def wifi_country(req):
    from irate_box.hub import wificountry

    names = wificountry.countries()
    code = req.get("country")
    if not isinstance(code, str) or code not in names:
        raise Refused(
            M("control.country.unknown", "{country} is not a country code the box knows.", country=str(code)[:8])
        )
    wificountry.REGDOM_CONF.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(wificountry.REGDOM_CONF, wificountry.conf_text(code), 0o644)
    try:
        r = run("iw", "reg", "set", code, timeout=30)
        code_now, out = r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        code_now, out = 1, str(exc)
    try:
        netinv.write(netinv.scan(), CONTROL / "netinv.json")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    said = M("control.country.saved", "The WiFi country is {name} ({country}).", name=names[code], country=code)
    if code_now:
        return M(
            "control.country.boot-only",
            "{said} It is saved for the next start, but the radios did not take it now. iw said: {output}",
            said=said,
            output=out[-160:],
        )
    return M(
        "control.country.now",
        "{said} The radios use it now. The hotspot takes it the next time it starts.",
        said=said,
    )


def nm_connection(req):
    from irate_box.root import nmconnection, nmhandover

    uuid = req.get("uuid")
    try:
        if req.get("undo") is True:
            return nmconnection.undo(uuid)
        return nmconnection.change(uuid, req.get("changes"), integrated=nmhandover.integrated())
    finally:
        safeio.write(CONTROL / "nm-connection-changes.json", json.dumps(nmconnection.public()))
        try:
            netinv.write(netinv.scan(), CONTROL / "netinv.json")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


WIFISYNC = "wifisync.service"


def wifisync(req):
    do = req.get("do")
    if do not in ("off", "on", "undo"):
        raise Refused(_do_bad(("off", "on", "undo")))
    rec_path = CONTROL / "wifisync.json"
    try:
        rec = json.loads(rec_path.read_text())
    except (OSError, ValueError):
        rec = {}
    now = netinv.enabled(WIFISYNC)
    if now is None:
        raise Refused(
            M(
                "control.wifisync.missing",
                "The service {unit} is not installed on this box.",
                unit="wifisync",
            )
        )
    if do == "undo":
        if not rec.get("was"):
            raise Refused(
                M(
                    "control.wifisync.nothing-to-undo",
                    "There is nothing to undo. The box did not change {unit}.",
                    unit="wifisync",
                )
            )
        want = rec["was"] == "enabled"
    else:
        want = do == "on"
        if "was" not in rec:
            rec["was"] = now  # the image's own state, before the first change made here
    r = subprocess.run(  # noqa: TID251
        ["systemctl", "enable" if want else "disable", "--now", WIFISYNC],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode:
        raise Refused(
            M(
                "control.wifisync.failed",
                "The command {command} failed: {output}",
                command=f"systemctl {'enable' if want else 'disable'} --now {WIFISYNC}",
                output=(r.stderr or r.stdout).strip()[:200],
            )
        )
    if do == "undo":
        rec = {}
    else:
        rec.update(now="enabled" if want else "disabled", at=int(time.time()))
    safeio.write(rec_path, json.dumps(rec))
    try:
        netinv.write(netinv.scan(), CONTROL / "netinv.json")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    if do == "undo":
        return M(
            "control.wifisync.undone",
            "{unit} is back in its state before the box changed it.",
            unit="wifisync",
        )
    if want:
        return M(
            "control.wifisync.on",
            "{unit} is enabled. It disables the WiFi whenever the WiFi setting of {daemon} is off.",
            unit="wifisync",
            daemon="meshtasticd",
        )
    return M(
        "control.wifisync.off",
        "{unit} is disabled. It no longer changes the WiFi radio.",
        unit="wifisync",
    )


def _do_bad(values):
    return M(
        "control.request.do-bad",
        "The request must give do as one of these values: {values}.",
        values=list(values),
    )


def other_ap(req):
    from irate_box.root import otherap

    do, uuid = req.get("do"), req.get("uuid")
    if do not in ("remove", "leave", "undo", "forget"):
        raise Refused(_do_bad(("remove", "leave", "undo", "forget")))
    try:
        return getattr(otherap, do)(uuid)
    finally:
        safeio.write(CONTROL / "other-ap.json", json.dumps(otherap.record()))
        try:
            netinv.write(netinv.scan(), CONTROL / "netinv.json")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def radio_test(req):
    from irate_box.root import radiotests

    inv = netinv.scan()
    try:
        return radiotests.experiment(inv, req.get("iface"), req.get("exp"))
    finally:
        try:
            netinv.write(netinv.scan(), CONTROL / "netinv.json")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def nm_handover(req):
    from irate_box.root import nmhandover

    name = req.get("name")
    try:
        return nmhandover.undo(name) if req.get("undo") is True else nmhandover.handover(name)
    finally:
        try:
            netinv.write(netinv.scan(), CONTROL / "netinv.json")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass


def _wifi_after(fn):
    """A network added or forgotten (wifijoin.py), then the inventory and control/wifi-joined.json afresh."""

    def action(req):
        from irate_box.root import wifijoin

        try:
            return fn(req, wifijoin)
        finally:
            safeio.write(CONTROL / "wifi-joined.json", json.dumps(wifijoin.public()))
            try:
                netinv.write(netinv.scan(), CONTROL / "netinv.json")
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

    action.__name__ = fn.__name__
    return action


@_wifi_after
def wifi_join(req, wifijoin):
    return wifijoin.join(
        req.get("ssid"),
        req.get("security", "wpa-psk"),
        req.get("psk") or "",
        req.get("hidden", False),
        req.get("now") is True,
    )


@_wifi_after
def wifi_forget(req, wifijoin):
    return wifijoin.forget(req.get("uuid"))


def _kits_status():
    safeio.write(CONTROL / "kits.json", json.dumps(kits.status()))


def _kit_hours(req):
    hours = req.get("hours", 24)
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise Refused(
            M(
                "control.kit.hours-bad",
                "The removal time must be from {min} to {max} hours, or never.",
                min=1,
                max=24 * 365,
            )
        )
    return hours


def _kit_sweep(req):
    """The librarian's sweep by age: older sets past their days removed, control/kits.json written after it."""
    days = req.get("days")
    if type(days) is not int or not 1 <= days <= 365:
        raise Refused(
            M(
                "control.kit.days-range",
                "The days for older sets must be from {min} to {max}.",
                min=1,
                max=365,
            )
        )
    try:
        return kits.drop_old_previous(days, log=lambda *_: None)
    finally:
        _kits_status()


def _kit_req(fn):
    """A toolkit action, and control/kits.json written after it, whatever happened."""

    def action(req):
        kit = req.get("kit")
        if fn is kits.define:
            try:
                return kits.define(kit)
            finally:
                _kits_status()
        if fn not in (kits.expire, kits.drop_all_previous) and not (isinstance(kit, str) and kits.ID_RE.match(kit)):
            raise Refused(KIT_BAD)
        try:
            if fn is kits.set_extra:
                return kits.set_extra(kit, req.get("packages"))
            if fn is kits.fetch:
                budget = req.get("budget_mb", 500)
                if type(budget) is not int or not 10 <= budget <= 1 << 16:
                    raise Refused(
                        M(
                            "control.kit.budget-range",
                            "The cache budget must be from {min} to {max} MB.",
                            min=10,
                            max=1 << 16,
                        )
                    )
                return kits.fetch(kit, budget_mb=budget, log=lambda *_: None)
            if fn is kits.install:
                return kits.install(kit, _kit_hours(req), log=lambda *_: None)
            if fn is kits.set_removal:
                return kits.set_removal(kit, _kit_hours(req))
            if fn is kits.drop_all_previous:
                return kits.drop_all_previous(log=lambda *_: None)
            if fn is kits.expire:
                # One answer per toolkit removed, said one after another.
                return kits.expire() or M("control.kit.expire.none", "No toolkit is due for removal.")
            return fn(kit, log=lambda *_: None)
        finally:
            _kits_status()

    return action


ACTIONS = {
    "service": service,
    "password": password,
    "claim": claim,
    "security-policy": policy_set,
    "tls-settings": tls_settings,
    "update-check": update_check,
    "update-fetch": update_fetch,
    "update-reach": update_reach,
    "update-install": update_install,
    "update-force-install": update_force_install,
    "update-doctor": update_doctor,
    "update-clear-cache": update_clear_cache,
    "update-poll-target": update_poll_target,
    "update-set-source": update_set_source,
    "update-signing": update_signing,
    "security-scan": security_scan,
    "security-audit": security_audit,
    "security-deep-audit": security_deep_audit,
    "security-fix": security_fix,
    "addon": addon,
    "tls-make": tls_make,
    "tls-renew": tls_renew,
    "tls-switch": tls_switch,
    "tls-import": tls_import,
    "tls-box": tls_box,
    "tls-admin-only": tls_admin_only,
    "usb-scan": usb_scan,
    "usb-import": usb_import,
    "usb-export": usb_export,
    "usb-kit-import": usb_kit_import,
    "usb-kit-export": usb_kit_export,
    "usb-export-many": usb_export_many,
    "backup-image": backup_image,
    "app-install": app_install,
    "app-rollback": app_rollback,
    "access": access_set,
    "admin-login": admin_login,
    "ap-on": ap_on,
    "ap-off": ap_off,
    "share-set": share_set,
    "share-allow": share_allow,
    "pkg-check": pkg_check,
    "pkg-fetch": pkg_fetch,
    "pkg-install": pkg_install,
    "pkg-rollback": pkg_rollback,
    "pkg-settings": pkg_settings,
    "ap-try": ap_try,
    "ap-confirm": ap_confirm,
    "offline-kit": offline_kit,
    "content-export": content_export,
    "health-scan": health_scan,
    "health-fix": health_fix,
    "net-scan": net_scan,
    "uplink-set": uplink_set,
    "uplink-do": uplink_do,
    "uplink-hold": uplink_hold,
    "uplink-profile": uplink_profile,
    "nm-defaults": nm_defaults,
    "wifi-country": wifi_country,
    "nm-handover": nm_handover,
    "nm-connection": nm_connection,
    "other-ap": other_ap,
    "wifisync": wifisync,
    "radio-test": radio_test,
    "wifi-join": wifi_join,
    "wifi-forget": wifi_forget,
    "admin-gate": admin_gate,
    "kit-fetch": _kit_req(kits.fetch),
    "kit-install": _kit_req(kits.install),
    "kit-remove": _kit_req(kits.remove),
    "kit-keep": _kit_req(kits.set_removal),
    "kit-rollback": _kit_req(kits.rollback),
    "kit-drop-previous": _kit_req(kits.drop_previous),
    "kit-drop-all-previous": _kit_req(kits.drop_all_previous),
    "kit-drop-old-previous": _kit_sweep,
    "kit-define": _kit_req(kits.define),
    "kit-undefine": _kit_req(kits.undefine),
    "kit-extra": _kit_req(kits.set_extra),
    "kit-expire": _kit_req(kits.expire),
    "kit-status": lambda req: (_kits_status(), KITS_STATUS)[1],
}

KITS_STATUS = M("control.kit.status", "The toolkit status is up to date.")


def answer(rid, ok, message):
    """The result the hub reads: message in English, as every reader of results expects; a message (msg.M) also as
    itself (msg), for a page that says it in the reader's language and a test that reads its ID. Several answers
    (a check of every watched package, every toolkit due removed) are a list, said one after another.
    """
    if isinstance(message, list):
        said = (
            {"message": paragraph(*message), "msg": message}
            if any(is_message(m) for m in message)
            else {"message": paragraph(*message)}
        )
    else:
        said = {"message": render(message), "msg": message} if is_message(message) else {"message": message}
    safeio.mkdir(RESULTS)
    safeio.write(
        RESULTS / f"{rid}.json",
        json.dumps({"id": rid, "ok": ok, **said, "at": time.time()}),
    )
    # Keep the last 50 answers; the page only ever looks at recent ones.
    old = sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-50]
    for p in old:
        p.unlink(missing_ok=True)


def _discard(path):
    """Whatever the hub left at a request's name gone, a folder too; never raises, so the queue goes on."""
    try:
        path.unlink(missing_ok=True)
    except IsADirectoryError:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _mtime(path):
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0


def main():
    policy_publish()
    # A request that arrives while this runs is picked up by the same loop.
    for _ in range(100):
        pending = sorted(REQUESTS.glob("*.json"), key=_mtime)
        if not pending:
            return 0
        for path in pending:
            rid = path.stem if ID_RE.match(path.stem) else "invalid"
            what = None
            try:
                text = safeio.read_request(path)  # never a link or a FIFO
                # Gone before anything else can fail on it: a request this process cannot handle
                # must never be found again by the next one, or the path unit restarts the helper
                # for ever and nothing queued behind it is served.
                _discard(path)
                req = json.loads(text)
                if not isinstance(req, dict):
                    raise Refused(M("control.request.bad", "The request is not valid."))
                what = req.get("action")
                action = ACTIONS.get(what)
                if not action:
                    raise Refused(
                        M(
                            "control.request.unknown",
                            "The root helper does not know the action {action}.",
                            action=str(what)[:40],
                        )
                    )
                policy_check(what)
                detail = next(
                    (str(req[k])[:60] for k in ("kit", "app", "choice", "name") if isinstance(req.get(k), str)),
                    "",
                )
                safeio.write(
                    HELPER_DOING,
                    json.dumps(
                        {
                            "action": str(what)[:40],
                            "detail": detail,
                            "started": time.time(),
                        }
                    ),
                )
                answer(rid, True, action(req))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                _discard(path)
                answer(rid, False, _why(exc))
            except Exception as exc:  # noqa: BLE001 - a handler's own bug: answered, and the queue goes on
                _discard(path)
                answer(
                    rid,
                    False,
                    M(
                        "control.request.crashed",
                        "The root helper stopped with an error: {error}",
                        error=f"{type(exc).__name__}: {exc}",
                    ),
                )
            finally:
                HELPER_DOING.unlink(missing_ok=True)
            if what in UPDATES:
                # The code on disk is new now; this process still has the old in memory. Stop, and
                # the path unit starts a fresh helper for what is still queued (else a kit fetched
                # just after an update runs with the old kits.py).
                return 0
    return 0


UPDATES = ("update-install", "update-force-install", "update-reach")

# A notebook per login (hub/notebooks.py): the hub lists which should run; this makes it so.
NOTEBOOK_RE = re.compile(r"^[a-z0-9_-][a-z0-9._-]{2,31}$")
NOTEBOOK_MAX = 10


def notebooks_sync():
    """Start irate-box-notebook@<name> for each name the hub lists and stop every other. The list
    is the hub's: read without following a link, its names checked, at most NOTEBOOK_MAX.
    """
    wanted = set()
    try:
        fd = os.open(STATE / "notebooks" / ".wanted.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as fh:
            names = json.loads(fh.read(1 << 16)).get("run", [])
        wanted = {n for n in names if isinstance(n, str) and NOTEBOOK_RE.match(n)}
    except (OSError, ValueError, AttributeError):
        pass
    wanted = set(sorted(wanted)[:NOTEBOOK_MAX])
    out = run(
        "systemctl",
        "list-units",
        "irate-box-notebook@*.service",
        "--state=active,activating",
        "--plain",
        "--no-legend",
        "--no-pager",
    )
    running = {
        line.split()[0][len("irate-box-notebook@") : -len(".service")]
        for line in out.stdout.splitlines()
        if line.startswith("irate-box-notebook@")
    }
    for name in sorted(running - wanted):
        run("systemctl", "stop", "--no-block", f"irate-box-notebook@{name}.service")
    for name in sorted(wanted - running):
        run("systemctl", "start", "--no-block", f"irate-box-notebook@{name}.service")
    return sorted(wanted)


if __name__ == "__main__":
    # NetworkManager's dispatcher (root/ap.py's hook): a link came up; a following hotspot moves.
    if len(sys.argv) == 3 and sys.argv[1] == "ap-follow" and IFACE_RE.match(sys.argv[2]):
        if os.geteuid() != 0:
            sys.exit("run as root")
        rec = ap._load(ap.RECORD, {})
        if not rec.get("up") or not (rec.get("plan") or {}).get("follows_uplink"):
            sys.exit(0)  # every link change calls this (ap0's own too): leave the last note alone
        try:
            moved, note = ap.follow(run, _ap_inventory(), _ap_settings(), sys.argv[2])
            if moved:
                _ap_record_status(None, note)
        except RuntimeError as exc:
            sys.exit(str(_ap_failed(exc)))
        sys.exit(0)
    # irate-box-notebooks.path: the hub's list of notebooks to run changed.
    if sys.argv[1:] == ["notebooks-sync"]:
        if os.geteuid() != 0:
            sys.exit("run as root")
        print("notebooks running:", " ".join(notebooks_sync()) or "none")
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
            _ap_record_status(
                None,
                M(
                    "control.hotspot.reverted",
                    "{stopped} Nobody confirmed the hotspot in time, so the box restored its WiFi link.",
                    stopped=ap.stop(run),
                ),
            )
        sys.exit(0)
    # The uninstaller, before it removes the hotspot's firewall (audit M6): the hotspot off and every file of it gone.
    # Exit 1, saying what is still up, when that cannot be confirmed: the uninstaller then stops, the firewall kept.
    if sys.argv[1:] == ["ap-remove"]:
        if os.geteuid() != 0:
            sys.exit("run as root")
        off, problems = ap.remove(run)
        if not off:
            sys.exit("The hotspot could not be confirmed off: " + "; ".join(problems))
        print("The hotspot is off, and its units, connection and files are removed.")
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "set-admin":
        if os.geteuid() != 0:
            sys.exit("run as root: sudo /opt/irate-box/irate-box hub_control set-admin NAME")
        print(render(console_set_admin(sys.argv[2])))
        sys.exit(0)
    if sys.argv[1:2] == ["security-policy"]:
        if os.geteuid() != 0:
            sys.exit("run as root: sudo /opt/irate-box/irate-box hub_control security-policy hub|console")
        if sys.argv[2:] not in (["hub"], ["console"]):
            sys.exit("usage: hub_control security-policy hub|console")
        print(render(policy_set({"mode": sys.argv[2], "confirmed": True})))
        sys.exit(0)
    if sys.argv[1:2] == ["script-login"] and sys.argv[2:] in ([], ["--off"]):
        if os.geteuid() != 0:
            sys.exit("run as root: sudo /opt/irate-box/irate-box hub_control script-login [--off]")
        print(console_script_login(sys.argv[2:] != ["--off"]))
        sys.exit(0)
    if sys.argv[1:] == ["reset-password"]:
        if os.geteuid() != 0:
            sys.exit("run as root: sudo ./irate-box hub_control reset-password")
        print(render(reset_password()))
        sys.exit(0)
    if sys.argv[1:] == ["syncthing-gui-password"]:
        # For install.sh: the password on stdin.
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
            print(render(access_install()))
        except (ValueError, OSError) as exc:
            sys.exit(f"access-install: {exc}")
        sys.exit(0)
    if len(sys.argv) == 4 and sys.argv[1] == "install-app":
        if os.geteuid() != 0:
            sys.exit("run as root")
        try:
            print(render(install_app(sys.argv[2], sys.argv[3])))
        except (ValueError, OSError) as exc:
            sys.exit(f"install-app: {exc}")
        sys.exit(0)
    sys.exit(main())
