# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""An app's docs in the manifest: the known books pass, each bad shape is refused, and the
librarian accepts exactly the source the manifest names. python3 tests/sim_manifest_docs.py"""

import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests
from irate_box.library import librarian

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def loaded(path):
    return json.loads((REPO / path).read_text())


def refuses(name, m, why):
    try:
        manifests._check(m, "test")
        check(name, False, "accepted")
    except manifests.ManifestError as exc:
        check(name, why in str(exc), str(exc))


known = {
    "apps.d/40-mermaid.json": ("mermaid-docs", "nightly-link"),
    "apps.d/20-meshtastic.json": ("meshtastic-docs", "nightly-link"),
    "apps.d/47-git.json": ("git-docs", "kiwix"),
}
for path, (book, kind) in known.items():
    m = loaded(path)
    manifests._check(m, path)
    docs = m.get("docs") or []
    check(
        f"{path}: one docs book, {kind}",
        len(docs) == 1 and docs[0]["book"] == book and docs[0]["source"]["type"] == kind,
        docs,
    )
    check(
        "  and the librarian takes its source as written",
        librarian.validate_source({"name": docs[0]["book"], **docs[0]["source"]})["type"] == kind,
        docs[0]["source"],
    )
check("an app with nothing known has no docs", "docs" not in loaded("apps.d/10-draw.json"))

base = loaded("apps.d/40-mermaid.json")
bad = copy.deepcopy(base)
bad["docs"][0]["source"] = {"type": "ftp", "repo": "a/b"}
refuses("an unknown source type is refused", bad, "docs.source.type")
bad = copy.deepcopy(base)
bad["docs"][0]["source"] = {"type": "url", "url": "http://example.org/book.zim"}
refuses("a plain HTTP source is refused", bad, "https://")
bad = copy.deepcopy(base)
bad["docs"][0]["book"] = "../book"
refuses("a book name outside the librarian's pattern is refused", bad, "docs.book")
bad = copy.deepcopy(base)
bad["docs"] = [copy.deepcopy(base["docs"][0]) for _ in range(manifests.MAX_DOCS + 1)]
refuses("more docs than the limit are refused", bad, "docs: a list of 1 to")
bad = copy.deepcopy(base)
bad["docs"][0]["size_mb"] = "big"
refuses("a size hint that is not whole megabytes is refused", bad, "docs.size_mb")
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
