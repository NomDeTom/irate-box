# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The modular MUI pin selection and overlay builder (root/mui.py), offline: candidates, validation,
DTS/config/service generation, activate/pending/active and deactivate. python3 tests/sim_mui.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="mui-"))
for sub in ("boot/overlay-user", "state/control", "dt/__symbols__", "spi", "units", "bin"):
    (T / sub).mkdir(parents=True)
(T / "boot_id").write_text("boot-one\n")
(T / "dt" / "model").write_bytes(b"Luckfox Lyra Zero W\0")
for bus in (0, 1):
    for signal, rmio in (("clk", 24), ("mosi", 25), ("miso", 26), ("csn0", 16), ("csn1", 28)):
        (T / "dt" / "__symbols__" / f"rm_io{rmio}_spi{bus}_{signal}").write_text("/pinctrl/x\0")
os.environ.update(
    HUB_BOOT_DIR=str(T / "boot"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_BOOT_ID=str(T / "boot_id"),
    HUB_DEVICE_TREE=str(T / "dt"),
    HUB_SPI_SYS=str(T / "spi"),
    HUB_UNIT_DIR=str(T / "units"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import hardware, mui

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


seen_dts = []


def fake_dtc(argv, inp=None):
    seen_dts.append(inp)
    out = Path(argv[argv.index("-o") + 1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("fake dtbo")
    return subprocess.CompletedProcess(argv, 0, "", "")


calls = []


def fake_systemctl(*args):
    calls.append(args)
    return subprocess.CompletedProcess(("systemctl",) + args, 0, "active", "")


binary = T / "bin" / "meshtasticd"
binary.write_text("#!/bin/sh\n")
binary.chmod(0o755)
mui.DEFAULT_BINARY = str(binary)
env = T / "boot" / "armbianEnv.txt"
env.write_text("verbosity=1\nuser_overlays=irate-box-watchdog\n")

mui._snapshot = lambda: {"header": []}
hardware._dtc = fake_dtc
mui._systemctl = fake_systemctl
mui.MESHTASTIC_CONFIG = T / "meshtasticd.yaml"
mui.MESHTASTIC_CONFIG_DIR = T / "meshtasticd.d"
mui.MESHTASTIC_CONFIG_DIR.mkdir()
sel = {}

check("a Lyra Zero W is supported", mui.supported() is True)
opt = mui.options()
check(
    "options offer SPI1 CLK/MOSI/MISO and both chip selects",
    [x["pin"] for x in opt["buses"][1]["signals"]["clk"]] == [27]
    and [x["pin"] for x in opt["buses"][1]["signals"]["mosi"]] == [29]
    and [x["pin"] for x in opt["buses"][1]["signals"]["miso"]] == [31]
    and sorted(x["pin"] for x in opt["buses"][1]["signals"]["csn0"] + opt["buses"][1]["signals"]["csn1"]) == [35, 37],
    opt["buses"],
)
check(
    "options carry root's usable/why for every candidate",
    opt["buses"][1]["signals"]["clk"][0]["usable"] is True
    and opt["buses"][1]["signals"]["clk"][0]["why"] == "free"
    and next(row for row in opt["gpios"] if row["pin"] == 8)["usable"] is False
    and "fixed for UART0 TX" in next(row for row in opt["gpios"] if row["pin"] == 8)["why"],
    opt["gpios"],
)
check(
    "defaults are the proven SPI1 layout",
    opt["defaults"]["bus"] == 1
    and opt["defaults"]["clk"] == 27
    and opt["defaults"]["display_cs"] == 35
    and opt["defaults"]["touch_cs"] == 37
    and opt["defaults"]["dc"] == 26
    and opt["defaults"]["rst"] == 28
    and opt["defaults"]["bl"] == 36,
    opt["defaults"],
)
check(
    "a pin that offers both CS numbers uses the one the display does not",
    mui._cs_candidate(1, 37, avoid=0)["cs"] == 1 and mui._validate({})["_touch_cs"]["cs"] == 1,
)
check(
    "the overlay names the selected bus, CS and backlight",
    "target = <&spi1>" in mui.dts(sel)
    and "&rm_io24_spi1_clk" in mui.dts(sel)
    and "&rm_io16_spi1_csn0" in mui.dts(sel)
    and "&rm_io28_spi1_csn1" in mui.dts(sel)
    and "gpios = <&gpio1 25 0>" in mui.dts(sel)
    and 'default-state = "on"' in mui.dts(sel),
    mui.dts(sel),
)
cfg = mui.config(sel)
check(
    "the config puts display on spidev1.0 and touch on spidev1.1",
    "spidev: spidev1.0" in cfg
    and "spidev: spidev1.1" in cfg
    and "Module: XPT2046" in cfg
    and "pin: 26" in cfg
    and "line: 9" in cfg
    and "Module: sim" in cfg
    and "spidev: spidev0.0" in cfg,
    cfg,
)
check(
    "a display pin cannot be the radio bus",
    refused(mui.dts, {"bus": 0}) == "mui.radio-bus",
)
check(
    "duplicate and power pins are refused",
    refused(mui.dts, {"dc": 27}) == "mui.duplicate-pin" and refused(mui.dts, {"dc": 1}) == "mui.not-gpio",
)
check(
    "a pin without that SPI function is refused",
    refused(mui.dts, {"clk": 12}) == "mui.not-spi",
)
check(
    "an element other than the XPT2046 touch mode is refused",
    refused(mui.dts, {"touch": "kernel"}) == "mui.touch-mode",
)
check(
    "SPI0 is allowed when the radio is told to use no spidev",
    "target = <&spi0>" in mui.dts({"bus": 0, "radio_spidev": ""}),
)
check(
    "a different DC/RST/BL is reflected in the config and overlay",
    "pin: 12" in mui.config({"dc": 12})
    and "gpiochip: 0" in mui.config({"dc": 12})
    and "line: 14" in mui.config({"dc": 12})
    and "gpios = <&gpio0 12 0>" in mui.dts({"bl": 18}),
)
real_snapshot = mui._snapshot
mui._snapshot = lambda: {"header": [{"pin": 26, "used": True, "consumer": "ff120000.spi"}]}
check("a pin owned by the radio is refused", refused(mui.dts, {}) == "mui.pin-busy")
mui._snapshot = real_snapshot
check(
    "unknown selection keys are not kept",
    not ({"_touch_cs", "bogus"} & set(mui.status({"_touch_cs": {"cs": 1}, "bogus": "x"})["selection"])),
)
check(
    "a trailing newline is not a spidev path",
    refused(mui.dts, {"radio_spidev": "spidev0.0\n"}) == "mui.radio-spidev",
)
check(
    "booleans and floats are not pin numbers",
    refused(mui.dts, {"dc": True}) == "mui.bad-selection"
    and refused(mui.dts, {"bus_frequency": 3.7}) == "mui.bad-selection",
)
mui.MESHTASTIC_CONFIG.write_text("Display:\n  Module: sx1262\n")
check("only the Lora section counts as a radio", mui._radio_configured() is False)
mui.MESHTASTIC_CONFIG.write_text("Display:\n  Module: sx1262\nLora:\n  Module: sx1262\n")
check("the Lora section is read", mui._radio_configured() is True)
(mui.MESHTASTIC_CONFIG_DIR / "20-radio.yaml").write_text("Lora:\n  Module: sim\n")
check("a later config.d file wins", mui._radio_configured() is False)
(mui.MESHTASTIC_CONFIG_DIR / "20-radio.yaml").unlink()
check(
    "a real radio configured for meshtasticd is refused unless replace_radio is true",
    refused(mui.activate, sel) == "mui.radio-configured",
)
mui.MESHTASTIC_CONFIG.unlink()
check(
    "MUI only activates with SimRadio",
    refused(mui.dts, {"radio_module": "sx1262"}) == "mui.radio-module.sim",
)

old_env = "verbosity=1\nuser_overlays=irate-box-watchdog luckfox-lyra-zero-w-spi1-lcd-touch\n"
env.write_text(old_env)
real_hardware_dtc = hardware._dtc


def failing_dtc(argv, inp=None):
    return subprocess.CompletedProcess(argv, 1, "", "dtc failed")


hardware._dtc = failing_dtc
check(
    "a failed dtc leaves the old display overlay in the boot line",
    refused(mui.activate, sel) == "mui.dtc-failed"
    and env.read_text() == old_env
    and "irate-box-mui" not in env.read_text(),
    env.read_text(),
)
hardware._dtc = real_hardware_dtc
env.write_text("verbosity=1\nuser_overlays=irate-box-watchdog\n")

msg = mui.activate(sel)
check(
    "activate compiles the DTS it passes to the runner",
    bool(seen_dts) and "/plugin/" in (seen_dts[-1] or "") and "rm_io24_spi1_clk" in (seen_dts[-1] or ""),
    seen_dts[-1] if seen_dts else None,
)
check(
    "activate writes the overlay, config, UI timeout and unit, and leaves the old display overlay behind",
    mui.DTBO.is_file()
    and "user_overlays=irate-box-watchdog irate-box-mui" in env.read_text()
    and mui.CONFIG.is_file()
    and (mui.FSDIR / "prefs" / "uiconfig.proto").read_bytes() == bytes.fromhex("0801109901180040014801")
    and mui.UNIT_PATH.is_file()
    and "Conflicts=meshtasticd.service" in mui.UNIT_PATH.read_text()
    and msg.get("id") == "mui.saved",
    (msg, env.read_text(), mui.UNIT_PATH.read_text() if mui.UNIT_PATH.is_file() else ""),
)
check("  activation is pending until the selected spidev devices exist", mui.status()["pending"] is True)
(T / "spi" / "spi1.0").write_text("")
(T / "spi" / "spi1.1").write_text("")
check("  the same selection becomes active when both devices appear", mui.status()["active"] is True)
mui.activate(sel)
check(
    "  a second activation keeps the earlier meshtasticd record",
    mui._state().get("meshtasticd_enabled") is True,
    mui._state(),
)
check(
    "  activating again replaces the old overlay names, never duplicates",
    "luckfox-lyra-zero-w-spi1-lcd-touch" not in env.read_text() and env.read_text().count("irate-box-mui") == 1,
    env.read_text(),
)
real_binary = mui.DEFAULT_BINARY
mui.DEFAULT_BINARY = "/nope"
check("activate refuses a missing per-host binary", refused(mui.activate, {}) == "mui.no-binary")
mui.DEFAULT_BINARY = real_binary
mui.deactivate()
check(
    "activate disables, and deactivate re-enables, meshtasticd",
    ("disable", "--now", "meshtasticd.service") in calls and ("enable", "--now", "meshtasticd.service") in calls,
    calls,
)
check(
    "deactivate removes the unit and overlay but keeps the saved config",
    not mui.UNIT_PATH.exists()
    and not mui.DTBO.exists()
    and "irate-box-mui" not in env.read_text()
    and mui.CONFIG.is_file()
    and mui.status()["overlay_enabled"] is False,
    (env.read_text(), mui.status()),
)
(T / "dt" / "model").write_bytes(b"Banana Pi BPI-M2-Zero\0")
check("another board is refused", refused(mui.activate, sel) == "mui.unsupported")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
