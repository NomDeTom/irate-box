# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's manifest step (root/secdoctor.py): the running box against the installed
security.json, for a secret's mode, a unit's sandbox line and the hub user's groups.
python3 tests/sim_secdoctor_manifest.py"""

import grp
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="secdoctor-manifest-"))
for d in ("etc", "state", "code", "state/control"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_USER="hub",
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(
        ("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")
    )  # lgtm[py/clear-text-logging-sensitive-data]
    fails += not cond


secret = T / "etc" / "admin-password"
secret.write_text("secret\n")
secret.chmod(0o644)
manifest = {
    "version": 1,
    "secrets": {
        str(secret): {
            "owner": os.environ.get("USER", "root"),
            "group": grp.getgrgid(secret.stat().st_gid).gr_name,
            "mode": "600",
            "readers": ["root"],
        }
    },
    "units": {"irate-box.service": {"User": "hub", "ProtectSystem": "strict", "PrivateTmp": "yes"}},
    "hub": {"groups": [], "secrets": []},
}
(T / "code" / "security.json").write_text(json.dumps(manifest))

said = {f["id"]: f for f in sd.step_manifest({})}
check(
    "a secret readable by others: a problem, naming the file and both modes",
    said["manifest-secrets"]["status"] == "problem"
    and any("644" in e and "600" in e for e in said["manifest-secrets"]["evidence"]),
    said["manifest-secrets"],
)

secret.chmod(0o600)
with mock.patch.object(
    sd,
    "_show",
    lambda name, *props: {
        "LoadState": "loaded",
        "User": "hub",
        "ProtectSystem": "yes",
        "PrivateTmp": "yes",
        "NoNewPrivileges": "yes",
    },
):
    with mock.patch.object(sd.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "hub\n"})()):
        said = {f["id"]: f for f in sd.step_manifest({})}
check(
    "  with the declared mode back, the secret is fine",
    said["manifest-secrets"]["status"] == "ok",
    said["manifest-secrets"],
)
check(
    "a unit whose ProtectSystem is weaker than the manifest: a problem",
    said["manifest-units"]["status"] == "problem"
    and any("ProtectSystem" in e for e in said["manifest-units"]["evidence"]),
    said["manifest-units"],
)
check(
    "  the hub user's primary group alone is not a widening",
    said["manifest-hub"]["status"] == "ok",
    said["manifest-hub"],
)

with mock.patch.object(sd, "_show", lambda name, *props: {}):

    def id_run(*args, **kwargs):
        return type("R", (), {"returncode": 0, "stdout": "hub\n" if "-gn" in args[0] else "hub video\n"})()

    with mock.patch.object(sd.subprocess, "run", id_run):
        said = {f["id"]: f for f in sd.step_manifest({})}
check(
    "the hub user in an undeclared group: a problem, naming it",
    said["manifest-hub"]["status"] == "problem" and said["manifest-hub"]["evidence"] == ["video"],
    said["manifest-hub"],
)

(T / "code" / "security.json").unlink()
said = sd.step_manifest({})
check(
    "no manifest in the installed code: a warning, not a clean bill",
    len(said) == 1 and said[0]["status"] == "warn" and "manifest.none" in message_ids(said[0]),
    said,
)
check(
    "the step is in the doctor's list, before the tools it needs",
    [s[0] for s in sd.STEPS][-2:] == ["manifest", "tools"],
    [s[0] for s in sd.STEPS][-3:],
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
