# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's names, offline: the setting kept in hotspot.json and checked by the hub and again by
root, the first name in the hostapd lines and the NetworkManager keyfile (one network and two), the
second name set and left empty (then the suffixed default), the same name for both refused, and the
default when nothing is set. python3 tests/sim_hotspot_names.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="hotspot-names-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
)
(T / "state").mkdir(parents=True)
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box import validate
from irate_box.hub import hotspot
from irate_box.msg import Refused
from irate_box.root import ap

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


CAPS = {"known": True, "ap_radios": 2, "ap_slots": 2, "sae": True, "radios": []}
PLAN = {"iface": "wlan0", "kind": "own-radio", "channel": 6, "band": "2.4 GHz"}


def settings(**kw):
    return {
        "mode": "open",
        "password": "",
        "allow_wpa2": False,
        "second": "owe",
        "ssid": hotspot.DEFAULT_SSID,
        "second_ssid": "",
        **kw,
    }


def refused(raw, caps=CAPS):
    try:
        hotspot.validate(raw, caps)
    except Refused as exc:
        return exc.message
    return None


# The default when nothing is set, and the round trip.
check(
    "nothing set: Irate-Box, no second name",
    hotspot.load()["ssid"] == "Irate-Box" and hotspot.load()["second_ssid"] == "",
)
saved = hotspot.save(settings(ssid="Guests"), CAPS)
check("saved: the name is kept", saved["ssid"] == "Guests" and hotspot.load()["ssid"] == "Guests")

# A WPA3 password is matched whole: a trailing newline is a different password in hostapd's file
# than the one that was typed, so it is refused in the page's check and in what is already saved.
check("a password that is fine: accepted", refused(settings(mode="sae", password="abcdefgh")) is None)
for why, bad_password in (
    ("with a trailing newline", "abcdefgh\n"),
    ("with CR and LF", "abcdefgh\r\n"),
    ("of seven characters and a newline", "abcdefg\n"),
    ("with a NUL", "abcdefgh\x00"),
    ("of seven characters", "abcdefg"),
):
    check(f"a password {why}: refused", refused(settings(mode="sae", password=bad_password)) is not None)
check("the pattern: a trailing newline does not match", hotspot.PASSWORD_RE.fullmatch("abcdefgh\n") is None)

# One network: the name reaches the hostapd lines and the keyfile.
one = settings(ssid="Guests")
kf = ap.keyfile(PLAN, one)
conf = ap.hostapd_conf(PLAN, one)
check("one network: the keyfile's ssid", validate.keyfile_ssid("Guests") + "\n" in kf, kf)
check("  the hostapd lines' ssid", validate.hostapd_ssid("Guests") + "\n" in conf, conf)

# Two networks: the second name set, and left empty (then the suffixed default).
two = settings(mode="two", ssid="Guests", second_ssid="Guests-Enc")
nets = hotspot.networks(two, two["ssid"], two["second_ssid"])
check(
    "two networks: the open name and the encrypted one set",
    [n["ssid"] for _, n in nets] == ["Guests", "Guests-Enc"],
    nets,
)
two_empty = settings(mode="two", ssid="Guests", second_ssid="")
nets = hotspot.networks(two_empty, two_empty["ssid"], two_empty["second_ssid"])
check("  left empty: the suffixed default", [n["ssid"] for _, n in nets] == ["Guests", "Guests (encrypted)"], nets)
check(
    "  the first name is still the one the keyfile and hostapd use",
    validate.keyfile_ssid("Guests") + "\n" in ap.keyfile(PLAN, two)
    and validate.hostapd_ssid("Guests") + "\n" in ap.hostapd_conf(PLAN, two),
)

# The same name for both is refused, in plain words.
said = refused({"mode": "two", "ssid": "Same", "second_ssid": "Same"})
check(
    "the same name for both: refused",
    said and said["id"] == "hotspot.ssid.same" and "different names" in said["en"],
    said,
)
try:
    ap.keyfile(PLAN, settings(mode="two", ssid="Same", second_ssid="Same"))
    root_said = None
except Refused as exc:
    root_said = exc.message
check("  root refuses it too, before the keyfile", root_said and root_said["id"] == "hotspot.ssid.same", root_said)

# A name _ssid_ok refuses: too long, empty; with words.
for label, name in (("too long", "x" * 33), ("empty", "")):
    said = refused({"mode": "open", "ssid": name})
    check(
        f"a name {label}: refused with words",
        said and said["id"] == "hotspot.ssid.invalid" and "1 to 32 bytes" in said["en"],
        said,
    )
try:
    ap.keyfile(PLAN, {"mode": "open", "ssid": "x" * 33})
    root_said = None
except Refused as exc:
    root_said = exc.message
check(
    "  root refuses a name the hub would have refused",
    root_said and root_said["id"] == "hotspot.ssid.invalid",
    root_said,
)

# Nothing set: the default reaches both.
default = {"mode": "open"}
check(
    "nothing set: the default name reaches the keyfile and hostapd",
    validate.keyfile_ssid("Irate-Box") + "\n" in ap.keyfile(PLAN, default)
    and validate.hostapd_ssid("Irate-Box") + "\n" in ap.hostapd_conf(PLAN, default),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
