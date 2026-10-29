# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The invasive hardening options (root/hardening.py; notes: invasive-hardening-plan stages 6 and 7), offline: the
two choices and their current values, a filesystem recorded, the U-Boot/RAUC and tryboot boot files written, the
plan's partitions, the Box doctor findings, and the Recovery choices through health.fix.
python3 tests/sim_hardening.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="hardening-"))
etc, dt, boot = T / "etc", T / "dt", T / "boot"
etc.mkdir()
dt.mkdir()
boot.mkdir()
(dt / "model").write_bytes(b"Luckfox Lyra Zero W\0")
bootstate, devmem = T / "bootstate", T / "mem"
bootstate.mkdir()
os.environ.update(
    HUB_ETC_DIR=str(etc),
    HUB_DEVICE_TREE=str(dt),
    HUB_BOOT_DIR=str(boot),
    HUB_AB_STATE_DIR=str(bootstate),
    HUB_DEV_MEM=str(devmem),
)
(boot / "armbianEnv.txt").write_text("verbosity=1\n")
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.msg import Refused
from irate_box.root import hardening, health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


check("the board is found from the device tree", hardening.board() == "lyra", hardening.board())
groups = {g["key"]: g for g in hardening.groups()}
check(
    "the two options: the filesystem and A/B, both off as they come",
    set(groups) == {"filesystem", "ab"}
    and groups["filesystem"]["current"] == "ext4"
    and groups["ab"]["current"] == "off",
    groups,
)
check(
    "  each option carries the root helper's card-set choice",
    all(o["choice"].startswith("card-set:") for g in groups.values() for o in g["options"])
    and {o["value"] for o in groups["filesystem"]["options"]} == {"ext4", "btrfs", "f2fs", "btrfs+f2fs"},
)
said = hardening.set_filesystem("btrfs+f2fs")
check(
    "a checksumming filesystem choice is recorded, and said it waits for a fresh install",
    said.get("id") == "harden.fs.set"
    and json.loads((etc / "hardening.json").read_text())["filesystem"] == "btrfs+f2fs"
    and hardening.filesystem() == "btrfs+f2fs",
    said,
)
try:
    hardening.set_filesystem("zfs")
    check("  a filesystem it does not offer: refused", False)
except Refused as exc:
    check("  a filesystem it does not offer: refused", exc.message["id"] == "harden.fs.unknown")

said = hardening.set_ab("uboot")
artifacts = {p.name for p in (etc / "ab").iterdir()}
check(
    "U-Boot and RAUC writes the system description, the boot script and the environment example",
    said.get("id") == "harden.ab.uboot.ready"
    and {"system.conf", "boot.cmd", "fw_env.config.example"} <= artifacts
    and "bootname=A" in (etc / "ab" / "system.conf").read_text()
    and "BOOT_ORDER" in (etc / "ab" / "boot.cmd").read_text()
    and "setenv BOOT_A_LEFT 2" in (etc / "ab" / "boot.cmd").read_text(),
    (said, artifacts),
)
uboot_cmd = (etc / "ab" / "boot.cmd").read_text()
check(
    "  each slot boots through its own Armbian boot.scr, from its own partition, loaded clear of load_addr",
    "setenv devnum 0:${BOOT_PART}" in uboot_cmd
    and "part number mmc 0 irate-system-${irate_slot} BOOT_PART" in uboot_cmd
    and "load mmc 0:${BOOT_PART} ${scriptaddr} /boot/boot.scr" in uboot_cmd
    and "source ${scriptaddr}" in uboot_cmd
    and "rootdev=PARTLABEL=irate-system-a" in uboot_cmd,
    uboot_cmd,
)
check(
    "  and sets no device tree, kernel or root of its own (the slot's armbianEnv.txt and boot.scr do)",
    "fdtfile" not in uboot_cmd.split("# CONFIG")[0].replace("its fdtfile", "")
    and "setenv fdtfile" not in uboot_cmd
    and "booti" not in uboot_cmd
    and "setenv bootargs" not in uboot_cmd,
    uboot_cmd,
)
env_example = (etc / "ab" / "fw_env.config.example").read_text().splitlines()
real_run = hardening.run
hardening.BOOTSTATE_DEVICE = Path("/dev/test-bootstate")
hardening.run = lambda *cmd, **kw: (
    subprocess.CompletedProcess(cmd, 0, "33554432\n", "") if cmd[0] == "blockdev" else real_run(*cmd, **kw)
)
check(
    "  the offsets follow the partition's size, not a fixed 16 MiB",
    hardening._env_offsets() == ((32 << 20) - 0x2000, (32 << 20) - 0x4000),
    hardening._env_offsets(),
)
hardening.run = real_run
check(
    "  the environment example has no active device line, and names the partition's top",
    all(not line or line.startswith("#") for line in env_example)
    and any(int(line.split()[2], 16) == (16 << 20) - 0x2000 for line in env_example if line.startswith("# /dev"))
    and any(int(line.split()[2], 16) == (16 << 20) - 0x4000 for line in env_example if line.startswith("# /dev")),
    env_example,
)
check(
    "  its status says the route and which tools are missing",
    hardening.status()["ab"] == "uboot"
    and set(hardening.status()["tools"]) == {"fw_printenv", "fw_setenv", "rauc", "mkimage"},
    hardening.status(),
)
said = hardening.set_ab("tryboot")
check(
    "the Lyra's tryboot waits until a spare register is recorded",
    said.get("id") == "harden.ab.tryboot.lyra.waiting" and not (etc / "ab" / "boot.cmd").exists(),
    said,
)
try:
    hardening.set_tryboot_reg("0xff910200")
    check("  U-Boot's own boot-mode register is refused", False)
