#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Adapt a clone of https://github.com/1j01/jspaint for the hub. Run by the librarian on what
it fetched (the commit its manifest pins, or the newest).

    adapt_jspaint.py TREE [HUB_STATIC_DIR]

JS Paint is a static app: index.html loads its modules from src/ and its libraries from lib/.
The hub's copy changes only this much:

  * a strict Content-Security-Policy (no outside requests) replaces the permissive one, and the
    page says it was adapted;
  * the Multi-User, Upload To Imgur and Load From URL menus go: all need a network service,
    and the app is otherwise entirely local. Sessions are forced to LocalSession, so "New
    session" still autosaves in this browser but never contacts Firebase;
  * English is the only language kept (localization/<lang> is 8.6 MB for all 26), and the
    language list says so. PDF import (pdf.js, 13 MB) and the Head Tracker (Tracky Mouse's
    face-tracking model, 22 MB) are left out. Tracky Mouse's dwell clicker stays: it needs
    only its 84 KB script and stylesheet, no camera and no model;
  * the build files, Cypress tests, Electron and Discord shells, the PWA manifest and the
    source repository's own paper files go. /jspaint/ serves the whole folder, so they must
    not remain.

The app is not the hub's to relicense: it is MIT, and LICENSE.txt stays. Running it twice
changes nothing. Stdlib only.
"""

import re
import shutil
import sys
from pathlib import Path

MARK = "irate-box-adapted"
ORIGIN = "https://github.com/1j01/jspaint"

FILES = (
    "about.html",
    "audio",
    "favicon.ico",
    "help",
    "images",
    "index.html",
    "lib",
    "LICENSE.txt",
    "localization",
    "privacy.html",
    "src",
    "styles",
)
DROP_FILES = (
    "browserconfig.xml",
    "CHANGELOG.md",
    "CLI.md",
    "CNAME",
    "CONTRIBUTING.md",
    "cspell.json",
    "cypress.json",
    "eslint.config.mjs",
    "forge.config.js",
    "jsconfig.json",
    "jspaint-bin.js",
    "manifest.webmanifest",
    "Package.appxmanifest",
    "package-lock.json",
    "package.json",
    "prune-globals.js",
    "README.md",
    "release-process.md",
    "sync-package.js",
    "test-news-newer.html",
    "TODO.md",
)
DROP_DIRS = ("cypress", "discord-activity", "scripts", "svg-paint")
DROP_LIB = (
    "discord-embedded-app-sdk-v1.2.0-bundled-with-skypack.js",
    "firebase.js",
    "pdf.js",
)

NOTE = """<!-- {mark}: {origin} at the commit in irate-box-bundle.json.
     Adapted for the Irate-Box by irate_box/library/adapt_jspaint.py: no outside requests, no
     online sessions, Imgur upload or Load From URL, English only, and the optional PDF import
     and Head Tracker were left out. The Dwell Clicker stays. Painting, opening and saving are
     unchanged. -->
