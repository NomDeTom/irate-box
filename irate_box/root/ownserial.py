# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""This box's own header pins as a serial console for a new board (the Rockchip page's serial route).

A USB serial adapter works already. This turns the box's own UART out onto the Pi's GPIO14/15 (header pins 8 and
10), or onto the Lyra Zero W's RMIO 22/23 (the same pins), so the box needs no adapter. It changes the box's boot
files and needs a restart: on a Pi 3B+ the PL011 is taken from Bluetooth (`enable_uart=1`, `dtoverlay=miniuart-bt`,
Bluetooth moved to the smaller UART, and `console=serial0` taken off the kernel command line so the pins are free);
on a Lyra an Armbian user overlay enables UART1 on RMIO 22 (TX, pin 8) and RMIO 23 (RX, pin 10). UART0 has no RMIO
pins on the RK3506 and UART2 is the box's own console, so UART1 is the free one.

  sudo irate-box ownserial status
  sudo irate-box ownserial on|off
  sudo irate-box ownserial undo-all          for uninstall.sh

State in /etc/hub/ownserial.json: what was added, so off and uninstall take only irate-box's changes away. Stdlib.
"""

import json
import os
import re
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import hardening, safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = ETC / "ownserial.json"
# The hub's copy of the status (it cannot read /etc/hub or this module): what the Rockchip page offers.
CONTROL = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "ownserial.json"
BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
DEVICE = {"pi": "/dev/ttyAMA0", "lyra": "/dev/ttyS1"}
OVERLAY = "irate-box-serial"
MARKER = "# irate-box: the header pins as a serial console (GPIO14 TX, GPIO15 RX)"
PI_LINES = ("enable_uart=1", "dtoverlay=miniuart-bt")
PI_CONSOLE = "console=serial0,115200"
WARNING = {
    "pi": "Bluetooth moves to the smaller UART: slower, and it can drop connections. The pins are not for 1 500 000 baud.",
    "lyra": "UART1 comes out on header pins 8 (TX) and 10 (RX); UART2 stays the box's own console.",
}
LYRA_DTS = """/dts-v1/;
/plugin/;
/ {
\tmetadata {
\t\ttitle = "irate-box: UART1 on header pins 8 and 10";
\t\tcompatible = "rockchip,rk3506b-lyra-zero";
\t\tcategory = "uart";
\t\texclusive = "uart1", "RM_IO22", "RM_IO23";
\t\tdescription = "Enable UART1 on RMIO22 (TX, pin 8) and RMIO23 (RX, pin 10).";
\t};
\tfragment@0 {
\t\ttarget = <&uart1>;
\t\t__overlay__ {
\t\t\tstatus = "okay";
\t\t\tpinctrl-names = "default";
\t\t\tpinctrl-0 = <&rm_io22_uart1_tx &rm_io23_uart1_rx>;
\t\t};
\t};
};\n"""


def run(*cmd, timeout=120, inp=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=inp)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _read(path):
    try:
        return path.read_text()
    except OSError:
        return ""


def _state():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data):
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(STATE, json.dumps(data, indent=1, sort_keys=True) + "\n", 0o644)
    safeio.mkdir(CONTROL.parent, mode=0o755)
    safeio.write(CONTROL, json.dumps(status(), sort_keys=True) + "\n", 0o644)


def device(board=None):
    """The device this box's own pins appear as, or None where the board has no such route."""
    board = board or hardening.board()
    return DEVICE.get(board)


def status():
    """{board, supported, on, pending, device, warning}: on only with its boot files in place; pending until the
    next restart makes the device appear."""
    board = hardening.board()
    st = _state()
    on = st.get("on") is True and st.get("board") == board
    dev = device(board)
    return {
        "board": board,
        "supported": board in DEVICE,
        "on": on,
        "pending": bool(on and dev and not Path(dev).exists()),
        "device": dev if on else "",
        "warning": WARNING.get(board, ""),
    }


def _pi_boot():
    return BOOT / "firmware" if (BOOT / "firmware" / "config.txt").is_file() else BOOT


