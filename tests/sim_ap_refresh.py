# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hotspot's units brought up to date by an install (ap.refresh_units): one written by an older version
is written again, one already current is left alone, and one never written (no hotspot set up) is not made.
python3 tests/sim_ap_refresh.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="aprefresh-"))
os.environ.update(HUB_UNIT_DIR=str(T), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.root import ap  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


check("no hotspot set up: nothing written", ap.refresh_units() == [] and not list(T.glob("*.service")))
(T / ap.DNSMASQ_UNIT).write_text("[Service]\nExecStart=/usr/sbin/dnsmasq\n")
(T / ap.BOOT_UNIT).write_text(ap.boot_unit())
changed = ap.refresh_units()
check("an older unit written again, a current one left alone", changed == [ap.DNSMASQ_UNIT], changed)
check("  the new text carries the sandbox", "ProtectSystem=strict" in (T / ap.DNSMASQ_UNIT).read_text())
check("  a second run changes nothing", ap.refresh_units() == [])
check("  hostapd's, never written, not made", not (T / ap.HOSTAPD_UNIT).exists())
import shutil  # noqa: E402

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
