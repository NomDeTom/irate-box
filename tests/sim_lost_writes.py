# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Lost writes on the SD card (health.py, notes: box-doctor-hardening-plan Part 1, step 2), offline: a record
that reads back as 0xFF is named, a record merely cut short is not; the boot's finding is kept for this boot and
not shown after the next; the optional test file is 4 KB, kept in root's own folder, and named when it reads as
0xFF; the timer, the boot check and the test file are units of their own, each switched off with its choice.
python3 tests/sim_lost_writes.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="lost-writes-"))
state, etc, root = T / "state", T / "etc", T / "root"
units = T / "systemd"
for p in (state, etc, root, units):
    p.mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(etc),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_SYSTEMD_ETC=str(units),
    HUB_BOOT_ID="boot-one",
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.hub import cardhistory
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


cardhistory.card = lambda: ("mmcblk0", "179:0")
# The real _on_card, against a mount table with the test's folders on the card (it is handed Paths: box 181 found
# that _mount_of took only a str, 2026-10-28).
(mountinfo := T / "mountinfo").write_text(
    f"30 1 179:1 / / rw - ext4 /dev/mmcblk0p1 rw\n"
    f"31 30 0:30 / {state} rw - ext4 /dev/mmcblk0p1 rw\n"
    f"32 30 0:31 / {root} rw - ext4 /dev/mmcblk0p1 rw\n"
)
health.MOUNTINFO = mountinfo
check(
    "the records found on the card through the real mount lookup, from Paths", health._on_card(state / "x", "mmcblk0")
)

record = state / "memory-history.json"
record.write_text('{"rows": []}\n')
check("a record that reads back whole: no finding", health.lost_writes_check() == [])

record.write_text("{")
check(
    "a record cut short at a power cut: no finding (the filesystem's own loss, not the card's)",
    health.lost_writes_check() == [],
)

record.write_bytes(b"\xff" * 256)
found = health.lost_writes_check()
check(
    "a record that reads back as 0xFF: the card loses writes, named",
    len(found) == 1
    and found[0]["id"] == "card-lost-writes"
    and found[0]["status"] == "problem"
    and str(record) in found[0]["evidence"]
    and "lost-writes.found" in message_ids(found[0]),
    found,
)

health.boot_check()
record.write_text('{"rows": []}\n')
check(
    "the boot's finding is kept for this boot, though the record is whole again",
    [f["id"] for f in health.check_lost_writes()] == ["card-lost-writes"],
)
os.environ["HUB_BOOT_ID"] = "boot-two"
check("  and not shown after the next boot: the check now finds nothing", health.check_lost_writes() == [])
os.environ["HUB_BOOT_ID"] = "boot-one"

(recommended_chosen := etc / "chosen.json").write_text(json.dumps({"canary": "on"}))
check("the test file: off leaves nothing, on writes one 4 KB block", health.canary_tick() is True)
canary = root / "canary.bin"
check(
    "  in root's own folder (not the hub's state), 4 KB",
    canary.is_file() and canary.stat().st_size == 4096 and not canary.is_relative_to(state),
)
check("  a whole test file: no finding", health.lost_writes_check() == [])
first = canary.read_bytes()[:32]
health.canary_tick()
check("  its sequence number moves at each write", canary.read_bytes()[:32] != first)
good = canary.read_bytes()
check("  its checksum matches what follows it", health._canary_intact(canary))
canary.write_bytes(good[:-64] + bytes(64))
found = health.lost_writes_check()
check(
    "  a test file with part of it gone (its checksum no longer matching): the same finding",
    len(found) == 1 and str(canary) in found[0]["evidence"],
    found,
)
canary.write_bytes(b"\xff" * 4096)
found = health.lost_writes_check()
check(
    "  a test file that reads as 0xFF: the same finding",
    len(found) == 1 and str(canary) in found[0]["evidence"],
    found,
)

# The units and their choices (systemctl stood in for).
calls = []


def fake_run(*cmd, **kw):
    calls.append(cmd)
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


