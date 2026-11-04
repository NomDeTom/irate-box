# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Pin hogs: header pins held by the kernel from boot, without a process of their own.

A pin hog is a gpio-hog node in an Armbian user overlay, so it lasts across reboots and needs nothing
running to keep it. The hogs are kept in /etc/hub/pin-hogs.json; the overlay and its user_overlays name
are written by hardware.overlays. A change is pending until the next restart, like every user overlay.

  pinhog.py status
  pinhog.py add PIN in|out [none|up|down] [0|1]     one pin (input, or output high/low)
  pinhog.py remove PIN
  pinhog.py assignments                            for hardware.json

Only a pin in this board's header map is accepted, never a raw chip and line; power, ground, unknown
pins and pins another function claims are refused. irate-box's own hog may be changed or removed.
Stdlib only.
"""

import json
import os
import subprocess  # Fixed argv, trusted executable, no shell. # nosec B404
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = ETC / "pin-hogs.json"
BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
DTBO = BOOT / "overlay-user" / "irate-box-pin-hogs.dtbo"
OVERLAY = "irate-box-pin-hogs"
BOOT_ID = Path(os.environ.get("HUB_BOOT_ID", "/proc/sys/kernel/random/boot_id"))
CONTROL = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control"
GPIO_SYS = Path(os.environ.get("HUB_GPIO_SYS", "/sys/bus/gpio/devices"))
DT_BASE = Path(os.environ.get("HUB_DT_BASE", "/sys/firmware/devicetree/base"))
MODES = ("in", "out")
BIASES = ("none", "up", "down")


def _read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def boot_id():
    return _read(BOOT_ID).strip()


def _entries():
    """Every saved pin, including a removal waiting for the next boot.

    A removal is dropped once the box has booted without it. Each spec keeps the boot id it was
    written in, so a hog that held from an earlier boot is not called pending by a later change.
    """
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}
    hogs = data.get("hogs") if isinstance(data, dict) else None
    if not isinstance(hogs, dict):
        return {}
    out = {}
    for pin, spec in hogs.items():
        if str(pin).isdigit() and isinstance(spec, dict):
            if spec.get("removed") and spec.get("boot_id") != boot_id():
                continue
            out[int(pin)] = spec
    return out


def load():
    """The hogs that are still wanted: {pin: {"mode", "bias", "value", "boot_id"}}."""
    return {pin: spec for pin, spec in _entries().items() if not spec.get("removed")}


def _state_boot():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return ""
    return data.get("boot_id") if isinstance(data, dict) else ""


def _save(entries):
    safeio.mkdir(ETC, mode=0o755)
    if not entries:
        STATE.unlink(missing_ok=True)
        return
    safeio.write(
        STATE,
        json.dumps({"boot_id": boot_id(), "hogs": {str(pin): spec for pin, spec in sorted(entries.items())}}, indent=1)
        + "\n",
        0o600,
    )


def _held(pin):
    row = _row(pin) or {}
    return bool(row.get("used")) and str(row.get("consumer") or "") == f"irate-box-pin{pin}"


def _pending(pin, spec):
    """A hog changed this boot, or a removal this boot, waits for the restart."""
    return spec.get("boot_id") == boot_id()


def pending(pin=None):
    """Any (or one pin's) change is waiting for the next boot."""
    entries = _entries()
    if pin is not None:
        return bool(pin in entries and _pending(pin, entries[pin]))
    return any(_pending(pin, spec) for pin, spec in entries.items())


def _row(pin):
    try:
        data = json.loads((CONTROL / "pins.json").read_text())
    except (OSError, ValueError):
        data = {}
    for row in data.get("header") or []:
        if isinstance(row, dict) and row.get("pin") == pin:
            return row
    return None


def _claimed_row(pin):
    from irate_box.root import pinmap

    snapshot = pinmap.snapshot()
    for row in snapshot.get("header") or []:
        if row.get("pin") == pin:
            return row
    return None


def _validate(pin):
    row = _claimed_row(pin)
    if row is None:
        raise Refused(M("pinhog.unknown-pin", "Pin {pin} is not in this board's header map.", pin=pin))
    if row.get("type") != "gpio":
        raise Refused(M("pinhog.not-gpio", "Pin {pin} is power or ground, not a GPIO.", pin=pin))
    from irate_box.root import hardware

    assigned = hardware.assignment_for(pin, include_pinhog=False)
    if assigned:
        raise Refused(
            M(
                "pinhog.assigned",
                "Pin {pin} is assigned to {role}.",
                pin=pin,
                role=assigned.get("role") or assigned.get("owner") or "another owner",
            )
        )
    consumer = str(row.get("consumer") or "")
    if row.get("used") and consumer != f"irate-box-pin{pin}":
        raise Refused(
            M(
                "pinhog.claimed",
                "Pin {pin} is already used by {consumer}.",
                pin=pin,
                consumer=consumer or "a function",
            )
        )
    return row


def _controller_path(chip):
    """The device tree path of the controller a gpiochip belongs to."""
    try:
        node = (GPIO_SYS / chip / "device/of_node").resolve(strict=True)
        base = DT_BASE.resolve(strict=True)
        relative = node.relative_to(base)
    except (OSError, ValueError):
        raise Refused(
            M(
                "pinhog.no-controller",
                "The device tree path of {chip} could not be read.",
                chip=chip,
            )
        ) from None
    return "/" + "/".join(relative.parts)


def dts(hogs):
    """The overlay's source: one fragment per controller, one gpio-hog node per pin."""
    groups = {}
    for pin, spec in sorted(hogs.items()):
        row = _row(pin) or _claimed_row(pin)
        if row is None:
            raise Refused(M("pinhog.unknown-pin", "Pin {pin} is not in this board's header map.", pin=pin))
        groups.setdefault(_controller_path(row["chip"]), []).append((pin, row["line"], spec))
    fragments = []
    for index, (target, entries) in enumerate(sorted(groups.items())):
        nodes = []
        for pin, line, spec in entries:
            if spec.get("mode") == "output":
                level = "output-high" if spec.get("value") else "output-low"
            else:
                level = "input"
            # The kernel's hog parser reads the pull from the flags cell of gpios, not a pinconf
            # property: 0x10 is GPIO_PULL_UP, 0x20 GPIO_PULL_DOWN.
            flags = {"up": 0x10, "down": 0x20}.get(spec.get("bias") or "none", 0)
            nodes.append(
                "\t\t\tirate-box-hog@{pin} {{\n"
                "\t\t\t\tgpio-hog;\n"
                "\t\t\t\tgpios = <{line} {flags}>;\n"
                '\t\t\t\tline-name = "irate-box-pin{pin}";\n'
                "\t\t\t\t{level};\n"
                "\t\t\t}};".format(
                    pin=pin,
                    line=line,
                    flags=flags,
                    level=level,
                )
            )
        fragments.append(
            '\tfragment@{index} {{\n\t\ttarget-path = "{target}";\n\t\t__overlay__ {{\n{nodes}\n\t\t}};\n\t}};'.format(
                index=index, target=target, nodes="\n".join(nodes)
            )
        )
    return "/dts-v1/;\n/plugin/;\n/ {\n" + "\n".join(fragments) + "\n};\n"


def _dtc(argv, inp=None):
    return subprocess.run(argv, capture_output=True, text=True, input=inp)  # noqa: TID251


def _write(entries):
    from irate_box.root import hardware

    hogs = {pin: spec for pin, spec in entries.items() if not spec.get("removed")}
    try:
        hardware.write_dtbo(OVERLAY, dts(hogs), run=_dtc, boot=BOOT)
        if hogs:
            hardware.set_overlay_names(add=OVERLAY, boot=BOOT)
        else:
            hardware.remove_overlay(OVERLAY, boot=BOOT)
    except hardware.OverlayError as exc:
        if exc.kind == "dtc-missing":
            raise Refused(M("pinhog.dtc-missing", "The device tree compiler (dtc) is not installed.")) from None
        raise Refused(
            M("pinhog.dtc-failed", "The device tree compiler (dtc) failed: {detail}", detail=exc.detail)
        ) from None
    _save(entries)


def add(pin, mode, bias="none", value=None):
    """Add or change one pin's hog, and write the overlay; it applies at the next restart."""
    _validate(pin)
    if mode not in MODES:
        raise Refused(M("pinhog.mode", "A pin hog is either in or out."))
    if bias not in BIASES:
        raise Refused(M("pinhog.bias", "The pull is none, up or down."))
    if mode == "in":
        value = None
    elif value not in (0, 1):
        raise Refused(M("pinhog.value", "An output is either 0 or 1."))
    entries = _entries()
    entries[pin] = {
        "mode": "input" if mode == "in" else "output",
        "bias": bias,
        "value": value,
        "boot_id": boot_id(),
    }
    _write(entries)
    level = "high" if value else "low"
    if mode == "in":
        said = M("pinhog.added.in", "Pin {pin} is held as an input. Restart the box to apply it.", pin=pin)
    else:
        said = M(
            "pinhog.added.out",
            "Pin {pin} is held as an output, {level}. Restart the box to apply it.",
            pin=pin,
            level=level,
        )
    return said


def remove(pin):
    """Take one pin's hog away; a line it still holds waits for the restart to be released."""
    entries = _entries()
    spec = entries.get(pin)
    if not spec or spec.get("removed"):
        raise Refused(M("pinhog.not-set", "Pin {pin} has no pin hog.", pin=pin))
    held = _held(pin)
    if held:
        entries[pin] = {**spec, "removed": True, "boot_id": boot_id()}
    else:
        del entries[pin]
    _write(entries)
    wanted = [p for p, item in entries.items() if not item.get("removed")]
    if wanted:
        return M("pinhog.removed.more", "Pin {pin} is no longer held. Restart the box to apply it.", pin=pin)
    if held:
        return M(
            "pinhog.removed.pending",
            "Pin {pin} is released after the next restart. The pin-hog overlay is gone then.",
            pin=pin,
        )
    return M("pinhog.removed.last", "Pin {pin} is no longer held. The pin-hog overlay is gone.", pin=pin)


def assignments():
    """For hardware.json: each saved hog, per pin, held now or waiting for the next boot."""
    out = []
    for pin, spec in sorted(_entries().items()):
        row = _row(pin) or {}
        consumer = str(row.get("consumer") or "")
        ours = _held(pin)
        if row.get("used") and not ours:
            state = "conflict"
        elif ours:
            state = "holding"
        else:
            state = "idle"
        removed = bool(spec.get("removed"))
        out.append(
            {
                "pin": pin,
                "role": "Pin hog",
                "by": "pinhog",
                "owner": "pinhog",
                "state": state,
                "source": "pinhog",
                "active_owner": consumer,
                "holding": state == "holding" and not removed,
                "pending": _pending(pin, spec),
                "removed": removed,
                "mode": spec.get("mode"),
                "bias": spec.get("bias"),
                "value": spec.get("value"),
            }
        )
    return out


def status():
    return {"pending": pending(), "hogs": {str(pin): spec for pin, spec in sorted(_entries().items())}}


def main(argv):
    if argv == ["status"]:
        print(json.dumps(status(), indent=1))
        return 0
    if len(argv) >= 4 and argv[0] == "add":
        value = int(argv[4]) if len(argv) > 4 else None
        print(add(int(argv[1]), argv[2], argv[3], value))
        return 0
    if len(argv) == 2 and argv[0] == "remove":
        print(remove(int(argv[1])))
        return 0
    if argv == ["assignments"]:
        print(json.dumps(assignments(), indent=1))
        return 0
    print("Usage: pinhog status | add PIN in|out [none|up|down] [0|1] | remove PIN | assignments", file=os.sys.stderr)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
