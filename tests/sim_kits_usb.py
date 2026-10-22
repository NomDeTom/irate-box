# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits by USB stick, offline: the export carries
the kit's .debs and the signed indexes that list them; the import accepts a .deb only when a signed
InRelease (its signature checked with the box's keys), the Packages file it lists, and the .deb's
own hash all agree. gpgv and dpkg-deb are stood in. python3 tests/sim_kits_usb.py"""

import hashlib
import json
import lzma
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="kitsusb-"))
(T / "defs").mkdir()
(T / "lists").mkdir()
(T / "keys").mkdir()
(T / "keys" / "debian-archive-keyring.gpg").write_text("good key")
(T / "keys" / "debian-archive-removed-keys.gpg").write_text("removed key")
# A third party's key the owner added for another repository: apt trusts it, a kit must not.
(T / "keys" / "vendor-archive-keyring.gpg").write_text("vendor key")
(T / "defs" / "small.json").write_text(
    json.dumps({"id": "small", "title": "Small", "summary": "s", "consent": "c", "packages": ["gdb"]})
)
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_KITS_ROOT=str(T / "cache"),
    HUB_KITS_DEFS=str(T / "defs"),
    HUB_APT_LISTS=str(T / "lists"),
    HUB_APT_KEYRINGS=str(T / "keys"),
    HUB_DEBIAN_KEYRING=str(T / "keys" / "debian-archive-keyring.gpg"),
)
sys.path.insert(0, str(REPO))
from irate_box.msg import args_of
from irate_box.root import kits

# The box believes its clock (network time) unless a case says otherwise.
kits._clock_trusted = lambda: "network time"

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


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
        out = Path(cmd[cmd.index("--output") + 1])
        src = Path(cmd[-1])
        text = src.read_text()
        rings = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--keyring"]
        # gpgv accepts a signature by any key in any keyring it is given, as the real one does.
        signer = next((Path(r).read_text() for r in rings if f"SIGNED BY: {Path(r).read_text()}" in text), None)
        good = signer is not None
        if good:
            out.write_text(text.replace(f"SIGNED BY: {signer}\n", ""))
        return R("", 0 if good else 2)
    raise AssertionError(cmd)


kits.run = fake

# A box's cache: one kit, two .debs, and apt's lists vouching for them.
kits.POOL.mkdir(parents=True)
kits.MANIFESTS.mkdir(parents=True)
pkgs = []
for n, v in (("gdb", "16.3-1"), ("libgdb", "1:16.3-1")):
    f = f"{n}_{v.replace(':', '%3a')}_armhf.deb"  # as apt saves an epoch
    (kits.POOL / f).write_text(json.dumps({"Package": n, "Version": v, "Architecture": "armhf"}))
    pkgs.append(
        {
            "name": n,
            "version": v,
            "arch": "armhf",
            "source": "gdb",
            "file": f,
            "size": (kits.POOL / f).stat().st_size,
            "sha256": sha(kits.POOL / f),
        }
    )
kits._write(
    kits.MANIFESTS / "small.json",
    {"id": "small", "fetched": 1790000000, "packages": pkgs, "on_box": [], "arch": "armhf", "bytes": 10},
)
packages = "".join(
    f"Package: {p['name']}\nVersion: {p['version']}\nFilename: pool/main/g/gdb/{p['name']}_{p['version'].split(':')[-1]}_armhf.deb\nSHA256: {p['sha256']}\n\n"
    for p in pkgs
)
L = T / "lists"
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages)
ph = sha(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages")
(L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(
    f"SIGNED BY: good key\nOrigin: Debian\nDate: Fri, 02 Oct 2026 00:00:00 UTC\nSHA256:\n {ph} {len(packages)} main/binary-armhf/Packages\n"
)
(L / "other.example_dists_x_main_binary-armhf_Packages").write_text(
    "Package: unrelated\nFilename: a/unrelated.deb\nSHA256: 00\n\n"
)
(L / "other.example_dists_x_InRelease").write_text("SIGNED BY: good key\nSHA256:\n")

stick = T / "stick" / "irate-box" / "kits"
stick.mkdir(parents=True)
line = kits.export_usb("small", stick)
kf = stick / "armhf" / "small"
check(
    "export: the .debs (an epoch's name as apt saves it), the manifest, and only the signed indexes that list them",
    sorted(p.name for p in (kf / "debs").iterdir()) == ["gdb_16.3-1_armhf.deb", "libgdb_1%3a16.3-1_armhf.deb"]
    and sorted(p.name for p in (kf / "lists").iterdir())
    == [
        "deb.debian.org_debian_dists_trixie_InRelease",
        "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages.xz",
    ]
    and lzma.decompress(
        (kf / "lists" / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages.xz").read_bytes()
    ).decode()
    == packages
    and json.loads((kf / "manifest.json").read_text())["title"] == "Small",
    line,
)
check(
    "the stick's kits are listed by a scan",
    kits.stick_kits(T / "stick")
    == [{"kit": "small", "arch": "armhf", "title": "Small", "packages": 2, "bytes": 10, "fetched": 1790000000}],
    kits.stick_kits(T / "stick"),
)


# Another box: an empty cache, the same keys.
def fresh():
    shutil.rmtree(kits.ROOT, ignore_errors=True)


fresh()
line = kits.import_usb(stick, "small", 500)
check(
    "import: every .deb vouched for, into the pool, the kit cached",
    kits.manifest("small")
    and len(kits.manifest("small")["packages"]) == 2
    and (kits.POOL / "gdb_16.3-1_armhf.deb").exists()
    and kits.verify() == []
    and args_of(line, "kits.usb.imported") == {"title": "Small", "n": 2},
    line,
)
check(
    "  the signature checked with Debian's archive keyring alone",
    gpgv_calls
    and [gpgv_calls[-1][i + 1] for i, a in enumerate(gpgv_calls[-1]) if a == "--keyring"]
    == [str(T / "keys" / "debian-archive-keyring.gpg")],
    gpgv_calls[-1:],
)
check("  and apt can install it: the index written", "Package: gdb" in (kits.POOL / "Packages").read_text())


def refused(mutate, why):
    fresh()
    s2 = T / f"s-{len(why)}-{abs(hash(why)) % 9999}"
    shutil.copytree(T / "stick", s2)
    mutate(s2 / "irate-box" / "kits" / "armhf" / "small")
    try:
        kits.import_usb(s2 / "irate-box" / "kits", "small", 500)
        check(f"refused: {why}", False)
    except ValueError as exc:
        check(f"refused: {why} ({exc})", not kits.MANIFESTS.exists() or not kits.manifest("small"))


refused(lambda k: (k / "debs" / "gdb_16.3-1_armhf.deb").write_text("evil"), "a .deb changed on the stick")
XZ = "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages.xz"
refused(
    lambda k: (k / "lists" / XZ).write_bytes(lzma.compress(packages.replace(pkgs[0]["sha256"], "0" * 64).encode())),
    "a Packages file not the one its release lists",
)
refused(
    lambda k: (k / "lists" / XZ.removesuffix(".xz")).write_text(packages.replace(pkgs[0]["sha256"], "0" * 64)),
    "a plain Packages file beside it, not the one its release lists",
)
refused(lambda k: (k / "lists" / XZ).write_bytes((k / "lists" / XZ).read_bytes()[:40]), "a cut-off .xz index")


def bomb(k):
    kits.INDEX_MAX = 1 << 16
    (k / "lists" / XZ).write_bytes(lzma.compress(packages.encode() + b"\n" * (1 << 20)))


refused(bomb, "an index that unpacks past the ceiling")
kits.INDEX_MAX = 512 << 20


def link_index(k):
    (k / "lists" / XZ).unlink()
    os.symlink(L / XZ.removesuffix(".xz"), k / "lists" / XZ)


refused(link_index, "a link in place of an index")
# A stick written before the indexes were compressed still reads.
fresh()
old = T / "old-stick"
shutil.copytree(T / "stick", old)
ol = old / "irate-box" / "kits" / "armhf" / "small" / "lists"
(ol / XZ.removesuffix(".xz")).write_bytes(lzma.decompress((ol / XZ).read_bytes()))
(ol / XZ).unlink()
check(
    "a stick with plain indexes (an older export) still imports",
    args_of(kits.import_usb(old / "irate-box" / "kits", "small", 500), "kits.usb.imported") is not None,
)


def resign(k):
    p = k / "lists" / "deb.debian.org_debian_dists_trixie_InRelease"
    p.write_text(p.read_text().replace("SIGNED BY: good key", "SIGNED BY: someone else"))


refused(resign, "an index signed by a key the box doesn't trust")


def vendor(k):
    p = k / "lists" / "deb.debian.org_debian_dists_trixie_InRelease"
    p.write_text(p.read_text().replace("SIGNED BY: good key", "SIGNED BY: vendor key"))


refused(vendor, "an index signed by a third party's key that apt trusts here, its Origin still saying Debian")

# No Debian keyring: no kit is checked, so none imports.
real_ring = kits.DEBIAN_KEYRING
kits.DEBIAN_KEYRING = T / "keys" / "absent.gpg"
fresh()
try:
    kits.import_usb(stick, "small", 500)
    why = None
except ValueError as exc:
    why = getattr(exc, "message", {}).get("id")
kits.DEBIAN_KEYRING = real_ring
check("no Debian keyring: refused, saying which file is missing", why == "kits.usb.no-keyring", why)


# A stick cannot replay an old signed set.
def expired(k):
    p = k / "lists" / "deb.debian.org_debian_dists_trixie_InRelease"
    p.write_text(
        p.read_text().replace("Origin: Debian\n", "Origin: Debian\nValid-Until: Thu, 01 Jan 2026 00:00:00 UTC\n")
    )


refused(expired, "a signed release past its Valid-Until")


def older(k):
    (L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(
        (L / "deb.debian.org_debian_dists_trixie_InRelease")
        .read_text()
        .replace("Origin: Debian\n", "Origin: Debian\nDate: Sat, 03 Oct 2026 00:00:00 UTC\n")
    )
    p = k / "lists" / "deb.debian.org_debian_dists_trixie_InRelease"
    p.write_text(p.read_text().replace("Origin: Debian\n", "Origin: Debian\nDate: Thu, 01 Jan 2026 00:00:00 UTC\n"))


refused(older, "a signed release older than the one this box already has")
(L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(
    (L / "deb.debian.org_debian_dists_trixie_InRelease")
    .read_text()
    .replace("Date: Sat, 03 Oct 2026 00:00:00 UTC\n", "")
)


def no_lists(k):
    shutil.rmtree(k / "lists")
    (k / "lists").mkdir()


refused(no_lists, "no signed index at all")


def extra_deb(k):
    m = json.loads((k / "manifest.json").read_text())
    (k / "debs" / "evil_1_armhf.deb").write_text("evil")
    m["packages"].append({"name": "evil", "version": "1", "file": "evil_1_armhf.deb"})
    (k / "manifest.json").write_text(json.dumps(m))


refused(extra_deb, "a package the indexes don't list, added to the manifest")


def link(k):
    (k / "debs" / "gdb_16.3-1_armhf.deb").unlink()
    os.symlink("/etc/shadow", k / "debs" / "gdb_16.3-1_armhf.deb")


refused(link, "a link in place of a .deb")
fresh()
try:
    kits.import_usb(stick, "small", 0)
    check("over the budget: refused", False)
except ValueError as exc:
    check("over the budget: refused", (args_of(exc.message, "kits.usb.over-limit") or {}).get("limit") == 0, exc)
fresh()
os.rename(stick / "armhf", stick / "arm64")
try:
    kits.import_usb(stick, "small", 500)
    check("a kit for another architecture: refused, and says so", False)
except ValueError as exc:
    check(
        "a kit for another architecture: refused, and says so",
        args_of(exc.message, "kits.usb.arch") == {"kit": "small", "arch": "armhf"},
        str(exc),
    )
os.rename(stick / "arm64", stick / "armhf")
# Export refuses what no signed index vouches for (the lists moved on since the fetch).
fresh()
kits.import_usb(stick, "small", 500)
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(
    packages.replace(pkgs[1]["sha256"], "1" * 64)
)
try:
    kits.export_usb("small", T / "stick2")
    check("export refuses a .deb no signed index lists", False)
except ValueError as exc:
    check(
        "export refuses a .deb no signed index lists",
        args_of(exc.message, "kits.export.unvouched") == {"packages": ["libgdb_1%3a16.3-1_armhf.deb"]},
        str(exc),
    )


# M10: a signature shows who made a release, not that it is the latest. Its expiry needs a clock the box believes.
def on_stick(edit=None, name="deb.debian.org_debian_dists_trixie_InRelease"):
    s = T / f"t-{abs(hash((edit, name))) % 99999}"
    shutil.rmtree(s, ignore_errors=True)
    shutil.copytree(T / "stick", s)
    lists = s / "irate-box" / "kits" / "armhf" / "small" / "lists"
    p = lists / "deb.debian.org_debian_dists_trixie_InRelease"
    if edit:
        p.write_text(edit(p.read_text()))
    if name != p.name:
        p.rename(lists / name)
    return s / "irate-box" / "kits"


def attempt(root, freshness=None):
    fresh()
    try:
        return kits.import_usb(root, "small", 500, freshness=freshness), None
    except ValueError as exc:
        return None, getattr(exc, "message", {}).get("id")


expired_text = lambda s: s.replace("Origin: Debian\n", "Origin: Debian\nValid-Until: Thu, 01 Jan 2026 00:00:00 UTC\n")  # noqa: E731
kits._clock_trusted = lambda: None
line, why = attempt(on_stick())
check(
    "clock not believed: refused, saying so, nothing cached",
    why == "kits.usb.time-untrusted" and not kits.manifest("small"),
    why,
)
line, why = attempt(on_stick(name="deb.debian.org_debian_dists_trixie_x_InRelease"))
check("  a renamed release file: refused the same", why == "kits.usb.time-untrusted" or line is None, why)
line, why = attempt(on_stick(), kits.UNKNOWN_TIME)
check(
    "  approved: imported, said as unchecked, the kit's manifest records it",
    why is None
    and args_of(line, "kits.usb.imported.unchecked") is not None
    and kits.manifest("small").get("freshness") == kits.UNKNOWN_TIME,
    (line, why),
)
line, why = attempt(on_stick(expired_text), kits.UNKNOWN_TIME)
check("  approved, past a Valid-Until the clock cannot judge: imported, unchecked", why is None, why)
older_box = (L / "deb.debian.org_debian_dists_trixie_InRelease").read_text()
(L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(
    older_box.replace("Date: Fri, 02 Oct 2026", "Date: Sat, 03 Oct 2026")
)
line, why = attempt(on_stick(), kits.UNKNOWN_TIME)
check("  approved, but older than the release this box holds: refused all the same", why == "kits.usb.older", why)
(L / "deb.debian.org_debian_dists_trixie_InRelease").write_text(older_box)
line, why = attempt(on_stick(lambda s: s.replace("Date: Fri, 02 Oct 2026 00:00:00 UTC\n", "")), kits.UNKNOWN_TIME)
check("  approved, a release with no Date: refused", why == "kits.usb.release-date", why)
line, why = attempt(
    on_stick(lambda s: s.replace("Origin: Debian\n", "Origin: Debian\nValid-Until: soon\n")), kits.UNKNOWN_TIME
)
check("  approved, an unreadable Valid-Until: refused", why == "kits.usb.release-date", why)
line, why = attempt(on_stick(lambda s: s.replace("SIGNED BY: good key", "SIGNED BY: someone else")), kits.UNKNOWN_TIME)
check("  approved, a bad signature: refused (approval is for freshness alone)", why == "kits.usb.signature", why)
fresh()
lines = kits.import_dir(on_stick(), 500, freshness=kits.UNKNOWN_TIME)
check(
    "  the console's import-dir --unknown-time: imported, its line says unchecked",
    args_of(lines[0], "kits.usb.imported.unchecked") is not None,
    lines,
)
kits._clock_trusted = lambda: "set by an administrator"
line, why = attempt(on_stick(expired_text))
check("clock believed (set by an administrator): an expired release refused", why == "kits.usb.expired", why)
line, why = attempt(on_stick())
check(
    "  a current one: imported as checked, no freshness mark",
    why is None and "freshness" not in kits.manifest("small"),
    why,
)
# The Security doctor: Debian's keyring, and kits imported unchecked, by how they stand now.
from irate_box.root import secdoctor


def doctor():
    return {f["id"]: f for f in secdoctor.step_kits_trust({})}


kits._clock_trusted = lambda: None
attempt(on_stick(expired_text), kits.UNKNOWN_TIME)
fs = doctor()
check(
    "doctor: a kit imported unchecked, clock not believed: a warning naming it, the Clock page its place",
    fs["kits-freshness-unknown"]["status"] == "warn"
    and "Small" in str(fs["kits-freshness-unknown"]["summary"])
    and fs["kits-freshness-unknown"]["go"]["section"] == "clock"
    and fs["kits-keyring"]["status"] == "ok",
    fs,
)
kits._clock_trusted = lambda: "network time"
fs = doctor()
check(
    "  the clock believed, the release had expired: a problem, Toolkits its place",
    fs["kits-freshness"]["status"] == "problem"
    and fs["kits-freshness"]["go"]["section"] == "toolkits"
    and "kits-freshness-unknown" not in fs,
    fs,
)
kits._clock_trusted = lambda: None
attempt(on_stick(), kits.UNKNOWN_TIME)
kits._clock_trusted = lambda: "network time"
fs = doctor()
check(
    "  the clock believed, the release current after all: cleared by itself",
    fs["kits-freshness"]["status"] == "ok" and kits.unchecked() == [],
    fs,
)
attempt(on_stick())
check("  re-imported with a believed clock: no mark at all", "freshness" not in kits.manifest("small"))
real_ring = kits.DEBIAN_KEYRING
kits.DEBIAN_KEYRING = T / "keys" / "absent.gpg"
fs = doctor()
check(
    "doctor: no Debian keyring: a problem, with the package to install",
    fs["kits-keyring"]["status"] == "problem"
    and fs["kits-keyring"]["commands"] == ["sudo apt install debian-archive-keyring"],
    fs["kits-keyring"],
)
kits.DEBIAN_KEYRING = real_ring

# A kit from Debian's debug archive (the symbols kit): its signed index is in the kits' own lists.
(L / "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages").write_text(packages)
kits.DEBUG_LISTS.mkdir(parents=True, exist_ok=True)
for f in (
    "deb.debian.org_debian_dists_trixie_main_binary-armhf_Packages",
    "deb.debian.org_debian_dists_trixie_InRelease",
):
    shutil.move(str(L / f), str(kits.DEBUG_LISTS / f.replace("debian_dists_trixie", "debian-debug_dists_trixie-debug")))
line = kits.export_usb("small", T / "stick3")
check(
    "export: a debug-archive kit vouched for by the index kept in the kits' own lists",
    sorted(p.name for p in (T / "stick3" / "armhf" / "small" / "lists").iterdir())
    == [
        "deb.debian.org_debian-debug_dists_trixie-debug_InRelease",
        "deb.debian.org_debian-debug_dists_trixie-debug_main_binary-armhf_Packages.xz",
    ],
    line,
)
from irate_box.root import hub_control

check("the root helper has both", {"usb-kit-import", "usb-kit-export"} <= set(hub_control.ACTIONS))
# The hub's approval is the security policy's to allow: hub mode passes it on, console mode refuses it.
(T / "state" / "control").mkdir(parents=True, exist_ok=True)
asked = []
hub_control.usbstick.import_kit = lambda dev, kit, budget, report=None, freshness=None: (
    asked.append(freshness) or "done"
)
hub_control._kits_status = lambda: None
for mode, want in (("hub", [kits.UNKNOWN_TIME]), ("console", [])):
    asked.clear()
    hub_control.policy_mode = lambda mode=mode: mode
    try:
        hub_control.usb_kit_import({"device": "sda1", "kit": "small", "freshness": "unknown-time"})
        why = None
    except ValueError as exc:
        why = exc.message["id"]
    check(
        f"the hub's approval, policy {mode}: "
        + ("passed to the import" if want else "refused, the console command said"),
        asked == want and (why is None if want else why == "kits.usb.unknown-time.console"),
        (asked, why),
    )
asked.clear()
hub_control.policy_mode = lambda: "console"
hub_control.usb_kit_import({"device": "sda1", "kit": "small"})
check("  an import with no approval runs under either policy", asked == [None], asked)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
