# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One header pin, read once or set once, through libgpiod.

A poll is a request, not a boot-file change: the line is opened for one request, read or set, and
released. Only a free GPIO in the board's header map is accepted, never a chip and line from the page;
a pin another function claims, or an irate-box pin hog holds, is refused. Stdlib plus gpiod.
"""

import os
from pathlib import Path

from irate_box.msg import M, Refused

CONTROL = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control"
MODES = ("in", "out")
BIASES = ("none", "up", "down")


def _row(pin):

    from irate_box.root import pinmap

    snapshot = pinmap.snapshot()
    for row in snapshot.get("header") or []:
        if isinstance(row, dict) and row.get("pin") == pin:
            return row
    return None


def _gpiod():
    try:
        import gpiod
    except ImportError:
        raise Refused(
            M("pins-gpio.no-gpiod", "The GPIO library (gpiod) is not installed. It comes with the gpiod package.")
        ) from None
    return gpiod


def set_pin(pin, mode, bias="none", value=None):
    """Read a free pin once, or set it once, then release it."""
    row = _row(pin)
    if row is None:
        raise Refused(M("pins-gpio.unknown-pin", "Pin {pin} is not in this board's header map.", pin=pin))
    if row.get("type") != "gpio":
        raise Refused(M("pins-gpio.not-gpio", "Pin {pin} is power or ground, not a GPIO.", pin=pin))
    from irate_box.root import hardware

    assigned = hardware.assignment_for(pin, include_pinhog=False)
    if assigned:
        raise Refused(
            M(
                "pins-gpio.assigned",
                "Pin {pin} is assigned to {role}.",
                pin=pin,
                role=assigned.get("role") or assigned.get("owner") or "another owner",
            )
        )
    consumer = str(row.get("consumer") or "")
    if row.get("used"):
        if consumer.startswith("irate-box-pin"):
            raise Refused(
                M(
                    "pins-gpio.hogged",
                    "Pin {pin} is held by a pin hog. Remove the hog before reading it.",
                    pin=pin,
                )
            )
        raise Refused(
            M(
                "pins-gpio.used",
                "Pin {pin} is already used by {consumer}.",
                pin=pin,
                consumer=consumer or "a function",
            )
        )
    if mode not in MODES:
        raise Refused(M("pins-gpio.mode", "A pin is read or set, in or out."))
    if bias not in BIASES:
        raise Refused(M("pins-gpio.bias", "The pull is none, up or down."))
    if mode == "in" and value is not None:
        raise Refused(M("pins-gpio.value.input", "An input is only read."))
    if mode == "out" and value not in (0, 1):
        raise Refused(M("pins-gpio.value.output", "An output is either 0 or 1."))
    gpiod = _gpiod()
    line = int(row["line"])
    device = f"/dev/{row['chip']}"
    try:
        if mode == "in":
            settings = gpiod.LineSettings(direction=gpiod.line.Direction.INPUT)
            if bias in ("up", "down"):
                settings = gpiod.LineSettings(
                    direction=gpiod.line.Direction.INPUT,
                    bias=gpiod.line.Bias.PULL_UP if bias == "up" else gpiod.line.Bias.PULL_DOWN,
                )
            request = gpiod.request_lines(device, consumer="irate-box-poll", config={line: settings})
            try:
                read = request.get_value(line)
            finally:
                request.release()
            high = read == gpiod.line.Value.ACTIVE
            return (
                M("pins-gpio.in.high", "Pin {pin} reads high.", pin=pin)
                if high
                else M("pins-gpio.in.low", "Pin {pin} reads low.", pin=pin)
            )
        settings = gpiod.LineSettings(
            direction=gpiod.line.Direction.OUTPUT,
            output_value=gpiod.line.Value.ACTIVE if value else gpiod.line.Value.INACTIVE,
        )
        request = gpiod.request_lines(device, consumer="irate-box-poll", config={line: settings})
        request.release()
    except OSError as exc:
        raise Refused(M("pins-gpio.failed", "The pin could not be opened: {detail}", detail=str(exc)[:160])) from None
    return M(
        "pins-gpio.out",
        "Pin {pin} was set {level} and released.",
        pin=pin,
        level="high" if value else "low",
    )
