# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Housecall doctor on images and folders (root/housecall.py, notes: housecall-doctor-plan steps 1 and 3),
offline: a small ext4 image read without a loop device or a mount, its files given the plan's verdicts (0xFF
damaged, zero without a reference suspect, a difference from a reference damaged, a partial 0xFF block unknown);
a partitioned image; a reference manifest; the image unchanged; a folder scanned past the cache; the report in
control/housecall.json. python3 tests/sim_housecall.py"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="housecall-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.msg import Refused
from irate_box.root import housecall

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def build_image(path, files, size="16M", offset=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        subprocess.run(["truncate", "-s", size, str(path)], check=True)
    args = ["mke2fs", "-q", "-t", "ext4"]
    if offset:
        args += ["-E", f"offset={offset}"]
    subprocess.run([*args, str(path)], check=True, stderr=subprocess.DEVNULL)
    source = f"{path}?offset={offset}" if offset else str(path)
    for name, data in files.items():
        local = T / "to-write"
        local.write_bytes(data)
        subprocess.run(
            ["debugfs", "-w", "-R", f"write {local} /{name}", source],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


good = b"hello\n"
changed_ref = b"what the reference says\n"
ff = b"\xff" * 4096
zero = b"\0" * 4096
partial = b"\xff" * 2048 + b"data"

image = T / "card.img"
build_image(image, {"good": good, "ff": ff, "zero": zero, "partial": partial, "changed": b"edited\n"})
before = hashlib.sha256(image.read_bytes()).hexdigest()
references = {
    "/good": hashlib.sha256(good).hexdigest(),
    "/changed": hashlib.sha256(changed_ref).hexdigest(),
}
report = housecall.examine_image(image, references)
by_path = {r["path"]: r for r in report["files"]}
check(
    "an all-0xFF file: damaged",
    by_path.get("/ff", {}).get("verdict") == "damaged" and by_path["/ff"]["ff_blocks"] == 1,
    report["files"],
)
check("a file that matches its reference: genuine", by_path.get("/good", {}).get("verdict") == "genuine")
check("a file that differs from its reference: damaged", by_path.get("/changed", {}).get("verdict") == "damaged")
check(
    "a file with a partial 0xFF block and no reference: unknown (a genuine padding can do this)",
    "/partial" not in by_path and report["counts"]["unknown"] == 1,
    report["counts"],
)
check("an all-zero file with no reference: suspect", report["counts"]["suspect"] == 1)
check(
    "the finding names the damaged files, and says how",
    any(f["id"].startswith("housecall-files:") and f["status"] == "problem" for f in report["findings"])
    and any("/ff: damaged" in e for f in report["findings"] for e in f["evidence"]),
    report["findings"],
)
check(
    "the image is unchanged (its SHA-256 before and after)",
    hashlib.sha256(image.read_bytes()).hexdigest() == before,
)
card_report = report
check(
    "the image's partition table is absent but its whole-disk filesystem is found",
    any(f["id"] == "housecall-fs:whole disk" and f["status"] == "ok" for f in report["findings"]),
    report["findings"],
)

# A partitioned image: the table is named and the ext4 partition at its offset is read.
parts = T / "parts.img"
subprocess.run(["truncate", "-s", "32M", str(parts)], check=True)
subprocess.run(
    ["sfdisk", "-q", str(parts)], input="label: dos\nstart=2048, size=40960, type=83\n", text=True, check=True
)
build_image(parts, {"good": good}, size="32M", offset=2048 * 512)
report = housecall.examine_image(parts)
check(
    "a partitioned image: its table and its filesystem are named",
    any(f["id"] == "housecall-partitions" and f["status"] == "ok" for f in report["findings"])
    and any(f["id"].startswith("housecall-fs:") and f["status"] == "ok" for f in report["findings"]),
    report["findings"],
)
check("  and the file in it is read", report["counts"]["files"] == 1, report["counts"])

# Step 2: the image first. A card (a small ext4 image stands in for one) is copied whole, then the copy is examined;
# the destination is refused on this box's own card unless the owner says so, and when it will not fit.
source_img = T / "source-card.img"
build_image(source_img, {"note": good}, size="16M")
source_sha = hashlib.sha256(source_img.read_bytes()).hexdigest()
first = T / "first.img"
said = housecall.copy_device(source_img, first)
check(
    "the card is imaged first: the same bytes, and no .part left behind",
    hashlib.sha256(first.read_bytes()).hexdigest() == source_sha
    and not (T / "first.img.part").exists()
    and said.get("id") == "housecall.image-first.done",
    said,
)
real_on_card, real_free = housecall._destination_on_card, housecall._free_bytes
housecall._destination_on_card = lambda image: True
try:
    housecall.copy_device(source_img, T / "on-card.img")
    check("  an image on this box's own card: refused unless it is allowed", False)
except Refused as exc:
    check(
        "  an image on this box's own card: refused unless it is allowed",
        exc.message["id"] == "housecall.image-first.own-card",
    )
housecall._destination_on_card = real_on_card
housecall._free_bytes = lambda folder: 1
try:
    housecall.copy_device(source_img, T / "no-room.img")
    check("  an image that will not fit: refused, and nothing written", False)
except Refused as exc:
    check(
        "  an image that will not fit: refused, and nothing written",
        exc.message["id"] == "housecall.image-first.space" and not (T / "no-room.img.part").exists(),
    )
housecall._free_bytes = real_free
first_report = housecall.examine_image_first(source_img, T / "first-examined.img")
check(
    "  the examination runs on the image, and says so",
    any(f["id"] == "housecall-image-first" and f["status"] == "ok" for f in first_report["findings"])
    and first_report["counts"]["files"] == 1
    and first_report.get("source_device") == str(source_img)
    and hashlib.sha256(source_img.read_bytes()).hexdigest() == source_sha,
    first_report["findings"],
)

# A folder, read directly (this box's own files): the same verdicts, past the cache.
folder = T / "folder"
(folder / "sub").mkdir(parents=True)
(folder / "good").write_bytes(good)
(folder / "sub" / "ff").write_bytes(ff)
(folder / "sub" / "zero").write_bytes(zero)
records = housecall.scan_dir(folder, {"/good": hashlib.sha256(good).hexdigest()})
by_path = {r["path"]: r for r in records}
check(
    "a folder: the same per-file verdicts, with paths from its root",
    by_path["/good"]["verdict"] == "genuine"
    and by_path["/sub/ff"]["verdict"] == "damaged"
    and by_path["/sub/zero"]["verdict"] == "suspect",
    records,
)

# The reference manifest as a file, and its refusal of a bad digest.
refs = T / "refs.json"
refs.write_text(json.dumps({"version": 1, "files": {"/good": {"sha256": hashlib.sha256(good).hexdigest()}}}))
check(
    "a reference manifest is read",
    housecall.load_references(refs) == {"/good": {"sha256": hashlib.sha256(good).hexdigest()}},
)
refs.write_text(json.dumps({"files": {"/bad": "not-a-digest"}}))
check("  a digest that is not SHA-256 is left out", housecall.load_references(refs) == {})

# The references the box already has: its packages' MD5 sums, and irate-box's git tree.
pkgroot = T / "pkgroot"
(pkgroot / "etc").mkdir(parents=True)
(pkgroot / "var/lib/dpkg/info").mkdir(parents=True)
(pkgroot / "etc" / "example.conf").write_bytes(b"package file\n")
(pkgroot / "var/lib/dpkg/info/example.md5sums").write_text(
    f"{hashlib.md5(b'package file\n').hexdigest()}  etc/example.conf\n"
)
dpkg_refs = housecall.references_from_dpkg(pkgroot)
check(
    "the installed packages' MD5 sums become references",
    dpkg_refs == {"/etc/example.conf": {"md5": hashlib.md5(b"package file\n").hexdigest()}},
    dpkg_refs,
)
check(
    "  and a package file that matches is genuine",
    next(r["verdict"] for r in housecall.scan_dir(pkgroot, dpkg_refs) if r["path"] == "/etc/example.conf") == "genuine",
)

repo = T / "repo"
(repo / "irate_box").mkdir(parents=True)
(repo / "irate_box" / "example.py").write_bytes(b"print('ok')\n")
subprocess.run(["git", "init", "-q", str(repo)], check=True)
subprocess.run(["git", "-C", str(repo), "add", "irate_box/example.py"], check=True)
subprocess.run(
    ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=t@example.invalid", "commit", "-qm", "one"],
    check=True,
)
commit = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()
gitroot = T / "gitroot"
(gitroot / "opt/irate-box/irate_box").mkdir(parents=True)
(gitroot / "opt/irate-box/irate_box/example.py").write_bytes(b"print('ok')\n")
git_refs = housecall.references_from_git(gitroot, repo, commit)
blob = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", f"{commit}:irate_box/example.py"],
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
check(
    "irate-box's git tree becomes references, by the blob SHA-1",
    git_refs == {"/opt/irate-box/irate_box/example.py": {"git": blob}},
    git_refs,
)
# The boxes write VERSION short ("4ccfedc (installed 2026-10-27)" on box 181): it is read, and resolved in the checkout.
(gitroot / "opt/irate-box/VERSION").write_text(f"{commit[:7]} (installed 2026-10-27)\n")
check(
    "  the short commit in VERSION, as the boxes write it, is read", housecall.installed_commit(gitroot) == commit[:7]
)
check(
    "  and resolves in the checkout to the same references",
    housecall.references_from_git(gitroot, repo) == {"/opt/irate-box/irate_box/example.py": {"git": blob}},
)
check(
    "  and a file that matches it is genuine",
    next(
        r["verdict"]
        for r in housecall.scan_dir(gitroot / "opt/irate-box", git_refs, prefix="/opt/irate-box")
        if r["path"] == "/opt/irate-box/irate_box/example.py"
    )
    == "genuine",
)

# Step 3: the references a card already holds. Box doctor's manifest, and an image's own packages and git tree.
manifest = T / "manifest.json"
manifest.write_text(json.dumps({"entries": {"/good": {"sha256": hashlib.sha256(good).hexdigest()}}}))
check(
    "Box doctor's manifest becomes references (its protected files)",
    housecall.load_manifest(manifest) == {"/good": {"sha256": hashlib.sha256(good).hexdigest()}},
    housecall.load_manifest(manifest),
)
default_manifest = housecall.MANIFEST
default_manifest.parent.mkdir(parents=True, exist_ok=True)
default_manifest.write_text(json.dumps({"entries": {"/etc/key": {"sha256": "a" * 64}}}))
check(
    "  this box's own manifest is found where Box doctor keeps it",
    housecall.load_manifest() == {"/etc/key": {"sha256": "a" * 64}},
    housecall.load_manifest(),
)
image = T / "refs.img"
build_image(image, {}, size="16M")


def ext_write(path, remote, data, offset=0):
    source = f"{path}?offset={offset}" if offset else str(path)
    parts = [p for p in remote.strip("/").split("/") if p]
    for i in range(1, len(parts)):
        subprocess.run(
            ["debugfs", "-w", "-R", f"mkdir /{'/'.join(parts[:i])}", source],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    local = T / "ext-write"
    local.write_bytes(data)
    subprocess.run(
        ["debugfs", "-w", "-R", f"write {local} /{'/'.join(parts)}", source],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


ext_write(image, "etc/example.conf", b"package file\n")
ext_write(
    image,
    "var/lib/dpkg/info/example.md5sums",
    f"{hashlib.md5(b'package file\n').hexdigest()}  etc/example.conf\n".encode(),
)
check(
    "an image's own packages' md5sums become references",
    housecall.references_from_dpkg_ext(str(image))
    == {"/etc/example.conf": {"md5": hashlib.md5(b"package file\n").hexdigest()}},
    housecall.references_from_dpkg_ext(str(image)),
)
ext_write(image, "opt/irate-box/irate_box/example.py", b"print('ok')\n")
ext_write(image, "opt/irate-box/VERSION", f"{commit[:7]} (installed 2026-10-27)\n".encode())
check("  an image's own installed commit is read", housecall.installed_commit_ext(str(image)) == commit[:7])
check(
    "  and its irate-box tree becomes references from the checkout",
    housecall.references_from_git_ext(str(image), repo) == {"/opt/irate-box/irate_box/example.py": {"git": blob}},
    housecall.references_from_git_ext(str(image), repo),
)
merged = housecall.references_from_target(str(image), use_dpkg=True, use_tree=True, repo=repo)
image_report = housecall.examine_image(image, merged)
by_path = {r["path"]: r for r in image_report["files"]}
check(
    "  the image's own references are used: both files genuine",
    by_path.get("/etc/example.conf", {}).get("verdict") == "genuine"
    and by_path.get("/opt/irate-box/irate_box/example.py", {}).get("verdict") == "genuine",
    image_report["files"],
)

# From inside: this box's own folders, with a file on the NAND left out and said to be the NAND's.
box = T / "box"
(box / "etc").mkdir(parents=True)
(box / "nand").mkdir(parents=True)
(box / "etc" / "damaged").write_bytes(ff)
(box / "nand" / "state.json").write_bytes(ff)
real_on_card = housecall._on_card
housecall._on_card = lambda path: "nand" not in str(path)
inside_report = housecall.examine_this_card(roots=[box])
housecall._on_card = real_on_card
by_path = {r["path"]: r for r in inside_report["files"]}
check(
    "from inside: a card file turned to 0xFF is found and named",
    next((r for path, r in by_path.items() if path.endswith("/etc/damaged")), {}).get("verdict") == "damaged",
    inside_report["files"],
)
check(
    "  a file on the NAND is left out, and the report says it is the NAND's",
    not any(path.endswith("/nand/state.json") for path in by_path)
    and any(
        f["id"] == "housecall-scope" and "housecall.scope.nand" in message_ids(f) for f in inside_report["findings"]
    ),
    inside_report["findings"],
)
check(
    "  the scan writes nothing under the folders it reads",
    sorted(str(p.relative_to(box)) for p in box.rglob("*")) == ["etc", "etc/damaged", "nand", "nand/state.json"],
)

# The root helper's action the page asks for: started in a unit of its own (it does one job at a time under a
# 50-minute limit, and an image took 32.5 min on the Pi), its running report written first.
from irate_box.root import hub_control

ran, active = [], {"state": "inactive"}


def helper_run(*cmd, timeout=None, **kw):
    ran.append(cmd)
    out = active["state"] + "\n" if cmd[:2] == ("systemctl", "is-active") else ""
    return subprocess.CompletedProcess(cmd, 0, out, "")


real_helper_run = hub_control.run
hub_control.run = helper_run
said = hub_control.housecall_scan({"target": "image", "path": str(image)})
started = [c for c in ran if c[0] == "systemd-run"]
running = json.loads((T / "state" / "control" / "housecall.json").read_text())
check(
    "the root helper's housecall-scan starts it in a unit of its own, at low priority, and says so",
    said.get("id") == "control.housecall.started"
    and len(started) == 1
    and f"--unit={hub_control.HOUSECALL_UNIT}" in started[0]
    and "IOSchedulingClass=idle" in started[0]
    and started[0][-3:] == ("--image", str(image), "--summary"),
    (said, started),
)
check("  its running report is written first, for the page", running.get("running") is True, running)
ran.clear()
said = hub_control.housecall_scan(
    {"target": "device", "path": str(source_img), "image_first": str(T / "helper-first.img"), "on_own_card": True}
)
started = [c for c in ran if c[0] == "systemd-run"]
check(
    "the root helper passes the image and the own-card permission",
    said.get("id") == "control.housecall.started"
    and started
    and "--image-first" in started[0]
    and str(T / "helper-first.img") in started[0]
    and "--on-own-card" in started[0],
    (said, started),
)
try:
    hub_control.housecall_scan({"target": "nonsense"})
    check("  an unknown target: refused", False)
except Refused:
    check("  an unknown target: refused", True)
ran.clear()
said = hub_control.housecall_scan(
    {"target": "image", "path": str(image), "dpkg": True, "git_tree": True, "manifest": str(manifest)}
)
started = [c for c in ran if c[0] == "systemd-run"]
check(
    "  the package and tree references for an image are passed to it",
    said.get("id") == "control.housecall.started"
    and started
    and "--dpkg" in started[0]
    and "--git-tree" in started[0]
    and "--manifest" in started[0],
    (said, started),
)
active["state"] = "active"
try:
    hub_control.housecall_scan({"target": "image", "path": str(image)})
    check("  a second while one runs: refused", False)
except Refused:
    check("  a second while one runs: refused", True)
said = hub_control.housecall_stop({})
stopped = json.loads((T / "state" / "control" / "housecall.json").read_text())
check(
    "  stopped: the unit stopped, and the report no longer running",
    any(c[:2] == ("systemctl", "stop") for c in ran) and stopped.get("running") is False,
    stopped,
)
hub_control.run = real_helper_run

# The report the page reads.
housecall.write_report(card_report)
saved = json.loads((T / "state" / "control" / "housecall.json").read_text())
check(
    "the report is control/housecall.json, in the doctors' shape",
    saved["counts"]["damaged"] == card_report["counts"]["damaged"]
    and all("summary" in f and "status" in f for f in saved["findings"]),
)
check(
    "  its damaged-file finding says the plan's words",
    any("housecall.files.damaged" in message_ids(f) for f in saved["findings"]),
)

# A file read short, or not at all, is damage, never left out (a truncated debugfs read, an I/O error).
short = housecall._record("/short", 8192, iter([b"x" * 4096]), {})
check("a file that reads back short is damaged", short["verdict"] == "damaged", short)
real_chunks = housecall._direct_chunks


def failing_chunks(path):
    raise OSError(5, "Input/output error")
    yield b""  # a generator, as the real one


(bad_dir := T / "unreadable").mkdir()
(bad_dir / "f").write_bytes(b"data")
housecall._direct_chunks = failing_chunks
records = housecall.scan_dir(bad_dir)
housecall._direct_chunks = real_chunks
check(
    "  a file that cannot be read at all is in the count, damaged",
    len(records) == 1 and records[0]["verdict"] == "damaged" and records[0]["sha256"] is None,
    records,
)

# From inside, the hub's large folders are left out whole.
(big := T / "inside" / "zim").mkdir(parents=True)
(big / "book.zim").write_bytes(b"\xff" * 4096)
(T / "inside" / "settings.json").write_bytes(b"{}")
records = housecall.scan_dir(T / "inside", skip=[big])
check(
    "a skipped folder is not read (the large content, from inside)",
    [r["path"] for r in records] == ["/settings.json"],
    records,
)

# The box's own disk, refused: the root partition's own disk, or another partition of it.
real_run = housecall.run


def fake_run(*cmd, timeout=None):
    out = ""
    if cmd[0] == "findmnt":
        out = "/dev/null\n"  # stands for the root partition: a device that exists
    elif cmd[0] == "lsblk":
        out = {"/dev/null": "mmcblk0", str(T / "mmcblk0p1"): "mmcblk0"}.get(cmd[-1], "") + "\n"
    return subprocess.CompletedProcess(cmd, 0, out, "")


for name in ("mmcblk0", "mmcblk0p1", "sda"):
    (T / name).write_bytes(b"")
housecall.run = fake_run
check(
    "the box's own disk is refused, and its other partitions; another disk is not",
    housecall._own_disk(T / "mmcblk0") and housecall._own_disk(T / "mmcblk0p1") and not housecall._own_disk(T / "sda"),
)
housecall.run = real_run

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
