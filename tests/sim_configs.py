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
os.environ["HUB_STATE_DIR"] = str(T)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.library import firmware, librarian  # noqa: E402
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
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
