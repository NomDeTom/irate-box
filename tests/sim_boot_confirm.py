# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The boot-confirm unit and the manual Confirm this boot (stage 3), offline: card_confirms
off, the hub not answering, the hub answering, a failed write, the manual confirmation,
card_confirms on and off, and the control record. Nothing writes a real environment: the
tools and the reboot are stubs.
python3 tests/sim_boot_confirm.py"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="boot-confirm-"))
proc_mtd, mtd_sys, fw_env, state = T / "proc-mtd", T / "mtd", T / "fw_env.config", T / "state"
mtd_sys.mkdir()
(mtd_sys / "mtd0").mkdir()
(mtd_sys / "mtd0" / "size").write_text("33554432\n")
proc_mtd.write_text('dev:    size   erasesize  name\nmtd0: 02000000 00020000 "boot"\n')
fw_env.write_text("/dev/mtd0 0x7E0000 0x20000 0x20000\n/dev/mtd0 0x7C0000 0x20000 0x20000\n")
state.mkdir()
environment = T / "env.json"
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
os.environ.update(
    ENV_STATE=str(environment),
    HUB_FW_ENV_CONFIG=str(fw_env),
    HUB_PROC_MTD=str(proc_mtd),
    HUB_MTD_SYS=str(mtd_sys),
    HUB_FW_PRINTENV=str(print_stub),
    HUB_FW_SETENV=str(set_stub),
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_ROOT_STATE_DIR=str(T / "root"),
    HUB_BOOT_CONFIRM_WAIT="0.2",
    HUB_BOOT_CONFIRM_INTERVAL="0.05",
)
(T / "etc").mkdir()
(T / "root").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import hub_control, rescueenv

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def reset(**changes):
    data = {
        "bootcount": "2",
        "bootlimit": "3",
        "card_confirms": "0",
        "altbootcmd": "run nand_boot_rescue",
    }
    data.update(changes)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
    environment.write_text(json.dumps(data))
    for suffix in (".fail-print", ".fail-set"):
        Path(str(environment) + suffix).unlink(missing_ok=True)
    (state / "control").mkdir(exist_ok=True)
    (state / "control" / "rescue-confirm.json").unlink(missing_ok=True)


def refused(call):
    try:
        call()
    except Refused as exc:
        return exc.message["id"], str(exc)
    return None, ""


def record():
    return json.loads((state / "control" / "rescue-confirm.json").read_text())


reset()
check(
    "the manual confirmation is refused without CONFIRM",
    refused(lambda: hub_control.rk_boot_confirm({"confirm": "YES"}))[0] == "rescue.confirm.confirm",
)
check(
    "a non-boolean card_confirms is refused",
    refused(lambda: hub_control.rk_boot_confirm({"confirm": "CONFIRM", "card_confirms": "yes"}))[0]
    == "rescue.confirm.bad-card",
)

said = hub_control.rk_boot_confirm({"confirm": "CONFIRM"})
check(
    "the manual confirmation resets the count",
    json.loads(environment.read_text())["bootcount"] == "0",
    environment.read_text(),
)
check("  and says so", said["id"] == "rescue.confirm.done", said)
check(
    "  the record says manual and confirmed", record()["over"] == "manual" and record()["confirmed"] is True, record()
)

reset()
said = hub_control.rk_boot_confirm({"confirm": "CONFIRM", "card_confirms": True})
check(
    "card_confirms on is written", json.loads(environment.read_text())["card_confirms"] == "1", environment.read_text()
)
check("  and the message says the card confirms", said["id"] == "rescue.confirm.card-on", said)
reset()
said = hub_control.rk_boot_confirm({"confirm": "CONFIRM", "card_confirms": False})
check(
    "card_confirms off is written", json.loads(environment.read_text())["card_confirms"] == "0", environment.read_text()
)
check("  and the message says it will not confirm", said["id"] == "rescue.confirm.card-off", said)

reset(card_confirms="0")
hub_control.boot_confirm_cli()
check(
    "the unit does nothing while card_confirms is off",
    json.loads(environment.read_text())["bootcount"] == "2" and record()["reason"] == "card-confirms-off",
    record(),
)

reset(card_confirms="1")
os.environ["HUB_PORT"] = "1"
hub_control.boot_confirm_cli()
check(
    "the unit waits for the hub and does not confirm without it",
    json.loads(environment.read_text())["bootcount"] == "2" and record()["reason"] == "hub-unreachable",
    record(),
)


class Hub(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
os.environ["HUB_PORT"] = str(server.server_address[1])
reset(card_confirms="1")
hub_control.boot_confirm_cli()
check(
    "the unit confirms once the hub answers",
    json.loads(environment.read_text())["bootcount"] == "0" and record()["confirmed"] is True,
    record(),
)
check("  the record says unit", record()["over"] == "unit", record())

reset(card_confirms="1")
Path(str(environment) + ".fail-set").write_text("1")
hub_control.boot_confirm_cli()
check(
    "a failed write leaves the count and says so",
    json.loads(environment.read_text())["bootcount"] == "2" and record()["reason"] == "env-write-failed",
    record(),
)

reset()
Path(str(environment) + ".fail-print").write_text("1")
code, text = refused(lambda: hub_control.rk_boot_confirm({"confirm": "CONFIRM"}))
check(
    "an unreadable environment is refused in the manual action",
    code == "rescue.confirm.no-env" and "no readable" in text,
    (code, text),
)

reset()
(state / "control" / "rescue.json").write_text(json.dumps({"env": rescueenv.snapshot()}))
(state / "control" / "rescue-confirm.json").write_text(
    json.dumps({"at": 1762500000, "over": "unit", "confirmed": True, "reason": ""})
)
from irate_box.hub import server as hubserver

hubserver.CONTROL_DIR = state / "control"
snapshot = hubserver.rkboard_snapshot()
check(
    "the Rockchip snapshot's boot_confirm carries the last confirmation",
    snapshot["boot_confirm"]["last_confirm_over"] == "unit" and snapshot["boot_confirm"]["confirmed"] is True,
    snapshot.get("boot_confirm"),
)

server.shutdown()
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
