#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Keep the box on its home network: watch the link, and repair it as eagerly as the owner chose.

irate-box-uplink.service runs this as root (`uplink.py run`). Every so often it checks the
link the box reaches its network by (the default route's interface, or the one chosen on
/admin): is the interface up, and does the gateway answer (a ping, or failing that its ARP
entry: some routers drop pings but none ignore ARP). The internet is never checked: the box
is meant to work offline, and a LAN with no internet is not a fault.

Two settings, both on /admin's Network page (and `install.sh --uplink`):

  Eagerness   how soon and how far it goes once the link is down:
    off         watch and log only
    patient     reconnect now and then; NetworkManager does the rest
    standard    reconnect, repeat with back-off, then restart the network service
    persistent  ... then reset the radio (not while guests are on the hotspot)
    stubborn    ... sooner, guests or not, and finally reboot (capped per day)
  Forgiveness how much flakiness it puts up with before it acts:
    tolerant    a link must fail 5 checks and stay down 5 minutes; repeated drops are noted
    normal      3 checks and 1 minute; repeated drops lock the link to the strongest AP
    strict      2 checks and 15 s; repeated drops count as a fault and get repaired

Any single value can be overridden (the page's "Custom" fields): see FIELDS. The ladder,
mildest first: reconnect (rescan and bring the profile up again), restart (NetworkManager,
or wpa_supplicant / ifupdown / networkd), radio (unbind and rebind the USB radio, or reload
its driver), reboot. Each step waits its time after the outage is declared and the grace
has run out; a step that did not help is not repeated, but reconnect is, with back-off.

Guards: no action within 2 minutes of a settings change, nor while the owner holds repairs
(the page's "Hold"); no radio reset or reboot while guests are on the box's hotspot unless
the setting says so; no reboot while a build, a library update or an irate-box update runs,
nor within an hour of boot or of the last one, nor more than the daily cap.

Changing the owner's own WiFi profile is separate and by consent (`uplink.py profile on`,
the page's "Keep retrying"): connection.autoconnect-retries 0 and connection.auth-retries 0,
so NetworkManager never parks it (see the AP research note: NM's NO_SECRETS block after 3
failed handshakes). The old values are recorded in /etc/hub/uplink-changes.json and put
back by `profile off` and by uninstall.sh (`undo-all`).

    uplink.py run [--dry-run]       the watchdog (--dry-run: decide, log, do nothing)
    uplink.py check                 one look at the link, printed
    uplink.py presets               the levels, as numbers
    uplink.py set EAGERNESS [FORGIVENESS]
    uplink.py hold MINUTES          no repairs for that long (0 ends a hold)
    uplink.py profile on|off        the consent change to the owner's profile, and its undo
    uplink.py undo-all              for uninstall.sh

Settings in /etc/hub/uplink.json (root's; the hub asks hub_control.py to change them). What
it sees and did goes to $HUB_STATE_DIR/control/uplink.json for /admin. Stdlib only.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from irate_box.hub import netinv

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
# The reboot history the daily cap counts, kept where only root writes (F14): not in the status
# file under $STATE, whose folder the hub could swap for one with an empty history.
REBOOTS = ETC / "uplink-reboots.json"
SETTINGS = ETC / "uplink.json"
RECORD = ETC / "uplink-changes.json"
STATUS = STATE / "control" / "uplink.json"
SYS_NET = Path("/sys/class/net")
# Running any of these, the box is busy with something a reboot would spoil.
BUSY_UNITS = {"irate-box-ci.service": "a build", "irate-box-librarian.service": "a library update",
              "irate-box-control.service": "an /admin job (perhaps an update)"}
STEPS = ("reconnect", "restart", "radio", "reboot")
STEP_LABEL = {"reconnect": "reconnect", "restart": "restart the network service", "radio": "reset the radio",
              "reboot": "reboot", "pin": "lock to the strongest access point"}

EAGERNESS = {
    "off": {"check": 120, "steps": {}, "repeat": 0, "guests": "protect"},
    "patient": {"check": 120, "steps": {"reconnect": 0}, "repeat": 600, "guests": "protect"},
    "standard": {"check": 60, "steps": {"reconnect": 0, "restart": 600}, "repeat": 300, "guests": "protect"},
    "persistent": {"check": 30, "steps": {"reconnect": 0, "restart": 300, "radio": 900}, "repeat": 180,
                   "guests": "protect"},
    "stubborn": {"check": 30, "steps": {"reconnect": 0, "restart": 180, "radio": 480, "reboot": 1800}, "repeat": 120,
                 "guests": "ignore"},
}
FORGIVENESS = {
    "tolerant": {"misses": 5, "grace": 300, "flap_count": 8, "flap_window": 1800, "flap_action": "note"},
    "normal": {"misses": 3, "grace": 60, "flap_count": 4, "flap_window": 600, "flap_action": "pin"},
    "strict": {"misses": 2, "grace": 15, "flap_count": 3, "flap_window": 600, "flap_action": "repair"},
}
COMMON = {"backoff": 2.0, "max_repeat": 3600, "reboots_per_day": 3, "reboot_gap": 3600, "pause_after_change": 120}
DEFAULT = {"eagerness": "patient", "forgiveness": "normal", "iface": "auto", "overrides": {}, "hold_until": 0}
DESCRIBE = {
    "off": "Watch and log only. NetworkManager (or whatever runs the link) is left to itself.",
    "patient": "Reconnect after an outage, and again every 10 minutes or so. Nothing heavier.",
    "standard": "Reconnect, repeat with back-off, and restart the network service after 10 minutes down.",
    "persistent": "As standard but sooner, and reset the radio after 15 minutes, unless guests are on the hotspot.",
    "stubborn": "Everything, soonest, guests or not, and reboot after 30 minutes down (at most 3 a day).",
    "tolerant": "Puts up with a lot: 5 failed checks and 5 minutes down before acting; repeated drops are only noted.",
    "normal": "3 failed checks and a minute down; a link that keeps dropping is locked to the strongest access point.",
    "strict": "2 failed checks and 15 s; a link that keeps dropping counts as a fault and is repaired.",
}
# Every value a Custom field may set: (low, high), or the allowed words.
FIELDS = {
    "check": (10, 600), "misses": (1, 20), "grace": (0, 3600), "repeat": (0, 86400), "backoff": (1.0, 4.0),
    "max_repeat": (60, 86400), "guests": ("protect", "ignore"), "flap_count": (2, 50), "flap_window": (60, 86400),
    "flap_action": ("note", "pin", "repair"), "reboots_per_day": (0, 10), "reboot_gap": (600, 86400),
    "steps": {s: (0, 86400) for s in STEPS},
}
IFACE_RE = re.compile(r"^(auto|[A-Za-z0-9_][A-Za-z0-9._-]{0,14})$")  # no leading "-" (F14)


def effective(chosen):
    """The numbers the watchdog runs on: forgiveness, then eagerness, then the overrides."""
    eff = dict(COMMON, **FORGIVENESS[chosen["forgiveness"]], **EAGERNESS[chosen["eagerness"]])
    eff["steps"] = dict(eff["steps"])
    for k, v in chosen.get("overrides", {}).items():
        if k == "steps":
            for s, t in v.items():
                if t is None:
                    eff["steps"].pop(s, None)
                else:
                    eff["steps"][s] = t
        else:
            eff[k] = v
    return eff


def validate(raw):
    """A settings dict as /admin sent it → a clean one, or ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    out = dict(DEFAULT)
    e, f = raw.get("eagerness", out["eagerness"]), raw.get("forgiveness", out["forgiveness"])
    if e not in EAGERNESS or f not in FORGIVENESS:
        raise ValueError("unknown eagerness or forgiveness")
    iface = str(raw.get("iface", "auto"))
    if not IFACE_RE.match(iface):
        raise ValueError("not an interface name")
    out.update(eagerness=e, forgiveness=f, iface=iface, overrides={})
    over = raw.get("overrides") or {}
    if not isinstance(over, dict):
        raise ValueError("overrides must be an object")
    for k, v in over.items():
        rule = FIELDS.get(k)
        if rule is None:
            raise ValueError(f"{k} is not a setting")
        if k == "steps":
            if not isinstance(v, dict):
                raise ValueError("steps must be an object")
            steps = {}
            for s, t in v.items():
                if s not in STEPS:
                    raise ValueError(f"{s} is not a step")
                if t is not None:
                    t = int(t)
                    if not 0 <= t <= 86400:
                        raise ValueError(f"steps.{s} out of range")
                steps[s] = t
            out["overrides"]["steps"] = steps
        elif isinstance(rule[0], str):
            if v not in rule:
                raise ValueError(f"{k} must be one of {', '.join(rule)}")
            out["overrides"][k] = v
        else:
            v = float(v) if isinstance(rule[0], float) else int(v)
            if not rule[0] <= v <= rule[1]:
                raise ValueError(f"{k} must be between {rule[0]} and {rule[1]}")
            out["overrides"][k] = v
    hold = raw.get("hold_until", 0)
    out["hold_until"] = float(hold) if isinstance(hold, (int, float)) and hold >= 0 else 0
    return out


def load_settings():
    try:
        return validate(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT)


def save_settings(s):
    s = validate(s)
    ETC.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.chmod(tmp, 0o644)
    os.replace(tmp, SETTINGS)
    return s


def human(secs):
    secs = int(secs)
    if secs < 90:
        return f"{secs} s"
    if secs < 5400:
        return f"{round(secs / 60)} min"
    return f"{secs / 3600:.1f} h".replace(".0 h", " h")


# --- the decisions ---------------------------------------------------------------------------

class Watch:
    """What the watchdog knows and decides, apart from how it looks and acts, so it can be
    driven by a test clock. tick() takes one observation and returns the actions to take."""

    def __init__(self, eff, events=None, reboots=None):
        self.eff = eff
        self.events = deque(events or [], maxlen=100)
        self.reboots = list(reboots or [])
        self.misses = 0
        self.first_fail = None
        self.outage = None  # {since, declared, done: [...], held: [...], next_reconnect, gap}
        self.drops = deque()
        self.last_flap = float("-inf")
        self.last_ok = None
        self.pause_until = 0.0
        self.owner_off = False

    def log(self, now, kind, text):
        self.events.append({"at": now, "kind": kind, "text": text})

    def tick(self, now, obs):
        """obs: link (bool), gateway (True/False/None: not known), drops ([times]), guests (int),
        busy (str or None), uptime (s), can (set of repairs), hold_until (epoch), owner_off (the
        owner took the link down on purpose: `nmcli dev disconnect`)."""
        eff = self.eff
        if obs.get("owner_off") and not obs["link"]:
            if not self.owner_off:
                self.owner_off = True
                self.log(now, "info", "Disconnected by hand (nmcli device disconnect): left alone until it is connected again.")
            self.misses, self.first_fail, self.outage = 0, None, None
            self.drops.clear()
            return []
        self.owner_off = False
        for t in obs.get("drops", ()):
            self.drops.append(t)
        while self.drops and self.drops[0] < now - eff["flap_window"]:
            self.drops.popleft()
        healthy = obs["link"] and obs.get("gateway") is not False
        if healthy:
            return self._healthy(now, obs)
        if self.first_fail is None:
            self.first_fail = min([now, *obs.get("drops", ())]) if not obs["link"] else now
        self.misses += 1
        # A lost link is certain; an unanswered gateway may be a dropped packet or two.
        if obs["link"] and self.misses < eff["misses"]:
            return []
        if self.outage is None:
            self.outage = {"since": self.first_fail, "declared": now, "done": [], "held": [],
                           "next_reconnect": None, "gap": eff["repeat"]}
            self.log(now, "down", "Link lost." if not obs["link"] else
                     f"The gateway stopped answering ({self.misses} checks).")
        return self._repair(now, obs)

    def _healthy(self, now, obs):
        actions = []
        if self.outage:
            o = self.outage
            tried = ", ".join(STEP_LABEL[s] for s in o["done"]) or "nothing"
            self.log(now, "up", f"Back after {human(now - o['since'])} (tried: {tried}).")
        elif self.misses:
            pass  # a check or two missed, then fine: forgiven, not logged
        self.misses, self.first_fail, self.outage, self.last_ok = 0, None, None, now
        eff = self.eff
        if len(self.drops) >= eff["flap_count"] and now - self.last_flap >= eff["flap_window"]:
            self.last_flap = now
            n, w = len(self.drops), human(eff["flap_window"])
            act = eff["flap_action"]
            if act == "pin" and "pin" not in obs.get("can", ()):
                act = "note"
                extra = " (locking to one access point needs NetworkManager)"
            else:
                extra = ""
            if self._paused(now, obs) and act != "note":
                act, extra = "note", " (repairs on hold)"
            if act == "note":
                self.log(now, "flap", f"Dropped {n} times in {w}; noted{extra}.")
            elif act == "pin":
                self.log(now, "flap", f"Dropped {n} times in {w}: locking to the strongest access point.")
                actions.append("pin")
            else:
                step = "restart" if "restart" in obs.get("can", ()) else "reconnect"
                self.log(now, "flap", f"Dropped {n} times in {w}: treated as a fault, {STEP_LABEL[step]}.")
                actions.append(step)
            self.drops.clear()
        return actions

    def _paused(self, now, obs):
        return now < max(self.pause_until, obs.get("hold_until") or 0)

    def _repair(self, now, obs):
        eff, o = self.eff, self.outage
        t = now - o["since"] - eff["grace"]
        if t < 0 or self._paused(now, obs):
            return []
        can = obs.get("can", set())
        due = [s for s in STEPS if s in eff["steps"] and eff["steps"][s] <= t and s not in o["done"]]
        for step in reversed(due):  # the heaviest step that is due, once
            if step not in can:
                o["done"].append(step)
                self.log(now, "skip", f"Cannot {STEP_LABEL[step]} here; skipped.")
                continue
            why = self._held(now, obs, step)
            if why:
                if step not in o["held"]:
                    o["held"].append(step)
                    self.log(now, "held", f"Would {STEP_LABEL[step]}, but {why}.")
                continue
            o["done"].append(step)
            # Lighter steps that came due at the same time are passed over: this one covers them.
            for s in due:
                if STEPS.index(s) < STEPS.index(step) and s not in o["done"]:
                    o["done"].append(s)
            if step == "reboot":
                self.reboots.append(now)
            o["next_reconnect"] = now + max(o["gap"], 30) if eff["repeat"] else None
            self.log(now, "repair", f"{STEP_LABEL[step].capitalize()} ({human(now - o['since'])} down).")
            return [step]
        if ("reconnect" in o["done"] and eff["repeat"] and "reconnect" in can and o["next_reconnect"]
                and now >= o["next_reconnect"]):
            o["gap"] = min(o["gap"] * eff["backoff"], eff["max_repeat"])
            o["next_reconnect"] = now + o["gap"]
            self.log(now, "repair", f"Reconnect again ({human(now - o['since'])} down; next in {human(o['gap'])}).")
            return ["reconnect"]
        return []

    def _held(self, now, obs, step):
        eff = self.eff
        if step in ("radio", "reboot") and eff["guests"] == "protect" and obs.get("guests", 0) > 0:
            n = obs["guests"]
            return f"{n} guest{'s are' if n != 1 else ' is'} on the hotspot"
        if step == "reboot":
            day = [t for t in self.reboots if t > now - 86400]
            if len(day) >= eff["reboots_per_day"]:
                return f"it has rebooted {len(day)} times today already"
            if day and now - day[-1] < eff["reboot_gap"]:
                return "it rebooted less than " + human(eff["reboot_gap"]) + " ago"
            if obs.get("uptime", 1e9) < eff["reboot_gap"]:
                return "the box started less than " + human(eff["reboot_gap"]) + " ago"
            if obs.get("busy"):
                return f"{obs['busy']} is running"
        return None

    def next_step(self, now):
        """What comes next in this outage, and when, for the page."""
        if not self.outage:
            return None
        o, eff = self.outage, self.eff
        start = o["since"] + eff["grace"]
        rest = [(start + eff["steps"][s], s) for s in STEPS if s in eff["steps"] and s not in o["done"]]
        if o["next_reconnect"]:
            rest.append((o["next_reconnect"], "reconnect"))
        if not rest:
            return None
        at, step = min(rest)
        return {"step": step, "label": STEP_LABEL[step], "at": max(at, now)}


# --- looking -------------------------------------------------------------------------------

def run(*cmd, timeout=60):
    if not shutil.which(cmd[0]):
        return 127, f"{cmd[0]} not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)


def link_up(iface):
    d = SYS_NET / iface
    return netinv.read(d / "carrier") == "1" and netinv.read(d / "operstate") in ("up", "unknown")


def gateway_of(iface):
    code, out = run("ip", "-4", "route", "show", "default", "dev", iface)
    m = re.search(r"via (\S+)", out) if code == 0 else None
    return m.group(1) if m else None


def neigh_state(iface, gw):
    code, out = run("ip", "neigh", "show", gw, "dev", iface)
    m = re.search(r"\b(REACHABLE|PERMANENT|STALE|DELAY|PROBE|FAILED|INCOMPLETE)\b", out) if code == 0 else None
    return m.group(1) if m else None


def gateway_answers(iface, gw):
    if run("ping", "-c", "1", "-W", "2", "-I", iface, gw, timeout=10)[0] == 0:
        return True
    # No pong (a router that drops pings, or ping not permitted): send it one UDP packet, which
    # makes the kernel confirm the gateway's address, and see whether the gateway answered ARP.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"", (gw, 9))
    except OSError:
        pass
    # From STALE the kernel waits ~5 s (DELAY) before it probes, so give it up to 7 s.
    for _ in range(28):
        state = neigh_state(iface, gw)
        if state in ("REACHABLE", "PERMANENT"):
            return True
        if state in ("FAILED", "INCOMPLETE", None):
            return False
        time.sleep(0.25)
    return False


def guests():
    n = 0
    for iface, info in netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).items():
        if info.get("type") == "AP":
            n += netinv.ap_stations(iface)
    return n


def busy():
    for unit, what in BUSY_UNITS.items():
        if netinv.active(unit):
            return what
    return None


def uptime():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 1e9


def pick_iface(chosen, last):
    if chosen != "auto":
        return chosen
    route = netinv.default_route()
    if route:
        return route["iface"]
    if last:
        return last  # in an outage the route goes with the link: keep watching the same one
    for iface, info in sorted(netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).items()):
        if info.get("type") == "managed":
            return iface
    return None


def backend_of(iface):
    """networkmanager | wpa_supplicant | ifupdown | networkd | dhcpcd | iwd | connman | none."""
    nm = netinv.nm_state() if shutil.which("nmcli") else None
    wpa, dbus = netinv.wpa_ifaces(netinv.processes())
    ifupdown, networkd = netinv.ifupdown_ifaces(), netinv.networkd_ifaces()
    owner = netinv.owner_of(iface, nm, wpa, dbus, ifupdown, networkd)
    wifi = (SYS_NET / iface / "wireless").exists() or (SYS_NET / iface / "phy80211").exists()
    if owner == "none" and not wifi:
        return netinv.manager_of(iface, ifupdown, networkd) or "none"
    return owner


def repairs_for(backend, iface):
    can = {"networkmanager": {"reconnect", "restart", "pin"}, "wpa_supplicant": {"reconnect", "restart"},
           "ifupdown": {"reconnect", "restart"}, "networkd": {"reconnect", "restart"},
           "dhcpcd": {"restart"}}.get(backend, set())
    facts = netinv.device_facts(iface)
    wifi = (SYS_NET / iface / "phy80211").exists()
    if wifi and (facts.get("usb") or facts.get("driver")):
        can.add("radio")
    if can:
        can.add("reboot")
    return can


def nm_owner_off(iface):
    """NetworkManager's device autoconnect is off: `nmcli device disconnect` does that, and it
    stays off until someone connects the device again."""
    code, out = run("nmcli", "-t", "-f", "GENERAL.AUTOCONNECT", "dev", "show", iface)
    return code == 0 and out.strip().endswith(":no")


def nm_connection(iface):
    code, out = run("nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", iface)
    name = out.split(":", 1)[1].strip() if code == 0 and ":" in out else ""
    if not name or name == "--":
        return None
    code, out = run("nmcli", "-t", "-g", "connection.uuid", "con", "show", name)
    return {"name": name, "uuid": out.strip()} if code == 0 and out.strip() else None


# --- acting ----------------------------------------------------------------------------------

class Actor:
    def __init__(self, iface, backend, dry=False):
        self.iface, self.backend, self.dry = iface, backend, dry
        self.profile = None  # {name, uuid}: NetworkManager's connection, remembered while up

    def remember(self):
        if self.backend == "networkmanager":
            self.profile = nm_connection(self.iface) or self.profile

    def do(self, step):
        if self.dry:
            return f"(dry run: would {STEP_LABEL[step]})"
        return getattr(self, "_" + step)()

    def _reconnect(self):
        i = self.iface
        if self.backend == "networkmanager":
            run("nmcli", "dev", "wifi", "rescan", "ifname", i, timeout=20)
            if self.profile:
                return run("nmcli", "-w", "45", "con", "up", "uuid", self.profile["uuid"], "ifname", i, timeout=60)[1]
            return run("nmcli", "-w", "45", "dev", "connect", i, timeout=60)[1]
        if self.backend == "wpa_supplicant":
            return run("wpa_cli", "-i", i, "reassociate")[1]
        if self.backend == "ifupdown":
            run("ifdown", "--force", i, timeout=60)
            return run("ifup", i, timeout=120)[1]
        if self.backend == "networkd":
            return run("networkctl", "reconfigure", i)[1]
        return "no way to reconnect here"

    def _restart(self):
        i = self.iface
        if self.backend == "networkmanager":
            return run("systemctl", "restart", "NetworkManager", timeout=90)[1] or "NetworkManager restarted"
        if self.backend == "wpa_supplicant":
            if netinv.active(f"wpa_supplicant@{i}"):
                return run("systemctl", "restart", f"wpa_supplicant@{i}", timeout=90)[1] or f"wpa_supplicant@{i} restarted"
            if i in netinv.ifupdown_ifaces():
                run("ifdown", "--force", i, timeout=60)
                return run("ifup", i, timeout=120)[1] or f"{i} down and up"
            out = run("systemctl", "restart", "wpa_supplicant", timeout=90)[1]
            if i in netinv.networkd_ifaces():
                run("networkctl", "reconfigure", i)
            return out or "wpa_supplicant restarted"
        if self.backend == "ifupdown":
            return run("systemctl", "restart", "networking", timeout=120)[1] or "networking restarted"
        if self.backend == "networkd":
            return run("systemctl", "restart", "systemd-networkd", timeout=90)[1] or "systemd-networkd restarted"
        if self.backend == "dhcpcd":
            return run("systemctl", "restart", "dhcpcd", timeout=90)[1] or "dhcpcd restarted"
        return "no network service to restart here"

    def _radio(self):
        facts = netinv.device_facts(self.iface)
        usb = facts.get("usb")
        if usb and re.fullmatch(r"[0-9]+-[0-9.]+", usb.get("port") or ""):
            drv = Path("/sys/bus/usb/drivers/usb")
            (drv / "unbind").write_text(usb["port"])
            time.sleep(3)
            (drv / "bind").write_text(usb["port"])
            return f"USB device {usb['port']} ({usb.get('product') or usb['id']}) unbound and bound again"
        mod = facts.get("driver")
        if not mod or not re.fullmatch(r"[A-Za-z0-9_-]+", mod):
            return "radio driver not known"
        code, out = run("modprobe", "-r", mod, timeout=60)
        if code:
            return f"modprobe -r {mod}: {out}"
        time.sleep(2)
        return run("modprobe", mod, timeout=60)[1] or f"{mod} reloaded"

    def _reboot(self):
        run("sync")
        subprocess.Popen(["systemctl", "reboot"])
        return "rebooting"

    def pin(self):
        if self.dry:
            return "(dry run: would lock to the strongest access point)"
        i = self.iface
        link = netinv.parse_link(netinv.run("iw", "dev", i, "link")[1])
        ssid = link.get("ssid")
        if not ssid or not self.profile:
            return "not connected to a known network: nothing to lock"
        code, out = run("nmcli", "-t", "-f", "BSSID,SSID,SIGNAL", "dev", "wifi", "list", "ifname", i, "--rescan", "no")
        rows = [r for r in netinv.nm_fields(out) if len(r) >= 3 and r[1] == ssid and r[2].isdigit()]
        if not rows:
            return f"no access point for {ssid} in the last scan"
        bssid = max(rows, key=lambda r: int(r[2]))[0]
        # For this connection only: the profile is not changed, and the next reconnect is free again.
        code, out = run("nmcli", "-w", "45", "con", "up", "uuid", self.profile["uuid"], "ifname", i, "ap", bssid, timeout=60)
        return f"locked to {bssid}" + ("" if code == 0 else f": {out}")


# --- the loop ----------------------------------------------------------------------------------

class DropWatcher(threading.Thread):
    """`ip -o monitor link`: each time the interface loses its carrier, between two checks too."""

    def __init__(self, wake):
        super().__init__(daemon=True)
        self.iface = None
        self.wake = wake
        self.lock = threading.Lock()
        self.drops = []
        self.up = {}

    def take(self):
        with self.lock:
            d, self.drops = self.drops, []
        return d

    def run(self):
        while True:
            try:
                proc = subprocess.Popen(["ip", "-o", "monitor", "link"], stdout=subprocess.PIPE, text=True)
            except OSError:
                return
            for line in proc.stdout:
                m = re.match(r"^(?:Deleted )?\d+:\s+([^:@]+)[@:].*<([^>]*)>", line)
                if not m:
                    continue
                name, flags = m.group(1), m.group(2).split(",")
                lower = "LOWER_UP" in flags and not line.startswith("Deleted")
                was = self.up.get(name)
                self.up[name] = lower
                if name == self.iface and was and not lower:
                    with self.lock:
                        self.drops.append(time.time())
                    self.wake.set()
            proc.wait()
            time.sleep(5)


def write_status(data):
    # As root, in control/, which the hub can change: never through a link it planted (F13).
    from irate_box.root import safeio
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(STATUS, json.dumps(data, indent=2))
    if "reboots" in data and os.geteuid() == 0:
        safeio.write(REBOOTS, json.dumps(data["reboots"]), 0o600)


def _reboot_history(old):
    """Root's own record when running as root; the status file only in a test or a dry run."""
    if os.geteuid() != 0 or not REBOOTS.exists():
        # Not root, or the first start since the record moved here: the status file's history.
        return old.get("reboots")
    try:
        got = json.loads(REBOOTS.read_text())
        return [float(t) for t in got if isinstance(t, (int, float))]
    except (OSError, ValueError, TypeError):
        return []


def read_status():
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}


def profile_record():
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return None


def _settings_mtime():
    return SETTINGS.stat().st_mtime if SETTINGS.exists() else 0


def nap(wake, secs, mtime):
    """Sleep up to secs, waking early for a link drop or a settings change (looked at every 5 s,
    so a choice made on /admin shows within seconds, whatever the check interval)."""
    end = time.time() + secs
    while (left := end - time.time()) > 0:
        if wake.wait(min(left, 5)) or _settings_mtime() != mtime:
            break
    wake.clear()


def serve(dry=False):
    old = read_status()
    chosen = load_settings()
    mtime = _settings_mtime()
    w = Watch(effective(chosen), old.get("events"), _reboot_history(old))
    now = time.time()
    if old.get("events") and old["events"][-1]["text"].startswith("Reboot"):
        w.log(now, "info", "Started again after the reboot.")
    w.log(now, "info", f"Watching ({chosen['eagerness']}, {chosen['forgiveness']})" + (" — dry run" if dry else "") + ".")
    wake = threading.Event()
    watcher = DropWatcher(wake)
    watcher.start()
    iface, backend, actor, can, looked = None, None, None, set(), 0
    pinned = None
    while True:
        now = time.time()
        # Settings changed on /admin: take them, and give the owner a moment before acting.
        m = _settings_mtime()
        if m != mtime:
            before, (mtime, chosen) = chosen, (m, load_settings())
            if {k: v for k, v in before.items() if k != "hold_until"} != {k: v for k, v in chosen.items() if k != "hold_until"}:
                w.eff = effective(chosen)
                w.pause_until = now + w.eff["pause_after_change"]
                w.log(now, "info", f"Settings changed: {chosen['eagerness']}, {chosen['forgiveness']}"
                      + (", with custom values" if chosen["overrides"] else "") + ".")
            if before.get("hold_until") != chosen.get("hold_until"):
                hold = chosen.get("hold_until") or 0
                w.log(now, "info", f"Repairs held until {time.strftime('%H:%M', time.localtime(hold))}."
                      if hold > now else "Hold ended: repairs as set.")
        new_iface = pick_iface(chosen["iface"], iface)
        if new_iface != iface or now - looked > 600:
            if new_iface != iface and iface:
                w.log(now, "info", f"Now watching {new_iface}.")
            iface, looked = new_iface, now
            if iface:
                backend = backend_of(iface)
                can = repairs_for(backend, iface)
                actor = Actor(iface, backend, dry) if not actor or actor.iface != iface else actor
                actor.backend = backend
                watcher.iface = iface
        if not iface:
            write_status({"at": now, "state": "no-link", "iface": None, "events": list(w.events),
                          "chosen": chosen, "settings": w.eff, "reboots": w.reboots, "dry_run": dry})
            nap(wake, w.eff["check"], mtime)
            continue
        up = link_up(iface)
        gw = gateway_of(iface) if up else None
        answers = gateway_answers(iface, gw) if gw else (False if up else None)
        obs = {"link": up, "gateway": answers, "drops": watcher.take(), "guests": guests(), "busy": busy(),
               "uptime": uptime(), "can": can, "hold_until": chosen.get("hold_until", 0),
               "owner_off": not up and backend == "networkmanager" and nm_owner_off(iface)}
        actions = w.tick(now, obs)
        if up and answers:
            actor.remember()
        for step in actions:
            if step == "pin":
                result = actor.pin()
                if "locked to" in result:
                    pinned = {"bssid": result.split()[2].rstrip(":"), "at": now}
            else:
                if step == "reboot":
                    write_status(_status(w, now, chosen, iface, backend, can, up, gw, answers, obs, pinned, dry))
                result = actor.do(step)
                pinned = None if step in ("reconnect", "restart", "radio") else pinned
            if result:
                w.log(time.time(), "result", str(result)[:300])
        write_status(_status(w, time.time(), chosen, iface, backend, can, up, gw, answers, obs, pinned, dry))
        # While a check or an outage is under way, look again sooner than the steady pace.
        wait = w.eff["check"] if not (w.misses or w.outage) else min(w.eff["check"], 15)
        nap(wake, wait, mtime)


def _status(w, now, chosen, iface, backend, can, up, gw, answers, obs, pinned, dry):
    link = netinv.parse_link(netinv.run("iw", "dev", iface, "link")[1]) if up and (SYS_NET / iface / "phy80211").exists() else {}
    state = ("off" if w.owner_off else "down" if w.outage else "checking" if w.misses
             else "up" if up and answers else "down")
    return {"at": now, "state": state, "iface": iface, "backend": backend, "repairs": sorted(can),
            "link": link, "gateway": gw, "gateway_answers": answers, "since": w.outage["since"] if w.outage else w.last_ok,
            "misses": w.misses, "drops_in_window": len(w.drops), "guests": obs["guests"],
            "outage": ({k: w.outage[k] for k in ("since", "declared", "done", "held")} if w.outage else None),
            "next": w.next_step(now), "paused_until": max(w.pause_until, chosen.get("hold_until") or 0) or None,
            "pinned": pinned, "events": list(w.events), "reboots": w.reboots[-10:], "chosen": chosen,
            "settings": w.eff, "profile_change": profile_record(), "dry_run": dry}


# --- the owner's profile, by consent ---------------------------------------------------------

def _uplink_profile():
    chosen = load_settings()
    iface = pick_iface(chosen["iface"], None)
    if not iface or backend_of(iface) != "networkmanager":
        raise ValueError("the box's link is not run by NetworkManager, so there is no profile to change")
    prof = nm_connection(iface)
    if not prof:
        raise ValueError(f"{iface} is not connected, so its profile is not known; try again once it is")
    return prof


def profile(on):
    rec = profile_record()
    if on:
        prof = _uplink_profile()
        if rec and rec["uuid"] == prof["uuid"]:
            return f"{prof['name']}: already set to keep retrying"
        if rec:
            profile(False)
        code, out = run("nmcli", "-t", "-g", "connection.autoconnect-retries,connection.auth-retries",
                        "con", "show", "uuid", prof["uuid"])
        vals = [v.strip().split(" ")[0] for v in out.splitlines()] if code == 0 else []
        if len(vals) != 2 or not all(re.fullmatch(r"-?\d+", v) for v in vals):
            raise ValueError(f"could not read {prof['name']}'s settings: {out}")
        code, out = run("nmcli", "con", "modify", "uuid", prof["uuid"],
                        "connection.autoconnect-retries", "0", "connection.auth-retries", "0")
        if code:
            raise ValueError(f"nmcli: {out}")
        RECORD.write_text(json.dumps({"uuid": prof["uuid"], "name": prof["name"], "at": time.time(),
                                      "old": {"connection.autoconnect-retries": vals[0],
                                              "connection.auth-retries": vals[1]}}, indent=2))
        os.chmod(RECORD, 0o644)
        return (f"{prof['name']}: autoconnect-retries {vals[0]} → 0 and auth-retries {vals[1]} → 0, "
                "so NetworkManager keeps trying. Undo puts them back.")
    if not rec:
        return "nothing to undo"
    args = [x for k, v in rec["old"].items() for x in (k, v)]
    code, out = run("nmcli", "con", "modify", "uuid", rec["uuid"], *args)
    if code and "not exist" not in out and "unknown connection" not in out.lower():
        raise ValueError(f"nmcli: {out}")
    RECORD.unlink(missing_ok=True)
    return f"{rec['name']}: back to " + ", ".join(f"{k.split('.')[1]} {v}" for k, v in rec["old"].items())


def check():
    chosen = load_settings()
    iface = pick_iface(chosen["iface"], None)
    if not iface:
        return "no link to watch"
    backend = backend_of(iface)
    up = link_up(iface)
    gw = gateway_of(iface) if up else None
    ans = gateway_answers(iface, gw) if gw else None
    link = netinv.parse_link(netinv.run("iw", "dev", iface, "link")[1]) if (SYS_NET / iface / "phy80211").exists() else {}
    lines = [f"{iface}: run by {backend}; repairs here: {', '.join(sorted(repairs_for(backend, iface))) or 'none'}",
             f"link {'up' if up else 'down'}" + (f", {link.get('ssid')} via {link.get('bssid')} ch {link.get('channel')} "
                                                f"{link.get('signal')} dBm" if link else ""),
             f"gateway {gw or 'none'}: {'answers' if ans else 'no answer' if gw else '-'}",
             f"settings: {chosen['eagerness']}, {chosen['forgiveness']}"
             + (f", overrides {json.dumps(chosen['overrides'])}" if chosen["overrides"] else "")]
    return "\n".join(lines)


def presets():
    out = []
    for e in EAGERNESS:
        for f in FORGIVENESS:
            eff = effective({"eagerness": e, "forgiveness": f, "overrides": {}})
            detect = f"{eff['misses']} × {human(eff['check'])}"
            steps = ", ".join(f"{s} +{human(eff['grace'] + t)}" for s, t in sorted(eff["steps"].items(), key=lambda x: x[1]))
            out.append(f"{e:10} {f:8}  check {human(eff['check']):6} detect {detect:12} steps: {steps or 'none'}"
                       f"; flaps {eff['flap_count']}/{human(eff['flap_window'])} → {eff['flap_action']}")
    return "\n".join(out)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "presets":
        print(presets())
        return 0
    # A dry run only looks, so it may run unprivileged (for trying it on a board).
    if os.geteuid() != 0 and cmd != "check" and not (cmd == "run" and "--dry-run" in rest):
        sys.exit("run as root")
    if cmd == "run":
        serve(dry="--dry-run" in rest)
    elif cmd == "check":
        print(check())
    elif cmd == "set" and 1 <= len(rest) <= 2:
        s = load_settings()
        s["eagerness"] = rest[0]
        if len(rest) == 2:
            s["forgiveness"] = rest[1]
        try:
            s = save_settings(s)
        except ValueError as exc:
            sys.exit(f"uplink.py set: {exc} (eagerness: {', '.join(EAGERNESS)}; forgiveness: {', '.join(FORGIVENESS)})")
        print(f"uplink: {s['eagerness']}, {s['forgiveness']}")
    elif cmd == "hold" and len(rest) == 1 and rest[0].isdigit():
        s = load_settings()
        s["hold_until"] = time.time() + int(rest[0]) * 60 if int(rest[0]) else 0
        save_settings(s)
        print(f"repairs held for {rest[0]} min" if int(rest[0]) else "hold ended")
    elif cmd == "profile" and rest in (["on"], ["off"]):
        try:
            print(profile(rest[0] == "on"))
        except ValueError as exc:
            sys.exit(f"uplink.py profile: {exc}")
    elif cmd == "undo-all":
        try:
            print(profile(False))
        except ValueError as exc:
            print(f"profile: {exc}")
    else:
        sys.exit("usage: uplink.py run [--dry-run] | check | presets | set EAGERNESS [FORGIVENESS] | hold MINUTES "
                 "| profile on|off | undo-all")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
