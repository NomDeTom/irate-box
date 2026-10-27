# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The write-intensity ladder, against a hub it starts: /admin/settings takes the four stops and refuses
others; /admin/box says what has been written to the card since boot where the system tells; the mesh
nodes are not written again when nothing changed. python3 tests/sim_card_writes.py"""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="cardwrites-"))
port = free_port()
SECRET = "e" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(state),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
try:
    for _ in range(150):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={
                "Host": f"127.0.0.1:{port}",
                "X-Irate-Front": SECRET,
                "X-Irate-Admin": "1",
                "Content-Type": "application/json",
            },
        )
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    st, got = go("GET", "/admin/settings")
    check("gentle by default (the recommended level)", got.get("card_writes") == "gentle", got.get("card_writes"))
    st, got = go("POST", "/admin/settings", {"card_writes": "saver"})
    check("a stop taken", got.get("card_writes") == "saver")
    st, got = go("POST", "/admin/settings", {"card_writes": "never"})
    check("  one not on offer refused", got.get("card_writes") == "saver")
    check(
        "  kept in the hub's settings, where crash watch reads it",
        json.loads((state / "settings.json").read_text())["card_writes"] == "saver",
    )
    st, box = go("GET", "/admin/box")
    sysinfo = box.get("system", {})
    check(
        "/admin/box: the card's writes since boot, where the system tells (a number, or left out)",
        "card_written" not in sysinfo or (isinstance(sysinfo["card_written"], int) and sysinfo["card_written"] >= 0),
        sysinfo.get("card_written"),
    )
    clock = state / "clock.json"
    before = clock.stat().st_mtime_ns if clock.exists() else 0
    time.sleep(0.05)
finally:
    hub.terminate()
    try:
        hub.wait(timeout=10)
    except subprocess.TimeoutExpired:
        hub.kill()
check(
    "stopped with SIGTERM: the hub saves its clock on the way out",
    (state / "clock.json").exists() and (state / "clock.json").stat().st_mtime_ns > before,
)
sys.path.insert(0, str(REPO))
os.environ["HUB_STATE_DIR"] = str(state)
from irate_box.hub import meshbridge

b = meshbridge.Heard()
b.nodes = {"!abc": {"last": time.time()}}
b.prune_and_save(force=True)
first = meshbridge.NODES.stat().st_mtime_ns if meshbridge.NODES.exists() else None
time.sleep(0.05)
b.prune_and_save(force=True)
check(
    "mesh nodes: not written again when nothing changed",
    first is not None and meshbridge.NODES.stat().st_mtime_ns == first,
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