health.run = fake_run
health.set_timer(True)
check(
    "the timer: the service and the timer written, the timer enabled",
    (units / "system" / health.DOCTOR_TIMER).is_file()
    and (units / "system" / health.DOCTOR_SERVICE).is_file()
    and any("enable" in c and health.DOCTOR_TIMER in c for c in calls),
    calls,
)
text = (units / "system" / health.DOCTOR_TIMER).read_text()
check(
    "  every 6 hours, its first run half an hour after boot, at a randomised delay",
    "OnBootSec=30min" in text and "OnUnitActiveSec=6h" in text and "RandomizedDelaySec=1h" in text,
    text,
)
check(
    "  the service: the doctor's run, low priority, and it writes only its report and root's records",
    f"ExecStart={health.CODE}/irate-box health run" in (units / "system" / health.DOCTOR_SERVICE).read_text()
    and "Nice=19" in (units / "system" / health.DOCTOR_SERVICE).read_text(),
)
health.set_timer(False)
check(
    "  switched off: both unit files gone",
    not (units / "system" / health.DOCTOR_TIMER).exists() and not (units / "system" / health.DOCTOR_SERVICE).exists(),
)
health.set_lostwrites(True)
check(
    "the boot check: a unit of its own, before the network and the hub, switched on",
    "Before=network.target irate-box.service" in (units / "system" / health.LOSTWRITES_SERVICE).read_text()
    and any("enable" in c and health.LOSTWRITES_SERVICE in c for c in calls),
)
boot_unit = (units / "system" / health.LOSTWRITES_SERVICE).read_text()
check(
    "  after the NAND's state (a Lyra's hub folder is the flash's only then), at normal priority on the boot's path",
    "After=local-fs.target nand-state.service" in boot_unit
    and "Nice=" not in boot_unit
    and "IOScheduling" not in boot_unit,
)
health.set_canary(True)
check(
    "the test file's timer: every 10 minutes, carried by a unit of its own",
    "OnUnitActiveSec=10min" in (units / "system" / health.CANARY_TIMER).read_text(),
)
health.set_canary(False)
check("  switched off: the timer, the service and the test file gone", not canary.exists())

# The installer honours each choice, including the owner's off (health.install_units).
(recommended_chosen).write_text(json.dumps({"health-timer": "off", "lost-writes": "off", "canary": "on"}))
health.install_units()
check(
    "install-units: the owner's off choices are left off at the next install, the test file on",
    not (units / "system" / health.DOCTOR_TIMER).exists()
    and not (units / "system" / health.LOSTWRITES_SERVICE).exists()
    and (units / "system" / health.CANARY_TIMER).exists(),
)
health.set_canary(False)

for good in ("health-timer:on", "health-timer:off", "lost-writes:on", "canary:off"):
    check(f"the doctor takes {good}", bool(health.CHOICE_RE.match(good)))
for bad in ("health-timer:always", "lost-writes:yes", "canary:10min"):
    check(f"  and refuses {bad}", not health.CHOICE_RE.match(bad))

# A timer run: the quick checks, the full one once a week, the report /admin reads, its timings kept.
seen = []


def fake_scan(quick=False):
    seen.append(quick)
    return {
        "at": 1,
        "findings": [{"status": "problem", "id": "x"}, {"status": "ok", "id": "y"}],
        "settings": None,
        "clock": None,
        "timings": {"kiwix": 0.1, "units": 3.2},
        "quick": quick,
    }


health.scan = fake_scan
health.run_check(full=False)
check(
    "a timer run: the quick check, its report written", seen == [True] and (state / "control" / "health.json").is_file()
)
health.run_check()
check("with no full run recorded: the next is a full one", seen[-1] is False)
health.run_check()
check("  then the quick one again, within the week", seen[-1] is True)
check(
    "  the report has the timings, and the full run's time",
    json.loads((state / "control" / "health.json").read_text())["timings"]["units"] == 3.2
    and json.loads((state / "control" / "health.json").read_text())["full_at"] > 0,
)

# The builds' size: walked in the full run, kept for the quick ones (the Lyra's slow step).
ci = state / "ci" / "run1"
ci.mkdir(parents=True)
(ci / "out.bin").write_bytes(b"x" * 4096)
check("the builds' size: walked in a full run", health._builds_bytes(state / "ci", quick=False) == 4096)
(ci / "more.bin").write_bytes(b"x" * 4096)
check("  kept for the quick run, not walked again", health._builds_bytes(state / "ci", quick=True) == 4096)
check("  walked again in the next full run", health._builds_bytes(state / "ci", quick=False) == 8192)
check(
    "/admin's run and each repair's: through run_report, so quick and keeping the full run's time",
    "health.run_report(full=False)"
    in (REPO / "irate_box/root/hub_control.py").read_text().split("def _write_health")[1].split("\ndef ")[0],
)

# A record on the onboard flash (nand-logs' state on) is not the card's.
mountinfo.write_text(
    f"30 1 179:1 / / rw - ext4 /dev/mmcblk0p1 rw\n31 30 0:30 /hub {state} rw - ubifs ubi9:logs rw\n"
    f"32 30 0:31 / {root} rw - ext4 /dev/mmcblk0p1 rw\n"
)
record.write_text('{"rows": []}\n')
check("  a record on the onboard flash is not counted", record not in health.records_on_card())
check("  nor a record on the onboard flash given as a str", not health._on_card(str(record), "mmcblk0"))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
