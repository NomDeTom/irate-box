# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's WiFi chip finding, and the USB gadget named as the way in: the AIC8800 driver
loaded with no WiFi client while the kernel repeats -19, against the Lyra's 2026-11-04 lines.
python3 tests/sim_wifi_usb.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="wifi-usb-"))
SYS = T / "sys"
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_SYSFS=str(SYS),
    HUB_UPLINK_RUN=str(T / "run"),
)
T.joinpath("etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.msg import render
from irate_box.root import crashwatch as cw
from irate_box.root import health

FIXTURE = (REPO / "tests/fixtures/aic8800-usb-hang-2026-11-04/journal.txt").read_text()
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def CP(cmd, code=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, code, out, err)


IWD = "phy#0\n\tInterface wlan0\n\t\ttype managed\n"
kernel = FIXTURE


def fake_run(*cmd, timeout=60, **kw):
    if cmd[:2] == ("iw", "dev"):
        return CP(cmd, 0, IWD)
    if cmd[:1] == ("journalctl",):
        return CP(cmd, 0, kernel)
    return CP(cmd, 0, "", "")


health.run = fake_run
gadget = {
    "peripheral": True,
    "settings": {"on": True},
    "up": True,
    "dnsmasq": True,
    "modules": {},
    "why": None,
}
health.usb_gadget_status = lambda: gadget
(SYS / "module/aic8800_fdrv").mkdir(parents=True)

# The finding: driver loaded, no client, the boot's -19 lines repeating.
IWD = ""
f = {x["id"]: x for x in health.check_wifi_gone()}
check("the AIC driver loaded, no client, -19 repeating: a problem", f["wifi-chip-gone"]["status"] == "problem", f)
check(
    "  it says how many lines and keeps one as evidence",
    msg_args(f["wifi-chip-gone"], "wifi-chip.summary")["n"] >= 3
    and any("fail:-19" in e for e in f["wifi-chip-gone"]["evidence"]),
    f["wifi-chip-gone"]["evidence"],
)
check(
    "  a hardware reset, never a soft reboot, and the setting's outcome when it is off",
    {"wifi-chip.fix.off", "wifi-chip.bus"} <= msg_ids(f["wifi-chip-gone"])
    and "reboot" not in render(f["wifi-chip-gone"]["fix"]).lower().replace("soft reboot", "")
    and not f["wifi-chip-gone"]["actions"],
    f["wifi-chip-gone"],
)
check(
    "  the USB gadget named as the way in, at its address and console",
    "wifi-chip.gadget" in msg_ids(f["wifi-chip-gone"])
    and "192.168.7.1" in (msg_args(f["wifi-chip-gone"], "wifi-chip.gadget") or {}).get("address", "")
    and "ttyGS0" in (msg_args(f["wifi-chip-gone"], "wifi-chip.gadget") or {}).get("tty", ""),
    f["wifi-chip-gone"],
)

# The owner's setting changes what the finding says the box will do.
cw.SETTINGS.write_text('{"chip_gone": "reset"}')
f = {x["id"]: x for x in health.check_wifi_gone()}["wifi-chip-gone"]
check(
    "with chip_gone reset: the finding says the box resets itself",
    "wifi-chip.fix.reset" in msg_ids(f) and "wifi-chip.fix.off" not in msg_ids(f),
    f,
)
cw.SETTINGS.write_text('{"chip_gone": "nonsense"}')
f = {x["id"]: x for x in health.check_wifi_gone()}["wifi-chip-gone"]
check("  anything else is read as off", "wifi-chip.fix.off" in msg_ids(f), f)
cw.SETTINGS.unlink()

# Not this failure: a client is there, the driver is not loaded, or -19 does not repeat.
IWD = "phy#0\n\tInterface wlan0\n\t\ttype managed\n"
check("a WiFi client interface there: nothing", health.check_wifi_gone() == [])
IWD = "phy#0\n\tInterface ap0\n\t\ttype AP\n"
check("  a hotspot interface is not a client: the failure still shows", bool(health.check_wifi_gone()))
IWD = ""
kern = "\n".join(line for line in FIXTURE.splitlines() if "fail:-19" not in line)
kernel = kern + "\n[10000.0] usb submit msg rx urb fail:-19\n[10001.0] usb submit msg rx urb fail:-19\n"
check("  two -19 lines are not a repeat: nothing", health.check_wifi_gone() == [])
kernel = FIXTURE
(SYS / "module/aic8800_fdrv").rename(SYS / "module/aic8800_fdrv.off")
check("  the AIC driver not loaded: nothing", health.check_wifi_gone() == [])
(SYS / "module/aic8800_fdrv.off").rename(SYS / "module/aic8800_fdrv")

# USB networking's own finding names the fallback too.
g = {x["id"]: x for x in health.check_usb_gadget()}
check(
    "USB networking up: the finding says it is the way in when the WiFi is down",
    g["usb-gadget"]["status"] == "ok" and "usb-gadget.ok.fallback" in msg_ids(g["usb-gadget"]),
    g["usb-gadget"],
)

print("\n" + ("OK" if not fails else f"{fails} FAILURES"))
sys.exit(1 if fails else 0)
