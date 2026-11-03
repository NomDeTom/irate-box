# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The status probe opens no TCP connection: port_listening reads the kernel's socket table
(/proc/net/tcp and tcp6) for a LISTEN entry, so a service that logs each connection (ngIRCd on box
181, four lines a time) is not written to the journal by the hub's status poll. A real listening
socket is found and a closed one is not; service_status uses it; and socket.create_connection is
made to fail if the probe ever calls it. python3 tests/sim_service_probe.py"""

import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="service-probe-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
)
(T / "state").mkdir(parents=True)
(T / "etc").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import server

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def no_connection(*args, **kwargs):
    raise AssertionError("the status probe opened a TCP connection")


def listen():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


with mock.patch.object(server.socket, "create_connection", no_connection):
    s, port = listen()
    check("a listening socket is found without connecting", server.port_listening(port) is True, port)
    check("a port with nothing listening is not", server.port_listening(port + 1) is False, port + 1)

    # service_status's port branch reads the same table, and still says running.
    services = [{"path": None, "name": "IRC server", "port": port, "unit": "probe.service"}]
    with mock.patch.object(server, "unit_states", lambda units: {"probe.service": (True, True, True)}):
        server._status_cache.update({"at": 0.0, "ports": {}, "sockets": {}, "units": {}, "why": {}})
        got = server.service_status(False, services)
    check("service_status says running for the listening port", got[0]["state"] == "running", got)

    s.close()
    with mock.patch.object(server, "unit_states", lambda units: {"probe.service": (True, True, True)}):
        server._status_cache.update({"at": 0.0, "ports": {}, "sockets": {}, "units": {}, "why": {}})
        got = server.service_status(False, services)
    check("and stopped once the socket closes", got[0]["state"] == "stopped", got)

# The IPv6 table is read too, where the runner has it.
try:
    s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s6.bind(("::1", 0))
    s6.listen(1)
    port6 = s6.getsockname()[1]
    with mock.patch.object(server.socket, "create_connection", no_connection):
        check("an IPv6 listener is found too", server.port_listening(port6) is True, port6)
    s6.close()
except OSError as exc:  # no IPv6 on this runner: the tcp6 file is simply absent or empty
    print(f"SKIP an IPv6 listener ({exc})")

sys.exit(1 if fails else 0)
