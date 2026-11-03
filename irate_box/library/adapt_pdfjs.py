#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a checkout of https://github.com/mozilla/pdf.js for the hub. Run by the librarian on
the tree it cloned (the commit its manifest pins), not by hand.

    adapt_pdfjs.py TREE [HUB_STATIC_DIR]

PDF.js's repository holds sources, not the viewer's build. The matching release's dist zip
(pdfjs-<version>-dist.zip, from the tag the tree is at) is unpacked over it, so the box serves
the release the manifest pins and nothing has to be built on the box.

The hub's copy changes only this much:

  * a strict Content-Security-Policy is added to the viewer (no request leaves the box);
  * the viewer's start page is the file drop's PDFs: index.html lists them and opens the chosen
    one in web/viewer.html?file=/api/drop/<id>, so a phone's browser never downloads the PDF;
  * the release's sample PDF and source maps go, and the Apache licence stays.

Running it twice changes nothing. Stdlib only.
"""

import json
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

MARK = "irate-box-adapted"
RELEASE = "https://github.com/mozilla/pdf.js/releases/download/v{version}/pdfjs-{version}-dist.zip"
POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' data: blob:; "
    "connect-src 'self' data: blob:; "
    "worker-src 'self' blob:; "
    "child-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'none'"
)
NOTE = (
    f"<!-- {MARK}: mozilla/pdf.js, adapted by irate-box/library/adapt_pdfjs.py. "
    "The start page lists the file drop's PDFs; the viewer's requests stay on this box. -->\n"
)
DROP = ("web/compressed.tracemonkey-pldi-09.pdf",)
DROP_SUFFIX = (".map", ".pdf")


class AdaptError(Exception):
    pass


INDEX_HTML = """<!DOCTYPE html>
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PDF viewer</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>\U0001f4c4</text></svg>">
  <style>
    html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; }
    body { display: flex; flex-direction: column; }
    header { padding: .6rem 1rem; background: #f4f4f4; border-bottom: 1px solid #ccc; display: flex; gap: 1rem; align-items: center; }
    header h1 { margin: 0; font-size: 1.1rem; }
    main { flex: 1; min-height: 0; }
    iframe { border: 0; width: 100%; height: 100%; }
  </style>
</head>
<body>
  <header>
    <h1>PDF viewer</h1>
    <button id="open" type="button">Open a PDF from the file drop</button>
    <span id="name"></span>
  </header>
  <main><iframe id="viewer" title="The opened PDF" src="web/viewer.html"></iframe></main>
  <script src="index.js"></script>
</body>
</html>
"""

INDEX_JS = """// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The drop's PDFs, chosen in the hub's bridge page and opened by PDF.js from the bytes it returns.
// This add-on never reaches the file drop itself.
(() => {
  const button = document.getElementById("open");
  const name = document.getElementById("name");
  const viewer = document.getElementById("viewer");
  function hubOrigin() {
    // The add-on server sends no Referer: ask the hub page that frames this app, as JS Paint does.
    return new Promise((resolve) => {
      let done = false;
      function finish(value) {
        if (done) return;
        done = true;
        window.removeEventListener("message", onMessage);
        resolve(value);
      }
      function onMessage(event) {
        if (event.source !== window.parent) return;
        const data = event.data || {};
        if (data.type === "irate-hub-origin" && typeof data.origin === "string") finish(data.origin);
      }
      window.addEventListener("message", onMessage);
      try {
        window.parent.postMessage({ type: "irate-hub-origin-request" }, "*");
      } catch (error) { /* not framed: no parent to ask */ }
      window.setTimeout(() => finish(null), 800);
    });
  }
  async function bridge(action, payload) {
    const origin = await hubOrigin();
    if (!origin) {
      throw new Error("open this app from the hub so the bridge can find it");
    }
    return new Promise((resolve, reject) => {
      const popup = window.open(`${origin}/addon-bridge.html`, "irate-box-bridge", "popup,width=560,height=620");
      if (!popup) {
        reject(new Error("the bridge window was blocked"));
        return;
      }
      let timer = null;
      function finish(fn, value) {
        window.removeEventListener("message", onMessage);
        if (timer) window.clearTimeout(timer);
        fn(value);
      }
      function onMessage(event) {
        if (event.origin !== origin || event.source !== popup) return;
        const data = event.data || {};
        if (data.type === "irate-bridge-ready") {
          popup.postMessage({ type: "irate-bridge-request", action, payload }, origin);
        } else if (data.type === "irate-bridge-result" && data.action === action) {
          if (data.ok) finish(resolve, data);
          else finish(reject, new Error(data.error || "the bridge refused"));
        }
      }
      window.addEventListener("message", onMessage);
      timer = window.setTimeout(() => finish(reject, new Error("the bridge did not answer")), 5 * 60 * 1000);
    });
  }
  function viewerApp() {
    try {
      return viewer.contentWindow && viewer.contentWindow.PDFViewerApplication;
    } catch (error) {
      return null;
    }
  }
  async function openPdf() {
    button.disabled = true;
    try {
      const answer = await bridge("pdf", { title: "PDF viewer" });
      const file = answer.file || {};
      const bytes = new Uint8Array(file.data);
      name.textContent = file.name || "PDF";
      const app = viewerApp();
      if (!app) throw new Error("the viewer is still loading");
      await app.initializedPromise;
      await app.open({ data: bytes });
    } catch (error) {
      name.textContent = `Not opened: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  }
  button.addEventListener("click", () => openPdf());
})();
"""


def _version(tree):
    try:
        data = json.loads((tree / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AdaptError("no package.json: is this mozilla/pdf.js?") from None
    version = str(data.get("version", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise AdaptError(f"package.json has no release version ({version!r})")
    return version


def _archive(tree, version):
    given = os.environ.get("PDFJS_DIST_ZIP")
    if given:
        return Path(given)
    if (tree / "build" / "pdf.mjs").is_file() and (tree / "web" / "viewer.html").is_file():
        return None  # an already unpacked dist tree
    url = RELEASE.format(version=version)
    part = tree / ".pdfjs-dist.zip"
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as out:  # https only, built here. # nosec B310
            shutil.copyfileobj(response, out)
    except OSError as exc:
        raise AdaptError(f"could not download {url}: {exc}") from None
    return part


def _unpack(tree, archive):
    try:
        zin = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AdaptError(f"not a PDF.js dist zip: {exc}") from None
    with zin:
        names = set(zin.namelist())
        for need in ("build/pdf.mjs", "build/pdf.worker.mjs", "web/viewer.html"):
            if need not in names:
                raise AdaptError(f"the dist zip has no {need}")
        for child in tree.iterdir():
            if child.name == archive.name:
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for name in sorted(names):
            if name.endswith("/") or name in DROP or name.endswith(DROP_SUFFIX):
                continue
            if name.split("/", 1)[0] not in ("build", "web", "LICENSE"):
                continue
            path = tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(zin.read(name))


def _patch_viewer(tree):
    viewer = tree / "web" / "viewer.html"
    text = viewer.read_text(encoding="utf-8")
    if MARK in text:
        return
    if "<meta charset=" not in text:
        raise AdaptError("viewer.html has no meta charset")
    text = text.replace(
        "<head>",
        f'<head>\n    <meta http-equiv="Content-Security-Policy" content="{POLICY}">\n    {NOTE}',
        1,
    )
    viewer.write_text(text, encoding="utf-8")


def adapt(tree):
    tree = Path(tree)
    viewer = tree / "web" / "viewer.html"
    if viewer.is_file() and MARK in viewer.read_text(encoding="utf-8") and (tree / "index.html").is_file():
        return  # already adapted
    version = _version(tree)
    archive = _archive(tree, version)
    if archive is not None:
        _unpack(tree, archive)
        if archive.name == ".pdfjs-dist.zip":
            archive.unlink(missing_ok=True)
    if not (tree / "web" / "viewer.html").is_file():
        raise AdaptError("the dist has no web/viewer.html")
    (tree / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (tree / "index.js").write_text(INDEX_JS, encoding="utf-8")
    _patch_viewer(tree)


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        adapt(argv[0])
    except (AdaptError, OSError) as exc:
        print(f"adapt_pdfjs: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
