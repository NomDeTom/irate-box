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
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

from irate_box.root import secdoctor_xref

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


def F(fid, title, status, detail, fix="", ref="", source="doctor", about=None):
    """One finding, in the shape every source shares (security-doctor-plan §4): which source said
    it, what it is about ({kind: package | service | setting | file | kit, key}), so the joint
    report can merge what several sources say about one thing; accepted is the reason when the
    box's design accepts it (stage 2)."""
    return {"id": fid, "title": title, "status": status, "detail": detail, "fix": fix, "ref": ref,
            "source": source, "about": about, "accepted": None}


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

def _sb_env(sh):
    """SilverBullet's environment as systemd builds it: Environment= lines, then the
    EnvironmentFile (which wins over them), then `env K=V` on the command line (which wins
    over both, and is where install.sh puts what the owner's file must not undo)."""
    env = {}
    try:
        for tok in shlex.split(sh.get("Environment", "")):
            if "=" in tok:
                k, v = tok.split("=", 1)
                env[k] = v
    except ValueError:
        pass
    for part in sh.get("EnvironmentFiles", "").split():
        if part.startswith("/"):
            for line in (_read(part) or "").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
    m = re.search(r"argv\[\]=(.*?)\s;", sh.get("ExecStart", ""))
    if m:
        argv = m.group(1).split()
        if argv and argv[0].endswith("/env"):
            for tok in argv[1:]:
                if "=" not in tok or tok.startswith("-"):
                    break
                k, v = tok.split("=", 1)
                env[k] = v
    return env


def _front_refuses_notes_capabilities(ctx):
    """Whether the front refuses /notes/.shell, .proxy and .runtime itself (install.sh's
    regex location in nginx, or the @capability matcher in Caddy)."""
    if ctx["front_kind"] == "caddy":
        text = json.dumps(ctx["front"]) if not isinstance(ctx["front"], str) else ctx["front"]
        return bool(re.search(r"notes/\\+\.\(shell\|proxy\|runtime\)", text)) and "403" in text
    for stack, d in ctx["front"] or []:
        if (len(stack) >= 2 and stack[0] == "server" and stack[1].startswith("location")
                and re.search(r"notes/\\\.\(shell\|proxy\|runtime\)", stack[1]) and d.startswith("return 403")):
            return True
    return False


def step_notes(ctx):
    """F1: SilverBullet's shell backend and its login. F31: its HTTP proxy, which reaches the
    hub's loopback API (no login there: the front asks for it) unless SilverBullet has no IP
    networking or the front refuses /notes/.proxy."""
    unit = "silverbullet.service"
    sh = _show(unit, "LoadState", "ActiveState", "UnitFileState", "User", "DynamicUser", "Environment", "EnvironmentFiles",
               "ExecStart", "RestrictAddressFamilies", "IPAddressDeny", "ProtectSystem", "ReadWritePaths")
    if not sh:
        return [_cannot("notes-shell", "Notes add-on", "systemctl is not answering", "F1")]
    if sh.get("LoadState") != "loaded":
        return [F("notes-shell", "Notes add-on (SilverBullet)", "ok", "Not installed.", ref="F1")]
    merged = _sb_env(sh)
    running = sh.get("ActiveState") == "active"
    # SilverBullet 2.11 (server/src/shell.rs): the shell runs when SB_SHELL_BACKEND is unset,
    # empty or "local"; any other value turns it off. Read-only mode turns it off too.
    shell_on = merged.get("SB_SHELL_BACKEND", "").strip().lower() in ("", "local")
    read_only = bool(merged.get("SB_READ_ONLY", "").strip())
    sb_login = bool(merged.get("SB_USER"))
    front_login = bool(ctx["front"] and front_gated(ctx, "/notes"))
    login = sb_login or front_login
    state = "running" if running else f"installed but {sh.get('ActiveState', 'not running')} (it starts at boot if enabled)"
    front_blocks = _front_refuses_notes_capabilities(ctx)
    out = []
    if not shell_on or read_only:
        out.append(F("notes-shell", "Notes: server-side shell", "ok",
                     f"Off ({'read-only mode' if read_only else 'SB_SHELL_BACKEND=' + merged['SB_SHELL_BACKEND']})"
                     f"{'; the front refuses /notes/.shell as well' if front_blocks else ''}; the add-on is {state}.", ref="F1"))
    elif front_blocks:
        out.append(F("notes-shell", "Notes: server-side shell on, refused by the front", "warn",
                     f"SilverBullet is {state} with its shell on; the web server refuses /notes/.shell, so it cannot be reached through it.",
                     "Rerun install.sh: it sets SB_SHELL_BACKEND=off on the unit's command line.", "F1"))
    elif not login:
        out.append(F("notes-shell", "Notes: server-side shell with no login", "problem",
                     f"SilverBullet is {state}. Its shell endpoint is on and /notes/ has no login, so anyone who can reach "
                     "the box runs commands as the hub user, which can queue root-helper requests (password change included).",
                     "Now: sudo systemctl stop silverbullet. Fix: rerun install.sh (shell off on the command line, "
                     "and the web server refuses /notes/.shell).", "F1"))
    else:
        out.append(F("notes-shell", "Notes: server-side shell on, behind a login", "warn",
                     f"SilverBullet is {state}. The shell endpoint is on; whoever holds the login runs commands as the hub user.",
                     "Rerun install.sh: it sets SB_SHELL_BACKEND=off on the unit's command line.", "F1"))
    # F31: the proxy. Two layers, either of which stops it: no IP networking for the unit, and
    # the front refusing /notes/.proxy. Read-only mode turns the proxy off as well.
    families = sh.get("RestrictAddressFamilies", "")
    no_ip = (families and not families.startswith("~") and "AF_INET" not in families) or "0.0.0.0/0" in sh.get("IPAddressDeny", "")
    if read_only or (no_ip and front_blocks):
        out.append(F("notes-proxy", "Notes: HTTP proxy to the hub's loopback API", "ok",
                     "Read-only mode." if read_only else "SilverBullet has no IP networking, and the web server refuses /notes/.proxy.", ref="F31"))
    elif no_ip or front_blocks:
        out.append(F("notes-proxy", "Notes: HTTP proxy, one of two guards", "warn",
                     ("SilverBullet has no IP networking, but the web server passes /notes/.proxy through." if no_ip else
                      "The web server refuses /notes/.proxy, but SilverBullet itself can still reach loopback (anything local that talks to it can use the proxy)."),
                     "Rerun install.sh: it sets both.", "F31"))
    elif not login:
        out.append(F("notes-proxy", "Notes: HTTP proxy reaches /admin with no login", "problem",
                     f"SilverBullet is {state}. Its /notes/.proxy/127.0.0.1:<port>/ forwards any request to the hub's loopback "
                     "port, which has no login of its own, so any guest can read and change /admin's settings and the password.",
                     "Now: sudo systemctl stop silverbullet. Fix: rerun install.sh (SilverBullet on a socket with no IP networking, "
                     "and the web server refuses /notes/.proxy).", "F31"))
    else:
        out.append(F("notes-proxy", "Notes: HTTP proxy reaches /admin, behind the notes login", "warn",
                     "Whoever holds the notes login can reach the hub's loopback API through /notes/.proxy, past the admin login.",
                     "Rerun install.sh.", "F31"))
    if not login and not read_only:
        out.append(F("notes-login", "Notes: no login", "warn",
                     "Guests can edit notes, and notes can carry scripts (Space Lua, widgets) that run on the hub's origin, "
                     "next to /admin.", "SB_USER, read-only mode, or /notes/ behind the admin login.", "F1/S4"))
    confined = sh.get("ProtectSystem") == "strict" and all(
        p.startswith(str(STATE / "notes")) for p in sh.get("ReadWritePaths", "").split()) and sh.get("ReadWritePaths")
    if sh.get("DynamicUser") != "yes" and sh.get("User") in (HUB_USER, "", "root") and not confined:
        who = sh.get("User") or "root"
        out.append(F("notes-user", "Notes: runs as a user that owns the hub's state", "warn",
                     f"silverbullet.service runs as '{who}', the same user that can write the root helper's request queue, "
                     "so a flaw in the add-on is a takeover of /admin.",
                     "Rerun install.sh: it confines the unit to the notes folder (ProtectSystem=strict).", "F1"))
    return out


