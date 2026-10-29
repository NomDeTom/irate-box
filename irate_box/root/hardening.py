# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The invasive hardening options, offered but never applied silently: a checksumming filesystem, and A/B updates.

Stage 6 (notes: invasive-hardening-plan) lays a card out as system, state and content. The state can be btrfs (data
checksums, about ext4's writes) and the content F2FS (about half ext4's writes, no data checksums). Stage 7 keeps two
system slots: the inactive one is patched and checked, then switched to; a boot chain that counts or tries once
falls back.

Nothing here repartitions a running card or reboots a box. The choice is recorded in /etc/hub/hardening.json, the
boot files for the chosen A/B route are written under /etc/hub/ab, and a fresh install or an offline repartition
lays the partitions out. The boot-chain details are in the vault:
  - 2026-10-29-bootloader-bootcount (U-Boot with a redundant environment and RAUC; the Pi's tryboot; the Lyra's
    one-shot tryboot in its boot script)
  - ab-slot-updates-lyra (patch the inactive slot; 84 MB against 1.5 GB for a whole slot)

  irate-box hardening status
  irate-box hardening plan
  irate-box hardening filesystem ext4|btrfs|f2fs|btrfs+f2fs
  irate-box hardening ab off|tryboot|uboot
  irate-box hardening tryboot-reg 0xADDRESS
  irate-box hardening default A|B
  irate-box hardening tryboot
"""

import json
import os
import re
import shutil
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import sys
from pathlib import Path

from irate_box.finding import finding
from irate_box.msg import M, Refused
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = ETC / "hardening.json"
AB_DIR = ETC / "ab"
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
FILESYSTEMS = ("ext4", "btrfs", "f2fs", "btrfs+f2fs")
AB_MODES = ("off", "tryboot", "uboot")
DEFAULT = {"filesystem": "ext4", "ab": "off"}
# The tryboot route's default slot: ab.txt on the bootstate partition (FAT for this route), which U-Boot reads and
# Linux writes whichever slot is running.
AB_STATE_DIR = Path(os.environ.get("HUB_AB_STATE_DIR", "/mnt/irate-bootstate"))
DEV_MEM = os.environ.get("HUB_DEV_MEM", "/dev/mem")
BOOTSTATE_DEVICE = Path(os.environ.get("HUB_BOOTSTATE_DEVICE", "/dev/disk/by-partlabel/irate-bootstate"))
CMDLINE = Path(os.environ.get("HUB_PROC_CMDLINE", "/proc/cmdline"))
# The one-shot register: a word in the 4 KiB page of the RK3506's PMU GRF that holds U-Boot's own boot-mode register
# (0xff910200, which U-Boot clears before the script runs). Narrower than the block until the TRM is read.
REG_PAGE = 0xFF910000
BOOT_MODE_REG = 0xFF910200
TRYBOOT_MAGIC = 0x5442


def run(*cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def board():
    """lyra, pi or other, from the device tree's model."""
    try:
        model = (DT / "model").read_bytes().rstrip(b"\0\n").decode(errors="replace")
    except OSError:
        return "other"
    low = model.lower()
    if "lyra" in low or "luckfox" in low:
        return "lyra"
    if "raspberry pi" in low:
        return "pi"
    return "other"


def state():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        data = {}
    out = dict(DEFAULT)
    if isinstance(data, dict):
        if data.get("filesystem") in FILESYSTEMS:
            out["filesystem"] = data["filesystem"]
        if data.get("ab") in AB_MODES:
            out["ab"] = data["ab"]
        if isinstance(data.get("tryboot_reg"), str) and re.fullmatch(r"0x[0-9a-fA-F]{1,8}", data["tryboot_reg"]):
            out["tryboot_reg"] = data["tryboot_reg"]
    return out


def _save(data):
    safeio.mkdir(ETC, mode=0o755)
    safeio.write_checked(STATE, json.dumps(data, indent=1, sort_keys=True) + "\n", 0o644)


def filesystem():
    return state()["filesystem"]


def ab():
    return state()["ab"]


