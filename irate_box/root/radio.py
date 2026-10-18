# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The radio reset the uplink watchdog takes (uplink.py's `radio` step), and the radio test's
(radiotests.py), so the two never disagree on how.

What brings back an AIC8800 whose firmware has wedged ("cmd queue crashed"): the USB device reset
at its port, the firmware loaded afresh, then the hotspot started again. With NetworkManager
stopped around it, each of these until the radio is back. A USB radio:

  1. the USB device reset at its port (USBDEVFS_RESET: what `usbreset` does; the driver probes
     again and loads the firmware afresh)
  2. the USB device unbound and bound again (its parent hub's when the device itself has gone)
  3. the USB device's `authorized` set to 0, then 1

A radio on the board's SDIO bus (a Raspberry Pi's):

  1. the SDIO function unbound from its driver and bound again (the driver starts again and loads
     the firmware afresh)
  2. the SD host controller it sits on unbound and bound again (the card found again from scratch;
     where the board has a power line for the chip, it is switched off and on with it)

A device on the PCI bus (an M.2 or mini-PCIe WiFi card, a PCIe network adapter):

  1. the PCI function reset (its `reset` file: a function-level or bus reset, as the device allows)
  2. the device unbound from its driver and bound again
  3. the device removed and the bus rescanned (found afresh, as at boot)

