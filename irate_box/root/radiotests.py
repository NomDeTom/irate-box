# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Experiments the box runs on its own radios, at the owner's asking, to answer what the hardware tab
would otherwise leave as "not tested": short, put back as they were, refused while guests are on the
hotspot. Each answer is kept per radio (its USB id or driver, and the driver's version) with when it
was found, and asked again when the driver changes.

  follows-roam   Does the hotspot stay on its channel, or follow, when the WiFi link moves to an access
                 point on another channel? The link's profile is held to one (of the same network) for
                 the test, the hotspot's channel read, and the profile given back what it had.
  radio-reset    How long a radio reset takes (root/radio.py, as the uplink watchdog does it), and
                 whether the hotspot comes back.
  driver-reset   Where the driver resets its own device on a command timeout and offers a fake one
                 (the patched AIC8800's fake_cmd_timeout): how long until the link is back.

Results: STATE/radio-tests.json ({radio key: {experiment: {answer, detail, at}}}). Root only; stdlib only.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RESULTS = STATE / "radio-tests.json"
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
QUESTIONS = {
    "follows-roam": "Whether the hotspot keeps its channel or follows the link, when your WiFi roams to another channel.",
    "radio-reset": "How long a radio reset takes, and whether the hotspot comes back after it.",
    "driver-reset": "Whether the driver reset brings the WiFi back without help, and how many seconds it takes.",
}


def run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def results():
    try:
        data = json.loads(RESULTS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _keep(key, exp, answer, detail):
    data = results()
    data.setdefault(key, {})[exp] = {"answer": answer, "detail": detail, "at": time.time()}
    from irate_box.root import safeio
    STATE.mkdir(parents=True, exist_ok=True)
    safeio.write(RESULTS, json.dumps(data, indent=1), 0o644)


def radio_key(radio):
    """A radio's identity for its answers: USB id (or driver) and the driver's version."""
    drv = radio.get("driver") or "?"
    ver = (SYS / "module" / drv.replace("-", "_") / "version")
    try:
        version = ver.read_text().strip()
    except OSError:
        version = ""
    who = (radio.get("usb") or {}).get("id") or drv
    return f"{who} {drv} {version}".strip()


def _channels():
    """{iface: channel} from iw dev."""
    code, out = run("iw", "dev")
    chans, cur = {}, None
    for line in out.splitlines() if code == 0 else []:
        m = re.match(r"\s*Interface (\S+)", line)
        if m:
            cur = m.group(1)
        m = re.match(r"\s*channel (\d+)", line)
        if m and cur:
            chans[cur] = int(m.group(1))
    return chans


def _guests(ap):
    code, out = run("iw", "dev", ap, "station", "dump")
    return sum(1 for l in out.splitlines() if l.startswith("Station")) if code == 0 else 0


def _link_up(iface, wait, sleep):
    for _ in range(int(wait)):
        code, out = run("iw", "dev", iface, "link")
        if code == 0 and out.startswith("Connected"):
            return True
        sleep(1)
    return False


def check_ready(inv, iface, exp):
    """(radio, ap iface or None) to run `exp` on, or ValueError saying why not."""
    if exp not in QUESTIONS:
        raise ValueError(f"Experiment unavailable: {exp} is not a known experiment.")
    if not IFACE_RE.match(str(iface or "")):
        raise ValueError("The interface name is not valid.")
    radio = next((r for r in inv.get("radios", []) if r.get("iface") == iface and r.get("type") == "managed"), None)
    if not radio:
        raise ValueError(f"{iface} is not a WiFi link on this box.")
    ap = next((r["iface"] for r in inv["radios"] if r.get("type") == "AP" and r.get("phy") == radio.get("phy")), None)
    if ap and _guests(ap):
        raise ValueError("Devices are connected to the hotspot. Select a time when no device is connected to it.")
    if exp == "follows-roam":
        if not ap:
            raise ValueError("There is no hotspot on this radio, so the hotspot has no channel to follow.")
        aps = (radio.get("roaming") or {}).get("aps") or []
        here = (radio.get("link") or {}).get("bssid")
        here_ch = (radio.get("link") or {}).get("channel")
        if not [a for a in aps if a.get("bssid") != here and a.get("channel") and a["channel"] != here_ch]:
            raise ValueError("Your network has no access point on another channel in reach, so the test cannot start a roam.")
    if exp == "driver-reset" and not (SYS / "module" / (radio.get("driver") or "?").replace("-", "_") / "parameters" / "fake_cmd_timeout").exists():
        raise ValueError("This driver has no fake command timeout, so the test cannot start the driver reset.")
    return radio, ap


def follows_roam(radio, ap, sleep=time.sleep):
    iface = radio["iface"]
    prof = radio.get("profile") or {}
    here = (radio.get("link") or {}).get("bssid")
    here_ch = (radio.get("link") or {}).get("channel")
    target = next(a for a in sorted((radio.get("roaming") or {}).get("aps") or [], key=lambda a: -(a.get("signal") or 0))
                  if a.get("bssid") != here and a.get("channel") and a["channel"] != here_ch)
    before = _channels().get(ap)
    # "nmcli con up ... ap BSSID" is only a hint (the supplicant may pick the strongest instead), so the
    # profile is held to the target for the test, and given back what it had after.
    code, was = run("nmcli", "-g", "802-11-wireless.bssid", "con", "show", "uuid", prof["uuid"])
    was = (was or "").replace("\\:", ":").strip() if code == 0 else ""
    t0 = time.time()
    try:
        run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", target["bssid"])
        code, out = run("nmcli", "con", "up", "uuid", prof["uuid"], timeout=60)
        took = round(time.time() - t0)
        sleep(5)
        chans = _channels()
        after, link_ch = chans.get(ap), chans.get(iface)
    finally:
        run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", was)
        back = run("nmcli", "con", "up", "uuid", prof["uuid"], timeout=60)[0] == 0
    if code != 0:
        return "unknown", f"The move to {target['bssid']} on channel {target['channel']} failed: {out[-120:]}"
    if link_ch == here_ch:
        return "unknown", f"The link did not move off channel {here_ch}. The test asked for {target['bssid']} on channel {target['channel']}."
    if after is None:
        from irate_box.root import radio as radio_mod
        said = radio_mod.restore_hotspot()
        if said:
            return "doesn't", f"The hotspot went down when the link moved to channel {link_ch}. {said}"
        return "doesn't", f"The hotspot went down when the link moved to channel {link_ch}."
    put_back = "" if back else " The link could not be brought back up afterwards."
    if after == link_ch and after != before:
        return "follows", f"The hotspot moved with the link from channel {before} to {after}. The move took {took} seconds.{put_back}"
    if after == before:
        return "stays", f"The hotspot stayed on channel {before} while the link moved to {link_ch}. This radio runs both at once.{put_back}"
    return "unknown", f"The hotspot went from channel {before} to {after} and the link went to {link_ch}.{put_back}"


def radio_reset(radio, ap, sleep=time.sleep):
    from irate_box.root import radio as radio_mod
    usb = radio.get("usb") or {}
    t0 = time.time()
    said = radio_mod.reset(radio["iface"], usb.get("port"), radio.get("driver"), usb.get("product"))
    up = _link_up(radio["iface"], 60, sleep)
    took = round(time.time() - t0)
    hotspot = (not ap) or ap in _channels()
    answer = "works" if up and hotspot else "doesn't"
    link_text = f"The link was back {took} seconds after the reset began." if up else f"The link was not back {took} seconds after the reset began."
    if not ap:
        return answer, f"{said} {link_text}"
    if hotspot:
        return answer, f"{said} {link_text} The hotspot was back."
    return answer, f"{said} {link_text} The hotspot was not back."


def driver_reset(radio, ap, sleep=time.sleep):
    params = SYS / "module" / radio["driver"].replace("-", "_") / "parameters"
    try:
        before = int((params / "recoveries").read_text().strip())
    except (OSError, ValueError):
        before = None
    (params / "fake_cmd_timeout").write_text("1")
    t0 = time.time()
    run("iw", "dev", radio["iface"], "scan", "trigger", timeout=15)
    sleep(3)
    up = _link_up(radio["iface"], 90, sleep)
    took = round(time.time() - t0)
    try:
        after = int((params / "recoveries").read_text().strip())
    except (OSError, ValueError):
        after = None
    reset = before is not None and after is not None and after > before
    if ap and ap not in _channels():
        from irate_box.root import radio as radio_mod
        radio_mod.restore_hotspot()
    if not reset and before is not None:
        return "doesn't", "The driver did not reset its device. Its count of resets did not change."
    return ("works" if up else "doesn't"), (f"The driver reset its device. The link was back {took} seconds later." if up else f"The driver reset its device. The link was not back {took} seconds later.")


def experiment(inv, iface, exp, sleep=time.sleep):
    radio, ap = check_ready(inv, iface, exp)
    answer, detail = {"follows-roam": follows_roam, "radio-reset": radio_reset, "driver-reset": driver_reset}[exp](radio, ap, sleep)
    _keep(radio_key(radio), exp, answer, detail)
    return f"{QUESTIONS[exp]} {detail}"
