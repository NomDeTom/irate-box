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
import urllib.parse

from irate_box import confine
from irate_box.hub import refused
from irate_box.library import firmware, holder, holdsurvey, librarian
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
FIRMWARE_ID = "meshtastic-firmware"
FIRMWARE_ROLES = {
    "full": M("appstore.firmware.role.full", "Full flash"),
    "ota": M("appstore.firmware.role.ota", "Update (OTA)"),
    "uf2": M("appstore.firmware.role.uf2", "UF2"),
    "dfu": M("appstore.firmware.role.dfu", "DFU"),
    "filesystem": M("appstore.firmware.role.filesystem", "File system"),
}


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


def _firmware_role(name, entry):
    """The role a kept firmware file plays, from its name and the manifest's part_name (the manifests carry no
    role field). None when the table does not know it: the name is shown without a role."""
    low = name.lower()
    part = str(entry.get("part_name") or "")
    if low.endswith(".elf"):
        return None
    if low.startswith("littlefs-") or part == "spiffs":
        return "filesystem"
    if low.endswith("-ota.zip") or low.endswith("-ota.bin") or part in ("app0", "app1"):
        return "ota"
    if low.endswith(".factory.bin"):
        return "full"
    if low.endswith(".uf2"):
        return "uf2"
    if low.endswith(".hex"):
        return "dfu"
    if low.endswith(".bin"):
        return "full"
    return None


def _firmware_entry(visible):
    """The firmware mirror as a read-only App store entry, from what firmware.py wrote. None when the
    downloads are off, nothing with boards is kept, or the flasher's access keeps this visitor out."""
    if not visible:
        return None
    try:
        if not firmware.settings().get("enabled"):
            return None
    except (OSError, ValueError):
        return None
    state = firmware.status()
    versions = state.get("versions") if isinstance(state.get("versions"), dict) else {}
    notes = {}
    try:
        listed = json.loads((firmware.ROOT / "index.json").read_text())
    except (OSError, ValueError):
        listed = {}
    for channel in ("stable", "alpha"):
        for item in (listed.get("releases") or {}).get(channel, []) if isinstance(listed, dict) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                notes[item["id"]] = item
    out = []
    for version, info in versions.items():
        if not isinstance(info, dict) or not info.get("boards") or not firmware.VERSION_RE.match(str(version)):
            continue
        vdir = firmware.ROOT / version
        boards = []
        flat = []
        digests = librarian._read_json(vdir / firmware.DIGESTS, {})
        build_epoch = None
        for board in info["boards"]:
            if not isinstance(board, str) or not firmware.BOARD_RE.match(board):
                continue
            mt = librarian._read_json(vdir / f"firmware-{board}-{version}.mt.json", {})
            if not isinstance(mt, dict) or not mt:
                continue
            architecture = str(mt.get("architecture") or "")
            if isinstance(mt.get("build_epoch"), int):
                build_epoch = max(build_epoch or 0, mt["build_epoch"])
            role_files = []
            for entry in mt.get("files", []) if isinstance(mt.get("files"), list) else []:
                name = entry.get("name") if isinstance(entry, dict) else None
                if not isinstance(name, str) or not firmware.FILE_RE.match(name) or name.endswith(".elf"):
                    continue
                path = vdir / name
                if not path.is_file() or path.is_symlink():
                    continue
                recorded = digests.get(name) if isinstance(digests, dict) else None
                sha = recorded[2] if isinstance(recorded, list) and len(recorded) == 3 else None
                role = _firmware_role(name, entry)
                template = f"/appstore/latest/{FIRMWARE_ID}/{board}/{role}" if role else None
                file = {
                    "name": name,
                    "size": int(entry.get("bytes") or path.stat().st_size),
                    "sha256": sha,
                    "md5": entry.get("md5"),
                    "url": f"/flasher/firmware/{version}/{name}",
                    "latest_url": template,
                    "board": board,
                    "architecture": architecture,
                    "role": role,
                }
                role_files.append(file)
                flat.append(file)
            if role_files:
                boards.append(
                    {
                        "board": board,
                        "name": str(mt.get("displayName") or board),
                        "architecture": architecture,
                        "files": role_files,
                    }
                )
        if not boards:
            continue
        if build_epoch:
            published = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(build_epoch))
        else:
            published = info.get("fetched") or ""
        note = notes.get(f"v{version}") or notes.get(version) or {}
        out.append(
            {
                "tag": version,
                "channel": info.get("channel", ""),
                "published": published,
                "notes": note.get("release_notes", ""),
                "boards": boards,
                "files": flat,
            }
        )
    if not out:
        return None
    out.sort(key=lambda item: item["published"] or "", reverse=True)
    return {
        "id": FIRMWARE_ID,
        "title": str(M("appstore.firmware.title", "Meshtastic firmware")),
        "repo": "meshtastic/firmware",
        "visibility": "public",
        "updated": state.get("last_check"),
        "empty": False,
        "versions": out,
    }


