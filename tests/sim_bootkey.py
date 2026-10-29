# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Lyra's BOOT key as a clean-shutdown button (root/bootkey.py; notes: box-doctor-hardening-plan Part 3,
step 3), offline: a three-second hold powers off, a short press does nothing, a board without the reading does
nothing, the unit text and switching it on and off. python3 tests/sim_bootkey.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="bootkey-"))
(T / "dt").mkdir()
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
os.environ.update(
    HUB_SYSTEMD_ETC=str(T / "systemd"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_BOOTKEY_ADC=str(T / "adc"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_ETC_DIR=str(T / "etc"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import bootkey, health

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def clocked(values):
    at = [0.0]

    def reader():
        return values.pop(0) if values else None

    def clock():
        at[0] += 0.2
        return at[0]

    return reader, clock


off = []
reader, clock = clocked([1019, 1019, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9])
said = bootkey.watch(reader=reader, clock=clock, poweroff=lambda: off.append(True), hold=1.0, poll=0, stop_after=12)
check("a hold of the set time powers off cleanly", off and said.get("id") == "bootkey.shutdown", said)
off.clear()
reader, clock = clocked([1019, 9, 1019, 9, 1019, 1019])
bootkey.watch(reader=reader, clock=clock, poweroff=lambda: off.append(True), hold=1.0, poll=0, stop_after=12)
check("  a short press does nothing", not off)
off.clear()
reader, clock = clocked([9, 9, 9, 9, 1019, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9])
bootkey.watch(reader=reader, clock=clock, poweroff=lambda: off.append(True), hold=1.0, poll=0, stop_after=6)
check("  a reading low at start is ignored until the key is seen released", not off)
off.clear()
reader, clock = clocked([1019, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9])
bootkey.watch(reader=reader, clock=clock, poweroff=lambda: off.append(True), hold=1.0, poll=0, stop_after=20)
check("  after a release, a hold powers off", bool(off))
real_euid = os.geteuid
os.geteuid = lambda: 0
check("  a board with no reading: the service returns at once", bootkey.main(["run"]) is None)
os.geteuid = real_euid
Path(os.environ["HUB_BOOTKEY_ADC"]).write_text("1019\n")
check("  supported: the Lyra with its ADC, not another board", bootkey.supported())
(T / "dt" / "model").write_bytes(b"Raspberry Pi 3 Model B Plus Rev 1.3\0")
check("  a Pi is not supported", not bootkey.supported())
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
check(
    "  the unit runs the module and is enabled at boot",
    "bootkey run" in bootkey.unit_text() and "WantedBy=multi-user.target" in bootkey.unit_text(),
)

calls = []
bootkey.run = lambda *a, **k: calls.append(a) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
said = bootkey.install_units(True)
unit = Path(os.environ["HUB_SYSTEMD_ETC"]) / "system" / bootkey.UNIT
check(
    "switched on: the unit written and started",
    said.get("id") == "bootkey.on" and unit.is_file() and ("systemctl", "enable", "--now", bootkey.UNIT) in calls,
    calls,
)
said = bootkey.install_units(False)
check(
    "  switched off: the unit stopped and removed",
    said.get("id") == "bootkey.off" and not unit.exists() and ("systemctl", "disable", "--now", bootkey.UNIT) in calls,
)


def no_adc():
    raise AssertionError("not reached")


os.environ["HUB_BOOTKEY_ADC"] = str(T / "missing")
bootkey.ADC = Path(os.environ["HUB_BOOTKEY_ADC"])
try:
    bootkey.install_units(True)
    check("  a board without the reading refuses to switch it on", False)
except Refused as exc:
    check("  a board without the reading refuses to switch it on", exc.message["id"] == "bootkey.not-lyra")

bootkey.ADC = Path(T / "adc")
os.environ["HUB_BOOTKEY_ADC"] = str(bootkey.ADC)
check(
    "the Recovery setting is offered on the Lyra, and health.fix switches it",
    health.CHOICE_RE.match("bootkey:on")
    and any(
        g["key"] == "bootkey"
        for g in health.setting_groups(
            {
                "snapshots": False,
                "panic": False,
                "watchdog": False,
                "undervoltage": True,
                "undervoltage_mv": 4630,
                "power_action": "log",
            },
            None,
        )
    ),
)
Path(os.environ["HUB_BOOTKEY_ADC"]).write_text("1019\n")
said = health.fix("bootkey:on")
check("  health.fix switches it on and off", said.get("id") == "bootkey.on", said)
said = health.fix("bootkey:off")
check("  and off", said.get("id") == "bootkey.off", said)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
