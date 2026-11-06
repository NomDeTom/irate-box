# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The card's view of the NAND U-Boot environment (root/rescueenv.py, stage 1), offline: the
config against /proc/mtd and /sys/class/mtd, both copies read separately, the values, the
refusals, and the snapshot the page reads when the reading cannot be made.
python3 tests/sim_rescue_env.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rescue-env-"))
proc_mtd, mtd_sys, fw_env = T / "proc-mtd", T / "mtd", T / "fw_env.config"
mtd_sys.mkdir()
(mtd_sys / "mtd0").mkdir()
(mtd_sys / "mtd0" / "size").write_text("33554432\n")
proc_mtd.write_text('dev:    size   erasesize  name\nmtd0: 02000000 00020000 "boot"\n')
fw_env.write_text("/dev/mtd0 0x7E0000 0x20000 0x20000\n/dev/mtd0 0x7C0000 0x20000 0x20000\n")
stub = T / "fw_printenv"
stub.write_text(
    """#!/usr/bin/env python3
import os, sys
from pathlib import Path
config = Path(sys.argv[sys.argv.index("-c") + 1]).read_text()
redundant_only = "0x7C0000" in config and "0x7E0000" not in config
if os.environ.get("STUB_FAIL"):
    sys.exit(1)
boot = 1 if redundant_only and os.environ.get("STUB_MODE") == "disagree" else 2
print(f"bootcount={boot}")
print("bootlimit=3")
print("card_confirms=0")
print("altbootcmd=run nand_boot_rescue")
"""
)
stub.chmod(0o755)
os.environ.update(
    HUB_FW_ENV_CONFIG=str(fw_env), HUB_PROC_MTD=str(proc_mtd), HUB_MTD_SYS=str(mtd_sys), HUB_FW_PRINTENV=str(stub)
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused  # noqa: E402
from irate_box.root import rescueenv  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(call):
    try:
        call()
    except Refused as exc:
        return exc.message["id"], str(exc)
    return None, ""


os.environ.pop("STUB_MODE", None)
os.environ.pop("STUB_FAIL", None)
data = rescueenv.show()
check(
    "the config is read against the 32 MiB boot MTD",
    data["device"] == {"mtd": 0, "name": "boot", "size": 33554432, "erasesize": 131072},
    data,
)
check("both copies are read", data["copies"] == {"primary": True, "redundant": True, "agree": True}, data)
check("the count and the limit", data["bootcount"] == 2 and data["bootlimit"] == 3, data)
check("card_confirms is read as false", data["card_confirms"] is False, data)
check("altbootcmd holds the rescue path", data["altbootcmd_rescue"] is True, data)
check("the snapshot is available", rescueenv.snapshot()["available"] is True)

os.environ["STUB_MODE"] = "disagree"
data = rescueenv.show()
check(
    "the copies disagreeing is reported, not hidden", data["copies"]["agree"] is False and data["bootcount"] == 2, data
)
os.environ.pop("STUB_MODE")

os.environ["STUB_FAIL"] = "1"
code, text = refused(rescueenv.show)
check("neither copy readable is refused", code == "rescue.env.unreadable" and "Neither copy" in text, (code, text))
check(
    "the snapshot says so without raising",
    rescueenv.snapshot()["available"] is False and "Neither copy" in rescueenv.snapshot()["error"],
)
os.environ.pop("STUB_FAIL")

saved = fw_env.read_text()
fw_env.unlink()
code, text = refused(rescueenv.show)
check("a missing config is refused", code == "rescue.env.no-config" and "no /etc/fw_env.config" in text, (code, text))
check("the snapshot names the missing config", rescueenv.snapshot()["config"]["present"] is False)
fw_env.write_text(saved)

proc_mtd.write_text('dev:    size   erasesize  name\nmtd0: 01000000 00020000 "boot"\n')
code, text = refused(rescueenv.show)
check(
    "a config naming a 16 MiB MTD is refused",
    code == "rescue.env.config-bad" and "32 MiB boot MTD" in text,
    (code, text),
)
proc_mtd.write_text('dev:    size   erasesize  name\nmtd0: 02000000 00020000 "boot"\n')

fw_env.write_text("/dev/mtd0 0x7E0000 0x20000 0x20000\n")
data = rescueenv.show()
check(
    "one copy is allowed, and says the other is absent",
    data["copies"] == {"primary": True, "redundant": False, "agree": False},
    data,
)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
