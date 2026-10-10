# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager's box defaults: the values every connection uses unless it sets its own, read from
NetworkManager's configuration files, and the owner's changes written to irate-box's own drop-in.

NetworkManager reads NetworkManager.conf, then the conf.d files of /usr/lib, /run and /etc
together in name order (a file in /etc shadows one of the same name in /run, which shadows /usr/lib),
then NetworkManager-intern.conf; for a key set twice, the later file wins. irate-box's drop-in is
named to sort after the image's own (Armbian's zz-10-…, zz-20-…), holds only what the owner changed,
and is removed by undo and by uninstall; whatever it shadowed applies again.

A box default lasts whatever made a connection's profile: netplan writes its profiles afresh at
boot, with these settings left at "default", so the drop-in fills them in.

Reading needs no root (NetworkManager's files are world-readable); writing is root's, through
hub_control.py. stdlib only.
"""
import configparser
import os
import re
from pathlib import Path

ETC = Path(os.environ.get("HUB_NM_ETC", "/etc/NetworkManager"))
RUN = Path(os.environ.get("HUB_NM_RUN", "/run/NetworkManager"))
LIB = Path(os.environ.get("HUB_NM_LIB", "/usr/lib/NetworkManager"))
INTERN = Path(os.environ.get("HUB_NM_INTERN", "/var/lib/NetworkManager/NetworkManager-intern.conf"))
DROPIN = ETC / "conf.d" / "zz-90-irate-box-network.conf"

# What the owner can set as a box default: (section, key), the values offered, and NetworkManager's own
# behaviour when nothing sets it. "default" in a change removes irate-box's line.
SETTINGS = {
    "autoconnect_retries": {
        "section": "main", "key": "autoconnect-retries-default", "kind": "count",
        "label": "Keep trying to connect", "nm_default": "4 tries, then 5 minutes blocked",
        "values": {"0": "forever"},
    },
    "auth_retries": {
        "section": "connection", "key": "connection.auth-retries", "kind": "count",
        "label": "Keep trying after a failed password check", "nm_default": "3 tries, then waits for someone",
        "values": {"0": "forever"},
    },
    "mac": {
        "section": "connection", "key": "wifi.cloned-mac-address", "kind": "choice",
        "label": "MAC address on WiFi", "nm_default": "as the device has it",
        "values": {"permanent": "the board's own", "preserve": "as the device has it", "random": "random each time",
                   "stable": "stable, made up per connection", "stable-ssid": "stable, made up per network"},
    },
    "scan_mac": {
        "section": "device", "key": "wifi.scan-rand-mac-address", "kind": "choice",
        "label": "MAC address while scanning", "nm_default": "random",
        "values": {"no": "the board's own", "yes": "random"},
    },
    "powersave": {
        "section": "connection", "key": "wifi.powersave", "kind": "choice",
        "label": "WiFi power save", "nm_default": "the driver's choice",
        "values": {"2": "off", "3": "on", "1": "leave to the driver (ignore)"},
    },
}
# Older names NetworkManager still reads, shown where they set the same thing.
LEGACY = {("connection", "wifi.mac-address-randomization"): {"1": "the board's own (never random)", "2": "random", "0": "default"}}


def _parser():
    cp = configparser.ConfigParser(interpolation=None, strict=False, delimiters=("=",), comment_prefixes=("#", ";"),
                                   inline_comment_prefixes=None, empty_lines_in_values=False)
    cp.optionxform = str
    return cp


def files():
    """NetworkManager's configuration files in the order it reads them."""
    out = []
    main = ETC / "NetworkManager.conf"
    if main.is_file():
        out.append(main)
    by_name = {}
    for d in (LIB / "conf.d", RUN / "conf.d", ETC / "conf.d"):   # later directories shadow earlier ones
        if d.is_dir():
            for f in d.glob("*.conf"):
                by_name[f.name] = f
    out += [by_name[n] for n in sorted(by_name)]
    if INTERN.is_file():
        out.append(INTERN)
    return out


def _read(path):
    cp = _parser()
    try:
        cp.read_string(path.read_text(errors="replace"), source=str(path))
    except (OSError, configparser.Error):
        return None
    return cp


def effective():
    """{name: {value, file, label, nm_default, values, mine}} for each box default, and the named
    sections that set the same keys for some devices only (Armbian's [device-…] match-device ones)."""
    out = {n: {"value": None, "file": None, "label": s["label"], "nm_default": s["nm_default"],
               "values": s["values"], "kind": s["kind"], "mine": None} for n, s in SETTINGS.items()}
    legacy, scoped, broken = [], [], []
    for f in files():
        cp = _read(f)
        if cp is None:
            broken.append(str(f))
            continue
        for n, s in SETTINGS.items():
            if cp.has_option(s["section"], s["key"]):
                v = cp.get(s["section"], s["key"]).strip()
                out[n]["value"], out[n]["file"] = v, str(f)
                if f == DROPIN:
                    out[n]["mine"] = v
        for (sec, key), meaning in LEGACY.items():
            if cp.has_option(sec, key):
                v = cp.get(sec, key).strip()
                legacy.append({"key": key, "value": v, "means": meaning.get(v, v), "file": str(f)})
        for sec in cp.sections():
            base = sec.split("-", 1)[0]
            if sec != base and base in ("connection", "device"):
                keys = [k for k in cp.options(sec) if k != "match-device"]
                if keys:
                    scoped.append({"section": sec, "match": cp.get(sec, "match-device", fallback=""),
                                   "keys": {k: cp.get(sec, k).strip() for k in keys}, "file": str(f)})
    return {"settings": out, "legacy": legacy, "scoped": scoped, "broken": broken, "dropin": str(DROPIN)}


def validate(changes):
    """{name: value or "default"} as asked, or ValueError."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nothing to change")
    clean = {}
    for n, v in changes.items():
        s = SETTINGS.get(n)
        if s is None:
            raise ValueError(f"not a box default: {n}")
        v = str(v).strip()
        if v == "default":
            clean[n] = None
        elif s["kind"] == "count" and re.fullmatch(r"\d{1,4}", v):
            clean[n] = str(int(v))
        elif s["kind"] == "choice" and v in s["values"]:
            clean[n] = v
        else:
            raise ValueError(f"{s['label']}: {v!r} is not one of the choices")
    return clean


def dropin_text(mine):
    """irate-box's drop-in for {name: value}, or "" when nothing is set."""
    by_section = {}
    for n, v in mine.items():
        if v is not None:
            s = SETTINGS[n]
            by_section.setdefault(s["section"], []).append(f"{s['key']}={v}")
    if not by_section:
        return ""
    lines = ["# irate-box: the box defaults chosen on /admin's Network page. Removing this file puts back",
             "# whatever it shadowed (the image's own drop-ins, or NetworkManager's defaults)."]
    for sec in ("main", "connection", "device"):
        if sec in by_section:
            lines += ["", f"[{sec}]", *by_section[sec]]
    return "\n".join(lines) + "\n"


def current_mine():
    cp = _read(DROPIN) if DROPIN.is_file() else None
    if cp is None:
        return {}
    return {n: cp.get(s["section"], s["key"]).strip() for n, s in SETTINGS.items() if cp.has_option(s["section"], s["key"])}
