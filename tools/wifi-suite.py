#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A test suite for a box's WiFi radios, run by hand as root, to learn how real adapters behave before the
tests go into the hub (root/radiotests.py). The plan: notes-sync irate-box/plans/wifi-radios-plan.md, Part 1.

  identify   chip, USB id and speed, driver and its version, modes, bands and channels, what it can do at
             once, the regulatory country; a known WiFi dongle still in USB storage mode, with no driver
  scan       how many networks it sees, and the signal of the box's own network
  client     joins the box's network with a temporary profile, then pings the gateway with power saving
             on and off (a radio already on that network is measured where it is)
  pair       one radio runs a test hotspot, another joins it from a network namespace of its own, so the
             traffic really crosses the air; ping and iperf3 both ways, the hotspot's power saving on and off
  combo      a hotspot on a second interface of a radio that is also a client: does it come up, on which
             channel, and does the client link survive
  soak       a pair and the box's own link for N minutes: drops, and what the kernel log says
  recovery   a USB radio's driver unbound and bound again: how long until it is back

Everything is put back after each test (profiles, the namespace, power saving, which radios NetworkManager
manages), and `--cleanup` puts back what a killed run left. Results: JSON (all of it) and Markdown with no
network names, MAC or IP addresses, to paste into an issue. Stdlib only.

  sudo python3 wifi-suite.py --home netplan-wlan0-SKYTB1H1 --json out.json --markdown out.md
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

SYS = Path("/sys")
IW = "/usr/sbin/iw"
NETNS = "wsuite"
PREFIX = "wsuite-"
AP_NET = ("10.231.0.1", "10.231.0.2")
# Known WiFi dongles that first show as a USB drive (holding a Windows driver) until switched.
STORAGE_MODE = {"a69c:5721": "AIC8800 (its WiFi id is a69c:8d80)"}
KERNEL_TROUBLE = re.compile(r"usb \S+: (USB disconnect|reset)|firmware|timeout|fail|error", re.IGNORECASE)


def say(text):
    print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)


def run(*cmd, timeout=60, netns=None):
    if netns:
        cmd = ("ip", "netns", "exec", netns, *cmd)
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return done.returncode, (done.stdout + done.stderr).strip()


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


# --- identify ----------------------------------------------------------------------------------------


def usb_device(iface):
    """The USB device directory a network interface sits on, or None (onboard SDIO, PCI)."""
    try:
        here = (SYS / "class/net" / iface / "device").resolve()
    except OSError:
        return None
    for folder in (here, *here.parents):
        if (folder / "idVendor").exists():
            return folder
    return None


def phy_of(iface):
    return read(SYS / "class/net" / iface / "phy80211/name") or None


def parse_phy(text):
    """Bands with their usable channels, interface modes, and the combinations line by line, from iw phy info."""
    bands, modes, combos = {}, [], []
    band, section = None, None
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"Band \d+:", stripped):
            band, section = stripped.rstrip(":"), None
            bands[band] = []
        elif stripped.startswith("Supported interface modes"):
            section = "modes"
        elif stripped.startswith("valid interface combinations"):
            section = "combos"
        elif stripped.startswith("Frequencies:"):
            section = "freqs"
        elif section == "modes" and stripped.startswith("* "):
            modes.append(stripped[2:])
        elif section == "combos" and (stripped.startswith("*") or stripped.startswith("total")):
            combos.append(stripped.lstrip("* "))
        elif section == "freqs" and stripped.startswith("* ") and "disabled" not in stripped:
            found = re.search(r"\[(\d+)\]", stripped)
            if found and band:
                bands[band].append(int(found.group(1)))
        elif section in ("modes", "freqs") and not stripped.startswith("*"):
            section = None
    return {"bands": bands, "modes": modes, "combinations": combos}


