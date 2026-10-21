#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A test suite for a box's Bluetooth controllers, run by hand as root: the Bluetooth part of wifi-suite.py,
to learn what the controllers do before anything goes into the hub. The plan: notes-sync
irate-box/plans/wifi-radios-plan.md, Part 4.

  identify     the controller's driver, USB ID, address (and whether it is a vendor's default, shared by every
               board of its kind), Bluetooth version, maker, what it supports and has on; whether BlueZ can use it
  passive      a Low Energy scan that only listens (sends nothing): devices heard, how soon the first
  active       a Low Energy scan that asks for names: devices found, their signal (RSSI)
  classic      classic (BR/EDR) discovery: devices found
  coexist      WiFi latency on a radio that shares the chip, with Bluetooth idle and then scanning hard

The controller is powered on for the tests and put back as it was. Output: JSON, and Markdown with no device
addresses or names, to paste into an issue. Stdlib only.

  sudo python3 bt-suite.py --hci hci0 --wifi wlan0 --json out.json --markdown out.md
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SYS = Path("/sys/class/bluetooth")
# Addresses a vendor's firmware falls back to when the board gives none: every such board then shares it.
DEFAULT_ADDRESSES = {"43:45:c0:00:1f:ac": "Broadcom BCM4345C0's default (Raspberry Pi 3B+, 4)"}


def say(text):
    print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)


def run(*cmd, timeout=60):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired as expired:
        out = expired.stdout or b""
        return 124, out.decode(errors="replace") if isinstance(out, bytes) else out
    return done.returncode, (done.stdout + done.stderr).strip()


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def index_of(hci):
    return hci.removeprefix("hci")


def mgmt_info(hci):
    """btmgmt's view of the controller: None when BlueZ can't use it (an index it doesn't list)."""
    code, out = run("btmgmt", "--index", index_of(hci), "info", timeout=10)
    if code or "Invalid Index" in out or "addr " not in out:
        return None
    found = {}
    address = re.search(r"addr ([0-9A-F:]{17}) version (\d+) manufacturer (\d+)", out)
    if address:
        found.update(
            address=address.group(1).lower(), version=int(address.group(2)), manufacturer=int(address.group(3))
        )
    for key in ("supported settings", "current settings"):
        line = re.search(rf"{key}: (.*)", out)
        found[key.replace(" ", "_")] = line.group(1).split() if line else []
    return found


def identify(hci):
    device = SYS / hci / "device"
    found = {
        "hci": hci,
        "driver": Path(os.path.realpath(device / "driver")).name if (device / "driver").exists() else None,
    }
    usb = Path(os.path.realpath(device)).parent
    if (usb / "idVendor").exists():
        found["usb_id"] = f"{read(usb / 'idVendor')}:{read(usb / 'idProduct')}"
        found["product"] = read(usb / "product")
    else:
        found["bus"] = "onboard"
    switch = rfkill(hci)
    if switch:
        found["rfkill_soft_blocked"], found["rfkill_hard_blocked"] = switch[1], switch[2]
    info = mgmt_info(hci)
    found["bluez_can_use"] = info is not None
    if info:
        found.update(info)
        found["default_address"] = DEFAULT_ADDRESSES.get(info.get("address", ""))
    else:
        errors = run("journalctl", "-k", "-b", "--no-pager", "-q", "--grep", f"{hci}:", timeout=20)[1].splitlines()
        found["kernel_said"] = [line.split("kernel: ", 1)[-1] for line in errors[-3:]]
    return found


def rfkill(hci):
    """The controller's rfkill switch: (its index, soft-blocked, hard-blocked), or None. Raspberry Pi OS ships
    Bluetooth soft-blocked: the suite unblocks it for its tests and blocks it again after."""
    for switch in (SYS / hci).glob("rfkill*"):
        return switch.name.removeprefix("rfkill"), read(switch / "soft") == "1", read(switch / "hard") == "1"
    return None


def powered(hci):
    info = mgmt_info(hci)
    return bool(info and "powered" in info.get("current_settings", []))


def power(hci, on):
    run("btmgmt", "--index", index_of(hci), "power", "on" if on else "off", timeout=10)


def passive(hci, seconds):
    """Listens only (hcitool's passive LE scan): devices heard, and how soon the first."""
    started = time.time()
    code, out = run("hcitool", "-i", hci, "lescan", "--passive", "--duplicates", timeout=seconds)
    heard = {line.split()[0] for line in out.splitlines() if re.match(r"^[0-9A-F:]{17}\b", line)}
    return {
        "devices": len(heard),
        "seconds": round(time.time() - started, 1),
        "error": None if heard or code in (0, 124) else out[-160:],
    }


