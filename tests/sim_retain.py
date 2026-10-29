# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The second copy of the settings and keys, and the check at boot (retain.py; notes: box-doctor-hardening-plan
Part 2, step 4), offline: the copy, a 0xFF file restored from it with its owner and mode, the damaged file kept
outside the include globs, a removed file left removed, a change the owner made left alone and copied by the next
sweep, a copy and its .prev both bad as a problem, an empty file not treated as damage, Box doctor's finding and the
two-boot verdict, and the units. python3 tests/sim_retain.py"""

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="retain-"))
etc, state, root, nginx, nm, units, code = (T / n for n in ("etc", "state", "root", "nginx", "nm", "units", "code"))
for p in (etc, state, root, nginx, nm, units, code):
    p.mkdir(parents=True)
journald = T / "journald.conf"
os.environ.update(
    HUB_ETC_DIR=str(etc),
    HUB_STATE_DIR=str(state),
    HUB_ROOT_STATE_DIR=str(root),
    HUB_NGINX_CONF_DIR=str(nginx),
    HUB_NM_DIR=str(nm),
    HUB_JOURNALD_CONF=str(journald),
    HUB_SYSTEMD_ETC=str(units),
    HUB_CODE_DIR=str(code),
    HUB_BOOT_ID="boot-one",
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids
from irate_box.root import health, retain

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def put(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)
    return path


hub_env = put(etc / "hub.env", "HUB_URL=/\n", 0o600)
server_key = put(etc / "tls" / "server.key", "KEY\n", 0o640)
front = put(etc / "tls" / "front" / "nginx-admin.conf", "front\n")
settings = put(state / "settings.json", '{"hub_title": "Box"}\n')
site = put(nginx / "conf.d" / "irate-box.conf", "server {}\n")
empty = put(etc / "nginx-addons.conf", "")
put(journald, "[Journal]\nStorage=volatile\n")
put(nm / "hub.nmconnection", "[connection]\nid=hub\n")

check("the copy is taken", retain.sweep() == [] and retain.MANIFEST.is_file())
manifest = json.loads(retain.MANIFEST.read_text())
check(
    "  every protected file is in it, with its copy and its owner and mode",
    {str(hub_env), str(server_key), str(front), str(settings), str(site), str(journald), str(empty)}
    <= set(manifest["entries"])
    and manifest["entries"][str(server_key)]["mode"] == 0o640
    and manifest["entries"][str(server_key)]["uid"] == os.geteuid(),
    sorted(manifest["entries"])[:8],
)
check("  and the copy reads back the same", retain._copy(hub_env).read_text() == "HUB_URL=/\n")
check("an empty file is copied, not called damage", retain._copy(empty).read_bytes() == b"" and not retain._bad(b""))

# Box 190's shape: 0xFF, the size and the mtime kept.
size = server_key.stat().st_size
mtime = server_key.stat().st_mtime_ns
server_key.write_bytes(b"\xff" * size)
os.utime(server_key, ns=(mtime, mtime))
repairs = retain.boot_check()
check(
    "a file that reads back as 0xFF is put back from the copy",
    server_key.read_text() == "KEY\n" and any("restored" in r["action"] for r in repairs),
    repairs,
)
check("  with its own mode", stat.S_IMODE(server_key.stat().st_mode) == 0o640, oct(server_key.stat().st_mode & 0o777))
check(
    "  and the damaged file is kept outside the folder nginx includes by glob",
    not Path(str(server_key) + ".damaged").exists() and any(retain.DAMAGED.iterdir()),
    list(retain.DAMAGED.iterdir()) if retain.DAMAGED.exists() else None,
)

# A file the owner removed is not put back; the next sweep drops it and its copy.
server_key.unlink()
retain.boot_check()
check("a removed file is left removed", not server_key.exists())
retain.sweep()
manifest = json.loads(retain.MANIFEST.read_text())
check(
    "  and the next sweep drops it from the manifest and the copy",
    str(server_key) not in manifest["entries"] and not retain._copy(server_key).exists(),
)

# A change the owner made: a different mtime and readable contents. It is left alone, and the next sweep copies it.
put(server_key, "NEW KEY\n", 0o640)
retain.sweep()
put(server_key, "NEWER KEY\n", 0o640)
retain.boot_check()
check("a change the owner made is not restored over", server_key.read_text() == "NEWER KEY\n")
retain.sweep()
check("  the next sweep copies it", retain._copy(server_key).read_text() == "NEWER KEY\n")
retain.boot_check()
check("  and the boot check is happy with it", server_key.read_text() == "NEWER KEY\n")

# Both the copy and its .prev bad.
entry = json.loads(retain.MANIFEST.read_text())["entries"][str(hub_env)]
put(hub_env, "different\n", 0o600)
os.utime(hub_env, ns=(entry["mtime_ns"], entry["mtime_ns"]))
for bad in (retain._copy(hub_env), retain._prev(hub_env)):
    bad.write_bytes(b"\xff" * 16)
repairs = retain.boot_check()
check(
    "a file whose copy and .prev are both damaged: a loss, not a restore",
    hub_env.read_text() == "different\n" and any("no good copy" in r["action"] for r in repairs),
    repairs,
)
found = {f["id"]: f for f in health.check_protected()}
check(
    "  Box doctor: a problem naming it",
    found["protected-files"]["status"] == "problem"
    and str(hub_env) in found["protected-files"]["evidence"]
    and "protected.lost" in message_ids(found["protected-files"]),
    found.get("protected-files"),
)

# Two boots running with repairs is the card verdict: the streak is what says they ran.
os.environ["HUB_BOOT_ID"] = "boot-two"
retain.STREAK.write_text(json.dumps({"boot": "boot-two", "streak": 2, "repaired": True}))
retain.FINDINGS.write_text(
    json.dumps([{"path": str(server_key), "action": "restored from copy", "boot": "boot-two", "at": time.time()}])
)
found = {f["id"]: f for f in health.check_protected()}
check(
    "repairs at two boots running: the card verdict",
    found["protected-files"]["status"] == "problem" and "protected.twice" in message_ids(found["protected-files"]),
    found.get("protected-files"),
)
retain.STREAK.write_text(json.dumps({"boot": "boot-two", "streak": 0, "repaired": False}))
retain.FINDINGS.write_text("[]")
check("a boot with no repairs clears the finding", health.check_protected() == [])

calls = []
retain.restore.run = lambda *a, **kw: calls.append(a) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
retain.install_units(True)
service = units / "system" / retain.SERVICE
sweep_service = units / "system" / retain.SWEEP_SERVICE
timer = units / "system" / retain.TIMER
check(
    "the boot check, the sweep's own service and the timer are written",
    service.is_file() and sweep_service.is_file() and timer.is_file(),
    calls,
)
check(
    "  the boot check runs after the NAND's mount and before the network and the hub",
    "After=local-fs.target nand-state.service" in service.read_text()
    and "Before=network.target irate-box.service" in service.read_text(),
)
check(
    "  the timer runs the sweep, not the boot check",
    f"Unit={retain.SWEEP_SERVICE}" in timer.read_text() and "retain sweep" in sweep_service.read_text(),
)
check(
    "  the boot check is enabled, and the timer started",
    ("systemctl", "enable", retain.SERVICE) in calls and ("systemctl", "enable", "--now", retain.TIMER) in calls,
    calls,
)
retain.install_units(False)
check(
    "  switching it off stops the timer and leaves nothing",
    ("systemctl", "disable", "--now", retain.TIMER) in calls
    and not service.exists()
    and not sweep_service.exists()
    and not timer.exists()
    and not retain.STORE.exists()
    and not retain.MANIFEST.exists(),
    calls,
)

# Extra stores: a NAND folder or a USB drive, named in retain.json.
store2 = T / "store2"
(T / "etc" / "retain.json").write_text(json.dumps({"stores": [str(store2)]}))
retain.sweep()
check(
    "an extra store gets the copy and its manifest",
    (store2 / "manifest.json").is_file() and (store2 / retain._ident(hub_env)).read_text() == hub_env.read_text(),
    list(store2.iterdir()) if store2.exists() else None,
)

# Damage the copy and its .prev in this store; the extra store's copy brings the file back.
real_owned = retain._package_owned
retain._package_owned = lambda path: False
good_env = hub_env.read_text()
put(hub_env, "damaged\n", 0o600)
entry = json.loads(retain.MANIFEST.read_text())["entries"][str(hub_env)]
os.utime(hub_env, ns=(entry["mtime_ns"], entry["mtime_ns"]))
retain._copy(hub_env).write_bytes(b"\xff" * 16)
retain._prev(hub_env).unlink(missing_ok=True)
(store2 / retain._ident(hub_env)).write_text(good_env)
repairs = retain.boot_check()
check(
    "  when the card's copy is bad, the extra store's copy is used",
    hub_env.read_text() == good_env and any("restored" in r["action"] for r in repairs),
    repairs,
)

# Extra stores as settings: keys go to the NAND, never to a removable drive unless its setting says so.
store3, store4 = T / "store3", T / "store4"
ca_key = put(etc / "tls" / "ca" / "ca.key", "CA\n", 0o600)
retain.set_store(str(store3), True, keys=True)
retain.set_store(str(store4), True, keys=False)
retain.sweep()
check(
    "an extra store setting: keys included where it says so, left out of the drive",
    (store3 / retain._ident(ca_key)).is_file()
    and not (store4 / retain._ident(ca_key)).exists()
    and any(p.is_file() for p in store4.iterdir()),
    (sorted(p.name for p in store3.iterdir()), sorted(p.name for p in store4.iterdir())),
)
retain.set_store(str(store3), False)
retain.set_store(str(store4), False)
check(
    "  switching one off deletes the copies irate-box made there, and the setting goes",
    not (store3 / retain._ident(ca_key)).exists()
    and not (store3 / "manifest.json").exists()
    and all(entry["path"] != str(store3) for entry in retain._store_entries()),
)

# Regeneration: the server key and chain from the CA, before the copy is tried.
(etc / "tls" / "ca").mkdir(parents=True, exist_ok=True)
ca_key.write_text("CA\n")
(etc / "tls" / "server.key").write_bytes(b"\xff" * 8)
(etc / "tls" / "server.key").chmod(0o640)
retain.sweep()
(etc / "tls" / "server.key").write_bytes(b"\xff" * 8)
(etc / "tls" / "server.key").chmod(0o640)
os.utime(
    etc / "tls" / "server.key",
    ns=(json.loads(retain.MANIFEST.read_text())["entries"][str(etc / "tls" / "server.key")]["mtime_ns"],) * 2,
)
for bad in (retain._copy(etc / "tls" / "server.key"), retain._prev(etc / "tls" / "server.key")):
    bad.write_bytes(b"\xff" * 8)
real_stores = retain.extra_stores
retain.extra_stores = lambda: []


class FakeTls:
    @staticmethod
    def make_cert():
        put(etc / "tls" / "server.key", "MADE\n", 0o640)
        return {}


import irate_box.root.tls as real_tls

real_tls_make = real_tls.make_cert
real_tls.make_cert = FakeTls.make_cert
repairs = retain.boot_check()
real_tls.make_cert = real_tls_make
retain.extra_stores = real_stores
check(
    "a server key that can be made again is, before the copy",
    (etc / "tls" / "server.key").read_text() == "MADE\n" and any("made again" in r["action"] for r in repairs),
    repairs,
)

# A good copy is used before a new server key is made.
put(etc / "tls" / "server.key", "COPY KEY\n", 0o640)
retain.sweep()
(etc / "tls" / "server.key").write_bytes(b"\xff" * 8)
os.utime(
    etc / "tls" / "server.key",
    ns=(json.loads(retain.MANIFEST.read_text())["entries"][str(etc / "tls" / "server.key")]["mtime_ns"],) * 2,
)
made = []
real_make = real_tls.make_cert
real_tls.make_cert = lambda: made.append(True)
repairs = retain.boot_check()
real_tls.make_cert = real_make
check(
    "a good copy is used before a new server key is made",
    (etc / "tls" / "server.key").read_text() == "COPY KEY\n"
    and not made
    and any("restored" in r["action"] for r in repairs),
    (made, repairs),
)

# nginx files that tls.initialise does not write come from the copy, and are never called regenerated.
htpasswd = put(nginx / "irate-box.htpasswd", "admin:hash\n", 0o644)
retain.sweep()
htpasswd.write_bytes(b"\xff" * 16)
os.utime(htpasswd, ns=(json.loads(retain.MANIFEST.read_text())["entries"][str(htpasswd)]["mtime_ns"],) * 2)
repairs = retain.boot_check()
check(
    "an nginx file tls.initialise does not write is restored from its copy, not called regenerated",
    htpasswd.read_text() == "admin:hash\n"
    and any("restored" in r["action"] for r in repairs)
    and not any("rendered" in r["action"] for r in repairs),
    repairs,
)
site = put(nginx / "conf.d" / "irate-box.conf", "server {}\n", 0o644)
retain.sweep()
site.write_bytes(b"\xff" * 16)
os.utime(site, ns=(json.loads(retain.MANIFEST.read_text())["entries"][str(site)]["mtime_ns"],) * 2)
for bad in (retain._copy(site), retain._prev(site)):
    bad.write_bytes(b"\xff" * 16)
retain.extra_stores = lambda: []
repairs = retain.boot_check()
retain.extra_stores = real_stores
check(
    "  with no copy either, it is a loss, not a false regeneration",
    any("no good copy" in r["action"] for r in repairs) and not any("rendered" in r["action"] for r in repairs),
    repairs,
)

# A package conffile with no copy comes back from its package.
journald.write_bytes(b"\xff" * 8)
journald.chmod(0o644)
retain.sweep()
journald.write_bytes(b"\xff" * 8)
journald.chmod(0o644)
for bad in (retain._copy(journald), retain._prev(journald)):
    bad.write_bytes(b"\xff" * 8)
real_owned, real_package = retain._package_owned, retain.restore.from_package
real_stores = retain.extra_stores
retain.extra_stores = lambda: []
retain._package_owned = lambda path: True


def fake_package(path):
    put(path, "[Journal]\nStorage=volatile\n")
    return "package"


retain.restore.from_package = fake_package
repairs = retain.boot_check()
retain._package_owned, retain.restore.from_package, retain.extra_stores = real_owned, real_package, real_stores
check(
    "a package conffile with no good copy is reinstalled",
    journald.read_text().startswith("[Journal]") and any("package" in r["action"] for r in repairs),
    repairs,
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
