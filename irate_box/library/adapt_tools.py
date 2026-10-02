#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a copy of the nomdetom.github.io calculators for the hub. Run by install.sh.

    adapt_tools.py TOOLS_DIR HUB_STATIC_DIR

The published site stays as it is; only the hub's copy changes, and only this much:

  * the hub's palette: the site's own hook for this is directory-override.css, which every
    maintained page loads after its embedded styles, so the hub's palette (web/tools-hub.css)
    is written there. A page without the hook gets a link to /tools-hub.css instead;
  * navigation: "Back to index" goes to the hub page that lists the calculator (RF & LoRa,
    Calculators, Electronics or Meshtastic, as the manifests in apps.d/ say), in the whole
    window rather than the hub bar's frame; the hub itself for a page none lists;
  * the site's index is recorded as irate-box-index.json (page, link text, group heading),
    so a calculator added to the index appears on the hub page for its group without a
    manifest entry of its own (manifests.py, "discover");
  * only the calculators the site's own index.html links to are kept; anything else in the
    repo is unlisted on purpose, and goes. index.html itself goes too: the hub's three tools
    pages replace it. So does everything that is not a page a guest opens or a file one of
    those pages loads (the repo's README-type files, scripts/, internal/, the Python tool):
    /tools/ serves the whole folder;
  * the IC Pinout ASCII Reference page, and links to it, are dropped: it documents a
    Python tool that never runs on the box.

Every page carries its own license (CC BY-SA, MIT or GPL-3.0, written in the page), and
both CC BY-SA and the GPL require a modified copy to say so, so each adapted page gets a
note saying what changed, with a link to the original. Running it twice changes nothing.
Stdlib only.
"""

import json
import re
import shutil
import sys
from pathlib import Path

from irate_box.hub import manifests

ORIGIN = "https://nomdetom.github.io/"
INDEX_JSON = "irate-box-index.json"
ALWAYS_KEEP = (INDEX_JSON, "irate-box-bundle.json")
DROP = ("ic-pinout-ascii-reference.html",)
MARK = "irate-box-adapted"
BACK_LINK = re.compile(r'<a\b([^>]*?)\bhref="(?:\./)?index\.html"([^>]*)>')
_DROPPED = r'<a\b[^>]*\bhref="(?:\./)?(?:' + "|".join(re.escape(d) for d in DROP) + r')"[^>]*>.*?</a\s*>'
# A list item holding only such a link goes with it, so no empty bullet is left behind.
DROPPED_ITEM = re.compile(r'<li\b[^>]*>\s*' + _DROPPED + r'\s*</li\s*>', re.S)
DROPPED_LINK = re.compile(_DROPPED, re.S)
STYLESHEET = '<link rel="stylesheet" href="/tools-hub.css">'
OVERRIDE = "directory-override.css"
HOOK = re.compile(r'<link\b[^>]*\bhref="(?:\./)?directory-override\.css"')


def homes(index):
    """Which hub page lists each calculator, from the manifests: the menu of its entry with the
    lowest order, or for a page only the site's index lists, its group's menu."""
    ms = manifests.load()
    menu_href = {mid: m["tile"]["href"] for mid, m in manifests.menus(ms).items()}
    out = {}
    for m in ms:
        for e in m.get("entries", []):
            page = re.match(r"^/app\.html#/tools/([^/#?]+\.html)$", e["href"])
            if page and e["menus"]:
                best = min(e["menus"], key=e["menus"].get)
                out.setdefault(page.group(1), menu_href.get(best, "/"))
        groups = (m.get("discover") or {}).get("groups", {})
        for item in index:
            menu = groups.get(item["group"])
            if menu:
                out.setdefault(item["page"], menu_href.get(menu, "/"))
    return out


HEADING_OR_LINK = re.compile(r'<h([23])\b[^>]*>(.*?)</h\1\s*>|<a\b[^>]*\bhref="(?:\./)?([A-Za-z0-9_-]+\.html)"[^>]*>(.*?)</a\s*>', re.S)


def index_groups(tools, keep):
    """The site's index as [{page, title, group}]: each kept page with its link text and the
    heading it sits under (an <h3> group inside an <h2> section, or the <h2> itself)."""
    try:
        text = (tools / "index.html").read_text(encoding="utf-8")
    except OSError:
        return None
    plain = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).replace("&amp;", "&").strip()
    h2 = h3 = ""
    out, seen = [], set()
    for m in HEADING_OR_LINK.finditer(text):
        if m.group(1) == "2":
            h2, h3 = plain(m.group(2)), ""
        elif m.group(1) == "3":
            h3 = plain(m.group(2))
        elif m.group(3) in keep and m.group(3) not in seen:
            seen.add(m.group(3))
            out.append({"page": m.group(3), "title": plain(m.group(4)), "group": h3 or h2})
    return out


def adapt(page, back):
    html = page.read_text(encoding="utf-8")
    if MARK in html:
        return False

    def to_hub(m):
        attrs = m.group(1) + m.group(2)
        target = "" if "target=" in attrs else ' target="_top"'
        return f'<a{m.group(1)}href="{back}"{target}{m.group(2)}>'

    html = BACK_LINK.sub(to_hub, html)
    html = DROPPED_LINK.sub("", DROPPED_ITEM.sub("", html))
    # After the page's own styles, so the hub's palette wins -- unless the page has the
    # site's override hook, which already loads the palette (written by main()).
    # </head> is optional in HTML, so fall back to just before <body>, or the very start.
    if HOOK.search(html):
        pass
    elif "</head>" in html:
        html = html.replace("</head>", f"  {STYLESHEET}\n</head>", 1)
    elif (body := re.search(r"<body\b", html)):
        html = html[:body.start()] + STYLESHEET + "\n" + html[body.start():]
    else:
        html = STYLESHEET + "\n" + html
    note = (f'<p class="{MARK}">Adapted for the Irate-Box: colours and navigation changed from '
            f'<a href="{ORIGIN}{page.name}">the original</a>, under the same license.</p>\n')
    head, sep, tail = html.rpartition("</body>")
    html = head + note + sep + tail if sep else html + note
    page.write_text(html, encoding="utf-8")
    return True


def published(tools):
    """The pages the site's own index.html links to: what the site means to publish."""
    try:
        text = (tools / "index.html").read_text(encoding="utf-8")
    except OSError:
        return None  # already adapted: index.html was removed on the first run
    return set(re.findall(r'href="(?:\./)?([A-Za-z0-9_-]+\.html)"', text)) - set(DROP)


ASSET = re.compile(r'\b(?:src|href)="(?:\./)?([A-Za-z0-9_][A-Za-z0-9_./-]*)"')


def loaded_by(tools, pages):
    """Local files the kept pages load (scripts, styles, images), relative to the folder."""
    out = set()
    for page in pages:
        for ref in ASSET.findall((tools / page).read_text(encoding="utf-8")):
            path = (tools / ref).resolve()
            # A link to another page is navigation, not something the page needs.
            if path.suffix != ".html" and path.is_file() and path.is_relative_to(tools.resolve()):
                out.add(path.relative_to(tools.resolve()).as_posix())
    return out


def prune(tools, pages):
    """Remove everything but the kept pages and what they load. Returns what went."""
    wanted = set(pages) | loaded_by(tools, pages) | set(ALWAYS_KEEP)
    dirs = {str(Path(w).parent) for w in wanted} - {"."}
    gone = []
    for entry in sorted(tools.iterdir()):
        name = entry.name
        if name in wanted or (entry.is_dir() and any(d == name or d.startswith(name + "/") for d in dirs)):
            continue
        shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        gone.append(name + ("/" if entry.is_dir() else ""))
    return gone


def main(tools_dir, static_dir):
    tools, static = Path(tools_dir), Path(static_dir)
    keep = published(tools)
    if keep is None:  # already adapted: the pages still here are the kept ones
        keep = {p.name for p in tools.glob("*.html")}
    else:
        print(f"    calculators: keeping the {len(keep)} the site's index links to")
        (tools / INDEX_JSON).write_text(json.dumps(index_groups(tools, keep), indent=1), encoding="utf-8")
    try:
        index = json.loads((tools / INDEX_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = []
    gone = prune(tools, sorted(n for n in keep if (tools / n).is_file()))
    if gone:
        print(f"    calculators: removed {', '.join(gone)}")
    # The site's own override file is a template, all commented out; the hub's palette
    # replaces it, so every page with the hook takes the hub's colours.
    (tools / OVERRIDE).write_text(
        "/* Written by irate-box's adapt_tools.py: the hub's palette, from web/tools-hub.css,\n"
        "   in place of the site's commented-out template. */\n"
        + (static / "tools-hub.css").read_text(encoding="utf-8"), encoding="utf-8")
    listed = homes(index)
    changed = 0
    for page in sorted(tools.glob("*.html")):
        changed += adapt(page, listed.get(page.name, "/"))
    print(f"    calculators adapted for the hub: {changed} pages")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(sys.argv[1], sys.argv[2]))
