#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a clone of https://github.com/jgraph/drawio for the hub. Run by the librarian on
what it fetched (the commit its manifest pins, or the newest).

    adapt_drawio.py TREE [HUB_STATIC_DIR]

The repository stays as JGraph publishes it; only the hub's copy changes, and only this much:

  * the built web app in src/main/webapp is kept: the page, its scripts, styles, images, shapes,
    stencils, resources, maths and mxGraph's stylesheet. Everything the repository needs to
    build or host it -- source, servlets, cloud clients, the PWA worker, the viewer and the
    templates -- goes. /drawio/ serves the whole folder, so nothing else may remain;
  * index.html gets a stricter Content-Security-Policy (no outside requests), no web-app
    manifest or canonical app.diagrams.net link, and a note saying what changed;
  * PreConfig.js starts in device mode, with no custom (web) fonts, no update check and no
    exit button, so the file menus stay on the box. The other integrations are still in the
    app, but their requests are blocked by the policy and the hub's own CSP;
  * LICENSE comes along, because the copy is modified.

Anything the app now needs that a new upstream commit no longer has stops this script with an
error, so the librarian keeps what the box has (or falls back to the pinned commit) rather
than install a page that half works. Running it twice changes nothing. Stdlib only.
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

MARK = "irate-box-adapted"
ORIGIN = "https://github.com/jgraph/drawio"

# The built app's own files, relative to src/main/webapp. Keep this list small and test it in a
# browser: app.min.js loads the three minified bundles and mxgraph/css/common.css itself.
FILES = (
    "index.html",
    "js/bootstrap.js",
    "js/main.js",
    "js/app.min.js",
    "js/PreConfig.js",
    "js/PostConfig.js",
    "js/shapes-14-6-5.min.js",
    "js/stencils.min.js",
    "js/extensions.min.js",
    "js/math-print.js",
    "js/diagramly/Extensions.js",
    "styles/grapheditor.css",
    "styles/high-contrast.css",
)
DIRS = ("images", "resources", "shapes", "stencils", "math4", "mxgraph")

NOTE = """<!-- {mark}: {origin} at the commit in irate-box-bundle.json.
     Adapted for the Irate-Box by irate_box/library/adapt_drawio.py: the rest of the repository
     was removed, the policy below was added, and PreConfig.js starts on the device. -->
"""

POLICY = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
    "worker-src 'self' blob:; "
    "connect-src 'self' data: blob:\">"
)

CONFIG = """// Irate-Box: the box is the only origin. No cloud picker, web fonts or update check.
Object.assign(urlParams, { gapi: "0", od: "0", gh: "0", gl: "0", tr: "0", ms365: "0", picker: "0" });
window.DRAWIO_CONFIG = Object.assign({}, window.DRAWIO_CONFIG, {
	defaultMode: "device",
	enableCustomFonts: false,
	disableUpdate: true,
	noExitBtn: true
});
"""

CANONICAL = re.compile(r'^\s*<link rel="canonical"[^>]*>\s*$\n?', re.M)
MANIFEST = re.compile(r'^\s*<link rel="manifest"[^>]*>\s*$\n?', re.M)
EXTERNAL_META = re.compile(r'^\s*<meta[^>]+content="https?://[^"]+"[^>]*>\s*$\n?', re.M)


class AdaptError(Exception):
    pass


def adapt_page(text):
    if MARK in text:
        return text
    first, sep, rest = text.partition("\n")
    if first.strip().lower() != "<!doctype html>":
        raise AdaptError("index.html does not start with <!DOCTYPE html>")
    if "<meta charset=" not in rest:
        raise AdaptError("index.html has no <meta charset=> to put the policy after")
    rest = CANONICAL.sub("", rest)
    rest = MANIFEST.sub("", rest)
    rest = EXTERNAL_META.sub("", rest)
    rest = rest.replace("<meta charset=", POLICY + "\n    <meta charset=", 1)
    return first + sep + NOTE.format(mark=MARK, origin=ORIGIN) + rest


def adapt(tree):
    tree = Path(tree)
    if (tree / "index.html").is_file() and MARK in (tree / "index.html").read_text(encoding="utf-8"):
        return  # already adapted
    web = tree / "src" / "main" / "webapp"
    if not web.is_dir():
        raise AdaptError("no src/main/webapp: is this jgraph/drawio?")
    missing = [name for name in FILES if not (web / name).is_file()]
    missing += [name for name in DIRS if not (web / name).is_dir()]
    if missing:
        raise AdaptError("the built app has changed: missing " + ", ".join(missing))
    pre = web / "js" / "PreConfig.js"
    if "window.DRAWIO_CONFIG = null;" not in pre.read_text(encoding="utf-8"):
        raise AdaptError("js/PreConfig.js has changed: no 'window.DRAWIO_CONFIG = null;'")
    license_file = tree / "LICENSE"
    if not license_file.is_file():
        raise AdaptError("no LICENSE: is this jgraph/drawio?")

    staging = Path(tempfile.mkdtemp(prefix=".drawio-adapt-", dir=tree.parent))
    try:
        for name in FILES:
            dst = staging / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(web / name, dst)
        for name in DIRS:
            shutil.copytree(web / name, staging / name)
        shutil.copy2(license_file, staging / "LICENSE")

        page = staging / "index.html"
        text = adapt_page(page.read_text(encoding="utf-8"))
        page.write_text(text, encoding="utf-8")

        pre = staging / "js" / "PreConfig.js"
        pre.write_text(
            pre.read_text(encoding="utf-8").replace("window.DRAWIO_CONFIG = null;", CONFIG, 1),
            encoding="utf-8",
        )

        for path in tree.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        for path in staging.iterdir():
            os.replace(path, tree / path.name)
        staging.rmdir()
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        adapt(argv[0])
    except (AdaptError, OSError) as exc:
        print(f"adapt_drawio: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
