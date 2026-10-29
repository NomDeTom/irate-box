# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Lyra U-Boot build (tools/build-uboot.sh), offline: the card fragment has the redundant environment and boot
count and no eMMC-partition setting, and a dry run names the pinned checkout, fragment merge, fixed output and
checksum without running git or make. python3 tests/sim_uboot_build.py"""

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fragment = (REPO / "tools" / "uboot" / "lyra-card.fragment").read_text()
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


for line in (
    "CONFIG_ENV_IS_NOWHERE=n",
    "CONFIG_ENV_IS_IN_MMC=y",
    "CONFIG_SYS_REDUNDAND_ENVIRONMENT=y",
    "CONFIG_ENV_OFFSET=0x1000000",
    "CONFIG_ENV_OFFSET_REDUND=0x1002000",
    "CONFIG_BOOTCOUNT_LIMIT=y",
    "CONFIG_BOOTCOUNT_ENV=y",
    "CONFIG_CMD_SETEXPR=y",
):
    check(f"the card fragment has {line}", line in fragment)
check(
    "  it does not use the eMMC hardware partition setting",
    not any(line.startswith("CONFIG_SYS_MMC_ENV_PART=") for line in fragment.splitlines()),
)
pin = (REPO / "tools" / "uboot" / "lyra-pin.txt").read_text()
check(
    "the pin: the repository, the tested commit and the Lyra defconfig",
    "source.denx.de/u-boot/contributors/kwiboo/u-boot.git" in pin
    and "0b8e25bd9e16e8043b600e8f49b926b95572dc47" in pin
    and "luckfox-lyra-zero-w-rk3506b_defconfig" in pin,
    pin,
)
check(
    "  the device-tree fragment names the bootstate partition",
    "u-boot,mmc-env-partition" in (REPO / "tools" / "uboot" / "irate-bootstate.dtsi").read_text(),
)
out = Path(tempfile.mkdtemp(prefix="uboot-out-"))
result = subprocess.run(
    ["bash", str(REPO / "tools" / "build-uboot.sh"), "--dry-run", "--out", str(out)],
    capture_output=True,
    text=True,
)
text = result.stdout
check(
    "a dry run: the pinned checkout, fragment, fixed output and checksum, no build",
    result.returncode == 0,
    result.stderr,
)
check(
    "  the pinned commit and defconfig are named",
    "0b8e25bd9e16e8043b600e8f49b926b95572dc47" in text and "luckfox-lyra-zero-w-rk3506b_defconfig" in text,
    text,
)
check(
    "  the fragment is merged and the config read back", "merge_config.sh" in text and "SOURCE_DATE_EPOCH" in text, text
)
check(
    "  the output is u-boot-rockchip.bin and its checksum", "u-boot-rockchip.bin" in text and "sha256sum" in text, text
)
check("  and nothing was cloned", not (out / "src").exists(), list(out.iterdir()))
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
