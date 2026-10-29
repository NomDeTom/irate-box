#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's radios switched on at every start: each rfkill switch for WiFi and Bluetooth unblocked, unless an
admin has chosen otherwise on Recovery and monitoring.

irate-box-rfkill.service runs this as root (`rfkill.py boot`) once at every start, after systemd-rfkill has put
back the blocks it saved and before the network starts.

  Why             an image can start with a radio blocked, and the box then has no WiFi with nothing to say why.
                  Armbian's image for the Luckfox Lyra Zero W starts with bt_default blocked, and on that board
                  bt_default is the WiFi chip's power line (POWER_LINES): the chip never reaches the USB bus.
                  mPWRD-OS ships a unit of its own that unblocks every switch; Armbian's image has none.
  The choice      per radio (RADIOS), the default "on" until an admin decides:
                    on     unblocked at every start
                    off    blocked at every start
                    leave  left as the image and systemd-rfkill have it
  Which switch    a switch of type wlan is WiFi's, one of type bluetooth is Bluetooth's; a known board's power
                  line for its WiFi chip (POWER_LINES) is WiFi's, whatever its type says. Blocking it takes the
                  whole chip off the bus, Bluetooth with it. Other types (wwan, gps, nfc) are left alone. A hard
                  block is a switch on the board and is only reported.
  Late switches   a chip whose power line was just unblocked brings its own switches (phy0, hci0) when it reaches
                  the bus, with whatever systemd-rfkill saved for them; they are looked at again once it has
                  (CHIP_WAIT).

    rfkill.py boot                    every switch as chosen
    rfkill.py status                  the choices and the switches, as JSON
    rfkill.py set RADIO VALUE         wifi|bluetooth on|off|leave; applied at once

Settings in /etc/hub/rfkill.json (root's). Stdlib only.
"""

import json
import os
import sys
import time
from pathlib import Path

from irate_box.msg import M, Refused

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
SETTINGS = ETC / "rfkill.json"

RADIOS = ("wifi", "bluetooth")
CHOICES = ("on", "off", "leave")
DEFAULT = {"wifi": "on", "bluetooth": "on"}
RADIO_WORDS = {"wifi": M("rfkill.radio.wifi", "WiFi"), "bluetooth": M("rfkill.radio.bluetooth", "Bluetooth")}
# Boards whose WiFi chip has a power line on an rfkill switch: the board's model → that switch's name. The
# Luckfox Lyra Zero W's AIC8800DC is enabled by GPIO1_C5 (WL_DIS#), which its device tree hands to Bluetooth's rfkill.
POWER_LINES = {"Luckfox Lyra Zero W": "bt_default"}
CHIP_WAIT = 15  # seconds for a chip just powered to reach the bus and bring its own switches


def read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def model():
    try:
        return (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace")
    except OSError:
        return None


def load_settings():
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULT)
    for radio in RADIOS:
        if isinstance(raw, dict) and raw.get(radio) in CHOICES:
            out[radio] = raw[radio]
    return out


def chosen():
    """The radios an admin has decided on: the rest follow the default."""
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return set()
    return {radio for radio in RADIOS if isinstance(raw, dict) and raw.get(radio) in CHOICES}


def save_settings(s):
    from irate_box.root import safeio

    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(SETTINGS, json.dumps(s, indent=1))


def switches():
    """Each rfkill switch: {index, name, type, radio, power_line, soft, hard}; radio "wifi", "bluetooth" or None."""
    power_line = POWER_LINES.get(model())
    out = []
    for switch in sorted((SYS / "class/rfkill").glob("rfkill*"), key=lambda p: int(p.name[6:] or 0)):
        name, kind = read(switch / "name"), read(switch / "type")
        if name is None:
            continue
        line = bool(power_line) and name == power_line
        radio = "wifi" if line or kind == "wlan" else "bluetooth" if kind == "bluetooth" else None
        out.append(
            {
                "index": switch.name[6:],
                "name": name,
                "type": kind,
                "radio": radio,
                "power_line": line,
                "soft": read(switch / "soft") == "1",
                "hard": read(switch / "hard") == "1",
            }
        )
    return out


def at_odds(sw, s=None):
    """Whether a switch's soft block differs from what its radio is set to."""
    want = (s or load_settings()).get(sw["radio"]) if sw["radio"] else "leave"
    return (want == "on" and sw["soft"]) or (want == "off" and not sw["soft"])


def _apply_once(s, done):
    changed = []
    for sw in switches():
        if sw["radio"] is None or not at_odds(sw, s) or sw["name"] in done:
            continue
        block = s[sw["radio"]] == "off"
        try:
            (SYS / "class/rfkill" / f"rfkill{sw['index']}" / "soft").write_text("1" if block else "0")
        except OSError as exc:
            changed.append((sw, None, str(exc)))
            continue
        done.add(sw["name"])
        changed.append((sw, "blocked" if block else "unblocked", None))
    return changed


def apply(s=None, sleep=time.sleep):
    """Every switch as chosen: [(switch, "blocked"|"unblocked"|None, error)]. A power line unblocked is waited
    for (CHIP_WAIT), and the switches its chip brings are set too."""
    s = s or load_settings()
    done = set()
    before = {sw["name"] for sw in switches()}
    changed = _apply_once(s, done)
    if any(sw["power_line"] and what == "unblocked" for sw, what, _ in changed):
        for _ in range(CHIP_WAIT):
            if {sw["name"] for sw in switches()} - before:
                sleep(1)  # the chip's second switch (Bluetooth's, after WiFi's)
                break
            sleep(1)
        changed += _apply_once(s, done)
    return changed


def said(changed):
    lines = []
    for sw, what, error in changed:
        if error:
            lines.append(f"rfkill{sw['index']} ({sw['name']}): could not be changed: {error}")
        else:
            lines.append(f"rfkill{sw['index']} ({sw['name']}, {sw['radio']}): {what}")
    return lines


def set_option(radio, value):
    if radio not in RADIOS or value not in CHOICES:
        raise Refused(M("rfkill-set.unknown", "The radios have no setting {radio} {value}.", radio=radio, value=value))
    s = load_settings()
    s[radio] = value
    save_settings({r: s[r] for r in chosen() | {radio}})  # only what an admin chose: the rest keep the default
    if value == "leave":
        return M(
            "rfkill-set.leave",
            "The box no longer switches {radio} on or off. It stays as the image has it.",
            radio=RADIO_WORDS[radio],
        )
    failed = [sw["name"] for sw, what, error in apply(s) if error]
    if failed:
        return M(
            "rfkill-set.failed",
            "The setting is saved. These switches could not be changed: {names}.",
            names=", ".join(failed),
        )
    return (
        M("rfkill-set.on", "{radio} is switched on now and at every start.", radio=RADIO_WORDS[radio])
        if value == "on"
        else M("rfkill-set.off", "{radio} is switched off now and at every start.", radio=RADIO_WORDS[radio])
    )


def status():
    return {
        "settings": load_settings(),
        "chosen": sorted(chosen()),
        "power_line": POWER_LINES.get(model()),
        "switches": switches(),
    }


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "boot":
        for line in said(apply()):
            print(line)
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 3:
        print(set_option(argv[1], argv[2]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
