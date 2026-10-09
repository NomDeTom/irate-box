# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""cgit's README and code views stay inert: whatever a pushed README
holds, the filter puts out no markup it did not ask for in Markdown, and both web servers
send cgit's pages a CSP that allows no script but the box's own; and cgit.css's fixed colours
are all restated from the palette. Runs with or without
Python-Markdown and Pygments installed (without them, the filters show text).
python3 tests/cgit_guard.py"""
import re, subprocess, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def run(script, name, text):
    return subprocess.run([sys.executable, str(REPO / "scripts" / script), name], input=text.encode(),
                          capture_output=True).stdout.decode()

EVIL = ('# Hi\n\n<script>alert(1)</script> <img src=x onerror=alert(2)> <b>b</b>\n\n<div onclick="x">block</div>\n\n'
        '<iframe src="http://evil.example"></iframe>\n\n[a](javascript:alert(3)) [b](java\tscript:alert(4)) '
        '[c](<java\nscript:x>) [d](vbscript:x) [e](data:text/html,hi) ![f](data:text/html,<script>x</script>)\n\n'
        '[ok](https://example.com) ![img](data:image/png;base64,AAA=)\n')
for name in ("README.md", "README", "notes.txt"):
    out = run("cgit-about.py", name, EVIL)
    tags = set(re.findall(r"<\s*([a-zA-Z][a-zA-Z0-9]*)", out))
    check(f"about {name}: no script, iframe or event handler as markup",
          not tags & {"script", "iframe", "object", "embed", "style", "svg"} and not re.search(r"<[^>]*\son\w+\s*=", out), out[:300])
    check(f"about {name}: the README's own <div> is text", not re.search(r"<div onclick", out), out[:300])
    hrefs = re.findall(r'(?:href|src)="([^"]*)"', out)
    check(f"about {name}: no javascript:, vbscript: or data: (but images) address",
          not [h for h in hrefs if re.match(r"(?i)\s*(javascript|vbscript|data:(?!image/))", re.sub(r"[\x00-\x20]", "", h))], hrefs)
md = run("cgit-about.py", "README.md", "# Title\n\n[ok](https://example.com)\n")
if "<h1>" in md:
    check("about README.md: Markdown is rendered (Python-Markdown here)", "<h1>Title</h1>" in md and 'href="https://example.com"' in md, md)
else:
    check("about README.md: without Python-Markdown, shown as text", md.startswith('<pre class="readme-text">'), md)
md = run("cgit-about.py", "README.md", "<!-- SPDX" + "-License-Identifier: MIT -->\n# T\n")  # split: REUSE reads the whole line
check("about README.md: HTML comments are dropped, not shown", "SPDX" not in md, md)

inst = (REPO / "install.sh").read_text()
rc = inst[inst.index("css=/git-static/cgit.css"):]
rc = rc[:rc.index("\nEOF")]
check("cgitrc: no source-filter (code is highlighted in the browser: Pygments takes seconds a page on a small board)",
      "source-filter" not in rc)
for key in ("head-include=$CODE/config/cgit-head.html",
            "about-filter=$CODE/scripts/cgit-about.py", "readme=:README.md", "js=/git-static/cgit.js"):
    check(f"cgitrc: {key.split('=')[0]} set, before scan-path", key in rc and rc.index(key) < rc.index("scan-path="))
check("install.sh installs markdown and highlight.js, not pygments", "python3-markdown" in inst and "libjs-highlight.js" in inst
      and "python3-pygments" not in inst)
head = (REPO / "config" / "cgit-head.html").read_text()
check("cgit's head loads the palette, the hub's cgit look and themes.js",
      all(s in head for s in ('href="/palette.css"', 'href="/cgit-hub.css"', 'src="/themes.js"', 'name="viewport"',
                              'src="/git-hl/highlight.min.js" defer', 'src="/cgit-hub.js" defer')))
check("nginx serves /git-hl/ from Debian's highlight.js, nosniff", "alias /usr/share/javascript/highlight.js/;" in (REPO / "config" / "irate-box.nginx").read_text())
check("Caddy serves /git-hl/ likewise", "root * /usr/share/javascript/highlight.js" in (REPO / "config" / "Caddyfile").read_text())
nginx = (REPO / "config" / "irate-box.nginx").read_text()
caddy = (REPO / "config" / "Caddyfile").read_text()
for front, text in (("nginx", nginx), ("Caddy", caddy)):
    for area in ("public", "private"):
        i = text.index(f"cgitrc-{area}")
        block = text[max(text.rfind("location", 0, i), text.rfind("handle", 0, i)):text.find("\n\t}", i) + 3]
        if front == "Caddy":
            block = text[text.rfind("handle", 0, i):i]
        check(f"{front}: cgit ({area}) sends a CSP with script-src 'self' and object-src 'none', and nosniff",
              "script-src 'self'" in block and "object-src 'none'" in block and "nosniff" in block, block[-400:])
check("style.css takes its colours from palette.css", (REPO / "web" / "style.css").read_text().split("\n")[2].startswith('@import url("palette.css")'))

# --- cgit's colours in every theme -------------------------------------------------
# cgit-hub.css is loaded after cgit's own cgit.css; any cgit.css rule with a fixed colour it does
# not restate shows light-mode colours in a dark theme (white rows, black links). Every (selector, kind) below, from Debian's cgit.css, must be restated in
# cgit-hub.css with the same selector. Where cgit is installed, its own cgit.css is read instead,
# so a Debian update that adds rules fails here until they are covered.
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\b(white|black|red|green|blue|gray|grey|yellow|orange|navy|maroon|purple|silver|teal|olive|lime|aqua|fuchsia)\b")
PROPS = {"color": "color", "background": "background", "background-color": "background", "border": "border",
         "border-color": "border", "border-top": "border", "border-bottom": "border", "border-left": "border",
         "border-right": "border", "border-top-color": "border", "border-bottom-color": "border",
         "outline": "border", "box-shadow": "background"}
def rules(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sels = [" ".join(s.split()) for s in m.group(1).split(",")]
        for decl in m.group(2).split(";"):
            if ":" not in decl: continue
            p, v = (x.strip().lower() for x in decl.split(":", 1))
            if p in PROPS:
                yield sels, PROPS[p], v
def fixed(css):
    out = set()
    for sels, kind, v in rules(css):
        if COLOUR.search(v):
            for s in sels: out.add((s, kind))
    return out
def covered(css):
    out = set()
    for sels, kind, v in rules(css):
        for s in sels: out.add((s, kind))
    return out

# Debian's cgit 1.2.3+git20240802 (trixie), /usr/share/cgit/cgit.css, sha256 330f5d43…: kind, selector.
CGIT_FIXED = """
background div#cgit
background div#cgit a.branch-deco
background div#cgit a.deco
background div#cgit a.remote-deco
background div#cgit a.tag-annotated-deco
background div#cgit a.tag-deco
background div#cgit div.cgit-panel table
background div#cgit div.notes
background div#cgit div.path
background div#cgit table#downloads th
background div#cgit table.blame div.alt:nth-child(even)
background div#cgit table.blame div.alt:nth-child(odd)
background div#cgit table.diffstat
background div#cgit table.diffstat td.graph td.add
background div#cgit table.diffstat td.graph td.rem
background div#cgit table.hgraph div.bar
background div#cgit table.hgraph th
background div#cgit table.list tr
background div#cgit table.list tr.logheader
background div#cgit table.list tr.nohover
background div#cgit table.list tr.nohover-highlight:hover:nth-child(even)
background div#cgit table.list tr.nohover-highlight:hover:nth-child(odd)
background div#cgit table.list tr.nohover:hover
background div#cgit table.list tr:hover
background div#cgit table.list tr:nth-child(even)
background div#cgit table.list tr:nth-child(odd)
background div#cgit table.ssdiff span.add
background div#cgit table.ssdiff span.del
background div#cgit table.ssdiff td.add
background div#cgit table.ssdiff td.add_dark
background div#cgit table.ssdiff td.changed
background div#cgit table.ssdiff td.changed_dark
background div#cgit table.ssdiff td.del
background div#cgit table.ssdiff td.del_dark
background div#cgit table.ssdiff td.hunk
background div#cgit table.ssdiff td.lineno
background div#cgit table.stats th
background div#cgit table.tabs td a.active
background div#cgit table.vgraph div.bar
background div#cgit table.vgraph th
border div#cgit a.branch-deco
border div#cgit a.deco
border div#cgit a.remote-deco
border div#cgit a.tag-annotated-deco
border div#cgit a.tag-deco
border div#cgit div#blob
border div#cgit div.cgit-panel table
border div#cgit div.content
border div#cgit div.notes
border div#cgit table#downloads
border div#cgit table#header td.sub
border div#cgit table.bin-blob
border div#cgit table.bin-blob td
border div#cgit table.bin-blob th
border div#cgit table.blob
border div#cgit table.blob td.linenumbers
border div#cgit table.diffstat
border div#cgit table.hgraph
border div#cgit table.hgraph th
border div#cgit table.ssdiff td
border div#cgit table.ssdiff td.foot
border div#cgit table.ssdiff td.head
border div#cgit table.ssdiff td.hunk
border div#cgit table.stats
border div#cgit table.stats td
border div#cgit table.stats th
border div#cgit table.tabs
border div#cgit table.vgraph
border div#cgit table.vgraph th
color div#cgit
color div#cgit a
color div#cgit a.branch-deco
color div#cgit a.deco
color div#cgit a.remote-deco
color div#cgit a.tag-annotated-deco
color div#cgit a.tag-deco
color div#cgit div.diffstat-summary
color div#cgit div.error
color div#cgit div.footer
color div#cgit div.footer a
color div#cgit div.path
color div#cgit span.age-days
color div#cgit span.age-hours
color div#cgit span.age-mins
color div#cgit span.age-months
color div#cgit span.age-weeks
color div#cgit span.age-years
color div#cgit span.deletions
color div#cgit span.insertions
color div#cgit table#header td.main a
color div#cgit table#header td.sub
color div#cgit table.blob .com
color div#cgit table.blob .esc
color div#cgit table.blob .kwa
color div#cgit table.blob .kwb
color div#cgit table.blob .kwc
color div#cgit table.blob .kwd
color div#cgit table.blob .lin
color div#cgit table.blob .num
color div#cgit table.blob .opt
color div#cgit table.blob .ppc
color div#cgit table.blob .pps
color div#cgit table.blob .slc
color div#cgit table.blob .str
color div#cgit table.blob td.hashes
color div#cgit table.blob td.linenumbers a
color div#cgit table.blob td.linenumbers a:hover
color div#cgit table.blob td.lines
color div#cgit table.diff td div.add
color div#cgit table.diff td div.del
color div#cgit table.diff td div.head
color div#cgit table.diff td div.hunk
color div#cgit table.diffstat td span.modechange
color div#cgit table.diffstat td.add a
color div#cgit table.diffstat td.del a
color div#cgit table.diffstat td.upd a
color div#cgit table.list td a
color div#cgit table.list td a.ls-dir
color div#cgit table.list td a:hover
color div#cgit table.list td.commitgraph .column1
color div#cgit table.list td.commitgraph .column2
color div#cgit table.list td.commitgraph .column3
color div#cgit table.list td.commitgraph .column4
color div#cgit table.list td.commitgraph .column5
color div#cgit table.list td.commitgraph .column6
color div#cgit table.list td.reposection
color div#cgit table.ssdiff td.add
color div#cgit table.ssdiff td.add_dark
color div#cgit table.ssdiff td.changed
color div#cgit table.ssdiff td.changed_dark
color div#cgit table.ssdiff td.del
color div#cgit table.ssdiff td.del_dark
color div#cgit table.ssdiff td.head div.head
color div#cgit table.ssdiff td.hunk
color div#cgit table.ssdiff td.lineno
color div#cgit table.ssdiff td.lineno a
color div#cgit table.ssdiff td.lineno a:hover
color div#cgit table.stats td.sum
color div#cgit table.tabs td a
color div#cgit table.tabs td a.active
color div#cgit ul.pager a
"""
installed = Path("/usr/share/cgit/cgit.css")
want = fixed(installed.read_text()) if installed.exists() else {(l.split(" ", 1)[1], l.split(" ", 1)[0]) for l in CGIT_FIXED.strip().splitlines()}
have = covered((REPO / "web" / "cgit-hub.css").read_text())
missing = sorted(want - have)
check(f"cgit-hub.css restates every fixed colour in cgit.css ({len(want)}, from {'the installed cgit.css' if installed.exists() else 'the list above'})",
      not missing, "; ".join(f"{k} {s}" for s, k in missing[:20]))
hub = re.sub(r"/\*.*?\*/", "", (REPO / "web" / "cgit-hub.css").read_text(), flags=re.S)
check("cgit-hub.css's own colours come from the palette (no white, black or grey of its own)",
      not re.search(r"(?<![\w-])(white|black|#fff\b|#ffffff|#000\b|#eee|#ccc|#777)(?![\w-])", hub))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
