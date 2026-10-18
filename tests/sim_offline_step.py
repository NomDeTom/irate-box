# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's offline-readiness step: a box that has
not been online for a month, no offline kit; then a fresh one. python3 tests/sim_offline_step.py"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="offline-step-"))
STATE, LISTS, CODE = T / "state", T / "lists", T / "code"
for d in (
    STATE / "control",
    STATE / "library" / "debsecan" / "release" / "1",
    STATE / "kits",
    LISTS,
    CODE,
    T / "kits-cache",
):
    d.mkdir(parents=True)
(T / "os-release").write_text('VERSION_CODENAME="trixie"\n')
(CODE / "VERSION").write_text("abc1234 (installed 2026-10-08)\n")
os.environ.update(
    HUB_STATE_DIR=str(STATE),
    HUB_APT_LISTS=str(LISTS),
    HUB_CODE_DIR=str(CODE),
    HUB_OS_RELEASE=str(T / "os-release"),
    HUB_KITS_ROOT=str(T / "kits-cache"),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


old = time.time() - 45 * 86400
os.utime(LISTS, (old, old))
got = {f["id"]: f for f in sd.step_offline({})}
check(
    "lists 45 days old, the tracker data never fetched: a warning naming both",
    got["offline-lists"]["status"] == "warn"
    and msg_args(got["offline-lists"], "offline.age.lists") == {"n": 45}
    and "offline.age.tracker.never" in msg_ids(got["offline-lists"]),
    got["offline-lists"],
)
check(
    "no offline kit yet: said",
    got["offline-bundle"]["status"] == "warn" and "offline.bundle.none" in msg_ids(got["offline-bundle"]),
)
check("an empty kits cache is whole", got["offline-kits"]["status"] == "ok")
check("no update state: nothing said of it", "offline-update" not in got)

now = time.time()
os.utime(LISTS, (now, now))
feed = STATE / "library" / "debsecan" / "release" / "1" / "trixie"
feed.write_text("x")
(STATE / "kits" / "kit.json").write_text(json.dumps({"name": "irate-box-kit-abc1234-armv7l.tar", "at": now}))
(STATE / "control" / "update.json").write_text(json.dumps({"fetched": now - 86400, "up_to_date": True}))
got = {f["id"]: f for f in sd.step_offline({})}
check(
    "fresh lists and data, the kit of this version, an update looked for yesterday: all ok",
    all(f["status"] == "ok" for f in got.values()) and "offline.bundle.current" in msg_ids(got["offline-bundle"]),
    {k: (v["status"], v["summary"], v["notes"]) for k, v in got.items()},
)
(STATE / "kits" / "kit.json").write_text(
    json.dumps({"name": "irate-box-kit-0ld0000-armv7l.tar", "at": now - 10 * 86400})
)
(STATE / "control" / "update.json").write_text(
    json.dumps({"fetched": now - 40 * 86400, "up_to_date": False, "available": "def5678"})
)
got = {f["id"]: f for f in sd.step_offline({})}
check(
    "a kit of another version, an update not looked for in 40 days: warnings",
    got["offline-bundle"]["status"] == "warn"
    and "offline.bundle.old" in msg_ids(got["offline-bundle"])
    and "VERSION abc1234" in got["offline-bundle"]["evidence"]
    and got["offline-update"]["status"] == "warn"
    and msg_args(got["offline-update"], "offline.update.available") == {"version": "def5678"},
    (got["offline-bundle"], got["offline-update"]),
)
check("the step is in the doctor's list", "offline" in [s[0] for s in sd.STEPS])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
