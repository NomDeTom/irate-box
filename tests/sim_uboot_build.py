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
    "CONFIG_ENV_REDUNDANT=y",
    "CONFIG_ENV_MMC_DEVICE_INDEX=0",
    "CONFIG_ENV_MMC_USE_DT=y",
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
    "github.com/Kwiboo/u-boot-rockchip.git" in pin
    and "dts=arch/arm/dts/rk3506b-luckfox-lyra-zero-w.dts" in pin
    and "0b8e25bd9e16e8043b600e8f49b926b95572dc47" in pin
    and "luckfox-lyra-zero-w-rk3506b_defconfig" in pin,
    pin,
)
# Found building on the Pi (2026-10-30): the old names were dropped by olddefconfig, so the redundant environment
# was never built in.
check(
    "  the fragment uses the names the pinned tree has, not the ones it renamed",
    not any(
        line.startswith(("CONFIG_SYS_REDUNDAND_ENVIRONMENT", "CONFIG_SYS_MMC_ENV_DEV"))
        for line in fragment.splitlines()
    ),
)
blobs = dict(line.split("=", 1) for line in pin.splitlines() if "=" in line and not line.startswith("#"))
check(
    "  the pin names the DDR-init and OP-TEE blobs the image needs, each with a SHA-256",
    blobs.get("rkbin", "").startswith("https://raw.githubusercontent.com/armbian/rkbin/")
    and blobs.get("tpl", "").endswith(".bin")
    and blobs.get("tee", "").endswith(".bin")
    and all(len(blobs.get(k, "")) == 64 for k in ("tpl_sha256", "tee_sha256")),
    blobs,
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
    "  merged only (-m), from the source tree, so its own make cannot run elsewhere and merge nothing",
    "merge_config.sh -m" in text,
    text,
)
check(
    "  the build is given the two blobs",
    "ROCKCHIP_TPL=" in text and "TEE=" in text and blobs["tpl_sha256"] in text and blobs["tee_sha256"] in text,
    text,
)
bare = Path(tempfile.mkdtemp(prefix="uboot-pin-")) / "pin.txt"
bare.write_text("\n".join(line for line in pin.splitlines() if not line.startswith(("rkbin", "tpl", "tee"))) + "\n")
said = subprocess.run(
    ["bash", str(REPO / "tools" / "build-uboot.sh"), "--dry-run", "--pin", str(bare), "--out", str(out)],
    capture_output=True,
    text=True,
)
check("  a pin without the blobs is refused", said.returncode != 0 and "blobs" in said.stderr, said.stderr)
check(
    "  the output is u-boot-rockchip.bin and its checksum", "u-boot-rockchip.bin" in text and "sha256sum" in text, text
)
check("  and nothing was cloned", not (out / "src").exists(), list(out.iterdir()))
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
