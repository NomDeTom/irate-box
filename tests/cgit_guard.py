# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""cgit's README and code views stay inert (next-work plan step 7): whatever a pushed README
holds, the filter puts out no markup it did not ask for in Markdown, and both web servers
send cgit's pages a CSP that allows no script but the box's own. Runs with or without
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
check("cgitrc: no source-filter (code is highlighted in the browser: Pygments took 2-5 s a page on the Lyra)",
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
        block = text[max(text.rfind("location", 0, i), text.rfind("handle", 0, i)):text.find("}", i) + 1]
        if front == "Caddy":
            block = text[text.rfind("handle", 0, i):i]
        check(f"{front}: cgit ({area}) sends a CSP with script-src 'self' and object-src 'none', and nosniff",
              "script-src 'self'" in block and "object-src 'none'" in block and "nosniff" in block, block[-400:])
check("style.css takes its colours from palette.css", (REPO / "web" / "style.css").read_text().split("\n")[2].startswith('@import url("palette.css")'))

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
