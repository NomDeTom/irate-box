#!/usr/bin/env python3
"""Root helper for the admin page: the few things the unprivileged hub cannot do itself.

The hub (server.py, user hub) writes one JSON request per file into
$HUB_STATE_DIR/control/requests/. irate-box-control.path notices the folder is not empty
and runs this as root, once per batch. Each request is checked against an allow-list,
carried out, answered in control/results/<id>.json, and deleted. Nothing else is accepted:

  {"id": ..., "action": "service", "unit": "<allowed unit>", "op": "start|stop|restart|enable|disable"}
      Optional services get every op; caddy and the hub itself only restart.
  {"id": ..., "action": "password", "password": "<new admin password>"[, "setup": true]}
      The one admin login: Caddy's basic_auth hashes (/admin, /sync, /term), ttyd's
      credential, Syncthing's GUI login, and /etc/hub/admin-password. With "setup", this is
      the first-use form on an unclaimed box: accepted only while UNCLAIMED exists, which
      it then deletes, so the first password chosen is the only one set this way.
  {"id": ..., "action": "update-check"}
      Clone or fast-forward irate-box into a root-owned cache, from the repository and branch
      install.sh recorded in /etc/hub/install-options, and say what is new. The summary goes
      to control/update.json. The hub never writes the code that root will run.
  {"id": ..., "action": "update-fetch"}
      Check first if the cache does not hold what the last check found, then verify it
      (verify_update) and prefetch the release downloads its install.sh will want into the
      download cache. Every check goes into control/update.json.
  {"id": ..., "action": "update-install"}
      Run that checkout's install.sh with the recorded options and the download cache;
      output in control/update.log. Refused unless the fetched commit passed verification.
  While any of these three runs, control/update-progress.json says which step it is on, of
  how many, and how far a download has got (Progress), for the bars on /admin.
  {"id": ..., "action": "update-doctor"}
      For when a check fails: look at everything an update needs (doctor) and write a plain
      report, each finding with what to do, to control/doctor.json.
  {"id": ..., "action": "update-clear-cache"}
      Remove the cached clone and downloads, so the next check starts afresh.
  {"id": ..., "action": "security-scan"}
      What the box exposes and how it is set up (security.py): to control/security.json.
  {"id": ..., "action": "security-fix", "choice": "<one of the page's offers>"}
      Carry out one fix the Security page offered (security.fix: a drop-in or a unit switched
      off, each recorded with how to undo it), then scan again.
  {"id": ..., "action": "app-install", "app": "draw|mermaid|serial|room", "zip": "<staged bundle>"}
      Check a bundle the librarian staged in $STATE/library/apps/ and swap it in under
      /usr/share/hub (the previous copy kept). {"action": "app-rollback", "app": ...} swaps back.

From a root shell, the same file also resets the login, for an owner who has lost it:

    sudo python3 /opt/irate-box/hub_control.py reset-password

which puts the box back to unclaimed: /admin asks the next visitor to choose a password, and
install.sh --apps-from-actions uses  hub_control.py install-app APP ZIP  for a first install.

Stdlib only.
"""

import hashlib
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

import manifests
import security

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
HUB_USER = os.environ.get("HUB_USER", "hub")
CONTROL = STATE / "control"
REQUESTS = CONTROL / "requests"
RESULTS = CONTROL / "results"
CADDYFILE = Path(os.environ.get("HUB_CADDYFILE", "/etc/caddy/Caddyfile"))
# Present while no admin password has been chosen (install.sh creates it). Caddy then lets
# /admin through without a login, and the hub serves only the set-the-password page.
UNCLAIMED = CADDYFILE.parent / "irate-box-unclaimed"
# On a box whose Caddy is the owner's, the hub's site (and so its login) is this file, imported
# by their Caddyfile (install.sh); otherwise the Caddyfile is wholly the hub's.
CADDY_SITE = CADDYFILE.parent / "irate-box.caddy"


def _login_file():
    return CADDY_SITE if CADDY_SITE.exists() else CADDYFILE
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
UPDATE_SRC = Path(os.environ.get("HUB_UPDATE_DIR", "/var/cache/irate-box/src"))
UPDATE_STATE = CONTROL / "update.json"
UPDATE_LOG = CONTROL / "update.log"
UPDATE_PROGRESS = CONTROL / "update-progress.json"
DOCTOR_STATE = CONTROL / "doctor.json"
SECURITY_STATE = CONTROL / "security.json"
SECURITY_LOG = CONTROL / security.UPDATES_LOG_NAME
# Release downloads install.sh would otherwise make itself (install.sh --download-cache).
DOWNLOADS = Path(os.environ.get("HUB_DOWNLOAD_CACHE", "/var/cache/irate-box/downloads"))
INSTALL_TIMEOUT = 45 * 60
# How many "==> " steps an install prints, until one has run here and been counted.
INSTALL_STEPS_GUESS = 12

OPS_ALL = ("start", "stop", "restart", "enable", "disable")
# The units each app's manifest offers for control (apps.d/, beside this file and as
# root-owned as it), and the two that may only be restarted.
MANIFESTS = manifests.load()
UNITS = {unit.replace("@hub.", f"@{HUB_USER}."): OPS_ALL for unit in manifests.controllable_units(MANIFESTS)}
UNITS.update({"caddy.service": ("restart",), "irate-box.service": ("restart",)})
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
HASH_LINE = re.compile(r"^(\s*admin\s+)\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}\s*$", re.M)
MIN_PASSWORD = 8


