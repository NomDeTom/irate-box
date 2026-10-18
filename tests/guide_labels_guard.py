# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The guide names what a person sees: every label it puts in bold (docs/user/*.md) is a word the box's pages,
helper messages or manifests still carry. A button renamed on a page and left in the guide fails here.
A path in bold (Access → Remote access) is checked part by part. Bold that ends in a full stop, a colon or a
question mark is a lead-in, and bold that starts in lower case is emphasis: neither is a label. Other bold that
names no control is listed in NOT_LABELS with its reason.
python3 tests/guide_labels_guard.py"""
import html
import re
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# Where a label can come from: the pages and their scripts, the hub and root helper (finding titles, action
# labels), the manifests (app and toolkit names), the installer and the dispatcher (their own output).
SOURCES = [*(REPO / "web").glob("*.js"), *(REPO / "web").glob("*.html"), *(REPO / "irate_box").rglob("*.py"),
           *(REPO / "apps.d").glob("*.json"), *(REPO / "addons").rglob("*.json"), *(REPO / "toolkits").rglob("*.json"),
           REPO / "install.sh", REPO / "irate-box"]
# Bold in the guide that is not a control's name: descriptions, LED patterns, glossary words for things with no
# label of their own. Each with why; remove an entry when its words become a label.
NOT_LABELS = {
    "1 second on, 1 second off": "an LED pattern, described",
    "Steady on": "an LED pattern, described",
    "Quick flashes": "an LED pattern, described",
    "Two short flashes": "an LED pattern, described",
    "The theme picker": "names the control, which shows an icon and no words",
    "A script calls the administration pages": "a case in a list (first-use.md), not a control",
    "Settings links": "a part of the Box doctor page, described (troubleshooting.md), not a control",
}


def source_text():
    parts = []
    for p in SOURCES:
        if p.is_file():
            parts.append(html.unescape(p.read_text(errors="ignore")))
    # Labels in HTML can wrap across lines and carry inline tags: one space, no tags.
    return re.sub(r"\s+", " ", re.sub(r"</?[A-Za-z][^<>]*>", "", " ".join(parts)))


def labels(md):
    """(label, line) for each bold span that names a control: whole, or each part of an → path."""
    for n, line in enumerate(md.read_text().splitlines(), 1):
        for span in re.findall(r"\*\*([^*]+)\*\*", line):
            span = re.sub(r"\s+", " ", span).strip()
            if not span or span[-1] in ".:?" or span[0].islower() or span in NOT_LABELS:
                continue
            for part in (s.strip() for s in span.split("→")):
                if part and part not in NOT_LABELS:
                    yield part, n


text = source_text()
pages = sorted((REPO / "docs" / "user").glob("*.md"))
check("the guide has pages to read", len(pages) > 5, [p.name for p in pages])
for md in pages:
    if md.name == "glossary.md":
        continue  # the glossary defines words; labels are checked where the pages use them
    missing = sorted({f"{lab} (line {n})" for lab, n in labels(md) if lab not in text})
    check(f"{md.name}: every bold label is on a page, in a helper message or in a manifest", not missing, "; ".join(missing))
stale = [k for k in NOT_LABELS if k in text]
check("NOT_LABELS holds no word that has since become a label", not stale, stale)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