def _index(level, firmware_visible=False):
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
                                "of": file.get("of"),
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
    if firmware_visible:
        entry = _firmware_entry(True)
        if entry:
            out.append(entry)
    return out


def _chips(listings):
    """{label: [words]} to filter the files by, read from their names (the survey's tables)."""
    out = {}
    for entry in listings:
        for version in entry["versions"]:
            for file in version["files"]:
                for label, words in holdsurvey.tags_of(file["name"]).items():
                    out.setdefault(label, set()).update(words)
                if file.get("architecture"):
                    out.setdefault("architecture", set()).add(file["architecture"])
    return {label: sorted(words) for label, words in out.items()}


PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🛒</text></svg>">
  <link rel="stylesheet" href="/style.css">
  <script src="/themes.js"></script>
  <link rel="stylesheet" href="/layout.css">
</head>
"""


def _channel(name):
    if name == "pre-release":
        return M("appstore.channel.pre-release", "Pre-release")
    if name in ("", "stable"):
        return M("appstore.channel.stable", "Stable")
    return str(name).capitalize()


def _esc(message):
    return html.escape(str(message))


def page(host, scheme, level, firmware_visible=False):
    """The /appstore/ page, whole: what is kept, newest first, as plain links and words.

    Every name on it came from a recorded file list or a title the administrator set, and is escaped; the
    release notes are shown as text in a <pre> and never as HTML. The words behind the JSON block and the
    rows' data-tags feed the filter buttons appstore.js adds: the page says everything again without them."""
    listings = _index(level, firmware_visible)
    parts = [
        PAGE_HEAD.format(title=_esc(M("appstore.page.title", "🛒 App store · Hub"))),
        '<body class="appstore">',
        "<header>",
        f'  <nav class="head-nav"><a class="head-btn labelled" href="/help.html"><span class="head-emoji" '
        f'aria-hidden="true">🛟</span> {_esc(M("appstore.help", "Help"))}</a><a class="head-btn labelled" href="/">'
        f'<span class="head-emoji" aria-hidden="true">🏠</span> {_esc(M("appstore.hub", "Hub"))}</a></nav>',
        f"<h1>{_esc(M('appstore.heading', '🛒 App store'))}</h1>",
        f'  <p class="subtitle">{_esc(M("appstore.subtitle", "App files kept on this box. Open one to install it."))}</p>',
        f'  <div class="theme-picker" role="group" aria-label="{_esc(M("appstore.theme", "Theme"))}"></div>',
        "</header>",
    ]
    if not any(not entry["empty"] for entry in listings):
        parts.append(f"  <section><p>{html.escape(str(M('appstore.empty', 'Nothing is kept yet.')))}</p>")
        if level == "admin":
            parts.append(
                f'  <p><a href="/admin/#appstore">{_esc(M("appstore.admin-link", "App store settings"))}</a></p>'
            )
        parts.append("  </section>")
    for entry in listings:
        label = html.escape(entry["title"])
        repo = entry["repo"]
        parts.append(f'  <section data-holder="{html.escape(entry["id"])}">')
        parts.append(f"    <h2>{label}</h2>")
        if repo:
            said = html.escape(str(M("appstore.from", "From {repo}", repo=repo))).replace(
                html.escape(repo), f'<code translate="no">{html.escape(repo)}</code>'
            )
            parts.append(f'    <p class="setting-desc">{said}</p>')
        if entry["empty"]:
            parts.append(
                f'    <p class="setting-desc">{html.escape(str(M("appstore.none-yet", "Nothing kept yet.")))}</p>'
            )
            parts.append("  </section>")
            continue
        if entry["id"] == FIRMWARE_ID:
            parts.append(
                f'    <p class="setting-desc">{_esc(M("appstore.firmware.where", "These are the files the Firmware page keeps."))}</p>'
            )
            for version in entry["versions"]:
                channel = html.escape(str(_channel(version["channel"])))
                parts.append(
                    f'    <h3><span translate="no">{html.escape(version["tag"])}</span>'
                    f' <span class="appstore-badge">{channel}</span>'
                    f' <span class="appstore-day">{_day(version["published"])}</span></h3>'
                )
                if version["notes"]:
                    parts.append(
                        f'    <details class="appstore-notes"><summary>'
                        f"{_esc(M('appstore.release-notes', 'Release notes'))}</summary>"
                        f"<pre>{html.escape(version['notes'])}</pre></details>"
                    )
                parts.append('    <div class="appstore-boards">')
                for board in version["boards"]:
                    board_id = html.escape(board["board"], quote=True)
                    arch = html.escape(board["architecture"], quote=True)
                    count = len(board["files"])
                    name = html.escape(board["name"])
                    parts.append(
                        f'      <details class="appstore-board" data-board="{board_id}" data-arch="{arch}">'
                        f'        <summary><code translate="no">{board_id}</code>'
                        f' <span class="setting-desc">{name} · {html.escape(board["architecture"])} · '
                        f"{_esc(M('appstore.firmware.file-count', {'one': '{n} file', 'other': '{n} files'}, n=count))}"
                        "</span></summary>"
                    )
                    parts.append('        <ul class="appstore-files">')
                    for file in board["files"]:
                        role = FIRMWARE_ROLES.get(file.get("role"))
                        role_html = f' <span class="appstore-role">{_esc(role)}</span>' if role else ""
                        digest = (
                            _esc(M("appstore.sha256", "sha-256: {digest}", digest=file["sha256"]))
                            if file.get("sha256")
                            else ""
                        )
                        md5 = _esc(M("appstore.md5", "md5: {digest}", digest=file["md5"])) if file.get("md5") else ""
                        details = "".join(f'<p><code translate="no">{d}</code></p>' for d in (digest, md5) if d)
                        parts.append(
                            f'          <li><a href="{html.escape(file["url"])}">'
                            f'<code translate="no">{html.escape(file["name"])}</code></a>{role_html}'
                            f' <span class="appstore-size">{_size(file["size"])}</span>'
                            f' <details class="appstore-digest"><summary>'
                            f"{_esc(M('appstore.details', 'Details'))}</summary>{details}</details></li>"
                        )
                    parts.append("        </ul>")
                    parts.append("      </details>")
                parts.append("    </div>")
            parts.append("  </section>")
            continue
        for version in entry["versions"]:
            channel = html.escape(str(_channel(version["channel"])))
            parts.append(
                f'    <h3><span translate="no">{html.escape(version["tag"])}</span>'
                f' <span class="appstore-badge">{channel}</span>'
                f' <span class="appstore-day">{_day(version["published"])}</span></h3>'
            )
            if version["notes"]:
                parts.append(
                    f'    <details class="appstore-notes"><summary>{_esc(M("appstore.release-notes", "Release notes"))}'
                    f"</summary><pre>{html.escape(version['notes'])}</pre></details>"
                )

            def item(file, nested=""):
                tags = html.escape(" ".join(sorted(w for ws in holdsurvey.tags_of(file["name"]).values() for w in ws)))
                return (
                    f'      <li data-tags="{tags}">'
                    f'<a href="{html.escape(file["url"])}"><code translate="no">{html.escape(file["name"])}</code></a>'
                    f' <span class="appstore-size">{_size(file["size"])}</span>'
                    f' <details class="appstore-digest"><summary>{_esc(M("appstore.details", "Details"))}</summary>'
                    f'<p><code translate="no">{_esc(M("appstore.sha256", "sha-256: {digest}", digest=file["sha256"]))}'
                    f"</code></p></details>{nested}</li>"
                )

            companions = {}
            for file in version["files"]:
                if file.get("of"):
                    companions.setdefault(file["of"], []).append(file)
            parts.append('    <ul class="appstore-files">')
            for file in version["files"]:
                if file.get("of"):
                    continue
                nested = companions.pop(file["name"], [])
                under = (
                    '<ul class="appstore-files appstore-companions">' + "".join(item(comp) for comp in nested) + "</ul>"
                    if nested
                    else ""
                )
                parts.append(item(file, under))
            for orphan in companions.values():
                for file in orphan:
                    parts.append(item(file))
            parts.append("    </ul>")
        parts.append("  </section>")
    certificate = str(M("appstore.certificate", "This box's certificate"))
    phone = html.escape(
        str(
            M(
                "appstore.phone",
                "📱 A phone needs permission to install an app from the browser you downloaded it with. Turn that on "
                "once, for this browser. A plain (unencrypted) address can bring a warning. {certificate} lets the "
                "phone trust it, and the HTTPS address of this page downloads without a warning.",
                certificate=certificate,
            )
        )
    ).replace(html.escape(certificate), f'<a href="/certificate">{html.escape(certificate)}</a>')
    parts.append(f'  <p class="setting-desc">{phone}</p>')
    address = html.escape(f"{scheme}://{host}/appstore/", quote=True)
    parts.append(
        f"  <section><p>{_esc(M('appstore.scan', 'Scan to open this page:'))}</p>"
        f'<span class="qr" id="appstore-qr" data-url="{address}" role="img" '
        f'aria-label="{_esc(M("appstore.qr-label", "QR code for the App store"))}"></span></section>'
    )
    chips = json.dumps(_chips(listings)).replace("<", "\\u003c")
    parts.append(f'  <script type="application/json" id="appstore-chips">{chips}</script>')
    parts.append('  <script src="/i18n.js"></script>')
    parts.append('  <script src="/hub.js"></script>')
    parts.append('  <script src="/qrcode.js"></script>')
    parts.append('  <script src="/appstore.js"></script>')
    parts.append("</body></html>")
    return "\n".join(parts).encode()


