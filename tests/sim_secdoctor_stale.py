# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Security doctor on stale code and unused nginx modules: unused modules are named from the real Debian
module files, a captured maps file with a deleted libssl is reported against its unit, and root's actions rescan
before they change anything. python3 tests/sim_secdoctor_stale.py"""

import os
import re
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

# Stage 5: the captured maps and cgroups from lyra-bench, a real armv7l box after its security updates.
capture = (REPO / "tests/fixtures/proc-maps-deleted-lyra-bench.txt").read_text()
for section in re.split(r"(?m)^### ", capture)[1:]:
    head, _, rest = section.partition("\n")
    ident = re.search(r"pid=(\d+)", head)
    if not ident or "## maps" not in rest:
        continue
    maps = rest.split("## maps (deleted lines)\n", 1)[1].split("## cgroup\n", 1)[0]
    cgroup = rest.split("## cgroup\n", 1)[1].strip().splitlines()[0]
    pid = ident.group(1)
    d = PROC / pid
    d.mkdir()
    (d / "maps").write_text(maps)
    (d / "cgroup").write_text(cgroup + "\n")

found = sd.stale_code()
check(
    "the real capture: the three services still on replaced libraries are found",
    set(found["services"]) == {"NetworkManager.service", "wpa_supplicant.service", "systemd-logind.service"},
    sorted(found["services"]),
)
check(
    "  each names the executable libraries, not locale-archive (data) or /dev/zero (shared memory)",
    any(e["path"].endswith("/libssl.so.3") for e in found["services"]["NetworkManager.service"])
    and any(e["path"].endswith("/libpcre2-8.so.0.14.0") for e in found["services"]["systemd-logind.service"])
    and not any(
        "locale-archive" in e["path"] or "/dev/zero" in e["path"] for es in found["services"].values() for e in es
    ),
    found["services"],
)
check(
    "  a deleted C library also asks for a restart of the box",
    {e["why"] for e in found["box"]} == {"libc"} and "nginx.service" not in found["services"],
    found,
)
stale = {f["id"]: f for f in sd.step_stale({})}
check(
    "the doctor reports each service and offers a restart",
    {"stale-networkmanager", "stale-wpa-supplicant", "stale-systemd-logind"} <= set(stale)
    and all(
        stale[fid]["actions"][0]["choice"] == "stale-restart"
        for fid in ("stale-networkmanager", "stale-wpa-supplicant", "stale-systemd-logind")
    )
    and "stale.service.why" in message_ids(stale["stale-networkmanager"]),
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