def _hub_port():
    for line in (_read(ETC / "hub.env") or "").splitlines():
        if line.startswith("PORT="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return 8000


def _hub_ask(method, path, headers, body=None):
    """One request straight to the hub's loopback port, past the front: the status code, or
    None when it does not answer."""
    import http.client
    try:
        conn = http.client.HTTPConnection("127.0.0.1", _hub_port(), timeout=5)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        resp.read()  # read to the end, so closing is not a reset the hub logs
        code = resp.status
        conn.close()
        return code
    except OSError:
        return None


def step_admin_gate(ctx):
    """F27/F31/F8 and S3, by behaviour: what the hub itself does with an /admin request that did
    not come through the front's /admin route (no secret), and with an /admin change that did
    not come from the /admin page (no X-Irate-Admin). Both are asked of the hub directly, on
    loopback, as anything else on the box could; neither changes anything."""
    out = []
    secret = ""
    for line in (_read(ETC / "front-secret.env") or "").splitlines():
        if line.startswith("HUB_FRONT_SECRET="):
            secret = line.split("=", 1)[1].strip()
    code = _hub_ask("GET", "/admin/settings", {"Host": "127.0.0.1"})
    if code is None:
        return [_cannot("admin-loopback", "/admin from loopback", "the hub does not answer on its port", "F27")]
    if code == 200:
        out.append(F("admin-loopback", "/admin answers anything on the box", "problem",
                     f"GET /admin/settings straight to the hub's port {_hub_port()}, with no login and no word from the front, "
                     "answered 200: anything that can send a request to loopback (a build, a proxy such as SilverBullet's "
                     "used to be, another user) is the admin.",
                     "Rerun install.sh: it makes the front's secret (/etc/hub/front-secret.env), and the hub refuses /admin without it.",
                     "F27"))
    else:
        out.append(F("admin-loopback", "/admin only through the front", "ok",
                     f"Straight to the hub's port with no word from the front: {code}.", ref="F27"))
    if secret:
        st = (ETC / "front-secret.env").stat()
        if st.st_uid != 0 or st.st_mode & 0o077:
            out.append(F("admin-secret-file", "The front's secret is readable beyond root", "problem",
                         f"{ETC / 'front-secret.env'} is mode {oct(st.st_mode & 0o777)}, owner uid {st.st_uid}.",
                         f"chown root:root {ETC / 'front-secret.env'}; chmod 600 {ETC / 'front-secret.env'}", "F27"))
        code = _hub_ask("POST", "/admin/settings", {"Host": "127.0.0.1", "X-Irate-Front": secret,
                                                    "Content-Type": "application/json", "Content-Length": "2"}, b"{}")
        if code == 200:
            out.append(F("admin-csrf", "/admin changes accepted without the /admin page's header", "problem",
                         "A POST to /admin with no X-Irate-Admin was accepted: a page elsewhere that the owner visits could "
                         "change settings with the owner's cached login.", "Update the hub (server.py _forged).", "S3"))
        elif code is not None:
            out.append(F("admin-csrf", "/admin changes need the /admin page's header", "ok",
                         f"A POST with the front's secret but no X-Irate-Admin: {code}.", ref="S3"))
    return out


def _addon_ask(path):
    """(status, headers) from the add-on origin on loopback, or (None, {})."""
    import http.client
    port = 8090
    for line in (_read(ETC / "hub.env") or "").splitlines():
        if line.startswith("HUB_ADDON_PORT="):
            try:
                port = int(line.split("=", 1)[1])
            except ValueError:
                pass
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers={"Host": "127.0.0.1"})
        resp = conn.getresponse()
        resp.read()
        out = (resp.status, {k.lower(): v for k, v in resp.getheaders()})
        conn.close()
        return out
    except OSError:
        return None, {}


