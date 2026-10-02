#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor: a passive audit of the box the hub runs on.

security.py looks at what a guest can reach (listeners, SSH, security updates) and offers
fixes. This looks at how the system is set up underneath, including what the hub inherited
from the owner's earlier setup, and reports it. It changes NOTHING: it reads files, runs
`systemctl show`/`list-units`, and walks /proc; it never starts, stops, writes or chowns
anything on the system. Its one write is its own report (control/security-audit.json).

It is the checkable half of notes/irate-box/2026-10-02-security-review. Each step covers
findings of that review (the "ref" on every line, F1 to F30, or S1 to S14 for the known
ones) that the state of the box can show. Findings the box cannot show (a race inside one
function, a missing checksum) are listed as "not covered" in the report rather than skipped
silently. Run it before the fixes and again after: the lines that were problems must clear.

Status: ok | warn | problem (critical and high findings are "problem", the rest "warn").
A check that cannot read what it needs (not root, no systemd) says so as a warn, never ok.

    sudo /opt/irate-box/irate-box secdoctor            step by step, with what to do
    sudo /opt/irate-box/irate-box secdoctor summary    problems and warnings only
    sudo /opt/irate-box/irate-box secdoctor json       the report, as the page reads it
    /admin → Health → Security doctor                   the same, through hub_control.py

The F2 fix (request bodies drained or the connection closed) declares itself with
`DRAINS_REQUEST_BODIES = True` at the top level of server.py, which the front step looks for.

Secrets are never printed: a password on a command line is reported by process and flag,
a world-readable file by name. Stdlib only.
"""

import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
HUB_USER = os.environ.get("HUB_USER", "hub")
CI_USER = "hubci"
CONTROL = STATE / "control"
NGINX_CONF = Path(os.environ.get("HUB_NGINX_CONF", "/etc/nginx/conf.d/irate-box.conf"))
PROC = Path(os.environ.get("HUB_PROC_DIR", "/proc"))
SYS_FS = Path(os.environ.get("HUB_SYSCTL_DIR", "/proc/sys"))
SUDOERS = Path(os.environ.get("HUB_SUDOERS", "/etc/sudoers"))
PASSWD = Path(os.environ.get("HUB_PASSWD", "/etc/passwd"))
SHADOW = Path(os.environ.get("HUB_SHADOW", "/etc/shadow"))
REPORT = CONTROL / "security-audit.json"

OUR_UNIT_PATTERNS = ("irate-box*", "silverbullet.service", "kiwix.service", "ttyd.service",
                     "mosquitto.service", "excalidraw-room.service", "nginx.service")
MAX_LISTED = 8          # names shown in a line before "…"
MAX_ENTRIES = 4000      # directory entries looked at per folder, so a hub-made flood cannot stall the audit


# --- reading without trusting ------------------------------------------------------------

def _read(path, limit=262144):
    """File text, or None. Never follows a link (a hub-owned folder is hub-controlled), never
    reads more than `limit` bytes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None


