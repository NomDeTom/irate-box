# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits by USB stick (next-work plan step 32, toolkits-plan §4.3), offline: the export carries
the kit's .debs and the signed indexes that list them; the import accepts a .deb only when a signed
InRelease (its signature checked with the box's keys), the Packages file it lists, and the .deb's
own hash all agree. gpgv and dpkg-deb are stood in (the real chain, with Debian's keys, was tried
on the Lyra: next-work-plan, step 32). python3 tests/sim_kits_usb.py"""
import hashlib, json, os, shutil, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="kitsusb-"))
(T / "defs").mkdir(); (T / "lists").mkdir(); (T / "keys").mkdir()
(T / "keys" / "debian-archive-keyring.gpg").write_text("good key")
(T / "keys" / "debian-archive-removed-keys.gpg").write_text("removed key")
(T / "defs" / "small.json").write_text(json.dumps({"id": "small", "title": "Small", "summary": "s", "consent": "c", "packages": ["gdb"]}))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_KITS_ROOT=str(T / "cache"), HUB_KITS_DEFS=str(T / "defs"),
                  HUB_APT_LISTS=str(T / "lists"), HUB_APT_KEYRINGS=str(T / "keys"))
sys.path.insert(0, str(REPO))
from irate_box.root import kits  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
class R:
    def __init__(self, out="", rc=0): self.stdout, self.stderr, self.returncode = out, "", rc
gpgv_calls = []
def fake(cmd, timeout=0, check=True):
    name = Path(cmd[0]).name
    if name == "dpkg" and cmd[1:] == ["--print-architecture"]:
        return R("armhf\n")
    if name == "dpkg-deb":
        d = json.loads(Path(cmd[2]).read_text())
        return R("".join(f"{k}: {d[k]}\n" for k in (cmd[3:] or list(d)) if k in d))
    if name == "gpgv":
        gpgv_calls.append(cmd)
        out = Path(cmd[cmd.index("--output") + 1]); src = Path(cmd[-1])
        text = src.read_text()
        good = "SIGNED BY: good key" in text and str(T / "keys" / "debian-archive-keyring.gpg") in cmd
        if good:
            out.write_text(text.replace("SIGNED BY: good key\n", ""))
        return R("", 0 if good else 2)
    raise AssertionError(cmd)
kits.run = fake

# A box's cache: one kit, two .debs, and apt's lists vouching for them.
kits.POOL.mkdir(parents=True); kits.MANIFESTS.mkdir(parents=True)
pkgs = []
for n, v in (("gdb", "16.3-1"), ("libgdb", "1:16.3-1")):
    f = f"{n}_{v.replace(':', '%3a')}_armhf.deb"  # as apt saves an epoch
    (kits.POOL / f).write_text(json.dumps({"Package": n, "Version": v, "Architecture": "armhf"}))
    pkgs.append({"name": n, "version": v, "arch": "armhf", "source": "gdb", "file": f, "size": (kits.POOL / f).stat().st_size, "sha256": sha(kits.POOL / f)})
kits._write(kits.MANIFESTS / "small.json", {"id": "small", "fetched": 1790000000, "packages": pkgs, "on_box": [], "arch": "armhf", "bytes": 10})
packages = "".join(f"Package: {p['name']}\nVersion: {p['version']}\nFilename: pool/main/g/gdb/{p['name']}_{p['version'].split(':')[-1]}_armhf.deb\nSHA256: {p['sha256']}\n\n" for p in pkgs)
L = T / "lists"
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages)
ph = sha(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages")
(L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(f"SIGNED BY: good key\nOrigin: Debian\nSHA256:\n {ph} {len(packages)} main/binary-armhf/Packages\n")
(L / "other.example_dists_x_main_binary-armhf_Packages").write_text("Package: unrelated\nFilename: a/unrelated.deb\nSHA256: 00\n\n")
(L / "other.example_dists_x_InRelease").write_text("SIGNED BY: good key\nSHA256:\n")

stick = T / "stick" / "irate-box" / "kits"; stick.mkdir(parents=True)
line = kits.export_usb("small", stick)
kf = stick / "armhf" / "small"
check("export: the .debs (an epoch's name as apt saves it), the manifest, and only the signed indexes that list them", sorted(p.name for p in (kf / "debs").iterdir()) == ["gdb_16.3-1_armhf.deb", "libgdb_1%3a16.3-1_armhf.deb"]
      and sorted(p.name for p in (kf / "lists").iterdir()) == ["deb.debian.org_debian_dists_trixie_InRelease", "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages"]
      and json.loads((kf / "manifest.json").read_text())["title"] == "Small", line)
check("the stick's kits are listed by a scan", kits.stick_kits(T / "stick") == [{"kit": "small", "arch": "armhf", "title": "Small", "packages": 2, "bytes": 10, "fetched": 1790000000}],
      kits.stick_kits(T / "stick"))

# Another box: an empty cache, the same keys.
def fresh():
    shutil.rmtree(kits.ROOT, ignore_errors=True)
fresh()
line = kits.import_usb(stick, "small", 500)
check("import: every .deb vouched for, into the pool, the kit cached", kits.manifest("small") and len(kits.manifest("small")["packages"]) == 2
      and (kits.POOL / "gdb_16.3-1_armhf.deb").exists() and kits.verify() == [] and "checked against Debian's signed indexes" in line, line)
check("  the signature checked with the box's keys, never the removed ones", gpgv_calls and all("removed" not in a for a in gpgv_calls[-1]))
check("  and apt can install it: the index written", "Package: gdb" in (kits.POOL / "Packages").read_text())
def refused(mutate, why):
    fresh()
    s2 = T / f"s-{len(why)}-{abs(hash(why)) % 9999}"; shutil.copytree(T / "stick", s2)
    mutate(s2 / "irate-box" / "kits" / "armhf" / "small")
    try:
        kits.import_usb(s2 / "irate-box" / "kits", "small", 500); check(f"refused: {why}", False)
    except ValueError as exc:
        check(f"refused: {why} ({exc})", not kits.MANIFESTS.exists() or not kits.manifest("small"))
refused(lambda k: (k / "debs" / "gdb_16.3-1_armhf.deb").write_text("evil"), "a .deb changed on the stick")
refused(lambda k: (k / "lists" / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages.replace(pkgs[0]["sha256"], "0" * 64)),
        "a Packages file not the one its release lists")
def resign(k):
    p = k / "lists" / "deb.debian.org_debian_dists_trixie_InRelease"
    p.write_text(p.read_text().replace("SIGNED BY: good key", "SIGNED BY: someone else"))
refused(resign, "an index signed by a key the box doesn't trust")
def no_lists(k):
    shutil.rmtree(k / "lists"); (k / "lists").mkdir()
refused(no_lists, "no signed index at all")
def extra_deb(k):
    m = json.loads((k / "manifest.json").read_text())
    (k / "debs" / "evil_1_armhf.deb").write_text("evil")
    m["packages"].append({"name": "evil", "version": "1", "file": "evil_1_armhf.deb"})
    (k / "manifest.json").write_text(json.dumps(m))
refused(extra_deb, "a package the indexes don't list, added to the manifest")
def link(k):
    (k / "debs" / "gdb_16.3-1_armhf.deb").unlink(); os.symlink("/etc/shadow", k / "debs" / "gdb_16.3-1_armhf.deb")
refused(link, "a link in place of a .deb")
fresh()
try:
    kits.import_usb(stick, "small", 0); check("over the budget: refused", False)
except ValueError as exc:
    check("over the budget: refused", "budget" in str(exc))
fresh()
os.rename(stick / "armhf", stick / "arm64")
try:
    kits.import_usb(stick, "small", 500); check("a kit for another architecture: refused, and says so", False)
except ValueError as exc:
    check("a kit for another architecture: refused, and says so", "for this box's architecture (armhf)" in str(exc), str(exc))
os.rename(stick / "arm64", stick / "armhf")
# Export refuses what no signed index vouches for (the lists moved on since the fetch).
fresh(); kits.import_usb(stick, "small", 500)
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages.replace(pkgs[1]["sha256"], "1" * 64))
try:
    kits.export_usb("small", T / "stick2"); check("export refuses a .deb no signed index lists", False)
except ValueError as exc:
    check("export refuses a .deb no signed index lists", "libgdb_1%3a16.3-1_armhf.deb" in str(exc), str(exc))
# A kit from Debian's debug archive (the symbols kit): its signed index is in the kits' own lists.
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages)
kits.DEBUG_LISTS.mkdir(parents=True, exist_ok=True)
for f in ("deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages", "deb.debian.org_debian_dists_trixie_InRelease"):
    shutil.move(str(L / f), str(kits.DEBUG_LISTS / f.replace("debian_dists_trixie", "debian-debug_dists_trixie-debug")))
line = kits.export_usb("small", T / "stick3")
check("export: a debug-archive kit vouched for by the index kept in the kits' own lists",
      sorted(p.name for p in (T / "stick3" / "armhf" / "small" / "lists").iterdir()) == ["deb.debian.org_debian-debug_dists_trixie-debug_InRelease",
      "deb.debian.org_debian-debug_dists_trixie-debug_main_binary-armhf_Packages"], line)
from irate_box.root import hub_control  # noqa: E402
check("the root helper has both", {"usb-kit-import", "usb-kit-export"} <= set(hub_control.ACTIONS))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