def step_web_addons(ctx):
    """The local add-ons (plans/no-root-addons-plan): their folder holds only plain files and
    folders (the hub writes it; nginx follows no links there, Caddy would), each one switched on
    was agreed to, and the add-on origin serves nothing of the hub's and sends each its CSP."""
    from irate_box.hub import access as acc, manifests as man
    out = []
    root = STATE / "addons"
    if not root.is_dir():
        return [F("addons", "Web add-ons", "ok", "None: no add-on folder on this box.")]
    odd = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                odd.append(str(p))
    if odd:
        out.append(F("addons-files", "Links or special files among the web add-ons", "problem",
                     f"{len(odd)} in {root}, e.g. {odd[0]}. The hub writes this folder; under Caddy a link would be followed.",
                     "Remove the add-on on /admin and add it again; if they come back, the hub user is not to be trusted.", ""))
    else:
        out.append(F("addons-files", "Web add-ons' files", "ok", f"Plain files and folders only, in {root}."))
    local, errors = man.load_local()
    for name, why in errors.items():
        out.append(F(f"addons-bad-{name}", "A web add-on's manifest is left out", "warn", why,
                     "Remove it on /admin (or the file in /var/lib/hub/apps.d).", ""))
    state = acc.read(ETC / "access.json")
    try:
        consents = json.loads(_read(STATE / "addons-consent.json") or "{}")
    except ValueError:
        consents = {}
    on = [m for m in local if acc.mode_of(state, m["id"]) != "off"]
    for m in on:
        if m["id"] not in consents:
            out.append(F(f"addons-consent-{m['id']}", f"{m['addon']['title']} is on with no consent recorded", "warn",
                         "It is switched on, but /admin has no record of the owner agreeing to it (added by hand?).",
                         "Remove it and add it again from /admin.", ""))
    try:
        cat_state = json.loads(_read(STATE / "addons-catalogue.json") or "{}")
    except ValueError:
        cat_state = {}
    for m in local:
        c = cat_state.get(m["id"]) or {}
        if c.get("status") == "held":
            out.append(F(f"addons-held-{m['id']}", f"{m['addon']['title']}: a newer catalogue version waits for you", "warn",
                         "It changes what you agreed to: " + "; ".join(c.get("held", [])) + ". The agreed version keeps running.",
                         "Read it, then accept it (or remove the add-on) on /admin, Add-ons, Web add-ons.", ""))
    code, _ = _addon_ask("/admin/settings")
    if code is None:
        out.append(F("addons-origin", "The add-on origin", "warn" if local else "ok",
                     "Nothing answers on the add-on port." + (" Added add-ons cannot be opened." if local else ""),
                     "Rerun install.sh: it sets up the add-on server." if local else ""))
    elif code == 200:
        out.append(F("addons-origin", "The add-on origin serves the hub's pages", "problem",
                     "/admin/settings answered 200 on the add-on port: it should serve nothing but the add-ons' folder.",
                     "Restore the add-on server block of irate-box.nginx (reinstall).", ""))
    else:
        out.append(F("addons-origin", "The add-on origin", "ok", f"Serves none of the hub's pages (/admin/settings: {code})."))
    for m in on:
        code, headers = _addon_ask(f"/{m['id']}/")
        if code not in (None, 404) and "content-security-policy" not in headers:
            out.append(F(f"addons-csp-{m['id']}", f"{m['addon']['title']} is served with no Content-Security-Policy", "problem",
                         "Its pages could load or send anything, anywhere.", "Rerun install.sh (the add-on server block).", ""))
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
                     "The front asks for the login on /admin. (Whether the hub refuses /admin that did not come through "
                     "it: the next step, \'/admin from inside the box\'.)", ref="F27"))
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
                     "Caddy asks for the login on /admin. (Whether the hub refuses /admin that did not come through "
                     "it: the next step, \'/admin from inside the box\'.)", ref="F27"))
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
    # The quarantine is the hub's own since F4's fix: health.py moves a book there as the hub, so a
    # link there leads only where the hub could write anyway. Still worth a look if it is one.
    q = _lstat(STATE / "zim" / "quarantine")
    if q is not None and stat.S_ISLNK(q.st_mode):
        out.append(F("folders-quarantine", "Kiwix quarantine folder", "warn",
                     "zim/quarantine is a link. The doctor's fix moves books there as the hub (not root), so it can only "
                     "reach what the hub can, but nothing of the box's makes it a link.",
                     "Look at where it points (ls -l), and remove it if you did not make it.", "F4"))
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
        elif _hub_ask("GET", "/admin/settings", {"Host": "127.0.0.1"}) == 403:
            # The hub refuses /admin without the front's secret (step admin-gate), so what builds
            # reach on loopback is what any guest reaches.
            out.append(F("units-ci-loopback", "Builds can reach the hub on loopback (not /admin)", "warn",
                         "irate-box-ci.service can connect to 127.0.0.1, so builds reach the hub's guest API, as any guest "
                         "can; /admin refuses them (it needs the front's secret, which builds cannot read).",
                         "IPAddressDeny=localhost on the unit, to keep builds off loopback altogether.", "F8"))
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
        out.append(("problem", "no --interface: ttyd listens on every interface", "--interface /run/ttyd/ttyd.sock"))
    else:
        i = argv.index("--interface") if "--interface" in argv else argv.index("-i")
        iface = argv[i + 1] if i + 1 < len(argv) else ""
        if not iface.startswith("/"):
            out.append(("warn", f"on a TCP port ({iface}): anything on the box can reach the login prompt",
                        "a socket only the web server's group can open (install.sh does this)"))
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


def _git_cfg(cfg, section, key):
    """A value from a repository's config text: [section] key = value (git's own case rules)."""
    m = re.search(rf"(?ims)^\s*\[{re.escape(section)}\]\s*$(.*?)(?=^\s*\[|\Z)", cfg)
    v = m and re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*(\S+)\s*$", m.group(1))
    return v.group(1).lower() if v else None


