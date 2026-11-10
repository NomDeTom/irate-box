# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's plan: which radio, how it shares it, which channel, adapted to whatever hardware
the box runs on.

Nothing is assumed from any one board. The network inventory (netinv.py, written by the root
helper) says what each radio can do (whether it can be an access point, its interface combinations
and how many channels it can use at once, its channels and their flags) and what links the box
has; ap_verdicts() there says, per radio, how a hotspot could run on it. plan() here takes the
first rung of a ladder that fits the whole box:

  1 own-radio    a radio that isn't the box's WiFi link (a second radio or a USB dongle, or the
                 only radio when the link is Ethernet or there is none): no sharing at all
  2 own-channel  the WiFi link's radio, beside it, keeping a channel of its own: only where the
                 driver offers two or more channels, the try on this box hasn't failed, and the
                 chip isn't known to stall that way (known_radios.json); guests never notice the
                 link roam
  3 follow       the WiFi link's radio, beside it, on the link's channel, moved when the link
                 roams (guests dropped for a few seconds each time)
  4 choose       a radio that can't run both: the owner chooses, the hotspot or the WiFi link;
                 the box never drops the owner's link without asking
  5 none         no radio here can be an access point

The channel is one the radio may start an access point on (not disabled, not "no IR", not a
radar (DFS) channel, which an AP must leave whenever radar appears) and the country allows, the
owner's if they chose one. The address is always 192.168.4.1, which the box's certificate
authority already permits (root/tls.py HOTSPOT). Pure: the root helper acts on the plan.
"""

ADDRESS = "192.168.4.1"
# The interface every hotspot runs on (root/ap.py), whatever its radio. In each plan, so a record written before it
# (the hotspot on the radio's own interface) is told apart.
HOTSPOT_IFACE = "ap0"
PREFERRED_24 = (1, 6, 11)  # the 2.4 GHz channels that don't overlap
TOP_24 = {"US": 11, "CA": 11, "TW": 11, "MX": 11}  # elsewhere, channels up to 13 (14: Japan, 802.11b only)
RUNG = {"own-radio": 1, "own-channel": 2, "follow": 3, "choose": 4, "none": 5}
TEXT = {
    "own-radio": "The hotspot has a radio to itself.",
    "own-channel": "The hotspot shares the radio with the box's WiFi link and uses its own channel. The link can roam without a change to the hotspot channel.",
    "follow": "The hotspot shares the radio with the box's WiFi link and follows the channel of the link. When the link roams, the hotspot changes channel and guests can lose their connection.",
    "choose": "This radio cannot run the hotspot and the WiFi link at the same time. Select the hotspot or the WiFi link.",
    "none": "No radio in this box can run a hotspot. A USB WiFi dongle that supports access-point mode makes a hotspot possible.",
}


def allowed(radio, country=None):
    """The channels this radio may start an access point on, here: [{channel, freq, band}]."""
    top = TOP_24.get((country or "").upper(), 13)
    out = []
    for c in radio.get("channels") or []:
        if not c.get("ap_ok"):
            continue
        band = "2.4 GHz" if c["freq"] < 3000 else "5 GHz" if c["freq"] < 5925 else "6 GHz"
        if band == "2.4 GHz" and c["channel"] > top:
            continue
        out.append({"channel": c["channel"], "freq": c["freq"], "band": band})
    return out


def pick_channel(chans, owner=None, near=None):
    """The owner's channel if it's allowed; else the link's (near) if allowed; else 1, 6 or 11;
    else the first allowed. owner: {"band", "channel"}."""
    owner = owner or {}
    pool = [c for c in chans if not owner.get("band") or c["band"] == owner["band"]] or chans
    by_ch = {c["channel"]: c for c in pool}
    for want in (owner.get("channel"), near, *PREFERRED_24):
        if want in by_ch:
            return by_ch[want]
    return pool[0] if pool else None


def plan(inv, verdicts, owner=None, caps=None):
    """The hotspot's plan for this box, from the inventory and netinv.ap_verdicts(inv).
    owner: the owner's choices {"radio", "band", "channel", "take_radio" (rung 4: True to give the
    WiFi link up for the hotspot)}. caps: {phy: stored rows} from root/radiocaps.py, merged with the
    driver and the chip table here (rung 2's own channel is a record row, not a try kept elsewhere)."""
    from irate_box.root import radiocaps

    owner, caps = owner or {}, caps or {}
    radios = {r["phy"]: r for r in inv.get("radios", [])}
    records = {phy: radiocaps.rows(radio, caps.get(phy) or {}) for phy, radio in radios.items()}
    up = inv.get("uplink") or {}
    country = inv.get("country")
    options = []
    for v in verdicts:
        if not v.get("possible"):
            continue
        r = radios.get(v["phy"], {})
        chans = allowed(r, country)
        if not chans:
            continue
        link_ch = (r.get("link") or {}).get("channel")
        if v["mode"] == "radio-alone":
            kind = "own-radio"
        elif v["mode"] == "beside-client":
            # A channel of its own where the record's channels-at-once offers two and its own-channel row is not
            # "no" (the chip list's firmware stall, or a try or the box's own sight): an unanswered row is left
            # to the try, as it always was.
            row = records.get(v["phy"]) or {}
            channels = (row.get("channels-at-once") or {}).get("value")
            own = (channels if isinstance(channels, int) else r.get("channels_at_once", 1)) >= 2 and (
                (row.get("own-channel") or {}).get("value") not in (False, "no")
            )
            kind = "own-channel" if own else "follow"
        elif v["mode"] == "takes-radio":
            kind = "choose"
        else:
            continue
        options.append((RUNG[kind], 0 if owner.get("radio") == v["iface"] else 1, v, r, chans, link_ch, kind))
    if not options:
        return {
            "rung": 5,
            "kind": "none",
            "text": TEXT["none"],
            "iface": None,
            "address": ADDRESS,
            "hotspot_iface": None,
        }
    options.sort(key=lambda o: (o[1], o[0]))  # the owner's radio first, then the lowest rung
    _, _, v, r, chans, link_ch, kind = options[0]
    out = {
        "rung": RUNG[kind],
        "kind": kind,
        "text": TEXT[kind],
        "iface": v["iface"],
        "phy": v["phy"],
        "backend": v["backend"],
        "address": ADDRESS,
        "hotspot_iface": HOTSPOT_IFACE,
        "uplink": up.get("iface"),
        "to_try": kind == "own-channel" and (records.get(v["phy"], {}).get("own-channel") or {}).get("value") is None,
    }
    if kind == "choose" and not owner.get("take_radio"):
        out.update(needs_choice=True, channel=None)
        return out
    if kind != "follow":
        ch = pick_channel(chans, owner, near=link_ch)
    else:
        ch = next((c for c in chans if c["channel"] == link_ch), None)
        if ch is None:  # the link is on a channel the hotspot may not use (DFS): can't follow it there
            out.update(
                rung=4,
                kind="choose",
                text=TEXT["choose"],
                needs_choice=True,
                channel=None,
                why=f"the WiFi link is on channel {link_ch}, where a hotspot cannot start",
            )
            return out
    out.update(
        channel=ch["channel"],
        freq=ch["freq"],
        band=ch["band"],
        follows_uplink=kind == "follow",
        needs_choice=False,
        drops_uplink=kind == "choose",
    )
    return out