def _filesystem_label(value):
    return {
        "ext4": M("harden.fs.ext4", "ext4 (now)"),
        "btrfs": M("harden.fs.btrfs", "btrfs for the state"),
        "f2fs": M("harden.fs.f2fs", "F2FS for the content"),
        "btrfs+f2fs": M("harden.fs.both", "btrfs state, F2FS content"),
    }[value]


def _ab_label(value):
    return {
        "off": M("harden.ab.off", "Off"),
        "tryboot": M("harden.ab.tryboot", "One-shot tryboot"),
        "uboot": M("harden.ab.uboot", "U-Boot and RAUC"),
    }[value]


def groups():
    """The two choices, in the Box doctor's setting-group shape (the Recovery page draws them as chips)."""
    return [
        {
            "key": "filesystem",
            "label": M("harden.fs.title", "Filesystem for a fresh install"),
            "desc": M(
                "harden.fs.desc",
                "This install runs on ext4. btrfs checksums the state's data, so a damaged read is an error at once. "
                "F2FS writes about half as much for content. A fresh install or an offline repartition applies it.",
            ),
            "current": filesystem(),
            "options": [
                {
                    "value": value,
                    "label": _filesystem_label(value),
                    "choice": f"card-set:filesystem:{value}",
                    **(
                        {
                            "confirm": M(
                                "harden.fs.confirm",
                                "Record {label} for a fresh install or an offline repartition? Nothing on this card "
                                "changes now.",
                                label=_filesystem_label(value),
                            )
                        }
                        if value != "ext4"
                        else {}
                    ),
                }
                for value in FILESYSTEMS
            ],
        },
        {
            "key": "ab",
            "label": M("harden.ab.title", "A/B system updates"),
            "desc": M(
                "harden.ab.desc",
                "Two system slots. An update patches the inactive one, checks it, then switches to it, and a failed "
                "boot falls back. The Lyra can use U-Boot with RAUC or a one-shot tryboot in its boot script. The Pi "
                "can use its firmware tryboot or U-Boot. The boot files are written to {dir}. Partitions and slots "
                "are made by a fresh install.",
                dir=str(AB_DIR),
            ),
            "current": ab(),
            "options": [
                {
                    "value": value,
                    "label": _ab_label(value),
                    "choice": f"card-set:ab:{value}",
                    **(
                        {
                            "confirm": M(
                                "harden.ab.confirm",
                                "Prepare {label}? This writes boot files under {dir}. It does not repartition or "
                                "reboot now.",
                                label=_ab_label(value),
                                dir=str(AB_DIR),
                            )
                        }
                        if value != "off"
                        else {}
                    ),
                }
                for value in AB_MODES
            ],
        },
    ]


def set_filesystem(value):
    if value not in FILESYSTEMS:
        raise Refused(M("harden.fs.unknown", "{value} is not a filesystem this box offers.", value=value))
    data = state()
    data["filesystem"] = value
    _save(data)
    if value == "ext4":
        return M("harden.fs.set.ext4", "A fresh install will use ext4, as this one does.")
    return M(
        "harden.fs.set",
        "{label} is recorded for a fresh install or an offline repartition. This card is unchanged.",
        label=_filesystem_label(value),
    )


def _system_conf():
    return (
        "# irate-box (A/B updates): RAUC's system description, written by irate-box. The slots are made by a fresh\n"
        "# install or an offline repartition; the boot chain follows 2026-10-29-bootloader-bootcount.\n"
        "[system]\ncompatible=irate-box\nbootloader=uboot\nmountprefix=/mnt/rauc\n\n"
        "[slot.system.0]\ndevice=/dev/disk/by-partlabel/irate-system-a\ntype=ext4\nbootname=A\n\n"
        "[slot.system.1]\ndevice=/dev/disk/by-partlabel/irate-system-b\ntype=ext4\nbootname=B\n"
    )


