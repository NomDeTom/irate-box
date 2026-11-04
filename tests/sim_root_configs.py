# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's config writers go through the shared writers, and root re-checks the values it is asked
for: the hotspot's dnsmasq/hostapd files, the library drive's mount unit, service RAM drop-ins,
NetworkManager's drop-in, the country file, and the hotspot's own WPA password.
python3 tests/sim_root_configs.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import hotspot, nmconf, wificountry
from irate_box.msg import Refused
from irate_box.root import ap, hub_control, librarydrive, servicewrites

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


# --- filesystem UUIDs ---
check("fs_uuid: an ext4 UUID", V.fs_uuid("1234abcd-56ef-7890-abcd-1234567890ab"))
refused("fs_uuid: ..", V.fs_uuid, "../../dev/sda")
refused("fs_uuid: a slash", V.fs_uuid, "abc/def")
refused("fs_uuid: empty", V.fs_uuid, "")


# --- the hotspot's files ---
plan = {"kind": "own-radio", "iface": "wlan0", "band": "2.4 GHz", "channel": 6}
settings = {"mode": "open"}
dns = ap.dnsmasq_conf(plan)
check(
    "dnsmasq: the interface and DHCP lines as written by the writer",
    "interface=wlan0" in dns and "dhcp-range=192.168.4.10,192.168.4.200,255.255.255.0,12h" in dns,
    dns,
)
refused(
    "dnsmasq: an interface with a newline refused",
    ap.dnsmasq_conf,
    {"kind": "own-radio", "iface": "wlan0\ndhcp-authoritative", "band": "2.4 GHz", "channel": 6},
)
conf = ap.hostapd_conf(plan, settings, "Irate-Box", "GB")
check(
    "hostapd: the dynamic lines as written by the writer",
    "interface=wlan0" in conf and "channel=6" in conf and "country_code=GB" in conf and "ssid2=" in conf,
    conf,
)
refused(
    "hostapd: an interface with a newline refused",
    ap.hostapd_conf,
    {"kind": "own-radio", "iface": "wlan0\nssid2=00", "band": "2.4 GHz", "channel": 6},
    settings,
    "Irate-Box",
)

# The password is checked on the NetworkManager route too, where root reads the settings from state.
refused(
    "hotspot: the NetworkManager route refuses a short WPA3 password",
    hotspot.nm_properties,
    {"mode": "sae", "password": "short"},
    "sae",
)
check(
    "hotspot: a good WPA3 password reaches the keyfile property",
    hotspot.nm_properties({"mode": "sae", "password": "correct horse"}, "sae")["802-11-wireless-security.psk"]
    == "correct horse",
)


# --- the library drive's mount unit ---
unit = librarydrive._unit_text("1234abcd-56ef-7890-abcd-1234567890ab", "ext4")
check(
    "library drive: the mount unit as written by the writer",
    "What=/dev/disk/by-uuid/1234abcd-56ef-7890-abcd-1234567890ab" in unit
    and "Type=ext4" in unit
    and "Options=nosuid,nodev,noexec,nofail,x-systemd.device-timeout=10s" in unit,
    unit,
)
refused("library drive: a UUID with ../ refused", librarydrive._unit_text, "../../dev/sda", "ext4")
refused("library drive: a filesystem with a newline refused", librarydrive._unit_text, "abcd", "ext4\nOptions=rw")


# --- service RAM drop-ins and NetworkManager's drop-in ---
drop = servicewrites.dropin_text("ews.service", ["/var/lib/ews/cache"])
check("service RAM: the BindPaths line", "BindPaths=/run/irate-box-ram/ews/0:/var/lib/ews/cache" in drop, drop)
refused(
    "service RAM: a path with a newline refused",
    servicewrites.dropin_text,
    "ews.service",
    ["/var/lib/ews/cache\nExecStart=/bin/sh"],
)
net = nmconf.dropin_text({"powersave": "2"}, {"wlan0": {"powersave": "2"}})
check(
    "NetworkManager: the box and interface lines as written by the writer",
    "wifi.powersave=2" in net and "match-device=interface-name:wlan0" in net,
    net,
)
refused("NetworkManager: a value with a newline refused", nmconf.dropin_text, {"powersave": "2\nx"})
refused("NetworkManager: an interface with a newline refused", nmconf.dropin_text, {}, {"wlan0\nx": {"powersave": "2"}})
check("country: the module option file", "ieee80211_regdom=GB" in wificountry.conf_text("GB"))
refused("country: a newline refused", wificountry.conf_text, "GB\noptions cfg80211 x=1")


# --- root re-checks the hotspot's requested radio, band and channel ---
refused("root: a radio with a newline refused", hub_control._ap_owner, {"radio": "wlan0\n"})
refused("root: a channel of the wrong type refused", hub_control._ap_owner, {"channel": True})
refused("root: a channel outside 1–196 refused", hub_control._ap_owner, {"channel": 197})
check(
    "root: the owner's checked choices",
    hub_control._ap_owner({"radio": "wlan0", "band": "5 GHz", "channel": 36, "take_radio": True})
    == {"radio": "wlan0", "band": "5 GHz", "channel": 36, "take_radio": True},
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
