# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A 3.5" SPI TFT and XPT2046 resistive touch on the Luckfox Lyra Zero W's SPI1.

The overlay puts SPI1 on header pins clear of SPI0 and the MeshAdv-Pi HAT: CLK 27, MOSI 29, MISO 31,
display CS 35, touch CS 37. PEN (28), D/C (18) and RST (22) are plain GPIOs for the display and touch
userspace code, or for an ads7846 node later. The overlay is an Armbian user overlay: the .dtbo in
/boot/overlay-user and its name on the user_overlays line of /boot/armbianEnv.txt. It applies at the
next restart, so status says pending until both spidev1.0 and spidev1.1 have appeared.

State in /var/lib/hub/control/lcdtouch.json, for the pin map page. Stdlib only.
"""

import json
import os
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import lcdpanel, safeio

BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
CONTROL = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "lcdtouch.json"
STATE = ETC / "lcdtouch.json"
BOOT_ID = Path(os.environ.get("HUB_BOOT_ID", "/proc/sys/kernel/random/boot_id"))
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
SPI_SYS = Path(os.environ.get("HUB_SPI_SYS", "/sys/bus/spi/devices"))
OVERLAY = "luckfox-lyra-zero-w-spi1-lcd-touch"
DTBO = BOOT / "overlay-user" / f"{OVERLAY}.dtbo"
ENV = BOOT / "armbianEnv.txt"
PINS = [
    {"signal": "SPI1 CLK", "pin": 27, "soc": "GPIO1_B1", "rmio": 24, "gpio": 41},
    {"signal": "SPI1 MOSI", "pin": 29, "soc": "GPIO1_B2", "rmio": 25, "gpio": 42},
    {"signal": "SPI1 MISO", "pin": 31, "soc": "GPIO1_B3", "rmio": 26, "gpio": 43},
    {"signal": "Display CS", "pin": 35, "soc": "GPIO0_C0", "rmio": 16, "gpio": 16},
    {"signal": "Touch TCS", "pin": 37, "soc": "GPIO1_C3", "rmio": 28, "gpio": 51},
    {"signal": "Touch PEN", "pin": 28, "soc": "GPIO1_D3", "rmio": 31, "gpio": 59},
    {"signal": "Display D/C", "pin": 18, "soc": "GPIO0_B4", "rmio": 12, "gpio": 12},
    {"signal": "Display RST", "pin": 22, "soc": "GPIO0_B3", "rmio": 11, "gpio": 11},
    {"signal": "Display BL", "pin": 36, "soc": "GPIO1_D1", "rmio": 29, "gpio": 57},
]
DTS = """/dts-v1/;
/plugin/;
/ {
\tmetadata {
\t\ttitle = "SPI1 for a 3.5in TFT + XPT2046 touch on Luckfox Lyra Zero W";
\t\tcompatible = "rockchip,rk3506b-lyra-zero";
\t\tcategory = "spi";
\t\texclusive = "spi1", "RM_IO16", "RM_IO24", "RM_IO25", "RM_IO26", "RM_IO28", "RM_IO29", "RM_IO31";
\t\tdescription = "SPI1 using RM_IO24(CLK), RM_IO25(MOSI), RM_IO26(MISO), RM_IO16(CS0/display), RM_IO28(CS1/touch); BL on header 36 (RMIO29), which the MeshAdv-Pi HAT uses for IRQ. Leaves SPI0 free.";
\t};
\tfragment@0 {
\t\ttarget = <&spi1>;
\t\t__overlay__ {
\t\t\tstatus = "okay";
\t\t\tnum-cs = <2>;
\t\t\t#address-cells = <1>;
\t\t\t#size-cells = <0>;
\t\t\tpinctrl-names = "default";
\t\t\tpinctrl-0 = <&rm_io24_spi1_clk &rm_io25_spi1_mosi &rm_io26_spi1_miso &rm_io16_spi1_csn0 &rm_io28_spi1_csn1>;
\t\t\tspi@0 {
\t\t\t\tcompatible = "rockchip,spidev";
\t\t\t\treg = <0>;
\t\t\t\tspi-max-frequency = <40000000>;
\t\t\t\tstatus = "okay";
\t\t\t};
\t\t\tspi@1 {
\t\t\t\tcompatible = "rockchip,spidev";
\t\t\t\treg = <1>;
\t\t\t\tspi-max-frequency = <2000000>;
\t\t\t\tstatus = "okay";
\t\t\t};
\t\t};
\t};
\tfragment@1 {\n\t\ttarget-path = "/";\n\t\t__overlay__ {\n\t\t\tlcd_backlight: lcd-backlight {\n\t\t\t\tcompatible = "gpio-leds";\n\t\t\t\tbacklight {\n\t\t\t\t\tlabel = "lcd-backlight";\n\t\t\t\t\tgpios = <&gpio1 25 0>;\n\t\t\t\t\tdefault-state = "off";\n\t\t\t\t};\n\t\t\t};\n\t\t};\n\t};\n};\n"""


def run(*cmd, timeout=60, inp=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=inp)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _read(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def model():
    """The board's model, from the device tree, or ""."""
    try:
        return (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace")
    except OSError:
        return ""


def supported():
    """Only the Lyra Zero W has this 40-pin header and this SPI0/SPI1 split."""
    return "lyra zero" in model().lower()


def _overlays():
    """The names on user_overlays= in armbianEnv.txt, in order."""
    for line in _read(ENV).splitlines():
        if line.startswith("user_overlays="):
            return line.split("=", 1)[1].split()
    return []


def _active():
    """Both spidev devices the overlay creates have appeared."""
    return all((SPI_SYS / name).exists() for name in ("spi1.0", "spi1.1"))


def _boot_id():
    return _read(BOOT_ID).strip()


def _state():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(data):
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(STATE, json.dumps(data, indent=1, sort_keys=True) + "\n", 0o600)


def _display_on():
    """The panel keeps its picture only until the next restart: the stored boot id says whether this boot
    initialised it."""
    state = _state()
    return state.get("display_on") is True and state.get("boot_id") == _boot_id()


def status():
    """The overlay's and the panel's state, for the pin map page."""
    enabled = OVERLAY in _overlays()
    installed = DTBO.is_file()
    on = enabled and installed
    active = on and _active()
    return {
        "supported": supported(),
        "on": on,
        "enabled": enabled,
        "installed": installed,
        "active": active,
        "pending": bool(on and not active),
        "display_on": bool(active and _display_on()),
        "overlay": OVERLAY,
        "dtbo": str(DTBO),
        "devices": ["/dev/spidev1.0", "/dev/spidev1.1"],
        "pins": PINS,
        "backlight": str(lcdpanel.BL),
        "warning": (
            "The display and touch share SPI1; BL is header 36 (the MeshAdv-Pi HAT's IRQ), so the "
            "HAT and the backlight control cannot both be fitted. Keep the panel's ribbon and "
            "backlight wires away from the LoRa antenna."
            if supported()
            else ""
        ),
    }


def write_status():
    """The hub's copy of the status, for the pin map page."""
    safeio.mkdir(CONTROL.parent, mode=0o755)
    safeio.write(CONTROL, json.dumps(status(), sort_keys=True) + "\n", 0o644)


def _boot_env(add=None, remove=None):
    """Armbian's armbianEnv.txt: user_overlays with our overlay put in or taken out."""
    if not ENV.is_file():
        return []
    lines = _read(ENV).splitlines()
    have, at = [], None
    for i, line in enumerate(lines):
        if line.startswith("user_overlays="):
            have, at = line.split("=", 1)[1].split(), i
    names = [n for n in have if n != remove] + ([add] if add and add not in have else [])
    if names != have:
        new = "user_overlays=" + " ".join(names)
        if at is None:
            lines.append(new)
        elif names:
            lines[at] = new
        else:
            del lines[at]
        safeio.write(ENV, "\n".join(lines) + "\n", 0o644)
    return names


def set_overlay(on):
    """Turn the overlay on or off in the boot files; it applies at the next restart."""
    if on and not supported():
        raise Refused(M("lcdtouch.unsupported", "This overlay is for the Luckfox Lyra Zero W only."))
    if on and not ENV.is_file():
        raise Refused(M("lcdtouch.no-boot-env", "This board has no Armbian boot environment (/boot/armbianEnv.txt)."))
    if on:
        DTBO.parent.mkdir(parents=True, exist_ok=True)
        r = run("dtc", "-@", "-q", "-I", "dts", "-O", "dtb", "-o", str(DTBO), "-", inp=DTS)
        if r.returncode:
            DTBO.unlink(missing_ok=True)
            if r.returncode == 127:
                raise Refused(M("lcdtouch.dtc-missing", "The device tree compiler (dtc) is not installed."))
            raise Refused(
                M(
                    "lcdtouch.dtc-failed",
                    "The device tree compiler (dtc) failed: {detail}",
                    detail=r.stderr.strip()[:160],
                )
            )
        _boot_env(add=OVERLAY)
        write_status()
        return M("lcdtouch.on", "Saved. Restart the box to use the SPI1 display and touch.")
    DTBO.unlink(missing_ok=True)
    if ENV.is_file():
        _boot_env(remove=OVERLAY)
    write_status()
    return M("lcdtouch.off", "Saved. The SPI1 display and touch overlay is off after a restart.")


def _require_display():
    if not supported():
        raise Refused(M("lcdtouch.unsupported", "This overlay is for the Luckfox Lyra Zero W only."))
    if not _active():
        raise Refused(M("lcdtouch.display-not-ready", "Enable the overlay and restart the box first."))


def set_display(on):
    """Initialise the panel to the test card, or blank it (DISPOFF and sleep)."""
    _require_display()
    try:
        if on:
            lcdpanel.show_card()
        else:
            lcdpanel.off()
    except Exception as exc:  # noqa: BLE001 - the panel driver says why
        raise Refused(
            M("lcdtouch.display-failed", "The display could not be changed: {detail}", detail=str(exc)[:160])
        ) from None
    _save_state({"display_on": bool(on), "boot_id": _boot_id()})
    write_status()
    if on:
        return M("lcdtouch.display-on", "The test card is on the display.")
    return M("lcdtouch.display-off", "The display is off.")


def run_screen_test(seconds=60):
    """Run the animation sequence, then blank the panel. It blocks the root helper for that time."""
    _require_display()
    try:
        lcdpanel.run_test(seconds)
    except Exception as exc:  # noqa: BLE001 - the panel driver says why
        raise Refused(
            M("lcdtouch.display-failed", "The display could not be changed: {detail}", detail=str(exc)[:160])
        ) from None
    finally:
        _save_state({"display_on": False, "boot_id": _boot_id()})
        write_status()
    return M("lcdtouch.test-done", "The {n}-second test ran. The display is off.", n=seconds)


def undo_all():
    """Uninstall: the panel blanked, and the overlay file and its user_overlays name taken away."""
    if _active():
        try:
            lcdpanel.off()
        except Exception:  # noqa: BLE001, B110 - the overlay is removed either way # nosec B110
            pass
    if _overlays() or DTBO.exists():
        set_overlay(False)


def main(argv):
    if argv == ["status"]:
        print(json.dumps(status(), indent=1))
        return 0
    if argv == ["on"]:
        print(set_overlay(True))
        return 0
    if argv == ["off"]:
        print(set_overlay(False))
        return 0
    if argv == ["display-on"]:
        print(set_display(True))
        return 0
    if argv == ["display-off"]:
        print(set_display(False))
        return 0
    if argv == ["test"]:
        print(run_screen_test(60))
        return 0
    if argv == ["undo-all"]:
        undo_all()
        return 0
    print(
        "Usage: irate-box lcdtouch status | on | off | display-on | display-off | test | undo-all",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
