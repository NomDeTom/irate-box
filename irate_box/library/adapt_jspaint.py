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
    not remain;
  * the news's pictures from outside the box: one from JS Paint's own GitHub tree points at
    the same file in this copy, where it has it; any other is left out, as the policy would
    block it anyway.

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
     unchanged, and Save to the box and Open from the box use the hub's own gallery (/api/saves). -->
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
HEAD_TRACKER_HASH = re.compile(r"if \(location\.hash\.match\(/head-tracker/i\)\) \{")
NOTE_HTML = (
    '<p class="irate-box-adapted"><strong>This copy on the Irate-Box</strong> has no online '
    "multi-user sessions, Imgur upload or Load From URL. Pictures can be saved in this box's gallery "
    "and opened from it, as well as saved to your device.</p>\n"
)
# A picture from outside the box, its tag spread over lines as the news writes it; and JS Paint's own GitHub raw URL
# (any commit) for a file under images/.
REMOTE_IMG = re.compile(r'<img\b[^>]*?\bsrc="(https?://[^"]*)"[^>]*>')
OWN_RAW = re.compile(r"^https://github\.com/1j01/jspaint/blob/[^/]+/(images/[^?\"]+)\?raw=true$")


def local_images(text, tree):
    """The page's outside pictures: JS Paint's own files from this copy where it has them, the others left out."""

    def swap(match):
        own = OWN_RAW.match(match.group(1))
        if own and (Path(tree) / own.group(1)).is_file():
            return match.group(0).replace(match.group(1), own.group(1))
        return ""

    return REMOTE_IMG.sub(swap, text)


