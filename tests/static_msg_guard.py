# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The pages' own words (web/*.html) are messages too: each piece of text a person reads carries a stable ID,
and the English stays in the page as the default (web/i18n.js says it in another language, from a catalogue).

  <h2 data-i18n="admin.overview.title">Overview</h2>
  <p data-i18n="admin.first-use.no-admin"><strong>This box has no administrator account.</strong> Anyone …
     <a href="/admin/setup">Set up this box</a> over HTTPS …</p>
     its English: "<0>This box has no administrator account.</0> Anyone … <1>Set up this box</1> over HTTPS …"
  <input placeholder="name or type" data-i18n-placeholder="admin.app-inventory.search.placeholder">

A message is the element's content. Inside it, an element that is part of the sentence (a link, strong, em, a
span: INLINE below, with letters in it) is a numbered tag, <n>…</n>, so a translation can move it and keeps the
element and its attributes. Any other element (code, kbd, pre, a form control, a <br>, an element with its own
data-i18n, one marked translate="no", one with no letters: an emoji, a number, a span the script fills) is <n/>:
kept whole, its content not in the message. n counts every element under the message, in document order.
web/i18n.js reads a message by the same rules; tests/jsdom/dom-i18n.cjs checks that the two agree.

Attributes a person reads: data-i18n-<name> holds the ID of the attribute <name> (placeholder, title, aria-label,
alt, content, label), or of data-<name> (the choices' data-says, data-why, data-often-says-N).

Not messages: commands, paths and code (<code>, <kbd>, <pre>, <samp>), a name or other data standing alone
(translate="no", the HTML attribute that tells browsers' translators the same), text with no letters.

  every visible piece of text sits in an element with data-i18n, or is one of those
  every text attribute has its data-i18n-<name>
  every ID has the form of a message ID (irate_box/msg.py ID_RE), page.section.thing, starting with its page
  one ID, one English, across the pages; and an ID that a message of the code (M(...)) also uses says the same
  no data-i18n on an element with nothing to say (an ID left behind)

python3 tests/static_msg_guard.py  [--list: every ID, its English and where]  [--json: {id: English}]"""

import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from irate_box.msg import ID_RE

WEB = REPO / "web"
# Keep these three in step with web/i18n.js.
INLINE = {
    "a",
    "abbr",
    "b",
    "cite",
    "dfn",
    "em",
    "i",
    "mark",
    "q",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
}
SKIP = {"script", "style", "code", "kbd", "pre", "samp", "var", "template"}
# A container (a list, a table row, a section) is a message only when it has words of its own between its
# children: one that holds only links or paragraphs holds several messages, each child its own.
CONTAINERS = {
    "body",
    "html",
    "head",
    "header",
    "footer",
    "main",
    "nav",
    "section",
    "article",
    "aside",
    "div",
    "ul",
    "ol",
    "dl",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "form",
    "fieldset",
    "details",
    "select",
    "datalist",
    "figure",
    "menu",
    "hgroup",
    "search",
    "optgroup",
}
TEXT_ATTRS = {"placeholder", "title", "aria-label", "alt", "label", "aria-description", "aria-placeholder"}
DATA_TEXT = re.compile(r"^data-(says(-\d+)?|why|often-says(-\d+)?)$")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "line", "end")

    def __init__(self, tag, attrs, parent, line, end=None):
        self.tag, self.attrs, self.children, self.parent, self.line, self.end = tag, attrs, [], parent, line, end

    def elements(self):
        return [c for c in self.children if isinstance(c, Node)]


class Tree(HTMLParser):
    """A page as a tree: elements, their attributes (in source order, as written) and text. end is the
    offset in the source of the start tag's closing '>' (where a tool can add an attribute)."""

    def __init__(self, src):
        super().__init__(convert_charrefs=True)
        self.src = src
        self.lines = [0]
        for m in re.finditer("\n", src):
            self.lines.append(m.end())
        self.root = Node("#root", {}, None, 0)
        self.cur = self.root
        self.feed(src)
        self.close()

    def _offset(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        raw = self.get_starttag_text()
        end = start + len(raw) - (2 if raw.endswith("/>") else 1)
        node = Node(tag, dict(attrs), self.cur, self.getpos()[0], end)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.cur = self.cur.parent

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def letters(s):
    return any(ch.isalpha() for ch in s)  # a letter of any script (\p{L}, as web/i18n.js tests)


def no_translate(node):
    return node.attrs.get("translate") == "no"


def has_words(node):
    """Letters in the node's own text, not counting what is skipped or kept apart."""
    for c in node.children:
        if isinstance(c, str):
            if letters(c):
                return True
        elif c.tag not in SKIP and not no_translate(c) and "data-i18n" not in c.attrs and has_words(c):
            return True
    return False


def inline(node):
    """A child that is part of its parent's sentence: its words are in the message, as <n>…</n>."""
    return node.tag in INLINE and "data-i18n" not in node.attrs and not no_translate(node) and has_words(node)


def message(el):
    """The English of a message element: its text, with its child elements as <n>…</n> or <n/>, and white
    space as a browser shows it (runs made one space, none at the ends)."""
    count = [0]

    def walk(node):
        out = []
        for c in node.children:
            if isinstance(c, str):
                out.append(c)
                continue
            n = count[0]
            count[0] += 1
            if inline(c):
                out.append(f"<{n}>{walk(c)}</{n}>")
            else:
                out.append(f"<{n}/>")
                count[0] += len(list(descendants(c)))
        return "".join(out)

    return re.sub(r"\s+", " ", walk(el)).strip()


def descendants(node):
    for c in node.elements():
        yield c
        yield from descendants(c)


def own_words(node):
    """Letters in the node's own text: directly in it, not in a child element."""
    return any(isinstance(c, str) and letters(c) for c in node.children)


def is_message(node):
    """Where a message sits: an element whose sentence (its text and its inline children) has words. A container
    only when it has words of its own between its children."""
    if node.tag in SKIP or no_translate(node):
        return False
    if node.tag in CONTAINERS:
        return own_words(node)
    return letters(re.sub(r"<\d+/?>|</\d+>", "", message(node)))


def text_attrs(node):
    """(attribute, its data-i18n-<name>) for each attribute of the node that a person reads."""
    for a, v in node.attrs.items():
        if v is None or not letters(v):
            continue
        if a in TEXT_ATTRS or (a == "content" and node.tag == "meta" and node.attrs.get("name") == "description"):
            yield a, "data-i18n-" + a
        elif DATA_TEXT.match(a):
            yield a, "data-i18n-" + a[len("data-") :]


def section_of(node):
    n = node.parent
    while n is not None:
        if n.tag == "section" and n.attrs.get("id"):
            return n.attrs["id"]
        if n.tag in ("head", "header", "footer", "nav") and n.parent is not None and n.parent.tag in ("html", "body"):
            return n.tag
        n = n.parent
    return ""


class Page:
    """What one page says: its messages [(node, id or None, English)], its attributes [(node, attribute,
    id or None, English)], text outside any message [(line, text)], and IDs on elements with nothing to say."""

    def __init__(self, path):
        self.path = path
        self.src = path.read_text(encoding="utf-8")
        self.tree = Tree(self.src)
        self.messages, self.attributes, self.loose, self.empty_ids = [], [], [], []
        self.walk(self.tree.root, False)

    def walk(self, node, in_message):
        for c in node.children:
            if isinstance(c, str):
                if (
                    not in_message
                    and letters(c)
                    and node.tag not in SKIP
                    and not (node.tag != "#root" and no_translate(node))
                ):
                    self.loose.append((node.line, re.sub(r"\s+", " ", c).strip()))
                continue
            for a, key in text_attrs(c):
                if not no_translate(c):
                    self.attributes.append((c, a, c.attrs.get(key), re.sub(r"\s+", " ", c.attrs[a]).strip()))
            if c.tag in SKIP or no_translate(c):
                continue
            if in_message and inline(c):
                self.walk(c, True)
            elif is_message(c):
                self.messages.append((c, c.attrs.get("data-i18n"), message(c)))
                self.walk(c, True)
            else:
                if "data-i18n" in c.attrs:
                    self.empty_ids.append((c.line, c.attrs["data-i18n"]))
                self.walk(c, False)


def pages():
    return [Page(p) for p in sorted(WEB.glob("*.html"))]


def collect(found):
    """{id: (English, where)} across the pages, and the problems: [(kind, text)]."""
    ids, problems = {}, []
    for page in found:
        stem = page.path.stem
        name = f"web/{page.path.name}"
        said = [(n.line, mid, en, "") for n, mid, en in page.messages] + [
            (n.line, mid, en, f" {a}") for n, a, mid, en in page.attributes
        ]
        for line, mid, en, attr in said:
            where = f"{name}:{line}{attr}"
            if mid is None:
                problems.append(("unmarked", f"{where} {en[:70]!r}"))
                continue
            if not ID_RE.match(mid) or not mid.startswith(stem + "."):
                problems.append(("bad-id", f"{where} {mid!r}"))
            if mid in ids and ids[mid][0] != en:
                problems.append(("two-english", f"{mid}: {ids[mid][1]} {ids[mid][0][:50]!r} and {where} {en[:50]!r}"))
            ids.setdefault(mid, (en, where))
        problems += [("loose", f"{name}:{line} {t[:70]!r}") for line, t in page.loose]
        problems += [("empty", f"{name}:{line} {mid}") for line, mid in page.empty_ids]
    return ids, problems


def main():
    found = pages()
    ids, problems = collect(found)
    if "--list" in sys.argv:
        for mid, (en, where) in sorted(ids.items()):
            print(f"{mid}\t{json.dumps(en, ensure_ascii=False)}\t{where}")
        sys.exit(0)
    if "--json" in sys.argv:
        print(json.dumps({mid: en for mid, (en, _) in sorted(ids.items())}, ensure_ascii=False, indent=1))
        sys.exit(0)

    fails = 0

    def check(name, cond, info=""):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
        fails += not cond

    def of(kind):
        return [t for k, t in problems if k == kind]

    per_page = ", ".join(
        f"{p.path.stem} {len(({m for _, m, _ in p.messages} | {m for _, _, m, _ in p.attributes}) - {None})}"
        for p in found
    )
    check(f"the pages say something ({len(ids)} IDs: {per_page})", len(ids) > 500, len(ids))
    check(
        'every piece of text a person reads is in an element with data-i18n (or code, or translate="no")',
        not of("unmarked") and not of("loose"),
        of("unmarked") + of("loose"),
    )
    check("every ID is a message ID, page.section.thing, starting with its page's name", not of("bad-id"), of("bad-id"))
    check("one ID, one English across the pages", not of("two-english"), of("two-english"))
    check("no data-i18n on an element with nothing to say", not of("empty"), of("empty"))

    import msg_guard  # the messages of the code, read with ast (nothing imported from irate_box but the ID form)

    clash = [
        f"{mid}: page {en[:50]!r}, code {msg_guard.messages[mid][0]!r} ({msg_guard.messages[mid][1]})"
        for mid, (en, _) in ids.items()
        if mid in msg_guard.messages and msg_guard.messages[mid][0] != en
    ]
    check("an ID that the code's messages also use says the same thing", not clash, clash)

    print("ok" if not fails else f"{fails} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
