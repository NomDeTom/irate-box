# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The protected files are written through the checked writer, and the records are not (notes:
box-doctor-hardening-plan Part 2, step 2). python3 tests/write_guard.py"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def source(relative):
    return (REPO / relative).read_text()


def body(text, name):
    match = re.search(rf"(?m)^def {re.escape(name)}\(", text)
    if not match:
        return ""
    end = re.search(r"(?m)^(?:def |class )", text[match.end() :])
    return text[match.start() : match.end() + end.start()] if end else text[match.start() :]


HUB = (
    ("irate_box/hub/server.py", "save_settings"),
    ("irate_box/hub/server.py", "save_visibility"),
    ("irate_box/hub/server.py", "save_locked_as"),
    ("irate_box/hub/server.py", "save_folders"),
    ("irate_box/hub/server.py", "save_reports"),
    ("irate_box/hub/server.py", "save_messages"),
    ("irate_box/hub/server.py", "_write_protected"),
    ("irate_box/hub/accounts.py", "_save"),
    ("irate_box/hub/hotspot.py", "save"),
    ("irate_box/hub/meshbridge.py", "_save_channels"),
)
for relative, name in HUB:
    text = body(source(relative), name)
    check(f"{relative}:{name} writes through durable.write_checked", "durable.write_checked(" in text, text[:200])
    check("  and does not write the file itself", ".write_text(" not in text and "os.replace(" not in text, text[:200])

board = source("irate_box/hub/board.py")
check(
    "board's thread state writes through durable.write_checked",
    "durable.write_checked(self.path, json.dumps(state))" in board,
)
server = source("irate_box/hub/server.py")
check(
    "the tile and status-tile arrangements write through durable.write_checked",
    "durable.write_checked(" in server
    and "TILES_FILE," in server
    and "durable.write_checked(STATUS_TILES_FILE" in server,
)

ROOT = (("irate_box/root/tls.py", "_write"), ("irate_box/root/hub_control.py", "_write_root_file"))
for relative, name in ROOT:
    text = body(source(relative), name)
    check(f"{relative}:{name} writes through safeio.write_checked", "safeio.write_checked(" in text, text[:200])

for record in (
    "irate_box/hub/jsonlog.py",
    "irate_box/hub/cardhistory.py",
    "irate_box/hub/svchistory.py",
    "irate_box/hub/memhistory.py",
    "irate_box/hub/pagehistory.py",
    "irate_box/hub/linkhistory.py",
    "irate_box/hub/watchlog.py",
):
    text = source(record)
    check(f"{record} stays a plain write (a record, not a setting)", "write_checked(" not in text)

retain = source("irate_box/root/retain.py")
check(
    "retain writes its copy and the manifest through durable.write_checked",
    "durable.write_checked(copy," in retain and "durable.write_checked(\n        MANIFEST," in retain,
)
check("retain restores through restore.from_copy", "restore.from_copy(" in retain and "sha256=wanted" in retain)
inst = source("install.sh")
check(
    "install.sh installs and sweeps the second copy",
    '"$CODE/irate-box" retain install-units' in inst and '"$CODE/irate-box" retain sweep' in inst,
)
check(
    "uninstall.sh removes the units and the copy",
    "irate-box-protected.{service,timer}" in source("uninstall.sh")
    and "/var/lib/irate-box-root/protected" in source("uninstall.sh"),
)

nf = source("irate_box/root/nginx_front.py")
check("the front writes its files together with durable.write_batch", "durable.write_batch(entries)" in nf)
check("install.sh calls nginx_front write-batch", 'nginx_prepare write-batch "$front_plan"' in source("install.sh"))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
