# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The read-only system: the root overlaid in RAM, so a power cut cannot damage it.

On the Pi this is raspi-config's overlay; on the Lyra (or another Armbian board) overlayroot's tmpfs. The setting is
recorded in /etc/hub/readonly and takes effect at the next restart. It writes a boot file on both boards: the Pi's
cmdline.txt and update-initramfs's initrd, the Lyra's only initrd (its /boot is a folder on the card). Nothing here
remounts the live root: on an overlay that would only make the RAM layer read-only."""

import os
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import hardening

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
CONF = Path(os.environ.get("HUB_OVERLAYROOT_CONF", "/etc/overlayroot.conf"))
HOOK = Path(os.environ.get("HUB_READONLY_HOOK", "/etc/apt/apt.conf.d/80-irate-box-readonly"))
MARK = ETC / "readonly"
MOUNTS = Path(os.environ.get("HUB_READONLY_MOUNTS", "/proc/mounts"))


def run(*cmd, timeout=600):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def enabled():
    return MARK.is_file()


def _installed(name):
    """Whether dpkg has the package, whatever an installer's exit code said."""
    return run("dpkg", "-s", name).returncode == 0


def _overlaid():
    """Whether / is an overlay now, as it is after the restart that turns this on."""
    try:
        return any(
            len(parts) >= 3 and parts[1] == "/" and parts[2] == "overlay"
            for parts in (line.split() for line in MOUNTS.read_text().splitlines())
        )
    except OSError:
        return False


def _save(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    tmp.chmod(0o644)
    os.replace(tmp, path)


def _why(said):
    return (said.stderr or said.stdout).strip()[-200:]


def _enable_pi():
    said = run("raspi-config", "nonint", "enable_overlayfs")
    if said.returncode:
        raise Refused(M("readonly.failed", "The overlay could not be switched on: {why}", why=_why(said)))
    if not _installed("overlayroot"):
        # raspi-config returns 0 after writing cmdline.txt even when its apt-get install failed.
        run("raspi-config", "nonint", "disable_overlayfs")
        raise Refused(
            M(
                "readonly.pi-not-installed",
                "The overlay could not be switched on: raspi-config did not install overlayroot, so the next boot "
                "would have the command-line switch but not the overlay.",
            )
        )


def _enable_lyra():
    if not _installed("overlayroot"):
        said = run(
            "apt-get",
            "install",
            "-y",
            "-o",
            "Acquire::http::Timeout=20",
            "-o",
            "Acquire::Retries=0",
            "overlayroot",
            timeout=900,
        )
        if said.returncode or not _installed("overlayroot"):
            raise Refused(
                M(
                    "readonly.lyra-not-installed",
                    "The overlay could not be switched on: overlayroot is not installed, and it could not be installed "
                    "({why}).",
                    why=_why(said) or "no answer from apt",
                )
            )
    _save(
        CONF,
        "# irate-box: the root is overlaid in RAM (overlayroot). Undo on Recovery and monitoring.\n"
        'overlayroot_cfgdisk="disabled"\n'
        'overlayroot="tmpfs"\n',
    )
    said = run("update-initramfs", "-u")
    if said.returncode:
        CONF.unlink(missing_ok=True)
        raise Refused(M("readonly.failed", "The overlay could not be switched on: {why}", why=_why(said)))


def _disable_pi():
    said = run("raspi-config", "nonint", "disable_overlayfs")
    if said.returncode:
        raise Refused(M("readonly.off-failed", "The overlay could not be switched off: {why}", why=_why(said)))


def _disable_lyra():
    old = CONF.read_text() if CONF.is_file() else None
    if _overlaid():
        # The live files are in RAM. Remove them from the lower root too, or they come back at the next restart.
        said = run(
            "overlayroot-chroot",
            "-c",
            f"rm -f {CONF} {HOOK} {MARK} && update-initramfs -u",
        )
        if said.returncode:
            raise Refused(M("readonly.off-failed", "The overlay could not be switched off: {why}", why=_why(said)))
        CONF.unlink(missing_ok=True)
        HOOK.unlink(missing_ok=True)
        MARK.unlink(missing_ok=True)
        return
    CONF.unlink(missing_ok=True)
    said = run("update-initramfs", "-u")
    if said.returncode:
        if old is not None:
            _save(CONF, old)
        raise Refused(M("readonly.off-failed", "The overlay could not be switched off: {why}", why=_why(said)))


def enable(on):
    """Turn the overlay on or off. The board's own tool does the boot side; nothing is recorded unless it worked."""
    board = hardening.board()
    if board not in ("pi", "lyra"):
        raise Refused(
            M(
                "readonly.unsupported",
                "This board does not have a read-only overlay route that the box knows (the Pi and the Lyra do).",
            )
        )
    if on:
        if board == "pi":
            _enable_pi()
        else:
            _enable_lyra()
        # An old install wrote apt hooks that remounted the live root. Remove them: they cannot reach the lower
        # root, and on a working overlay they make the RAM layer itself read-only.
        HOOK.unlink(missing_ok=True)
        _save(MARK, "on\n")
        return M(
            "readonly.on",
            "The system will be read-only over a RAM overlay after the next restart. Everything written until then, "
            "packages and the box's own updates included, is lost at that restart.",
        )
    if board == "pi":
        _disable_pi()
    else:
        _disable_lyra()
    HOOK.unlink(missing_ok=True)
    MARK.unlink(missing_ok=True)
    return M(
        "readonly.off",
        "The system will be writable again after the next restart. The overlay's changes are lost at that restart.",
    )


def groups():
    if hardening.board() not in ("pi", "lyra"):
        return []
    return [
        {
            "key": "readonly",
            "label": M("readonly.setting.title", "A read-only system"),
            "desc": M(
                "readonly.setting.desc",
                "The root filesystem is overlaid in RAM: a power cut cannot damage it. Every write goes to RAM, so "
                "packages installed and the box's own updates last only until the next restart; the board needs "
                "enough memory (raspi-config recommends 512 MB). On the Lyra, whose /boot is a folder on the same "
                "card, a watchdog overlay, the A/B and boot-key settings and a kernel update cannot be kept while "
                "this is on. It takes effect at the next restart.",
            ),
            "current": "on" if enabled() else "off",
            "options": [
                {
                    "value": "on",
                    "label": M("readonly.setting.on", "Read-only"),
                    "choice": "readonly:on",
                    "confirm": M(
                        "readonly.setting.on.confirm",
                        "Turn the system read-only? It takes effect at the next restart, and changes made meanwhile "
                        "(packages, updates and settings that write the card or /boot) are lost at that restart.",
                    ),
                },
                {"value": "off", "label": M("readonly.setting.off", "Writable"), "choice": "readonly:off"},
            ],
        }
    ]
