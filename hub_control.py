#!/usr/bin/env python3
"""Root helper for the admin page: the few things the unprivileged hub cannot do itself.

The hub (server.py, user hub) writes one JSON request per file into
$HUB_STATE_DIR/control/requests/. irate-box-control.path notices the folder is not empty
and runs this as root, once per batch. Each request is checked against an allow-list,
carried out, answered in control/results/<id>.json, and deleted. Nothing else is accepted:

  {"id": ..., "action": "service", "unit": "<allowed unit>", "op": "start|stop|restart|enable|disable"}
      Optional services get every op; caddy and the hub itself only restart.
  {"id": ..., "action": "password", "password": "<new admin password>"}
      The one admin login: Caddy's basic_auth hashes (/admin, /sync, /term), ttyd's
      credential, Syncthing's GUI login, and /etc/hub/admin-password.
  {"id": ..., "action": "update-fetch"}
      Clone or fast-forward irate-box into a root-owned cache, from the repository and branch
      install.sh recorded in /etc/hub/install-options, and summarise what is new in
      control/update.json. The hub never writes the code that root will run.
  {"id": ..., "action": "update-install"}
      Run that checkout's install.sh with the recorded options; output in control/update.log.

Stdlib only.
"""

import json
import os
import pwd
import re
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
HUB_USER = os.environ.get("HUB_USER", "hub")
CONTROL = STATE / "control"
REQUESTS = CONTROL / "requests"
RESULTS = CONTROL / "results"
CADDYFILE = Path(os.environ.get("HUB_CADDYFILE", "/etc/caddy/Caddyfile"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
UPDATE_SRC = Path(os.environ.get("HUB_UPDATE_DIR", "/var/cache/irate-box/src"))
UPDATE_STATE = CONTROL / "update.json"
UPDATE_LOG = CONTROL / "update.log"
INSTALL_TIMEOUT = 45 * 60

OPS_ALL = ("start", "stop", "restart", "enable", "disable")
UNITS = {
    "kiwix.service": OPS_ALL,
    "silverbullet.service": OPS_ALL,
    f"syncthing@{HUB_USER}.service": OPS_ALL,
    "mosquitto.service": OPS_ALL,
    "ttyd.service": OPS_ALL,
    "excalidraw-room.service": OPS_ALL,
    "caddy.service": ("restart",),
    "irate-box.service": ("restart",),
}
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
    # Caddy first: if the new config does not validate, nothing else has changed.
    hashed = run("caddy", "hash-password", "--plaintext", pw)
    if hashed.returncode != 0:
        raise ValueError("caddy hash-password failed")
    new_hash = hashed.stdout.strip()
    text = CADDYFILE.read_text()
    updated, count = HASH_LINE.subn(lambda m: m.group(1) + new_hash, text)
    if not count:
        raise ValueError("no admin login found in /etc/caddy/Caddyfile")
    candidate = CADDYFILE.with_suffix(".new")
    candidate.write_text(updated)
    check = run("caddy", "validate", "--adapter", "caddyfile", "--config", str(candidate))
    if check.returncode != 0:
        candidate.unlink(missing_ok=True)
        raise ValueError("the new Caddy config did not validate; nothing was changed")
    os.replace(candidate, CADDYFILE)
    run("systemctl", "reload", "caddy")

    secret = ETC / "admin-password"
    secret.write_text(pw + "\n")
    secret.chmod(0o600)
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
    return "admin password changed: " + ", ".join(d for d in done if d)


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


def update_fetch(req):
    opts = _install_options()
    repo = _option(opts, "--repo")
    branch = _option(opts, "--branch", "main")
    if not repo:
        raise ValueError("install-options names no repository")
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
    head = _git("-C", src, "rev-parse", "--short=7", "HEAD")
    installed = _installed_commit()
    known = bool(installed) and run("git", "-C", src, "cat-file", "-e", f"{installed}^{{commit}}").returncode == 0
    span = f"{installed}..HEAD" if known else "-10"
    changes = [c for c in _git("-C", src, "log", "--format=%h %cs %s", span).splitlines() if c]
    up_to_date = bool(installed) and head.startswith(installed[:7]) or (known and not changes)
    _write_update_state({
        "repo": repo, "branch": branch, "available": head,
        "available_date": _git("-C", src, "log", "-1", "--format=%cs"),
        "installed": installed, "up_to_date": up_to_date,
        "changes": changes[:50], "changes_known": known, "fetched": time.time(),
    })
    if up_to_date:
        return f"irate-box is up to date ({head} on {branch})"
    count = f"{len(changes)} new commit{'s' if len(changes) != 1 else ''}" if known else "a different version"
    return f"update available: {count}, {head} on {branch}"


def update_install(req):
    if not (UPDATE_SRC / "install.sh").is_file():
        raise ValueError("nothing fetched yet: check for updates first")
    opts = _install_options()
    cmd = ["bash", str(UPDATE_SRC / "install.sh"), "--src", str(UPDATE_SRC), *opts]
    with open(UPDATE_LOG, "w") as log:
        _for_hub(UPDATE_LOG)
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        try:
            code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  timeout=INSTALL_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            raise ValueError(f"install.sh did not finish within {INSTALL_TIMEOUT // 60} minutes; see update.log")
    if code != 0:
        raise ValueError(f"install.sh exited with {code}; see update.log")
    try:
        state = json.loads(UPDATE_STATE.read_text())
        state.update(installed=_installed_commit(), up_to_date=True, changes=[])
        _write_update_state(state)
    except (OSError, ValueError):
        pass
    return f"updated to {_installed_commit() or 'the fetched version'}"


ACTIONS = {"service": service, "password": password,
           "update-fetch": update_fetch, "update-install": update_install}


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
    sys.exit(main())
