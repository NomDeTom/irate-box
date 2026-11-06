# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""SVG-Edit's bundle as the hub expects it: the trim keeps only the wrapper's extensions and
shapes and fails when a file the wrapper needs is missing, the manifest installs and updates
from the fork's bundle, and the wrapper loads no inline or outside script.

With SVGEDIT_BUNDLE set to a built tree, it also checks that real tree: the marker, the sums,
no source maps or tests, and no outside resource loads. The fork's workflow runs it there.
python3 tests/sim_svgedit_bundle.py"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRIM = REPO / "apps" / "svgedit" / "trim.py"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


KEEP_EXTENSIONS = (
    "ext-connector",
    "ext-eyedropper",
    "ext-grid",
    "ext-layer_view",
    "ext-markers",
    "ext-opensave",
    "ext-panning",
    "ext-polystar",
    "ext-shapes",
)
KEEP_SHAPELIB = ("basic", "arrow", "dialog_balloon", "electronics", "flowchart", "math", "symbol")
REMOVED_SHAPELIB = ("animal", "game", "misc", "music", "object", "raphael_1", "raphael_2")
REMOVED_EXTENSIONS = ("ext-helloworld", "ext-overview_window", "ext-storage")


def make_tree(root):
    """A small stand-in for upstream's dist/editor, with every file the trim names."""
    root = Path(root)
    files = {
        "index.html": "<!DOCTYPE html><html><body></body></html>",
        "Editor.js": "// editor\n",
        "svgedit.css": "body{}\n",
        "browser-not-supported.js": "export {}\n",
        "browser-not-supported.html": "<!DOCTYPE html><html></html>",
        "components/jgraduate/images/AlphaBar.png": "png",
        "images/logo.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>",
        "images/netlify-dark.svg": "<svg/>",
        "images/svg-edit-home.svg": "<svg/>",
        "Editor.js.map": "{}",
        "iife-Editor.js": "//",
        "iife-index.html": "<html></html>",
        "xdomain-index.html": "<html></html>",
        "tests/unit.html": "<html></html>",
        "extensions/node_modules/browser-fs-access/dist/index.modern.js": "export {}\n",
        "extensions/node_modules/browser-fs-access/dist/index.modern.js.map": "{}",
        "extensions/_virtual/_vite/preload-helper.js": "export {}\n",
    }
    for name in KEEP_EXTENSIONS:
        files[f"extensions/{name}/{name}.js"] = "export default {}\n"
        files[f"extensions/{name}/{name}.js.map"] = "{}"
        files[f"extensions/{name}/locale/en.js"] = "export default {}\n"
        files[f"extensions/{name}/locale/fr.js"] = "export default {}\n"
    for name in REMOVED_EXTENSIONS:
        files[f"extensions/{name}/{name}.js"] = "export default {}\n"
        files[f"extensions/{name}/locale/en.js"] = "export default {}\n"
    files["extensions/ext-shapes/shapelib/license-MIT-raphael.txt"] = "MIT\n"
    for name in KEEP_SHAPELIB + REMOVED_SHAPELIB:
        files[f"extensions/ext-shapes/shapelib/{name}.json"] = "{}\n"
    files["extensions/ext-shapes/shapelib/index.json"] = json.dumps(
        {"lib": ["basic", *REMOVED_SHAPELIB, "arrow", "dialog_balloon", "electronics", "flowchart", "math", "symbol"]}
    )
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def tree_names(root):
    return {path.relative_to(root).as_posix() for path in Path(root).rglob("*") if path.is_file()}


def run_trim(tree):
    return subprocess.run([sys.executable, str(TRIM), str(tree)], capture_output=True, text=True)


T = Path(tempfile.mkdtemp(prefix="svgedit-bundle-"))
tree = make_tree(T / "editor")
result = run_trim(tree)
check("a complete build: trimmed", result.returncode == 0, result.stderr)
names = tree_names(tree)
check("  no source maps", not [name for name in names if name.endswith(".map")], names)
check(
    "  no tests or the IIFE and xdomain pages", not [n for n in names if n.startswith(("tests/", "iife-", "xdomain-"))]
)
check("  no brand images", not [n for n in names if n in ("images/netlify-dark.svg", "images/svg-edit-home.svg")])
check(
    "  the extensions the wrapper does not load are gone",
    not [n for n in names if any(n.startswith(f"extensions/{e}/") for e in REMOVED_EXTENSIONS)],
)
check(
    "  each kept extension has its script and only English text",
    all(
        f"extensions/{e}/{e}.js" in names
        and f"extensions/{e}/locale/en.js" in names
        and not [n for n in names if n.startswith(f"extensions/{e}/locale/") and not n.endswith("/en.js")]
        for e in KEEP_EXTENSIONS
    ),
)
check(
    "  only the Shapes panel's libraries remain, and index.json names exactly those",
    all(f"extensions/ext-shapes/shapelib/{name}.json" in names for name in KEEP_SHAPELIB)
    and not [
        n
        for n in names
        if n.startswith("extensions/ext-shapes/shapelib/")
        and n.endswith(".json")
        and n.rsplit("/", 1)[-1][:-5] not in (*KEEP_SHAPELIB, "index")
    ],
    sorted(n for n in names if n.startswith("extensions/ext-shapes/shapelib/")),
)
index = json.loads((tree / "extensions/ext-shapes/shapelib/index.json").read_text())
check("  index.json is rewritten to the kept set", index == {"lib": [*KEEP_SHAPELIB]}, index)