HAND_OVER = (
    "  if part number mmc 0 irate-system-${irate_slot} BOOT_PART; then\n"
    "    setenv devtype mmc\n"
    "    setenv devnum 0:${BOOT_PART}\n"
    "    setenv prefix /boot/\n"
    "    if load mmc 0:${BOOT_PART} ${scriptaddr} /boot/boot.scr; then source ${scriptaddr}; fi\n"
    "  fi\n"
)
SLOT_NOTES = (
    "# Each slot boots through its own Armbian boot.scr: devnum is set to the slot's partition, so that script reads\n"
    "# the slot's armbianEnv.txt (its fdtfile, overlays and console) and loads its zImage, uInitrd and device tree.\n"
    "# Each slot's armbianEnv.txt must carry rootdev=PARTLABEL=irate-system-a (or -b). A slot whose script returns\n"
    "# did not boot, and the next is tried. boot.scr is loaded at scriptaddr, clear of the load_addr it uses.\n"
)


def _uboot_boot_cmd():
    return (
        "# RAUC's U-Boot script for the Lyra Zero W (notes: 2026-10-29-bootloader-bootcount).\n"
        + SLOT_NOTES
        + "# It needs U-Boot rebuilt with a persistent redundant environment (CONFIG_ENV_IS_IN_MMC,\n"
        "# CONFIG_SYS_REDUNDAND_ENVIRONMENT), CONFIG_CMD_SAVEENV, and, for a failed boot that never reaches\n"
        "# userspace, CONFIG_BOOTCOUNT_LIMIT with CONFIG_BOOTCOUNT_ENV. Build with mkimage -T script.\n"
        'if test "${BOOT_ORDER}" = ""; then setenv BOOT_ORDER "A B"; fi\n'
        'if test "${BOOT_A_LEFT}" = ""; then setenv BOOT_A_LEFT 3; fi\n'
        'if test "${BOOT_B_LEFT}" = ""; then setenv BOOT_B_LEFT 3; fi\n'
        "for BOOT_SLOT in ${BOOT_ORDER}; do\n"
        "  setenv irate_slot\n"
        '  if test "x${BOOT_SLOT}" = "xA" && test ${BOOT_A_LEFT} -gt 0; then\n'
        "    if test ${BOOT_A_LEFT} = 3; then setenv BOOT_A_LEFT 2; elif test ${BOOT_A_LEFT} = 2; then "
        "setenv BOOT_A_LEFT 1; else setenv BOOT_A_LEFT 0; fi\n"
        "    setenv irate_slot a\n"
        '  elif test "x${BOOT_SLOT}" = "xB" && test ${BOOT_B_LEFT} -gt 0; then\n'
        "    if test ${BOOT_B_LEFT} = 3; then setenv BOOT_B_LEFT 2; elif test ${BOOT_B_LEFT} = 2; then "
        "setenv BOOT_B_LEFT 1; else setenv BOOT_B_LEFT 0; fi\n"
        "    setenv irate_slot b\n"
        "  fi\n"
        '  if test -n "${irate_slot}"; then\n'
        "    saveenv\n" + HAND_OVER.replace("\n  ", "\n    ").replace("  if part", "    if part", 1) + "  fi\n"
        "done\n"
        'echo "irate-box: no slot booted (A ${BOOT_A_LEFT} left, B ${BOOT_B_LEFT} left)"\n'
    )


def _lyra_tryboot_cmd(reg):
    return (
        "# A one-shot tryboot in the Lyra's boot script (notes: 2026-10-29-bootloader-bootcount, Option H).\n"
        f"# The register {reg} must be a spare PMU GRF register that survives a warm reset and is cleared on a power\n"
        "# cut. Confirm it on the board before trusting this: read it at the U-Boot prompt (md.l), reboot, read it\n"
        "# again, then power-cycle and read it again. Never use 0xff910200: U-Boot clears it before this runs.\n"
        "# The default slot is ab_default in ab.txt on the bootstate partition (FAT), which Linux writes whichever\n"
        "# slot runs. A trial boots the other slot once; if its boot returns, the default is booted.\n"
        + SLOT_NOTES
        + "setenv ab_default A\n"
        "if part number mmc 0 irate-bootstate STATE_PART; then\n"
        "  if load mmc 0:${STATE_PART} ${scriptaddr} ab.txt; then env import -t ${scriptaddr} ${filesize}; fi\n"
        "fi\n"
        'if test "${ab_default}" = "B"; then setenv irate_default b; setenv irate_other a; '
        "else setenv irate_default a; setenv irate_other b; fi\n"
        "setenv irate_slot ${irate_default}\n"
        f"if itest.l *{reg} == 0x{TRYBOOT_MAGIC:x}; then\n"
        f"  mw.l {reg} 0 1\n"
        "  setenv irate_slot ${irate_other}\n"
        "fi\n"
        + HAND_OVER.replace("\n  ", "\n").replace("  if part", "if part", 1)
        + 'if test "${irate_slot}" != "${irate_default}"; then\n'
        "  setenv irate_slot ${irate_default}\n" + HAND_OVER + "fi\n"
        'echo "irate-box: no slot booted"\n'
    )


