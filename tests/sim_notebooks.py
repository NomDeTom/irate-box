# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A notebook per login (hub/notebooks.py and the root helper's notebooks-sync): the mode by the admin's
choice or by sign-up; opening one within the count and the memory kept free; the web server's question
answered by whose notebook it is only once its socket answers; the idle time, sign-out and communal mode
taking it off the list; the root helper starting and stopping units to match, names checked and at most
ten. Then the hub's own answers, against a hub it starts. python3 tests/sim_notebooks.py"""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="notebooks-"))
os.environ["HUB_STATE_DIR"] = str(state)
sys.path.insert(0, str(REPO))
from irate_box.hub import notebooks  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


settings = {"notebooks": "", "notebooks_max": 2, "notebooks_keep_free_mb": 96}
signup = {"v": "open"}
up, live = set(), {"alice", "bob", "carol"}
nb = notebooks.Notebooks(lambda path: path in up, lambda k: k in live, lambda: dict(settings), lambda: signup["v"])
alice, bob, carol = {"name": "Alice"}, {"name": "bob"}, {"name": "carol"}


def wanted():
    return json.loads(notebooks.WANTED.read_text())["run"]


check("by sign-up: private while people may sign up", nb.mode() == "private")
signup["v"] = "off"
check("  communal with sign-up off", nb.mode() == "communal" and nb.status(alice)["state"] == "off")
signup["v"] = "open"
settings["notebooks"] = "communal"
check("the admin's choice wins: communal", nb.mode() == "communal")
settings["notebooks"] = ""
check("not signed in: 401 to the web server", nb.gate(None) == (401, None))
check("signed in, not running: 403 (the waiting page)", nb.gate(alice) == (403, None))
notebooks.available_mb = lambda: 400
st = nb.open(alice)
check(
    "opened: listed (lower-cased), its folder made with a page saying it is private",
    st["state"] == "starting"
    and wanted() == ["alice"]
    and "only you can open it" in (state / "notebooks" / "alice" / "index.md").read_text(),
    (st, wanted()),
)
check("  still 403 until its socket answers", nb.gate(alice) == (403, None))
up.add(str(notebooks.socket_path("alice")))
check("  then 204, with whose it is", nb.gate(alice) == (204, "alice") and nb.status(alice)["state"] == "ready")
nb.open(bob)
check("a second within the count", wanted() == ["alice", "bob"])
check("  a third past it: full, nothing started", nb.open(carol)["state"] == "full" and wanted() == ["alice", "bob"])
settings["notebooks_max"] = 5
notebooks.available_mb = lambda: 50
check("  memory below what is kept free: none started", nb.open(carol)["state"] == "memory" and "carol" not in wanted())
notebooks.available_mb = lambda: 400
check("  room again: started", nb.open(carol)["state"] == "starting" and "carol" in wanted())
check("a name that cannot be a unit's is refused", nb.gate({"name": ".x"}) == (404, None))
nb.close("BOB")
check("signed out: off the list at once", wanted() == ["alice", "carol"])
now = time.monotonic()
nb.reap(now + 10)
check("in use: kept", wanted() == ["alice", "carol"])
live.discard("carol")
nb.reap(now + 20)
check("no session left: stopped", wanted() == ["alice"])
nb.reap(now + notebooks.IDLE + 5)
check("unused for the idle time: stopped", wanted() == [])
nb.open(alice)
settings["notebooks"] = "communal"
nb.reap()
check(
    "communal: every notebook stopped, the folders kept",
    wanted() == [] and (state / "notebooks" / "alice").is_dir() and (state / "notebooks" / "bob").is_dir(),
)
settings["notebooks"] = ""
nb.open(alice)
again = notebooks.Notebooks(lambda path: True, lambda k: True, lambda: dict(settings), lambda: "open")
check("a hub starting again lists none (any left running stop)", wanted() == [] and again.wanted == set())

# The root helper's side: the hub's list, checked, at most ten.
from irate_box.root import hub_control  # noqa: E402

calls = []


class Out:
    def __init__(self, stdout=""):
        self.stdout, self.returncode = stdout, 0


def fake_run(*cmd, **kw):
    calls.append(cmd)
    if cmd[1] == "list-units":
        return Out(
            "irate-box-notebook@alice.service loaded active running SilverBullet notebook of alice\n"
            "irate-box-notebook@zed.service loaded active running SilverBullet notebook of zed\n"
        )
    return Out()


hub_control.run = fake_run
hub_control.STATE = state
notebooks.WANTED.write_text(
    json.dumps({"run": ["alice", "bob", "../etc", "-x", "Upper", 5] + [f"user{i:02}" for i in range(20)]})
)
got = hub_control.notebooks_sync()
started = [c[-1] for c in calls if c[1] == "start"]
stopped = [c[-1] for c in calls if c[1] == "stop"]
check(
    "root: bad names left out, at most ten",
    len(got) == 10 and "../etc" not in got and "Upper" not in got and "alice" in got,
    got,
)
check(
    "  starts what is listed and not running; stops what is running and not listed",
    "irate-box-notebook@bob.service" in started
    and "irate-box-notebook@alice.service" not in started
    and stopped == ["irate-box-notebook@zed.service"],
    (started, stopped),
)
link = state / "elsewhere.json"
link.write_text(json.dumps({"run": ["mallory"]}))
notebooks.WANTED.unlink()
notebooks.WANTED.symlink_to(link)
calls.clear()
check("  never through a link: none wanted", hub_control.notebooks_sync() == [])

# The hub's answers, signed out, against a hub it starts.
hstate = Path(tempfile.mkdtemp(prefix="notebooks-hub-"))
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "e" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(hstate),
    HUB_ETC_DIR=str(hstate),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
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

    def go(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(
            method,
            path,
            body=json.dumps(body) if body is not None else None,
            headers={
                "Host": f"127.0.0.1:{port}",
                "X-Irate-Front": SECRET,
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        r = c.getresponse()
        return r.status, r.read(), r.getheader("X-Irate-Notebook")

    check("hub: the web server's question, signed out: 401", go("GET", "/_irate/notebook")[0] == 401)
    st, body, _ = go("GET", "/api/notebook")
    check("hub: status signed out", st == 200 and json.loads(body)["state"] == "signed-out", body)
    check("hub: opening needs the hub's own page", go("POST", "/api/notebook", {"action": "open"})[0] == 403)
    st, body, _ = go("POST", "/api/notebook", {"action": "open"}, {"X-Irate-Account": "1"})
    check("  from it, signed out: says so", st == 200 and json.loads(body)["state"] == "signed-out", body)
    check("hub: download signed out: 401", go("GET", "/api/notebook/download")[0] == 401)
    st, body, _ = go(
        "POST",
        "/admin/settings",
        {"notebooks": "private", "notebooks_max": 6, "notebooks_keep_free_mb": 1},
        {"X-Irate-Admin": "1"},
    )
    got = json.loads(body)
    check(
        "admin settings: a mode and a count taken; a memory floor not on offer refused",
        got["notebooks"] == "private" and got["notebooks_max"] == 6 and got["notebooks_keep_free_mb"] == 96,
        got,
    )
    st, body, _ = go("GET", "/admin/notebooks", None, {"X-Irate-Admin": "1"})
    check(
        "admin view: the mode and none running",
        st == 200 and json.loads(body)["mode"] == "private" and json.loads(body)["running"] == [],
        body,
    )
finally:
    hub.terminate()
print("failures:", fails)
sys.exit(1 if fails else 0)
