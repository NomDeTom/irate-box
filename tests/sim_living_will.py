# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The living will (root/livingwill.py, root/health.py): the signed master, the per-box hotspot
password, the NAND and USB copies, the restore check for a single-image card, the doctor's
findings, and the two doctor repairs. Offline.
python3 tests/sim_living_will.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="living-will-"))
state, etc, root, nand, usb = T / "state", T / "etc", T / "root", T / "nand", T / "usb"
for folder in (state, etc, root, nand, usb):
    folder.mkdir()
os.environ.update(
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(etc),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_NAND_STATE=str(nand),
    HUB_RESCUE_USB=str(usb),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args, message_ids  # noqa: E402
from irate_box.msg import Refused  # noqa: E402
from irate_box.root import health, livingwill  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


check("no master: the safe defaults are shown", livingwill.show()["valid"] is False)
check("  with the safe auto settings", livingwill.defaults()["auto"]["repair_fs"] is False)

view, generated = livingwill.save({})
check("saving makes a signed master", livingwill.MASTER.is_file() and livingwill.SIGNERS.is_file(), view)
check(
    "  and an Ed25519 key of the box's own",
    livingwill.KEY.is_file() and oct(livingwill.KEY.stat().st_mode)[-3:] == "600",
)
check("  the view says the will is valid", view["valid"] is True and view["written"] is not None, view)
check("  a per-box hotspot password was made", len(generated) == 20 and view["password_set"] is True, generated)
check(
    "  the password uses the unambiguous alphabet",
    set(generated) <= set(livingwill.ALPHABET) and not set("ilo01") & set(generated),
    generated,
)
master = json.loads(livingwill.MASTER.read_text())
check("  the master keeps the password", master["instructions"]["reach"]["password"] == generated)
check("  the master is signed in the rescue's format", master["signature"]["namespace"] == "irate-living-will")

view, generated = livingwill.save({"reach": {"password": "a-known-password"}})
check("a password the owner sets is kept", generated == "" and view["password_set"] is True, view)
check(
    "  and the master's value is the owner's",
    json.loads(livingwill.MASTER.read_text())["instructions"]["reach"]["password"] == "a-known-password",
)

view, _ = livingwill.save({"restore": {"method": "other-slot"}})
findings = {f["id"]: f for f in health.check_living_will()}
check(
    "a single-image card cannot use the other A/B slot",
    findings["living-will-restore"]["status"] == "problem",
    findings["living-will-restore"],
)
check(
    "  the finding says exactly that",
    "living-will-restore.other-slot" in message_ids(findings["living-will-restore"])
    and "one system image and no second slot" in str(findings["living-will-restore"]["summary"]),
    findings["living-will-restore"],
)
check(
    "  the living will itself is valid",
    findings["living-will"]["status"] == "ok",
    findings["living-will"],
)
check(
    "  and it has its own hotspot password",
    findings["living-will-reach"]["status"] == "ok",
    findings["living-will-reach"],
)

livingwill.save({"restore": {"method": "image", "image": {"source": "usb", "name": "missing.img", "sha256": "0" * 64}}})
findings = {f["id"]: f for f in health.check_living_will()}
check(
    "a named image that is not there is a problem",
    findings["living-will-restore"]["status"] == "problem",
    findings["living-will-restore"],
)
check(
    "  with the image's message id",
    "living-will.image.missing" in message_ids(findings["living-will-restore"]),
    findings["living-will-restore"],
)

livingwill.save({"restore": {"method": "none"}})
result = livingwill.push("WRITE WILL")
check("push writes both copies that are present", result == {"nand": "written", "usb": "written"}, result)
check("  and they match the master", livingwill._copies() == {"nand": "match", "usb": "match"}, livingwill._copies())
findings = {f["id"]: f for f in health.check_living_will()}
check("matching copies are ok", findings["living-will-copies"]["status"] == "ok", findings["living-will-copies"])

livingwill.save({"reach": {"password": "a-second-password"}})
check(
    "a newer master leaves the old copies stale",
    livingwill._copies() == {"nand": "stale", "usb": "stale"},
    livingwill._copies(),
)
findings = {f["id"]: f for f in health.check_living_will()}
check(
    "  the doctor warns about them", findings["living-will-copies"]["status"] == "warn", findings["living-will-copies"]
)
check(
    "  and offers the push repair", any(a["choice"] == "will-push" for a in findings["living-will-copies"]["actions"])
)

try:
    livingwill.push("WRITE")
    check("push needs the typed sentence", False)
except Refused as exc:
    check(
        "push needs the typed sentence",
        exc.message["id"] == "living-will.confirm"
        and str(exc) == "Type WRITE WILL to write the living will to the onboard flash and the USB drive.",
        exc,
    )

livingwill.MASTER.write_text(livingwill.MASTER.read_text().replace("a-second-password", "a-third-password"))
check("a tampered master is not valid", livingwill.show()["valid"] is False)
try:
    livingwill.push("WRITE WILL")
    check("  and cannot be pushed", False)
except Refused as exc:
    check("  and cannot be pushed", exc.message["id"] == "living-will.master-invalid", exc)

livingwill.MASTER.unlink()
livingwill.SIGNERS.unlink()
findings = {f["id"]: f for f in health.check_living_will()}
check("no will: the doctor warns", findings["living-will"]["status"] == "warn", findings["living-will"])
check(
    "  with the fixed-password warning too",
    findings["living-will-reach"]["status"] == "warn",
    findings["living-will-reach"],
)
check(
    "  the fixed-password warning is exact",
    "living-will.reach.fixed" in message_ids(findings["living-will-reach"]),
    findings["living-will-reach"],
)
said = health.fix("will-save-defaults")
check(
    "the doctor's safe-defaults repair saves a will",
    said["id"] == "repair.will-defaults" and livingwill.MASTER.is_file(),
    said,
)
check("  and it has a password again", livingwill.show()["password_set"] is True)

print("FAILURES:", fails)
sys.exit(1 if fails else 0)