def _name_of(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (KeyError, ImportError):
        return str(uid)


def _uid_of(name):
    try:
        import pwd
        return pwd.getpwnam(name).pw_uid
    except (KeyError, ImportError):
        return None


def _sc(*args):
    """`systemctl` output, or "" when systemd is not there. Only read-only verbs are used."""
    try:
        r = subprocess.run(("systemctl", "--no-pager", *args), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _show(unit, *props):
    out = _sc("show", unit, *(f"-p{p}" for p in props))
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _list(names, limit=MAX_LISTED):
    names = list(names)
    return ", ".join(names[:limit]) + ("…" if len(names) > limit else "")


def F(fid, title, status, detail, fix="", ref=""):
    return {"id": fid, "title": title, "status": status, "detail": detail, "fix": fix, "ref": ref}


def _cannot(fid, title, why, ref=""):
    return F(fid, title, "warn", f"Could not check: {why}.", "Run the doctor as root (sudo /opt/irate-box/irate-box secdoctor).", ref)


# --- nginx -------------------------------------------------------------------------------

def parse_nginx(text):
    """[(block path, directive)] with the block path a tuple like ('server', 'location /notes/')."""
    text = re.sub(r"#[^\n]*", "", text)
    stack, out, buf = [], [], ""
    for ch in text:
        if ch == "{":
            stack.append(" ".join(buf.split()))
            buf = ""
        elif ch == "}":
            if stack:
                stack.pop()
            buf = ""
        elif ch == ";":
            out.append((tuple(stack), " ".join(buf.split())))
            buf = ""
        else:
            buf += ch
    return out


def nginx_size(text):
    m = re.fullmatch(r"(\d+)([kKmMgG]?)", text.strip())
    if not m:
        return None
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()]


def parse_caddy(text):
    """[(block path, line, opens_block)] for a Caddyfile. A block opens on a line ending in '{'
    and closes on a line that is just '}'; `{$ENV:default}` and `{path}` inside a line do not
    count. The global-options block has the header ''."""
    stack, out = [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not re.search(r'["\']', raw) else raw.strip()
        if line.startswith("#") or not line:
            continue
        if line == "}":
            if stack:
                stack.pop()
        elif line.endswith("{"):
            head = " ".join(line[:-1].split())
            out.append((tuple(stack), head, True))
            stack.append(head)
        else:
            out.append((tuple(stack), " ".join(line.split()), False))
    return out


CADDY_FILES = ("irate-box.caddy", "Caddyfile")


def load_front():
    """(kind, parsed config, source note) for the web server in front: ('nginx'|'caddy', ...),
    or (None, None, why). The one that is running wins when both configs are on disk."""
    caddy_dir = Path(os.environ.get("HUB_CADDY_DIR", "/etc/caddy"))
    caddy_up = _sc("is-active", "caddy.service").strip() == "active"
    nginx_up = _sc("is-active", "nginx.service").strip() == "active"
    nginx_text = _read(NGINX_CONF)
    caddy_texts = [(n, _read(caddy_dir / n)) for n in CADDY_FILES]
    caddy_texts = [(n, t) for n, t in caddy_texts if t]
    caddy_ours = [t for _, t in caddy_texts if re.search(r"(?m)^\s*handle\w*\s+/admin", t)]
    options = []
    if nginx_text is not None:
        options.append(("nginx", nginx_up, lambda: (parse_nginx(nginx_text), str(NGINX_CONF))))
    if caddy_ours:
        options.append(("caddy", caddy_up, lambda: (parse_caddy("\n".join(t for _, t in caddy_texts)),
                                                   ", ".join(str(caddy_dir / n) for n, _ in caddy_texts))))
    options.sort(key=lambda o: not o[1])  # the running one first
    if options:
        kind, _, load = options[0]
        parsed, note = load()
        return kind, parsed, note
    return None, None, f"neither {NGINX_CONF} nor a Caddyfile with /admin in {caddy_dir} is readable"


def _caddy_routes(parsed, *prefixes):
    """{route header: [lines inside it, nested too]} for the site's top-level handle/route
    blocks whose path starts with one of the prefixes. Redirect-only blocks are left out."""
    groups = {}
    for stack, line, opens in parsed:
        full = stack + ((line,) if opens else ())
        if len(full) >= 2 and re.match(r"(handle|handle_path|route)\b", full[1]):
            groups.setdefault(full[1], []).append(line)
    out = {}
    for head, lines in groups.items():
        path = next((w for w in head.split()[1:] if w.startswith("/")), "")
        if path and any(path.startswith(p) for p in prefixes) and not any(l.startswith("redir ") for l in lines):
            out[head] = lines
    return out


CADDY_AUTH = ("basic_auth", "basicauth", "forward_auth")


def _caddy_gated(parsed, *prefixes):
    routes = _caddy_routes(parsed, *prefixes)
    return bool(routes) and all(any(l.split()[0] in CADDY_AUTH for l in lines) for lines in routes.values())


def _locations(directives, *prefixes):
    """{location header: [directives]} for the server locations whose path starts with one of
    the prefixes (a regex location counts by its leading ^/path). Redirect-only ones are left
    out: a `return 301` has nothing to protect."""
    blocks = {}
    for stack, d in directives:
        if len(stack) >= 2 and stack[0] == "server" and stack[1].startswith("location"):
            blocks.setdefault(stack[1], []).append(d)
    out = {}
    for head, ds in blocks.items():
        words = head.split()
        path = words[-1].lstrip("^") if len(words) > 1 else ""
        if any(path.startswith(p) for p in prefixes) and not any(d.startswith("return ") for d in ds):
            out[head] = ds
    return out


def _gated(directives, *prefixes):
    """True when every (non-redirect) location under the prefixes asks for a login."""
    locs = _locations(directives, *prefixes)
    return bool(locs) and all(
        any(d.split()[0] in ("auth_basic", "auth_request") and d.split()[-1] != "off" for d in ds if d.split())
        for ds in locs.values())


def front_gated(ctx, *prefixes):
    if ctx["front_kind"] == "caddy":
        return _caddy_gated(ctx["front"], *prefixes)
    return _gated(ctx["front"], *prefixes)


def front_has(ctx, *prefixes):
    if ctx["front_kind"] == "caddy":
        return bool(_caddy_routes(ctx["front"], *prefixes))
    return bool(_locations(ctx["front"], *prefixes))


# --- the steps ---------------------------------------------------------------------------
# Each takes the shared context and returns findings. A step never raises: run_step wraps it.

def step_notes(ctx):
    """F1: SilverBullet's shell backend and its login."""
    unit = "silverbullet.service"
    sh = _show(unit, "LoadState", "ActiveState", "UnitFileState", "User", "DynamicUser", "Environment", "EnvironmentFiles")
    if not sh:
        return [_cannot("notes-shell", "Notes add-on", "systemctl is not answering", "F1")]
    if sh.get("LoadState") != "loaded":
        return [F("notes-shell", "Notes add-on (SilverBullet)", "ok", "Not installed.", ref="F1")]
    env = {}
    try:
        for tok in shlex.split(sh.get("Environment", "")):
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v
    except ValueError:
        pass
    from_file = {}
    for part in sh.get("EnvironmentFiles", "").split():
        if part.startswith("/"):
            for line in (_read(part) or "").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    from_file.setdefault(k.strip(), v.strip().strip("'\""))
    merged = {**from_file, **env}
    running = sh.get("ActiveState") == "active"
    shell_off = merged.get("SB_SHELL_BACKEND", "").lower() in ("off", "false", "0")
    read_only = merged.get("SB_READ_ONLY", "").lower() in ("true", "1", "yes", "on")
    sb_login = bool(merged.get("SB_USER"))
    front_login = bool(ctx["front"] and front_gated(ctx, "/notes"))
    login = sb_login or front_login
    state = "running" if running else f"installed but {sh.get('ActiveState', 'not running')} (it starts at boot if enabled)"
    out = []
    if shell_off or read_only:
        out.append(F("notes-shell", "Notes: server-side shell", "ok",
                     f"Off ({'SB_SHELL_BACKEND=off' if shell_off else 'read-only mode'}); the add-on is {state}.", ref="F1"))
    elif not login:
        out.append(F("notes-shell", "Notes: server-side shell with no login", "problem",
                     f"SilverBullet is {state}. Its shell endpoint is on and /notes/ has no login, so anyone who can reach "
                     "the box runs commands as the hub user, which can queue root-helper requests (password change included).",
                     "Now: sudo systemctl stop silverbullet. Fix: Environment=SB_SHELL_BACKEND=off in the unit (not the editable env file), "
                     "plus a login or read-only mode.", "F1"))
    else:
        out.append(F("notes-shell", "Notes: server-side shell on, behind a login", "warn",
                     f"SilverBullet is {state}. The shell endpoint is on; whoever holds the login runs commands as the hub user.",
                     "Set Environment=SB_SHELL_BACKEND=off in the unit.", "F1"))
    if not login and not read_only:
        out.append(F("notes-login", "Notes: no login", "warn",
                     "Guests can edit notes, and notes can carry scripts (Space Lua, widgets) that run on the hub's origin, "
                     "next to /admin.", "SB_USER, read-only mode, or /notes/ behind the admin login.", "F1/S4"))
    if sh.get("DynamicUser") != "yes" and sh.get("User") in (HUB_USER, "", "root"):
        who = sh.get("User") or "root"
        out.append(F("notes-user", "Notes: runs as a user that owns the hub's state", "warn",
                     f"silverbullet.service runs as '{who}', the same user that can write the root helper's request queue, "
                     "so a flaw in the add-on is a takeover of /admin.",
                     "Run it as its own DynamicUser with only the notes folder writable.", "F1"))
    return out


def step_front(ctx):
    """F2, F15, F24, F27 and S3's first line of defence: how the front is set up."""
    directives, note = ctx["front"], ctx["front_note"]
    if directives is None:
        return [F("front", "The web server in front", "warn", f"Not audited: {note}.", ref="F2/F15/F24")]
    if ctx["front_kind"] == "caddy":
        return step_front_caddy(ctx)
    out = []
    # F2: request smuggling needs a kept-alive upstream and a hub that leaves bodies unread.
    ka = [d for s, d in directives if s and s[0].startswith("upstream irate_box_hub") and d.startswith("keepalive")]
    drains = ctx["hub_drains_bodies"]
    if ka and drains is False:
        out.append(F("front-keepalive", "Kept-alive connections to the hub, which leaves request bodies unread", "problem",
                     f"nginx reuses its connections to the hub ({ka[0]}) and this hub version answers some requests "
                     "(the login check, declined PUT/DELETE) without reading the body. The unread bytes become the next request "
                     "on that connection, which nginx never checked against /admin's login: an unauthenticated guest can "
                     "have any /admin action carried out.",
                     "Stopgap: remove 'keepalive' from the upstream. Fix: the hub reads or discards the body before answering, "
                     "or closes the connection.", "F2"))
    elif ka:
        out.append(F("front-keepalive", "Kept-alive connections to the hub", "ok" if drains else "warn",
                     f"nginx keeps connections open ({ka[0]})" + (" and the hub drains request bodies." if drains else
                     "; could not read the hub's code to tell whether it drains request bodies."), ref="F2"))
    else:
        out.append(F("front-keepalive", "Connections to the hub", "ok",
                     "nginx opens one connection per request (no upstream keepalive), so a body the hub leaves unread "
                     "cannot be taken for a second request.", ref="F2"))
    # F15: body size.
    sizes = [(s, d) for s, d in directives if d.startswith("client_max_body_size")]
    server_level = [d.split()[-1] for s, d in sizes if s == ("server",)]
    if not server_level:
        out.append(F("front-body", "Request size limit", "ok", "nginx's default (1 MB) applies server-wide.", ref="F15"))
    else:
        n = nginx_size(server_level[0])
        bigger = [f"{s[-1].split()[-1]} {d.split()[-1]}" for s, d in sizes if s != ("server",)]
        if n == 0:
            out.append(F("front-body", "No request size limit", "warn",
                         "client_max_body_size 0 server-wide: nginx buffers any body to disk before the hub sees it, and the "
                         "hub reads it into memory. One guest can fill the card or exhaust RAM with a few large requests to any path."
                         + (f" Routes with their own limit: {_list(bigger)}." if bigger else ""),
                         "Default 1m, raised only on the store, saves, drop and git routes.", "F15"))
        elif n is None or n > 64 * 1024 ** 2:
            out.append(F("front-body", "Large request size limit", "warn", f"client_max_body_size {server_level[0]} server-wide.",
                         "Keep the default small and raise it per route.", "F15"))
        else:
            out.append(F("front-body", "Request size limit", "ok", f"{server_level[0]} server-wide.", ref="F15"))
    if ctx["hub_caps_json"] is False:
        out.append(F("hub-body", "The hub reads request bodies without a cap", "warn",
                     "server.py's _read_payload reads Content-Length bytes into memory with no limit.",
                     "Cap JSON bodies at about 64 KB.", "F15"))
    # F24: the first X-Forwarded-For entry is the client's to choose.
    xff = [d for s, d in directives if d.startswith("proxy_set_header X-Forwarded-For")]
    if any("$proxy_add_x_forwarded_for" in d for d in xff):
        out.append(F("front-xff", "X-Forwarded-For is appended, not replaced", "warn",
                     "A guest's own X-Forwarded-For header is passed on with the real address after it. A hub that trusts "
                     "the first entry (this version does) lets a guest pick the address it is seen as.",
                     "proxy_set_header X-Forwarded-For $remote_addr;", "F24"))
    elif xff:
        out.append(F("front-xff", "X-Forwarded-For", "ok", "Set to the connecting address only.", ref="F24"))
    # S3/F27: the front is the only login. Check it is really there.
    if not front_gated(ctx, "/admin"):
        out.append(F("front-admin", "/admin has no login in the front", "problem",
                     "No auth_basic or auth_request on the /admin locations: the hub does no authentication of its own.",
                     "Restore the /admin block of irate-box.nginx (reinstall).", "F27"))
    else:
        out.append(F("front-admin", "/admin login", "ok",
                     "The front asks for the login on /admin. (The hub trusts the front completely: F27 asks it to refuse "
                     "requests that did not come with the front's header.)", ref="F27"))
    for pfx, name in (("/term", "/term (the web terminal)"), ("/git-private", "/git-private/")):
        if front_has(ctx, pfx) and not front_gated(ctx, pfx):
            out.append(F(f"front-{pfx.strip('/')}", f"{name} has no login in the front", "problem",
                         f"The route exists in the front with no auth_basic/auth_request.", "Put it behind the admin login.", "S1"))
    return out


def step_front_caddy(ctx):
    """The same four questions as step_front, for Caddy (install.sh --web caddy)."""
    parsed = ctx["front"]
    drains = ctx["hub_drains_bodies"]
    out = []
    # F2: Go's reverse proxy forwards bodies and reuses upstream connections unless told not to.
    ka_off = any(not o and re.match(r"keepalive\s+off\b", l) for _, l, o in parsed)
    if ka_off:
        out.append(F("front-keepalive", "Connections to the hub", "ok",
                     "Caddy's upstream keepalive is off, so an unread body cannot be taken for a second request.", ref="F2"))
    elif drains is False:
        out.append(F("front-keepalive", "Kept-alive connections to the hub, which leaves request bodies unread", "problem",
                     "Caddy reuses its connections to the hub and forwards request bodies, and this hub version answers some "
                     "requests (the login check, declined PUT/DELETE) without reading the body. The unread bytes can become the "
                     "next request on that connection, past Caddy's /admin login. Likely, not yet demonstrated on Caddy (it was "
                     "on nginx).",
                     "The hub reads or discards the body before answering, or closes the connection; stopgap: "
                     "`transport http { keepalive off }` in each reverse_proxy to 127.0.0.1:8000.", "F2"))
    else:
        out.append(F("front-keepalive", "Kept-alive connections to the hub", "ok" if drains else "warn",
                     "Caddy keeps connections open" + (" and the hub drains request bodies." if drains else
                     "; could not read the hub's code to tell whether it drains request bodies."), ref="F2"))
    # F15: Caddy has no default body cap at all.
    site = [(st, l) for st, l, o in parsed if not o and len(st) == 2 and st[1] == "request_body" and l.startswith("max_size")]
    # a cap placed in the site's own request_body, not inside a handle
    top = [l.split()[-1] for st, l in site]
    per = [f"{h.split()[1]} {next((x.split()[-1] for x in ls if x.startswith('max_size')), '?')}"
           for h, ls in ((h, ls) for h, ls in _caddy_routes(parsed, "/").items()) if any(x.startswith("max_size") for x in ls)]
    if top:
        out.append(F("front-body", "Request size limit", "ok", f"{top[0]} site-wide.", ref="F15"))
    else:
        out.append(F("front-body", "No request size limit", "warn",
                     "Caddy has no default body cap and no site-wide request_body max_size is set: Caddy streams bodies, but the "
                     "hub reads each one into memory, and one guest can exhaust RAM with a few large requests to any path."
                     + (f" Routes with their own limit: {_list(per)}." if per else ""),
                     "A small site-wide request_body { max_size 1MB }, raised only on the store, saves, drop and git routes.", "F15"))
    if ctx["hub_caps_json"] is False:
        out.append(F("hub-body", "The hub reads request bodies without a cap", "warn",
                     "server.py's _read_payload reads Content-Length bytes into memory with no limit.",
                     "Cap JSON bodies at about 64 KB.", "F15"))
    # F24: Caddy (2.6+) replaces an untrusted client's X-Forwarded-For unless trusted_proxies says otherwise.
    trusted = [l for _, l, o in parsed if not o and l.startswith("trusted_proxies")]
    header_up = [l for _, l, o in parsed if not o and re.match(r"header_up\s+X-Forwarded-For", l, re.I)]
    if trusted or any("{http.request.header.X-Forwarded-For}" in l for l in header_up):
        out.append(F("front-xff", "X-Forwarded-For from the client is trusted", "warn",
                     f"{(trusted + header_up)[0]}: a guest's own X-Forwarded-For header is passed on, and a hub that trusts the "
                     "first entry (this version does) lets a guest pick the address it is seen as.",
                     "Drop trusted_proxies, or set header_up X-Forwarded-For {remote_host}.", "F24"))
    else:
        out.append(F("front-xff", "X-Forwarded-For", "ok",
                     "No trusted_proxies: Caddy 2.6 and later replace the client's header with the connecting address (an older "
                     "Caddy appended to it: check `caddy version`).", ref="F24"))
    # S3/F27 and the others: the logins.
    if not front_gated(ctx, "/admin"):
        out.append(F("front-admin", "/admin has no login in the front", "problem",
                     "No basic_auth in the /admin handle: the hub does no authentication of its own.",
                     "Restore the /admin block of the Caddyfile (reinstall).", "F27"))
    else:
        out.append(F("front-admin", "/admin login", "ok",
                     "Caddy asks for the login on /admin. (The hub trusts the front completely: F27 asks it to refuse "
                     "requests that did not come with the front's header.)", ref="F27"))
    for pfx, name in (("/term", "/term (the web terminal)"), ("/git-private", "/git-private/")):
        if front_has(ctx, pfx) and not front_gated(ctx, pfx):
            out.append(F(f"front-{pfx.strip('/')}", f"{name} has no login in the front", "problem",
                         "The route exists in the Caddyfile with no basic_auth.", "Put it behind the admin login.", "S1"))
    # Caddy's own admin API (localhost:2019) can rewrite the whole front; builds can reach loopback (F8).
    if not any(not o and st == ("",) and l.startswith("admin off") for st, l, o in parsed):
        out.append(F("front-caddy-admin", "Caddy's admin API is on", "warn",
                     "No `admin off` in the global options: Caddy's API listens on 127.0.0.1:2019, and any local process that "
                     "can connect (a build, a compromised add-on) can replace the whole config, /admin's login included.",
                     "Add `admin off` to the global options block.", "F8"))
    return out


def _walk_links(root, depth, cap=MAX_ENTRIES):
    """Links under root down to `depth` levels, as (path, target). Never follows one."""
    found, seen, stack = [], 0, [(root, 0)]
    while stack:
        d, level = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > cap:
                        return found, True
                    if e.is_symlink():
                        try:
                            found.append((e.path, os.readlink(e.path)))
                        except OSError:
                            found.append((e.path, "?"))
                    elif level + 1 < depth and e.is_dir(follow_symlinks=False):
                        stack.append((e.path, level + 1))
        except OSError:
            continue
    return found, False


def step_folders(ctx):
    """F3, F4, F5, F13: links and root-written files in folders the hub owns."""
    if os.geteuid() != 0:
        return [_cannot("folders", "Hub-owned folders", "not root, so other users' folders may be unreadable", "F3")]
    out = []
    ctrl = _lstat(CONTROL)
    if ctrl is None:
        return [F("folders", "Hub-owned folders", "warn", f"{CONTROL} is not there: nothing to audit (is the hub installed?).", ref="F3")]
    hub_uid = _uid_of(HUB_USER)
    # Root's output folders: who owns them decides whether F3/F13 are open.
    owner = _name_of(ctrl.st_uid)
    results = _lstat(CONTROL / "results")
    hub_owned = [p for p, st in (("control/", ctrl), ("control/results/", results)) if st and st.st_uid not in (0,)]
    root_files, root_in_hub = 0, []
    try:
        with os.scandir(CONTROL) as it:
            for e in it:
                st = e.stat(follow_symlinks=False)
                if st.st_uid == 0 and stat.S_ISREG(st.st_mode):
                    root_files += 1
                    root_in_hub.append(e.name)
    except OSError:
        pass
    if hub_owned:
        out.append(F("folders-control", "Root writes into folders the hub owns", "problem",
                     f"{_list(hub_owned)} {'is' if len(hub_owned) == 1 else 'are'} owned by '{owner}', and root writes, chowns and "
                     f"replaces files there ({root_files} root-written file{'s' if root_files != 1 else ''}"
                     f"{': ' + _list(root_in_hub) if root_in_hub else ''}). Python's write, open and chown follow links, so "
                     "code running as the hub can make root overwrite or hand over any file, root's own code included: "
                     "hub compromise becomes root. The uplink watchdog, network inventory and RTC timers do it with no request at all.",
                     "control/ root-owned with only requests/ writable by the hub; results and state files in a root-owned "
                     "folder the hub can only read.", "F3/F13"))
    else:
        out.append(F("folders-control", "Root's output folders", "ok", "control/ and control/results/ are root-owned.", ref="F3/F13"))
    # Links planted in the folders root works in.
    watch = [CONTROL, CONTROL / "results", STATE / "zim", STATE / "library", STATE / "firmware",
             STATE / "git", STATE / "ci", STATE / "ci" / "queue", STATE / "ci" / "runs", STATE / "ci" / "work",
             STATE / "ci" / "home", STATE / "notes"]
    links, truncated = [], False
    for w in watch:
        depth = 1 if w in (STATE / "notes", STATE / "git", STATE / "zim") else 2
        found, cut = _walk_links(w, depth)
        links += found
        truncated = truncated or cut
    # Folders root recreates itself at every install are the interesting ones.
    risky = [p for p, _ in links if Path(p).parent.name in ("control", "results", "zim", "library", "firmware", "ci", "queue", "runs", "work", "home")
             or Path(p).name == "quarantine"]
    if links:
        shown = [f"{Path(p).relative_to(STATE)} → {t}" for p, t in links[:MAX_LISTED]]
        out.append(F("folders-links", "Links inside the hub's folders", "problem" if risky else "warn",
                     f"{len(links)} link{'s' if len(links) != 1 else ''}{'+' if truncated else ''}: {_list(shown)}. Root's "
                     "installer, helper and watchdogs chown, chmod and write paths here; a link to /etc, /root or /opt/irate-box "
                     "makes them act on the target. Notes and library content may hold links on purpose; control/, zim/ and ci/ never should.",
                     "Inspect each (ls -l), remove any not made by you, and find out how it got there.", "F3/F4/F5"))
    else:
        out.append(F("folders-links", "Links inside the hub's folders", "ok",
                     "None in control/, zim/, library/, firmware/, git/, ci/ or notes/ (top levels).", ref="F3/F4/F5"))
    q = _lstat(STATE / "zim" / "quarantine")
    if q is not None and (stat.S_ISLNK(q.st_mode) or (hub_uid is not None and q.st_uid == hub_uid)):
        out.append(F("folders-quarantine", "Kiwix quarantine folder", "problem" if stat.S_ISLNK(q.st_mode) else "warn",
                     "zim/quarantine is " + ("a link" if stat.S_ISLNK(q.st_mode) else f"owned by '{_name_of(q.st_uid)}'") +
                     "; the doctor's fix chowns it, and the hub chooses what it points at.",
                     "Keep the quarantine folder root-owned, outside zim/.", "F4"))
    ci = _lstat(STATE / "ci")
    if ci is not None and ci.st_uid != 0:
        out.append(F("folders-ci", "The build user owns its folders", "warn",
                     f"{STATE}/ci is owned by '{_name_of(ci.st_uid)}': it can turn queue/, runs/, work/ or home/ into links that "
                     "the next install chowns or chmods (damage to /opt/irate-box or /root; the 2770 variant hands the target to the hub).",
                     "Make ci/ root-owned with only its subfolders owned by hubci.", "F5"))
    return out


def step_code(ctx):
    """F6, F20 and the installed code: owned by root, allow-lists, markers of the review's fixes."""
    out = []
    if not CODE.is_dir():
        return [F("code", "Installed code", "warn", f"{CODE} is not there.", ref="F6")]
    loose, seen = [], 0
    for dirpath, dirs, files in os.walk(CODE):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in dirs + files:
            seen += 1
            if seen > MAX_ENTRIES * 4:
                break
            st = _lstat(os.path.join(dirpath, name))
            if st and (st.st_uid != 0 or (st.st_mode & 0o022 and not stat.S_ISLNK(st.st_mode))):
                loose.append(os.path.relpath(os.path.join(dirpath, name), CODE))
    if loose:
        out.append(F("code-owner", "Installed code not root-only", "problem",
                     f"{len(loose)} path(s) under {CODE} are not root-owned or are group/world-writable: {_list(loose)}. "
                     "The root helper, the doctors and install.sh run from here, so whoever can write them is root.",
                     f"chown -R root:root {CODE} && chmod -R go-w {CODE}", "F3"))
    else:
        out.append(F("code-owner", "Installed code", "ok", f"Everything under {CODE} is root-owned and not group/world-writable.", ref="F3"))
    root_dir = CODE / "irate_box" / "root"
    st = _lstat(str(root_dir))
    if st and stat.S_ISDIR(st.st_mode):
        if st.st_mode & 0o077:
            out.append(F("code-root-dir", "Root-only modules readable by others", "warn",
                         f"{root_dir} is mode {stat.S_IMODE(st.st_mode):o}: the hub's users can read and run the root helper "
                         "and the doctors (they would fail without root, but nothing they run needs them).",
                         f"chmod 700 {root_dir} (install.sh does this)", "F3"))
        else:
            out.append(F("code-root-dir", "Root-only modules", "ok", f"{root_dir} is readable by root alone.", ref="F3"))
    health = ctx["src"].get("health.py") or ""
    m = re.search(r"OUR_UNIT\s*=\s*re\.compile\((.*?)\)\s*$", health, re.S | re.M)
    wide = bool(m and re.search(r"syncthing@\[", m.group(1)))
    instances = [u.split()[0] for u in _sc("list-units", "syncthing@*", "--all", "--no-legend").splitlines() if u.split()]
    foreign = [u for u in instances if u != f"syncthing@{HUB_USER}.service"]
    if foreign:
        out.append(F("code-syncthing-running", "Syncthing instance for another account", "problem",
                     f"{_list(foreign)} exists. A syncthing@root instance runs Syncthing as root with a GUI the hub can drive "
                     "to share root's files.", "Disable it: systemctl disable --now <unit>.", "F6"))
    if wide:
        out.append(F("code-syncthing", "Restart allow-list permits syncthing@<any user>", "problem",
                     "health.py's OUR_UNIT accepts syncthing@[a-z_]…, so a forged request can start syncthing@root.service.",
                     f"Allow only syncthing@{HUB_USER}.service; build the list from the units the install created.", "F6"))
    elif health:
        out.append(F("code-syncthing", "Restart allow-list", "ok", "Syncthing instances are not open-ended in OUR_UNIT.", ref="F6"))
    store = ctx["src"].get("store.py") or ""
    if re.search(r"Access-Control-Allow-Origin[\"'],\s*[\"']\*[\"']", store):
        out.append(F("code-cors", "Wildcard CORS on the store, saves and drop", "warn",
                     "store.py answers Access-Control-Allow-Origin: *, so a website visited by anyone who can reach the hub can "
                     "read drop files and saves and (with F16) overwrite them.",
                     "Drop the header: on the box the apps are same-origin.", "F20"))
    elif store:
        out.append(F("code-cors", "CORS on the store", "ok", "No wildcard Access-Control-Allow-Origin in store.py.", ref="F20"))
    return out


# Units the audit looks at for sandboxing: the hub's own and the add-ons'.
def _our_units():
    names = set()
    for pat in OUR_UNIT_PATTERNS:
        for line in _sc("list-unit-files", pat, "--no-legend").splitlines():
            n = line.split()[0] if line.split() else ""
            if n.endswith(".service") and n != "nginx.service":
                names.add(n)
    return sorted(names)


UNIT_PROPS = ("LoadState", "User", "DynamicUser", "ProtectSystem", "NoNewPrivileges", "PrivateTmp", "ProtectHome",
              "ProtectProc", "IPAddressDeny", "PrivateNetwork", "RestrictAddressFamilies", "CapabilityBoundingSet")


def step_units(ctx):
    """F8, F19: build unit's reach to loopback; sandboxing of root and hub units."""
    units = ctx["units"]
    if not units:
        return [_cannot("units", "Unit sandboxing", "no systemd units found (or systemctl is not answering)", "F19")]
    out, root_weak, hub_weak = [], [], []
    for u, p in units.items():
        if p.get("LoadState") != "loaded":
            continue
        is_root = p.get("User", "") in ("", "root") and p.get("DynamicUser") != "yes"
        strict = p.get("ProtectSystem") in ("strict",)
        weak = [name for name, ok in (
            ("ProtectSystem=strict", strict), ("NoNewPrivileges", p.get("NoNewPrivileges") == "yes"),
            ("PrivateTmp", p.get("PrivateTmp") == "yes"), ("ProtectHome", p.get("ProtectHome") not in ("no", "", None)),
            ("ProtectProc=invisible", p.get("ProtectProc") in ("invisible", "noaccess"))) if not ok]
        if len(weak) >= 4:
            (root_weak if is_root else hub_weak).append(f"{u[:-8]} ({len(weak)} of 5 missing)")
    if root_weak:
        out.append(F("units-root", "Root units without sandboxing", "warn",
                     f"{_list(root_weak)} run as root with little or no ProtectSystem, NoNewPrivileges, PrivateTmp, ProtectHome "
                     "or ProtectProc. A flaw in one is root with nothing in between.",
                     "ProtectSystem=strict with real ReadWritePaths, NoNewPrivileges, PrivateTmp, ProtectHome, "
                     "ProtectProc=invisible; narrow CapabilityBoundingSet and SystemCallFilter where possible.", "F19"))
    else:
        out.append(F("units-root", "Root units", "ok", "None of the hub's root units is left with its sandbox almost empty.", ref="F19"))
    if hub_weak:
        out.append(F("units-hub", "Guest-reachable units with a bare sandbox", "warn",
                     f"{_list(hub_weak)}: guest-reachable services (the hub, fcgiwrap with cgit and git-http-backend, add-ons) "
                     "with few of ProtectSystem=strict, NoNewPrivileges, PrivateTmp, ProtectHome, ProtectProc. The git servers "
                     "run as the same user as the hub, with write access to the root helper's request queue.",
                     "Harden the units and give the git servers a user of their own.", "F19"))
    else:
        out.append(F("units-hub", "Guest-reachable units", "ok", "Each has most of the standard sandboxing set.", ref="F19"))
    ci = units.get("irate-box-ci.service")
    if ci and ci.get("LoadState") == "loaded":
        deny = ci.get("IPAddressDeny", "").strip()
        private = ci.get("PrivateNetwork") == "yes"
        families = ci.get("RestrictAddressFamilies", "")
        blocks_loopback = private or any(x in deny for x in ("127.", "localhost", "::1", "any"))
        if blocks_loopback:
            out.append(F("units-ci-loopback", "Builds and the hub's loopback", "ok",
                         "irate-box-ci.service cannot connect to 127.0.0.1" + (" (private network)." if private else f" (IPAddressDeny={deny})."), ref="F8"))
        else:
            out.append(F("units-ci-loopback", "Builds can reach the hub's admin API on loopback", "problem",
                         "irate-box-ci.service has no IPAddressDeny, PrivateNetwork or RestrictAddressFamilies"
                         f"{' (' + families + ')' if families.strip() else ''}. Builds run code from private repos and what they "
                         "fetch (submodules, PlatformIO packages), and can call 127.0.0.1:8000 where the hub does no authentication "
                         "of its own: password, services, updates.",
                         "IPAddressDeny=localhost on the unit (or PrivateNetwork=yes with an opt-in for internet), ProtectProc=invisible.", "F8"))
    return out


# --- add-ons: one generic check for everything apps.d declares, plus a probe table ----------

MOSQUITTO_DIR = Path(os.environ.get("HUB_MOSQUITTO_DIR", "/etc/mosquitto"))
LOOPBACK_WORDS = ("127.0.0.1", "localhost", "::1", "lo")
WIDE_WORDS = ("0.0.0.0", "::", "*", "[::]")


def load_addons():
    """[{id, title, unit, port, path, option, consent}] for every app in apps.d that names a
    service (so an add-on added there later is audited with no change here), plus Tailscale,
    which the OS image ships and no manifest describes."""
    out = []
    folder = next((d for d in (CODE / "apps.d", Path(__file__).resolve().parents[2] / "apps.d") if d.is_dir()), None)
    for f in sorted(folder.glob("*.json")) if folder else ():
        try:
            m = json.loads(_read(f) or "")
        except ValueError:
            continue
        st, ad = m.get("status") or {}, m.get("addon") or {}
        if not st.get("unit"):
            continue
        out.append({"id": m.get("id", f.stem), "title": ad.get("title") or st.get("name") or m.get("id", f.stem),
                    "unit": st["unit"].replace("@hub.", f"@{HUB_USER}."), "port": st.get("port"), "path": st.get("path"),
                    "option": ad.get("option"), "consent": ad.get("consent")})
    out.append({"id": "tailscale", "title": "Tailscale", "unit": "tailscaled.service", "port": None, "path": None,
                "option": None, "consent": None, "root_ok": True})
    return out


def _requested_options():
    """The --with-* options the install was given, or None when there is no record."""
    text = _read(ETC / "install-options")
    if text is None:
        return None
    return {l.strip() for l in text.splitlines() if l.strip().startswith("--with-")}


def _listening():
    """{port: [addresses]} of every TCP/UDP listener, loopback included; None if ss is missing."""
    try:
        r = subprocess.run(("ss", "-H", "-ltnu"), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    found = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            addr, _, port = parts[4].rpartition(":")
            if port.isdigit():
                found.setdefault(int(port), set()).add(addr.partition("%")[0].strip("[]") or "*")
    return {k: sorted(v) for k, v in found.items()}


def _is_loop(addr):
    return addr.startswith("127.") or addr == "::1" or addr.startswith("::ffff:127.")


def _argv_of(show):
    m = re.search(r"argv\[\]=(.*?) ; ignore_errors", show.get("ExecStart", ""))
    return m.group(1).split() if m else []


def _env_of(show):
    env = {}
    try:
        for tok in shlex.split(show.get("Environment", "")):
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v
    except ValueError:
        pass
    return env


def _mosquitto_conf():
    texts = [_read(MOSQUITTO_DIR / "mosquitto.conf") or ""]
    try:
        texts += [_read(p) or "" for p in sorted((MOSQUITTO_DIR / "conf.d").glob("*.conf"))]
    except OSError:
        pass
    return "\n".join(texts)


def _runs_as(a, show):
    """The user the add-on's process ends up as. Mosquitto started as root drops to its `user`
    setting, "mosquitto" unless the config says otherwise."""
    if show.get("DynamicUser") == "yes":
        return "a dynamic user"
    user = show.get("User", "") or "root"
    if user == "root" and a["unit"].startswith("mosquitto"):
        m = re.findall(r"(?m)^\s*user\s+(\S+)", _mosquitto_conf())
        return m[-1] if m else "mosquitto"
    return user


def probe_mosquitto(ctx, a, show):
    """S9: the broker's own config. Returns [(severity, text, fix)]."""
    conf = _mosquitto_conf()
    if not conf.strip():
        return [("warn", f"could not read its config under {MOSQUITTO_DIR}", "")]
    anon = re.search(r"(?m)^\s*allow_anonymous\s+true\b", conf)
    acl = re.search(r"(?m)^\s*acl_file\s+(\S+)", conf)
    pw = re.search(r"(?m)^\s*password_file\s+\S+", conf)
    out = []
    if anon and not acl:
        out.append(("problem", "anonymous clients are allowed with no ACL: anyone on the network can publish and subscribe to any topic", "acl_file with `topic readwrite msh/#`, or password_file and allow_anonymous false"))
    elif anon:
        topics = re.findall(r"(?m)^\s*topic\s+(?:readwrite|write|read)?\s*(\S+)", _read(acl.group(1)) or "")
        if "#" in topics:
            out.append(("problem", "the ACL gives anonymous clients every topic (#)", "limit it to msh/#"))
        else:
            out.append(("warn", "anonymous clients are allowed" + (f", limited by the ACL to {_list(topics)}" if topics else " (ACL unreadable)") +
                        (" and a password file exists, so check it is used" if pw else ""), "an access point's address only, and a password file, once the box is an AP"))
    if not re.search(r"(?m)^\s*message_size_limit\s+[1-9]", conf):
        out.append(("warn", "no message_size_limit: one client can send messages of any size", "message_size_limit 4096"))
    if not re.search(r"(?m)^\s*max_connections\s+\d+", conf):
        out.append(("warn", "no max_connections: one client can open sockets until the box runs out", "max_connections 64 per listener"))
    return out


def probe_syncthing(ctx, a, show):
    """The GUI is driven through the hub: look at its config, not only its unit."""
    cfg = next((t for t in (_read(STATE / ".local/state/syncthing/config.xml"), _read(STATE / ".config/syncthing/config.xml")) if t), None)
    if cfg is None:
        return [("warn", f"could not read its config.xml under {STATE}", "")]
    out = []
    gui = re.search(r"<gui\b.*?</gui>", cfg, re.S)
    g = gui.group(0) if gui else ""
    addr = re.search(r"<address>([^<]*)</address>", g)
    if addr and not _is_loop(addr.group(1).rpartition(":")[0].strip("[]") or "*"):
        out.append(("problem", f"its GUI listens on {addr.group(1)}, not loopback", "--gui-address=127.0.0.1:8384"))
    if re.search(r"<insecureAdminAccess>true", g):
        out.append(("problem", "insecureAdminAccess is on: the GUI accepts non-local requests with no password", ""))
    if not re.search(r"<password>[^<]+</password>", g):
        out.append(("warn", "the GUI has no password; the front's /sync/ login is the only gate, and the hub user can drive it directly", "set a GUI password"))
    for tag, what in (("globalAnnounceEnabled", "global discovery"), ("relaysEnabled", "relay use")):
        if re.search(rf"<{tag}>true</{tag}>", cfg):
            out.append(("warn", f"{what} is on: with an uplink this box announces itself to Syncthing's public servers", f"<{tag}>false</{tag}> for a box that only syncs on its own network"))
    ur = re.search(r"<urAccepted>(-?\d+)</urAccepted>", cfg)
    if ur and int(ur.group(1)) > 0:
        out.append(("warn", "usage reporting is on", "<urAccepted>-1</urAccepted>"))
    return out


def probe_kiwix(ctx, a, show):
    return [] if "--blockexternal" in _argv_of(show) else [("warn", "no --blockexternal: book pages may load content from other sites", "add --blockexternal")]


def probe_ttyd(ctx, a, show):
    argv = _argv_of(show)
    out = []
    if argv and os.path.basename(argv[-1]) in ("sh", "bash", "zsh", "dash", "ash"):
        out.append(("problem", f"it runs a bare shell ({argv[-1]}), so the login is the only barrier to a shell as "
                    f"{show.get('User') or 'root'}", "run /bin/login, which asks for a real account"))
    if "--interface" not in argv and "-i" not in argv:
        out.append(("problem", "no --interface: ttyd listens on every interface", "--interface lo"))
    return out


def probe_tailscale(ctx, a, show):
    try:
        r = subprocess.run(("tailscale", "debug", "prefs"), capture_output=True, text=True, timeout=20)
        prefs = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return [("warn", "could not read its preferences (tailscale debug prefs)", "")]
    out = []
    if prefs.get("RunSSH"):
        out.append(("warn", "Tailscale SSH is on: tailnet members the ACL allows get a shell on this box", "tailscale set --ssh=false"))
    routes = prefs.get("AdvertiseRoutes") or []
    if routes:
        out.append(("warn", f"it advertises routes ({_list(routes)}): the box forwards tailnet traffic into its own network", "tailscale set --advertise-routes="))
    if prefs.get("LoggedOut"):
        out.append(("warn", "logged out: installed but not joined to a tailnet", ""))
    return out


ADDON_PROBES = (("mosquitto", probe_mosquitto), ("syncthing@", probe_syncthing), ("kiwix", probe_kiwix),
                ("ttyd", probe_ttyd), ("tailscaled", probe_tailscale))
RANK_STATUS = {"problem": 0, "warn": 1}


def step_addons(ctx):
    """Every add-on and app service apps.d declares, installed or not. The same questions for
    each: does it run as root, how is it bound, did the owner ask for it, who can reach it
    through the front; then the probe for that program's own config, if there is one."""
    addons = ctx["addons"]
    if not ctx["units"] and not any(ctx["addon_props"].get(a["unit"]) for a in addons):
        return [_cannot("addons", "Add-ons", "systemctl is not answering", "S1 S9")]
    requested, listening = ctx["requested"], ctx["listening"]
    out, absent = [], []
    for a in addons:
        show = ctx["addon_props"].get(a["unit"]) or {}
        if show.get("LoadState") != "loaded":
            absent.append(a["title"])
            continue
        argv, env = _argv_of(show), _env_of(show)
        active = show.get("ActiveState") == "active"
        enabled = show.get("UnitFileState") in ("enabled", "enabled-runtime", "static", "indirect", "generated", "alias")
        as_root = _runs_as(a, show) == "root"
        issues = []   # (severity, text, fix)
        if as_root and not a.get("root_ok"):
            issues.append(("warn", "runs as root: a flaw in it is root", "run it as an unprivileged or dynamic user"))
        wide = [t for t in argv if t in WIDE_WORDS or re.search(r"(?:^|[=:])(0\.0\.0\.0|\[?::\]?)(?::\d+)?$", t)]
        wide += [f"{k}={v}" for k, v in env.items() if v in WIDE_WORDS and k.upper() in ("HOST", "HOSTNAME", "BIND", "ADDRESS", "LISTEN", "SB_HOSTNAME")]
        wide += [t for t in argv if re.search(r"(?:insecure|no-?auth|disable-auth|allow-anonymous)", t)]
        if wide:
            issues.append(("problem", f"its start command opens it up ({_list(wide, 3)})", "bind it to 127.0.0.1 behind the front's login"))
        if a["port"] and listening is not None and active:
            addrs = listening.get(a["port"], [])
            open_on = [x for x in addrs if not _is_loop(x)]
            if open_on:
                said = f' Its install consent says: "{a["consent"]}"' if a["consent"] else ""
                issues.append(("warn", f"port {a['port']} answers on {_list(open_on, 3)}, not only on loopback: anyone on the network reaches it directly, not through the front's login.{said}",
                               "bind to 127.0.0.1 and route it through the front, unless the network-facing port is the point"))
            elif not addrs:
                issues.append(("warn", f"active, but nothing listens on port {a['port']}", f"journalctl -u {a['unit']} -n 30"))
        if a["option"] and requested is not None and a["option"] not in requested and active:
            issues.append(("warn", f"running, but {a['option']} is not in the install record: it was added or left over from outside irate-box",
                           f"remove it if unwanted: systemctl disable --now {a['unit']}"))
        if a["path"] and ctx["front"] is not None:
            has, gated = front_has(ctx, a["path"]), front_gated(ctx, a["path"])
            if has and not gated and as_root:
                issues.append(("problem", f"a root service open to guests: {a['path']} has no login in the front", "put it behind the admin login"))
        if a["unit"] not in ctx["units"]:  # sandboxing not covered by the units step
            weak = [n for n, ok in (("ProtectSystem=strict", show.get("ProtectSystem") == "strict"), ("NoNewPrivileges", show.get("NoNewPrivileges") == "yes"),
                                   ("PrivateTmp", show.get("PrivateTmp") == "yes"), ("ProtectHome", show.get("ProtectHome") not in ("no", "", None))) if not ok]
            if len(weak) >= 3:
                issues.append(("warn", f"little sandboxing ({len(weak)} of 4 missing)", "ProtectSystem=strict, NoNewPrivileges, PrivateTmp, ProtectHome"))
        for prefix, probe in ADDON_PROBES:
            if a["unit"].startswith(prefix):
                try:
                    issues += probe(ctx, a, show)
                except Exception as exc:
                    issues.append(("warn", f"its probe failed: {type(exc).__name__}: {exc}", ""))
        worst = min((RANK_STATUS[i[0]] for i in issues), default=2)
        if not active and not enabled and worst == 0:
            # Installed but neither running nor started at boot: a risk only once it is started.
            worst = 1
            issues.insert(0, ("warn", "installed but not running or started at boot, so these apply once it is started", ""))
        status = {0: "problem", 1: "warn", 2: "ok"}[worst]
        state = "running" if active else show.get("ActiveState", "stopped")
        who = _runs_as(a, show)
        if issues:
            detail = f"{state}, as {who}. " + " ".join(f"{i[1][0].upper()}{i[1][1:]}." if not i[1].endswith(".") else f"{i[1][0].upper()}{i[1][1:]}" for i in issues)
            fix = "; ".join(i[2] for i in issues if i[2])
        else:
            detail, fix = f"{state}, as {who}; no problem found by the generic checks" + (" or its own probe." if any(a["unit"].startswith(p) for p, _ in ADDON_PROBES) else "."), ""
        out.append(F(f"addon-{a['id']}", a["title"], status, detail, fix, "S1 S9" if a["unit"].startswith("mosquitto") else "F19"))
    if absent:
        out.append(F("addons-absent", "Add-ons not installed", "ok", f"{_list(absent, 12)}: nothing to audit.", ref=""))
    return out


SECRET_FLAGS = re.compile(r"^--?(credential|password|passwd|pass|secret|token|plaintext)$", re.I)
SECRET_KV = re.compile(r"^(?:--?)?[A-Za-z_-]*(pass(word)?|secret|token|credential)[A-Za-z_-]*=\S+", re.I)


def step_cmdlines(ctx):
    """F9: secrets on process command lines, and who can read /proc."""
    hits, scanned, me = [], 0, os.getpid()
    try:
        entries = [e for e in os.listdir(PROC) if e.isdigit()]
    except OSError:
        return [_cannot("cmdlines", "Process command lines", f"{PROC} is not readable", "F9")]
    for pid in entries:
        if int(pid) == me:
            continue
        raw = _read(PROC / pid / "cmdline", 16384)
        if not raw:
            continue
        scanned += 1
        args = [a for a in raw.split("\0") if a]
        name = os.path.basename(args[0]) if args else "?"
        for i, a in enumerate(args):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if SECRET_FLAGS.match(a) and a.lstrip("-").lower() != "plaintext" and nxt:
                hits.append(f"{name} (pid {pid}) {a} …")
            elif a.lstrip("-").lower() == "plaintext" and nxt:
                hits.append(f"{name} (pid {pid}) {a} …")
            elif a.lower() == "password" and nxt.lower() == "set":
                hits.append(f"{name} (pid {pid}) password set …")
            elif SECRET_KV.match(a) and not a.startswith("/"):
                hits.append(f"{name} (pid {pid}) {a.split('=', 1)[0]}=…")
    mount = _read(PROC / "mounts") or ""
    m = re.search(r"^\S+ /proc proc (\S+)", mount, re.M)
    hidepid = bool(m and re.search(r"hidepid=(1|2|invisible|noaccess|ptraceable)\b", m.group(1)))
    out = []
    hits = sorted(set(hits))
    if hits:
        out.append(F("cmdlines-secrets", "A secret on a command line", "problem",
                     f"{len(hits)} process(es): {_list(hits)}. /proc/<pid>/cmdline is readable by every user"
                     f"{'' if not hidepid else ' (hidepid is set here, so only the owner and root)'}: the web terminal's credential is "
                     "the admin password for as long as ttyd runs; password changes and installs show it for a moment.",
                     "Pass secrets on stdin or in a file; give ttyd a credential that is not the admin password; hide /proc "
                     "(hidepid=2) or set ProtectProc=invisible on the untrusted units.", "F9"))
    else:
        out.append(F("cmdlines-secrets", "Secrets on command lines", "ok",
                     f"None among {scanned} processes right now. (A password change, install or ttyd start shows one for a moment.)", ref="F9"))
    if hidepid:
        out.append(F("cmdlines-proc", "/proc hidden from other users", "ok", "hidepid is set on /proc.", ref="F9"))
    else:
        out.append(F("cmdlines-proc", "/proc visible to every user", "warn",
                     "Any local process (a build, a compromised add-on, www-data) can read every command line.",
                     "Mount /proc with hidepid=2, or ProtectProc=invisible on the untrusted units.", "F9"))
    return out


SECRETY = re.compile(r"(?i)(password|passwd|token|secret|credential|api[_-]?key|://[^/\s:@]+:[^/\s@]+@)")


def step_secrets(ctx):
    """S13, F30: secrets at rest in /etc/hub and the install record."""
    if not ETC.is_dir():
        return [F("secrets", "Secrets at rest", "warn", f"{ETC} is not there.", ref="S13")]
    readable, creds = [], []
    est = _lstat(ETC)
    for p in sorted(ETC.iterdir()):
        st = _lstat(p)
        if not st or not stat.S_ISREG(st.st_mode):
            continue
        text = _read(p, 65536) or ""
        if SECRETY.search(text) or p.name in ("admin-password", "ttyd.env"):
            if st.st_mode & 0o044:
                readable.append(p.name)
        if p.name == "install-options" and re.search(r"://[^/\s:@]+:[^/\s@]+@", text):
            creds.append(p.name)
    out = []
    if readable:
        bad = [n for n in readable if n in ("admin-password", "ttyd.env")]
        out.append(F("secrets-files", "Secret-looking files readable by other users", "problem" if bad else "warn",
                     f"{_list(readable)} in {ETC} can be read by group or others.",
                     f"chmod 600 {ETC}/<file> (or 640 root:hub where the hub must read it).", "S13"))
    else:
        out.append(F("secrets-files", "Secret-looking files in /etc/hub", "ok", "None is readable by group or others.", ref="S13"))
    if est and est.st_mode & 0o022:
        out.append(F("secrets-dir", "/etc/hub is writable by group or others", "problem", f"{ETC} mode {oct(est.st_mode & 0o7777)}.",
                     f"chmod 755 {ETC}; chown root:root {ETC}", "S13"))
    if creds:
        out.append(F("secrets-install-options", "Credentials in the install record", "warn",
                     "install-options holds a URL with a user:token@ part (taken from `git remote get-url origin`), and the file "
                     "is not 0600.", "Strip credentials before recording; chmod 600.", "F30"))
    return out


def step_git(ctx):
    """F17, F18: what pushed content can do, when guests may push."""
    root = STATE / "git"
    guest = _lstat(root / "guest-push") is not None
    out = []
    missing = []
    for area in ("public", "private"):
        text = _read(root / f"cgitrc-{area}")
        if text is None:
            continue
        if not all(re.search(rf"(?m)^mimetype\.{ext}\s*=\s*text/plain\s*$", text) for ext in ("html", "svg")):
            missing.append(area)
    if _lstat(root / "cgitrc-public") is None and _lstat(root / "cgitrc-private") is None:
        return [F("git", "Git servers", "ok", "Not set up.", ref="F17/F18")]
    if missing:
        out.append(F("git-mimetype", "cgit serves pushed HTML as HTML", "warn",
                     f"cgitrc-{_list(missing)} does not map .html and .svg to text/plain, so cgit's /plain/ view serves a pushed "
                     "page on the hub's origin, next to /admin"
                     + (" — and guest push is ON, so a guest's page is one link away from the owner's login." if guest
                        else " (guest push is off, so only pages the owner pushes)."),
                     "mimetype.html=text/plain, mimetype.svg=text/plain (also .htm, .xhtml) in cgitrc, or Content-Security-Policy: sandbox.", "F17"))
    else:
        out.append(F("git-mimetype", "cgit's /plain/ view", "ok", "HTML and SVG are served as text/plain.", ref="F17"))
    weak = []
    pub = root / "public"
    try:
        repos = [p for p in sorted(pub.iterdir()) if (p / "HEAD").exists() and not p.is_symlink()][:200]
    except OSError:
        repos = []
    for r in repos:
        cfg = _read(r / "config") or ""
        miss = [k for k in ("denyNonFastForwards", "denyDeletes", "fsckObjects")
                if not re.search(rf"(?im)^\s*{k}\s*=\s*(true|yes|on|1)\s*$", cfg)]
        if miss:
            weak.append(f"{r.name} ({', '.join(miss)})")
    if weak:
        out.append(F("git-public", "Public repositories can be rewritten", "warn",
                     f"{_list(weak)} lack receive.deny* / fsckObjects"
                     + (": with guest push on, any guest can force-push or delete a branch, and repeated pushes fill the card"
                        " (the 64 MB limit is per push)." if guest else " (guest push is off, so only the owner can push)."),
                     "git config receive.denyNonFastForwards true; receive.denyDeletes true; receive.fsckObjects true; add a total-size quota.", "F18"))
    elif repos:
        out.append(F("git-public", "Public repositories", "ok", f"{len(repos)} protected against rewrites.", ref="F18"))
    if guest and not weak and not missing:
        out.append(F("git-guest", "Guest push", "warn", "Guests may push to the public repositories. Disk fill is the remaining risk (no total quota).",
                     "A pre-receive size check.", "F18"))
    return out


def _sudo_lines():
    """[(file, line)] of active sudoers rules."""
    files = [SUDOERS]
    d = SUDOERS.parent / (SUDOERS.name + ".d")
    try:
        files += [p for p in sorted(d.iterdir()) if "." not in p.name and not p.name.endswith("~")]
    except OSError:
        pass
    seen_any = False
    out = []
    for f in files:
        text = _read(f)
        if text is None:
            continue
        seen_any = True
        for line in text.replace("\\\n", " ").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith("@") and not s.startswith("Defaults"):
                out.append((f.name, s))
    return out if seen_any else None


def step_accounts(ctx):
    """Passwordless sudo, accounts with no password, a second root, the hub users' shells."""
    out = []
    rules = _sudo_lines()
    if rules is None and not os.path.lexists(SUDOERS) and not os.path.lexists(str(SUDOERS) + ".d"):
        out.append(F("acct-sudo", "sudo rules", "ok", "sudo is not installed: there are no sudo rules to check.", ref=""))
    elif rules is None:
        out.append(_cannot("acct-sudo", "sudo rules", f"{SUDOERS} is not readable"))
    else:
        nopass = [(f, s) for f, s in rules if "NOPASSWD" in s]
        service = [(f, s) for f, s in nopass if s.split()[0].lstrip("%") in (HUB_USER, CI_USER, "www-data", "nginx", "caddy")]
        if service:
            out.append(F("acct-sudo-hub", "A hub or web account has passwordless sudo", "problem",
                         f"{_list(f'{f}: {s}' for f, s in service)}. Anything that compromises this account is root.",
                         "Remove the rule.", "F1/F3"))
        other = [(f, s) for f, s in nopass if (f, s) not in service]
        if other:
            temp = [f for f, _ in other if "temp" in f]
            out.append(F("acct-sudo", "Passwordless sudo rules", "warn",
                         f"{_list(f'{f}: {s}' for f, s in other)}."
                         + (" A '-temp' file is a rule meant to be removed after the work it was added for." if temp else ""),
                         "Keep each only as long as it is needed: rm /etc/sudoers.d/<file>, then check `sudo -n true` fails.", ""))
        else:
            out.append(F("acct-sudo", "Passwordless sudo", "ok", "No NOPASSWD rule in sudoers or sudoers.d.", ref=""))
    passwd = _read(PASSWD)
    shadow = _read(SHADOW)
    if passwd is None:
        return out + [_cannot("acct-users", "Accounts", f"{PASSWD} is not readable")]
    users = {}
    for line in passwd.splitlines():
        f = line.split(":")
        if len(f) >= 7:
            users[f[0]] = {"uid": int(f[2]) if f[2].isdigit() else -1, "shell": f[6]}
    roots = [u for u, v in users.items() if v["uid"] == 0 and u != "root"]
    if roots:
        out.append(F("acct-uid0", "A second account with UID 0", "problem", f"{_list(roots)}.", "Remove it or give it a unique UID.", ""))
    nologin = ("nologin", "false")
    if shadow is None:
        out.append(_cannot("acct-nopw", "Accounts with no password", f"{SHADOW} is not readable (run as root)"))
    else:
        empty = []
        for line in shadow.splitlines():
            f = line.split(":")
            if len(f) >= 2 and f[1] == "" and f[0] in users and not users[f[0]]["shell"].endswith(nologin):
                empty.append(f[0])
        if empty:
            out.append(F("acct-nopw", "Accounts with an empty password", "problem",
                         f"{_list(empty)} can log in with no password.", "passwd <user>, or passwd -l <user>.", ""))
        else:
            out.append(F("acct-nopw", "Accounts with an empty password", "ok", "None with a login shell.", ref=""))
    badsh = [u for u in (HUB_USER, CI_USER) if u in users and not users[u]["shell"].endswith(nologin)]
    if badsh:
        out.append(F("acct-shells", "Service accounts with a login shell", "warn", f"{_list(badsh)}.",
                     "usermod -s /usr/sbin/nologin <user>", "F19"))
    elif any(u in users for u in (HUB_USER, CI_USER)):
        out.append(F("acct-shells", "Service accounts", "ok", "The hub and build users have no login shell.", ref="F19"))
    return out


def _sysctl(name):
    t = _read(SYS_FS / name.replace(".", "/"), 64)
    try:
        return int(t.strip()) if t else None
    except ValueError:
        return None


def step_kernel(ctx):
    """The kernel's own protections against the link and process tricks in F3/F9."""
    out = []
    links = [(n, _sysctl(n)) for n in ("fs.protected_symlinks", "fs.protected_hardlinks")]
    reg = [(n, _sysctl(n)) for n in ("fs.protected_regular", "fs.protected_fifos")]
    off = [f"{n.split('.')[1]}=0" for n, v in links if v == 0]
    na = [n.split(".")[1] for n, v in links + reg if v is None]
    weakreg = [f"{n.split('.')[1]}=0" for n, v in reg if v == 0]
    if off:
        out.append(F("kernel-links", "Link protections off", "problem",
                     f"{_list(off)}: other users' links in sticky world-writable folders (/tmp) are followed.",
                     "sysctl -w fs.protected_symlinks=1 fs.protected_hardlinks=1 (persist in /etc/sysctl.d).", "F3"))
    else:
        out.append(F("kernel-links", "Link protections", "ok" if not [1 for _, v in links if v is None] else "warn",
                     "protected_symlinks and protected_hardlinks are on" + ("." if not na else f" (not present on this kernel: {_list(na)})."),
                     "They only cover sticky world-writable folders: root's writes in hub-owned folders (F3) are not helped.", "F3"))
    if weakreg:
        out.append(F("kernel-regular", "protected_regular / protected_fifos off", "warn", f"{_list(weakreg)}.",
                     "sysctl -w fs.protected_regular=2 fs.protected_fifos=2", ""))
    pt = _sysctl("kernel.yama.ptrace_scope")
    if pt == 0:
        out.append(F("kernel-ptrace", "Any process may ptrace another of the same user", "warn",
                     "kernel.yama.ptrace_scope=0: a compromised hub process can read the memory of its siblings (the admin "
                     "password handled by the hub).", "sysctl -w kernel.yama.ptrace_scope=1", "F9"))
    hard = [(n.split('.')[-1], _sysctl(n)) for n in ("kernel.kptr_restrict", "kernel.dmesg_restrict")]
    lax = [f"{n}=0" for n, v in hard if v == 0]
    if lax:
        out.append(F("kernel-info", "Kernel addresses and log readable by everyone", "warn", f"{_list(lax)}.",
                     "sysctl -w kernel.kptr_restrict=1 kernel.dmesg_restrict=1", ""))
    elif not out or all(f["status"] == "ok" for f in out):
        out.append(F("kernel-info", "Other kernel settings", "ok",
                     "ptrace scope, kptr_restrict and dmesg_restrict are not wide open (or not present on this kernel).", ref=""))
    return out


STEPS = [
    ("notes", "Notes add-on", "F1", step_notes),
    ("front", "The web server in front", "F2 F15 F24 F27", step_front),
    ("folders", "Links and root-written files in hub-owned folders", "F3 F4 F5 F13", step_folders),
    ("code", "Installed code and allow-lists", "F3 F6 F20", step_code),
    ("units", "Unit sandboxing and the build unit", "F8 F19", step_units),
    ("addons", "Add-ons and app services", "S1 S9 S12 F6", step_addons),
    ("cmdlines", "Secrets on command lines, /proc", "F9", step_cmdlines),
    ("secrets", "Secrets at rest", "S13 F30", step_secrets),
    ("git", "Git servers and pushed content", "F17 F18", step_git),
    ("accounts", "Sudo rules and accounts", "", step_accounts),
    ("kernel", "Kernel protections", "F3 F9", step_kernel),
]

# What the box's state cannot show, so the report says so instead of implying a clean bill.
NOT_COVERED = [
    "F7 app-install check/extract/delete race, F10 firmware cache paths, F12 kiwix-manage as root, F14 forged-request reach, "
    "F16 gallery ownership (device locks exist since 2026-10-02; unlocked saves stay open to every guest), F21 verified flag, F23, F25 app.html framing, F26, F28, F29: flaws inside code paths, not settings "
    "(code review and tests).",
    "F11 app bundle source pinning, F22 plain-text transports, F30 checksums and the packaging guard: need the code or the network.",
    "S2 password in clear, S5 SSH, S6 updates and which ports answer (S9): the Security page's scan above. S12 npm audits: not run (they need the network).",
]


# The files the code step reads, by the name its findings use.
HUB_SOURCES = {"server.py": "irate_box/hub/server.py", "health.py": "irate_box/root/health.py",
               "store.py": "irate_box/hub/store.py"}


def _hub_source(name):
    rel = HUB_SOURCES[name]
    return _read(CODE / rel) or _read(Path(__file__).resolve().parents[2] / rel)


def make_context():
    kind, front, note = load_front()
    src = {n: _hub_source(n) for n in ("server.py", "health.py", "store.py")}
    server = src["server.py"]
    drains = None
    caps = None
    if server:
        # The F2 fix declares itself (DRAINS_REQUEST_BODIES = True in server.py); the function names
        # are a fallback for a fix written without the marker.
        drains = bool(re.search(r"(?m)^DRAINS_REQUEST_BODIES\s*=\s*True\b", server)
                      or re.search(r"def _drain|_discard_body|drain_body|def _read_body", server))
        m = re.search(r"def _read_payload\(self\):(.*?)(?=\n    def )", server, re.S)
        caps = bool(m and re.search(r"MAX_|limit|too large|413", m.group(1)))
    units = {u: _show(u, *UNIT_PROPS) for u in _our_units()}
    addons = load_addons()
    addon_props = {a["unit"]: _show(a["unit"], *UNIT_PROPS, "ActiveState", "UnitFileState", "ExecStart", "Environment")
                   for a in addons}
    return {"front": front, "front_kind": kind, "front_note": note, "src": src, "hub_drains_bodies": drains,
            "hub_caps_json": caps, "units": units, "addons": addons, "addon_props": addon_props,
            "requested": _requested_options(), "listening": _listening()}


def run_step(sid, fn, ctx):
    try:
        return fn(ctx)
    except Exception as exc:  # one broken check must not hide the rest
        return [F(f"{sid}-error", "This step failed", "warn", f"{type(exc).__name__}: {exc}", "Report it: the audit's own bug.", "")]


def audit(progress=None):
    """The whole report. `progress(n, total, step, findings)` is called after each step."""
    try:
        ctx = make_context()
    except Exception as exc:
        ctx = {"front": None, "front_kind": None, "front_note": f"setup failed: {exc}", "src": {}, "hub_drains_bodies": None, "hub_caps_json": None, "units": {},
               "addons": [], "addon_props": {}, "requested": None, "listening": None}
    steps = []
    for n, (sid, title, ref, fn) in enumerate(STEPS, 1):
        found = run_step(sid, fn, ctx)
        steps.append({"id": sid, "title": title, "ref": ref, "findings": found})
        if progress:
            progress(n, len(STEPS), steps[-1])
    flat = [f for s in steps for f in s["findings"]]
    version = (_read(CODE / "VERSION") or "unknown").strip()
    return {"at": time.time(), "version": version, "root": os.geteuid() == 0, "steps": steps,
            "counts": {k: sum(1 for f in flat if f["status"] == k) for k in ("problem", "warn", "ok")},
            "not_covered": NOT_COVERED}


def write_report(report, path=REPORT, owner=None):
    """The one thing the doctor writes: its report, to a file root creates itself. O_EXCL on a
    fresh name, then rename over the old one, so a link planted at `path` is replaced, never
    written through (the hub owns the folder this goes in)."""
    path = Path(path)
    # A random name: one the hub cannot guess and create first to block the report.
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        os.write(fd, json.dumps(report, indent=2).encode())
        if owner is not None:
            os.fchown(fd, *owner)
    finally:
        os.close(fd)
    os.replace(tmp, path)


MARK = {"ok": "ok     ", "warn": "WARN   ", "problem": "PROBLEM"}


def print_report(report, only_bad=False, out=sys.stdout):
    for n, s in enumerate(report["steps"], 1):
        shown = [f for f in s["findings"] if not only_bad or f["status"] != "ok"]
        if only_bad and not shown:
            continue
        print(f"\n[{n}/{len(report['steps'])}] {s['title']}" + (f"  ({s['ref']})" if s["ref"] else ""), file=out)
        for f in shown:
            print(f"  {MARK[f['status']]} {f['title']}" + (f"  [{f['ref']}]" if f["ref"] else ""), file=out)
            if f["status"] != "ok" or not only_bad:
                print(f"          {f['detail']}", file=out)
            if f["status"] != "ok" and f["fix"]:
                print(f"          -> {f['fix']}", file=out)
    c = report["counts"]
    print(f"\n{c['problem']} problem(s), {c['warn']} warning(s), {c['ok']} ok."
          + ("" if report["root"] else " Not run as root: some checks could not read what they need."), file=out)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd not in ("report", "summary", "json"):
        sys.exit("usage: secdoctor.py [report | summary | json]")
    if cmd == "json":
        print(json.dumps(audit(), indent=2))
    else:
        rep = audit(progress=(lambda n, t, s: print(f"[{n}/{t}] {s['title']} ...", file=sys.stderr)) if cmd == "report" and sys.stderr.isatty() else None)
        print_report(rep, only_bad=(cmd == "summary"))
