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


def run(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


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


ACTIONS = {"service": service, "password": password}


def answer(rid, ok, message):
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{rid}.json"
    path.write_text(json.dumps({"id": rid, "ok": ok, "message": message, "at": time.time()}))
    hub = pwd.getpwnam(HUB_USER)
    os.chown(path, hub.pw_uid, hub.pw_gid)
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
