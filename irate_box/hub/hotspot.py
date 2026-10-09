# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""How the hub's own hotspot is secured: the owner's choice, and what the radios allow.

The Security page's "The hotspot's own WiFi" section. Four modes, from most reachable to most
private:

  open   anyone joins with no password; nothing is protected at the radio layer
  owe    Enhanced Open (OWE): no password, but each guest's traffic is encrypted on its own
  sae    WPA3 with a password that is published (Join QR, front page): a listener who knows it
         still cannot read other guests' traffic
  two    two networks at once, one open and one encrypted (OWE or WPA3), so older devices can
         still join; needs two access-point interfaces (a second radio, usually a USB dongle)

What the radios allow comes from the network inventory (netinv.py, control/netinv.json): SAE
support, how many access-point interfaces exist across the radios. OWE support is not reported
by drivers, so it is offered with a warning to test it first.

There is no hotspot yet: the choice is kept in the hub's state and the hotspot
add-on applies it when it exists, checking it again then. Stdlib only.
"""

import json
import os
import re
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SETTINGS = STATE / "hotspot.json"
NETINV = STATE / "control" / "netinv.json"
MODES = ("open", "owe", "sae", "two")
DEFAULT = {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"}
PASSWORD_RE = re.compile(r"^[\x20-\x7e]{8,63}$")  # what WPA accepts as a passphrase

LABEL = {
    "open": "Open: no password",
    "owe": "Enhanced Open (OWE): no password, encrypted",
    "sae": "WPA3 with a published password",
    "two": "Two networks: one open, one encrypted",
}
WHAT = {
    "open": "Anyone nearby can join with no password. The usual choice for a public hub, and the one every "
            "device can use.",
    "owe": "Guests still join with no password, but each device's traffic is encrypted with its own key, so "
           "someone listening nearby cannot read it.",
    "sae": "Guests type a password that you publish (the Join QR and the front page show it). Because WPA3 gives "
           "each device its own key, knowing the password does not let a listener read anyone else's traffic.",
    "two": "An open network for devices that cannot use encryption, and an encrypted one (OWE or WPA3) beside "
           "it for those that can. Guests choose.",
}
WARNINGS = {
    "open": [
        "Anyone nearby can read everything guests send and receive on the hub: pages, messages, uploads, and "
        "the admin login if you log in over the hotspot.",
        "Device locks on saves and files still work, but anyone listening can see what guests do.",
    ],
    "owe": [
        "Older devices cannot join: roughly Android before 10, iPhones and iPads before iOS 16, Windows 10 "
        "before its 2020 updates. They will not see the network or will fail to connect.",
        "Someone can still set up a fake hotspot with the same name, and a guest who joins it is not protected.",
        "Not tested on this radio's driver yet: try it with your own devices before relying on it.",
    ],
    "sae": [
        "Older devices cannot join: roughly Android before 10, iPhones and iPads before iOS 13, Windows 10 "
        "before 1903.",
        "Anyone who knows the published password can set up a fake hotspot with the same name and password.",
        "The password is public by design: it keeps out listeners, not people.",
    ],
    "two": [
        "Guests on the open network are as exposed as with an open hotspot; only the encrypted one protects "
        "its guests.",
        "Needs a second access-point interface: usually a USB WiFi dongle beside the built-in radio.",
    ],
}
WPA2_WARNING = ("Letting WPA2 devices join (WPA3 transition mode) brings older devices back, but they get "
                "no protection: anyone who knows the password can read their traffic.")
ALWAYS = ("Whatever the mode, someone with the right equipment can impersonate the hotspot or tamper with "
          "pages in transit. Only HTTPS (planned, with a certificate guests install on first use) protects "
          "against that.")


def load():
    try:
        s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return dict(DEFAULT)
    return {**DEFAULT, **{k: s[k] for k in DEFAULT if k in s}}


def capabilities(inv=None):
    """What the radios allow, from the network inventory."""
    if inv is None:
        try:
            inv = json.loads(NETINV.read_text())
        except (OSError, ValueError):
            inv = None
    if not inv:
        return {"known": False, "ap_radios": 0, "ap_slots": 0, "sae": False, "radios": []}
    radios, seen = [], set()
    for r in inv.get("radios", []):
        if r.get("phy") in seen or not r.get("ap"):
            continue
        seen.add(r.get("phy"))
        # The most access points this radio can run at once, from its interface combinations.
        slots = max([sum(g["max"] for g in c["groups"] if "AP" in g["modes"]) for c in r.get("combinations", [])] or [1])
        radios.append({"iface": r["iface"], "driver": r.get("driver"), "bus": r.get("bus"), "sae": bool(r.get("sae")),
                       "ap_slots": slots})
    return {"known": True, "ap_radios": len(radios), "ap_slots": sum(r["ap_slots"] for r in radios),
            "sae": any(r["sae"] for r in radios), "radios": radios}


def available(caps):
    """{mode: None if it can be chosen, or why not}."""
    out = {"open": None}
    if not caps["known"]:
        why = "the radios have not been looked at yet (Network → Look again)"
        return {"open": None, "owe": why, "sae": why, "two": why}
    if not caps["ap_radios"]:
        why = "no radio here can be an access point"
        return {"open": why, "owe": why, "sae": why, "two": why}
    out["owe"] = None  # drivers do not report OWE; offered with a warning to test it
    out["sae"] = None if caps["sae"] else "the radio does not report WPA3 (SAE) support"
    out["two"] = None if caps["ap_slots"] >= 2 else (
        "only one access-point interface here: add a second radio (a USB WiFi dongle) for two networks")
    return out


def validate(raw, caps):
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")
    mode = raw.get("mode")
    if mode not in MODES:
        raise ValueError("unknown mode")
    why = available(caps).get(mode)
    if why:
        raise ValueError(f"{LABEL[mode]} cannot be chosen here: {why}")
    second = raw.get("second", "owe")
    if second not in ("owe", "sae"):
        raise ValueError("the encrypted network is OWE or WPA3")
    if mode == "two" and second == "sae" and not caps["sae"]:
        raise ValueError("the radio does not report WPA3 (SAE) support")
    password = str(raw.get("password", ""))
    needs_pw = mode == "sae" or (mode == "two" and second == "sae")
    if needs_pw and not PASSWORD_RE.match(password):
        raise ValueError("the WPA3 password is 8 to 63 ordinary characters (letters, digits, spaces, punctuation)")
    return {"mode": mode, "password": password if needs_pw else "", "allow_wpa2": bool(raw.get("allow_wpa2")) and needs_pw,
            "second": second}


def save(raw, caps=None):
    s = validate(raw, caps or capabilities())
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    os.replace(tmp, SETTINGS)
    return s


AP_STATUS = STATE / "control" / "ap.json"   # the root helper's: the hotspot running or not (root/ap.py)


def running():
    """The root helper's last word on the hotspot, and the plan it would follow now (apmode.py),
    from the last inventory: what the Network page shows before and after switching it on."""
    from irate_box.hub import apmode
    try:
        st = json.loads(AP_STATUS.read_text())
    except (OSError, ValueError):
        st = {"up": False, "plan": None, "tried": {}, "owner": {}, "confirmed": True, "note": ""}
    try:
        inv = json.loads(NETINV.read_text())
        preview = apmode.plan(inv, inv.get("ap") or [], st.get("owner") or {}, st.get("tried") or {})
        radios = [{"iface": r["iface"], "channels": [c["channel"] for c in apmode.allowed(r, inv.get("country"))]}
                  for r in inv.get("radios", []) if r.get("ap")]
    except (OSError, ValueError, KeyError):
        preview, radios = None, []
    return dict(st, preview=preview, radios=radios)


def snapshot():
    caps = capabilities()
    run = running()
    return {"settings": load(), "capabilities": caps, "available": available(caps), "modes": list(MODES),
            "label": LABEL, "what": WHAT, "warnings": WARNINGS, "wpa2_warning": WPA2_WARNING, "always": ALWAYS,
            "hotspot_exists": True, "running": run}


# --- what the hotspot add-on applies -------------------------------------------------------
# The choice above as settings for the two AP backends: NetworkManager (`nmcli con
# add/modify` properties) and hostapd (config lines). Built here so the add-on only has to apply
# them; it checks the choice again against the radios first (validate). Not yet tried on a radio:
# OWE on the AIC8800 is the first test once AP mode exists.

def _ssid_ok(ssid):
    if not ssid or len(ssid.encode()) > 32 or any(c in ssid for c in "\n\r\0"):
        raise ValueError("an SSID is 1 to 32 bytes, on one line")
    return ssid


def _with_suffix(ssid, suffix=" (safe)"):
    """ssid plus suffix in at most 32 bytes, trimming whole characters off the end of ssid."""
    room = 32 - len(suffix.encode())
    while len(ssid.encode()) > room:
        ssid = ssid[:-1]
    return ssid.rstrip() + suffix


def networks(settings, ssid, second_ssid=None):
    """[(role, security)] for the networks the choice means: one, or two for "two"."""
    mode = settings["mode"]
    if mode == "two":
        return [("open", {"ssid": _ssid_ok(ssid), "kind": "open"}),
                ("encrypted", {"ssid": _ssid_ok(second_ssid or _with_suffix(ssid)), "kind": settings["second"]})]
    return [("only", {"ssid": _ssid_ok(ssid), "kind": mode})]


def nm_properties(settings, kind):
    """NetworkManager connection properties for one network's security (kind: open|owe|sae)."""
    if kind == "open":
        return {}
    if kind == "owe":
        return {"802-11-wireless-security.key-mgmt": "owe", "802-11-wireless-security.pmf": "3"}
    if kind == "sae":
        if settings.get("allow_wpa2"):
            # NetworkManager has no WPA2/WPA3 transition mode for an access point.
            raise ValueError("WPA2 devices alongside WPA3 (transition mode) needs the hostapd backend")
        return {"802-11-wireless-security.key-mgmt": "sae", "802-11-wireless-security.psk": settings["password"],
                "802-11-wireless-security.pmf": "3"}
    raise ValueError(f"unknown security {kind}")


def hostapd_lines(settings, kind, ssid):
    """hostapd.conf lines for one network (the add-on adds interface, channel and the rest)."""
    lines = [f"ssid2={ssid.encode().hex()}"]  # hex form: any byte in the name is safe
    if kind == "open":
        return lines
    lines += ["wpa=2", "rsn_pairwise=CCMP"]
    if kind == "owe":
        return lines + ["wpa_key_mgmt=OWE", "ieee80211w=2"]
    if kind == "sae":
        pw = settings["password"]
        if not PASSWORD_RE.match(pw):
            raise ValueError("the WPA3 password is 8 to 63 ordinary characters")
        if settings.get("allow_wpa2"):
            return lines + ["wpa_key_mgmt=WPA-PSK SAE", f"wpa_passphrase={pw}", f"sae_password={pw}", "ieee80211w=1"]
        return lines + ["wpa_key_mgmt=SAE", f"sae_password={pw}", "ieee80211w=2", "sae_require_mfp=1"]
    raise ValueError(f"unknown security {kind}")
