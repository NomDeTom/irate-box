# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The owner's levels for security changes (root/secchanges.py): the proposed defaults, saving a
level, the console switch (on from the page, off only from the console), an approval that only
matches its own release and changes, and the append-only record. python3 tests/sim_security_changes.py"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="secchanges-"))
for d in ("etc", "state/control", "root"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ROOT_STATE_DIR=str(T / "root"),
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused, render
from irate_box.root import secchanges

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


data = secchanges.load()
check("the proposed defaults: four authorise and two flag", data["toggles"] == secchanges.DEFAULTS, data)
check("  and the console switch starts off", data["console_only"] is False, data)
check(
    "  the effective levels are the same while it is off",
    secchanges.levels(data) == data["toggles"],
    secchanges.levels(data),
)

said = secchanges.save({"toggles": {"root_actions": "console", "outbound": "authorise"}})
check("a level is saved", said["id"] == "security.changes.saved", said)
check(
    "  and published for the page",
    json.loads((T / "state/control/security-changes.json").read_text())["toggles"]["root_actions"] == "console",
)
check("  the file is root's alone", stat.S_IMODE((T / "etc/security-changes.json").stat().st_mode) == 0o600)

said = secchanges.save({"console_only": True})
check("the page may turn the console switch on", said["id"] == "security.changes.saved-console", said)
check("  which raises every authorise to console", secchanges.levels()["outbound"] == "console", secchanges.levels())
check("  and leaves flag at flag", secchanges.levels()["root_code"] == "flag", secchanges.levels())

try:
    secchanges.save({"console_only": False})
    off = None
except Refused as exc:
    off = exc.message
check(
    "the page cannot turn it off: refused, naming the console command",
    off and off["id"] == "security.changes.console-only-off" and "security-console off" in render(off),
    off,
)
check("  and it is still on", secchanges.load()["console_only"] is True)

said = secchanges.console_only_off()
check(
    "the console command turns it off",
    said["id"] == "security.changes.console-off" and not secchanges.load()["console_only"],
    said,
)

changes = ["a new root action, pins"]
record = secchanges.approve("a" * 40, "aaaaaaa", changes, "https", "an administrator over HTTPS", secchanges.levels())
check(
    "an approval names its release, its changes and how it was given",
    record["sha"] == "a" * 40 and record["how"] == "https",
    record,
)
check("  it matches the release and the same changes", secchanges.approved("a" * 40, changes) is not None)
check("  and not another release", secchanges.approved("b" * 40, changes) is None)
check("  nor other changes", secchanges.approved("a" * 40, changes + ["a new host, evil.example"]) is None)

secchanges.record_install(
    "abcdef0",
    "v1",
    "github",
    secchanges.levels(),
    {"widenings": [{"text": changes[0]}], "narrowings": [], "neutral": []},
    "console",
    True,
    ["Security changes"],
)
entries = secchanges.entries()
last = entries[-1]
check(
    "the record keeps the install, its signing level, how and that it was forced",
    last["event"] == "installed" and last["signing"] == "github" and last["how"] == "console" and last["forced"],
    last,
)
check("  the approval is cleared after the install", not (T / "etc/security-approval.json").exists())
check(
    "the record is root's alone, and append-only",
    stat.S_IMODE((T / "root/security-changes.jsonl").stat().st_mode) == 0o600 and len(entries) == 2,
    entries,
)
check(
    "  and reads as it was written", secchanges.record_health() == {"ok": True, "why": ""}, secchanges.record_health()
)
log = T / "root/security-changes.jsonl"
log.write_text(log.read_text().splitlines(keepends=True)[0])
check(
    "a record shorter than it was is found",
    secchanges.record_health() == {"ok": False, "why": "shortened"},
    secchanges.record_health(),
)
log.unlink()
check("  and a missing one", secchanges.record_health() == {"ok": False, "why": "missing"}, secchanges.record_health())

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
