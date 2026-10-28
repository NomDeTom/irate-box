# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
r"""The App store's hub side (🛒): the page people open from their phone on the hotspot, the files they
download, and the machine-readable lists beside them.

What the keeper (library/holder.py) holds under $HUB_HOLDS_ROOT is listed here:

  /appstore/                      the page: one section per holder, the kept versions newest first, each file
                                  a plain link (no script needed to read it, so Obtainium's HTML source
                                  works too), sizes, digests behind a toggle
  /appstore/files/<id>/<tag>/<name>  one held file: only names in that version's sha256.json, confined to the
                                  folder, Range supported (phones resume), never a directory listing
  /appstore/latest/<id>/<name>    the same file of the newest kept version that has it: one address that
                                  always points at the newest, for a QR or a printed sheet
  /appstore/index.json            every holder and version, the shape the pages and scripts read
  /appstore/feed.xml              an Atom feed of the held files, newest first

Who sees what: the app's own switch (access.py: public, users, private, off) covers the whole route, and
inside it a holder marked "members" is listed and served only to a signed-in visitor. The web server's gate
and the checks here are both in force: a members-only file cannot be fetched by its address alone.

Content types come only from the table below, never from what the forge said. Stdlib only.
"""

import html
import json
import re
import time

from irate_box import confine
from irate_box.hub import refused
from irate_box.library import holder, holdsurvey, librarian
from irate_box.msg import M

TYPES = {
    ".apk": "application/vnd.android.package-archive",
    ".aab": "application/octet-stream",
    ".xapk": "application/zip",
    ".deb": "application/vnd.debian.binary-package",
    ".rpm": "application/x-rpm",
    ".exe": "application/x-msdownload",
    ".msi": "application/x-msdownload",
    ".dmg": "application/x-apple-diskimage",
    ".pkg": "application/octet-stream",
    ".appimage": "application/x-executable",
    ".jar": "application/java-archive",
    ".zip": "application/zip",
    ".gz": "application/gzip",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
}
HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9._-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::[0-9]{1,5})?$"
)  # flasher.py's: a port may come
NAME_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")  # a holder id or a tag as a folder name
MAX_FEED = 200


def _type_of(name):
    low = name.lower()
    for ending in sorted(TYPES, key=len, reverse=True):
        if low.endswith(ending):
            return TYPES[ending]
    return None


def _size(n):
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{max(1, n >> 10)} KB" if n else "-"


def _day(published):
    return str(published or "")[:10] or "-"


def _index(level):
    """The holders the visitor may see, each with its kept versions, as the page, index.json and feed read them."""
    out = []
    for cfg_holder in holder.settings()["holders"]:
        index = librarian._read_json(holder.ROOT / cfg_holder["id"] / "index.json", {})
        if not isinstance(index, dict) or index.get("id") != cfg_holder["id"]:
            if level == "admin":
                out.append(
                    {
                        "id": cfg_holder["id"],
                        "title": cfg_holder["title"] or cfg_holder["id"],
                        "repo": cfg_holder["repo"],
                        "visibility": cfg_holder["visibility"],
                        "empty": True,
                        "versions": [],
                    }
                )
            continue
        if cfg_holder["visibility"] == "members" and not level:
            continue
        out.append(
            {
                "id": index["id"],
                "title": index.get("title") or index["id"],
                "repo": index.get("repo", ""),
                "visibility": cfg_holder["visibility"],
                "updated": index.get("updated"),
                "empty": not index.get("versions"),
                "versions": [
                    {
                        "tag": version["tag"],
                        "channel": version.get("channel", ""),
                        "published": version.get("published"),
                        "notes": version.get("notes", ""),
                        "files": [
                            {
                                "name": file["name"],
                                "size": file["size"],
                                "sha256": file["sha256"],
                                "url": f"/appstore/files/{index['id']}/{version['tag']}/{file['name']}",
                                "latest_url": f"/appstore/latest/{index['id']}/{file['name']}",
                            }
                            for file in version.get("files", [])
                        ],
                    }
                    for version in index.get("versions", [])
                ],
            }
        )
    return out


def _chips(listings):
    """{label: [words]} to filter the files by, read from their names (the survey's tables)."""
    out = {}
    for entry in listings:
        for version in entry["versions"]:
            for file in version["files"]:
                for label, words in holdsurvey.tags_of(file["name"]).items():
                    out.setdefault(label, set()).update(words)
    return {label: sorted(words) for label, words in out.items()}


PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🛒 App store · Hub</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🛒</text></svg>">
  <link rel="stylesheet" href="/style.css">
  <script src="/themes.js"></script>
  <link rel="stylesheet" href="/layout.css">
