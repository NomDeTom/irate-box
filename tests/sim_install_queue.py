# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The quick installer's queue, offline: the order (apps and add-ons before books), what an item
may be, the file's shape across a reload, and an empty install refused. python3 tests/sim_install_queue.py"""

import json
import os
import sys
import tempfile
import threading
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="install-queue-"))
os.environ["HUB_STATE_DIR"] = str(T)
os.environ["HUB_ETC_DIR"] = str(T)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import server

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def item(kind, ident, **more):
    return {"kind": kind, "id": ident, "title": ident, **more}


books = item(
    "book",
    "mermaid-docs",
    book="mermaid-docs",
    title="Mermaid docs",
    source={
        "type": "nightly-link",
        "repo": "NomDeTom/mermaid",
        "workflow": "docs-zim.yml",
        "branch": "zim-build",
        "pattern": "mermaid-docs-*",
    },
    size_mb=12,
)
app = item("app", "mermaid", title="Mermaid")
addon = item("addon", "wiki", title="Kiwix")
ordered = server.queue_order([books, addon, app])
check("apps and add-ons come before the books", [x["kind"] for x in ordered] == ["app", "addon", "book"], ordered)
check("a book needs its source", server._queue_item({"kind": "book", "id": "x", "book": "x"}) is None)
check("an app id is checked", server._queue_item({"kind": "app", "id": "../../etc"}) is None)
check(
    "a book's source comes from its manifest, not the request",
    server._queue_item({**books, "source": {"type": "url", "url": "https://elsewhere.invalid/x.zim"}})["source"]["type"]
    == "nightly-link",
)
local = server._queue_item({"kind": "local", "id": "eliza"})
check("a web add-on carries its consent text", local and local.get("consent"), local)

code, snap = server.queue_action({"action": "save", "items": [books, app, addon]})
check(
    "saving keeps the order and the items",
    code == 200 and [x["kind"] for x in snap["items"]] == ["app", "addon", "book"],
    snap,
)
saved = json.loads(server.INSTALL_QUEUE.read_text())
check(
    "  the file has the administrator's list, not the browser's",
    [x["id"] for x in saved["items"]] == ["mermaid", "wiki", "mermaid-docs"],
    saved,
)
code, snap = server.queue_action({"action": "save", "items": []})
check("  an empty list clears it", code == 200 and snap["items"] == [], snap)
code, body = server.queue_action({"action": "install"})
check("  installing nothing is refused in words", code == 400 and body.get("error"), body)
code, _ = server.queue_action({"action": "save", "items": [local]})
code, body = server.queue_action({"action": "install"})
check(
    "  a web add-on without its consent tick is refused",
    code == 400 and body["message"]["id"] == "install-queue.consent",
    body,
)
irc = server._queue_item({"kind": "addon", "id": "irc"})
check("a service add-on carries its own consent", irc and irc.get("consent"), irc)
server.queue_action({"action": "save", "items": [irc]})
code, body = server.queue_action({"action": "install"})
check("  and Install refuses without its tick", code == 400 and body["message"]["id"] == "install-queue.consent", body)

# lyra-tft, 2026-11-07 01:14: SVG-Edit was queued beside InspIRCd, whose consent was never ticked.
# It must install, and InspIRCd must stay in the queue for its own tick. The install must finish:
# the queue used to wait on its own thread, so no app or book install ever moved past "waiting".
real_librarian = {
    name: getattr(server.librarian, name)
    for name in ("add_source", "default_app_source", "load_config", "load_status", "update")
}
real_library_start = server.library_start
calls = []


def fake_update(names, mode=None, log=None):
    calls.append(("update", list(names)))
    return {names[0]: "up to date: test"}


def fake_library_start(action, fn, kind="all"):
    result = fn()
    done = threading.Thread(target=lambda: None)
    done.start()
    done.join()
    with server._library_lock:
        server._library_job["thread"] = done
        server._library_job["result"] = result
    return True


server.librarian.add_source = lambda src: calls.append(("add", src.get("name")))
server.librarian.default_app_source = lambda name: {"name": name, "kind": "app", "type": "nightly-link"}
server.librarian.load_config = lambda: {"sources": []}
server.librarian.load_status = lambda: {}
server.librarian.update = fake_update
server.library_start = fake_library_start
code, _ = server.queue_action(
    {
        "action": "save",
        "items": [
            {"kind": "app", "id": "svg", "title": "SVG-Edit"},
            {"kind": "addon", "id": "inspircd", "title": "IRC server (InspIRCd)"},
        ],
    }
)
code, body = server.queue_action({"action": "install", "ticks": {}})
check("an unticked consent does not stop the app queued before it", code == 200, body)
thread = server._queue_job["thread"]
thread.join(timeout=10)
check("  the install thread finishes, rather than waiting on itself", not thread.is_alive())
check("  the app's update ran", ("update", ["svg"]) in calls, calls)
check(
    "  its line says done",
    any(r["item"]["id"] == "svg" and r["ok"] for r in server._queue_job.get("results") or []),
    server._queue_job.get("results"),
)
left = json.loads(server.INSTALL_QUEUE.read_text())
check("  the add-on stays queued for its own consent", [x["id"] for x in left["items"]] == ["inspircd"], left)
check("  and it was not added", not any(c == ("add", "inspircd") for c in calls), calls)
server.library_start = real_library_start
for name, fn in real_librarian.items():
    setattr(server.librarian, name, fn)
server.queue_action({"action": "clear"})
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