except Refused as exc:
    check("  U-Boot's own boot-mode register is refused", exc.message["id"] == "harden.ab.reg.invalid")
try:
    hardening.set_tryboot_reg("0x0")
    check("  address zero is refused", False)
except Refused as exc:
    check("  address zero is refused", exc.message["id"] == "harden.ab.reg.invalid")
for bad in ("0xff910201", "0x00001000", "0xff911000", "0xff90fffc", "ff910208"):
    try:
        hardening.set_tryboot_reg(bad)
        check(f"  {bad} is refused (unaligned, or outside the PMU GRF page)", False)
    except Refused as exc:
        check(
            f"  {bad} is refused (unaligned, or outside the PMU GRF page)", exc.message["id"] == "harden.ab.reg.invalid"
        )
hardening.set_tryboot_reg("0xff910208")
said = hardening.set_default("B")
check(
    "  the default slot is written to ab.txt on the bootstate partition, where the boot script reads it",
    said.get("id") == "harden.ab.default.set"
    and (bootstate / "ab.txt").read_text() == "ab_default=B\n"
    and "ab_default" not in (boot / "armbianEnv.txt").read_text(),
    said,
)
os.environ["HUB_AB_STATE_DIR"] = str(T / "not-mounted")
hardening.AB_STATE_DIR = T / "not-mounted"
try:
    hardening.set_default("A")
    check("  with the bootstate partition not mounted, refused", False)
except Refused as exc:
    check("  with the bootstate partition not mounted, refused", exc.message["id"] == "harden.ab.default.no-state")
os.environ["HUB_AB_STATE_DIR"] = str(bootstate)
hardening.AB_STATE_DIR = bootstate
# Arming: the magic at the register's offset, through a stand-in /dev/mem (sparse, as large as the address).
with open(devmem, "wb") as fh:
    fh.truncate(0xFF911000)
said = hardening.arm_tryboot()
with open(devmem, "rb") as fh:
    fh.seek(0xFF910000)
    page = fh.read(0x1000)  # the one page: the file is as large as the address, sparse
