# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Each network link's history, for the uptime heatmaps on /admin → Network. The uplink watchdog (uplink.py, root) records; the hub reads and sums.

Five-minute slots, 72 days of them, one character each per interface, holding the worst state
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
A slot number is the Unix time // 300. About 20 KB per interface. Stdlib only.
"""

import time

SLOT = 300
KEEP = 72 * 86400 // SLOT          # 20736 slots
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
    """What the page draws, per interface: the last 72 hours by hour, the last 72 days by day, in
    the box's local time (githubstatus.com's format), and a line of words' worth of numbers."""
    now = time.time() if now is None else now
    out = {}
    today = _midnight(now)
    anchor = int(now // 3600) * 3600          # the hour now in progress, started
    hour_at = lambda i: anchor - (71 - i) * 3600  # noqa: E731 - hours[i] covers [hour_at(i), hour_at(i)+3600)
    hour_cols = [time.strftime("%H:00", time.localtime(hour_at(i))) if i % 12 == 0 else "" for i in range(72)]
    hour_full = [time.strftime("%a %d %H:00", time.localtime(hour_at(i)))
                 + "–" + time.strftime("%H:00", time.localtime(hour_at(i) + 3600)) for i in range(72)]
    for iface, e in sorted((hist or {}).get("ifaces", {}).items()):
        if not isinstance(e, dict) or not isinstance(e.get("s"), str) or not isinstance(e.get("first"), int):
            continue
        days = []
        for back in range(71, -1, -1):
            start = _midnight(today - back * 86400 + 43200)   # noon, then its midnight: DST-proof
            end = _midnight(start + 36 * 3600)
            days.append((start, end))
        month = [dict(_bucket(_span(e, int(s // SLOT), int(t // SLOT))) or {}, date=time.strftime("%Y-%m-%d", time.localtime(s)))
                 for s, t in days]
        hours = [_bucket(_span(e, int(hour_at(i) // SLOT), int((hour_at(i) + 3600) // SLOT))) for i in range(72)]
        span = _span(e, int((now - 72 * 3600) // SLOT), int(now // SLOT) + 1)
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
        base = int((now - 72 * 3600) // SLOT)
        out[iface] = {"kind": e.get("kind", "link"), "hours": hours, "hour_cols": hour_cols, "hour_full": hour_full,
                      "month": month, "summary": {
            "up": round(sum(c == "u" for c in seen) / len(seen), 4) if seen else None,
            "hours_seen": round(len(seen) * SLOT / 3600, 1),
            "drops": sum(1 for a, b in zip(span, span[1:]) if a == "u" and b in DOWN),
            "longest": {"minutes": longest * SLOT // 60, "at": (base + at) * SLOT} if longest else None}}
    return out
