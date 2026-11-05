# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root never trusts a name from the hub (item 87, pattern P5). Every action root answers is a function
in root/hub_control.py's ACTIONS table, and takes its values from the request the hub wrote. Read with
ast: for each action, the request values whose names say they are a path, a device, a file, a unit, a
name, a host or an app (a `req.get("...")` or `req["..."]` in the handler) are listed in REVIEWED
with the check that makes each safe, and the text of that check, which must still be in the named
file. A new request value of that kind fails until it is reviewed and listed here; a check that is
removed or renamed fails because its text is gone.

A path a request names is opened through `safeio` or a directory fd (as `_take_staged` does after
`validate.path`), or is one that root's own listing already knows, or is only read. `validate.path`
is a check, not a handle: it returns the normalised name, so the file that is opened afterwards could
have been swapped in between.

python3 tests/root_trust_guard.py"""

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = "irate_box/root/"
KEY = re.compile(
    r"path|file|folder|dir|dest|src|zip|image|name|device|mount|card|unit|target|drive|stick|disk|source|host|console|manifest|reference|serial|app\b|book|kit\b",
    re.I,
)

# (action, request key) -> (file, text of the check that must still be there, how the value is held)
REVIEWED = {
    ("service", "unit"): (ROOT + "hub_control.py", "if unit not in UNITS:", "one of a fixed list of units"),
    ("usb-import", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("usb-import", "file"): (
        ROOT + "usbstick.py",
        'rel.is_absolute() or ".." in rel.parts',
        "relative, no .., a .zim; copied with O_NOFOLLOW, one link, a plain file (_copy)",
    ),
    ("usb-export", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("usb-export", "book"): (
        ROOT + "usbstick.py",
        'book = validate.name(book, "The book name", 64)',
        "a book name, then <zim folder>/<name>.zim, read as a plain file not a link",
    ),
    ("usb-kit-import", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("usb-kit-import", "kit"): (ROOT + "hub_control.py", "if not kits.ID_RE.fullmatch(kit):", "a kit id"),
    ("usb-kit-export", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("usb-kit-export", "kit"): (ROOT + "hub_control.py", "if not kits.ID_RE.fullmatch(kit):", "a kit id"),
    ("usb-export-many", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("usb-export-many", "books"): (ROOT + "hub_control.py", "usbstick.NAME_RE.fullmatch(b)", "each a book name"),
    ("backup-image", "device"): (
        ROOT + "usbstick.py",
        'if dev["name"] == name:',
        "matched against the devices root lists itself, now",
    ),
    ("app-install", "app"): (ROOT + "hub_control.py", "if app not in APPS:", "one of the built-in apps"),
    ("app-install", "zip"): (
        ROOT + "hub_control.py",
        'validate.path(zip_path, APP_STAGING, "The bundle path", must_exist=True)',
        "inside the staging folder, then taken into root's own folder through a directory fd (_take_staged) before it is read",
    ),
    ("app-rollback", "app"): (ROOT + "hub_control.py", "if app not in APPS:", "one of the built-in apps"),
    ("access", "app"): (
        ROOT + "hub_control.py",
        "if app not in access.ROUTED and app not in local",
        "a routed app, a local add-on, or a local id",
    ),
    ("housecall-scan", "path"): (
        ROOT + "housecall.py",
        "path = Path(path).resolve()",
        "the owner's choice of what to examine; only read, never written",
    ),
    ("housecall-scan", "target"): (
        ROOT + "hub_control.py",
        'if kind not in ("image", "device", "root", "this-card", "card", "peer"):',
        "one of six words",
    ),
    ("housecall-scan", "on_own_card"): (
        ROOT + "hub_control.py",
        'req.get("on_own_card") is True',
        "a true or false, never used as a path",
    ),
    ("housecall-scan", "image_first"): (
        ROOT + "hub_control.py",
        '"\\0" in image_first or len(image_first) > 4096',
        "a path the owner chose, read only; no NUL, bounded",
    ),
    ("housecall-scan", "reference"): (
        ROOT + "housecall.py",
        "path = Path(path).resolve()",
        "the owner's choice of a reference to compare with; only read",
    ),
    ("housecall-scan", "manifest"): (
        ROOT + "housecall.py",
        "path = Path(path).resolve()",
        "the owner's choice; only read",
    ),
    ("housecall-restore", "path"): (
        ROOT + "housecall.py",
        'record = next((r for r in report.get("files") or [] if r.get("path") == path), None)',
        "only a path that root's own examination report lists, with its own plan for it",
    ),
    ("housecall-pair", "name"): (ROOT + "housecall.py", "if not PEER_RE.fullmatch(name or", "a short name"),
    ("housecall-pair", "host"): (
        ROOT + "housecall.py",
        'if not HOST_RE.fullmatch(host or "") or host.startswith("-")',
        "a host name or address, no leading dash",
    ),
    ("housecall-unpair", "name"): (
        ROOT + "hub_control.py",
        'if not name or "\\0" in name or len(name) > 64:',
        "looked up among the paired boxes",
    ),
    ("rk-serial-setup", "console"): (
        ROOT + "hub_control.py",
        "CONSOLE_RE.fullmatch(console) and (SERIAL_BY_ID / console).exists()",
        "a name in /dev/serial/by-id that exists",
    ),
    ("rk-backup-delete", "name"): (
        ROOT + "rkboard.py",
        "if not isinstance(name, str) or not BACKUP_NAME_RE.fullmatch(name):",
        "a saved copy's name checked again in root: one file, no path",
    ),
    ("rk-backup-restore", "name"): (
        ROOT + "rkboard.py",
        "if not isinstance(name, str) or not BACKUP_NAME_RE.fullmatch(name):",
        "a saved copy's name checked again in root: one file, no path",
    ),
    ("rk-bundle-stage", "name"): (
        ROOT + "rkboard.py",
        "if not BUNDLE_FILE_RE.fullmatch(temp) or not BUNDLE_FILE_RE.fullmatch(name):",
        "a bundle file name, opened once with O_NOFOLLOW and copied from that descriptor",
    ),
    ("rk-card-setup", "card"): (
        ROOT + "rkcard.py",
        "name = validate.card(name)",
        "a card name, then matched against the cards root finds in a reader",
    ),
    ("rk-card-setup", "serial"): (
        ROOT + "rkcard.py",
        'found["serial"] != (serial or "")',
        "compared with the card root found; never used as a path",
    ),
    ("box-hostname", "hostname"): (ROOT + "hub_control.py", 'validate.hostname(req.get("hostname"))', "a host name"),
    ("nm-handover", "name"): (
        ROOT + "nmhandover.py",
        'if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,128}\\.yaml"',
        "the name of a netplan file",
    ),
    ("kit-check", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("content-export", "books"): (
        ROOT + "hub_control.py",
        "usbstick.NAME_RE.fullmatch(b)",
        "each a book name",
    ),
    ("offline-kit", "books"): (
        ROOT + "hub_control.py",
        "usbstick.NAME_RE.fullmatch(b)",
        "each a book name",
    ),
    ("kit-fetch", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-install", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-remove", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-keep", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-rollback", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-drop-previous", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-undefine", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-extra", "kit"): (ROOT + "hub_control.py", "kits.ID_RE.fullmatch(kit)", "a kit id"),
    ("kit-define", "kit"): (
        "irate_box/root/kits.py",
        "if not ID_RE.fullmatch(kid)",
        "the toolkit definition's own id check",
    ),
    ("rk-card-check", "card"): (
        ROOT + "rkcard.py",
        "name = validate.card(name)",
        "a card name, then matched against the cards root finds in a reader",
    ),
    ("rk-card-check", "serial"): (
        ROOT + "rkcard.py",
        'found["serial"] != (serial or "")',
        "compared with the card root found; never used as a path",
    ),
    ("rk-card-check", "image"): (
        ROOT + "rkboard.py",
        "name = validate.image(name)",
        "not read for a check; the flash action's image is checked against the box's own image list",
    ),
    ("rk-card-genuine", "card"): (
        ROOT + "rkcard.py",
        "name = validate.card(name)",
        "a card name, then matched against the cards root finds in a reader",
    ),
    ("rk-card-genuine", "serial"): (
        ROOT + "rkcard.py",
        'found["serial"] != (serial or "")',
        "compared with the card root found; never used as a path",
    ),
    ("rk-card-genuine", "image"): (
        ROOT + "rkboard.py",
        "name = validate.image(name)",
        "not read for a genuine test; the flash action's image is checked against the box's own image list",
    ),
    ("rk-card-flash", "card"): (
        ROOT + "rkcard.py",
        "name = validate.card(name)",
        "a card name, then matched against the cards root finds in a reader",
    ),
    ("rk-card-flash", "serial"): (
        ROOT + "rkcard.py",
        'found["serial"] != (serial or "")',
        "compared with the card root found; never used as a path",
    ),
    ("rk-card-flash", "image"): (
        ROOT + "rkboard.py",
        "name = validate.image(name)",
        "an image in the box's own image list, opened without following a link",
    ),
    ("rk-write", "image"): (
        ROOT + "rkboard.py",
        "name = validate.image(name)",
        "an image in the box's own image list, opened without following a link",
    ),
}


def _passes_request(call):
    """Whether a call hands its `req` to a helper, so the helper's request values belong to the action."""
    return any(isinstance(arg, ast.Name) and arg.id == "req" for arg in call.args) or any(
        isinstance(word.value, ast.Name) and word.value.id == "req" for word in call.keywords
    )


