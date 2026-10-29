# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The second copy of the settings and keys, and the check at boot (retain.py; notes: box-doctor-hardening-plan
Part 2, step 4), offline: the copy, a 0xFF file restored from it, a missing file restored, a change the owner made
left alone and copied by the next sweep, a copy and its .prev both bad as a problem, Box doctor's finding, and the
units installed and removed. python3 tests/sim_retain.py"""

import json
import os
import sys
import tempfile
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


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


hub_env = put(etc / "hub.env", "HUB_URL=/\n")
server_key = put(etc / "tls" / "server.key", "KEY\n")
front = put(etc / "tls" / "front" / "nginx-admin.conf", "front\n")
settings = put(state / "settings.json", '{"hub_title": "Box"}\n')
site = put(nginx / "conf.d" / "irate-box.conf", "server {}\n")
put(journald, "[Journal]\nStorage=volatile\n")
put(nm / "hub.nmconnection", "[connection]\nid=hub\n")

check("the copy is taken", retain.sweep() == [] and retain.MANIFEST.is_file())
manifest = json.loads(retain.MANIFEST.read_text())
check(
    "  every protected file is in it, with its copy",
    {str(hub_env), str(server_key), str(front), str(settings), str(site), str(journald)} <= set(manifest["entries"]),
    sorted(manifest["entries"])[:8],
)
check("  and the copy reads back the same", retain._copy(hub_env).read_text() == "HUB_URL=/\n")

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
check("  the damaged file is kept beside it", Path(str(server_key) + ".damaged").exists())

server_key.unlink()
retain.boot_check()
check("a missing file is put back from the copy", server_key.read_text() == "KEY\n")

# A change the owner made: a different mtime and readable contents. It is left alone, and the next sweep copies it.
put(server_key, "NEW KEY\n")
retain.boot_check()
check("a change the owner made is not restored over", server_key.read_text() == "NEW KEY\n")
retain.sweep()
check("  the next sweep copies it", retain._copy(server_key).read_text() == "NEW KEY\n")
retain.boot_check()
check("  and the boot check is happy with it", server_key.read_text() == "NEW KEY\n")

# Both the copy and its .prev bad. The live file keeps its mtime, so this is damage, not an edit.
entry = json.loads(retain.MANIFEST.read_text())["entries"][str(hub_env)]
put(hub_env, "different\n")
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

# Two boots running with repairs is the card verdict.
retain.FINDINGS.unlink(missing_ok=True)
for boot in ("boot-one", "boot-two"):
    retain._findings([{"path": str(server_key), "action": "restored from copy"}], "boot", boot=boot)
found = {f["id"]: f for f in health.check_protected()}
check(
    "repairs at two boots running: the card verdict",
    found["protected-files"]["status"] == "problem" and "protected.twice" in message_ids(found["protected-files"]),
    found.get("protected-files"),
)

calls = []
retain.restore.run = lambda *a, **kw: calls.append(a) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
retain.install_units(True)
check(
    "the units are written and the timer started",
    (units / "system" / retain.SERVICE).is_file() and (units / "system" / retain.TIMER).is_file() and calls,
    calls,
)
check(
    "  the service runs before the network and the hub, and after the local file systems",
    "Before=network.target irate-box.service" in (units / "system" / retain.SERVICE).read_text()
    and "After=local-fs.target" in (units / "system" / retain.SERVICE).read_text(),
)
retain.install_units(False)
check(
    "  switching it off leaves nothing",
    not (units / "system" / retain.SERVICE).exists() and not (units / "system" / retain.TIMER).exists(),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
