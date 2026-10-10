#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What the box has for networking: its radios, who runs each one, and what each can do.

Read-only. install.sh runs it once (`netinv.py --write`, then its summary in "Found on this
box"); /admin's Network page runs it again through hub_control.py ("net-scan"), for a new
dongle or another device; and it runs on its own on any Linux board, before anything is
installed, to see what irate-box would make of it:

    ./irate-box netinv                 a plain report
    ./irate-box netinv --iface wlan1   the same, for one device only
    ./irate-box netinv --json          the whole inventory as JSON
    ./irate-box netinv --write [PATH]  JSON to $HUB_STATE_DIR/control/netinv.json (or PATH)
    ./irate-box netinv summary         one line per point worth knowing, for install.sh

What it looks at:
  - each radio (`iw`): driver, bus (USB, SDIO, PCI), the modes it supports, whether it can run
    an access point beside a client link (its interface combinations) and on how many
    channels, power save, rfkill, the link it is on now
  - which stack runs each interface: NetworkManager, iwd, connman, wpa_supplicant (under
    ifupdown, systemd-networkd or init scripts), or nothing; netplan above any of them
  - the client profile's settings that decide how it reconnects (NetworkManager)
  - things that could take the radio away or get in the way: mPWRD-OS's wifisync (it
    rfkills the radio when meshtasticd's WiFi setting is off), another AP profile (Armbian's
    setup hotspot), USB autosuspend, no regulatory country
  - the verdicts the AP add-on and the uplink watchdog (uplink.py) use: which AP backend, in
    which mode (beside the client link, the radio alone, or only by taking it), and which
    repairs the uplink's stack supports

Root sees more (netplan's files, meshtasticd's config); without it those show as unknown.
Stdlib only, and no other irate-box module, so it can be copied to a board on its own.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
OUT = STATE / "control" / "netinv.json"
SYS_NET = Path("/sys/class/net")
AP_PROFILE = "irate-box-ap"  # the hub's own hotspot profile, when it exists
MESH_CONFIG = Path("/var/lib/meshtasticd/.portduino/default/prefs/config.proto")
# Driver options that bear on power save and roaming, read when the driver has them.
DRIVER_PARAMS = ("ps_on", "dpsm", "roamoff", "feature_disable", "power_save", "rtw_power_mgnt",
                 "rtw_ips_mode", "rtw_enusbss", "swcrypto", "ant_div", "tx_lft")


def run(*cmd, timeout=15):
    """(returncode, stdout); 127 when the command is not there."""
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def active(unit):
    return run("systemctl", "is-active", unit)[1].strip() == "active"


def enabled(unit):
    """enabled | disabled | static | ... | None when there is no such unit."""
    code, out = run("systemctl", "is-enabled", unit)
    out = out.strip()
    return out if out and out != "not-found" and "No such file" not in out else None


# --- radios (iw) --------------------------------------------------------------------------

COMBO_GROUP_RE = re.compile(r"#\{\s*([^}]*)\}\s*<=\s*(\d+)")


def parse_combos(lines):
    """The phy's "valid interface combinations" (the indented lines under that heading) as
    [{groups: [(modes, n)], total, channels}]."""
    combos = []
    for chunk in " ".join(l.strip() for l in lines).split("* ")[1:]:
        groups = [([m.strip() for m in modes.split(",") if m.strip()], int(n)) for modes, n in COMBO_GROUP_RE.findall(chunk)]
        total = re.search(r"total\s*<=\s*(\d+)", chunk)
        chans = re.search(r"#channels\s*<=\s*(\d+)", chunk)
        if groups:
            combos.append({"groups": groups, "total": int(total.group(1)) if total else sum(n for _, n in groups),
                           "channels": int(chans.group(1)) if chans else 1})
    return combos


def concurrent_ap(combos):
    """(can an AP run beside a client link, on up to how many channels at once)."""
    best = None
    for c in combos:
        if c["total"] < 2:
            continue
        man = [i for i, (modes, _) in enumerate(c["groups"]) if "managed" in modes]
        ap = [i for i, (modes, _) in enumerate(c["groups"]) if "AP" in modes]
        if any(a != b or c["groups"][a][1] >= 2 for a in man for b in ap):
            best = max(best or 0, c["channels"])
    return (best is not None), (best or 0)


def parse_phys(text):
    """`iw list` → {phyN: {...}}."""
    phys, name, block = {}, None, []

    def done():
        if name:
            phys[name] = parse_phy("\n".join(block))

    for line in text.splitlines():
        m = re.match(r"^Wiphy (\S+)", line)
        if m:
            done()
            name, block = m.group(1), []
        elif name:
            block.append(line)
    done()
    return phys