def step_git(ctx):
    """F17, F18 and the push presets (next-work plan step 10): what pushed content can do."""
    root = STATE / "git"
    if _lstat(root / "cgitrc-public") is None and _lstat(root / "cgitrc-private") is None:
        return [F("git", "Git servers", "ok", "Not set up.", ref="F17/F18")]
    out = []
    # cgit 1.2.3 serves /plain/ as text/plain with its own CSP, unless cgitrc maps a type.
    loose = [a for a in ("public", "private")
             if re.search(r"(?m)^(mimetype-file\s*=|mimetype\.(html?|xhtml|svg)\s*=\s*(?!text/plain))", _read(root / f"cgitrc-{a}") or "")]
    if loose:
        out.append(F("git-mimetype", "cgit serves pushed HTML as HTML", "warn",
                     f"cgitrc-{_list(loose)} maps .html or .svg to a type that runs, so a pushed page runs on the hub's origin.",
                     "Remove the mimetype lines, or map them to text/plain.", "F17"))
    else:
        out.append(F("git-mimetype", "cgit's /plain/ view", "ok", "Pushed files are served as text, with cgit's own CSP.", ref="F17"))
    everyone, unhooked, leaky = [], [], []
    for area in ("public", "private"):
        try:
            repos = [p for p in sorted((root / area).iterdir()) if (p / "HEAD").exists() and not p.is_symlink()][:200]
        except OSError:
            repos = []
        for r in repos:
            cfg = _read(r / "config") or ""
            level = _git_cfg(cfg, "irate-box", "write") or "admin"
            if area == "public" and level == "everyone":
                everyone.append(r.name)
            if area == "public" and not ((_git_cfg(cfg, "core", "hookspath") or "").endswith("git-hooks-public")
                                         and _git_cfg(cfg, "receive", "fsckobjects") in ("true", "yes", "on", "1")):
                unhooked.append(r.name)
            if level == "nobody" and _git_cfg(cfg, "http", "receivepack") not in ("false", "no", "off", "0"):
                leaky.append(r.name)
    if everyone:
        out.append(F("git-everyone", "Public repositories anyone may push to", "warn",
                     f"{_list(everyone)} {'is' if len(everyone) == 1 else 'are'} public-everything: anyone on the network may push "
                     "(no rewrites or deletions, and a size cap, by the public hook).",
                     "On /admin's Git page, public-admin-writes unless guests are meant to push.", "F17/F18"))
    if unhooked:
        out.append(F("git-public", "Public repositories without their hook", "warn",
                     f"{_list(unhooked)} lack the public pre-receive hook or receive.fsckObjects, so a push could rewrite "
                     "history or fill the card.", "Rerun install.sh, which sets both.", "F18"))
    if leaky:
        out.append(F("git-readonly", "Read-only repositories that still take pushes", "problem",
                     f"{_list(leaky)} {'is' if len(leaky) == 1 else 'are'} read-only by preset, but http.receivepack is not false.",
                     "Set the preset again on /admin's Git page (it sets both).", "F18"))
    if not (everyone or unhooked or leaky):
        out.append(F("git-public", "Who may push", "ok", "Every repository's preset is held, and the public ones have their hook.", ref="F18"))
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


# Which apps run on an origin of their own (next-work plan step 11; S4): what an app's pages run
# can act with the owner's login only where it shares the hub's origin.
OWN_ORIGIN = (("notes", "Notes (SilverBullet)", r"proxy_pass\s+http://unix:/run/silverbullet/"),
              ("wiki", "Kiwix's books", r"proxy_pass\s+http://127\.0\.0\.1:8081"),
              ("git", "cgit and pushed content", r"cgit\.cgi"))


def _nginx_servers(text):
    """[(listen ports, the server block's text)] for each top-level server block."""
    out, depth, start = [], 0, None
    for m in re.finditer(r"(?m)^\s*server\s*\{|[{}]", text):
        tok = m.group(0)
        if tok.strip().startswith("server") and depth == 0:
            start, depth = m.start(), 1
        elif tok == "{" and start is not None:
            depth += 1
        elif tok == "}" and start is not None:
            depth -= 1
            if depth == 0:
                block = text[start:m.end()]
                out.append((sorted(set(re.findall(r"(?m)^\s*listen\s+(?:\[::\]:)?(\d+)", block))), block))
                start = None
    return out


def step_origins(ctx):
    """Which origin each app is on: the hub's own port, or one of its own (S4)."""
    if ctx["front_kind"] != "nginx":
        return [_cannot("origins", "Apps' origins", "only read from nginx's config", "S4")]
    servers = _nginx_servers(_read(NGINX_CONF) or "")
    if not servers:
        return [_cannot("origins", "Apps' origins", f"{NGINX_CONF} could not be read", "S4")]
    hub_ports = servers[0][0]
    out = []
    for key, name, pattern in OWN_ORIGIN:
        where = [ports for ports, block in servers if re.search(pattern, block)]
        if not where:
            continue
        ports = where[0]
        if ports == hub_ports:
            out.append(F(f"origin-{key}", f"{name}: the hub's own origin", "warn",
                         f"Served on port {', '.join(hub_ports)} with /admin, so what its pages run could act with the owner's login.",
                         "Its own origin (next-work plan step 11).", "S4"))
        else:
            out.append(F(f"origin-{key}", f"{name}: an origin of its own", "ok", f"Port {', '.join(ports)}; the hub is on {', '.join(hub_ports)}.", ref="S4"))
    return out


# --- debsecan: Debian's packages against Debian's security tracker (step 29) -----------------
# Run from the security kit: installed if it is, otherwise unpacked from its cache (debsecan and
# python3-apt's apt_pkg, a module over libapt-pkg, which every Debian has), with the tracker's
# data the librarian keeps (library/debsecan/). Nothing is installed for it, nothing fetched.

DEBSECAN_FEED = STATE / "library" / "debsecan" / "release" / "1"
KITS_ROOT = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
SUMMARY_RE = re.compile(r"^(\S+) (\S+)(?: \((.*)\))?$")


def _codename():
    for line in (_read(Path(os.environ.get("HUB_OS_RELEASE", "/etc/os-release"))) or "").splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _debsecan_command(work):
    """[argv...] and the environment to run debsecan with, or (None, why)."""
    if Path("/usr/bin/debsecan").exists():
        try:
            import apt_pkg  # noqa: F401
            return ["/usr/bin/debsecan"], {}
        except ImportError:
            pass
    pool = KITS_ROOT / "pool"
    debs = {}
    for name in ("debsecan", "python3-apt", "python-apt-common"):
        found = sorted(pool.glob(f"{name}_*.deb")) if pool.is_dir() else []
        if not found:
            return None, "debsecan is not on the box, and the security kit's cache doesn't hold it"
        debs[name] = found[-1]
    # Run as root: only files that are still what the kit's manifest says was fetched.
    from irate_box.root import kits
    damaged = {Path(p.split(" ", 1)[0]).name for p in kits.verify()}
    for d in debs.values():
        if d.name in damaged:
            return None, f"{d.name} in the security kit's cache fails its check"
        r = subprocess.run(["dpkg-deb", "-x", str(d), str(work)], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None, f"unpacking {d.name}: {r.stderr.strip()[:200]}"
    return [sys.executable, str(work / "usr" / "bin" / "debsecan")], {"PYTHONPATH": str(work / "usr" / "lib" / "python3" / "dist-packages")}


def _debsecan(cmd, env, suite, status=None, timeout=900):
    """{package: [(cve, {"fixed", "remote", "urgency"})]} from debsecan --format summary. Fifteen
    minutes: on the Lyra with a firmware build and the deep audit running (load 25 on four cores,
    2026-10-07) five were not enough, and a busy box is not a failed check."""
    args = [*cmd, "--suite", suite, "--source", f"file://{DEBSECAN_FEED}/", "--format", "summary"]
    if status:
        args += ["--status", str(status)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **env))
    except subprocess.TimeoutExpired:
        try:
            load = Path("/proc/loadavg").read_text().split()[1]
        except OSError:
            load = "?"
        raise RuntimeError(f"debsecan took over {timeout // 60} minutes (the box's load: {load}); run the doctor again when it is quieter")
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["debsecan failed"])[-1][:300])
    out = {}
    for line in r.stdout.splitlines():
        m = SUMMARY_RE.match(line.strip())
        if not m:
            continue
        notes = [n.strip() for n in (m.group(3) or "").split(",") if n.strip()]
        urgency = next((n.split()[0] for n in notes if n.endswith(" urgency")), "")
        out.setdefault(m.group(2), []).append((m.group(1), {"fixed": "fixed" in notes, "remote": "remotely exploitable" in notes,
                                                            "urgency": urgency}))
    return out


