#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""USB networking for a board whose USB port is a device: NCM (a network link Linux, macOS and recent
Windows drive without a driver) and ACM (a serial console) over the same cable.

irate-box-usb-gadget.service runs this as root (`usbgadget run`). The vendor kernel builds the
gadget framework and configfs but not the NCM and ACM functions, so the service fetches the four
function sources pinned in the Driver resources toolkit (`toolkits/drivers.json`'s `gadget`), checks
each file's sha256, builds them with DKMS from `gadget/`, loads them, composes the gadget through
configfs, gives `usb0` a fixed locally administered MAC and a small private subnet with DHCP, and
puts a getty on the ACM port. Only on a board whose UDC is in peripheral mode.

  usbgadget.py run                 the service: set it up, keep it up, tear it down on stop
  usbgadget.py status              what is there, as JSON
  usbgadget.py set on|off          the owner's choice, applied at once

The choice is on Recovery and monitoring, on by default on a Lyra (it is how a headless board is
reached). Settings in /etc/hub/usb-gadget.json (root's). Stdlib only.
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from irate_box.msg import M, Refused

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
CONFIGFS = Path(os.environ.get("HUB_CONFIGFS", "/sys/kernel/config"))
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
RUN = Path(os.environ.get("HUB_USB_GADGET_RUN", "/run/irate-box-usb-gadget"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
KITS = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
SRC = Path(os.environ.get("HUB_GADGET_SRC", "/usr/src/usb-gadget-1.0.0"))
SETTINGS = ETC / "usb-gadget.json"
STATUS = RUN / "status.json"
GADGET = CONFIGFS / "usb_gadget" / "irate"
FUNCTIONS = GADGET / "functions"
CONFIGS = GADGET / "configs"
STRINGS = GADGET / "strings"
UDC_DIR = SYS / "class/udc"

UNIT = "irate-box-usb-gadget.service"
PACKAGE = "usb-gadget"
VERSION = "1.0.0"
MODULES = ("u_ether", "usb_f_ncm", "u_serial", "usb_f_acm")
IFACE = "usb0"
ADDRESS = "192.168.7.1"
PREFIX = 24
NETMASK = "255.255.255.0"
DHCP_FIRST = "192.168.7.10"
DHCP_LAST = "192.168.7.50"
TTY = "ttyGS0"
DEV_ADDR = "02:00:00:00:01:01"
HOST_ADDR = "02:00:00:00:01:02"
VENDOR = "0x1d6b"
PRODUCT = "0x0104"
BCD_USB = "0x0200"
BCD_DEVICE = "0x0100"
MAX_POWER = "250"
LOOK = 10  # seconds between looks at the gadget
WAIT = 15  # seconds for usb0 and the ACM port to appear after the UDC is bound
FETCH_TIMEOUT = 30
BUILD_TIMEOUT = 900


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def model():
    try:
        return (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace")
    except OSError:
        return None


def default_on():
    """A Lyra has no other way in: USB networking is on there until an administrator decides."""
    return "Lyra" in (model() or "")


def load_settings():
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        raw = {}
    on = raw.get("on") if isinstance(raw, dict) and isinstance(raw.get("on"), bool) else default_on()
    return {"on": on}


def chosen():
    """Whether an administrator has decided: the file says on or off."""
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and isinstance(raw.get("on"), bool)


def save_settings(on):
    from irate_box.root import safeio

    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(SETTINGS, json.dumps({"on": bool(on)}))


def peripherals():
    """The UDC names the board offers: a port in device (peripheral) mode, not a host port."""
    try:
        return sorted(p.name for p in UDC_DIR.iterdir())
    except OSError:
        return []


def modules_loaded():
    return {m: (SYS / "module" / m).is_dir() for m in MODULES}


def gadget_state():
    functions = sorted(p.name for p in FUNCTIONS.glob("*")) if FUNCTIONS.is_dir() else []
    bound = bool(read(GADGET / "UDC"))
    return {
        "present": GADGET.is_dir(),
        "bound": bound,
        "functions": functions,
        "iface": IFACE,
        "address": f"{ADDRESS}/{PREFIX}",
        "tty": TTY,
        "up": bool(bound and (SYS / "class/net" / IFACE).exists() and (SYS / "class/tty" / TTY).exists()),
    }


def read_status():
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(up, why):
    RUN.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"up": bool(up), "why": why, "at": time.time()}))


def status():
    udcs = peripherals()
    state = gadget_state()
    saved = read_status()
    return {
        "model": model(),
        "peripheral": bool(udcs),
        "udc": udcs[0] if udcs else None,
        "settings": load_settings(),
        "chosen": chosen(),
        "modules": modules_loaded(),
        "gadget": state,
        "up": state["up"],
        "why": saved.get("why"),
    }


def _run(cmd, timeout=120):
    try:
        return subprocess.run(  # noqa: TID251
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


# A test stands the commands in; the service uses this one.
run = _run


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pin():
    """The gadget sources' pin from the Driver resources toolkit, or (None, why)."""
    try:
        kit = json.loads((CODE / "toolkits" / "drivers.json").read_text())
    except (OSError, ValueError):
        return None, "the Driver resources toolkit is not on the box"
    pin = kit.get("gadget") if isinstance(kit, dict) else None
    if not isinstance(pin, dict) or not pin.get("commit") or not isinstance(pin.get("files"), dict):
        return None, "the USB gadget sources are not pinned in the Driver resources toolkit"
    return pin, None


def fetch_sources(log=print):
    """The pinned vendor files, from the kit's cache or fetched and checked: ({name: path}, why)."""
    pin, why = _pin()
    if not pin:
        return None, why
    cache = KITS / "gadget"
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    for path, want in pin["files"].items():
        name = Path(path).name
        dest = cache / name
        if dest.is_file() and _sha256(dest) == want:
            out[name] = dest
            continue
        url = f"https://raw.githubusercontent.com/armbian/linux-rockchip/{pin['commit']}/{path}"
        log(f"usbgadget: fetching {name}")
        try:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as answer:  # nosec B310 - https, pinned
                data = answer.read()
        except (OSError, urllib.error.URLError) as exc:
            return None, f"{name}: could not be fetched ({exc}); the box may be offline and the file is not cached"
        if hashlib.sha256(data).hexdigest() != want:
            return None, f"{name}: the download does not match its pinned sha256"
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        out[name] = dest
    return out, None


def build_modules(log=print):
    """Build and install the four modules with DKMS from the pinned sources: (ok, why)."""
    files, why = fetch_sources(log)
    if not files:
        return False, why
    source = CODE / "gadget"
    if not (source / "Makefile").is_file() or not (source / "dkms.conf").is_file():
        return False, "the gadget build files are not on the box"
    try:
        shutil.rmtree(SRC, ignore_errors=True)
        SRC.mkdir(parents=True)
        for name, path in files.items():
            shutil.copyfile(path, SRC / name)
        for name in ("Makefile", "dkms.conf"):
            shutil.copyfile(source / name, SRC / name)
    except OSError as exc:
        return False, f"the gadget sources could not be put in {SRC}: {exc}"
    steps = (
        ["dkms", "remove", "-m", PACKAGE, "-v", VERSION, "--all"],
        ["dkms", "add", "-m", PACKAGE, "-v", VERSION],
        ["dkms", "build", "-m", PACKAGE, "-v", VERSION],
        ["dkms", "install", "-m", PACKAGE, "-v", VERSION, "--force"],
    )
    for cmd in steps:
        log(f"usbgadget: {' '.join(cmd)}")
        result = run(cmd, timeout=BUILD_TIMEOUT)
        if result.returncode and cmd[1] != "remove":
            why = (result.stderr or result.stdout or "").strip().splitlines()
            return False, f"{' '.join(cmd)} failed: {why[-1] if why else result.returncode}"
    return True, None


def _load_modules():
    for module in MODULES:
        result = run(["modprobe", module], timeout=60)
        if result.returncode:
            why = (result.stderr or result.stdout or "").strip()
            return False, f"modprobe {module}: {why or result.returncode}"
    return True, None


def ensure_modules(log=print):
    """Load the four modules, building them once from the pinned sources if they are not there."""
    if all(modules_loaded().values()):
        return True, None
    # Installed already (the usual boot): load them, and build only if the load fails.
    ok, why = _load_modules()
    if ok:
        return True, None
    built, why = build_modules(log)
    if not built:
        return False, why
    return _load_modules()


def _remove(path):
    """Remove a configfs group, or a fake one: its links and attributes first, then the folder. On real
    configfs an attribute cannot be unlinked; the rmdir is what removes it, so a failed unlink is fine."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError:
            pass
        return
    try:
        children = list(path.iterdir())
    except OSError:
        return
    for child in children:
        _remove(child)
    try:
        path.rmdir()
    except OSError:
        pass


def _teardown_tree():
    if not GADGET.is_dir():
        return
    try:
        write(GADGET / "UDC", "")
    except OSError:
        pass
    for link in (CONFIGS / "c.1").glob("*") if (CONFIGS / "c.1").is_dir() else []:
        if link.is_symlink():
            try:
                link.unlink()
            except OSError:
                pass
    for folder in (
        FUNCTIONS / "ncm.usb0",
        FUNCTIONS / "acm.usb0",
        CONFIGS / "c.1" / "strings" / "0x409",
        CONFIGS / "c.1",
        STRINGS / "0x409",
        STRINGS,
        CONFIGS,
        FUNCTIONS,
        GADGET,
    ):
        _remove(folder)


def _link(function):
    dest = CONFIGS / "c.1" / function
    if not dest.exists():
        os.symlink(FUNCTIONS / function, dest)


def _serial():
    return (read("/etc/machine-id") or "irate-box")[:16]


def dnsmasq_conf():
    return "\n".join(
        [
            "# Written by irate-box (root/usbgadget.py): DHCP and DNS on the USB network only.",
            f"interface={IFACE}",
            "bind-dynamic",
            "except-interface=lo",
            "no-resolv",
            "no-hosts",
            f"dhcp-range={DHCP_FIRST},{DHCP_LAST},{NETMASK},12h",
            f"dhcp-option=option:router,{ADDRESS}",
            f"dhcp-option=option:dns-server,{ADDRESS}",
            f"address=/#/{ADDRESS}",
            f"dhcp-leasefile={RUN / 'dnsmasq.leases'}",
            "dhcp-authoritative",
            "",
        ]
    )


def start_children():
    """dnsmasq for the USB network and a getty on the ACM port; they live and die with the service."""
    RUN.mkdir(parents=True, exist_ok=True)
    conf = RUN / "dnsmasq.conf"
    conf.write_text(dnsmasq_conf())
    log = (RUN / "dnsmasq.log").open("ab")
    children = [
        subprocess.Popen(  # noqa: TID251 - dnsmasq runs for the service's lifetime
            ["/usr/sbin/dnsmasq", "--keep-in-foreground", f"--conf-file={conf}", "--pid-file="],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    ]
    children.append(subprocess.Popen(["agetty", "--noclear", "-8", "-L", TTY, "linux"]))  # noqa: TID251 - the console runs for the service's lifetime
    return children


def stop_children(children):
    for child in children:
        try:
            child.terminate()
        except OSError:
            pass
    for child in children:
        try:
            child.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                child.kill()
            except OSError:
                pass


def setup(log=print, sleep=time.sleep):
    """Compose the gadget and bring its network up: (ok, why, children)."""
    if not load_settings()["on"]:
        return False, "off", []
    udcs = peripherals()
    if not udcs:
        return False, "the board's USB port is not in peripheral mode", []
    ok, why = ensure_modules(log)
    if not ok:
        return False, why, []
    _teardown_tree()
    try:
        write(GADGET / "idVendor", VENDOR)
        write(GADGET / "idProduct", PRODUCT)
        write(GADGET / "bcdUSB", BCD_USB)
        write(GADGET / "bcdDevice", BCD_DEVICE)
        write(STRINGS / "0x409" / "serialnumber", _serial())
        write(STRINGS / "0x409" / "manufacturer", "irate-box")
        write(STRINGS / "0x409" / "product", "irate-box USB networking")
        write(CONFIGS / "c.1" / "strings" / "0x409" / "configuration", "USB networking")
        write(CONFIGS / "c.1" / "MaxPower", MAX_POWER)
        write(CONFIGS / "c.1" / "bmAttributes", "0x80")
        write(FUNCTIONS / "ncm.usb0" / "dev_addr", DEV_ADDR)
        write(FUNCTIONS / "ncm.usb0" / "host_addr", HOST_ADDR)
        (FUNCTIONS / "acm.usb0").mkdir(parents=True, exist_ok=True)
        _link("ncm.usb0")
        _link("acm.usb0")
        write(GADGET / "UDC", udcs[0])
    except OSError as exc:
        _teardown_tree()
        return False, f"configfs: {exc}", []
    for _ in range(WAIT):
        if (SYS / "class/net" / IFACE).exists() and (SYS / "class/tty" / TTY).exists():
            break
        sleep(1)
    if not (SYS / "class/net" / IFACE).exists():
        return False, f"{IFACE} did not appear", []
    run(["ip", "addr", "replace", f"{ADDRESS}/{PREFIX}", "dev", IFACE], timeout=30)
    run(["ip", "link", "set", IFACE, "up"], timeout=30)
    return True, None, start_children()


def teardown(children=()):
    stop_children(children)
    run(["ip", "addr", "flush", "dev", IFACE], timeout=30)
    _teardown_tree()
    try:
        STATUS.unlink()
    except OSError:
        pass


def serve(log=print):
    """The service: set the gadget up, keep it and its helpers alive, tear it down on SIGTERM."""
    stopping = {"now": False}

    def stop(_signum, _frame):
        stopping["now"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if not load_settings()["on"] or not peripherals():
        write_status(False, "off" if not load_settings()["on"] else "no peripheral controller")
        return 0
    children = []
    while not stopping["now"]:
        if not gadget_state()["up"]:
            stop_children(children)
            children = []
            write_status(False, "starting")
            ok, why, children = setup(log)
            write_status(ok, why)
            log(f"usbgadget: {'up' if ok else 'not up'}" + (f": {why}" if why else ""))
        elif any(child.poll() is not None for child in children):
            stop_children(children)
            children = start_children()
        time.sleep(LOOK)
    teardown(children)
    return 0


def set_option(value):
    if value not in ("on", "off"):
        raise Refused(M("usb-gadget-set.unknown", "USB networking has no setting {value}.", value=value))
    save_settings(value == "on")
    if value == "on":
        run(["systemctl", "restart", UNIT], timeout=60)
        return M(
            "usb-gadget-set.on",
            "USB networking is on. The box offers a network link at {address} and a console on /dev/{tty} "
            "while its USB port is connected.",
            address=ADDRESS,
            tty=TTY,
        )
    run(["systemctl", "stop", UNIT], timeout=60)
    return M("usb-gadget-set.off", "USB networking is off. The USB port is left alone.")


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        return serve()
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 2:
        print(set_option(argv[1]))
    elif cmd == "build":
        ok, why = build_modules()
        print("ok" if ok else f"failed: {why}")
        return 0 if ok else 1
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
