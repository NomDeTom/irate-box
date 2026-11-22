# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The front's sandbox: install.sh writes the drop-in for nginx and Caddy (only when it changed), uninstall.sh takes
it out, and the Security doctor reports a front whose unit or running process has lost it.
python3 tests/sim_front_sandbox.py"""

import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="front-sandbox-"))
PROC = T / "proc"
PROC.mkdir()
os.environ["HUB_PROC_DIR"] = str(PROC)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.msg import is_message
from irate_box.root import hub_control
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def drop_in(script, unit):
    """The text install.sh writes to a unit's drop-in, from the real script."""
    patterns = (
        rf"cat >/etc/systemd/system/{re.escape(unit)} <<-?EOF\n(.*?)^\s*EOF$",
        rf"cat <<-?EOF \| write_if_changed /etc/systemd/system/{re.escape(unit)} 644\n(.*?)^\s*EOF$",
    )
    match = next((m for p in patterns for m in [re.search(p, script, re.S | re.M)] if m), None)
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

for unit, paths, wx in (
    ("nginx.service.d/irate-box-sandbox.conf", "/var/log/nginx /var/lib/nginx /run", "yes"),
    ("caddy.service.d/irate-box-sandbox.conf", "/var/lib/caddy /run", None),
):
    got = drop_in(INSTALL, unit)
    check(f"install.sh writes {unit}", got is not None)
    check(
        "  with every sandbox line",
        got is not None and all(got.get(k) == v for k, v in COMMON.items()),
        {k: (got or {}).get(k) for k, v in COMMON.items() if (got or {}).get(k) != v},
    )
    check(
        "  and its own writable paths",
        got is not None and got.get("ReadWritePaths") == paths,
        (got or {}).get("ReadWritePaths"),
    )
    check(
        f"  MemoryDenyWriteExecute={wx or 'left out'}",
        (got or {}).get("MemoryDenyWriteExecute") == wx,
        (got or {}).get("MemoryDenyWriteExecute"),
    )
    check(f"uninstall.sh removes {unit}", unit in UNINSTALL)

check(
    "the drop-in is written only when its text changed, and an nginx restart applies it",
    "write_if_changed /etc/systemd/system/nginx.service.d/irate-box-sandbox.conf" in INSTALL
    and "NGINX_SANDBOX_CHANGED" in INSTALL
    and "systemctl restart nginx" in INSTALL,
)


def process(pid, no_new="1", seccomp="2"):
    d = PROC / str(pid)
    d.mkdir(exist_ok=True)
    (d / "status").write_text(f"Name:\tnginx\nNoNewPrivs:\t{no_new}\nSeccomp:\t{seccomp}\n")


def shown(main=4242, strict="strict", no_new="yes"):
    return {"LoadState": "loaded", "MainPID": str(main), "ProtectSystem": strict, "NoNewPrivileges": no_new}


process(4242)
process(5252, "0", "0")
with mock.patch.object(sd, "_show", lambda unit, *props: shown()):
    for kind, unit in (("nginx", "nginx.service"), ("caddy", "caddy.service")):
        seen = []

        def show(u, *p, _seen=seen):
            _seen.append(u)
            return shown()

        with mock.patch.object(sd, "_show", show):
            out = {f["id"]: f for f in sd._front_sandbox(kind)}
        check(
            f"the doctor: {unit} with the lines and a running process is ok",
            out["front-sandbox"]["status"] == "ok" and seen == [unit],
            out,
        )

with mock.patch.object(sd, "_show", lambda unit, *props: shown(5252)):
    out = {f["id"]: f for f in sd._front_sandbox("nginx")}
check(
    "  the lines set but not yet in the running process: pending, with a restart",
    out["front-sandbox"]["status"] == "warn"
    and [a["choice"] for a in out["front-sandbox"]["actions"]] == ["front-restart"]
    and "front.sandbox.pending.why" in message_ids(out["front-sandbox"]),
    out,
)

with mock.patch.object(sd, "_show", lambda unit, *props: shown(strict="no", no_new="no")):
    out = {f["id"]: f for f in sd._front_sandbox("nginx")}
check(
    "  without the lines: reported, with the unit's values and the installer repair",
    out["front-sandbox"]["status"] == "warn"
    and "front.sandbox.fix" in message_ids(out["front-sandbox"])
    and any("ProtectSystem=no" in e for e in out["front-sandbox"]["evidence"]),
    out,
)

with mock.patch.object(sd, "_show", lambda unit, *props: {"LoadState": "not-found"}):
    check("  a unit that is not loaded is not reported", sd._front_sandbox("nginx") == [])

calls = []


def fake_run(*cmd, **kw):
    calls.append(cmd)
    return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()


with mock.patch.object(hub_control, "_web_server", lambda: "caddy"), mock.patch.object(hub_control, "run", fake_run):
    said = hub_control._security_front_restart()
check(
    "the doctor's restart action restarts the front without blocking the page",
    ("systemctl", "restart", "--no-block", "caddy.service") in calls
    and is_message(said)
    and said["id"] == "control.security.front-restarted",
    calls,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