def _sources():
    """{binary package: its source package}, from dpkg."""
    r = subprocess.run(["dpkg-query", "-W", "-f", "${Package}\t${source:Package}\n"], capture_output=True, text=True, timeout=60)
    return dict(l.split("\t", 1) for l in r.stdout.splitlines() if "\t" in l)


def debsecan_findings(installed, cached, kit_of, feed_date, source_of=None):
    """Findings from debsecan's two runs. A fix Debian has released but the box hasn't installed:
    a problem when remotely exploitable or high urgency (Tom, 2026-10-06), else a warning; one
    finding per package. Unfixed ones are listed, not counted. A kit's cache holding a version
    with a released fix: a warning on that kit (refresh it while online)."""
    out = []
    age = f" Tracker data of {time.strftime('%Y-%m-%d', time.localtime(feed_date))}." if feed_date else ""
    # One finding per source package (perl, perl-base, libperl5.40… are one fix), naming its binaries.
    source_of = source_of or {}
    groups = {}
    for pkg, vulns in installed.items():
        fixed = [(c, n) for c, n in vulns if n["fixed"]]
        if fixed:
            g = groups.setdefault(source_of.get(pkg) or pkg, {"bins": set(), "cves": {}})
            g["bins"].add(pkg)
            for c, n in fixed:
                g["cves"][c] = n
    for src, g in sorted(groups.items()):
        fixed = sorted(g["cves"].items())
        bad = [c for c, n in fixed if n["remote"] or n["urgency"] == "high"]
        bins = sorted(g["bins"])
        out.append(F(f"debsecan-{src}", f"{src}: Debian has released a fix that isn't installed", "problem" if bad else "warn",
                     (f"In {', '.join(bins)}. " if bins != [src] else "") +
                     f"{len(fixed)} fixed: " + ", ".join(c + (" (remotely exploitable)" if n["remote"] else "") +
                                                         (f" ({n['urgency']} urgency)" if n["urgency"] else "") for c, n in fixed[:8])
                     + ("…" if len(fixed) > 8 else "") + "." + age,
                     "Install the security updates: the Security page, or apt-get upgrade while online.", "",
                     source="debsecan", about={"kind": "package", "key": src}))
    unfixed = sorted(p for p, v in installed.items() if not any(n["fixed"] for _, n in v))
    out.append(F("debsecan-unfixed", "Known vulnerabilities with no fix released yet", "ok",
                 (f"{len(unfixed)} installed packages have CVEs Debian hasn't fixed yet (listed, not counted): " + _list(unfixed, 20) + "."
                  if unfixed else "None.") + age, "", "", source="debsecan"))
    per_kit = {}
    for pkg, vulns in cached.items():
        if any(n["fixed"] for _, n in vulns):
            for kid in kit_of.get(pkg, []):
                per_kit.setdefault(kid, set()).add(pkg)
    for kid, pkgs in sorted(per_kit.items()):
        out.append(F(f"debsecan-kit-{kid}", f"The {kid} kit's cache has packages with fixes released since", "warn",
                     f"Cached versions of {_list(sorted(pkgs))} have fixes Debian has released.{age}",
                     "Refresh the kit while the box has internet (Library → Toolkits); installing it now would bring those versions.",
                     "", source="debsecan", about={"kind": "kit", "key": kid}))
    return out


def step_debsecan(ctx):
    suite = _codename()
    feed = DEBSECAN_FEED / suite if suite else None
    if not feed or not feed.is_file():
        return [F("debsecan-data", "Debian's security tracker data", "warn",
                  "Not on the box yet: the librarian fetches it daily while the security kit is kept current and the box is online.",
                  "Library → Toolkits: keep the Security kit current, and let the box reach the internet once.", "", source="debsecan")]
    import tempfile
    import shutil as _sh
    work = Path(tempfile.mkdtemp(prefix="debsecan-"))
    try:
        cmd, env = _debsecan_command(work)
        if cmd is None:
            return [F("debsecan-tool", "debsecan", "warn", f"Could not run it: {env}.",
                      "Library → Toolkits: refresh the Security kit while online (it is not installed for this; its cache is enough).",
                      "", source="debsecan")]
        installed = _debsecan(cmd, env, suite)
        kit_of = {}
        for m in sorted((KITS_ROOT / "manifests").glob("*.json")) if (KITS_ROOT / "manifests").is_dir() else []:
            if m.name.endswith(".previous.json"):
                continue
            for p in (json.loads(_read(m) or "{}") or {}).get("packages", []):
                kit_of.setdefault(p["name"], []).append(m.stem)
        status = KITS_ROOT / "kits-status"
        cached = _debsecan(cmd, env, suite, status) if status.is_file() else {}
        return debsecan_findings(installed, cached, kit_of, feed.stat().st_mtime, _sources())
    finally:
        _sh.rmtree(work, ignore_errors=True)


def step_deep_cis(ctx):
    return _deep_findings("debian-cis")


def step_deep_lynis(ctx):
    return _deep_findings("lynis")


def _deep_findings(source):
    """The deep audit's kept findings for one source, dated; a warning when it is old."""
    from irate_box.root import deepaudit
    rep = deepaudit.load()
    got = (rep or {}).get("sources", {}).get(source)
    if not got:
        return [F(f"{source}-none", f"{source}: not run yet", "ok",
                  "It runs in the deep audit: weekly, or Deep audit on this page (about 8 minutes on a small board).", "", "", source=source)]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(got["at"]))
    out = [dict(f) for f in got["findings"]]
    if time.time() - got["at"] > 14 * 86400:
        out.append(F(f"{source}-old", f"{source}: the last deep audit is old", "warn", f"From {when}.", "Run the deep audit again.", "", source=source))
    for f in out:
        f["detail"] = f"{f['detail']} (deep audit of {when})"
    return out