A device on the SoC itself (the platform bus: a board's own Ethernet, WiFi built into the chip):

  1. the device unbound from its driver and bound again

Any, last:

  4. the driver reloaded: the modules that hold it unloaded first (brcmfmac_cyw holds brcmfmac),
     then it (rmmod by the name it is loaded under), then modprobe by its module file's name, which
     DKMS may have changed (aic8800_fdrv from aic8800_fdrv_usb.ko), and the holders again; each
     with a time limit

then NetworkManager started again if it was running, and the hotspot (irate-box-ap.service, the
same boot path a reboot takes) if it was up. The radio counts as back when the interface asked
about, or any network interface of that USB device, is there again: the hotspot's comes back only
when it is started again. Root only; stdlib only.

A driver that resets its own device when the firmware stops answering (the AIC8800's, patched)
says so in sysfs: `recoveries` and `recovered_at` (seconds since boot) among its parameters, and in
the kernel's log. `driver_reset()` reads them, so that the uplink watchdog and crash watch stand
back for DRIVER_GRACE while it works, rather than reset the radio on top of it.
"""

import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
DEV_USB = Path(os.environ.get("HUB_DEV_USB", "/dev/bus/usb"))
MODULES = Path(os.environ.get("HUB_MODULES_DIR", "/lib/modules"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
AP_RECORD = ETC / "ap.json"  # root/ap.py's: whether the hotspot is up
AP_UNIT = "irate-box-ap.service"
PORT_RE = re.compile(r"[0-9]+-[0-9.]+")
PCI_RE = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
MOD_RE = re.compile(r"[A-Za-z0-9_-]+")
BACK_WAIT = 20  # seconds for the interface to come back after each way
USBDEVFS_RESET = 0x5514  # _IO('U', 20)
DRIVER_GRACE = 90  # seconds a driver's own reset is left to work
DRIVER_LINE = "aic8800: firmware not answering: resetting the device"


def _run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as exc:
        return 1, str(exc)


def _there(iface, target):
    if (SYS / "class/net" / iface).exists():
        return True
    return bool(target) and any((SYS / "bus/usb/devices" / target).glob("*/net/*"))


def _back(iface, wait, sleep, target=None):
    """The interface, or another of the USB device's, there again within `wait` seconds."""
    for _ in range(max(int(wait), 1)):
        if _there(iface, target):
            return True
        sleep(1)
    return _there(iface, target)


def _usb_node(target):
    """/dev/bus/usb/BBB/DDD for a USB device, from its busnum and devnum."""
    d = SYS / "bus/usb/devices" / target
    try:
        return DEV_USB / f"{int((d / 'busnum').read_text()):03d}" / f"{int((d / 'devnum').read_text()):03d}"
    except (OSError, ValueError):
        return None


def _port_reset(target):
    node = _usb_node(target)
    if node is None:
        raise OSError("no device node")
    fd = os.open(node, os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)


def _module_file(name):
    """The module file's name to load a driver loaded as `name` again: DKMS may install it as
    name_<variant>.ko (aic8800_fdrv_usb.ko for aic8800_fdrv). The name itself when nothing better."""
    try:
        dep = (MODULES / os.uname().release / "modules.dep").read_text()
    except OSError:
        return name
    stems = {Path(line.split(":", 1)[0]).name.split(".ko", 1)[0] for line in dep.splitlines() if line}
    stems = {st.replace("-", "_") for st in stems}
    if name.replace("-", "_") in stems:
        return name
    near = sorted(st for st in stems if st.startswith(name.replace("-", "_") + "_"))
    return near[0] if len(near) == 1 else name


def _uptime():
    try:
        return float((PROC / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _journal_reset(run=_run, grace=DRIVER_GRACE):
    """When the kernel's journal last said the driver reset its device, in seconds since boot, or
    None: for a driver that says so but has no `recovered_at`."""
    code, out = run(
        "journalctl",
        "-k",
        "-b",
        "-q",
        "--no-pager",
        "-o",
        "short-monotonic",
        "--since",
        f"-{int(grace)}s",
        "-g",
        DRIVER_LINE,
        timeout=15,
    )
    if code != 0:
        return None
    at = None
    for line in out.splitlines():
        m = re.match(r"\[\s*([0-9.]+)\]", line)
        if m:
            at = float(m.group(1))
    return at


def driver_reset(run=_run):
    """(marker, age): the last reset a radio's driver made of its own device, `marker` telling one
    reset from the next and `age` its seconds ago; (None, None) when there was none. From sysfs
    (recovered_at, with recoveries in the marker), else from the kernel's journal."""
    up = _uptime()
    best = (None, None)
    for p in sorted(SYS.glob("module/*/parameters/recovered_at")):
        try:
            at = int(p.read_text().strip())
            n = (p.parent / "recoveries").read_text().strip()
        except (OSError, ValueError):
            continue
        if at > 0 and up is not None:
            age = max(up - at, 0.0)
            if best[1] is None or age < best[1]:
                best = (f"{p.parts[-3]}:{n}:{at}", age)
    if best[0] is not None or any(SYS.glob("module/*/parameters/recovered_at")):
        return best
    at = _journal_reset(run)
    if at is None or up is None:
        return (None, None)
    return (f"journal:{at}", max(up - at, 0.0))


def _usb_target(port):
    """The USB device to reset: the radio's own, or the hub it hangs off when it has gone."""
    if not port or not PORT_RE.fullmatch(port):
        return None
    if (SYS / "bus/usb/devices" / port).exists():
        return port
    parent = port.rsplit(".", 1)[0] if "." in port else None
    return parent if parent and (SYS / "bus/usb/devices" / parent).exists() else None


def _sdio_target(iface):
    """For a radio on the SDIO bus: (its function's name, its driver, the SD host's platform device, the
    host's driver), from the interface's place in sysfs; None for any other."""
    try:
        dev = (SYS / "class/net" / iface / "device").resolve()
    except OSError:
        return None
    parts = dev.parts
    if "mmc_host" not in parts or not (dev / "driver").exists():
        return None
    host = parts[parts.index("mmc_host") - 1]
    host_drv = SYS / "bus/platform/devices" / host / "driver"
    return {
        "func": dev.name,
        "driver": (dev / "driver").resolve().name,
        "host": host,
        "host_driver": host_drv.resolve().name if host_drv.exists() else None,
    }


def _bus_target(iface):
    """For a device on the PCI or the platform bus: {bus, name, driver}, from the interface's place in sysfs; None
    for any other (USB and SDIO have their own ways)."""
    try:
        dev = (SYS / "class/net" / iface / "device").resolve()
        bus = (dev / "subsystem").resolve().name
    except OSError:
        return None
    if bus not in ("pci", "platform") or not (dev / "driver").exists():
        return None
    if bus == "pci" and not PCI_RE.fullmatch(dev.name):
        return None
    return {"bus": bus, "name": dev.name, "driver": (dev / "driver").resolve().name}


def _rebind(bus, driver, name, sleep, pause=3):
    d = SYS / "bus" / bus / "drivers" / driver
    (d / "unbind").write_text(name)
    sleep(pause)
    (d / "bind").write_text(name)


def _holders(driver):
    try:
        return sorted(h.name for h in (SYS / "module" / driver.replace("-", "_") / "holders").iterdir())
    except OSError:
        return []


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
    return "The hotspot started again." if code == 0 else f"The hotspot did not start again: {out[-160:]}"


def reset(iface, port=None, driver=None, product=None, run=_run, sleep=time.sleep, wait=BACK_WAIT):
    """Reset the radio behind iface (its USB port and driver as netinv.device_facts gives them).
    One line saying what was done and whether the interface came back."""
    tried = []
    sdio = None if _usb_target(port) else _sdio_target(iface)  # looked up while the interface is still there
    onbus = None if _usb_target(port) or sdio else _bus_target(iface)
    nm = run("systemctl", "is-active", "--quiet", "NetworkManager", timeout=10)[0] == 0
    if nm:
        run("systemctl", "stop", "NetworkManager", timeout=90)
    back = False
    try:
        target = _usb_target(port)
        if target:
            name = (
                f"USB {target}"
                + (f" ({product})" if product else "")
                + ("" if target == port else f", the hub {port} hangs off")
            )
            try:
                _port_reset(target)
                tried.append(f"{name} reset at its port")
                back = _back(iface, wait, sleep, target)
            except OSError as exc:
                tried.append(f"{name}: port reset failed ({exc})")
            if not back:
                try:
                    drv = SYS / "bus/usb/drivers/usb"
                    (drv / "unbind").write_text(target)
                    sleep(3)
                    (drv / "bind").write_text(target)
                    tried.append(f"{name} unbound and bound again")
                    back = _back(iface, wait, sleep, target)
                except OSError as exc:
                    tried.append(f"{name}: unbind/bind failed ({exc})")
            if not back:
                auth = SYS / "bus/usb/devices" / target / "authorized"
                try:
                    auth.write_text("0")
                    sleep(2)
                    auth.write_text("1")
                    tried.append(f"{name} de-authorised and authorised again")
                    back = _back(iface, wait, sleep, target)
                except OSError as exc:
                    tried.append(f"{name}: authorized failed ({exc})")
        if not back and sdio:
            name = f"SDIO {sdio['func']} ({sdio['driver']})"
            try:
                _rebind("sdio", sdio["driver"], sdio["func"], sleep)
                tried.append(f"{name} unbound and bound again")
                back = _back(iface, wait, sleep)
            except OSError as exc:
                tried.append(f"{name}: unbind/bind failed ({exc})")
            if not back and sdio["host_driver"]:
                try:
                    _rebind("platform", sdio["host_driver"], sdio["host"], sleep)
                    tried.append(f"its SD host {sdio['host']} unbound and bound again")
                    back = _back(iface, wait, sleep)
                except OSError as exc:
                    tried.append(f"SD host {sdio['host']}: unbind/bind failed ({exc})")
        if not back and onbus and onbus["bus"] == "pci":
            name, pci = f"PCI {onbus['name']} ({onbus['driver']})", SYS / "bus/pci/devices" / onbus["name"]
            if (pci / "reset").exists():
                try:
                    (pci / "reset").write_text("1")
                    tried.append(f"{name} reset")
                    back = _back(iface, wait, sleep)
                except OSError as exc:
                    tried.append(f"{name}: reset failed ({exc})")
            if not back:
                try:
                    _rebind("pci", onbus["driver"], onbus["name"], sleep)
                    tried.append(f"{name} unbound and bound again")
                    back = _back(iface, wait, sleep)
                except OSError as exc:
                    tried.append(f"{name}: unbind/bind failed ({exc})")
            if not back:
                try:
                    (pci / "remove").write_text("1")
                    sleep(2)
                    (SYS / "bus/pci/rescan").write_text("1")
                    tried.append(f"{name} removed and the bus rescanned")
                    back = _back(iface, wait, sleep)
                except OSError as exc:
                    tried.append(f"{name}: remove and rescan failed ({exc})")
        if not back and onbus and onbus["bus"] == "platform":
            name = f"Platform device {onbus['name']} ({onbus['driver']})"
            try:
                _rebind("platform", onbus["driver"], onbus["name"], sleep)
                tried.append(f"{name} unbound and bound again")
                back = _back(iface, wait, sleep)
            except OSError as exc:
                tried.append(f"{name}: unbind/bind failed ({exc})")
        if not back and driver and MOD_RE.fullmatch(driver):
            loaded = (SYS / "module" / driver.replace("-", "_")).exists()
            holders = [h for h in _holders(driver) if MOD_RE.fullmatch(h)] if loaded else []
            code, out = 0, ""
            for m in [*holders, driver] if loaded else []:
                code, out = run("rmmod", m, timeout=30)
                if code:
                    break
            if code == 0:
                sleep(2)
                mod = _module_file(driver)
                code, out = run("modprobe", mod, timeout=60)
                for h in holders:  # the driver may load its own again; any it has not, by hand
                    if not (SYS / "module" / h).exists():
                        run("modprobe", h, timeout=60)
                tried.append(
                    (f"Driver {driver} reloaded" + (f" (with {', '.join(holders)})" if holders else ""))
                    if code == 0
                    else f"Loading {mod} failed: {out[-120:]}"
                )
                back = code == 0 and _back(iface, wait, sleep, target)
            else:
                tried.append(f"Driver {driver} would not unload ({out[-120:] or f'rc {code}'})")
    finally:
        if nm:
            run("systemctl", "start", "NetworkManager", timeout=90)
    if not tried:
        return "This box has no way to reset this radio."
    said = ". ".join(t[0].upper() + t[1:] for t in tried) + (
        f". {iface} is back." if back else f". {iface} did not return."
    )
    hs = restore_hotspot(run)
    return said + (f" {hs}" if hs else "")
