#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""cgit's about-filter: a repository's README on stdin, its name as the argument, HTML out for
the about page.

Anyone allowed to push can write a README, and the page is on the hub's own origin, where a
visitor's browser may hold the admin login. So nothing in it becomes markup the README did not
ask for in Markdown: HTML written in the file (blocks and inline) is shown as text, and a link
or image whose address is javascript:, data: (but images), vbscript: or file: becomes inert.
The web server also forbids every script but the box's own on these pages (Content-Security-
Policy); this is the first of the two locks, not the only one.

Markdown (.md, .markdown) is rendered with Python-Markdown; anything else is shown as text.
"""
import html
import re
import sys

MAX = 512 * 1024
BAD_SCHEME = re.compile(r"^\s*(javascript|vbscript|file|data):", re.I)


def plain(text):
    sys.stdout.write('<pre class="readme-text">' + html.escape(text, quote=False) + "</pre>\n")


def markdown_html(text):
    import markdown
    from markdown.treeprocessors import Treeprocessor

    class Inert(Treeprocessor):
        def run(self, root):
            for el in root.iter():
                for attr in ("href", "src"):
                    v = el.get(attr)
                    if v is None:
                        continue
                    # Browsers drop tabs, newlines and controls inside a scheme: java\tscript: is javascript:.
                    v = re.sub(r"[\x00-\x20\x7f]", "", v)
                    if BAD_SCHEME.match(v) and not (attr == "src" and re.match(r"^data:image/(png|gif|jpeg|webp);", v, re.I)):
                        el.set(attr, "#")
                if el.tag == "a" and el.get("href", "").startswith(("http://", "https://")):
                    el.set("rel", "nofollow noopener noreferrer")

    md = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists"], output_format="html")
    # Raw HTML is text: no block of it, and no inline tag, passes through as markup.
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    md.treeprocessors.register(Inert(md), "irate_box_inert", 0)
    return md.convert(text)


def main():
    raw = sys.stdin.buffer.read(MAX + 1)
    text = raw[:MAX].decode("utf-8", errors="replace")
    name = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if name.endswith((".md", ".markdown")):
        # HTML comments (licence lines, notes to editors) are not for the reader: dropped, not shown.
        text = re.sub(r"<!--.*?-->[ \t]*\n?", "", text, flags=re.S)
        try:
            sys.stdout.write('<div class="readme-md">' + markdown_html(text) + "</div>\n")
            return
        except ImportError:
            pass
    plain(text)


if __name__ == "__main__":
    main()
