# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's writers never follow a link the hub planted. Each writer is run against a state folder with a symlink, a hardlink or a FIFO
waiting where it writes or reads, and the file the link leads to must be untouched. Runs as
any user: following a link is the same mistake whoever does it. python3 tests/sim_root_links.py
"""

import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="root-links-"))
STATE, ETC = T / "state", T / "etc"
for d in (STATE / "control" / "results", STATE / "control" / "requests", ETC):
    d.mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(STATE),
    HUB_ETC_DIR=str(ETC),
    HUB_RUN_DIR=str(T / "run"),
    HUB_SHARE_DIR=str(T / "share"),
    HUB_APP_TAKEN=str(T / "taken"),
)
sys.path.insert(0, str(REPO))
from irate_box.hub import netinv, uplink
from irate_box.root import hub_control, safeio

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


UNCHANGED = "root's file: must not change\n"


def victim():
    v = T / f"victim-{os.urandom(4).hex()}"
    v.write_text(UNCHANGED)
    v.chmod(0o600)
    return v


def untouched(v):
    return v.read_text() == UNCHANGED and (v.stat().st_mode & 0o777) == 0o600


def plant(path, kind="symlink"):
    path = Path(path)
    path.unlink(missing_ok=True)
    v = victim()
    os.symlink(v, path) if kind == "symlink" else os.link(v, path)
    return v


# --- the helper itself ---------------------------------------------------------------------
for kind in ("symlink", "hardlink"):
    p = STATE / "control" / f"x-{kind}.json"
    v = plant(p, kind)
    safeio.write(p, '{"ok": 1}')
    check(f"safeio.write over a {kind}: the target is untouched", untouched(v))
    check(
        f"safeio.write over a {kind}: the name is now a plain file of its own",
        not p.is_symlink() and p.stat().st_nlink == 1 and json.loads(p.read_text()) == {"ok": 1},
    )
    p = STATE / "control" / f"log-{kind}"
    v = plant(p, kind)
    with safeio.open_new(p) as fh:
        fh.write("log line\n")
    check(
        f"safeio.open_new over a {kind}: the target is untouched",
        untouched(v) and p.read_text() == "log line\n",
    )
v = victim().parent / "elsewhere"
v.mkdir()
(STATE / "swapped").symlink_to(v)
try:
    safeio.write(STATE / "swapped" / "f.json", "{}")
    refused = False
except OSError:
    refused = True
check(
    "safeio.write into a folder that is a link: refused",
    refused and not any(v.iterdir()),
)
p = STATE / "control" / "requests" / "abcdef0123456789.json"
v = plant(p)
try:
    safeio.read_request(p)
    read = True
except OSError:
    read = False
check("safeio.read_request: a link is not read", not read)
p.unlink()
os.mkfifo(p)
box = {}


def try_fifo():
    try:
        safeio.read_request(p)
        box["read"] = True
    except OSError:
        box["read"] = False


th = threading.Thread(target=try_fifo, daemon=True)
th.start()
th.join(3)
check(
    "safeio.read_request: a FIFO is refused, not waited on",
    not th.is_alive() and box.get("read") is False,
    box,
)
p.unlink()
p = STATE / "zim-copy.usb"
v = plant(p)
p.unlink()  # usbstick unlinks first; a link put back in the gap still stops it:
os.symlink(v, p)
try:
    safeio.create(p).close()
    made = True
except OSError:
    made = False
check(
    "safeio.create where a link is: refused, the target untouched",
    not made and untouched(v),
)

# --- each of root's writers in control/ ------------------------------------------
writers = {
    "answer() results/<id>.json": (
        hub_control.RESULTS / "0123456789abcdef.json",
        lambda: hub_control.answer("0123456789abcdef", True, "done"),
    ),
    "update.json": (
        hub_control.UPDATE_STATE,
        lambda: hub_control._write_update_state({"x": 1}),
    ),
    "update-progress.json": (
        hub_control.UPDATE_PROGRESS,
        lambda: hub_control.Progress("check", 3)._write(),
    ),
    "security.json": (
        hub_control.SECURITY_STATE,
        lambda: hub_control._write_security({"findings": []}),
    ),
    "usb.json": (hub_control.USB_STATE, lambda: hub_control._write_usb({"sticks": []})),
    "access.json": (
        hub_control.ACCESS_STATE,
        lambda: hub_control._access_record({"drop": "public"}),
    ),
    "security-audit.json": (
        hub_control.AUDIT_STATE,
        lambda: hub_control.secdoctor.write_report({"counts": {}}, hub_control.AUDIT_STATE),
    ),
    "uplink.json (the watchdog, F13)": (
        uplink.STATUS,
        lambda: uplink.write_status({"ok": True}),
    ),
    "netinv.json": (
        STATE / "control" / "netinv.json",
        lambda: netinv.write({"radios": []}, STATE / "control" / "netinv.json"),
    ),
}
for name, (path, run) in writers.items():
    for kind in ("symlink", "hardlink"):
        v = plant(path, kind)
        try:
            run()
            err = None
        except Exception as exc:  # noqa: BLE001
            err = exc
        check(
            f"{name} over a {kind}: the target is untouched",
            untouched(v) and err is None,
            err,
        )
    path.unlink(missing_ok=True)

# The request loop reads a link as nothing, and answers it.
req = hub_control.REQUESTS / "fedcba9876543210.json"
v = victim()
v.write_text(json.dumps({"action": "net-scan"}))
v.chmod(0o600)
os.symlink(v, req)
hub_control.main()
ans = json.loads((hub_control.RESULTS / "fedcba9876543210.json").read_text())
check(
    "a request that is a link is refused, not carried out",
    ans["ok"] is False and not req.exists(),
    ans,
)


# A request that is not an object, or one whose handler has a bug, is answered and gone: never
# found again by the next helper.
poison = {
    "0000000000000001": "[]",
    "0000000000000002": '"x"',
    "0000000000000003": json.dumps({"action": "uplink-set", "settings": [1]}),
}
for rid, body in poison.items():
    (hub_control.REQUESTS / f"{rid}.json").write_text(body)
try:
    hub_control.main()
    crashed = None
except Exception as exc:  # noqa: BLE001
    crashed = exc
answers = {
    rid: json.loads((hub_control.RESULTS / f"{rid}.json").read_text())
    for rid in poison
    if (hub_control.RESULTS / f"{rid}.json").exists()
}
check(
    "a non-object request and a handler's own error are answered, not left to loop the helper",
    crashed is None
    and set(answers) == set(poison)
    and all(a["ok"] is False for a in answers.values())
    and not list(hub_control.REQUESTS.glob("*.json")),
    (crashed, answers),
)
# A folder named like a request (audit M2): removed and answered, and the request behind it served.
(hub_control.REQUESTS / "0000000000000004.json").mkdir()
(hub_control.REQUESTS / "0000000000000004.json" / "inner").write_text("x")
time.sleep(0.01)
(hub_control.REQUESTS / "0000000000000005.json").write_text("[]")
try:
    hub_control.main()
    crashed = None
except Exception as exc:  # noqa: BLE001
    crashed = exc
check(
    "a folder named like a request does not stop the helper",
    crashed is None
    and not list(hub_control.REQUESTS.iterdir())
    and all((hub_control.RESULTS / f"000000000000000{n}.json").exists() for n in (4, 5)),
    crashed,
)

# --- what is left as source checks ---------------------------------------------------------
src = {
    p: (REPO / p).read_text()
    for p in (
        "irate_box/root/hub_control.py",
        "irate_box/root/rtc.py",
        "irate_box/root/health.py",
        "irate_box/root/usbstick.py",
        "irate_box/root/security.py",
        "irate_box/hub/server.py",
        "irate_box/library/selfupdate.py",
        "install.sh",
        "scripts/tailscale-apply.sh",
    )
}
check(
    "hub_control chowns nothing to the hub by path",
    "_for_hub" not in src["irate_box/root/hub_control.py"],
)
check(
    "rtc's control/ files go through safeio",
    src["irate_box/root/rtc.py"].count("safeio.write(") >= 2
    and not re.search(r"(FOUND|STATUS)\.write_text", src["irate_box/root/rtc.py"]),
)
check(
    "the quarantine is never chowned by root",
    "chown(QUARANTINE" not in src["irate_box/root/health.py"] and '"mv", "-n", "-T"' in src["irate_box/root/health.py"],
)
check(
    "kiwix_rebuild does not chown the hub's new library by path",
    "shutil.chown(new" not in src["irate_box/root/health.py"],
)
check(
    "the security updates log is opened through safeio",
    "safeio.open_new(log_path)" in src["irate_box/root/security.py"],
)
check(
    "the USB copy is made through safeio, on stick mounts without links",
    "safeio.create(tmp" in src["irate_box/root/usbstick.py"] and "nosymfollow" in src["irate_box/root/usbstick.py"],
)
check(
    "the hub queues requests from its own folder, not control/",
    'STATE_DIR / f".request-{rid}.tmp"' in src["irate_box/hub/server.py"]
    and 'STATE_DIR / f".request-{rid}.tmp"' in src["irate_box/library/selfupdate.py"],
)
ts = src["scripts/tailscale-apply.sh"]
check(
    "tailscale-apply writes the hub's file only as the hub, reads plain files only",
    "runuser -u $HUB_USER" in ts and '[ ! -L "$WANT" ]' in ts and not re.search(r"printf 'off\\\\n' >\"\$WANT\"", ts),
)
inst = src["install.sh"]
bad = [line.strip() for line in inst.splitlines() if re.search(r'install -d .*"\$STATE/', line)]
check(
    "install.sh makes no $STATE folder with install -d (state_dir walks without links)",
    not bad,
    bad,
)
check(
    "install.sh chowns nothing in control/",
    ("control/netinv.json" not in inst.split("netinv --write")[1].split("\n")[1] if "netinv --write" in inst else True),
)

# --- app-install: one read of the staged bundle, into root's own copy ------------------
import zipfile

staging = hub_control.APP_STAGING
staging.mkdir(parents=True, exist_ok=True)


def bundle(path, app="draw", extra=None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.html", "<h1>draw</h1>")
        zf.writestr(
            "irate-box-bundle.json",
            json.dumps({"app": app, "commit": "abc1234", "ref": "main", "built": "2026-10-06"}),
        )
        for k, v in (extra or {}).items():
            zf.writestr(k, v)


good = staging / "draw-1.zip"
bundle(good)
msg = hub_control.install_app("draw", str(good))
check(
    "a staged bundle installs",
    msg["id"] == "control.app.installed"
    and msg["args"]["commit"] == "abc1234"
    and (T / "share/apps/draw/index.html").exists(),
    msg,
)
check(
    "the staged zip is removed, and root's copy too",
    not good.exists() and not any((T / "taken").iterdir()),
)
for unsafe in ("../escape.html", "/escape.html", "nested/../../escape.html"):
    bad_bundle = staging / "unsafe.zip"
    bundle(bad_bundle, extra={unsafe: "outside"})
    try:
        hub_control.install_app("draw", str(bad_bundle))
        refused = False
    except ValueError:
        refused = True
    check(
        f"unsafe bundle member refused: {unsafe}",
        refused and not (T / "share/apps/escape.html").exists() and not any((T / "taken").iterdir()),
    )
    bad_bundle.unlink(missing_ok=True)

with patch.object(hub_control.urllib.request, "build_opener") as opener:
    for unsafe_url in (
        "file:///etc/passwd",
        "ftp://example.org/file",
        "http://example.org/",
    ):
        try:
            hub_control._web_open(Request(unsafe_url), timeout=1)
            refused = False
        except ValueError:
            refused = True
        check(f"unsafe download URL refused: {unsafe_url}", refused and not opener.called)
    request = Request("https://example.org/file")
    hub_control._web_open(request, timeout=1)
    redirect = opener.call_args.args[0]
    for unsafe_url in ("file:///etc/passwd", "http://example.org/file"):
        try:
            redirect.redirect_request(request, None, 302, "Found", {}, unsafe_url)
            refused = False
        except ValueError:
            refused = True
        check(f"unsafe download redirect refused: {unsafe_url}", refused)
    local_origin = "http://127.0.0.1:8384"
    local_request = Request(f"{local_origin}/rest/config/gui")
    hub_control._web_open(local_request, timeout=1, local_origin=local_origin)
    redirect = opener.call_args.args[0]
    for unsafe_url in ("http://127.0.0.1:9999/", "https://example.org/"):
        try:
            redirect.redirect_request(local_request, None, 302, "Found", {}, unsafe_url)
            refused = False
        except ValueError:
            refused = True
        check(f"local API redirect cannot escape its origin: {unsafe_url}", refused)

v = victim()
link = staging / "draw-2.zip"
os.symlink(v, link)
try:
    hub_control.install_app("draw", str(link))
    refused = False
except ValueError:
    refused = True
check(
    "a staged 'bundle' that is a link: refused, its target untouched",
    refused and untouched(v) and link.is_symlink(),
)
link.unlink()
# The folder swapped for a link (to a folder of root's holding a real bundle): nothing read or removed.
elsewhere = T / "roots-folder"
elsewhere.mkdir()
bundle(elsewhere / "draw-3.zip")
real = staging
os.rename(real, T / "apps.real")
os.symlink(elsewhere, real)
try:
    hub_control.install_app("draw", str(real / "draw-3.zip"))
    refused = False
except (ValueError, OSError):
    refused = True
check(
    "the staging folder swapped for a link: refused, nothing removed there",
    refused and (elsewhere / "draw-3.zip").exists(),
)
os.unlink(real)
os.rename(T / "apps.real", real)
src_ = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "the check and the extraction read root's copy only",
    "_install_taken(app, taken, zip_path)" in src_ and "Path(zip_path).unlink" not in src_,
)

# --- the offline kit: kits/ as a link the hub planted ---------------------------------------------
target = T / "kit-target"
target.mkdir()
target.chmod(0o700)
(STATE / "kits").symlink_to(target)
try:
    hub_control.offline_kit({"books": False})
    refused = False
except (ValueError, OSError):
    refused = True
check(
    "kits/ as a link: refused, its target untouched",
    refused and (target.stat().st_mode & 0o777) == 0o700 and not any(target.iterdir()),
)
(STATE / "kits").unlink()
src_ = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "the kit is built in root's own work folder and moved into kits/ through its fd, with no chown or chmod by path",
    "dst_dir_fd=self.fd" in src_ and "KIT_WORK" in src_ and "os.chown(KITS" not in src_ and "os.chmod(KITS" not in src_,
)

# --- install.sh's state_dir, for real --------------------------------------------------------
fn = inst[inst.index("state_dir() {") :]
fn = fn[: fn.index("\n}\n") + 3]
me, grp_ = (
    pwd.getpwuid(os.getuid()).pw_name,
    grp.getgrgid(os.getgid()).gr_name,
)
S = T / "s2"
S.mkdir()


def state_dir(*paths, mode="755"):
    script = f'die() {{ echo "DIED: $*"; exit 1; }}\nSTATE={S}\n{fn}\nstate_dir {me} {grp_} {mode} ' + " ".join(
        map(str, paths)
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


r = state_dir(S / "a", S / "a" / "b")
check(
    "state_dir makes nested folders",
    r.returncode == 0 and (S / "a" / "b").is_dir(),
    r.stdout + r.stderr,
)
target = T / "target-dir"
target.mkdir()
target.chmod(0o700)
(S / "zim").symlink_to(target)
r = state_dir(S / "zim")
check(
    "state_dir refuses a folder that is a link, and leaves its target's mode",
    r.returncode != 0 and "DIED" in r.stdout and (target.stat().st_mode & 0o777) == 0o700,
    r.stdout + r.stderr,
)
(S / "git").symlink_to(target)
r = state_dir(S / "git" / "public")
check(
    "state_dir refuses a link higher up the path, and makes nothing there",
    r.returncode != 0 and not (target / "public").exists(),
    r.stdout,
)

# --- the kit's copies of the hub's folders (audit H1, L20): read as the hub, unpacked by tarfile's data filter ---
from irate_box.msg import Refused

repo = STATE / "git" / "public" / "r.git"
repo.mkdir(parents=True)
(repo / "HEAD").write_text("ref: refs/heads/main\n")
hub_control._copy_as_hub(repo, T / "copy-ok")
check(
    "a hub folder copies through the hub's tar",
    (T / "copy-ok" / "HEAD").read_text().startswith("ref:"),
)
v = victim()
(repo / "leak").symlink_to(v)
try:
    hub_control._copy_as_hub(repo, T / "copy-leak")
    refused = False
except Exception:  # noqa: BLE001
    refused = True
check(
    "a link out of a hub folder: refused by the data filter, nothing copied through it",
    refused and untouched(v) and not (T / "copy-leak" / "leak").exists(),
)
(repo / "leak").unlink()
hub_control.ZIM_DIR.mkdir(parents=True, exist_ok=True)
book = hub_control.ZIM_DIR / "a.zim"
book.write_bytes(b"zim one")
import tarfile

part = T / "kit.tar"
tarfile.open(part, "w").close()
hub_control._append_books(part, [book], "kit/zim/", hub_control._hash_as_hub([book]))
with tarfile.open(part) as tf:
    got = {m.name: (m.mode, tf.extractfile(m).read()) for m in tf}
check(
    "a book goes into the kit as a plain 0644 file with its hash checked",
    got == {"kit/zim/a.zim": (0o644, b"zim one")},
    got,
)
for case, sums in (("changed after hashing", {"a.zim": "0" * 64}), ("a link", None)):
    if case == "a link":
        book.unlink()
        v = victim()
        book.symlink_to(v)
    tarfile.open(part, "w").close()
    try:
        hub_control._append_books(part, [book], "kit/zim/", sums)
        refused = False
    except Refused:
        refused = True
    check(f"a book {case}: refused", refused)
check(
    "no copytree or root tar of the hub's folders is left",
    "copytree(d" not in src_ and 'str(ZIM_DIR), "-rf"' not in src_,
)

# --- root's own state beside $STATE, not in it (audit H2, M1, L18) -----------------------------
from irate_box.root import crashwatch, nmhandover, otherap

check(
    "root's kept files live beside $STATE",
    all(not p.is_relative_to(STATE) for p in (otherap.KEPT, crashwatch.DIR, nmhandover.BACKUP)),
    safeio.ROOT_STATE,
)


def linked_folder(path, files):
    d = T / f"victim-dir-{os.urandom(4).hex()}"
    d.mkdir()
    d.chmod(0o700)
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    Path(path).symlink_to(d)
    return d


# H2: an old kept profile, its folder swapped for a link.
NM = T / "nm"
NM.mkdir()
otherap.KEYFILES = NM
d = linked_folder(STATE / "other-ap", {"x.nmconnection": UNCHANGED})
uid = "12345678-1234-1234-1234-123456789abc"
otherap.RECORD.write_text(
    json.dumps(
        {
            uid: {
                "name": "a",
                "ssid": "s",
                "choice": "removed",
                "at": 0,
                "kept": str(STATE / "other-ap" / "x.nmconnection"),
                "file": str(NM / "x.nmconnection"),
            }
        }
    )
)
try:
    otherap.undo(uid)
    refused = False
except Refused:
    refused = True
check(
    "other-ap undo: an old kept copy through a link is not read",
    refused and not (NM / "x.nmconnection").exists(),
)
otherap.forget(uid)
check(
    "other-ap forget: nothing deleted through the link",
    (d / "x.nmconnection").read_text() == UNCHANGED,
)
# M1: crash watch's old folder moved once; a link in its place left alone.
old = STATE / "crashwatch"
old.mkdir()
old.chmod(0o700)
(old / "run.json").write_text('{"boot": "b"}')
said = crashwatch.migrate()
check(
    "crash watch's old files move to root's own folder",
    (crashwatch.DIR / "run.json").exists() and not old.exists() and (crashwatch.DIR.stat().st_mode & 0o777) == 0o700,
    said,
)
d = linked_folder(old, {"snap.log": UNCHANGED})
said = crashwatch.migrate()
crashwatch.append_snapshot("look\n", sync=False)
check(
    "crash watch: a link at the old folder is not followed, and snapshots go to root's own",
    (d / "snap.log").read_text() == UNCHANGED
    and sorted(p.name for p in d.iterdir()) == ["snap.log"]
    and crashwatch.SNAP.read_text().endswith("look\n"),
    said,
)
# L18: an old hand-over's copies, their folder swapped for a link.
NP = T / "netplan"
NP.mkdir()
d = linked_folder(STATE / "netplan-handover", {"20260101-x/30-wifis.yaml": "network: {}\n"})
nmhandover.RECORD.write_text(
    json.dumps(
        [
            {
                "name": "30-wifis.yaml",
                "file": str(NP / "30-wifis.yaml"),
                "at": 0,
                "backup": str(STATE / "netplan-handover" / "20260101-x"),
                "connections": [],
            }
        ]
    )
)
try:
    nmhandover.undo("30-wifis.yaml")
    refused = False
except Refused:
    refused = True
check(
    "netplan hand-over undo: an old copy through a link is not used",
    refused and not (NP / "30-wifis.yaml").exists(),
)

check("security policy starts with hub administration", hub_control.policy_mode() == "hub")
try:
    hub_control.policy_set({"mode": "console"})
    refused = False
except Refused:
    refused = True
check(
    "locking security policy requires confirmation",
    refused and hub_control.policy_mode() == "hub",
)
hub_control.policy_set({"mode": "console", "confirmed": True})
ran = []
hub_control.ACTIONS["policy-test-read"] = lambda req: ran.append("read") or "read"
for number, req in enumerate(
    (
        {"action": "security-policy", "mode": "hub"},
        {"action": "tls-switch", "on": False},
        {"action": "policy-test-read"},
    ),
    20,
):
    (hub_control.REQUESTS / f"{number:016d}.json").write_text(json.dumps(req))
hub_control.main()
answers = [json.loads((hub_control.RESULTS / f"{number:016d}.json").read_text()) for number in range(20, 23)]
check(
    "a queued request cannot unlock policy or change TLS, but ordinary operations continue",
    [answer["ok"] for answer in answers] == [False, False, True]
    and ran == ["read"]
    and hub_control.policy_mode() == "console",
    answers,
)
hub_control.POLICY_FILE.write_text("broken")
check("malformed root policy fails closed", hub_control.policy_mode() == "console")
hub_control.POLICY_FILE.unlink()
target = victim()
hub_control.POLICY_FILE.symlink_to(target)
check(
    "a symlinked policy fails closed",
    hub_control.policy_mode() == "console" and untouched(target),
)
hub_control.POLICY_FILE.unlink()
hub_control.policy_set({"mode": "hub", "confirmed": True})
check(
    "console recovery restores hub policy changes",
    hub_control.policy_publish() == "hub" and json.loads(hub_control.POLICY_STATE.read_text())["mode"] == "hub",
)
shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