def run(*cmd, timeout=120, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _for_hub(path):
    hub = pwd.getpwnam(HUB_USER)
    os.chown(path, hub.pw_uid, hub.pw_gid)


def service(req):
    unit, op = str(req.get("unit", "")), str(req.get("op", ""))
    if unit not in UNITS:
        raise ValueError(f"{unit} is not a unit the admin page controls")
    if op not in UNITS[unit]:
        raise ValueError(f"{op} is not allowed for {unit}")
    if run("systemctl", "cat", unit).returncode != 0:
        raise ValueError(f"{unit} is not installed")
    args = {"enable": ("enable", "--now"), "disable": ("disable", "--now")}.get(op, (op,))
    if unit == "irate-box.service":
        # Restarting the hub from a request the hub made: answer first, then go.
        subprocess.Popen(["systemd-run", "--on-active=2", "systemctl", "restart", unit])
        return f"{unit} restarting"
    out = run("systemctl", *args, unit)
    if out.returncode != 0:
        raise ValueError((out.stderr or out.stdout).strip().splitlines()[-1] if (out.stderr or out.stdout).strip() else f"systemctl {op} failed")
    return f"{unit}: {op} done, now {run('systemctl', 'is-active', unit).stdout.strip()}"


def password(req):
    pw = str(req.get("password", ""))
    if len(pw) < MIN_PASSWORD or len(pw) > 128 or any(c in pw for c in "\n\r\0"):
        raise ValueError(f"the password must be {MIN_PASSWORD}-128 characters, on one line")
    if req.get("setup") and not UNCLAIMED.exists():
        raise ValueError("the admin password has already been set; log in to change it")
    done = set_login(pw)
    if req.get("setup"):
        UNCLAIMED.unlink(missing_ok=True)
        return "admin password set: " + done
    return "admin password changed: " + done


def reset_password():
    """Back to unclaimed: an unknown random login everywhere, and the first-use form on
    /admin. For the console, when the owner has lost the password."""
    set_login(secrets.token_urlsafe(24), keep=False)
    UNCLAIMED.write_text("no admin password chosen yet (hub_control.py reset-password)\n")
    UNCLAIMED.chmod(0o644)
    return "admin password reset: /admin now asks the next visitor to choose one"


def set_login(pw, keep=True):
    """Put pw everywhere the admin login is used. keep=False leaves /etc/hub/admin-password
    out (removed): a random placeholder nobody is meant to know."""
    # Caddy first: if the new config does not validate, nothing else has changed.
    hashed = run("caddy", "hash-password", "--plaintext", pw)
    if hashed.returncode != 0:
        raise ValueError("caddy hash-password failed")
    new_hash = hashed.stdout.strip()
    target = _login_file()
    text = target.read_text()
    updated, count = HASH_LINE.subn(lambda m: m.group(1) + new_hash, text)
    if not count:
        raise ValueError(f"no admin login found in {target}")
    candidate = target.with_name(target.name + ".new")
    candidate.write_text(updated)
    check = run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate))
    if check.returncode != 0:
        candidate.unlink(missing_ok=True)
        raise ValueError("the new Caddy config did not validate; nothing was changed")
    os.replace(candidate, target)
    # The hub's site inside the owner's config: the whole of it must still validate.
    if target != CADDYFILE and run("caddy", "validate", "--adapter", "caddyfile", "--config", str(CADDYFILE)).returncode != 0:
        target.write_text(text)
        raise ValueError("the Caddy config did not validate with the new login; nothing was changed")
    # Restart, not reload: the Caddyfile turns Caddy's admin API off, which reload needs.
    subprocess.Popen(["systemd-run", "--on-active=1", "systemctl", "restart", "caddy"])

    secret = ETC / "admin-password"
    if keep:
        secret.write_text(pw + "\n")
        secret.chmod(0o600)
    else:
        secret.unlink(missing_ok=True)
    ttyd_env = ETC / "ttyd.env"
    if ttyd_env.exists():
        # Explicitly 600: a umask only applies when a file is created, and this one exists.
        ttyd_env.chmod(0o600)
        ttyd_env.write_text(f"TTYD_CREDENTIAL=admin:{pw}\n")
        ttyd_env.chmod(0o600)
        run("systemctl", "try-restart", "ttyd")
    done = [f"Caddy ({count} logins)", "ttyd" if ttyd_env.exists() else None]
    if run("systemctl", "is-active", "--quiet", f"syncthing@{HUB_USER}").returncode == 0:
        st = run("runuser", "-u", HUB_USER, "--", "env", f"HOME={STATE}",
                 "syncthing", "cli", "config", "gui", "password", "set", pw)
        done.append("Syncthing" if st.returncode == 0 else "Syncthing (failed)")
    return ", ".join(d for d in done if d)


# --- updates ---------------------------------------------------------------------

def _install_options():
    try:
        lines = (ETC / "install-options").read_text().splitlines()
    except OSError:
        raise ValueError("no /etc/hub/install-options: this box was installed before updates "
                         "from /admin existed. Run install.sh once by hand to record them.")
    return [line for line in lines if line]


def _option(opts, name, default=None):
    return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else default


def _git(*args):
    out = run("git", *args, timeout=600)
    if out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        raise ValueError(f"git {args[0] if args[0] != '-C' else args[2]}: {lines[-1] if lines else 'failed'}")
    return out.stdout.strip()


