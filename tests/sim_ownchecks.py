# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The owner's own checks (/etc/irate-box/checks.d/*.json), offline: one named group per file in the manifests'
check format, a failing check a finding of its own, a pass silent, and a broken file said. python3 tests/sim_ownchecks.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="ownchecks-"))
(T / "checks.d").mkdir()
(T / "root" / "etc").mkdir(parents=True)
(T / "root" / "etc" / "own.conf").write_text("enabled=no\n")
os.environ.update(HUB_CHECKS_D=str(T / "checks.d"), HUB_PRESENT_ROOT=str(T / "root"))
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


good = {
    "name": "The workshop's own service",
    "checks": [
        {
            "id": "enabled",
            "kind": "config",
            "path": "/etc/own.conf",
            "status": "warn",
            "title": "Enabled",
            "says": "The workshop service is not enabled.",
            "fix": "Set enabled=yes in /etc/own.conf.",
            "present": "^enabled=yes",
        }
    ],
}
(T / "checks.d" / "workshop.json").write_text(json.dumps(good))
issues = secdoctor.own_checks()
check(
    "a named group's failing check is a finding of its own, under the file's key",
    len(issues) == 1
    and issues[0]["key"] == "checks-workshop:enabled"
    and issues[0]["status"] == "warn"
    and "workshop service" in str(issues[0]["summary"]),
    issues,
)
(T / "root" / "etc" / "own.conf").write_text("enabled=yes\n")
check("  a pass says nothing", secdoctor.own_checks() == [])
(T / "checks.d" / "broken.json").write_text("{not json")
issues = secdoctor.own_checks()
check(
    "a broken file is said, not fatal",
    len(issues) == 1 and issues[0]["key"] == "checks-broken" and issues[0]["status"] == "warn",
    issues,
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