def _pi_tryboot_txt():
    return (
        "# irate-box (A/B updates): the Pi firmware's one-shot tryboot configuration. The switch writes the slot's\n"
        "# kernel and cmdline here, then `reboot '0 tryboot'`; the firmware falls back to config.txt if it fails.\n"
        "# See 2026-10-29-bootloader-bootcount, option F. This file is a template: a fresh install fills the paths.\n"
        "[all]\n# kernel=slot-b/Image\n# cmdline=slot-b/cmdline.txt\n"
    )


def _env_offsets():
    """U-Boot puts the environment at the top of the bootstate partition: the last 8 KiB, and the copy before it."""
    size = 16 << 20
    said = run("blockdev", "--getsize64", str(BOOTSTATE_DEVICE), timeout=30)
    if said.returncode == 0 and said.stdout.strip().isdigit():
        size = int(said.stdout.strip())
    return size - 0x2000, size - 0x4000


def _fw_env_example():
    first, second = _env_offsets()
    return (
        "# irate-box (A/B updates): where U-Boot's saved environment lives.\n"
        "# Nothing here is active until the offset is confirmed on the board (notes:\n"
        "# 2026-10-29-bootloader-bootcount). Copying this file as it stands does nothing.\n"
        "# With the partition named in the device tree, U-Boot keeps the environment at its top.\n"
        "# device offset size\n"
        f"# {BOOTSTATE_DEVICE} 0x{first:08X} 0x2000\n"
        f"# {BOOTSTATE_DEVICE} 0x{second:08X} 0x2000\n"
    )


def prepare(value=None):
    """Write the pieces the chosen A/B route needs, without touching a boot chain or a partition. Returns what is
    ready and what remains."""
    value = value or ab()
    if value == "off":
        shutil.rmtree(AB_DIR, ignore_errors=True)
        return M("harden.ab.cleared", "The A/B boot files irate-box wrote are gone. Partitions are not changed.")
    if value not in AB_MODES:
        raise Refused(M("harden.ab.unknown", "{value} is not an A/B route this box offers.", value=value))
    shutil.rmtree(AB_DIR, ignore_errors=True)
    AB_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    written = []
    if value == "uboot":
        for name, text in (
            ("system.conf", _system_conf()),
            ("boot.cmd", _uboot_boot_cmd()),
            ("fw_env.config.example", _fw_env_example()),
        ):
            safeio.write_checked(AB_DIR / name, text, 0o600)
            written.append(name)
        missing = [tool for tool in ("fw_printenv", "fw_setenv", "rauc", "mkimage") if not shutil.which(tool)]
        return M(
            "harden.ab.uboot.ready",
            "Wrote {files} under {dir}. Rebuild U-Boot with the environment and bootcount, put its config in "
            "/etc/fw_env.config, build boot.cmd with mkimage, install RAUC, then make the slots with a fresh install. "
            "Missing tools: {missing}.",
            files=", ".join(written),
            dir=str(AB_DIR),
            missing=", ".join(missing) or "none",
        )
    if board() == "pi":
        for name, text in (("tryboot.txt", _pi_tryboot_txt()),):
            safeio.write_checked(AB_DIR / name, text, 0o600)
            written.append(name)
        return M(
            "harden.ab.tryboot.pi",
            "Wrote {files} under {dir}. Test tryboot on the 3B+ first (one reboot), then a fresh install fills the "
            "slots and the switch writes the inactive slot before `reboot '0 tryboot'`.",
            files=", ".join(written),
            dir=str(AB_DIR),
        )
    reg = state().get("tryboot_reg")
    if not reg:
        return M(
            "harden.ab.tryboot.lyra.waiting",
            "The tryboot route is recorded, and its boot script is not written yet: record a spare PMU GRF register "
            "first. Nothing on this card changed.",
        )
    safeio.write_checked(AB_DIR / "boot.cmd", _lyra_tryboot_cmd(reg), 0o600)
    return M(
        "harden.ab.tryboot.lyra",
        "Wrote boot.cmd under {dir} with register {reg}. Build it as boot.scr, then test the trial and the power cut. "
        "The default slot is ab_default in ab.txt on the bootstate partition, and each slot's armbianEnv.txt "
        "names its own root.",
        dir=str(AB_DIR),
        reg=reg,
    )


