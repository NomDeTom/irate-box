# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Local add-ons (plans/no-root-addons-plan), offline: what a local manifest may say, a bad one
left out without stopping the rest, a real bundle installed and rolled back as the hub user, the
hub's add / paste / remove (in-process, the fetch stood in), tiles off until switched, and the
add-on server's generated maps. python3 tests/sim_local_addons.py"""
import io, json, os, sys, tempfile, zipfile
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="local-addons-"))
for d in ("library", "control", "apps.d", "addons"):
    (T / d).mkdir()
os.environ["HUB_STATE_DIR"] = str(T)
os.environ["HUB_ETC_DIR"] = str(T)
REPO = Path(__file__).resolve().parents[1]
# A catalogue of its own, which the test changes as a hub update would (step 19).
CAT = T / "catalogue"
CAT.mkdir()
(CAT / "eliza.json").write_text((REPO / "addons" / "eliza.json").read_text())
os.environ["HUB_ADDON_CATALOGUE"] = str(CAT)
sys.path.insert(0, str(REPO))
from irate_box.hub import access, manifests as M  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def refused(name, m, why):
    try:
        M.check_local(m, "t", {"draw", "tools"})
        check(name, False, "accepted")
    except M.ManifestError as exc:
        check(name, why in str(exc), str(exc))

ELIZA = json.loads((REPO / "addons" / "eliza.json").read_text())
good = {"id": "demo", "order": 99, "tile": {"icon": "🧪", "name": "Demo", "desc": "d", "path": "index.html"},
        "source": {"type": "git", "repo": "https://example.org/x/demo", "pin": "a" * 40}, "needs": "index.html",
        "addon": {"title": "Demo", "summary": "s", "consent": "c"}, "capabilities": {"connect": ["ws://{box}/mqtt"], "storage": True}}
M.check_local(good, "t", {"draw"}); check("a plain local manifest passes", True)
M.check_local(ELIZA, "t", {"draw"}); check("ELIZA's catalogue entry passes", True)
builtin_ids = {m["id"] for m in M.load()}
for f in sorted((REPO / "addons").glob("*.json")) + sorted((REPO / "addons" / "templates").glob("*.json")):
    try:
        M.check_local(json.loads(f.read_text()), f.name, builtin_ids); check(f"addons/{f.relative_to(REPO / 'addons')} passes", True)
    except M.ManifestError as exc:
        check(f"addons/{f.relative_to(REPO / 'addons')} passes", False, str(exc))
refused("refuses keys a local add-on may not have", dict(good, install={"dir": "x"}), "not allowed")
refused("refuses a built-in's id", dict(good, id="draw"), "the hub's own")
refused("refuses an id the hub's pages use", dict(good, id="admin"), "the hub's own")
refused("refuses a git source with no pin", dict(good, source={"type": "git", "repo": "https://example.org/x/y"}), "pin")
refused("refuses an adapt script not of the hub's", dict(good, source=dict(good["source"], adapt="adapt_evil.py")), "adapt")
refused("refuses a path out of the add-on", dict(good, tile=dict(good["tile"], path="../admin")), "tile.path")
refused("refuses an absolute href as a path", dict(good, tile=dict(good["tile"], path="http://evil/")), "tile.path")
refused("refuses connect to javascript:", dict(good, capabilities={"connect": ["javascript:alert(1)"]}), "connect")
refused("refuses a quote in connect", dict(good, capabilities={"connect": ["ws://a\";/"]}), "connect")
refused("refuses no consent", dict(good, addon={"title": "t", "summary": "s"}), "consent")
refused("refuses entries on another list", dict(good, menu={"title": "t", "subtitle": "s"},
        entries=[{"menus": {"tools": 1}, "name": "n", "path": "x.html"}]), "own list")

