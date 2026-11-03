# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""PDF.js's adaptation, offline: a real source tree's version, a release dist zip stood in by a
small one, the start page over the file drop, the viewer's strict policy, and a second run
changing nothing. python3 tests/sim_adapt_pdfjs.py"""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import access, manifests
from irate_box.library import adapt_pdfjs

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


T = Path(tempfile.mkdtemp(prefix="adapt-pdfjs-"))
tree = T / "tree"
(tree).mkdir()
(tree / "package.json").write_text(json.dumps({"version": "5.4.149"}))
(tree / "src").mkdir()
(tree / "src" / "viewer.js").write_text("source only")
VIEWER = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>PDF.js viewer</title>
<script src="../build/pdf.mjs" type="module"></script>
<script src="viewer.mjs" type="module"></script>
</head><body></body></html>
"""
dist = T / "pdfjs-5.4.149-dist.zip"
with zipfile.ZipFile(dist, "w") as z:
    z.writestr("LICENSE", "Apache License 2.0")
    z.writestr("build/pdf.mjs", "export {};")
    z.writestr("build/pdf.worker.mjs", "export {};")
    z.writestr("build/pdf.sandbox.mjs", "export {};")
    z.writestr("build/pdf.mjs.map", "{}")
    z.writestr("web/viewer.html", VIEWER)
    z.writestr("web/viewer.mjs", "export {};")
    z.writestr("web/viewer.css", "body {}")
    z.writestr("web/locale/locale.json", "{}")
    z.writestr("web/compressed.tracemonkey-pldi-09.pdf", "%PDF")
os.environ["PDFJS_DIST_ZIP"] = str(dist)
adapt_pdfjs.adapt(tree)
check(
    "the release's viewer and build are unpacked, the clone's sources gone",
    (tree / "build" / "pdf.mjs").is_file() and (tree / "web" / "viewer.html").is_file() and not (tree / "src").exists(),
)
check(
    "  the sample PDF and source maps go, the licence stays",
    not (tree / "web" / "compressed.tracemonkey-pldi-09.pdf").exists()
    and not (tree / "build" / "pdf.mjs.map").exists()
    and (tree / "LICENSE").is_file(),
)
index_js = (tree / "index.js").read_text() if (tree / "index.js").is_file() else ""
check(
    "the start page asks the hub's bridge for a PDF and opens the bytes in the viewer",
    (tree / "index.html").is_file()
    and (tree / "index.js").is_file()
    and 'bridge("pdf"' in index_js
    and "/addon-bridge.html" in index_js
    and "PDFViewerApplication" in index_js
    and "app.open({ data: bytes })" in index_js
    and 'fetch("/api/drop")' not in index_js,
)
viewer = (tree / "web" / "viewer.html").read_text()
check(
    "  the viewer carries a strict Content-Security-Policy",
    'http-equiv="Content-Security-Policy"' in viewer
    and "connect-src 'self'" in viewer
    and "default-src 'self'" in viewer
    and "object-src 'none'" in viewer,
)
before = {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()}
adapt_pdfjs.adapt(tree)
check("a second run changes nothing", before == {p: p.read_bytes() for p in tree.rglob("*") if p.is_file()})
# Its Access setting (a local add-on's): off answers 404 for the page and its files; users asks the hub's
# session check. The manifest itself must pass the catalogue's checks.
raw = json.loads((REPO / "addons" / "pdfjs.json").read_text())
built = manifests.load()
manifests.check_local(
    raw, "addons/pdfjs.json", {m["id"] for m in built}, {m["id"] for m in built if (m.get("menu") or {}).get("addons")}
)
local = manifests.local(raw)
off = access.clean({"pdfjs": "off"})
users = access.clean({"pdfjs": "users"})
onf = access.addon_nginx_conf(off, [local])
check(
    "the add-on passes the catalogue's checks; off answers 404 and users asks the hub",
    'pdfjs "1"' in onf
    and "auth_request /_irate_user;" in access.addon_gates(users, [local])["users-pdfjs.conf"]
    and "try_files" in access.addon_gates(users, [local])["users-pdfjs.conf"],
)
check(
    "  and Caddy: off errors 404, users forward_auths",
    "handle /pdfjs/*" not in access.addon_caddy_routes(off, [local], "HASH")
    and "handle /pdfjs/*" in access.addon_caddy_routes(users, [local], "HASH")
    and "forward_auth 127.0.0.1:8000" in access.addon_caddy_routes(users, [local], "HASH"),
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