def _pi_config(on, added):
    """The Pi's config.txt: our marker and the two lines at the end, under [all] (so a [pi4] or [cm4] last section does
    not swallow them), or the exact block we put in taken out. Returns the block added."""
    path = _pi_boot() / "config.txt"
    lines = _read(path).splitlines()
    if on:
        last = next((line.strip() for line in reversed(lines) if re.fullmatch(r"\[[^\]]+\]", line.strip())), "")
        block = ([] if last == "[all]" else ["[all]"]) + [MARKER] + list(PI_LINES)
        safeio.write(path, "\n".join(lines + block) + "\n", 0o644)
        return block
    block = list(added or [])
    if block:
        for i in range(len(lines) - len(block), -1, -1):
            if lines[i : i + len(block)] == block:
                del lines[i : i + len(block)]
                break
        safeio.write(path, "\n".join(lines) + "\n", 0o644)
    return []


def _pi_cmdline(on, removed):
    """The kernel command line: `console=serial0` off while the pins are the board's, put back as it was after."""
    path = _pi_boot() / "cmdline.txt"
    words = _read(path).split()
    if on:
        token = next((w for w in words if w == PI_CONSOLE or w.startswith("console=serial0")), None)
        if token:
            words = [w for w in words if w != token]
            safeio.write(path, " ".join(words) + "\n", 0o644)
            return token
        return removed
    if removed and removed not in words:
        words.insert(0, removed)
        safeio.write(path, " ".join(words) + "\n", 0o644)
    return None


def _boot_env(add=None, remove=None):
    """Armbian's armbianEnv.txt: user_overlays with our overlay put in or taken out."""
    path = BOOT / "armbianEnv.txt"
    lines = _read(path).splitlines()
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
        safeio.write(path, "\n".join(lines) + "\n", 0o644)


def _lyra_overlay(on):
    dtbo = BOOT / "overlay-user" / f"{OVERLAY}.dtbo"
    if not on:
        dtbo.unlink(missing_ok=True)
        if (BOOT / "armbianEnv.txt").is_file():
            _boot_env(remove=OVERLAY)
        return
    dtbo.parent.mkdir(parents=True, exist_ok=True)
    r = run("dtc", "-@", "-q", "-I", "dts", "-O", "dtb", "-o", str(dtbo), "-", inp=LYRA_DTS)
    if r.returncode:
        dtbo.unlink(missing_ok=True)
        raise Refused(
            M("ownserial.dtc-failed", "The device tree compiler (dtc) failed: {detail}", detail=r.stderr.strip()[:160])
        )
    _boot_env(add=OVERLAY)


def set_pins(on):
    """Turn this box's own pins on or off in its boot files; the device appears at the next restart."""
    board = hardening.board()
    if board not in DEVICE:
        raise Refused(
            M("ownserial.unsupported", "This board has no route irate-box knows from its header pins to a serial port.")
        )
    st = _state()
    if board == "pi":
        added = _pi_config(on, st.get("added") or [])
        removed = _pi_cmdline(on, st.get("cmdline_removed"))
        _save({"on": on, "board": board, "added": added, "cmdline_removed": removed})
    else:
        _lyra_overlay(on)
        _save({"on": on, "board": board})
    if on:
        return M(
            "ownserial.on",
            "Saved. Restart the box to use its own header pins as a serial console. {warning}",
            warning=WARNING.get(board, ""),
        )
    return M("ownserial.off", "Saved. The box's own pins are free again after a restart.")


def undo_all():
    """Uninstall: the boot files back as they were."""
    if _state().get("on"):
        set_pins(False)


def main(argv):
    if argv == ["status"]:
        for k, v in status().items():
            print(f"{k}: {v}")
        return 0
    if argv == ["on"]:
        print(set_pins(True))
        return 0
    if argv == ["off"]:
        print(set_pins(False))
        return 0
    if argv == ["undo-all"]:
        undo_all()
        return 0
    print("Usage: irate-box ownserial status | on | off | undo-all", file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