def index_json(level, firmware_visible=False):
    return {
        "app": "appstore",
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "holders": _index(level, firmware_visible),
    }


def feed_xml(level, firmware_visible=False):
    """The held files as Atom, newest first: titles, sizes, types, links. No script, no HTML."""
    entries = [
        (version["published"] or "", entry["title"], file)
        for entry in _index(level, firmware_visible)
        for version in entry["versions"]
        for file in version["files"]
    ]
    entries.sort(key=lambda item: item[0], reverse=True)
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"<title>{_esc(M('appstore.feed-title', 'App store'))}</title>",
        f"<updated>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}</updated>",
        "<id>tag:appstore-hub</id>",
        '<link href="/appstore/feed.xml" rel="self"/>',
    ]
    for published, title, file in entries[:MAX_FEED]:
        kind = _type_of(file["name"]) or "application/octet-stream"
        where = ""
        if file.get("board"):
            role = FIRMWARE_ROLES.get(file.get("role"))
            where = f" ({file['board']}{' · ' + str(role) if role else ''})"
        heading = html.escape(f"{title} {file['name']}{where}")
        identifier = file.get("sha256") or urllib.parse.quote(file["url"])
        categories = "".join(
            f'    <category term="{html.escape(str(file[key]), quote=True)}"/>\n'
            for key in ("board", "architecture", "role")
            if file.get(key)
        )
        out += [
            "  <entry>",
            f"    <title>{heading}</title>",
            f'    <link rel="enclosure" type="{kind}" length="{file["size"]}" '
            f'href="{html.escape(file["url"], quote=True)}"/>',
            f"    <id>urn:sha256:{html.escape(identifier)}</id>",
            categories.rstrip("\n"),
            f"    <updated>{html.escape(published)}</updated>",
            "  </entry>",
        ]
    out.append("</feed>")
    return "\n".join(out).encode()