def identify(iface):
    driver = Path(os.path.realpath(SYS / "class/net" / iface / "device/driver")).name
    found = {"iface": iface, "driver": driver, "phy": phy_of(iface)}
    found["driver_version"] = read(SYS / "module" / driver / "version") or run("uname", "-r")[1]
    usb = usb_device(iface)
    if usb:
        found["usb_id"] = f"{read(usb / 'idVendor')}:{read(usb / 'idProduct')}"
        found["usb_speed_mbps"] = read(usb / "speed")
        found["product"] = read(usb / "product")
        found["maker"] = read(usb / "manufacturer")
    else:
        found["bus"] = "onboard"
    if found["phy"]:
        found.update(parse_phy(run(IW, "phy", found["phy"], "info")[1]))
    found["power_save"] = run(IW, "dev", iface, "get", "power_save")[1].split(":")[-1].strip()
    return found


def stuck_dongles():
    """Known WiFi dongles still in USB storage mode: they have no network interface to test."""
    out = []
    for device in (SYS / "bus/usb/devices").iterdir():
        usb_id = f"{read(device / 'idVendor')}:{read(device / 'idProduct')}"
        if usb_id in STORAGE_MODE:
            out.append({"usb_id": usb_id, "chip": STORAGE_MODE[usb_id], "product": read(device / "product")})
    return out


def country():
    found = re.search(r"country (\S+):", run(IW, "reg", "get")[1])
    return found.group(1) if found else None


# --- the box's own network ------------------------------------------------------------------------------


def home_network(profile):
    """The SSID and passphrase of a NetworkManager profile, held in memory only."""
    ssid = run("nmcli", "-g", "802-11-wireless.ssid", "connection", "show", profile)[1]
    psk = run("nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", profile)[1]
    return ssid, psk


def gateway(iface, netns=None):
    found = re.search(r"default via (\S+)", run("ip", "route", "show", "dev", iface, netns=netns)[1])
    return found.group(1) if found else None


def ping(target, iface, seconds, netns=None):
    code, out = run(
        "ping", "-I", iface, "-c", str(seconds), "-i", "1", "-W", "2", target, timeout=seconds + 15, netns=netns
    )
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    return {
        "loss_pct": float(loss.group(1)) if loss else 100.0,
        "rtt_ms": dict(zip(("min", "avg", "max", "mdev"), map(float, rtt.groups()), strict=True)) if rtt else None,
    }


def power_save(iface, on, netns=None):
    code, out = run(IW, "dev", iface, "set", "power_save", "on" if on else "off", netns=netns)
    return code == 0 or out


# --- scan -----------------------------------------------------------------------------------------------


def scan(iface, home_ssid):
    was_up = "UP" in run("ip", "-br", "link", "show", iface)[1].split()[1:2]
    run("ip", "link", "set", iface, "up")
    out = ""
    for _ in range(4):
        code, out = run(IW, "dev", iface, "scan", timeout=30)
        if code == 0:
            break
        time.sleep(3)
    if not was_up:
        run("ip", "link", "set", iface, "down")
    seen, home = 0, None
    for block in out.split("\nBSS ")[0:]:
        if "SSID:" not in block:
            continue
        seen += 1
        signal = re.search(r"signal: (-?[\d.]+) dBm", block)
        ssid = re.search(r"\n\s*SSID: (.*)", block)
        if ssid and ssid.group(1) == home_ssid and signal:
            home = max(home or -999.0, float(signal.group(1)))
    return {"networks_seen": seen, "home_signal_dbm": home}


# --- client ---------------------------------------------------------------------------------------------


def connected_to(iface, ssid):
    return f"SSID: {ssid}" in run(IW, "dev", iface, "link")[1]


def join_home(iface, home):
    """Joins the box's network on this radio with a temporary profile (a low-priority route, so the box's own
    link keeps the default): whether it joined, how long it took, and NetworkManager's last words if not."""
    ssid, psk = home
    name = PREFIX + "client"
    run("nmcli", "connection", "delete", name)
    run(
        "nmcli",
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        iface,
        "con-name",
        name,
        "ssid",
        ssid,
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        psk,
        "connection.autoconnect",
        "no",
        "ipv4.route-metric",
        "900",
        "ipv6.method",
        "disabled",
    )
    started = time.time()
    code, out = run("nmcli", "--wait", "45", "connection", "up", name, timeout=60)
    if code:
        run("nmcli", "connection", "delete", name)
    return code == 0, round(time.time() - started, 1), (out.splitlines()[-1][:200] if code and out else "")


