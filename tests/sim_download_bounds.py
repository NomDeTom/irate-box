# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What a source can send the librarian (audit L13-L16): a download stops past the size claimed, and when the
card is almost full; nothing asked for over https is taken over plain HTTP, the JSON listings included; the
debsecan data's first line is read without unpacking the rest; and a submodule's URL never shadows a mirror's
own in mirror-urls.json. The network is stood in for. python3 tests/sim_download_bounds.py"""

import io
import json
import os
import sys
import tempfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="dlbounds-"))
os.environ.update(HUB_STATE_DIR=str(T), GITHUB_TOKEN="")
sys.path.insert(0, str(REPO))
from irate_box.hub import gitrepos
from irate_box.library import librarian as L
from irate_box.library import mirrors, toolkits

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Body(io.BytesIO):
    """An answer: its body, its Content-Length (if any), and the URL it ended at."""

    def __init__(self, data, length=None, url="https://example.org/x"):
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": str(length)}
        self.status = 200
        self.url = url

    def geturl(self):
        return self.url


answer = {"resp": None}
L.urllib.request.urlopen = lambda req, timeout=None: answer["resp"]
L._pace_hit = lambda: None
L.CHUNK = 1024
dest = T / "part"


def download(expected=0):
    try:
        L._download("https://example.org/x", dest, expected=expected)
        return None
    except L.LibrarianError as e:
        return e.message["id"]


answer["resp"] = Body(b"x" * 5000, length=5000)
check("a download of the size claimed: kept whole", download(5000) is None and dest.stat().st_size == 5000)
answer["resp"] = Body(b"x" * 9000, length=2000)
check("a server that sends more than its Content-Length: stopped", download() == "library.download.too-long")
answer["resp"] = Body(b"x" * 9000)
check("more than the source's listing claimed: stopped", download(3000) == "library.download.too-long")

real_usage = L.shutil.disk_usage
L.shutil.disk_usage = lambda p: real_usage(p)._replace(free=1 << 20)
answer["resp"] = Body(b"x" * 40_000)
check("a download with no size, on a card almost full: stopped", download() == "library.download.card-full")
L.shutil.disk_usage = real_usage

answer["resp"] = Body(b'{"x": 1}', url="http://example.org/x")
try:
    L._api("/repos/a/b/releases")
    got = None
except L.LibrarianError as e:
    got = e.message["id"]
check("a JSON listing redirected to plain HTTP: refused", got == "library.download.http-redirect", got)
answer["resp"] = Body(b"x" * 10, url="http://example.org/x")
check("a download redirected to plain HTTP: refused", download() == "library.download.http-redirect")

# The debsecan data: only its first line is unpacked to check it.
seen = []
real_obj = zlib.decompressobj


class Counting:
    def __init__(self):
        self.d = real_obj()

    def decompress(self, data, max_length=0):
        out = self.d.decompress(data, max_length)
        seen.append(len(out))
        return out


toolkits.zlib.decompressobj = Counting
toolkits.zlib.decompress = lambda *_: seen.append("whole") or b""
toolkits.suite = lambda: "trixie"
packed = zlib.compress(b"VERSION 1\n" + b"\0" * (64 << 20))
L.urllib.request.urlopen = lambda req, timeout=None: Body(packed)  # one answer per suite asked for
try:
    toolkits.refresh_feed(log=lambda *_: None)
except L.LibrarianError:
    pass
check(
    "the debsecan data: its first line unpacked, never the whole",
    seen and "whole" not in seen and max(seen) <= 16,
    seen,
)

# mirror-urls.json: a mirror's own upstream wins over a submodule's URL that names it.
root = T / "git"
gitrepos.ROOT = root
for area, name in (("public", "a"), ("public", "b"), ("public", "evil")):
    (root / area / f"{name}.git").mkdir(parents=True)
    (root / area / f"{name}.git" / "HEAD").write_text("ref: refs/heads/main\n")
mirrors.load = lambda: [
    {"name": "a", "area": "public", "upstream": "https://github.com/x/a"},
    {"name": "b", "area": "public", "upstream": "https://github.com/x/b"},
]
mirrors.repo_path = lambda m: root / m["area"] / f"{m['name']}.git"
# A later mirror's submodule names an earlier mirror (the order where the last mapping used to win).
mirrors.status = lambda: {"b": {"submodules": {"s": {"url": "https://github.com/x/a.git", "repo": "evil"}}}}
mirrors.URLS = T / "mirror-urls.json"
urls = mirrors.write_urls()
check(
    "a submodule's URL that names another mirror: that mirror's own repository kept",
    urls["https://github.com/x/a"] == f"file://{root}/public/a.git"
    and urls["https://github.com/x/a.git"] == f"file://{root}/public/a.git",
    json.dumps(urls, indent=1),
)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
