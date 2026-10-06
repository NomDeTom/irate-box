# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Latest and pinned (library/librarian.py), offline: ELIZA's manifest pins a commit; the
librarian records the newest and the pinned one, installs the one the source follows, and,
following the newest, falls back as it says when the newest does not fetch, adapt or check.
Network, cloning and the root helper are stood in for. The real fetch of the pinned commit
from github.com/anthay/ELIZA was checked by hand (librarian app-fetch eliza).
python3 tests/sim_pin.py"""
import os, sys, tempfile
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="pin-")); (T / "library").mkdir()
os.environ["HUB_STATE_DIR"] = str(T)
os.environ["HUB_SHARE_DIR"] = str(T / "share")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# ELIZA is a local add-on (from the catalogue): added as /admin adds it, before the librarian reads its apps.
(T / "apps.d").mkdir()
(T / "apps.d" / "eliza.json").write_text((Path(__file__).resolve().parents[1] / "addons" / "eliza.json").read_text())
from irate_box.library import librarian as L  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

PIN = L.APPS["eliza"]["source"]["pin"]
NEW = "b" * 40
world = {"head": NEW, "bad": set(), "offline": False}
fetched, queued = [], []
def resolve(src):
    if world["offline"]:
        raise L.LibrarianError("cannot reach github.com")
    return {"version": f"git-{world['head']}", "url": src["repo"], "size": 0, "zip": False,
            "commit": world["head"], "label": f"master at {world['head'][:7]}", "auth": None}
def fetch_app(src, cand):
    if world["offline"]:
        raise L.LibrarianError("cannot reach github.com")
    if cand["commit"] in world["bad"]:
        raise L.LibrarianError("adapt_eliza.py failed: src/eliza.html has changed")
    fetched.append(cand["commit"])
    (L.APP_STAGING).mkdir(parents=True, exist_ok=True)
    (L.APP_STAGING / f"{src['name']}.zip").write_bytes(b"zip")
    return L.APP_STAGING / f"{src['name']}.zip", {"commit": cand["commit"]}
def queue(req):
    queued.append(req); return "r1"
def install_local(name, zip_path):  # a local add-on installs itself (no root helper): stood in too
    queued.append({"action": "install-local", "app": name}); return f"{name}: installed"
L.resolve, L.fetch_app, L._queue_root, L.install_local = resolve, fetch_app, queue, install_local

def installed(commit):
    d = L.app_dir("eliza"); d.mkdir(parents=True, exist_ok=True)
    (d / L.BUNDLE_JSON).write_text(f'{{"app": "eliza", "commit": "{commit}", "ref": "x"}}')
def run(mode="update"):
    fetched.clear(); queued.clear()
    return L.update(["eliza"], mode=mode, log=lambda *_: None)["eliza"], L.load_status()["eliza"]

L.add_source(L.default_app_source("eliza"))
src = L.load_config()["sources"][0]
check("a new source follows the pin", src.get("follow") == "pinned", src)

out, st = run("check")
check("check records the newest and the pinned", st["latest"]["version"] == f"git-{NEW}" and st["pinned"]["version"] == f"git-{PIN}", st)
check("check says the newer is not followed", "newer, not followed" in out, out)

out, st = run()
check("following the pin, nothing installed: installs the pin", fetched == [PIN] and queued, (out, fetched))
installed(PIN)
out, st = run()
check("the pin installed: up to date, newer reported", out.startswith("up to date") and "newer, not followed" in out and not fetched, out)

world["offline"] = True
out, st = run()
check("offline, pin installed: up to date, the newest unknown", out.startswith("up to date") and "not known" in out and "error" not in st, (out, st.get("error")))
world["offline"] = False

L.add_source(dict(src, follow="latest"))
out, st = run()
check("following latest: installs the newest", fetched == [NEW] and queued, (out, fetched))
installed(NEW)

NEWER = "c" * 40
world["head"], world["bad"] = NEWER, {NEWER}
out, st = run()
check("the newest fails, a build installed: keeps it", "keeping bbbbbbb" in out and not queued, out)
check("the failure is recorded", st.get("latest_failed", {}).get("version") == f"git-{NEWER}", st)

import shutil
shutil.rmtree(L.app_dir("eliza"))
out, st = run()
check("the newest fails, nothing installed: installs the pin", fetched == [PIN] and queued and "failed" in out, (out, fetched))

world["bad"] = set()
out, st = run()
check("the newest works again: installs it, failure cleared", fetched == [NEWER] and "latest_failed" not in st, (out, st))

try:
    L.add_source({"name": "tools", "kind": "app", "type": "git", "follow": "pinned"})
    check("a source without a pin cannot follow pinned", False)
except L.LibrarianError:
    check("a source without a pin cannot follow pinned", True)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