def client(iface, home, seconds):
    result = {}
    temporary = not connected_to(iface, home[0])
    if temporary:
        joined, took, error = join_home(iface, home)
        result.update(joined=joined, join_seconds=took)
        if not joined:
            return {**result, "error": error or "no answer"}
    target = gateway(iface)
    if not target:
        result["error"] = "no gateway on this interface"
    else:
        before = run(IW, "dev", iface, "get", "power_save")[1]
        for on in (True, False):
            accepted = power_save(iface, on)
            result["power_save_on" if on else "power_save_off"] = {
                **ping(target, iface, seconds),
                "set": accepted is True,
            }
        power_save(iface, "on" in before.split(":")[-1])
    link = run(IW, "dev", iface, "link")[1]
    rate = re.search(r"tx bitrate: ([\d.]+) MBit/s", link)
    signal = re.search(r"signal: (-?\d+) dBm", link)
    result["tx_rate_mbps"] = float(rate.group(1)) if rate else None
    result["signal_dbm"] = int(signal.group(1)) if signal else None
    if temporary:
        run("nmcli", "connection", "delete", PREFIX + "client")
    return result


# --- a hotspot on one radio, a client on another in its own namespace ------------------------------------


def hotspot_up(iface, channel=6):
    name = PREFIX + "ap-" + iface
    ssid, psk = "irate-test-" + secrets.token_hex(3), secrets.token_urlsafe(12)
    run("nmcli", "connection", "delete", name)
    run(
        "nmcli",
        "connection",
        "add",
        "type",
        "wifi",
        "ifname",
        iface,
        "con-name",
        name,
        "ssid",
        ssid,
        "802-11-wireless.mode",
        "ap",
        "802-11-wireless.band",
        "bg" if channel <= 14 else "a",
        "802-11-wireless.channel",
        str(channel),
        "wifi-sec.key-mgmt",
        "wpa-psk",
        "wifi-sec.psk",
        psk,
        "connection.autoconnect",
        "no",
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        AP_NET[0] + "/24",
        "ipv6.method",
        "disabled",
    )
    started = time.time()
    code, out = run("nmcli", "--wait", "45", "connection", "up", name, timeout=60)
    return code == 0, round(time.time() - started, 1), ssid, psk, (out.splitlines()[-1][:200] if code and out else "")


def hotspot_down(iface):
    run("nmcli", "connection", "delete", PREFIX + "ap-" + iface)


def join_in_namespace(iface, ssid, psk, wait=40):
    """Moves the radio into the namespace and joins with wpa_supplicant there. True once associated."""
    phy = phy_of(iface)
    run("nmcli", "device", "set", iface, "managed", "no")
    run("ip", "netns", "add", NETNS)
    run(IW, "phy", phy, "set", "netns", "name", NETNS)
    run("ip", "link", "set", iface, "up", netns=NETNS)
    conf = Path(f"/run/{PREFIX}wpa.conf")
    conf.write_text(
        f'ctrl_interface=/run/{PREFIX}wpa\nnetwork={{\n ssid="{ssid}"\n psk="{psk}"\n key_mgmt=WPA-PSK\n}}\n'
    )
    conf.chmod(0o600)
    run("wpa_supplicant", "-B", "-i", iface, "-c", str(conf), "-P", f"/run/{PREFIX}wpa.pid", netns=NETNS)
    started = time.time()
    while time.time() - started < wait:
        if "Connected to" in run(IW, "dev", iface, "link", netns=NETNS)[1]:
            run("ip", "addr", "add", AP_NET[1] + "/24", "dev", iface, netns=NETNS)
            return True, round(time.time() - started, 1)
        time.sleep(1)
    return False, wait


def leave_namespace(iface):
    pid = read(f"/run/{PREFIX}wpa.pid")
    if pid.isdigit():
        run("kill", pid)
        time.sleep(1)
    for path in (f"/run/{PREFIX}wpa.conf", f"/run/{PREFIX}wpa.pid"):
        Path(path).unlink(missing_ok=True)
    code, out = run("ip", "netns", "exec", NETNS, "ls", "/sys/class/ieee80211")
    for phy in out.split() if code == 0 else []:
        run(IW, "phy", phy, "set", "netns", "1", netns=NETNS)
    run("ip", "netns", "del", NETNS)
    time.sleep(1)
    run("nmcli", "device", "set", iface, "managed", "yes")


