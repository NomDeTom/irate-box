# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The kernel the box runs: its published configuration, and the features the hub relies on.

The Box and Security doctors both ask the same questions, so they ask here: whether the kernel can attach
systemd's cgroup network programs (IPAddressDeny and IPAccounting), and whether it has AppArmor. Read from the
kernel's own configuration where it publishes one, from the module or security folder in sysfs, and from this
boot's log for systemd's own words. Read only; Stdlib only.

The paths can be moved for the tests (HUB_SYSFS, HUB_PROC or HUB_PROC_DIR, HUB_BOOT_DIR, HUB_KERNEL_RELEASE).
"""

import gzip
import os
import re
import subprocess
from pathlib import Path

SYSFS = Path(os.environ.get("HUB_SYSFS", "/sys"))
PROC = Path(os.environ.get("HUB_PROC") or os.environ.get("HUB_PROC_DIR") or "/proc")
BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
RELEASE = os.environ.get("HUB_KERNEL_RELEASE") or os.uname().release


def run(*cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251


def config():
    """The booted kernel's configuration, as {CONFIG_NAME: value} (value "n" when it says unset), or None when the
    kernel does not publish it."""
    for path in (BOOT / f"config-{RELEASE}", PROC / "config.gz"):
        try:
            text = (
                gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
                if path.suffix == ".gz"
                else path.read_text(errors="replace")
            )
        except (OSError, ValueError):
            continue
        out = {}
        for line in text.splitlines():
            m = re.fullmatch(r"^(CONFIG_[A-Z0-9_]+)=(.*)$", line)
            if m:
                out[m[1]] = m[2].strip()
            m = re.fullmatch(r"^# (CONFIG_[A-Z0-9_]+) is not set$", line)
            if m:
                out[m[1]] = "n"
        if out:
            return out
    return None


def bpf_errors():
    """systemd's own words when the kernel cannot attach its cgroup programs, from this boot's kernel log."""
    try:
        out = run("journalctl", "-k", "-b", "--no-pager", "-g", "bpf-firewall|error 524")
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip()[:200] for line in out.stdout.splitlines() if re.search(r"bpf-firewall|524", line)][-3:]


def cgroup_bpf(found=None):
    """Whether the kernel can attach systemd's cgroup network programs: True, False, or None when it cannot say."""
    if found is not None:
        return found.get("CONFIG_CGROUP_BPF", "n") in ("y", "m")
    return False if bpf_errors() else None


def apparmor(found=None):
    """Whether the kernel has AppArmor: True, False, or None when it cannot say."""
    if (SYSFS / "module/apparmor").exists() or (SYSFS / "kernel/security/apparmor").is_dir():
        return True
    if found is not None:
        return found.get("CONFIG_SECURITY_APPARMOR", "n") in ("y", "m")
    return None
