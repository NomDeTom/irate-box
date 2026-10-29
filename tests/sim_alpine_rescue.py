# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Alpine rescue root (tools/make-alpine-rescue.sh), offline: the package list has the checks' tools, and a dry
run names the armv7 apk root, no scripts, pinned versions, the kernel/driver copies, the du and the zstd squashfs.
python3 tests/sim_alpine_rescue.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="alpine-"))
out = T / "rescue"
versions = T / "versions"
versions.write_text("python3=3.12.0-r0\n")
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


packages = (REPO / "tools" / "alpine" / "packages.txt").read_text().split()
check(
    "the package list: base, SSH, WiFi, and the checks' tools",
    {"alpine-base", "openssh", "wpa_supplicant", "python3", "e2fsprogs", "util-linux", "dpkg", "git", "gdisk", "f3"}
    <= set(packages),
    packages,
)
result = subprocess.run(
    [
        "bash",
        str(REPO / "tools" / "make-alpine-rescue.sh"),
        "--dry-run",
        "--out",
        str(out),
        "--versions",
        str(versions),
        "--kernel-modules",
        "/tmp/kernel-modules",
        "--kernel-version",
        "6.1.0-lyra",
        "--aic8800",
        "/tmp/aic8800.ko",
    ],
    capture_output=True,
    text=True,
)
text = result.stdout
check("a dry run: apk.static for armv7, an initdb root, no scripts", result.returncode == 0, result.stderr)
check("  the Alpine release and the armv7 arch are named", "Alpine 3.22" in text and "--arch armv7" in text, text)
check("  the packages' scripts are left for the Pi or qemu", "--no-scripts" in text, text)
check(
    "  Alpine's keys are given to apk, never --allow-untrusted",
    "--keys-dir" in text and "--allow-untrusted" not in text,
    text,
)
check("  a pinned version replaces the name", "python3=3.12.0-r0" in text, text)
check(
    "  the kernel modules and the AIC8800 driver are copied, the driver under its kernel",
    "/tmp/kernel-modules" in text and "lib/modules/6.1.0-lyra/extra/" in text and "depmod" in text,
    text,
)
check(
    "  the root is measured and made a zstd squashfs",
    "du -sk" in text and "mksquashfs" in text and "-comp zstd" in text,
    text,
)
check(
    "  the source and key notes are in the recipe",
    (REPO / "tools" / "alpine" / "README.md").is_file()
    and (REPO / "tools" / "alpine" / "keys" / "README.md").is_file(),
)
keys = {path.name for path in (REPO / "tools" / "alpine" / "keys").glob("*.pub")}
check(
    "  the armv7 signing keys are in the repository",
    any("524d27bb" in name for name in keys)
    and any("616adfeb" in name for name in keys)
    and not any("4a6a0840" in name for name in keys),
    keys,
)
check("  nothing was made", not out.exists(), list(T.iterdir()))
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
