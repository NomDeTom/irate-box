# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""USB networking (root/usbgadget.py), offline: a fake configfs and /sys, the gadget tree the
service writes (NCM plus ACM, the machine's MACs, the UDC), the owner's setting (on by default on a
Lyra, off elsewhere), the pinned source fetch and its sha256 check, and the Box doctor's finding
when it is on but the gadget did not come up. python3 tests/sim_usb_gadget.py"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="usb-gadget-"))
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_SYSFS=str(T / "sys"),
    HUB_CONFIGFS=str(T / "config"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_USB_GADGET_RUN=str(T / "run"),
    HUB_KITS_ROOT=str(T / "kits"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_GADGET_SRC=str(T / "src"),
    HUB_MACHINE_ID=str(T / "machine-id"),
    HUB_DNSMASQ_BIN=str(T / "dnsmasq"),
    HUB_MKFS_VFAT="mkfs.vfat",
    HUB_MCOPY="mcopy",
    HUB_MDIR="mdir",
)
for d in (
    "etc",
    "state",
    "sys/class/udc",
    "sys/class/net",
    "sys/class/tty",
    "config",
    "dt",
    "run",
    "kits",
    "code/gadget",
    "code/toolkits",
    "src",
):
    (T / d).mkdir(parents=True, exist_ok=True)
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\x00")
(T / "machine-id").write_text("machine-one\n")
(T / "dnsmasq").write_text("")
(T / "sys/class/udc/ff740000.usb").write_text("")
for m in ("u_ether", "usb_f_ncm", "u_serial", "usb_f_acm", "usb_f_mass_storage"):
    (T / "sys/module" / m).mkdir(parents=True)
(T / "sys/class/net/usb0").mkdir()
(T / "sys/class/tty/ttyGS0").mkdir()
for f in ("Makefile", "dkms.conf"):
    shutil.copyfile(REPO / "gadget" / f, T / "code" / "gadget" / f)

sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import health, usbgadget

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Answer:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.data


ran = []


def fake_run(cmd, timeout=120):
    ran.append(cmd)
    return mock.Mock(returncode=0, stdout="", stderr="")


usbgadget.run = fake_run
usbgadget.start_children = lambda: []

# The setting: on for a Lyra, off elsewhere, and the file wins once chosen.
check("a Lyra: USB networking is on by default", usbgadget.load_settings()["on"] is True)
check("  and it is not yet an administrator's choice", usbgadget.chosen() is False)
(T / "dt" / "model").write_bytes(b"Some other board\x00")
check("another board: off by default", usbgadget.load_settings()["on"] is False)
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\x00")
(T / "machine-id").write_text("machine-one\n")
(T / "dnsmasq").write_text("")

# The gadget tree the service writes.
ok, why, children = usbgadget.setup(sleep=lambda _: None)
g = T / "config/usb_gadget/irate"
check("setup: the gadget is composed", ok, why)
check("  vendor and product", (g / "idVendor").read_text() == "0x1d6b" and (g / "idProduct").read_text() == "0x0104")
check(
    "  its strings",
    (g / "strings/0x409/manufacturer").read_text() == "irate-box"
    and (g / "strings/0x409/product").read_text() == "irate-box USB networking",
)
check(
    "  the config",
    (g / "configs/c.1/MaxPower").read_text() == "250"
    and (g / "configs/c.1/strings/0x409/configuration").read_text() == "USB networking",
)
dev, host = usbgadget.macs()
check(
    "  NCM with the machine's own locally administered MACs",
    (g / "functions/ncm.usb0/dev_addr").read_text() == dev
    and (g / "functions/ncm.usb0/host_addr").read_text() == host
    and dev != host
    and all(int(m.split(":")[0], 16) & 0x03 == 0x02 for m in (dev, host)),
    (dev, host),
)
other = usbgadget.macs("machine-two")
check("  another machine id: another pair", other != (dev, host) and other[0] != other[1], other)
conf = usbgadget.dnsmasq_conf()
check(
    "  the DHCP answer is an address only: no router, no DNS, no wildcard",
    "option:router" not in conf
    and "option:dns-server" not in conf
    and "address=/#/" not in conf
    and "dhcp-option=3" in conf
    and "dhcp-option=6" in conf,
    conf,
)
check("  ACM too", (g / "functions/acm.usb0").is_dir())
check(
    "  both linked into the config",
    (g / "configs/c.1/ncm.usb0").is_symlink() and (g / "configs/c.1/acm.usb0").is_symlink(),
)
check("  bound to the board's UDC", (g / "UDC").read_text() == "ff740000.usb")
check(
    "  usb0 addressed and up",
    ["ip", "addr", "replace", "192.168.7.1/24", "dev", "usb0"] in ran and ["ip", "link", "set", "usb0", "up"] in ran,
)
st = usbgadget.status()
check("status: on, peripheral, up", st["peripheral"] and st["settings"]["on"] and st["up"], st)
check(
    "  the functions and the address",
    st["gadget"]["functions"] == ["acm.usb0", "ncm.usb0"] and st["gadget"]["address"] == "192.168.7.1/24",
    st["gadget"],
)
check("  gadget_state says up too", usbgadget.gadget_state()["up"] is True)

# The doctor: up, down, off, and no peripheral port.
found = health.check_usb_gadget(st)
check(
    "the doctor: up is an ok finding naming the address and the console",
    found and found[0]["status"] == "ok" and "usb-gadget.ok" in message_ids(found[0]),
    found,
)
groups = health.usb_gadget_groups(st)
check(
    "  the setting's group: on, with both choices",
    groups
    and groups[0]["current"] == "on"
    and [o["choice"] for o in groups[0]["options"]] == ["usb-gadget:on", "usb-gadget:off"],
    groups,
)
down = dict(st, up=False, why="u_ether: could not be fetched (offline)", modules={"u_ether": False})
found = health.check_usb_gadget(down)
check(
    "  down: a problem, the why in the evidence, and a restart",
    found
    and found[0]["status"] == "problem"
    and "usb-gadget.down" in message_ids(found[0])
    and found[0]["actions"][0]["choice"] == "unit-restart:irate-box-usb-gadget.service"
    and any("offline" in e for e in found[0]["evidence"]),
    found,
)
off = dict(st, settings={"on": False})
check(
    "  off: no finding, and the group still offers it",
    health.check_usb_gadget(off) == [] and health.usb_gadget_groups(off)[0]["current"] == "off",
)
nodns = dict(st, dnsmasq=False)
found = health.check_usb_gadget(nodns)
check(
    "  dnsmasq missing: a problem naming dnsmasq-base",
    found and found[0]["status"] == "problem" and "usb-gadget.dnsmasq" in message_ids(found[0]),
    found,
)
starting = dict(st, up=False, why="starting")
found = health.check_usb_gadget(starting)
check(
    "  starting: a note, not a problem",
    found and found[0]["status"] == "note" and "usb-gadget.starting" in message_ids(found[0]),
    found,
)
host = dict(st, peripheral=False)
check(
    "  a host port: no finding and no group",
    health.check_usb_gadget(host) == [] and health.usb_gadget_groups(host) == [],
)

# The choice is saved and applied through the unit.
ran.clear()
said = usbgadget.set_option("off")
check(
    "set off: saved, and the unit stopped",
    usbgadget.load_settings()["on"] is False and ["systemctl", "stop", "irate-box-usb-gadget.service"] in ran,
    said,
)
ran.clear()
said = usbgadget.set_option("on")
check(
    "set on: saved, and the unit restarted",
    usbgadget.load_settings()["on"] is True and ["systemctl", "restart", "irate-box-usb-gadget.service"] in ran,
    said,
)

# The USB drive: the file drop as a FAT image, read-only by default, writable by choice.
check("USB drive: off by default", usbgadget.load_settings()["drive"] is False)
drop = T / "state/drop"
drop.mkdir(parents=True, exist_ok=True)
(drop / "one.data").write_bytes(b"hello")
(drop / "one.meta").write_text(json.dumps({"id": "one", "name": "hello.txt", "size": 5, "created": 0}))
said = usbgadget.set_drive("on")
check("  set on: saved", usbgadget.load_settings()["drive"] is True, said)
ran.clear()
ok, why, children = usbgadget.setup(sleep=lambda _: None)
g = T / "config/usb_gadget/irate"
lun = g / "functions/mass_storage.usb0/lun.0"
image = drop / ".usb-drive.img"
check("  setup builds the image and the LUN", ok and image.is_file() and lun.is_dir(), why)
check("  the LUN points at the image", lun.joinpath("file").read_text() == str(image))
check("  read-only by default", lun.joinpath("ro").read_text() == "1")
check(
    "  the manifest names the drop's file",
    json.loads((drop / ".usb-drive-manifest.json").read_text())["hello.txt"]["id"] == "one",
)
said = usbgadget.set_drive_write("on")
ran.clear()
ok, why, children = usbgadget.setup(sleep=lambda _: None)
check("  writable: the LUN is read-write", ok and lun.joinpath("ro").read_text() == "0", why)
manifest = json.loads((drop / ".usb-drive-manifest.json").read_text())


def fake_import_run(cmd, timeout=120):
    if cmd[0] == usbgadget.MDIR:
        return mock.Mock(returncode=0, stdout="hello.txt\nnew.txt\n", stderr="")
    if cmd[0] == usbgadget.MCOPY:
        Path(cmd[-1]).write_bytes(b"new")
        return mock.Mock(returncode=0, stdout="", stderr="")
    return fake_run(cmd, timeout)


usbgadget.run = fake_import_run
added = usbgadget.import_drive(log=lambda *_: None)
check("  a new file on the drive is added to the drop", added == 1, added)
check(
    "  the manifest remembers it, so it is not imported twice",
    json.loads((drop / ".usb-drive-manifest.json").read_text()).get("new.txt", {}).get("size") == 3,
)
usbgadget.run = fake_run
st_drive = usbgadget.status()
st_drive["drive"]["tools"]["mkfs.vfat"] = False
found = health.check_usb_gadget(st_drive)
check(
    "  missing dosfstools: a problem naming the tools",
    found and found[0]["status"] == "problem" and "usb-gadget.tools" in message_ids(found[0]),
    found,
)
usbgadget.set_drive_write("off")
usbgadget.set_drive("off")
check("  off: the drive choice is saved", usbgadget.load_settings()["drive"] is False)

# The pinned source fetch: the cache first, then the commit's raw file, checked against its sha256.
data = b"// a pinned source\n"
sha = hashlib.sha256(data).hexdigest()
pin = {
    "id": "drivers",
    "title": "Driver resources",
    "packages": ["dkms"],
    "gadget": {
        "repo": "https://github.com/armbian/linux-rockchip",
        "tag": "v6.1.115",
        "commit": "8f84d3b",
        "files": {"drivers/usb/gadget/function/f_ncm.c": sha},
    },
}
(T / "code/toolkits/drivers.json").write_text(json.dumps(pin))
with mock.patch.object(usbgadget.urllib.request, "urlopen", lambda url, timeout=30: Answer(data)):
    files, why = usbgadget.fetch_sources(log=lambda *_: None)
check(
    "fetch: the pinned file, checked and cached",
    files and files["f_ncm.c"].read_bytes() == data and (T / "kits/gadget/f_ncm.c").read_bytes() == data,
    why,
)
(T / "kits/gadget/f_ncm.c").write_bytes(b"tampered")
with mock.patch.object(usbgadget.urllib.request, "urlopen", lambda url, timeout=30: Answer(data)):
    files, why = usbgadget.fetch_sources(log=lambda *_: None)
check("  a tampered cache is replaced", files and (T / "kits/gadget/f_ncm.c").read_bytes() == data, why)
(T / "kits/gadget/f_ncm.c").write_bytes(b"tampered")
with mock.patch.object(usbgadget.urllib.request, "urlopen", lambda url, timeout=30: Answer(b"not it")):
    files, why = usbgadget.fetch_sources(log=lambda *_: None)
check("  a download that does not match its hash is refused", files is None and "sha256" in why, why)

# The build copies the pinned files and the repo's Makefile/dkms.conf, then runs DKMS.
ran.clear()
fake_files = {"f_ncm.c": T / "kits/gadget/f_ncm.c"}
with mock.patch.object(usbgadget, "fetch_sources", lambda log=print: (fake_files, None)):
    ok, why = usbgadget.build_modules(log=lambda *_: None)
check(
    "build: the sources and the build files are in /usr/src, and DKMS ran",
    ok
    and (T / "src/f_ncm.c").is_file()
    and (T / "src/Makefile").is_file()
    and (T / "src/dkms.conf").is_file()
    and any(c[:2] == ["dkms", "build"] for c in ran),
    why,
)

# Installed modules are loaded at boot, not rebuilt.
(T / "sys/module/u_ether").rmdir()
ran.clear()
with mock.patch.object(usbgadget, "build_modules", lambda log=print: (_ for _ in ()).throw(AssertionError("built"))):
    ok, why = usbgadget.ensure_modules(log=lambda *_: None)
check("installed modules are loaded, not rebuilt", ok and ["modprobe", "u_ether"] in ran, why)
(T / "sys/module/u_ether").mkdir()

# Tearing it down unbinds the UDC and removes the tree.
usbgadget.teardown([])
check("teardown: the UDC unbound and the gadget gone", not (T / "config/usb_gadget/irate").exists())

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
