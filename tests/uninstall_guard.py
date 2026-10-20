# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every systemd unit file install.sh writes, uninstall.sh removes: a unit left behind can come back at every boot and run
from code that is gone (audit M6 on the Pi, 2026-10-20: the notebooks' path unit was still enabled after an uninstall).
Read as text: the units install.sh writes (cat >… or a copy into the unit folder) against the files uninstall.sh deletes,
its {a,b} brace lists expanded. A clock module's units are rtc.py's own (rtc remove, which uninstall.sh runs).
python3 tests/uninstall_guard.py"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


inst = (REPO / "install.sh").read_text()
un = (REPO / "uninstall.sh").read_text()
UNIT = r"[A-Za-z0-9@._-]+\.(?:service|path|timer|socket)"
written = set(re.findall(rf"(?:/etc/systemd/system|\$UNITDIR|\$\{{UNITDIR\}})/({UNIT})", inst))


def expand(text):
    """Bash brace lists expanded: irate-box-ci.{path,service} -> irate-box-ci.path irate-box-ci.service."""
    while True:
        m = re.search(r"([^\s{}]*)\{([^{}\s]+,[^{}\s]+)\}([^\s{}]*)", text)
        if not m:
            return text
        text = (
            text[: m.start()] + " ".join(m.group(1) + x + m.group(3) for x in m.group(2).split(",")) + text[m.end() :]
        )


removed = set(re.findall(rf"(?:\$UNITDIR\"?/|/etc/systemd/system/)({UNIT})", expand(un)))
missed = sorted(u for u in written if u not in removed)
check(
    f"uninstall.sh deletes every unit file install.sh writes ({len(written)})", len(written) > 20 and not missed, missed
)
stopped = expand(un.split('say "Removing unit files and drop-ins"')[0])
not_stopped = sorted(u for u in written if u.endswith((".path", ".timer", ".socket")) and u not in stopped)
check("  and stops and disables each path, timer and socket first", not not_stopped, not_stopped)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
