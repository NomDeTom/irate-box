# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One radio reset, for the uplink watchdog (uplink.py's `radio` step) and crash watch (its
pre-emption) both, so the two never disagree on how.

What brings back an AIC8800 whose firmware has wedged ("cmd queue crashed"): NetworkManager stopped, the USB device de-authorised and authorised again, NetworkManager started,
then the hotspot started again. Unloading the driver was refused (modprobe -r: rc 1). So, with
NetworkManager stopped around it, each of these until the interface is back:

  1. the USB device unbound and bound again (its parent hub's when the device itself has gone)
  2. the USB device's `authorized` set to 0, then 1
  3. the driver reloaded (modprobe -r, then modprobe), each with a time limit

then NetworkManager started again if it was running, and the hotspot (irate-box-ap.service, the
same boot path a reboot takes) if it was up. Root only; stdlib only.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
AP_RECORD = ETC / "ap.json"          # root/ap.py's: whether the hotspot is up
AP_UNIT = "irate-box-ap.service"
PORT_RE = re.compile(r"[0-9]+-[0-9.]+")
MOD_RE = re.compile(r"[A-Za-z0-9_-]+")
BACK_WAIT = 20                        # seconds for the interface to come back after each way


def _run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)


def _back(iface, wait, sleep):
    """The interface there again within `wait` seconds."""
    for _ in range(max(int(wait), 1)):
        if (SYS / "class/net" / iface).exists():
            return True
        sleep(1)
    return (SYS / "class/net" / iface).exists()


def _usb_target(port):
    """The USB device to reset: the radio's own, or the hub it hangs off when it has gone."""
    if not port or not PORT_RE.fullmatch(port):
        return None
    if (SYS / "bus/usb/devices" / port).exists():
        return port
    parent = port.rsplit(".", 1)[0] if "." in port else None
    return parent if parent and (SYS / "bus/usb/devices" / parent).exists() else None


def hotspot_up():
    try:
        return bool(json.loads(AP_RECORD.read_text()).get("up"))
    except (OSError, ValueError, AttributeError):
        return False


def restore_hotspot(run=_run):
    """After a radio reset or a restart of the network service, which take the hotspot down with
    them: start it again the way a boot does, if it was up."""
    if not hotspot_up():
        return ""
    code, out = run("systemctl", "start", AP_UNIT, timeout=120)
    return "the hotspot started again" if code == 0 else f"the hotspot did not start again: {out[-160:]}"


def reset(iface, port=None, driver=None, product=None, run=_run, sleep=time.sleep, wait=BACK_WAIT):
    """Reset the radio behind iface (its USB port and driver as netinv.device_facts gives them).
    One line saying what was done and whether the interface came back."""
    tried = []
    nm = run("systemctl", "is-active", "--quiet", "NetworkManager", timeout=10)[0] == 0
    if nm:
        run("systemctl", "stop", "NetworkManager", timeout=90)
    back = False
    try:
        target = _usb_target(port)
        if target:
            name = f"USB {target}" + (f" ({product})" if product else "") + ("" if target == port else f", the hub {port} hangs off")
            try:
                drv = SYS / "bus/usb/drivers/usb"
                (drv / "unbind").write_text(target)
                sleep(3)
                (drv / "bind").write_text(target)
                tried.append(f"{name} unbound and bound again")
                back = _back(iface, wait, sleep)
            except OSError as exc:
                tried.append(f"{name}: unbind/bind failed ({exc})")
            if not back:
                auth = SYS / "bus/usb/devices" / target / "authorized"
                try:
                    auth.write_text("0")
                    sleep(2)
                    auth.write_text("1")
                    tried.append(f"{name} de-authorised and authorised again")
                    back = _back(iface, wait, sleep)
                except OSError as exc:
                    tried.append(f"{name}: authorized failed ({exc})")
        if not back and driver and MOD_RE.fullmatch(driver):
            code, out = run("modprobe", "-r", driver, timeout=30)
            if code == 0:
                sleep(2)
                code, out = run("modprobe", driver, timeout=60)
                tried.append(f"{driver} reloaded" if code == 0 else f"modprobe {driver}: {out[-120:]}")
                back = code == 0 and _back(iface, wait, sleep)
            else:
                tried.append(f"{driver} would not unload ({out[-120:] or f'rc {code}'})")
    finally:
        if nm:
            run("systemctl", "start", "NetworkManager", timeout=90)
    if not tried:
        return "no way to reset this radio here"
    said = "; ".join(tried) + (f"; {iface} is back" if back else f"; {iface} did not come back")
    hs = restore_hotspot(run)
    return said + (f"; {hs}" if hs else "")
