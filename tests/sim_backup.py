# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Backups and a new box as offers with sizes (item 34; hub/backup.py, root's kit, stick and image), offline:
the state sorted into settings, data and what is fetched again (the Lyra's own layout, 2026-10-09, in small);
each level's download holding just its part; the sizes on offer; the kit's choices checked and refused over
budget; the full image written to a "stick" in parts and readable back. python3 tests/sim_backup.py"""
import gzip, io, json, os, sys, tarfile, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="backup-"))
S = T / "state"
os.environ.update(HUB_STATE_DIR=str(S))
sys.path.insert(0, str(REPO))
from irate_box.hub import backup as B  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def put(rel, n=10):
    p = S / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)
# The Lyra's state, in small (its sizes, 2026-10-09: ci 13.7 GB, firmware 0.7 GB, zim 0.36 GB, a few MB made).
for rel in ("settings.json", "accounts.json", "tiles.json", "install-options", "tailscale.want", "admin-seen.key",
            "apps.d/x.json", "addons/eliza/conf.json", "factory-targets/a.json", "library/sources.json", "library/mirrors.json",
            "mesh/channels.json", "git/cgitrc-public", "git/mirror-urls.json"):
    put(rel, 100)
for rel in ("notes/index.md", "store/saves/a.json", "drop/f.data", "board.json", "messages.json", "service-history.json",
            "mesh/nodes.json", "git/public/mine.git/HEAD", "git/private/secret.git/HEAD", "library/status.json", "security-imports/r.json"):
    put(rel, 1000)
for rel in ("ci/home/big", "ci/runs/r", "firmware/2.8/fw.bin", "zim/book.zim", "crashwatch/snap.log", "control/x.json",
            "kits/kit.json", "source/irate-box-source.tar.gz", "library/archive/old.zim", "library/api-cache/x", "library/github-token",
            "library/tmp/part", "git/public/firmware.git/HEAD", "git/public/firmware--protobufs.git/HEAD", "git/.incoming/x", "helper-busy.json", "notes/x.tmp"):
    put(rel, 100000)
put(".local/state/syncthing/key.pem", 50)
(S / "library" / "mirrors.json").write_text(json.dumps({"mirrors": [{"name": "firmware", "area": "public"}]}))
SET = 1300 + (S / "library" / "mirrors.json").stat().st_size   # 13 settings files of 100 bytes, and the mirrors' settings
gm = B.git_mirrors(S)
check("the git mirrors read from the mirrors' settings, their submodules with them", gm == ("git/public/firmware.git", "git/public/firmware--"), gm)
check("  a submodule mirror is fetched again, not backed up", B.kind_of("git/public/firmware--protobufs.git/HEAD", gm) == "refetched")
k = lambda rel: B.kind_of(rel, gm)  # noqa: E731
check("settings: the hub's own files, the apps' and add-ons', the sources, the mesh's channels, cgit's",
      all(k(r) == "settings" for r in ("settings.json", "install-options", "tailscale.want", "apps.d/x.json", "addons/eliza/conf.json",
                                       "library/sources.json", "mesh/channels.json", "git/cgitrc-public", "factory-targets/a.json")))
check("data: notes, saves, drops, the board and shoutbox, the heard nodes, the box's own repositories",
      all(k(r) == "data" for r in ("notes/index.md", "store/saves/a.json", "drop/f.data", "board.json", "messages.json",
                                   "mesh/nodes.json", "git/public/mine.git/HEAD", "git/private/secret.git/HEAD", "security-imports/r.json")))
check("never: builds, the firmware and git mirrors, books, crash evidence, caches, the token, transient files",
      all(k(r) == "refetched" for r in ("ci/home/big", "firmware/2.8/fw.bin", "zim/book.zim", "git/public/firmware.git/HEAD", "crashwatch/snap.log",
                                        "control/x.json", "kits/kit.json", "library/archive/old.zim", "library/api-cache/x", "library/github-token",
                                        "git/.incoming/x", "notes/x.tmp", "helper-busy.json")))
check("  a folder nobody named is data, a new top-level file settings: kept rather than lost", k("newthing/a") == "data" and k("new.json") == "settings")
sums = B.walk(S)
check("the sizes: settings about 1.4 KB, data 11 KB; Syncthing's keys apart; books and firmware measured, builds not walked",
      sums["settings"] == SET and sums["data"] == 11000 and sums["syncthing"] == 50 and sums["books"] == 100000 and sums["firmware"] == 100000, sums)
def members(level, keys=False):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(S, arcname="irate-box-state", filter=B.keep_for(level, keys, gm))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        return {m.name.split("/", 1)[1] for m in tar.getmembers() if m.isfile()}, buf.getbuffer().nbytes
m, n = members("settings")
check("settings only: just the settings (14 files), none of what was made", len(m) == 14 and "settings.json" in m and "notes/index.md" not in m, sorted(m))
m, n = members("data")
check("settings and data: both (25 files), nothing fetched again, no keys; small (the Lyra's would have been 14 GB)",
      len(m) == 25 and "git/public/mine.git/HEAD" in m and not any(x.startswith(("ci/", "firmware/", "zim/", "git/public/firmware.git", "crashwatch/")) for x in m)
      and ".local/state/syncthing/key.pem" not in m and n < 5000, (len(m), n))
m, _ = members("data", keys=True)
check("  with Syncthing's keys when asked", ".local/state/syncthing/key.pem" in m)
(S / "zim").joinpath("book.zim").rename(S / "zim" / "book.zim")
p = B.plan(S, S / "zim", {"kits": {"build": {"cached": {"bytes": 25 << 20}}, "debug": {"cached": None}}},
           apps_dir=None, kit_titles={"build": "Building"})
check("the plan: each level's size, books with theirs, a cached toolkit (not one never fetched), repositories with mirrors marked",
      p["levels"] == {"settings": SET, "data": SET + 11000} and p["books"] == [{"name": "book", "size": 100000}]
      and p["kits"] == [{"id": "build", "title": "Building", "size": 25 << 20}]
      and {(r["name"], r["mirror"]) for r in p["repos"]} == {("mine", False), ("firmware", True), ("firmware--protobufs", True), ("secret", False)}
      and p["left_out"] == {"books": 100000, "firmware": 100000} and "used" in p["image"], p)
srv = (REPO / "irate_box/hub/server.py").read_text()
check("the hub: the level from the address, the plan at /admin/backup/plan, no list of its own left", "level=settings" in srv
      and '"/admin/backup/plan"' in srv and "BACKUP_SKIP" not in srv)

# The root's half: the kit's choices checked, and refused over budget before anything is made.
os.environ.update(HUB_STATE_DIR=str(S), HUB_CONTROL_DIR=str(T / "control"))
from irate_box.root import hub_control as H  # noqa: E402
H.STATE, H.ZIM_DIR = S, S / "zim"
for bad, why in (({"books": ["../etc"]}, "a path for a book"), ({"books": ["nosuch"]}, "a book not there"), ({"kits": ["Bad Kit"]}, "a kit id"),
                 ({"repos": ["public/../x"]}, "a repository path"), ({"repos": ["public/nosuch"]}, "a repository not there"),
                 ({"state": "everything"}, "a state level"), ({"budget_mb": -1}, "a budget"), ({"books": "yes"}, "books not a list")):
    try:
        H.kit_choices(bad); check(f"kit refused: {why}", False)
    except ValueError:
        check(f"kit refused: {why}", True)
z, kids, repos, state, budget = H.kit_choices({"books": ["book"], "repos": ["public/mine"], "state": "data", "budget_mb": 100})
check("kit choices read", [x.name for x in z] == ["book.zim"] and [r.name for r in repos] == ["mine.git"] and state == "data" and budget == 100)
H._du = lambda *paths: sum(B.du(p) for p in paths)
try:
    H.offline_kit({"books": ["book"], "state": "data", "budget_mb": 1}); check("over the budget: refused before anything is made", False)
except ValueError as exc:
    check("over the budget: refused before anything is made, the size said", "over the budget of 1 MB" in str(exc)
          and not list((S / "kits").glob(".kit-*")), str(exc))

# The full image onto a "stick": a file standing for the card, written in parts on FAT, joined and read back whole.
from irate_box.root import usbstick as U  # noqa: E402
card = T / "card.img"
card.write_bytes(os.urandom(300_000) + b"\0" * 700_000)
stick = T / "stick"; stick.mkdir()
U.IMAGE_PART = 100_000
U._find = lambda _s, name: {"name": name, "path": "/dev/sdz1", "fstype": "vfat", "mountpoint": str(stick)}
U.shutil.disk_usage = lambda p: type("u", (), {"used": 1000, "free": 10 ** 9, "total": 10 ** 9})()
U.run = lambda *c, **k: type("r", (), {"stdout": "", "returncode": 0, "stderr": ""})()
seen = []
msg = U.export_image("sdz1", lambda d, t: seen.append((d, t)), disk=str(card), stamp="20261009-1500")
parts = sorted((stick / "irate-box" / "images").glob("*.part*"))
whole = gzip.decompress(b"".join(p.read_bytes() for p in parts))
check("the image: in parts on a FAT stick, joined they give back the card byte for byte, with a README", len(parts) >= 3 and whole == card.read_bytes()
      and (stick / "irate-box" / "images" / "irate-box-image-20261009-1500.img.gz.README.txt").exists() and "parts" in msg and seen[-1][0] == 1_000_000, (len(parts), msg))
U._find = lambda _s, name: {"name": name, "path": "/dev/mmcblk0p2", "fstype": "ext4", "mountpoint": str(stick)}
try:
    U.export_image("x", disk="/dev/mmcblk0"); check("the box's own disk as the stick: refused", False)
except ValueError:
    check("the box's own disk as the stick: refused", True)
inst = (REPO / "install.sh").read_text()
check("setup.sh restores the state as the hub, copies repositories it has not got, imports the toolkits checked",
      "runuser -u hub -- tar -xzf \"$here/state-backup.tar.gz\"" in inst and "is there already: left as it is" in inst and "kits import-dir" in inst)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
