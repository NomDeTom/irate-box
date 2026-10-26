#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Keep the box on its home network: watch the link, and repair it as eagerly as the owner chose.

irate-box-uplink.service runs this as root (`uplink.py run`). Every so often it checks each
link it watches: is the interface up, and does the gateway answer (a ping, or failing that its
ARP entry: some routers drop pings but none ignore ARP). The internet is never checked: the box
is meant to work offline, and a LAN with no internet is not a fault.

Links  Each link (a WiFi client, a cable, a USB adapter; not the hotspot's radio) is watched or
       not, as the owner sets it (watch); one not set follows watch_new: watched while it holds
       the box's route (the default, and while it is in trouble after), always, or never. Each
       watched link has a watchdog of its own (Watch, in a Link), with the settings below and its
       own per_iface ones. The steps that reach every link (restart, reboot) wait while another
       link works, unless box_steps says otherwise; a cable with no carrier is waited for
       (cable_out), as nothing the box does mends a missing cable. The report keeps the route's
       link at the top, as it was before there were several, and each link under "links".

Its settings, on /admin's Network page (and `install.sh --uplink`), each meaning one thing:

  Pace        how soon it first acts once the link is down, how often it reconnects again, and
              when each heavier step comes: gentle, steady, brisk, urgent
  Reach       the heaviest step it may take: watch (log only), reconnect, restart, radio, reboot
              (the default: the highest; a reboot at most 3 a day, never during a build or update)
  Guests      protect (the default): no radio reset or reboot, nor a restart when the hotspot
              shares the radio, while guests are on the hotspot; or ignore
  Sensitivity the number of missed checks (a failed check, or the link dropping between two) within
              the pace's window that put the link on the ladder: together (an outage) or spread
              out (a flapping link); 1 to 20, 3 by default (fewer is more sensitive)

Any single value can be overridden (the page's "Custom" fields): see FIELDS. The ladder,
mildest first: reconnect (rescan and bring the profile up again), restart (NetworkManager,
or wpa_supplicant / ifupdown / networkd), radio (unbind and rebind the USB radio, or reload
its driver), reboot. Each step waits its time after the link went on the ladder; a step that
did not help is not repeated, but reconnect is, with back-off (locked to the strongest access
point after the first, or at once when the link is flapping).
Outages close together are one episode: one that starts within `relapse` (15 min) of the
last one's end continues it, the steps' times count from its start, and a step that "worked"
but was followed by a relapse did not hold, so it is passed over while a heavier one is left
(a flapping link's repair climbs the same way). It ends after `relapse` steady.

Each outage has a cause, as far as the box can tell (outage_cause): the radio has failed (its
driver wedged: wedge_evidence, the backend gone though the interface is there, no supplicant for
it, scans failing as busy, crash watch's radio failure; or the interface gone with its driver), or
the network has (not in range, not letting the box join, the gateway silent). A failed radio:
with `on_wedge: radio` it goes straight to a radio reset if the level reaches it, as reconnecting
and restarting can't mend it; with `reboot`, a reboot too if the radio is not back 3 minutes after.
A network's fault is never met with a radio reset or a reboot: reconnects only, at most every
`retry` (eager, steady, sparing: for a box on a battery) and for as long as `give_up` (never, the
default; an hour, a day, a week). This is the only part of irate-box that repairs the link or
reboots the box for it; crash watch only records.

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
    uplink.py set KEY=VALUE...      pace, reach, sensitivity, guests, on_wedge, retry, give_up
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

from irate_box.hub import jsonlog, linkhistory, netinv, watchlog
from irate_box.hub.refused import Refused
from irate_box.msg import M

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
# The reboot history the daily cap counts, kept where only root writes: not in the status
# file under $STATE, whose folder the hub could swap for one with an empty history.
REBOOTS = ETC / "uplink-reboots.json"
SETTINGS = ETC / "uplink.json"
RECORD = ETC / "uplink-changes.json"
# A step asked for on /admin (hub_control's uplink-do), taken by the running watchdog within seconds,
# so its guards apply and its log says what happened. Root's folder: the hub cannot plant one.
NOW = ETC / "uplink-now.json"
STATUS = STATE / "control" / "uplink.json"
# On the box the report lives in RAM (the unit's RuntimeDirectory): it is rewritten at every check and
# rebuilt within seconds of a start, so it need not wear the card. Where that folder isn't (a test, a
# checkout), it stays in control/.
RUN_STATUS = Path(os.environ.get("HUB_UPLINK_RUN", "/run/irate-box-uplink")) / "uplink.json"


def status_path():
    return RUN_STATUS if RUN_STATUS.parent.is_dir() else STATUS


HISTORY = STATE / "control" / "uplink-history.json"  # each link's five-minute history (linkhistory.py)
LADDER = STATE / "control" / "uplink-ladder.json"  # what the watchdog did, for the box doctor's chart
# The events that make the escalation chart: whether more patience or more aggression is needed.
LADDER_KINDS = (
    "down",
    "up",
    "repair",
    "held",
    "skip",
    "stalled",
    "wedged",
    "flap",
    "cause",
)  # cause: its s is the cause
LADDER_KEEP = 72 * 86400
SYS_NET = Path("/sys/class/net")
# Running any of these, the box is busy with something a reboot would spoil.
BUSY_UNITS = {
    "irate-box-ci.service": "a build",
    "irate-box-librarian.service": "a library update",
    "irate-box-control.service": "an /admin job (perhaps an update)",
}
STEPS = ("reconnect", "restart", "radio", "reboot")
STEP_LABEL = {
    "reconnect": "reconnect",
    "restart": "restart the network service",
    "radio": "reset the radio",
    "reboot": "reboot",
    "pin": "lock to the strongest access point",
}

# Two dials, each meaning one thing:
# PACE: how soon after an outage is declared it first acts, how often it reconnects again, and when
# each heavier step comes (seconds after it went on the ladder, counted across an episode); and REACH:
# the heaviest step it may take at all. Gentle and far-reaching at once is a real choice now.
# A pace's window is the time it counts missed checks over: the sensitivity's number of misses within it puts the link on the ladder.
PACE = {
    "gentle": {
        "check": 120,
        "window": 1800,
        "repeat": 900,
        "steps": {"reconnect": 300, "restart": 1800, "radio": 3600, "reboot": 7200},
    },
    "steady": {
        "check": 60,
        "window": 600,
        "repeat": 300,
        "steps": {"reconnect": 60, "restart": 600, "radio": 1200, "reboot": 3600},
    },
    "brisk": {
        "check": 30,
        "window": 300,
        "repeat": 180,
        "steps": {"reconnect": 0, "restart": 300, "radio": 900, "reboot": 1800},
    },
    "urgent": {
        "check": 30,
        "window": 120,
        "repeat": 120,
        "steps": {"reconnect": 0, "restart": 180, "radio": 480, "reboot": 1800},
    },
}
REACH = ("watch", "reconnect", "restart", "radio", "reboot")  # watch: look and log, never act
GUESTS = ("protect", "ignore")
# Sensitivity: how many missed checks (a failed check, or the link dropping between two) within
# the pace's window put the link on the repair ladder, whether they came together (an outage) or
# spread out (a flapping link: "if the link trips the level of misses within the pace window, then
# it is on the repair ladder"). Fewer is more sensitive.
SENSITIVITY = (1, 20)
PER_IFACE = (
    "pace",
    "reach",
    "sensitivity",
    "guests",
    "on_wedge",
    "retry",
    "give_up",
)  # what a connection may have of its own
# The old forgiveness, as a sensitivity: settings saved with it still load.
FORGIVENESS_WAS = {"tolerant": 5, "normal": 3, "strict": 2}
COMMON = {
    "backoff": 2.0,
    "max_repeat": 3600,
    "reboots_per_day": 3,
    "reboot_gap": 3600,
    "pause_after_change": 120,
    "relapse": 900,
    "blip": 20,
}
# Roaming between access points that share a name: from the least change to the owner's
# system to the most. roam: as the WiFi stack does (the default); no-scan: wpa_supplicant's background
# scans off while the hotspot shares the radio (roaming.py, by consent); lock: the WiFi profile held to one
# access point (its BSSID), by consent and undone the same way.
# Apart from that, whether a roam counts as a missed check: ignore_roams, off by default (each is a
# miss); on, a drop of the link that is back within `blip` seconds (20 s) on the same network is a roam, not a miss.
ROAMING = ("roam", "no-scan", "lock")
ROAMING_WAS = {"count": ("roam", False), "ignore": ("roam", True)}  # the branch's first four, read as these
BSSID_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")
# The default reach is the highest; guests stay protected, and a reboot keeps its guards (the daily cap, the gap, not while
# a build or an update runs, not soon after boot).
DEFAULT = {
    "pace": "gentle",
    "reach": "reboot",
    "guests": "protect",
    "sensitivity": 3,
    "iface": "auto",
    "overrides": {},
    "hold_until": 0,
    "on_wedge": "ladder",
    "roaming": "roam",
    "lock_bssid": None,
    "ignore_roams": False,
    "steps_off": {},
    "retry": "steady",
    "give_up": "never",
    "watch": {},
    "watch_hw": {},
    "watch_new": "uplink",
    "box_steps": "nothing-works",
    "cable_out": "wait",
    "cable_reach": "reconnect",
}
# Which links are watched, each with a watchdog of its own. A link the owner set is watched or not (watch:
# {iface: bool}); any other follows watch_new: "uplink", the link that holds the box's route (and one in
# trouble while it was, as the route goes with its link); "look", that and every other link watched but looked at
# only (logged and said, nothing done); "all"; or "none". A link is known by its name, then by its hardware (watch_hw:
# {iface: {mac, path}}, recorded as the settings are saved), so a USB adapter that comes back as another name keeps its own.
WATCH_NEW = ("uplink", "look", "all", "none")
HW_PATH_RE = re.compile(r"^[a-z0-9_]{1,16}:[A-Za-z0-9:._-]{1,64}$")
# Steps that reach every link, not only the failing one: restarting the network service drops them all, and a
# reboot drops everything. Taken only when no other link works ("nothing-works"), when the failing link holds
# the box's route ("uplink"), or for any watched link as for one ("any").
BOX_STEPS = ("nothing-works", "uplink", "any")
WHOLE_BOX = ("restart", "reboot")
# A cable link with no carrier: no cable, or the other end off. Nothing the box does mends that, so by default
# it waits and says so ("wait"); or it reconnects now and then ("reconnect"); or takes its whole ladder ("ladder").
CABLE_OUT = ("wait", "reconnect", "ladder")
# How far the watchdog goes on a cable link of its own accord: a lost wired link is most often a cable pulled or the other
# end off, which no step mends, so by default it reconnects (when the cable is back) and goes no further. The lesser of this
# and the reach is used; a reach set for that link alone (per_iface, kept where it differs from the box's) is used instead.
# The steps the owner has switched off for a connection ({iface: [step, ...]}): applied while the
# watchdog watches that one (a radio that a network restart unsettles, a cable that has no radio).
# When the radio itself has failed (outage_cause "radio": its driver wedged, its interface gone): keep to
# the ladder as set; go straight to a radio reset if the reach allows it; or that, and a reboot when the
# radio is still not back RADIO_WAIT after its reset (if the reach allows that too).
ON_WEDGE = ("ladder", "radio", "reboot")
RADIO_WAIT = 180
# Why the link is down, as far as the box can tell (outage_cause): the radio has failed, or the
# network has: gone from the scan (absent), seen but not letting the box join (join), joined with the
# gateway silent (gateway). A network's fault is not mended by resetting the radio or rebooting, so
# those causes only reconnect (and, for join, restart the network service), as often as `retry` says
# and for as long as `give_up` says.
NET_CAUSES = ("absent", "join", "gateway")
CAUSE_WORDS = {
    "radio": "the radio has failed",
    "cable": "no cable is plugged in, or the other end is off",
    "plugged": "the cable is back, but the link has not come up with it",
    "absent": "the network is not in range (the radio scans, and does not see it)",
    "join": "the network is in range, but joining it fails",
    "gateway": "joined, but the gateway does not answer",
}
# How often a reconnect is tried again, at most (the gap grows by `backoff` up to this): fruitless tries
# cost power on a box that runs on a battery.
RETRY = {"eager": 600, "steady": 3600, "sparing": 21600}
HOTSPOT_RETRY = 300  # seconds between starts of a hotspot found down with the link up (one that won't start isn't retried every check)
_hotspot_tried = [0.0]  # when the last such start was tried (one for the box: several links share it)
FOLLOW_STEADY = 20  # seconds the link holds a new channel before a hotspot that follows it moves there
FOLLOW_HOLD = 300  # seconds between two moves of a following hotspot: each drops its guests for 15-30 s
# How long to keep reconnecting while the network is at fault; then stop, and say so.
GIVE_UP = {"never": None, "hour": 3600, "day": 86400, "week": 7 * 86400}
# A pace's old name: settings saved with it still load.
PACE_WAS = {"prompt": "brisk"}
# The old single dial, as pace, reach and guests: settings saved with it still load.
EAGERNESS_WAS = {
    "off": ("gentle", "watch", "protect"),
    "patient": ("gentle", "reboot", "protect"),
    "standard": ("steady", "reboot", "protect"),
    "persistent": ("brisk", "reboot", "protect"),
    "stubborn": ("urgent", "reboot", "ignore"),
}
DESCRIBE = {
    "gentle": "Waits 5 minutes before the first reconnect and 15 between them; heavier steps after half an hour, an hour, two.",
    "steady": "A reconnect after a minute, again every 5; heavier steps after 10, 20 and 60 minutes down.",
    "brisk": "A reconnect at once, again every 3 minutes; heavier steps after 5, 15 and 30 minutes down.",
    "urgent": "Everything soonest: heavier steps after 3, 8 and 30 minutes down.",
    "watch": "Looks and logs. Nothing else: NetworkManager (or whatever runs the link) is left to itself.",
    "reconnect": "Reconnects.",
    "restart": "Reconnects; then restarts the network service.",
    "radio": "Reconnects; restarts the network service; then resets the radio. A USB radio: port reset, then rebind, then "
    "re-authorise. An SDIO radio: rebind the function, then rebind the SD host. Either, last: reload the driver.",
    "reboot": "All of the above; then reboots the box: at most 3 times a day, never during a build or an update, nor soon after it started.",
}
# Every value a Custom field may set: (low, high), or the allowed words.
FIELDS = {
    "check": (10, 600),
    "window": (60, 86400),
    "repeat": (0, 86400),
    "backoff": (1.0, 4.0),
    "max_repeat": (60, 86400),
    "relapse": (60, 86400),
    "reboots_per_day": (0, 10),
    "reboot_gap": (600, 86400),
    "blip": (5, 120),
    "steps": {s: (0, 86400) for s in STEPS},
}
# Custom fields there were before sensitivity: dropped when settings load, not refused.
FIELDS_WAS = ("misses", "grace", "flap_count", "flap_window", "flap_action", "guests")
IFACE_RE = re.compile(r"^(auto|[A-Za-z0-9_][A-Za-z0-9._-]{0,14})$")  # no leading "-"
# Refusals said in several places. A BSSID: also roaming.py's, for a lock asked by hand.
BAD_BSSID = M(
    "roaming.bssid.invalid",
    "Invalid BSSID. Enter six pairs of hexadecimal digits separated by colons, for example aa:bb:cc:dd:ee:ff.",
)
NO_NM = M(
    "uplink.profile.not-nm",
    "NetworkManager does not run the box's link, so the box has no connection profile to change.",
)


def _bad_iface(name):
    return M("uplink.iface.invalid", "Invalid network interface name: {iface}.", iface=str(name))


def _sensitivity_range():
    return M(
        "uplink.sensitivity.range",
        "The sensitivity must be a whole number from {low} to {high} missed checks.",
        low=SENSITIVITY[0],
        high=SENSITIVITY[1],
    )


# A setting that is not one of its allowed values, in the words the Network page uses for it.
_NOT_ONE_OF = {
    "pace": lambda v: M(
        "uplink.pace.unknown", "The recovery timing (Pace) must be one of these values: {values}.", values=v
    ),
    "reach": lambda v: M(
        "uplink.reach.unknown", "The maximum recovery action (Reach) must be one of these values: {values}.", values=v
    ),
    "guests": lambda v: M(
        "uplink.guests.unknown",
        "The setting for guests on the hotspot must be one of these values: {values}.",
        values=v,
    ),
    "on_wedge": lambda v: M(
        "uplink.on-wedge.unknown", "The setting for a failed radio must be one of these values: {values}.", values=v
    ),
    "retry": lambda v: M(
        "uplink.retry.unknown",
        "The retry interval for an unavailable network must be one of these values: {values}.",
        values=v,
    ),
    "give_up": lambda v: M(
        "uplink.give-up.unknown",
        "The retry period for an unavailable network must be one of these values: {values}.",
        values=v,
    ),
    "roaming": lambda v: M(
        "uplink.roaming.unknown", "The roaming setting must be one of these values: {values}.", values=v
    ),
    "watch_new": lambda v: M(
        "uplink.watch-new.unknown",
        "The setting for new network interfaces must be one of these values: {values}.",
        values=v,
    ),
    "box_steps": lambda v: M(
        "uplink.box-steps.unknown",
        "The setting for system-wide recovery actions must be one of these values: {values}.",
        values=v,
    ),
    "cable_out": lambda v: M(
        "uplink.cable-out.unknown",
        "The setting for a disconnected cable must be one of these values: {values}.",
        values=v,
    ),
    "cable_reach": lambda v: M(
        "uplink.cable-reach.unknown", "The wired-link recovery limit must be one of these values: {values}.", values=v
    ),
}


def _not_one_of(key, allowed):
    return _NOT_ONE_OF[key](list(allowed))


# A Custom field out of its range, in the words of its label on the Network page.
_OUT_OF_RANGE = {
    "check": lambda lo, hi: M(
        "uplink.custom.check", "The check interval must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "window": lambda lo, hi: M(
        "uplink.custom.window", "The failure-count window must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "repeat": lambda lo, hi: M(
        "uplink.custom.repeat", "The reconnect interval must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "backoff": lambda lo, hi: M(
        "uplink.custom.backoff", "The retry backoff multiplier must be from {low} to {high}.", low=lo, high=hi
    ),
    "max_repeat": lambda lo, hi: M(
        "uplink.custom.max-repeat", "The maximum retry interval must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "relapse": lambda lo, hi: M(
        "uplink.custom.relapse", "The outage grouping window must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "reboots_per_day": lambda lo, hi: M(
        "uplink.custom.reboots-per-day",
        "The maximum number of reboots per day must be from {low} to {high}.",
        low=lo,
        high=hi,
    ),
    "reboot_gap": lambda lo, hi: M(
        "uplink.custom.reboot-gap", "The minimum reboot interval must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "blip": lambda lo, hi: M(
        "uplink.custom.blip", "The short-roam limit must be from {low} to {high} seconds.", low=lo, high=hi
    ),
    "steps.reconnect": lambda lo, hi: M(
        "uplink.custom.step-reconnect",
        "The time to reconnect must be from {low} to {high} seconds, or off.",
        low=lo,
        high=hi,
    ),
    "steps.restart": lambda lo, hi: M(
        "uplink.custom.step-restart",
        "The time to restart the network service must be from {low} to {high} seconds, or off.",
        low=lo,
        high=hi,
    ),
    "steps.radio": lambda lo, hi: M(
        "uplink.custom.step-radio",
        "The time to reset the radio must be from {low} to {high} seconds, or off.",
        low=lo,
        high=hi,
    ),
    "steps.reboot": lambda lo, hi: M(
        "uplink.custom.step-reboot", "The time to reboot must be from {low} to {high} seconds, or off.", low=lo, high=hi
    ),
}


def effective(chosen, iface=None):
    """The numbers the watchdog runs on: the pace's, its steps up to the reach, the sensitivity, then
    the overrides (a step overridden beyond the reach is beyond it still); last, the steps switched off
    for the connection watched (iface). The connection's own pace, reach, sensitivity, guests and wedge
    choice (per_iface), where it has them, come before all of that."""
    if iface and (chosen.get("per_iface") or {}).get(iface):
        chosen = {**chosen, **chosen["per_iface"][iface]}
    pace, reach = PACE[chosen["pace"]], chosen["reach"]
    allowed = STEPS[: REACH.index(reach)]
    eff = dict(
        COMMON,
        check=pace["check"],
        window=pace["window"],
        repeat=pace["repeat"],
        sensitivity=int(chosen.get("sensitivity", DEFAULT["sensitivity"])),
    )
    eff["steps"] = {s: v for s, v in pace["steps"].items() if s in allowed}
    eff["guests"] = chosen.get("guests", "protect")
    eff["on_wedge"] = chosen.get("on_wedge", "ladder")
    eff["retry"] = chosen.get("retry", DEFAULT["retry"])
    eff["max_repeat"] = RETRY[eff["retry"]]
    eff["give_up"] = GIVE_UP[chosen.get("give_up", DEFAULT["give_up"])]
    eff["roaming"] = chosen.get("roaming", "roam")
    eff["ignore_roams"] = bool(chosen.get("ignore_roams"))
    eff["box_steps"] = chosen.get("box_steps", DEFAULT["box_steps"])
    eff["cable_out"] = chosen.get("cable_out", DEFAULT["cable_out"])
    for k, v in chosen.get("overrides", {}).items():
        if k == "steps":
            for s, t in v.items():
                if t is None:
                    eff["steps"].pop(s, None)
                elif s in allowed:
                    eff["steps"][s] = t
        else:
            eff[k] = v
    for s in (chosen.get("steps_off") or {}).get(iface, ()) if iface else ():
        eff["steps"].pop(s, None)
    if "reconnect" not in eff["steps"]:
        eff["repeat"] = 0
    return eff


def levels():
    """What the Network page offers, from here, so the page and the watchdog cannot disagree."""
    return {
        "pace": list(PACE),
        "reach": list(REACH),
        "sensitivity": list(SENSITIVITY),
        "guests": list(GUESTS),
        "on_wedge": list(ON_WEDGE),
        "steps": list(STEPS),
        "retry": RETRY,
        "give_up": GIVE_UP,
        "roaming": list(ROAMING),
        "watch_new": list(WATCH_NEW),
        "box_steps": list(BOX_STEPS),
        "cable_out": list(CABLE_OUT),
        "describe": DESCRIBE,
        "default": DEFAULT,
        "presets": {"pace": PACE, "common": COMMON},
        "fields": {
            k: list(v) if isinstance(v, tuple) else {s: list(r) for s, r in v.items()} for k, v in FIELDS.items()
        },
    }


def migrate(raw):
    """Settings saved before the two dials: their eagerness as pace, reach and guests, and an
    overridden guests as the setting it is now."""
    raw = dict(raw)
    if "watch_new" not in raw:
        # One interface chosen to watch (the old "Watch" field): that one watched, no other; "auto": the route's link.
        iface = raw.get("iface", "auto")
        if isinstance(iface, str) and iface != "auto" and IFACE_RE.match(iface):
            raw["watch"] = {**(raw.get("watch") if isinstance(raw.get("watch"), dict) else {}), iface: True}
            raw["watch_new"] = "none"
            raw["iface"] = "auto"
        else:
            raw["watch_new"] = "uplink"
    if "eagerness" in raw and "pace" not in raw:
        old = raw.pop("eagerness")
        if old not in EAGERNESS_WAS:
            raise Refused(M("uplink.eagerness.unknown", "Unknown eagerness level: {value}.", value=str(old)))
        pace, reach, guests = EAGERNESS_WAS[old]
        raw.update(pace=pace, reach=reach)
        raw.setdefault("guests", guests)
    raw.pop("eagerness", None)
    if raw.get("pace") in PACE_WAS:
        raw["pace"] = PACE_WAS[raw["pace"]]
    if isinstance(raw.get("per_iface"), dict):
        raw["per_iface"] = {
            i: (dict(o, pace=PACE_WAS[o["pace"]]) if isinstance(o, dict) and o.get("pace") in PACE_WAS else o)
            for i, o in raw["per_iface"].items()
        }
    if "forgiveness" in raw and "sensitivity" not in raw:
        old = raw.pop("forgiveness")
        if old not in FORGIVENESS_WAS:
            raise Refused(M("uplink.forgiveness.unknown", "Unknown forgiveness level: {value}.", value=str(old)))
        raw["sensitivity"] = FORGIVENESS_WAS[old]
    raw.pop("forgiveness", None)
    over = raw.get("overrides")
    if isinstance(over, dict) and any(k in over for k in FIELDS_WAS):
        over = dict(over)
        if "guests" in over:
            raw["guests"] = over.pop("guests")
        if "misses" in over and "sensitivity" not in raw:
            raw["sensitivity"] = over["misses"]
        for k in FIELDS_WAS:
            over.pop(k, None)
        raw["overrides"] = over
    return raw


def validate(raw):
    """A settings dict as /admin sent it → a clean one, or ValueError."""
    if not isinstance(raw, dict):
        raise Refused(M("uplink.settings.not-object", "The network recovery settings must be a JSON object."))
    raw = migrate(raw)
    out = dict(DEFAULT)
    pace, reach = raw.get("pace", out["pace"]), raw.get("reach", out["reach"])
    if pace not in PACE:
        raise Refused(_not_one_of("pace", PACE))
    if reach not in REACH:
        raise Refused(_not_one_of("reach", REACH))
    try:
        sens = int(raw.get("sensitivity", out["sensitivity"]))
    except (TypeError, ValueError):
        raise Refused(_sensitivity_range()) from None
    if not SENSITIVITY[0] <= sens <= SENSITIVITY[1]:
        raise Refused(_sensitivity_range())
    guests = raw.get("guests", "protect")
    if guests not in GUESTS:
        raise Refused(_not_one_of("guests", GUESTS))
    iface = str(raw.get("iface", "auto"))
    if not IFACE_RE.match(iface):
        raise Refused(_bad_iface(iface))
    w = raw.get("on_wedge", "ladder")
    if w not in ON_WEDGE:
        raise Refused(_not_one_of("on_wedge", ON_WEDGE))
    retry, give_up = raw.get("retry", DEFAULT["retry"]), raw.get("give_up", DEFAULT["give_up"])
    if retry not in RETRY:
        raise Refused(_not_one_of("retry", RETRY))
    if give_up not in GIVE_UP:
        raise Refused(_not_one_of("give_up", GIVE_UP))
    roaming, ignore = raw.get("roaming", "roam"), raw.get("ignore_roams", False)
    if roaming in ROAMING_WAS:
        roaming, ignore = ROAMING_WAS[roaming][0], ignore or ROAMING_WAS[roaming][1]
    if not isinstance(ignore, bool):
        raise Refused(M("uplink.ignore-roams.not-bool", "The setting for short roams must be true or false."))
    if roaming not in ROAMING:
        raise Refused(_not_one_of("roaming", ROAMING))
    lock = raw.get("lock_bssid")
    lock = lock.lower() if isinstance(lock, str) else None
    if roaming == "lock" and not (lock and BSSID_RE.match(lock)):
        raise Refused(
            M(
                "uplink.lock.no-bssid",
                "Invalid or missing BSSID. To lock the link to one access point, select that access point.",
            )
        )
    watch = raw.get("watch") or {}
    if not isinstance(watch, dict) or len(watch) > 16:
        raise Refused(
            M(
                "uplink.watch.not-object",
                "The watched network interfaces must be a JSON object of at most 16 interfaces.",
            )
        )
    for name, on in watch.items():
        if not IFACE_RE.match(str(name)) or name == "auto" or not isinstance(on, bool):
            raise Refused(
                M(
                    "uplink.watch.entry",
                    "The watch entry {name} must name a network interface, with the value true or false.",
                    name=str(name),
                )
            )
    hw = raw.get("watch_hw") or {}
    if not isinstance(hw, dict) or len(hw) > 16:
        raise Refused(
            M(
                "uplink.watch-hw.not-object",
                "The hardware of the watched network interfaces must be a JSON object of at most 16 interfaces.",
            )
        )
    clean_hw = {}
    for name, h in hw.items():
        if not IFACE_RE.match(str(name)) or name == "auto" or not isinstance(h, dict):
            raise Refused(
                M(
                    "uplink.watch-hw.entry",
                    "The hardware entry {name} must name a network interface, with its hardware.",
                    name=str(name),
                )
            )
        mac, path = h.get("mac"), h.get("path")
        if mac is not None and not (isinstance(mac, str) and BSSID_RE.match(mac)):
            raise Refused(
                M(
                    "uplink.watch-hw.mac",
                    "The hardware address of {name} must have the form aa:bb:cc:dd:ee:ff.",
                    name=name,
                )
            )
        if path is not None and not (isinstance(path, str) and HW_PATH_RE.match(path)):
            raise Refused(
                M("uplink.watch-hw.path", "The hardware path of {name} must name a bus and a device.", name=name)
            )
        clean_hw[name] = {"mac": mac, "path": path}
    choices = {k: raw.get(k, DEFAULT[k]) for k in ("watch_new", "box_steps", "cable_out", "cable_reach")}
    for k, allowed in (
        ("watch_new", WATCH_NEW),
        ("box_steps", BOX_STEPS),
        ("cable_out", CABLE_OUT),
        ("cable_reach", REACH),
    ):
        if choices[k] not in allowed:
            raise Refused(_not_one_of(k, allowed))
    out.update(
        pace=pace,
        reach=reach,
        guests=guests,
        sensitivity=sens,
        iface=iface,
        overrides={},
        on_wedge=w,
        retry=retry,
        give_up=give_up,
        roaming=roaming,
        lock_bssid=lock if roaming == "lock" else None,
        ignore_roams=ignore,
        watch=dict(watch),
        watch_hw=clean_hw,
        **choices,
    )
    over = raw.get("overrides") or {}
    if not isinstance(over, dict):
        raise Refused(M("uplink.custom.not-object", "The custom values must be a JSON object."))
    for k, v in over.items():
        rule = FIELDS.get(k)
        if rule is None:
            raise Refused(M("uplink.custom.unknown", "Unknown custom value: {name}.", name=str(k)))
        if k == "steps":
            if not isinstance(v, dict):
                raise Refused(
                    M(
                        "uplink.custom.steps-not-object",
                        "The custom times of the recovery actions must be a JSON object.",
                    )
                )
            steps = {}
            for s, t in v.items():
                if s not in STEPS:
                    raise Refused(
                        M(
                            "uplink.step.unknown-name",
                            "Unknown recovery action: {name}. The recovery actions are: {values}.",
                            name=str(s),
                            values=list(STEPS),
                        )
                    )
                if t is not None:
                    t = int(t)
                    lo, hi = rule[s]
                    if not lo <= t <= hi:
                        raise Refused(_OUT_OF_RANGE["steps." + s](lo, hi))
                steps[s] = t
            out["overrides"]["steps"] = steps
        elif isinstance(rule[0], str):
            if v not in rule:
                raise Refused(
                    M(
                        "uplink.custom.choice",
                        "The custom value {name} must be one of these values: {values}.",
                        name=k,
                        values=list(rule),
                    )
                )
            out["overrides"][k] = v
        else:
            v = float(v) if isinstance(rule[0], float) else int(v)
            if not rule[0] <= v <= rule[1]:
                raise Refused(
                    _OUT_OF_RANGE[k](rule[0], rule[1])
                    if k in _OUT_OF_RANGE
                    else M(
                        "uplink.custom.range",
                        "The custom value {name} must be from {low} to {high}.",
                        name=k,
                        low=rule[0],
                        high=rule[1],
                    )
                )
            out["overrides"][k] = v
    off = raw.get("steps_off") or {}
    if not isinstance(off, dict) or len(off) > 16:
        raise Refused(
            M(
                "uplink.steps-off.not-object",
                "The recovery actions for each connection must be a JSON object of at most 16 interfaces.",
            )
        )
    out["steps_off"] = {}
    for name, steps in off.items():
        if not IFACE_RE.match(str(name)) or name == "auto":
            raise Refused(_bad_iface(name))
        if not isinstance(steps, list) or any(x not in STEPS for x in steps):
            raise Refused(
                M(
                    "uplink.steps-off.list",
                    "The recovery actions disabled for {iface} must be a list of some of: {values}.",
                    iface=name,
                    values=list(STEPS),
                )
            )
        if steps:
            out["steps_off"][name] = [x for x in STEPS if x in steps]
    per = raw.get("per_iface") or {}
    if not isinstance(per, dict) or len(per) > 16:
        raise Refused(
            M(
                "uplink.per-iface.not-object",
                "The settings for each connection must be a JSON object of at most 16 interfaces.",
            )
        )
    out["per_iface"] = {}
    for name, own in per.items():
        if not IFACE_RE.match(str(name)) or name == "auto":
            raise Refused(_bad_iface(name))
        if not isinstance(own, dict) or any(k not in PER_IFACE for k in own):
            raise Refused(
                M(
                    "uplink.per-iface.keys",
                    "The settings for {iface} must be some of: {values}.",
                    iface=name,
                    values=list(PER_IFACE),
                )
            )
        clean = {}
        for k, v in own.items():
            allowed = {
                "pace": PACE,
                "reach": REACH,
                "guests": GUESTS,
                "on_wedge": ON_WEDGE,
                "retry": RETRY,
                "give_up": GIVE_UP,
            }
            if k == "sensitivity":
                if type(v) is not int or not SENSITIVITY[0] <= v <= SENSITIVITY[1]:
                    raise Refused(
                        M(
                            "uplink.per-iface.sensitivity",
                            "The sensitivity for {iface} must be a whole number from {low} to {high} missed checks.",
                            iface=name,
                            low=SENSITIVITY[0],
                            high=SENSITIVITY[1],
                        )
                    )
            elif v not in allowed[k]:
                raise Refused(
                    M(
                        "uplink.per-iface.value",
                        "The setting {name} for {iface} must be one of these values: {values}.",
                        name=k,
                        iface=name,
                        values=list(allowed[k]),
                    )
                )
            if v != out[k]:
                clean[k] = v  # the same as for every connection is not kept as its own
        if clean:
            out["per_iface"][name] = clean
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

    def __init__(self, eff, events=None, reboots=None, labels=None, causes=None):
        self.eff = eff
        # The words for its steps and causes: a cable link's adapter is reset, not a radio.
        self.labels = {**STEP_LABEL, **(labels or {})}
        self.causes = {**CAUSE_WORDS, **(causes or {})}
        self.events = deque(events or [], maxlen=100)
        self.reboots = list(reboots or [])
        self.misses = 0
        self.first_fail = None
        self.outage = None  # {since, declared, done: [...], held: [...], next_reconnect, gap}
        self.miss_at = deque()  # the missed checks and link drops within the pace's window
        self.last_ok = None
        self.pause_until = 0.0
        self.owner_off = False
        self.can = set()  # the repairs possible at the last look, for next_step() and stall()
        self.ladder = []  # the ladder's events since serve() last took them (Ladder.add)
        self.shared = []  # every event since serve() last wrote them to the watchers' record (watchlog)
        # Outages close together are one episode: {since, outages,
        # failed: steps that "worked" and were followed by a relapse, tried, last_end, last_done}.
        self.episode = None

    def log(self, now, kind, text, step=None, by=None):
        """An event for the page's log; those on the ladder (an outage, a step taken, held,
        skipped or a stall) carry their step and who took it (auto, hand, flap), and are kept
        for the box doctor's escalation chart too (Ladder)."""
        e = {"at": now, "kind": kind, "text": text}
        if step:
            e["step"] = step
        if by:
            e["by"] = by
        self.events.append(e)
        self.shared.append(e)
        if kind in LADDER_KINDS:
            self.ladder.append(e)

    def tick(self, now, obs):
        """obs: link (bool), gateway (True/False/None: not known), drops ([times]), guests (int),
        busy (str or None), uptime (s), can (set of repairs), hold_until (epoch), owner_off (the
        owner took the link down on purpose: `nmcli dev disconnect`).

        Each failed check, and each drop of the link between two, is a miss, kept for the pace's
        window; `sensitivity` misses within it put the link on the ladder: down,
        an outage; up but dropping, "flapping", on the ladder until the misses fall below the line."""
        eff = self.eff
        self.can = set(obs.get("can", ()))
        if obs.get("owner_off") and not obs["link"]:
            if not self.owner_off:
                self.owner_off = True
                self.log(
                    now,
                    "info",
                    "Disconnected by hand (nmcli device disconnect): left alone until it is connected again.",
                )
            self.misses, self.first_fail, self.outage, self.episode = 0, None, None, None
            self.miss_at.clear()
            return []
        self.owner_off = False
        drops = list(obs.get("drops", ()))
        self.miss_at.extend(sorted(drops))
        healthy = obs["link"] and obs.get("gateway") is not False
        if not healthy:
            self.miss_at.append(now)
            if self.first_fail is None:
                self.first_fail = min([now, *drops])
        while self.miss_at and self.miss_at[0] < now - eff["window"]:
            self.miss_at.popleft()
        self.misses = len(self.miss_at)
        tripped = self.misses >= eff["sensitivity"]
        if healthy:
            if self.outage and self.outage.get("flapping") and tripped:
                return self._repair(now, obs)
            self._healthy(now, obs)
            # Counted again: an outage that has just ended took its misses with it.
            if drops and len(self.miss_at) >= eff["sensitivity"]:
                self._declare(now, obs, flapping=True)
                return self._repair(now, obs)
            return []
        if self.outage is None:
            if not tripped:
                return []
            self._declare(now, obs, flapping=False)
        else:
            self.outage["flapping"] = False  # down now: an outage proper
        return self._repair(now, obs)

    def _declare(self, now, obs, flapping):
        eff, n, w = self.eff, self.misses, human(self.eff["window"])
        since = min(self.miss_at) if self.miss_at else now
        self.outage = {
            "since": since,
            "declared": now,
            "done": [],
            "held": [],
            "skipped": [],
            "next_reconnect": None,
            "gap": eff["repeat"],
            "flapping": flapping,
        }
        self.log(
            now,
            "down",
            f"Dropped or missed {n} times within {w}: on the ladder while it flaps."
            if flapping
            else f"Link lost ({n} missed checks within {w})."
            if not obs["link"]
            else f"The gateway stopped answering ({n} missed checks within {w}).",
        )
        self._episode(now, since)

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
                self.log(
                    now,
                    "info",
                    f"Down again {human(start - (ep['end_was'] or start))} after it came back: "
                    f"{', '.join(self.labels[s] for s in gone)} did not hold, so it is not repeated while a heavier step is left "
                    f"(outage {ep['outages']} of this episode).",
                )
            return ep
        # The steps' times count from when it went on the ladder (not from the window's first miss).
        self.episode = {
            "since": now,
            "outages": 1,
            "failed": [],
            "tried": [],
            "last_end": None,
            "last_done": [],
            "end_was": None,
        }
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
        """Up, and off the ladder: an outage ends (its misses with it, so a link that has come
        back is not repaired for them); an episode ends after `relapse` steady."""
        eff, ep = self.eff, self.episode
        if self.outage:
            o = self.outage
            done = [s for s in o["done"] if s not in o.get("skipped", ())]
            tried = ", ".join(self.labels[s] for s in done) or "nothing"
            self.log(
                now,
                "up",
                (
                    f"Steady again: fewer than {eff['sensitivity']} misses within {human(eff['window'])} (tried: {tried})."
                    if o.get("flapping")
                    else f"Back after {human(now - o['since'])} (tried: {tried})."
                ),
            )
            if not o.get("flapping"):
                self.miss_at.clear()
                self.misses = 0
            if ep:
                ep["last_end"] = ep["end_was"] = now
                ep["last_done"] = done
                ep["tried"] += [s for s in done if s not in ep["tried"]]
        self.first_fail, self.outage, self.last_ok = None, None, now
        if ep and ep["last_end"] is not None and now - ep["last_end"] >= eff["relapse"]:
            if ep["outages"] > 1:
                tried = ", ".join(self.labels[s] for s in ep["tried"]) or "nothing"
                self.log(now, "info", f"Steady again after an episode of {ep['outages']} outages (tried: {tried}).")
            self.episode = None

    def _paused(self, now, obs):
        return now < max(self.pause_until, obs.get("hold_until") or 0)

    def _cause(self, now, obs):
        """The outage's cause, from this look (outage_cause): kept while a look can't tell, and said
        when it changes. Each cause takes its own steps (_allowed)."""
        o, cause = self.outage, obs.get("cause")
        if obs.get("wedged") and cause is None:
            cause = "radio"
        if cause == "plugged" and o.get("cause") == "cable":
            # Plugged back in, and the link didn't come up with it (a headless box would otherwise need a reboot to get
            # its cable back): the steps count afresh from now, and the first, a reconnect, is due at once.
            o.update(
                declared=now, done=[], held=[], skipped=[], next_reconnect=None, gap=self.eff["repeat"], replugged=now
            )
            if self.episode:
                self.episode.update(since=now, failed=[])
        if cause and cause != o.get("cause"):
            o["cause"] = cause
            says = (
                f"{'an adapter' if self.labels['radio'] != STEP_LABEL['radio'] else 'a radio'} reset, then a reboot if it is not back"
                if cause == "radio"
                else "reconnecting now, then the steps as the pace says"
                if cause == "plugged"
                else {
                    "wait": "plugging it in, or switching the other end on; the box waits",
                    "reconnect": "plugging it in; meanwhile the box reconnects now and then",
                    "ladder": "plugging it in; meanwhile the box takes its steps",
                }[self.eff.get("cable_out", "wait")]
                if cause == "cable"
                else "reconnecting"
                + (" (and once a restart of the network service)" if cause == "join" else "")
                + ", never a radio reset or a reboot"
            )
            self.log(now, "cause", f"Why it is down: {self.causes[cause]}. What can help: {says}.", cause)
        return o.get("cause")

    def _allowed(self, o):
        """The steps this outage's cause may take: all of them for the radio or an unknown cause;
        for a network's fault, reconnecting (and, when joining fails, restarting the network service)."""
        cause = o.get("cause")
        if cause == "cable":
            return {"wait": set(), "reconnect": {"reconnect"}}.get(self.eff.get("cable_out", "wait"), set(STEPS))
        if cause not in NET_CAUSES:
            return set(STEPS)
        return {"reconnect", "restart"} if cause == "join" else {"reconnect"}

    def _given_up(self, now, o):
        """The network at fault for longer than the owner keeps trying (give_up)."""
        g = self.eff.get("give_up")
        return bool(g) and o.get("cause") in NET_CAUSES and now - o["since"] >= g

    def _radio_first(self, o):
        """A failed radio, and the owner's choice to reset it at once rather than climb the ladder."""
        return o.get("cause") == "radio" and self.eff["on_wedge"] in ("radio", "reboot")

    def _repair(self, now, obs):
        eff, o = self.eff, self.outage
        wedged = obs.get("wedged")
        if wedged and not o.get("wedged"):
            o["wedged"] = wedged
            self.log(now, "wedged", f"The radio's driver looks wedged: {wedged}.")
        cause = self._cause(now, obs)
        own = obs.get("driver_reset")
        if own is not None:
            # The driver reset its device itself (its firmware stopped answering): left to it, as a
            # reset from here on top of it would only start it over.
            if not o.get("own_reset"):
                o["own_reset"] = True
                self.log(now, "info", f"The radio's driver reset it itself {human(own)} ago: left to it to come back.")
            return []
        if self._paused(now, obs):
            return []
        can = obs.get("can", set())
        if self._given_up(now, o):
            if not o.get("gave_up"):
                o["gave_up"] = True
                self.log(
                    now,
                    "stalled",
                    f"Stopped trying after {human(now - o['since'])}: {self.causes[cause]}. "
                    "It starts again when the network returns. The Network page setting Network unavailable: retry period sets how long it tries.",
                )
            return []
        # The owner's choice (on_wedge "radio" or "reboot"): reconnecting and restarting can't mend a
        # failed radio, and on some drivers they make it worse, so straight to the radio reset if the
        # level reaches it; with "reboot", a reboot when it is still not back RADIO_WAIT after that.
        # Beyond the level, or held, it waits or stalls, saying why.
        if self._radio_first(o) and "radio" not in o["done"]:
            for s in ("reconnect", "restart"):
                if s not in o["done"]:
                    o["done"].append(s)
                    o["skipped"].append(s)
            if "radio" in eff["steps"] and "radio" in can:
                why = self._held(now, obs, "radio")
                if not why:
                    o["done"].append("radio")
                    o["next_reconnect"], o["radio_at"] = None, now
                    self.log(
                        now,
                        "repair",
                        f"Reset the radio ({human(now - o['since'])} down): reconnecting or restarting can't mend a failed radio.",
                        "radio",
                        "auto",
                    )
                    return ["radio"]
                if "radio" not in o["held"]:
                    o["held"].append("radio")
                    self.log(now, "held", f"Would reset the radio, but {why}.", "radio")
        if (
            eff["on_wedge"] == "reboot"
            and cause == "radio"
            and obs.get("cause") == "radio"
            and o.get("radio_at")
            and now - o["radio_at"] >= RADIO_WAIT
            and "reboot" not in o["done"]
            and "reboot" in eff["steps"]
            and "reboot" in can
        ):
            why = self._held(now, obs, "reboot")
            if not why:
                o["done"].append("reboot")
                self.reboots.append(now)
                self.log(
                    now,
                    "repair",
                    f"Reboot ({human(now - o['since'])} down): the radio is not back {human(now - o['radio_at'])} after its reset.",
                    "reboot",
                    "auto",
                )
                return ["reboot"]
            if "reboot" not in o["held"]:
                o["held"].append("reboot")
                self.log(now, "held", f"Would reboot, but {why}.", "reboot")
        allowed = self._allowed(o)
        if (
            o.get("replugged")
            and "reconnect" not in o["done"]
            and "reconnect" in can
            and "reconnect" in eff["steps"]
            and not self._held(now, obs, "reconnect")
        ):
            o["done"].append("reconnect")
            o["next_reconnect"] = now + max(o["gap"], 30) if eff["repeat"] else None
            self.log(
                now, "repair", "Reconnect: the cable is back, and the link didn't come up with it.", "reconnect", "auto"
            )
            return ["reconnect"]
        if (
            o.get("replugged")
            and now - o["replugged"] >= RADIO_WAIT
            and "radio" not in o["done"]
            and "radio" in can
            and "radio" in eff["steps"]
            and not self._held(now, obs, "radio")
        ):
            # Still not up a few minutes after the reconnect: the adapter reset, before anything heavier, as a stuck port
            # or PHY is the likeliest reason a cable that is back gives no link.
            o["done"].append("radio")
            o["radio_at"] = now
            self.log(
                now,
                "repair",
                f"{self.labels['radio'].capitalize()}: the cable is back, and a reconnect didn't bring the link up.",
                "radio",
                "auto",
            )
            return ["radio"]
        # Each step's time counts from the episode's start, so relapses climb as one long outage
        # would; a step that did not hold earlier in it is passed over (_passed).
        t = now - (self.episode["since"] if self.episode else o["declared"])
        due = [
            s
            for s in STEPS
            if s in eff["steps"]
            and eff["steps"][s] <= t
            and s not in o["done"]
            and not self._passed(s)
            and s in allowed
        ]
        for step in reversed(due):  # the heaviest step that is due, once
            if step not in can:
                o["done"].append(step)
                o["skipped"].append(step)  # done with, but never tried: the page and the log say so
                self.log(now, "skip", f"Cannot {self.labels[step]} here; skipped.", step)
                continue
            why = self._held(now, obs, step)
            if why:
                if step not in o["held"]:
                    o["held"].append(step)
                    self.log(now, "held", f"Would {self.labels[step]}, but {why}.", step)
                continue
            o["done"].append(step)
            # Lighter steps that came due at the same time are passed over: this one covers them.
            for s in due:
                if STEPS.index(s) < STEPS.index(step) and s not in o["done"]:
                    o["done"].append(s)
            if step == "reboot":
                self.reboots.append(now)
            if step == "radio":
                o["radio_at"] = now
            o["next_reconnect"] = now + max(o["gap"], 30) if eff["repeat"] else None
            act = self._reconnect_as(o, can) if step == "reconnect" else step
            self.log(
                now,
                "repair",
                f"{self.labels[step].capitalize()}{', locked to the strongest access point' if act == 'pin' else ''}"
                f" ({human(now - o['since'])} {'flapping' if o.get('flapping') else 'down'}).",
                step,
                "auto",
            )
            return [act]
        if (
            "reconnect" in o["done"]
            and eff["repeat"]
            and "reconnect" in can
            and o["next_reconnect"]
            and now >= o["next_reconnect"]
            and not self._radio_first(o)
        ):
            o["gap"] = min(o["gap"] * eff["backoff"], eff["max_repeat"])
            o["next_reconnect"] = now + o["gap"]
            act = self._reconnect_as(o, can)
            self.log(
                now,
                "repair",
                f"Reconnect again{', locked to the strongest access point' if act == 'pin' else ''}"
                f" ({human(now - o['since'])} {'flapping' if o.get('flapping') else 'down'}; next in {human(o['gap'])}).",
                "reconnect",
                "auto",
            )
            return [act]
        st = self.stall(now)
        if st and not o.get("stalled"):
            o["stalled"] = True
            self.log(now, "stalled", st["text"], st["needs"])
        return []

    def _reconnect_as(self, o, can):
        """A reconnect locked to the strongest access point (NetworkManager only) when the link is
        flapping, or once a plain reconnect has not held in this episode or this outage: what the
        old flap detector's "pin" did, now a way of taking the ladder's first step."""
        tried = (self.episode and "reconnect" in self.episode["failed"]) or o.get("repeats", 0) > 0
        if "pin" in can and (o.get("flapping") or tried):
            return "pin"
        o["repeats"] = o.get("repeats", 0) + 1
        return "reconnect"

    def _held(self, now, obs, step, hand=False):
        eff = self.eff
        # A step that reaches every link waits while another link works, as the owner chose (box_steps); one
        # asked for by hand is the owner's own call.
        others = obs.get("others_up") or []
        rule = eff.get("box_steps", "nothing-works")
        if (
            step in WHOLE_BOX
            and others
            and not hand
            and (rule == "nothing-works" or rule == "uplink" and not obs.get("is_uplink"))
        ):
            return f"{', '.join(others)} {'is' if len(others) == 1 else 'are'} up, and " + (
                "the network service and the box are restarted only when nothing works"
                if rule == "nothing-works"
                else "this link does not hold the route of the box"
            )
        # A restart of the network service takes the hotspot down too when it shares the uplink's
        # radio: held then like a radio reset, only on a shared radio.
        heavy = step in ("radio", "reboot") or (step == "restart" and obs.get("shared_radio"))
        if heavy and eff["guests"] == "protect" and obs.get("guests", 0) > 0:
            n = obs["guests"]
            return f"{n} guest{'s are' if n != 1 else ' is'} on the hotspot" + (
                ", which shares the radio" if step == "restart" else ""
            )
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
        if self._given_up(now, o):
            return None
        start, floor = (self.episode["since"] if self.episode else o["declared"]), o["declared"]
        skip = ("reconnect", "restart") if self._radio_first(o) else ()
        allowed = self._allowed(o)
        rest = [
            (max(start + eff["steps"][s], floor), s)
            for s in STEPS
            if s in eff["steps"]
            and s not in o["done"]
            and s in self.can
            and not self._passed(s)
            and s not in skip
            and s in allowed
        ]
        if o["next_reconnect"] and eff["repeat"] and "reconnect" in self.can and not skip:
            rest.append((o["next_reconnect"], "reconnect"))
        if (
            eff["on_wedge"] == "reboot"
            and o.get("cause") == "radio"
            and o.get("radio_at")
            and "reboot" not in o["done"]
            and "reboot" in eff["steps"]
            and "reboot" in self.can
        ):
            rest.append((o["radio_at"] + RADIO_WAIT, "reboot"))
        if not rest:
            return None
        at, step = min(rest)
        return {"step": step, "label": self.labels[step], "at": max(at, now)}

    def stall(self, now):
        """On the ladder, with nothing left that the level allows and the box can
        do: {needs: the lightest possible step beyond the level, or None; text}. Not when the level
        is watch-only (that is the owner's choice, not a stall), nor while a step is only held."""
        o, eff = self.outage, self.eff
        if o and o.get("cause") == "cable" and eff.get("cable_out", "wait") == "wait":
            return {
                "needs": None,
                "label": None,
                "text": "Waiting: no cable is connected, or the other end has no power. The box cannot repair this. "
                "The box reconnects when the cable link returns.",
            }
        if o and self._given_up(now, o):
            return {
                "needs": None,
                "label": None,
                "text": f"Stopped trying after {human(now - o['since'])}: {self.causes[o['cause']]}. "
                "The box reconnects when the network returns.",
            }
        if not o or not eff["steps"] or self.next_step(now):
            return None
        allowed = self._allowed(o)
        needs = next(
            (s for s in STEPS if s in self.can and s not in eff["steps"] and s not in o["done"] and s in allowed), None
        )
        top = max(eff["steps"], key=STEPS.index)
        text = (
            f"Stalled: what could help now is to {self.labels[needs]}, and the reach set goes no further than to "
            f"{self.labels[top]}."
            if needs
            else "Stalled: nothing the box can do from here could help "
            f"(possible now: {', '.join(sorted(self.can)) or 'nothing'})."
        )
        if o.get("wedged"):
            text += f" The radio's driver looks wedged: {o['wedged']}."
        return {"needs": needs, "label": self.labels[needs] if needs else None, "text": text}

    def by_hand(self, now, obs, step):
        """A step the owner asked for on /admin: done now, whatever the level and the hold, but
        only when possible here and not held by a guard (guests, the reboot caps). The steps to
        take, and the reason when held."""
        if step not in STEPS:
            return [], f"{step} is not a step"
        if step not in obs.get("can", ()):
            self.log(now, "skip", f"Asked on /admin to {self.labels[step]}, but it cannot be done here.", step, "hand")
            return [], f"cannot {self.labels[step]} here"
        why = self._held(now, obs, step, hand=True)
        if why:
            self.log(now, "held", f"Asked on /admin to {self.labels[step]}, but {why}.", step, "hand")
            return [], why
        if step == "reboot":
            self.reboots.append(now)
        if self.outage and step not in self.outage["done"]:
            self.outage["done"].append(step)
        self.log(now, "repair", f"{self.labels[step].capitalize()}, asked on /admin.", step, "hand")
        return [step], None


# --- looking -------------------------------------------------------------------------------


def run(*cmd, timeout=60):
    if not shutil.which(cmd[0]):
        return 127, f"{cmd[0]} not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
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


def shared_radio(iface):
    """The box's hotspot runs on the same radio (phy) as the watched link."""
    devs = netinv.parse_iw_dev(netinv.run("iw", "dev")[1])
    phy = (devs.get(iface) or {}).get("phy")
    return bool(phy) and any(i != iface and d.get("type") == "AP" and d.get("phy") == phy for i, d in devs.items())


def words(chosen):
    """The settings in a few words, for the log and the page's messages."""
    n = chosen.get("sensitivity", DEFAULT["sensitivity"])
    parts = [f"{chosen['pace']} pace, reach {chosen['reach']}, sensitivity {n} missed check{'s' if n != 1 else ''}"]
    if chosen.get("guests") == "ignore":
        parts.append("guests or not")
    parts.append(
        {
            "radio": "a failed radio reset at once",
            "reboot": "a failed radio reset at once and the box rebooted if it is not back",
        }.get(chosen.get("on_wedge"), "")
    )
    if chosen.get("retry", "steady") != "steady":
        parts.append(f"reconnects at most every {human(RETRY[chosen['retry']])}")
    if chosen.get("give_up", "never") != "never":
        parts.append(
            f"stops trying after {'an' if chosen['give_up'] == 'hour' else 'a'} {chosen['give_up']} of the network at fault"
        )
    parts.append(
        {
            "no-scan": "no background scans while the hotspot shares the radio",
            "lock": f"locked to {chosen.get('lock_bssid')}",
        }.get(chosen.get("roaming"), "")
    )
    if chosen.get("ignore_roams"):
        parts.append("roams not counted")
    parts.append(
        {
            "uplink": "the network service and the box restarted for the route's link even with another up",
            "any": "the network service and the box restarted for any watched link",
        }.get(chosen.get("box_steps"), "")
    )
    if chosen.get("cable_reach", "reconnect") != "reconnect":
        cr = chosen["cable_reach"]
        parts.append(
            "on a cable, watch only"
            if cr == "watch"
            else f"on a cable as far as to {'reset the adapter' if cr == 'radio' else STEP_LABEL[cr]}"
        )
    parts.append(
        {
            "reconnect": "a cable unplugged reconnected now and then",
            "ladder": "a cable unplugged met with the whole ladder",
        }.get(chosen.get("cable_out"), "")
    )
    parts += [
        f"on {i} never {' or '.join(STEP_LABEL[x].lower() for x in steps)}"
        for i, steps in sorted((chosen.get("steps_off") or {}).items())
    ]
    return ", ".join(x for x in parts if x)


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


def watching_words(chosen):
    """Which links are watched, as the settings say it: "wlan0 and eth0", "the route's link and wlan0"."""
    on = sorted(i for i, v in (chosen.get("watch") or {}).items() if v)
    off = sorted(i for i, v in (chosen.get("watch") or {}).items() if v is False)
    new = {
        "uplink": "the link holding the box's route",
        "look": "the link holding the box's route, and every other looked at only",
        "all": "every link",
        "none": None,
    }[chosen.get("watch_new", "uplink")]
    parts = ([new] if new else []) + on
    said = " and ".join([", ".join(parts[:-1]), parts[-1]] if len(parts) > 1 else parts) or "no link"
    return said + (f" (never {', '.join(off)})" if off else "")


def main_link(chosen):
    """The link the box's settings speak of when one is meant (the profile's retries, a look from a shell): the
    route's link if it is watched, else the first one watched."""
    route = netinv.default_route()
    r = route["iface"] if route else None
    cands = candidate_links()
    want = watched_links(chosen, r, cands, alias=aliases(chosen, cands))
    full = sorted(i for i, how in want.items() if how == "full")
    return r if r in want else (full or sorted(want) or [None])[0]


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


def repairs_for(backend, iface, gone=None):
    """The steps possible on iface. gone: the facts of its radio as last seen (netinv.device_facts),
    for an interface that has gone with its driver: a radio reset and a reboot are still possible."""
    can = {
        "networkmanager": {"reconnect", "restart", "pin"},
        "wpa_supplicant": {"reconnect", "restart"},
        "ifupdown": {"reconnect", "restart"},
        "networkd": {"reconnect", "restart"},
        "dhcpcd": {"restart"},
    }.get(backend, set())
    here = (SYS_NET / iface).exists()
    facts = netinv.device_facts(iface) if here else (gone or {})
    wifi = (SYS_NET / iface / "phy80211").exists() if here else bool(gone)
    cable = here and not wifi and link_kind(iface) == "ethernet"
    if (wifi or cable) and (facts.get("usb") or facts.get("driver")):
        can.add("radio")
    if can:
        can.add("reboot")
    return can


SCAN_BUSY = re.compile(r"CTRL-EVENT-SCAN-FAILED ret=-16\b")
NO_SUPPLICANT = re.compile(
    r"Couldn't initialize supplicant interface|supplicant interface keeps failing|Failed to initialize driver interface"
)


def wedge_evidence(iface, backend, was_backend, journal, cw_events, now, exists=True, wifi=True, since=0.0):
    """Why the radio's driver looks wedged, in words, or None. From
    the last two minutes of NetworkManager's and wpa_supplicant's journal, crash watch's radio
    events, and the backend: any one of
      - the backend gone (it was there before) while the interface still exists
      - NetworkManager unable to initialise the supplicant for it
      - scans failing as busy (ret=-16) more than 5 times
      - crash watch's radio failure for it in the last 30 minutes, not since recovered
      - the interface gone, though it was a radio's (its driver did not bring it back)
    as an AIC8800 shows when its firmware wedges. journal is read from `since` (the last reset of
    the radio, which leaves the lines from before it behind), and so are crash watch's events."""
    if not wifi:
        return None
    why = []
    if not exists:
        why.append(f"{iface} has gone: its radio's driver did not bring it back")
    if exists and backend == "none" and was_backend not in (None, "none"):
        why.append(f"{was_backend} no longer runs {iface}, though it is still there")
    mine = [ln for ln in journal if f"{iface}:" in ln or f"({iface})" in ln or f"'{iface}'" in ln]
    if any(NO_SUPPLICANT.search(ln) for ln in mine):
        why.append(f"the supplicant cannot be started for {iface}")
    busy = sum(1 for ln in mine if SCAN_BUSY.search(ln))
    if busy > 5:
        why.append(f"scans keep failing as busy ({busy} in 2 minutes)")
    last = next((e for e in reversed(cw_events or []) if e.get("iface") == iface), None)
    if last and last.get("kind") == "failed" and now - last.get("at", 0) < 1800 and last.get("at", 0) >= since:
        why.append(f"crash watch saw the radio fail ({last.get('text', '')[:120]})")
    return "; ".join(why) or None


def recent_journal(secs=120, since=0.0):
    code, out = run(
        "journalctl",
        f"--since=@{int(max(time.time() - secs, since))}",
        "--no-pager",
        "-o",
        "cat",
        "-t",
        "NetworkManager",
        "-t",
        "wpa_supplicant",
        timeout=20,
    )
    return out.splitlines() if code == 0 else []


def outage_cause(iface, up, answers, wifi, exists, wedged, own, ssids, run=run, carrier=None):
    """Why the link is down (CAUSE_WORDS), or None when the box can't tell (a wired link, no
    network known, no scan list): the radio has failed (the driver resetting itself, wedge evidence,
    the interface gone); joined with the gateway silent; else NetworkManager's last scan, without
    asking for a new one (each costs power): a network the box knows (ssids) in it but not joined,
    or none of them in it."""
    if own is not None or wedged or (wifi and not exists):
        return "radio"
    if not wifi:
        # A cable link: no carrier is no cable (or the other end off); gone with its driver, a failed adapter.
        if not exists and carrier is not None:
            return "radio"
        if carrier is False:
            return "cable"
        if carrier and not up:
            return "plugged"
        return "gateway" if up and answers is False else None
    if up:
        return "gateway" if answers is False else None
    ssids = {ssids} if isinstance(ssids, str) else set(ssids or ())
    ssids.discard("")
    if not ssids:
        return None
    code, out = run("nmcli", "-t", "-f", "SSID", "dev", "wifi", "list", "ifname", iface, "--rescan", "no")
    if code != 0:
        return None
    seen = {line.replace("\\:", ":") for line in out.splitlines() if line}
    return "join" if ssids & seen else "absent"


def known_ssids(run=run):
    """The names of the WiFi networks NetworkManager has profiles for: what the box would join."""
    code, out = run("nmcli", "-t", "-f", "UUID,TYPE", "con", "show")
    if code != 0:
        return set()
    out_ssids = set()
    for line in out.splitlines():
        uuid, _, kind = line.partition(":")
        if kind == "802-11-wireless" and uuid:
            c, ssid = run("nmcli", "-t", "-g", "802-11-wireless.ssid", "con", "show", "uuid", uuid)
            if c == 0 and ssid.strip():
                out_ssids.add(ssid.strip())
    return out_ssids


def _file_reset(iface, port):
    """A radio reset filed as crash watch files a radio's failure (its snapshots and the kernel's last
    ten minutes), so it leaves something to read after; joined to crash watch's own if it filed one."""
    try:
        from irate_box.root import crashwatch

        code, out = run(
            "journalctl", "-k", "-b", "--no-pager", "-o", "short-monotonic", "--since", "-10min", timeout=30
        )
        crashwatch.file_radio(
            time.time(), iface, "radio reset by the uplink watchdog", out.splitlines() if code == 0 else [], port
        )
    except (OSError, ValueError, ImportError) as exc:
        print(f"uplink: the radio reset not filed: {exc}", file=sys.stderr)


def own_reset():
    """Seconds since the radio's driver reset its device itself, while that is recent enough to
    leave it to come back (root/radio.py's DRIVER_GRACE); None otherwise."""
    from irate_box.root import radio

    try:
        _marker, age = radio.driver_reset()
    except OSError:
        return None
    return age if age is not None and age < radio.DRIVER_GRACE else None


def crashwatch_events():
    from irate_box.root import crashwatch

    try:
        return json.loads(crashwatch.EVENTS.read_text())
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
    code, out = run("nmcli", "-t", "-g", "connection.uuid,802-11-wireless.ssid", "con", "show", name)
    vals = out.splitlines() if code == 0 else []
    if not vals or not vals[0].strip():
        return None
    return {"name": name, "uuid": vals[0].strip(), "ssid": vals[1].strip() if len(vals) > 1 else ""}


# --- acting ----------------------------------------------------------------------------------


class Actor:
    def __init__(self, iface, backend, dry=False, kind="wifi"):
        self.iface, self.backend, self.dry, self.kind = iface, backend, dry, kind
        self.profile = None  # {name, uuid, ssid}: NetworkManager's connection, remembered while up
        self.facts = None  # its radio's facts (netinv.device_facts), remembered while it is there

    def remember(self):
        if self.backend == "networkmanager":
            self.profile = nm_connection(self.iface) or self.profile

    def do(self, step):
        if self.dry:
            return f"(dry run: would {STEP_LABEL[step]})"
        return getattr(self, "_" + step)()

    def _reconnect(self):
        i = self.iface
        if self.kind == "ethernet":
            # A cable: the link negotiated afresh with the other end, where ethtool is there to ask.
            run("ethtool", "-r", i, timeout=20)
        if self.backend == "networkmanager":
            if self.kind == "wifi":
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
            # Its restart takes the box's hotspot down too: start it again.
            from irate_box.root import radio

            hs = radio.restore_hotspot()
            return out + (f"; {hs}" if hs else "")
        if self.backend == "wpa_supplicant":
            if netinv.active(f"wpa_supplicant@{i}"):
                return (
                    run("systemctl", "restart", f"wpa_supplicant@{i}", timeout=90)[1] or f"wpa_supplicant@{i} restarted"
                )
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

        facts = netinv.device_facts(self.iface) if (SYS_NET / self.iface).exists() else (self.facts or {})
        usb = facts.get("usb") or {}
        if self.kind == "wifi":
            _file_reset(self.iface, usb.get("port"))
        return radio.reset(self.iface, usb.get("port"), facts.get("driver"), usb.get("product") or usb.get("id"))

    def _reboot(self):
        run("sync")
        subprocess.Popen(["systemctl", "reboot"])  # noqa: TID251
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
        code, out = run(
            "nmcli", "-w", "45", "con", "up", "uuid", self.profile["uuid"], "ifname", i, "ap", bssid, timeout=60
        )
        return f"locked to {bssid}" + ("" if code == 0 else f": {out}")


# --- the loop ----------------------------------------------------------------------------------


class DropWatcher(threading.Thread):
    """`ip -o monitor link`: each time a watched link loses its carrier, between two checks too."""

    def __init__(self, wake):
        super().__init__(daemon=True)
        self.ifaces = set()
        self.wake = wake
        self.lock = threading.Lock()
        self.drops = {}  # iface: [[lost, back or None]]
        self.up = {}

    def take(self, iface, now=None, blip=0):
        """The link's drops since its last take, as (lost, back): back None while the link is still down. One
        still down and younger than `blip` waits for the next take, to be told whether it was a roam."""
        now = now or time.time()
        with self.lock:
            mine = self.drops.get(iface, [])
            ready = [d for d in mine if d[1] is not None or now - d[0] > blip]
            self.drops[iface] = [d for d in mine if d not in ready]
        return [tuple(d) for d in ready]

    def run(self):
        while True:
            try:
                proc = subprocess.Popen(["ip", "-o", "monitor", "link"], stdout=subprocess.PIPE, text=True)  # noqa: TID251
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
                if name in self.ifaces and was and not lower:
                    with self.lock:
                        self.drops.setdefault(name, []).append([time.time(), None])
                    self.wake.set()
                elif name in self.ifaces and was is False and lower:
                    with self.lock:
                        for d in reversed(self.drops.get(name, [])):
                            if d[1] is None:
                                d[1] = time.time()
                                break
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

    def note(self, now, watched, state, others=None, hotspots=None):
        """state: the watched link's (linkhistory's letters); others, hotspots: {iface: "u" | "d"}."""
        if not self._turn(now):
            return
        if watched and state:
            self.dirty |= linkhistory.record(self.hist, watched, state, now, "uplink")
        for iface, st in (others or {}).items():
            self.dirty |= linkhistory.record(self.hist, iface, st, now, "link")
        for iface, st in (hotspots or {}).items():
            self.dirty |= linkhistory.record(self.hist, iface, st, now, "hotspot")

    def flush(self):
        if not self.dirty:
            return
        from irate_box.root import safeio

        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(HISTORY, json.dumps(self.hist, separators=(",", ":")))
        self.dirty = False

    def hotspots(self, now):
        """The hotspot's interfaces (WiFi in AP mode), up or down."""
        self.others(None, now)  # the interfaces' types, looked at afresh when due
        return {
            name: ("u" if link_up(name) else "d")
            for name, kind in sorted(self.types.items())
            if kind == "AP" and (SYS_NET / name).exists()
        }

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


class Ladder:
    """The ladder's events kept for 72 days (at most 20 000): {at, k: kind, s: step, b: by, t: text},
    for the box doctor's escalation chart. Written when something happened, which is rare."""

    def __init__(self, path=None):
        self.path = path or LADDER
        self.rows = jsonlog.read(self.path)

    def misses(self, now, n, line, iface):
        """The symptoms beside the steps: the most misses within the pace's
        window seen in each five-minute slot, and the sensitivity then, as {at, k: "m", n, th}.
        Kept only for slots with a miss, or when the line moved; written as each slot closes."""
        slot = int(now // 300) * 300
        cur = getattr(self, "_cur", None)
        if cur and cur["at"] != slot:
            last_th = next((r.get("th") for r in reversed(self.rows) if r.get("k") == "m"), None)
            if cur["n"] or cur["th"] != last_th:
                self.add([], iface, now, [cur])
            cur = None
        if not cur:
            cur = self._cur = {"at": slot, "k": "m", "n": 0, "th": line, "i": iface}
        cur["n"], cur["th"] = max(cur["n"], int(n)), line

    def started(self, now, last, uptime):
        """The watchdog started: {at, k: "start", last, t}, where last is its last check before it stopped
        (None if not known). The chart ends an outage left open there, and draws the time between as
        no record rather than as the outage going on: a box that froze or rebooted mid-outage."""
        boot = uptime < 600
        row = {
            "at": round(now, 1),
            "k": "start",
            "t": "Watching again after the box started." if boot else "Watching again (the watchdog restarted).",
        }
        if isinstance(last, (int, float)) and 0 < last < now:
            row["last"] = round(last, 1)
        self.add([], None, now, [row])

    def add(self, events, iface, now, raw=()):
        if not events and not raw:
            return
        new = list(raw)
        for e in events:
            row = {"at": round(e["at"], 1), "k": e["kind"], "i": iface}
            if e.get("step"):
                row["s"] = e["step"]
            if e.get("by"):
                row["b"] = e["by"]
            row["t"] = e["text"][:200]
            new.append(row)
        self.rows = [r for r in self.rows + new if r.get("at", 0) > now - LADDER_KEEP][-20000:]
        # A line per event on the card; the whole record rewritten only when the file has grown to
        # twice what is kept (old events dropped), or is in the older form.
        if not jsonlog.append(self.path, new) or jsonlog.count(self.path) > 2 * len(self.rows) + 100:
            jsonlog.rewrite(self.path, self.rows)


def sort_drops(pairs, eff, same_network):
    """The drops a take() gave, as (misses, roams): a drop back within `blip` seconds on the same network is a
    roam; with ignore_roams it is not a miss; otherwise every drop is one."""
    roams = [d for d in pairs if d[1] is not None and d[1] - d[0] <= eff.get("blip", 20) and same_network]
    misses = [d[0] for d in pairs if not (eff.get("ignore_roams") and d in roams)]
    return misses, roams


class Roams:
    """Roaming made visible: each roam (a short drop and back, or a new access point
    between two checks) noted, the last hour's counted for the page, and each one a row of the ladder's
    record ({at, k: "roam", i, b: bssid, c: channel}) for the chart's marks."""

    def __init__(self):
        self.times = deque()
        self.last = None  # {bssid, channel, at}

    def see(self, now, link, blips):
        """link: iw's for the check (bssid, channel); blips: the roams the drops held. Returns the rows to keep."""
        rows = []
        moved = link.get("bssid") and self.last and link["bssid"] != self.last["bssid"]
        for lost, back in blips:
            rows.append({"at": round(back, 1), "k": "roam", "d": round(back - lost, 1)})
        if moved and not blips:
            rows.append({"at": round(now, 1), "k": "roam", "d": 0})
        if rows and link.get("bssid"):
            rows[-1].update(b=link["bssid"], c=link.get("channel"))
        if link.get("bssid"):
            self.last = {"bssid": link["bssid"], "channel": link.get("channel"), "at": now}
        self.times.extend(r["at"] for r in rows)
        while self.times and self.times[0] < now - 3600:
            self.times.popleft()
        return rows

    def hour(self):
        return len(self.times)


class HotspotFollow:
    """A hotspot that follows its link's channel, left on the old one after a roam. NetworkManager's dispatcher
    moves it only when the link comes up, and a roam within one connection never does; on an AIC8800 a hotspot on
    another channel than the link's is behind every recorded stall. Moved once the link has held its channel for
    FOLLOW_STEADY with no WiFi join under way, at most every FOLLOW_HOLD (one for the box: the hotspot is one),
    so a link that roams back in the meantime is left to settle. The move is root/ap.py's follow(), as the
    dispatcher's (hub_control ap-follow), which also says it on the Network page."""

    def __init__(self, record=None, channels=None, joining=None, move=None):
        self.record = record or _ap_record
        self.channels = channels or _channels_now
        self.joining = joining or _wifi_joining
        self.move = move or _ap_follow
        self.seen = {}  # link -> (its channel, since when), while the hotspot is on another
        self.moved = 0.0

    def check(self, iface, now, roamed_at=None):
        """One line of what was done, or "" (nothing to do, or not yet). roamed_at: the link's last roam, if seen."""
        from irate_box.root import ap

        rec = self.record()
        plan = rec.get("plan") or {}
        if not rec.get("up") or not plan.get("follows_uplink") or plan.get("uplink") != iface:
            self.seen.pop(iface, None)
            return ""
        hotspot = ap.ap_iface(plan)
        channels = self.channels()
        link_channel, hotspot_channel = channels.get(iface), channels.get(hotspot)
        if link_channel is None or hotspot_channel is None or link_channel == hotspot_channel:
            self.seen.pop(iface, None)
            return ""
        channel, since = self.seen.get(iface, (None, now))
        if channel != link_channel:
            since = roamed_at if roamed_at is not None and roamed_at <= now else now
            self.seen[iface] = (link_channel, since)
        if now - since < FOLLOW_STEADY or now - self.moved < FOLLOW_HOLD or self.joining():
            return ""
        self.moved = now
        code, out = self.move(iface)
        if self.channels().get(hotspot) == link_channel:
            self.seen.pop(iface, None)
            return f"The hotspot moved to channel {link_channel} with the link (it was on {hotspot_channel})."
        why = out.strip()[-160:] if code and out.strip() else "it is still on another channel"
        return f"The hotspot did not move to channel {link_channel} with the link: {why}."


def _ap_record():
    from irate_box.root import ap

    return ap._load(ap.RECORD, {})


def _channels_now():
    return {i: d.get("channel") for i, d in netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).items()}


def _wifi_joining():
    from irate_box.root import radio

    return radio.wifi_joining()


def _ap_follow(iface):
    from irate_box.root import ap

    return run(str(ap.CODE / "irate-box"), "hub_control", "ap-follow", iface, timeout=180)


_follow = HotspotFollow()


def history_state(state, link):
    """The watched link's letter for linkhistory, from the status's state."""
    return {"off": "o", "up": "u", "checking": "g"}.get(state, "g" if link else "d")


def write_status(data):
    # As root, in control/, which the hub can change: never through a link it planted.
    from irate_box.root import safeio

    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(path, json.dumps(data, indent=2))
    # The reboot record is root's, on the card: written only when it changed, not at every check.
    if "reboots" in data and os.geteuid() == 0:
        text = json.dumps(data["reboots"])
        if text != _reboots_written.get("text"):
            safeio.write(REBOOTS, text, 0o600)
            _reboots_written["text"] = text


_reboots_written = {}


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
        # The last report: in RAM, or (the first start after an update) where it used to be.
        return json.loads((status_path() if status_path().exists() else STATUS).read_text())
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
        raise Refused(
            M("uplink.step.unknown", "The recovery action must be one of these values: {values}.", values=list(STEPS))
        )
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


# --- the links ------------------------------------------------------------------------------


def link_kind(iface):
    """wifi | ethernet | other (a cellular modem, anything else): what a link is, for its steps and words."""
    d = SYS_NET / iface
    if (d / "phy80211").exists() or (d / "wireless").exists():
        return "wifi"
    if "DEVTYPE=wwan" in (netinv.read(d / "uevent") or ""):
        return "other"
    return "ethernet" if netinv.read(d / "type") == "1" else "other"


def carrier(iface):
    """The link's carrier: True, False, or None when it can't be read (the interface switched off)."""
    v = netinv.read(SYS_NET / iface / "carrier")
    return {"1": True, "0": False}.get(v)


def candidate_links(types=None):
    """The links that could carry the box to a network, {iface: kind}: every interface with a device behind it,
    less the loopback and a WiFi radio that is not a client (the hotspot's, a monitor)."""
    if types is None:
        types = {i: info.get("type") for i, info in netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).items()}
    out = {}
    try:
        dirs = sorted(SYS_NET.iterdir())
    except OSError:
        return out
    for d in dirs:
        if d.name == "lo" or not (d / "device").exists():
            continue
        if (d / "phy80211").exists() and types.get(d.name) not in (None, "managed"):
            continue
        out[d.name] = link_kind(d.name)
    return out


def link_hw(iface, run=run):
    """What a link's hardware is, so its settings follow it if it comes back under another name (a USB adapter
    plugged into another port, a second one before it): {mac: its own address, path: bus and device}."""
    d = SYS_NET / iface
    mac = netinv.read(d / "address")
    if netinv.read(d / "addr_assign_type") not in (None, "0"):  # an address given to it, not its own
        code, out = run("ethtool", "-P", iface, timeout=10)
        m = re.search(r"((?:[0-9a-f]{2}:){5}[0-9a-f]{2})", out or "") if code == 0 else None
        mac = m.group(1) if m else None
    try:
        dev = (d / "device").resolve()
        path = f"{(dev / 'subsystem').resolve().name}:{dev.name}" if (dev / "subsystem").exists() else None
    except OSError:
        path = None
    mac = mac.lower() if mac and BSSID_RE.match(mac.lower()) and mac != "00:00:00:00:00:00" else None
    return {"mac": mac, "path": path if path and HW_PATH_RE.match(path) else None}


def aliases(chosen, candidates, hw=link_hw):
    """{iface: the name its settings are under} for each link there now that the settings don't name, whose hardware
    is that of a name they do and that isn't there now: a link is known by its name, then by its hardware (its own
    address first, then its place on the bus)."""
    known = set(chosen.get("watch") or {}) | set(chosen.get("per_iface") or {}) | set(chosen.get("steps_off") or {})
    saved = {n: h for n, h in (chosen.get("watch_hw") or {}).items() if n in known and n not in candidates}
    out = {}
    for i in sorted(candidates):
        if i in known or not saved:
            continue
        h = hw(i)
        hit = next((n for n, s in saved.items() if h.get("mac") and s.get("mac") == h["mac"]), None) or next(
            (n for n, s in saved.items() if h.get("path") and s.get("path") == h["path"]), None
        )
        if hit:
            out[i] = hit
            saved.pop(hit)
    return out


def watched_links(chosen, route, candidates, troubled=(), alias=None):
    """The links to watch, {iface: "full" | "look"}: each the owner set to be watched (there or not: a radio whose driver
    failed takes its interface with it; under another name, when its hardware says it is the same link), and the
    others as watch_new says: the route's link (with one in trouble while it was, since the route goes with its link),
    every link there is, the route's link and every other looked at only, or none; never one the owner set not to be
    watched. troubled: {iface: how it was watched} for the links in an outage now."""
    alias = alias or {}
    watch, new = chosen.get("watch") or {}, chosen.get("watch_new", DEFAULT["watch_new"])
    troubled = troubled if isinstance(troubled, dict) else {i: "full" for i in troubled}
    said = lambda i: watch.get(alias.get(i, i))  # noqa: E731
    out = {}
    for name, on in watch.items():
        if on:
            moved = [i for i, n in alias.items() if n == name]
            out[moved[0] if moved and name not in candidates else name] = "full"
    for i in candidates:
        if said(i) is True:
            out[i] = "full"
    if new in ("uplink", "look"):
        if route and said(route) is not False:
            out[route] = "full"
        for i, how in troubled.items():
            if said(i) is not False:
                out.setdefault(i, how)
    if new in ("all", "look"):
        for i in [*candidates, *troubled]:
            if said(i) is not False:
                out.setdefault(i, "full" if new == "all" else "look")
    if new == "uplink" and not out and not route:
        # No route at all, nothing watched: the first WiFi client, as it is the likeliest to need help.
        first = next((i for i, k in sorted(candidates.items()) if k == "wifi" and said(i) is not False), None)
        if first:
            out[first] = "full"
    return out


NM_TYPES = {"wifi": "802-11-wireless", "ethernet": "802-3-ethernet"}


def autoconnect_for(iface, kind, run=run):
    """The NetworkManager connections set to start by themselves on this link (their names): what the link carries,
    or would if it worked. One up on another link, or bound to another interface, is not this link's."""
    if kind not in NM_TYPES or not shutil.which("nmcli"):
        return []
    code, out = run("nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,ACTIVE", "con", "show")
    if code != 0:
        return []
    names = []
    for row in netinv.nm_fields(out):
        if len(row) < 6 or row[2] != NM_TYPES[kind] or row[4] != "yes" or row[3] not in ("", iface):
            continue
        c, bound = run("nmcli", "-g", "connection.interface-name", "con", "show", "uuid", row[1])
        if c == 0 and bound.strip() not in ("", iface):
            continue
        names.append(row[0])
    return names


class Link:
    """One watched link: its watchdog (Watch) and what the loop keeps of it between looks."""

    def __init__(self, iface, chosen, old=None, reboots=None, dry=False, name=None, how="full"):
        self.iface, self.name, self.how = iface, name or iface, how
        old = old or {}
        self.kind = link_kind(iface) if (SYS_NET / iface).exists() else old.get("kind", "wifi")
        cable = self.kind == "ethernet"
        self.w = Watch(
            self.settings(chosen),
            old.get("events"),
            None,
            {"radio": "reset the adapter"} if cable else None,
            {"radio": "the adapter has failed"} if cable else None,
        )
        if reboots is not None:
            self.w.reboots = reboots  # one record for the box: the caps count every link's reboots
        self.dry = dry
        self.backend, self.actor, self.can, self.looked = None, None, set(), 0
        self.known_backend = None  # the last real backend seen, so its going away can be told
        self.radio_seen = None  # its radio's facts while it was there, for when it has gone
        self.reset_at = 0.0  # the radio's last reset (here or by its driver): older evidence is left behind
        self.hotspot_due = False  # the driver reset its radio: the hotspot started again once the link is back
        self.ssids_known = None  # the WiFi networks NetworkManager knows, read once an outage needs them
        self.pinned = None
        self.roams = Roams()
        self.roamed_at = None  # its last roam: a following hotspot moves once the link has held the new channel
        self.obs, self.up, self.gw, self.answers, self.roam_rows = {}, False, None, None, []

    def settings(self, chosen):
        """Its numbers: its own settings (under the name they are saved as); looked at only, nothing done (reach watch)."""
        if self.how == "look":
            return effective(dict(chosen, reach="watch"), self.name)
        own = (chosen.get("per_iface") or {}).get(self.name) or {}
        if self.kind == "ethernet" and "reach" not in own:
            cap = chosen.get("cable_reach", DEFAULT["cable_reach"])
            reach = min(chosen["reach"], cap, key=REACH.index)
            return effective(dict(chosen, reach=reach), self.name)
        return effective(chosen, self.name)

    def refresh(self, now, force=False):
        if force or now - self.looked > 600 or not self.actor:
            self.looked = now
            self.backend = backend_of(self.iface)
            self.can = repairs_for(self.backend, self.iface, self.radio_seen)
            if not self.actor:
                self.actor = Actor(self.iface, self.backend, self.dry)
            self.actor.backend, self.actor.kind = self.backend, self.kind

    def look(self, now, chosen, watcher):
        """One look at the link: up, the gateway, its drops and roams; closer when in trouble (the backend afresh, a
        wedged driver, the cause). Fills self.obs for tick()."""
        i, w = self.iface, self.w
        self.refresh(now)
        wifi_here = (SYS_NET / i / "phy80211").exists()
        if wifi_here:
            self.radio_seen = netinv.device_facts(i)
            self.actor.facts = self.radio_seen
        up = link_up(i)
        gw = gateway_of(i) if up else None
        answers = gateway_answers(i, gw) if gw else (False if up else None)
        link = netinv.parse_link(netinv.run("iw", "dev", i, "link")[1]) if up and wifi_here else {}
        same = bool(link.get("ssid")) and link.get("ssid") == (self.roams.last or {}).get("ssid", link.get("ssid"))
        drops, blips = sort_drops(watcher.take(i, now, w.eff.get("blip", 20)), w.eff, same)
        self.roam_rows = self.roams.see(now, link, blips)
        if self.roam_rows:
            self.roamed_at = self.roam_rows[-1]["at"]
        if self.roams.last:
            self.roams.last["ssid"] = link.get("ssid") or self.roams.last.get("ssid")
        obs = {
            "link": up,
            "gateway": answers,
            "drops": drops,
            "guests": guests(),
            "busy": busy(),
            "uptime": uptime(),
            "can": self.can,
            "hold_until": chosen.get("hold_until", 0),
            "owner_off": not up and self.backend == "networkmanager" and nm_owner_off(i),
            "shared_radio": shared_radio(i) if wifi_here else False,
        }
        if w.outage or w.misses:
            # In trouble: look closer, each check, for a wedged driver (and the backend afresh).
            was, self.backend = self.backend, backend_of(i)
            if self.backend != was:
                self.can = obs["can"] = repairs_for(self.backend, i, self.radio_seen)
                self.actor.backend = self.backend
                w.log(now, "info", f"{i} is now run by {self.backend} (was {was}).")
            known = was if was != "none" else self.known_backend
            exists, wifi = (SYS_NET / i).exists(), self.radio_seen is not None and self.kind == "wifi"
            own = own_reset() if wifi else None
            if own is not None:
                self.reset_at, self.hotspot_due = max(self.reset_at, now - own), True
            obs["wedged"] = wedge_evidence(
                i,
                self.backend,
                known,
                recent_journal(since=self.reset_at),
                crashwatch_events(),
                now,
                exists,
                wifi,
                self.reset_at,
            )
            obs["driver_reset"] = own
            ssid = (self.actor.profile or {}).get("ssid")
            if not ssid and self.backend == "networkmanager" and wifi and not up:
                if self.ssids_known is None:
                    self.ssids_known = known_ssids()
                ssid = self.ssids_known
            # A cable link's carrier; when it can't be read (the interface switched off), a cable is assumed pulled.
            cable = carrier(i) is True if self.kind == "ethernet" else None
            obs["cause"] = outage_cause(i, up, answers, wifi, exists, obs["wedged"], own, ssid, carrier=cable)
        elif self.backend and self.backend != "none":
            self.known_backend = self.backend
        if chosen.get("roaming") == "no-scan" and up and wifi_here and not self.dry:
            from irate_box.hub import roaming

            said = roaming.clear_scan(i)
            if said:
                w.log(now, "info", f"Roaming: {said} (NetworkManager had set them again).")
        self.obs, self.up, self.gw, self.answers = obs, up, gw, answers
        return obs

    def healthy(self):
        return bool(self.up) and self.answers is not False and self.gw is not None

    def act(self, now, actions, status):
        """The steps the watchdog decided on, taken; status(): this link's report, written before a reboot."""
        w, _i = self.w, self.iface
        if self.up and self.answers:
            self.actor.remember()
            self.ssids_known = None
            # The hotspot down while the link is up: started again. After a driver's own reset seen during an
            # outage (hotspot_due) at once; otherwise at any check, at most every HOTSPOT_RETRY, since a reset
            # the driver mends between two checks (an AIC8800's ~13 s) never shows as an outage here at all.
            due, self.hotspot_due = self.hotspot_due, False
            from irate_box.root import radio

            if (
                not self.dry
                and (due or now - _hotspot_tried[0] >= HOTSPOT_RETRY)
                and radio.hotspot_up()
                and not any(d.get("type") == "AP" for d in netinv.parse_iw_dev(netinv.run("iw", "dev")[1]).values())
            ):
                _hotspot_tried[0] = now
                said = radio.restore_hotspot() or "the hotspot is up"
                w.log(now, "result", said if due else f"The hotspot was down with the link up: {said}")
            elif not self.dry and self.kind == "wifi" and self.obs.get("hold_until", 0) <= now:
                said = _follow.check(self.iface, now, self.roamed_at)
                if said:
                    w.log(now, "result", said)
        for step in actions:
            if step == "pin":
                result = self.actor.pin()
                if "locked to" in result:
                    self.pinned = {"bssid": result.split()[2].rstrip(":"), "at": now}
            else:
                if step == "reboot":
                    write_status(status())
                result = self.actor.do(step)
                self.pinned = None if step in ("reconnect", "restart", "radio") else self.pinned
                if step == "radio":
                    self.reset_at = time.time()
            if result:
                w.log(time.time(), "result", str(result)[:300])


def _no_links_status(now, chosen, links_info, events, reboots, dry, said):
    return {
        "at": now,
        "state": "no-link",
        "iface": None,
        "events": events,
        "chosen": chosen,
        "settings": effective(chosen),
        "reboots": reboots[-10:],
        "dry_run": dry,
        "links": {},
        "inventory": links_info,
        "watching": said,
    }


def serve(dry=False):
    old = read_status()
    chosen = load_settings()
    mtime = _settings_mtime()
    reboots = list(_reboot_history(old) or [])
    old_links = old.get("links") if isinstance(old.get("links"), dict) else {}

    def old_of(iface):
        # A link's own events from the last report, or the single link's from a report written before links.
        o = old_links.get(iface) or {}
        return o if o.get("events") else ({"events": old.get("events")} if old.get("iface") == iface else o)

    rebooted = bool(old.get("events")) and old["events"][-1]["text"].startswith("Reboot")
    wake = threading.Event()
    watcher = DropWatcher(wake)
    watcher.start()
    links = {}  # iface: Link, the watched ones
    resting = {}  # iface: the last report of a link no longer watched, kept for the page
    history = History()
    ladder = Ladder()
    ladder.started(time.time(), old.get("at"), uptime())
    tls_at, cands, cands_at, inv, inv_at, first, alias = 0, {}, 0, {}, 0, True, {}

    def stop(*_):
        history.flush()  # the slot under way, rather than lose up to five minutes of it
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    while True:
        now = time.time()
        # Settings changed on /admin: take them, and give the owner a moment before acting.
        m = _settings_mtime()
        changed = False
        if m != mtime:
            before, (mtime, chosen) = chosen, (m, load_settings())
            if {k: v for k, v in before.items() if k != "hold_until"} != {
                k: v for k, v in chosen.items() if k != "hold_until"
            }:
                changed = True
                for lk in links.values():
                    lk.w.eff = lk.settings(chosen)
                    lk.w.pause_until = now + lk.w.eff["pause_after_change"]
                    lk.w.log(
                        now,
                        "info",
                        f"Settings changed: {words(chosen)}"
                        + (", with custom values" if chosen["overrides"] else "")
                        + ".",
                    )
            if before.get("hold_until") != chosen.get("hold_until"):
                hold = chosen.get("hold_until") or 0
                for lk in links.values():
                    lk.w.log(
                        now,
                        "info",
                        f"Repairs held until {time.strftime('%H:%M', time.localtime(hold))}."
                        if hold > now
                        else "Hold ended: repairs as set.",
                    )
        if first or changed or now - cands_at > 60:
            cands, cands_at = candidate_links(), now
            alias = aliases(chosen, cands)
        route = netinv.default_route()
        route_if = route["iface"] if route else None
        troubled = {i: lk.how for i, lk in links.items() if lk.w.outage or lk.w.misses}
        want = watched_links(chosen, route_if, cands, troubled, alias)
        for i in sorted(set(want) - set(links)):
            lk = links[i] = Link(
                i, chosen, old_of(i) if first else resting.pop(i, None), reboots, dry, alias.get(i), want[i]
            )
            resting.pop(i, None)
            if alias.get(i):
                lk.w.log(
                    now, "info", f"{i} is {alias[i]} under another name (the same hardware): its settings follow it."
                )
            if first and rebooted and old.get("iface") == i:
                lk.w.log(now, "info", "Started again after the reboot.")
            lk.w.log(
                now,
                "info",
                (f"Watching {i}" if first else f"Now watching {i} too")
                + (": looking only, nothing done for it" if lk.how == "look" else f" ({words(chosen)})")
                + (" — dry run" if dry else "")
                + ".",
            )
        for i, lk in links.items():
            if i in want and want[i] != lk.how:
                lk.how = want[i]
                lk.w.eff = lk.settings(chosen)
                lk.w.log(
                    now,
                    "info",
                    f"{i}: "
                    + (
                        "looked at only from now on."
                        if lk.how == "look"
                        else f"watched in full from now on ({words(chosen)})."
                    ),
                )
        for i in sorted(set(links) - set(want)):
            lk = links.pop(i)
            lk.w.log(
                now,
                "info",
                f"No longer watching {i}: "
                + (
                    "set not to be watched."
                    if (chosen.get("watch") or {}).get(i) is False
                    else "it no longer holds the box's route."
                ),
            )
            share(lk.w, i, dry)
            resting[i] = dict(
                _status(
                    lk.w,
                    now,
                    chosen,
                    i,
                    lk.backend,
                    lk.can,
                    lk.up,
                    lk.gw,
                    lk.answers,
                    lk.obs or {"guests": 0},
                    lk.pinned,
                    dry,
                ),
                watched=False,
                kind=lk.kind,
            )
        first = False
        watcher.ifaces = set(links)
        if first or now - inv_at > 600 or changed:
            inv_at = now
            inv = {
                i: {"kind": k, "watched": i in links, "autoconnect": autoconnect_for(i, k)} for i, k in cands.items()
            }
            for i, n in alias.items():
                if i in inv:
                    inv[i]["as"] = n
        for i, k in cands.items():
            inv.setdefault(i, {"kind": k, "autoconnect": []})
            inv[i].update(
                kind=k,
                watched=i in links,
                look=i in links and links[i].how == "look",
                up=link_up(i),
                route=i == route_if,
            )
        for i in [x for x in inv if x not in cands]:
            inv.pop(i)
        if not links:
            history.note(now, None, None, history.others(None, now), history.hotspots(now))
            write_status(
                _no_links_status(
                    now,
                    chosen,
                    inv,
                    [],
                    reboots,
                    dry,
                    "nothing: no link is set to be watched"
                    if chosen.get("watch_new") == "none"
                    else "nothing: no link holds the box's route",
                )
            )
            nap(wake, effective(chosen)["check"], mtime)
            continue
        for lk in links.values():
            lk.look(now, chosen, watcher)
        # What else works now: the links a whole-box step would cut off (box_steps).
        works = {i for i, lk in links.items() if lk.healthy()}
        works |= {i for i in cands if i not in links and inv.get(i, {}).get("up") and gateway_of(i)}
        full = sorted(i for i in links if links[i].how == "full") or sorted(links)
        primary = route_if if route_if in links else next((i for i in full if links[i].w.outage), full[0])
        asked = take_request(now) if NOW.exists() else None
        for i, lk in sorted(links.items()):
            lk.obs["others_up"] = sorted(works - {i})
            lk.obs["is_uplink"] = i == route_if
            actions = lk.w.tick(now, lk.obs)
            if asked and i == primary:
                more, _ = lk.w.by_hand(now, lk.obs, asked)
                actions += [s for s in more if s not in actions]
            share(lk.w, i, dry)  # before acting: a reboot leaves no time after
            # inv, primary and chosen do not change in this loop; the callback runs inside the iteration.
            lk.act(now, actions, lambda lk=lk: _report(links, resting, inv, primary, chosen, dry, reboots, time.time()))  # noqa: B023
            if not dry:
                try:
                    ladder.add(lk.w.ladder, i, now, [dict(r, i=i) for r in lk.roam_rows])
                    if i == primary:
                        ladder.misses(now, lk.w.misses, lk.w.eff["sensitivity"], i)
                except OSError as exc:
                    lk.w.log(now, "info", f"The escalation record was not written: {exc}")
            lk.w.ladder = []
            share(lk.w, i, dry)
        if not dry and os.geteuid() == 0 and now - tls_at > 6 * 3600:
            tls_at = now
            try:
                from irate_box.root import tls

                if tls.status().get("set_up"):
                    tls.renew()
            except Exception as exc:  # noqa: BLE001 - the watchdog goes on whatever happens here
                links[primary].w.log(now, "info", f"HTTPS certificate renewal failed: {exc}")
        report = _report(links, resting, inv, primary, chosen, dry, reboots, time.time())
        history.note(
            report["at"],
            primary,
            history_state(report["state"], links[primary].up),
            history.others(primary, report["at"]),
            history.hotspots(report["at"]),
        )
        write_status(report)
        # While a check or an outage is under way, look again sooner than the steady pace.
        trouble = any(lk.w.misses or lk.w.outage for lk in links.values())
        check = min(lk.w.eff["check"] for lk in links.values())
        nap(wake, check if not trouble else min(check, 15), mtime)


def _report(links, resting, inv, primary, chosen, dry, reboots, now):
    """The report: the primary link's at the top, as it always was (the page, the box doctor and the status light read
    it); each watched link's own under "links"; every link there is, watched or not, under "inventory"; the events of
    all of them, newest last, each with its link."""
    per = {}
    for i, lk in links.items():
        st = _status(lk.w, now, chosen, i, lk.backend, lk.can, lk.up, lk.gw, lk.answers, lk.obs, lk.pinned, dry)
        st["roams_hour"], st["roam_last"] = lk.roams.hour(), lk.roams.last
        per[i] = dict(
            st, watched=True, kind=lk.kind, look=lk.how == "look", **({"as": lk.name} if lk.name != i else {})
        )
    top = dict(per[primary])
    events = sorted(({**e, "iface": i} for i, st in per.items() for e in st["events"]), key=lambda e: e["at"])[-100:]
    for st in per.values():
        for k in ("chosen", "profile_change", "roaming_change", "reboots"):
            st.pop(k, None)
    top.update(events=events, reboots=reboots[-10:], links={**resting, **per}, inventory=inv, primary=primary)
    return top


def share(w, iface, dry):
    """The watchdog's new events into the watchers' record (watchlog), shared with crash watch's."""
    if not dry:
        for e in w.shared:
            cause = e["kind"] == "cause"  # its step is the cause (LADDER_KINDS)
            watchlog.add(
                "watchdog",
                e["kind"],
                e["text"],
                iface,
                at=e["at"],
                step=None if cause else e.get("step"),
                cause=e.get("step") if cause else None,
                how=e.get("by"),
            )
    w.shared = []


def _status(w, now, chosen, iface, backend, can, up, gw, answers, obs, pinned, dry):
    link = (
        netinv.parse_link(netinv.run("iw", "dev", iface, "link")[1])
        if up and (SYS_NET / iface / "phy80211").exists()
        else {}
    )
    stall = w.stall(now)
    state = (
        "off"
        if w.owner_off
        else "stalled"
        if stall
        else "down"
        if w.outage
        else "checking"
        if w.misses
        else "up"
        if up and answers
        else "down"
    )
    return {
        "at": now,
        "state": state,
        "iface": iface,
        "backend": backend,
        "repairs": sorted(can),
        "link": link,
        "gateway": gw,
        "gateway_answers": answers,
        "since": w.outage["since"] if w.outage else w.last_ok,
        "misses": w.misses,
        "guests": obs["guests"],
        "outage": ({k: w.outage[k] for k in ("since", "declared", "done", "held", "skipped")} if w.outage else None),
        "wedged": (w.outage or {}).get("wedged"),
        "cause": (w.outage or {}).get("cause"),
        "episode": ({k: w.episode[k] for k in ("since", "outages", "failed", "tried")} if w.episode else None),
        "next": w.next_step(now),
        "stall": stall,
        "paused_until": max(w.pause_until, chosen.get("hold_until") or 0) or None,
        "pinned": pinned,
        "events": list(w.events),
        "reboots": w.reboots[-10:],
        "chosen": chosen,
        "settings": w.eff,
        "profile_change": profile_record(),
        "roaming_change": _roaming_record(),
        "dry_run": dry,
    }


def _roaming_record():
    from irate_box.hub import roaming

    return roaming.record() or None


# --- the owner's profile, by consent ---------------------------------------------------------


def _uplink_profile():
    chosen = load_settings()
    iface = main_link(chosen)
    if not iface or backend_of(iface) != "networkmanager":
        raise Refused(NO_NM)
    prof = nm_connection(iface)
    if not prof:
        raise Refused(
            M(
                "uplink.profile.not-connected",
                "The network interface {iface} is not connected, so the box does not "
                "know its connection profile. When {iface} is connected, try again.",
                iface=iface,
            )
        )
    return prof


def profile(on):
    rec = profile_record()
    if on:
        prof = _uplink_profile()
        code, out = run(
            "nmcli",
            "-t",
            "-g",
            "connection.autoconnect-retries,connection.auth-retries",
            "con",
            "show",
            "uuid",
            prof["uuid"],
        )
        vals = [v.strip().split(" ")[0] for v in out.splitlines()] if code == 0 else []
        if len(vals) != 2 or not all(re.fullmatch(r"-?\d+", v) for v in vals):
            raise Refused(
                M(
                    "uplink.profile.unreadable",
                    "The box could not read the settings of the connection profile {name}. "
                    "NetworkManager reported: {output}",
                    name=prof["name"],
                    output=out.strip(),
                )
            )
        if rec and rec["uuid"] == prof["uuid"]:
            # Decided by what the profile holds, not by the record: a profile written afresh (netplan's,
            # at boot) has lost the change, and gets it again; the record keeps the values from before.
            if vals == ["0", "0"]:
                return f"{prof['name']}: already set to keep retrying"
            vals = [rec["old"]["connection.autoconnect-retries"], rec["old"]["connection.auth-retries"]]
        elif rec:
            profile(False)
        code, out = run(
            "nmcli",
            "con",
            "modify",
            "uuid",
            prof["uuid"],
            "connection.autoconnect-retries",
            "0",
            "connection.auth-retries",
            "0",
        )
        if code:
            raise Refused(
                M(
                    "uplink.profile.change-failed",
                    "The box could not change the connection profile {name}. NetworkManager reported: {output}",
                    name=prof["name"],
                    output=out.strip(),
                )
            )
        RECORD.write_text(
            json.dumps(
                {
                    "uuid": prof["uuid"],
                    "name": prof["name"],
                    "at": time.time(),
                    "old": {"connection.autoconnect-retries": vals[0], "connection.auth-retries": vals[1]},
                },
                indent=2,
            )
        )
        os.chmod(RECORD, 0o644)
        return (
            f"{prof['name']}: autoconnect-retries {vals[0]} → 0 and auth-retries {vals[1]} → 0, "
            "so NetworkManager keeps trying. Undo restores the earlier values."
        )
    if not rec:
        return "nothing to undo"
    args = [x for k, v in rec["old"].items() for x in (k, v)]
    code, out = run("nmcli", "con", "modify", "uuid", rec["uuid"], *args)
    if code and "not exist" not in out and "unknown connection" not in out.lower():
        raise Refused(
            M(
                "uplink.profile.undo-failed",
                "The box could not restore the earlier values of the connection profile "
                "{name}. NetworkManager reported: {output}",
                name=rec["name"],
                output=out.strip(),
            )
        )
    RECORD.unlink(missing_ok=True)
    return f"{rec['name']}: back to " + ", ".join(f"{k.split('.')[1]} {v}" for k, v in rec["old"].items())


def check():
    chosen = load_settings()
    iface = main_link(chosen)
    if not iface:
        return "no link to watch"
    backend = backend_of(iface)
    up = link_up(iface)
    gw = gateway_of(iface) if up else None
    ans = gateway_answers(iface, gw) if gw else None
    link = (
        netinv.parse_link(netinv.run("iw", "dev", iface, "link")[1]) if (SYS_NET / iface / "phy80211").exists() else {}
    )
    lines = [
        f"{iface}: run by {backend}; repairs here: {', '.join(sorted(repairs_for(backend, iface))) or 'none'}",
        f"link {'up' if up else 'down'}"
        + (
            f", {link.get('ssid')} via {link.get('bssid')} ch {link.get('channel')} {link.get('signal')} dBm"
            if link
            else ""
        ),
        f"gateway {gw or 'none'}: {'answers' if ans else 'no answer' if gw else '-'}",
        f"settings: {words(chosen)}"
        + (f", overrides {json.dumps(chosen['overrides'])}" if chosen["overrides"] else ""),
    ]
    return "\n".join(lines)


def presets():
    out = []
    for pace in PACE:
        eff = effective({"pace": pace, "reach": "reboot", "overrides": {}})
        steps = ", ".join(f"{s} +{human(t)}" for s, t in sorted(eff["steps"].items(), key=lambda x: x[1]))
        out.append(
            f"{pace:7} check {human(eff['check']):6} window {human(eff['window']):6} steps: {steps}; reconnect again every {human(eff['repeat'])}"
        )
    out.append(f"reach: {', '.join(REACH)} (the steps above, up to and including the one chosen; watch: none)")
    out.append(
        f"sensitivity: {SENSITIVITY[0]}-{SENSITIVITY[1]} missed checks within the pace's window (default {DEFAULT['sensitivity']})"
    )
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
    elif cmd == "set" and rest and (all("=" in a for a in rest) or (len(rest) <= 2 and rest[0] in EAGERNESS_WAS)):
        s = load_settings()
        if "=" not in rest[0]:  # the old single level (install.sh --uplink standard,strict)
            pace, reach, guests = EAGERNESS_WAS[rest[0]]
            rest = [f"pace={pace}", f"reach={reach}", f"guests={guests}"] + [
                f"sensitivity={FORGIVENESS_WAS.get(f, f)}" for f in rest[1:]
            ]
        for a in rest:
            k, _, v = a.partition("=")
            if k == "forgiveness" and v in FORGIVENESS_WAS:  # the old name, still read
                k, v = "sensitivity", FORGIVENESS_WAS[v]
            if k not in ("pace", "reach", "sensitivity", "guests", "on_wedge", "retry", "give_up", "ignore_roams"):
                # Not roaming's lock or no-scan: they change the owner's WiFi, so they are asked on /admin.
                sys.exit(
                    f"uplink.py set: {k} is not one of pace, reach, sensitivity, guests, on_wedge, retry, give_up, ignore_roams"
                )
            if k == "ignore_roams":
                if v.lower() not in ("yes", "no", "true", "false", "on", "off"):
                    sys.exit("uplink.py set: ignore_roams is yes or no")
                v = v.lower() in ("yes", "true", "on")
            s[k] = v
        try:
            s = save_settings(s)
        except ValueError as exc:
            sys.exit(
                f"uplink.py set: {exc} (pace: {', '.join(PACE)}; reach: {', '.join(REACH)}; "
                f"sensitivity: {SENSITIVITY[0]}-{SENSITIVITY[1]} missed checks)"
            )
        print(f"uplink: {words(s)}")
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
        from irate_box.hub import roaming

        try:
            for line in roaming.undo_all(main_link(load_settings())):
                print(line)
        except ValueError as exc:
            print(f"roaming: {exc}")
    else:
        sys.exit(
            "usage: uplink.py run [--dry-run] | check | presets | set KEY=VALUE... | hold MINUTES "
            "| profile on|off | undo-all"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
