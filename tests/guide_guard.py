# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The user guide (docs/guide/*.md, irate_box/library/guide.py): the box's pages in web/guide/ are what the source
renders to (run `./irate-box guide build` after an edit); every link between the pages resolves; every page is
in the index; no vault link ([[…]]); each source page carries its SPDX lines; the ZIM builds, every page in it.
python3 tests/guide_guard.py"""
import re, struct, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import guide  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
src = sorted(guide.SOURCE.glob("*.md"))
check("the source: pages under docs/guide", len(src) >= 10, [p.name for p in src])
stale = [s for s, md in guide.pages() if (guide.WEB / f"{s}.html").read_text() != guide.page_html(s, md)] if guide.WEB.is_dir() else ["(none)"]
check("web/guide/ is what the source renders to (./irate-box guide build)", not stale, stale)
extra = sorted({p.stem for p in guide.WEB.glob("*.html")} - {p.stem for p in src})
check("no page in web/guide/ without its source", not extra, extra)
index = (guide.SOURCE / "index.md").read_text()
missing = [p.stem for p in src if p.stem != "index" and f"({p.stem}.md)" not in index]
check("every page in the index", not missing, missing)
dead = [(p.name, h) for p in guide.WEB.glob("*.html") for h in re.findall(r'href="([^"#:]+\.html)"', p.read_text())
        if not h.startswith("/") and not (guide.WEB / h).is_file()]
dead += [(p.name, h) for p in guide.WEB.glob("*.html") for h in re.findall(r'href="(/guide/[^"#]+)"', p.read_text())
         if not (REPO / "web" / h.lstrip("/")).is_file()]
check("every link between the pages resolves", not dead, dead)
check("no vault links", not [p.name for p in src if "[[" in p.read_text()])
check("each source page carries its SPDX lines", all(p.read_text().startswith("<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->") for p in src))
out = Path(tempfile.mkdtemp(prefix="guide-")) / guide.ZIM_FILE
guide.zim(out, "test")
data = out.read_bytes()
magic, major, _, _, n = struct.unpack_from("<IHH16sI", data)
check("the ZIM: a ZIM, every page in it, the index its main page", magic == 72173914 and major == 5
      and {f"{p.stem}.html".encode() for p in src} <= {u for u in re.findall(rb"([a-z0-9-]+\.html)\0", data)}, (magic, major, n))
check("  its pages link among themselves, not to the box's paths", b'href="/guide/' not in data)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
