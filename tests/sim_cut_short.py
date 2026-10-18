# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""An answer cut short while it is read (a chunked body that stops, a read that times out) or one that is not JSON
fails that source with a LibrarianError, as a refused connection does, so the run goes on to the next source; and a
mirror whose release listing is cut short is recorded as that mirror's error. The network is stood in for.
python3 tests/sim_cut_short.py"""
import http.client
import io
import os
import sys
import tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="cutshort-"))
os.environ.update(HUB_STATE_DIR=str(T), GITHUB_TOKEN="")
sys.path.insert(0, str(REPO))
from irate_box.library import librarian as L  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

class Cut(io.BytesIO):
    """A body that stops part-way, as http.client says when a chunked answer ends early."""
    headers = {}
    def __init__(self, exc):
        super().__init__(b"")
        self.exc = exc
    def read(self, *a):
        raise self.exc
answer = {"resp": None}
L.urllib.request.urlopen = lambda req, timeout=None: answer["resp"]
L._pace_hit = lambda: None

for label, exc in (("a chunked body that stops", http.client.IncompleteRead(b"{\"x\": 1", 18846)),
                   ("a read that times out", TimeoutError("timed out")),
                   ("a connection reset mid-body", ConnectionResetError(104, "reset"))):
    L._memo.clear()
    answer["resp"] = Cut(exc)
    try:
        L._api("/repos/a/b/releases")
        check(f"{label}: a LibrarianError", False, "no error")
    except L.LibrarianError as e:
        check(f"{label}: a LibrarianError, saying it was cut short", e.message["id"] == "library.download.cut-short" and e.message["args"]["host"] == "api.github.com", str(e))
    except Exception as e:  # noqa: BLE001
        check(f"{label}: a LibrarianError", False, repr(e))

class Body(io.BytesIO):
    headers = {}
L._memo.clear()
answer["resp"] = Body(b"<html>not json</html>")
try:
    L._api("/repos/a/b/releases")
    check("an answer that is not JSON: a LibrarianError", False, "no error")
except L.LibrarianError as e:
    check("an answer that is not JSON: a LibrarianError", e.message["id"] == "library.download.not-json", str(e))

# A download read in chunks: cut short is the download's error, not a crash.
answer["resp"] = Cut(http.client.IncompleteRead(b"", 100))
try:
    L._download("https://example.org/a.zim", T / "a.zim")
    check("a download cut short: a LibrarianError", False, "no error")
except L.LibrarianError as e:
    check("a download cut short: a LibrarianError", e.message["id"] == "library.download.cut-short", str(e))
except TypeError as e:
    check("a download cut short: a LibrarianError", False, f"_download's signature: {e}")

# The mirrors: one mirror's cut-short release listing is that mirror's error; the run goes on.
from irate_box.library import mirrors as M  # noqa: E402
M._remote_refs = lambda upstream: {"refs/heads/main": "a" * 40}
def cut_releases(m, entry):
    L._memo.clear()
    answer["resp"] = Cut(http.client.IncompleteRead(b"", 10))
    return L._api("/repos/a/b/releases")
M._releases = cut_releases
line = M.sync({"name": "m", "upstream": "https://github.com/a/b"}, check_only=True, log=lambda *_: None)
check("a mirror whose release listing is cut short: said as its error, the run not stopped", "ended early" in str(line), line)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
