# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The uplink watchdog's decisions against a simulated clock: every level, flapping, the guards,
overrides. No network is touched. python3 tests/sim_uplink.py"""
import sys
from pathlib import Path
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
from irate_box.hub import uplink as U  # noqa: E402

ALL = {"reconnect", "restart", "radio", "reboot", "pin"}
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += (not cond)

# The levels by name in these tests: pace/reach (and guests). The old single dial's names map to
# these where a case is about the same thing (patient: reconnect only; stubborn: everything, guests or not).
LV = {"off": ("gentle", "watch", "protect"), "patient": ("gentle", "reconnect", "protect"), "standard": ("steady", "restart", "protect"),
      "persistent": ("prompt", "radio", "protect"), "stubborn": ("urgent", "reboot", "ignore")}
def C(e, f="normal", over=None, **kw):
    pace, reach, guests = LV[e] if e in LV else (*e.split("/"), "protect")
    return dict({"pace": pace, "reach": reach, "guests": guests, "forgiveness": f, "overrides": over or {}}, **kw)

def outage(e, f, dur, link=False, guests=0, busy=None, uptime=1e9, can=ALL, over=None, step=5):
    """Link down (or gateway silent) for dur seconds, observed every `step` s; returns [(t, action)]."""
    w = U.Watch(U.effective(C(e, f, over)))
    acts = []
    for t in range(0, dur, step):
        obs = {"link": link, "gateway": False if link else None, "drops": [], "guests": guests, "busy": busy,
               "uptime": uptime + t, "can": can}
        for a in w.tick(1000 + t, obs):
            acts.append((t, a))
    return acts, w

# 1. Ladder timing, link lost (certain: no misses needed), normal forgiveness (grace 60)
a, w = outage("patient", "normal", 3600)
check("gentle pace: the first reconnect 5 min after the grace (60 + 300 s)", a[0] == (360, "reconnect"), a[:3])
check("reach reconnect: only reconnects", {x for _, x in a} == {"reconnect"}, a)
gaps = [a[i+1][0]-a[i][0] for i in range(len(a)-1)]
check("gentle: reconnects back off 900→1800→…", gaps[:2] == [900, 1800], gaps)
a, w = outage("gentle/reboot", "normal", 8000)
check("gentle pace, reach reboot (the defaults): restart at +30 min, radio +60, reboot +120, all after the grace",
      [x for x in a if x[1] != "reconnect"] == [(1860, "restart"), (3660, "radio"), (7260, "reboot")], [x for x in a if x[1] != "reconnect"])
check("the defaults are the gentlest pace and the highest reach (Tom)", U.DEFAULT["pace"] == "gentle" and U.DEFAULT["reach"] == "reboot"
      and U.load_settings()["reach"] == "reboot")

a, w = outage("standard", "normal", 3600)
check("steady pace: reconnect at 60+60, restart at 60+600", a[0] == (120, "reconnect") and (660, "restart") in a, a)
check("reach restart: no radio/reboot", not ({"radio","reboot"} & {x for _, x in a}), a)

a, w = outage("persistent", "normal", 3600)
check("persistent: restart 360, radio 960", (360, "restart") in a and (960, "radio") in a, a)

a, w = outage("stubborn", "normal", 7200)
check("stubborn: reboot at 60+1800", (1860, "reboot") in a, [x for x in a if x[1] != "reconnect"])

# 2. Gateway silent (link up): misses before declaring
a, w = outage("standard", "strict", 600, link=True, step=30)
check("strict, gateway silent: 2 misses then +15 s grace, then steady's minute → reconnect by 2 min", a and a[0][1] == "reconnect" and a[0][0] <= 120, a[:2])
a, w = outage("standard", "tolerant", 1200, link=True, step=60)
check("tolerant, gateway silent: no action before 5 min down (misses overlap the grace)", a and a[0][0] >= 300, a[:2])

# 3. A blip shorter than the misses is forgiven and not logged
w = U.Watch(U.effective(C("standard", "normal", {})))
for t, ok in [(0, False), (60, False), (120, True)]:
    w.tick(t, {"link": True, "gateway": ok, "drops": [], "can": ALL})
check("normal: 2 missed checks then fine → no outage, no events", w.outage is None and not w.events, list(w.events))

# 4. Flaps
def flap(f, can=ALL, hold=0):
    w = U.Watch(U.effective(C("standard", f, {})))
    acts = []
    for i in range(10):
        acts += w.tick(1000 + i * 60, {"link": True, "gateway": True, "drops": [1000 + i * 60 - 1], "can": can, "hold_until": hold})
    return acts, w
a, w = flap("normal"); check("normal: 4 drops in 10 min → pin, once per window", a == ["pin"], a)
a, w = flap("normal", can=ALL - {"pin"}); check("normal without NM → noted", a == [] and "noted" in w.events[-1]["text"], list(w.events))
a, w = flap("strict"); check("strict: 3 drops → restart (rate-limited)", a == ["restart"], a)
a, w = flap("tolerant"); check("tolerant: 8 drops in 30 min → noted only", a == [] and any(e["kind"] == "flap" for e in w.events), list(w.events))
a, w = flap("normal", hold=1e12); check("hold: flap noted, no pin", a == [], a)

# 5. Guards
a, w = outage("persistent", "normal", 3600, guests=2)
check("persistent + guests: radio held, logged once", "radio" not in {x for _, x in a} and sum(e["kind"] == "held" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"]=="held"])
a, w = outage("stubborn", "normal", 7200, guests=2)
check("stubborn + guests: radio and reboot go ahead", {"radio", "reboot"} <= {x for _, x in a}, a)
a, w = outage("stubborn", "normal", 7200, busy="a build")
check("stubborn + build running: reboot held", "reboot" not in {x for _, x in a} and any("a build" in e["text"] for e in w.events))
a, w = outage("stubborn", "normal", 7200, uptime=0)
check("stubborn soon after boot: reboot only after reboot_gap uptime", (1860, "reboot") not in a and any(x == "reboot" for _, x in a), [x for x in a if x[1]=="reboot"])
w = U.Watch(U.effective(C("stubborn", "normal", {})), reboots=[0, 10000, 20000])
acts = []
for t in range(30000, 40000, 5):
    acts += w.tick(t, {"link": False, "drops": [], "can": ALL})
check("stubborn: daily cap of 3 reboots", "reboot" not in acts and any("3 times today" in e["text"] for e in w.events))
a, w = outage("persistent", "normal", 3600, can={"reconnect", "reboot"})
check("radio not possible → skipped, logged", "radio" not in {x for _, x in a} and any(e["kind"] == "skip" for e in w.events))

# 6. Overrides
a, w = outage("persistent", "normal", 3600, over={"grace": 0, "steps": {"restart": 120, "radio": 300}, "repeat": 0})
check("custom: grace 0, restart 120, radio 300, no repeats", a == [(0, "reconnect"), (120, "restart"), (300, "radio")], a)
a, w = outage("standard", "normal", 3600, over={"steps": {"radio": 300}})
check("custom: a step's time set beyond the reach stays beyond it", "radio" not in {x for _, x in a}, a)
a, w = outage("stubborn", "normal", 7200, over={"steps": {"reboot": None}})
check("custom: stubborn without reboot", "reboot" not in {x for _, x in a}, a)

# 7. Recovery logs duration and steps; next outage starts afresh
w = U.Watch(U.effective(C("standard", "normal", {})))
for t in range(0, 700, 5): w.tick(t, {"link": False, "drops": [], "can": ALL})
w.tick(700, {"link": True, "gateway": True, "drops": [], "can": ALL})
check("recovery logged with what was tried", w.events[-1]["kind"] == "up" and "restart" in w.events[-1]["text"], w.events[-1])
check("outage cleared", w.outage is None and w.misses == 0)

# 7b. The night of 2026-10-09 on the Lyra (uplink-ladder-plan, stage 1), replayed: the link lost at 0–2,
# 3.5–5 and 11–22 min with NetworkManager there; then NM's restart left no backend (only a radio reset
# or a reboot possible) and the link stayed down for 5 hours.
def night(e="standard", f="normal", over=None, step=30, hours=5):
    w = U.Watch(U.effective(C(e, f, over)))
    acts, down = [], [(0, 120), (210, 300), (660, 1320)]
    for t in range(0, 1320 + hours * 3600, step):
        lost = t >= 1320 or any(a <= t < b for a, b in down)
        can = ALL if t < 1320 else {"radio", "reboot"}
        for a in w.tick(t, {"link": not lost, "gateway": True if not lost else None, "drops": [], "can": can}):
            acts.append((t, a))
    return acts, w
a, w = night()
# Stage 3, memory across outages: was a reconnect at 1.0, 4.5 and 12.0 min (the ladder starting afresh
# each outage; the third wedged the firmware). Now the three outages are one episode: one reconnect,
# nothing on the quick relapse (restart is not due yet), and the restart at 12 min, as one long outage would.
check("night: one reconnect in the episode, then the restart when due, not reconnect again", a[:2] == [(270, "reconnect"), (720, "restart")]
      and [x for _, x in a].count("reconnect") == 1, a[:5])
check("  the relapse said, and the episode kept", any("did not hold" in e["text"] for e in w.events) and w.episode["outages"] == 3
      and w.episode["failed"] == ["reconnect"], (w.episode, [e["text"] for e in w.events][:8]))
# Stage 2, honest stalls: nothing it may do after NM's restart, and it says so, rather than "next: reconnect".
end = 1320 + 5 * 3600
check("  after NM's restart, no next step: none it may take is possible", not [x for t, x in a if t >= 1320] and w.next_step(end) is None,
      (a[-3:], w.next_step(end)))
st = w.stall(end)
check("  stalled, needing a radio reset, which reach restart does not go to", st and st["needs"] == "radio"
      and st["text"] == "Stalled: what could help now is to reset the radio, and the reach set goes no further than to restart the network service.", st)
a, w = night("gentle/reboot")
check("night with the defaults (gentle, reach reboot): nothing on the two short outages, one reconnect at 12 min, the restart"
      " skipped (NM gone), the radio reset at 61 min, a reboot at 121 if that fails: not 5 hours stalled",
      a == [(720, "reconnect"), (3660, "radio"), (7260, "reboot")] and any(e["text"] == "Cannot restart the network service here; skipped." for e in w.events), a)
check("  said once in the log", sum(e["kind"] == "stalled" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"] == "stalled"])

# Stalls: not while a step is only held, not for watch-only, nothing possible at all said so.
a, w = outage("persistent", "normal", 3600, guests=2, can={"radio", "reboot"})
check("stall: a radio reset held for guests is held, not a stall", w.stall(1000 + 3600) is None and w.next_step(1000 + 3600)["step"] == "radio")
a, w = outage("off", "normal", 3600)
check("stall: watch-only is the owner's choice, never a stall", w.stall(1000 + 3600) is None and not any(e["kind"] == "stalled" for e in w.events))
a, w = outage("standard", "normal", 3600, can=set())
check("stall: nothing possible at all", w.stall(1000 + 3600)["needs"] is None and "nothing the box can do" in w.stall(1000 + 3600)["text"])

# A step asked for on /admin: done now whatever the level and a hold, but only if possible and not held.
w = U.Watch(U.effective(C("standard", "normal", {})))
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

# 7c. Episodes (stage 3): relapses climb within reach, a single outage is as before, an episode ends.
def relapses(e, downs, can=ALL, total=None, step=10):
    w = U.Watch(U.effective(C(e, "normal", {})))
    acts = []
    for t in range(0, total or downs[-1][1] + 1200, step):
        lost = any(a <= t < b for a, b in downs)
        for x in w.tick(t, {"link": not lost, "gateway": True if not lost else None, "drops": [], "can": can}):
            acts.append((t, x))
    return acts, w
a, w = relapses("persistent", [(0, 120), (360, 480), (960, 1080)])
check("episode: three relapses climb reconnect → restart → radio (persistent: restart +5 min, radio +15 min)",
      a == [(60, "reconnect"), (420, "restart"), (1020, "radio")], a)
check("  each relapse names what did not hold", [e["text"].split(":")[1].split(",")[0].strip() for e in w.events if "did not hold" in e["text"]]
      == ["reconnect did not hold", "restart the network service did not hold"], [e["text"] for e in w.events if "did not hold" in e["text"]])
check("  steady again: the episode ends after `relapse` (15 min) up, said with what was tried",
      w.episode is None and "Steady again after an episode of 3 outages (tried: reconnect, restart the network service, reset the radio)." in [e["text"] for e in w.events],
      [e["text"] for e in w.events][-3:])
a, w = relapses("persistent", [(0, 120), (2000, 2120)])
check("episode: an outage more than 15 min after the last is a new episode, from the bottom", [x for _, x in a] == ["reconnect", "reconnect"], a)
a, w = relapses("patient", [(0, 120), (300, 420), (600, 720)])
check("episode: with nothing heavier to climb to (reach reconnect), reconnect is tried again", a == [(360, "reconnect"), (660, "reconnect")], a)
a1, _ = outage("standard", "normal", 3600)
check("episode: a single outage is as it was (reconnect, back-off, restart at +10 min)", (660, "restart") in a1 and a1[0] == (120, "reconnect"), a1[:3])
w = U.Watch(U.effective(C("persistent", "strict", {})))
acts = []
for k in range(3):  # three rounds of flapping, each repaired, within the relapse window
    for i in range(4):
        tt = 1000 + k * 700 + i * 60
        acts += w.tick(tt, {"link": True, "gateway": True, "drops": [tt - 1], "can": ALL})
check("flapping, strict: repaired as an episode, climbing (restart, then radio) and staying at the top", acts == ["restart", "radio", "radio"], acts)

# 7d. A wedged driver (stage 4): the evidence, from the Lyra's own journal lines, and what is done with it.
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
    w = U.Watch(U.effective(C(e, "normal", on_wedge=on_wedge)))
    acts = []
    for t in range(0, 3600, 30):
        obs = {"link": False, "drops": [], "can": ALL if t < 600 else {"radio", "reboot"}, "guests": guests,
               "wedged": "scans keep failing as busy (32 in 2 minutes)" if t >= 300 else None}
        acts += [(t, x) for x in w.tick(t, obs)]
    return acts, w
a, w = wedged_night("persistent", "radio")
check("on_wedge radio, the level reaching it: straight to the radio reset when the evidence comes (not restart at 6 min, radio at 16)",
      a == [(60, "reconnect"), (240, "reconnect"), (300, "radio")] and any("can't mend a wedged driver" in e["text"] for e in w.events), a)
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

# 7e. Stage 5: two dials, the restart guard on a shared radio, settings from before carried over.
def shared(guests, sharing, ignore=False):
    w = U.Watch(U.effective(C("prompt/radio", guests="ignore" if ignore else "protect")))
    acts = []
    for t in range(0, 1200, 10):
        acts += [(t, x) for x in w.tick(t, {"link": False, "drops": [], "can": ALL, "guests": guests, "shared_radio": sharing})]
    return acts, w
a, w = shared(2, True)
check("D3: guests on a hotspot that shares the radio: the restart held too, said why", "restart" not in [x for _, x in a]
      and any("2 guests are on the hotspot, which shares the radio" in e["text"] for e in w.events), [e["text"] for e in w.events if e["kind"] == "held"])
a, w = shared(2, False)
check("  a hotspot on a radio of its own: the restart goes ahead (the radio reset is held)", (360, "restart") in a and "radio" not in [x for _, x in a], a)
a, w = shared(0, True)
check("  no guests: the restart goes ahead", (360, "restart") in a, a)
a, w = shared(2, True, ignore=True)
check("  guests ignored by the owner: everything goes ahead", {"restart", "radio"} <= {x for _, x in a}, a)
v = U.validate({"eagerness": "standard", "forgiveness": "strict", "overrides": {"guests": "ignore", "check": 45}})
check("settings from before carried over: eagerness as pace and reach, an overridden guests as the setting",
      (v["pace"], v["reach"], v["guests"], v["forgiveness"], v["overrides"]) == ("steady", "reboot", "ignore", "strict", {"check": 45}), v)
check("  stubborn: urgent, reboot, guests or not; off: watch", U.validate({"eagerness": "stubborn"})["guests"] == "ignore"
      and U.validate({"eagerness": "off"})["reach"] == "watch")
check("  watch: no steps at all, no repeats", U.effective(C("gentle/watch"))["steps"] == {} and U.effective(C("gentle/watch"))["repeat"] == 0)
for bad in [{"pace": "fast"}, {"reach": "nuke"}, {"guests": "maybe"}]:
    try: U.validate(bad); check(f"validate rejects {bad}", False)
    except ValueError: check(f"validate rejects {bad}", True)
check("the settings in words", U.words(U.validate({"pace": "steady", "reach": "radio", "guests": "ignore", "on_wedge": "radio"}))
      == "steady pace, reach radio, normal, guests or not, a wedged radio reset at once")

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
check("the record: compact rows, the interface, the text kept short", rows[0] == {"at": 0, "k": "down", "i": "wlan0", "t": "Link lost."}
      and any(r.get("s") == "radio" and r.get("b") == "auto" for r in rows), rows[:3])
L.add([{"at": 100000 + 73 * 86400, "kind": "down", "text": "Link lost."}], "wlan0", 100000 + 73 * 86400)
check("  72 days kept, older ones dropped", [r["at"] for r in __import__("json").loads(L.path.read_text())] == [100000 + 73 * 86400])
check("  nothing to add: nothing written", L.add([], "wlan0", 0) is None)

# 8. validate()
for bad in [{"eagerness": "max"}, {"overrides": {"check": 5}}, {"overrides": {"rm": 1}}, {"iface": "a;b"},
            {"overrides": {"steps": {"nuke": 1}}}, {"overrides": {"flap_action": "x"}}]:
    try: U.validate(bad); check(f"validate rejects {bad}", False)
    except ValueError: check(f"validate rejects {bad}", True)
v = U.validate({"eagerness": "stubborn", "forgiveness": "strict", "overrides": {"check": "45", "steps": {"reboot": None}}})
check("validate accepts and coerces", v["overrides"] == {"check": 45, "steps": {"reboot": None}}, v)

# 9. Disconnected by hand: no repairs, logged once; connected again → normal
w = U.Watch(U.effective(C("stubborn", "strict", {})))
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
# The links' uptime history (step 34, linkhistory.py): five-minute slots, the worst state in each,
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
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
