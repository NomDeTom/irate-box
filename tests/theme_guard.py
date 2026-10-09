# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The themes stay in one place (web/themes.js and web/themes/): no other file names a theme
other than light and dark, every page with a picker loads themes.js after style.css, and no
page carries its own copy of the boot script or of the picker's buttons.
python3 tests/theme_guard.py"""
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


registry = (WEB / "themes.js").read_text(encoding="utf-8")
ids = re.findall(r"\{ id: '([a-z0-9-]+)'", registry)
check("themes.js lists light and dark as the main theme", {"light", "dark"} <= set(ids), ids)
others = [i for i in ids if i not in ("light", "dark")]
for t in others:
    check(f"themes/{t}.css exists", (WEB / "themes" / f"{t}.css").is_file())
    css = (WEB / "themes" / f"{t}.css").read_text(encoding="utf-8")
    bad = [line for line in css.splitlines() if "{" in line and not line.lstrip().startswith(("/*", "@media", "}"))
           and f'[data-theme="{t}"]' not in line and not line.strip().endswith(",")]
    check(f"themes/{t}.css styles only [data-theme=\"{t}\"]", not bad, bad[:3])

# Every file the pages are made from, but the registry and the themes themselves.
sources = [p for p in list(WEB.rglob("*")) + [REPO / "irate_box" / "hub" / "server.py"]
           if p.is_file() and p.suffix in (".html", ".js", ".css", ".py")
           and p != WEB / "themes.js" and WEB / "themes" not in p.parents]
for t in others:
    named = [str(p.relative_to(REPO)) for p in sources if re.search(rf"\b{re.escape(t)}\b", p.read_text(encoding="utf-8", errors="replace"))]
    check(f"'{t}' named only in themes.js and themes/", not named, named)

for p in sorted(WEB.glob("*.html")) + [REPO / "irate_box" / "hub" / "server.py"]:
    text = p.read_text(encoding="utf-8")
    if "theme-picker" not in text:
        continue
    name = p.relative_to(REPO)
    check(f"{name}: loads /themes.js after style.css",
          re.search(r'style\.css">\s*\n\s*<script src="/themes\.js"></script>', text) is not None)
    check(f"{name}: no boot script of its own", "localStorage.getItem('theme')" not in text)
    check(f"{name}: no hand-written theme buttons", "data-theme-choice" not in text)

# Not a theme, but the same kind of drift: every page's 🏠 is labelled "Hub".
bare = [str(p.relative_to(REPO)) for p in sorted(WEB.glob("*.html")) + [REPO / "irate_box" / "hub" / "server.py"]
        if re.search(r'>🏠</a>', p.read_text(encoding="utf-8"))]
check("every 🏠 link says Hub", not bare, bare)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
