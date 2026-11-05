# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The AIC8800's chip gone from the USB bus while its driver stack is still loaded.

On the Lyra's 2026-11-04 boot the chip disappeared at the driver's own power-key restart and
never came back as device 4. Only `aic_load_fw` had bound before it went: `aic8800_fdrv` loads
with the chip's return, so a check that waits for `aic8800_fdrv` sees nothing on this failure.
The driver stack is any of the AIC modules; the failure is one of them loaded, no WiFi client
interface, and the kernel repeating ENODEV (`-19`) for the device that is gone.

One function for the Box doctor and crash watch, so they cannot disagree on the modules, the
line shape or the threshold. Stdlib only.
"""

import os
import re
import subprocess
from pathlib import Path

SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
MODULES = ("aic_load_fw", "aic8800_fdrv", "aic8800_bsp")
GONE_LINE = re.compile(r"(?:urb fail|error):?\s*-19\b", re.I)
REPEAT = 3  # more than a stray error: the driver is still submitting to a device that is gone


def _run(*cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def loaded(sysfs=None):
    """The AIC modules loaded now, in MODULES order (aic_load_fw is enough on the stuck boot)."""
    sysfs = Path(sysfs or SYS)
    return [name for name in MODULES if (sysfs / "module" / name).exists()]


def clients(sysfs=None, run=None):
    """The WiFi client interfaces, from iw; a hotspot's AP interface is not one. Where iw cannot
    answer, every wireless interface counts, so a missing client is never claimed without a look."""
    run = run or _run
    code, out = run("iw", "dev", timeout=10)
    if code == 0:
        found, name = [], None
        for line in out.splitlines():
            match = re.match(r"\s*Interface (\S+)", line)
            if match:
                name = match.group(1)
                continue
            match = re.search(r"\btype (\w+)", line)
            if match and name:
                if match.group(1) == "managed":
                    found.append(name)
                name = None
        return found
    sysfs = Path(sysfs or SYS)
    try:
        return sorted(
            d.name
            for d in (sysfs / "class/net").iterdir()
            if d.name != "lo" and ((d / "wireless").exists() or (d / "phy80211").exists())
        )
    except OSError:
        return []


def chip_gone(kernel, sysfs=None, run=None):
    """{gone, modules, lines, why}: the chip gone while its driver stack is loaded. `kernel` is the
    lines of this boot to search; `run` takes (returncode, output), as crash watch's and the
    uplink's do. Empty `gone` when a module is absent, a client interface is there, or -19 is not
    repeating."""
    sysfs = Path(sysfs or SYS)
    modules = loaded(sysfs)
    if not modules:
        return {"gone": False, "modules": [], "lines": [], "why": ""}
    if clients(sysfs=sysfs, run=run):
        return {"gone": False, "modules": modules, "lines": [], "why": ""}
    lines = [line.strip() for line in kernel if GONE_LINE.search(line)]
    if len(lines) < REPEAT:
        return {"gone": False, "modules": modules, "lines": lines, "why": ""}
    why = f"{' and '.join(modules)} loaded with no WiFi interface, and {len(lines)} lines repeat: {lines[-1][:160]}"
    return {"gone": True, "modules": modules, "lines": lines, "why": why}
