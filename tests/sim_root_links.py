# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's writers never follow a link the hub planted (security review F3, F4, F5, F13; next-work
plan step 5). Each writer is run against a state folder with a symlink, a hardlink or a FIFO
waiting where it writes or reads, and the file the link leads to must be untouched. Runs as
any user: following a link is the same mistake whoever does it. python3 tests/sim_root_links.py"""
import json, os, re, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="root-links-"))
STATE, ETC = T / "state", T / "etc"
for d in (STATE / "control" / "results", STATE / "control" / "requests", ETC):
    d.mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(STATE), HUB_ETC_DIR=str(ETC), HUB_RUN_DIR=str(T / "run"),
                  HUB_SHARE_DIR=str(T / "share"), HUB_APP_TAKEN=str(T / "taken"))
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control, safeio  # noqa: E402
from irate_box.hub import netinv, uplink  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

SECRET = "root's file: must not change\n"
def victim():
    v = T / f"victim-{os.urandom(4).hex()}"
    v.write_text(SECRET); v.chmod(0o600)
    return v
def untouched(v):
    return v.read_text() == SECRET and (v.stat().st_mode & 0o777) == 0o600
def plant(path, kind="symlink"):
    path = Path(path); path.unlink(missing_ok=True)
    v = victim()
    os.symlink(v, path) if kind == "symlink" else os.link(v, path)
    return v

# --- the helper itself ---------------------------------------------------------------------
for kind in ("symlink", "hardlink"):
    p = STATE / "control" / f"x-{kind}.json"
    v = plant(p, kind)
    safeio.write(p, '{"ok": 1}')
    check(f"safeio.write over a {kind}: the target is untouched", untouched(v))
    check(f"safeio.write over a {kind}: the name is now a plain file of its own",
          not p.is_symlink() and p.stat().st_nlink == 1 and json.loads(p.read_text()) == {"ok": 1})
    p = STATE / "control" / f"log-{kind}"
    v = plant(p, kind)
    with safeio.open_new(p) as fh:
        fh.write("log line\n")
    check(f"safeio.open_new over a {kind}: the target is untouched", untouched(v) and p.read_text() == "log line\n")
v = victim().parent / "elsewhere"; v.mkdir()
(STATE / "swapped").symlink_to(v)
try:
    safeio.write(STATE / "swapped" / "f.json", "{}"); refused = False
except OSError:
    refused = True
check("safeio.write into a folder that is a link: refused", refused and not any(v.iterdir()))
p = STATE / "control" / "requests" / "abcdef0123456789.json"
v = plant(p)
try:
    safeio.read_request(p); read = True
except OSError:
    read = False
check("safeio.read_request: a link is not read", not read)
p.unlink(); os.mkfifo(p)
box = {}
def try_fifo():
    try:
        safeio.read_request(p); box["read"] = True
    except OSError:
        box["read"] = False
th = threading.Thread(target=try_fifo, daemon=True); th.start(); th.join(3)
check("safeio.read_request: a FIFO is refused, not waited on", not th.is_alive() and box.get("read") is False, box)
p.unlink()
p = STATE / "zim-copy.usb"
v = plant(p)
p.unlink()  # usbstick unlinks first; a link put back in the gap still stops it:
os.symlink(v, p)
try:
    safeio.create(p).close(); made = True
except OSError:
    made = False
check("safeio.create where a link is: refused, the target untouched", not made and untouched(v))

# --- each of root's writers in control/ (F3, F13) ------------------------------------------
writers = {
    "answer() results/<id>.json": (hub_control.RESULTS / "0123456789abcdef.json", lambda: hub_control.answer("0123456789abcdef", True, "done")),
    "update.json": (hub_control.UPDATE_STATE, lambda: hub_control._write_update_state({"x": 1})),
    "update-progress.json": (hub_control.UPDATE_PROGRESS, lambda: hub_control.Progress("check", 3)._write()),
    "security.json": (hub_control.SECURITY_STATE, lambda: hub_control._write_security({"findings": []})),
    "usb.json": (hub_control.USB_STATE, lambda: hub_control._write_usb({"sticks": []})),
    "access.json": (hub_control.ACCESS_STATE, lambda: hub_control._access_record({"drop": "public"})),
    "security-audit.json": (hub_control.AUDIT_STATE, lambda: hub_control.secdoctor.write_report({"counts": {}}, hub_control.AUDIT_STATE)),
    "uplink.json (the watchdog, F13)": (uplink.STATUS, lambda: uplink.write_status({"ok": True})),
    "netinv.json (F13)": (STATE / "control" / "netinv.json", lambda: netinv.write({"radios": []}, STATE / "control" / "netinv.json")),
}
for name, (path, run) in writers.items():
    for kind in ("symlink", "hardlink"):
        v = plant(path, kind)
        try:
            run(); err = None
        except Exception as exc:  # noqa: BLE001
            err = exc
        check(f"{name} over a {kind}: the target is untouched", untouched(v) and err is None, err)
    path.unlink(missing_ok=True)

# The request loop reads a link as nothing, and answers it.
req = hub_control.REQUESTS / "fedcba9876543210.json"
v = victim(); v.write_text(json.dumps({"action": "net-scan"})); v.chmod(0o600)
os.symlink(v, req)
hub_control.main()
ans = json.loads((hub_control.RESULTS / "fedcba9876543210.json").read_text())
check("a request that is a link is refused, not carried out", ans["ok"] is False and not req.exists(), ans)


# A request that is not an object, or one whose handler has a bug, is answered and gone: never
# found again by the next helper (stance review 2026-10-08, N6).
poison = {"0000000000000001": "[]", "0000000000000002": '"x"', "0000000000000003": json.dumps({"action": "uplink-set", "settings": [1]})}
for rid, body in poison.items():
    (hub_control.REQUESTS / f"{rid}.json").write_text(body)
try:
    hub_control.main(); crashed = None
except Exception as exc:  # noqa: BLE001
    crashed = exc
answers = {rid: json.loads((hub_control.RESULTS / f"{rid}.json").read_text()) for rid in poison if (hub_control.RESULTS / f"{rid}.json").exists()}
check("N6: a non-object request and a handler's own error are answered, not left to loop the helper",
      crashed is None and set(answers) == set(poison) and all(a["ok"] is False for a in answers.values())
      and not list(hub_control.REQUESTS.glob("*.json")), (crashed, answers))

# --- what is left as source checks ---------------------------------------------------------
src = {p: (REPO / p).read_text() for p in ("irate_box/root/hub_control.py", "irate_box/root/rtc.py", "irate_box/root/health.py",
                                           "irate_box/root/usbstick.py", "irate_box/root/security.py", "irate_box/hub/server.py",
                                           "irate_box/library/selfupdate.py", "install.sh", "scripts/tailscale-apply.sh")}
check("hub_control chowns nothing to the hub by path", "_for_hub" not in src["irate_box/root/hub_control.py"])
check("rtc's control/ files go through safeio", src["irate_box/root/rtc.py"].count("safeio.write(") >= 2
      and not re.search(r"(FOUND|STATUS)\.write_text", src["irate_box/root/rtc.py"]))
check("F4: the quarantine is never chowned by root", "chown(QUARANTINE" not in src["irate_box/root/health.py"]
      and '"mv", "-n", "-T"' in src["irate_box/root/health.py"])
check("kiwix_rebuild does not chown the hub's new library by path", "shutil.chown(new" not in src["irate_box/root/health.py"])
check("the security updates log is opened through safeio", "safeio.open_new(log_path)" in src["irate_box/root/security.py"])
check("the USB copy is made through safeio, on stick mounts without links", "safeio.create(tmp" in src["irate_box/root/usbstick.py"]
      and "nosymfollow" in src["irate_box/root/usbstick.py"])
check("the hub queues requests from its own folder, not control/",
      'STATE_DIR / f".request-{rid}.tmp"' in src["irate_box/hub/server.py"] and 'STATE_DIR / f".request-{rid}.tmp"' in src["irate_box/library/selfupdate.py"])
ts = src["scripts/tailscale-apply.sh"]
check("tailscale-apply writes the hub's file only as the hub, reads plain files only",
      "runuser -u $HUB_USER" in ts and '[ ! -L "$WANT" ]' in ts and not re.search(r"printf 'off\\\\n' >\"\$WANT\"", ts))
inst = src["install.sh"]
bad = [l.strip() for l in inst.splitlines() if re.search(r'install -d .*"\$STATE/', l)]
check("install.sh makes no $STATE folder with install -d (state_dir walks without links)", not bad, bad)
check("install.sh chowns nothing in control/", "control/netinv.json" not in inst.split("netinv --write")[1].split("\n")[1] if "netinv --write" in inst else True)

# --- app-install (F7): one read of the staged bundle, into root's own copy ------------------
import zipfile  # noqa: E402
staging = hub_control.APP_STAGING; staging.mkdir(parents=True, exist_ok=True)
def bundle(path, app="draw", extra=None):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("index.html", "<h1>draw</h1>")
        zf.writestr("irate-box-bundle.json", json.dumps({"app": app, "commit": "abc1234", "ref": "main", "built": "2026-10-06"}))
        for k, v in (extra or {}).items():
            zf.writestr(k, v)
good = staging / "draw-1.zip"; bundle(good)
msg = hub_control.install_app("draw", str(good))
check("F7: a staged bundle installs", "installed abc1234" in msg and (T / "share/apps/draw/index.html").exists(), msg)
check("F7: the staged zip is removed, and root's copy too", not good.exists() and not any((T / "taken").iterdir()))
v = victim(); link = staging / "draw-2.zip"; os.symlink(v, link)
try:
    hub_control.install_app("draw", str(link)); refused = False
except ValueError:
    refused = True
check("F7: a staged 'bundle' that is a link: refused, its target untouched", refused and untouched(v) and link.is_symlink())
link.unlink()
# The folder swapped for a link (to a folder of root's holding a real bundle): nothing read or removed.
elsewhere = T / "roots-folder"; elsewhere.mkdir(); bundle(elsewhere / "draw-3.zip")
real = staging; os.rename(real, T / "apps.real"); os.symlink(elsewhere, real)
try:
    hub_control.install_app("draw", str(real / "draw-3.zip")); refused = False
except (ValueError, OSError):
    refused = True
check("F7: the staging folder swapped for a link: refused, nothing removed there", refused and (elsewhere / "draw-3.zip").exists())
os.unlink(real); os.rename(T / "apps.real", real)
src_ = (REPO / "irate_box/root/hub_control.py").read_text()
check("F7: the check and the extraction read root's copy only", "_install_taken(app, taken, zip_path)" in src_ and "Path(zip_path).unlink" not in src_)

# --- the offline kit: kits/ as a link the hub planted (stance review 2026-10-08, N1) -------------
target = T / "kit-target"; target.mkdir(); target.chmod(0o700)
(STATE / "kits").symlink_to(target)
try:
    hub_control.offline_kit({"books": False}); refused = False
except (ValueError, OSError):
    refused = True
check("N1: kits/ as a link: refused, its target untouched", refused and (target.stat().st_mode & 0o777) == 0o700 and not any(target.iterdir()))
(STATE / "kits").unlink()
src_ = (REPO / "irate_box/root/hub_control.py").read_text()
check("N1: the kit is built in root's own work folder and moved into kits/ through its fd, with no chown or chmod by path",
      "dst_dir_fd=self.fd" in src_ and "KIT_WORK" in src_ and "os.chown(KITS" not in src_ and "os.chmod(KITS" not in src_)

# --- install.sh's state_dir, for real --------------------------------------------------------
fn = inst[inst.index("state_dir() {"):]
fn = fn[:fn.index("\n}\n") + 3]
me, grp_ = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip(), subprocess.run(["id", "-gn"], capture_output=True, text=True).stdout.strip()
S = T / "s2"; S.mkdir()
def state_dir(*paths, mode="755"):
    script = f'die() {{ echo "DIED: $*"; exit 1; }}\nSTATE={S}\n{fn}\nstate_dir {me} {grp_} {mode} ' + " ".join(map(str, paths))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)
r = state_dir(S / "a", S / "a" / "b")
check("state_dir makes nested folders", r.returncode == 0 and (S / "a" / "b").is_dir(), r.stdout + r.stderr)
target = T / "target-dir"; target.mkdir(); target.chmod(0o700)
(S / "zim").symlink_to(target)
r = state_dir(S / "zim")
check("state_dir refuses a folder that is a link, and leaves its target's mode", r.returncode != 0 and "DIED" in r.stdout
      and (target.stat().st_mode & 0o777) == 0o700, r.stdout + r.stderr)
(S / "git").symlink_to(target)
r = state_dir(S / "git" / "public")
check("state_dir refuses a link higher up the path, and makes nothing there", r.returncode != 0 and not (target / "public").exists(), r.stdout)

shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
