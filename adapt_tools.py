#!/usr/bin/env python3
"""Adapt a copy of the nomdetom.github.io calculators for the hub. Run by install.sh.

    adapt_tools.py TOOLS_DIR HUB_STATIC_DIR

The published site stays as it is; only the hub's copy changes, and only this much:

  * the hub's palette: each page links /tools-hub.css after its own styles;
  * navigation: "Back to index" goes to the hub page that lists the calculator (RF & LoRa,
    Calculators, Electronics, or Meshtastic, read from those pages' own links), in the
    whole window rather than the hub bar's frame; the hub itself for a page none lists;
  * only the calculators the site's own index.html links to are kept; anything else in the
    repo is unlisted on purpose, and goes. index.html itself goes too: the hub's three tools
    pages replace it;
  * the IC Pinout ASCII Reference page, and links to it, are dropped: it documents a
    Python tool that never runs on the box.

Every page carries its own license (CC BY-SA, MIT or GPL-3.0, written in the page), and
both CC BY-SA and the GPL require a modified copy to say so, so each adapted page gets a
note saying what changed, with a link to the original. Running it twice changes nothing.
Stdlib only.
"""

import re
import sys
from pathlib import Path

ORIGIN = "https://nomdetom.github.io/"
SUBPAGES = ("tools-rf.html", "tools-general.html", "tools-electronics.html", "meshtastic.html")
DROP = ("ic-pinout-ascii-reference.html",)
MARK = "irate-box-adapted"
BACK_LINK = re.compile(r'<a\b([^>]*?)\bhref="(?:\./)?index\.html"([^>]*)>')
_DROPPED = r'<a\b[^>]*\bhref="(?:\./)?(?:' + "|".join(re.escape(d) for d in DROP) + r')"[^>]*>.*?</a\s*>'
# A list item holding only such a link goes with it, so no empty bullet is left behind.
DROPPED_ITEM = re.compile(r'<li\b[^>]*>\s*' + _DROPPED + r'\s*</li\s*>', re.S)
DROPPED_LINK = re.compile(_DROPPED, re.S)
STYLESHEET = '<link rel="stylesheet" href="/tools-hub.css">'


def homes(static):
    """Which hub page lists each calculator: the first of SUBPAGES that links to it."""
    out = {}
    for sub in SUBPAGES:
        try:
            text = (static / sub).read_text(encoding="utf-8")
        except OSError:
            continue
        for name in re.findall(r'href="/app\.html#/tools/([^"#?]+\.html)', text):
            out.setdefault(name, "/" + sub)
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
    # After the page's own styles, so the hub's palette wins. </head> is optional in HTML,
    # so fall back to just before <body>, or the very start.
    if "</head>" in html:
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


def main(tools_dir, static_dir):
    tools, static = Path(tools_dir), Path(static_dir)
    keep = published(tools)
    if keep is not None:
        removed = [p for p in tools.glob("*.html") if p.name not in keep]
        for page in removed:
            page.unlink()
        print(f"    calculators: keeping the {len(keep)} the site's index links to; "
              f"dropped {', '.join(sorted(p.name for p in removed))}")
    listed = homes(static)
    changed = 0
    for page in sorted(tools.glob("*.html")):
        changed += adapt(page, listed.get(page.name, "/"))
    print(f"    calculators adapted for the hub: {changed} pages")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.split("\n\n")[1])
    sys.exit(main(sys.argv[1], sys.argv[2]))