def iperf(seconds, reverse=False):
    server = subprocess.Popen(
        ["iperf3", "-s", "-1", "-B", AP_NET[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    args = ["iperf3", "-c", AP_NET[0], "-t", str(seconds), "-J"] + (["-R"] if reverse else [])
    code, out = run(*args, timeout=seconds + 30, netns=NETNS)
    server.kill()
    try:
        return round(json.loads(out)["end"]["sum_received"]["bits_per_second"] / 1e6, 1)
    except (ValueError, KeyError):
        return None


def pair(ap_iface, client_iface, seconds):
    result = {"hotspot": ap_iface, "client": client_iface}
    up, took, ssid, psk, error = hotspot_up(ap_iface)
    result.update(hotspot_up=up, hotspot_seconds=took)
    if not up:
        result["error"] = error
        hotspot_down(ap_iface)
        return result
    try:
        joined, took = join_in_namespace(client_iface, ssid, psk)
        result.update(joined=joined, join_seconds=took)
        if joined:
            for on in (True, False):
                accepted = power_save(ap_iface, on)
                result["hotspot_power_save_on" if on else "hotspot_power_save_off"] = {
                    **ping(AP_NET[0], client_iface, seconds, netns=NETNS),
                    "set": accepted is True,
                    "to_hotspot_mbps": iperf(seconds),
                    "from_hotspot_mbps": iperf(seconds, reverse=True),
                }
            link = run(IW, "dev", client_iface, "link", netns=NETNS)[1]
            signal = re.search(r"signal: (-?\d+) dBm", link)
            result["signal_dbm"] = int(signal.group(1)) if signal else None
    finally:
        leave_namespace(client_iface)
        hotspot_down(ap_iface)
    return result


# --- a hotspot and a client link on one radio ------------------------------------------------------------


def combo(iface, home, seconds):
    ssid, _ = home
    result = {}
    temporary = not connected_to(iface, ssid)
    if temporary:
        joined, _, error = join_home(iface, home)
        if not joined:
            return {"error": "could not join the network first: " + error}
    second = iface[:9] + "ap"
    # Its own address, locally administered: a second interface with the radio's own (ath9k_htc gives it
    # that) never becomes available to NetworkManager. A driver that picks one itself may refuse ours.
    own = read(SYS / "class/net" / iface / "address").split(":")
    address = ":".join([f"{int(own[0], 16) | 0x02:02x}", *own[1:5], f"{int(own[5], 16) ^ 0x01:02x}"])
    code, out = run(IW, "dev", iface, "interface", "add", second, "type", "__ap", "addr", address)
    if code:
        code, out = run(IW, "dev", iface, "interface", "add", second, "type", "__ap")
    run("ip", "link", "set", second, "up")
    if code:
        result["error"] = "no second interface: " + out[:160]
    else:
        # NetworkManager takes a moment to see a new interface, and may leave it unmanaged.
        for _ in range(20):
            if second in run("nmcli", "-t", "-f", "DEVICE", "device")[1].split():
                break
            time.sleep(0.5)
        run("nmcli", "device", "set", second, "managed", "yes")
        time.sleep(1)
        # On the link's own channel: a radio that can use only one channel at a time (ath9k_htc) refuses another.
        on_channel = re.search(r"channel (\d+)", run(IW, "dev", iface, "info")[1])
        up, took, _, _, error = hotspot_up(second, int(on_channel.group(1)) if on_channel else 6)
        result.update(hotspot_up=up, hotspot_seconds=took)
        if up:
            channel = lambda dev: (re.search(r"channel (\d+)", run(IW, "dev", dev, "info")[1]) or [None, None])[1]  # noqa: E731
            result["link_channel"], result["hotspot_channel"] = channel(iface), channel(second)
            target = gateway(iface)
            result["link_while_hotspot"] = ping(target, iface, seconds) if target else None
        else:
            result["error"] = error
        hotspot_down(second)
        run(IW, "dev", second, "del")
    if temporary:
        run("nmcli", "connection", "delete", PREFIX + "client")
    return result


# --- soak -----------------------------------------------------------------------------------------------


def soak(ap_iface, client_iface, home_iface, minutes):
    result = {"hotspot": ap_iface, "client": client_iface, "minutes": minutes}
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    up, _, ssid, psk, error = hotspot_up(ap_iface)
    if not up:
        hotspot_down(ap_iface)
        return {**result, "error": error}
    rounds = []
    try:
        joined, _ = join_in_namespace(client_iface, ssid, psk)
        if not joined:
            return {**result, "error": "the client did not join"}
        home_gateway = gateway(home_iface) if home_iface else None
        ending = time.time() + minutes * 60
        while time.time() < ending:
            round_result = {"pair": ping(AP_NET[0], client_iface, 50, netns=NETNS), "to_hotspot_mbps": iperf(5)}
            if home_gateway:
                round_result["home"] = ping(home_gateway, home_iface, 5)
            round_result["associated"] = "Connected to" in run(IW, "dev", client_iface, "link", netns=NETNS)[1]
            rounds.append(round_result)
            say(f"soak: {len(rounds)} rounds, pair loss {round_result['pair']['loss_pct']}%")
    finally:
        leave_namespace(client_iface)
        hotspot_down(ap_iface)
    losses = [item["pair"]["loss_pct"] for item in rounds]
    result.update(
        rounds=len(rounds),
        pair_loss_pct_avg=round(sum(losses) / len(losses), 2) if losses else None,
        pair_loss_pct_worst=max(losses) if losses else None,
        rounds_not_associated=sum(1 for item in rounds if not item["associated"]),
        throughput_mbps=[item["to_hotspot_mbps"] for item in rounds],
        home_loss_pct_worst=max((item["home"]["loss_pct"] for item in rounds if "home" in item), default=None),
        kernel=[
            line
            for line in run("journalctl", "-k", "--since", since, "--no-pager", "-q")[1].splitlines()
            if KERNEL_TROUBLE.search(line)
        ][:40],
    )
    return result


# --- recovery -------------------------------------------------------------------------------------------


def recovery(iface):
    usb = usb_device(iface)
    if not usb:
        return {"skipped": "not a USB radio"}
    interface_dir = Path(os.path.realpath(SYS / "class/net" / iface / "device"))
    driver = interface_dir.joinpath("driver").resolve().name
    port = interface_dir.name
    started = time.time()
    Path(f"/sys/bus/usb/drivers/{driver}/unbind").write_text(port)
    time.sleep(1)
    Path(f"/sys/bus/usb/drivers/{driver}/bind").write_text(port)
    while time.time() - started < 60:
        names = [path.name for path in (interface_dir / "net").glob("*")] if (interface_dir / "net").exists() else []
        if names:
            return {"back": True, "seconds": round(time.time() - started, 1), "iface_after": names[0]}
        time.sleep(0.5)
    return {"back": False, "seconds": 60}


# --- putting back, and the run --------------------------------------------------------------------------


def cleanup(home_profile):
    """Whatever a killed run left: the namespace (its radios back), temporary profiles, unmanaged radios."""
    if NETNS in run("ip", "netns", "list")[1]:
        leave_namespace("")
    for line in run("nmcli", "-t", "-f", "NAME", "connection", "show")[1].splitlines():
        if line.startswith(PREFIX):
            run("nmcli", "connection", "delete", line)
    for line in run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device")[1].splitlines():
        device, kind, state = (line.split(":") + ["", ""])[:3]
        if kind == "wifi" and state == "unmanaged":
            run("nmcli", "device", "set", device, "managed", "yes")
    for path in Path("/sys/class/net").glob("*ap"):
        if path.name.startswith("wlan"):
            run(IW, "dev", path.name, "del")
    if home_profile:
        run("nmcli", "--wait", "45", "connection", "up", home_profile, timeout=60)


def markdown(report):
    lines = [f"## WiFi radios: {report['board']}", "", f"Kernel {report['kernel']}, country `{report['country']}`.", ""]
    for stuck in report["stuck"]:
        lines.append(f"- **{stuck['chip']}** (`{stuck['usb_id']}`): in USB storage mode, no WiFi interface.")
    for radio in report["radios"].values():
        ident = radio["identify"]
        lines += [
            "",
            f"### {ident.get('product') or ident['driver']} (`{ident.get('usb_id', ident.get('bus'))}`, {ident['driver']} {ident['driver_version']})",
            "",
        ]
        bands = ", ".join(f"{band}: {len(chans)} channels" for band, chans in ident.get("bands", {}).items())
        lines.append(f"- Modes: {', '.join(ident.get('modes', []))}. {bands}.")
        for test in ("scan", "client", "combo", "recovery"):
            if test in radio:
                shown = {key: value for key, value in radio[test].items() if key not in ("error",)}
                lines.append(
                    f"- {test}: `{json.dumps(shown)}`"
                    + (f" ({radio[test]['error']})" if radio[test].get("error") else "")
                )
    for item in report.get("pairs", []):
        lines.append(
            f"- pair {item['hotspot']}→{item['client']}: `{json.dumps({k: v for k, v in item.items() if k not in ('hotspot', 'client')})}`"
        )
    if report.get("soak"):
        lines.append(f"- soak: `{json.dumps({k: v for k, v in report['soak'].items() if k != 'kernel'})}`")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", help="the NetworkManager profile of the box's own WiFi network")
    parser.add_argument("--radios", help="comma-separated interfaces (default: every WiFi interface)")
    parser.add_argument("--tests", default="identify,scan,client,pair,combo,recovery")
    parser.add_argument("--seconds", type=int, default=20, help="ping and iperf3 time per measurement")
    parser.add_argument("--soak-minutes", type=int, default=20)
    parser.add_argument("--json")
    parser.add_argument("--markdown")
    parser.add_argument("--cleanup", action="store_true", help="only put back what a killed run left")
    args = parser.parse_args()
    if os.geteuid():
        sys.exit("run as root")
    if args.cleanup:
        cleanup(args.home)
        return
    tests = set(args.tests.split(","))
    radios = (
        args.radios.split(",")
        if args.radios
        else sorted(path.parent.name for path in (SYS / "class/net").glob("*/phy80211"))
    )
    home = home_network(args.home) if args.home else (None, None)
    report = {
        "board": read("/proc/device-tree/model").rstrip("\x00") or "unknown board",
        "kernel": run("uname", "-r")[1],
        "country": country(),
        "stuck": stuck_dongles(),
        "radios": {},
        "pairs": [],
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        for iface in radios:
            say(f"identify {iface}")
            report["radios"][iface] = {"identify": identify(iface)}
        can_host = [iface for iface in radios if "AP" in report["radios"][iface]["identify"].get("modes", [])]
        for iface in radios:
            radio = report["radios"][iface]
            if "scan" in tests and home[0]:
                say(f"scan {iface}")
                radio["scan"] = scan(iface, home[0])
            if "client" in tests and home[0]:
                say(f"client {iface}")
                radio["client"] = client(iface, home, args.seconds)
            if "combo" in tests and home[0] and iface in can_host:
                say(f"combo {iface}")
                radio["combo"] = combo(iface, home, args.seconds)
        if "pair" in tests:
            for ap_iface in can_host:
                for client_iface in radios:
                    if client_iface != ap_iface:
                        say(f"pair {ap_iface} -> {client_iface}")
                        report["pairs"].append(pair(ap_iface, client_iface, args.seconds))
                        if args.home:
                            run("nmcli", "--wait", "45", "connection", "up", args.home, timeout=60)
        if "soak" in tests and can_host:
            ap_iface = can_host[0]
            others = [
                iface for iface in radios if iface != ap_iface and not (args.home and connected_to(iface, home[0]))
            ]
            if others:
                home_iface = next((iface for iface in radios if home[0] and connected_to(iface, home[0])), None)
                say(f"soak {ap_iface} -> {others[0]} for {args.soak_minutes} min")
                report["soak"] = soak(ap_iface, others[0], home_iface, args.soak_minutes)
        if "recovery" in tests:
            for iface in radios:
                say(f"recovery {iface}")
                report["radios"][iface]["recovery"] = recovery(iface)
                time.sleep(5)
    finally:
        cleanup(args.home)
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if args.json:
            Path(args.json).write_text(json.dumps(report, indent=1))
        if args.markdown:
            Path(args.markdown).write_text(markdown(report))
        say("done")


if __name__ == "__main__":
    main()
