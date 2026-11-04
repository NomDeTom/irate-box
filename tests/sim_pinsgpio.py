# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One-shot pin reads and sets (root/pinsgpio.py), offline: a pull-up and a pull-down read, an output set
high, the line opened once and released, and a claimed, hogged, power or unknown pin refused.
python3 tests/sim_pinsgpio.py"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="pinsgpio-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import pinmap, pinsgpio

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
        "pin": 35,
        "type": "gpio",
        "name": "GPIO0_C0",
        "chip": "gpiochip0",
        "line": 16,
        "used": True,
        "consumer": "spidev",
    },
    {
        "pin": 36,
        "type": "gpio",
        "name": "GPIO0_C1",
        "chip": "gpiochip0",
        "line": 17,
        "used": True,
        "consumer": "irate-box-pin36",
    },
]
pinmap.snapshot = lambda: {"header": header}
reads = []
requests = []


class Request:
    value = "active"

    def __init__(self, device, config):
        self.device = device
        self.config = config
        self.released = False

    def get_value(self, line):
        return Request.value

    def release(self):
        self.released = True


class LineSettings:
    def __init__(self, **fields):
        self.fields = fields


def request_lines(device, consumer, config):
    request = Request(device, config)
    requests.append((device, consumer, config, request))
    return request


fake = SimpleNamespace(
    line=SimpleNamespace(
        Direction=SimpleNamespace(INPUT="input", OUTPUT="output"),
        Bias=SimpleNamespace(PULL_UP="pull-up", PULL_DOWN="pull-down"),
        Value=SimpleNamespace(ACTIVE="active", INACTIVE="inactive"),
    ),
    LineSettings=LineSettings,
    request_lines=request_lines,
)
pinsgpio._gpiod = lambda: fake

said = pinsgpio.set_pin(18, "in", "up")
device, consumer, config, request = requests[-1]
check(
    "a pull-up read: one request, the line read and released",
    said["id"] == "pins-gpio.in.high"
    and device == "/dev/gpiochip0"
    and consumer == "irate-box-pin"
    and config[12].fields["direction"] == "input"
    and config[12].fields["bias"] == "pull-up"
    and request.released,
    (said, device, consumer, config[12].fields, request.released),
)
Request.value = "inactive"
said = pinsgpio.set_pin(22, "in", "down")
check(
    "a pull-down read low",
    said["id"] == "pins-gpio.in.low" and requests[-1][2][11].fields["bias"] == "pull-down",
    said,
)
said = pinsgpio.set_pin(18, "in", "none")
check(
    "no pull: no bias field, just an input",
    said["id"] == "pins-gpio.in.low" and "bias" not in requests[-1][2][12].fields,
    requests[-1][2][12].fields,
)
said = pinsgpio.set_pin(18, "out", "none", 1)
device, consumer, config, request = requests[-1]
check(
    "an output high: one request, output value ACTIVE, released",
    said["id"] == "pins-gpio.out"
    and config[12].fields["direction"] == "output"
    and config[12].fields["output_value"] == "active"
    and request.released,
    (said, config[12].fields, request.released),
)
said = pinsgpio.set_pin(18, "out", "none", 0)
check("an output low: INACTIVE", requests[-1][2][12].fields["output_value"] == "inactive", said)

check("a claimed pin is refused", refused(pinsgpio.set_pin, 35, "in", "none") == "pins-gpio.used")
check("a pin hog's pin is refused", refused(pinsgpio.set_pin, 36, "in", "none") == "pins-gpio.hogged")
check("a power pin is refused", refused(pinsgpio.set_pin, 1, "in", "none") == "pins-gpio.not-gpio")
check("an unknown pin is refused", refused(pinsgpio.set_pin, 99, "in", "none") == "pins-gpio.unknown-pin")
check("a bad mode is refused", refused(pinsgpio.set_pin, 18, "sideways", "none") == "pins-gpio.mode")
check("a bad pull is refused", refused(pinsgpio.set_pin, 18, "in", "sideways") == "pins-gpio.bias")
check(
    "an output with no level is refused", refused(pinsgpio.set_pin, 18, "out", "none", None) == "pins-gpio.value.output"
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
