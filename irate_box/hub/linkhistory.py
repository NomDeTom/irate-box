# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Each network link's history, for the uptime heatmaps on /admin → Network (next-work plan step
34, new-feature-input). The uplink watchdog (uplink.py, root) records; the hub reads and sums.

Five-minute slots, 35 days of them, one character each per interface, holding the worst state
seen in the slot:

  .  no data: nothing was recorded (the watchdog not running, or the clock not trusted)
  u  up: the link, and for the uplink the gateway answering
  o  off: the owner switched the link off (NetworkManager), not a fault
  g  no gateway: the link up but the gateway not answering (the uplink only)
  d  down: no link

A slot is only written while the clock is trusted (network time, or set by the owner: rtc.py),
so a box that boots with no clock does not put an outage on the wrong day. A sample earlier than
the last slot (the clock stepped back) is dropped; a gap is "no data".

The file, $HUB_STATE_DIR/control/uplink-history.json (root's, readable by the hub):
  {"slot": 300, "ifaces": {"wlan0": {"first": <slot number>, "s": "uuuug..d", "kind": "uplink"}}}
A slot number is the Unix time // 300. About 10 KB per interface. Stdlib only.
"""

import time

SLOT = 300
KEEP = 35 * 86400 // SLOT          # 10080 slots
RANK = {".": 0, "u": 1, "o": 2, "g": 3, "d": 4}
DOWN = ("g", "d")                  # what counts as an outage (off by the owner does not)


def worst(a, b):
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def record(hist, iface, state, now, kind="link"):
    """Add one look at a link to hist (in place). Returns whether it was kept."""
    if state not in RANK or state == ".":
        return False
    idx = int(now // SLOT)
    ifaces = hist.setdefault("ifaces", {})
    hist["slot"] = SLOT
    e = ifaces.get(iface)
    if not e or not e.get("s"):
        ifaces[iface] = {"first": idx, "s": state, "kind": kind}
        return True
    e["kind"] = kind
    last = e["first"] + len(e["s"]) - 1
    if idx < last:
        return False                   # the clock went back: the slots ahead stand
    if idx == last:
        e["s"] = e["s"][:-1] + worst(e["s"][-1], state)
    elif idx - last - 1 >= KEEP:
        e["first"], e["s"] = idx, state
    else:
        e["s"] += "." * (idx - last - 1) + state
    if len(e["s"]) > KEEP:
        cut = len(e["s"]) - KEEP
        e["first"] += cut
        e["s"] = e["s"][cut:]
    return True


def _span(e, start, end):
    """The states of slots [start, end) (slot numbers), '.' where nothing was recorded."""
    first, s = e["first"], e["s"]
    lo, hi = max(start, first), min(end, first + len(s))
    if lo >= hi:
        return "." * (end - start)
    return "." * (lo - start) + s[lo - first:hi - first] + "." * (end - hi)


def _bucket(states):
    """One cell: the share of slots with data that were up, how many had data, the drops (a slot
    up followed by one down), and whether the owner had it off."""
    seen = [c for c in states if c != "."]
    if not seen:
        return None
    drops = sum(1 for a, b in zip(states, states[1:]) if a == "u" and b in DOWN)
    return {"up": round(sum(c == "u" for c in seen) / len(seen), 3), "n": len(seen), "drops": drops,
            "off": "o" in seen}


def _midnight(t):
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def summarize(hist, now=None):
    """What the page draws, per interface: the last 7 days by hour, the last 35 by day, in the
    box's local time, and a line of words' worth of numbers."""
    now = time.time() if now is None else now
    out = {}
    today = _midnight(now)
    for iface, e in sorted((hist or {}).get("ifaces", {}).items()):
        if not isinstance(e, dict) or not isinstance(e.get("s"), str) or not isinstance(e.get("first"), int):
            continue
        days = []
        for back in range(34, -1, -1):
            start = _midnight(today - back * 86400 + 43200)   # noon, then its midnight: DST-proof
            end = _midnight(start + 36 * 3600)
            days.append((start, end))
        month = [dict(_bucket(_span(e, int(s // SLOT), int(t // SLOT))) or {}, date=time.strftime("%Y-%m-%d", time.localtime(s)))
                 for s, t in days]
        week = []
        for s, t in days[-7:]:
            hours = []
            for h in range(24):
                hs = s + h * 3600
                he = min(t, hs + 3600)
                hours.append(_bucket(_span(e, int(hs // SLOT), int(he // SLOT))) if hs < t else None)
            week.append({"date": time.strftime("%Y-%m-%d", time.localtime(s)), "label": time.strftime("%a %d", time.localtime(s)),
                         "hours": hours})
        span = _span(e, int((now - 7 * 86400) // SLOT), int(now // SLOT) + 1)
        seen = [c for c in span if c != "."]
        longest, run, run_start, at = 0, 0, 0, None
        for i, c in enumerate(span):
            if c in DOWN:
                run_start = i if run == 0 else run_start
                run += 1
                if run > longest:
                    longest, at = run, run_start
            elif c != ".":
                run = 0
        base = int((now - 7 * 86400) // SLOT)
        out[iface] = {"kind": e.get("kind", "link"), "week": week, "month": month, "summary": {
            "up": round(sum(c == "u" for c in seen) / len(seen), 4) if seen else None,
            "hours_seen": round(len(seen) * SLOT / 3600, 1),
            "drops": sum(1 for a, b in zip(span, span[1:]) if a == "u" and b in DOWN),
            "longest": {"minutes": longest * SLOT // 60, "at": (base + at) * SLOT} if longest else None}}
    return out