"""

POLICY = (
    '<meta http-equiv="Content-Security-Policy" content="'
    "default-src 'self'; "
    "script-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "media-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
    "worker-src 'self' blob:; "
    "connect-src 'self' data: blob:; "
    "object-src 'none'; "
    "base-uri 'self'\">"
)

CSP = re.compile(r'<meta http-equiv="Content-Security-Policy" content=".*?">', re.S)
PDF_JS = re.compile(r'^\s*<script src="lib/pdf.js/build/pdf.js"></script>\s*$\n?', re.M)
MANIFEST_LINK = re.compile(r'^\s*<link rel="manifest"[^>]*>\s*$\n?', re.M)
IMGUR_MENU = re.compile(
    r'\n\t\t\{\n\t\t\tlabel: localize\("&Upload To Imgur"\),.*?\n\t\t\},\n\t\tMENU_DIVIDER,\n'
    r'(?=\t\t\{\n\t\t\tlabel: localize\("Manage Storage"\))',
    re.S,
)
MULTIUSER_MENU = re.compile(
    r'\n\t\t\{\n\t\t\temoji_icon: "👥",.*?\n\t\t\},\n(?=\t\t\{\n\t\t\temoji_icon: "💄",)',
    re.S,
)
HEAD_TRACKER_MENU = re.compile(r'\n\t\t\{\n\t\t\temoji_icon: "🧑",.*?\n\t\t\},\n(?=\t\t// Later on)', re.S)
LOAD_URL_MENU = re.compile(
    r'\n\t\t\{\n\t\t\tlabel: localize\("&Load From URL"\),.*?\n\t\t\},\n\t\tMENU_DIVIDER,\n'
    r'(?=\t\t\{\n\t\t\tlabel: localize\("Manage Storage"\))',
    re.S,
)
BODY = re.compile(r"(<body\b[^>]*>)", re.I)
LANGUAGES = re.compile(r"const available_languages = \[[^\n]*\];")
NOTE_HTML = (
    '<p class="irate-box-adapted"><strong>This copy on the Irate-Box</strong> has no online '
    "multi-user sessions, Imgur upload or Load From URL. Pictures stay in this browser, where "
    "other web add-ons on the box can read them, unless you save one to your device.</p>\n"
)
IMGUR_BUTTON = re.compile(r'\n\t\t\t\$win\.\$Button\("Upload to Imgur",.*?\n\t\t\t\}\)\.focus\(\);\n', re.S)


class AdaptError(Exception):
    pass


def adapt_page(text):
    if MARK in text:
        return text
    first, sep, rest = text.partition("\n")
    if first.strip().lower() != "<!doctype html>":
        raise AdaptError("index.html does not start with <!doctype html>")
    if not CSP.search(rest):
        raise AdaptError("index.html has no Content-Security-Policy to replace")
    rest = CSP.sub(POLICY, rest, count=1)
    rest = rest.replace("lib/tracky-mouse/images/tracky-mouse-logo-512.png", "images/tracky-mouse-16x16.png")
    for pattern, what in (
        (PDF_JS, "the PDF.js script"),
        (MANIFEST_LINK, "the web-app manifest"),
    ):
        rest, n = pattern.subn("", rest)
        if n != 1:
            raise AdaptError(f"index.html changed: {what} is there {n} times, not once")
    return first + sep + NOTE.format(mark=MARK, origin=ORIGIN) + rest


def adapt(tree):
    tree = Path(tree)
    index = tree / "index.html"
    if index.is_file() and MARK in index.read_text(encoding="utf-8"):
        return  # already adapted
    if not index.is_file() or not (tree / "src" / "sessions.js").is_file():
        raise AdaptError("no index.html and src/sessions.js: is this 1j01/jspaint?")
    menus = tree / "src" / "menus.js"
    sessions = tree / "src" / "sessions.js"
    localization = tree / "src" / "app-localization.js"
    if not (tree / "localization" / "en").is_dir():
        raise AdaptError("no localization/en: is this 1j01/jspaint?")
    for path in (menus, sessions, localization):
        if not path.is_file():
            raise AdaptError(f"the app has changed: no {path.relative_to(tree)}")
    menu_text = menus.read_text(encoding="utf-8")
    if (
        len(MULTIUSER_MENU.findall(menu_text)) != 1
        or len(IMGUR_MENU.findall(menu_text)) != 1
        or len(HEAD_TRACKER_MENU.findall(menu_text)) != 1
        or len(LOAD_URL_MENU.findall(menu_text)) != 1
    ):
        raise AdaptError("src/menus.js changed: a menu to remove is not found once")
    functions = tree / "src" / "functions.js"
    if not functions.is_file():
        raise AdaptError("the app has changed: no src/functions.js")
    function_text = functions.read_text(encoding="utf-8")
    if len(IMGUR_BUTTON.findall(function_text)) != 1:
        raise AdaptError("src/functions.js changed: the GIF dialog's Upload to Imgur button is not found once")
    session_text = sessions.read_text(encoding="utf-8")
    if session_text.count("\t\t\tif (local) {\n") != 1:
        raise AdaptError("src/sessions.js changed: 'if (local) {' is not there once")
    language_text = localization.read_text(encoding="utf-8")
    if len(LANGUAGES.findall(language_text)) != 1:
        raise AdaptError("src/app-localization.js changed: the language list is not found once")

    page_text = adapt_page(index.read_text(encoding="utf-8"))
    menu_text = MULTIUSER_MENU.sub("", IMGUR_MENU.sub("", HEAD_TRACKER_MENU.sub("", LOAD_URL_MENU.sub("", menu_text))))
    function_text = IMGUR_BUTTON.sub("", function_text)
    session_text = session_text.replace(
        "\t\t\tif (local) {\n",
        "\t\t\tif (true) { // Irate-Box: no Firebase; every session, named or local, stays in this browser.\n",
        1,
    )
    language_text = LANGUAGES.sub('const available_languages = ["en"];', language_text)
    pages = {}
    for name in ("about.html", "privacy.html"):
        page = tree / name
        if not page.is_file():
            raise AdaptError(f"the app has changed: no {name}")
        body = BODY.search(page.read_text(encoding="utf-8"))
        if body is None:
            raise AdaptError(f"{name} has no <body> to put the note in")
        pages[name] = (
            page.read_text(encoding="utf-8")[: body.end()] + NOTE_HTML + page.read_text(encoding="utf-8")[body.end() :]
        )

    # The librarian clones into a throwaway tree and drops it on failure, so deleting first and
    # writing after is safe here; draw.io stages a new tree because it replaces the whole clone.
    shutil.rmtree(tree / ".git", ignore_errors=True)
    for name in DROP_FILES:
        (tree / name).unlink(missing_ok=True)
    for name in DROP_DIRS:
        shutil.rmtree(tree / name, ignore_errors=True)
    for name in DROP_LIB:
        path = tree / "lib" / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    # Dwell Clicker needs TrackyMouse itself (84 KB), not the face-tracking model or images.
    tracky = tree / "lib" / "tracky-mouse"
    for path in tracky.iterdir():
        if path.name != "core":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    for path in (tracky / "core").iterdir():
        if path.name not in ("tracky-mouse.js", "tracky-mouse.css"):
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    for path in (tree / "localization").iterdir():
        if path.is_dir() and path.name != "en":
            shutil.rmtree(path)

    index.write_text(page_text, encoding="utf-8")
    menus.write_text(menu_text, encoding="utf-8")
    functions.write_text(function_text, encoding="utf-8")
    sessions.write_text(session_text, encoding="utf-8")
    localization.write_text(language_text, encoding="utf-8")
    for name, text in pages.items():
        (tree / name).write_text(text, encoding="utf-8")


def main(argv):
    if not 1 <= len(argv) <= 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    try:
        adapt(argv[0])
    except (AdaptError, OSError) as exc:
        print(f"adapt_jspaint: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