def _installed_commit():
    """The commit in VERSION (git describe output: abc1234, or v1.2-3-gabc1234, maybe -dirty)."""
    try:
        first = (CODE / "VERSION").read_text().split()[0]
    except (OSError, IndexError):
        return None
    first = first.removesuffix("-dirty")
    match = re.search(r"-g([0-9a-f]{7,40})$", first) or re.match(r"^([0-9a-f]{7,40})$", first)
    return match.group(1) if match else None


def _write_update_state(data):
    UPDATE_STATE.write_text(json.dumps(data, indent=2))
    _for_hub(UPDATE_STATE)


def _read_update_state():
    try:
        return json.loads(UPDATE_STATE.read_text())
    except (OSError, ValueError):
        return {}


class Progress:
    """control/update-progress.json while a check, fetch or install runs: step `step` of
    `steps` (`estimate` when the count is a guess), what it is doing, and the bytes of a
    download in flight. The hub only reads it, and ignores it once `pid` has gone; it is
    removed when the action ends, however it ends."""

    def __init__(self, action, steps, estimate=False):
        self.data = {"action": action, "pid": os.getpid(), "step": 0, "steps": steps,
                     "estimate": estimate, "label": "", "done": 0, "total": 0}
        self._last = 0.0

    def __enter__(self):
        self._write()
        return self

    def __exit__(self, *exc):
        UPDATE_PROGRESS.unlink(missing_ok=True)

    def add_steps(self, n):
        self.data["steps"] += n
        self._write()

    def step(self, label):
        step = self.data["step"] + 1
        self.data.update(step=step, steps=max(self.data["steps"], step), label=label, done=0, total=0)
        self._write()

    def bytes(self, done, total):
        """Written at most once a second, and once more when a download completes."""
        self.data.update(done=done, total=total)
        if time.monotonic() - self._last >= 1 or (total and done >= total):
            self._write()

    def _write(self):
        self._last = time.monotonic()
        tmp = UPDATE_PROGRESS.with_name(UPDATE_PROGRESS.name + ".tmp")
        tmp.write_text(json.dumps(self.data))
        _for_hub(tmp)
        os.replace(tmp, UPDATE_PROGRESS)


# --- verifying a fetched update -------------------------------------------------------
# Everything that can be checked before install.sh runs is checked here, so a bad push or a
# broken download shows up as a red line on /admin rather than as a half-installed box.
# Each check: {"name", "ok", "detail", "warn"}; warn marks one that does not block.

def _check(name, ok, detail="", warn=False):
    return {"name": name, "ok": bool(ok), "detail": detail, "warn": warn}


