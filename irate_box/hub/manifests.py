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
    "menu":    {"title", "subtitle", "about", "art": "librarian" | "doctor" | "controller" | "electronics",
                "banner": ["<line>", ...]}
                                      the tile opens a list page (at its href), which the hub
                                      renders from every manifest's entries aimed at it; art: a
                                      krab in its bottom-left corner (web/art/); banner: text art,
                                      as written, as a faint ident in the bottom-right corner
    "entries": [{"menus": {"<menu id>": order, ...}, "name", "desc", "href", "new_tab": bool}]
                                      lines on those list pages (greyed with this app's status)
    "discover": {"file", "href", "order", "groups": {"<group>": "<menu id>"}}
                                      more entries, from a JSON list [{page, title, group}] the
                                      app's adapt script leaves in its install folder: whatever
                                      the entries above do not already name
    "status":  {"path", "name", "port" | "root_env" | "socket", "unit", "control": bool, "note"},
                                      how /status and /admin see it: a loopback port to probe,
                                      or the environment variable naming the folder the
                                      web server serves it from; "control" offers start/stop/boot on /admin
    "install": {"dir", "needs", "title", "restart"},
                                      where its bundle goes, under /usr/share/hub ("apps/draw");
                                      the file (or glob) that proves a bundle is whole; a unit
                                      to restart after installing
    "source":  {"type": "bundle", "repo", "branch", "workflow", "pattern"}
             | {"type": "git", "repo", "branch", "adapt", "pin"},
                                      where the librarian finds updates: a fork's Actions
                                      artifact, or a git repository (cloned, then run through
                                      the "adapt" script from the hub's code); pin: the full
                                      hash of the last commit known good, which the librarian
                                      installs unless the owner follows the newest (librarian.py)
    "access":  {"title", "note"}       who may open it is set on /admin (public, private, off) for
                                      the apps access.py names; these word its line there
    "core": true                      kept current by default ("Keep all apps current")
  }

Local add-ons (plans/no-root-addons-plan): a static web app the owner adds from /admin, with
no root and no change to this code. Its manifest lives in $HUB_STATE_DIR/apps.d/ (the hub's to
write), says much less than one here, and is turned into the full shape by local():

  {
    "id": "eliza",                    as above; not a built-in's id, nor a page of the hub's
    "order": 65,
    "tile": {"icon", "name", "desc", "path"}      path: the page inside the add-on (default "")
    "menu": {"title", "subtitle", "about", "banner"}   optional: a list page at /<id>.html
    "entries": [{"menus": {"<id>": order}, "name", "desc", "path"}]   lines on its list page
    "source": {"type": "git", "repo", "branch", "pin", "adapt"}   pin required; adapt only a
             | {"type": "bundle", "repo", "workflow", "branch", "pattern"}   script of this code
    "needs": "eliza.html",            the file that proves an install whole
    "addon": {"title", "summary", "consent"}      consent required
    "capabilities": {"connect": ["ws://{box}/mqtt", ...], "storage": true}
  }

It is served on the add-on origin (a second port of the web server, from
$HUB_STATE_DIR/addons/<id>/), reached as /addons/<id>/ on the hub's own, which redirects there;
its pages are framed by app.html like any app. connect: what its pages may reach besides
their own origin ({box}: the hub's own host), the rest of its Content-Security-Policy.

Stdlib only.
"""

import json
import os
import re
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
APPS_D = Path(os.environ.get("HUB_APPS_D", CHECKOUT / "apps.d"))
SHARE = Path(os.environ.get("HUB_SHARE_DIR", "/usr/share/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
LOCAL_D = STATE / "apps.d"            # the local add-ons' manifests (the hub's)
ADDONS = STATE / "addons"             # and their files, one folder each (the add-on origin)
CATALOGUE = CHECKOUT / "addons"       # the add-ons the hub offers on /admin

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
UNIT_RE = re.compile(r"^[A-Za-z0-9@_.-]+\.service$")
DIR_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GIT_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+/[A-Za-z0-9_./-]+$")


LOCAL_KEYS = {"id", "order", "tile", "menu", "entries", "source", "needs", "addon", "capabilities"}
# A path inside an add-on: no scheme, no host, no "..", no leading "/".
LOCAL_PATH_RE = re.compile(r"^(?![./])(?!.*\.\.)[A-Za-z0-9._~/-]*(\?[A-Za-z0-9._~=&%+-]*)?$")
# What an add-on's pages may connect to: {box} is the hub's own host.
CONNECT_RE = re.compile(r"^(wss?|https?)://(\{box\}|[a-z0-9.-]+)(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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
        need("banner" not in menu or (isinstance(menu["banner"], list) and 0 < len(menu["banner"]) <= 20
                                      and all(isinstance(x, str) and len(x) <= 120 for x in menu["banner"])),
             "menu.banner: up to 20 lines of up to 120 characters")
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
        need(sum(k in status for k in ("port", "root_env", "socket")) == 1, "status: one of port, root_env or socket")
        need("socket" not in status or re.match(r"^/run/[a-z0-9-]+/[a-z0-9.-]+\.sock$", str(status["socket"])),
             "status.socket: a socket under /run")
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
            need("pin" not in src or re.match(r"^[0-9a-f]{40}$", str(src["pin"])), "source.pin: a full commit hash")


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


def _hub_pages():
    """Names the hub's own pages and routes use, which no local add-on may take."""
    web = CHECKOUT / "web"
    names = {p.stem for p in web.glob("*.html")} | {p.name for p in web.iterdir() if p.is_dir()}
    return names | {"admin", "api", "app", "addons", "status", "messages", "board", "source", "flasher",
                    "notes", "sync", "term", "wiki", "git", "git-private", "mqtt", "socket.io"}


def check_local(m, where="local manifest", builtin_ids=()):
    """A local add-on's manifest, as pasted or from the catalogue: only what it may say."""
    def need(cond, what):
        if not cond:
            raise ManifestError(f"{where}: {what}")

    need(isinstance(m, dict), "not a JSON object")
    extra = set(m) - LOCAL_KEYS
    need(not extra, f"not allowed in a local add-on: {', '.join(sorted(extra))}")
    need(isinstance(m.get("id"), str) and ID_RE.match(m["id"]), "id: lower-case letters, digits, '-'")
    need(m["id"] not in builtin_ids and m["id"] not in _hub_pages(), f"id: {m['id']} is the hub's own")
    need(type(m.get("order")) is int, "order: an integer")
    tile = m.get("tile")
    need(isinstance(tile, dict) and all(isinstance(tile.get(k), str) for k in ("icon", "name", "desc")),
         "tile: icon, name and desc")
    need(set(tile) <= {"icon", "name", "desc", "path"}, "tile: icon, name, desc and path only")
    need(LOCAL_PATH_RE.match(str(tile.get("path", ""))), "tile.path: a path inside the add-on")
    menu = m.get("menu")
    if menu is not None:
        need(isinstance(menu, dict) and set(menu) <= {"title", "subtitle", "about", "banner"}, "menu: title, subtitle, about, banner")
        need(all(isinstance(menu.get(k), str) for k in ("title", "subtitle")), "menu: title and subtitle")
        need("banner" not in menu or (isinstance(menu["banner"], list) and 0 < len(menu["banner"]) <= 20
                                      and all(isinstance(x, str) and len(x) <= 120 for x in menu["banner"])),
             "menu.banner: up to 20 lines of up to 120 characters")
    for e in m.get("entries", []):
        need(isinstance(e, dict) and set(e) <= {"menus", "name", "desc", "path"}, "entries: menus, name, desc, path")
        need(isinstance(e.get("menus"), dict) and set(e["menus"]) == {m["id"]} and type(e["menus"][m["id"]]) is int,
             "entries.menus: only this add-on's own list")
        need(isinstance(e.get("name"), str) and LOCAL_PATH_RE.match(str(e.get("path", ""))), "entries: name and path")
    need(not m.get("entries") or menu is not None, "entries need a menu")
    src = m.get("source")
    need(isinstance(src, dict) and src.get("type") in ("git", "bundle"), "source: git or bundle")
    if src["type"] == "git":
        need(set(src) <= {"type", "repo", "branch", "pin", "adapt"}, "source: type, repo, branch, pin, adapt")
        need(GIT_URL_RE.match(str(src.get("repo", ""))), "source.repo: an https:// git URL")
        need(SHA_RE.match(str(src.get("pin", ""))), "source.pin: the full hash of a commit (required)")
        need("adapt" not in src or (re.match(r"^adapt_[a-z_]+\.py$", str(src["adapt"]))
                                    and (CHECKOUT / "irate_box" / "library" / src["adapt"]).is_file()),
             "source.adapt: an adapt script of the hub's own (irate_box/library/adapt_*.py)")
    else:
        need(set(src) <= {"type", "repo", "workflow", "branch", "pattern"}, "source: type, repo, workflow, branch, pattern")
        need(REPO_RE.match(str(src.get("repo", ""))), "source.repo: OWNER/REPO")
        need(re.match(r"^[A-Za-z0-9_.-]+\.ya?ml$", str(src.get("workflow", ""))), "source.workflow: a file name")
    need(isinstance(m.get("needs"), str) and LOCAL_PATH_RE.match(m["needs"]) and m["needs"], "needs: a file inside the add-on")
    addon = m.get("addon")
    need(isinstance(addon, dict) and set(addon) <= {"title", "summary", "consent"}
         and all(isinstance(addon.get(k), str) and addon[k].strip() for k in ("title", "summary", "consent")),
         "addon: title, summary and consent")
    caps = m.get("capabilities", {})
    need(isinstance(caps, dict) and set(caps) <= {"connect", "storage"}, "capabilities: connect, storage")
    need(isinstance(caps.get("connect", []), list) and len(caps.get("connect", [])) <= 8
         and all(isinstance(c, str) and CONNECT_RE.match(c) for c in caps.get("connect", [])),
         "capabilities.connect: up to 8 of ws://, wss://, http:// or https:// with a host or {box}")
    need(type(caps.get("storage", False)) is bool, "capabilities.storage: true or false")


def local(m):
    """A checked local manifest in the full shape the rest of the code reads."""
    i = m["id"]
    base = f"/app.html#/addons/{i}/"
    full = {"id": i, "order": m["order"], "local": True,
            "tile": {"icon": m["tile"]["icon"], "name": m["tile"]["name"], "desc": m["tile"]["desc"],
                     "href": f"/{i}.html" if m.get("menu") else base + m["tile"].get("path", ""),
                     "new_tab": not m.get("menu")},
            "status": {"path": f"/addons/{i}/", "name": m["tile"]["name"], "local_dir": str(ADDONS / i)},
            "install": {"dir": i, "needs": m["needs"], "title": m["addon"]["title"]},
            "source": dict(m["source"]), "addon": dict(m["addon"]),
            "capabilities": {"connect": list(m.get("capabilities", {}).get("connect", [])),
                             "storage": bool(m.get("capabilities", {}).get("storage", False))}}
    if m.get("menu"):
        full["menu"] = dict(m["menu"])
    if m.get("entries"):
        full["entries"] = [{"menus": dict(e["menus"]), "name": e["name"], "desc": e.get("desc", ""),
                            "href": base + e.get("path", "")} for e in m["entries"]]
    return full


def load_local(folder=None, builtin=None):
    """([full manifest], {file name: why it was left out}): the local add-ons. A bad one is
    left out and said, never fatal: the hub wrote it, perhaps from a pasted manifest."""
    folder = Path(folder or LOCAL_D)
    builtin_ids = {m["id"] for m in (builtin if builtin is not None else load())}
    out, errors, seen = [], {}, set()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            if path.is_symlink() or path.stat().st_size > 64 << 10:
                raise ManifestError(f"{path.name}: a link, or over 64 KB")
            m = json.loads(path.read_text(encoding="utf-8"))
            check_local(m, path.name, builtin_ids)
            if m["id"] in seen or path.stem != m["id"]:
                raise ManifestError(f"{path.name}: the file is named for its id, once")
            seen.add(m["id"])
            out.append(local(m))
        except (ManifestError, ValueError, OSError) as exc:
            errors[path.name] = str(exc)
    return out, errors


def load_all():
    """The built-in manifests and the local add-ons', sorted by order."""
    builtin = load()
    extra, _ = load_local(builtin=builtin)
    return sorted(builtin + extra, key=lambda m: (m["order"], m["id"]))


def catalogue():
    """{id: raw local manifest} for the add-ons the hub offers (addons/ in this code)."""
    out = {}
    builtin_ids = {m["id"] for m in load()}
    for path in sorted(CATALOGUE.glob("*.json")) if CATALOGUE.is_dir() else []:
        m = json.loads(path.read_text(encoding="utf-8"))
        check_local(m, f"addons/{path.name}", builtin_ids)
        out[m["id"]] = m
    return out


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
    """{id: manifest} for the add-ons install.sh adds and removes (an "addon" part with its
    --with-* option). The local add-ons, which the hub adds itself, are not among them."""
    return {m["id"]: m for m in (manifests if manifests is not None else load()) if m.get("addon") and not m.get("local")}


def installable(manifests=None):
    """{id: manifest} for apps the librarian and the root helper install."""
    return {m["id"]: m for m in (manifests or load()) if m.get("install")}


def install_dir(m):
    return ADDONS / m["install"]["dir"] if m.get("local") else SHARE / m["install"]["dir"]


def controllable_units(manifests=None):
    return [m["status"]["unit"] for m in (manifests or load())
            if m.get("status", {}).get("control")]
