# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The onboard flash's health in the Box doctor (health.py, notes: box-doctor-hardening-plan Part 1, step 4),
offline: the chip's corrected bits and uncorrectable reads, UBI's bad blocks and highest erase count, a finding
when they grow; UBIFS data checksum errors in the kernel log; the chk_data_crc setting and its one repair; and
nand-logs.sh carrying the option into the mount. python3 tests/sim_nand_health.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="nand-health-"))
mtd, ubi, state, root, units = T / "mtd", T / "ubi", T / "state", T / "root", T / "systemd"
for p in (mtd / "mtd0", ubi / "ubi0", state, root, units):
    p.mkdir(parents=True)
for name, value in (("corrected_bits", 0), ("ecc_failures", 0)):
    (mtd / "mtd0" / name).write_text(f"{value}\n")
for name, value in (("mtd_num", 0), ("max_ec", 5), ("bad_peb_count", 4)):
    (ubi / "ubi0" / name).write_text(f"{value}\n")
conf = T / "nand-logs.conf"
conf.write_text("mtd=0\ncrc=1\n")
os.environ.update(
    HUB_MTD_SYS=str(mtd),
    HUB_UBI_SYS=str(ubi),
    HUB_NAND_STATE=str(conf),
    HUB_STATE_DIR=str(state),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_SYSTEMD_ETC=str(units),
    HUB_PROC_MTD=str(T / "proc-mtd"),
)
(Path(os.environ["HUB_PROC_MTD"])).write_text("dev:    size   erasesize  name\n")
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


kernel = {"crc": []}


def fake_run(*cmd, **kw):
    stdout = "\n".join(kernel["crc"]) if "journalctl" in cmd else ""
    return type("R", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


health.run = fake_run
health._nand = lambda: ("on", None)
health._flash_part = lambda: None
health._flash_rate = lambda: (None, None)


def finding():
    got = [f for f in health._flash_findings() if f["id"] == "card-flash"]
    return got[0] if got else {}


f = finding()
check(
    "a healthy flash: no finding (0 corrected, 0 uncorrectable, 4 factory bad blocks, erase count 5)",
    f.get("status") == "ok" and not any("grew" in i for i in message_ids(f)),
    f,
)
check(
    "  its counters are kept for the next check",
    (root / "nand-health.json").is_file() and '"corrected": 0' in (root / "nand-health.json").read_text(),
    (root / "nand-health.json").read_text() if (root / "nand-health.json").exists() else None,
)
(mtd / "mtd0" / "corrected_bits").write_text("3\n")
f = finding()
check(
    "corrected bits growing: a warning that names the growth",
    f.get("status") == "warn" and "card-flash.corrected-grew" in message_ids(f),
    f,
)
(mtd / "mtd0" / "ecc_failures").write_text("1\n")
f = finding()
check(
    "a read the chip could not correct: a problem",
    f.get("status") == "problem" and "card-flash.ecc" in message_ids(f),
    f,
)
kernel["crc"] = [
    "UBIFS error (ubi0:0 pid 1): ubifs_check_node: Bad CRC: calculated 0x1, read 0x2",
]
f = finding()
check(
    "a UBIFS data checksum error in the kernel log: a problem, with the line",
    f.get("status") == "problem" and "card-flash.crc" in message_ids(f) and any("Bad CRC" in e for e in f["evidence"]),
    f,
)
conf.write_text("mtd=0\n")
f = finding()
check(
    "  checks off: the finding says so, and points to the setting",
    "card-flash.crc-off" in message_ids(f),
    f,
)

g = {x["key"]: x for x in health.card_groups()}
check(
    "the setting: UBIFS data checked on read, on (the state file) and off, a choice each",
    g["flash_crc"]["current"] == "off"
    and [o["choice"] for o in g["flash_crc"]["options"]] == ["card-set:flash_crc:on", "card-set:flash_crc:off"],
    g.get("flash_crc"),
)
calls = []
health._nand_tool = lambda *verb: calls.append(verb)
health.card_set("flash_crc", "on")
check("  its repair runs nand-logs.sh crc on", calls == [("crc", "on")], calls)
check(
    "  the doctor takes the choice, and refuses others",
    bool(health.CHOICE_RE.match("card-set:flash_crc:off")) and not health.CHOICE_RE.match("card-set:flash_crc:maybe"),
)
conf.write_text("mtd=0\ncrc=1\n")
check("  a flash with the setting on reads as on", health.nand_crc_on() is True)

tool = (REPO / "tools/nand-logs.sh").read_text()
check(
    "nand-logs.sh: the UBIFS volume mounted with chk_data_crc when on, and a crc on|off command",
    "chk_data_crc" in tool and "crc_set" in tool and "crc=1" in tool and "crc) crc_set" in tool,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
