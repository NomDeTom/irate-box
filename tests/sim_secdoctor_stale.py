# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Security doctor on stale code and unused nginx modules: unused modules are named from the real Debian
module files, a captured maps file with a deleted libssl is reported against its unit, and root's actions rescan
before they change anything. python3 tests/sim_secdoctor_stale.py"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-stale-"))
PROC = T / "proc"
PROC.mkdir()
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_NGINX_CONF=str(REPO / "config/irate-box.nginx"),
    HUB_NGINX_MODULES=str(REPO / "tests/fixtures/nginx-modules"),
    HUB_PROC_DIR=str(PROC),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.msg import is_message
from irate_box.root import hub_control, secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# Stage 3: the real Debian module files, and the real irate-box config (which uses none of them).
loaded = {m["stem"] for m in sd._loaded_modules()}
check(
    "the loaded modules are read from Debian's modules-enabled files",
    loaded == {"mail", "stream", "http_geoip"},
    loaded,
)
unused = {m["stem"] for m in sd._unused_modules((REPO / "config/irate-box.nginx").read_text())}
check("modules whose directives the config does not use are unused", unused == loaded, unused)
check(
    "  a module the config does use is not unused",
    "mail" not in {m["stem"] for m in sd._unused_modules("mail { server {} }")},
)
modules = {f["id"]: f for f in sd._front_modules()}
check(
    "the doctor names each unused module, with an action to remove it",
    modules["front-modules"]["status"] == "warn"
    and {a["choice"] for a in modules["front-modules"]["actions"]}
    == {"module-remove:mail", "module-remove:stream", "module-remove:http_geoip"}
    and "front.modules.fix" in message_ids(modules["front-modules"]),
    modules,
)

# Stage 5: a real capture of a process's maps with its libssl deleted, and its real cgroup line.
maps = (REPO / "tests/fixtures/proc-maps-deleted-libssl.txt").read_text()
for pid, unit, text in (
    (1234, "nginx.service", maps),
    (
        4321,
        "nginx.service",
        "7f00-7f01 r-xp 00000000 08:30 1 /usr/lib/x86_64-linux-gnu/libc.so.6 (deleted)\n",
    ),
    (1, None, maps),
):
    d = PROC / str(pid)
    d.mkdir()
    (d / "maps").write_text(text)
    (d / "cgroup").write_text(f"0::/system.slice/{unit}\n" if unit else "0::/init.scope\n")

found = sd.stale_code()
check(
    "a service with deleted libssl is found, once per unit",
    list(found["services"]) == ["nginx.service"]
    and {e["path"] for e in found["services"]["nginx.service"]} == {"/usr/lib/x86_64-linux-gnu/libssl.so.3"},
    found,
)
check(
    "  PID 1 and a deleted C library need the box restarted instead",
    {e["why"] for e in found["box"]} == {"pid1", "libc"},
    found["box"],
)
stale = {f["id"]: f for f in sd.step_stale({})}
check(
    "the doctor reports the service and offers a restart",
    stale["stale-nginx"]["status"] == "warn"
    and [a["choice"] for a in stale["stale-nginx"]["actions"]] == ["stale-restart"]
    and "stale.service.why" in message_ids(stale["stale-nginx"]),
    stale,
)
check(
    "  the box restart finding offers no action",
    stale["stale-box"]["status"] == "warn" and not stale["stale-box"]["actions"],
    stale["stale-box"],
)


class Done:
    returncode = 0
    stdout = ""
    stderr = ""


calls = []


def fake_run(*cmd, **kw):
    calls.append(cmd)
    if cmd[0] == "dpkg-query":
        return type(
            "R", (), {"returncode": 0, "stdout": "libnginx-mod-mail: /usr/lib/nginx/modules/x\n", "stderr": ""}
        )()
    return Done()


with mock.patch.object(hub_control, "run", fake_run):
    said = hub_control._security_module_remove("mail")
    check(
        "the module action removes the package root's own scan found",
        ("apt-get", "remove", "-y", "-q", "--no-install-recommends", "libnginx-mod-mail") in calls
        and is_message(said)
        and said["id"] == "control.security.module-removed",
        calls,
    )

calls.clear()
with (
    mock.patch.object(hub_control, "run", fake_run),
    mock.patch.object(sd, "stale_code", lambda: {"services": {"nginx.service": [{"pid": 1, "path": "/x"}]}, "box": []}),
):
    said = hub_control._security_stale_restart()
check(
    "the restart action takes the unit from root's scan, not from the request",
    ("systemctl", "restart", "nginx.service") in calls
    and all(c[0] != "systemctl" or c[2] == "nginx.service" for c in calls)
    and is_message(said)
    and said["id"] == "control.security.stale-restarted",
    calls,
)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