</head>
"""


def page(host, scheme, level):
    """The /appstore/ page, whole: what is kept, newest first, as plain links and words.

    Every name on it came from a recorded file list or a title the administrator set, and is escaped; the
    release notes are shown as text in a <pre> and never as HTML. The words behind the JSON block and the
    rows' data-tags feed the filter buttons appstore.js adds: the page says everything again without them."""
    listings = _index(level)
    parts = [
        PAGE_HEAD,
        '<body class="appstore">',
        "<header>",
        '  <nav class="head-nav"><a class="head-btn labelled" href="/help.html"><span class="head-emoji" '
        'aria-hidden="true">🛟</span> Help</a><a class="head-btn labelled" href="/"><span class="head-emoji" '
        'aria-hidden="true">🏠</span> Hub</a></nav>',
        "<h1>🛒 App store</h1>",
        '  <p class="subtitle">App files kept on this box. Open one to install it.</p>',
        '  <div class="theme-picker" role="group" aria-label="Theme"></div>',
        "</header>",
    ]
    if not any(not entry["empty"] for entry in listings):
        parts.append(f"  <section><p>{html.escape(str(M('appstore.empty', 'Nothing is kept yet.')))}</p>")
        if level == "admin":
            parts.append('  <p><a href="/admin/#appstore">App store settings</a></p>')
        parts.append("  </section>")
    for entry in listings:
        label = html.escape(entry["title"])
        repo = html.escape(entry["repo"])
        parts.append(f'  <section data-holder="{html.escape(entry["id"])}">')
        parts.append(f"    <h2>{label}</h2>")
        if repo:
            parts.append(f'    <p class="setting-desc">From <code translate="no">{repo}</code></p>')
        if entry["empty"]:
            parts.append(
                f'    <p class="setting-desc">{html.escape(str(M("appstore.none-yet", "Nothing kept yet.")))}</p>'
            )
            parts.append("  </section>")
            continue
        for version in entry["versions"]:
            channel = (
                "Pre-release"
                if version["channel"] == "pre-release"
                else version["channel"].capitalize()
                if version["channel"] not in ("", "stable")
                else "Stable"
            )
            parts.append(
                f'    <h3><span translate="no">{html.escape(version["tag"])}</span>'
                f' <span class="appstore-badge">{channel}</span>'
                f' <span class="appstore-day">{_day(version["published"])}</span></h3>'
            )
            if version["notes"]:
                parts.append(
                    '    <details class="appstore-notes"><summary>Release notes</summary>'
                    f"<pre>{html.escape(version['notes'])}</pre></details>"
                )
            parts.append('    <ul class="appstore-files">')
            for file in version["files"]:
                tags = html.escape(" ".join(sorted(w for ws in holdsurvey.tags_of(file["name"]).values() for w in ws)))
                parts.append(
                    f'      <li data-tags="{tags}">'
                    f'<a href="{html.escape(file["url"])}"><code translate="no">{html.escape(file["name"])}</code></a>'
                    f' <span class="appstore-size">{_size(file["size"])}</span>'
                    f' <details class="appstore-digest"><summary>Details</summary>'
                    f'<p><code translate="no">sha-256: {html.escape(file["sha256"])}</code></p></details></li>'
                )
            parts.append("    </ul>")
        parts.append("  </section>")
    parts.append(
        '  <p class="setting-desc">📱 A phone needs permission to install an app from the browser you '
        "downloaded it with. Turn that on once, for this browser. A plain (unencrypted) address can bring a "
        'warning. <a href="/certificate">This box\'s certificate</a> lets the phone trust it, and the HTTPS '
        "address of this page downloads without a warning.</p>"
    )
    address = html.escape(f"{scheme}://{host}/appstore/", quote=True)
    parts.append(
        f'  <section><p>Scan to open this page:</p><span class="qr" id="appstore-qr" data-url="{address}" '
        'role="img" aria-label="QR code for the App store"></span></section>'
    )
    chips = json.dumps(_chips(listings)).replace("<", "\\u003c")
    parts.append(f'  <script type="application/json" id="appstore-chips">{chips}</script>')
    parts.append('  <script src="/i18n.js"></script>')
    parts.append('  <script src="/hub.js"></script>')
    parts.append('  <script src="/qrcode.js"></script>')
    parts.append('  <script src="/appstore.js"></script>')
    parts.append("</body></html>")
    return "\n".join(parts).encode()


def index_json(level):
    return {
        "app": "appstore",
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "holders": _index(level),
    }