again = run_trim(tree)
check("a second run: unchanged", again.returncode == 0 and tree_names(tree) == names, again.stderr)

broken = make_tree(T / "broken")
(broken / "Editor.js").unlink()
result = run_trim(broken)
check(
    "a build without Editor.js: refused, saying what is missing",
    result.returncode == 1 and "Editor.js" in result.stderr,
    result.stderr,
)

broken = make_tree(T / "broken-extension")
(broken / "extensions" / "ext-grid" / "ext-grid.js").unlink()
result = run_trim(broken)
check(
    "a build without a kept extension: refused",
    result.returncode == 1 and "ext-grid" in result.stderr,
    result.stderr,
)

manifest = json.loads((REPO / "apps.d" / "12-svg.json").read_text())
check(
    "the manifest installs a bundle called svg, needs index.html, and watches the fork's workflow",
    manifest["id"] == "svg"
    and manifest["install"] == {"dir": "apps/svg", "needs": "index.html", "title": "SVG-Edit"}
    and manifest["source"]["repo"] == "NomDeTom/svgedit"
    and manifest["source"]["workflow"] == "irate-box-bundle.yml"
    and manifest["source"]["pattern"] == "irate-box-svgedit-*"
    and manifest["status"]["root_env"] == "HUB_SVG_ROOT"
    and manifest["tile"]
    == {
        "icon": "🖋️",
        "name": "SVG-Edit",
        "desc": "Edit SVG drawings",
        "href": "/app.html#/svg/",
        "new_tab": True,
    }
    and manifest["entries"][0]["menus"] == {"drawing": 15}
    and manifest["entries"][0]["name"] == "SVG-Edit"
    and "admin" not in manifest,
    manifest,
)

wrapper = (REPO / "apps" / "svgedit" / "index.html").read_text()
hub_js = (REPO / "apps" / "svgedit" / "hub.js").read_text()
check(
    "the wrapper loads no inline script (the /svg/ CSP has no unsafe-inline)",
    not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", wrapper, re.I),
    wrapper,
)
check(
    "  and no script, stylesheet, picture or frame from outside",
    not re.search(r"""(?:src|href)=["'](?:https?:)?//""", wrapper, re.I),
    wrapper,
)
check(
    "hub.js fixes the extension list: no ext-storage, no URL configuration",
    "noDefaultExtensions: true" in hub_js
    and "preventAllURLConfig: true" in hub_js
    and "lockExtensions: true" in hub_js
    and all(f"'{name}'" in hub_js for name in KEEP_EXTENSIONS)
    and "ext-storage" not in hub_js,
    hub_js,
)
check(
    "the gallery extension ships, and the wrapper loads it and the hub's lock helper",
    (REPO / "apps" / "svgedit" / "ext-hub-store.js").is_file()
    and "userExtensions: [{ pathName: './ext-hub-store.js' }]" in hub_js
    and '<script src="./lock.js"></script>' in wrapper,
)

install = (REPO / "install.sh").read_text()
check(
    "install.sh carries svg through --apps, --apps-from-actions, the offline kit and the root",
    "for app in mermaid draw svg tools serial flasher; do" in install
    and "fetch_apps+=(draw mermaid svg serial)" in install
    and "for app in draw mermaid svg serial tools room flasher; do" in install
    and "for app in mermaid draw svg tools serial; do" in install
    and "HUB_SVG_ROOT=$APPS/svg" in install,
)

built = os.environ.get("SVGEDIT_BUNDLE")
if built:
    bundle = Path(built)
    marker = json.loads((bundle / "irate-box-bundle.json").read_text())
    commit = str(marker.get("commit", ""))
    check(
        "the built bundle's marker names the app and the commit actually built",
        marker.get("app") == "svg"
        and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
        and str(marker.get("source", "")).endswith(commit),
        marker,
    )
    bundle_names = tree_names(bundle)
    check("  it has index.html", "index.html" in bundle_names)
    check(
        "  and the hub's gallery extension",
        "ext-hub-store.js" in bundle_names,
        sorted(name for name in bundle_names if name.endswith(".js") and "/" not in name),
    )
    check(
        "  no source maps, tests, IIFE or xdomain pages",
        not [n for n in bundle_names if n.endswith(".map") or n.startswith(("tests/", "iife-", "xdomain-"))],
    )
    check(
        "  no Google Fonts",
        not [n for n in bundle_names if "fonts.googleapis" in (bundle / n).read_text(errors="ignore")],
    )
    loads = []
    for name in bundle_names:
        if not name.endswith(".html") or name == "browser-not-supported.html":
            continue
        page = (bundle / name).read_text(errors="ignore")
        loads += re.findall(r"""(?:src|href)=["'](?:https?:)?//[^"']*""", page, re.I)
    check("  the editor page loads nothing from outside", not loads, loads)
    sums = bundle / "SHA256SUMS"
    check("  SHA256SUMS covers the tree", sums.is_file())
    if sums.is_file():
        wrong = []
        for line in sums.read_text().splitlines():
            digest, _, name = line.partition("  ")
            path = bundle / name.removeprefix("./")
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                wrong.append(name)
        check("  and every sum matches", not wrong, wrong)

shutil.rmtree(T, ignore_errors=True)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
