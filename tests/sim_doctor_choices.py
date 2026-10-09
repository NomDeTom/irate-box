#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every choice the box doctor offers gets past the hub: the hub's own pattern (server.py) accepts
each choice health.py does, so no button on the page is refused before it reaches root."""
import os, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="doctor-choices-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_SYSFS=str(T / "sys"), HUB_PROC=str(T / "proc"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.root import crashwatch, health  # noqa: E402
from irate_box.hub import server  # noqa: E402

fails = 0
def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok

offered = ([f"crashwatch-preempt:{lv}" for lv in crashwatch.PREEMPT]
           + [f"crashwatch-{k}:{v}" for k in ("snapshots", "panic", "watchdog") for v in ("on", "off")]
           + ["unit-restart:meshtasticd.service", "unit-enable:irate-box-uplink.service", "kiwix-quarantine:book",
              "other-restart:armbian-led-state.service", "kiwix-rebuild", "kiwix-off", "clock-set:1791580000",
              "rtc-find", "rtc-save", "rtc-remove", "rtc-setup:ds3231:1:0x68"])
for c in offered:
    check(f"{c}: the doctor does it, and the hub lets it through",
          bool(health.CHOICE_RE.match(c)) and bool(server.HEALTH_CHOICE_RE.match(c)))
for bad in ("crashwatch-preempt:moon", "crashwatch-rm:on", "crashwatch-panic:on;reboot", "unit-restart:a b"):
    check(f"{bad}: refused by the hub", not server.HEALTH_CHOICE_RE.match(bad))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