# --- the routes ----------------------------------------------------------------------------


def _not_found(handler):
    handler.send_json(404, refused.say(M("hub.not-found", "Not found.")))


def route(handler, path, mode, flasher_mode="off"):
    """GET/HEAD under /appstore. The app's switch covers every address here; a holder marked "members"
    needs a signed-in visitor as well, page and file alike. The firmware entry also follows the
    flasher's access: a visitor who could not fetch /flasher/firmware/… is not shown it."""
    if (
        mode == "off"
        or (mode == "private" and handler._viewer() != "admin")
        or (mode == "users" and not handler._viewer())
    ):
        return _not_found(handler)
    level = handler._viewer()
    firmware_visible = flasher_mode == "public" or (flasher_mode == "private" and level == "admin")
    if path in ("/appstore", "/appstore/"):
        # The Host as it came, port and all: a QR of this page must answer on the address it was loaded from.
        host = handler.headers.get("Host", "")
        if not HOST_RE.match(host):
            host = "box.local"
        body = page(host, "https" if handler._https() else "http", level, firmware_visible)
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
        body = json.dumps(index_json(level, firmware_visible)).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
        return
    if path == "/appstore/feed.xml":
        body = feed_xml(level, firmware_visible)
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
        parts = path[len("/appstore/latest/") :].strip("/").split("/")
        if parts[:1] == [FIRMWARE_ID]:
            return _firmware_latest(handler, parts[1:], firmware_visible)
        return _latest(handler, parts, level)
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


def _firmware_latest(handler, parts, visible):
    """The newest kept version of the firmware that has this board and role, as a 302 to the flasher's own
    address for the file. `?channel=alpha|beta` narrows it; either channel by default."""
    if not visible or len(parts) != 2:
        return _not_found(handler)
    board, role = parts
    if not NAME_PART_RE.match(board) or role not in FIRMWARE_ROLES:
        return _not_found(handler)
    wanted = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).get("channel", [""])[0]
    entry = _firmware_entry(True)
    for version in entry["versions"] if entry else []:
        if wanted and version["channel"] != wanted:
            continue
        for one in version["boards"]:
            if one["board"] != board:
                continue
            for file in one["files"]:
                if file.get("role") == role:
                    handler.send_response(302)
                    handler.send_header("Location", file["url"])
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
                end = min(int(last), size - 1) if last else size - 1  # a last byte past the end means the end
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
