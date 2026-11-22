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
from irate_box.root import hub_control
from irate_box.root import secdoctor as sd

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
    "the real capture: NetworkManager, wpa_supplicant and systemd-logind are not restartable alone",
    found["services"] == {} and {e["why"] for e in found["box"]} == {"unit", "libc"},
    found,
)
check(
    "  their executable libraries are named, not locale-archive (data) or /dev/zero (shared memory)",
    any(e["path"].endswith("/libssl.so.3") for e in found["box"])
    and any(e["path"].endswith("/libpcre2-8.so.0.14.0") for e in found["box"])
    and not any("locale-archive" in e["path"] or "/dev/zero" in e["path"] for e in found["box"]),
    found["box"],
)
stale = {f["id"]: f for f in sd.step_stale({})}
check(
    "the doctor asks for a restart of the box, with no per-service action",
    stale["stale-box"]["status"] == "warn" and not stale["stale-box"]["actions"] and len(stale) == 1,
    stale,
)

# A service that can be restarted alone gets its own action; a user session never becomes a system service.
(RPROC := PROC / "7000").mkdir()
(RPROC / "maps").write_text("7f00-7f01 r-xp 00000000 08:30 1 /usr/lib/x86_64-linux-gnu/libssl.so.3 (deleted)\n")
(RPROC / "cgroup").write_text("0::/system.slice/foo.service\n")
UPROC = PROC / "7001"
UPROC.mkdir()
(UPROC / "maps").write_text("7f02-7f03 r-xp 00000000 08:30 1 /usr/lib/x86_64-linux-gnu/libssl.so.3 (deleted)\n")
(UPROC / "cgroup").write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/foo.service\n")
found = sd.stale_code()
check(
    "a restartable system service is listed, and a user@ service is not",
    list(found["services"]) == ["foo.service"]
    and any(e["why"] == "no-unit" and e["pid"] == 7001 for e in found["box"]),
    found,
)
stale = {f["id"]: f for f in sd.step_stale({})}
check(
    "  its finding offers its own unit to restart",
    [a["choice"] for a in stale["stale-foo"]["actions"]] == ["stale-restart:foo.service"],
    stale,
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
    said = hub_control._security_stale_restart("nginx.service")
check(
    "the restart action takes the unit only as a pointer, and starts it without blocking the page",
    ("systemctl", "restart", "--no-block", "nginx.service") in calls
    and is_message(said)
    and said["id"] == "control.security.stale-restarted",
    calls,
)
for unit, services in (
    ("NetworkManager.service", {}),
    ("NetworkManager.service", {"NetworkManager.service": [{"pid": 1, "path": "/x"}]}),
):
    try:
        with mock.patch.object(sd, "stale_code", lambda services=services: {"services": services, "box": []}):
            hub_control._security_stale_restart(unit)
        check(f"  {unit} (listed: {bool(services)}) is refused", False)
    except hub_control.Refused:
        check(f"  {unit} (listed: {bool(services)}) is refused", True)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
