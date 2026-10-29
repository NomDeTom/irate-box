#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every choice the box doctor offers gets past the hub: the hub's own pattern (server.py) accepts
each choice health.py does, so no button on the page is refused before it reaches root."""

import ast
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
from irate_box.root import crashwatch, health

fails = 0


def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok


check(
    "one shared pattern: the hub and the root helper use the same object",
    health.CHOICE_RE is choices.CHOICE_RE and server.HEALTH_CHOICE_RE is choices.CHOICE_RE,
)
groups = health.card_groups() + health.hardening_groups() + health.setting_groups(crashwatch.load_settings(), None)
group_choices = [option["choice"] for group in groups for option in group.get("options", [])]
check(
    "every choice a settings group offers passes the shared pattern",
    all(choices.CHOICE_RE.match(choice) for choice in group_choices),
    [choice for choice in group_choices if not choices.CHOICE_RE.match(choice)],
)
source = ast.parse((REPO / "irate_box" / "root" / "health.py").read_text())
actions = {
    node.args[0].value
    for node in ast.walk(source)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "action"
    and node.args
    and isinstance(node.args[0], ast.Constant)
    and isinstance(node.args[0].value, str)
}


# The page completes clock-set with the time it read; it is the one action whose literal is a prefix.
def action_choice(choice):
    return "clock-set:1791580000" if choice == "clock-set" else choice


check(
    "every literal finding action passes the shared pattern",
    all(choices.CHOICE_RE.match(action_choice(choice)) for choice in actions),
    sorted(choice for choice in actions if not choices.CHOICE_RE.match(action_choice(choice))),
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
