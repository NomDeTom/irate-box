#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""USB networking and a USB drive for a board whose USB port is a device: NCM (a network link Linux,
macOS and recent Windows drive without a driver), ACM (a serial console) and mass storage (the file
drop as a FAT drive) over the same cable.

irate-box-usb-gadget.service runs this as root (`usbgadget run`). The vendor kernel builds the
gadget framework and configfs but not the NCM, ACM and mass-storage functions, so the service
fetches the function sources pinned in the Driver resources toolkit (`toolkits/drivers.json`'s
`gadget`), checks each file's sha256, builds them with DKMS from `gadget/`, loads them, composes the
gadget through configfs, gives `usb0` a fixed locally administered MAC and a small private subnet
with DHCP, puts a getty on the ACM port, and gives the drive a FAT image of the file drop. Only on a
board whose UDC is in peripheral mode.

  usbgadget.py run                      the service: set it up, keep it up, tear it down on stop
  usbgadget.py status                   what is there, as JSON
  usbgadget.py set on|off               USB networking, applied at once
  usbgadget.py drive on|off             the USB drive, applied at once
  usbgadget.py drive-write on|off       read-only or writable, applied at once
  usbgadget.py drive-refresh            rebuild the drive image from the file drop

The choices are on Recovery and monitoring; networking is on by default on a Lyra (it is how a
headless board is reached), the drive is off. Settings in /etc/hub/usb-gadget.json (root's).
Stdlib only.
"""

import fcntl
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
SRC = Path(os.environ.get("HUB_GADGET_SRC", "/usr/src/usb-gadget-1.1.0"))
SETTINGS = ETC / "usb-gadget.json"
STATUS = RUN / "status.json"
ACTIVE = RUN / "active.json"
IMPORT_STATUS = RUN / "drive-import.json"
GADGET = CONFIGFS / "usb_gadget" / "irate"
FUNCTIONS = GADGET / "functions"
CONFIGS = GADGET / "configs"
STRINGS = GADGET / "strings"
UDC_DIR = SYS / "class/udc"

UNIT = "irate-box-usb-gadget.service"
PACKAGE = "usb-gadget"
VERSION = "1.1.0"
MODULES = ("u_ether", "usb_f_ncm", "u_serial", "usb_f_acm", "usb_f_mass_storage")
IFACE = "usb0"
ADDRESS = "192.168.7.1"
PREFIX = 24
NETMASK = "255.255.255.0"
DHCP_FIRST = "192.168.7.10"
DHCP_LAST = "192.168.7.50"
TTY = "ttyGS0"
VENDOR = "0x1d6b"
PRODUCT = "0x0104"
BCD_USB = "0x0200"
BCD_DEVICE = "0x0100"
MAX_POWER = "250"
DRIVE_FUNCTION = "mass_storage.usb0"
DRIVE_LUN = FUNCTIONS / DRIVE_FUNCTION / "lun.0"
DRIVE_IMAGE = STATE / "drop" / ".usb-drive.img"
DRIVE_MANIFEST = STATE / "drop" / ".usb-drive-manifest.json"
DRIVE_MIN_BYTES = 16 * 1024 * 1024
DRIVE_FREE_MIN = 64 * 1024 * 1024
DRIVE_SOURCE = "drop"
LOOK = 10  # seconds between looks at the gadget
WAIT = 15  # seconds for usb0 and the ACM port to appear after the UDC is bound
FETCH_TIMEOUT = 30
BUILD_TIMEOUT = 900
MACHINE_ID = Path(os.environ.get("HUB_MACHINE_ID", "/etc/machine-id"))
DNSMASQ = Path(os.environ.get("HUB_DNSMASQ_BIN", "/usr/sbin/dnsmasq"))
MKFS_VFAT = os.environ.get("HUB_MKFS_VFAT", "mkfs.vfat")
MCOPY = os.environ.get("HUB_MCOPY", "mcopy")
MDIR = os.environ.get("HUB_MDIR", "mdir")


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
    if not isinstance(raw, dict):
        raw = {}
    on = raw.get("on") if isinstance(raw.get("on"), bool) else default_on()
    drive = raw.get("drive") if isinstance(raw.get("drive"), bool) else False
    drive_write = raw.get("drive_write") if isinstance(raw.get("drive_write"), bool) else False
    source = raw.get("drive_source") if raw.get("drive_source") == DRIVE_SOURCE else DRIVE_SOURCE
    return {"on": on, "drive": drive, "drive_write": drive_write, "drive_source": source}


def chosen():
    """Whether an administrator has decided: the file says on or off."""
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and isinstance(raw.get("on"), bool)


def save_settings(**changes):
    from irate_box.root import safeio

    current = load_settings()
    current.update(changes)
    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(SETTINGS, json.dumps(current))


def peripherals():
    """The UDC names the board offers: a port in device (peripheral) mode, not a host port."""
    try:
        return sorted(p.name for p in UDC_DIR.iterdir())
    except OSError:
        return []


def modules_loaded():
    return {m: (SYS / "module" / m).is_dir() for m in MODULES}


def gadget_state():
    settings = load_settings()
    functions = sorted(p.name for p in FUNCTIONS.glob("*")) if FUNCTIONS.is_dir() else []
    bound = bool(read(GADGET / "UDC"))
    drive_file = read(DRIVE_LUN / "file") or ""
    network_up = not settings["on"] or ((SYS / "class/net" / IFACE).exists() and (SYS / "class/tty" / TTY).exists())
    drive_up = not settings["drive"] or bool(drive_file)
    return {
        "present": GADGET.is_dir(),
        "bound": bound,
        "functions": functions,
        "iface": IFACE,
        "address": f"{ADDRESS}/{PREFIX}",
        "tty": TTY,
        "network": settings["on"],
        "drive": settings["drive"],
        "drive_file": drive_file,
        "up": bool(bound and network_up and drive_up),
    }


def active_settings():
    try:
        data = json.loads(ACTIVE.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def import_status():
    try:
        data = json.loads(IMPORT_STATUS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_status():
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_status(up, why, pending=False):
    RUN.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"up": bool(up), "why": why, "pending": bool(pending), "at": time.time()}))


def status():
    udcs = peripherals()
    state = gadget_state()
    saved = read_status()
    settings = load_settings()
    pending = bool(udcs and (settings["on"] or settings["drive"]) and not state["up"])
    return {
        "model": model(),
        "peripheral": bool(udcs),
        "udc": udcs[0] if udcs else None,
        "settings": settings,
        "chosen": chosen(),
        "modules": modules_loaded(),
        "dnsmasq": dnsmasq_present(),
        "gadget": state,
        "drive": drive_state(),
        "up": state["up"],
        "pending": pending,
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
        FUNCTIONS / DRIVE_FUNCTION,
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
    return (read(MACHINE_ID) or "irate-box")[:16]


def macs(seed=None):
    """The NCM pair's locally administered MACs, from the machine id: stable per box, and two Lyras
    on one computer do not collide. The locally administered bit is set and the multicast bit clear."""
    seed = seed if seed is not None else (read(MACHINE_ID) or model() or "irate-box")
    digest = hashlib.sha256(str(seed).encode()).digest()

    def one(offset):
        octets = bytearray(digest[offset : offset + 6])
        octets[0] = (octets[0] | 0x02) & 0xFE
        return ":".join(f"{b:02x}" for b in octets)

    return one(0), one(6)


def dnsmasq_present():
    return DNSMASQ.is_file()


def dnsmasq_conf():
    return "\n".join(
        [
            "# Written by irate-box (root/usbgadget.py): an address on the USB network only. No router and",
            "# no DNS: a computer that plugs in keeps its own internet and name service.",
            f"interface={IFACE}",
            "bind-dynamic",
            "except-interface=lo",
            "no-resolv",
            "no-hosts",
            f"dhcp-range={DHCP_FIRST},{DHCP_LAST},{NETMASK},12h",
            "dhcp-option=3",
            "dhcp-option=6",
            f"dhcp-leasefile={RUN / 'dnsmasq.leases'}",
            "dhcp-authoritative",
            "",
        ]
    )


def drive_tools():
    """The image tools, or the names that are missing."""
    found = {
        "mkfs.vfat": os.environ.get("HUB_MKFS_VFAT") or shutil.which(MKFS_VFAT),
        "mcopy": os.environ.get("HUB_MCOPY") or shutil.which(MCOPY),
        "mdir": os.environ.get("HUB_MDIR") or shutil.which(MDIR),
    }
    return {name: bool(path) for name, path in found.items()}


def fat_name(name):
    """A display name a FAT filesystem and mtools accept."""
    name = "".join(c if c.isprintable() and c not in '<>:"/\\|?*' else "_" for c in str(name))
    return name.strip().rstrip(".") or "file"


def drive_groups():
    """The drive's file-drop source, as {name: {id, size}}; the image uses the display names."""
    from irate_box.hub import store

    drop = store.Drop(DRIVE_IMAGE.parent)
    used = set()
    out = []
    for meta in drop.list():
        data = DRIVE_IMAGE.parent / (meta["id"] + ".data")
        if not data.is_file():
            continue
        name = fat_name(store.clean_filename(meta.get("name") or "file"))
        base, n = name, 2
        while name in used:
            stem, dot, ext = base.rpartition(".")
            name = (stem or base) + f" ({n})" + (f".{ext}" if dot else "")
            n += 1
        used.add(name)
        out.append((name, data, int(meta.get("size") or data.stat().st_size), meta["id"]))
    return out


def drive_manifest():
    try:
        data = json.loads(DRIVE_MANIFEST.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def build_drive_image(log=print):
    """A FAT image of the file drop, ready for the mass-storage LUN: (ok, why)."""
    tools = drive_tools()
    missing = [name for name, ok in tools.items() if not ok]
    if missing:
        return False, f"the USB drive needs {', '.join(missing)} (dosfstools and mtools)"
    files = drive_groups()
    total = sum(size for _, _, size, _ in files)
    size = max(DRIVE_MIN_BYTES, total + total // 10 + 1024 * 1024)
    size = (size + (1 << 20) - 1) // (1 << 20) * (1 << 20)
    if size > 2 * 1024**3:
        return False, "the file drop is too large for one USB drive"
    DRIVE_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(DRIVE_IMAGE.parent).free < size + DRIVE_FREE_MIN:
        return False, "not enough free space for the USB drive image"
    tmp = DRIVE_IMAGE.with_name(DRIVE_IMAGE.name + ".new")
    tmp.unlink(missing_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.truncate(size)
        ok = run([MKFS_VFAT, "-F", "16" if size <= 2 * 1024**3 else "32", "-n", "IRATE-BOX", str(tmp)], timeout=120)
        if ok.returncode:
            return False, (ok.stderr or ok.stdout or "mkfs.vfat failed").strip().splitlines()[-1]
        for name, data, _, _ in files:
            log(f"usbgadget: adding {name} to the USB drive")
            copied = run([MCOPY, "-o", "-i", str(tmp), str(data), "::" + name], timeout=120)
            if copied.returncode:
                return False, (copied.stderr or copied.stdout or "mcopy failed").strip().splitlines()[-1]
        os.replace(tmp, DRIVE_IMAGE)
    finally:
        tmp.unlink(missing_ok=True)
    manifest = {name: {"id": ident, "size": size} for name, _, size, ident in files}
    DRIVE_MANIFEST.write_text(json.dumps(manifest))
    return True, None


def refresh_drive(log=print):
    ok, why = build_drive_image(log)
    if not ok:
        return False, why
    return True, None


def import_drive(log=print):
    """Bring files the host added to the USB drive into the file drop. Existing names are left
    alone; the drive is a copy, not a two-way mirror. It stops when the drop is full, rather
    than evicting a guest's file. Returns how many were added."""
    IMPORT_STATUS.unlink(missing_ok=True)
    if not DRIVE_IMAGE.is_file():
        return 0
    tools = drive_tools()
    if not tools["mdir"] or not tools["mcopy"]:
        return 0
    known = set(drive_manifest())
    tmp = RUN / "drive-import"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    listed = run([MDIR, "-b", "-i", str(DRIVE_IMAGE), "::/"], timeout=120)
    if listed.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    from irate_box.hub import store

    drop = store.Drop(DRIVE_IMAGE.parent)
    manifest = drive_manifest()
    added = 0
    stopped = ""
    for raw in (line.strip() for line in (listed.stdout or "").splitlines()):
        name = raw[3:] if raw.startswith("::/") else raw
        if not name or name in known or name in (".", "..") or "/" in name:
            continue
        one = tmp / "one"
        one.unlink(missing_ok=True)
        got = run([MCOPY, "-n", "-i", str(DRIVE_IMAGE), "::" + name, str(one)], timeout=120)
        if got.returncode or not one.is_file():
            continue
        size = one.stat().st_size
        if size > store.DROP_MAX_FILE:
            one.unlink(missing_ok=True)
            log(f"usbgadget: skipped {name} from the USB drive: larger than the file drop allows")
            continue
        try:
            with one.open("rb") as fh:
                meta = drop.receive(fh, size, name, by="USB drive", evict=False)
        except Refused as exc:
            one.unlink(missing_ok=True)
            stopped = str(exc)
            log(f"usbgadget: stopped importing at {name}: {exc}")
            break
        except (OSError, ValueError) as exc:
            one.unlink(missing_ok=True)
            log(f"usbgadget: skipped {name} from the USB drive: {exc}")
            continue
        one.unlink(missing_ok=True)
        manifest[name] = {"id": meta["id"], "size": size}
        known.add(name)
        added += 1
    if added:
        DRIVE_MANIFEST.write_text(json.dumps(manifest))
    shutil.rmtree(tmp, ignore_errors=True)
    RUN.mkdir(parents=True, exist_ok=True)
    IMPORT_STATUS.write_text(json.dumps({"added": added, "stopped": stopped, "at": time.time()}))
    log(f"usbgadget: imported {added} file(s) from the USB drive" + (f". Stopped: {stopped}" if stopped else ""))
    return added


def drive_state():
    settings = load_settings()
    return {
        "on": settings["drive"],
        "write": settings["drive_write"],
        "source": settings["drive_source"],
        "image": DRIVE_IMAGE.is_file(),
        "file": read(DRIVE_LUN / "file") or "",
        "tools": drive_tools(),
        "import": import_status(),
    }


def start_children():
    """dnsmasq for the USB network and a getty on the ACM port; they live and die with the service."""
    if not load_settings()["on"]:
        return []
    RUN.mkdir(parents=True, exist_ok=True)
    conf = RUN / "dnsmasq.conf"
    conf.write_text(dnsmasq_conf())
    log = (RUN / "dnsmasq.log").open("ab")
    children = [
        subprocess.Popen(  # noqa: TID251 - dnsmasq runs for the service's lifetime
            [str(DNSMASQ), "--keep-in-foreground", f"--conf-file={conf}", "--pid-file="],
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


def _applying():
    """The configfs tree is one shared resource: the service and a live apply take turns."""
    import contextlib

    @contextlib.contextmanager
    def held():
        RUN.mkdir(parents=True, exist_ok=True)
        with open(RUN / "apply.lock", "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    return held()


def _compose(settings, udcs, log=print, sleep=time.sleep, children=True):
    """Write the configfs tree from settings and bind it; the modules, dnsmasq and drive image are ready."""
    _teardown_tree()
    try:
        write(GADGET / "idVendor", VENDOR)
        write(GADGET / "idProduct", PRODUCT)
        write(GADGET / "bcdUSB", BCD_USB)
        write(GADGET / "bcdDevice", BCD_DEVICE)
        write(STRINGS / "0x409" / "serialnumber", _serial())
        write(STRINGS / "0x409" / "manufacturer", "irate-box")
        if settings["on"] and settings["drive"]:
            product, config = "irate-box USB networking and drive", "USB networking and drive"
        elif settings["on"]:
            product, config = "irate-box USB networking", "USB networking"
        else:
            product, config = "irate-box USB drive", "USB drive"
        write(STRINGS / "0x409" / "product", product)
        write(CONFIGS / "c.1" / "strings" / "0x409" / "configuration", config)
        write(CONFIGS / "c.1" / "MaxPower", MAX_POWER)
        write(CONFIGS / "c.1" / "bmAttributes", "0x80")
        if settings["on"]:
            dev_addr, host_addr = macs()
            write(FUNCTIONS / "ncm.usb0" / "dev_addr", dev_addr)
            write(FUNCTIONS / "ncm.usb0" / "host_addr", host_addr)
            (FUNCTIONS / "acm.usb0").mkdir(parents=True, exist_ok=True)
            _link("ncm.usb0")
            _link("acm.usb0")
        if settings["drive"]:
            (FUNCTIONS / DRIVE_FUNCTION / "lun.0").mkdir(parents=True, exist_ok=True)
            write(DRIVE_LUN / "file", str(DRIVE_IMAGE))
            write(DRIVE_LUN / "ro", "0" if settings["drive_write"] else "1")
            write(DRIVE_LUN / "removable", "1")
            write(DRIVE_LUN / "cdrom", "0")
            _link(DRIVE_FUNCTION)
        write(GADGET / "UDC", udcs[0])
    except OSError as exc:
        _teardown_tree()
        return False, f"configfs: {exc}", []
    if settings["on"]:
        for _ in range(WAIT):
            if (SYS / "class/net" / IFACE).exists() and (SYS / "class/tty" / TTY).exists():
                break
            sleep(1)
        if not (SYS / "class/net" / IFACE).exists():
            return False, f"{IFACE} did not appear", []
        run(["ip", "addr", "replace", f"{ADDRESS}/{PREFIX}", "dev", IFACE], timeout=30)
        run(["ip", "link", "set", IFACE, "up"], timeout=30)
    return True, None, start_children() if children else []


def _prepare(settings, log=print):
    """Everything the tree needs before it is written: modules, dnsmasq and the drive image."""
    udcs = peripherals()
    if not udcs:
        return None, "the board's USB port is not in peripheral mode"
    ok, why = ensure_modules(log)
    if not ok:
        return None, why
    if settings["on"] and not dnsmasq_present():
        return None, "dnsmasq is not installed (the dnsmasq-base package)"
    if settings["drive"]:
        missing = [name for name, present in drive_tools().items() if not present]
        if missing:
            return None, f"the USB drive needs {', '.join(missing)} (dosfstools and mtools)"
        if not (settings["drive_write"] and DRIVE_IMAGE.is_file()):
            built, why = build_drive_image(log)
            if not built:
                return None, why
    return udcs, None


def setup(log=print, sleep=time.sleep):
    """Compose the gadget, build the drive image and bring the network up: (ok, why, children)."""
    settings = load_settings()
    if not settings["on"] and not settings["drive"]:
        return False, "off", []
    RUN.mkdir(parents=True, exist_ok=True)
    with _applying():
        ACTIVE.write_text(json.dumps(settings))
        udcs, why = _prepare(settings, log)
        if not udcs:
            return False, why, []
        return _compose(settings, udcs, log, sleep)


def apply(settings=None, log=print, sleep=time.sleep):
    """Put the saved settings into the live gadget, without restarting the service: (ok, why).

    The UDC is unbound, the functions are unlinked and made again, and the UDC is bound: the host sees a
    disconnect and a reconnect. If the new tree cannot be made, the previous settings are put back.
    """
    old = active_settings() or load_settings()
    new = load_settings() if settings is None else dict(settings)
    RUN.mkdir(parents=True, exist_ok=True)
    with _applying():
        # The service may have made this change already between the save and this call.
        if active_settings() == new and gadget_state()["up"]:
            write_status(True, None)
            return True, None
        if not new["on"] and not new["drive"]:
            _teardown_tree()
            ACTIVE.write_text(json.dumps(new))
            if old.get("on"):
                run(["ip", "addr", "flush", "dev", IFACE], timeout=30)
            write_status(False, "off")
            return True, None
        udcs, why = _prepare(new, log)
        if not udcs:
            write_status(False, why)
            return False, why
        ACTIVE.write_text(json.dumps(new))
        write_status(False, "changing", pending=True)
        ok, why, _children = _compose(new, udcs, log, sleep, children=False)
        if not ok:
            # Put the tree the box had back, so a failed change is not a lost gadget.
            ACTIVE.write_text(json.dumps(old))
            _compose(old, udcs, log, sleep, children=False)
            write_status(False, why)
            return False, why
    write_status(True, None)
    return True, None


def _tick(children, log=print, sleep=time.sleep):
    """One look by the service: apply a changed setting, or repair a gadget that fell over."""
    settings = load_settings()
    if not settings["on"] and not settings["drive"]:
        stop_children(children)
        if active_settings() != settings:
            ok, why = apply(settings, log=log, sleep=sleep)
            write_status(ok, why)
        else:
            write_status(False, "off")
        return [], False
    if active_settings() != settings:
        ok, why = apply(settings, log=log, sleep=sleep)
        write_status(ok, why, pending=not ok)
        log(f"usbgadget: {'changed' if ok else 'not changed'}" + (f": {why}" if why else ""))
        return children, True
    if not gadget_state()["up"]:
        stop_children(children)
        write_status(False, "starting", pending=True)
        ok, why, children = setup(log, sleep)
        write_status(ok, why, pending=not ok)
        log(f"usbgadget: {'up' if ok else 'not up'}" + (f": {why}" if why else ""))
        return children, True
    if settings["on"] and not children:
        return start_children(), True
    if any(child.poll() is not None for child in children):
        stop_children(children)
        return start_children(), True
    return children, False


def teardown(children=()):
    stop_children(children)
    settings = active_settings() or load_settings()
    if settings.get("on"):
        run(["ip", "addr", "flush", "dev", IFACE], timeout=30)
    _teardown_tree()
    if settings.get("drive") and settings.get("drive_write") and DRIVE_IMAGE.is_file():
        os.sync()
        import_drive()
    for path in (STATUS, ACTIVE):
        try:
            path.unlink()
        except OSError:
            pass


def serve(log=print):
    """The service: set the gadget up, keep it and its helpers alive, tear it down on SIGTERM."""
    stopping = {"now": False}

    def stop(_signum, _frame):
        stopping["now"] = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    while not stopping["now"]:
        children, _changed = _tick(children, log)
        time.sleep(LOOK)
    teardown(children)
    return 0


def set_option(value):
    if value not in ("on", "off"):
        raise Refused(M("usb-gadget-set.unknown", "USB networking has no setting {value}.", value=value))
    save_settings(on=(value == "on"))
    ok, why = apply()
    if not ok:
        raise Refused(
            M(
                "usb-gadget-set.not-applied",
                "The setting is saved, but the USB gadget could not change now: {why}",
                why=why or "unknown",
            )
        )
    if value == "on":
        return M(
            "usb-gadget-set.on",
            "USB networking is on. The box offers a network link at {address} and a console on /dev/{tty} "
            "while its USB port is connected.",
            address=ADDRESS,
            tty=TTY,
        )
    return M("usb-gadget-set.off", "USB networking is off. The USB port is left alone.")


def set_drive(value):
    if value not in ("on", "off"):
        raise Refused(M("usb-gadget-set.unknown", "USB networking has no setting {value}.", value=value))
    save_settings(drive=(value == "on"))
    ok, why = apply()
    if not ok:
        raise Refused(
            M(
                "usb-gadget-set.not-applied",
                "The setting is saved, but the USB gadget could not change now: {why}",
                why=why or "unknown",
            )
        )
    if value == "on":
        return M(
            "usb-gadget-drive-set.on",
            "The USB drive is on. Its image comes from the file drop. Connect the USB port to a computer to use it.",
        )
    return M("usb-gadget-drive-set.off", "The USB drive is off.")


def set_drive_write(value):
    if value not in ("on", "off"):
        raise Refused(M("usb-gadget-set.unknown", "USB networking has no setting {value}.", value=value))
    save_settings(drive_write=(value == "on"))
    ok, why = apply()
    if not ok:
        raise Refused(
            M(
                "usb-gadget-set.not-applied",
                "The setting is saved, but the USB gadget could not change now: {why}",
                why=why or "unknown",
            )
        )
    if value == "on":
        return M(
            "usb-gadget-drive-write.on",
            "The USB drive is writable. Files added to it are copied into the file drop when the drive stops. "
            "Changes to files already on it are not copied back.",
        )
    return M("usb-gadget-drive-write.off", "The USB drive is read-only.")


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        return serve()
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 2:
        print(set_option(argv[1]))
    elif cmd == "drive" and len(argv) == 2:
        print(set_drive(argv[1]))
    elif cmd == "drive-write" and len(argv) == 2:
        print(set_drive_write(argv[1]))
    elif cmd == "drive-refresh":
        ok, why = refresh_drive()
        print("ok" if ok else f"failed: {why}")
        return 0 if ok else 1
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