def parse_phy(text):
    modes, in_modes = [], False
    for line in text.splitlines():
        if re.match(r"^\tSupported interface modes:", line):
            in_modes = True
            continue
        if in_modes:
            m = re.match(r"^\t\t \* (.+)$", line)
            if m:
                modes.append(m.group(1).strip())
                continue
            in_modes = False
    freqs = [int(f) for f in re.findall(r"^\t\t\t\* (\d+)(?:\.\d+)? MHz(?! \[\d+\] \(disabled\))", text, re.M)]
    # Each channel the driver lists, with what stops an access point there: disabled, no initiating
    # radiation ("no IR": a client may answer, never start), or radar detection (DFS: an AP must
    # listen first and leave when radar appears, so not for a hotspot that has to stay up).
    chan_list = []
    for m in re.finditer(r"^\t\t\t\* (\d+)(?:\.\d+)? MHz \[(\d+)\](.*)$", text, re.M):
        flags = m.group(3)
        chan_list.append({"freq": int(m.group(1)), "channel": int(m.group(2)),
                         "ap_ok": not any(f in flags for f in ("disabled", "no IR", "radar detection", "passive scan"))})
    combo_lines, in_combos = [], False
    for line in text.splitlines():
        if line.strip() == "valid interface combinations:":
            in_combos = True
        elif in_combos and line.startswith("\t\t"):
            combo_lines.append(line)
        elif in_combos:
            break
    combos = parse_combos(combo_lines)
    conc, channels = concurrent_ap(combos)
    sched = re.search(r"max # sched scan SSIDs:\s*(\d+)", text)
    return {
        "modes": modes,
        "ap": "AP" in modes,
        "ap_beside_client": conc,
        "channels_at_once": channels,
        "bands": sorted({"2.4 GHz" if f < 3000 else "5 GHz" if f < 5925 else "6 GHz" for f in freqs}),
        "combinations": [{"groups": [{"modes": g, "max": n} for g, n in c["groups"]],
                          "total": c["total"], "channels": c["channels"]} for c in combos],
        "sched_scan": bool(sched and int(sched.group(1)) > 0),
        "sae": "SAE" in text and "Device supports SAE" in text,
        "channels": chan_list,
    }


def parse_iw_dev(text):
    """`iw dev` → {iface: {phy, type, ssid, channel, freq}}."""
    out, phy, cur = {}, None, None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^phy#(\d+)", s)
        if m:
            phy = f"phy{m.group(1)}"
            continue
        m = re.match(r"^Interface (\S+)", s)
        if m:
            cur = out.setdefault(m.group(1), {"phy": phy})
            continue
        if s.startswith("Unnamed/non-netdev interface"):
            # A P2P device (wpa_supplicant's p2p-dev-wlan0): not an interface, and its own type and address are
            # not the one above's (ap0 would read as "P2P-device" and the hotspot go unseen).
            cur = None
            continue
        if cur is None:
            continue
        for key, pat in (("type", r"^type (\S+)"), ("ssid", r"^ssid (.+)"), ("addr", r"^addr (\S+)")):
            m = re.match(pat, s)
            if m:
                cur[key] = m.group(1)
        m = re.match(r"^channel (\d+) \((\d+) MHz\)", s)
        if m:
            cur["channel"], cur["freq"] = int(m.group(1)), int(m.group(2))
    return out


def parse_link(text):
    """`iw dev X link` → {bssid, ssid, freq, signal, bitrate} or {} when not connected."""
    if not text.startswith("Connected to"):
        return {}
    link = {"bssid": text.split()[2]}
    for key, pat, conv in (("ssid", r"SSID: (.+)", str), ("freq", r"freq: (\d+)", int),
                           ("signal", r"signal: (-?\d+) dBm", int), ("bitrate", r"tx bitrate: ([\d.]+ MBit/s)", str)):
        m = re.search(pat, text)
        if m:
            link[key] = conv(m.group(1))
    if "freq" in link:
        link["channel"] = freq_channel(link["freq"])
    return link


def freq_channel(f):
    if f == 2484:
        return 14
    if 2412 <= f < 2484:
        return (f - 2407) // 5
    if 5000 <= f < 5925:
        return (f - 5000) // 5
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    return None


# --- roaming -----------------------------------------------------------------------------------------------
# Which access points share the uplink's network name, the background scan that makes it roam, and which
# of the owner's choices can work here: roaming as the WiFi stack does, always (and ignoring short roams, the
# watchdog's own); no background scans where wpa_supplicant takes them live (its control
# socket); a lock to one access point wherever NetworkManager runs it.
ROAMING = ("roam", "no-scan", "lock")


def parse_wifi_list(text, ssid):
    """`nmcli -t -f BSSID,CHAN,FREQ,SIGNAL,SSID dev wifi list` → the access points named ssid, strongest first."""
    aps = []
    for f in nm_fields(text):
        if len(f) >= 5 and f[4] == ssid and re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", f[0]):
            freq = int(re.match(r"\d+", f[2]).group()) if re.match(r"\d+", f[2]) else None
            aps.append({"bssid": f[0].lower(), "channel": int(f[1]) if f[1].isdigit() else None, "freq": freq,
                        "signal": int(f[3]) if f[3].isdigit() else None})
    return sorted(aps, key=lambda a: -(a["signal"] or 0))


def wpa_bgscan(iface, run_=None):
    """The background scan wpa_supplicant runs for the network in use, and whether it can be changed live:
    (value or None, writable)."""
    run_ = run_ or run
    if not shutil.which("wpa_cli"):
        return None, False
    code, out = run_("wpa_cli", "-i", iface, "status")
    m = re.search(r"^id=(\d+)$", out, re.M) if code == 0 else None
    if not m:
        return None, False
    code, out = run_("wpa_cli", "-i", iface, "get_network", m.group(1), "bgscan")
    if code != 0 or out.strip().startswith("FAIL"):
        return None, True
    return out.strip().strip('"'), True


def roaming_facts(r, nm, run_=None):
    """For a client radio: the access points sharing its network, the background scan, and the choices that work."""
    run_ = run_ or run
    ssid = (r.get("link") or {}).get("ssid")
    aps = parse_wifi_list(run_("nmcli", "-t", "-f", "BSSID,CHAN,FREQ,SIGNAL,SSID", "dev", "wifi", "list", "ifname", r["iface"],
                               "--rescan", "no")[1], ssid) if ssid and r.get("owner") == "networkmanager" else []
    bgscan, live = wpa_bgscan(r["iface"], run_) if r.get("owner") in ("networkmanager", "wpa_supplicant") else (None, False)
    why = {}
    if r.get("owner") != "networkmanager":
        why["lock"] = "NetworkManager does not run this link"
    if not live:
        why["no-scan"] = "wpa_supplicant's control socket is not there to change the background scan"
    return {"ssid": ssid, "aps": aps, "channels": sorted({a["channel"] for a in aps if a["channel"]}),
            "bgscan": bgscan, "nm_version": (nm or {}).get("version"),
            "choices": [c for c in ROAMING if c not in why], "why": why}


