# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""No dead links or stylesheets: every local href and src in the pages under web/ (links, scripts, stylesheets,
pictures), and every url() and @import in web/'s stylesheets, names a file in web/, one of the hub's own routes, or
a path under an app's mount; and every page has its tab icon. A dead one shows only in a browser's developer tools (a stylesheet that sets variables
has fallbacks, so the page still looks right), so this reads the files instead.
Each route below is checked against the code that serves it, so the list cannot go stale either.
python3 tests/link_guard.py"""

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


# The hub's own routes: exact paths, each written in full, in quotes, in server.py.
HUB = REPO / "irate_box" / "hub" / "server.py"
HUB_ROUTES = {
    "/admin/",
    "/admin/backup",
    "/admin/factory.html",
    "/admin/mesh.html",
    "/admin/notes/download",
    "/admin/setup",
    "/api/notebook/download",
    "/certificate",
    "/certificate/ca.crt",
    "/layout.css",
    "/source",
}
# Apps' mounts: anything under them is the app's own (installed with it, not in web/). Each is a location in the
# nginx config, or the module named.
NGINX = REPO / "config" / "irate-box.nginx"
MOUNTS = {
    "/draw/": NGINX,
    "/flasher/": NGINX,
    "/mermaid/": NGINX,
    "/serial/": NGINX,
    "/sync/": NGINX,
    "/wiki/": NGINX,
    "/git/": REPO / "irate_box" / "hub" / "gitrepos.py",
    "/git-private/": REPO / "irate_box" / "hub" / "gitrepos.py",
}

hub_src = HUB.read_text(encoding="utf-8")
for r in sorted(HUB_ROUTES):
    check(
        f"route {r}: served by the hub",
        f'"{r}"' in hub_src,
        f"not in {HUB.relative_to(REPO)}",
    )
for m, src in sorted(MOUNTS.items()):
    text = src.read_text(encoding="utf-8")
    seen = (
        re.search(rf"location\s+(?:\^~\s+|=\s+)?{re.escape(m)}", text)
        if src == NGINX
        else f'"{m}"' in text
    )
    check(f"mount {m}: served ({src.relative_to(REPO)})", bool(seen))


def alive(url, base):
    """The local file or route a link names, or None: not ours to check (another site, a page anchor, a template)."""
    url = url.split("#")[0].split("?")[0]
    if not url or re.match(r"^([a-z][a-z0-9+.-]*:|//|\$\{|\{\{)", url):
        return None
    if url.startswith("/"):
        if url in HUB_ROUTES or any(url.startswith(m) for m in MOUNTS):
            return True
        target = WEB / url.lstrip("/")
    else:
        target = base / url
    if url.endswith("/"):
        target = target / "index.html"
    return target.is_file()


pages = sorted(WEB.rglob("*.html"))
sheets = sorted(WEB.rglob("*.css"))
links = 0
for p in pages:
    for m in re.finditer(
        r"""\b(?:href|src)\s*=\s*["']([^"']*)["']""",
        p.read_text(encoding="utf-8", errors="replace"),
    ):
        ok = alive(m.group(1), p.parent)
        if ok is None:
            continue
        links += 1
        if not ok:
            check(
                f"{p.relative_to(REPO)}: {m.group(1)}",
                False,
                "no such file in web/, and no route or mount",
            )
for s in sheets:
    for m in re.finditer(
        r"""(?:@import\s+(?:url\(\s*)?|url\(\s*)["']?([^"')\s;]+)""",
        s.read_text(encoding="utf-8", errors="replace"),
    ):
        ok = alive(m.group(1), s.parent)
        if ok is None:
            continue
        links += 1
        if not ok:
            check(
                f"{s.relative_to(REPO)}: url({m.group(1)})",
                False,
                "no such file in web/",
            )
# Every page has its tab icon (its emoji, in the page): one, or the browser shows a blank one. A page that only
# sends people on (a meta refresh) needs none.
for p in pages:
    text = p.read_text(encoding="utf-8", errors="replace")
    if 'http-equiv="refresh"' in text:
        continue
    n = len(re.findall(r'<link\s+rel="icon"\s+href="data:image/svg\+xml,', text))
    if n != 1:
        check(f"{p.relative_to(REPO)}: one tab icon", False, f"{n} found")
check(
    f"every local link and stylesheet reference alive ({len(pages)} pages, {len(sheets)} stylesheets, {links} references)",
    links > 0 and not fails,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
