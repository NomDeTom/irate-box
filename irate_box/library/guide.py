# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The user guide: docs/guide/*.md, the source, rendered to the box's pages (web/guide/*.html, kept in the
repository beside their source: tests/guide_guard.py checks they match) and packed as a ZIM for Kiwix's library,
where Kiwix is chosen (install.sh). The Markdown is the plain kind the guide uses: headings, paragraphs, lists
(one level of nesting), fenced code, tables, `code`, **bold**, *emphasis* and [links](page.md), a link to
another page of the guide becoming its .html.

    ./irate-box guide build            web/guide/*.html from docs/guide/*.md
    ./irate-box guide zim OUT.zim      the guide as one ZIM
Stdlib only."""

import hashlib
import html
import re
import struct
import sys
import time
import uuid as uuidlib
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
SOURCE = CHECKOUT / "docs" / "guide"
WEB = CHECKOUT / "web" / "guide"
ZIM_NAME = "irate-box-guide"
ZIM_FILE = f"{ZIM_NAME}.zim"

# --- Markdown, the guide's kind ------------------------------------------------------------------

def _inline(text):
    out, pos = [], 0
    for m in re.finditer(r"`([^`]+)`|\*\*(.+?)\*\*|\*(.+?)\*|\[([^\]]+)\]\(([^)\s]+)\)", text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        code, bold, em, label, href = m.groups()
        if code is not None:
            out.append(f"<code>{html.escape(code, quote=False)}</code>")
        elif bold is not None:
            out.append(f"<strong>{_inline(bold)}</strong>")
        elif em is not None:
            out.append(f"<em>{_inline(em)}</em>")
        else:
            if not re.match(r"^[a-z]+:|^/|^#", href):
                href = re.sub(r"\.md(#|$)", r".html\1", href)
            out.append(f'<a href="{html.escape(href)}">{_inline(label)}</a>')
        pos = m.end()
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def _list(lines):
    """Lines of one list (items start with "- " or "1. "; deeper lines indented): its HTML."""
    ordered = bool(re.match(r"^\d+\. ", lines[0]))
    items = []
    for l in lines:
        if re.match(r"^(- |\d+\. )", l):
            items.append([re.sub(r"^(- |\d+\. )", "", l)])
        else:
            items[-1].append(l)
    tag = "ol" if ordered else "ul"
    parts = []
    for it in items:
        first, rest = it[0], [l[2:] if l.startswith("  ") else l.lstrip() for l in it[1:]]
        nested = [l for l in rest if re.match(r"^\s*(- |\d+\. )", l)]
        text = " ".join([first] + [l.strip() for l in rest if l not in nested])
        sub = _list([l.strip() if re.match(r"^\s*(- |\d+\. )", l) else l for l in nested]) if nested else ""
        parts.append(f"<li>{_inline(text)}{sub}</li>")
    return f"<{tag}>" + "".join(parts) + f"</{tag}>"


def _table(lines):
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines if not re.match(r"^\s*\|?\s*:?-{3,}", l)]
    head, body = rows[0], rows[1:]
    return ("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")


def render(md):
    """(title, subtitle, body HTML) from a page's Markdown: the first heading the title, an emphasised first
    paragraph the subtitle."""
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    lines = md.split("\n")
    title, subtitle, out, i = "", "", [], 0
    while i < len(lines):
        l = lines[i]
        if not l.strip():
            i += 1
            continue
        if l.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append('<pre class="help-code"><code>' + html.escape("\n".join(lines[i + 1:j]), quote=False) + "</code></pre>")
            i = j + 1
            continue
        h = re.match(r"^(#{1,3}) (.*)$", l)
        if h:
            if len(h.group(1)) == 1 and not title:
                title = h.group(2).strip()
            else:
                n = len(h.group(1))
                out.append(f"<h{n}>{_inline(h.group(2).strip())}</h{n}>")
            i += 1
            continue
        j = i
        if re.match(r"^(- |\d+\. )", l):
            while j < len(lines) and lines[j].strip() and (re.match(r"^(- |\d+\. )", lines[j]) or lines[j].startswith(" ")):
                j += 1
            out.append(_list(lines[i:j]))
            i = j
            continue
        if l.lstrip().startswith("|"):
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.append(_table(lines[i:j]))
            i = j
            continue
        while j < len(lines) and lines[j].strip() and not re.match(r"^(#{1,3} |```|- |\d+\. |\s*\|)", lines[j]):
            j += 1
        text = " ".join(x.strip() for x in lines[i:j])
        m = re.fullmatch(r"\*([^*].*)\*", text)
        if m and title and not subtitle and not out:
            subtitle = m.group(1)
        else:
            out.append(f'<p class="help-note">{_inline(text)}</p>')
        i = j
    return title, subtitle, "\n  ".join(out)


# --- the box's pages -----------------------------------------------------------------------------

# REUSE-IgnoreStart
PAGE = """<!DOCTYPE html>
<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
<!-- Made from docs/guide/{src} by irate_box/library/guide.py (./irate-box guide build): edit that, not this. -->
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title_text} · Guide · Hub</title>
  <link rel="stylesheet" href="/style.css">
  <script src="/themes.js"></script>
  <link rel="stylesheet" href="/layout.css">
</head>
<body class="help guide">
  <header>
    <nav class="head-nav"><a class="head-btn labelled" href="/help.html" title="Quick help"><span class="head-emoji" aria-hidden="true">🛟</span> Help</a><a class="head-btn labelled" href="/" title="Back to the hub"><span class="head-emoji" aria-hidden="true">🏠</span> Hub</a></nav>
    <h1>📖 {title}</h1>
    <p class="subtitle">{subtitle}</p>
    <div class="theme-picker" role="group" aria-label="Theme"></div>
  </header>

  {body}

  <p class="help-note">{nav}</p>
  <script src="/hub.js"></script>
</body>
</html>
"""
# REUSE-IgnoreEnd


def pages():
    """The guide's pages, in the index's order: [(stem, markdown)], index first."""
    src = {p.stem: p.read_text() for p in sorted(SOURCE.glob("*.md"))}
    order = ["index"] + [s for s in re.findall(r"\]\(([a-z0-9-]+)\.md\)", src.get("index", "")) if s in src and s != "index"]
    return [(s, src[s]) for s in order + [s for s in src if s not in order]]


def page_html(stem, md):
    title, subtitle, body = render(md)
    nav = ('<a href="/help.html">Quick help</a>' if stem == "index"
           else '<a href="/guide/index.html">The guide\'s other pages</a> · <a href="/help.html">Quick help</a>')
    return PAGE.format(src=f"{stem}.md", title_text=html.escape(title), title=_inline(title), subtitle=_inline(subtitle),
                       body=body, nav=nav)


def build(out=WEB):
    out.mkdir(parents=True, exist_ok=True)
    made = []
    for stem, md in pages():
        (out / f"{stem}.html").write_text(page_html(stem, md))
        made.append(f"{stem}.html")
    return made


# --- the ZIM -------------------------------------------------------------------------------------

ZIM_STYLE = ("body{font-family:system-ui,sans-serif;max-width:46rem;margin:1.5rem auto;padding:0 1rem;line-height:1.55;color:#222}"
             "h1{color:#5b3fd6}pre{background:#f4f4f6;border:1px solid #ddd;border-radius:6px;padding:.6rem .8rem;overflow-x:auto}"
             "code{font-size:.9em}table{border-collapse:collapse}td,th{border-bottom:1px solid #ddd;padding:.2rem .6rem;text-align:left}"
             ".subtitle{color:#666}@media (prefers-color-scheme:dark){body{background:#16161a;color:#ddd}pre{background:#222;border-color:#333}}")


def zim_page(stem, md):
    title, subtitle, body = render(md)
    body = body.replace('href="/guide/', 'href="').replace(' class="help-note"', "").replace(' class="help-code"', "")
    nav = "" if stem == "index" else '<p><a href="index.html">The guide\'s other pages</a></p>'
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">'
            f"<title>{html.escape(title)}</title><style>{ZIM_STYLE}</style></head><body><h1>{_inline(title)}</h1>"
            f'<p class="subtitle">{_inline(subtitle)}</p>{body}{nav}</body></html>')


def write_zim(path, articles, meta, main="index.html", uid=None):
    """A ZIM (major version 5, the A and M namespaces): articles [(url, title, html)], meta {key: text}; one
    uncompressed cluster, its MD5 at the end. Small books only."""
    mimes = ["text/html", "text/plain"]
    entries = [("A", url, title, 0, body.encode()) for url, title, body in articles]
    entries += [("M", k, "", 1, v.encode()) for k, v in sorted(meta.items())]
    entries.sort(key=lambda e: (e[0], e[1]))
    blobs = [e[4] for e in entries]
    offs, pos = [], 4 * (len(blobs) + 1)
    for b in blobs:
        offs.append(pos)
        pos += len(b)
    offs.append(pos)
    cluster = bytes([1]) + b"".join(struct.pack("<I", o) for o in offs) + b"".join(blobs)
    mime_list = b"".join(m.encode() + b"\0" for m in mimes) + b"\0"
    dirents = [struct.pack("<HBcIII", mime, 0, ns.encode(), 0, 0, i) + url.encode() + b"\0" + t.encode() + b"\0"
               for i, (ns, url, t, mime, _) in enumerate(entries)]
    n = len(entries)
    mime_pos = 80
    url_ptr_pos = mime_pos + len(mime_list)
    title_ptr_pos = url_ptr_pos + 8 * n
    cluster_ptr_pos = title_ptr_pos + 4 * n
    dirent_pos = cluster_ptr_pos + 8
    dpos, url_ptrs = dirent_pos, []
    for d in dirents:
        url_ptrs.append(dpos)
        dpos += len(d)
    cluster_pos = dpos
    checksum_pos = cluster_pos + len(cluster)
    titles = sorted(range(n), key=lambda i: (entries[i][0], entries[i][2] or entries[i][1]))
    main_i = next(i for i, e in enumerate(entries) if e[0] == "A" and e[1] == main)
    uid = uid or uuidlib.uuid4().bytes
    header = struct.pack("<IHH16sIIQQQQIIQ", 72173914, 5, 0, uid, n, 1, url_ptr_pos, title_ptr_pos, cluster_ptr_pos,
                         mime_pos, main_i, 0xFFFFFFFF, checksum_pos)
    data = (header + mime_list + b"".join(struct.pack("<Q", p) for p in url_ptrs) + b"".join(struct.pack("<I", i) for i in titles)
            + struct.pack("<Q", cluster_pos) + b"".join(dirents) + cluster)
    Path(path).write_bytes(data + hashlib.md5(data).digest())


def zim(out, version=""):
    """The guide as one ZIM at `out`; its UUID from the pages' content, so the same guide is the same book."""
    arts = [(f"{stem}.html", render(md)[0], zim_page(stem, md)) for stem, md in pages()]
    digest = hashlib.sha256("".join(a[2] for a in arts).encode()).digest()
    meta = {"Title": "Irate-Box guide", "Name": ZIM_NAME, "Language": "eng", "Creator": "Irate-Box", "Publisher": "Irate-Box",
            "Description": "How to set up and look after this box" + (f" ({version})" if version else ""),
            "Date": time.strftime("%Y-%m-%d"), "Tags": "_category:other;guide"}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(out) + ".part")
    write_zim(tmp, arts, meta, uid=digest[:16])
    tmp.replace(out)
    return out


def main(argv):
    if argv == ["build"]:
        print("\n".join(build()))
        return 0
    if len(argv) in (2, 3) and argv[0] == "zim":
        print(zim(Path(argv[1]), argv[2] if len(argv) == 3 else ""))
        return 0
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
