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


def _uboot_boot_cmd():
    return (
        "# RAUC's U-Boot script, adapted for the Lyra Zero W (notes: 2026-10-29-bootloader-bootcount).\n"
        "# The Lyra boots zImage with bootz, and needs its device tree and initrd (the board's own /boot/boot.cmd).\n"
        "# It needs U-Boot rebuilt with a persistent redundant environment (CONFIG_ENV_IS_IN_MMC,\n"
        "# CONFIG_SYS_REDUNDAND_ENVIRONMENT), CONFIG_CMD_SAVEENV, and, for a failed boot that never reaches\n"
        "# userspace, CONFIG_BOOTCOUNT_LIMIT with CONFIG_BOOTCOUNT_ENV. Build with mkimage -T script.\n"
        'if test "${BOOT_ORDER}" = ""; then setenv BOOT_ORDER "A B"; fi\n'
        'if test "${BOOT_A_LEFT}" = ""; then setenv BOOT_A_LEFT 3; fi\n'
        'if test "${BOOT_B_LEFT}" = ""; then setenv BOOT_B_LEFT 3; fi\n'
        'if test -z "${fdtfile}"; then setenv fdtfile rockchip/rk3506-luckfox-lyra-zero-w.dtb; fi\n'
        "for BOOT_SLOT in ${BOOT_ORDER}; do\n"
        '  if test "x${BOOT_SLOT}" = "xA" && test ${BOOT_A_LEFT} -gt 0; then\n'
        "    if test ${BOOT_A_LEFT} = 3; then setenv BOOT_A_LEFT 2; elif test ${BOOT_A_LEFT} = 2; then "
        "setenv BOOT_A_LEFT 1; else setenv BOOT_A_LEFT 0; fi\n"
        "    saveenv\n"
        "    if part number mmc 0 irate-system-a BOOT_PART; then\n"
        "      load mmc 0:${BOOT_PART} ${kernel_addr_r} /boot/zImage &&\n"
        "      load mmc 0:${BOOT_PART} ${fdt_addr_r} /boot/dtb/${fdtfile} &&\n"
        "      load mmc 0:${BOOT_PART} ${ramdisk_addr_r} /boot/uInitrd &&\n"
        '      setenv bootargs "root=/dev/disk/by-partlabel/irate-system-a" &&\n'
        "      bootz ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}\n"
        "    fi\n"
        '  elif test "x${BOOT_SLOT}" = "xB" && test ${BOOT_B_LEFT} -gt 0; then\n'
        "    if test ${BOOT_B_LEFT} = 3; then setenv BOOT_B_LEFT 2; elif test ${BOOT_B_LEFT} = 2; then "
        "setenv BOOT_B_LEFT 1; else setenv BOOT_B_LEFT 0; fi\n"
        "    saveenv\n"
        "    if part number mmc 0 irate-system-b BOOT_PART; then\n"
        "      load mmc 0:${BOOT_PART} ${kernel_addr_r} /boot/zImage &&\n"
        "      load mmc 0:${BOOT_PART} ${fdt_addr_r} /boot/dtb/${fdtfile} &&\n"
        "      load mmc 0:${BOOT_PART} ${ramdisk_addr_r} /boot/uInitrd &&\n"
        '      setenv bootargs "root=/dev/disk/by-partlabel/irate-system-b" &&\n'
        "      bootz ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}\n"
        "    fi\n"
        "  fi\n"
        "done\n"
        "# No slot confirmed: the rescue system, or the last slot that booted (notes: nand-recovery-os-plan).\n"
        'setenv bootargs "root=/dev/disk/by-partlabel/irate-system-a"\n'
        "bootz ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}\n"
    )


def _lyra_tryboot_cmd(reg):
    return (
        "# A one-shot tryboot in the Lyra's boot script (notes: 2026-10-29-bootloader-bootcount, Option H).\n"
        f"# The register {reg} must be a spare PMU GRF register that survives a warm reset and is cleared on a power\n"
        "# cut. Confirm it on the board before trusting this: read it at the U-Boot prompt (md.l), reboot, read it\n"
        "# again, then power-cycle and read it again. Never use 0xff910200: U-Boot clears it before this runs.\n"
        "# The default slot is ab_default in armbianEnv.txt, which Linux writes.\n"
        'if test "${ab_default}" = "B"; then setenv tryboot_slot B; else setenv tryboot_slot A; fi\n'
        f"if itest.l *{reg} == 0x5442; then\n"
        f"  mw.l {reg} 0 1\n"
        "  setenv tryboot_slot B\n"
        "fi\n"
        'if test "${fdtfile}" = ""; then setenv fdtfile rockchip/rk3506-luckfox-lyra-zero-w.dtb; fi\n'
        "if part number mmc 0 irate-system-${tryboot_slot} BOOT_PART; then\n"
        "  load mmc 0:${BOOT_PART} ${kernel_addr_r} /boot/zImage &&\n"
        "  load mmc 0:${BOOT_PART} ${fdt_addr_r} /boot/dtb/${fdtfile} &&\n"
        "  load mmc 0:${BOOT_PART} ${ramdisk_addr_r} /boot/uInitrd &&\n"
        '  setenv bootargs "root=/dev/disk/by-partlabel/irate-system-${tryboot_slot}" &&\n'
        "  bootz ${kernel_addr_r} ${ramdisk_addr_r} ${fdt_addr_r}\n"
        "fi\n"
    )


