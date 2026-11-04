# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""An install that finished with problems is not a stopped install: hub_control._problem_only_install,
_run_install's result and _install_fetched's record and state, plus the doctor's state sync.
python3 tests/sim_update_install.py"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="update-install-"))
(T / "log").mkdir()
(T / "etc").mkdir()
(T / "etc" / "install-options").write_text("--web\nnginx\n")
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_LOG_DIR=str(T / "log"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_UPDATE_DIR=str(T / "cache" / "src"),
    HUB_WEB_SERVER="nginx",
)
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control as H

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


state_file = T / "log" / "install-state.json"


def install_state(aborted=False, problems=("could not set Syncthing's GUI password",), started=None):
    state_file.write_text(
        json.dumps(
            {
                "started": int(time.time() if started is None else started),
                "aborted": aborted,
                "problems": list(problems),
                "exit": 1,
            }
        )
    )


with mock.patch.object(H.health, "INSTALL_STATE", state_file):
    install_state()
    check(
        "a finished installer with problems is handed back, not raised",
        H._problem_only_install(time.time() - 2, 1) == ["could not set Syncthing's GUI password"],
        H._problem_only_install(time.time() - 2, 1),
    )
    install_state(aborted=True)
    check("an aborted installer still raises", H._problem_only_install(time.time() - 2, 1) is None)
    install_state(started=time.time() - 600)
    check("an install-state file from an older run is ignored", H._problem_only_install(time.time() - 2, 1) is None)
    check("a clean exit needs no reading", H._problem_only_install(time.time() - 2, 0) is None)

written = {}
state = {
    "installed": "old",
    "security": {"changes": {"widenings": [], "narrowings": [], "neutral": []}, "levels": {}},
    "tag": None,
}
with (
    mock.patch.object(H, "_run_install", lambda *a, **k: (12, ["could not set Syncthing's GUI password"])),
    mock.patch.object(H, "_installed_commit", lambda: "new"),
    mock.patch.object(H.secchanges, "record_install", lambda *a, **k: None),
    mock.patch.object(H, "_read_update_state", lambda: dict(state)),
    mock.patch.object(H, "_write_update_state", lambda data: written.update(data)),
    mock.patch.object(H, "_doctor_again", lambda: None),
    mock.patch.object(H.signing, "load", lambda etc: {"level": "off"}),
    mock.patch.object(H.secchanges, "levels", lambda: {"root_actions": "authorise"}),
):
    said = H._install_fetched(state)
check(
    "a problem-only install is recorded and the state follows the installed commit",
    said.get("id") == "update.install.done-problems"
    and written.get("installed") == "new"
    and written.get("up_to_date") is True
    and written.get("install_steps") == 12
    and "security_approved_sha" in written
    and written["security_approved_sha"] is None,
    (said, written),
)

written.clear()


def stop(*args, **kwargs):
    raise H.Refused(H.M("test.stop", "stopped"))


aborted = {**state, "security_approved_sha": "new", "security_approved_commit": "new"}
with (
    mock.patch.object(H, "_run_install", stop),
    mock.patch.object(H, "_read_update_state", lambda: dict(aborted)),
    mock.patch.object(H, "_write_update_state", lambda data: written.update(data)),
):
    try:
        H._install_fetched(aborted)
    except H.Refused:
        pass
check(
    "an aborted install clears the approval flag it cannot use",
    written.get("security_approved_sha") is None and written.get("security_approved_commit") is None,
    written,
)

written.clear()
stale = {
    "installed": "old",
    "available_sha": "new",
    "checks": [{"id": "security", "ok": False, "warn": False}],
    "security": {"held": [{"toggle": "root_actions", "text": "a widening"}]},
    "security_approved_sha": "new",
}
with (
    mock.patch.object(H, "_installed_commit", lambda: "new"),
    mock.patch.object(H, "_read_update_state", lambda: dict(stale)),
    mock.patch.object(H, "_write_update_state", lambda data: written.update(data)),
    mock.patch.object(H, "_doctor_again", lambda: None),
):
    said = H.update_state_sync({})
check(
    "the doctor's sync brings the state to the running commit and clears the older checks",
    said.get("id") == "update.sync.done"
    and written.get("installed") == "new"
    and written.get("checks") == []
    and written.get("security") == {}
    and written.get("up_to_date") is True
    and written.get("security_approved_sha") is None,
    (said, written),
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
