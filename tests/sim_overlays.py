# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The one overlay writer (root/hardware.py): armbianEnv.txt other overlays left alone, duplicates, a
missing file, and the device tree compiler's failure leaving no .dtbo behind.
python3 tests/sim_overlays.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="overlays-"))
BOOT, BOOT2 = T / "boot", T / "boot2"
BOOT.mkdir()
os.environ.update(HUB_BOOT_DIR=str(BOOT), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.root import hardware

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


env = BOOT / "armbianEnv.txt"
env.write_text("verbosity=1\nuser_overlays=mine irate-box-watchdog\n")
check("read: the names in order", hardware.overlay_names() == ["mine", "irate-box-watchdog"])

names = hardware.set_overlay_names(add="irate-box-serial")
check(
    "add: ours at the end, the owner's kept, the other line untouched",
    names == ["mine", "irate-box-watchdog", "irate-box-serial"]
    and env.read_text() == "verbosity=1\nuser_overlays=mine irate-box-watchdog irate-box-serial\n",
    env.read_text(),
)
check("  adding it again writes nothing new", hardware.set_overlay_names(add="irate-box-serial") == names)

env.write_text("verbosity=1\nuser_overlays=mine irate-box-serial irate-box-serial\n")
check(
    "duplicates: ours added once and removed every time",
    hardware.set_overlay_names(add="irate-box-serial") == ["mine", "irate-box-serial", "irate-box-serial"]
    and hardware.set_overlay_names(remove="irate-box-serial") == ["mine"]
    and env.read_text() == "verbosity=1\nuser_overlays=mine\n",
    env.read_text(),
)

env.write_text("verbosity=1\n")
check(
    "no user_overlays line: one is added; the last is taken away without a blank line left",
    hardware.set_overlay_names(add="irate-box-watchdog") == ["irate-box-watchdog"]
    and "user_overlays=irate-box-watchdog\n" in env.read_text()
    and hardware.set_overlay_names(remove="irate-box-watchdog") == []
    and "user_overlays" not in env.read_text(),
    env.read_text(),
)

# A missing boot environment: adding is refused, removing is a no-op.
missing = BOOT2 / "armbianEnv.txt"
try:
    hardware.set_overlay_names(add="irate-box-serial", path=missing)
    kind = None
except hardware.OverlayError as exc:
    kind = exc.kind
check("a missing armbianEnv.txt: adding is refused as such", kind == "no-boot-env", kind)
check("  removing from it changes nothing", hardware.set_overlay_names(remove="irate-box-serial", path=missing) == [])


def fake_dtc(ok):
    def run(argv, inp=None):
        if ok:
            Path(argv[argv.index("-o") + 1]).write_text("fake dtbo")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "syntax error")

    return run


dtbo = BOOT / "overlay-user" / "irate-box-serial.dtbo"
said = hardware.write_dtbo("irate-box-serial", "dts", run=fake_dtc(True))
check("the compiler writes the .dtbo where the boot loader finds it", said == dtbo and dtbo.is_file())
hardware.remove_dtbo("irate-box-serial")
check("  and it is taken away again", not dtbo.exists())

hardware.write_dtbo("irate-box-serial", "old dts", run=fake_dtc(True))
before = dtbo.read_bytes()
try:
    hardware.write_dtbo("irate-box-serial", "bad dts", run=fake_dtc(False))
    kind, detail = None, ""
except hardware.OverlayError as exc:
    kind, detail = exc.kind, exc.detail
check(
    "a compiler failure: refused, its detail kept, and the working .dtbo still there",
    kind == "dtc-failed" and detail == "syntax error" and dtbo.read_bytes() == before,
    (kind, detail, dtbo.read_bytes() if dtbo.exists() else None),
)
hardware.remove_dtbo("irate-box-serial")
try:
    hardware.write_dtbo("irate-box-serial", "bad dts", run=fake_dtc(False))
    kind = None
except hardware.OverlayError as exc:
    kind = exc.kind
check("  with no previous file, a failure leaves nothing behind", kind == "dtc-failed" and not dtbo.exists(), kind)

try:
    hardware.write_dtbo(
        "irate-box-serial", "dts", run=lambda argv, inp=None: subprocess.CompletedProcess(argv, 127, "", "no dtc")
    )
    kind = None
except hardware.OverlayError as exc:
    kind = exc.kind
check("  dtc missing is its own kind", kind == "dtc-missing", kind)

try:
    hardware.write_dtbo("../escape", "dts", run=fake_dtc(True))
    kind = None
except hardware.OverlayError as exc:
    kind = exc.kind
check("a name that is not an overlay name is refused", kind == "bad-name", kind)

env.write_text("user_overlays=mine\n")
hardware.add_overlay("irate-box-watchdog", "dts", run=fake_dtc(True))
check(
    "add_overlay: the file and the name go together",
    (BOOT / "overlay-user" / "irate-box-watchdog.dtbo").is_file()
    and env.read_text() == "user_overlays=mine irate-box-watchdog\n",
    env.read_text(),
)
hardware.remove_overlay("irate-box-watchdog")
check(
    "remove_overlay: both go away, the owner's kept",
    not (BOOT / "overlay-user" / "irate-box-watchdog.dtbo").exists() and env.read_text() == "user_overlays=mine\n",
    env.read_text(),
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