def _request_keys(function, functions, seen):
    """The request values an action's handler reads, including those in local helpers it passes `req` to."""
    keys = set()
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "req"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            keys.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "req"
            and isinstance(node.slice, ast.Constant)
        ):
            keys.add(node.slice.value)
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in functions and name not in seen and _passes_request(node):
                seen.add(name)
                keys |= _request_keys(functions[name], functions, seen)
    return {key for key in keys if isinstance(key, str) and KEY.search(key)}


def request_keys():
    """({action: {key}}, failures) for every handler in ACTIONS. A handler must be a plain module-level
    function, and every request value its handler (and the local helpers it passes `req` to) reads is
    returned for review."""
    tree = ast.parse((REPO / ROOT / "hub_control.py").read_text())
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    table = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ACTIONS"
    )
    found, failures = {}, []
    for action, handler in zip(table.keys, table.values, strict=False):
        if not (isinstance(handler, ast.Name) and handler.id in functions):
            failures.append(f"{action.value}: the handler {ast.unparse(handler)} is not a plain module-level function")
            continue
        keys = _request_keys(functions[handler.id], functions, {handler.id})
        if keys:
            found[action.value] = keys
    return found, failures


def main():
    found, failures = request_keys()
    listed = {}
    for action, key in REVIEWED:
        listed.setdefault(action, set()).add(key)
    for action, keys in sorted(found.items()):
        for key in sorted(keys - listed.get(action, set())):
            failures.append(
                f"{action}: the request value {key!r} is not reviewed (list it in REVIEWED with the check that holds it)"
            )
    for action, keys in sorted(listed.items()):
        for key in sorted(keys - found.get(action, set())):
            failures.append(
                f"{action}: {key!r} is reviewed but the handler no longer reads it (remove it from REVIEWED)"
            )
    for (action, key), (path, text, how) in sorted(REVIEWED.items()):
        if text not in (REPO / path).read_text():
            failures.append(f"{action} {key!r}: the check {text!r} is no longer in {path}; {how}")
    for failure in failures:
        print("FAIL " + failure)
    print(
        f"{sum(len(keys) for keys in found.values())} request values in {len(found)} actions, {len(REVIEWED)} reviewed; failures: {len(failures)}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
