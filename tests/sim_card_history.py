# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""SD card writes, recorded and judged (hub/cardhistory.py, root/health.py check_card_writes), offline, against a
made /proc, /sys and cgroup tree: a line an hour with the card's writes since boot and each service's, the hour's
lowest memory and the write settings; the hours from those lines across a hub restart and a reboot; writes per day,
by service, and before and after a setting changed; then the Box doctor's findings (the rate, writes by service
not counted, a journal synced often, an architecture with nothing installed) and its two repairs.
python3 tests/sim_card_history.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="cardhist-"))
PROC, SYS = T / "proc", T / "sys"
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_PROC=str(PROC),
    HUB_SYS=str(SYS),
    HUB_SYSTEMD_ETC=str(T / "systemd"),
    HUB_APT_CONF_D=str(T / "apt.conf.d"),
    HUB_APT_CACHE_DIR=str(T / "apt-cache"),
    HUB_SYSCTL_D=str(T / "sysctl.d"),
    HUB_FSTAB=str(T / "fstab"),
    HUB_RPI_CMDLINE=str(T / "cmdline.txt"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_NAND_STATE=str(T / "nand-logs.conf"),
    HUB_PROC_MTD=str(T / "mtd"),
    HUB_UBI_SYS=str(T / "ubi"),
)
for d in ("state", "etc", "systemd/journald.conf.d", "apt.conf.d", "apt-cache"):
    (T / d).mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import cardhistory
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# The box: / on mmcblk0p2, the card mmcblk0 (179:0), 32 GB.
(PROC / "self").mkdir(parents=True)
(PROC / "self" / "mountinfo").write_text("22 1 179:2 / / rw,relatime shared:1 - ext4 /dev/mmcblk0p2 rw\n")
(PROC / "sys" / "kernel" / "random").mkdir(parents=True)
card = SYS / "devices" / "mmc" / "mmcblk0"
(card / "mmcblk0p2").mkdir(parents=True)
(card / "mmcblk0p2" / "partition").write_text("2\n")
(card / "dev").write_text("179:0\n")
(card / "size").write_text(str(32 * 10**9 // 512) + "\n")
(SYS / "class" / "block").mkdir(parents=True)
(SYS / "class" / "block" / "mmcblk0").symlink_to(card)
(SYS / "class" / "block" / "mmcblk0p2").symlink_to(card / "mmcblk0p2")
cg = SYS / "fs" / "cgroup" / "system.slice"
cg.mkdir(parents=True)


def box(boot, written_mb, units, mem_mb=200, accounting=True, memcg=True):
    """The counters as the kernel would show them now."""
    (SYS / "fs" / "cgroup" / "cgroup.controllers").write_text(
        "cpuset cpu io memory pids\n" if memcg else "cpuset cpu io pids\n"
    )
    (PROC / "sys" / "kernel" / "random" / "boot_id").write_text(boot + "-0000-0000\n")
    sectors = written_mb * 2048
    (card / "stat").write_text(f"1 2 3 4 5 6 {sectors} 8 9 10 11\n")
    (PROC / "meminfo").write_text(f"MemTotal: 475000 kB\nMemAvailable: {mem_mb * 1024} kB\n")
    (cg / "cgroup.subtree_control").write_text("cpu io memory pids\n" if accounting else "cpu memory pids\n")
    for d in cg.glob("*.service"):
        (d / "io.stat").write_text("")
    for unit, mb in units.items():
        (cg / unit).mkdir(exist_ok=True)
        (cg / unit / "io.stat").write_text(f"179:0 rbytes=1 wbytes={mb << 20} rios=1 wios=1\n8:0 wbytes=99\n")


H = 3600
start = 1_793_000_000 - 1_793_000_000 % 86400  # a midnight (UTC), so the days below are whole
level = {"card_writes": "thrifty"}
s = cardhistory.Sampler(lambda: dict(level), trusted=lambda: True)
box("aaaa1111", 100, {"apt-daily.service": 60, "irate-box.service": 10})
check("the first tick writes the first line", s.tick(start + 10) and len(cardhistory.load()) == 1)
check("  not again within the hour", not s.tick(start + 20 * 60))
box("aaaa1111", 140, {"apt-daily.service": 90, "irate-box.service": 15}, mem_mb=150)
s.tick(start + 40 * 60)  # memory sampled between lines: its lowest kept for the line
box("aaaa1111", 180, {"apt-daily.service": 100, "irate-box.service": 20}, mem_mb=180)
s.tick(start + H + 10)
rows = cardhistory.load()
check(
    "a line an hour: the card's and each service's writes since boot, the hour's lowest memory, the settings",
    len(rows) == 2
    and rows[1]["card"] == 180 << 20
    and rows[1]["units"] == {"apt-daily.service": 100 << 20, "irate-box.service": 20 << 20}
    and rows[1]["mem_min"] == 150 << 20
    and rows[1]["settings"] == level
    and rows[1]["accounting"] is True,
    rows,
)
check("  a line is small", len(json.dumps(rows[1])) < 400, len(json.dumps(rows[1])))
# A hub restart: a new sampler goes on from the file; then a reboot, its counts begin again.
s = cardhistory.Sampler(lambda: dict(level), trusted=lambda: True)
box("aaaa1111", 230, {"apt-daily.service": 140, "irate-box.service": 22})
check("after a hub restart: not twice in the hour", s.tick(start + H + 600) is False)
s.tick(start + 2 * H + 10)
box("bbbb2222", 30, {"irate-box.service": 3})
s.tick(start + 3 * H + 10)
hours = cardhistory._hours(cardhistory.load())
check(
    "the hours: differences within a boot, everything since boot after a reboot",
    [h["card"] >> 20 for h in hours] == [100, 80, 50, 30]
    and hours[2]["units"] == {"apt-daily.service": 40 << 20, "irate-box.service": 2 << 20}
    and hours[3]["span"] is None,
    [(h["card"] >> 20, h["units"]) for h in hours],
)
# Two days at the old level, two at the new: 2 GB a day, then 0.5 GB a day.
rows = []
for i in range(4 * 24 + 1):
    rows.append(
        {
            "at": start + i * H,
            "boot": "cccc3333",
            "card": (i * 2048 if i <= 48 else 48 * 2048 + (i - 48) * 512) << 20 >> 5,
            "units": {"apt-daily.service": i << 20},
            "other": 0,
            "accounting": True,
            "mem_min": 100 << 20,
            "settings": {"card_writes": "thrifty" if i <= 48 else "gentle"},
        }
    )
summary = cardhistory.summarize(rows, now=start + 96 * H + 60)
change = summary["changes"][0] if summary["changes"] else {}
check(
    "a setting changed: the daily writes before and after, from 24 hours either side",
    len(summary["changes"]) == 1
    and change["from"] == {"card_writes": "thrifty"}
    and change["to"] == {"card_writes": "gentle"}
    and change["before"] == 24 * (2048 << 20 >> 5)
    and change["after"] == 24 * (512 << 20 >> 5),
    change,
)
check(
    "  writes per day, the last day's, and the services that wrote most this week",
    len(summary["days"]) >= 4
    and summary["last_day"] == 24 * (512 << 20 >> 5)
    and summary["week_units"][0][0] == "apt-daily.service",
    summary,
)
young = cardhistory.summarize(rows[:60], now=start + 59 * H + 60)
check(
    "  until 24 hours after a change are recorded, no figure for after",
    young["changes"] and young["changes"][0]["after"] is None,
)

# The Box doctor. The card's record: three whole days of 2 GB, today's start.
ran = []


def run(*cmd, timeout=60, **kw):
    ran.append(cmd)
    out = ""
    if cmd[:2] == ("dpkg", "--print-foreign-architectures"):
        out = "armhf\n"
    elif cmd[0] == "dpkg-query":
        out = "arm64\nall\narm64\n"
    elif cmd[0] == "apt-config":
        out = 'Acquire::Languages "";\nAcquire::Languages:: "en_GB";\nAcquire::Languages:: "none";\n'
    elif cmd[0] == "bash" and cmd[2] == "check":
        out = 'mtd0 "spi-nand0": 256 MiB raw NAND, erase block 128 KiB\n  free. On it (about 204 MiB usable): ramlog\n'
    elif cmd[0] == "bash" and cmd[2] == "on":
        (T / "nand-logs.conf").write_text("mtd=0\n")
    elif cmd[0] == "bash" and cmd[2] == "off":
        (T / "nand-logs.conf").unlink()
    elif cmd[0] == "systemd-analyze":
        out = "# /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf\n[Journal]\nStorage=volatile\n"
    return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


health.run = run
day0 = start - 3 * 86400
lines = [
    {
        "at": day0 + i * H,
        "boot": "dddd4444",
        "card": i * ((2 << 30) // 24),
        "units": {"meshtasticd.service": i << 26, "apt-daily.service": i << 25},
        "other": 0,
        "accounting": True,
        "mem_min": 100 << 20,
        "settings": {"card_writes": "thrifty"},
    }
    for i in range(3 * 24 + 2)
]
cardhistory.FILE.write_text("".join(json.dumps(r) + "\n" for r in lines))
box("dddd4444", 1, {}, accounting=False)
(T / "systemd" / "journald.conf.d" / "zz-test.conf").write_text("[Journal]\nSyncIntervalSec=5s\n")
(T / "systemd" / "journald.conf.d" / "ok.conf").write_text("[Journal]\nSyncIntervalSec=10min\n")
got = {f["id"]: f for f in health.check_card_writes(now=day0 + 3 * 86400 + 2 * H)}
cw = got.get("card-writes", {})
check(
    "the rate over three whole days: above 1 GB a day, a warning naming the services that wrote most, with the setting",
    cw.get("status") == "warn"
    and cw["evidence"][0].startswith("meshtasticd.service")
    and cw.get("go", {}).get("section") == "card-writes",
    cw,
)
check(
    "writes by service not counted: said, with the repair that switches it on",
    got.get("card-accounting", {}).get("status") == "warn"
    and [a["choice"] for a in got["card-accounting"]["actions"]] == ["card-accounting-on"],
    got.get("card-accounting"),
)
check(
    "a journal drop-in syncing every 5 s: named; one at 10 minutes: not",
    "journal-sync:zz-test.conf" in got and "journal-sync:ok.conf" not in got,
    sorted(got),
)
check(
    "armhf added with nothing installed for it: said, with its removal offered",
    [a["choice"] for a in got.get("arch-unused:armhf", {}).get("actions", [])] == ["arch-remove:armhf"],
    got.get("arch-unused:armhf"),
)
check(
    "the choices pass the doctor's check",
    all(health.CHOICE_RE.match(c) for c in ("card-accounting-on", "arch-remove:armhf")),
)
ran.clear()
said = health.fix("card-accounting-on")
check(
    "Count writes by service: the setting in a file of irate-box's, systemd reloaded and re-executed",
    "DefaultIOAccounting=yes" in health.IO_ACCOUNTING.read_text()
    and ("systemctl", "daemon-reload") in ran
    and ("systemctl", "daemon-reexec") in ran
    and said["id"] == "repair.card-accounting.on",
    ran,
)
ran.clear()
said = health.fix("arch-remove:armhf")
check(
    "Remove armhf: dpkg --remove-architecture",
    ("dpkg", "--remove-architecture", "armhf") in ran and said["id"] == "repair.arch.removed",
    ran,
)
try:
    health.fix("arch-remove:arm64")
    refused = False
except health.Refused:
    refused = True
check("  an architecture that has packages installed: refused", refused)
cardhistory.FILE.write_text("")
box("dddd4444", 1, {}, accounting=True)
check(
    "fewer than three whole days recorded, accounting on: no rate yet, nothing said about accounting",
    not {"card-writes", "card-accounting"} & {f["id"] for f in health.check_card_writes()},
)

# The system's write settings (Recovery → SD card writes): their groups, then each applied and undone.
groups = {g["key"]: g for g in health.card_groups()}
check(
    "the system write settings: apt's cache, package descriptions (this box downloads some), journal, write-back delay",
    list(groups) == ["apt_cache", "apt_languages", "journal", "writeback"]
    and groups["apt_cache"]["current"] == "card"
    and groups["journal"]["current"] == "memory"  # the image keeps it in memory already
    and groups["writeback"]["current"] == "standard",
    {k: g["current"] for k, g in groups.items()},
)
check(
    "  each option's choice passes the doctor's check and the hub's",
    all(
        health.CHOICE_RE.match(o["choice"]) and o["choice"].startswith("card-set:")
        for g in groups.values()
        for o in g["options"]
    ),
)
check("  a value no setting has: refused by the check", not health.CHOICE_RE.match("card-set:apt_cache:disk"))
unit = T / "systemd" / "system" / health.APT_RAM_UNIT
(T / "apt-cache" / "pkgcache.bin").write_text("old")
ran.clear()
health.fix("card-set:apt_cache:memory")
conf = (T / "apt.conf.d" / "50irate-box-cache").read_text()
check(
    "apt's cache in memory: a tmpfs of its own, mounted now and at every boot; apt pointed at it; the old cache gone",
    "What=tmpfs" in unit.read_text()
    and "size=192M" in unit.read_text()
    and ("systemctl", "enable", "--now", health.APT_RAM_UNIT) in ran
    and 'Dir::Cache::pkgcache "/run/irate-box-apt/pkgcache.bin";' in conf
    and 'Dir::Cache::srcpkgcache "";' in conf
    and not (T / "apt-cache" / "pkgcache.bin").exists()
    and {g["key"]: g for g in health.card_groups()}["apt_cache"]["current"] == "memory",
    ran,
)
ran.clear()
health.fix("card-set:apt_cache:card")
check(
    "  and back on the card: the file and the mount gone",
    not (T / "apt.conf.d" / "50irate-box-cache").exists()
    and not unit.exists()
    and ("systemctl", "disable", "--now", health.APT_RAM_UNIT) in ran,
    ran,
)
health.fix("card-set:apt_languages:none")
lang = T / "apt.conf.d" / "50irate-box-languages"
check("package descriptions not downloaded: Acquire::Languages none", 'Acquire::Languages "none";' in lang.read_text())
health.fix("card-set:apt_languages:download")
check("  downloaded again: the file gone", not lang.exists())
ran.clear()
health.fix("card-set:journal:card")
jconf = T / "systemd" / "journald.conf.d" / "50-irate-box.conf"
check(
    "the journal on the card: Storage=persistent, journald restarted",
    "Storage=persistent" in jconf.read_text() and ("systemctl", "restart", "systemd-journald") in ran,
)
health.fix("card-set:journal:memory")
check(
    "  in memory: Storage=volatile, capped",
    "Storage=volatile" in jconf.read_text()
    and "RuntimeMaxUse=24M" in jconf.read_text()
    and {g["key"]: g for g in health.card_groups()}["journal"]["current"] == "memory",
)
(T / "fstab").write_text("PARTUUID=1 /boot vfat defaults 0 2\nPARTUUID=2 / ext4 defaults,noatime 0 1\n")
ran.clear()
health.fix("card-set:writeback:longer")
commit_unit = T / "systemd" / "system" / health.COMMIT_UNIT
check(
    "the longer write-back delay: the kernel's two settings now and at boot, / committed every 60 s, not 5",
    "vm.dirty_expire_centisecs = 6000" in (T / "sysctl.d" / "50-irate-box-writeback.conf").read_text()
    and ("sysctl", "-q", "-w", "vm.dirty_writeback_centisecs=1500") in ran
    and "commit=60" in commit_unit.read_text()
    and ("mount", "-o", "remount,commit=60", "/") in ran,
    ran,
)
ran.clear()
health.fix("card-set:writeback:standard")
check(
    "  standard again: the files gone, the kernel's defaults, / as fstab says",
    not (T / "sysctl.d" / "50-irate-box-writeback.conf").exists()
    and not commit_unit.exists()
    and ("sysctl", "-q", "-w", "vm.dirty_expire_centisecs=3000") in ran
    and ("mount", "-o", "remount,commit=5", "/") in ran,
    ran,
)
(T / "fstab").write_text("UUID=3 / ext4 defaults,commit=120,errors=remount-ro 0 1\n")
ran.clear()
health.fix("card-set:writeback:longer")
check(
    "  a box whose fstab already commits every 120 s (the Lyra): kept at 120, no unit",
    not commit_unit.exists() and ("mount", "-o", "remount,commit=120", "/") in ran,
    ran,
)
health.fix("card-set:writeback:standard")

# The kernel's memory controller: without it, accounting counts almost nothing (a Raspberry Pi kernel's default).
box("dddd4444", 1, {}, accounting=True, memcg=False)
(T / "cmdline.txt").write_text("console=tty1 root=PARTUUID=90ce46b6-02 rootwait quiet")
got = {f["id"]: f for f in health.check_card_writes()}
check(
    "accounting on, the memory controller off: said, with the repair on a Raspberry Pi",
    "card-memcg" in got and [a["choice"] for a in got["card-memcg"]["actions"]] == ["card-memcg-on"],
    got.get("card-memcg"),
)
said = health.fix("card-memcg-on")
check(
    "  the option added to the one line of cmdline.txt, from the next restart",
    (T / "cmdline.txt").read_text() == "console=tty1 root=PARTUUID=90ce46b6-02 rootwait quiet cgroup_enable=memory"
    and said["id"] == "repair.card-memcg.on",
    (T / "cmdline.txt").read_text(),
)
pending = {f["id"]: f for f in health.check_card_writes()}["card-memcg"]
check("  until then: said as waiting for the restart, nothing to press", pending["actions"] == [], pending)
box("dddd4444", 1, {}, accounting=True)
# The onboard flash: free, offered as the journal's place; in use, its wear.
(T / "code" / "tools").mkdir(parents=True)
(T / "code" / "tools" / "nand-logs.sh").write_text("#!/bin/bash\n")
(T / "mtd").write_text('dev:    size   erasesize  name\nmtd0: 10000000 00020000 "spi-nand0"\n')
got = {f["id"]: f for f in health.check_card_writes()}
check(
    "a free onboard flash: said, with the size, pointing to the setting",
    got.get("card-flash", {}).get("status") == "warn" and got["card-flash"]["summary"]["args"] == {"mb": 256},
    got.get("card-flash"),
)
journal = {g["key"]: g for g in health.card_groups()}["journal"]
check(
    "  System journal offers On the onboard flash, asking first",
    [o["value"] for o in journal["options"]] == ["card", "memory", "flash"] and journal["options"][2].get("confirm"),
)
ran.clear()
health.fix("card-set:journal:flash")
check(
    "On the onboard flash: nand-logs.sh on, irate-box's own journal file removed",
    any(c[0] == "bash" and c[2] == "on" for c in ran)
    and not jconf.exists()
    and {g["key"]: g for g in health.card_groups()}["journal"]["current"] == "flash",
    ran,
)
(T / "ubi" / "ubi9").mkdir(parents=True)
(T / "ubi" / "ubi9" / "max_ec").write_text("3\n")
(T / "ubi" / "ubi9" / "bad_peb_count").write_text("0\n")
on = {f["id"]: f for f in health.check_card_writes()}.get("card-flash", {})
check(
    "  in use: its wear as UBI counts it",
    on.get("status") == "ok" and on["summary"]["args"] == {"n": 3, "bad": 0},
    on,
)
ran.clear()
health.fix("card-set:journal:memory")
check(
    "  back to memory: nand-logs.sh off first, then the journal in memory",
    any(c[0] == "bash" and c[2] == "off" for c in ran) and "Storage=volatile" in jconf.read_text(),
    ran,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
