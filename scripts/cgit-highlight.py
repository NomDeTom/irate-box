#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""cgit's source-filter: a file's text on stdin, its name as the argument, highlighted HTML out
(cgit puts it inside <pre><code>). Pygments, with class names only and no colours of its own:
web/cgit-hub.css colours the classes from the hub's palette, so they follow the theme.

Large files, and anything Pygments does not know or cannot read, come out escaped as plain
text: on a small board a 1 MB file must not tie up the CPU for a page nobody reads in colour.
"""
import html
import sys

MAX = 256 * 1024


def plain(text):
    sys.stdout.write(html.escape(text, quote=False))


def main():
    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", errors="replace")
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    if len(raw) > MAX:
        return plain(text)
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import guess_lexer_for_filename
        from pygments.util import ClassNotFound
    except ImportError:
        return plain(text)
    try:
        lexer = guess_lexer_for_filename(name, text, stripnl=False)
    except ClassNotFound:
        return plain(text)
    sys.stdout.write(highlight(text, lexer, HtmlFormatter(nowrap=True, classprefix="hl-")))


if __name__ == "__main__":
    main()
