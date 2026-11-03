# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""This box's own header pins as a serial console (root/ownserial.py), offline: the Pi's config.txt and cmdline
(enable_uart, miniuart-bt, console=serial0 off and put back, lines already there left alone), the Lyra's Armbian
user overlay for UART1 on RMIO22/23 and its armbianEnv, on and off, what is ours taken away, and the pending state
until the device appears. No board is touched. python3 tests/sim_ownserial.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="ownserial-"))
(T / "boot" / "firmware").mkdir(parents=True)
(T / "etc").mkdir()
(T / "state" / "control").mkdir(parents=True)
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_BOOT_DIR=str(T / "boot"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import ownserial

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except Refused as exc:
        return exc.message["id"]
    return None


config = T / "boot" / "firmware" / "config.txt"
cmdline = T / "boot" / "firmware" / "cmdline.txt"
config.write_text("dtparam=audio=on\nenable_uart=1\n")
cmdline.write_text("console=serial0,115200 root=PARTUUID=x rootwait\n")
ownserial.hardening.board = lambda: "pi"
check(
    "a Pi: supported, off, its own console at /dev/ttyAMA0",
    ownserial.status()
    == {
        "board": "pi",
        "supported": True,
        "on": False,
        "pending": False,
        "device": "",
        "warning": ownserial.WARNING["pi"],
    },
    ownserial.status(),
)
ownserial.set_pins(True)
text = config.read_text()
check(
    "on: [all], the marker and both lines at the end, console=serial0 off",
    text.splitlines()[-4:] == ["[all]", ownserial.MARKER, "enable_uart=1", "dtoverlay=miniuart-bt"]
    and "console=serial0" not in cmdline.read_text(),
    (text, cmdline.read_text()),
)
check(
    "  on until a restart gives the device, and the state says what was added",
    ownserial.status()["on"] is True
    and ownserial.status()["pending"] is True
    and ownserial._state()["added"] == ["[all]", ownserial.MARKER, "enable_uart=1", "dtoverlay=miniuart-bt"]
    and ownserial._state()["cmdline_removed"] == "console=serial0,115200",
    ownserial._state(),
)
ownserial.set_pins(False)
check(
    "off: the file back as it was, enable_uart still there, the console token back",
    config.read_text() == "dtparam=audio=on\nenable_uart=1\n"
    and cmdline.read_text().startswith("console=serial0,115200 "),
    (config.read_text(), cmdline.read_text()),
)
# A config that ends in another section: our lines still land in [all], and off gives the file back.
config.write_text("dtparam=audio=on\n[pi4]\ngpu_mem=64\n")
ownserial.set_pins(True)
text = config.read_text()
check(
    "a config ending in [pi4]: [all] and our lines after it, not under [pi4]",
    text.splitlines()[-4:] == ["[all]", ownserial.MARKER, "enable_uart=1", "dtoverlay=miniuart-bt"]
    and "[pi4]\ngpu_mem=64\n" in text,
    text,
)
ownserial.set_pins(False)
check(
    "  off: [pi4] and its line back as they were",
    config.read_text() == "dtparam=audio=on\n[pi4]\ngpu_mem=64\n",
    config.read_text(),
)
check(
    "  off: status says so and the device is not offered",
    ownserial.status()["on"] is False and ownserial.status()["device"] == "",
)

# A Lyra: an Armbian user overlay for UART1 on RMIO22 (TX, pin 8) and RMIO23 (RX, pin 10).
for f in (T / "boot").glob("*"):
    if f.is_file():
        f.unlink()
(T / "boot" / "armbianEnv.txt").write_text("verbosity=1\nuser_overlays=irate-box-watchdog\n")
dts = []
real_run = ownserial.run


def fake_dtc(*cmd, timeout=120, inp=None):
    dts.append(inp)
    out = Path(cmd[cmd.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("dtbo")
    return subprocess.CompletedProcess(cmd, 0, "", "")


check("the Lyra's own console is /dev/ttyS1", ownserial.DEVICE["lyra"] == "/dev/ttyS1")
ownserial.run = fake_dtc
ownserial.hardening.board = lambda: "lyra"
ownserial.DEVICE = {"pi": "/dev/ttyAMA0", "lyra": str(T / "ttyS1-not-here")}
ownserial.set_pins(True)
dtbo = T / "boot" / "overlay-user" / "irate-box-serial.dtbo"
check(
    "a Lyra: the overlay built and in user_overlays, UART1 on RMIO22/23 in its DTS",
    dtbo.is_file()
    and "user_overlays=irate-box-watchdog irate-box-serial" in (T / "boot" / "armbianEnv.txt").read_text()
    and "&uart1" in dts[-1]
    and "&rm_io22_uart1_tx" in dts[-1]
    and "&rm_io23_uart1_rx" in dts[-1],
    (dtbo.exists(), (T / "boot" / "armbianEnv.txt").read_text(), dts[-1] if dts else None),
)
check(
    "  its device is the UART1 node, pending until a restart gives it",
    ownserial.status()["device"] == str(T / "ttyS1-not-here") and ownserial.status()["pending"] is True,
    ownserial.status(),
)
ownserial.set_pins(False)
check(
    "off: the overlay gone and its name out of user_overlays",
    not dtbo.exists() and "user_overlays=irate-box-watchdog" in (T / "boot" / "armbianEnv.txt").read_text(),
    (dtbo.exists(), (T / "boot" / "armbianEnv.txt").read_text()),
)
ownserial.run = real_run
ownserial.hardening.board = lambda: "other"
check("a board with no known pins: refused", refused(ownserial.set_pins, True) == "ownserial.unsupported")
ownserial.hardening.board = lambda: "pi"
ownserial.set_pins(True)
ownserial.undo_all()
check("uninstall: undo-all puts it back off", ownserial.status()["on"] is False)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