def feed_xml(level):
    """The held files as Atom, newest first: titles, sizes, types, links. No script, no HTML."""
    entries = [
        (version["published"] or "", entry["title"], file)
        for entry in _index(level)
        for version in entry["versions"]
        for file in version["files"]
    ]
    entries.sort(key=lambda item: item[0], reverse=True)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        "<title>App store</title>",
        f"<updated>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}</updated>",
        "<id>tag:appstore-hub</id>",
        f'<link href="/appstore/feed.xml" rel="self"/>',
    ]
    for published, title, file in entries[:MAX_FEED]:
        kind = _type_of(file["name"]) or "application/octet-stream"
        heading = html.escape(f"{title} {file['name']}")
        out += [
            "  <entry>",
            f"    <title>{heading}</title>",
            f'    <link rel="enclosure" type="{kind}" length="{file["size"]}" '
            f'href="{html.escape(file["url"], quote=True)}"/>',
            f"    <id>urn:sha256:{html.escape(file['sha256'])}</id>",
            f"    <updated>{html.escape(published)}</updated>",
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out).encode()


# --- the routes ----------------------------------------------------------------------------


def _not_found(handler):
    handler.send_json(404, refused.say(M("hub.not-found", "Not found.")))


def route(handler, path, mode):
    """GET/HEAD under /appstore. The app's switch covers every address here; a holder marked "members"
    needs a signed-in visitor as well, page and file alike."""
    if (
        mode == "off"
        or (mode == "private" and handler._viewer() != "admin")
        or (mode == "users" and not handler._viewer())
    ):
        return _not_found(handler)
    level = handler._viewer()
    if path in ("/appstore", "/appstore/"):
        # The Host as it came, port and all: a QR of this page must answer on the address it was loaded from.
        host = handler.headers.get("Host", "")
        if not HOST_RE.match(host):
            host = "box.local"
        body = page(host, "https" if handler._https() else "http", level)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
        return
    if path == "/appstore/index.json":
        body = json.dumps(index_json(level)).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
        return
    if path == "/appstore/feed.xml":
        body = feed_xml(level)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/atom+xml; charset=utf-8")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
        return
    if path.startswith("/appstore/latest/"):
        return _latest(handler, path[len("/appstore/latest/") :].strip("/").split("/"), level)
    if path.startswith("/appstore/files/"):
        return _file(handler, path[len("/appstore/files/") :].strip("/").split("/"), level)
    return _not_found(handler)


def _index_of(holder_id):
    """The holder's published index, or None (a folder of its own is not proof the id is a holder)."""
    for entry in _index("admin"):
        if entry["id"] == holder_id:
            return entry
    return None


def _latest(handler, parts, level):
    """The newest kept version of a holder that has this file, as a 302 to its versioned address."""
    if len(parts) != 2 or not all(NAME_PART_RE.match(part) for part in parts):
        return _not_found(handler)
    holder_id, name = parts
    entry = _index_of(holder_id)
    if entry is None or (entry["visibility"] == "members" and not level):
        return _not_found(handler)
    for version in entry["versions"]:
        if any(file["name"] == name for file in version["files"]):
            handler.send_response(302)
            handler.send_header("Location", f"/appstore/files/{holder_id}/{version['tag']}/{name}")
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
    return _not_found(handler)


def _file(handler, parts, level):
    """One held file, streamed, with Range. Only a name the keeper recorded for that version, in that
    version's folder; the id and tag must be the holder's own, as published."""
    if len(parts) != 3 or not all(NAME_PART_RE.match(part) for part in parts[:2]) or not holder.FILE_RE.match(parts[2]):
        return _not_found(handler)
    holder_id, tag, name = parts
    entry = _index_of(holder_id)
    if entry is None or (entry["visibility"] == "members" and not level):
        return _not_found(handler)
    if not any(version["tag"] == tag for version in entry["versions"]):
        return _not_found(handler)
    recorded = librarian._read_json(holder.ROOT / holder_id / tag / holder.DIGESTS, {})
    note = recorded.get(name)
    if not (isinstance(note, list) and len(note) == 3):
        return _not_found(handler)
    kind = _type_of(name)
    if kind is None:
        return _not_found(handler)
    try:
        path = confine.under(holder.ROOT / holder_id / tag, name)
    except ValueError:
        return _not_found(handler)
    if not path.is_file() or path.is_symlink():
        return _not_found(handler)
    size = path.stat().st_size
    start, end, code = 0, size - 1, 200
    unit, _, spec = handler.headers.get("Range", "").partition("=")
    if spec and unit.strip().lower() == "bytes" and "," not in spec:
        first, _, last = spec.partition("-")
        try:
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            elif last:
                start = max(0, size - int(last))
            if start > end or end >= size:
                raise ValueError
        except ValueError:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        code = 206
    length = end - start + 1
    handler.send_response(code)
    handler.send_header("Content-Type", kind)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Disposition", f'attachment; filename="{name}"')
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("ETag", f'"{note[2]}"')
    handler.send_header("Cache-Control", "public, max-age=86400")
    if code == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Content-Length", str(length))
    handler.end_headers()
    if handler.command == "HEAD" or length <= 0:
        return
    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                left -= len(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        handler.close_connection = True
