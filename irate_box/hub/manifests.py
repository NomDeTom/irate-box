# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's app manifests: one JSON file per app in apps.d/, read by server.py (tiles and
/status), librarian.py (where updates come from) and hub_control.py (where an app installs,
and which units the admin page may control). Adding an app is adding a file here.

apps.d/ ships with the code (/opt/irate-box/apps.d, root-owned), so the root helper can act
on it. Every part but "id" and "order" is optional:

  {
    "id": "draw",                     letters, digits, '-'; unique
    "order": 10,                      tiles and /status are sorted by this
    "tile":    {"icon", "name", "desc", "href", "new_tab": bool, "element_id": str,
                "row": "apps" | "box"}  a tile on the home page; or {"widget": "people" | "qr" |
                                      "system", "row": "box"}, one of the hub's live tiles
    "menu":    {"title", "subtitle", "about", "art": "librarian" | "doctor" | "controller" | "electronics"}
                                      the tile opens a list page (at its href), which the hub
                                      renders from every manifest's entries aimed at it; art: a
                                      krab in its bottom-left corner (web/art/)
    "entries": [{"menus": {"<menu id>": order, ...}, "name", "desc", "href", "new_tab": bool}]
                                      lines on those list pages (greyed with this app's status)
    "discover": {"file", "href", "order", "groups": {"<group>": "<menu id>"}}
                                      more entries, from a JSON list [{page, title, group}] the
                                      app's adapt script leaves in its install folder: whatever
                                      the entries above do not already name
    "status":  {"path", "name", "port" | "root_env", "unit", "control": bool, "note"},
                                      how /status and /admin see it: a loopback port to probe,
                                      or the environment variable naming the folder the
                                      web server serves it from; "control" offers start/stop/boot on /admin
    "install": {"dir", "needs", "title", "restart"},
                                      where its bundle goes, under /usr/share/hub ("apps/draw");
                                      the file (or glob) that proves a bundle is whole; a unit
                                      to restart after installing
    "source":  {"type": "bundle", "repo", "branch", "workflow", "pattern"}
             | {"type": "git", "repo", "branch", "adapt"},
                                      where the librarian finds updates: a fork's Actions
                                      artifact, or a git repository (cloned, then run through
                                      the "adapt" script from the hub's code)
    "access":  {"title", "note"}       who may open it is set on /admin (public, private, off) for
                                      the apps access.py names; these word its line there
    "core": true                      kept current by default ("Keep all apps current")
  }

Stdlib only.
"""

import json
import os
import re
from pathlib import Path

APPS_D = Path(os.environ.get("HUB_APPS_D", Path(__file__).resolve().parents[2] / "apps.d"))
SHARE = Path(os.environ.get("HUB_SHARE_DIR", "/usr/share/hub"))

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
DIR_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+$")


WIDGETS = ("people", "qr", "system")
PAGE_RE = re.compile(r"^[A-Za-z0-9_-]+\.html$")


class ManifestError(ValueError):
    pass


def _check(m, where):
    def need(cond, what):
        if not cond:
            raise ManifestError(f"{where}: {what}")

    need(isinstance(m, dict), "not a JSON object")
    need(isinstance(m.get("id"), str) and ID_RE.match(m["id"]), "id: lower-case letters, digits, '-'")
    need(type(m.get("order")) is int, "order: an integer")
    tile = m.get("tile")
    if tile is not None:
        need(tile.get("row", "apps") in ("apps", "box"), "tile.row: apps or box")
        if "widget" in tile:
            need(tile["widget"] in WIDGETS, f"tile.widget: one of {', '.join(WIDGETS)}")
        else:
            for k in ("icon", "name", "desc", "href"):
                need(isinstance(tile.get(k), str), f"tile.{k}: a string")
            need(tile["href"].startswith("/"), "tile.href: a path on this hub")
    menu = m.get("menu")
    if menu is not None:
        need(tile is not None and "href" in tile and re.match(r"^/[a-z0-9-]+\.html$", tile["href"]),
             "a menu needs a tile whose href is its page, /<name>.html")
        for k in ("title", "subtitle"):
            need(isinstance(menu.get(k), str), f"menu.{k}: a string")
        need(menu.get("art") in (None, "librarian", "doctor", "controller", "electronics"), "menu.art: librarian, doctor, controller or electronics")
    for e in m.get("entries", []):
        need(isinstance(e, dict) and isinstance(e.get("menus"), dict) and e["menus"], "entries: each with menus")
        need(all(type(v) is int for v in e["menus"].values()), "entries.menus: menu id -> order")
        for k in ("name", "href"):
            need(isinstance(e.get(k), str), f"entries.{k}: a string")
        need(e["href"].startswith("/"), "entries.href: a path on this hub")
    disc = m.get("discover")
    if disc is not None:
        need(m.get("install") is not None, "discover needs an install")
        need(re.match(r"^[a-z0-9_.-]+\.json$", str(disc.get("file", ""))), "discover.file: a .json name")
        need("{page}" in str(disc.get("href", "")), "discover.href: containing {page}")
        need(isinstance(disc.get("groups"), dict), "discover.groups: group -> menu id")
    status = m.get("status")
    if status is not None:
        need(isinstance(status.get("name"), str), "status.name: a string")
        need(status.get("path") is None or isinstance(status["path"], str), "status.path: a string or null")
        need(("port" in status) != ("root_env" in status), "status: either port or root_env")
        need("port" not in status or type(status["port"]) is int, "status.port: an integer")
        need("unit" not in status or UNIT_RE.match(str(status["unit"])), "status.unit: a .service name")
        need(not status.get("control") or "unit" in status, "status.control needs a unit")
    inst = m.get("install")
    if inst is not None:
        need(isinstance(inst.get("dir"), str) and DIR_RE.match(inst["dir"]), "install.dir: e.g. apps/draw")
        need(isinstance(inst.get("needs"), str) and ".." not in inst["needs"]
             and not inst["needs"].startswith("/"), "install.needs: a relative file or glob")
        need("restart" not in inst or UNIT_RE.match(str(inst["restart"])), "install.restart: a .service name")
    addon = m.get("addon")
    if addon is not None:
        need(re.match(r"^--with-(notes|sync|mqtt|term|collab)$", str(addon.get("option", ""))),
             "addon.option: one of install.sh's --with-notes, --with-sync, --with-mqtt, --with-term, --with-collab")
        for k in ("title", "summary"):
            need(isinstance(addon.get(k), str), f"addon.{k}: a string")
        need("consent" not in addon or isinstance(addon["consent"], str), "addon.consent: a string")
        need("needs" not in addon or isinstance(addon["needs"], str), "addon.needs: a string")
    src = m.get("source")
    if src is not None:
        need(inst is not None, "a source needs an install")
        need(src.get("type") in ("bundle", "git"), "source.type: bundle or git")
        if src["type"] == "bundle":
            need(REPO_RE.match(str(src.get("repo", ""))), "source.repo: OWNER/REPO")
            need(re.match(r"^[A-Za-z0-9_.-]+\.ya?ml$", str(src.get("workflow", ""))), "source.workflow: a file name")
        else:
            need(GIT_URL_RE.match(str(src.get("repo", ""))), "source.repo: an https:// git URL")
            need("adapt" not in src or re.match(r"^[a-z_]+\.py$", str(src["adapt"])), "source.adapt: a script in the hub's code")


def load(folder=None):
    """Every manifest, sorted by order. A broken file raises: better a loud failure at start
    than an app that silently vanishes."""
    folder = Path(folder or APPS_D)
    out, seen = [], set()
    for path in sorted(folder.glob("*.json")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ManifestError(f"{path.name}: {exc}")
        _check(m, path.name)
        if m["id"] in seen:
            raise ManifestError(f"{path.name}: id {m['id']} is used twice")
        seen.add(m["id"])
        out.append(m)
    return sorted(out, key=lambda m: (m["order"], m["id"]))


def menus(manifests):
    """{menu id: manifest} for the list pages."""
    return {m["id"]: m for m in manifests if m.get("menu")}


def entries_for(menu_id, manifests, skip=()):
    """The lines of one list page, in order: [(order, entry, status path)]. Entries named in a
    manifest come first by their order; discovered ones (an app's adapt script found them,
    e.g. a calculator added to the site's index) fill in what the entries do not name. skip:
    apps whose entries are left out (not public: access.py)."""
    out = []
    for m in manifests:
        if m["id"] in skip:
            continue
        status_path = m.get("status", {}).get("path")
        named = set()
        for e in m.get("entries", []):
            named.add(e["href"])
            if menu_id in e["menus"]:
                out.append((e["menus"][menu_id], e, status_path))
        disc = m.get("discover")
        if disc:
            try:
                found = json.loads((install_dir(m) / disc["file"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                found = []
            for n, item in enumerate(found if isinstance(found, list) else []):
                if not isinstance(item, dict) or not PAGE_RE.match(str(item.get("page", ""))):
                    continue
                href = disc["href"].replace("{page}", item["page"])
                if href in named or disc["groups"].get(item.get("group")) != menu_id:
                    continue
                named.add(href)
                out.append((disc.get("order", 1000) + n,
                            {"name": str(item.get("title") or item["page"])[:120], "desc": "", "href": href},
                            status_path))
    return sorted(out, key=lambda x: x[0])


def addons(manifests=None):
    """{id: manifest} for the add-ons /admin can add and remove (those with an "addon" part)."""
    return {m["id"]: m for m in (manifests if manifests is not None else load()) if m.get("addon")}


def installable(manifests=None):
    """{id: manifest} for apps the librarian and the root helper install."""
    return {m["id"]: m for m in (manifests or load()) if m.get("install")}


def install_dir(m):
    return SHARE / m["install"]["dir"]


def controllable_units(manifests=None):
    return [m["status"]["unit"] for m in (manifests or load())
            if m.get("status", {}).get("control")]
