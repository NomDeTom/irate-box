# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The front and TLS files are written only when they change (notes: box-doctor-hardening-plan Part 2, step 6),
offline: tls._write leaves an identical file alone (its inode, mtime and bytes); nginx_front.prepare renders the
same bytes for the same inputs, reuses that bundle and keeps one in the staging folder; and install.sh sends the
front, its logins, hub.env and install-options through write_if_changed rather than rewriting them.
python3 tests/sim_front_writes.py"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="front-writes-"))
etc, state, stage = T / "etc", T / "state", T / "stage"
for p in (etc, state, stage):
    p.mkdir(parents=True)
os.environ.update(HUB_ETC_DIR=str(etc), HUB_STATE_DIR=str(state), HUB_CODE_DIR=str(REPO))
stage.chmod(0o700)
sys.path.insert(0, str(REPO))
from irate_box.root import nginx_front, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


path = etc / "tls" / "ports.json"
tls._write(path, '{"http": {}}\n', 0o644)
before = path.stat()
check("a new TLS file is written", path.read_text() == '{"http": {}}\n' and before.st_ino, before)

path.stat()
time.sleep(0.01)
tls._write(path, '{"http": {}}\n', 0o644)
after = path.stat()
check(
    "the same words and mode: the file is left alone (same inode, mtime and bytes)",
    after.st_ino == before.st_ino and after.st_mtime_ns == before.st_mtime_ns and path.read_text() == '{"http": {}}\n',
    (before.st_ino, after.st_ino, before.st_mtime_ns, after.st_mtime_ns),
)

tls._write(path, '{"http": {}}\n', 0o600)
same = path.stat()
check(
    "a mode-only change: chmod, not a rewrite",
    same.st_ino == after.st_ino and same.st_mode & 0o777 == 0o600 and same.st_mtime_ns == after.st_mtime_ns,
    (same.st_ino, after.st_ino, oct(same.st_mode & 0o777)),
)

tls._write(path, '{"http": {"main": "8080"}}\n', 0o644)
check(
    "different words: a new file",
    path.read_text() == '{"http": {"main": "8080"}}\n' and path.stat().st_ino != same.st_ino,
    path.read_text(),
)

template = stage / "template.conf"
template.write_text("server {\n\tlisten @PORT@;\n}\n")
values = {"PORT": "80"}
first = nginx_front.prepare(template, stage, values, "commit-one")
manifest, site = nginx_front.read_bundle(first)
check(
    "a prepared bundle: the render, and the generation taken from the render's own inputs",
    site.read_bytes() == first.joinpath("site.conf").read_bytes()
    and manifest["generation"] in site.read_text()
    and manifest["source"] == "commit-one",
    manifest,
)
site_before, manifest_before = site.stat(), (first / "manifest.json").stat()
time.sleep(0.01)
second = nginx_front.prepare(template, stage, values, "commit-one")
check(
    "the same template, settings and commit: the same bundle, nothing written",
    second == first
    and (first / "site.conf").stat().st_mtime_ns == site_before.st_mtime_ns
    and (first / "manifest.json").stat().st_mtime_ns == manifest_before.st_mtime_ns,
    (site_before.st_mtime_ns, (first / "site.conf").stat().st_mtime_ns),
)
bundles = [p for p in stage.iterdir() if p.name.startswith("nginx-")]
check("  and one bundle in the staging folder", bundles == [first], bundles)

third = nginx_front.prepare(template, stage, values, "commit-two")
bundles = [p for p in stage.iterdir() if p.name.startswith("nginx-")]
check(
    "a changed input: a new bundle, the earlier one left for any transaction that names it",
    third != first and set(bundles) == {first, third},
    (third, bundles),
)
third_generation = nginx_front.read_bundle(third)[0]["generation"]
changed = nginx_front.prepare(template, stage, {"PORT": "8080"}, "commit-two")
check(
    "a changed setting: a different generation",
    nginx_front.read_bundle(changed)[0]["generation"] != third_generation,
)

inst = (REPO / "install.sh").read_text()
check("install.sh has the write-if-changed helper", "write_if_changed() {" in inst and 'cmp -s "$tmp" "$path"' in inst)
for call in (
    'write_if_changed "$ETC/hub.env" 644',
    'write_if_changed "$ETC/install-options" 644',
    'write_if_changed "$STATE/install-options" 644',
):
    check(f"  {call}", call in inst, call)
check(
    "  a repeat install keeps the htpasswd hash when it still verifies the password",
    'openssl passwd -6 -salt "$salt" -stdin' in inst and 'hash="${existing#*:}"' in inst,
)
check(
    "  the front's four files are written together through write-batch",
    'nginx_prepare write-batch "$front_plan"' in inst
    and '"path": site, "text": open(site_src).read(), "mode": 0o644' in inst
    and '"path": front, "text": front_text, "mode": 0o600' in inst
    and '"path": via, "text": via_text, "mode": 0o600' in inst
    and '"path": logins, "text": f"admin:{hash_}\\n", "mode": 0o640, "group": group' in inst,
)
nf = (REPO / "irate_box/root/nginx_front.py").read_text()
check(
    "  nginx_front has the write-batch command, using durable.write_batch",
    'command == "write-batch"' in nf and "durable.write_batch(entries)" in nf,
)
for rewrites in ('>"$NGINX_FRONT"', '>"$NGINX_VIA"', 'cat >"$ETC/hub.env"', '} >"$ETC/install-options"'):
    check(f"  no in-place rewrite left: {rewrites}", rewrites not in inst, rewrites)

shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
