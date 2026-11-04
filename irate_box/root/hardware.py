# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""This box's hardware state, gathered from the modules that already own each part.

The board and its header, the GPIO lines, the boot overlays, the SPI display, the USB gadget, the
status light, the radios, the clock and its module, this box's own serial pins and the watchdog are
each read from their module; this file only merges them into one document, /var/lib/hub/control/
hardware.json, for the admin pages. It is status, not a service: the root helper publishes it when it
starts, after a hardware action, and when a page asks, and the pages read the published file.

Nothing here touches the hardware itself. The one write is the status file. Stdlib only.
"""

import json
import os
import re
import time
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
STATUS = CONTROL / "hardware.json"
BOOT_ID = Path(os.environ.get("HUB_BOOT_ID", "/proc/sys/kernel/random/boot_id"))


def _module(name):
    """One of the hardware modules, imported on use: each imports only what it needs, and some import this
    file's overlay writer, so a top-level import here would go round in a circle."""
    module = __import__(f"irate_box.root.{name}", fromlist=[name])
    return module


def _read(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def _json(path, default):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def boot_id():
    return _read(BOOT_ID).strip()


def board():
    """The board's model, the kind of header it has, and whether irate-box knows that header."""
    from irate_box.root import hardening, pinmap

    model = pinmap.model()
    kind = pinmap.board_kind(hardening.board(), model)
    return {"model": model, "kind": kind, "supported": kind in ("pi", "lyra", "bpi")}


def pins():
    """The last pin map read, from control/pins.json: gpioinfo and pinmux-pins stay on that read, not here."""
    data = _json(CONTROL / "pins.json", {})
    if not data:
        return {"header": [], "lines": [], "note": ""}
    return data


def _optional(name):
    """A module this layer uses when it is there (pinhog, before its own stage), or None."""
    try:
        return _module(name)
    except ImportError:
        return None


def _assignment(pin, role, by, source, row, active=False, tokens=()):
    """One intended pin: idle when nothing claims it, active when the claim is ours, conflict when not."""
    state = "idle"
    active_owner = ""
    if row and row.get("used"):
        consumer = str(row.get("consumer") or row.get("label") or "").lower()
        function = " ".join(str(row.get(key) or "") for key in ("function", "group", "name")).lower()
        active_owner = consumer or str(row.get("function") or row.get("name") or "")
        if "irate-box" in consumer or any(token in consumer for token in tokens):
            state = "active"
        elif not consumer and active and any(token in function for token in tokens):
            state = "active"
        else:
            state = "conflict"
    return {
        "pin": pin,
        "role": role,
        "by": by,
        "owner": by,
        "state": state,
        "source": source,
        "active_owner": active_owner,
        "holding": state == "active" and "irate-box" in active_owner.lower(),
    }


def assignments():
    """The header pins something means to use, separate from what claims them now.

    The display's overlay names its SPI and touch pins and its plain D/C, RST, PEN and backlight lines;
    those stay assigned while nothing requests them, so a released D/C is not drawn as free. The serial
    pins are assigned while own serial is on. pinhog adds its own once that stage is installed.
    """
    header = {
        row.get("pin"): row
        for row in (pins().get("header") or [])
        if isinstance(row, dict) and isinstance(row.get("pin"), int)
    }
    lcd = _module("lcdtouch")
    lcd_state = lcd.status()
    out = []
    if lcd_state.get("supported"):
        out = [
            _assignment(
                item.get("pin"),
                item.get("signal") or item.get("role") or "display",
                "display",
                "lcdtouch",
                header.get(item.get("pin")),
                active=bool(lcd_state.get("active")),
                tokens=("spidev", "ili9488", "ads7846", "xpt2046", "spi1"),
            )
            for item in (getattr(lcd, "PINS", []) or [])
            if isinstance(item, dict) and isinstance(item.get("pin"), int)
        ]
    serial = _module("ownserial").status()
    if serial.get("on"):
        for pin, role in ((8, "UART TX"), (10, "UART RX")):
            out.append(
                _assignment(
                    pin,
                    role,
                    "serial",
                    "ownserial",
                    header.get(pin),
                    active=not serial.get("pending"),
                    tokens=("serial", "uart", "tty"),
                )
            )
    pinhog = _optional("pinhog")
    if pinhog is not None and hasattr(pinhog, "assignments"):
        out.extend(a for a in pinhog.assignments() if isinstance(a, dict))
    return out


def overlays():
    """The names in user_overlays= and the three overlays irate-box can put there."""
    names = overlay_names()
    lcd = _module("lcdtouch").status()
    serial = _module("ownserial").status()
    cw = _module("crashwatch").status()
    watchdog_pending = bool(cw.get("watchdog_pending"))
    watchdog_device = cw.get("watchdog_device")
    return {
        "user_overlays": names,
        "items": {
            "serial": {
                "supported": bool(serial.get("supported")),
                "enabled": bool(serial.get("on")),
                "pending": bool(serial.get("pending")),
                "device": serial.get("device") or "",
            },
            "watchdog": {
                "supported": bool(watchdog_device or watchdog_pending),
                "enabled": bool((cw.get("settings") or {}).get("watchdog") or watchdog_pending),
                "active": bool(watchdog_device and not watchdog_pending),
                "pending": watchdog_pending,
            },
            "lcd": dict(lcd),
        },
    }


def display():
    """The SPI1 panel: its overlay's state and whether the test card is on it."""
    st = _module("lcdtouch").status()
    supported = bool(st.get("supported"))
    panel = _module("lcdpanel")
    return {
        "supported": supported,
        "on": bool(st.get("display_on")) if supported else False,
        "pending": bool(st.get("pending")) if supported else False,
        "pixels": [panel.W, panel.H] if supported else [],
        "driver": getattr(panel, "DRIVER", "ili9488") if supported else "",
    }


def usb():
    """The USB gadget: on, up, and the drive made from the file drop."""
    mod = _module("usbgadget")
    st = mod.status()
    settings = st.get("settings") or {}
    drive = st.get("drive") or {}
    return {
        "supported": bool(st.get("peripheral")),
        "on": bool(settings.get("on")),
        "up": bool(st.get("up")),
        "drive": bool(drive.get("on")),
        "drive_write": bool(settings.get("drive_write")),
        "pending": bool(st.get("pending")),
        "address": mod.ADDRESS,
    }


def led():
    """The status light: which one, and what it is showing."""
    st = _module("led").status()
    name = st.get("led") or ""
    return {
        "supported": bool(st.get("found")),
        "on": bool(name) and not bool(st.get("off")),
        "show": st.get("show") or "",
        "led": name,
    }


def radios():
    """The radios' switches, as rfkill keeps them."""
    settings = _module("rfkill").status().get("settings") or {}
    return {"wifi": settings.get("wifi", ""), "bluetooth": settings.get("bluetooth", "")}


def clock():
    """The time source: the image's own daemon, chrony, or the clock module."""
    st = _module("clock").status()
    source = st.get("daemon") or st.get("service") or "image"
    servers = st.get("pool") or st.get("local") or ""
    return {"source": source, "servers": servers}


def rtc():
    """The I2C clock module, when one is set up."""
    st = _module("rtc").status()
    return {"found": bool(st)}


def serial():
    """This box's own header pins as a serial console."""
    st = _module("ownserial").status()
    return {
        "supported": bool(st.get("supported")),
        "on": bool(st.get("on")),
        "pending": bool(st.get("pending")),
        "device": st.get("device") or "",
    }


def watchdog():
    """The watchdog: the board's own, softdog, or none."""
    cw = _module("crashwatch").status()
    pending = bool(cw.get("watchdog_pending"))
    device = cw.get("watchdog_device")
    return {
        "supported": bool(device or pending),
        "mode": device or ("pending" if pending else "off"),
        "enabled": bool((cw.get("settings") or {}).get("watchdog") or pending),
        "pending": pending,
    }


SECTIONS = (
    ("board", board),
    ("pins", pins),
    ("assignments", assignments),
    ("overlays", overlays),
    ("display", display),
    ("usb_gadget", usb),
    ("led", led),
    ("radios", radios),
    ("clock", clock),
    ("rtc", rtc),
    ("serial", serial),
    ("watchdog", watchdog),
)


def status():
    """Every section, merged, with a section that failed named in errors rather than taking the rest down."""
    out = {"at": time.time(), "boot_id": boot_id(), "errors": []}
    for name, reader in SECTIONS:
        try:
            out[name] = reader()
        except Exception as exc:  # noqa: BLE001 - one unreadable part must not hide the others
            out[name] = {"supported": False, "error": f"{type(exc).__name__}: {exc}"}
            out["errors"].append({"section": name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def write_status(data=None):
    """Publish the hardware status for the pages. Returns the document that was written."""
    data = status() if data is None else data
    safeio.mkdir(CONTROL, mode=0o755)
    safeio.write(STATUS, json.dumps(data, indent=1, sort_keys=True) + "\n", 0o644)
    return data


def publish():
    """The root helper's hw-status: read every part again and publish it."""
    data = write_status()
    if data.get("errors"):
        return M(
            "control.hardware.published.some",
            "The hardware status is up to date, except for {n} part(s) that could not be read.",
            n=len(data["errors"]),
        )
    return M("control.hardware.published", "The hardware status is up to date.")


# --- the one action gate ------------------------------------------------------------------------
# Every admin choice about a hardware part goes through here: the doctor's settings groups and the
# hardware page both end at set(), which checks the shared pattern and then calls the module that owns
# the part. The dedicated pins actions stay as their own root actions (they carry a bool or a pin), and
# the Box doctor's health-fix keeps working as an alias.

_HARDWARE_KINDS = frozenset(
    {
        "rfkill",
        "rfkill-apply",
        "usb-gadget",
        "usb-gadget-drive",
        "usb-gadget-drive-write",
        "clock-sources",
        "clock-undo",
        "clock-set",
        "rtc-find",
        "rtc-look",
        "rtc-save",
        "rtc-remove",
    }
)


def handles(choice):
    """Whether this choice concerns a hardware part this layer owns."""
    if not isinstance(choice, str):
        return False
    kind = choice.partition(":")[0]
    return kind in _HARDWARE_KINDS or kind.startswith(("crashwatch-", "led-", "rtc-setup"))


def set(choice):
    """One hardware choice, checked against the shared pattern, carried out by the module that owns it."""
    from irate_box import choices

    if not isinstance(choice, str) or not choices.CHOICES_RE.fullmatch(choice):
        raise Refused(
            M(
                "hardware.set.unknown",
                "The hardware layer does not know the setting {choice}.",
                choice=str(choice)[:80],
            )
        )
    if not handles(choice):
        raise Refused(
            M(
                "hardware.set.not-hardware",
                "{choice} is not a hardware setting. Use the page that offers it.",
                choice=choice[:80],
            )
        )
    kind, _, arg = choice.partition(":")
    if kind == "clock-sources":
        return _module("clock").apply(choice)
    if kind == "clock-undo":
        return _module("clock").undo()
    if kind == "clock-set":
        return _module("health").set_clock(int(arg))
    if kind.startswith("crashwatch-"):
        from irate_box.root import recommended

        said = _module("crashwatch").set_option(kind[len("crashwatch-") :], arg)
        recommended.record(kind, arg)  # the owner's own choice: never offered back
        return said
    if kind.startswith("led-"):
        led_module = _module("led")
        return led_module.flash(arg) if kind == "led-flash" else led_module.set_option(kind[len("led-") :], arg)
    if kind == "rfkill":
        radio, _, value = arg.partition(":")
        return _module("rfkill").set_option(radio, value)
    if kind == "rfkill-apply":
        rfkill_module = _module("rfkill")
        changed = rfkill_module.apply()
        failed = [switch["name"] for switch, _, error in changed if error]
        if failed:
            return M("repair.rfkill.failed", "These switches could not be changed: {names}.", names=", ".join(failed))
        return M("repair.rfkill.done", "Every radio switch is as set.")
    if kind == "usb-gadget":
        return _module("usbgadget").set_option(arg)
    if kind == "usb-gadget-drive":
        return _module("usbgadget").set_drive(arg)
    if kind == "usb-gadget-drive-write":
        return _module("usbgadget").set_drive_write(arg)
    rtc_module = _module("rtc")
    if kind == "rtc-find":
        found = rtc_module.find()
        if found["found"]:
            return M(
                "repair.rtc.found",
                "The search found {modules}.",
                modules=[
                    M(
                        "repair.rtc.found-one",
                        "the {chip} on bus {bus} at {addr}",
                        chip=x["label"],
                        bus=x["bus"],
                        addr=x["addr"],
                    )
                    for x in found["found"]
                ],
            )
        return (
            M("repair.rtc.none", "The search found no clock module.")
            if found["buses"]
            else M("repair.rtc.no-bus", "The search found no clock module. No I2C bus is enabled.")
        )
    try:
        if kind == "rtc-look":
            return rtc_module.look(asked=True)
        if kind == "rtc-setup":
            chip, bus, addr = arg.split(":")
            return rtc_module.setup(chip, int(bus), addr)
        if kind == "rtc-save":
            return rtc_module.save()
        if kind == "rtc-remove":
            return rtc_module.remove()
    except rtc_module.RtcError as exc:
        raise Refused(exc.message) from exc
    raise Refused(
        M(
            "hardware.set.unknown",
            "The hardware layer does not know the setting {choice}.",
            choice=str(choice)[:80],
        )
    )


# --- the one overlay writer ---------------------------------------------------------------------
# armbianEnv.txt's user_overlays= and the .dtbo files: ownserial, crashwatch and lcdtouch all use these,
# so an overlay is added or taken away one way, and a failure leaves no half-written file behind.

BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
ENV = BOOT / "armbianEnv.txt"
OVERLAY_DIR = BOOT / "overlay-user"
OVERLAY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class OverlayError(Exception):
    """The device tree compiler could not write an overlay, or there is no boot environment to add it to."""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


def _dtc(argv, inp=None):
    import subprocess  # Fixed argv, trusted executable, no shell. # nosec B404

    return subprocess.run(argv, capture_output=True, text=True, input=inp)  # noqa: TID251


def _env_path(boot=None):
    return Path(boot) / "armbianEnv.txt" if boot else ENV


def _overlay_dir(boot=None):
    return Path(boot) / "overlay-user" if boot else OVERLAY_DIR


def overlay_names(boot=None, path=None):
    """The names on user_overlays=, in order; the last such line wins, as the boot loader reads it."""
    text = _read(Path(path) if path else _env_path(boot))
    for line in reversed(text.splitlines()):
        if line.startswith("user_overlays="):
            return line.split("=", 1)[1].split()
    return []


def set_overlay_names(add=None, remove=None, boot=None, path=None):
    """user_overlays= with `add` put in (once) or `remove` taken out (every time). Returns the names now."""
    env = Path(path) if path else _env_path(boot)
    try:
        lines = env.read_text().splitlines()
    except OSError:
        if add:
            raise OverlayError("no-boot-env", str(env)) from None
        return []
    have, at = [], None
    for i, line in enumerate(lines):
        if line.startswith("user_overlays="):
            have, at = line.split("=", 1)[1].split(), i
    names = [name for name in have if name != remove]
    if add and add not in names:
        names.append(add)
    if names != have:
        new = "user_overlays=" + " ".join(names)
        if at is None:
            lines.append(new)
        elif names:
            lines[at] = new
        else:
            del lines[at]
        safeio.write(env, "\n".join(lines) + "\n", 0o644)
    return names


def write_dtbo(name, dts, run=None, boot=None):
    """Compile one overlay beside its final name, then replace it: a failure keeps the old .dtbo."""
    if not OVERLAY_RE.match(name or ""):
        raise OverlayError("bad-name", str(name))
    path = _overlay_dir(boot) / f"{name}.dtbo"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.unlink(missing_ok=True)
    argv = ["dtc", "-@", "-q", "-I", "dts", "-O", "dtb", "-o", str(tmp), "-"]
    said = (run or _dtc)(argv, dts)
    if said.returncode:
        tmp.unlink(missing_ok=True)
        if said.returncode == 127:
            raise OverlayError("dtc-missing", said.stderr.strip()[:160])
        raise OverlayError("dtc-failed", said.stderr.strip()[:160])
    os.replace(tmp, path)
    return path


def remove_dtbo(name, boot=None):
    """Take one overlay file away, if it is there."""
    path = _overlay_dir(boot) / f"{name}.dtbo"
    path.unlink(missing_ok=True)
    return path


def add_overlay(name, dts=None, run=None, boot=None):
    """Write the .dtbo when there is one, then add the name to user_overlays=."""
    if dts is not None:
        write_dtbo(name, dts, run=run, boot=boot)
    return set_overlay_names(add=name, boot=boot)


def remove_overlay(name, boot=None):
    """Take the .dtbo away and the name off user_overlays=."""
    remove_dtbo(name, boot=boot)
    if _env_path(boot).is_file():
        return set_overlay_names(remove=name, boot=boot)
    return overlay_names(boot=boot)
