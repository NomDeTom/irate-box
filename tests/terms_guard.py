# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's words (docs/dev/terms.json): the glossary page is made from it, and what a person reads keeps
to it. Only what a person reads: the English of every message (M(...) in irate_box/, as msg_guard.py reads
them), the guide's sources (docs/user/*.md) and the visible text of the pages (web/*.html). Not comments,
log lines, commands, evidence or what people write.

  docs/user/glossary.md is what terms.json makes (--write makes it; then ./irate-box guide build)
  a message uses none of the words to avoid (a failure: the messages are new text)
  a sentence of a message has at most 25 words (STE 6.3; a warning, as an instruction's 20 is not told apart)
  the pages use none of the words to avoid; the guide neither (warnings while tests/guide-pending holds it back)

python3 tests/terms_guard.py  [--write] [--all: every warning, not the first few]"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import msg_guard  # noqa: E402  (its scan of the messages)

TERMS = json.loads((REPO / "docs" / "dev" / "terms.json").read_text())
AVOID = [(re.compile(a["words"], re.I), a["use"]) for a in TERMS["avoid"]]
GLOSSARY = REPO / "docs" / "user" / "glossary.md"
SHOWN = None if "--all" in sys.argv else 5
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def warn(name, items):
    if items:
        more = f" … and {len(items) - SHOWN} more (--all)" if SHOWN and len(items) > SHOWN else ""
        print(f"WARN {name} ({len(items)})  {items[:SHOWN]}{more}")


def glossary():
    rows = "".join(f"| {t['term']} | {t['definition']} |\n" for t in TERMS["terms"])
    return ("<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->\n<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->\n# Glossary\n\n"
            "*Words that the box pages use.*\n\n| Term | Definition |\n|---|---|\n" + rows)


def hits(text):
    return [f"{m.group(0)!r} → {use}" for rx, use in AVOID for m in rx.finditer(text)]


class Visible(HTMLParser):
    """A page's text as a person sees it: not scripts, styles or attributes."""
    def __init__(self):
        super().__init__()
        self.skip, self.text = 0, []

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "code", "pre")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "code", "pre") and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def markdown_prose(md):
    md = re.sub(r"(?s)```.*?```|<!--.*?-->", " ", md)
    md = re.sub(r"`[^`]*`", " ", md)
    return re.sub(r"\]\([^)]*\)", "]", md)   # a link's target is not prose


if "--write" in sys.argv:
    GLOSSARY.write_text(glossary())
    print(f"wrote {GLOSSARY.relative_to(REPO)}; now ./irate-box guide build")
check("docs/user/glossary.md is what docs/dev/terms.json makes (--write)", GLOSSARY.read_text() == glossary())

bad, long_ = [], []
for mid, (en, where) in sorted(msg_guard.messages.items()):
    for text in (en.values() if isinstance(en, dict) else [en]):
        bad += [f"{mid}: {h}" for h in hits(text)]
        long_ += [f"{mid}: {len(s.split())} words" for s in re.split(r"(?<=[.?!])\s+", text) if len(s.split()) > 25]
check(f"the messages ({len(msg_guard.messages)}) use none of the words to avoid", not bad, bad)
warn("sentences of messages longer than 25 words", long_)

# The guide: warned while it waits for its own pass (tests/guide-pending, placeholder mode), checked after.
guide_said = check if not (REPO / "tests" / "guide-pending").is_file() else (lambda name, cond, info="": warn(name.replace("no words", "words"), info))
for md in sorted((REPO / "docs" / "user").glob("*.md")):
    found = hits(markdown_prose(md.read_text()))
    guide_said(f"docs/user/{md.name}: no words to avoid", not found, found)
for page in sorted((REPO / "web").glob("*.html")):
    v = Visible()
    v.feed(page.read_text())
    found = hits(" ".join(v.text))
    check(f"web/{page.name}: no words to avoid", not found, found)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