SAVE_AS_MENU = re.compile(
    r'(\n\t\t\{\n\t\t\tlabel: localize\("Save &As"\),.*?\n\t\t\},\n)',
    re.S,
)
SAVE_MENU_ITEMS = (
    "\t\t{\n"
    '\t\t\temoji_icon: "💾",\n'
    '\t\t\tlabel: "Save to the &box",\n'
    "\t\t\taction: () => { file_save_to_box(); },\n"
    '\t\t\tdescription: "Saves the picture in this box\'s gallery.",\n'
    "\t\t},\n"
    "\t\t{\n"
    '\t\t\temoji_icon: "📂",\n'
    '\t\t\tlabel: "Open from the b&ox",\n'
    "\t\t\taction: () => { file_open_from_box(); },\n"
    '\t\t\tdescription: "Opens a picture saved in this box\'s gallery.",\n'
    "\t\t},\n"
)
BOX_SAVES_JS = r"""
// Irate-Box: the box's gallery, as the other adapted apps use it (/api/saves).
let box_save_id = null;
let box_save_locked = false;
let box_save_lock_n = 0;
function box_escape(text) {
	return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function box_thumbnail() {
	const scale = Math.min(1, 128 / Math.max(main_canvas.width, main_canvas.height));
	const thumb = document.createElement("canvas");
	thumb.width = Math.max(1, Math.round(main_canvas.width * scale));
	thumb.height = Math.max(1, Math.round(main_canvas.height * scale));
	thumb.getContext("2d").drawImage(main_canvas, 0, 0, thumb.width, thumb.height);
	return thumb.toDataURL("image/png");
}
async function file_save_to_box() {
	let state;
	try {
		state = main_canvas.toDataURL("image/png");
	} catch (error) {
		show_error_message("The picture could not be read to save it.", error);
		return;
	}
	const suggested = (file_name || "Picture").replace(/\.[^.]+$/, "");
	const newCopy = box_save_id && box_save_locked && !(window.HubLock && HubLock.has("saves", box_save_id));
	const { $window, promise } = showMessageBox({
		messageHTML: `<p>Save this picture in the box's gallery.</p>
			${newCopy ? "<p>This browser does not hold this picture's key, so saving makes a new copy.</p>" : ""}
			<p><label>Name <input type="text" id="irate-box-save-name" value="${box_escape(suggested)}"></label></p>`,
		buttons: [
			{ label: "Save", value: "save", default: true },
			{ label: "Cancel", value: "cancel" },
		],
	});
	const choice = await promise;
	if (choice !== "save") {
		return;
	}
	const name = ($window.$content.find("#irate-box-save-name").val() || "").trim() || "Picture";
	// A locked save is changed only by a browser that holds its key. This one re-saves in place;
	// one opened on another device saves as a new copy, and its dialog says so.
	let id = box_save_id;
	let change = null;
	let fresh = null;
	if (box_save_id && box_save_locked) {
		if (window.HubLock && HubLock.has("saves", box_save_id)) {
			change = HubLock.change("saves", box_save_id, box_save_lock_n);
		}
		if (!change) {
			id = null;
		}
	}
	if (!id && window.HubLock) {
		fresh = HubLock.create("saves");
	}
	const headers = { "Content-Type": "application/json" };
	if (change) {
		Object.assign(headers, change.headers);
	}
	if (fresh) {
		headers["X-Lock-New"] = fresh.header;
	}
	try {
		const response = await fetch("/api/saves", {
			method: "POST",
			headers,
			body: JSON.stringify({ kind: "jspaint", id: id || undefined, name, state, thumb: box_thumbnail() }),
		});
		if (!response.ok) {
			throw new Error(`the gallery answered ${response.status}`);
		}
		const meta = await response.json();
		if (change) {
			change.done();
		}
		if (fresh) {
			HubLock.keep("saves", meta.id, fresh.seed);
		}
		box_save_id = meta.id;
		box_save_locked = !!meta.locked;
		box_save_lock_n = meta.lock_n || 0;
		file_name = `${name}.png`;
		saved = true;
		update_title();
	} catch (error) {
		show_error_message("The picture could not be saved in the box's gallery.", error);
	}
}
async function file_open_from_box() {
	let saves;
	try {
		const response = await fetch("/api/saves");
		if (!response.ok) {
			throw new Error(`the gallery answered ${response.status}`);
		}
		const data = await response.json();
		saves = (data.saves || []).filter((save) => save.kind === "jspaint");
	} catch (error) {
		show_error_message("The box's gallery could not be read.", error);
		return;
	}
	if (!saves.length) {
		showMessageBox({ message: "No picture is saved in this box's gallery yet." });
		return;
	}
	const options = saves
		.map((save) => `<option value="${box_escape(save.id)}">${box_escape(save.name || "untitled")}</option>`)
		.join("");
	const { $window, promise } = showMessageBox({
		messageHTML: `<p>Open a picture saved in the box's gallery.</p>
			<p><label>Picture <select id="irate-box-open-name">${options}</select></label></p>`,
		buttons: [
			{ label: "Open", value: "open", default: true },
			{ label: "Cancel", value: "cancel" },
		],
	});
	const choice = await promise;
	if (choice !== "open") {
		return;
	}
	const id = $window.$content.find("#irate-box-open-name").val();
	const save = saves.find((item) => item.id === id);
	if (!save) {
		return;
	}
	try {
		const whole = await fetch(`/api/saves/${encodeURIComponent(save.id)}`);
		if (!whole.ok) {
			throw new Error(`the gallery answered ${whole.status}`);
		}
		const full = await whole.json();
		const info = await load_image_from_uri(full.state);
		box_save_id = save.id;
		box_save_locked = !!(full.locked || save.locked);
		box_save_lock_n = full.lock_n || save.lock_n || 0;
		file_name = `${save.name || "Picture"}.png`;
		open_from_image_info(info);
		if (box_save_locked && !(window.HubLock && HubLock.has("saves", box_save_id))) {
			showMessageBox({ message: "This picture is locked by another device. Saving it here will make a new copy." });
		}
	} catch (error) {
		show_error_message("The picture could not be opened from the box's gallery.", error);
	}
}
"""
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


