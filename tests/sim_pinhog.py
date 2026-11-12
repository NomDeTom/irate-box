# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Pin hogs (root/pinhog.py), offline: the overlay's text for an input and an output on one controller,
add, change and remove, pending until the next boot, a claimed and a power pin refused, the last hog
taking the overlay away, and a dtc failure leaving the tree as it was.
python3 tests/sim_pinhog.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="pinhog-"))
for d in ("boot", "state/control", "etc", "dt/soc/gpio@ff120000", "gpio/devices/gpiochip0/device"):
    (T / d).mkdir(parents=True, exist_ok=True)
(T / "boot_id").write_text("boot-one\n")
(T / "boot" / "armbianEnv.txt").write_text("verbosity=1\nuser_overlays=mine\n")
node = T / "gpio" / "devices" / "gpiochip0" / "device" / "of_node"
node.symlink_to(T / "dt" / "soc" / "gpio@ff120000", target_is_directory=True)
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_BOOT_DIR=str(T / "boot"),
    HUB_BOOT_ID=str(T / "boot_id"),
    HUB_GPIO_SYS=str(T / "gpio" / "devices"),
    HUB_DT_BASE=str(T / "dt"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import lcdtouch, ownserial, pinhog, pinmap

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


header = [
    {"pin": 1, "type": "3v3", "name": "3V3_OUT", "used": False},
    {"pin": 18, "type": "gpio", "name": "GPIO0_B4", "chip": "gpiochip0", "line": 12, "used": False, "consumer": ""},
    {"pin": 22, "type": "gpio", "name": "GPIO0_B3", "chip": "gpiochip0", "line": 11, "used": False, "consumer": ""},
    {
        "pin": 21,
        "type": "gpio",
        "name": "GPIO0_A5",
        "chip": "gpiochip0",
        "line": 5,
        "used": True,
        "consumer": "other-driver",
    },
    {"pin": 26, "type": "gpio", "name": "GPIO0_B1", "chip": "gpiochip0", "line": 9, "used": False, "consumer": ""},
    {
        "pin": 35,
        "type": "gpio",
        "name": "GPIO0_C0",
        "chip": "gpiochip0",
        "line": 16,
        "used": True,
        "consumer": "spidev",
    },
]
(T / "state" / "control" / "pins.json").write_text(json.dumps({"header": header}))
pinmap.snapshot = lambda: {"header": header}
lcdtouch.status = lambda: {"supported": True, "enabled": True, "active": False}
ownserial.status = lambda: {"supported": False, "on": False}

compiled = []


def fake_dtc(argv, inp=None):
    compiled.append(inp)
    Path(argv[argv.index("-o") + 1]).write_text("fake dtbo")
    return subprocess.CompletedProcess(argv, 0, "", "")


pinhog._dtc = fake_dtc
env = T / "boot" / "armbianEnv.txt"

said = pinhog.add(18, "in", "up")
text = compiled[-1]
check(
    "an input with a pull-up: the gpio-hog node and line-name under its controller",
    said["id"] == "pinhog.added.in"
    and pinhog.DTBO.is_file()
    and 'target-path = "/soc/gpio@ff120000"' in text
    and "gpios = <12 16>" in text
    and 'line-name = "irate-box-pin18"' in text
    and "input;" in text
    and "bias-pull-up;" not in text
    and "user_overlays=mine irate-box-pin-hogs" in env.read_text(),
    (compiled[-1], env.read_text()),
)
check(
    "  pending until the next boot, and assigned idle",
    pinhog.pending() is True
    and pinhog.assignments()[0]["pending"] is True
    and pinhog.assignments()[0]["state"] == "idle",
    pinhog.assignments(),
)
said = pinhog.add(22, "out", "none", 1)
text = compiled[-1]
check(
    "another pin on the same controller: one fragment, an output high",
    said["id"] == "pinhog.added.out"
    and text.count("fragment@") == 1
    and text.count("gpio-hog;") == 2
    and "gpios = <11 0>" in text
    and "output-high;" in text
    and set(pinhog.load()) == {18, 22},
    compiled[-1],
)
(T / "boot_id").write_text("boot-two\n")
header[1].update(used=True, consumer="irate-box-pin18")
(T / "state" / "control" / "pins.json").write_text(json.dumps({"header": header}))
hogs = {item["pin"]: item for item in pinhog.assignments()}
check(
    "  a restart makes an applied hog held, not pending, and not merely an active owner",
    pinhog.pending() is False
    and hogs[18]["state"] == "holding"
    and hogs[18]["holding"] is True
    and hogs[18]["owner"] == "pinhog"
    and hogs[18]["active_owner"] == "irate-box-pin18"
    and hogs[22]["state"] == "idle",
    hogs,
)
pinhog.add(22, "out", "none", 1)
hogs = {item["pin"]: item for item in pinhog.assignments()}
check(
    "  a held hog stays holding when another is added; only the new one waits",
    hogs[18]["state"] == "holding"
    and hogs[18]["pending"] is False
    and hogs[22]["state"] == "idle"
    and hogs[22]["pending"] is True,
    hogs,
)
said = pinhog.add(18, "out", "none", 0)
check(
    "  our own applied hog can be changed",
    said["id"] == "pinhog.added.out"
    and "irate-box-pin18" in compiled[-1]
    and "output-low;" in compiled[-1]
    and pinhog.load()[18]["mode"] == "output",
    (said, pinhog.load()),
)
pinhog.remove(18)
check(
    "remove one: its node gone, the other kept, the overlay still on",
    set(pinhog.load()) == {22}
    and "irate-box-pin18" not in compiled[-1]
    and "irate-box-pin22" in compiled[-1]
    and pinhog.DTBO.is_file()
    and "irate-box-pin-hogs" in env.read_text(),
    compiled[-1],
)
pinhog.remove(22)
check(
    "remove the last: the .dtbo and the overlay name go too",
    not pinhog.load() and not pinhog.DTBO.exists() and "user_overlays=mine\n" in env.read_text(),
    env.read_text(),
)
removed = {item["pin"]: item for item in pinhog.assignments()}
check(
    "  a held hog removed last stays pending removal until the next boot",
    pinhog.pending() is True
    and removed[18]["removed"] is True
    and removed[18]["state"] == "holding"
    and removed[18]["pending"] is True,
    removed,
)
(T / "boot_id").write_text("boot-three\n")
check(
    "  after the next boot the removal is gone",
    pinhog.pending() is False and pinhog.assignments() == [],
    pinhog.assignments(),
)

check(
    "a pin another function claims is refused",
    refused(pinhog.add, 21, "in", "none") == "pinhog.claimed",
)
check("a power pin is refused", refused(pinhog.add, 1, "in", "none") == "pinhog.not-gpio")
check("an assigned display pin is refused", refused(pinhog.add, 26, "in", "none") == "pinhog.assigned")
check("an unknown pin is refused", refused(pinhog.add, 99, "in", "none") == "pinhog.unknown-pin")
check("a bad pull or level is refused", refused(pinhog.add, 18, "in", "sideways") == "pinhog.bias")
check("  and an output with no level", refused(pinhog.add, 18, "out", "none") == "pinhog.value")

# A compiler that fails leaves no new overlay, and keeps the one that was there.
pinhog.add(18, "in", "none")
before = pinhog.DTBO.read_bytes()
pinhog._dtc = lambda argv, inp=None: subprocess.CompletedProcess(argv, 1, "", "syntax error")
check(
    "a compile failure: refused, the previous .dtbo kept and no change",
    refused(pinhog.add, 22, "out", "none", 0) == "pinhog.dtc-failed"
    and pinhog.DTBO.read_bytes() == before
    and set(pinhog.load()) == {18},
    pinhog.load(),
)
pinhog._dtc = lambda argv, inp=None: subprocess.CompletedProcess(argv, 127, "", "no dtc")
check("  dtc missing is its own refusal", refused(pinhog.add, 22, "in", "none") == "pinhog.dtc-missing")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
