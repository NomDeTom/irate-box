# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A new board's setup hotspot, root's half (route R1): the box joins `armbiansetup-<host>` (WPA2, the same
password `armbian1234` on every board), checks that the page it finds is armbian-web-config, posts the settings,
then leaves and goes back to the network it was on. The wizard writes the same Armbian presets as the card route.

The wizard's password is public, so the administrator's WiFi password is sent only after the page has been checked
for the form this box knows. The wizard keeps answering after it has been used until the board restarts, so the
finish stops it. The uplink watchdog is held for the step, with a hold that ends by itself if this dies halfway.
Root's own scan decides which hotspot is the board's (hub/rksetup.hotspots). Stdlib only.
"""

import json
import time
import urllib.error
import urllib.request

from irate_box.hub import rksetup, uplink
from irate_box.msg import M, Refused

WIZARD = "http://10.42.0.1:8080"
SETUP_PASSWORD = "armbian1234"  # nosec B105 - the same public password on every board
CONNECTION = "irate-box-board-setup"
HOLD_MINUTES = 5
# What armbian-web-config's page has when it is the wizard: its form's field names. Anything else is refused
# before the administrator's WiFi password is sent.
MARKERS = ("wifiSsid", "rootPass", "userName")


def run(*cmd, timeout=90):
    """Every command goes through here (the tests stand it in)."""
    import subprocess

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _get(path):
    """The wizard's page at path: (status, text)."""
    with urllib.request.urlopen(WIZARD + path, timeout=15) as answer:  # noqa: S310 - the board's fixed address
        return answer.status, answer.read(1 << 20).decode("utf-8", "replace")


def _post(body):
    """POST /api/setup, as the wizard's form does."""
    data = json.dumps(body).encode()
    asked = urllib.request.Request(
        WIZARD + "/api/setup", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(asked, timeout=30) as answer:  # noqa: S310 - the board's fixed address
        return answer.status, answer.read(1 << 16).decode("utf-8", "replace")


def active_wifi():
    """The name of the WiFi profile this box is on now, or "" (the hotspot is not it)."""
    got = run("nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active")
    for line in (got.stdout or "").splitlines():
        name, _, kind = line.partition(":")
        if kind == "802-11-wireless" and name and not name.startswith("irate-box-ap"):
            return name
    return ""


def free_radio():
    """A WiFi device not in use, for the step: a second adapter keeps the box's own link and hotspot up. "" when
    there is none, and the box's one radio is used."""
    got = run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status")
    for line in (got.stdout or "").splitlines():
        fields = line.split(":")
        if len(fields) >= 3 and fields[1] == "wifi" and fields[2] == "disconnected":
            return fields[0]
    return ""


def _hold_at(when):
    """The uplink watchdog's hold set to an epoch (0 releases it). A hold ends by itself, so a step that dies does
    not leave the box's link unwatched."""
    try:
        settings = uplink.load_settings()
        settings["hold_until"] = when
        uplink.save_settings(settings)
    except (OSError, ValueError):
        pass


def _previous_hold():
    try:
        return float(uplink.load_settings().get("hold_until") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def _body(choices):
    """What the wizard takes. The keys are left empty: it treats them as URLs, so the finish puts the box's key
    on the board over SSH instead."""
    return {
        "wifiSsid": choices.get("ssid") or "",
        "wifiPass": choices.get("wifi_password") or "",
        "wifiCountryCode": choices.get("country") or "",
        "ethEnabled": False,
        "useStaticIp": False,
        "locale": choices.get("locale") or "",
        "timezone": choices.get("timezone") or "",
        "rootPass": choices.get("root_password") or "",
        "rootKey": "",
        "userName": choices.get("user") or "armbian",
        "userPass": choices.get("user_password") or "",
        "userKey": "",
        "realName": choices.get("real_name") or "",
        "userShell": "/bin/bash",
    }


def setup(hotspot, choices, hold_minutes=HOLD_MINUTES):
    """Join the board's setup hotspot, check its page, send the choices, and leave. Returns what was done; the board
    is on the administrator's network next, for the finish."""
    spots = rksetup.hotspots()
    found = next((s for s in spots if s["ssid"] == hotspot), None)
    if not found:
        raise Refused(
            M(
                "rkspot.not-in-range",
                "That setup hotspot is not in range now. Power the board near the box and look at the list again.",
            )
        )
    previous = active_wifi()
    held = _previous_hold()
    _hold_at(time.time() + hold_minutes * 60)
    run("nmcli", "con", "delete", CONNECTION, timeout=60)
    radio = free_radio()
    joined = run(
        "nmcli",
        "con",
        "add",
        "type",
        "wifi",
        "con-name",
        CONNECTION,
        *(["ifname", radio] if radio else []),
        "ssid",
        found["ssid"],
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        SETUP_PASSWORD,
    )
    if joined.returncode != 0:
        _hold_at(held)
        raise Refused(M("rkspot.join", "The box could not join {ssid}.", ssid=found["ssid"]))
    try:
        up = run("nmcli", "con", "up", CONNECTION, timeout=120)
        if up.returncode != 0:
            raise Refused(M("rkspot.join", "The box could not join {ssid}.", ssid=found["ssid"]))
        try:
            status, page = _get("/")
        except (urllib.error.URLError, OSError) as exc:
            raise Refused(M("rkspot.no-page", "The board's setup page did not answer: {detail}", detail=str(exc)[:160]))
        if status != 200 or not all(marker in page for marker in MARKERS):
            raise Refused(
                M(
                    "rkspot.not-wizard",
                    "That hotspot is not the setup page this box knows: nothing was sent to it.",
                )
            )
        try:
            status, answer = _post(_body(choices))
        except (urllib.error.URLError, OSError) as exc:
            raise Refused(
                M("rkspot.send", "The board's setup page did not take the choices: {detail}", detail=str(exc)[:160])
            )
        if status not in (200, 201, 204):
            raise Refused(
                M(
                    "rkspot.send-status",
                    "The board's setup page did not take the choices: HTTP {status}.",
                    status=status,
                )
            )
    finally:
        run("nmcli", "con", "delete", CONNECTION, timeout=60)
        if previous:
            run("nmcli", "con", "up", previous, timeout=120)
        _hold_at(held)
    return {"ssid": found["ssid"], "host": found.get("host") or "", "answer": answer[:200]}