def add_box_saves(menu_text, function_text):
    """The File menu's two gallery items, and the functions they call, added to JS Paint's own source."""
    if len(SAVE_AS_MENU.findall(menu_text)) != 1:
        raise AdaptError("src/menus.js changed: the Save As item is not found once")
    menu_text = SAVE_AS_MENU.sub(lambda m: m.group(1) + SAVE_MENU_ITEMS, menu_text, count=1)
    if menu_text.count("file_save_to_box, file_open_from_box") != 1:
        menu_text = menu_text.replace(
            "file_save_as, image_attributes",
            "file_save_as, file_save_to_box, file_open_from_box, image_attributes",
            1,
        )
    if menu_text.count("file_save_to_box, file_open_from_box") != 1:
        raise AdaptError("src/menus.js changed: its import list is not found once")
    if function_text.count("file_save_as, getSelectionText") != 1 or "\nexport {\n" not in function_text:
        raise AdaptError("src/functions.js changed: its export list is not found")
    function_text = function_text.replace(
        "file_save_as, getSelectionText",
        "file_save_as, file_save_to_box, file_open_from_box, getSelectionText",
        1,
    )
    function_text = function_text.replace("\nexport {\n", "\n" + BOX_SAVES_JS + "\nexport {\n", 1)
    return menu_text, function_text


LOCK_JS = Path(__file__).resolve().parents[2] / "web" / "lock.js"


def add_lock_script(text):
    """The page's copy of the hub's device-lock module (web/lock.js), before the app's own scripts."""
    if LOCK_JS.is_file() and '<script src="lock.js"></script>' not in text:
        text = text.replace("</body>", '\t<script src="lock.js"></script>\n</body>', 1)
    return text


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
    app = tree / "src" / "app.js"
    if not app.is_file():
        raise AdaptError("the app has changed: no src/app.js")
    app_text = app.read_text(encoding="utf-8")
    if len(HEAD_TRACKER_HASH.findall(app_text)) != 1 or app_text.count("/dwell-clicker|head-tracker/i") != 1:
        raise AdaptError("src/app.js changed: the Head Tracker hash is not found once")
    function_text = functions.read_text(encoding="utf-8")
    if len(IMGUR_BUTTON.findall(function_text)) != 1:
        raise AdaptError("src/functions.js changed: the GIF dialog's Upload to Imgur button is not found once")
    session_text = sessions.read_text(encoding="utf-8")
    if session_text.count("\t\t\tif (local) {\n") != 1:
        raise AdaptError("src/sessions.js changed: 'if (local) {' is not there once")
    language_text = localization.read_text(encoding="utf-8")
    if len(LANGUAGES.findall(language_text)) != 1:
        raise AdaptError("src/app-localization.js changed: the language list is not found once")

    page_text = add_lock_script(local_images(adapt_page(index.read_text(encoding="utf-8")), tree))
    menu_text = MULTIUSER_MENU.sub("", IMGUR_MENU.sub("", HEAD_TRACKER_MENU.sub("", LOAD_URL_MENU.sub("", menu_text))))
    function_text = IMGUR_BUTTON.sub("", function_text)
    # The box's gallery: the manifest says what is kept there and who can see it.
    menu_text, function_text = add_box_saves(menu_text, function_text)
    app_text = app_text.replace("/dwell-clicker|head-tracker/i", "/dwell-clicker/i").replace(
        HEAD_TRACKER_HASH.search(app_text).group(0),
        "if (false) { // Irate-Box: no Head Tracker in this copy.",
    )
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
    app.write_text(app_text, encoding="utf-8")
    sessions.write_text(session_text, encoding="utf-8")
    localization.write_text(language_text, encoding="utf-8")
    for name, text in pages.items():
        (tree / name).write_text(text, encoding="utf-8")
    if LOCK_JS.is_file():
        shutil.copyfile(LOCK_JS, tree / "lock.js")


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