def set_ab(value):
    if value not in AB_MODES:
        raise Refused(M("harden.ab.unknown", "{value} is not an A/B route this box offers.", value=value))
    said = prepare(value)
    data = state()
    data["ab"] = value
    _save(data)
    return said


def _reg_ok(value):
    """A 4-byte-aligned word in REG_PAGE that is not U-Boot's own boot-mode register."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{1,8}", value or ""):
        return False
    address = int(value, 16)
    return address % 4 == 0 and REG_PAGE <= address < REG_PAGE + 0x1000 and address != BOOT_MODE_REG


def set_tryboot_reg(value):
    """Record the spare PMU GRF register the Lyra's tryboot uses: a word in the page that holds U-Boot's boot-mode
    register, which itself is refused."""
    if not _reg_ok(value):
        raise Refused(
            M(
                "harden.ab.reg.invalid",
                "{value} is not a spare PMU GRF register: a 4-byte-aligned address from 0xff910000 to 0xff910ffc, "
                "other than 0xff910200.",
                value=value,
            )
        )
    data = state()
    data["tryboot_reg"] = value.lower()
    _save(data)
    return M("harden.ab.reg.set", "The tryboot register is {reg}.", reg=value.lower())


def set_default(slot):
    """The default slot: ab.txt on the bootstate partition, which the boot script reads whichever slot runs."""
    if slot not in ("A", "B"):
        raise Refused(M("harden.ab.default.invalid", "The default slot is A or B."))
    mounted = AB_STATE_DIR.is_dir() and (os.environ.get("HUB_AB_STATE_DIR") or os.path.ismount(AB_STATE_DIR))
    if not mounted:
        raise Refused(
            M(
                "harden.ab.default.no-state",
                "The bootstate partition is not mounted at {dir}, so the default slot cannot be written.",
                dir=str(AB_STATE_DIR),
            )
        )
    safeio.write_checked(AB_STATE_DIR / "ab.txt", f"ab_default={slot}\n", 0o644)
    return M("harden.ab.default.set", "The default slot is {slot}.", slot=slot)


def arm_tryboot():
    """Set the one-shot flag for the next boot, in the recorded PMU GRF register: only on a Lyra, only a register
    _reg_ok takes. Requires /dev/mem and root."""
    if board() != "lyra":
        raise Refused(M("harden.ab.tryboot.not-lyra", "The one-shot tryboot is for the Lyra: this board has none."))
    reg = state().get("tryboot_reg")
    if not reg:
        raise Refused(M("harden.ab.reg.missing", "Record the tryboot register first."))
    if not _reg_ok(reg):
        raise Refused(M("harden.ab.reg.invalid-recorded", "The recorded register {reg} is not a spare one.", reg=reg))
    address = int(reg, 16)
    try:
        fd = os.open(DEV_MEM, os.O_RDWR | os.O_SYNC | os.O_CLOEXEC)
    except OSError as exc:
        raise Refused(M("harden.ab.mem", "The one-shot flag could not be written: {why}", why=str(exc))) from None
    try:
        import mmap

        page = address & ~0xFFF
        page_map = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        try:
            page_map[address - page : address - page + 4] = TRYBOOT_MAGIC.to_bytes(4, "little")
        finally:
            page_map.close()
    finally:
        os.close(fd)
    return M("harden.ab.tryboot.armed", "The next boot tries the other slot once, then falls back to the default.")


def running_slot():
    """a, b or "": the slot the running system came from, from the kernel's root=."""
    try:
        text = CMDLINE.read_text()
    except OSError:
        return ""
    found = re.search(r"root=(?:/dev/disk/by-partlabel/|PARTLABEL=)irate-system-([ab])\b", text)
    return found.group(1) if found else ""


