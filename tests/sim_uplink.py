# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The uplink watchdog's decisions against a simulated clock: pace, reach and sensitivity, flapping on
the ladder, the guards, overrides, episodes, stalls, a wedged driver. No network is touched.
python3 tests/sim_uplink.py"""
import sys
from pathlib import Path
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
from irate_box.hub import uplink as U  # noqa: E402

ALL = {"reconnect", "restart", "radio", "reboot", "pin"}
NO_PIN = ALL - {"pin"}
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += (not cond)

# The levels by name in these tests: pace/reach (and guests). The old single dial's names map to
# these where a case is about the same thing (patient: reconnect only; stubborn: everything, guests or not).
LV = {"off": ("gentle", "watch", "protect"), "patient": ("gentle", "reconnect", "protect"), "standard": ("steady", "restart", "protect"),
      "persistent": ("prompt", "radio", "protect"), "stubborn": ("urgent", "reboot", "ignore")}
def C(e, sens=3, over=None, **kw):
    pace, reach, guests = LV[e] if e in LV else (*e.split("/"), "protect")
    return dict({"pace": pace, "reach": reach, "guests": guests, "sensitivity": sens, "overrides": over or {}}, **kw)

def outage(e, sens, dur, link=False, guests=0, busy=None, uptime=1e9, can=NO_PIN, over=None, step=5):
    """Link down (or gateway silent) for dur seconds, observed every `step` s; returns [(t, action)].
    Without NetworkManager's "pin" unless asked, so the ladder's own steps show."""
    w = U.Watch(U.effective(C(e, sens, over)))
    acts = []
    for t in range(0, dur, step):
        obs = {"link": link, "gateway": False if link else None, "drops": [], "guests": guests, "busy": busy,
               "uptime": uptime + t, "can": can}
        for a in w.tick(1000 + t, obs):
            acts.append((t, a))
    return acts, w

# 1. On the ladder after `sensitivity` misses (3 here, every 5 s: at 10 s); then each step at its pace's time.
a, w = outage("patient", 3, 3600)
check("sensitivity 3: on the ladder at the third miss; gentle's reconnect 5 min after (10 + 300 s)", a[0] == (310, "reconnect"), a[:3])
check("reach reconnect: only reconnects", {x for _, x in a} == {"reconnect"}, a)
gaps = [a[i+1][0]-a[i][0] for i in range(len(a)-1)]
check("gentle: reconnects back off 900→1800→…", gaps[:2] == [900, 1800], gaps)
a, w = outage("gentle/reboot", 3, 8000)
check("gentle pace, reach reboot (the defaults): restart at +30 min, radio +60, reboot +120 from going on the ladder",
      [x for x in a if x[1] != "reconnect"] == [(1810, "restart"), (3610, "radio"), (7210, "reboot")], [x for x in a if x[1] != "reconnect"])
check("the defaults are the gentlest pace, the highest reach and 3 missed checks", U.DEFAULT["pace"] == "gentle"
      and U.DEFAULT["reach"] == "reboot" and U.DEFAULT["sensitivity"] == 3 and U.load_settings()["reach"] == "reboot")
a, w = outage("standard", 3, 3600)
check("steady pace: reconnect at 10+60, restart at 10+600", a[0] == (70, "reconnect") and (610, "restart") in a, a)
check("reach restart: no radio/reboot", not ({"radio", "reboot"} & {x for _, x in a}), a)
a, w = outage("persistent", 3, 3600)
check("prompt: reconnect at once, restart at +5 min, radio +15", a[0] == (10, "reconnect") and (310, "restart") in a and (910, "radio") in a, a)
a, w = outage("stubborn", 3, 7200)
check("urgent: reboot at +30 min", (1810, "reboot") in a, [x for x in a if x[1] != "reconnect"])

# 2. Sensitivity: the number of misses, within the pace's window.
a, w = outage("standard", 1, 600, link=True, step=30)
check("sensitivity 1: the first missed check puts it on the ladder", a[0] == (60, "reconnect"), a[:2])
a, w = outage("standard", 5, 1200, link=True, step=60)
check("sensitivity 5, a check a minute: on the ladder at the fifth miss (4 min), the reconnect a minute later", a[0] == (300, "reconnect"), a[:2])
w = U.Watch(U.effective(C("standard", 3)))
for t, ok in [(0, False), (60, False), (120, True)]:
    w.tick(t, {"link": True, "gateway": ok, "drops": [], "can": ALL})
check("two misses then fine: not on the ladder, nothing logged", w.outage is None and not w.events and w.misses == 2, list(w.events))
for t, ok in [(700, False)]:
    w.tick(t, {"link": True, "gateway": ok, "drops": [], "can": ALL})
check("  the two have left steady's 10-minute window: one miss, still not on it", w.outage is None and w.misses == 1, w.misses)
w = U.Watch(U.effective(C("standard", 3)))
for t in (0, 200, 400):   # a miss every 200 s, fine between: spread out, still 3 within 10 min
    w.tick(t, {"link": True, "gateway": False, "drops": [], "can": ALL}); w.tick(t + 60, {"link": True, "gateway": True, "drops": [], "can": ALL})
check("misses spread out within the window count as much as ones together",
      w.events and w.events[0]["kind"] == "down", list(w.events))
try:
    U.validate({"sensitivity": 0}); check("sensitivity 0 refused", False)
except ValueError:
    check("sensitivity 0 refused", True)
check("sensitivity as a number, from a form's string too", U.validate({"sensitivity": "7"})["sensitivity"] == 7)

# 3. Flapping: drops between checks are misses; past the line the link is on the ladder while it flaps.
def flap(sens=3, can=ALL, hold=0, n=10, every=60, reach="standard"):
    w = U.Watch(U.effective(C(reach, sens)))
    acts = []
    for i in range(n):
        acts += [(i, a) for a in w.tick(1000 + i * every, {"link": True, "gateway": True, "drops": [1000 + i * every - 1], "can": can, "hold_until": hold})]
    return acts, w
a, w = flap()
check("drops between healthy checks: at the third, on the ladder while it flaps (said so)", w.events[0]["kind"] == "down"
      and "on the ladder while it flaps" in w.events[0]["text"] and w.outage and w.outage["flapping"], list(w.events)[:2])
check("  its reconnect locked to the strongest access point (what the flap detector's pin did)", [x for _, x in a][:1] == ["pin"], a)
a, w = flap(can=NO_PIN)
check("  without NetworkManager: a plain reconnect", [x for _, x in a][:1] == ["reconnect"], a)
a, w = flap(hold=1e12)
check("  repairs held: nothing done", a == [], a)
a, w = flap(n=4)
for t in range(1300, 2200, 60):
    w.tick(t, {"link": True, "gateway": True, "drops": [], "can": ALL})
check("  the drops stop: off the ladder once fewer than the sensitivity are in the window", w.outage is None
      and any(e["text"].startswith("Steady again: fewer than 3 misses within 10 min") for e in w.events), [e["text"] for e in w.events][-2:])
a, w = flap(sens=20)
check("  sensitivity 20: ten drops in ten minutes stay below the line", a == [] and w.outage is None, a)

# 4. Guards
a, w = outage("persistent", 3, 3600, guests=2)
check("reach radio + guests: radio held, logged once", "radio" not in {x for _, x in a} and sum(e["kind"] == "held" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"]=="held"])
a, w = outage("stubborn", 3, 7200, guests=2)
check("guests ignored: radio and reboot go ahead", {"radio", "reboot"} <= {x for _, x in a}, a)
a, w = outage("stubborn", 3, 7200, busy="a build")
check("a build running: reboot held", "reboot" not in {x for _, x in a} and any("a build" in e["text"] for e in w.events))
a, w = outage("stubborn", 3, 7200, uptime=0)
check("soon after boot: reboot only after reboot_gap uptime", (1810, "reboot") not in a and any(x == "reboot" for _, x in a), [x for x in a if x[1]=="reboot"])
w = U.Watch(U.effective(C("stubborn", 3)), reboots=[0, 10000, 20000])
acts = []
for t in range(30000, 40000, 5):
    acts += w.tick(t, {"link": False, "drops": [], "can": ALL})
check("daily cap of 3 reboots", "reboot" not in acts and any("3 times today" in e["text"] for e in w.events))
a, w = outage("persistent", 3, 3600, can={"reconnect", "reboot"})
check("radio not possible → skipped, logged", "radio" not in {x for _, x in a} and any(e["kind"] == "skip" for e in w.events))

# 5. Overrides
a, w = outage("persistent", 3, 3600, over={"steps": {"restart": 120, "radio": 300}, "repeat": 0})
check("custom: restart 120, radio 300, no repeats", a == [(10, "reconnect"), (130, "restart"), (310, "radio")], a)
a, w = outage("standard", 3, 3600, over={"steps": {"radio": 300}})
check("custom: a step's time set beyond the reach stays beyond it", "radio" not in {x for _, x in a}, a)
a, w = outage("stubborn", 3, 7200, over={"steps": {"reboot": None}})
check("custom: urgent without reboot", "reboot" not in {x for _, x in a}, a)
a, w = outage("standard", 3, 900, link=True, step=60, over={"window": 90})
check("custom window of 90 s with a check a minute: 3 misses can never fit, so never on the ladder", a == [] and w.outage is None, a)

# 6. Recovery logs duration and steps; its misses go with it
w = U.Watch(U.effective(C("standard", 3)))
for t in range(0, 700, 5): w.tick(t, {"link": False, "drops": [], "can": ALL})
w.tick(700, {"link": True, "gateway": True, "drops": [], "can": ALL})
check("recovery logged with what was tried", w.events[-1]["kind"] == "up" and "restart" in w.events[-1]["text"], w.events[-1])
check("off the ladder, and the outage's misses cleared (a link that came back is not repaired for them)", w.outage is None and w.misses == 0)
w.tick(705, {"link": True, "gateway": False, "drops": [], "can": ALL})
check("  so one miss after it is just one", w.outage is None and w.misses == 1)

# 7. A night on a real board, replayed: the link lost at 0–2,
# 3.5–5 and 11–22 min with NetworkManager there; then NM's restart left no backend (only a radio reset
# or a reboot possible) and the link stayed down for 5 hours.
def night(e="standard", sens=3, over=None, step=30, hours=5):
    w = U.Watch(U.effective(C(e, sens, over)))
    acts, down = [], [(0, 120), (210, 300), (660, 1320)]
    for t in range(0, 1320 + hours * 3600, step):
        lost = t >= 1320 or any(a <= t < b for a, b in down)
        can = NO_PIN if t < 1320 else {"radio", "reboot"}
        for a in w.tick(t, {"link": not lost, "gateway": True if not lost else None, "drops": [], "can": can}):
            acts.append((t, a))
    return acts, w
a, w = night()
# Was a reconnect at 1.0, 4.5 and 12.0 min (the ladder starting afresh each outage; the third wedged the
# firmware). Now: on the ladder at the third missed check (1 min), one reconnect, nothing on the quick
# relapse, the restart when its time from the episode's start comes.
check("night: one reconnect in the episode (on the second outage: the first was back before its minute), then the restart when due",
      a[:2] == [(270, "reconnect"), (720, "restart")]
      and [x for _, x in a].count("reconnect") == 1, a[:5])
check("  the relapse said, and the episode kept", any("did not hold" in e["text"] for e in w.events) and w.episode["outages"] == 3
      and w.episode["failed"] == ["reconnect"], (w.episode, [e["text"] for e in w.events][:8]))
end = 1320 + 5 * 3600
check("  after NM's restart, no next step: none it may take is possible", not [x for t, x in a if t >= 1320] and w.next_step(end) is None,
      (a[-3:], w.next_step(end)))
st = w.stall(end)
check("  stalled, needing a radio reset, which reach restart does not go to", st and st["needs"] == "radio"
      and st["text"] == "Stalled: what could help now is to reset the radio, and the reach set goes no further than to restart the network service.", st)
check("  said once in the log", sum(e["kind"] == "stalled" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"] == "stalled"])
a, w = night("gentle/reboot")
check("night with the defaults (gentle, reach reboot, 3 misses): the two short outages back before gentle's 5 minutes, one reconnect"
      " at 12 min, the restart skipped (NM gone), the radio reset at 61 min, a reboot at 121 if that fails: not 5 hours stalled",
      a == [(720, "reconnect"), (3660, "radio"), (7260, "reboot")] and any(e["text"] == "Cannot restart the network service here; skipped." for e in w.events), a)

# Stalls: not while a step is only held, not for watch-only, nothing possible at all said so.
a, w = outage("persistent", 3, 3600, guests=2, can={"radio", "reboot"})
check("stall: a radio reset held for guests is held, not a stall", w.stall(1000 + 3600) is None and w.next_step(1000 + 3600)["step"] == "radio")
a, w = outage("off", 3, 3600)
check("stall: watch-only is the owner's choice, never a stall", w.stall(1000 + 3600) is None and not any(e["kind"] == "stalled" for e in w.events))
a, w = outage("standard", 3, 3600, can=set())
check("stall: nothing possible at all", w.stall(1000 + 3600)["needs"] is None and "nothing the box can do" in w.stall(1000 + 3600)["text"])

# A step asked for on /admin: done now whatever the level and a hold, but only if possible and not held.
w = U.Watch(U.effective(C("standard", 3)))
obs = {"link": False, "drops": [], "can": {"radio", "reboot"}, "guests": 0, "hold_until": 1e12}
for t in range(0, 1200, 30):
    w.tick(t, obs)
check("by hand: the radio reset goes, held repairs or not, and is logged", w.by_hand(1200, obs, "radio") == (["radio"], None)
      and w.events[-1]["text"] == "Reset the radio, asked on /admin." and "radio" in w.outage["done"])
check("  not with guests on the hotspot (the guard holds), said why", w.by_hand(1210, dict(obs, guests=1), "radio") == ([], "1 guest is on the hotspot"))
check("  not what cannot be done here", w.by_hand(1220, obs, "reconnect")[0] == [] and "cannot" in w.events[-1]["text"])
check("  not a made-up step", w.by_hand(1230, obs, "nuke") == ([], "nuke is not a step"))
import tempfile as _tf  # noqa: E402
U.ETC = Path(_tf.mkdtemp()); U.NOW = U.ETC / "uplink-now.json"
msg = U.request_step("radio")
check("the request: written for the watchdog, root's only", U.NOW.exists() and oct(U.NOW.stat().st_mode & 0o777) == "0o600" and "reset the radio" in msg)
check("  taken once", U.take_request(__import__("time").time()) == "radio" and not U.NOW.exists() and U.take_request(0) is None)
U.request_step("reboot")
check("  one from ten minutes ago is not wanted late", U.take_request(__import__("time").time() + 700) is None and not U.NOW.exists())
U.NOW.write_text('{"step": "rm -rf", "at": 1}')
check("  a made-up one is dropped", U.take_request(2) is None and not U.NOW.exists())
try:
    U.request_step("nuke"); check("request_step refuses a made-up step", False)
except ValueError:
    check("request_step refuses a made-up step", True)

# 7c. Episodes: relapses climb within reach, a single outage is as before, an episode ends.
def relapses(e, downs, can=NO_PIN, total=None, step=10, sens=3):
    w = U.Watch(U.effective(C(e, sens)))
    acts = []
    for t in range(0, total or downs[-1][1] + 1200, step):
        lost = any(a <= t < b for a, b in downs)
        for x in w.tick(t, {"link": not lost, "gateway": True if not lost else None, "drops": [], "can": can}):
            acts.append((t, x))
    return acts, w
a, w = relapses("persistent", [(0, 120), (360, 480), (960, 1080)])
check("episode: three relapses climb reconnect → restart → radio (prompt: restart +5 min, radio +15 min from the episode's start)",
      a == [(20, "reconnect"), (380, "restart"), (980, "radio")], a)
check("  each relapse names what did not hold", [e["text"].split(":")[1].split(",")[0].strip() for e in w.events if "did not hold" in e["text"]]
      == ["reconnect did not hold", "restart the network service did not hold"], [e["text"] for e in w.events if "did not hold" in e["text"]])
check("  steady again: the episode ends after `relapse` (15 min) up, said with what was tried",
      w.episode is None and "Steady again after an episode of 3 outages (tried: reconnect, restart the network service, reset the radio)." in [e["text"] for e in w.events],
      [e["text"] for e in w.events][-3:])
a, w = relapses("persistent", [(0, 120), (2000, 2120)])
check("episode: an outage more than 15 min after the last is a new episode, from the bottom", [x for _, x in a] == ["reconnect", "reconnect"], a)
a, w = relapses("patient", [(0, 120), (300, 420), (600, 720)])
check("episode: with nothing heavier to climb to (reach reconnect), reconnect is tried again", a == [(320, "reconnect"), (620, "reconnect")], a)
a, w = relapses("patient", [(0, 120), (300, 420), (600, 720)], can=ALL)
check("  with NetworkManager, the second one locked to the strongest access point", a == [(320, "reconnect"), (620, "pin")], a)
a1, _ = outage("standard", 3, 3600)
check("episode: a single outage is as it was (reconnect, back-off, restart at +10 min)", (610, "restart") in a1 and a1[0] == (70, "reconnect"), a1[:3])
a, w = flap(reach="persistent", n=30, every=60)
check("flapping on: climbs the ladder by the episode's times (prompt: restart at +5 min, radio at +15)",
      [x for _, x in a if x != "pin"][:2] == ["restart", "radio"] and a[0][1] == "pin", a)

# 7d. A wedged driver: the evidence, from a real board's journal lines, and what is done with it.
J = (Path(REPO) / "tests/fixtures/aic8800-wedge-2026-10-09/journal.txt").read_text().splitlines()
minute5 = [l for l in J if l.startswith("Oct 09 04:05")]
ev = U.wedge_evidence("wlan0", "networkmanager", "networkmanager", minute5, [], 0)
check("wedged: scans failing as busy, from the journal (04:05, 32 of them)", ev == "scans keep failing as busy (32 in 2 minutes)", ev)
after = [l for l in J if l.startswith("Oct 09 04:10")]
ev = U.wedge_evidence("wlan0", "none", "networkmanager", after, [], 0)
check("  after NM's restart: the backend gone though wlan0 is there, and no supplicant for it",
      ev == "networkmanager no longer runs wlan0, though it is still there; the supplicant cannot be started for wlan0", ev)
check("  ap0's own lines are not wlan0's", U.wedge_evidence("ap0", "networkmanager", "networkmanager", minute5, [], 0) is None)
cwev = [{"at": 900, "iface": "wlan0", "kind": "failed", "text": "57 driver errors in a minute: cmd queue crashed"}]
check("  crash watch's radio failure, while not recovered", "crash watch saw the radio fail" in U.wedge_evidence("wlan0", "networkmanager", None, [], cwev, 1000)
      and U.wedge_evidence("wlan0", "networkmanager", None, [], cwev + [{"at": 950, "iface": "wlan0", "kind": "recovered"}], 1000) is None)
check("  a quiet journal and a backend that never was: nothing", U.wedge_evidence("wlan0", "none", None, ["wlan0: CTRL-EVENT-CONNECTED"], [], 0) is None)
check("  not WiFi: never", U.wedge_evidence("eth0", "none", "networkmanager", after, cwev, 1000, wifi=False) is None)

def wedged_night(e, on_wedge, guests=0):
    w = U.Watch(U.effective(C(e, 3, on_wedge=on_wedge)))
    acts = []
    for t in range(0, 3600, 30):
        obs = {"link": False, "drops": [], "can": ALL if t < 600 else {"radio", "reboot"}, "guests": guests,
               "wedged": "scans keep failing as busy (32 in 2 minutes)" if t >= 300 else None}
        acts += [(t, x) for x in w.tick(t, obs)]
    return acts, w
a, w = wedged_night("persistent", "radio")
check("on_wedge radio, the level reaching it: straight to the radio reset when the evidence comes (not restart at 6 min, radio at 16)",
      a == [(60, "reconnect"), (240, "pin"), (300, "radio")] and any("can't mend a wedged driver" in e["text"] for e in w.events), a)
a, w = wedged_night("persistent", "ladder")
check("on_wedge ladder (the default): the ladder as set, the evidence said once",
      (360, "restart") in a and (960, "radio") in a and sum(e["kind"] == "wedged" for e in w.events) == 1, a)
a, w = wedged_night("standard", "radio")
st = w.stall(3600)
check("on_wedge radio, the level stopping short of it: stalled, needing a radio reset, with the evidence",
      [x for _, x in a] == ["reconnect"] and st and st["needs"] == "radio" and "looks wedged: scans keep failing" in st["text"], (a, st))
a, w = wedged_night("persistent", "radio", guests=1)
check("on_wedge radio, guests on the hotspot: held, said once, not reset", "radio" not in [x for _, x in a]
      and sum(e["kind"] == "held" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"] == "held"])
check("validate: on_wedge ladder by default, radio accepted, nothing else",
      U.validate({})["on_wedge"] == "ladder" and U.validate({"on_wedge": "radio"})["on_wedge"] == "radio")
try:
    U.validate({"on_wedge": "reboot"}); check("validate refuses on_wedge reboot", False)
except ValueError:
    check("validate refuses on_wedge reboot", True)

# 7e. Two dials, the restart guard on a shared radio, settings from before carried over.
def shared(guests, sharing, ignore=False):
    w = U.Watch(U.effective(C("prompt/radio", guests="ignore" if ignore else "protect")))
    acts = []
    for t in range(0, 1200, 10):
        acts += [(t, x) for x in w.tick(t, {"link": False, "drops": [], "can": ALL, "guests": guests, "shared_radio": sharing})]
    return acts, w
a, w = shared(2, True)
check("guests on a hotspot that shares the radio: the restart held too, said why", "restart" not in [x for _, x in a]
      and any("2 guests are on the hotspot, which shares the radio" in e["text"] for e in w.events), [e["text"] for e in w.events if e["kind"] == "held"])
a, w = shared(2, False)
check("  a hotspot on a radio of its own: the restart goes ahead (the radio reset is held)", (320, "restart") in a and "radio" not in [x for _, x in a], a)
a, w = shared(0, True)
check("  no guests: the restart goes ahead", (320, "restart") in a, a)
a, w = shared(2, True, ignore=True)
check("  guests ignored by the owner: everything goes ahead", {"restart", "radio"} <= {x for _, x in a}, a)
v = U.validate({"eagerness": "standard", "forgiveness": "strict", "overrides": {"guests": "ignore", "check": 45, "grace": 15, "flap_action": "pin"}})
check("settings from before carried over: eagerness as pace and reach, forgiveness as sensitivity (strict: 2), an overridden guests "
      "as the setting, grace and the flap settings dropped", (v["pace"], v["reach"], v["guests"], v["sensitivity"], v["overrides"])
      == ("steady", "reboot", "ignore", 2, {"check": 45}) and "forgiveness" not in v, v)
check("  tolerant: 5 missed checks; normal: 3; an overridden misses: that number", U.validate({"forgiveness": "tolerant"})["sensitivity"] == 5
      and U.validate({"forgiveness": "normal"})["sensitivity"] == 3 and U.validate({"overrides": {"misses": 7}})["sensitivity"] == 7)
check("  stubborn: urgent, reboot, guests or not; off: watch", U.validate({"eagerness": "stubborn"})["guests"] == "ignore"
      and U.validate({"eagerness": "off"})["reach"] == "watch")
check("  watch: no steps at all, no repeats", U.effective(C("gentle/watch"))["steps"] == {} and U.effective(C("gentle/watch"))["repeat"] == 0)
for bad in [{"pace": "fast"}, {"reach": "nuke"}, {"guests": "maybe"}]:
    try: U.validate(bad); check(f"validate rejects {bad}", False)
    except ValueError: check(f"validate rejects {bad}", True)
check("the settings in words", U.words(U.validate({"pace": "steady", "reach": "radio", "guests": "ignore", "on_wedge": "radio"}))
      == "steady pace, reach radio, sensitivity 3 missed checks, guests or not, a wedged radio reset at once")

# 7f. The escalation record (the box doctor's chart): each outage, step, hold and stall, with who took it.
a, w = night("gentle/reboot")
lad = [(e["kind"], e.get("step"), e.get("by")) for e in w.ladder]
check("the ladder's events carry their step and who took it", ("repair", "reconnect", "auto") in lad and ("repair", "radio", "auto") in lad
      and ("skip", "restart", None) in lad and lad[0] == ("down", None, None) and ("up", None, None) in lad, lad[:8])
check("  the log's other events are not on it", all(k in U.LADDER_KINDS for k, _, _ in lad) and not any(e["kind"] == "info" for e in w.ladder))
w.by_hand(99999, {"link": False, "can": ALL, "guests": 0}, "radio")
check("  a step asked on /admin says so", w.ladder[-1]["step"] == "radio" and w.ladder[-1]["by"] == "hand")
import tempfile as _tf2  # noqa: E402
import irate_box.root.safeio as _sio  # noqa: E402
_sio.write = lambda path, text, mode=0o644: Path(path).write_text(text)
L = U.Ladder(Path(_tf2.mkdtemp()) / "uplink-ladder.json")
L.add(w.ladder, "wlan0", 100000)
rows = __import__("json").loads(L.path.read_text())
check("the record: compact rows, the interface, the text kept short", rows[0] == {"at": 60, "k": "down", "i": "wlan0", "t": "Link lost (3 missed checks within 30 min)."}
      and any(r.get("s") == "radio" and r.get("b") == "auto" for r in rows), rows[:3])
L.add([{"at": 100000 + 73 * 86400, "kind": "down", "text": "Link lost."}], "wlan0", 100000 + 73 * 86400)
check("  72 days kept, older ones dropped", [r["at"] for r in __import__("json").loads(L.path.read_text())] == [100000 + 73 * 86400])
check("  nothing to add: nothing written", L.add([], "wlan0", 0) is None)
L2 = U.Ladder(Path(_tf2.mkdtemp()) / "uplink-ladder.json")
for at, n in ((1000, 0), (1010, 1), (1100, 2), (1250, 1), (1320, 0), (1500, 0), (1650, 0)):
    L2.misses(at, n, 3, "wlan0")
rows2 = __import__("json").loads(L2.path.read_text()) if L2.path.exists() else []
check("the symptoms: the most misses in each five-minute slot, with the line (the sensitivity) then; quiet slots not kept",
      [(r["at"], r["n"], r["th"]) for r in rows2 if r["k"] == "m"] == [(900, 2, 3), (1200, 1, 3)], rows2)
L2.misses(1800, 0, 5, "wlan0"); L2.misses(2100, 0, 5, "wlan0")
check("  a quiet slot is kept when the line moved", [(r["at"], r["n"], r["th"]) for r in __import__("json").loads(L2.path.read_text())][-1] == (1800, 0, 5))

# 8. validate()
for bad in [{"eagerness": "max"}, {"forgiveness": "lax"}, {"sensitivity": 21}, {"sensitivity": "many"}, {"overrides": {"check": 5}},
            {"overrides": {"rm": 1}}, {"iface": "a;b"}, {"overrides": {"steps": {"nuke": 1}}}, {"overrides": {"window": 10}}]:
    try: U.validate(bad); check(f"validate rejects {bad}", False)
    except ValueError: check(f"validate rejects {bad}", True)
v = U.validate({"eagerness": "stubborn", "forgiveness": "strict", "overrides": {"check": "45", "steps": {"reboot": None}}})
check("validate accepts and coerces", v["overrides"] == {"check": 45, "steps": {"reboot": None}}, v)

# 9. Disconnected by hand: no repairs, logged once; connected again → normal
w = U.Watch(U.effective(C("stubborn", 2)))
acts = []
for t in range(0, 3000, 10):
    acts += w.tick(t, {"link": False, "drops": [], "can": ALL, "owner_off": True})
check("owner disconnect: no action in 50 min", acts == [], acts[:3])
check("owner disconnect: logged once", sum("by hand" in e["text"] for e in w.events) == 1, list(w.events))
w.tick(3000, {"link": True, "gateway": True, "drops": [], "can": ALL})
acts = []
for t in range(3010, 3200, 10):
    acts += w.tick(t, {"link": False, "drops": [], "can": ALL, "owner_off": False})
check("after reconnecting, a real drop is repaired again", "reconnect" in acts, acts)
# The links' uptime history (linkhistory.py): five-minute slots, the worst state in each,
# gaps as no data, the clock going back, 72 days kept; the hour and day buckets the page draws.
import os, json, tempfile, time  # noqa: E402
from irate_box.hub import linkhistory as LH  # noqa: E402
os.environ["TZ"] = "UTC"; time.tzset()
D0 = 1791331200  # 2026-10-07 00:00 UTC
h = {}
LH.record(h, "wlan0", "u", D0 + 10); LH.record(h, "wlan0", "g", D0 + 100); LH.record(h, "wlan0", "u", D0 + 200)
check("history: a slot holds the worst state seen in it", h["ifaces"]["wlan0"]["s"] == "g", h)
LH.record(h, "wlan0", "u", D0 + 300 * 3 + 5)
check("  a gap is no data", h["ifaces"]["wlan0"]["s"] == "g..u", h["ifaces"]["wlan0"]["s"])
check("  the clock gone back: the sample dropped, the slots ahead stand", not LH.record(h, "wlan0", "d", D0 + 300) and h["ifaces"]["wlan0"]["s"] == "g..u")
check("  an unknown state is not recorded", not LH.record(h, "wlan0", "x", D0 + 1500) and not LH.record(h, "wlan0", ".", D0 + 1500))
for k in range(4, LH.KEEP + 11):
    LH.record(h, "wlan0", "u", D0 + 300 * k)
e = h["ifaces"]["wlan0"]
check("  72 days kept, the oldest dropped", len(e["s"]) == LH.KEEP and e["first"] + len(e["s"]) - 1 == (D0 + 300 * (LH.KEEP + 10)) // 300, (len(e["s"]), e["first"]))
LH.record(h, "wlan0", "u", D0 + 300 * (3 * LH.KEEP))
check("  a jump past 72 days starts again", h["ifaces"]["wlan0"]["s"] == "u")
# The last 72 hours: nothing for its first two hours, then up throughout, except a 15-minute
# outage in hour 10 and the owner switching it off for the whole of hour 20.
WIN = D0 - 71 * 3600   # the window's oldest hour starts here (hours[0])
h = {}
for slot_i in range(24, 71 * 12 + 6):   # hour index 2 onward; the window's last hour stops 30 min in
    t = WIN + slot_i * 300 + 30
    hr = slot_i // 12
    st = "o" if hr == 20 else "d" if hr == 10 and 2 <= slot_i % 12 < 5 else "u"
    LH.record(h, "wlan0", st, t, "uplink")
now = WIN + 71 * 3600 + 1800   # half an hour into the current (72nd) hour
out = LH.summarize(h, now)["wlan0"]
check("summary: 72 hours, 72 days, in local time", len(out["hours"]) == 72 and len(out["hour_cols"]) == 72
      and len(out["hour_full"]) == 72 and len(out["month"]) == 72, (len(out["hours"]), len(out["month"])))
check("  the outage's hour: up 9 of 12, one drop", out["hours"][10] == {"up": 0.75, "n": 12, "drops": 1, "off": False}, out["hours"][10])
check("  the owner's hour off: not up, not a drop, marked off", out["hours"][20]["up"] == 0 and out["hours"][20]["drops"] == 0 and out["hours"][20]["off"])
check("  before the record began: no data", out["hours"][0] is None and out["hours"][1] is None)
check("  the current (partial) hour: fewer samples", out["hours"][71] is not None and out["hours"][71]["n"] == 6, out["hours"][71])
sm = out["summary"]
check("  in words' worth: the share up, one drop, the longest outage and when", sm["drops"] == 1
      and sm["longest"] == {"minutes": 15, "at": WIN + 10 * 3600 + 2 * 300} and 0.9 < sm["up"] < 1, sm)
check("  a day with nothing recorded: no data", out["month"][0].get("up") is None and "date" in out["month"][0])
# The watchdog's recorder: written once a slot closes, never while the clock is not trusted.
U.HISTORY = Path(tempfile.mkdtemp()) / "uplink-history.json"
import irate_box.root.safeio as SIO  # noqa: E402
SIO.write = lambda path, text, mode=0o644: Path(path).write_text(text)
trust = {"ok": False}
rec = U.History(trusted=lambda: trust["ok"])
rec.note(D0, "wlan0", "u", {"eth0": "d"})
rec.note(D0 + 400, "wlan0", "u", {"eth0": "d"})
check("recorder: nothing while the clock is not trusted", not U.HISTORY.exists() and not rec.hist)
trust["ok"] = True
rec.note(D0 + 700, "wlan0", "u", {"eth0": "d"}); rec.note(D0 + 800, "wlan0", "g", {"eth0": "u"})
check("  kept in memory within a slot", not U.HISTORY.exists())
rec.note(D0 + 900, "wlan0", "u", {})
saved = json.loads(U.HISTORY.read_text())
check("  written as the slot closes: the uplink's full state, the others' link (down, then up: down)", saved["ifaces"]["wlan0"]["s"] == "g" and saved["ifaces"]["wlan0"]["kind"] == "uplink"
      and saved["ifaces"]["eth0"]["s"] == "d" and saved["ifaces"]["eth0"]["kind"] == "link", saved)
check("  the watched link's letter from its state", [U.history_state(x, l) for x, l in (("up", True), ("checking", True), ("down", True), ("down", False), ("off", False))]
      == ["u", "g", "g", "d", "o"])

# Roaming: by default it roams naturally between access points; ignoring short roams is a checkbox; the blip is 20 s.
check("roaming: roam naturally by default, no scans or a lock as choices; a lock needs a BSSID, kept only with lock", U.ROAMING == ("roam", "no-scan", "lock")
      and U.validate({})["roaming"] == "roam" and U.validate({"roaming": "lock", "lock_bssid": "AA:BB:CC:00:00:01"})["lock_bssid"] == "aa:bb:cc:00:00:01"
      and U.validate({"roaming": "roam", "lock_bssid": "aa:bb:cc:00:00:01"})["lock_bssid"] is None)
check("  ignoring short roams a checkbox apart, off by default; the first names read as these", U.validate({})["ignore_roams"] is False
      and (U.validate({"roaming": "ignore"})["roaming"], U.validate({"roaming": "ignore"})["ignore_roams"]) == ("roam", True)
      and (U.validate({"roaming": "count"})["roaming"], U.validate({"roaming": "count"})["ignore_roams"]) == ("roam", False)
      and U.validate({"roaming": "lock", "lock_bssid": "aa:bb:cc:00:00:01", "ignore_roams": True})["ignore_roams"] is True)
for bad in ({"roaming": "never"}, {"roaming": "lock"}, {"roaming": "lock", "lock_bssid": "aa:bb"}, {"overrides": {"blip": 300}}, {"ignore_roams": "yes"}):
    try:
        U.validate(bad); check(f"  {bad} refused", False)
    except ValueError:
        check(f"  {bad} refused", True)
check("  the blip 20 s unless set, and said in the settings' words", U.effective(C("gentle/reboot"))["blip"] == 20
      and "roams not counted" in U.words(U.validate({"ignore_roams": True})) and "locked to aa:bb:cc:00:00:01" in U.words(U.validate({"roaming": "lock", "lock_bssid": "aa:bb:cc:00:00:01"})))
# The drop watcher keeps when the link came back; a drop still down and younger than the blip waits.
dw = U.DropWatcher.__new__(U.DropWatcher)
import threading
dw.lock, dw.drops = threading.Lock(), [[100.0, 104.0], [150.0, None], [190.0, None]]
got = dw.take(now=200, blip=20)
check("drops taken: the one back, and the one down past the blip; the young one waits", got == [(100.0, 104.0), (150.0, None)] and dw.drops == [[190.0, None]], got)
eff_i, eff_c = U.effective(C("gentle/reboot", ignore_roams=True)), U.effective(C("gentle/reboot"))
pairs = [(100.0, 104.0), (300.0, 360.0), (500.0, None)]
check("sorted: back within 20 s on the same network is a roam; ignored, it is no miss; counted, it is", U.sort_drops(pairs, eff_i, True) == ([300.0, 500.0], [(100.0, 104.0)])
      and U.sort_drops(pairs, eff_c, True) == ([100.0, 300.0, 500.0], [(100.0, 104.0)]))
check("  back on another network: not a roam, a miss", U.sort_drops(pairs, eff_i, False)[0] == [100.0, 300.0, 500.0])
def roaming_night(ignore, every=300, hours=3, gw_fail_at=()):
    """A roam every `every` s (a 4 s blip), gentle pace, sensitivity 3; the gateway failing at the given checks."""
    eff = U.effective(C("gentle/reboot", ignore_roams=ignore))
    w = U.Watch(eff)
    acts = []
    for t in range(0, hours * 3600, 120):
        pairs = [(float(b), b + 4.0) for b in range(t - 120 + 7, t, every) if b > 0 and (b - 7) % every == 0]
        drops, _ = U.sort_drops(pairs, eff, True)
        acts += [(t, a) for a in w.tick(1000 + t, {"link": True, "gateway": t not in gw_fail_at, "drops": [1000 + d for d in drops], "can": ALL})]
    return acts, w
a, w = roaming_night(True)
check("roams ignored: a roam every 5 min, gentle pace, sensitivity 3: never on the ladder", a == [] and w.outage is None and not w.misses, a[:3])
a, w = roaming_night(False)
check("roams counted (the default): the same roams put it on the ladder as flapping, a reconnect locked to the strongest", any(x == "pin" or x == "reconnect" for _, x in a)
      and any(e["kind"] == "down" and "flaps" in e["text"] for e in w.events), a[:3])
a, w = roaming_night(True, gw_fail_at=(1200, 1320, 1440))
check("  ignore still counts failed gateway checks: three in the window, on the ladder", any(e["kind"] == "down" and "gateway" in e["text"] for e in w.events),
      [e["text"] for e in w.events])
eff = U.effective(C("gentle/reboot", ignore_roams=True))
check("  and a 2-minute loss is a miss whatever the choice", U.sort_drops([(100.0, 220.0)], eff, True) == ([100.0], []))
# Roams made visible: a short drop and back, or a new access point between checks.
r = U.Roams()
r.see(1000, {"bssid": "aa:00:00:00:00:01", "channel": 6}, [])
rows = r.see(1300, {"bssid": "aa:00:00:00:00:02", "channel": 11}, [(1290.0, 1294.0)])
check("roams noted: one row per roam, with where it went", rows == [{"at": 1294.0, "k": "roam", "d": 4.0, "b": "aa:00:00:00:00:02", "c": 11}], rows)
rows = r.see(1600, {"bssid": "aa:00:00:00:00:01", "channel": 6}, [])
check("  a new access point with no drop seen is a roam too", rows == [{"at": 1600, "k": "roam", "d": 0, "b": "aa:00:00:00:00:01", "c": 6}] and r.hour() == 2, rows)
r.see(5300, {"bssid": "aa:00:00:00:00:01", "channel": 6}, [])
check("  the last hour's count forgets older ones", r.hour() == 0)

# The watchdog's start on the ladder: its last check before it stopped, so the chart can end an outage
# a freeze or a reboot left open, and draw the time between as no record.
L3 = U.Ladder(Path(_tf2.mkdtemp()) / "uplink-ladder.json")
L3.started(10000, 8000.04, 120)
L3.started(20000, None, 5000)
L3.started(30000, 31000, 5000)
rows3 = __import__("json").loads(L3.path.read_text())
check("a start: its last check, and whether the box had just started", rows3[0] == {"at": 10000, "k": "start", "t": "Watching again after the box started.", "last": 8000.0}, rows3[0])
check("  the watchdog restarted alone; no last check known, none said", rows3[1] == {"at": 20000, "k": "start", "t": "Watching again (the watchdog restarted)."}, rows3[1])
check("  a last check after the start (the clock stepped) left out", "last" not in rows3[2], rows3[2])
H = __import__("irate_box.hub.linkhistory", fromlist=["gaps"])
S = 300
hist = {"slot": S, "ifaces": {"wlan0": {"first": 100, "s": "uuu....uug", "kind": "uplink"}, "ap0": {"first": 102, "s": "dd", "kind": "link"}}}
check("no record: the slots no link was recorded in", H.gaps(hist, 110 * S) == [[104 * S, 107 * S]], H.gaps(hist, 110 * S))
check("  the slots after the last: not while it may still be writing", H.gaps(hist, 111 * S) == [[104 * S, 107 * S]])
check("  but once two slots have passed, up to now", H.gaps(hist, 113 * S + 7) == [[104 * S, 107 * S], [110 * S, 113 * S + 7]], H.gaps(hist, 113 * S + 7))
check("  nothing recorded: no gaps", H.gaps({}, 1000) == [] and H.gaps(None, 1000) == [])

# Steps switched off per connection: kept as the owner chose them, applied to the connection watched.
c = U.validate({"pace": "steady", "steps_off": {"wlan0": ["restart", "restart"], "eth0": []}})
check("steps_off: kept per connection, in the ladder's order, empty ones dropped", c["steps_off"] == {"wlan0": ["restart"]}, c["steps_off"])
check("  the WiFi's ladder without the restart; a wired link's whole", sorted(U.effective(c, "wlan0")["steps"]) == ["radio", "reboot", "reconnect"]
      and "restart" in U.effective(c, "eth0")["steps"] and "restart" in U.effective(c)["steps"])
check("  said in words", "on wlan0 never restart the network service" in U.words(c), U.words(c))
for bad in ({"steps_off": {"auto": ["restart"]}}, {"steps_off": {"wlan0": ["nap"]}}, {"steps_off": {"-x": ["radio"]}}, {"steps_off": ["wlan0"]}):
    try:
        U.validate(bad); check(f"  refused: {bad}", False)
    except ValueError:
        check(f"  refused: {bad}", True)
c2 = U.validate({"pace": "steady", "steps_off": {"wlan0": ["reconnect"]}})
check("  no reconnect on a connection: no repeats either", U.effective(c2, "wlan0")["repeat"] == 0)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
