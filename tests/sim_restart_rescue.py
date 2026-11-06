# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Restart into the rescue (root/rescueenv.py, stage 2), offline: the confirmation, every
missing key, the count set past the limit, the old count put back when the write or the
restart fails, and the HTTPS-only rule. Nothing is rebooted here: the reboot command is a
stub that records the call.
python3 tests/sim_restart_rescue.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="restart-test-"))
proc_mtd, mtd_sys, fw_env = T / "proc-mtd", T / "mtd", T / "fw_env.config"
mtd_sys.mkdir()
(mtd_sys / "mtd0").mkdir()
(mtd_sys / "mtd0" / "size").write_text("33554432\n")
proc_mtd.write_text('dev:    size   erasesize  name\nmtd0: 02000000 00020000 "boot"\n')
fw_env.write_text("/dev/mtd0 0x7E0000 0x20000 0x20000\n/dev/mtd0 0x7C0000 0x20000 0x20000\n")
environment = T / "env.json"
environment.write_text(
    json.dumps(
        {
            "bootcount": "2",
            "bootlimit": "3",
            "card_confirms": "0",
            "altbootcmd": "run nand_boot_rescue",
        }
    )
)
print_stub = T / "fw_printenv"
print_stub.write_text(
    """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if Path(os.environ["ENV_STATE"] + ".fail-print").exists():
    sys.exit(1)
for key, value in json.loads(Path(os.environ["ENV_STATE"]).read_text()).items():
    print(f"{key}={value}")
"""
)
print_stub.chmod(0o755)
set_stub = T / "fw_setenv"
set_stub.write_text(
    """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if Path(os.environ["ENV_STATE"] + ".fail-set").exists():
    sys.exit(1)
path = Path(os.environ["ENV_STATE"])
data = json.loads(path.read_text())
data[sys.argv[-2]] = sys.argv[-1]
path.write_text(json.dumps(data))
"""
)
set_stub.chmod(0o755)
reboot_stub = T / "reboot"
reboot_stub.write_text(
    """#!/usr/bin/env python3
import os
from pathlib import Path
if Path(os.environ["ENV_STATE"] + ".fail-reboot").exists():
    raise SystemExit(1)
Path(os.environ["ENV_STATE"] + ".rebooted").write_text("1")
"""
)
reboot_stub.chmod(0o755)
os.environ.update(
    ENV_STATE=str(environment),
    HUB_FW_ENV_CONFIG=str(fw_env),
    HUB_PROC_MTD=str(proc_mtd),
    HUB_MTD_SYS=str(mtd_sys),
    HUB_FW_PRINTENV=str(print_stub),
    HUB_FW_SETENV=str(set_stub),
    HUB_REBOOT=str(reboot_stub),
)
sys.path.insert(0, str(REPO))
from irate_box.hub import transport  # noqa: E402
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


def reset(**changes):
    data = {
        "bootcount": "2",
        "bootlimit": "3",
        "card_confirms": "0",
        "altbootcmd": "run nand_boot_rescue",
    }
    data.update(changes)
    for key in [key for key, value in changes.items() if value is None]:
        data.pop(key, None)
    environment.write_text(json.dumps(data))
    for suffix in (".fail-print", ".fail-set", ".fail-reboot", ".rebooted"):
        Path(str(environment) + suffix).unlink(missing_ok=True)


reset()
check("a good environment is ready", rescueenv.rescue_state()["ready"] is True, rescueenv.rescue_state())
check("restart-rescue is HTTPS only", "restart-rescue" in transport.HTTPS_ONLY)

code, text = refused(lambda: rescueenv.restart_rescue("RESTART NOW"))
check("a wrong confirmation is refused", code == "rescue.restart.confirm" and "Type RESTART" in text, (code, text))

reset()
said = rescueenv.restart_rescue("RESTART")
check(
    "the count is set past the limit", json.loads(environment.read_text())["bootcount"] == "4", environment.read_text()
)
check("the restart was called", Path(str(environment) + ".rebooted").exists())
check(
    "the action says the connection will drop",
    said["id"] == "rescue.restart.started" and "connection will drop" in str(said),
    said,
)

reset()
Path(str(environment) + ".fail-reboot").write_text("1")
code, text = refused(lambda: rescueenv.restart_rescue("RESTART"))
check("a failed restart is refused", code == "rescue.restart.reboot-failed" and "put back" in text, (code, text))
check("  and the old count is back", json.loads(environment.read_text())["bootcount"] == "2", environment.read_text())

reset()
Path(str(environment) + ".fail-set").write_text("1")
code, text = refused(lambda: rescueenv.restart_rescue("RESTART"))
check("a failed environment write is refused", code == "rescue.restart.set-failed", (code, text))
check(
    "  and the old count is unchanged", json.loads(environment.read_text())["bootcount"] == "2", environment.read_text()
)

for key, message in (
    ("bootlimit", "rescue.restart.no-bootlimit"),
    ("bootcount", "rescue.restart.no-bootcount"),
):
    reset(**{key: None})
    code, text = refused(lambda: rescueenv.restart_rescue("RESTART"))
    check(f"no {key} is refused", code == message and key in text, (code, text))
    check("  and says why in the state", rescueenv.rescue_state()["ready"] is False)

reset(altbootcmd="run nand_boot_card")
code, text = refused(lambda: rescueenv.restart_rescue("RESTART"))
check(
    "an altbootcmd without the rescue is refused",
    code == "rescue.restart.no-rescue" and "altbootcmd" in text,
    (code, text),
)

reset()
Path(str(environment) + ".fail-print").write_text("1")
code, text = refused(lambda: rescueenv.restart_rescue("RESTART"))
check("no readable environment is refused", code == "rescue.restart.no-env" and "no readable" in text, (code, text))

from irate_box.hub import server  # noqa: E402

(T / "control").mkdir(exist_ok=True)
(T / "control" / "rescue.json").write_text(
    json.dumps({"env": {"available": True, "bootcount": 2}, "rescue": {"ready": True, "reasons": []}})
)
server.CONTROL_DIR = T / "control"
snapshot = server.rkboard_snapshot()
check(
    "the Rockchip snapshot builds with env and rescue",
    snapshot["env"]["bootcount"] == 2 and snapshot["rescue"]["ready"] is True,
    snapshot.get("rescue"),
)
check("the snapshot's rescue reader is defined", callable(server.rescue_control_snapshot))

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
