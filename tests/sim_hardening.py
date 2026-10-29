# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The invasive hardening options (root/hardening.py; notes: invasive-hardening-plan stages 6 and 7), offline: the
two choices and their current values, a filesystem recorded, the U-Boot/RAUC and tryboot boot files written, the
plan's partitions, the Box doctor findings, and the Recovery choices through health.fix.
python3 tests/sim_hardening.py"""

import json
import os
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
os.environ.update(HUB_ETC_DIR=str(etc), HUB_DEVICE_TREE=str(dt), HUB_BOOT_DIR=str(boot))
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
    and "zImage" in (etc / "ab" / "boot.cmd").read_text()
    and "bootz" in (etc / "ab" / "boot.cmd").read_text()
    and "booti" not in (etc / "ab" / "boot.cmd").read_text()
    and "setenv BOOT_A_LEFT 2" in (etc / "ab" / "boot.cmd").read_text(),
    (said, artifacts),
)
env_example = (etc / "ab" / "fw_env.config.example").read_text().splitlines()
check(
    "  the environment example has no active device line",
    all(not line or line.startswith("#") for line in env_example),
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
hardening.set_tryboot_reg("0xff910208")
said = hardening.set_default("B")
check(
    "  the default slot is written where the boot script reads it",
    said.get("id") == "harden.ab.default.set" and "ab_default=B" in (boot / "armbianEnv.txt").read_text(),
    said,
)
said = hardening.set_ab("tryboot")
check(
    "  with a spare register, the boot script uses zImage/bootz and the one-shot flag",
    said.get("id") == "harden.ab.tryboot.lyra"
    and "itest.l *0xff910208" in (etc / "ab" / "boot.cmd").read_text()
    and "zImage" in (etc / "ab" / "boot.cmd").read_text()
    and "bootz" in (etc / "ab" / "boot.cmd").read_text()
    and "booti" not in (etc / "ab" / "boot.cmd").read_text(),
    said,
)
(dt / "model").write_bytes(b"Rockchip RK3588 EVB\0")
check("a Rockchip board that is not a Lyra is not offered the Lyra boot script", hardening.board() == "other")
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
