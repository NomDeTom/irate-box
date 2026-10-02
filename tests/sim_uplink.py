# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The uplink watchdog's decisions against a simulated clock: every level, flapping, the guards,
overrides. No network is touched. python3 tests/sim_uplink.py"""
import sys
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)
from irate_box.hub import uplink as U  # noqa: E402

ALL = {"reconnect", "restart", "radio", "reboot", "pin"}
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += (not cond)

def outage(e, f, dur, link=False, guests=0, busy=None, uptime=1e9, can=ALL, over=None, step=5):
    """Link down (or gateway silent) for dur seconds, observed every `step` s; returns [(t, action)]."""
    w = U.Watch(U.effective({"eagerness": e, "forgiveness": f, "overrides": over or {}}))
    acts = []
    for t in range(0, dur, step):
        obs = {"link": link, "gateway": False if link else None, "drops": [], "guests": guests, "busy": busy,
               "uptime": uptime + t, "can": can}
        for a in w.tick(1000 + t, obs):
            acts.append((t, a))
    return acts, w

# 1. Ladder timing, link lost (certain: no misses needed), normal forgiveness (grace 60)
a, w = outage("patient", "normal", 3600)
check("patient: first reconnect at grace (60 s)", a[0] == (60, "reconnect"), a[:3])
check("patient: only reconnects", {x for _, x in a} == {"reconnect"}, a)
gaps = [a[i+1][0]-a[i][0] for i in range(len(a)-1)]
check("patient: reconnects back off 600→1200→…", gaps[:2] == [600, 1200], gaps)

a, w = outage("standard", "normal", 3600)
check("standard: restart at 60+600", (660, "restart") in a, a)
check("standard: no radio/reboot", not ({"radio","reboot"} & {x for _, x in a}), a)

a, w = outage("persistent", "normal", 3600)
check("persistent: restart 360, radio 960", (360, "restart") in a and (960, "radio") in a, a)

a, w = outage("stubborn", "normal", 7200)
check("stubborn: reboot at 60+1800", (1860, "reboot") in a, [x for x in a if x[1] != "reconnect"])

# 2. Gateway silent (link up): misses before declaring
a, w = outage("standard", "strict", 600, link=True, step=30)
check("strict, gateway silent: 2 misses then +15 s grace → reconnect at t=30+…", a and a[0][1] == "reconnect" and a[0][0] <= 60, a[:2])
a, w = outage("standard", "tolerant", 1200, link=True, step=60)
check("tolerant, gateway silent: no action before 5 min down (misses overlap the grace)", a and a[0][0] >= 300, a[:2])

# 3. A blip shorter than the misses is forgiven and not logged
w = U.Watch(U.effective({"eagerness": "standard", "forgiveness": "normal", "overrides": {}}))
for t, ok in [(0, False), (60, False), (120, True)]:
    w.tick(t, {"link": True, "gateway": ok, "drops": [], "can": ALL})
check("normal: 2 missed checks then fine → no outage, no events", w.outage is None and not w.events, list(w.events))

# 4. Flaps
def flap(f, can=ALL, hold=0):
    w = U.Watch(U.effective({"eagerness": "standard", "forgiveness": f, "overrides": {}}))
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
w = U.Watch(U.effective({"eagerness": "stubborn", "forgiveness": "normal", "overrides": {}}), reboots=[0, 10000, 20000])
acts = []
for t in range(30000, 40000, 5):
    acts += w.tick(t, {"link": False, "drops": [], "can": ALL})
check("stubborn: daily cap of 3 reboots", "reboot" not in acts and any("3 times today" in e["text"] for e in w.events))
a, w = outage("persistent", "normal", 3600, can={"reconnect", "reboot"})
check("radio not possible → skipped, logged", "radio" not in {x for _, x in a} and any(e["kind"] == "skip" for e in w.events))

# 6. Overrides
a, w = outage("standard", "normal", 3600, over={"grace": 0, "steps": {"restart": 120, "radio": 300}, "repeat": 0})
check("custom: grace 0, restart 120, radio 300, no repeats", a == [(0, "reconnect"), (120, "restart"), (300, "radio")], a)
a, w = outage("stubborn", "normal", 7200, over={"steps": {"reboot": None}})
check("custom: stubborn without reboot", "reboot" not in {x for _, x in a}, a)

# 7. Recovery logs duration and steps; next outage starts afresh
w = U.Watch(U.effective({"eagerness": "standard", "forgiveness": "normal", "overrides": {}}))
for t in range(0, 700, 5): w.tick(t, {"link": False, "drops": [], "can": ALL})
w.tick(700, {"link": True, "gateway": True, "drops": [], "can": ALL})
check("recovery logged with what was tried", w.events[-1]["kind"] == "up" and "restart" in w.events[-1]["text"], w.events[-1])
check("outage cleared", w.outage is None and w.misses == 0)

# 8. validate()
for bad in [{"eagerness": "max"}, {"overrides": {"check": 5}}, {"overrides": {"rm": 1}}, {"iface": "a;b"},
            {"overrides": {"steps": {"nuke": 1}}}, {"overrides": {"flap_action": "x"}}]:
    try: U.validate(bad); check(f"validate rejects {bad}", False)
    except ValueError: check(f"validate rejects {bad}", True)
v = U.validate({"eagerness": "stubborn", "forgiveness": "strict", "overrides": {"check": "45", "steps": {"reboot": None}}})
check("validate accepts and coerces", v["overrides"] == {"check": 45, "steps": {"reboot": None}}, v)

# 9. Disconnected by hand: no repairs, logged once; connected again → normal
w = U.Watch(U.effective({"eagerness": "stubborn", "forgiveness": "strict", "overrides": {}}))
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
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
