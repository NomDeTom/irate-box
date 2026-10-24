# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One Save and Discard on /admin: every primary Save is a section's (AW.section in
web/admin-widgets.js), so it keeps one set of rules: compared with what is saved, Discard beside it, one question at
Save, the draft kept until the save is done. Read as text, nothing run; tests/jsdom/dom-save-guard.cjs checks the
pages as drawn.

  admin.js and admin-layout.js draw no button labelled Save themselves: a section's footer is the only one
  admin-widgets.js labels Save in the section only, and Discard beside it
  the old words are gone: "Save changes", "(unsaved)", "Save all folder changes", "Save network settings", a
    "Save (N unsaved)" count, a Save named for what it saves
  admin.html's Save buttons are in forms that admin.js makes sections (AW.form), by id or by class

python3 tests/save_guard.py"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


js = (WEB / "admin.js").read_text()
layout = (WEB / "admin-layout.js").read_text()
widgets = (WEB / "admin-widgets.js").read_text()
html = (WEB / "admin.html").read_text()

# A button the page draws with a Save label: actionButton("Save…"), AW.btn("Save…"), textContent: "Save…", text: "Save…".
DRAWN = re.compile(r"""(?:actionButton|AW\.btn|btn)\(\s*["'`]Save\b|(?:textContent|text)\s*[:=]\s*["'`]Save\b""")
for name, src in (("admin.js", js), ("admin-layout.js", layout)):
    found = [m.group(0) for m in DRAWN.finditer(src)]
    check(f"{name} draws no Save button of its own (a section's footer is the only one)", not found, found)

labels = re.findall(r"""btn\(\s*['"]Save['"]""", widgets)
check("admin-widgets.js: Save labelled once, in the section", len(labels) == 1 and "aw-save" in widgets, labels)
check(
    "  Discard beside it",
    re.search(r"""btn\(\s*['"]Discard['"],\s*\{\s*class:\s*['"]action-btn aw-discard""", widgets) is not None,
)

OLD = [
    r"Save changes",
    r"\(unsaved\)",
    r"Save all folder changes",
    r"Discard all folder changes",
    r"Save network settings",
    r"Save default settings",
    r"Save retry settings",
    r"unsaved\)`",
    r"Save toolkit",
]
for name, src in (("admin.js", js), ("admin-widgets.js", widgets), ("admin.html", html)):
    left = [w for w in OLD if re.search(w, src)]
    check(f"{name}: none of the old Save words", not left, left)

# admin.html's Save buttons: each in a form admin.js makes a section, named by id or by its class's selector.
forms = []
for m in re.finditer(r"<form\b([^>]*)>(.*?)</form>", html, re.S):
    attrs, body = m.group(1), m.group(2)
    if re.search(r"<button[^>]*>\s*Save\s*</button>", body):
        fid = re.search(r'\bid="([^"]+)"', attrs)
        cls = re.search(r'\bclass="([^"]+)"', attrs)
        forms.append((fid.group(1) if fid else "", cls.group(1).split() if cls else []))
made = js + layout


def sectioned(fid, classes):
    if fid and re.search(rf"""getElementById\(\s*["']{re.escape(fid)}["']\s*\)""", made) and "AW.form(" in made:
        # The element named, then made a section: by a const holding it, or passed straight in.
        held = re.findall(rf"""(\w+)\s*[:=]\s*document\.getElementById\(\s*["']{re.escape(fid)}["']\s*\)""", made)
        if re.search(rf"""AW\.form\(\s*document\.getElementById\(\s*["']{re.escape(fid)}["']""", made):
            return True
        return any(re.search(rf"""AW\.form\(\s*(?:\w+\.)?{re.escape(h)}\b""", made) for h in held)
    return False


def by_class(classes):
    return any(
        re.search(rf"""querySelectorAll\(\s*["']form\.{re.escape(c)}["']\)[\s\S]{{0,200}}AW\.form\(""", made)
        or re.search(rf"""querySelectorAll\(\s*["']\.{re.escape(c)}["']\)\][\s\S]{{0,200}}AW\.form\(""", made)
        for c in classes
    )


loose = [f or " ".join(c) for f, c in forms if not (sectioned(f, c) or by_class(c))]
check(
    f"admin.html: each of its {len(forms)} forms with a Save is made a section by admin.js",
    len(forms) >= 10 and not loose,
    loose,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
