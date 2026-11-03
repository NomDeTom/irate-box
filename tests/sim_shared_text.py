# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's shared texts (store.py's notepad and pastebin namespaces, one key "text" each): a visitor writes and
reads the same document, the ETag saves a poll's body, a document over the size is refused, another key is not
found, and the other namespaces are untouched. python3 tests/sim_shared_text.py"""

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import store

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


class Clock:
    t = 0

    def ticks(self):
        Clock.t += 1
        return Clock.t


class Handler:
    def __init__(self, body=b"", headers=None):
        self.command = "GET"
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}
        self.rfile = io.BytesIO(body)
        self.code = None
        self.body = b""
        self.sent = {}

    def send_response(self, code):
        self.code = code

    def send_header(self, key, value):
        self.sent[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body += data

    def json(self):
        return json.loads(self.body or b"{}")


T = Path(tempfile.mkdtemp(prefix="shared-text-"))
s = store.Store(T / "store", Clock())


def call(method, path, body=b"", **headers):
    h = Handler(body, headers)
    handled = store.handle(h, method, path, s)
    return handled, h


handled, h = call("PUT", "/api/v2/notepad/text", json.dumps({"text": "first words"}).encode())
check(
    "a shared note is accepted at its one key", handled and h.code == 200 and h.json() == {"ok": True}, (h.code, h.body)
)
handled, h = call("GET", "/api/v2/notepad/text")
check(
    "  and read back whole, with an ETag for the poll",
    handled and h.code == 200 and json.loads(h.body)["text"] == "first words" and h.sent.get("ETag"),
    (h.code, h.body, h.sent),
)
etag = h.sent["ETag"]
handled, h2 = call("GET", "/api/v2/notepad/text", **{"If-None-Match": etag})
check("  a poll with the same ETag gets no body", handled and h2.code == 304 and h2.body == b"", (h2.code, h2.body))
call("PUT", "/api/v2/notepad/text", json.dumps({"text": "second words"}).encode())
handled, h3 = call("GET", "/api/v2/notepad/text")
check(
    "  a new document has a new ETag and the new words",
    h3.sent["ETag"] != etag and json.loads(h3.body)["text"] == "second words",
    (h3.sent, h3.body),
)
handled, h4 = call("PUT", "/api/v2/pastebin/text", json.dumps({"text": "a config"}).encode())
handled, h5 = call("GET", "/api/v2/pastebin/text")
check(
    "the paste bin is its own document",
    h4.code == 200 and json.loads(h5.body)["text"] == "a config",
    h5.body,
)
handled, h6 = call("PUT", "/api/v2/notepad/other", b"{}")
check("another key in the namespace is not found", handled and h6.code == 404, h6.code)
big = b"{" + b"x" * store.SHARED_MAX + b"}"
handled, h7 = call("PUT", "/api/v2/notepad/text", big)
check("a document over the size is refused", handled and h7.code == 413, h7.code)
handled, h8 = call("POST", "/api/v2/notepad/text", b"{}")
check("only GET and PUT are answered there", handled and h8.code == 405, h8.code)
handled, h9 = call("GET", "/api/v2/scenes/123")
check("the Excalidraw namespaces still go their own way", handled and h9.code == 404, (handled, h9.code))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
