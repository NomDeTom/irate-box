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

from irate_box import durable
from irate_box.hub.refused import Refused
from irate_box.msg import M

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SETTINGS = STATE / "hotspot.json"
NETINV = STATE / "control" / "netinv.json"
MODES = ("open", "owe", "sae", "two")
# No password until the owner sets one.
DEFAULT = {"mode": "open", "password": "", "allow_wpa2": False, "second": "owe"}  # nosec B105
PASSWORD_RE = re.compile(r"^[\x20-\x7e]{8,63}$")  # what WPA accepts as a passphrase

# Refusals said in several places.
NO_WPA3 = M(
    "hotspot.wpa3.unsupported", "The radio does not report WPA3 (SAE) support. Select a different security mode."
)
BAD_PASSWORD = M(
    "hotspot.password.invalid",
    "The WPA3 password must have 8 to 63 characters. Use letters, digits, spaces or punctuation.",
)

LABEL_SAID = {
    "open": M("hotspot.label.open", "Open: no password"),
    "owe": M("hotspot.label.owe", "Enhanced Open (OWE): no password, encrypted"),
    "sae": M("hotspot.label.sae", "WPA3 with a published password"),
    "two": M("hotspot.label.two", "Two networks: one open, one encrypted"),
}
LABEL = {mode: m["en"] for mode, m in LABEL_SAID.items()}  # the page's choices, in English (data-says)
WHAT = {
    "open": "Guests join with no password. Every WiFi device can join. The WiFi link is not encrypted.",
    "owe": "Guests join with no password. The WiFi link is encrypted with a separate key for each device. "
    "A device near the hotspot cannot read the WiFi traffic of other devices.",
    "sae": "Guests enter a password that you publish on the Join QR and on the front page. WPA3 gives each device "
    "its own key. A guest who knows the password cannot read the WiFi traffic of other devices.",
    "two": "One open network for devices that cannot use encryption. One encrypted network (OWE or WPA3) for "
    "devices that can. Guests select a network.",
}
WARNINGS = {
    "open": [
        "Open WiFi does not encrypt the WiFi link. Anyone nearby can read traffic that has no other encryption, "
        "such as plain HTTP. Use HTTPS to encrypt web traffic between the device and the box.",
        "If an administrator signs in over plain HTTP on the hotspot, anyone nearby can read the administrator password.",
        "A device lock on a saved file or tile does not encrypt the WiFi link.",
    ],
    "owe": [
        "Devices that do not support OWE cannot join. They do not show the network or they fail to connect.",
        "Check that your guests' devices support OWE before you rely on this mode.",
        "A person with a fake hotspot of the same name can still read the traffic of a guest who joins it. "
        "OWE does not identify the hotspot.",
        "No test of OWE on this radio driver has been done. Test it with your own devices first.",
    ],
    "sae": [
        "Devices that do not support WPA3 cannot join, unless you let WPA2 devices join.",
        "A person who knows the published password can set up a fake hotspot with the same name and password.",
        "The password is published on purpose. It prevents a listener from reading the WiFi link. "
        "It does not identify who joins.",
    ],
    "two": [
        "The open network does not encrypt the WiFi link. Only the encrypted network encrypts the WiFi link.",
        "This mode needs a second access-point interface, usually a USB WiFi dongle beside the built-in radio.",
    ],
}
WPA2_WARNING = (
    "If you let WPA2 devices join (WPA3 transition mode), older devices can join. "
    "WPA2 devices do not get a separate key. A person who knows the password can read their WiFi traffic."
)
ALWAYS = (
    "In every mode, a person with the right equipment can impersonate the hotspot or change pages in transit. "
    "HTTPS prevents this on devices that trust the box's certificate."
)


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
        slots = max(
            [sum(g["max"] for g in c["groups"] if "AP" in g["modes"]) for c in r.get("combinations", [])] or [1]
        )
        radios.append(
            {
                "iface": r["iface"],
                "driver": r.get("driver"),
                "bus": r.get("bus"),
                "sae": bool(r.get("sae")),
                "ap_slots": slots,
            }
        )
    return {
        "known": True,
        "ap_radios": len(radios),
        "ap_slots": sum(r["ap_slots"] for r in radios),
        "sae": any(r["sae"] for r in radios),
        "radios": radios,
    }


def available(caps):
    """{mode: None if it can be chosen, or why not}."""
    out = {"open": None}
    if not caps["known"]:
        why = "the box has not checked the radios yet (Network → Look again)"
        return {"open": None, "owe": why, "sae": why, "two": why}
    if not caps["ap_radios"]:
        why = "no radio in this box can run a hotspot"
        return {"open": why, "owe": why, "sae": why, "two": why}
    out["owe"] = None  # drivers do not report OWE; offered with a warning to test it
    out["sae"] = None if caps["sae"] else "the radio does not report WPA3 (SAE) support"
    out["two"] = (
        None
        if caps["ap_slots"] >= 2
        else (
            "this box has one access-point interface. Add a second radio, for example a USB WiFi dongle, to run two networks"
        )
    )
    return out