def step_security_page(ctx):
    """The Security page's own scan (security.py, control/security.json), as a source of its own,
    so the joint report can say where it and the doctor (or nmap) agree."""
    try:
        scan = json.loads(_read(CONTROL / "security.json") or "")
    except ValueError:
        scan = None
    if not scan:
        return [F("security-page-none", "The Security page's scan", "ok", "Not run yet: it runs when the Security page is opened.", "", "",
                  source="security-page")]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(scan.get("at") or 0))
    out = []
    for f in scan.get("findings", []):
        out.append(F(f"page-{f['id']}", f["title"], f["status"], f"{f['detail']} (scan of {when})", f.get("fix", ""), "",
                     source="security-page", about=secdoctor_xref.about("security-page", f["id"])))
    return out


IMPORTS = STATE / "security-imports"


def step_imports(ctx):
    """OpenVAS and nmap reports imported on the page (secimports.py), kept until replaced: OpenVAS's
    results by port, a problem from CVSS 7, a warning from 4; nmap's open ports, a warning for one
    the box was not listening on when the report came in; and either one old, or from before the
    box's ports changed, a warning of its own."""
    out = []
    try:
        now_ports = sorted({f"{l['proto']}/{l['port']}" for l in json.loads(_read(CONTROL / "security.json") or "{}").get("listeners", [])})
    except (ValueError, KeyError, TypeError):
        now_ports = None
    for kind in ("openvas", "nmap"):
        try:
            rep = json.loads(_read(IMPORTS / f"{kind}.json", limit=4 << 20) or "")
        except ValueError:
            rep = None
        if not rep:
            continue
        when = time.strftime("%Y-%m-%d", time.localtime(rep.get("ran") or rep.get("imported") or 0))
        by_port = {}
        for r in rep.get("results", []):
            if kind == "openvas" and r.get("cvss", 0) <= 0:
                continue
            by_port.setdefault((r["proto"], r["port"]), []).append(r)
        for (proto, port), rs in sorted(by_port.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            about = {"kind": "service", "key": f"{proto}/{port}"} if isinstance(port, int) else None
            where = f"{proto.upper()} {port}" if isinstance(port, int) else "the box as a whole"
            if kind == "openvas":
                worst = max(r["cvss"] for r in rs)
                status = "problem" if worst >= 7 else "warn" if worst >= 4 else "ok"
                names = "; ".join(f"{r['name']} (CVSS {r['cvss']:g})" for r in sorted(rs, key=lambda r: -r["cvss"])[:5])
                fix = next((r["solution"] for r in sorted(rs, key=lambda r: -r["cvss"]) if r.get("solution")), "")
                out.append(F(f"openvas-{proto}-{port}", f"OpenVAS on {where}: {len(rs)} result{'s' if len(rs) != 1 else ''}", status,
                             f"{names}. (Scan of {when}.)", fix, "", source="openvas", about=about))
            else:
                seen = f"{proto}/{port}"
                listening = rep.get("box_ports")
                status = "warn" if listening is not None and seen not in listening else "ok"
                svc = next((r["service"] for r in rs if r.get("service")), "")
                out.append(F(f"nmap-{proto}-{port}", f"nmap found {where} open" + (f" ({svc})" if svc else ""), status,
                             ("The box was not listening there when the report came in: worth a look." if status == "warn" else "Open, as the box serves it.")
                             + f" (Scan of {when}.)", "", "", source="nmap", about=about))
        age = (time.time() - (rep.get("ran") or rep.get("imported") or 0)) / 86400
        changed = now_ports is not None and rep.get("box_ports") is not None and sorted(rep["box_ports"]) != now_ports
        if changed:
            gained = sorted(set(now_ports) - set(rep["box_ports"]))
            out.append(F(f"{kind}-ports-changed", f"The {kind} report is from before the box's ports changed", "warn",
                         f"Since it was imported the box also listens on {', '.join(gained) or 'nothing new'}; "
                         f"no longer on {', '.join(sorted(set(rep['box_ports']) - set(now_ports))) or 'nothing'}.",
                         f"Run {kind} against the box again and import it.", "", source=kind))
        elif age > 90:
            out.append(F(f"{kind}-old", f"The {kind} report is {int(age)} days old", "warn", f"From {when}.",
                         f"Run {kind} against the box again and import it.", "", source=kind))
    if not out:
        out.append(F("imports-none", "Imported scans", "ok",
                     "None yet. Run OpenVAS (Greenbone) or nmap from a PC on the hotspot against the box, and import its report here: "
                     "the outside view, including software debsecan can't see.", "", "", source="openvas"))
    return out


KIND_SOURCES = {"service": ("security-page", "nmap", "openvas")}


def freshness():
    """Each source's date and its data's date (§5.6)."""
    out = {"doctor": time.time()}
    try:
        out["security-page"] = json.loads(_read(CONTROL / "security.json") or "{}").get("at")
    except ValueError:
        pass
    suite = _codename()
    feed = DEBSECAN_FEED / suite if suite else None
    if feed and feed.is_file():
        out["debsecan"] = feed.stat().st_mtime
    try:
        deep = json.loads(_read(CONTROL / "security-deep.json", limit=8 << 20) or "{}")
        for src, v in (deep.get("sources") or {}).items():
            out[src] = v.get("at")
    except ValueError:
        pass
    for kind in ("openvas", "nmap"):
        try:
            rep = json.loads(_read(IMPORTS / f"{kind}.json", limit=4 << 20) or "{}")
            if rep:
                out[kind] = rep.get("ran") or rep.get("imported")
        except ValueError:
            pass
    return {k: v for k, v in out.items() if v}


def joint(steps, freshness=None):
    """The joint report (security-doctor-plan §5): findings merged by what they are about, each
    item saying which sources agree, the worst status and its fix; items only one source saw where
    others could have (worth a look, or a false positive); each source's counts, what it covers,
    and how fresh it is; and the counts after merging, which the page's badge shows, so four tools
    saying the same thing count once."""
    rank = {"ok": 0, "warn": 1, "problem": 2}
    items, sources, loose = {}, {}, {"problem": 0, "warn": 0}
    for f in (f for s in steps for f in s["findings"]):
        src = f.get("source", "doctor")
        c = sources.setdefault(src, {"problem": 0, "warn": 0, "ok": 0})
        c[f["status"]] = c.get(f["status"], 0) + 1
        a = f.get("about")
        if f.get("accepted"):
            continue
        if not a:
            if f["status"] in loose:
                loose[f["status"]] += 1
            continue
        key = f"{a['kind']}:{a['key']}"
        label = secdoctor_xref.title(a["key"]) if a["kind"] == "setting" else \
            " ".join(x.upper() if i == 0 else x for i, x in enumerate(a["key"].split("/"))) if a["kind"] == "service" else a["key"]
        it = items.setdefault(key, {"about": a, "title": label,
                                    "sources": [], "status": "ok", "titles": [], "fix": ""})
        if src not in it["sources"]:
            it["sources"].append(src)
        it["titles"].append(f"{src}: {f['title']}")
        if rank[f["status"]] > rank[it["status"]] or (f["fix"] and not it["fix"] and f["status"] == it["status"]):
            it["status"], it["fix"] = max((it["status"], f["status"]), key=rank.get), f["fix"] or it["fix"]
    # A source ran when it said something real, not only "not run yet" / "none imported".
    ran = {f.get("source", "doctor") for s in steps for f in s["findings"] if not f["id"].endswith("-none")}
    for it in items.values():
        # Who could have said the same: any port-seeing source for a port; for a setting, the
        # sources the cross-reference table lists for it; a package or a kit, debsecan alone.
        a = it["about"]
        could = set(KIND_SOURCES["service"]) if a["kind"] == "service" else \
            {k for k, v in secdoctor_xref.XREF.get(a["key"], {}).items() if isinstance(v, list)} if a["kind"] == "setting" else set()
        could &= ran
        it["alone"] = len(it["sources"]) == 1 and len(could - set(it["sources"])) > 0
        it["could_see"] = sorted(could - set(it["sources"]))
    merged = sorted((i for i in items.values() if i["status"] != "ok"), key=lambda i: (-rank[i["status"]], i["about"]["kind"], i["about"]["key"]))
    agreed = [i for i in items.values() if i["status"] == "ok" and len(i["sources"]) > 1]
    after = {"problem": loose["problem"] + sum(1 for i in merged if i["status"] == "problem"),
             "warn": loose["warn"] + sum(1 for i in merged if i["status"] == "warn")}
    return {"items": merged, "agreed_ok": len(agreed), "sources": sources, "after": after,
            "coverage": {s: secdoctor_xref.COVERAGE.get(s, "") for s in sources}, "freshness": freshness or {}}

def _local_headers(url):
    """A local request's status and headers (the box's own front), or None. Certificates unchecked:
    this asks what the front sends, not whether it is trusted. A GET whose body is never read: the
    hub answers HEAD with 501 (found on the Lyra, 2026-10-07), which is not "not answering"."""
    import ssl
    import urllib.error
    import urllib.request
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=5, context=ctx) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {})
    except (OSError, ValueError):
        return None