def device_facts(iface):
    """Driver, bus and USB identity of a network interface, from sysfs."""
    dev = SYS_NET / iface / "device"
    facts = {"driver": None, "bus": None}
    if not dev.exists():
        return facts
    real = dev.resolve()
    path = str(real)
    facts["bus"] = "usb" if "/usb" in path else "sdio" if "mmc" in path else "pci" if "/pci" in path else "platform"
    drv = (dev / "driver")
    name = drv.resolve().name if drv.exists() else None
    if name in (None, "usb"):
        # The netdev hangs off the USB device, and the WiFi driver is bound to one of its
        # interfaces -- beside, on a combined chip, a Bluetooth one (btusb). The WiFi one is
        # the one that registered with cfg80211.
        wifi_mods = {h.name for m in ("cfg80211", "mac80211") for h in (Path("/sys/module") / m / "holders").glob("*")}
        bound = [(child / "driver").resolve().name for child in sorted(real.glob("*:*")) if (child / "driver").exists()]
        name = next((b for b in bound if b.replace("-", "_") in wifi_mods), None)
        if not name and len(wifi_mods - {"mac80211"}) == 1:
            name = next(iter(wifi_mods - {"mac80211"}))
    mod = (dev / "driver" / "module")
    if mod.exists() and mod.resolve().name != "usbcore":
        name = mod.resolve().name
    facts["driver"] = name
    if facts["bus"] == "usb":
        usb = real if (real / "idVendor").exists() else real.parent
        if (usb / "idVendor").exists():
            facts["usb"] = {"id": f"{read(usb / 'idVendor')}:{read(usb / 'idProduct')}",
                            "port": usb.name, "product": read(usb / "product"),
                            "autosuspend": read(usb / "power" / "control") == "auto"}
    if name:
        params = {}
        pdir = Path("/sys/module") / name.replace("-", "_") / "parameters"
        for p in DRIVER_PARAMS:
            v = read(pdir / p)
            if v is not None:
                params[p] = v
        if params:
            facts["params"] = params
    return facts


def rfkill_for(phy):
    for r in Path("/sys/class/rfkill").glob("rfkill*"):
        if read(r / "name") == phy:
            return {"soft": read(r / "soft") == "1", "hard": read(r / "hard") == "1"}
    return None


# --- who runs what ------------------------------------------------------------------------

def nm_fields(text):
    """nmcli -t output (one "a:b:c" per line, colons in values escaped as \\:) → rows of fields."""
    rows = []
    for line in text.splitlines():
        fields, cur, esc = [], "", False
        for ch in line:
            if esc:
                cur += ch
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == ":":
                fields.append(cur)
                cur = ""
            else:
                cur += ch
        fields.append(cur)
        rows.append(fields)
    return rows


def nm_keyvals(text):
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k] = v.replace("\\:", ":")
    return out


PROFILE_FIELDS = ("connection.id", "connection.uuid", "connection.autoconnect", "connection.autoconnect-priority",
                  "connection.autoconnect-retries", "connection.auth-retries", "802-11-wireless.ssid",
                  "802-11-wireless.mode", "802-11-wireless.bssid", "802-11-wireless.band",
                  "802-11-wireless.powersave", "ipv4.dhcp-timeout", "ipv4.method", "connection.interface-name",
                  "connection.metered", "802-11-wireless.cloned-mac-address", "802-11-wireless.channel",
                  "802-11-wireless.hidden")


CLONED_MODES = ("preserve", "permanent", "random", "stable", "stable-ssid")


def cloned_mode(value):
    """The profile's MAC address choice as a mode; an address set by hand is said to be one, not repeated."""
    if not value:
        return None
    return value if value in CLONED_MODES else "fixed"


def nm_profile(ref):
    code, out = run("nmcli", "-t", "-f", ",".join(PROFILE_FIELDS), "con", "show", ref)
    if code:
        return None
    kv = nm_keyvals(out)
    words = {"default": 0, "ignore": 1, "disable": 2, "enable": 3}

    def num(k):
        v = (kv.get(k) or "").split(" ")[0]
        return int(v) if re.fullmatch(r"-?\d+", v) else words.get(v)
    return {"name": kv.get("connection.id"), "uuid": kv.get("connection.uuid"),
            "autoconnect": kv.get("connection.autoconnect") == "yes",
            "priority": num("connection.autoconnect-priority"),
            "autoconnect_retries": num("connection.autoconnect-retries"),
            "auth_retries": num("connection.auth-retries"),
            "ssid": kv.get("802-11-wireless.ssid") or None, "mode": kv.get("802-11-wireless.mode") or None,
            "bssid_lock": kv.get("802-11-wireless.bssid") or None, "band_lock": kv.get("802-11-wireless.band") or None,
            "powersave": num("802-11-wireless.powersave"), "dhcp_timeout": num("ipv4.dhcp-timeout"),
            "ipv4": kv.get("ipv4.method"), "iface": kv.get("connection.interface-name") or None,
            "metered": kv.get("connection.metered") or None, "cloned": cloned_mode(kv.get("802-11-wireless.cloned-mac-address")),
            "channel": num("802-11-wireless.channel"), "hidden": kv.get("802-11-wireless.hidden") == "yes",
            "netplan": (kv.get("connection.id") or "").startswith("netplan-")}


