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
Outages close together are one episode: one that starts within `relapse` (15 min) of the
last one's end continues it, the steps' times count from its start, and a step that "worked"
but was followed by a relapse did not hold, so it is passed over while a heavier one is left
(a flapping link's repair climbs the same way). It ends after `relapse` steady.

A wedged radio driver (wedge_evidence: the backend gone though the interface is there, no
supplicant for it, scans failing as busy, crash watch's radio failure) is said, with the
evidence; with `on_wedge: radio` (the owner's, off by default) it goes straight to a radio
reset if the level reaches it, as reconnecting and restarting can't mend it.

When nothing the level allows can be done here (NetworkManager gone, say, and the level
stops before a radio reset) it has stalled: it says so, and what could help, and /admin
offers that step by hand (hub_control's uplink-do, through /etc/hub/uplink-now.json).

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
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from irate_box.hub import linkhistory, netinv

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
# The reboot history the daily cap counts, kept where only root writes (F14): not in the status
# file under $STATE, whose folder the hub could swap for one with an empty history.
REBOOTS = ETC / "uplink-reboots.json"
SETTINGS = ETC / "uplink.json"
RECORD = ETC / "uplink-changes.json"
# A step asked for on /admin (hub_control's uplink-do), taken by the running watchdog within seconds,
# so its guards apply and its log says what happened. Root's folder: the hub cannot plant one.
NOW = ETC / "uplink-now.json"
STATUS = STATE / "control" / "uplink.json"
HISTORY = STATE / "control" / "uplink-history.json"   # each link's five-minute history (linkhistory.py)
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
COMMON = {"backoff": 2.0, "max_repeat": 3600, "reboots_per_day": 3, "reboot_gap": 3600, "pause_after_change": 120,
          "relapse": 900}
DEFAULT = {"eagerness": "patient", "forgiveness": "normal", "iface": "auto", "overrides": {}, "hold_until": 0,
           "on_wedge": "ladder"}
# When the evidence says the radio's driver has wedged (wedge_evidence): keep to the ladder as set,
# or go straight to a radio reset if the level reaches it (Tom, 2026-10-09: a setting per box, off).
ON_WEDGE = ("ladder", "radio")
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
    "max_repeat": (60, 86400), "relapse": (60, 86400), "guests": ("protect", "ignore"), "flap_count": (2, 50), "flap_window": (60, 86400),
    "flap_action": ("note", "pin", "repair"), "reboots_per_day": (0, 10), "reboot_gap": (600, 86400),
    "steps": {s: (0, 86400) for s in STEPS},
}
IFACE_RE = re.compile(r"^(auto|[A-Za-z0-9_][A-Za-z0-9._-]{0,14})$")  # no leading "-" (F14)


def effective(chosen):
    """The numbers the watchdog runs on: forgiveness, then eagerness, then the overrides."""
    eff = dict(COMMON, **FORGIVENESS[chosen["forgiveness"]], **EAGERNESS[chosen["eagerness"]])
    eff["steps"] = dict(eff["steps"])
    eff["on_wedge"] = chosen.get("on_wedge", "ladder")
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
    w = raw.get("on_wedge", "ladder")
    if w not in ON_WEDGE:
        raise ValueError(f"on_wedge must be one of {', '.join(ON_WEDGE)}")
    out.update(eagerness=e, forgiveness=f, iface=iface, overrides={}, on_wedge=w)
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
        self.can = set()  # the repairs possible at the last look, for next_step() and stall()
        # Outages close together are one episode (uplink-ladder-plan, stage 3): {since, outages,
        # failed: steps that "worked" and were followed by a relapse, tried, last_end, last_done}.
        self.episode = None

    def log(self, now, kind, text):
        self.events.append({"at": now, "kind": kind, "text": text})

    def tick(self, now, obs):
        """obs: link (bool), gateway (True/False/None: not known), drops ([times]), guests (int),
        busy (str or None), uptime (s), can (set of repairs), hold_until (epoch), owner_off (the
        owner took the link down on purpose: `nmcli dev disconnect`)."""
        eff = self.eff
        self.can = set(obs.get("can", ()))
        if obs.get("owner_off") and not obs["link"]:
            if not self.owner_off:
                self.owner_off = True
                self.log(now, "info", "Disconnected by hand (nmcli device disconnect): left alone until it is connected again.")
            self.misses, self.first_fail, self.outage, self.episode = 0, None, None, None
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
            self.outage = {"since": self.first_fail, "declared": now, "done": [], "held": [], "skipped": [],
                           "next_reconnect": None, "gap": eff["repeat"]}
            self.log(now, "down", "Link lost." if not obs["link"] else
                     f"The gateway stopped answering ({self.misses} checks).")
            self._episode(now, self.first_fail)
        return self._repair(now, obs)

    def _episode(self, now, start):
        """An outage (or a flapping link's repair) begins: within `relapse` of the last one's end it
        continues the episode, and what was done last time did not hold; else a new episode."""
        ep, eff = self.episode, self.eff
        if ep and ep["last_end"] is not None and start - ep["last_end"] <= eff["relapse"]:
            ep["outages"] += 1
            gone = [s for s in ep["last_done"] if s not in ep["failed"]]
            ep["failed"] += gone
            ep["last_end"], ep["last_done"] = None, []
            if gone:
                self.log(now, "info", f"Down again {human(start - (ep['end_was'] or start))} after it came back: "
                         f"{', '.join(STEP_LABEL[s] for s in gone)} did not hold, so it is not repeated while a heavier step is left "
                         f"(outage {ep['outages']} of this episode).")
            return ep
        self.episode = {"since": start, "outages": 1, "failed": [], "tried": [], "last_end": None, "last_done": [], "end_was": None}
        return self.episode

    def _passed(self, step):
        """A step that did not hold in this episode is passed over while a heavier one the level
        allows is possible: the episode climbs, and stays at the top of the level's ladder once
        everything below has failed. The top (or the only) step may be tried again."""
        ep, eff = self.episode, self.eff
        if not ep or step not in ep["failed"]:
            return False
        return any(STEPS.index(h) > STEPS.index(step) and h in eff["steps"] and h in self.can for h in STEPS)

    def _healthy(self, now, obs):
        actions = []
        eff, ep = self.eff, self.episode
        if self.outage:
            o = self.outage
            done = [s for s in o["done"] if s not in o.get("skipped", ())]
            tried = ", ".join(STEP_LABEL[s] for s in done) or "nothing"
            self.log(now, "up", f"Back after {human(now - o['since'])} (tried: {tried}).")
            if ep:
                ep["last_end"] = ep["end_was"] = now
                ep["last_done"] = done
                ep["tried"] += [s for s in done if s not in ep["tried"]]
        elif self.misses:
            pass  # a check or two missed, then fine: forgiven, not logged
        self.misses, self.first_fail, self.outage, self.last_ok = 0, None, None, now
        if ep and ep["last_end"] is not None and now - ep["last_end"] >= eff["relapse"]:
            if ep["outages"] > 1:
                tried = ", ".join(STEP_LABEL[s] for s in ep["tried"]) or "nothing"
                self.log(now, "info", f"Steady again after an episode of {ep['outages']} outages (tried: {tried}).")
            self.episode = None
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
                # A fault, repaired as an episode's outage is: it climbs past what did not hold. A
                # flapping link is reconnecting by itself already, so it starts past reconnect.
                ep = self._episode(now, now)
                ep["last_end"] = None
                can = obs.get("can", ())
                ladder = [s for s in STEPS if s in eff["steps"] and s in can and not self._passed(s)]
                step = next((s for s in ladder if s != "reconnect"), ladder[0] if ladder else None)
                why = self._held(now, obs, step) if step else "no step the level allows is possible here"
                if why:
                    self.log(now, "flap", f"Dropped {n} times in {w}: treated as a fault, but {why}.")
                else:
                    self.log(now, "flap", f"Dropped {n} times in {w}: treated as a fault, {STEP_LABEL[step]}.")
                    actions.append(step)
                    ep["last_end"] = ep["end_was"] = now
                    ep["last_done"] = [step]
                    ep["tried"] += [step] if step not in ep["tried"] else []
            self.drops.clear()
        return actions

    def _paused(self, now, obs):
        return now < max(self.pause_until, obs.get("hold_until") or 0)

    def _repair(self, now, obs):
        eff, o = self.eff, self.outage
        wedged = obs.get("wedged")
        if wedged and not o.get("wedged"):
            o["wedged"] = wedged
            self.log(now, "wedged", f"The radio's driver looks wedged: {wedged}.")
        if now - o["since"] < eff["grace"] or self._paused(now, obs):
            return []
        can = obs.get("can", set())
        # The owner's choice (on_wedge "radio"): reconnecting and restarting can't mend a wedged
        # driver, and on the Lyra they made it worse, so straight to the radio reset if the level
        # reaches it. Beyond the level, or held, it waits or stalls, saying why.
        if o.get("wedged") and eff["on_wedge"] == "radio" and "radio" not in o["done"]:
            for s in ("reconnect", "restart"):
                if s not in o["done"]:
                    o["done"].append(s)
                    o["skipped"].append(s)
            if "radio" in eff["steps"] and "radio" in can:
                why = self._held(now, obs, "radio")
                if not why:
                    o["done"].append("radio")
                    o["next_reconnect"] = None
                    self.log(now, "repair", f"Reset the radio ({human(now - o['since'])} down): reconnecting or restarting can't mend a wedged driver.")
                    return ["radio"]
                if "radio" not in o["held"]:
                    o["held"].append("radio")
                    self.log(now, "held", f"Would reset the radio, but {why}.")
        # Each step's time counts from the episode's start, so relapses climb as one long outage
        # would; a step that did not hold earlier in it is passed over (_passed).
        t = now - (self.episode or o)["since"] - eff["grace"]
        due = [s for s in STEPS if s in eff["steps"] and eff["steps"][s] <= t and s not in o["done"] and not self._passed(s)]
        for step in reversed(due):  # the heaviest step that is due, once
            if step not in can:
                o["done"].append(step)
                o["skipped"].append(step)  # done with, but never tried: the page and the log say so
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
                and now >= o["next_reconnect"] and not (o.get("wedged") and eff["on_wedge"] == "radio")):
            o["gap"] = min(o["gap"] * eff["backoff"], eff["max_repeat"])
            o["next_reconnect"] = now + o["gap"]
            self.log(now, "repair", f"Reconnect again ({human(now - o['since'])} down; next in {human(o['gap'])}).")
            return ["reconnect"]
        st = self.stall(now)
        if st and not o.get("stalled"):
            o["stalled"] = True
            self.log(now, "stalled", st["text"])
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
        """What comes next in this outage, and when, for the page: only a step that is possible
        here (a ladder whose rungs are all impossible has no next step; it has stalled)."""
        if not self.outage:
            return None
        o, eff = self.outage, self.eff
        start, floor = (self.episode or o)["since"] + eff["grace"], o["since"] + eff["grace"]
        skip = ("reconnect", "restart") if o.get("wedged") and eff["on_wedge"] == "radio" else ()
        rest = [(max(start + eff["steps"][s], floor), s) for s in STEPS
                if s in eff["steps"] and s not in o["done"] and s in self.can and not self._passed(s) and s not in skip]
        if o["next_reconnect"] and eff["repeat"] and "reconnect" in self.can and not skip:
            rest.append((o["next_reconnect"], "reconnect"))
        if not rest:
            return None
        at, step = min(rest)
        return {"step": step, "label": STEP_LABEL[step], "at": max(at, now)}

    def stall(self, now):
        """In an outage, past the grace, with nothing left that the level allows and the box can
        do: {needs: the lightest possible step beyond the level, or None; text}. Not when the level
        is watch-only (that is the owner's choice, not a stall), nor while a step is only held."""
        o, eff = self.outage, self.eff
        if not o or not eff["steps"] or now - o["since"] < eff["grace"] or self.next_step(now):
            return None
        needs = next((s for s in STEPS if s in self.can and s not in eff["steps"] and s not in o["done"]), None)
        top = max(eff["steps"], key=STEPS.index)
        text = (f"Stalled: what could help now is to {STEP_LABEL[needs]}, and this level goes no further than to "
                f"{STEP_LABEL[top]}." if needs else "Stalled: nothing the box can do from here could help "
                f"(possible now: {', '.join(sorted(self.can)) or 'nothing'}).")
        if o.get("wedged"):
            text += f" The radio's driver looks wedged: {o['wedged']}."
        return {"needs": needs, "label": STEP_LABEL[needs] if needs else None, "text": text}

    def by_hand(self, now, obs, step):
        """A step the owner asked for on /admin: done now, whatever the level and the hold, but
        only when possible here and not held by a guard (guests, the reboot caps). The steps to
        take, and the reason when held."""
        if step not in STEPS:
            return [], f"{step} is not a step"
        if step not in obs.get("can", ()):
            self.log(now, "skip", f"Asked on /admin to {STEP_LABEL[step]}, but it cannot be done here.")
            return [], f"cannot {STEP_LABEL[step]} here"
        why = self._held(now, obs, step)
        if why:
            self.log(now, "held", f"Asked on /admin to {STEP_LABEL[step]}, but {why}.")
            return [], why
        if step == "reboot":
            self.reboots.append(now)
        if self.outage and step not in self.outage["done"]:
            self.outage["done"].append(step)
        self.log(now, "repair", f"{STEP_LABEL[step].capitalize()}, asked on /admin.")
        return [step], None


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


SCAN_BUSY = re.compile(r"CTRL-EVENT-SCAN-FAILED ret=-16\b")
NO_SUPPLICANT = re.compile(r"Couldn't initialize supplicant interface|supplicant interface keeps failing|Failed to initialize driver interface")


def wedge_evidence(iface, backend, was_backend, journal, cw_events, now, exists=True, wifi=True):
    """Why the radio's driver looks wedged, in words, or None (uplink-ladder-plan, stage 4). From
    the last two minutes of NetworkManager's and wpa_supplicant's journal, crash watch's radio
    events, and the backend: any one of
      - the backend gone (it was there before) while the interface still exists
      - NetworkManager unable to initialise the supplicant for it
      - scans failing as busy (ret=-16) more than 5 times
      - crash watch's radio failure for it in the last 30 minutes, not since recovered
    as the Lyra's AIC8800 showed when its firmware wedged (2026-10-09)."""
    if not wifi:
        return None
    why = []
    if exists and backend == "none" and was_backend not in (None, "none"):
        why.append(f"{was_backend} no longer runs {iface}, though it is still there")
    mine = [l for l in journal if f"{iface}:" in l or f"({iface})" in l or f"'{iface}'" in l]
    if any(NO_SUPPLICANT.search(l) for l in mine):
        why.append(f"the supplicant cannot be started for {iface}")
    busy = sum(1 for l in mine if SCAN_BUSY.search(l))
    if busy > 5:
        why.append(f"scans keep failing as busy ({busy} in 2 minutes)")
    last = next((e for e in reversed(cw_events or []) if e.get("iface") == iface), None)
    if last and last.get("kind") == "failed" and now - last.get("at", 0) < 1800:
        why.append(f"crash watch saw the radio fail ({last.get('text', '')[:120]})")
    return "; ".join(why) or None


def recent_journal(secs=120):
    code, out = run("journalctl", f"--since=@{int(time.time() - secs)}", "--no-pager", "-o", "cat",
                    "-t", "NetworkManager", "-t", "wpa_supplicant", timeout=20)
    return out.splitlines() if code == 0 else []


def crashwatch_events():
    try:
        return json.loads((STATE / "crashwatch" / "events.json").read_text())
    except (OSError, ValueError):
        return []


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
            out = run("systemctl", "restart", "NetworkManager", timeout=90)[1] or "NetworkManager restarted"
            # Its restart takes the box's hotspot down too (the Lyra, 2026-10-09): start it again.
            from irate_box.root import radio
            hs = radio.restore_hotspot()
            return out + (f"; {hs}" if hs else "")
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
        # The one reset crash watch uses too (root/radio.py): NetworkManager stopped around it,
        # unbind/bind, then `authorized` 0/1, then the driver; the hotspot started again after.
        from irate_box.root import radio
        facts = netinv.device_facts(self.iface)
        usb = facts.get("usb") or {}
        return radio.reset(self.iface, usb.get("port"), facts.get("driver"), usb.get("product") or usb.get("id"))

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


def _clock_trusted():
    try:
        from irate_box.root import rtc
        return bool(rtc.trusted())
    except Exception:  # noqa: BLE001 - no module, no timedatectl: not known to be right
        return False


class History:
    """The links' uptime history (linkhistory.py), for /admin's heatmaps: the watched link's full
    state, and link only for the box's other wired and WiFi-client interfaces. Kept in memory and
    written when a five-minute slot closes, so the card sees one small write in five minutes.
    Nothing is recorded while the clock is not trusted (no network time, not set by hand)."""

    def __init__(self, trusted=_clock_trusted):
        try:
            self.hist = json.loads(HISTORY.read_text())
            if not isinstance(self.hist, dict):
                raise ValueError
        except (OSError, ValueError):
            self.hist = {}
        self.trusted, self.slot, self.ok, self.dirty = trusted, None, False, False
        self.types, self.typed = {}, 0

    def _turn(self, now):
        idx = int(now // linkhistory.SLOT)
        if idx != self.slot:
            self.flush()
            self.slot, self.ok = idx, self.trusted()
        return self.ok

    def note(self, now, watched, state, others=None):
        """state: the watched link's (linkhistory's letters); others: {iface: "u" | "d"}."""
        if not self._turn(now):
            return
        if watched and state:
            self.dirty |= linkhistory.record(self.hist, watched, state, now, "uplink")
        for iface, st in (others or {}).items():
            self.dirty |= linkhistory.record(self.hist, iface, st, now, "link")

    def flush(self):
        if not self.dirty:
            return
        from irate_box.root import safeio
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(HISTORY, json.dumps(self.hist, separators=(",", ":")))
        self.dirty = False

    def others(self, watched, now):
        """The other wired and WiFi-client links, up or down (the hotspot's interface is not one)."""
        if now - self.typed > 600:
            self.types = {i: info.get("type") for i, info in netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).items()}
            self.typed = now
        out = {}
        try:
            names = sorted(p.name for p in SYS_NET.iterdir())
        except OSError:
            return out
        for name in names:
            d = SYS_NET / name
            if name in (watched, "lo") or not (d / "device").exists():
                continue
            if (d / "phy80211").exists() and self.types.get(name) != "managed":
                continue
            out[name] = "u" if link_up(name) else "d"
        return out


def history_state(state, link):
    """The watched link's letter for linkhistory, from the status's state."""
    return {"off": "o", "up": "u", "checking": "g"}.get(state, "g" if link else "d")


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
    """Sleep up to secs, waking early for a link drop, a settings change or a step asked for on
    /admin (looked at every 5 s, so a choice made there shows within seconds, whatever the check
    interval)."""
    end = time.time() + secs
    while (left := end - time.time()) > 0:
        if wake.wait(min(left, 5)) or _settings_mtime() != mtime or NOW.exists():
            break
    wake.clear()


def request_step(step):
    """For hub_control's uplink-do: ask the running watchdog to take one step now."""
    if step not in STEPS:
        raise ValueError(f"step must be one of {', '.join(STEPS)}")
    ETC.mkdir(parents=True, exist_ok=True)
    tmp = NOW.with_name(NOW.name + ".tmp")
    tmp.write_text(json.dumps({"step": step, "at": time.time()}))
    os.chmod(tmp, 0o600)
    os.replace(tmp, NOW)
    return f"Asked the watchdog to {STEP_LABEL[step]} now; what it did shows in its log within seconds."


def take_request(now):
    """The step asked for on /admin, once (the file is removed), or None. One older than ten
    minutes is dropped: the watchdog was not running to take it, and it is not wanted late."""
    try:
        req = json.loads(NOW.read_text())
    except (OSError, ValueError):
        return None
    finally:
        NOW.unlink(missing_ok=True)
    if not isinstance(req, dict) or req.get("step") not in STEPS or not isinstance(req.get("at"), (int, float)):
        return None
    return req["step"] if now - req["at"] < 600 else None


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
    known_backend = None  # the last real backend seen, so its going away can be told
    pinned = None
    history = History()
    tls_at = 0  # HTTPS (step 15): the certificate renewed when due, looked at every six hours

    def stop(*_):
        history.flush()  # the slot under way, rather than lose up to five minutes of it
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
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
            history.note(now, None, None, history.others(None, now))
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
        if w.outage or w.misses:
            # In trouble: look closer, each check, for a wedged driver (and the backend afresh).
            was, backend = backend, backend_of(iface)
            if backend != was:
                can = obs["can"] = repairs_for(backend, iface)
                actor.backend = backend
                w.log(now, "info", f"{iface} is now run by {backend} (was {was}).")
            known = was if was != "none" else known_backend
            obs["wedged"] = wedge_evidence(iface, backend, known, recent_journal(), crashwatch_events(), now,
                                           (SYS_NET / iface).exists(), (SYS_NET / iface / "phy80211").exists())
        elif backend and backend != "none":
            known_backend = backend
        actions = w.tick(now, obs)
        asked = take_request(now) if NOW.exists() else None
        if asked:
            more, _ = w.by_hand(now, obs, asked)
            actions += [s for s in more if s not in actions]
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
        if not dry and os.geteuid() == 0 and now - tls_at > 6 * 3600:
            tls_at = now
            try:
                from irate_box.root import tls
                if tls.status().get("set_up"):
                    tls.renew()
            except Exception as exc:  # noqa: BLE001 - the watchdog goes on whatever happens here
                w.log(now, "info", f"HTTPS certificate renewal failed: {exc}")
        report = _status(w, time.time(), chosen, iface, backend, can, up, gw, answers, obs, pinned, dry)
        history.note(report["at"], iface, history_state(report["state"], up), history.others(iface, report["at"]))
        write_status(report)
        # While a check or an outage is under way, look again sooner than the steady pace.
        wait = w.eff["check"] if not (w.misses or w.outage) else min(w.eff["check"], 15)
        nap(wake, wait, mtime)


def _status(w, now, chosen, iface, backend, can, up, gw, answers, obs, pinned, dry):
    link = netinv.parse_link(netinv.run("iw", "dev", iface, "link")[1]) if up and (SYS_NET / iface / "phy80211").exists() else {}
    stall = w.stall(now)
    state = ("off" if w.owner_off else "stalled" if stall else "down" if w.outage else "checking" if w.misses
             else "up" if up and answers else "down")
    return {"at": now, "state": state, "iface": iface, "backend": backend, "repairs": sorted(can),
            "link": link, "gateway": gw, "gateway_answers": answers, "since": w.outage["since"] if w.outage else w.last_ok,
            "misses": w.misses, "drops_in_window": len(w.drops), "guests": obs["guests"],
            "outage": ({k: w.outage[k] for k in ("since", "declared", "done", "held", "skipped")} if w.outage else None),
            "wedged": (w.outage or {}).get("wedged"),
            "episode": ({k: w.episode[k] for k in ("since", "outages", "failed", "tried")} if w.episode else None),
            "next": w.next_step(now), "stall": stall, "paused_until": max(w.pause_until, chosen.get("hold_until") or 0) or None,
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
