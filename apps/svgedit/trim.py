#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Trim the built SVG-Edit editor to the set the Irate-Box serves at /svg/.

    python3 apps/svgedit/trim.py DIST_EDITOR

Upstream's build is 22 MB because it carries source maps, tests, the duplicate IIFE bundle and
every extension in every language. The hub keeps the editor, the nine extensions its wrapper
loads, their English text, the shape libraries the Shapes panel offers, and nothing else.

An input the wrapper needs that is no longer there stops the script with an error, as the
adapt scripts do: a bundle that half works is worse than the old one the librarian keeps.
Running it twice changes nothing. Stdlib only.
"""

import json
import shutil
import sys
from pathlib import Path

KEEP_EXTENSIONS = {
    "ext-connector",
    "ext-eyedropper",
    "ext-grid",
    "ext-layer_view",
    "ext-markers",
    "ext-opensave",
    "ext-panning",
    "ext-polystar",
    "ext-shapes",
}
# The Shapes panel asks for these; index.json is rewritten to match so it never fetches a file
# that was removed. Order follows upstream's index: basic first, the specialist libraries after.
KEEP_SHAPELIB = ("basic", "arrow", "dialog_balloon", "electronics", "flowchart", "math", "symbol")
BRAND_IMAGES = ("images/netlify-dark.svg", "images/svg-edit-home.svg")
# The files the wrapper and the editor need. If the build stops producing one, stop rather than
# ship a page that fails halfway.
REQUIRED = (
    "index.html",
    "Editor.js",
    "svgedit.css",
    "browser-not-supported.js",
    "browser-not-supported.html",
    "components/jgraduate/images/AlphaBar.png",
    "images/logo.svg",
    "extensions/node_modules/browser-fs-access/dist/index.modern.js",
    "extensions/_virtual/_vite/preload-helper.js",
)


class TrimError(Exception):
    pass


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _locale_only_english(extension):
    locale = extension / "locale"
    if not locale.is_dir():
        return
    for path in locale.iterdir():
        if path.name != "en.js":
            _remove(path)


def _rewrite_shapelib(shapes):
    index = shapes / "shapelib" / "index.json"
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TrimError(f"ext-shapes/shapelib/index.json cannot be read: {exc}") from exc
    libraries = data.get("lib")
    if not isinstance(libraries, list):
        raise TrimError("ext-shapes/shapelib/index.json has no lib list")
    kept = [name for name in libraries if name in KEEP_SHAPELIB]
    if set(kept) != set(KEEP_SHAPELIB):
        raise TrimError(f"ext-shapes/shapelib/index.json does not name every kept library: {kept}")
    index.write_text(json.dumps({"lib": kept}, indent=4) + "\n", encoding="utf-8")


def trim(tree):
    tree = Path(tree)
    if not tree.is_dir():
        raise TrimError(f"{tree} is not the built editor")
    missing = [name for name in REQUIRED if not (tree / name).is_file()]
    if missing:
        raise TrimError("the built app has changed: missing " + ", ".join(missing))

    for path in list(tree.rglob("*.map")):
        _remove(path)
    for name in ("tests", "iife-Editor.js", "iife-index.html", "xdomain-index.html"):
        _remove(tree / name)
    for name in BRAND_IMAGES:
        _remove(tree / name)

    extensions = tree / "extensions"
    for path in list(extensions.iterdir()):
        if path.is_dir() and path.name.startswith("ext-") and path.name not in KEEP_EXTENSIONS:
            _remove(path)
    for name in sorted(KEEP_EXTENSIONS):
        extension = extensions / name
        if not (extension / f"{name}.js").is_file():
            raise TrimError(f"the built app has changed: {name}/{name}.js is missing")
        if not (extension / "locale" / "en.js").is_file():
            raise TrimError(f"the built app has changed: {name}/locale/en.js is missing")
        _locale_only_english(extension)

    shapes = extensions / "ext-shapes"
    for name in KEEP_SHAPELIB:
        if not (shapes / "shapelib" / f"{name}.json").is_file():
            raise TrimError(f"the built app has changed: ext-shapes/shapelib/{name}.json is missing")
    for path in list((shapes / "shapelib").iterdir()):
        if path.suffix == ".json" and path.name != "index.json" and path.stem not in KEEP_SHAPELIB:
            _remove(path)
    _rewrite_shapelib(shapes)

    after = [name for name in REQUIRED if not (tree / name).is_file()]
    if after:
        raise TrimError("the trim removed a file the wrapper needs: " + ", ".join(after))
    return tree


def main(argv):
    if len(argv) != 2:
        print("usage: trim.py DIST_EDITOR", file=sys.stderr)
        return 2
    try:
        tree = trim(argv[1])
    except TrimError as exc:
        print(f"trim.py: {exc}", file=sys.stderr)
        return 1
    files = sum(1 for path in tree.rglob("*") if path.is_file())
    size = sum(path.stat().st_size for path in tree.rglob("*") if path.is_file())
    print(f"trimmed {tree}: {files} files, {size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