NETPLAN_PROFILES = "/run/NetworkManager/system-connections/netplan-"


def nm_state():
    code, out = run("nmcli", "-t", "-f", "RUNNING,VERSION", "general")
    if code:
        return None
    row = (nm_fields(out) or [[""]])[0]
    if row[0] != "running":
        return {"running": False}
    devices = {}
    for f in nm_fields(run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev")[1]):
        if len(f) >= 4:
            devices[f[0]] = {"type": f[1], "state": f[2], "connection": f[3] or None}
    profiles = []
    for f in nm_fields(run("nmcli", "-t", "-f", "NAME,UUID,TYPE,FILENAME", "con", "show")[1]):
        if len(f) >= 3 and f[2] == "802-11-wireless":
            p = nm_profile(f[1])
            if p:
                p["file"] = f[3] if len(f) > 3 and f[3] else None
                # Made by netplan (/run, written afresh at every boot): a change made with nmcli does not last.
                if p["file"]:
                    p["netplan"] = p["file"].startswith(NETPLAN_PROFILES)
                profiles.append(p)
    from irate_box.hub import nmconf
    return {"running": True, "version": row[1] if len(row) > 1 else None, "devices": devices,
            "wifi_profiles": profiles, "defaults": nmconf.effective()}


def processes():
    """[(comm, argv)] for every process."""
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")
            out.append(((p / "comm").read_text().strip(), [a.decode(errors="replace") for a in argv if a]))
        except OSError:
            pass
    return out


def wpa_ifaces(procs):
    """Interfaces a wpa_supplicant was started on with -i, and whether one runs for D-Bus (-u)."""
    ifaces, dbus = set(), False
    for comm, argv in procs:
        if comm != "wpa_supplicant":
            continue
        dbus |= "-u" in argv
        for i, a in enumerate(argv):
            if a == "-i" and i + 1 < len(argv):
                ifaces.add(argv[i + 1])
            elif a.startswith("-i") and len(a) > 2:
                ifaces.add(a[2:])
    return ifaces, dbus


def ifupdown_ifaces():
    found = set()
    for f in [Path("/etc/network/interfaces"), *Path("/etc/network/interfaces.d").glob("*")]:
        for m in re.finditer(r"^\s*(?:iface|auto|allow-hotplug)\s+(.+)$", read(f, "") or "", re.M):
            found.update(w for w in m.group(1).split() if w not in ("inet", "inet6", "dhcp", "static", "manual"))
    return found


def networkd_ifaces():
    if not active("systemd-networkd"):
        return set()
    out = set()
    for line in run("networkctl", "list", "--no-legend", "--no-pager")[1].splitlines():
        f = line.split()
        if len(f) >= 5 and f[4] not in ("unmanaged", "-"):
            out.add(f[1])
    return out


def netplan_facts():
    files = sorted(Path("/etc/netplan").glob("*.yaml")) if Path("/etc/netplan").is_dir() else []
    if not files:
        return None
    renderers, readable = set(), True
    for f in files:
        text = read(f)
        if text is None:
            readable = False
            continue
        renderers.update(re.findall(r"^\s*renderer:\s*(\S+)", text, re.M))
    return {"files": [f.name for f in files], "renderers": sorted(renderers) or (["networkd"] if readable else []),
            "readable": readable}


def owner_of(iface, nm, wpa, wpa_dbus, ifupdown, networkd):
    """Which stack runs iface: networkmanager | iwd | connman | wpa_supplicant | none."""
    if nm and nm.get("running") and iface in nm["devices"] and nm["devices"][iface]["state"] not in ("unmanaged", "unavailable"):
        return "networkmanager"
    if active("iwd"):
        return "iwd"
    if active("connman"):
        return "connman"
    if iface in wpa or active(f"wpa_supplicant@{iface}") or (wpa_dbus and not (nm and nm.get("running"))):
        return "wpa_supplicant"
    if iface in ifupdown or iface in networkd:
        return "wpa_supplicant" if (SYS_NET / iface / "wireless").exists() or (SYS_NET / iface / "phy80211").exists() else "ifupdown" if iface in ifupdown else "networkd"
    return "none"


def manager_of(iface, ifupdown, networkd):
    """What brings the address up beside wpa_supplicant: ifupdown | networkd | dhcpcd | ?."""
    if iface in ifupdown:
        return "ifupdown"
    if iface in networkd:
        return "networkd"
    if active("dhcpcd"):
        return "dhcpcd"
    return None


# --- things in the way --------------------------------------------------------------------

def _varint(data, i):
    v, shift = 0, 0
    while i < len(data):
        b = data[i]
        i += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, i
        shift += 7
    raise ValueError("bad varint")


def _proto_field(data, want, wire):
    """The last value of field `want` (varint, or bytes for wire type 2) in a protobuf message."""
    i, found = 0, None
    while i < len(data):
        key, i = _varint(data, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(data, i)
        elif wt == 2:
            n, i = _varint(data, i)
            v, i = data[i:i + n], i + n
        elif wt == 1:
            v, i = None, i + 8
        elif wt == 5:
            v, i = None, i + 4
        else:
            raise ValueError("bad wire type")
        if num == want and wt == wire:
            found = v
    return found


def mesh_wifi_enabled():
    """meshtasticd's config.network.wifi_enabled (field 4 → 1), or None when unreadable."""
    try:
        net = _proto_field(MESH_CONFIG.read_bytes(), 4, 2)
        return bool(_proto_field(net, 1, 0)) if net is not None else False
    except (OSError, ValueError, TypeError):
        return None


def reg_country():
    m = re.search(r"country (\S+):", run("iw", "reg", "get")[1])
    return m.group(1) if m else None


def default_route():
    code, out = run("ip", "-4", "route", "show", "default")
    best = None
    for line in out.splitlines():
        m = re.search(r"via (\S+) dev (\S+)", line)
        metric = int((re.search(r"metric (\d+)", line) or [0, 0])[1])
        if m and (best is None or metric < best[2]):
            best = (m.group(1), m.group(2), metric)
    return {"gateway": best[0], "iface": best[1]} if best else None


def wired():
    out = []
    for d in sorted(SYS_NET.iterdir()) if SYS_NET.is_dir() else []:
        if d.name == "lo" or (d / "wireless").exists() or (d / "phy80211").exists() or not (d / "device").exists():
            continue
        out.append({"iface": d.name, "carrier": read(d / "carrier") == "1", **device_facts(d.name)})
    return out


def ap_stations(iface):
    return sum(1 for line in run("iw", "dev", iface, "station", "dump")[1].splitlines() if line.startswith("Station"))


# --- the inventory ------------------------------------------------------------------------

def scan(focus=None):
    """The whole inventory as a dict. focus: one interface (or phy) to report on alone."""
    procs = processes()
    wpa, wpa_dbus = wpa_ifaces(procs)
    nm = nm_state()
    ifupdown, networkd = ifupdown_ifaces(), networkd_ifaces()
    iw_ok = shutil.which("iw") is not None
    phys = parse_phys(run("iw", "list")[1]) if iw_ok else {}
    ifaces = parse_iw_dev(run("iw", "dev")[1]) if iw_ok else {}
    route = default_route()
    stacks = {
        "networkmanager": nm,
        "iwd": {"running": active("iwd")} if shutil.which("iwctl") or active("iwd") else None,
        "connman": {"running": active("connman")} if shutil.which("connmanctl") else None,
        "wpa_supplicant": {"version": (re.search(r"v(\S+)", run("wpa_supplicant", "-v")[1] or "") or [None, None])[1],
                           "on": sorted(wpa), "dbus": wpa_dbus} if shutil.which("wpa_supplicant") else None,
        "systemd_networkd": {"running": active("systemd-networkd"), "manages": sorted(networkd)},
        "ifupdown": {"manages": sorted(ifupdown)} if ifupdown else None,
        "dhcpcd": {"running": active("dhcpcd")} if shutil.which("dhcpcd") else None,
        "netplan": netplan_facts(),
        "hostapd": {"installed": bool(shutil.which("hostapd")), "running": any(c == "hostapd" for c, _ in procs)},
        "dnsmasq": {"installed": bool(shutil.which("dnsmasq"))},
    }

    radios = []
    for iface, info in sorted(ifaces.items()):
        phy = info.get("phy")
        if focus and focus not in (iface, phy):
            continue
        cap = phys.get(phy, {})
        owner = owner_of(iface, nm, wpa, wpa_dbus, ifupdown, networkd)
        r = {"iface": iface, "phy": phy, "type": info.get("type"), **device_facts(iface), **cap,
             "owner": owner, "manager": manager_of(iface, ifupdown, networkd) if owner == "wpa_supplicant" else None,
             "rfkill": rfkill_for(phy)}
        if info.get("type") == "managed":
            r["link"] = parse_link(run("iw", "dev", iface, "link")[1])
            ps = re.search(r"Power save: (\S+)", run("iw", "dev", iface, "get", "power_save")[1])
            r["power_save"] = ps.group(1) if ps else None
            if owner == "networkmanager":
                conn = nm["devices"].get(iface, {}).get("connection")
                r["profile"] = next((p for p in nm["wifi_profiles"] if p["name"] == conn), None) if conn else None
            if r["link"]:
                r["roaming"] = roaming_facts(r, nm)
        elif info.get("type") == "AP":
            r["stations"] = ap_stations(iface)
        radios.append(r)
    # The defaults each WiFi interface gets, as NetworkManager finds them (irate-box's per-interface ones among them).
    if nm and nm.get("running") and nm.get("defaults"):
        from irate_box.hub import nmconf
        nm["defaults"]["ifaces"] = {r["iface"]: nmconf.effective_for(r["iface"], r.get("driver"))
                                    for r in radios if r.get("type") == "managed"}
        nm["defaults"]["ifaces_mine"] = nmconf.current_ifaces()
    # The hotspot on the same radio as a roaming link moves with it.
    for r in radios:
        if r.get("roaming") is not None:
            r["roaming"]["hotspot_shares"] = any(a.get("type") == "AP" and a.get("phy") == r.get("phy") for a in radios)

    inv = {"at": time.time(), "host": read("/etc/hostname"), "os": _os_name(), "root": os.geteuid() == 0,
           "focus": focus, "iw": iw_ok, "stacks": stacks, "radios": radios, "wired": wired(),
           "default_route": route, "country": reg_country() if iw_ok else None}
    if inv["root"]:
        # Connections netplan makes, and whether each can be handed over to NetworkManager (root reads netplan's files).
        try:
            from irate_box.root import nmhandover
            inv["handover"] = nmhandover.candidates()
        except (OSError, ValueError, ImportError) as exc:
            inv["handover"] = {"error": str(exc)[:200]}
    inv["hazards"] = hazards(inv)
    inv["uplink"] = uplink_verdict(inv)
    inv["ap"] = ap_verdicts(inv)
    return inv


def _os_name():
    text = read("/etc/os-release", "") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)', text, re.M)
    rel = read("/etc/mPWRD-release")
    return (m.group(1) if m else None) if not rel else f"mPWRD-OS ({m.group(1) if m else 'Debian'})"


def _h(hid, status, title, detail, fix="", iface=None):
    """A finding; iface: the device it is about, so /admin shows it on that device's card."""
    out = {"id": hid, "status": status, "title": title, "detail": detail, "fix": fix}
    if iface:
        out["iface"] = iface
    return out


NM_RETRIES = {"auth_retries": 3, "autoconnect_retries": 4}


def effective_retries(profile, key, defaults):
    """(the retries NetworkManager uses for this profile: 0 forever, else a count; where that is set)."""
    own = profile.get(key)
    if own not in (None, -1):
        return own, "this network's own profile"
    d = ((defaults or {}).get("settings") or {}).get(key) or {}
    v = d.get("value")
    if v is not None and re.fullmatch(r"-?\d+", str(v)) and int(v) >= 0:
        return int(v), f"the box default ({d.get('file')})"
    return NM_RETRIES[key], "NetworkManager's own default"


def hazards(inv):
    out = []
    radios = [r for r in inv["radios"] if r.get("type") in ("managed", "AP")]
    ws = enabled("wifisync.service")
    if ws:
        mesh = mesh_wifi_enabled()
        on = ws == "enabled"
        status = "problem" if on and mesh is False else "warn" if on else "ok"
        out.append(_h("wifisync", status, "mPWRD-OS wifisync",
                      f"{'Enabled' if on else 'Installed, disabled'}: it switches the WiFi radio off (rfkill) whenever "
                      f"meshtasticd's WiFi setting is off. meshtasticd's WiFi is "
                      f"{'on' if mesh else 'off' if mesh is False else 'unknown (needs root)'}.",
                      "Keep Meshtastic's WiFi setting on while the box uses its WiFi (client link or hotspot)."
                      if on else ""))
    nm = inv["stacks"].get("networkmanager") or {}
    for p in nm.get("wifi_profiles", []) if nm.get("running") else []:
        if p.get("mode") == "ap" and p["name"] != AP_PROFILE:
            out.append(_h(f"other-ap:{p['uuid']}", "warn" if p["autoconnect"] else "ok", f"Another hotspot profile: {p['name']}",
                          f"SSID {p['ssid']!r} on {p.get('iface') or 'any WiFi device'}, autoconnect "
                          f"{'on: it could take the radio at boot' if p['autoconnect'] else 'off'}. Not irate-box's; left as it is."))
        if p.get("mode") in (None, "infrastructure"):
            for key, field, gives_up in (
                    ("auth_retries", "auth-retries",
                     "After 3 failed handshakes (a flaky link looks like a wrong password) NetworkManager stops trying "
                     "until someone reconnects it by hand, which a headless box cannot ask for."),
                    ("autoconnect_retries", "autoconnect-retries",
                     "After 4 failed tries NetworkManager stops trying this network for 5 minutes.")):
                value, where = effective_retries(p, key, nm.get("defaults"))
                if value != 0:
                    out.append(_h(f"{field}:{p['uuid']}", "warn",
                                  f"WiFi network {p['ssid'] or p['name']}: stops trying after {value} failure{'s' if value != 1 else ''}"
                                  if value and value > 0 else f"WiFi network {p['ssid'] or p['name']}: gives up after repeated failures",
                                  f"{gives_up} Set by {where}.",
                                  "Network page, Box defaults: \"Keep trying\" forever, for every network.",
                                  iface=p.get("iface")))
    for r in radios:
        rf = r.get("rfkill")
        if rf and (rf["soft"] or rf["hard"]):
            out.append(_h(f"rfkill:{r['iface']}", "problem", f"{r['iface']}: radio switched off",
                          f"rfkill: {'hard' if rf['hard'] else 'soft'} blocked.",
                          "A hard block is a switch or the board; a soft one: rfkill unblock wifi (and see wifisync).",
                          iface=r["iface"]))
        usb = r.get("usb") or {}
        if usb.get("autosuspend"):
            out.append(_h(f"autosuspend:{r['iface']}", "warn", f"{r['iface']}: USB autosuspend on",
                          "The kernel may suspend the USB radio when idle, which some drivers do not survive.",
                          f"echo on > /sys/bus/usb/devices/{usb.get('port')}/power/control (a udev rule keeps it).",
                          iface=r["iface"]))
        if r.get("power_save") == "on":
            out.append(_h(f"powersave:{r['iface']}", "warn", f"{r['iface']}: power save on",
                          "Power save makes a link slower to answer and, on some drivers, drop.",
                          "NetworkManager: wifi.powersave = 2; otherwise iw dev IFACE set power_save off.",
                          iface=r["iface"]))
        params = r.get("params") or {}
        if r.get("power_save") == "off" and params.get("ps_on") in ("Y", "1"):
            out.append(_h(f"driver-ps:{r['iface']}", "ok", f"{r['iface']}: driver loaded with ps_on",
                          f"{r.get('driver')} has its own power-save option on, while iw reports power save off. "
                          "Which one wins is not known yet (a test in the AP research).", iface=r["iface"]))
        p = r.get("profile") or {}
        if p.get("netplan"):
            ho = inv.get("handover") or {}
            if not ho.get("integrated"):
                out.append(_h(f"netplan:{r['iface']}", "warn", f"{r['iface']}: its connection is written afresh by netplan at every boot",
                              f"{p['name']} comes from /etc/netplan, and this NetworkManager cannot write changes back there: a "
                              "setting made for this connection alone is lost at the next boot. Box defaults still apply.",
                              "Network page, WiFi: hand it over to NetworkManager, so its own settings last (undo puts it back).",
                              iface=r["iface"]))
    if inv.get("country") in ("00", None) and radios:
        out.append(_h("regdom", "warn", "No WiFi country set",
                      "The radio uses the world-safe channel set, which limits a hotspot's channels and power.",
                      "Set it in the OS (netplan regulatory-domain, raspi-config, or iw reg set XX)."))
    hap = inv["stacks"].get("hostapd") or {}
    if hap.get("running"):
        out.append(_h("hostapd", "warn", "hostapd is already running",
                      "Something on the box already runs an access point with hostapd; irate-box's would compete for the radio."))
    return out


def uplink_verdict(inv):
    """How the box reaches the home network, and which repairs uplink.py can do there."""
    route = inv.get("default_route")
    radios = {r["iface"]: r for r in inv["radios"]}
    if route:
        iface = route["iface"]
    else:
        # No route now: the WiFi client interface it would use.
        iface = next((r["iface"] for r in inv["radios"] if r.get("type") == "managed"), None)
    if not iface:
        return {"iface": None, "kind": None, "backend": None, "repairs": [], "detail": "No network link found."}
    r = radios.get(iface)
    kind = "wifi" if r else "wired"
    if r:
        backend = r["owner"]
    else:
        nm = inv["stacks"].get("networkmanager") or {}
        backend = "networkmanager" if nm.get("running") and nm["devices"].get(iface, {}).get("state") not in (None, "unmanaged") \
            else manager_of(iface, set((inv["stacks"].get("ifupdown") or {}).get("manages", [])),
                            set(inv["stacks"]["systemd_networkd"]["manages"])) or "none"
    repairs = {"networkmanager": ["reconnect", "restart", "pin"], "wpa_supplicant": ["reconnect", "restart"],
               "ifupdown": ["reconnect", "restart"], "networkd": ["reconnect", "restart"],
               "dhcpcd": ["restart"]}.get(backend, [])
    if r and r.get("bus") in ("usb", "sdio", "pci") and r.get("driver"):
        repairs = repairs + ["radio"]
    if repairs:
        repairs = repairs + ["reboot"]
    detail = {"iwd": "iwd runs this interface; uplink.py does not drive iwd yet (watch only).",
              "connman": "connman runs this interface; uplink.py does not drive connman yet (watch only).",
              "none": "Nothing that uplink.py knows runs this interface (watch only)."}.get(backend, "")
    return {"iface": iface, "kind": kind, "backend": backend, "gateway": route["gateway"] if route else None,
            "profile": (r or {}).get("profile", {}) and r["profile"].get("name"), "repairs": repairs, "detail": detail}


def ap_verdicts(inv):
    """For each radio: could irate-box run its hotspot there, how, and at what cost. detail is
    one sentence (the doctor, install.sh's summary); conditions the same in parts, each
    {text, kind}: kind "how" (how it would work), "limit", "untested" or "needs" (/admin lists
    them one per line)."""
    out = []
    up = inv.get("uplink") or {}
    nm_on = bool((inv["stacks"].get("networkmanager") or {}).get("running"))
    hostapd = (inv["stacks"].get("hostapd") or {}).get("installed")
    seen = set()
    for r in inv["radios"]:
        if r.get("phy") in seen or r.get("type") not in ("managed", "AP"):
            continue
        seen.add(r.get("phy"))
        if not r.get("ap"):
            out.append({"phy": r["phy"], "iface": r["iface"], "possible": False, "mode": None, "backend": None,
                        "detail": f"{r.get('driver') or 'This radio'} cannot be an access point.",
                        "conditions": [{"kind": "limit", "text": f"{r.get('driver') or 'This radio'} cannot be an access point."}]})
            continue
        is_uplink = up.get("iface") == r["iface"] and up.get("kind") == "wifi"
        conditions = []
        if not is_uplink:
            mode, detail = "radio-alone", "The radio is not the box's link to the network: the hotspot can have it to itself."
            conditions.append({"kind": "how", "text": "This radio isn't the box's link to your network, so the hotspot can have it to itself."})
        elif r.get("ap_beside_client"):
            mode = "beside-client"
            detail = (f"A second interface beside the client link, which stays up. Up to {r['channels_at_once']} channels at once"
                      + (" (whether the hotspot can stay put when the client link roams to another channel is untested)."
                         if r["channels_at_once"] > 1 else ": the hotspot must follow the client link's channel."))
            conditions.append({"kind": "how", "text": "It can run the hotspot and stay on your WiFi at the same time, as a second interface beside the link."})
            if r["channels_at_once"] > 1:
                conditions.append({"kind": "limit", "text": f"It can use up to {r['channels_at_once']} channels at once, so the hotspot need not follow your WiFi's channel."})
                conditions.append({"kind": "untested", "text": "Whether the hotspot stays put when your WiFi roams to another channel."})
            else:
                conditions.append({"kind": "limit", "text": "It works on one channel at a time, so the hotspot follows your WiFi's channel."})
        else:
            mode, detail = "takes-radio", "Only by taking the radio from the client link: the box would leave the home network."
            conditions.append({"kind": "limit", "text": "Only by taking the radio from your WiFi: the box would leave your network while the hotspot runs."})
        owner = r["owner"]
        if owner == "networkmanager" or (owner == "none" and nm_on):
            backend = "networkmanager"
        elif owner in ("wpa_supplicant", "none"):
            backend = "hostapd"
            if not hostapd:
                detail += " Needs hostapd (apt install hostapd)."
                conditions.append({"kind": "needs", "text": "hostapd, which isn't installed (apt install hostapd)."})
        else:
            backend = None
            detail += f" {owner} runs this radio, and irate-box has no {owner} backend for the hotspot yet."
            conditions.append({"kind": "limit", "text": f"{owner} runs this radio, and irate-box can't run a hotspot through {owner} yet."})
        out.append({"phy": r["phy"], "iface": r["iface"], "possible": backend is not None, "mode": mode,
                    "backend": backend, "detail": detail, "conditions": conditions})
    return out


def write(inv, path=OUT):
    path = Path(path)
    # As root, in control/, which the hub can change: never through a link it planted.
    from irate_box.root import safeio
    path.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(path, json.dumps(inv, indent=2))


# --- plain text -------------------------------------------------------------------------

def summary(inv):
    """The few lines install.sh prints: the link, each radio's hotspot verdict, the hazards."""
    lines = []
    up = inv["uplink"]
    if up.get("iface"):
        lines.append(f"network  {up['iface']} ({up['kind']}), run by {up['backend']}"
                     + (f"; profile {up['profile']}" if up.get("profile") else "")
                     + (f". {up['detail']}" if up.get("detail") else ""))
    else:
        lines.append("network  no link found")
    for a in inv["ap"]:
        r = next((x for x in inv["radios"] if x["phy"] == a["phy"]), {})
        what = f"{a['iface']} ({r.get('driver') or '?'}{', ' + r['bus'].upper() if r.get('bus') else ''})"
        if not a["possible"]:
            lines.append(f"hotspot  {what}: no. {a['detail']}")
        else:
            lines.append(f"hotspot  {what}: yes, {a['mode'].replace('-', ' ')}, through {a['backend']}. {a['detail']}")
    for h in inv["hazards"]:
        if h["status"] != "ok":
            lines.append(f"{'problem' if h['status'] == 'problem' else 'note   '}  {h['title']}: {h['detail']}")
    return lines


def report(inv):
    out = [f"{inv.get('host') or 'this box'}: {inv.get('os') or 'Linux'}"
           + ("" if inv["root"] else "  (not root: some details unknown)")]
    nm = inv["stacks"].get("networkmanager")
    st = inv["stacks"]
    stack = []
    if nm and nm.get("running"):
        stack.append(f"NetworkManager {nm.get('version')}")
    for key, label in (("iwd", "iwd"), ("connman", "connman"), ("dhcpcd", "dhcpcd")):
        if (st.get(key) or {}).get("running"):
            stack.append(label)
    if st.get("wpa_supplicant"):
        stack.append(f"wpa_supplicant {st['wpa_supplicant'].get('version') or ''}".strip())
    if st["systemd_networkd"]["manages"]:
        stack.append("systemd-networkd (" + ", ".join(st["systemd_networkd"]["manages"]) + ")")
    if st.get("ifupdown"):
        stack.append("ifupdown (" + ", ".join(st["ifupdown"]["manages"]) + ")")
    if st.get("netplan"):
        stack.append("netplan → " + ("/".join(st["netplan"]["renderers"]) or "? (its files need root)"))
    out.append("stack:   " + (", ".join(stack) or "nothing recognised"))
    if not inv["iw"]:
        out.append("radios:  `iw` is not installed, so the radios cannot be read (apt install iw)")
    for r in inv["radios"]:
        bits = [r.get("driver") or "?", (r.get("bus") or "").upper()]
        if r.get("usb"):
            bits.append(r["usb"]["id"])
        out.append(f"radio:   {r['iface']} ({r['phy']}, {' '.join(b for b in bits if b)}), {r.get('type')}, run by {r['owner']}"
                   + (f" + {r['manager']}" if r.get("manager") else ""))
        out.append(f"         modes: {', '.join(r.get('modes') or ['?'])}; bands: {', '.join(r.get('bands') or ['?'])}")
        if r.get("ap"):
            out.append(f"         AP beside a client link: {'yes, up to ' + str(r['channels_at_once']) + ' channel(s)' if r.get('ap_beside_client') else 'no'}")
        link = r.get("link") or {}
        if link:
            out.append(f"         link: {link.get('ssid')} via {link.get('bssid')}, channel {link.get('channel')}, "
                       f"{link.get('signal')} dBm; power save {r.get('power_save')}")
        p = r.get("profile")
        if p:
            out.append(f"         profile {p['name']}: autoconnect-retries {p['autoconnect_retries']}, auth-retries "
                       f"{p['auth_retries']}, bssid lock {p['bssid_lock'] or 'none'}, powersave {p['powersave']}")
        if r.get("params"):
            out.append("         driver options: " + ", ".join(f"{k}={v}" for k, v in r["params"].items()))
    for w in inv["wired"]:
        out.append(f"wired:   {w['iface']} ({w.get('driver') or '?'}), carrier {'yes' if w['carrier'] else 'no'}")
    out.append("")
    out += summary(inv)
    return "\n".join(out)


def main(argv):
    focus, mode, path = None, "report", None
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--iface" and args:
            focus = args.pop(0)
        elif a == "--json":
            mode = "json"
        elif a == "--write":
            mode = "write"
            if args and not args[0].startswith("-"):
                path = args.pop(0)
        elif a == "summary":
            mode = "summary"
        else:
            sys.exit(__doc__.split("\n\n")[1] if a in ("-h", "--help") else f"netinv.py: unknown argument {a}")
    inv = scan(focus)
    if mode == "json":
        print(json.dumps(inv, indent=2))
    elif mode == "write":
        write(inv, path or OUT)
        print("\n".join(summary(inv)))
    elif mode == "summary":
        print("\n".join(summary(inv)))
    else:
        print(report(inv))


if __name__ == "__main__":
    main(sys.argv[1:])
