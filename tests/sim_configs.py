# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""meshtasticd's bin/config.d for the pinout map (library/firmware.py sync_configs), offline: a
small source package made like Meshtastic's (a zip whose first member, deflated, is the
.tar.xz), the order the sources are tried in, and what is said when none works. The git and
streamed paths need the network: they were checked against v2.8.1.8e6a88d (the same 71 files
both ways). python3 tests/sim_configs.py"""
import io, json, os, sys, tarfile, tempfile, zipfile
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="configs-")); (T / "library").mkdir()
os.environ.update(HUB_STATE_DIR=str(T), HUB_GIT_ROOT=str(T / "git"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
real_git = None
from irate_box.library import firmware, librarian  # noqa: E402
real_git = librarian._git
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
V = "9.9.9.abcdef0"
rel = {"version": V, "tag": "v" + V, "source": {"name": f"meshtasticd-{V}-src.zip", "url": "http://unused.invalid/x", "size": 1}}
def package(path, configs):
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:xz") as tf:
        def add(name, data=None):
            ti = tarfile.TarInfo(name)
            if data is None: ti.type = tarfile.DIRTYPE; tf.addfile(ti)
            else: ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
        add("meshtasticd"); add("meshtasticd/README.md", b"x" * 5000); add("meshtasticd/bin")
        add("meshtasticd/bin/config.d")
        for rel_path, text in configs.items():
            if "/" in rel_path: add("meshtasticd/bin/config.d/" + rel_path.rsplit("/", 1)[0])
            add("meshtasticd/bin/config.d/" + rel_path, text.encode())
        add("meshtasticd/bin/device-install.sh", b"#!/bin/sh\n")
        add("meshtasticd/src"); add("meshtasticd/src/main.cpp", b"int main(){}" * 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"meshtasticd_{V}.tar.xz", tar.getvalue()); z.writestr(f"meshtasticd_{V}.dsc", "dsc")
held = firmware.ROOT / V / rel["source"]["name"]
package(held, {"lora-a.yaml": "Lora:\n  CS: 8\n", "OpenWRT/one.yaml": "Lora:\n  CS: 7\n", "display.yml": "Display: x\n"})
calls = []
def git(*a, **k):
    calls.append(a); raise librarian.LibrarianError("git is not reachable (simulated)")
librarian._git = git
r = firmware.sync_configs(rel, log=lambda *a: None)
d = json.loads(firmware.CONFIGS.read_text())
check("from the source package held on the box", "held on this box" in r and d["from"].endswith("held on this box"), r)
check("  git not even tried", not calls, calls)
check("  every yaml, with its path below config.d", [f["path"] for f in d["files"]] == ["OpenWRT/one.yaml", "display.yml", "lora-a.yaml"], d["files"])
check("  the texts as in the package", d["files"][2]["text"] == "Lora:\n  CS: 8\n")
check("  stops at the folder's end (nothing from bin/ or src/ after it)", all("device-install" not in f["path"] for f in d["files"]))
r = firmware.sync_configs(rel, log=lambda *a: None)
check("the same release again: not fetched", "already held" in r, r)
firmware.CONFIGS.unlink(); held.unlink()
rel2 = dict(rel, source=None)
try:
    firmware.sync_configs(rel2, log=lambda *a: None); check("nothing works: an error", False)
except librarian.LibrarianError as exc:
    check(f"nothing works: says what was tried ({exc})", "git" in str(exc) and calls)
bad = firmware.ROOT / V / "bad-src.zip"; bad.write_bytes(b"not a zip at all")
try:
    firmware._configs_from_package(lambda off: firmware._seek(open(bad, "rb"), off)); check("a broken package: refused", False)
except librarian.LibrarianError as exc:
    check(f"a broken package: refused ({exc})", "not a zip" in str(exc))

# From a mirror of meshtastic/firmware on the box (step 23): read with git at the tag, no network,
# tried before everything else.
import subprocess
from unittest import mock
from irate_box.library import mirrors  # noqa: E402
librarian._git = real_git
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
work = T / "work"
subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
(work / "bin" / "config.d" / "OpenWRT").mkdir(parents=True)
(work / "bin" / "config.d" / "lora-a.yaml").write_text("Lora:\n  CS: 8\n")
(work / "bin" / "config.d" / "OpenWRT" / "one.yaml").write_text("Lora:\n  CS: 7\n")
(work / "bin" / "config.d" / "notes.txt").write_text("not a config\n")
os.symlink("lora-a.yaml", work / "bin" / "config.d" / "link.yaml")
(work / "bin" / "other.yaml").write_text("outside\n")
subprocess.run(["git", "add", "-A"], cwd=work, check=True)
subprocess.run(["git", "commit", "-qm", "c"], cwd=work, env=ENV, check=True)
subprocess.run(["git", "tag", rel["tag"]], cwd=work, check=True)
fw_mirror = {"name": "meshtastic-firmware", "area": "public", "upstream": "https://github.com/Meshtastic/firmware"}
bare = mirrors.repo_path(fw_mirror)
bare.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
other = {"name": "something-else", "area": "public", "upstream": "https://github.com/meshtastic/protobufs"}
with mock.patch.object(mirrors, "load", return_value=[other, fw_mirror]):
    check("the firmware's source mirror is found by its upstream (any case)", firmware.source_mirror() == fw_mirror)
    check("  and named in the Firmware pane's data", firmware.snapshot()["source"] == {"name": "meshtastic-firmware", "area": "public"})
    if firmware.CONFIGS.exists(): firmware.CONFIGS.unlink()
    r = firmware.sync_configs(rel, log=lambda *a: None)
    d = json.loads(firmware.CONFIGS.read_text())
    check("config.d from the mirror, first", "the mirror meshtastic-firmware.git" in r and d["from"].startswith("the mirror"), r)
    check("  the yaml files below config.d, not the symlink, the text file or one outside",
          [f["path"] for f in d["files"]] == ["OpenWRT/one.yaml", "lora-a.yaml"], d["files"])
    check("  their text", d["files"][1]["text"] == "Lora:\n  CS: 8\n")
    firmware.CONFIGS.unlink()
    calls.clear(); librarian._git = lambda *a, **k: git(*a) if a[0] == "clone" else (calls.append(a), real_git(*a, **k))[1]
    r2 = dict(rel, tag="v9.9.8.0000000", version="9.9.8.0000000", source=None)
    try:
        firmware.sync_configs(r2, log=lambda *a: None)
    except librarian.LibrarianError as exc:
        check("a tag the mirror lacks: falls through to the next source, and says why", "does not hold v9.9.8.0000000" in str(exc)
              and any(c[0] == "clone" for c in calls), str(exc))
    librarian._git = real_git
with mock.patch.object(mirrors, "load", return_value=[other]):
    check("no mirror of it: None", firmware.source_mirror() is None and firmware.snapshot()["source"] is None)

# The build cache is the builds' now: carried with flash files off.
with mock.patch.object(firmware, "_releases", return_value=[dict(rel, channel="alpha", deps={"url": "x", "size": 1, "name": "d.zip"})]), \
     mock.patch.object(firmware, "sync_configs", return_value="configs: held"), \
     mock.patch.object(firmware, "_carry_cache", return_value={"version": V, "mode": "native", "bytes": 215 << 20}) as carry:
    firmware.set_settings(enabled=False, cache="native")
    out = firmware.sync(log=lambda *a: None)
    check("flash files off, cache on: the cache is still carried", carry.called and "build cache 215 MB" in out
          and firmware.status()["cache"]["mode"] == "native", out)
# A download cut off part way resumes with a range request (librarian._download, step 33b).
class Resp(io.BytesIO):
    def __init__(self, data, status):
        super().__init__(data); self.status = status; self.headers = {"Content-Length": str(len(data))}
whole = bytes(range(256)) * 40
asked = []
def fake_open(url, auth=None, method="GET", timeout=60, extra=None):
    asked.append(extra)
    start = int(extra["Range"][6:-1]) if extra else 0
    return Resp(whole[start:], 206) if extra and honour else Resp(whole, 200)
real_open, librarian._open = librarian._open, fake_open
part = T / "part.bin"
for honour, why in ((True, "a server that allows ranges: the rest appended"), (False, "one that ignores the range (200): started again")):
    part.write_bytes(whole[:3000]); asked.clear()
    librarian._download("http://x/y", part, expected=len(whole), resume=True)
    check(f"resume: {why}", part.read_bytes() == whole and asked == [{"Range": "bytes=3000-"}], (part.stat().st_size, asked))
part.write_bytes(b"z" * 99999); asked.clear()
librarian._download("http://x/y", part, expected=len(whole), resume=True)
check("resume: a part bigger than the file is not trusted", part.read_bytes() == whole and asked == [None], asked)
part.write_bytes(whole[:3000]); asked.clear()
librarian._download("http://x/y", part, expected=len(whole))
check("without resume: never a range", part.read_bytes() == whole and asked == [None], asked)
librarian._open = real_open

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
