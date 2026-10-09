#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""SilverBullet's manual in the notes (library/sbdocs.py): its links re-pointed, nothing of it run, the
site's configuration left out, the starting page's two links to the copy, a reproducible pack."""
import io, os, sys, tarfile, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import sbdocs  # noqa: E402

fails = 0
def check(name, ok, extra=None):
    global fails
    print(("PASS " if ok else "FAIL ") + name + ("" if ok or extra is None else f"  {extra!r}"))
    fails += not ok

T = Path(tempfile.mkdtemp(prefix="sbdocs-"))

# --- one page ---
page = """See [[Install]], [[Getting Started#Next|start here]] and ![[Document#Media resizing]].
Std: [[^Library/Std/Config]]; online: [the manual](https://silverbullet.md/Space%20Lua#Intro).
A query: ${query[[from p = index.pages("guide") select templates.pageItem(p)]]} and `[[Not a link]]`.
```space-lua
command.define { name = "Danger" }
```
```space-style
body { color: red }
```
```markdown
[[Example]]
```
"""
got = sbdocs.adapt(page)
check("links to the manual's pages point at the copy, anchors and labels kept",
      "[[SilverBullet/Install]]" in got and "[[SilverBullet/Getting Started#Next|start here]]" in got
      and "![[SilverBullet/Document#Media resizing]]" in got, got)
check("  SilverBullet's own library links left alone", "[[^Library/Std/Config]]" in got)
check("  a link to silverbullet.md becomes one to the copy, its label kept", "[[SilverBullet/Space Lua#Intro|the manual]]" in got, got)
check("  live queries and inline code untouched", '${query[[from p = index.pages("guide") select templates.pageItem(p)]]}' in got
      and "`[[Not a link]]`" in got, got)
check("  nothing runs: space-lua and space-style shown as lua and css", "```space-lua" not in got and "```space-style" not in got
      and "```lua\ncommand.define" in got and "```css\nbody" in got, got)
check("  links inside a code block untouched", "```markdown\n[[Example]]\n```" in got, got)

# --- a pack, reproducible ---
src = T / "docs"
for rel, text in (("Manual.md", "Start at [[Getting Started]]."), ("Getting Started.md", "Hello."), ("CONFIG.md", "```space-lua\nx=1\n```"),
                  ("Library/Website.md", "```space-lua\ny=1\n```"), ("API/space.md", "See [[Manual]]."), ("pic.png", "\x89PNG")):
    (src / rel).parent.mkdir(parents=True, exist_ok=True)
    (src / rel).write_text(text)
sbdocs.pack(src, T / "a.tar.gz")
os.utime(src / "Manual.md", (1, 1))
sbdocs.pack(src, T / "b.tar.gz")
check("the pack is reproducible: the same pages give the same bytes", (T / "a.tar.gz").read_bytes() == (T / "b.tar.gz").read_bytes())
check("  its digest is of the tar it holds", len(sbdocs.sha256(T / "a.tar.gz")) == 64)

# --- into a space ---
space = T / "notes"; space.mkdir()
start = ("If you're confused, have a look at the [Manual](https://silverbullet.md/Manual), or perhaps the "
         "[Getting Started](https://silverbullet.md/Getting%20Started) page. [the community forums](https://community.silverbullet.md/).\n")
(space / "index.md").write_text(start)
(space / "My page.md").write_text("mine")
said = sbdocs.install(T / "a.tar.gz", space, "2.11.1")
c = space / "SilverBullet"
check("installed under SilverBullet/, the site's configuration left out", (c / "Manual.md").is_file() and (c / "API/space.md").is_file()
      and (c / "pic.png").is_file() and not (c / "CONFIG.md").exists() and not (c / "Library").exists() and "3 pages" in said, said)
check("  its pages' links re-pointed", (c / "Manual.md").read_text() == "Start at [[SilverBullet/Getting Started]]."
      and (c / "API/space.md").read_text() == "See [[SilverBullet/Manual]].")
check("  a page that says what the folder is", "replaced whole" in (space / "SilverBullet.md").read_text())
idx = (space / "index.md").read_text()
check("  the starting page's two links point at the copy; the forums link left as it is",
      "[[SilverBullet/Manual|Manual]]" in idx and "[[SilverBullet/Getting Started|Getting Started]]" in idx
      and "https://community.silverbullet.md/" in idx and "silverbullet.md/Manual" not in idx, idx)
check("  Syncthing leaves it out", "/SilverBullet\n" in (space / ".stignore").read_text())
check("  the owner's pages untouched", (space / "My page.md").read_text() == "mine")
(c / "Manual.md").write_text("edited")
check("the same version again: nothing replaced", "already" in sbdocs.install(T / "a.tar.gz", space, "2.11.1")
      and (c / "Manual.md").read_text() == "edited")
sbdocs.install(T / "a.tar.gz", space, "2.12.0")
check("a new version: replaced whole", (c / "Manual.md").read_text().startswith("Start at") and not (space / ".SilverBullet.old").exists()
      and not [p for p in space.iterdir() if p.name.startswith(".sbdocs-")])
check("  .stignore not repeated", (space / ".stignore").read_text().count("/SilverBullet\n") == 1)
(space / "index.md").write_text("The owner's own start page. [Manual](https://example.org/Manual)\n")
sbdocs.install(T / "a.tar.gz", space, "2.12.0")
check("an owner's own starting page left alone", (space / "index.md").read_text() == "The owner's own start page. [Manual](https://example.org/Manual)\n")

# --- a pack that tries to write outside ---
evil = T / "evil.tar.gz"
with tarfile.open(evil, "w:gz") as tar:
    for name in ("docs/../../escape.md", "docs/ok.md"):
        info = tarfile.TarInfo(name); data = b"x"; info.size = len(data); tar.addfile(info, io.BytesIO(data))
sbdocs.install(evil, space, "9.9.9")
check("a pack's member that climbs out is skipped", not (T / "escape.md").exists() and (c / "ok.md").is_file())

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