word = page[0x208:0x20C]
check(
    "  arming writes the magic at the recorded register, and nothing else in its page",
    said.get("id") == "harden.ab.tryboot.armed"
    and word == (0x5442).to_bytes(4, "little")
    and sum(1 for b in page if b) == 2,
    (said, word),
)
devmem.unlink()
said = hardening.set_ab("tryboot")
tryboot_cmd = (etc / "ab" / "boot.cmd").read_text()
check(
    "  with a spare register, the boot script reads the default from ab.txt and takes the one-shot flag",
    said.get("id") == "harden.ab.tryboot.lyra"
    and "load mmc 0:${STATE_PART} ${scriptaddr} ab.txt" in tryboot_cmd
    and "env import -t ${scriptaddr} ${filesize}" in tryboot_cmd
    and "itest.l *0xff910208 == 0x5442" in tryboot_cmd
    and "mw.l 0xff910208 0 1" in tryboot_cmd
    and "setenv fdtfile" not in tryboot_cmd
    and "booti" not in tryboot_cmd,
    tryboot_cmd,
)
check(
    "  a trial boots the slot that is not the default, and falls back to the default if it returns",
    tryboot_cmd.index("setenv irate_slot ${irate_other}") > tryboot_cmd.index("itest.l")
    and 'if test "${irate_slot}" != "${irate_default}"' in tryboot_cmd
    and tryboot_cmd.count("source ${scriptaddr}") == 2,
    tryboot_cmd,
)
check(
    "  the plan's bootstate is FAT for this route (U-Boot reads ab.txt, Linux writes it)",
    hardening.plan()["partitions"][0] == {"name": "bootstate", "size": "16 MiB", "filesystem": "vfat"},
    hardening.plan()["partitions"][0],
)
(dt / "model").write_bytes(b"Rockchip RK3588 EVB\0")
check("a Rockchip board that is not a Lyra is not offered the Lyra boot script", hardening.board() == "other")
try:
    hardening.arm_tryboot()
    check("  and arming the one-shot flag there is refused, before /dev/mem", False)
except Refused as exc:
    check(
        "  and arming the one-shot flag there is refused, before /dev/mem",
        exc.message["id"] == "harden.ab.tryboot.not-lyra" and not devmem.exists(),
    )
(dt / "model").write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
hardening.set_ab("tryboot")
check(
    "  the Pi's tryboot writes its tryboot configuration instead",
    (etc / "ab" / "tryboot.txt").is_file() and not (etc / "ab" / "boot.cmd").exists(),
    list((etc / "ab").iterdir()),
)
check(
    "the plan lays out system, bootstate, state and content, twice for A/B",
    [p["name"] for p in hardening.plan()["partitions"]] == ["bootstate", "system-a", "system-b", "state", "content"]
    and hardening.plan()["partitions"][-2]["filesystem"] == "btrfs"
    and hardening.plan()["partitions"][-1]["filesystem"] == "f2fs",
    hardening.plan(),
)
found = {f["id"]: f for f in hardening.findings()}
check(
    "Box doctor: the filesystem choice is a warning until a fresh install, A/B says it waits for the board",
    found["hardening-filesystem"]["status"] == "warn"
    and "harden.finding.fs.chosen" in message_ids(found["hardening-filesystem"])
    and found["hardening-ab"]["status"] in ("ok", "warn"),
    found,
)
hardening.set_filesystem("ext4")
hardening.set_ab("off")
check(
    "switching A/B off removes only irate-box's boot files",
    not (etc / "ab").exists() and hardening.ab() == "off",
    list(etc.iterdir()),
)

# The Recovery page's choices go through the root helper's one repair route.
check(
    "the Recovery choices are accepted by the Box doctor's repair check",
    health.CHOICE_RE.match("card-set:filesystem:btrfs")
    and health.CHOICE_RE.match("card-set:ab:tryboot")
    and not health.CHOICE_RE.match("card-set:ab:never"),
)
said = health.fix("card-set:filesystem:f2fs")
check(
    "  health.fix records the filesystem",
    said.get("id") == "harden.fs.set" and hardening.filesystem() == "f2fs",
    said,
)
said = health.fix("card-set:ab:tryboot")
check("  health.fix prepares the A/B route", said.get("id") == "harden.ab.tryboot.pi", said)
groups = {g["key"]: g for g in health.hardening_groups()}
check(
    "  and the Recovery page's groups show the new values",
    groups["filesystem"]["current"] == "f2fs" and groups["ab"]["current"] == "tryboot",
    groups,
)
check(
    "a Box doctor check adds the two findings",
    {f["id"] for f in health.check_hardening()} == {"hardening-filesystem", "hardening-ab"},
)
from irate_box.hub import server

choices = [o["choice"] for g in health.card_groups() + health.hardening_groups() for o in g["options"]]
check(
    "every choice the page offers is accepted by the hub's health check",
    all(server.HEALTH_CHOICE_RE.match(c) for c in choices),
    [c for c in choices if not server.HEALTH_CHOICE_RE.match(c)],
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
