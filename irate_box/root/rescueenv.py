# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The card's view of the NAND U-Boot environment (stage 1): read only.

The card carries /etc/fw_env.config naming the two raw env copies on the boot MTD.  This
module reads the config, checks it against /proc/mtd and /sys/class/mtd, reads each copy on
its own and compares them, and returns the keys the page shows: bootcount, bootlimit,
card_confirms and whether altbootcmd holds the rescue path.  It never writes anything.

Refusals are Refused(M(...)) with the messages in rescue-interfaces.md §4.2.  Stdlib only.
"""

import os
import re
import shutil
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import tempfile
from pathlib import Path

from irate_box.msg import M, Refused

FW_ENV = Path(os.environ.get("HUB_FW_ENV_CONFIG", "/etc/fw_env.config"))
PROC_MTD = Path(os.environ.get("HUB_PROC_MTD", "/proc/mtd"))
MTD_SYS = Path(os.environ.get("HUB_MTD_SYS", "/sys/class/mtd"))
FW_PRINTENV = os.environ.get("HUB_FW_PRINTENV", "fw_printenv")
BOOT_BYTES = 32 << 20
PRIMARY = 0x7E0000
REDUNDANT = 0x7C0000
ENV_BYTES = 0x20000
MTD_RE = re.compile(r"^/dev/mtd(\d+)$")


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _mtds():
    out = {}
    try:
        lines = PROC_MTD.read_text().splitlines()[1:]
    except OSError:
        return out
    for line in lines:
        match = re.match(r'(mtd\d+):\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"(.*)"', line.strip())
        if match:
            out[int(match.group(1)[3:])] = {
                "mtd": int(match.group(1)[3:]),
                "size": int(match.group(2), 16),
                "erasesize": int(match.group(3), 16),
                "name": match.group(4),
            }
    return out


def _entries():
    try:
        text = FW_ENV.read_text()
    except OSError:
        raise Refused(
            M(
                "rescue.env.no-config",
                "This card has no /etc/fw_env.config, so the box cannot read U-Boot's environment.",
            )
        ) from None
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) < 3:
            raise _bad_config()
        device = fields[0]
        try:
            offset, size = int(fields[1], 0), int(fields[2], 0)
            erase = int(fields[3], 0) if len(fields) > 3 else size
        except ValueError:
            raise _bad_config() from None
        if not MTD_RE.fullmatch(device) or size != ENV_BYTES or offset not in (PRIMARY, REDUNDANT):
            raise _bad_config()
        entries.append(
            {
                "device": device,
                "mtd": int(MTD_RE.fullmatch(device).group(1)),
                "offset": offset,
                "size": size,
                "erase": erase,
            }
        )
    if not entries:
        raise _bad_config()
    return entries


def _bad_config():
    return Refused(
        M(
            "rescue.env.config-bad",
            "The boot-environment config does not name the 32 MiB boot MTD, so the box will not use it.",
        )
    )


def _boot_mtd(entries):
    mtds = _mtds()
    wanted = mtds.get(entries[0]["mtd"])
    if wanted is None or wanted["size"] != BOOT_BYTES or wanted["name"] != "boot":
        raise _bad_config()
    try:
        size = int((MTD_SYS / f"mtd{wanted['mtd']}" / "size").read_text().strip())
        if size != wanted["size"]:
            raise _bad_config()
    except (OSError, ValueError):
        pass
    return wanted


def _read(config):
    tool = FW_PRINTENV if os.path.isabs(FW_PRINTENV) else shutil.which(FW_PRINTENV)
    if not tool:
        raise Refused(
            M(
                "rescue.env.no-tool",
                "The fw_printenv tool is not installed, so the box cannot read U-Boot's environment.",
            )
        )
    result = run([tool, "-c", str(config)])
    if result.returncode:
        return None
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _one(config, entry):
    with tempfile.TemporaryDirectory(prefix="rescue-env-") as tmp:
        path = Path(tmp) / "fw_env.config"
        path.write_text(f"{entry['device']} 0x{entry['offset']:X} 0x{entry['size']:X} 0x{entry['erase']:X}\n")
        return _read(path)


def _number(values, key):
    text = values.get(key, "")
    return int(text) if text.isdigit() else None


def show():
    entries = _entries()
    device = _boot_mtd(entries)
    primary = _one(FW_ENV, entries[0])
    redundant = _one(FW_ENV, entries[1]) if len(entries) > 1 else None
    if primary is None and redundant is None:
        raise Refused(M("rescue.env.unreadable", "Neither copy of U-Boot's environment can be read."))
    values = primary if primary is not None else redundant
    return {
        "available": True,
        "config": {"path": str(FW_ENV), "present": True, "valid": True},
        "device": {
            "mtd": device["mtd"],
            "name": device["name"],
            "size": device["size"],
            "erasesize": device["erasesize"],
        },
        "bootcount": _number(values, "bootcount"),
        "bootlimit": _number(values, "bootlimit"),
        "card_confirms": values.get("card_confirms") == "1" if "card_confirms" in values else None,
        "altbootcmd": values.get("altbootcmd", ""),
        "altbootcmd_rescue": "nand_boot_rescue" in values.get("altbootcmd", ""),
        "copies": {
            "primary": primary is not None,
            "redundant": redundant is not None,
            "agree": primary is not None and redundant is not None and primary == redundant,
        },
    }


def snapshot():
    """The control JSON's env: the reading, or why it could not be made. Never raises."""
    try:
        return show()
    except Refused as exc:
        return {
            "available": False,
            "error": str(exc),
            "config": {
                "path": str(FW_ENV),
                "present": FW_ENV.is_file(),
                "valid": False,
            },
        }