def discover(hci, kind):
    """btmgmt's discovery: -l Low Energy (active: asks names), -b classic. Devices found and their signal."""
    code, out = run("btmgmt", "--index", index_of(hci), "find", kind, timeout=40)
    found = {}
    for match in re.finditer(r"dev_found: ([0-9A-F:]{17}) type (\S+(?: \S+)?) rssi (-?\d+)", out):
        found[match.group(1)] = int(match.group(3))
    rssi = sorted(found.values())
    return {
        "devices": len(found),
        "rssi_strongest": rssi[-1] if rssi else None,
        "rssi_median": rssi[len(rssi) // 2] if rssi else None,
        "error": None if code == 0 or found else out[-160:],
    }


def ping(target, iface, seconds):
    code, out = run("ping", "-I", iface, "-c", str(seconds * 5), "-i", "0.2", "-W", "2", target, timeout=seconds + 20)
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    return {
        "loss_pct": float(loss.group(1)) if loss else 100.0,
        "rtt_ms": dict(zip(("min", "avg", "max", "mdev"), map(float, rtt.groups()), strict=True)) if rtt else None,
    }


def coexist(hci, wifi, seconds):
    """WiFi latency to the gateway with Bluetooth idle, then while it runs LE discovery back to back."""
    route = re.search(r"default via (\S+)", run("ip", "route", "show", "dev", wifi)[1])
    if not route:
        return {"error": f"{wifi} has no gateway"}
    idle = ping(route.group(1), wifi, seconds)
    scanning = subprocess.Popen(
        ["sh", "-c", f"while :; do btmgmt --index {index_of(hci)} find -l >/dev/null 2>&1; done"],
        start_new_session=True,
    )
    try:
        busy = ping(route.group(1), wifi, seconds)
    finally:
        os.killpg(scanning.pid, 15)
        scanning.wait()
    return {"wifi": wifi, "idle": idle, "bluetooth_scanning": busy}


def markdown(report):
    lines = [f"## Bluetooth: {report['board']}", "", f"Kernel {report['kernel']}.", ""]
    for name, controller in report["controllers"].items():
        ident = controller["identify"]
        what = ident.get("product") or ident.get("bus") or ""
        lines.append(f"### {name}: {ident['driver']} {ident.get('usb_id', '')} {what}".rstrip())
        lines.append("")
        if not ident["bluez_can_use"]:
            lines.append(f"- BlueZ can't use it. The kernel said: `{' | '.join(ident.get('kernel_said', []))}`")
            continue
        lines.append(
            f"- Bluetooth version code {ident.get('version')}, maker {ident.get('manufacturer')}; supports: {', '.join(ident.get('supported_settings', []))}."
        )
        if ident.get("default_address"):
            lines.append(f"- Its address is a vendor's default: {ident['default_address']}.")
        for test in ("passive", "active", "classic", "coexist"):
            if test in controller:
                lines.append(f"- {test}: `{json.dumps(controller[test])}`")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hci", help="comma-separated controllers (default: every one)")
    parser.add_argument("--tests", default="identify,passive,active,classic,coexist")
    parser.add_argument("--wifi", help="the WiFi interface on the same chip, for the coexistence test")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--json")
    parser.add_argument("--markdown")
    args = parser.parse_args()
    if os.geteuid():
        sys.exit("run as root")
    tests = set(args.tests.split(","))
    controllers = args.hci.split(",") if args.hci else sorted(path.name for path in SYS.glob("hci*"))
    report = {
        "board": read("/proc/device-tree/model").rstrip("\x00") or "unknown board",
        "kernel": run("uname", "-r")[1],
        "controllers": {},
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for hci in controllers:
        say(f"identify {hci}")
        result = report["controllers"][hci] = {"identify": identify(hci)}
        if not result["identify"]["bluez_can_use"]:
            continue
        was_on, switch = powered(hci), rfkill(hci)
        blocked = bool(switch and switch[1])
        try:
            if blocked:
                run("rfkill", "unblock", switch[0])
                time.sleep(1)
            if not was_on:
                power(hci, True)
                time.sleep(1)
            if not powered(hci):
                result["error"] = "it would not power on"
                continue
            if "passive" in tests:
                say(f"passive {hci}")
                result["passive"] = passive(hci, args.seconds)
            if "active" in tests:
                say(f"active {hci}")
                result["active"] = discover(hci, "-l")
            if "classic" in tests:
                say(f"classic {hci}")
                result["classic"] = discover(hci, "-b")
            if "coexist" in tests and args.wifi:
                say(f"coexist {hci} with {args.wifi}")
                result["coexist"] = coexist(hci, args.wifi, args.seconds)
        finally:
            if not was_on:
                power(hci, False)
            if blocked:
                run("rfkill", "block", switch[0])
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1))
    if args.markdown:
        Path(args.markdown).write_text(markdown(report))
    say("done")


if __name__ == "__main__":
    main()