full = M.local(good)
check("local(): tile into the add-on, through app.html", full["tile"]["href"] == "/app.html#/addons/demo/index.html", full["tile"])
check("local(): status and install in the add-on folder", full["status"]["local_dir"] == str(T / "addons" / "demo")
      and M.install_dir(full) == T / "addons" / "demo")
check("local(): not one of install.sh's add-ons", "demo" not in M.addons([full]))

(T / "apps.d" / "demo.json").write_text(json.dumps(good))
(T / "apps.d" / "broken.json").write_text('{"id": "broken", "order": 1}')
(T / "apps.d" / "named-wrong.json").write_text(json.dumps(dict(good, id="other")))
loaded, errors = M.load_local()
check("a bad local manifest is left out, the good one loads", [m["id"] for m in loaded] == ["demo"], loaded)
check("and each left-out one says why", set(errors) == {"broken.json", "named-wrong.json"}, errors)
for f in ("broken.json", "named-wrong.json"):
    (T / "apps.d" / f).unlink()

# --- the librarian installs a real bundle as the hub user
from irate_box.library import librarian as L  # noqa: E402
L.reload_apps()
def bundle(commit, files, link=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("irate-box-bundle.json", json.dumps({"app": "demo", "commit": commit, "ref": "pinned", "built": "2026-10-06"}))
        for n, t in files.items():
            zf.writestr(n, t)
        if link:
            info = zipfile.ZipInfo("evil"); info.external_attr = 0o120777 << 16; zf.writestr(info, "/etc/passwd")
    p = T / "library" / f"demo-{commit[:4]}.zip"; p.write_bytes(buf.getvalue()); return p
L.install_local("demo", bundle("1" * 40, {"index.html": "one"}))
d = T / "addons" / "demo"
check("installed into its folder", (d / "index.html").read_text() == "one" and oct((d / "index.html").stat().st_mode & 0o777) == "0o644")
L.install_local("demo", bundle("2" * 40, {"index.html": "two"}))
check("a second install keeps the first beside it", (d / "index.html").read_text() == "two" and (T / "addons" / ".demo.prev" / "index.html").read_text() == "one")
L.rollback_local("demo")
check("roll back swaps them", (d / "index.html").read_text() == "one" and (T / "addons" / ".demo.prev" / "index.html").read_text() == "two")
try:
    L.install_local("demo", bundle("3" * 40, {"index.html": "x"}, link=True)); check("a bundle with a link is refused", False)
except L.LibrarianError as exc:
    check("a bundle with a link is refused", "symlink" in str(exc), str(exc))
check("and what was installed stays", (d / "index.html").read_text() == "one")

# --- the hub: add from the catalogue, paste, remove (the fetch stood in)
from irate_box.hub import server as S  # noqa: E402
started = []
S.library_start = lambda action, fn: started.append(action) or True
code, body = S.local_addons_action({"action": "add", "id": "eliza"})
check("add without agreeing is refused", code == 400 and "agree" in body["error"], body)
code, body = S.local_addons_action({"action": "add", "id": "eliza", "agree": True})
check("add from the catalogue: manifest written, fetch started", code == 202 and (T / "apps.d" / "eliza.json").exists() and started, body)
check("its consent recorded", json.loads((T / "addons-consent.json").read_text())["eliza"]["how"] == "catalogue")
check("its librarian source follows the pin", any(s["name"] == "eliza" and s["follow"] == "pinned" for s in L.load_config()["sources"]))
check("the hub sees it at once (menu page, status)", "/eliza.html" in S.MENU_PAGES and any(s.get("local_dir", "").endswith("/eliza") for s in S.SERVICES))
every_tile = [m["id"] for m in M.load() if m.get("tile")]
shown = S.render_tiles("apps", S.hidden_apps()) + S.render_tiles("box", S.hidden_apps())
check("every built-in tile still shows (no switch means never hidden)",
      all(m["tile"].get("href", "") in shown or m["tile"].get("widget") for m in M.load() if m.get("tile") and m["id"] not in S.access.ROUTED)
      and all(f'id="{w}-card"' in shown or w == "qr" for w in ("people", "system")), [m["id"] for m in M.load() if m.get("tile") and m["tile"].get("href") and m["tile"]["href"] not in shown])
check("its tile is off until switched on", "eliza" in S.hidden_apps() and 'href="/eliza.html"' not in S.render_tiles("apps", S.hidden_apps()))
(T / "control" / "access.json").write_text(json.dumps({"eliza": "public"}))
check("switched public, its tile shows", 'href="/eliza.html"' in S.render_tiles("apps", S.hidden_apps()))
code, body = S.local_addons_action({"action": "add", "id": "eliza", "agree": True})
check("added twice is refused", code == 409, body)
code, body = S.local_addons_action({"action": "paste", "manifest": dict(good, id="pasted")})
check("a paste without the acknowledgement is refused", code == 400 and "warning" in body["error"], body)
code, body = S.local_addons_action({"action": "paste", "manifest": dict(good, id="draw"),
                                    "understood": "I understand this runs someone else's code on this box's address"})
check("a pasted manifest is checked like any other", code == 400 and "the hub's own" in body["error"], body)
code, body = S.local_addons_action({"action": "paste", "manifest": dict(good, id="pasted"),
                                    "understood": "I understand this runs someone else's code on this box's address"})
check("a pasted manifest, acknowledged, is added", code == 202 and json.loads((T / "addons-consent.json").read_text())["pasted"]["how"] == "pasted", body)
snap = S.local_addons_snapshot()
check("the snapshot lists the catalogue and what is added", any(c["id"] == "eliza" and c["added"] for c in snap["catalogue"])
      and {a["id"] for a in snap["added"]} >= {"eliza", "pasted", "demo"}, snap["added"])
(T / "addons" / "pasted").mkdir()
code, body = S.local_addons_action({"action": "remove", "id": "pasted"})
check("remove: manifest, files and consent gone", code == 200 and not (T / "apps.d" / "pasted.json").exists()
      and not (T / "addons" / "pasted").exists() and "pasted" not in json.loads((T / "addons-consent.json").read_text()), body)
code, body = S.local_addons_action({"action": "remove", "id": "../control"})
check("remove refuses anything but an added id", code == 400, body)

# --- catalogue updates reach an added add-on (next-work-plan step 19)
def sync():
    S._local_stamp["at"] = None      # as after a hub update: look again
    S.refresh_manifests()
    return json.loads((T / "addons-catalogue.json").read_text())
copy = lambda: json.loads((T / "apps.d" / "eliza.json").read_text())
entry = json.loads((CAT / "eliza.json").read_text())
check("before any change: nothing to do", sync().get("eliza", {}).get("status") in (None, "current"))
entry["tile"]["desc"] = "A description the catalogue changed"
(CAT / "eliza.json").write_text(json.dumps(entry))
st = sync()
check("a tile change reaches the added copy", copy()["tile"]["desc"] == "A description the catalogue changed" and st["eliza"]["status"] == "updated"
      and st["eliza"]["changed"] == ["tile"], st)
started.clear()
entry["source"]["pin"] = "b" * 40
(CAT / "eliza.json").write_text(json.dumps(entry))
st = sync()
check("a moved pin reaches it, and is fetched at once", copy()["source"]["pin"] == "b" * 40 and started == ["update"]
      and L.APPS["eliza"]["source"]["pin"] == "b" * 40, (copy()["source"], started))
entry["capabilities"] = {"connect": ["wss://example.org"], "storage": False}
(CAT / "eliza.json").write_text(json.dumps(entry))
st = sync()
check("a new connect waits for the owner; the agreed copy keeps running", st["eliza"]["status"] == "held"
      and copy()["capabilities"]["connect"] == [] and any("wss://example.org" in h for h in st["eliza"]["held"]), st)
snap = S.local_addons_snapshot()
check("/admin is told it is held", next(a for a in snap["added"] if a["id"] == "eliza")["catalogue"]["status"] == "held")
code, body = S.local_addons_action({"action": "accept", "id": "eliza"})
check("accept without agreeing is refused", code == 400, body)
code, body = S.local_addons_action({"action": "accept", "id": "eliza", "agree": True})
st = sync()
check("accepted: the new version, a new consent, and current again", code == 200 and copy()["capabilities"]["connect"] == ["wss://example.org"]
      and st["eliza"]["status"] == "current", (code, body, st.get("eliza")))
code, body = S.local_addons_action({"action": "paste", "manifest": dict(good, id="mine"),
                                    "understood": "I understand this runs someone else's code on this box's address"})
(CAT / "mine.json").write_text(json.dumps(dict(good, id="mine", tile=dict(good["tile"], desc="from the catalogue"))))
st = sync()
check("a pasted add-on is never touched, even when the catalogue later has one by its name",
      json.loads((T / "apps.d" / "mine.json").read_text())["tile"]["desc"] == "d" and "mine" not in st, st.get("mine"))
(CAT / "mine.json").unlink()
(CAT / "eliza.json").unlink()
st = sync()
check("gone from the catalogue: kept, and said", (T / "apps.d" / "eliza.json").exists() and st["eliza"]["status"] == "gone", st)
(CAT / "eliza.json").write_text(json.dumps(entry))

# --- the add-on server's maps
loc = [m for m in M.load_local()[0]]
conf = access.addon_nginx_conf(access.clean({"eliza": "private"}), loc)
check("maps: an unknown id is off", 'default "1";' in conf.split("map $irate_box_addon $irate_box_addon_off {")[1].split("}")[0])
check("maps: never switched is off, private asks the login", '\tdemo "1";' in conf and '\teliza "";' in conf and '\teliza "Irate-Box";' in conf, conf)
check("maps: connect from the manifest, {box} as the hub's host", "connect-src 'self' ws://$host/mqtt" in conf, conf)
routes = access.addon_caddy_routes(access.clean({"eliza": "public"}), loc, "H")
check("Caddy routes: off ones left out, the rest 404", "handle /eliza/*" in routes and "handle /demo/*" not in routes and routes.rstrip().endswith("}"))

# The MQTT explorer (step 13): the hub's own page, cut out of a clone of the hub at the pin.
import shutil  # noqa: E402
from irate_box.library import adapt_mqtt_explorer as AX  # noqa: E402
REPO = Path(__file__).resolve().parents[1]
tree = T / "mx" / "tree"
shutil.copytree(REPO / "extras", tree / "extras"); (tree / "LICENSES").mkdir(); shutil.copy(REPO / "LICENSES" / "MIT.txt", tree / "LICENSES")
(tree / "install.sh").write_text("#!/bin/sh\n"); (tree / "irate_box").mkdir(); (tree / "irate_box" / "x.py").write_text("")
AX.adapt(tree)
check("the explorer: only its page, at the top, with its licence", sorted(p.name for p in tree.iterdir()) == ["LICENSE", "explorer.css", "explorer.js", "index.html"],
      sorted(p.name for p in tree.iterdir()))
AX.adapt(tree)
check("  twice changes nothing", len(list(tree.iterdir())) == 4 and not (T / "mx" / ".tree.keep").exists())
bare = T / "mx" / "old"; bare.mkdir(); (bare / "README.md").write_text("x")
try:
    AX.adapt(bare); check("a commit without the explorer: refused", False)
except AX.AdaptError as exc:
    check("a commit without the explorer: refused", "not a commit with the explorer" in str(exc), str(exc))
js = (REPO / "extras/mqtt-explorer/explorer.js").read_text()
check("the page reaches nothing but the broker", "fetch(" not in js and "XMLHttpRequest" not in js and js.count("new WebSocket(") == 1
      and "src=\"http" not in (REPO / "extras/mqtt-explorer/index.html").read_text())

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
