# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The read-only system: the root overlaid in RAM, so a power cut cannot damage it, with apt remount hooks.

On the Pi this is raspi-config's overlay; on the Lyra (or another Armbian board) overlayroot's tmpfs. The setting is
recorded in /etc/hub/readonly, and the apt hooks remount the real root read-write for a package run and back after.
Nothing here writes a boot file, a card's boot area or a NAND."""

import os
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import hardening

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
CONF = Path(os.environ.get("HUB_OVERLAYROOT_CONF", "/etc/overlayroot.conf"))
HOOK = Path(os.environ.get("HUB_READONLY_HOOK", "/etc/apt/apt.conf.d/80-irate-box-readonly"))
MARK = ETC / "readonly"
HOOK_TEXT = (
    "// irate-box: the root is read-only; a package run gets it read-write and puts it back.\n"
    'DPkg::Pre-Invoke { "mount -o remount,rw / || true"; };\n'
    'DPkg::Post-Invoke { "mount -o remount,ro / || true"; };\n'
)


def run(*cmd, timeout=600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def enabled():
    return MARK.is_file()


def _save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    tmp.chmod(0o644)
    os.replace(tmp, path)


def enable(on):
    """Turn the overlay and the apt hooks on or off. The board's own tool does the boot side."""
    board = hardening.board()
    if on:
        if board == "pi":
            said = run("raspi-config", "nonint", "enable_overlayfs")
        else:
            _save(
                CONF,
                "# irate-box: the root is overlaid in RAM (overlayroot). Undo on Recovery and monitoring.\n"
                'overlayroot_cfgdisk="disabled"\noverlayroot="tmpfs"\n',
            )
            said = run("update-initramfs", "-u")
        if said.returncode:
            raise Refused(
                M(
                    "readonly.failed",
                    "The overlay could not be switched on: {why}",
                    why=(said.stderr or said.stdout).strip()[-200:],
                )
            )
        _save(HOOK, HOOK_TEXT)
        _save(MARK, "on\n")
        return M("readonly.on", "The system is read-only over a RAM overlay. apt gets it read-write and puts it back.")
    if board == "pi":
        said = run("raspi-config", "nonint", "disable_overlayfs")
    else:
        CONF.unlink(missing_ok=True)
        said = run("update-initramfs", "-u")
    HOOK.unlink(missing_ok=True)
    MARK.unlink(missing_ok=True)
    if said.returncode:
        raise Refused(
            M(
                "readonly.off-failed",
                "The overlay could not be switched off: {why}",
                why=(said.stderr or said.stdout).strip()[-200:],
            )
        )
    return M("readonly.off", "The system is writable again, and the apt hooks are gone.")


def groups():
    return [
        {
            "key": "readonly",
            "label": M("readonly.setting.title", "A read-only system"),
            "desc": M(
                "readonly.setting.desc",
                "The root filesystem is overlaid in RAM: a power cut cannot damage it, and changes outside apt are "
                "lost at the next restart. apt gets the real root read-write for a package run and puts it back.",
            ),
            "current": "on" if enabled() else "off",
            "options": [
                {"value": "on", "label": M("readonly.setting.on", "Read-only"), "choice": "readonly:on"},
                {
                    "value": "off",
                    "label": M("readonly.setting.off", "Writable"),
                    "choice": "readonly:off",
                    "confirm": M(
                        "readonly.setting.off.confirm",
                        "Make the root writable again and remove the apt hooks? The overlay's changes are lost either way.",
                    ),
                },
            ],
        }
    ]
