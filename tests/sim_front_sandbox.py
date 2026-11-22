# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The front's sandbox: install.sh writes the drop-in for nginx and Caddy, uninstall.sh takes it out, and the
Security doctor reports a front whose unit has lost its lines. python3 tests/sim_front_sandbox.py"""

import re
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def drop_in(script, unit):
    """The text install.sh writes to a unit's drop-in, from the real script."""
    match = re.search(
        rf"cat >/etc/systemd/system/{re.escape(unit)} <<-?EOF\n(.*?)^\s*EOF$",
        script,
        re.S | re.M,
    )
    if not match:
        return None
    lines = [line.strip("\t") for line in match.group(1).splitlines()]
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#") and "=" in line)


INSTALL = (REPO / "install.sh").read_text()
UNINSTALL = (REPO / "uninstall.sh").read_text()

COMMON = {
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "NoNewPrivileges": "yes",
    "ProtectKernelTunables": "yes",
    "ProtectKernelModules": "yes",
    "ProtectKernelLogs": "yes",
    "ProtectControlGroups": "yes",
    "ProtectClock": "yes",
    "ProtectHostname": "yes",
    "RestrictNamespaces": "yes",
    "RestrictRealtime": "yes",
    "RestrictSUIDSGID": "yes",
    "LockPersonality": "yes",
    "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
    "CapabilityBoundingSet": "CAP_NET_BIND_SERVICE CAP_SETUID CAP_SETGID CAP_CHOWN CAP_DAC_OVERRIDE",
    "SystemCallFilter": "@system-service",
    "SystemCallArchitectures": "native",
}

for unit, paths in (
    ("nginx.service.d/irate-box-sandbox.conf", "/var/log/nginx /var/lib/nginx /run"),
    (
        "caddy.service.d/irate-box-sandbox.conf",
        "/var/lib/caddy /run",
    ),
):
    got = drop_in(INSTALL, unit)
    check(f"install.sh writes {unit}", got is not None)
    check(
        f"  with every sandbox line",
        got is not None and all(got.get(k) == v for k, v in COMMON.items()),
        {k: (got or {}).get(k) for k, v in COMMON.items() if (got or {}).get(k) != v},
    )
    check(
        f"  and its own writable paths",
        got is not None and got.get("ReadWritePaths") == paths,
        (got or {}).get("ReadWritePaths"),
    )
    check(
        f"  and no MemoryDenyWriteExecute (Debian's nginx needs writable-executable memory)",
        got is not None and "MemoryDenyWriteExecute" not in got,
        got,
    )
    check(f"uninstall.sh removes {unit}", unit in UNINSTALL)

good = {"LoadState": "loaded", "ProtectSystem": "strict", "NoNewPrivileges": "yes"}
with mock.patch.object(sd, "_show", lambda unit, *props: good):
    for kind, unit in (("nginx", "nginx.service"), ("caddy", "caddy.service")):
        seen = []

        def show(u, *p, _seen=seen):
            _seen.append(u)
            return good

        with mock.patch.object(sd, "_show", show):
            out = {f["id"]: f for f in sd._front_sandbox(kind)}
        check(
            f"the doctor: {unit} with the lines is ok",
            out["front-sandbox"]["status"] == "ok" and seen == [unit],
            out,
        )

weak = {"LoadState": "loaded", "ProtectSystem": "no", "NoNewPrivileges": "no"}
with mock.patch.object(sd, "_show", lambda unit, *props: weak):
    out = {f["id"]: f for f in sd._front_sandbox("nginx")}
check(
    "  without them: reported, with the unit's values and the installer repair",
    out["front-sandbox"]["status"] == "warn"
    and "front.sandbox.fix" in message_ids(out["front-sandbox"])
    and any("ProtectSystem=no" in e for e in out["front-sandbox"]["evidence"]),
    out,
)

with mock.patch.object(sd, "_show", lambda unit, *props: {"LoadState": "not-found"}):
    check("  a unit that is not loaded is not reported", sd._front_sandbox("nginx") == [])

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