def _arch_names():
    m = platform.machine()
    return {"ttyd": {"x86_64": "x86_64", "aarch64": "aarch64", "armv7l": "armhf", "armv8l": "armhf",
                     "armv6l": "arm"}.get(m),
            "sb": {"x86_64": "x86_64", "aarch64": "aarch64", "armv7l": "armv7", "armv8l": "armv7"}.get(m),
            "caddy": {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7", "armv8l": "armv7"}.get(m)}


def _fetch(url, dest, timeout=300, report=None):
    """Download url to dest, atomically, calling report(done, total) as bytes arrive (total
    is 0 when the server does not say). Returns dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-update"})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if report:
                report(done, total)
    os.replace(tmp, dest)
    return dest


def _digest(path, algo):
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sum_for(sums_file, name):
    for line in Path(sums_file).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def _var(script, name):
    m = re.search(rf"^{name}=([0-9A-Za-z._-]+)\s*$", script, re.M)
    return m.group(1) if m else None


def _have_version(cmd, version):
    try:
        return version in run(*cmd, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False


def prefetch(src, opts, progress=None):
    """Download into DOWNLOADS the release files the new install.sh will want and the box
    does not already have, each checked against its published checksum. Returns checks.
    The downloads are listed before any starts, so progress knows how many steps they are."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    script = (src / "install.sh").read_text()
    arch = _arch_names()
    report = progress.bytes if progress else None
    jobs = []  # (step label, function returning a check or None)

    ttyd = _var(script, "TTYD_VERSION")
    if ttyd and arch["ttyd"] and not _have_version(("/usr/local/bin/ttyd", "--version"), ttyd):
        def get_ttyd():
            base = f"https://github.com/tsl0922/ttyd/releases/download/{ttyd}"
            name = f"ttyd.{arch['ttyd']}"
            try:
                sums = _fetch(f"{base}/SHA256SUMS", DOWNLOADS / f"ttyd-{ttyd}-SHA256SUMS")
                binary = _fetch(f"{base}/{name}", DOWNLOADS / f"ttyd-{ttyd}-{name}", report=report)
                ok = _digest(binary, "sha256") == _sum_for(sums, name)
                return _check(f"ttyd {ttyd} downloaded", ok, "checksum matches" if ok else "checksum MISMATCH")
            except OSError as exc:
                return _check(f"ttyd {ttyd} downloaded", False, str(exc))
        jobs.append((f"Downloading ttyd {ttyd}", get_ttyd))

    sb = _var(script, "SB_VERSION")
    if sb and "--with-notes" in opts and arch["sb"] and \
            not _have_version(("/usr/local/bin/silverbullet", "--version"), sb):
        def get_sb():
            name = f"silverbullet-server-linux-{arch['sb']}.zip"
            try:
                z = _fetch(f"https://github.com/silverbulletmd/silverbullet/releases/download/{sb}/{name}",
                           DOWNLOADS / f"silverbullet-{sb}-{name}", report=report)
                # SilverBullet publishes no checksums; at least the zip must be whole.
                try:
                    with zipfile.ZipFile(z) as zf:
                        ok = zf.testzip() is None
                except zipfile.BadZipFile:
                    ok = False
                return _check(f"SilverBullet {sb} downloaded", ok, "zip is intact" if ok else "zip is damaged")
            except OSError as exc:
                return _check(f"SilverBullet {sb} downloaded", False, str(exc))
        jobs.append((f"Downloading SilverBullet {sb}", get_sb))

    # Caddy from its GitHub release, on a box whose apt repository failed verification.
    if (ETC / "caddy-from-release").exists() and arch["caddy"]:
        def get_caddy():
            try:
                req = urllib.request.Request("https://api.github.com/repos/caddyserver/caddy/releases/latest",
                                             headers={"User-Agent": "irate-box-update"})
                with urllib.request.urlopen(req, timeout=60) as resp:
                    tag = json.load(resp)["tag_name"]
                ver = tag.lstrip("v")
                installed = run("dpkg-query", "-W", "-f=${Version}", "caddy").stdout.strip()
                (DOWNLOADS / "caddy-release-tag").write_text(tag + "\n")
                if installed == ver:
                    return None
                deb = f"caddy_{ver}_linux_{arch['caddy']}.deb"
                base = f"https://github.com/caddyserver/caddy/releases/download/{tag}"
                sums = _fetch(f"{base}/caddy_{ver}_checksums.txt", DOWNLOADS / f"caddy_{ver}_checksums.txt")
                pkg = _fetch(f"{base}/{deb}", DOWNLOADS / deb, report=report)
                ok = _digest(pkg, "sha512") == _sum_for(sums, deb)
                return _check(f"Caddy {ver} downloaded", ok, "checksum matches" if ok else "checksum MISMATCH")
            except (OSError, ValueError, KeyError) as exc:
                return _check("Caddy release downloaded", False, str(exc))
        jobs.append(("Downloading Caddy's latest release", get_caddy))

    if progress:
        progress.add_steps(len(jobs))
    checks = []
    for label, job in jobs:
        if progress:
            progress.step(label)
        check = job()
        if check:
            checks.append(check)
    return checks


def verify_update(src, installed, opts, progress=None):
    """Checks on a fetched tree; any failure not marked warn blocks the install."""
    checks = []
    if installed:
        known = run("git", "-C", str(src), "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
        ff = known and run("git", "-C", str(src), "merge-base", "--is-ancestor", installed, "HEAD").returncode == 0
        checks.append(_check("A fast-forward of the installed version", ff,
                             "history continues from the installed commit" if ff else
                             f"{installed} is not an ancestor: the branch was rewritten, or the box was "
                             "installed from elsewhere. Read the changes before installing.", warn=True))
    for script in ("install.sh", "uninstall.sh", "tailscale-apply.sh"):
        path = src / script
        if path.exists():
            out = run("bash", "-n", str(path))
            checks.append(_check(f"{script} parses", out.returncode == 0,
                                 (out.stderr.strip().splitlines() or [""])[-1]))
    bad = []
    pys = sorted(src.glob("*.py"))
    for py in pys:
        try:
            compile(py.read_text(), str(py), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            bad.append(f"{py.name}: {exc}")
    checks.append(_check("Python files compile", not bad, "; ".join(bad) or f"{len(pys)} files"))

    caddyfile = src / "Caddyfile"
    if caddyfile.exists():
        # As install.sh will write it: the box's current login hash in place of the marker.
        current = _login_file().read_text() if _login_file().exists() else ""
        m = HASH_LINE.search(current)
        text = caddyfile.read_text()
        if m:
            text = text.replace("$2a$14$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT", m.group(0).split()[-1])
        with tempfile.NamedTemporaryFile("w", suffix=".Caddyfile", delete=False) as tmp:
            tmp.write(text)
        try:
            out = run("caddy", "validate", "--adapter", "caddyfile", "--config", tmp.name)
            detail = "" if out.returncode == 0 else ((out.stderr or out.stdout).strip().splitlines() or [""])[-1]
            checks.append(_check("The new Caddyfile validates", out.returncode == 0, detail[:300]))
        except (OSError, subprocess.SubprocessError) as exc:
            checks.append(_check("The new Caddyfile validates", False, str(exc)))
        finally:
            os.unlink(tmp.name)
    checks += prefetch(src, opts, progress)
    return checks


def _check_for_update(progress):
    """Bring the cache up to the recorded branch and work out what is new: two steps.
    Returns (state for update.json, install options)."""
    opts = _install_options()
    repo = _option(opts, "--repo")
    branch = _option(opts, "--branch", "main")
    if not repo:
        raise ValueError("install-options names no repository")
    progress.step(f"Fetching {branch} from {repo}")
    src = str(UPDATE_SRC)
    if (UPDATE_SRC / ".git").is_dir():
        _git("-C", src, "remote", "set-url", "origin", repo)
        _git("-C", src, "fetch", "--depth", "200", "origin", branch)
        _git("-C", src, "reset", "--hard", "FETCH_HEAD")
        _git("-C", src, "clean", "-fdx")
    else:
        if UPDATE_SRC.exists():
            subprocess.run(["rm", "-rf", src], check=True)
        UPDATE_SRC.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--depth", "200", "--branch", branch, repo, src)
    progress.step("Reading the changes")
    head = _git("-C", src, "rev-parse", "--short=7", "HEAD")
    installed = _installed_commit()
    known = bool(installed) and run("git", "-C", src, "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
    span = f"{installed}..HEAD" if known else "-10"
    changes = [c for c in _git("-C", src, "log", "--format=%h %cs %s", span).splitlines() if c]
    up_to_date = bool(installed) and head.startswith(installed[:7]) or (known and not changes)
    state = {
        "repo": repo, "branch": branch, "available": head,
        "available_date": _git("-C", src, "log", "-1", "--format=%cs"),
        "installed": installed, "up_to_date": up_to_date,
        "changes": changes[:50], "changes_known": known, "fetched": time.time(),
        "verified": None, "checks": [],
    }
    # The same commit fetched before: its checks and downloads still stand.
    old = _read_update_state()
    if not up_to_date and old.get("verified") == head:
        state.update(verified=head, checks=old.get("checks", []))
    if old.get("install_steps"):
        state["install_steps"] = old["install_steps"]
    return state, opts


def _change_count(state):
    n = len(state["changes"])
    return f"{n} new commit{'s' if n != 1 else ''}" if state["changes_known"] else "a different version"


def update_check(req):
    with Progress("check", 2) as progress:
        state, _ = _check_for_update(progress)
    _write_update_state(state)
    if state["up_to_date"]:
        return f"irate-box is up to date ({state['available']} on {state['branch']})"
    if state["verified"]:
        return f"update available: {_change_count(state)}, {state['available']} on {state['branch']}, already fetched and verified"
    return f"update available: {_change_count(state)}, {state['available']} on {state['branch']}; fetch it to verify it"


def update_fetch(req):
    # Steps: the verification, plus the check if one is needed, plus each download
    # (prefetch adds those once it knows them).
    with Progress("fetch", 1) as progress:
        state = _read_update_state()
        cached = (UPDATE_SRC / ".git").is_dir() and \
            run("git", "-C", str(UPDATE_SRC), "rev-parse", "--short=7", "HEAD").stdout.strip()
        if state.get("up_to_date") or not cached or cached != state.get("available"):
            progress.add_steps(2)
            state, opts = _check_for_update(progress)
        else:
            opts = _install_options()
        if state["up_to_date"]:
            _write_update_state(state)
            return f"irate-box is up to date ({state['available']} on {state['branch']})"
        progress.step("Checking the new version")
        checks = verify_update(UPDATE_SRC, state["installed"], opts, progress)
    head = state["available"]
    failed = [c for c in checks if not c["ok"] and not c["warn"]]
    state.update(checks=checks, verified=None if failed else head)
    _write_update_state(state)
    if failed:
        return f"update {head} found but did not pass verification: {failed[0]['name']} ({failed[0]['detail']})"
    return f"update fetched: {_change_count(state)}, {head} on {state['branch']}, verified"


STEP_LINE = re.compile(rb"==> (.*?)(?:\x1b\[0m)?\s*$")


def _follow_install(proc, progress):
    """Wait for install.sh, making each "==> " heading it prints (its say()) a step.
    Returns (exit code, steps seen)."""
    deadline = time.monotonic() + INSTALL_TIMEOUT
    seen, offset, partial = 0, 0, b""
    while True:
        code = proc.poll()
        with open(UPDATE_LOG, "rb") as fh:
            fh.seek(offset)
            new = fh.read()
            offset = fh.tell()
        *lines, partial = (partial + new).split(b"\n")
        for line in lines:
            m = STEP_LINE.search(line)
            if m:
                seen += 1
                progress.step(m.group(1).decode(errors="replace"))
        if code is not None:
            return code, seen
        if time.monotonic() > deadline:
            proc.kill()
            proc.wait()
            raise ValueError(f"install.sh did not finish within {INSTALL_TIMEOUT // 60} minutes; see update.log")
        time.sleep(1)


def update_install(req):
    if not (UPDATE_SRC / "install.sh").is_file():
        raise ValueError("nothing fetched yet: check for updates first")
    state = _read_update_state()
    head = _git("-C", str(UPDATE_SRC), "rev-parse", "--short=7", "HEAD")
    if state.get("verified") != head:
        raise ValueError("the fetched version has not passed verification: fetch the update again")
    opts = _install_options()
    cmd = ["bash", str(UPDATE_SRC / "install.sh"), "--src", str(UPDATE_SRC), *opts]
    if "--download-cache" in (UPDATE_SRC / "install.sh").read_text():
        cmd += ["--download-cache", str(DOWNLOADS)]
    # As many steps as the last install here printed; a guess until one has.
    steps = state.get("install_steps")
    with Progress("install", steps or INSTALL_STEPS_GUESS, estimate=not steps) as progress, \
            open(UPDATE_LOG, "w") as log:
        _for_hub(UPDATE_LOG)
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        # systemd gives the helper no HOME, and Caddy warns about it on every validate.
        env = dict(os.environ, HOME=os.environ.get("HOME", "/root"))
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env)
        code, seen = _follow_install(proc, progress)
    if code != 0:
        raise ValueError(f"install.sh exited with {code}; see update.log")
    state = _read_update_state()
    if state:
        state.update(installed=_installed_commit(), up_to_date=True, changes=[], install_steps=seen)
        _write_update_state(state)
    return f"updated to {_installed_commit() or 'the fetched version'}"


# --- the update doctor ---------------------------------------------------------------
# For when "Check for updates" fails: look at everything an update depends on and say, in
# plain words, what is wrong and what to do. Read-only apart from `apt-get update`, which
# only refreshes package lists. Each finding: {"check", "status": ok|warn|problem,
# "detail", "fix"}.

def _finding(check, status, detail, fix=""):
    return {"check": check, "status": status, "detail": detail, "fix": fix}


def _local_addresses():
    out = run("ip", "-o", "addr", "show", timeout=10).stdout
    return set(re.findall(r"inet6? ([0-9a-f.:]+)/", out))


def _tls_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "irate-box-doctor"})
    return urllib.request.urlopen(req, timeout=timeout)


def doctor():
    import socket
    import ssl
    from email.utils import parsedate_to_datetime
    findings = []

    # 1. How this box updates.
    try:
        opts = _install_options()
        repo, branch = _option(opts, "--repo"), _option(opts, "--branch", "main")
        findings.append(_finding("Install options", "ok", f"updates come from {repo} ({branch})"))
    except ValueError as exc:
        opts, repo, branch = [], None, "main"
        findings.append(_finding("Install options", "problem", str(exc),
                                 "Run install.sh once by hand (sudo ./install.sh …) to record them."))

    # 2. Names: on the hub's own access point every name resolves to the box itself.
    host = "github.com"
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
        mine = addrs & (_local_addresses() | {"127.0.0.1", "::1"})
        if mine:
            findings.append(_finding("DNS", "problem", f"{host} resolves to this box ({', '.join(sorted(mine))})",
                                     "The box is answering every name itself, as it does in access-point mode. "
                                     "Join it to a network with internet, or update from a laptop that has one."))
        else:
            findings.append(_finding("DNS", "ok", f"{host} → {', '.join(sorted(addrs)[:3])}"))
    except OSError as exc:
        addrs = set()
        findings.append(_finding("DNS", "problem", f"cannot resolve {host}: {exc}",
                                 "No working DNS: check the network the box is on (Ethernet, or WiFi it has joined)."))

    # 3. A route out.
    reachable = False
    if addrs:
        try:
            with socket.create_connection((host, 443), timeout=8):
                reachable = True
            findings.append(_finding("Connection", "ok", f"{host}:443 answers"))
        except OSError as exc:
            findings.append(_finding("Connection", "problem", f"cannot connect to {host}:443: {exc}",
                                     "The network has no way out, or blocks HTTPS. Try another network."))

    # 4. TLS, and the clock it depends on. A board with no RTC can boot years in the past,
    #    and then every certificate looks not-yet-valid.
    now = time.time()
    installed_on = None
    try:
        m = re.search(r"installed (\d{4}-\d{2}-\d{2})", (CODE / "VERSION").read_text())
        installed_on = time.mktime(time.strptime(m.group(1), "%Y-%m-%d")) if m else None
    except OSError:
        pass
    if reachable:
        try:
            with _tls_get(f"https://{host}/") as resp:
                server = parsedate_to_datetime(resp.headers["Date"]).timestamp()
            skew = now - server
            if abs(skew) > 600:
                findings.append(_finding("Clock", "problem", f"the box's clock is {abs(skew) / 3600:.1f} h "
                                         f"{'ahead' if skew > 0 else 'behind'}",
                                         "Set the time (timedatectl, or let NTP sync once online); "
                                         "certificates and update checks depend on it."))
            else:
                findings.append(_finding("Clock", "ok", f"within {abs(skew):.0f} s of {host}"))
            findings.append(_finding("TLS", "ok", f"https://{host}/ verifies"))
        except ssl.SSLError as exc:
            findings.append(_finding("TLS", "problem", f"certificate check failed: {exc}",
                                     "Usually the clock (see below) or missing ca-certificates; "
                                     "a network that intercepts HTTPS also does this."))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            findings.append(_finding("TLS", "problem", f"https://{host}/ failed: {exc}", ""))
    if installed_on and now < installed_on - 86400:
        findings.append(_finding("Clock", "problem",
                                 f"the clock says {time.strftime('%Y-%m-%d', time.gmtime(now))}, before this "
                                 f"hub was installed", "Set the time: timedatectl set-time 'YYYY-MM-DD HH:MM'."))

    # 5. GitHub's API, which the librarian and Caddy's release lookup use.
    if reachable:
        try:
            with _tls_get("https://api.github.com/rate_limit") as resp:
                core = json.load(resp)["resources"]["core"]
            if core["remaining"] == 0:
                wait = max(0, core["reset"] - now) / 60
                findings.append(_finding("GitHub API", "problem", "rate limit used up (60 requests an hour "
                                         "without a token)", f"Wait about {wait:.0f} min, or set a GitHub token "
                                         "in Library."))
            else:
                findings.append(_finding("GitHub API", "ok", f"{core['remaining']} of {core['limit']} requests left this hour"))
        except (OSError, ValueError, KeyError) as exc:
            findings.append(_finding("GitHub API", "warn", f"could not ask: {exc}", ""))

    # 6. The repository and branch exist and answer.
    if repo and reachable:
        out = run("git", "ls-remote", "--heads", repo, branch, timeout=60)
        if out.returncode != 0:
            findings.append(_finding("Repository", "problem", (out.stderr.strip().splitlines() or ["git ls-remote failed"])[-1],
                                     "Check the repository still exists and is public."))
        elif not out.stdout.strip():
            findings.append(_finding("Repository", "problem", f"{repo} has no branch {branch}",
                                     "The branch was renamed or deleted; reinstall naming the right one with --branch."))
        else:
            findings.append(_finding("Repository", "ok", f"{branch} is at {out.stdout.split()[0][:7]}"))

    # 7. Space for the clone, downloads and the install.
    for label, path, need in (("Space for the update cache", UPDATE_SRC.parent, 200),
                              ("Space on the system card", Path("/"), 300)):
        path = path if path.exists() else path.parent
        free = shutil.disk_usage(path).free >> 20
        findings.append(_finding(label, "ok" if free >= need else "problem", f"{free} MB free in {path}",
                                 "" if free >= need else f"Free at least {need} MB (old books in Library, saved work)."))

    # 8. The cached clone.
    if (UPDATE_SRC / ".git").is_dir():
        locks = [p.name for p in (UPDATE_SRC / ".git").glob("*.lock")]
        if locks:
            findings.append(_finding("Update cache", "problem", f"stale git lock: {', '.join(locks)}",
                                     "A check was interrupted. Clear the cache below and check again."))
        else:
            status = run("git", "-C", str(UPDATE_SRC), "status", "--porcelain", timeout=60)
            remote = run("git", "-C", str(UPDATE_SRC), "remote", "get-url", "origin").stdout.strip()
            if status.returncode != 0:
                findings.append(_finding("Update cache", "problem", "the cached clone is damaged",
                                         "Clear the cache below and check again."))
            elif repo and remote != repo:
                findings.append(_finding("Update cache", "warn", f"cloned from {remote}, not {repo}",
                                         "The next check switches it over."))
            else:
                findings.append(_finding("Update cache", "ok", "the cached clone is intact"))
    else:
        findings.append(_finding("Update cache", "ok", "empty: the next check clones afresh"))

    # 9. Package sources: a repository that fails verification fails every apt-get update.
    out = run("apt-get", "update", "-q", timeout=300)
    text = out.stdout + out.stderr
    bad = sorted(set(re.findall(r"(?:Err|E|W):\S*\s+(https?://\S+)", text)))
    if out.returncode == 0 and not re.search(r"^(E|Err):", text, re.M):
        findings.append(_finding("Package sources", "ok", "apt-get update succeeds"))
    else:
        lines = [l for l in text.splitlines() if re.match(r"^(E|Err|W):", l)][:3]
        findings.append(_finding("Package sources", "problem", " | ".join(lines)[:400] or "apt-get update failed",
                                 "A repository that fails verification (Caddy's did, on 2026-10-01) blocks "
                                 "every update. install.sh copes with Caddy's; for others, remove the list "
                                 "under /etc/apt/sources.list.d/ from the terminal."))

    # 10. The last fetch, as the page last saw it.
    try:
        last = json.loads(UPDATE_STATE.read_text())
        failed = [c for c in last.get("checks", []) if not c.get("ok") and not c.get("warn")]
        if failed:
            findings.append(_finding("Last update check", "problem",
                                     "; ".join(f"{c['name']}: {c['detail']}" for c in failed)[:400],
                                     "Fix the cause, then check for updates again."))
    except (OSError, ValueError):
        pass
    return findings


def update_doctor(req):
    findings = doctor()
    DOCTOR_STATE.write_text(json.dumps({"at": time.time(), "findings": findings}, indent=2))
    _for_hub(DOCTOR_STATE)
    problems = [f for f in findings if f["status"] == "problem"]
    if not problems:
        return "update doctor: no problems found"
    return f"update doctor: {len(problems)} problem{'s' if len(problems) != 1 else ''}, first: {problems[0]['check']}"


def update_clear_cache(req):
    """Remove the cached clone and downloads; the next check starts afresh."""
    for path in (UPDATE_SRC, DOWNLOADS):
        if path.exists():
            shutil.rmtree(path)
    UPDATE_STATE.unlink(missing_ok=True)
    return "update cache cleared"


# --- the security page -----------------------------------------------------------------

def _write_security(data):
    SECURITY_STATE.write_text(json.dumps(data, indent=2))
    _for_hub(SECURITY_STATE)


def security_scan(req):
    data = security.scan()
    _write_security(data)
    bad = [f for f in data["findings"] if f["status"] == "problem"]
    return f"security scan: {len(bad)} to fix" if bad else "security scan: nothing to fix"


def security_fix(req):
    choice = str(req.get("choice", ""))
    if not re.fullmatch(r"[a-z-]+(:[A-Za-z0-9@._-]+)?", choice):
        raise ValueError("not a Security page choice")
    if choice == "security-updates":
        SECURITY_LOG.touch()
        _for_hub(SECURITY_LOG)
    try:
        msg = security.fix(choice, SECURITY_LOG)
    finally:
        _write_security(security.scan())
    return msg


# --- app bundles -------------------------------------------------------------------
# The librarian (as the hub user) downloads a bundle into $STATE/library/apps/ and checks it;
# this checks it again -- the hub wrote that file -- and swaps it in, keeping the previous
# copy as .<app>.prev for app-rollback. Where each app goes, what proves a bundle whole and
# which unit to restart after come from its manifest (apps.d/, "install").

APP_STAGING = STATE / "library" / "apps"
APP_MAX_BYTES = 400 << 20
APPS = manifests.installable(MANIFESTS)


def _app_dir(app):
    return manifests.install_dir(APPS[app])


def _has_needed(names, pattern):
    """The manifest's "needs": a file, or a glob such as *.html, present at the top level."""
    import fnmatch
    return any(fnmatch.fnmatchcase(n, pattern) for n in names) if any(c in pattern for c in "*?[") else pattern in names


def _checked_bundle(app, zip_path):
    """The bundle's metadata, or ValueError: the same rules as librarian.check_bundle."""
    if app not in APPS:
        raise ValueError(f"{app} is not an app the hub installs")
    path = Path(zip_path)
    staging = APP_STAGING.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(staging) or not path.is_file():
        raise ValueError("the bundle must be a file the librarian staged")
    try:
        with zipfile.ZipFile(path) as zf:
            names, total = set(), 0
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in info.filename:
                    raise ValueError(f"unsafe path in the bundle: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"symlink in the bundle: {info.filename}")
                total += info.file_size
                names.add(info.filename)
            if total > APP_MAX_BYTES:
                raise ValueError("the bundle is too large")
            meta = json.loads(zf.read("irate-box-bundle.json"))
            if meta.get("app") != app or not _has_needed(names, APPS[app]["install"]["needs"]):
                raise ValueError(f"not a bundle of {app}")
            return meta
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid bundle: {exc}")


def install_app(app, zip_path):
    meta = _checked_bundle(app, zip_path)
    target = _app_dir(app)
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.parent / f".{target.name}.new"
    prev = target.parent / f".{target.name}.prev"
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(new)
        # Root-owned and world-readable, whatever modes the zip carried.
        as_root = os.geteuid() == 0
        for root, dirs, files in os.walk(new):
            if as_root:
                os.chown(root, 0, 0)
            os.chmod(root, 0o755)
            for name in files:
                p = os.path.join(root, name)
                if as_root:
                    os.chown(p, 0, 0)
                os.chmod(p, 0o644)
        if target.exists():
            shutil.rmtree(prev, ignore_errors=True)
            os.rename(target, prev)
        os.rename(new, target)
    finally:
        shutil.rmtree(new, ignore_errors=True)
    Path(zip_path).unlink(missing_ok=True)
    if APPS[app]["install"].get("restart"):
        run("systemctl", "try-restart", APPS[app]["install"]["restart"])
    return f"{app}: installed {str(meta.get('commit', ''))[:7]} ({meta.get('ref')}, built {str(meta.get('built', ''))[:10]})"


def app_install(req):
    return install_app(str(req.get("app", "")), str(req.get("zip", "")))


def app_rollback(req):
    app = str(req.get("app", ""))
    if app not in APPS:
        raise ValueError(f"{app} is not an app the hub installs")
    target = _app_dir(app)
    prev = target.parent / f".{target.name}.prev"
    if not prev.is_dir():
        raise ValueError(f"no previous {app} to go back to")
    swap = target.parent / f".{target.name}.swap"
    shutil.rmtree(swap, ignore_errors=True)
    if target.exists():
        os.rename(target, swap)
    os.rename(prev, target)
    if swap.exists():
        os.rename(swap, prev)  # so a second roll back goes forward again
    if APPS[app]["install"].get("restart"):
        run("systemctl", "try-restart", APPS[app]["install"]["restart"])
    meta = json.loads((target / "irate-box-bundle.json").read_text()) if (target / "irate-box-bundle.json").exists() else {}
    return f"{app}: rolled back to {str(meta.get('commit', 'the previous build'))[:7]}"


ACTIONS = {"service": service, "password": password,
           "update-check": update_check, "update-fetch": update_fetch, "update-install": update_install,
           "update-doctor": update_doctor, "update-clear-cache": update_clear_cache,
           "security-scan": security_scan, "security-fix": security_fix,
           "app-install": app_install, "app-rollback": app_rollback}


def answer(rid, ok, message):
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{rid}.json"
    path.write_text(json.dumps({"id": rid, "ok": ok, "message": message, "at": time.time()}))
    _for_hub(path)
    # Keep the last 50 answers; the page only ever looks at recent ones.
    old = sorted(RESULTS.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-50]
    for p in old:
        p.unlink(missing_ok=True)


def main():
    # A request that arrives while this runs is picked up by the same loop.
    for _ in range(100):
        pending = sorted(REQUESTS.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not pending:
            return 0
        for path in pending:
            rid = path.stem if ID_RE.match(path.stem) else "invalid"
            try:
                req = json.loads(path.read_text())
                path.unlink()
                action = ACTIONS.get(req.get("action"))
                if not action:
                    raise ValueError("unknown action")
                answer(rid, True, action(req))
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                path.unlink(missing_ok=True)
                answer(rid, False, str(exc))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["reset-password"]:
        if os.geteuid() != 0:
            sys.exit("run as root: sudo python3 hub_control.py reset-password")
        print(reset_password())
        sys.exit(0)
    if len(sys.argv) == 4 and sys.argv[1] == "install-app":
        if os.geteuid() != 0:
            sys.exit("run as root")
        try:
            print(install_app(sys.argv[2], sys.argv[3]))
        except (ValueError, OSError) as exc:
            sys.exit(f"install-app: {exc}")
        sys.exit(0)
    sys.exit(main())