def unavailable(caps, mode):
    """Why a mode cannot be selected on this box, as a refusal (the same cases as available), or None."""
    if not caps["known"]:
        return (
            None
            if mode == "open"
            else M(
                "hotspot.mode.radios-unchecked",
                "The box has not checked its radios yet. On the Network page, select Look again.",
            )
        )
    if not caps["ap_radios"]:
        return M("hotspot.mode.no-radio", "No radio in this box can run a hotspot.")
    if mode == "sae" and not caps["sae"]:
        return NO_WPA3
    if mode == "two" and caps["ap_slots"] < 2:
        return M(
            "hotspot.mode.one-interface",
            "This box has one access-point interface, so it cannot run two networks. "
            "To run two networks, add a second radio, for example a USB WiFi dongle.",
        )
    return None


def validate(raw, caps):
    if not isinstance(raw, dict):
        raise Refused(M("hotspot.settings.not-object", "The hotspot settings must be a JSON object."))
    mode = raw.get("mode")
    if mode not in MODES:
        raise Refused(
            M(
                "hotspot.mode.unknown",
                "The hotspot security mode must be one of these values: {values}.",
                values=list(MODES),
            )
        )
    why = unavailable(caps, mode)
    if why:
        raise Refused(why)
    second = raw.get("second", "owe")
    if second not in ("owe", "sae"):
        raise Refused(M("hotspot.second.unknown", "The encrypted network must use OWE or WPA3."))
    if mode == "two" and second == "sae" and not caps["sae"]:
        raise Refused(NO_WPA3)
    password = str(raw.get("password", ""))
    needs_pw = mode == "sae" or (mode == "two" and second == "sae")
    if needs_pw and not PASSWORD_RE.match(password):
        raise Refused(BAD_PASSWORD)
    return {
        "mode": mode,
        "password": password if needs_pw else "",
        "allow_wpa2": bool(raw.get("allow_wpa2")) and needs_pw,
        "second": second,
    }


def save(raw, caps=None):
    s = validate(raw, caps or capabilities())
    durable.write_checked(SETTINGS, json.dumps(s, indent=2))
    return s


AP_STATUS = STATE / "control" / "ap.json"  # the root helper's: the hotspot running or not (root/ap.py)


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
        radios = [
            {"iface": r["iface"], "channels": [c["channel"] for c in apmode.allowed(r, inv.get("country"))]}
            for r in inv.get("radios", [])
            if r.get("ap")
        ]
    except (OSError, ValueError, KeyError):
        preview, radios = None, []
    return dict(st, preview=preview, radios=radios)


def snapshot():
    caps = capabilities()
    run = running()
    return {
        "settings": load(),
        "capabilities": caps,
        "available": available(caps),
        "modes": list(MODES),
        "label": LABEL,
        "what": WHAT,
        "warnings": WARNINGS,
        "wpa2_warning": WPA2_WARNING,
        "always": ALWAYS,
        "hotspot_exists": True,
        "running": run,
    }


# --- what the hotspot add-on applies -------------------------------------------------------
# The choice above as settings for the two AP backends: NetworkManager (`nmcli con
# add/modify` properties) and hostapd (config lines). Built here so the add-on only has to apply
# them; it checks the choice again against the radios first (validate). Not yet tried on a radio:
# OWE on the AIC8800 is the first test once AP mode exists.


def _ssid_ok(ssid):
    if not ssid or len(ssid.encode()) > 32 or any(c in ssid for c in "\n\r\0"):
        raise Refused(
            M(
                "hotspot.ssid.invalid",
                "The hotspot name (SSID) must have 1 to 32 bytes on one line. "
                "A letter with an accent or an emoji uses more than 1 byte.",
            )
        )
    return ssid


def _with_suffix(ssid, suffix=" (encrypted)"):
    """ssid plus suffix in at most 32 bytes, trimming whole characters off the end of ssid."""
    room = 32 - len(suffix.encode())
    while len(ssid.encode()) > room:
        ssid = ssid[:-1]
    return ssid.rstrip() + suffix


def networks(settings, ssid, second_ssid=None):
    """[(role, security)] for the networks the choice means: one, or two for "two"."""
    mode = settings["mode"]
    if mode == "two":
        return [
            ("open", {"ssid": _ssid_ok(ssid), "kind": "open"}),
            ("encrypted", {"ssid": _ssid_ok(second_ssid or _with_suffix(ssid)), "kind": settings["second"]}),
        ]
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
            raise Refused(
                M(
                    "hotspot.wpa2.needs-hostapd",
                    "WPA3 transition mode needs the hostapd backend. NetworkManager runs the hotspot "
                    "on this box. To start the hotspot, clear the option that lets older WPA2 devices join.",
                )
            )
        return {
            "802-11-wireless-security.key-mgmt": "sae",
            "802-11-wireless-security.psk": settings["password"],
            "802-11-wireless-security.pmf": "3",
        }
    raise Refused(M("hotspot.security.unknown", "Unknown hotspot security mode {kind}.", kind=kind))


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
            raise Refused(BAD_PASSWORD)
        if settings.get("allow_wpa2"):
            return lines + ["wpa_key_mgmt=WPA-PSK SAE", f"wpa_passphrase={pw}", f"sae_password={pw}", "ieee80211w=1"]
        return lines + ["wpa_key_mgmt=SAE", f"sae_password={pw}", "ieee80211w=2", "sae_require_mfp=1"]
    raise Refused(M("hotspot.security.unknown", "Unknown hotspot security mode {kind}.", kind=kind))