def step_tls(ctx):
    """HTTPS (next-work plan step 15, certificates-plan stage 6): the certificate's expiry, that it
    covers the box's addresses, the CA's name constraints, the keys private, and, while HTTPS is
    on, no HSTS and port 80 still answering (the captive portal needs it)."""
    from irate_box.root import tls
    about = {"kind": "setting", "key": "tls"}
    st = tls.status()
    if not st.get("set_up"):
        return [F("tls", "HTTPS", "warn", "No certificate: every page is plain HTTP, so the admin password crosses the WiFi in clear.",
                  "/admin → Security → HTTPS: Make the box's certificate, then install it from /certificate on your own devices.", "S2", about=about)]
    out = []
    c = st.get("cert") or {}
    days = (c.get("expires", 0) - time.time()) / 86400
    out.append(F("tls-expiry", "The HTTPS certificate's expiry", "problem" if days < 7 else "warn" if days < 30 else "ok",
                 f"{'Expired' if days < 0 else f'{days:.0f} days left'}" + ("" if c.get("own") else "; the box renews its own at two-thirds of its life") + ".",
                 "" if days >= 30 else ("Bring a renewed one (/admin → Security → HTTPS)." if c.get("own") else "Renew it: /admin → Security → HTTPS, or ./irate-box tls renew as root."),
                 "", about=about))
    if st.get("outside"):
        out.append(F("tls-covers", "What the certificate covers", "problem",
                     f"The box is at {', '.join(st['outside'])}, which its CA may not vouch for: HTTPS there is refused.",
                     "Make a new CA for this network (/admin → Security → HTTPS), and install it again on each device.", "", about=about))
    else:
        out.append(F("tls-covers", "What the certificate covers", "warn" if st.get("due") else "ok",
                     f"{', '.join(c.get('names', []) + c.get('addresses', []))}" + (f"; due to be re-made: {st['due']}" if st.get("due") else "."),
                     "The uplink watchdog renews it within six hours, or: ./irate-box tls renew." if st.get("due") else "", "", about=about))
    text = ""
    try:
        text = tls.openssl("x509", "-in", str(tls.CA_CERT), "-noout", "-text")
    except (RuntimeError, OSError):
        pass
    out.append(F("tls-constraints", "The CA can vouch only for the box", "ok" if "X509v3 Name Constraints: critical" in text else "problem",
                 "Its name constraints are in place: installed, it is trusted for nothing else." if "X509v3 Name Constraints: critical" in text
                 else "The CA has no name constraints: a device that installs it would trust it for any site.",
                 "" if "X509v3 Name Constraints: critical" in text else "Make a new CA (/admin → Security → HTTPS).", "", about=about))
    private = st.get("keys_private") and tls.KEY.exists() and (tls.KEY.stat().st_mode & 0o007) == 0
    out.append(F("tls-keys", "The certificate keys", "ok" if private else "problem",
                 "The CA's key root's only, the server key not readable by everyone." if private else "A key is readable by more than it should be.",
                 "" if private else "chmod 600 /etc/hub/tls/ca/ca.key; chmod 640 /etc/hub/tls/server.key", "", about=about))
    if st.get("on"):
        got = _local_headers(f"https://127.0.0.1:{(st.get('ports') or {}).get('main', 443)}/")
        if got is None:
            out.append(F("tls-hsts", "HTTPS at the front", "warn", "HTTPS is on, but the box's HTTPS port did not answer.",
                         "nginx -t; systemctl reload nginx", "", about=about))
        else:
            hsts = any(k.lower() == "strict-transport-security" for k in got[1])
            out.append(F("tls-hsts", "No HSTS", "problem" if hsts else "ok",
                         "The front sends Strict-Transport-Security: a phone that saw it could never reach the plain pages again, the captive portal's among them."
                         if hsts else "The front sends no Strict-Transport-Security, so plain HTTP keeps working.",
                         "Remove the Strict-Transport-Security header from the web server's config." if hsts else "", "", about=about))
        plain = _local_headers("http://127.0.0.1/")
        out.append(F("tls-plain", "Plain HTTP still answers", "ok" if plain and plain[0] < 500 else "problem",
                     "Port 80 answers, as the captive portal's sign-in sheet needs." if plain and plain[0] < 500 else "Port 80 does not answer: phones on the hotspot get no sign-in sheet.",
                     "" if plain and plain[0] < 500 else "systemctl status nginx", "", about=about))
    return out