def _pi_tryboot_txt():
    return (
        "# irate-box (A/B updates): the Pi firmware's one-shot tryboot configuration. The switch writes the slot's\n"
        "# kernel and cmdline here, then `reboot '0 tryboot'`; the firmware falls back to config.txt if it fails.\n"
        "# See 2026-10-29-bootloader-bootcount, option F. This file is a template: a fresh install fills the paths.\n"
        "[all]\n# kernel=slot-b/Image\n# cmdline=slot-b/cmdline.txt\n"
    )


def _fw_env_example():
    return (
        "# irate-box (A/B updates): where U-Boot's saved environment lives.\n"
        "# Nothing here is active until the offset is confirmed on the board (notes:\n"
        "# 2026-10-29-bootloader-bootcount). Copying this file as it stands does nothing.\n"
        "# device offset size\n# /dev/disk/by-partlabel/irate-bootstate 0x00000000 0x2000\n"
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
        "The default slot is ab_default in armbianEnv.txt.",
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


def set_tryboot_reg(value):
    """Record the spare PMU GRF register the Lyra's tryboot uses. U-Boot's own boot-mode register is refused."""
    if not re.fullmatch(r"0x[0-9a-fA-F]{1,8}", value or "") or int(value, 16) == 0 or value.lower() == "0xff910200":
        raise Refused(M("harden.ab.reg.invalid", "{value} is not a spare PMU GRF register.", value=value))
    data = state()
    data["tryboot_reg"] = value
    _save(data)
    return M("harden.ab.reg.set", "The tryboot register is {reg}.", reg=value)


def set_default(slot):
    """The default slot, in armbianEnv.txt, which Linux writes and the boot script reads."""
    if slot not in ("A", "B"):
        raise Refused(M("harden.ab.default.invalid", "The default slot is A or B."))
    env = Path(os.environ.get("HUB_BOOT_DIR", "/boot")) / "armbianEnv.txt"
    if not env.exists():
        raise Refused(M("harden.ab.default.no-env", "There is no armbianEnv.txt on this box."))
    lines = [line for line in env.read_text().splitlines() if not line.startswith("ab_default=")]
    lines.append(f"ab_default={slot}")
    safeio.write_checked(env, "\n".join(lines) + "\n", 0o644)
    return M("harden.ab.default.set", "The default slot is {slot}.", slot=slot)


def arm_tryboot():
    """Set the one-shot flag for the next boot, in the recorded PMU GRF register. Requires /dev/mem and root."""
    reg = state().get("tryboot_reg")
    if not reg:
        raise Refused(M("harden.ab.reg.missing", "Record the tryboot register first."))
    address = int(reg, 16)
    try:
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC | os.O_CLOEXEC)
    except OSError as exc:
        raise Refused(M("harden.ab.mem", "The one-shot flag could not be written: {why}", why=str(exc))) from None
    try:
        import mmap

        page = address & ~0xFFF
        page_map = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        try:
            page_map[address - page : address - page + 4] = (0x5442).to_bytes(4, "little")
        finally:
            page_map.close()
    finally:
        os.close(fd)
    return M("harden.ab.tryboot.armed", "The next boot tries slot B once, then falls back to the default.")


def status():
    return {
        "board": board(),
        "filesystem": filesystem(),
        "ab": ab(),
        "artifacts": sorted(p.name for p in AB_DIR.iterdir() if p.is_file()) if AB_DIR.is_dir() else [],
        "tools": {tool: bool(shutil.which(tool)) for tool in ("fw_printenv", "fw_setenv", "rauc", "mkimage")},
    }


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
            {"name": "bootstate", "size": "16 MiB", "filesystem": "raw"},
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
    else:
        raise SystemExit(
            "usage: hardening status | plan | filesystem VALUE | ab VALUE | prepare [VALUE] | tryboot-reg ADDRESS | "
            "default A|B | tryboot"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
