# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's name on its networks (the root helper's box-hostname; Network → Connection settings → Box name):
hostnamectl asked, /etc/hosts' 127.0.1.1 line following (the old name replaced, other names kept; the line added
where there is none), a name outside Debian's rules refused before anything runs, and, where HTTPS is set up, the
answer saying the certificate authority still covers the old .local name. hostnamectl and the files stood in for.
python3 tests/sim_box_name.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="boxname-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_HOSTS=str(T / "hosts"))
(T / "state/control").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused, ids
from irate_box.root import hub_control, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


ran = []


def fake_run(*cmd, **kw):
    ran.append(cmd)
    return subprocess.CompletedProcess(cmd, 0, "", "")


hub_control.run = fake_run
hub_control.netinv.scan = lambda: {"stand-in": True}
hub_control.netinv.write = lambda inv, path: None
hub_control.socket.gethostname = lambda: "luckfox-lyra-zero-w"
tls._record = lambda: {}

(T / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tluckfox-lyra-zero-w\n::1\tlocalhost ip6-localhost\n")
said = hub_control.box_hostname({"hostname": "lyra-2"})
check(
    "renamed: hostnamectl asked, the 127.0.1.1 line follows, the other lines kept",
    ("hostnamectl", "set-hostname", "lyra-2") in ran
    and (T / "hosts").read_text() == "127.0.0.1\tlocalhost\n127.0.1.1\tlyra-2\n::1\tlocalhost ip6-localhost\n",
    (T / "hosts").read_text(),
)
check("  said with its .local name", said["id"] == "control.hostname.set" and said["args"]["name"] == "lyra-2", said)

(T / "hosts").write_text("127.0.1.1\tluckfox-lyra-zero-w.localdomain luckfox-lyra-zero-w extra\n")
hub_control.box_hostname({"hostname": "lyra-2"})
check(
    "  the old name and its .localdomain form replaced, another name on the line kept",
    (T / "hosts").read_text() == "127.0.1.1\tlyra-2\textra\n",
    (T / "hosts").read_text(),
)

(T / "hosts").write_text("127.0.0.1\tlocalhost\n")
hub_control.box_hostname({"hostname": "lyra-2"})
check("  no 127.0.1.1 line: one added", (T / "hosts").read_text().endswith("127.0.1.1\tlyra-2\n"))

for bad in ("Lyra", "-lyra", "lyra-", "lyra_2", "a" * 64, "", None, "lyra.local"):
    ran.clear()
    try:
        hub_control.box_hostname({"hostname": bad})
        refused = False
    except Refused:
        refused = True
    check(f"refused before anything runs: {bad!r}", refused and not ran)

tls._record = lambda: {"cert": "x"}
said = hub_control.box_hostname({"hostname": "lyra-3"})
check(
    "HTTPS set up: the answer says the certificate authority still covers the old .local name",
    "control.hostname.tls" in ids(said) and "control.hostname.set" in ids(said),
    said,
)
route = (REPO / "irate_box/hub/server.py").read_text()
check(
    "the hub sends it on from Network's hostname action, checked by the same rule",
    'act == "hostname"' in route and '"action": "box-hostname"' in route,
)
check("the root helper's action table has it", hub_control.ACTIONS.get("box-hostname") is hub_control.box_hostname)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
