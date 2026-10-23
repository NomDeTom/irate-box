# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor's Port 80 (health.check_port_80), as should the hub be on port 80 and is it: on it and
listening, on it with nothing or another program listening, plain HTTP turned off, moved off it with port 80
free since (the Lyra, 2026-10-21: http://box/ opened nothing), and moved because another program or another
site in the same web server holds it; ss and the HTTPS setting stood in for. python3 tests/sim_port_80.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="port-80-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.finding import english, message_ids
from irate_box.root import health, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


listening = {"out": ""}


def fake_run(*cmd, **kw):
    if cmd[0] == "ss":
        return subprocess.CompletedProcess(cmd, 0, listening["out"], "")
    raise AssertionError(cmd)


health.run = fake_run
transport = {"now": "both"}
tls.settings = lambda: {"transport": transport["now"], "admin_http": "allow"}


def options(*extra):
    (T / "etc" / "install-options").write_text(
        "\n".join(["--repo", "https://example.org/x", "--web", "nginx", *extra]) + "\n"
    )


def port_80():
    (found,) = health.check_port_80()
    return found


NGINX = 'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1241,fd=6))\n'
APACHE = 'LISTEN 0 511 *:80 *:* users:(("apache2",pid=900,fd=4))\n'

options()
listening["out"] = NGINX
check("the hub on port 80, nginx listening: fine", message_ids(port_80()) == {"port-80.ok"})
listening["out"] = ""
f = port_80()
check(
    "the hub on port 80, nothing listening: a problem",
    f["status"] == "problem" and "port-80.silent" in message_ids(f),
    f,
)
listening["out"] = APACHE
f = port_80()
check(
    "the hub on port 80, apache2 listening: a problem naming it",
    f["status"] == "problem" and "port-80.taken" in message_ids(f) and "apache2" in english(f)["detail"],
    f,
)
transport["now"] = "https"
listening["out"] = ""
check("plain HTTP turned off: nothing on port 80 is right", message_ids(port_80()) == {"port-80.https-only"})
transport["now"] = "both"

options("--port", "8080")
listening["out"] = ""
f = port_80()
check(
    "moved to 8080, port 80 free since: a problem, with the installer's --port 80",
    f["status"] == "problem"
    and message_ids(f) >= {"port-80.free", "port-80.why", "port-80.free.fix"}
    and "8080" in english(f)["detail"]
    and f["commands"] == [f"sudo {health.CODE}/install.sh --port 80"],
    f,
)
moved = [a for a in f.get("actions", []) if a["choice"] == "rerun-install-port-80"]
check(
    "  with a button that moves the hub, asking first, naming the port it leaves",
    len(moved) == 1 and moved[0]["confirm"]["args"]["port"] == "8080",
    f.get("actions"),
)
from irate_box.hub.server import HEALTH_CHOICE_RE

check("  the hub passes that choice to the root helper", bool(HEALTH_CHOICE_RE.match("rerun-install-port-80")))
from irate_box.root.hub_control import _with_port

check(
    "  the installer's options with --port 80 in place of the old port, the rest kept",
    _with_port(["--with-collab", "--port", "8080", "--web", "nginx"], "80")
    == ["--with-collab", "--web", "nginx", "--port", "80"]
    and _with_port(["--port=8080"], "80") == ["--port", "80"]
    and _with_port(["--web", "nginx"], "80") == ["--web", "nginx", "--port", "80"],
)

listening["out"] = APACHE
f = port_80()
check(
    "moved because apache2 holds port 80: a warning naming it, with --take-port-80",
    f["status"] == "warn"
    and message_ids(f) >= {"port-80.held", "port-80.held.fix"}
    and "apache2" in english(f)["detail"],
    f,
)

listening["out"] = NGINX
f = port_80()
check(
    "moved because another nginx site holds port 80: a warning, that site to move",
    f["status"] == "warn" and message_ids(f) >= {"port-80.held.same", "port-80.held.same.fix"},
    f,
)

check("registered in the doctor's scan", "check_port_80" in health.scan.__code__.co_names)

sys.exit(1 if fails else 0)
