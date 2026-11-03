# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The quick installer's queue, offline: the order (apps and add-ons before books), what an item
may be, the file's shape across a reload, and an empty install refused. python3 tests/sim_install_queue.py"""

import json
import os
import sys
import tempfile
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
check("a book keeps the source it was given", server._queue_item(books)["source"]["type"] == "nightly-link")

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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