MOSQUITTO = Path(os.environ.get("HUB_MOSQUITTO_DIR", "/etc/mosquitto"))


def step_mqtt(ctx):
    """The MQTT broker and the decoder bridge (next-work plan step 18's rules): never bridged to a
    broker outside the box (mqtt.meshtastic.org above all: the mesh's traffic would leave the box,
    and its downlink would come in); anonymous clients limited to msh/#; the bridge's channel keys
    private to the hub."""
    about = {"kind": "service", "key": "mosquitto"}
    confs = sorted(MOSQUITTO.glob("conf.d/*.conf")) + ([MOSQUITTO / "mosquitto.conf"] if (MOSQUITTO / "mosquitto.conf").exists() else [])
    if not confs:
        return [F("mqtt", "MQTT broker", "ok", "Not installed.", about=about)]
    out = []
    lines = [(c.name, l.strip()) for c in confs for l in (_read(c) or "").splitlines() if l.strip() and not l.strip().startswith("#")]
    bridges = [f"{name}: {l}" for name, l in lines if re.match(r"(connection|address)\s", l)]
    out.append(F("mqtt-bridge", "The broker is not bridged anywhere", "problem" if bridges else "ok",
                 ("Bridged out of the box: " + "; ".join(bridges[:3]) + ". The mesh's traffic leaves the box, and what the other broker sends comes in to the radios.")
                 if bridges else "No connection to another broker: the mesh's traffic stays on the box.",
                 "Remove the bridge (connection/address lines) from /etc/mosquitto." if bridges else "", "", about=about))
    acl = next((l.split(None, 1)[1] for _, l in lines if l.startswith("acl_file ")), None)
    anon = any(l == "allow_anonymous true" for _, l in lines)
    acl_text = _read(Path(acl)) if acl else None
    limited = bool(acl_text) and "topic readwrite msh/#" in acl_text and not re.search(r"^\s*topic\s+(readwrite|write|read)\s+#\s*$", acl_text, re.M)
    out.append(F("mqtt-acl", "Anonymous clients limited to msh/#", "ok" if (not anon or limited) else "warn",
                 "Anyone may publish and read only under msh/#." if anon and limited else "No anonymous access." if not anon
                 else "Anonymous clients are not limited to msh/#: the broker is a free message bus for anything on the network.",
                 "" if (not anon or limited) else "Reinstall the MQTT add-on (install.sh --with-mqtt) to restore its acl_file.", "S9", about=about))
    keys = STATE / "mesh" / "channels.json"
    if keys.exists():
        private = (keys.stat().st_mode & 0o077) == 0
        out.append(F("mqtt-keys", "The decoder's channel keys", "ok" if private else "problem",
                     "Private to the hub." if private else "Readable by others than the hub: a private channel's key would be.",
                     "" if private else f"chmod 600 {keys}", "", about=about))
    return out


STEPS = [
    ("notes", "Notes add-on", "F1", step_notes),
    ("front", "The web server in front", "F2 F15 F24 F27", step_front),
    ("admin-gate", "/admin from inside the box", "F27 F31 S3", step_admin_gate),
    ("web-addons", "Web add-ons", "", step_web_addons),
    ("origins", "Which origin each app is on", "S4", step_origins),
    ("folders", "Links and root-written files in hub-owned folders", "F3 F4 F5 F13", step_folders),
    ("code", "Installed code and allow-lists", "F3 F6 F20", step_code),
    ("units", "Unit sandboxing and the build unit", "F8 F19", step_units),
    ("addons", "Add-ons and app services", "S1 S9 S12 F6", step_addons),
    ("cmdlines", "Secrets on command lines, /proc", "F9", step_cmdlines),
    ("secrets", "Secrets at rest", "S13 F30", step_secrets),
    ("git", "Git servers and pushed content", "F17 F18", step_git),
    ("accounts", "Sudo rules and accounts", "", step_accounts),
    ("kernel", "Kernel protections", "F3 F9", step_kernel),
    ("debsecan", "Debian's packages against Debian's security tracker (debsecan)", "", step_debsecan),
    ("debian-cis", "The CIS benchmark (debian-cis, in the deep audit)", "", step_deep_cis),
    ("lynis", "Lynis (in the deep audit)", "", step_deep_lynis),
    ("security-page", "The Security page's scan", "", step_security_page),
    ("imports", "Imported scans (OpenVAS, nmap)", "", step_imports),
    ("tls", "HTTPS: the certificate and the front", "S2", step_tls),
    ("mqtt", "The MQTT broker and the mesh decoder", "S9", step_mqtt),
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
        # The F2 fix declares itself (DRAINS_REQUEST_BODIES = True in server.py). Only the marker
        # counts: hub versions before it had _discard_body, but drained only refused requests.
        drains = bool(re.search(r"(?m)^DRAINS_REQUEST_BODIES\s*=\s*True\b", server))
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
    for f in (f for s in steps for f in s["findings"]):
        if not f.get("about") and f.get("source", "doctor") in ("doctor", "debian-cis", "lynis"):
            f["about"] = secdoctor_xref.about(f.get("source", "doctor"), f["id"].removeprefix("cis-").removeprefix("lynis-"))
    flat = [f for s in steps for f in s["findings"]]
    version = (_read(CODE / "VERSION") or "unknown").strip()
    return {"at": time.time(), "version": version, "root": os.geteuid() == 0, "steps": steps, "joint": joint(steps, freshness()),
            "counts": {k: sum(1 for f in flat if f["status"] == k) for k in ("problem", "warn", "ok")},
            "not_covered": NOT_COVERED}


def write_report(report, path=REPORT):
    """The one thing the doctor writes: its report, root's, 0644 (safeio: a fresh file renamed
    over the old one, so a link planted at `path` is replaced, never written through)."""
    from irate_box.root import safeio
    safeio.write(path, json.dumps(report, indent=2))


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
