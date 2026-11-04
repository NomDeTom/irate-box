# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The SPI1 LCD and touch overlay (root/lcdtouch.py), offline: the Lyra Zero W's armbianEnv on and off,
the .dtbo written and removed, the pending state until both spidev devices appear, another board refused,
and dtc missing said. No board is touched. python3 tests/sim_lcdtouch.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="lcdtouch-"))
(T / "boot").mkdir()
(T / "state" / "control").mkdir(parents=True)
(T / "dt").mkdir()
(T / "spi").mkdir()
(T / "etc").mkdir()
(T / "boot_id").write_text("boot-one\n")
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
os.environ.update(
    HUB_BOOT_DIR=str(T / "boot"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_BOOT_ID=str(T / "boot_id"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_SPI_SYS=str(T / "spi"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import lcdtouch

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except Refused as exc:
        return exc.message["id"]
    return None


def fake_dtc(*cmd, inp=None, timeout=60):
    out = Path(cmd[cmd.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("fake dtbo")
    return subprocess.CompletedProcess(cmd, 0, "", "")


env = T / "boot" / "armbianEnv.txt"
env.write_text("verbosity=1\nuser_overlays=irate-box-watchdog\n")
lcdtouch.run = fake_dtc

check(
    "a Lyra Zero W: supported, off, no devices yet",
    lcdtouch.status()["supported"] is True
    and lcdtouch.status()["on"] is False
    and lcdtouch.status()["pending"] is False
    and lcdtouch.status()["pins"][0] == {"signal": "SPI1 CLK", "pin": 27, "soc": "GPIO1_B1", "rmio": 24, "gpio": 41},
    lcdtouch.status(),
)
lcdtouch.set_overlay(True)
check(
    "on: the .dtbo written and the name added, off the other overlays left alone",
    lcdtouch.DTBO.is_file()
    and "user_overlays=irate-box-watchdog luckfox-lyra-zero-w-spi1-lcd-touch" in env.read_text()
    and lcdtouch.status()["on"] is True
    and lcdtouch.status()["pending"] is True
    and lcdtouch.status()["active"] is False,
    (env.read_text(), lcdtouch.status()),
)
check(
    "  the page's copy says the same",
    lcdtouch.CONTROL.is_file() and '"pending": true' in lcdtouch.CONTROL.read_text(),
    lcdtouch.CONTROL.read_text() if lcdtouch.CONTROL.is_file() else "",
)
(T / "spi" / "spi1.0").write_text("")
(T / "spi" / "spi1.1").write_text("")
check(
    "  both spidev devices: active, no longer pending",
    lcdtouch.status()["active"] is True and lcdtouch.status()["pending"] is False,
    lcdtouch.status(),
)
calls = []
lcdtouch.lcdpanel.show_card = lambda: calls.append("card")
lcdtouch.lcdpanel.off = lambda: calls.append("off")
lcdtouch.lcdpanel.run_test = lambda n: calls.append(("test", n))
check(
    "display on: the test card drawn, and the state says on",
    lcdtouch.set_display(True).get("id") == "lcdtouch.display-on"
    and calls[-1] == "card"
    and lcdtouch.status()["display_on"] is True,
    (calls, lcdtouch.status()),
)
check(
    "display off: blanked, and the state says off",
    lcdtouch.set_display(False).get("id") == "lcdtouch.display-off"
    and calls[-1] == "off"
    and lcdtouch.status()["display_on"] is False,
    (calls, lcdtouch.status()),
)
check(
    "the one-minute test: run for its time, then off",
    lcdtouch.run_screen_test(1).get("id") == "lcdtouch.test-done"
    and calls[-1] == ("test", 1)
    and lcdtouch.status()["display_on"] is False,
    (calls, lcdtouch.status()),
)
lcdtouch.set_display(True)
(T / "boot_id").write_text("boot-two\n")
check(
    "a restart clears the display state: the picture is gone",
    lcdtouch.status()["display_on"] is False,
    lcdtouch.status(),
)
(T / "boot_id").write_text("boot-one\n")
lcdtouch.lcdpanel.show_card = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
check(
    "a panel error is refused, with its detail",
    refused(lcdtouch.set_display, True) == "lcdtouch.display-failed",
)
lcdtouch.lcdpanel.show_card = lambda: calls.append("card")
real_active = lcdtouch._active
lcdtouch._active = lambda: False
check(
    "the overlay not active: the display button is refused until a restart",
    refused(lcdtouch.set_display, True) == "lcdtouch.display-not-ready",
)
lcdtouch._active = real_active
lcdtouch.set_overlay(False)
check(
    "off: the .dtbo gone and only our name taken away",
    not lcdtouch.DTBO.exists()
    and "user_overlays=irate-box-watchdog" in env.read_text()
    and "luckfox-lyra-zero-w-spi1-lcd-touch" not in env.read_text()
    and lcdtouch.status()["on"] is False,
    (env.read_text(), lcdtouch.status()),
)
lcdtouch.set_overlay(True)
lcdtouch.undo_all()
check("uninstall: undo-all takes it away", lcdtouch.status()["on"] is False and not lcdtouch.DTBO.exists())

lcdtouch.run = lambda *a, **k: subprocess.CompletedProcess(a, 127, "", "dtc: not found")
check(
    "dtc missing: refused, and no half-written .dtbo left",
    refused(lcdtouch.set_overlay, True) == "lcdtouch.dtc-missing" and not lcdtouch.DTBO.exists(),
)
lcdtouch.run = fake_dtc

(T / "dt" / "model").write_bytes(b"Banana Pi BPI-M2-Zero\0")
check(
    "another board: not supported, and on is refused",
    lcdtouch.status()["supported"] is False and refused(lcdtouch.set_overlay, True) == "lcdtouch.unsupported",
    lcdtouch.status(),
)
check("  off still removes anything left behind", lcdtouch.set_overlay(False).get("id") == "lcdtouch.off")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
