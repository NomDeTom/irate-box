# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Experiments the box runs on its own radios, at the owner's asking, to answer what the hardware tab
would otherwise leave as "not tested": short, put back as they were, refused while guests are on the
hotspot. Each answer is kept per radio (its USB id or driver, and the driver's version) with when it
was found, and asked again when the driver changes.

  follows-roam   Does the hotspot stay on its channel, or follow, when the WiFi link moves to an access
                 point on another channel? The link's profile is held to one (of the same network) for
                 the test, the hotspot's channel read, and the profile given back what it had.
  radio-reset    How long a radio reset takes (root/radio.py, as the uplink watchdog and crash watch do
                 it), and whether the hotspot comes back.
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
    "follows-roam": "Whether the hotspot stays put, or follows, when your WiFi roams to another channel.",
    "radio-reset": "How long a radio reset takes, and whether the hotspot comes back after it.",
    "driver-reset": "Whether the driver's own reset brings the WiFi back by itself, and how fast.",
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
        raise ValueError(f"not an experiment: {exp}")
    if not IFACE_RE.match(str(iface or "")):
        raise ValueError("not an interface name")
    radio = next((r for r in inv.get("radios", []) if r.get("iface") == iface and r.get("type") == "managed"), None)
    if not radio:
        raise ValueError(f"{iface} is not a WiFi link here")
    ap = next((r["iface"] for r in inv["radios"] if r.get("type") == "AP" and r.get("phy") == radio.get("phy")), None)
    if ap and _guests(ap):
        raise ValueError("guests are on the hotspot: try again when nobody is")
    if exp == "follows-roam":
        if not ap:
            raise ValueError("no hotspot on this radio, so there's nothing to follow")
        aps = (radio.get("roaming") or {}).get("aps") or []
        here = (radio.get("link") or {}).get("bssid")
        here_ch = (radio.get("link") or {}).get("channel")
        if not [a for a in aps if a.get("bssid") != here and a.get("channel") and a["channel"] != here_ch]:
            raise ValueError("your network has no access point on another channel in reach, so there's no roam to try")
    if exp == "driver-reset" and not (SYS / "module" / (radio.get("driver") or "?").replace("-", "_") / "parameters" / "fake_cmd_timeout").exists():
        raise ValueError("this driver offers no fake command timeout to try its own reset with")
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
        return "unknown", f"the move to {target['bssid']} (channel {target['channel']}) failed: {out[-120:]}"
    if link_ch == here_ch:
        return "unknown", f"the link did not move off channel {here_ch} (asked for {target['bssid']} on channel {target['channel']})"
    if after is None:
        from irate_box.root import radio as radio_mod
        said = radio_mod.restore_hotspot()
        return "doesn't", (f"the hotspot went down when the link moved to channel {link_ch}" + (f"; {said}" if said else ""))
    put_back = "" if back else " (the link could not be brought back up afterwards)"
    if after == link_ch and after != before:
        return "follows", f"the hotspot moved with the link from channel {before} to {after}; the move took {took} s{put_back}"
    if after == before:
        return "stays", f"the hotspot stayed on channel {before} while the link moved to {link_ch}: this radio runs both at once{put_back}"
    return "unknown", f"the hotspot went from channel {before} to {after}, the link to {link_ch}{put_back}"


def radio_reset(radio, ap, sleep=time.sleep):
    from irate_box.root import radio as radio_mod
    usb = radio.get("usb") or {}
    t0 = time.time()
    said = radio_mod.reset(radio["iface"], usb.get("port"), radio.get("driver"), usb.get("product"))
    up = _link_up(radio["iface"], 60, sleep)
    took = round(time.time() - t0)
    hotspot = (not ap) or ap in _channels()
    answer = "works" if up and hotspot else "doesn't"
    return answer, f"{said}; the link {'back' if up else 'not back'} {took} s after it began" + ("" if not ap else f", the hotspot {'back' if hotspot else 'not back'}")


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
        return "doesn't", "the driver did not reset its device (its count of resets did not move)"
    return ("works" if up else "doesn't"), f"the driver reset its device; the link was {'back' if up else 'not back'} {took} s after"


def experiment(inv, iface, exp, sleep=time.sleep):
    radio, ap = check_ready(inv, iface, exp)
    answer, detail = {"follows-roam": follows_roam, "radio-reset": radio_reset, "driver-reset": driver_reset}[exp](radio, ap, sleep)
    _keep(radio_key(radio), exp, answer, detail)
    return f"{QUESTIONS[exp]} {detail[0].upper() + detail[1:]}."
