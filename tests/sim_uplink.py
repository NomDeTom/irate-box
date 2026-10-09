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

# 7b. The night of 2026-10-09 on the Lyra (uplink-ladder-plan, stage 1), replayed: the link lost at 0–2,
# 3.5–5 and 11–22 min with NetworkManager there; then NM's restart left no backend (only a radio reset
# or a reboot possible) and the link stayed down for 5 hours.
def night(e="standard", f="normal", over=None, step=30, hours=5):
    w = U.Watch(U.effective({"eagerness": e, "forgiveness": f, "overrides": over or {}}))
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
check("night: one reconnect in the episode, then the restart when due, not reconnect again", a[:2] == [(60, "reconnect"), (720, "restart")]
      and [x for _, x in a].count("reconnect") == 1, a[:5])
check("  the relapse said, and the episode kept", any("did not hold" in e["text"] for e in w.events) and w.episode["outages"] == 3
      and w.episode["failed"] == ["reconnect"], (w.episode, [e["text"] for e in w.events][:8]))
# Stage 2, honest stalls: nothing it may do after NM's restart, and it says so, rather than "next: reconnect".
end = 1320 + 5 * 3600
check("  after NM's restart, no next step: none it may take is possible", not [x for t, x in a if t >= 1320] and w.next_step(end) is None,
      (a[-3:], w.next_step(end)))
st = w.stall(end)
check("  stalled, needing a radio reset, which standard does not go to", st and st["needs"] == "radio"
      and st["text"] == "Stalled: what could help now is to reset the radio, and this level goes no further than to restart the network service.", st)
check("  said once in the log", sum(e["kind"] == "stalled" for e in w.events) == 1, [e["text"] for e in w.events if e["kind"] == "stalled"])

# Stalls: not while a step is only held, not for watch-only, nothing possible at all said so.
a, w = outage("persistent", "normal", 3600, guests=2, can={"radio", "reboot"})
check("stall: a radio reset held for guests is held, not a stall", w.stall(1000 + 3600) is None and w.next_step(1000 + 3600)["step"] == "radio")
a, w = outage("off", "normal", 3600)
check("stall: watch-only is the owner's choice, never a stall", w.stall(1000 + 3600) is None and not any(e["kind"] == "stalled" for e in w.events))
a, w = outage("standard", "normal", 3600, can=set())
check("stall: nothing possible at all", w.stall(1000 + 3600)["needs"] is None and "nothing the box can do" in w.stall(1000 + 3600)["text"])

# A step asked for on /admin: done now whatever the level and a hold, but only if possible and not held.
w = U.Watch(U.effective({"eagerness": "standard", "forgiveness": "normal", "overrides": {}}))
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
    w = U.Watch(U.effective({"eagerness": e, "forgiveness": "normal", "overrides": {}}))
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
check("episode: with nothing heavier to climb to (reconnect only), reconnect is tried again", [x for _, x in a] == ["reconnect"] * 3, a)
a1, _ = outage("standard", "normal", 3600)
check("episode: a single outage is as it was (reconnect, back-off, restart at +10 min)", (660, "restart") in a1 and a1[0] == (60, "reconnect"))
w = U.Watch(U.effective({"eagerness": "persistent", "forgiveness": "strict", "overrides": {}}))
acts = []
for k in range(3):  # three rounds of flapping, each repaired, within the relapse window
    for i in range(4):
        tt = 1000 + k * 700 + i * 60
        acts += w.tick(tt, {"link": True, "gateway": True, "drops": [tt - 1], "can": ALL})
check("flapping, strict: repaired as an episode, climbing (restart, then radio) and staying at the top", acts == ["restart", "radio", "radio"], acts)

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
