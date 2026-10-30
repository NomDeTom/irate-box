# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Generation from the security manifest (root/secmanifest.py): a unit's sandbox lines and a
secret's mode and owner come from security.json, not from the module's own literals; with no
manifest the module's fallback is used; a symlink is not followed. python3 tests/sim_secmanifest_generate.py"""

import grp
import json
import os
import pwd
import stat
import sys
import tempfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="secgen-"))
for d in ("code", "state/control", "etc"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_CODE_DIR=str(T / "code"),
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_ROOT_STATE_DIR=str(T / "root"),
    HUB_SYSTEMD_ETC=str(T / "systemd"),
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import ap, bootkey, health, secmanifest

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


manifest = json.loads((REPO / "security.json").read_text())
manifest["units"][bootkey.UNIT] = {
    "source": "code",
    "ProtectSystem": "full",
    "ReadOnlyPaths": ["/tmp/sys"],
    "PrivateTmp": "no",
    "NoNewPrivileges": "no",
}
manifest["units"][ap.HOSTAPD_UNIT] = {
    "source": "code",
    "ProtectSystem": "strict",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectHome": "yes",
    "ProtectProc": "invisible",
    "ReadWritePaths": ["-/tmp/hostapd"],
}
manifest["units"][health.DOCTOR_SERVICE] = {
    "source": "code",
    "ProtectSystem": "strict",
    "ReadWritePaths": ["/tmp/control", "/tmp/root"],
    "PrivateTmp": "yes",
    "NoNewPrivileges": "yes",
    "ProtectHome": "no",
    "ProtectProc": "invisible",
}
secret = T / "secret"
secret.write_text("secret\n")
secret.chmod(0o644)
me, my_group = pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name
manifest["secrets"] = {
    str(secret): {"owner": me, "group": my_group, "mode": "600", "readers": ["root"]},
}
(T / "code" / "security.json").write_text(json.dumps(manifest))

text = bootkey.unit_text()
check(
    "a unit's sandbox comes from the manifest: ProtectSystem, ReadOnlyPaths, PrivateTmp, NoNewPrivileges",
    "ProtectSystem=full\n" in text
    and "ReadOnlyPaths=/tmp/sys\n" in text
    and "PrivateTmp=no\n" in text
    and "NoNewPrivileges=no\n" in text,
    text,
)
text = ap.hostapd_unit()
check(
    "  an AP unit's writable path comes from the manifest",
    "ReadWritePaths=-/tmp/hostapd\n" in text and "ProtectHome=yes\n" in text,
    text,
)
text = health.unit_text(health.DOCTOR_SERVICE)
check(
    "  a health unit's ProtectHome comes from the manifest",
    "ProtectHome=no\n" in text and f"ReadWritePaths={T / 'state'}/control {T / 'root'}\n" in text,
    text,
)

with mock.patch.object(secmanifest, "_manifest", lambda: None):
    text = bootkey.unit_text()
    check(
        "with no manifest, the module's own fallback is used",
        "ReadOnlyPaths=/sys\n" in text and "ProtectSystem=strict\n" in text,
        text,
    )

changes = secmanifest.apply_secrets()
check(
    "a secret's declared mode is applied",
    stat.S_IMODE(secret.stat().st_mode) == 0o600 and any(str(secret) in line for line in changes),
    (changes, oct(stat.S_IMODE(secret.stat().st_mode))),
)
target = T / "target"
target.write_text("keep\n")
target.chmod(0o644)
link = T / "link"
link.symlink_to(target)
manifest["secrets"] = {str(link): {"owner": me, "group": my_group, "mode": "600", "readers": ["root"]}}
(T / "code" / "security.json").write_text(json.dumps(manifest))
secmanifest.apply_secrets()
check(
    "a symlink is not followed, and its target keeps its mode",
    stat.S_IMODE(target.stat().st_mode) == 0o644 and link.is_symlink(),
    oct(stat.S_IMODE(target.stat().st_mode)),
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