def default_slot():
    """a, b or "": ab_default in ab.txt on the bootstate partition."""
    try:
        found = re.search(r"^ab_default=([AB])$", (AB_STATE_DIR / "ab.txt").read_text(), re.M)
    except OSError:
        return ""
    return found.group(1).lower() if found else ""


def armed():
    """Whether the one-shot flag in the recorded register is set. Reads /dev/mem; never writes."""
    reg = state().get("tryboot_reg")
    if not reg or not _reg_ok(reg):
        return False
    try:
        fd = os.open(DEV_MEM, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        return int.from_bytes(os.pread(fd, 4, int(reg, 16)), "little") == TRYBOOT_MAGIC
    except OSError:
        return False
    finally:
        os.close(fd)


def status():
    return {
        "board": board(),
        "filesystem": filesystem(),
        "ab": ab(),
        "running": running_slot(),
        "default": default_slot(),
        "armed": armed(),
        "artifacts": sorted(p.name for p in AB_DIR.iterdir() if p.is_file()) if AB_DIR.is_dir() else [],
        "tools": {tool: bool(shutil.which(tool)) for tool in ("fw_printenv", "fw_setenv", "rauc", "mkimage")},
    }


def _slot_state(slot):
    try:
        said = (AB_STATE_DIR / f"{slot.lower()}.state").read_text().strip()
    except OSError:
        return "good"
    return said if said in ("good", "bad") else "good"


def rauc_handler(argv):
    """RAUC's custom bootloader handler: get-primary, set-primary SLOT, get-state SLOT, set-state SLOT STATE.
    The default slot and the one-shot trial are the same files and register the boot script reads."""
    command = argv[0] if argv else ""
    if command == "get-primary":
        slot = default_slot()
        if not slot:
            raise SystemExit("hardening rauc: there is no default slot to read")
        print(slot.upper())
        return
    if command == "set-primary" and len(argv) == 2 and argv[1].lower() in ("a", "b"):
        slot = argv[1].lower()
        if not default_slot():
            set_default(slot.upper())
            return
        if slot == default_slot():
            return
        # The trial boots the slot that is not the default: leave the default, arm the new slot once. Only a
        # set-state good while it runs makes it the default.
        if board() != "lyra":
            raise SystemExit("hardening rauc: the Pi's tryboot is not armed by set-primary yet")
        arm_tryboot()
        return
    if command == "get-state" and len(argv) == 2 and argv[1].lower() in ("a", "b"):
        print(_slot_state(argv[1]))
        return
    if command == "set-state" and len(argv) == 3 and argv[1].lower() in ("a", "b") and argv[2] in ("good", "bad"):
        slot = argv[1].lower()
        AB_STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        safeio.write_checked(AB_STATE_DIR / f"{slot}.state", argv[2] + "\n", 0o600)
        if argv[2] == "good" and running_slot() == slot:
            set_default(slot.upper())
        return
    raise SystemExit("usage: hardening rauc get-primary | set-primary A|B | get-state A|B | set-state A|B good|bad")


def plan():
    """Stage 6's layout, in sizes, without writing anything: system (twice with A/B), bootstate, state, content."""
    board_name = board()
    system_gb = 8 if board_name == "pi" else 4
    slots = 2 if ab() != "off" else 1
    return {
        "board": board_name,
        "filesystem": filesystem(),
        "ab": ab(),
        "partitions": [
            {"name": "bootstate", "size": "16 MiB", "filesystem": "vfat" if ab() == "tryboot" else "raw"},
            *[
                {"name": f"system-{chr(ord('a') + i)}", "size": f"{system_gb} GiB", "filesystem": "ext4"}
                for i in range(slots)
            ],
            {"name": "state", "size": "1 GiB", "filesystem": "btrfs" if "btrfs" in filesystem() else "ext4"},
            {"name": "content", "size": "the rest", "filesystem": "f2fs" if "f2fs" in filesystem() else "ext4"},
        ],
        # The inactive slot is patched, not rewritten (measured on 181: 84 MB against 1.5 GB).
        "update": "patch the inactive slot, check it with rsync --checksum, switch, confirm after Box doctor's run",
    }


def findings():
    """Two Box doctor findings: the filesystem recorded, and the A/B route and how ready it is."""
    out = []
    fs = filesystem()
    if fs == "ext4":
        out.append(
            finding(
                "hardening-filesystem",
                M("harden.finding.fs.title", "Filesystem"),
                "ok",
                M(
                    "harden.finding.fs.ext4",
                    "ext4. A checksumming filesystem is offered on Recovery: btrfs for the state, F2FS for content.",
                ),
                notes=[M("harden.finding.fs.ext4.note", "No data checksums now: a damaged read can return quietly.")],
            )
        )
    else:
        out.append(
            finding(
                "hardening-filesystem",
                M("harden.finding.fs.title", "Filesystem"),
                "warn",
                M(
                    "harden.finding.fs.chosen",
                    "{label} is recorded. This install still runs on ext4.",
                    label=_filesystem_label(fs),
                ),
                M(
                    "harden.finding.fs.why",
                    "A fresh install or an offline repartition applies the choice. Until then the state has no data "
                    "checksums and the content writes as ext4 does.",
                ),
                fix=M(
                    "harden.finding.fs.fix", "Lay out a spare card with a fresh install, or run the filesystem trial."
                ),
            )
        )
    mode = ab()
    now = status()
    if mode == "off":
        out.append(
            finding(
                "hardening-ab",
                M("harden.finding.ab.title", "A/B system updates"),
                "ok",
                M("harden.finding.ab.off", "Off. One system slot, updated in place."),
                notes=[M("harden.finding.ab.off.note", "A/B is offered on Recovery, for a fresh install.")],
            )
        )
    else:
        missing = [tool for tool, there in now["tools"].items() if not there and mode == "uboot"]
        status_word = "warn" if missing or not now["artifacts"] else "ok"
        out.append(
            finding(
                "hardening-ab",
                M("harden.finding.ab.title", "A/B system updates"),
                status_word,
                M(
                    "harden.finding.ab.on",
                    "{label} is recorded, with {n} boot file(s) under {dir}.",
                    label=_ab_label(mode),
                    n=len(now["artifacts"]),
                    dir=str(AB_DIR),
                ),
                M(
                    "harden.finding.ab.why",
                    "The slots and the first switch need a fresh install. The boot chain must be confirmed on the board.",
                )
                if status_word != "ok"
                else None,
                notes=[
                    M(
                        "harden.finding.ab.missing",
                        "Missing tools: {tools}.",
                        tools=", ".join(missing) or "none",
                    )
                ]
                if missing
                else [],
                fix=M(
                    "harden.finding.ab.fix",
                    "Follow the boot-chain notes for this board, then test the fallback before an update relies on it.",
                ),
            )
        )
    return out


def main(argv):
    command = argv[0] if argv else "status"
    if os.geteuid() != 0 and command not in ("status", "plan"):
        raise SystemExit("run as root")
    if command == "status":
        print(json.dumps(status(), indent=2))
    elif command == "plan":
        print(json.dumps(plan(), indent=2))
    elif command == "filesystem" and len(argv) == 2:
        print(set_filesystem(argv[1]))
    elif command == "ab" and len(argv) == 2:
        print(set_ab(argv[1]))
    elif command == "prepare":
        print(prepare(argv[1] if len(argv) > 1 else None))
    elif command == "tryboot-reg" and len(argv) == 2:
        print(set_tryboot_reg(argv[1]))
    elif command == "default" and len(argv) == 2:
        print(set_default(argv[1]))
    elif command == "tryboot":
        print(arm_tryboot())
    elif command == "rauc":
        rauc_handler(argv[1:])
    else:
        raise SystemExit(
            "usage: hardening status | plan | filesystem VALUE | ab VALUE | prepare [VALUE] | tryboot-reg ADDRESS | "
            "default A|B | tryboot | rauc get-primary|set-primary|get-state|set-state"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
