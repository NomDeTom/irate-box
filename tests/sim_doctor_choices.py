#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every choice the box doctor offers gets past the hub: the hub's own pattern (server.py) accepts
each choice health.py does, so no button on the page is refused before it reaches root."""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="doctor-choices-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"), HUB_SYSFS=str(T / "sys"), HUB_PROC=str(T / "proc"), HUB_ETC_DIR=str(T / "etc")
)
sys.path.insert(0, str(REPO))
from irate_box import choices
from irate_box.hub import server
from irate_box.root import health

fails = 0


def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok


check(
    "one shared pattern: the hub and the root helper use the same object",
    health.CHOICE_RE is choices.CHOICE_RE and server.HEALTH_CHOICE_RE is choices.CHOICE_RE,
)
offered = [f"crashwatch-{k}:{v}" for k in ("snapshots", "panic", "watchdog", "undervoltage") for v in ("on", "off")] + [
    "crashwatch-undervoltage-mv:4630",
    "crashwatch-power-action:shutdown",
    "card-ram:irate-box.service:0",
    "card-ram-off:irate-box.service:0",
    "card-ram-apply",
    "unit-restart:meshtasticd.service",
    "unit-enable:irate-box-uplink.service",
    "kiwix-quarantine:book",
    "other-restart:armbian-led-state.service",
    "kiwix-rebuild",
    "kiwix-off",
    "clock-set:1791580000",
    "rtc-find",
    "rtc-look",
    "rtc-save",
    "rtc-remove",
    "rtc-setup:ds3231:1:0x68",
    "clock-sources:chrony:1:0:1:1:192.168.1.1",
    "clock-sources:chrony:1:0:1:1:192.168.1.1:pool,local,gps,module",
    "clock-sources:image:1:0:0:0:-:local,pool",
    "clock-undo",
    "uplink-skip:wlan0:restart",
    "uplink-skip:eth0:radio",
    "idle-off:meshtasticd.service",
    "idle-on:bluetooth.service",
    "idle-off:ModemManager.service",
    "rerun-install",
    "rerun-install-port-80",
    "net-scan",
]
for c in offered:
    check(
        f"{c}: the doctor does it, and the hub lets it through",
        bool(health.CHOICE_RE.match(c)) and bool(server.HEALTH_CHOICE_RE.match(c)),
    )
for bad in (
    "clock-sources:chrony:1:0:1:1:-:pool,disk",
    "clock-sources:chrony:1:0:1:1:-:pool;reboot",
    "crashwatch-preempt:reboot",
    "crashwatch-rm:on",
    "crashwatch-panic:on;reboot",
    "unit-restart:a b",
    "uplink-skip:wlan0:nap",
    "uplink-skip:-x:radio",
    "uplink-skip:wlan0",
    "idle-off:ssh.service",
    "idle-off:meshtasticd",
):
    check(f"{bad}: refused by the hub", not server.HEALTH_CHOICE_RE.match(bad))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
