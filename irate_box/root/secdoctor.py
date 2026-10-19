#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor: a passive audit of the box the hub runs on.

security.py looks at what a guest can reach (listeners, SSH, security updates) and offers
fixes. This looks at how the system is set up underneath, including what the hub inherited
from the owner's earlier setup, and reports it. It changes NOTHING: it reads files, runs
`systemctl show`/`list-units`, and walks /proc; it never starts, stops, writes or chowns
anything on the system. Its one write is its own report (control/security-audit.json).

Each step covers findings of the hub's security review (the "ref" on every line, F1 to F31, or
S1 to S14 for the known ones) that the state of the box can show. Findings the box cannot show (a race inside one
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

from irate_box.finding import current, finding
from irate_box.msg import M, named, when
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

OUR_UNIT_PATTERNS = (
    "irate-box*",
    "silverbullet.service",
    "kiwix.service",
    "ttyd.service",
    "mosquitto.service",
    "excalidraw-room.service",
    "nginx.service",
)  # and the declared services' units
MAX_LISTED = 8  # names shown in a line before "…"
MAX_ENTRIES = 4000  # directory entries looked at per folder, so a hub-made flood cannot stall the audit


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
        r = subprocess.run(  # noqa: TID251
            ("systemctl", "--no-pager", *args),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _show(unit, *props):
    out = _sc("show", unit, *(f"-p{p}" for p in props))
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _some(names, limit=MAX_LISTED):
    """The first names of a list, as evidence: data, with "…" when there are more."""
    names = [str(n) for n in names]
    return names[:limit] + (["…"] if len(names) > limit else [])


# Every finding is in the shape of finding.py, with what the joint report needs besides: ref (the
# security review's finding), source (which source said it: "doctor" when left out), about ({kind:
# package | service | setting | file | kit, key}, so the joint report can merge what several sources
# say about one thing), and accepted (the reason, when the box's design accepts it).
AS_ROOT = "sudo /opt/irate-box/irate-box secdoctor"


def _cannot(fid, title, reason, ref="", root=False):
    """A check that could not read what it needs: a warning, never ok. reason: a message. root: the
    reason is permission, so running the doctor as root is the fix."""
    return finding(
        fid,
        title,
        "warn",
        M(
            "check.cannot",
            "The doctor could not do this check. {reason}",
            reason=reason,
        ),
        fix=M("check.cannot.root", "Run the doctor as root.") if root else None,
        commands=[AS_ROOT] if root else [],
        ref=ref,
    )


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
    return int(m.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[m.group(2).lower()]


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


def _tls_includes(text):
    from irate_box.root import tls

    pattern = r"(?m)^\s*(?:include|import)\s+(?:\S*/)(nginx-(?:(?:http|policy)-[a-z]+\.inc|[a-z]+\.conf\*)|caddy-(?:(?:http|policy)-[a-z]+\.inc|site-\*\.caddy));?\s*$"
    return re.sub(
        pattern,
        lambda match: "\n".join(_read(path) or "" for path in sorted(tls.FRONT.glob(match[1]))),
        text,
    )


def load_front():
    """(kind, parsed config, source note) for the web server in front: ('nginx'|'caddy', ...),
    or (None, None, why). The one that is running wins when both configs are on disk."""
    caddy_dir = Path(os.environ.get("HUB_CADDY_DIR", "/etc/caddy"))
    caddy_up = _sc("is-active", "caddy.service").strip() == "active"
    nginx_up = _sc("is-active", "nginx.service").strip() == "active"
    nginx_text = _read(NGINX_CONF)
    if nginx_text:
        nginx_text = _tls_includes(nginx_text)
    caddy_texts = [(n, _read(caddy_dir / n)) for n in CADDY_FILES]
    caddy_texts = [(n, _tls_includes(t)) for n, t in caddy_texts if t]
    caddy_ours = [t for _, t in caddy_texts if re.search(r"(?m)^\s*handle\w*\s+/admin", t)]
    options = []
    if nginx_text is not None:
        options.append(("nginx", nginx_up, lambda: (parse_nginx(nginx_text), str(NGINX_CONF))))
    if caddy_ours:
        options.append(
            (
                "caddy",
                caddy_up,
                lambda: (
                    parse_caddy("\n".join(t for _, t in caddy_texts)),
                    ", ".join(str(caddy_dir / n) for n, _ in caddy_texts),
                ),
            )
        )
    options.sort(key=lambda o: not o[1])  # the running one first
    if options:
        kind, _, load = options[0]
        parsed, note = load()
        return kind, parsed, note
    return (
        None,
        None,
        f"neither {NGINX_CONF} nor a Caddyfile with /admin in {caddy_dir} is readable",
    )


def _caddy_routes(parsed, *prefixes):
    """{route header: [lines inside it, nested too]} for the site's top-level handle/route
    blocks whose path starts with one of the prefixes. Redirect-only blocks are left out.
    """
    groups = {}
    for stack, line, opens in parsed:
        full = stack + ((line,) if opens else ())
        if len(full) >= 2 and re.match(r"(handle|handle_path|route)\b", full[1]):
            groups.setdefault(full[1], []).append(line)
    out = {}
    for head, lines in groups.items():
        path = next((w for w in head.split()[1:] if w.startswith("/")), "")
        if path and any(path.startswith(p) for p in prefixes) and not any(line.startswith("redir ") for line in lines):
            out[head] = lines
    return out


CADDY_AUTH = ("basic_auth", "basicauth", "forward_auth")


def _caddy_gated(parsed, *prefixes):
    routes = _caddy_routes(parsed, *prefixes)
    return bool(routes) and all(any(line.split()[0] in CADDY_AUTH for line in lines) for lines in routes.values())


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
        for ds in locs.values()
    )


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
        if (
            len(stack) >= 2
            and stack[0] == "server"
            and stack[1].startswith("location")
            and re.search(r"notes/\\\.\(shell\|proxy\|runtime\)", stack[1])
            and d.startswith("return 403")
        ):
            return True
    return False


def step_notes(ctx):
    """F1: SilverBullet's shell backend and its login. F31: its HTTP proxy, which reaches the
    hub's loopback API (no login there: the front asks for it) unless SilverBullet has no IP
    networking or the front refuses /notes/.proxy."""
    unit = "silverbullet.service"
    sh = _show(
        unit,
        "LoadState",
        "ActiveState",
        "UnitFileState",
        "User",
        "DynamicUser",
        "Environment",
        "EnvironmentFiles",
        "ExecStart",
        "RestrictAddressFamilies",
        "IPAddressDeny",
        "ProtectSystem",
        "ReadWritePaths",
    )
    shell_t = M("notes.shell.title", "Notes shell endpoint")
    if not sh:
        return [
            _cannot(
                "notes-shell",
                shell_t,
                M("check.cannot.systemctl", "systemctl does not answer."),
                "F1",
            )
        ]
    if sh.get("LoadState") != "loaded":
        return [
            finding(
                "notes-shell",
                shell_t,
                "ok",
                M(
                    "notes.not-installed",
                    "The notes add-on (SilverBullet) is not installed.",
                ),
                ref="F1",
            )
        ]
    merged = _sb_env(sh)
    running = sh.get("ActiveState") == "active"
    # SilverBullet 2.11 (server/src/shell.rs): the shell runs when SB_SHELL_BACKEND is unset,
    # empty or "local"; any other value turns it off. Read-only mode turns it off too.
    shell_on = merged.get("SB_SHELL_BACKEND", "").strip().lower() in ("", "local")
    read_only = bool(merged.get("SB_READ_ONLY", "").strip())
    sb_login = bool(merged.get("SB_USER"))
    front_login = bool(ctx["front"] and front_gated(ctx, "/notes"))
    login = sb_login or front_login
    idle = (
        []
        if running
        else [
            M(
                "notes.idle",
                "The notes add-on is installed but not running now. It starts at boot if it is enabled.",
            )
        ]
    )
    front_blocks = _front_refuses_notes_capabilities(ctx)
    stop = ["sudo systemctl stop silverbullet"]
    out = []
    if not shell_on or read_only:
        out.append(
            finding(
                "notes-shell",
                shell_t,
                "ok",
                (
                    M(
                        "notes.shell.read-only",
                        "The shell is off, because the notes are in read-only mode.",
                    )
                    if read_only
                    else M("notes.shell.off", "The shell is off.")
                ),
                notes=(
                    [
                        M(
                            "notes.shell.refused-too",
                            "The web server also refuses /notes/.shell.",
                        )
                    ]
                    if front_blocks
                    else []
                )
                + idle,
                evidence=([] if read_only else [f"SB_SHELL_BACKEND={merged['SB_SHELL_BACKEND']}"]),
                ref="F1",
            )
        )
    elif front_blocks:
        out.append(
            finding(
                "notes-shell",
                shell_t,
                "warn",
                M(
                    "notes.shell.refused",
                    "The shell is on, but the web server refuses /notes/.shell. "
                    "A request through the web server cannot reach it.",
                ),
                M(
                    "notes.shell.refused.why",
                    "The web server's rule is the only guard. A change to the web server's "
                    "configuration makes the shell reachable again.",
                ),
                M(
                    "notes.shell.fix",
                    "Run the installer again. It turns the shell off on the unit's command line.",
                ),
                notes=idle,
                evidence=["SB_SHELL_BACKEND"],
                ref="F1",
            )
        )
    elif not login:
        out.append(
            finding(
                "notes-shell",
                shell_t,
                "problem",
                M("notes.shell.open", "The shell is on, and /notes/ has no sign-in."),
                M(
                    "notes.shell.open.why",
                    "Anyone who can reach the box can run commands as the hub user. The hub user can "
                    "send requests to the root helper, a password change included.",
                ),
                M(
                    "notes.shell.open.fix",
                    "Stop the notes add-on now. Then run the installer again. It turns the shell off, "
                    "and the web server then refuses /notes/.shell.",
                ),
                commands=stop,
                notes=idle,
                ref="F1",
            )
        )
    else:
        out.append(
            finding(
                "notes-shell",
                shell_t,
                "warn",
                M("notes.shell.signed-in", "The shell is on, behind a sign-in."),
                M(
                    "notes.shell.signed-in.why",
                    "Anyone who can sign in to the notes can run commands as the hub user.",
                ),
                M(
                    "notes.shell.fix",
                    "Run the installer again. It turns the shell off on the unit's command line.",
                ),
                notes=idle,
                ref="F1",
            )
        )
    # F31: the proxy. Two layers, either of which stops it: no IP networking for the unit, and
    # the front refusing /notes/.proxy. Read-only mode turns the proxy off as well.
    proxy_t = M("notes.proxy.title", "Notes proxy endpoint")
    families = sh.get("RestrictAddressFamilies", "")
    no_ip = (families and not families.startswith("~") and "AF_INET" not in families) or "0.0.0.0/0" in sh.get(
        "IPAddressDeny", ""
    )
    if read_only:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "ok",
                M(
                    "notes.proxy.read-only",
                    "The proxy is off, because the notes are in read-only mode.",
                ),
                ref="F31",
            )
        )
    elif no_ip and front_blocks:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "ok",
                M(
                    "notes.proxy.both",
                    "SilverBullet has no IP networking, and the web server refuses /notes/.proxy.",
                ),
                ref="F31",
            )
        )
    elif no_ip:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "warn",
                M(
                    "notes.proxy.passed",
                    "SilverBullet has no IP networking, but the web server passes /notes/.proxy through.",
                ),
                M(
                    "notes.proxy.passed.why",
                    "One of the two guards is missing. If SilverBullet gets IP networking again, "
                    "its proxy reaches the hub's loopback API.",
                ),
                M("notes.proxy.fix", "Run the installer again. It sets both guards."),
                ref="F31",
            )
        )
    elif front_blocks:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "warn",
                M(
                    "notes.proxy.networked",
                    "The web server refuses /notes/.proxy, but SilverBullet can still reach the loopback address.",
                ),
                M(
                    "notes.proxy.networked.why",
                    "A program on the box that can talk to SilverBullet can use its proxy to reach "
                    "the hub's loopback API.",
                ),
                M("notes.proxy.fix", "Run the installer again. It sets both guards."),
                ref="F31",
            )
        )
    elif not login:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "problem",
                M(
                    "notes.proxy.open",
                    "The proxy at /notes/.proxy forwards requests to the hub's loopback port, and /notes/ "
                    "has no sign-in.",
                ),
                M(
                    "notes.proxy.open.why",
                    "The hub's loopback port has no sign-in of its own. Any guest can read and change "
                    "the administration settings and the administrator password.",
                ),
                M(
                    "notes.proxy.open.fix",
                    "Stop the notes add-on now. Then run the installer again. It puts SilverBullet on "
                    "a socket with no IP networking, and the web server then refuses /notes/.proxy.",
                ),
                commands=stop,
                ref="F31",
            )
        )
    else:
        out.append(
            finding(
                "notes-proxy",
                proxy_t,
                "warn",
                M(
                    "notes.proxy.signed-in",
                    "The proxy reaches the hub's loopback API. Only people who can sign in to the notes can use it.",
                ),
                M(
                    "notes.proxy.signed-in.why",
                    "Through the proxy, they get past the administrator sign-in.",
                ),
                M("notes.proxy.fix", "Run the installer again. It sets both guards."),
                ref="F31",
            )
        )
    if not login and not read_only:
        out.append(
            finding(
                "notes-login",
                M("notes.login.title", "Notes sign-in"),
                "warn",
                M(
                    "notes.login.none",
                    "Anyone who can open the hub can read and change the notes.",
                ),
                M(
                    "notes.login.why",
                    "Notes can carry scripts (Space Lua, widgets). They run in the browser of each reader, "
                    "on the address of the notes, apart from the administration pages.",
                ),
                M(
                    "notes.login.fix",
                    "On Apps, select who can open Notes. If the notes are for everyone, leave them open.",
                ),
                ref="F1/S4",
            )
        )
    confined = (
        sh.get("ProtectSystem") == "strict"
        and all(p.startswith(str(STATE / "notes")) for p in sh.get("ReadWritePaths", "").split())
        and sh.get("ReadWritePaths")
    )
    if sh.get("DynamicUser") != "yes" and sh.get("User") in (HUB_USER, "", "root") and not confined:
        who = sh.get("User") or "root"
        out.append(
            finding(
                "notes-user",
                M("notes.user.title", "Notes add-on account"),
                "warn",
                M(
                    "notes.user.shared",
                    "silverbullet.service runs as {user}. That account can write the root helper's request queue.",
                    user=who,
                ),
                M(
                    "notes.user.why",
                    "A flaw in the notes add-on then gives control of the administration pages.",
                ),
                M(
                    "notes.user.fix",
                    "Run the installer again. It limits the unit to the notes folder (ProtectSystem=strict).",
                ),
                evidence=[unit, f"User={who}"],
                ref="F1",
            )
        )
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


def _raw_response(sock):
    """The status code of the next HTTP response on `sock`, its body read by Content-Length."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    m = re.search(rb"Content-Length:\s*(\d+)", head, re.I)
    want = int(m.group(1)) if m else 0
    while len(rest) < want:
        chunk = sock.recv(65536)
        if not chunk:
            break
        rest += chunk
    try:
        return int(head.split(b" ", 2)[1])
    except (IndexError, ValueError):
        return None


def probe_drains():
    """F2, for real: on one kept-alive connection to the hub, a refused request whose body is
    itself a request for /admin, then an ordinary one. A hub that reads the body away answers
    the ordinary one (200 for /status); one that leaves it answers the smuggled one first (403
    for /admin without the front's word). None when the hub does not answer."""
    import socket

    smuggled = b"GET /admin/settings HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    first = (
        b"PUT /nothing-here HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n"
        + b"Content-Length: %d\r\n\r\n" % len(smuggled)
        + smuggled
    )
    try:
        with socket.create_connection(("127.0.0.1", _hub_port()), timeout=5) as s:
            s.sendall(first)
            if _raw_response(s) is None:
                return None
            s.sendall(b"GET /status HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            code = _raw_response(s)
    except OSError:
        return None
    return None if code is None else code == 200


def probe_json_cap():
    """F15, for real: a JSON body over 256 KB to a guest route must be refused (413) and the
    connection stay usable. None when the hub does not answer."""
    import http.client

    big = b'{"text": "' + b"x" * (300 * 1024) + b'"}'
    try:
        conn = http.client.HTTPConnection("127.0.0.1", _hub_port(), timeout=10)
        conn.request(
            "POST",
            "/messages",
            body=big,
            headers={"Host": "127.0.0.1", "Content-Type": "application/json"},
        )
        r = conn.getresponse()
        r.read()
        refused = r.status == 413
        conn.request("GET", "/status", headers={"Host": "127.0.0.1"})
        r2 = conn.getresponse()
        r2.read()
        conn.close()
    except OSError:
        return None
    return refused and r2.status == 200


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
    gate_t = M("admin-gate.title", "Administrator authentication gate")
    code = _hub_ask("GET", "/admin/settings", {"Host": "127.0.0.1"})
    if code is None:
        return [
            _cannot(
                "admin-loopback",
                gate_t,
                M("check.cannot.hub", "The hub does not answer on its port."),
                "F27",
            )
        ]
    asked = f"GET /admin/settings → 127.0.0.1:{_hub_port()}: {code}"
    if code == 200:
        out.append(
            finding(
                "admin-loopback",
                gate_t,
                "problem",
                M(
                    "admin-gate.open",
                    "The hub answered an /admin request sent straight to its port, with no sign-in and "
                    "without the web server's secret.",
                ),
                M(
                    "admin-gate.open.why",
                    "Any program on the box that can send a request to the loopback address has "
                    "administrator rights: a build, a proxy, or another account.",
                ),
                M(
                    "admin-gate.open.fix",
                    "Run the installer again. It makes the web server's secret, and the hub then "
                    "refuses /admin without it.",
                ),
                evidence=[asked, str(ETC / "front-secret.env")],
                ref="F27",
            )
        )
    else:
        out.append(
            finding(
                "admin-loopback",
                gate_t,
                "ok",
                M(
                    "admin-gate.closed",
                    "The hub refuses an /admin request sent straight to its port without the web server's secret.",
                ),
                evidence=[asked],
                ref="F27",
            )
        )
    if secret:
        path = ETC / "front-secret.env"
        st = path.stat()
        if st.st_uid != 0 or st.st_mode & 0o077:
            out.append(
                finding(
                    "admin-secret-file",
                    M("admin-secret.title", "Web server secret permissions"),
                    "problem",
                    M(
                        "admin-secret.open",
                        "Accounts other than root can read the web server's secret.",
                    ),
                    M(
                        "admin-secret.why",
                        "With the secret, a program on the box can send /admin requests that the hub "
                        "takes as coming through the web server.",
                    ),
                    M(
                        "admin-secret.fix",
                        "Make the file belong to root, readable by root only.",
                    ),
                    commands=[f"sudo chown root:root {path}", f"sudo chmod 600 {path}"],
                    evidence=[
                        str(path),
                        f"mode {oct(st.st_mode & 0o777)}",
                        f"uid {st.st_uid}",
                    ],
                    ref="F27",
                )
            )
        code = _hub_ask(
            "POST",
            "/admin/settings",
            {
                "Host": "127.0.0.1",
                "X-Irate-Front": secret,
                "Content-Type": "application/json",
                "Content-Length": "2",
            },
            b"{}",
        )
        csrf_t = M("admin-csrf.title", "Administrator change requests")
        if code == 200:
            out.append(
                finding(
                    "admin-csrf",
                    csrf_t,
                    "problem",
                    M(
                        "admin-csrf.open",
                        "The hub accepted a change to /admin without the header that the administration "
                        "pages send (X-Irate-Admin).",
                    ),
                    M(
                        "admin-csrf.why",
                        "A page elsewhere that an administrator opens can change settings with the "
                        "administrator's saved sign-in.",
                    ),
                    M("admin-csrf.fix", "Update the hub."),
                    evidence=[f"POST /admin/settings: {code}"],
                    ref="S3",
                )
            )
        elif code is not None:
            out.append(
                finding(
                    "admin-csrf",
                    csrf_t,
                    "ok",
                    M(
                        "admin-csrf.closed",
                        "The hub refuses a change to /admin without the header that the administration "
                        "pages send (X-Irate-Admin).",
                    ),
                    evidence=[f"POST /admin/settings: {code}"],
                    ref="S3",
                )
            )
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
    """The local add-ons: their folder holds only plain files and
    folders (the hub writes it; nginx follows no links there, Caddy would), each one switched on
    was agreed to, and the add-on origin serves nothing of the hub's and sends each its CSP.
    """
    from irate_box.hub import access as acc
    from irate_box.hub import manifests as man

    out = []
    root = STATE / "addons"
    files_t = M("addons.files.title", "Web add-on files")
    if not root.is_dir():
        return [
            finding(
                "addons",
                M("addons.title", "Web add-ons"),
                "ok",
                M("addons.none", "This box has no web add-ons."),
            )
        ]
    odd = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink() or not (p.is_dir() or p.is_file()):
                odd.append(str(p))
    if odd:
        out.append(
            finding(
                "addons-files",
                files_t,
                "problem",
                M(
                    "addons.files.odd",
                    {
                        "one": "The web add-on folder holds {n} link or special file.",
                        "other": "The web add-on folder holds {n} links or special files.",
                    },
                    n=len(odd),
                ),
                M(
                    "addons.files.why",
                    "The hub writes this folder. Under Caddy, the web server follows a link out of it.",
                ),
                M(
                    "addons.files.fix",
                    "On Add-ons, remove the add-on and add it again. If the links come back, find out "
                    "what on the box makes them.",
                ),
                evidence=_some(odd),
            )
        )
    else:
        out.append(
            finding(
                "addons-files",
                files_t,
                "ok",
                M(
                    "addons.files.plain",
                    "The web add-on folder holds only plain files and folders.",
                ),
                evidence=[str(root)],
            )
        )
    local, errors = man.load_local()
    for name, why in errors.items():
        out.append(
            finding(
                f"addons-bad-{name}",
                named(name, M("addons.role.manifest", "web add-on manifest")),
                "warn",
                M(
                    "addons.bad",
                    "The hub left this add-on out, because its manifest is not valid.",
                ),
                fix=M(
                    "addons.bad.fix",
                    "On Add-ons, remove the add-on. Or delete its file in /var/lib/hub/apps.d.",
                ),
                evidence=[why],
            )
        )
    state = acc.read(ETC / "access.json")
    try:
        consents = json.loads(_read(STATE / "addons-consent.json") or "{}")
    except ValueError:
        consents = {}
    on = [m for m in local if acc.mode_of(state, m["id"]) != "off"]
    for m in on:
        if m["id"] not in consents:
            out.append(
                finding(
                    f"addons-consent-{m['id']}",
                    named(m["addon"]["title"], M("addons.role.consent", "consent")),
                    "warn",
                    M(
                        "addons.consent.none",
                        "The add-on is on, but the hub has no record that an administrator agreed to it.",
                    ),
                    M(
                        "addons.consent.why",
                        "Someone can have added it by hand, past the administration pages.",
                    ),
                    M(
                        "addons.consent.fix",
                        "On Add-ons, remove the add-on and add it again.",
                    ),
                )
            )
    try:
        cat_state = json.loads(_read(STATE / "addons-catalogue.json") or "{}")
    except ValueError:
        cat_state = {}
    for m in local:
        c = cat_state.get(m["id"]) or {}
        if c.get("status") == "held":
            out.append(
                finding(
                    f"addons-held-{m['id']}",
                    named(m["addon"]["title"], M("addons.role.update", "update approval")),
                    "warn",
                    M(
                        "addons.held",
                        "A newer version in the catalogue changes what you agreed to. The version you agreed "
                        "to keeps running.",
                    ),
                    fix=M(
                        "addons.held.fix",
                        "On Add-ons, Web add-ons, read the changes. Then accept the new version, or remove the add-on.",
                    ),
                    evidence=c.get("held", []),
                )
            )
    origin_t = M("addons.origin.title", "Add-on origin")
    code, _ = _addon_ask("/admin/settings")
    if code is None:
        out.append(
            finding(
                "addons-origin",
                origin_t,
                "warn" if local else "ok",
                (
                    M("addons.origin.down", "Nothing answers on the add-on port.")
                    if not local
                    else M(
                        "addons.origin.down-used",
                        "Nothing answers on the add-on port. The added add-ons cannot open.",
                    )
                ),
                fix=(
                    M(
                        "addons.origin.fix",
                        "Run the installer again. It sets up the add-on server.",
                    )
                    if local
                    else None
                ),
            )
        )
    elif code == 200:
        out.append(
            finding(
                "addons-origin",
                origin_t,
                "problem",
                M("addons.origin.hub", "The add-on port serves the hub's pages."),
                M(
                    "addons.origin.hub.why",
                    "An add-on's page could then read the hub's pages, the administration pages included.",
                ),
                M(
                    "addons.origin.hub.fix",
                    "Run the installer again. It restores the add-on server's configuration.",
                ),
                evidence=[f"/admin/settings: {code}"],
            )
        )
    else:
        out.append(
            finding(
                "addons-origin",
                origin_t,
                "ok",
                M(
                    "addons.origin.ok",
                    "The add-on port serves none of the hub's pages.",
                ),
                evidence=[f"/admin/settings: {code}"],
            )
        )
    for m in on:
        code, headers = _addon_ask(f"/{m['id']}/")
        if code not in (None, 404) and "content-security-policy" not in headers:
            out.append(
                finding(
                    f"addons-csp-{m['id']}",
                    named(
                        m["addon"]["title"],
                        M("addons.role.csp", "content security policy"),
                    ),
                    "problem",
                    M(
                        "addons.csp.none",
                        "The add-on's pages come with no Content-Security-Policy header.",
                    ),
                    M(
                        "addons.csp.why",
                        "Without one, its pages can load scripts from, and send data to, more places than "
                        "the box intends.",
                    ),
                    M(
                        "addons.csp.fix",
                        "Run the installer again. It restores the add-on server's configuration.",
                    ),
                )
            )
    return out


FRONT_T = M("front.title", "Web server in front")
BODY_T = M("front.keepalive.title", "Request-body handling")
SIZE_T = M("front.size.title", "Request size limit")
XFF_T = M("front.xff.title", "Forwarded client address")
FIX_HUB = M("check.fix.update-hub", "Update the hub.")
SMUGGLE_WHY = M(
    "front.keepalive.smuggle.why",
    "The unread bytes become the next request on that connection. The web server never checked "
    "that request against the /admin sign-in, so a guest with no sign-in can have any /admin "
    "action done.",
)
SIZE_FIX = M(
    "front.size.fix",
    "Keep a small limit for the whole site. Raise it only on the routes that take uploads: the store, "
    "saves, the file drop and Git.",
)
XFF_WHY = M(
    "front.xff.why",
    "The hub takes the first address in the header as the guest's. A guest can then select the address that "
    "the hub sees.",
)


def step_front(ctx):
    """F2, F15, F24, F27 and S3's first line of defence: how the front is set up."""
    directives, note = ctx["front"], ctx["front_note"]
    if directives is None:
        return [
            finding(
                "front",
                FRONT_T,
                "warn",
                M(
                    "front.not-audited",
                    "The doctor could not read the web server's configuration.",
                ),
                evidence=[str(note)],
                ref="F2/F15/F24",
            )
        ]
    if ctx["front_kind"] == "caddy":
        return step_front_caddy(ctx)
    out = []
    # F2: request smuggling needs a kept-alive upstream and a hub that leaves bodies unread.
    ka = [d for s, d in directives if s and s[0].startswith("upstream irate_box_hub") and d.startswith("keepalive")]
    drains = ctx["hub_drains_bodies"]
    if ka and drains is False:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "problem",
                M(
                    "front.keepalive.smuggle",
                    "nginx keeps its connections to the hub "
                    "open, and this hub version answers some requests without reading their body.",
                ),
                SMUGGLE_WHY,
                FIX_HUB,
                notes=[
                    M(
                        "front.keepalive.stopgap",
                        "Until the hub is updated, remove keepalive from the "
                        "hub's upstream block in the nginx configuration.",
                    )
                ],
                evidence=[ka[0]],
                ref="F2",
            )
        )
    elif ka:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "ok" if drains else "warn",
                (
                    M(
                        "front.keepalive.drained",
                        "nginx keeps its connections to the hub open, and the hub reads every request body.",
                    )
                    if drains
                    else M(
                        "front.keepalive.unknown",
                        "nginx keeps its connections to the hub open. The doctor could "
                        "not read the hub's code to find out if it reads every request body.",
                    )
                ),
                evidence=[ka[0]],
                ref="F2",
            )
        )
    else:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "ok",
                M(
                    "front.keepalive.off",
                    "nginx opens one connection to the hub per request. A request body that the hub "
                    "leaves unread cannot become a second request.",
                ),
                ref="F2",
            )
        )
    # F15: body size.
    sizes = [(s, d) for s, d in directives if d.startswith("client_max_body_size")]
    server_level = [d.split()[-1] for s, d in sizes if s == ("server",)]
    if not server_level:
        out.append(
            finding(
                "front-body",
                SIZE_T,
                "ok",
                M(
                    "front.size.default",
                    "nginx's default limit of 1 MB applies to the whole site.",
                ),
                ref="F15",
            )
        )
    else:
        n = nginx_size(server_level[0])
        bigger = [f"{s[-1].split()[-1]} {d.split()[-1]}" for s, d in sizes if s != ("server",)]
        if n == 0:
            out.append(
                finding(
                    "front-body",
                    SIZE_T,
                    "warn",
                    M(
                        "front.size.none",
                        "The web server has no limit on the size of a request.",
                    ),
                    M(
                        "front.size.none.why.nginx",
                        "nginx stores each request body on the card before the hub gets it, and the "
                        "hub reads it into memory. A few large requests from one guest can fill "
                        "the card or the memory.",
                    ),
                    SIZE_FIX,
                    notes=(
                        [
                            M(
                                "front.size.routes",
                                "Some routes set a limit of their own.",
                            )
                        ]
                        if bigger
                        else []
                    ),
                    evidence=["client_max_body_size 0"] + _some(bigger),
                    ref="F15",
                )
            )
        elif n is None or n > 64 * 1024**2:
            out.append(
                finding(
                    "front-body",
                    SIZE_T,
                    "warn",
                    M(
                        "front.size.large",
                        "The limit on the size of a request is large for the whole site.",
                    ),
                    M(
                        "front.size.large.why",
                        "A few large requests from one guest can use up the memory of the box.",
                    ),
                    SIZE_FIX,
                    evidence=[f"client_max_body_size {server_level[0]}"],
                    ref="F15",
                )
            )
        else:
            out.append(
                finding(
                    "front-body",
                    SIZE_T,
                    "ok",
                    M(
                        "front.size.set",
                        "The site has a limit on the size of a request.",
                    ),
                    evidence=[f"client_max_body_size {server_level[0]}"],
                    ref="F15",
                )
            )
    out += _hub_body(ctx)
    # F24: the first X-Forwarded-For entry is the client's to choose.
    xff = [d for s, d in directives if d.startswith("proxy_set_header X-Forwarded-For")]
    if any("$proxy_add_x_forwarded_for" in d for d in xff):
        out.append(
            finding(
                "front-xff",
                XFF_T,
                "warn",
                M(
                    "front.xff.appended",
                    "nginx adds the connecting address after the guest's own "
                    "X-Forwarded-For header, and does not replace it.",
                ),
                XFF_WHY,
                M(
                    "front.xff.fix.nginx",
                    "Set the header to the connecting address only.",
                ),
                commands=["proxy_set_header X-Forwarded-For $remote_addr;"],
                evidence=_some(xff),
                ref="F24",
            )
        )
    elif xff:
        out.append(
            finding(
                "front-xff",
                XFF_T,
                "ok",
                M(
                    "front.xff.replaced",
                    "The web server sets the header to the connecting address only.",
                ),
                evidence=_some(xff),
                ref="F24",
            )
        )
    out += _front_gates(
        ctx,
        M(
            "front.admin.none.nginx",
            "The /admin routes have no auth_basic or auth_request in the nginx configuration.",
        ),
    )
    # A baseline CSP on the hub's own origin, where /admin lives.
    csp = [d for s, d in directives if d.startswith("add_header Content-Security-Policy") and s and s[0] == "server"]
    csp_t = M("front.csp.title", "Content security policy of the hub")
    if csp:
        out.append(
            finding(
                "front-csp",
                csp_t,
                "ok",
                M(
                    "front.csp.set",
                    "The hub's address has a Content-Security-Policy for the whole site.",
                ),
                notes=[
                    M(
                        "front.csp.does",
                        "It blocks plugins and changes of the base address, and only this address can frame the pages.",
                    )
                ],
                ref="S4",
            )
        )
    else:
        out.append(
            finding(
                "front-csp",
                csp_t,
                "warn",
                M(
                    "front.csp.none",
                    "The hub's address has no Content-Security-Policy.",
                ),
                M(
                    "front.csp.none.why",
                    "A script that gets into a page, through a flaw in a bundled editor for example, "
                    "can then use every feature of the browser.",
                ),
                M(
                    "front.csp.fix",
                    "Add this line to the server block of the nginx configuration.",
                ),
                commands=[
                    "add_header Content-Security-Policy \"object-src 'none'; base-uri 'none'; frame-ancestors 'self'\" always;"
                ],
                ref="S4",
            )
        )
    return out


def step_front_caddy(ctx):
    """The same four questions as step_front, for Caddy (install.sh --web caddy)."""
    parsed = ctx["front"]
    drains = ctx["hub_drains_bodies"]
    out = []
    # F2: Go's reverse proxy forwards bodies and reuses upstream connections unless told not to.
    ka_off = any(not opened and re.match(r"keepalive\s+off\b", line) for _, line, opened in parsed)
    if ka_off:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "ok",
                M(
                    "front.keepalive.off.caddy",
                    "Caddy's keepalive to the hub is off. A request body that the hub leaves unread "
                    "cannot become a second request.",
                ),
                ref="F2",
            )
        )
    elif drains is False:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "problem",
                M(
                    "front.keepalive.smuggle.caddy",
                    "Caddy keeps its connections to the hub open, and this hub version answers "
                    "some requests without reading their body.",
                ),
                SMUGGLE_WHY,
                FIX_HUB,
                notes=[
                    M(
                        "front.keepalive.caddy-untested",
                        "This was shown on nginx. It is likely on Caddy, but not yet shown.",
                    ),
                    M(
                        "front.keepalive.stopgap.caddy",
                        "Until the hub is updated, turn keepalive off in each reverse_proxy to the hub.",
                    ),
                ],
                commands=["transport http { keepalive off }"],
                ref="F2",
            )
        )
    else:
        out.append(
            finding(
                "front-keepalive",
                BODY_T,
                "ok" if drains else "warn",
                (
                    M(
                        "front.keepalive.drained.caddy",
                        "Caddy keeps its connections to the hub open, and the hub reads every request body.",
                    )
                    if drains
                    else M(
                        "front.keepalive.unknown.caddy",
                        "Caddy keeps its connections to the hub open. The doctor could not read "
                        "the hub's code to find out if it reads every request body.",
                    )
                ),
                ref="F2",
            )
        )
    # F15: Caddy has no default body cap at all.
    site = [
        (st, line)
        for st, line, o in parsed
        if not o and len(st) == 2 and st[1] == "request_body" and line.startswith("max_size")
    ]
    # a cap placed in the site's own request_body, not inside a handle
    top = [line.split()[-1] for st, line in site]
    per = [
        f"{h.split()[1]} {next((x.split()[-1] for x in ls if x.startswith('max_size')), '?')}"
        for h, ls in ((h, ls) for h, ls in _caddy_routes(parsed, "/").items())
        if any(x.startswith("max_size") for x in ls)
    ]
    if top:
        out.append(
            finding(
                "front-body",
                SIZE_T,
                "ok",
                M("front.size.set", "The site has a limit on the size of a request."),
                evidence=[f"max_size {top[0]}"],
                ref="F15",
            )
        )
    else:
        out.append(
            finding(
                "front-body",
                SIZE_T,
                "warn",
                M(
                    "front.size.none",
                    "The web server has no limit on the size of a request.",
                ),
                M(
                    "front.size.none.why.caddy",
                    "Caddy has no limit by default. The hub reads each request body into memory, so "
                    "a few large requests from one guest can use up the memory of the box.",
                ),
                SIZE_FIX,
                notes=([M("front.size.routes", "Some routes set a limit of their own.")] if per else []),
                commands=["request_body { max_size 1MB }"],
                evidence=_some(per),
                ref="F15",
            )
        )
    out += _hub_body(ctx)
    # F24: Caddy (2.6+) replaces an untrusted client's X-Forwarded-For unless trusted_proxies says otherwise.
    trusted = [line for _, line, opened in parsed if not opened and line.startswith("trusted_proxies")]
    header_up = [
        line for _, line, opened in parsed if not opened and re.match(r"header_up\s+X-Forwarded-For", line, re.I)
    ]
    if trusted or any("{http.request.header.X-Forwarded-For}" in line for line in header_up):
        out.append(
            finding(
                "front-xff",
                XFF_T,
                "warn",
                M(
                    "front.xff.trusted",
                    "Caddy passes on the guest's own X-Forwarded-For header.",
                ),
                XFF_WHY,
                M(
                    "front.xff.fix.caddy",
                    "Remove trusted_proxies, or set the header to the connecting address.",
                ),
                commands=["header_up X-Forwarded-For {remote_host}"],
                evidence=(trusted + header_up)[:1],
                ref="F24",
            )
        )
    else:
        out.append(
            finding(
                "front-xff",
                XFF_T,
                "ok",
                M(
                    "front.xff.caddy",
                    "Caddy has no trusted_proxies. Caddy 2.6 and later replace the "
                    "guest's header with the connecting address.",
                ),
                notes=[
                    M(
                        "front.xff.caddy-old",
                        "An older Caddy adds the address after the guest's header. Check the version.",
                    )
                ],
                commands=["caddy version"],
                ref="F24",
            )
        )
    out += _front_gates(
        ctx,
        M(
            "front.admin.none.caddy",
            "The /admin routes have no basic_auth in the Caddyfile.",
        ),
    )
    # Caddy's own admin API (localhost:2019) can rewrite the whole front; builds can reach loopback (F8).
    if not any(not opened and st == ("",) and line.startswith("admin off") for st, line, opened in parsed):
        out.append(
            finding(
                "front-caddy-admin",
                M("front.caddy-admin.title", "Caddy administration API"),
                "warn",
                M(
                    "front.caddy-admin.on",
                    "Caddy's administration API listens on 127.0.0.1:2019.",
                ),
                M(
                    "front.caddy-admin.why",
                    "A program on the box that can connect to it, a build for example, can replace the "
                    "whole Caddy configuration, the /admin sign-in included.",
                ),
                M(
                    "front.caddy-admin.fix",
                    "Add this line to the global options block of the Caddyfile.",
                ),
                commands=["admin off"],
                ref="F8",
            )
        )
    return out


def _hub_body(ctx):
    """F15 in the hub itself: a JSON body read with no cap (probe_json_cap, else its code)."""
    if ctx["hub_caps_json"] is not False:
        return []
    return [
        finding(
            "hub-body",
            M("hub-body.title", "Request body limit in the hub"),
            "warn",
            M("hub-body.none", "The hub reads a request body of any size into memory."),
            M(
                "front.size.large.why",
                "A few large requests from one guest can use up the memory of the box.",
            ),
            FIX_HUB,
            ref="F15",
        )
    ]


def _front_gates(ctx, none):
    """S3/F27 and S1: the sign-ins the front asks for on /admin, /term and /git-private. none: the
    words for a missing /admin sign-in in this front's terms."""
    out = []
    admin_t = M("front.admin.title", "Administrator route authentication")
    if not front_gated(ctx, "/admin"):
        out.append(
            finding(
                "front-admin",
                admin_t,
                "problem",
                none,
                M(
                    "front.admin.none.why",
                    "Unless the hub asks for a sign-in itself, anyone who can reach the box can open the "
                    "administration pages.",
                ),
                M(
                    "front.admin.fix",
                    "Run the installer again. It restores the web server's configuration.",
                ),
                ref="F27",
            )
        )
    else:
        out.append(
            finding(
                "front-admin",
                admin_t,
                "ok",
                M("front.admin.set", "The web server asks for a sign-in on /admin."),
                notes=[
                    M(
                        "front.admin.next",
                        'The step "Administrator authentication gate" checks that the hub refuses '
                        "/admin requests that do not come through the web server.",
                    )
                ],
                ref="F27",
            )
        )
    for pfx, title in (
        ("/term", M("front.term.title", "Web terminal authentication")),
        ("/git-private", M("front.git-private.title", "Private Git authentication")),
    ):
        if front_has(ctx, pfx) and not front_gated(ctx, pfx):
            out.append(
                finding(
                    f"front-{pfx.strip('/')}",
                    title,
                    "problem",
                    M(
                        "front.route.open",
                        "The web server serves {route} with no sign-in.",
                        route=pfx,
                    ),
                    M(
                        "front.route.open.why",
                        "Anyone who can reach the box can use it.",
                    ),
                    M(
                        "front.route.fix",
                        "Run the installer again. It puts the route behind the administrator sign-in.",
                    ),
                    ref="S1",
                )
            )
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
    folders_t = M("folders.title", "State-directory permissions")
    if os.geteuid() != 0:
        return [
            _cannot(
                "folders",
                folders_t,
                M(
                    "check.cannot.not-root",
                    "It needs root to read the folders of other accounts.",
                ),
                "F3",
                root=True,
            )
        ]
    out = []
    ctrl = _lstat(CONTROL)
    if ctrl is None:
        return [
            finding(
                "folders",
                folders_t,
                "warn",
                M(
                    "folders.no-control",
                    "The root helper's folder is not there. Is the hub installed?",
                ),
                evidence=[str(CONTROL)],
                ref="F3",
            )
        ]
    # Root's output folders: who owns them decides whether F3/F13 are open.
    owner = _name_of(ctrl.st_uid)
    results = _lstat(CONTROL / "results")
    hub_owned = [p for p, st in (("control/", ctrl), ("control/results/", results)) if st and st.st_uid not in (0,)]
    root_in_hub = []
    try:
        with os.scandir(CONTROL) as it:
            for e in it:
                st = e.stat(follow_symlinks=False)
                if st.st_uid == 0 and stat.S_ISREG(st.st_mode):
                    root_in_hub.append(e.name)
    except OSError:
        pass
    if hub_owned:
        out.append(
            finding(
                "folders-control",
                folders_t,
                "problem",
                M(
                    "folders.control.hub",
                    "Root writes, replaces and changes the ownership of files in folders that belong to "
                    "the account {account}.",
                    account=owner,
                ),
                M(
                    "folders.control.why",
                    "Python's write, open and chown follow links. Code that runs as the hub can then make "
                    "root overwrite or hand over any file, root's own code included. The uplink "
                    "watchdog, the network inventory and the clock timers do this with no request.",
                ),
                M(
                    "folders.control.fix",
                    "Run the installer again. It makes control/ belong to root, with only requests/ "
                    "writable by the hub.",
                ),
                evidence=hub_owned + _some(root_in_hub),
                ref="F3/F13",
            )
        )
    else:
        out.append(
            finding(
                "folders-control",
                folders_t,
                "ok",
                M(
                    "folders.control.root",
                    "control/ and control/results/ belong to root.",
                ),
                ref="F3/F13",
            )
        )
    # Links planted in the folders root works in.
    watch = [
        CONTROL,
        CONTROL / "results",
        STATE / "zim",
        STATE / "library",
        STATE / "firmware",
        STATE / "git",
        STATE / "ci",
        STATE / "ci" / "queue",
        STATE / "ci" / "runs",
        STATE / "ci" / "work",
        STATE / "ci" / "home",
        STATE / "notes",
        STATE / "kits",
    ]
    links, truncated = [], False
    for w in watch:
        depth = 1 if w in (STATE / "notes", STATE / "git", STATE / "zim") else 2
        found, cut = _walk_links(w, depth)
        links += found
        truncated = truncated or cut
    # A relative link to its own folder or below (a git checkout's .dockerignore → .gitignore, in
    # ci/home) can't send root anywhere else: counted, not listed.
    inside = [(p, t) for p, t in links if not t.startswith("/") and ".." not in Path(t).parts]
    links = [x for x in links if x not in inside]
    # Folders root recreates itself at every install are the interesting ones.
    risky = [
        p
        for p, _ in links
        if Path(p).parent.name
        in (
            "control",
            "results",
            "zim",
            "library",
            "firmware",
            "ci",
            "queue",
            "runs",
            "work",
            "home",
            "kits",
        )
        or Path(p).name == "quarantine"
    ]
    links_t = M("folders.links.title", "Symbolic links")
    if links:
        out.append(
            finding(
                "folders-links",
                links_t,
                "problem" if risky else "warn",
                M(
                    "folders.links.out",
                    {
                        "one": "{n} link in the hub's folders leads out of its folder.",
                        "other": "{n} links in the hub's folders lead out of their folders.",
                    },
                    n=len(links),
                ),
                M(
                    "folders.links.why",
                    "Root's installer, helper and watchdogs change the ownership, the mode and the contents of "
                    "paths here. A link to /etc, /root or /opt/irate-box makes them change the target.",
                ),
                M(
                    "folders.links.fix",
                    "Look at each link. Remove each one that you did not make, and find out how it got there.",
                ),
                notes=[
                    M(
                        "folders.links.where",
                        "Notes and the library can hold links on purpose. control/, zim/ and ci/ never should.",
                    )
                ]
                + (
                    [
                        M(
                            "folders.links.more",
                            "The doctor stopped counting at its limit, so there can be more.",
                        )
                    ]
                    if truncated
                    else []
                ),
                commands=["ls -l <link>"],
                evidence=_some(f"{Path(p).relative_to(STATE)} → {t}" for p, t in links),
                ref="F3/F4/F5",
            )
        )
    else:
        out.append(
            finding(
                "folders-links",
                links_t,
                "ok",
                M(
                    "folders.links.none",
                    "No link in the hub's folders leads out of its folder.",
                ),
                notes=(
                    [
                        M(
                            "folders.links.inside",
                            {
                                "one": "{n} link stays in its own folder, as in a Git checkout.",
                                "other": "{n} links stay in their own folders, as in a Git checkout.",
                            },
                            n=len(inside),
                        )
                    ]
                    if inside
                    else []
                ),
                ref="F3/F4/F5",
            )
        )
    # The quarantine is the hub's own: health.py moves a book there as the hub, so a
    # link there leads only where the hub could write anyway. Still worth a look if it is one.
    q = _lstat(STATE / "zim" / "quarantine")
    if q is not None and stat.S_ISLNK(q.st_mode):
        out.append(
            finding(
                "folders-quarantine",
                M("folders.quarantine.title", "Kiwix quarantine folder"),
                "warn",
                M("folders.quarantine.link", "The Kiwix quarantine folder is a link."),
                M(
                    "folders.quarantine.why",
                    "The Box doctor moves books there as the hub, not as root, so the link reaches "
                    "only what the hub can. But nothing on the box makes it a link.",
                ),
                M(
                    "folders.quarantine.fix",
                    "Look at where it leads. If you did not make it, remove it.",
                ),
                commands=[f"ls -l {STATE / 'zim' / 'quarantine'}"],
                ref="F4",
            )
        )
    ci = _lstat(STATE / "ci")
    if ci is not None and ci.st_uid != 0:
        out.append(
            finding(
                "folders-ci",
                M("folders.ci.title", "Build isolation"),
                "warn",
                M(
                    "folders.ci.owner",
                    "The build folder belongs to the account {account}.",
                    account=_name_of(ci.st_uid),
                ),
                M(
                    "folders.ci.why",
                    "That account can make queue/, runs/, work/ or home/ into links. The next install then "
                    "changes the ownership or mode of the target, in /opt/irate-box or /root for example.",
                ),
                M(
                    "folders.ci.fix",
                    "Run the installer again. It makes ci/ belong to root, with only its subfolders "
                    "belonging to hubci.",
                ),
                evidence=[f"{STATE}/ci"],
                ref="F5",
            )
        )
    return out


def step_code(ctx):
    """F6, F20 and the installed code: owned by root, allow-lists, markers of the review's fixes."""
    out = []
    code_t = M("code.title", "Installed code ownership")
    if not CODE.is_dir():
        return [
            finding(
                "code",
                code_t,
                "warn",
                M("code.missing", "The installed code is not there."),
                evidence=[str(CODE)],
                ref="F6",
            )
        ]
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
        out.append(
            finding(
                "code-owner",
                code_t,
                "problem",
                M(
                    "code.loose",
                    {
                        "one": "{n} path in the installed code does not belong to root, or others can write it.",
                        "other": "{n} paths in the installed code do not belong to root, or others can write them.",
                    },
                    n=len(loose),
                ),
                M(
                    "code.loose.why",
                    "The root helper, the doctors and the installer run from this code. An account that can "
                    "change it can run code as root.",
                ),
                M(
                    "code.loose.fix",
                    "Make the installed code belong to root, with no write permission for others.",
                ),
                commands=[
                    f"sudo chown -R root:root {CODE}",
                    f"sudo chmod -R go-w {CODE}",
                ],
                evidence=_some(loose),
                ref="F3",
            )
        )
    else:
        out.append(
            finding(
                "code-owner",
                code_t,
                "ok",
                M(
                    "code.root",
                    "All the installed code belongs to root, and only root can write it.",
                ),
                evidence=[str(CODE)],
                ref="F3",
            )
        )
    root_dir = CODE / "irate_box" / "root"
    st = _lstat(str(root_dir))
    if st and stat.S_ISDIR(st.st_mode):
        root_t = M("code.root-dir.title", "Root-only modules")
        if st.st_mode & 0o077:
            out.append(
                finding(
                    "code-root-dir",
                    root_t,
                    "warn",
                    M(
                        "code.root-dir.open",
                        "Accounts other than root can read the root helper and the doctors.",
                    ),
                    M(
                        "code.root-dir.why",
                        "They cannot run them without root, but nothing they run needs them.",
                    ),
                    M(
                        "code.root-dir.fix",
                        "Make the folder readable by root only. The installer does this.",
                    ),
                    commands=[f"sudo chmod 700 {root_dir}"],
                    evidence=[f"{root_dir} {stat.S_IMODE(st.st_mode):o}"],
                    ref="F3",
                )
            )
        else:
            out.append(
                finding(
                    "code-root-dir",
                    root_t,
                    "ok",
                    M(
                        "code.root-dir.closed",
                        "Only root can read the root helper and the doctors.",
                    ),
                    ref="F3",
                )
            )
    health = ctx["src"].get("health.py") or ""
    m = re.search(r"OUR_UNIT\s*=\s*re\.compile\((.*?)\)\s*$", health, re.S | re.M)
    wide = bool(m and re.search(r"syncthing@\[", m.group(1)))
    instances = [
        u.split()[0] for u in _sc("list-units", "syncthing@*", "--all", "--no-legend").splitlines() if u.split()
    ]
    foreign = [u for u in instances if u != f"syncthing@{HUB_USER}.service"]
    allow_t = M("code.allow.title", "Box doctor restart list")
    if foreign:
        out.append(
            finding(
                "code-syncthing-running",
                M("code.syncthing.title", "Syncthing for other accounts"),
                "problem",
                M(
                    "code.syncthing.other",
                    "A Syncthing unit runs for an account that is not the hub's.",
                ),
                M(
                    "code.syncthing.why",
                    "syncthing@root runs Syncthing as root, with a control page that the hub can use to "
                    "share root's files.",
                ),
                M("code.syncthing.fix", "Disable each of these units now and at boot."),
                commands=[f"sudo systemctl disable --now {u}" for u in foreign],
                evidence=foreign,
                ref="F6",
            )
        )
    if wide:
        out.append(
            finding(
                "code-syncthing",
                allow_t,
                "problem",
                M(
                    "code.allow.wide",
                    "The restart list of the Box doctor accepts a Syncthing unit for any account.",
                ),
                M(
                    "code.allow.wide.why",
                    "A forged request can then start syncthing@root.service.",
                ),
                M(
                    "code.allow.wide.fix",
                    "Update the hub. Its restart list takes only the units that the installer made.",
                ),
                evidence=["health.py OUR_UNIT: syncthing@[a-z_]…"],
                ref="F6",
            )
        )
    elif health:
        out.append(
            finding(
                "code-syncthing",
                allow_t,
                "ok",
                M(
                    "code.allow.narrow",
                    "The restart list of the Box doctor accepts Syncthing for the hub's account only.",
                ),
                ref="F6",
            )
        )
    store = ctx["src"].get("store.py") or ""
    cors_t = M("code.cors.title", "Cross-origin access to the store")
    if re.search(r"Access-Control-Allow-Origin[\"'],\s*[\"']\*[\"']", store):
        out.append(
            finding(
                "code-cors",
                cors_t,
                "warn",
                M(
                    "code.cors.wide",
                    "The store, the saves and the file drop answer every website ({header}).",
                    header="Access-Control-Allow-Origin: *",
                ),
                M(
                    "code.cors.why",
                    "A website that a guest opens can read the files in the drop and the saves.",
                ),
                M(
                    "code.cors.fix",
                    "Update the hub. On the box, the apps are on the same address as the store, so they do "
                    "not need the header.",
                ),
                ref="F20",
            )
        )
    elif store:
        out.append(
            finding(
                "code-cors",
                cors_t,
                "ok",
                M("code.cors.narrow", "The store does not answer every website."),
                ref="F20",
            )
        )
    return out


# Units the audit looks at for sandboxing: the hub's own and the add-ons'.
def _our_units():
    from irate_box.hub import services

    names = set()
    for pat in (
        *OUR_UNIT_PATTERNS,
        *(s["unit"] for s in services.declared() if s["unit"]),
    ):
        for line in _sc("list-unit-files", pat, "--no-legend").splitlines():
            n = line.split()[0] if line.split() else ""
            if n.endswith(".service") and n != "nginx.service":
                names.add(n)
    return sorted(names)


# Sandbox lines a unit goes without by design, each with its reason: the audit counts them as set and says why.
ALL_LINES = (
    "ProtectSystem=strict",
    "NoNewPrivileges",
    "PrivateTmp",
    "ProtectHome",
    "ProtectProc=invisible",
)
SANDBOX_EXEMPT = {
    "irate-box-control.service": {
        "ProtectSystem=strict": M(
            "units.exempt.control-system",
            "The root helper installs updates and packages, so it writes across the system.",
        ),
        "ProtectHome": M(
            "units.exempt.control-home",
            "The root helper sets up the accounts of the box, their home folders included.",
        ),
    },
    "ttyd.service": {
        line: M(
            "units.exempt.ttyd",
            "It is the administrator's terminal: a sign-in shell that must see and reach the whole box, sudo included.",
        )
        for line in ALL_LINES
    },
    "irate-box-uplink.service": {
        "ProtectSystem=strict": M(
            "units.exempt.uplink",
            "Its repairs start the network's own tools (ifup, the DHCP client), which "
            "write /run, /var/lib and /etc/resolv.conf.",
        )
    },
}


UNIT_PROPS = (
    "LoadState",
    "User",
    "DynamicUser",
    "ProtectSystem",
    "NoNewPrivileges",
    "PrivateTmp",
    "ProtectHome",
    "ProtectProc",
    "IPAddressDeny",
    "PrivateNetwork",
    "RestrictAddressFamilies",
    "CapabilityBoundingSet",
)


def _ci_nft():
    """The rule that keeps builds off loopback (install.sh's inet irate_box_ci), as nft lists it, or None when it is not
    loaded. IPAddressDeny does not work on vendor kernels that cannot attach systemd's cgroup programs.
    """
    try:
        r = subprocess.run(  # noqa: TID251
            ("nft", "list", "table", "inet", "irate_box_ci"),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and re.search(r'skuid "?hubci"? oif "?lo"? drop', r.stdout) else None


def step_units(ctx):
    """F8, F19: build unit's reach to loopback; sandboxing of root and hub units."""
    units = ctx["units"]
    sandbox_t = M("units.title", "Service sandboxing")
    if not units:
        return [
            _cannot(
                "units",
                sandbox_t,
                M(
                    "check.cannot.units",
                    "It found no systemd units, or systemctl does not answer.",
                ),
                "F19",
            )
        ]
    out, root_weak, hub_weak, exempt = [], [], [], []
    for u, p in units.items():
        if p.get("LoadState") != "loaded":
            continue
        is_root = p.get("User", "") in ("", "root") and p.get("DynamicUser") != "yes"
        strict = p.get("ProtectSystem") in ("strict",)
        weak = [
            name
            for name, ok in (
                ("ProtectSystem=strict", strict),
                ("NoNewPrivileges", p.get("NoNewPrivileges") == "yes"),
                ("PrivateTmp", p.get("PrivateTmp") == "yes"),
                ("ProtectHome", p.get("ProtectHome") not in ("no", "", None)),
                (
                    "ProtectProc=invisible",
                    p.get("ProtectProc") in ("invisible", "noaccess"),
                ),
            )
            if not ok
        ]
        kept = SANDBOX_EXEMPT.get(u, {})
        exempt += [
            M(
                "units.exempt.one",
                "{unit} runs without {line}. {reason}",
                unit=u[:-8],
                line=name,
                reason=kept[name],
            )
            for name in weak
            if name in kept
        ]
        weak = [name for name in weak if name not in kept]
        # A root unit is reported for any line it lacks: a flaw in one is root, so each missing line
        # counts. A guest-reachable unit when most of them are gone.
        if is_root and weak:
            root_weak.append(f"{u[:-8]}: {', '.join(weak)}")
        elif not is_root and len(weak) >= 4:
            hub_weak.append(f"{u[:-8]}: {', '.join(weak)}")
        elif not is_root and p.get("CapabilityBoundingSet", "").strip() not in (
            "",
            "0",
        ):
            hub_weak.append(f"{u[:-8]}: CapabilityBoundingSet={p['CapabilityBoundingSet'][:60]}")
    lines = "ProtectSystem=strict, NoNewPrivileges, PrivateTmp, ProtectHome, ProtectProc=invisible"
    if root_weak:
        out.append(
            finding(
                "units-root",
                M("units.root.title", "Root service sandboxing"),
                "warn",
                M(
                    "units.root.weak",
                    {
                        "one": "{n} of the hub's root units is missing a sandbox line.",
                        "other": "{n} of the hub's root units are missing sandbox lines.",
                    },
                    n=len(root_weak),
                ),
                M(
                    "units.root.why",
                    "A flaw in a root unit runs as root with nothing in between. Each missing line is one "
                    "more thing that the flaw can reach.",
                ),
                notes=[
                    M(
                        "units.lines",
                        "The lines are {lines}. CapabilityBoundingSet and SystemCallFilter narrow a unit further.",
                        lines=lines,
                    )
                ]
                + exempt,
                evidence=_some(root_weak, 12),
                ref="F19",
            )
        )
    else:
        out.append(
            finding(
                "units-root",
                M("units.root.title", "Root service sandboxing"),
                "ok",
                M(
                    "units.root.ok",
                    "Each of the hub's root units has the sandbox lines it can have.",
                ),
                notes=exempt,
                ref="F19",
            )
        )
    hub_t = M("units.hub.title", "Guest-reachable service sandboxing")
    if hub_weak:
        out.append(
            finding(
                "units-hub",
                hub_t,
                "warn",
                M(
                    "units.hub.weak",
                    {
                        "one": "{n} service that guests can reach has few sandbox lines.",
                        "other": "{n} services that guests can reach have few sandbox lines.",
                    },
                    n=len(hub_weak),
                ),
                M(
                    "units.hub.why",
                    "These are the hub, the Git servers (fcgiwrap with cgit and git-http-backend) and the "
                    "add-ons. The Git servers run as the hub's account, which can write the root helper's "
                    "request queue.",
                ),
                notes=[
                    M(
                        "units.lines",
                        "The lines are {lines}. CapabilityBoundingSet and SystemCallFilter narrow a unit further.",
                        lines=lines,
                    )
                ],
                evidence=_some(hub_weak),
                ref="F19",
            )
        )
    else:
        out.append(
            finding(
                "units-hub",
                hub_t,
                "ok",
                M(
                    "units.hub.ok",
                    "Each service that guests can reach has most of the sandbox lines.",
                ),
                ref="F19",
            )
        )
    ci = units.get("irate-box-ci.service")
    if ci and ci.get("LoadState") == "loaded":
        deny = ci.get("IPAddressDeny", "").strip()
        private = ci.get("PrivateNetwork") == "yes"
        families = ci.get("RestrictAddressFamilies", "")
        nft = None if private or any(x in deny for x in ("127.", "localhost", "::1", "any")) else _ci_nft()
        blocks_loopback = private or nft is not None or any(x in deny for x in ("127.", "localhost", "::1", "any"))
        ci_t = M("units.ci.title", "Build isolation from the hub")
        deny_line = ["IPAddressDeny=localhost"]
        if blocks_loopback:
            out.append(
                finding(
                    "units-ci-loopback",
                    ci_t,
                    "ok",
                    M(
                        "units.ci.closed",
                        "Builds cannot connect to the loopback address.",
                    ),
                    evidence=[
                        (
                            "PrivateNetwork=yes"
                            if private
                            else ("nft: inet irate_box_ci (hubci to lo: DNS only)" if nft else f"IPAddressDeny={deny}")
                        )
                    ],
                    ref="F8",
                )
            )
        elif _hub_ask("GET", "/admin/settings", {"Host": "127.0.0.1"}) == 403:
            # The hub refuses /admin without the front's secret (step admin-gate), so what builds
            # reach on loopback is what any guest reaches.
            out.append(
                finding(
                    "units-ci-loopback",
                    ci_t,
                    "warn",
                    M(
                        "units.ci.guest",
                        "Builds can connect to the hub on the loopback address, but the hub refuses them /admin.",
                    ),
                    M(
                        "units.ci.guest.why",
                        "Builds reach what any guest reaches. /admin needs the web server's secret, which "
                        "builds cannot read.",
                    ),
                    M(
                        "units.ci.fix",
                        "Add this line to the build unit to keep builds off the loopback address.",
                    ),
                    commands=deny_line,
                    evidence=["irate-box-ci.service"],
                    ref="F8",
                )
            )
        else:
            out.append(
                finding(
                    "units-ci-loopback",
                    ci_t,
                    "problem",
                    M(
                        "units.ci.open",
                        "Builds can reach the hub's administration API on the loopback address.",
                    ),
                    M(
                        "units.ci.open.why",
                        "Builds run code from private repositories and what they download: submodules "
                        "and PlatformIO packages. On 127.0.0.1:8000 they can change the password, the "
                        "services and the updates.",
                    ),
                    M(
                        "units.ci.fix",
                        "Add this line to the build unit to keep builds off the loopback address.",
                    ),
                    commands=deny_line,
                    evidence=["irate-box-ci.service"]
                    + ([f"RestrictAddressFamilies={families}"] if families.strip() else []),
                    ref="F8",
                )
            )
    return out


# --- add-ons: one generic check for everything apps.d declares, plus a probe table ----------

MOSQUITTO_DIR = Path(os.environ.get("HUB_MOSQUITTO_DIR", "/etc/mosquitto"))
NGIRCD_DIR = Path(os.environ.get("HUB_NGIRCD_DIR", "/etc/ngircd"))
LOOPBACK_WORDS = ("127.0.0.1", "localhost", "::1", "lo")
WIDE_WORDS = ("0.0.0.0", "::", "*", "[::]")


def load_addons():
    """[{id, title, unit, port, path, option, consent}] for every app in apps.d that names a
    service (so an add-on added there later is audited with no change here), plus Tailscale,
    which the OS image ships and no manifest describes."""
    out = []
    folder = next(
        (d for d in (CODE / "apps.d", Path(__file__).resolve().parents[2] / "apps.d") if d.is_dir()),
        None,
    )
    for f in sorted(folder.glob("*.json")) if folder else ():
        try:
            m = json.loads(_read(f) or "")
        except ValueError:
            continue
        st, ad = m.get("status") or {}, m.get("addon") or {}
        if not st.get("unit"):
            continue
        out.append(
            {
                "id": m.get("id", f.stem),
                "title": ad.get("title") or st.get("name") or m.get("id", f.stem),
                "unit": st["unit"].replace("@hub.", f"@{HUB_USER}."),
                "port": st.get("port"),
                "path": st.get("path"),
                "option": ad.get("option"),
                "consent": ad.get("consent"),
            }
        )
    out.append(
        {
            "id": "tailscale",
            "title": "Tailscale",
            "unit": "tailscaled.service",
            "port": None,
            "path": None,
            "option": None,
            "consent": None,
            "root_ok": True,
        }
    )
    return out


def _requested_options():
    """The --with-* options the install was given, or None when there is no record."""
    text = _read(ETC / "install-options")
    if text is None:
        return None
    return {line.strip() for line in text.splitlines() if line.strip().startswith("--with-")}


def _listening():
    """{port: [addresses]} of every TCP/UDP listener, loopback included; None if ss is missing."""
    try:
        r = subprocess.run(("ss", "-H", "-ltnu"), capture_output=True, text=True, timeout=20)  # noqa: TID251
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
    """The account the add-on's process ends up as, or None for a dynamic account (DynamicUser=yes).
    Mosquitto started as root drops to its `user` setting, "mosquitto" unless the config says otherwise.
    """
    if show.get("DynamicUser") == "yes":
        return None
    user = show.get("User", "") or "root"
    if user == "root" and a["unit"].startswith("mosquitto"):
        m = re.findall(r"(?m)^\s*user\s+(\S+)", _mosquitto_conf())
        return m[-1] if m else "mosquitto"
    return user


def _issue(key, status, summary, why=None, fix=None, commands=(), evidence=(), notes=()):
    """One point an add-on's checks found: a finding of its own (step_addons gives it its id)."""
    return {
        "key": key,
        "status": status,
        "summary": summary,
        "why": why,
        "fix": fix,
        "commands": list(commands),
        "evidence": [str(e) for e in evidence],
        "notes": list(notes),
    }


UNREAD = M("addon.config.unread", "The doctor could not read its configuration.")
ANYONE_TOPICS = M(
    "mqtt.anonymous.why",
    "Anyone on the network can publish to and subscribe to these topics.",
)


def probe_mosquitto(ctx, a, show):
    """S9: the broker's own config."""
    conf = _mosquitto_conf()
    if not conf.strip():
        return [_issue("config", "warn", UNREAD, evidence=[MOSQUITTO_DIR])]
    anon = re.search(r"(?m)^\s*allow_anonymous\s+true\b", conf)
    acl = re.search(r"(?m)^\s*acl_file\s+(\S+)", conf)
    pw = re.search(r"(?m)^\s*password_file\s+\S+", conf)
    out = []
    if anon and not acl:
        out.append(
            _issue(
                "anonymous",
                "problem",
                M(
                    "mqtt.anonymous.open",
                    "Clients can connect with no sign-in, and no access list limits their topics.",
                ),
                M(
                    "mqtt.anonymous.open.why",
                    "Anyone on the network can publish to and subscribe to every topic.",
                ),
                M(
                    "mqtt.anonymous.fix",
                    "Add an access list that limits clients to msh/#. Or use a password file, and refuse "
                    "clients with no sign-in.",
                ),
                commands=["topic readwrite msh/#"],
                evidence=["allow_anonymous true"],
            )
        )
    elif anon:
        topics = re.findall(
            r"(?m)^\s*topic\s+(?:readwrite|write|read)?\s*(\S+)",
            _read(acl.group(1)) or "",
        )
        if "#" in topics:
            out.append(
                _issue(
                    "anonymous",
                    "problem",
                    M(
                        "mqtt.anonymous.all",
                        "The access list gives clients with no sign-in every topic (#).",
                    ),
                    M(
                        "mqtt.anonymous.open.why",
                        "Anyone on the network can publish to and subscribe to every topic.",
                    ),
                    M("mqtt.anonymous.limit", "Limit the access list to msh/#."),
                    commands=["topic readwrite msh/#"],
                    evidence=[acl.group(1)],
                )
            )
        else:
            out.append(
                _issue(
                    "anonymous",
                    "warn",
                    M(
                        "mqtt.anonymous.limited",
                        "Clients can connect with no sign-in. The access list limits their topics.",
                    ),
                    ANYONE_TOPICS,
                    M(
                        "mqtt.anonymous.ap",
                        "When the box runs as an access point, listen on its address only, and use a password file.",
                    ),
                    evidence=_some(topics) or [acl.group(1)],
                    notes=(
                        []
                        if topics
                        else [
                            M(
                                "mqtt.acl.unread",
                                "The doctor could not read the access list.",
                            )
                        ]
                    )
                    + (
                        [
                            M(
                                "mqtt.password-file",
                                "A password file exists. Check that the broker uses it.",
                            )
                        ]
                        if pw
                        else []
                    ),
                )
            )
    if not re.search(r"(?m)^\s*message_size_limit\s+[1-9]", conf):
        out.append(
            _issue(
                "msg-size",
                "warn",
                M(
                    "mqtt.size.none",
                    "The broker has no limit on the size of a message.",
                ),
                M("mqtt.size.why", "One client can send messages of any size."),
                M(
                    "mqtt.size.fix",
                    "Set a message size limit in the broker's configuration.",
                ),
                commands=["message_size_limit 4096"],
            )
        )
    if not re.search(r"(?m)^\s*max_connections\s+\d+", conf):
        out.append(
            _issue(
                "max-conn",
                "warn",
                M("addon.conn.none", "It has no limit on the number of connections."),
                M(
                    "addon.conn.why",
                    "One client can open connections until the box runs out.",
                ),
                M(
                    "mqtt.conn.fix",
                    "Set a connection limit for each listener in the broker's configuration.",
                ),
                commands=["max_connections 64"],
            )
        )
    return out


def probe_ngircd(ctx, a, show):
    """The IRC server's own config (the one irate-box points the unit at)."""
    conf = _read(NGIRCD_DIR / "irate-box.conf")
    if conf is None:
        return [_issue("config", "warn", UNREAD, evidence=[NGIRCD_DIR / "irate-box.conf"])]
    live = "\n".join(line for line in conf.splitlines() if not line.lstrip().startswith((";", "#")))
    out = []
    if re.search(r"(?mi)^\s*\[Operator\]", live):
        out.append(
            _issue(
                "operator",
                "warn",
                M("irc.operator", "The IRC server has an operator ([Operator])."),
                M(
                    "irc.operator.why",
                    "Whoever has the operator password can disconnect users and change any channel. The "
                    "password is in the configuration file.",
                ),
                M(
                    "irc.operator.fix",
                    "Keep the configuration file readable by root and the IRC account only.",
                ),
                evidence=[NGIRCD_DIR / "irate-box.conf"],
            )
        )
    m = re.search(r"(?mi)^\s*MaxConnections\s*=\s*(\d+)", live)
    if not m or int(m.group(1)) == 0:
        out.append(
            _issue(
                "max-conn",
                "warn",
                M("addon.conn.none", "It has no limit on the number of connections."),
                M(
                    "addon.conn.why",
                    "One client can open connections until the box runs out.",
                ),
                M(
                    "irc.conn.fix",
                    "Set a connection limit in the IRC server's configuration.",
                ),
                commands=["MaxConnections = 64"],
            )
        )
    m = re.search(r"(?mi)^\s*MaxConnectionsIP\s*=\s*(\d+)", live)
    if not m or int(m.group(1)) == 0:
        out.append(
            _issue(
                "max-conn-ip",
                "warn",
                M(
                    "irc.conn-ip.none",
                    "The IRC server has no limit on the connections from one address.",
                ),
                M("irc.conn-ip.why", "One address can take every connection."),
                M(
                    "irc.conn-ip.fix",
                    "Set a connection limit for each address in the IRC server's configuration.",
                ),
                commands=["MaxConnectionsIP = 5"],
            )
        )
    if not re.search(r"(?mi)^\s*CloakHost\s*=\s*\S", live):
        out.append(
            _issue(
                "cloak",
                "warn",
                M(
                    "irc.cloak.none",
                    "The IRC server shows the address of each client to every other user.",
                ),
                M(
                    "irc.cloak.why",
                    "Other users can see where each person connects from.",
                ),
                M(
                    "irc.cloak.fix",
                    "Hide the addresses in the IRC server's configuration.",
                ),
                commands=["CloakHost = %x.irate-box"],
            )
        )
    if not re.search(r"(?mi)^\s*DNS\s*=\s*(no|false|0)\b", live):
        out.append(
            _issue(
                "dns",
                "warn",
                M("irc.dns.on", "The IRC server looks up the name of each client."),
                M(
                    "irc.dns.why",
                    "With no internet connection, each client waits for a timeout before it can register.",
                ),
                M(
                    "irc.dns.fix",
                    "Turn off the name and Ident lookups in the IRC server's configuration.",
                ),
                commands=["DNS = no", "Ident = no"],
            )
        )
    return out


def probe_syncthing(ctx, a, show):
    """The GUI is driven through the hub: look at its config, not only its unit."""
    cfg = next(
        (
            t
            for t in (
                _read(STATE / ".local/state/syncthing/config.xml"),
                _read(STATE / ".config/syncthing/config.xml"),
            )
            if t
        ),
        None,
    )
    if cfg is None:
        return [
            _issue(
                "config",
                "warn",
                UNREAD,
                evidence=[STATE / ".local/state/syncthing/config.xml"],
            )
        ]
    out = []
    gui = re.search(r"<gui\b.*?</gui>", cfg, re.S)
    g = gui.group(0) if gui else ""
    addr = re.search(r"<address>([^<]*)</address>", g)
    if addr and not _is_loop(addr.group(1).rpartition(":")[0].strip("[]") or "*"):
        out.append(
            _issue(
                "gui-listen",
                "problem",
                M(
                    "syncthing.gui.wide",
                    "The Syncthing control page listens on addresses other than the loopback address.",
                ),
                M(
                    "syncthing.gui.wide.why",
                    "Anyone on the network can reach the control page directly.",
                ),
                M(
                    "syncthing.gui.wide.fix",
                    "Make the control page listen on the loopback address only.",
                ),
                commands=["--gui-address=127.0.0.1:8384"],
                evidence=[addr.group(1)],
            )
        )
    if re.search(r"<insecureAdminAccess>true", g):
        out.append(
            _issue(
                "gui-insecure",
                "problem",
                M(
                    "syncthing.gui.insecure",
                    "The Syncthing control page accepts requests from other addresses with no password.",
                ),
                M(
                    "syncthing.gui.insecure.why",
                    "Anyone who reaches the control page can change what Syncthing shares.",
                ),
                M("syncthing.gui.insecure.fix", "Turn insecureAdminAccess off."),
                commands=["<insecureAdminAccess>false</insecureAdminAccess>"],
            )
        )
    if not re.search(r"<password>[^<]+</password>", g):
        out.append(
            _issue(
                "gui-password",
                "warn",
                M(
                    "syncthing.gui.no-password",
                    "The Syncthing control page has no password.",
                ),
                M(
                    "syncthing.gui.no-password.why",
                    "The web server's sign-in for /sync/ is the only gate. The hub's account can "
                    "use the control page directly.",
                ),
                M(
                    "syncthing.gui.no-password.fix",
                    "Set a password for the control page.",
                ),
            )
        )
    if re.search(r"<globalAnnounceEnabled>true</globalAnnounceEnabled>", cfg):
        out.append(
            _issue(
                "discovery",
                "warn",
                M("syncthing.discovery.on", "Global discovery is on."),
                M(
                    "syncthing.discovery.why",
                    "When the box has an internet connection, it announces itself to the public "
                    "servers of Syncthing. The devices that it accepts still decide who can sync.",
                ),
                M(
                    "syncthing.discovery.fix",
                    "If the box syncs only on its own network, turn global discovery off.",
                ),
                commands=["<globalAnnounceEnabled>false</globalAnnounceEnabled>"],
            )
        )
    if re.search(r"<relaysEnabled>true</relaysEnabled>", cfg):
        out.append(
            _issue(
                "relays",
                "warn",
                M("syncthing.relays.on", "Relays are on."),
                M(
                    "syncthing.relays.why",
                    "When the box has an internet connection, its connections can go through the public "
                    "relays of Syncthing. The data stays encrypted.",
                ),
                M(
                    "syncthing.relays.fix",
                    "If the box syncs only on its own network, turn relays off.",
                ),
                commands=["<relaysEnabled>false</relaysEnabled>"],
            )
        )
    ur = re.search(r"<urAccepted>(-?\d+)</urAccepted>", cfg)
    if ur and int(ur.group(1)) > 0:
        out.append(
            _issue(
                "usage",
                "warn",
                M("syncthing.usage.on", "Usage reporting is on."),
                M(
                    "syncthing.usage.why",
                    "When the box has an internet connection, Syncthing sends usage data to its makers.",
                ),
                M("syncthing.usage.fix", "Turn usage reporting off."),
                commands=["<urAccepted>-1</urAccepted>"],
            )
        )
    return out


def probe_kiwix(ctx, a, show):
    if "--blockexternal" in _argv_of(show):
        return []
    return [
        _issue(
            "external",
            "warn",
            M("kiwix.external", "Kiwix runs without --blockexternal."),
            M(
                "kiwix.external.why",
                "Book pages can load content from other websites, which can then see what is read.",
            ),
            M(
                "kiwix.external.fix",
                "Add --blockexternal to the start command of Kiwix.",
            ),
            commands=["--blockexternal"],
        )
    ]


def _ttyd_runs(argv):
    """What ttyd runs in its terminal: the words after "--" (or the last word), looking through setsid(1)
    (a session of its own) and script(1)'s -c (a terminal of its own)."""
    cmd = argv[argv.index("--") + 1 :] if "--" in argv else argv[-1:]
    while cmd and os.path.basename(cmd[0]) == "setsid":
        cmd = [w for w in cmd[1:] if not w.startswith("-")] or cmd[1:]
    if cmd and os.path.basename(cmd[0]) == "script":
        for i, w in enumerate(cmd[1:], 1):
            if w.startswith("-") and not w.startswith("--") and "c" in w[1:] and i + 1 < len(cmd):
                return cmd[i + 1].split()[0]
            if w.startswith("--command="):
                return w.split("=", 1)[1].split()[0]
    return cmd[0] if cmd else ""


def probe_ttyd(ctx, a, show):
    argv = _argv_of(show)
    out = []
    if argv and os.path.basename(_ttyd_runs(argv)) in (
        "sh",
        "bash",
        "zsh",
        "dash",
        "ash",
    ):
        out.append(
            _issue(
                "shell",
                "problem",
                M(
                    "ttyd.shell",
                    "The web terminal starts a shell with no sign-in of its own.",
                ),
                M(
                    "ttyd.shell.why",
                    "The web server's sign-in is the only barrier to a shell as {user}.",
                    user=show.get("User") or "root",
                ),
                M(
                    "ttyd.shell.fix",
                    "Make the terminal start {program}, which asks for an account of the box.",
                    program="/bin/login",
                ),
                evidence=[_ttyd_runs(argv)],
            )
        )
    if "--interface" not in argv and "-i" not in argv:
        out.append(
            _issue(
                "listen",
                "problem",
                M("ttyd.listen.all", "ttyd listens on every interface."),
                M(
                    "ttyd.listen.all.why",
                    "Anyone on the network can reach the terminal directly, past the web server's sign-in.",
                ),
                M(
                    "ttyd.listen.fix",
                    "Run the installer again. It makes ttyd listen on a socket that only the web server's "
                    "group can open.",
                ),
                commands=["--interface /run/ttyd/ttyd.sock"],
            )
        )
    else:
        i = argv.index("--interface") if "--interface" in argv else argv.index("-i")
        iface = argv[i + 1] if i + 1 < len(argv) else ""
        if not iface.startswith("/"):
            out.append(
                _issue(
                    "listen",
                    "warn",
                    M("ttyd.listen.tcp", "ttyd listens on a TCP port."),
                    M(
                        "ttyd.listen.tcp.why",
                        "Any program on the box can reach its sign-in prompt.",
                    ),
                    M(
                        "ttyd.listen.fix",
                        "Run the installer again. It makes ttyd listen on a socket that only the web server's "
                        "group can open.",
                    ),
                    evidence=[iface],
                )
            )
    return out


def probe_tailscale(ctx, a, show):
    try:
        r = subprocess.run(("tailscale", "debug", "prefs"), capture_output=True, text=True, timeout=20)  # noqa: TID251
        prefs = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return [
            _issue(
                "config",
                "warn",
                M(
                    "tailscale.prefs.unread",
                    "The doctor could not read the settings of Tailscale.",
                ),
                commands=["tailscale debug prefs"],
            )
        ]
    out = []
    if prefs.get("RunSSH"):
        out.append(
            _issue(
                "ssh",
                "warn",
                M("tailscale.ssh.on", "Tailscale SSH is on."),
                M(
                    "tailscale.ssh.why",
                    "Members of the tailnet that its access rules let in get a shell on this box.",
                ),
                M("tailscale.ssh.fix", "If you do not use Tailscale SSH, turn it off."),
                commands=["sudo tailscale set --ssh=false"],
            )
        )
    routes = prefs.get("AdvertiseRoutes") or []
    if routes:
        out.append(
            _issue(
                "routes",
                "warn",
                M("tailscale.routes", "Tailscale advertises routes to other networks."),
                M(
                    "tailscale.routes.why",
                    "The box forwards traffic from the tailnet into those networks.",
                ),
                M(
                    "tailscale.routes.fix",
                    "If you do not use these routes, stop advertising them.",
                ),
                commands=["sudo tailscale set --advertise-routes="],
                evidence=_some(routes),
            )
        )
    if prefs.get("LoggedOut"):
        out.append(
            _issue(
                "signed-out",
                "warn",
                M(
                    "tailscale.signed-out",
                    "Tailscale is installed and running, but not signed in to a tailnet.",
                ),
                M(
                    "tailscale.signed-out.why",
                    "It does nothing for the box until someone signs it in.",
                ),
            )
        )
    return out


ADDON_PROBES = (
    ("mosquitto", probe_mosquitto),
    ("ngircd", probe_ngircd),
    ("syncthing@", probe_syncthing),
    ("kiwix", probe_kiwix),
    ("ttyd", probe_ttyd),
    ("tailscaled", probe_tailscale),
)


def step_addons(ctx):
    """Every add-on and app service apps.d declares, installed or not. For each: a finding of its state
    (addon-<id>), and one for each point its checks find (addon-<id>-<point>): does it run as root, how is
    it bound, did the administrator ask for it, who can reach it through the front, then the probe for that
    program's own config, if there is one."""
    addons = ctx["addons"]
    if not ctx["units"] and not any(ctx["addon_props"].get(a["unit"]) for a in addons):
        return [
            _cannot(
                "addons",
                M("addons.services.title", "Add-on services"),
                M("check.cannot.systemctl", "systemctl does not answer."),
                "S1 S9",
            )
        ]
    requested, listening = ctx["requested"], ctx["listening"]
    out, absent = [], []
    for a in addons:
        show = ctx["addon_props"].get(a["unit"]) or {}
        if show.get("LoadState") != "loaded":
            absent.append(a["title"])
            continue
        argv, env = _argv_of(show), _env_of(show)
        active = show.get("ActiveState") == "active"
        enabled = show.get("UnitFileState") in (
            "enabled",
            "enabled-runtime",
            "static",
            "indirect",
            "generated",
            "alias",
        )
        who = _runs_as(a, show)
        as_root = who == "root"
        issues = []
        if as_root and not a.get("root_ok"):
            issues.append(
                _issue(
                    "user",
                    "warn",
                    M("addon.root", "It runs as root."),
                    M("addon.root.why", "A flaw in it gives root access."),
                    M(
                        "addon.root.fix",
                        "Run it as an account with no special rights, or with DynamicUser=yes.",
                    ),
                )
            )
        wide = [t for t in argv if t in WIDE_WORDS or re.search(r"(?:^|[=:])(0\.0\.0\.0|\[?::\]?)(?::\d+)?$", t)]
        wide += [
            f"{k}={v}"
            for k, v in env.items()
            if v in WIDE_WORDS and k.upper() in ("HOST", "HOSTNAME", "BIND", "ADDRESS", "LISTEN", "SB_HOSTNAME")
        ]
        wide += [t for t in argv if re.search(r"(?:insecure|no-?auth|disable-auth|allow-anonymous)", t)]
        if wide:
            issues.append(
                _issue(
                    "bind",
                    "problem",
                    M("addon.bind.wide", "Its start command opens it to the network."),
                    M(
                        "addon.bind.wide.why",
                        "Anyone on the network can reach it directly.",
                    ),
                    M(
                        "addon.bind.wide.fix",
                        "Make it listen on 127.0.0.1, behind the web server's sign-in.",
                    ),
                    evidence=_some(wide, 3),
                )
            )
        if a["port"] and listening is not None and active:
            addrs = listening.get(a["port"], [])
            open_on = [x for x in addrs if not _is_loop(x)]
            if open_on:
                issues.append(
                    _issue(
                        "listen",
                        "warn",
                        M(
                            "addon.listen.wide",
                            "Port {port} answers on addresses other than the loopback address.",
                            port=a["port"],
                        ),
                        M(
                            "addon.listen.wide.why",
                            "Anyone on the network reaches it directly, not through the web server's sign-in.",
                        ),
                        M(
                            "addon.listen.wide.fix",
                            "Unless it must answer the network, make it listen on 127.0.0.1 and reach "
                            "it through the web server.",
                        ),
                        evidence=_some(open_on, 3),
                        notes=(
                            [
                                M(
                                    "addon.consent",
                                    "Its install consent says: {consent}",
                                    consent=a["consent"],
                                )
                            ]
                            if a["consent"]
                            else []
                        ),
                    )
                )
            elif not addrs:
                issues.append(
                    _issue(
                        "listen",
                        "warn",
                        M(
                            "addon.listen.none",
                            "It is running, but nothing listens on port {port}.",
                            port=a["port"],
                        ),
                        fix=M("addon.listen.none.fix", "Read its log to find the cause."),
                        commands=[f"journalctl -u {a['unit']} -n 30"],
                    )
                )
        if a["option"] and requested is not None and a["option"] not in requested and active:
            issues.append(
                _issue(
                    "record",
                    "warn",
                    M(
                        "addon.record.missing",
                        "It is running, but the install record does not include it.",
                    ),
                    M(
                        "addon.record.missing.why",
                        "Someone added it from outside irate-box, or it is left over.",
                    ),
                    M(
                        "addon.record.missing.fix",
                        "If you do not want it, disable it now and at boot.",
                    ),
                    commands=[f"sudo systemctl disable --now {a['unit']}"],
                    evidence=[a["option"]],
                )
            )
        if a["path"] and ctx["front"] is not None:
            has, gated = front_has(ctx, a["path"]), front_gated(ctx, a["path"])
            if has and not gated and as_root:
                issues.append(
                    _issue(
                        "front",
                        "problem",
                        M(
                            "addon.front.open",
                            "It runs as root, and the web server serves its route with no sign-in.",
                        ),
                        M(
                            "addon.front.open.why",
                            "Guests can reach a service that runs as root.",
                        ),
                        M(
                            "front.route.fix",
                            "Run the installer again. It puts the route behind the administrator sign-in.",
                        ),
                        evidence=[a["path"]],
                    )
                )
        if a["unit"] not in ctx["units"]:  # sandboxing not covered by the units step
            weak = [
                n
                for n, ok in (
                    ("ProtectSystem=strict", show.get("ProtectSystem") == "strict"),
                    ("NoNewPrivileges", show.get("NoNewPrivileges") == "yes"),
                    ("PrivateTmp", show.get("PrivateTmp") == "yes"),
                    ("ProtectHome", show.get("ProtectHome") not in ("no", "", None)),
                )
                if not ok
            ]
            if len(weak) >= 3:
                issues.append(
                    _issue(
                        "sandbox",
                        "warn",
                        M(
                            "addon.sandbox.weak",
                            {
                                "one": "Its unit is missing {n} of the 4 standard sandbox lines.",
                                "other": "Its unit is missing {n} of the 4 standard sandbox lines.",
                            },
                            n=len(weak),
                        ),
                        M(
                            "addon.sandbox.why",
                            "A flaw in it can reach more of the box.",
                        ),
                        M("addon.sandbox.fix", "Add the missing lines to its unit."),
                        evidence=weak,
                    )
                )
        for prefix, probe in ADDON_PROBES:
            if a["unit"].startswith(prefix):
                try:
                    issues += probe(ctx, a, show)
                except Exception as exc:  # noqa: BLE001 - one broken probe must not hide the others
                    issues.append(
                        _issue(
                            "probe",
                            "warn",
                            M(
                                "addon.probe.failed",
                                "The check of its own configuration failed.",
                            ),
                            evidence=[f"{type(exc).__name__}: {exc}"],
                        )
                    )
        title = named(a["title"], M("addon.role", "add-on"))
        about = secdoctor_xref.about("doctor", f"addon-{a['id']}")
        later = (
            []
            if active or enabled
            else [
                M(
                    "addon.not-started",
                    "It is installed but not running or started at boot. This applies once it starts.",
                )
            ]
        )
        if who is None:
            state = (
                M(
                    "addon.running-dynamic",
                    "It is running, as a dynamic account that systemd makes for it.",
                )
                if active
                else M(
                    "addon.stopped-dynamic",
                    "It is installed but not running. It runs as a dynamic account that systemd makes for it.",
                )
            )
        else:
            state = (
                M("addon.running", "It is running, as the account {user}.", user=who)
                if active
                else M(
                    "addon.stopped",
                    "It is installed but not running. It runs as the account {user}.",
                    user=who,
                )
            )
        probed = any(a["unit"].startswith(p) for p, _ in ADDON_PROBES)
        out.append(
            finding(
                f"addon-{a['id']}",
                title,
                "ok",
                state,
                notes=[
                    (
                        M(
                            "addon.issues",
                            {
                                "one": "These checks found {n} point to look at. It is listed apart.",
                                "other": "These checks found {n} points to look at. They are listed apart.",
                            },
                            n=len(issues),
                        )
                        if issues
                        else (
                            M(
                                "addon.clean.probed",
                                "No problem found by these checks, the check of its own configuration included.",
                            )
                            if probed
                            else M("addon.clean", "No problem found by these checks.")
                        )
                    )
                ],
                evidence=[a["unit"]],
                ref="S1 S9" if a["unit"].startswith("mosquitto") else "F19",
                about=about,
            )
        )
        seen = set()
        for i in issues:
            fid = f"addon-{a['id']}-{i['key']}"
            while fid in seen:
                fid += "-2"
            seen.add(fid)
            # Installed but neither running nor started at boot: a risk only once it is started.
            status = "warn" if later and i["status"] == "problem" else i["status"]
            out.append(
                finding(
                    fid,
                    title,
                    status,
                    i["summary"],
                    i["why"],
                    i["fix"],
                    notes=i["notes"] + later,
                    commands=i["commands"],
                    evidence=i["evidence"],
                    ref="S1 S9" if a["unit"].startswith("mosquitto") else "F19",
                    about=about,
                )
            )
    if absent:
        out.append(
            finding(
                "addons-absent",
                M("addons.absent.title", "Add-ons not installed"),
                "ok",
                M(
                    "addons.absent",
                    {
                        "one": "{n} add-on is not installed, so there is nothing to check.",
                        "other": "{n} add-ons are not installed, so there is nothing to check.",
                    },
                    n=len(absent),
                ),
                evidence=_some(absent, 12),
            )
        )
    return out


SECRET_FLAGS = re.compile(r"^--?(credential|password|passwd|pass|secret|token|plaintext)$", re.I)
SECRET_KV = re.compile(r"^(?:--?)?[A-Za-z_-]*(pass(word)?|secret|token|credential)[A-Za-z_-]*=\S+", re.I)


def step_cmdlines(ctx):
    """F9: secrets on process command lines, and who can read /proc."""
    hits, scanned, me = [], 0, os.getpid()
    args_t = M("cmdlines.title", "Secrets in process arguments")
    try:
        entries = [e for e in os.listdir(PROC) if e.isdigit()]
    except OSError:
        return [
            _cannot(
                "cmdlines",
                args_t,
                M("check.cannot.proc", "It cannot read {path}.", path=str(PROC)),
                "F9",
            )
        ]
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
    # The box's own /proc, as PID 1 has it: under ProtectProc (this unit's and the root helper's) /proc/mounts is the
    # unit's own instance, always with hidepid set.
    mount = _read(PROC / "1" / "mounts") or ""
    m = re.search(r"^\S+ /proc proc (\S+)", mount, re.M)
    hidepid = bool(m and re.search(r"hidepid=(1|2|invisible|noaccess|ptraceable)\b", m.group(1)))
    out = []
    hits = sorted(set(hits))
    moment = M(
        "cmdlines.moment",
        "A password change, an install or the start of the web terminal shows a secret for a moment. "
        "A look now cannot show what was visible then.",
    )
    if hits:
        out.append(
            finding(
                "cmdlines-secrets",
                args_t,
                "problem",
                M(
                    "cmdlines.found",
                    {
                        "one": "{n} running process has a secret on its command line.",
                        "other": "{n} running processes have a secret on their command line.",
                    },
                    n=len(hits),
                ),
                (
                    M(
                        "cmdlines.found.why.hidden",
                        "Root and the account that runs each process can read its command line.",
                    )
                    if hidepid
                    else M(
                        "cmdlines.found.why",
                        "Every account on the box can read every command line.",
                    )
                ),
                M("cmdlines.found.fix", "Pass secrets on standard input or in a file."),
                notes=(
                    [
                        M(
                            "cmdlines.ttyd",
                            "The web terminal's credential is the administrator password for as long as ttyd "
                            "runs. Give the web terminal a credential of its own.",
                        )
                    ]
                    if any(h.startswith("ttyd ") for h in hits)
                    else []
                )
                + [moment],
                evidence=_some(hits),
                ref="F9",
            )
        )
    else:
        out.append(
            finding(
                "cmdlines-secrets",
                args_t,
                "ok",
                M(
                    "cmdlines.none",
                    {
                        "one": "No secret is on the command line of the {n} process running now.",
                        "other": "No secret is on the command line of the {n} processes running now.",
                    },
                    n=scanned,
                ),
                notes=[moment],
                ref="F9",
            )
        )
    proc_t = M("cmdlines.proc.title", "Process visibility")
    if hidepid:
        out.append(
            finding(
                "cmdlines-proc",
                proc_t,
                "ok",
                M(
                    "cmdlines.proc.hidden",
                    "Each account sees only its own processes (hidepid is set on /proc).",
                ),
                ref="F9",
            )
        )
    else:
        out.append(
            finding(
                "cmdlines-proc",
                proc_t,
                "warn",
                M(
                    "cmdlines.proc.visible",
                    "Every account on the box can see every process and its command line.",
                ),
                M(
                    "cmdlines.proc.visible.why",
                    "A local process, a build or a compromised add-on for example, can read a secret "
                    "that another process has on its command line.",
                ),
                M(
                    "cmdlines.proc.fix",
                    "On Security, under Process visibility, select Each account sees only its own. Root "
                    "and the doctors keep their view of every process.",
                ),
                ref="F9",
            )
        )
    return out


SECRETY = re.compile(r"(?i)(password|passwd|token|secret|credential|api[_-]?key|://[^/\s:@]+:[^/\s@]+@)")


def step_secrets(ctx):
    """S13, F30: secrets at rest in /etc/hub and the install record."""
    files_t = M("secrets.title", "Credential-file permissions")
    if not ETC.is_dir():
        return [
            finding(
                "secrets",
                files_t,
                "warn",
                M("secrets.no-etc", "The hub's settings folder is not there."),
                evidence=[str(ETC)],
                ref="S13",
            )
        ]
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
        out.append(
            finding(
                "secrets-files",
                files_t,
                "problem" if bad else "warn",
                M(
                    "secrets.readable",
                    {
                        "one": "{n} file with a secret in it can be read by accounts other than root.",
                        "other": "{n} files with secrets in them can be read by accounts other than root.",
                    },
                    n=len(readable),
                ),
                M(
                    "secrets.readable.why",
                    "Any account on the box, a build or an add-on for example, can read the secret.",
                ),
                M(
                    "secrets.readable.fix",
                    "Make each file readable by root only. Where the hub must read a file, make it "
                    "readable by root and the hub's group.",
                ),
                commands=["sudo chmod 600 " + " ".join(shlex.quote(f"{ETC}/{n}") for n in readable)],
                evidence=readable,
                ref="S13",
            )
        )
    else:
        out.append(
            finding(
                "secrets-files",
                files_t,
                "ok",
                M(
                    "secrets.closed",
                    "Only root can read the files that hold secrets in the hub's settings folder.",
                ),
                evidence=[str(ETC)],
                ref="S13",
            )
        )
    if est and est.st_mode & 0o022:
        out.append(
            finding(
                "secrets-dir",
                M("secrets.dir.title", "Settings folder permissions"),
                "problem",
                M(
                    "secrets.dir.open",
                    "Accounts other than root can write the hub's settings folder.",
                ),
                M(
                    "secrets.dir.why",
                    "They can then replace the files that hold the hub's secrets and settings.",
                ),
                M(
                    "secrets.dir.fix",
                    "Make the folder belong to root, with write permission for root only.",
                ),
                commands=[f"sudo chown root:root {ETC}", f"sudo chmod 755 {ETC}"],
                evidence=[f"{ETC} {oct(est.st_mode & 0o7777)}"],
                ref="S13",
            )
        )
    if creds:
        out.append(
            finding(
                "secrets-install-options",
                M("secrets.install.title", "Credentials in install record"),
                "warn",
                M(
                    "secrets.install.url",
                    "The install record holds a repository address with a user name and token in it.",
                ),
                M(
                    "secrets.install.why",
                    "The installer took the address from the Git checkout. Other accounts can read the "
                    "file unless it is readable by root only.",
                ),
                M(
                    "secrets.install.fix",
                    "Remove the user name and token from the address, and make the file readable by root only.",
                ),
                commands=[f"sudo chmod 600 {ETC / 'install-options'}"],
                evidence=[str(ETC / "install-options")],
                ref="F30",
            )
        )
    return out


def _git_cfg(cfg, section, key):
    """A value from a repository's config text: [section] key = value (git's own case rules)."""
    m = re.search(rf"(?ims)^\s*\[{re.escape(section)}\]\s*$(.*?)(?=^\s*\[|\Z)", cfg)
    v = m and re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*(\S+)\s*$", m.group(1))
    return v.group(1).lower() if v else None


def step_git(ctx):
    """F17, F18 and the push presets: what pushed content can do."""
    root = STATE / "git"
    push_t = M("git.push.title", "Git push permissions")
    if _lstat(root / "cgitrc-public") is None and _lstat(root / "cgitrc-private") is None:
        return [
            finding(
                "git",
                M("git.title", "Git servers"),
                "ok",
                M("git.none", "The Git servers are not set up."),
                ref="F17/F18",
            )
        ]
    out = []
    # cgit 1.2.3 serves /plain/ as text/plain with its own CSP, unless cgitrc maps a type.
    loose = [
        a
        for a in ("public", "private")
        if re.search(
            r"(?m)^(mimetype-file\s*=|mimetype\.(html?|xhtml|svg)\s*=\s*(?!text/plain))",
            _read(root / f"cgitrc-{a}") or "",
        )
    ]
    html_t = M("git.html.title", "Pushed content served as active HTML")
    if loose:
        out.append(
            finding(
                "git-mimetype",
                html_t,
                "warn",
                M(
                    "git.html.runs",
                    "cgit serves pushed .html or .svg files as a type that runs in the browser.",
                ),
                M(
                    "git.html.why",
                    "A page that someone pushes then runs on the hub's address, with access to what the "
                    "browser keeps for the hub.",
                ),
                M(
                    "git.html.fix",
                    "Remove the mimetype lines from the cgit configuration, or map the types to text/plain.",
                ),
                evidence=[f"cgitrc-{a}" for a in loose],
                ref="F17",
            )
        )
    else:
        out.append(
            finding(
                "git-mimetype",
                html_t,
                "ok",
                M(
                    "git.html.text",
                    "cgit serves pushed files as text, with its own Content-Security-Policy.",
                ),
                ref="F17",
            )
        )
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
            if area == "public" and not (
                (_git_cfg(cfg, "core", "hookspath") or "").endswith("git-hooks-public")
                and _git_cfg(cfg, "receive", "fsckobjects") in ("true", "yes", "on", "1")
            ):
                unhooked.append(r.name)
            if level == "nobody" and _git_cfg(cfg, "http", "receivepack") not in (
                "false",
                "no",
                "off",
                "0",
            ):
                leaky.append(r.name)
    if everyone:
        out.append(
            finding(
                "git-everyone",
                push_t,
                "warn",
                M(
                    "git.everyone",
                    {
                        "one": "Anyone on the network can push to {n} public repository.",
                        "other": "Anyone on the network can push to {n} public repositories.",
                    },
                    n=len(everyone),
                ),
                M(
                    "git.everyone.why",
                    "The public hook refuses rewrites and deletions, and limits the size of a push. It "
                    "does not limit what is pushed.",
                ),
                M(
                    "git.everyone.fix",
                    "On Git, let only administrators push, unless guests are meant to.",
                ),
                evidence=_some(everyone),
                ref="F17/F18",
            )
        )
    if unhooked:
        out.append(
            finding(
                "git-public",
                push_t,
                "warn",
                M(
                    "git.unhooked",
                    {
                        "one": "{n} public repository has no public hook, or does not check what is pushed.",
                        "other": "{n} public repositories have no public hook, or do not check what is pushed.",
                    },
                    n=len(unhooked),
                ),
                M(
                    "git.unhooked.why",
                    "A push can then rewrite history or fill the card.",
                ),
                M(
                    "git.unhooked.fix",
                    "Run the installer again. It sets the hook and the check.",
                ),
                evidence=_some(unhooked),
                ref="F18",
            )
        )
    if leaky:
        out.append(
            finding(
                "git-readonly",
                push_t,
                "problem",
                M(
                    "git.leaky",
                    {
                        "one": "{n} repository is read-only by its setting, but still takes pushes.",
                        "other": "{n} repositories are read-only by their setting, but still take pushes.",
                    },
                    n=len(leaky),
                ),
                M(
                    "git.leaky.why",
                    "Anyone who can reach the repository can push to it.",
                ),
                M(
                    "git.leaky.fix",
                    "On Git, select the setting of each repository again. It sets both parts.",
                ),
                evidence=_some(leaky),
                ref="F18",
            )
        )
    if not (everyone or unhooked or leaky):
        out.append(
            finding(
                "git-public",
                push_t,
                "ok",
                M(
                    "git.push.ok",
                    "Each repository takes pushes as its setting says, and the public ones have their hook.",
                ),
                ref="F18",
            )
        )
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
    sudo_t = M("acct.sudo.title", "Passwordless sudo")
    keep_root = M(
        "acct.sudo.keep-root",
        "Before you remove a rule, make sure that you can still get root another way: with a "
        "password, or at the console.",
    )
    rules = _sudo_lines()
    if rules is None and not os.path.lexists(SUDOERS) and not os.path.lexists(str(SUDOERS) + ".d"):
        out.append(
            finding(
                "acct-sudo",
                sudo_t,
                "ok",
                M(
                    "acct.sudo.none",
                    "sudo is not installed, so there are no sudo rules.",
                ),
            )
        )
    elif rules is None:
        out.append(
            _cannot(
                "acct-sudo",
                sudo_t,
                M("check.cannot.read", "It cannot read {path}.", path=str(SUDOERS)),
                root=True,
            )
        )
    else:
        nopass = [(f, s) for f, s in rules if "NOPASSWD" in s]
        service = [
            (f, s) for f, s in nopass if s.split()[0].lstrip("%") in (HUB_USER, CI_USER, "www-data", "nginx", "caddy")
        ]
        if service:
            out.append(
                finding(
                    "acct-sudo-hub",
                    M("acct.sudo-hub.title", "Passwordless sudo for service accounts"),
                    "problem",
                    M(
                        "acct.sudo-hub",
                        "An account of the hub or the web server has passwordless sudo.",
                    ),
                    M(
                        "acct.sudo-hub.why",
                        "Anything that takes over that account gets root.",
                    ),
                    M("acct.sudo-hub.fix", "Remove the rule."),
                    evidence=_some(f"{f}: {s}" for f, s in service),
                    ref="F1/F3",
                )
            )
        other = [(f, s) for f, s in nopass if (f, s) not in service]
        if other:
            temp = [f for f, _ in other if "temp" in f]
            # Together a problem: a passwordless-sudo account that
            # sshd lets in by password is root for whoever guesses one password over the WiFi.
            guessable = _sshd_passwords()
            out.append(
                finding(
                    "acct-sudo",
                    sudo_t,
                    "problem" if guessable else "warn",
                    (
                        M(
                            "acct.sudo.ssh",
                            {
                                "one": "{n} sudo rule needs no password, and SSH accepts passwords.",
                                "other": "{n} sudo rules need no password, and SSH accepts passwords.",
                            },
                            n=len(other),
                        )
                        if guessable
                        else M(
                            "acct.sudo.rules",
                            {
                                "one": "{n} sudo rule needs no password.",
                                "other": "{n} sudo rules need no password.",
                            },
                            n=len(other),
                        )
                    ),
                    (
                        M(
                            "acct.sudo.ssh.why",
                            "One password guessed over the network gives root, with no step in between.",
                        )
                        if guessable
                        else M(
                            "acct.sudo.why",
                            "Whoever gets into one of these accounts gets root without a password.",
                        )
                    ),
                    (
                        M(
                            "acct.sudo.ssh.fix",
                            "Keep each rule only as long as you need it. Until you remove it, turn SSH password "
                            "sign-in off on Security.",
                        )
                        if guessable
                        else M(
                            "acct.sudo.fix",
                            "Keep each rule only as long as you need it. When you remove one, check that sudo asks "
                            "for a password again.",
                        )
                    ),
                    notes=[keep_root]
                    + (
                        [
                            M(
                                "acct.sudo.temp",
                                "A file named with -temp holds a rule that someone meant to remove after a job.",
                            )
                        ]
                        if temp
                        else []
                    ),
                    commands=["sudo rm /etc/sudoers.d/<file>", "sudo -k; sudo -n true"],
                    evidence=_some(f"{f}: {s}" for f, s in other),
                )
            )
        else:
            out.append(
                finding(
                    "acct-sudo",
                    sudo_t,
                    "ok",
                    M("acct.sudo.ok", "No sudo rule goes without a password."),
                )
            )
    passwd = _read(PASSWD)
    shadow = _read(SHADOW)
    if passwd is None:
        return out + [
            _cannot(
                "acct-users",
                M("acct.users.title", "System accounts"),
                M("check.cannot.read", "It cannot read {path}.", path=str(PASSWD)),
            )
        ]
    users = {}
    for line in passwd.splitlines():
        f = line.split(":")
        if len(f) >= 7:
            users[f[0]] = {"uid": int(f[2]) if f[2].isdigit() else -1, "shell": f[6]}
    roots = [u for u, v in users.items() if v["uid"] == 0 and u != "root"]
    if roots:
        out.append(
            finding(
                "acct-uid0",
                M("acct.uid0.title", "Duplicate UID 0"),
                "problem",
                M("acct.uid0", "An account other than root has user ID 0."),
                M("acct.uid0.why", "That account is root under another name."),
                M(
                    "acct.uid0.fix",
                    "Remove the account, or give it a user ID of its own.",
                ),
                evidence=roots,
            )
        )
    nologin = ("nologin", "false")
    nopw_t = M("acct.nopw.title", "Empty password")
    if shadow is None:
        out.append(
            _cannot(
                "acct-nopw",
                nopw_t,
                M("check.cannot.read", "It cannot read {path}.", path=str(SHADOW)),
                root=True,
            )
        )
    else:
        empty = []
        for line in shadow.splitlines():
            f = line.split(":")
            if len(f) >= 2 and f[1] == "" and f[0] in users and not users[f[0]]["shell"].endswith(nologin):
                empty.append(f[0])
        if empty:
            out.append(
                finding(
                    "acct-nopw",
                    nopw_t,
                    "problem",
                    M(
                        "acct.nopw",
                        {
                            "one": "{n} account can sign in with no password.",
                            "other": "{n} accounts can sign in with no password.",
                        },
                        n=len(empty),
                    ),
                    M(
                        "acct.nopw.why",
                        "Anyone at the console, or with another way in, can use them.",
                    ),
                    M("acct.nopw.fix", "Give each account a password, or lock it."),
                    commands=["sudo passwd <account>", "sudo passwd -l <account>"],
                    evidence=empty,
                )
            )
        else:
            out.append(
                finding(
                    "acct-nopw",
                    nopw_t,
                    "ok",
                    M(
                        "acct.nopw.none",
                        "No account with a sign-in shell has an empty password.",
                    ),
                )
            )
    shells_t = M("acct.shells.title", "Service account shells")
    badsh = [u for u in (HUB_USER, CI_USER) if u in users and not users[u]["shell"].endswith(nologin)]
    if badsh:
        out.append(
            finding(
                "acct-shells",
                shells_t,
                "warn",
                M(
                    "acct.shells.open",
                    "An account of the hub can sign in with a shell.",
                ),
                M(
                    "acct.shells.why",
                    "These accounts run services. They do not need to sign in.",
                ),
                M("acct.shells.fix", "Give each account the nologin shell."),
                commands=[f"sudo usermod -s /usr/sbin/nologin {u}" for u in badsh],
                evidence=badsh,
                ref="F19",
            )
        )
    elif any(u in users for u in (HUB_USER, CI_USER)):
        out.append(
            finding(
                "acct-shells",
                shells_t,
                "ok",
                M(
                    "acct.shells.closed",
                    "The hub's account and the build account cannot sign in with a shell.",
                ),
                ref="F19",
            )
        )
    return out


def step_accounts_hub(ctx):
    """The hub's own accounts: no account with a hash that
    isn't scrypt at the hub's parameters, users mode never left loosely over plain HTTP, and every app
    in users mode has its gate actually wired in the web server's config."""
    from irate_box.hub import access as acc
    from irate_box.hub import accounts as accmod

    about = {"kind": "setting", "key": "accounts"}
    try:
        data = json.loads(_read(STATE / "accounts.json") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    accounts = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
    out = []
    weak = []
    for name, a in accounts.items():
        h = a.get("hash") if isinstance(a, dict) else None
        if not h:
            continue  # waiting for its one-time code (make()); it cannot log in yet
        parts = h.split("$")
        ok = len(parts) == 6 and parts[0] == "scrypt" and parts[1].isdigit() and int(parts[1]) >= accmod.SCRYPT["n"]
        if not ok:
            weak.append(name)
    hash_t = M("accounts.hash.title", "Hub account password hashes")
    params = f"scrypt N={accmod.SCRYPT['n']}"
    if not accounts:
        out.append(
            finding(
                "accounts-hash",
                hash_t,
                "ok",
                M("accounts.hash.none", "The hub has no user accounts yet."),
                about=about,
            )
        )
    elif weak:
        out.append(
            finding(
                "accounts-hash",
                hash_t,
                "problem",
                M(
                    "accounts.hash.weak",
                    {
                        "one": "{n} hub account has a password hash weaker than the hub makes now.",
                        "other": "{n} hub accounts have password hashes weaker than the hub makes now.",
                    },
                    n=len(weak),
                ),
                M(
                    "accounts.hash.weak.why",
                    "Someone can have edited accounts.json by hand, or an older version made the hash. "
                    "A weak hash is quicker to guess from a copy of the file.",
                ),
                M(
                    "accounts.hash.weak.fix",
                    "On Accounts, reset the password of each account. The hub then hashes it again.",
                ),
                evidence=_some(weak) + [params],
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "accounts-hash",
                hash_t,
                "ok",
                M(
                    "accounts.hash.ok",
                    "Each hub account's password hash uses the parameters that the hub makes now.",
                ),
                evidence=[params],
                about=about,
            )
        )

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    signup = settings.get("signup") if settings.get("signup") in accmod.SIGNUP else accmod.DEFAULTS["signup"]
    http = settings.get("http") if settings.get("http") in accmod.HTTP else accmod.DEFAULTS["http"]
    state = acc.read(ETC / "access.json")
    users_apps = sorted(i for i in acc.ROUTED if acc.mode_of(state, i) == "users")
    if signup == "off" or not users_apps:
        return out
    from irate_box.root import tls

    https_on = bool(tls.status().get("on"))
    http_t = M("accounts.http.title", "Sign-in over plain HTTP")
    if not https_on and http != "prevented":
        out.append(
            finding(
                "accounts-http",
                http_t,
                "problem",
                M(
                    "accounts.http.open",
                    "Some apps ask for an account, and the box has no HTTPS.",
                ),
                M(
                    "accounts.http.why",
                    "A password sent over plain HTTP can be read off the air.",
                ),
                M(
                    "accounts.http.fix",
                    "On Security, set up HTTPS. Then, on Accounts, refuse passwords over plain HTTP.",
                ),
                evidence=_some(users_apps) + [f"http={http}"],
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "accounts-http",
                http_t,
                "ok",
                (
                    M("accounts.http.https", "The box has HTTPS.")
                    if https_on
                    else M(
                        "accounts.http.refused",
                        "The hub refuses passwords over plain HTTP.",
                    )
                ),
                about=about,
            )
        )

    kind = ctx.get("front_kind")
    missing = []
    if kind == "nginx":
        gates_dir = ETC / "nginx-access.conf.d"
        for i in users_apps:
            if acc.NGINX_GATE.splitlines()[0] not in (_read(gates_dir / f"gate-{i}.conf") or ""):
                missing.append(i)
    elif kind == "caddy":
        caddy_dir = Path(os.environ.get("HUB_CADDY_DIR", "/etc/caddy"))
        for i in users_apps:
            text = _read(caddy_dir / f"{i}.caddy") or ""
            if "forward_auth" not in text or "/_irate/user" not in text:
                missing.append(i)
    gate_t = M("accounts.gate.title", "User-app authentication gate")
    if missing:
        out.append(
            finding(
                "accounts-gate",
                gate_t,
                "problem",
                M(
                    "accounts.gate.missing",
                    {
                        "one": "{n} app is set for users with an account, but the web server does not "
                        "check for a sign-in there.",
                        "other": "{n} apps are set for users with an account, but the web server does not "
                        "check for a sign-in there.",
                    },
                    n=len(missing),
                ),
                M("accounts.gate.why", "A guest can open them with no sign-in."),
                M(
                    "accounts.gate.fix",
                    "Run the installer again. It makes the checks again. Or, on Apps, set the access of "
                    "each app again.",
                ),
                evidence=missing,
                about=about,
            )
        )
    elif kind in ("nginx", "caddy"):
        out.append(
            finding(
                "accounts-gate",
                gate_t,
                "ok",
                M(
                    "accounts.gate.ok",
                    "The web server checks for a sign-in on each app that is set for users with an account.",
                ),
                evidence=_some(users_apps),
                about=about,
            )
        )
    return out


def _sshd_passwords():
    """Whether sshd here lets anyone in by password (sshd -T); False when there is no sshd."""
    try:
        from irate_box.root import security

        s = security.sshd_settings()
    except Exception:  # noqa: BLE001  (an sshd that will not answer -T is not a password login)
        return False
    return bool(s) and s.get("passwordauthentication", "yes") == "yes"


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
    off = [f"{n}=0" for n, v in links if v == 0]
    na = [n for n, v in links + reg if v is None]
    weakreg = [f"{n}=0" for n, v in reg if v == 0]
    links_t = M("kernel.links.title", "Kernel link protections")
    if off:
        out.append(
            finding(
                "kernel-links",
                links_t,
                "problem",
                M(
                    "kernel.links.off",
                    "The kernel's protections for links in shared folders are off.",
                ),
                M(
                    "kernel.links.off.why",
                    "Root and other accounts follow links that another account makes in shared, "
                    "world-writable folders such as /tmp.",
                ),
                M(
                    "kernel.links.fix",
                    "Turn the protections on, and keep the setting in /etc/sysctl.d.",
                ),
                commands=[
                    "printf 'fs.protected_symlinks=1\\nfs.protected_hardlinks=1\\n' | sudo tee /etc/sysctl.d/61-links.conf "
                    "&& sudo sysctl --system"
                ],
                evidence=off,
                ref="F3",
            )
        )
    else:
        out.append(
            finding(
                "kernel-links",
                links_t,
                "warn" if any(v is None for _, v in links) else "ok",
                M(
                    "kernel.links.on",
                    "The kernel's protections for links in shared folders are on.",
                ),
                notes=[
                    M(
                        "kernel.links.scope",
                        "They cover only shared, world-writable folders. They do not cover root's "
                        "writes in the hub's own folders.",
                    )
                ]
                + (
                    [
                        M(
                            "kernel.links.absent",
                            "This kernel does not have some of these settings.",
                        )
                    ]
                    if na
                    else []
                ),
                evidence=na,
                ref="F3",
            )
        )
    if weakreg:
        out.append(
            finding(
                "kernel-regular",
                M("kernel.regular.title", "Kernel file and FIFO protections"),
                "warn",
                M(
                    "kernel.regular.off",
                    "The kernel's protections for files and FIFOs in shared folders are off.",
                ),
                M(
                    "kernel.regular.why",
                    "A process can open a file or FIFO that another account put in /tmp in its place.",
                ),
                M(
                    "kernel.regular.fix",
                    "Turn the protections on, and keep the setting in /etc/sysctl.d.",
                ),
                commands=[
                    "printf 'fs.protected_regular=2\\nfs.protected_fifos=2\\n' | sudo tee /etc/sysctl.d/61-regular.conf "
                    "&& sudo sysctl --system"
                ],
                evidence=weakreg,
            )
        )
    pt = _sysctl("kernel.yama.ptrace_scope")
    if pt == 0:
        out.append(
            finding(
                "kernel-ptrace",
                M("kernel.ptrace.title", "Process tracing between processes"),
                "warn",
                M(
                    "kernel.ptrace.open",
                    "Any process can trace another process of the same account.",
                ),
                M(
                    "kernel.ptrace.why",
                    "A compromised process of the hub can then read the memory of the hub's other processes. "
                    "That memory includes the administrator password.",
                ),
                M(
                    "kernel.ptrace.fix",
                    "Limit tracing to parent processes, and keep the setting in /etc/sysctl.d.",
                ),
                commands=[
                    "echo kernel.yama.ptrace_scope=1 | sudo tee /etc/sysctl.d/61-ptrace.conf && sudo sysctl --system"
                ],
                evidence=["kernel.yama.ptrace_scope=0"],
                ref="F9",
            )
        )
    hard = [(n, _sysctl(n)) for n in ("kernel.kptr_restrict", "kernel.dmesg_restrict")]
    lax = [f"{n}=0" for n, v in hard if v == 0]
    info_t = M("kernel.info.title", "Kernel restrictions")
    if lax:
        out.append(
            finding(
                "kernel-info",
                info_t,
                "warn",
                M(
                    "kernel.info.open",
                    "Every account can read the kernel's addresses or its log.",
                ),
                M(
                    "kernel.info.why",
                    "Kernel addresses and the kernel log help an attacker who already runs code on the box "
                    "to aim an exploit.",
                ),
                M(
                    "kernel.info.fix",
                    "Restrict them to root, and keep the setting in /etc/sysctl.d.",
                ),
                commands=[
                    "printf 'kernel.kptr_restrict=1\\nkernel.dmesg_restrict=1\\n' | sudo tee /etc/sysctl.d/61-info.conf "
                    "&& sudo sysctl --system"
                ],
                evidence=lax,
            )
        )
    elif not out or all(f["status"] == "ok" for f in out):
        out.append(
            finding(
                "kernel-info",
                info_t,
                "ok",
                M(
                    "kernel.info.ok",
                    "Process tracing, kernel addresses and the kernel log are not open to every account, or "
                    "this kernel does not have the settings.",
                ),
            )
        )
    return out


# Which apps run on an origin of their own (S4): what an app's pages run
# can act with the owner's login only where it shares the hub's origin.
OWN_ORIGIN = (
    (
        "notes",
        "Notes (SilverBullet)",
        r"proxy_pass\s+http://unix:/run/silverbullet/|reverse_proxy\s+unix//run/silverbullet/",
    ),
    (
        "wiki",
        "Kiwix's books",
        r"proxy_pass\s+http://127\.0\.0\.1:8081|reverse_proxy\s+127\.0\.0\.1:8081",
    ),
    ("git", "cgit and pushed content", r"cgit\.cgi"),
    ("draw", "Excalidraw", r"alias\s+\S+/draw/;|root\s+\*\s+\{\$HUB_DRAW_ROOT"),
    ("mermaid", "Mermaid", r"alias\s+\S+/mermaid/;|root\s+\*\s+\{\$HUB_MERMAID_ROOT"),
    ("sync", "Syncthing's web page", r"proxy_pass\s+http://127\.0\.0\.1:8384|reverse_proxy\s+127\.0\.0\.1:8384"),
    # The block that sends the hub the front's secret is the one that serves /admin.
    ("admin", "The administration pages", r"irate-box-front\.conf|header_up\s+X-Irate-Front"),
)


def _caddy_servers(parsed):
    """[(listen ports, the site's lines with its snippets' in place)] for each top-level site
    block of a parsed Caddyfile, in file order. A site's port is its address's (or the default
    of its {$ENV:default})."""
    blocks, order = {}, []
    for stack, line, opens in parsed:
        if not stack and opens:
            blocks.setdefault(line, [])
            order.append(line)
        elif stack:
            blocks.setdefault(stack[0], []).append(line)
    snippets = {h[1:-1]: lines for h, lines in blocks.items() if h.startswith("(") and h.endswith(")")}
    out = []
    for head in order:
        m = re.fullmatch(r"(?:https?://)?:(?:(\d+)|\{\$\w+:(\d+)\})", head)
        if not m:
            continue
        lines = []
        for line in blocks[head]:
            name = line.split()[1] if line.startswith("import ") and len(line.split()) == 2 else None
            lines.extend(snippets.get(name, [line]))
        out.append(([m.group(1) or m.group(2)], "\n".join(lines)))
    return out


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
                block = text[start : m.end()]
                out.append(
                    (
                        sorted(set(re.findall(r"(?m)^\s*listen\s+(?:\[::\]:)?(\d+)", block))),
                        block,
                    )
                )
                start = None
    return out


def step_origins(ctx):
    """Which origin each app is on: the hub's own port, or one of its own (S4)."""
    origins_t = M("origins.title", "Application origins")
    if ctx["front_kind"] == "caddy":
        servers = _caddy_servers(ctx["front"] or [])
        if not servers:
            return [
                _cannot(
                    "origins",
                    origins_t,
                    M(
                        "check.cannot.caddy-sites",
                        "It finds no site blocks in the Caddy configuration.",
                    ),
                    "S4",
                )
            ]
    elif ctx["front_kind"] != "nginx":
        return [
            _cannot(
                "origins",
                origins_t,
                M("check.cannot.no-front", "It finds no nginx or Caddy configuration."),
                "S4",
            )
        ]
    else:
        servers = _nginx_servers(_tls_includes(_read(NGINX_CONF) or ""))
    if not servers:
        return [
            _cannot(
                "origins",
                origins_t,
                M("check.cannot.read", "It cannot read {path}.", path=str(NGINX_CONF)),
                "S4",
            )
        ]
    hub_ports = servers[0][0]
    out = []
    for key, name, pattern in OWN_ORIGIN:
        where = [ports for ports, block in servers if re.search(pattern, block)]
        if not where:
            continue
        ports = where[0]
        title = named(name, M("origins.role", "origin"))
        if ports == hub_ports and key == "admin":
            out.append(
                finding(
                    f"origin-{key}",
                    title,
                    "warn",
                    M(
                        "origins.admin-shared",
                        "The administration pages are served on the same address and port as the hub's pages.",
                    ),
                    M(
                        "origins.admin-shared.why",
                        "Script in any page of the hub's origin can act with the session of an administrator "
                        "who opens the administration pages.",
                    ),
                    M(
                        "origins.shared.fix",
                        "Serve it on a port of its own. An update of the hub does this.",
                    ),
                    evidence=[f"port {', '.join(ports)}"],
                    ref="S4",
                )
            )
        elif ports == hub_ports:
            out.append(
                finding(
                    f"origin-{key}",
                    title,
                    "warn",
                    M(
                        "origins.shared",
                        "It is served on the same address and port as the administration pages.",
                    ),
                    M(
                        "origins.shared.why",
                        "Scripts in its pages can act with the session of an administrator who opens them.",
                    ),
                    M(
                        "origins.shared.fix",
                        "Serve it on a port of its own. An update of the hub does this.",
                    ),
                    evidence=[f"port {', '.join(ports)}"],
                    ref="S4",
                )
            )
        else:
            out.append(
                finding(
                    f"origin-{key}",
                    title,
                    "ok",
                    M(
                        "origins.own",
                        "It is served on a port of its own, apart from the hub.",
                    ),
                    evidence=[
                        f"port {', '.join(ports)}",
                        f"hub {', '.join(hub_ports)}",
                    ],
                    ref="S4",
                )
            )
    return out


# --- debsecan: Debian's packages against Debian's security tracker ---------------------------
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
            return (
                None,
                "debsecan is not on the box, and the security kit's cache does not hold it",
            )
        debs[name] = found[-1]
    # Run as root: only files that are still what the kit's manifest says was fetched.
    from irate_box.root import kits

    damaged = {Path(p.split(" ", 1)[0]).name for p in kits.verify()}
    for d in debs.values():
        if d.name in damaged:
            return None, f"{d.name} in the security kit's cache fails its check"
        r = subprocess.run(  # noqa: TID251
            ["dpkg-deb", "-x", str(d), str(work)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode != 0:
            return None, f"unpacking {d.name}: {r.stderr.strip()[:200]}"
    return [sys.executable, str(work / "usr" / "bin" / "debsecan")], {
        "PYTHONPATH": str(work / "usr" / "lib" / "python3" / "dist-packages")
    }


def _deb_cmp(a, b):
    """Debian's version order (deb-version(7)): epoch, then upstream and revision, each compared in
    turns of non-digits (letters before other characters, ~ before everything, even the end) and
    digits (as numbers). Negative, zero or positive, as cmp."""

    def split(v):
        epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
        up, _, rev = rest.rpartition("-") if "-" in rest else (rest, "", "0")
        return int(epoch or 0), up, rev

    def order(c):
        return -1 if c == "~" else 0 if c == "" else ord(c) if c.isalpha() else ord(c) + 256

    def part(x, y):
        while x or y:
            i = j = 0
            while i < len(x) and not x[i].isdigit():
                i += 1
            while j < len(y) and not y[j].isdigit():
                j += 1
            nx, ny = x[:i], y[:j]
            for k in range(max(len(nx), len(ny))):
                d = order(nx[k] if k < len(nx) else "") - order(ny[k] if k < len(ny) else "")
                if d:
                    return d
            x, y = x[i:], y[j:]
            i = j = 0
            while i < len(x) and x[i].isdigit():
                i += 1
            while j < len(y) and y[j].isdigit():
                j += 1
            d = int(x[:i] or 0) - int(y[:j] or 0)
            if d:
                return d
            x, y = x[i:], y[j:]
        return 0

    (ea, ua, ra), (eb, ub, rb) = split(a), split(b)
    return (ea > eb) - (ea < eb) or part(ua, ub) or part(ra, rb)


def newest_only(status_text):
    """The kits' cache as dpkg's status file lists it (kits.py: kits-status), with only the newest
    version of each package and architecture: an older one kept beside it for rolling back is not
    what an install would bring, so its CVEs are not the cache's. Returns (text, how many left out).
    """
    newest = {}
    for stanza in status_text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line)
        if "Package" not in fields or "Version" not in fields:
            continue
        key = (fields["Package"], fields.get("Architecture", ""))
        if key not in newest or _deb_cmp(fields["Version"], newest[key][0]) > 0:
            newest[key] = (fields["Version"], stanza.strip("\n"))
    total = sum(1 for st in status_text.split("\n\n") if "Package: " in st)
    return "".join(st + "\n\n" for _, (_, st) in sorted(newest.items())), total - len(newest)


def _debsecan(cmd, env, suite, status=None, timeout=900):
    """{package: [(cve, {"fixed", "remote", "urgency"})]} from debsecan --format summary. Fifteen
    minutes: with a firmware build and the deep audit running (load 25 on four cores) five are not
    enough, and a busy box is not a failed check."""
    args = [
        *cmd,
        "--suite",
        suite,
        "--source",
        f"file://{DEBSECAN_FEED}/",
        "--format",
        "summary",
    ]
    if status:
        args += ["--status", str(status)]
    try:
        r = subprocess.run(  # noqa: TID251
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(os.environ, **env),
        )
    except subprocess.TimeoutExpired as exc:
        try:
            load = Path("/proc/loadavg").read_text().split()[1]
        except OSError:
            load = "?"
        raise RuntimeError(
            f"debsecan took over {timeout // 60} minutes (the box's load: {load}); run the doctor again when it is quieter"
        ) from exc
    if r.returncode != 0:
        raise RuntimeError((r.stderr.strip().splitlines() or ["debsecan failed"])[-1][:300])
    out = {}
    for line in r.stdout.splitlines():
        m = SUMMARY_RE.match(line.strip())
        if not m:
            continue
        notes = [n.strip() for n in (m.group(3) or "").split(",") if n.strip()]
        urgency = next((n.split()[0] for n in notes if n.endswith(" urgency")), "")
        out.setdefault(m.group(2), []).append(
            (
                m.group(1),
                {
                    "fixed": "fixed" in notes,
                    "remote": "remotely exploitable" in notes,
                    "urgency": urgency,
                },
            )
        )
    return out


def _sources():
    """{binary package: its source package}, from dpkg."""
    r = subprocess.run(  # noqa: TID251
        ["dpkg-query", "-W", "-f", "${Package}\t${source:Package}\n"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return dict(line.split("\t", 1) for line in r.stdout.splitlines() if "\t" in line)


def debsecan_findings(installed, cached, kit_of, feed_date, source_of=None):
    """Findings from debsecan's two runs. A fix Debian has released but the box hasn't installed:
    a problem when remotely exploitable or high urgency, else a warning; one
    finding per package. Unfixed ones are listed, not counted. A kit's cache holding a version
    with a released fix: a warning on that kit (refresh it while online)."""
    out = []
    age = (
        [
            M(
                "debsecan.feed-date",
                "The tracker data is from {date}.",
                date=when(feed_date),
            )
        ]
        if feed_date
        else []
    )
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

        def tags(n):
            return "".join(
                t
                for t in (
                    " remote" if n["remote"] else "",
                    f" {n['urgency']}" if n["urgency"] else "",
                )
            )

        out.append(
            finding(
                f"debsecan-{src}",
                named(src, M("debsecan.role", "Debian package")),
                "problem" if bad else "warn",
                M(
                    "debsecan.fixed",
                    {
                        "one": "Debian has released a fix for {n} known vulnerability that is not installed.",
                        "other": "Debian has released fixes for {n} known vulnerabilities that are not installed.",
                    },
                    n=len(fixed),
                ),
                (
                    M(
                        "debsecan.fixed.remote",
                        "Some of them can be used over the network, or Debian rates them as urgent.",
                    )
                    if bad
                    else None
                ),
                M(
                    "debsecan.fixed.fix",
                    "On Updates, install Debian's security updates. The box needs an internet connection "
                    "to download them, unless a toolkit cache already holds them.",
                ),
                notes=(
                    [
                        M(
                            "debsecan.binaries",
                            "The fixes are in these packages: {packages}.",
                            packages=bins,
                        )
                    ]
                    if bins != [src]
                    else []
                )
                + age,
                evidence=_some([f"{c}{tags(n)}" for c, n in fixed]),
                source="debsecan",
                about={"kind": "setting", "key": "security-updates"},
            )
        )
    unfixed = sorted(p for p, v in installed.items() if not any(n["fixed"] for _, n in v))
    out.append(
        finding(
            "debsecan-unfixed",
            M("debsecan.unfixed.title", "Unfixed vulnerabilities"),
            "ok",
            (
                M(
                    "debsecan.unfixed",
                    {
                        "one": "{n} installed package has known vulnerabilities that Debian has not fixed yet.",
                        "other": "{n} installed packages have known vulnerabilities that Debian has not fixed yet.",
                    },
                    n=len(unfixed),
                )
                if unfixed
                else M(
                    "debsecan.unfixed.none",
                    "No installed package has a known vulnerability that Debian has not fixed yet.",
                )
            ),
            notes=(
                [
                    M(
                        "debsecan.unfixed.count",
                        "They are listed, not counted: there is nothing to install for them yet.",
                    )
                ]
                if unfixed
                else []
            )
            + age,
            evidence=_some(unfixed, 20),
            source="debsecan",
        )
    )
    per_kit = {}
    for pkg, vulns in cached.items():
        if any(n["fixed"] for _, n in vulns):
            for kid in kit_of.get(pkg, []):
                per_kit.setdefault(kid, set()).add(pkg)
    for kid, pkgs in sorted(per_kit.items()):
        out.append(
            finding(
                f"debsecan-kit-{kid}",
                named(kid, M("debsecan.kit.role", "toolkit cache")),
                "warn",
                M(
                    "debsecan.kit",
                    {
                        "one": "{n} package in this toolkit's cache has a fix that Debian released since.",
                        "other": "{n} packages in this toolkit's cache have fixes that Debian released since.",
                    },
                    n=len(pkgs),
                ),
                M(
                    "debsecan.kit.why",
                    "An install of the toolkit now brings the versions without the fixes.",
                ),
                M(
                    "debsecan.kit.fix",
                    "On Toolkits, refresh the toolkit while the box has an internet connection.",
                ),
                notes=age,
                evidence=_some(sorted(pkgs)),
                source="debsecan",
                about={"kind": "kit", "key": kid},
            )
        )
    return out


def step_debsecan(ctx):
    suite = _codename()
    feed = DEBSECAN_FEED / suite if suite else None
    if not feed or not feed.is_file():
        return [
            finding(
                "debsecan-data",
                M("debsecan.data.title", "Tracker download time"),
                "warn",
                M(
                    "debsecan.data.missing",
                    "The data of Debian's security tracker is not on the box yet.",
                ),
                M(
                    "debsecan.data.why",
                    "Without it, the doctor cannot tell which installed packages have known vulnerabilities.",
                ),
                M(
                    "debsecan.data.fix",
                    "On Toolkits, keep the Security toolkit current. The librarian then downloads the data "
                    "each day that the box has an internet connection.",
                ),
                source="debsecan",
            )
        ]
    import shutil as _sh
    import tempfile

    work = Path(tempfile.mkdtemp(prefix="debsecan-"))
    try:
        cmd, env = _debsecan_command(work)
        if cmd is None:
            return [
                finding(
                    "debsecan-tool",
                    M("debsecan.tool.title", "debsecan"),
                    "warn",
                    M("debsecan.tool.missing", "The doctor could not run debsecan."),
                    M(
                        "debsecan.data.why",
                        "Without it, the doctor cannot tell which installed packages have known vulnerabilities.",
                    ),
                    M(
                        "debsecan.tool.fix",
                        "On Toolkits, refresh the Security toolkit while the box has an internet connection. "
                        "Its cache is enough: the box does not install it for this.",
                    ),
                    evidence=[env],
                    source="debsecan",
                )
            ]
        installed = _debsecan(cmd, env, suite)
        kit_of = {}
        for m in sorted((KITS_ROOT / "manifests").glob("*.json")) if (KITS_ROOT / "manifests").is_dir() else []:
            if m.name.endswith(".previous.json"):
                continue
            for p in (json.loads(_read(m) or "{}") or {}).get("packages", []):
                kit_of.setdefault(p["name"], []).append(m.stem)
        # Judged by the newest cached version of each package: older ones stay only for Roll back.
        status = KITS_ROOT / "kits-status"
        if status.is_file():
            newest = work / "kits-status-newest"
            newest.write_text(newest_only(_read(status) or "")[0])
            cached = _debsecan(cmd, env, suite, newest)
        else:
            cached = {}
        return debsecan_findings(installed, cached, kit_of, feed.stat().st_mtime, _sources())
    finally:
        _sh.rmtree(work, ignore_errors=True)


def step_kits_trust(ctx):
    """What vouches for the toolkits a USB drive brings: Debian's archive keyring (kits verify against it alone),
    and the kits imported without a freshness check (kits.unchecked: listed while the clock cannot date them, or
    once it can and a release had expired; a kit found current after all leaves the list by itself)."""
    from irate_box.root import health, kits

    go_kits = health._go("toolkits", M("go.toolkits", "Toolkits"))
    out = []
    if kits.DEBIAN_KEYRING.is_file():
        out.append(
            finding(
                "kits-keyring",
                M("kits-trust.keyring.title", "Debian's archive keyring"),
                "ok",
                M("kits-trust.keyring.ok", "A toolkit from a USB drive is checked against Debian's archive keyring."),
                evidence=[kits.DEBIAN_KEYRING],
            )
        )
    else:
        out.append(
            finding(
                "kits-keyring",
                M("kits-trust.keyring.title", "Debian's archive keyring"),
                "problem",
                M(
                    "kits-trust.keyring.missing",
                    "Debian's archive keyring {file} is missing.",
                    file=str(kits.DEBIAN_KEYRING),
                ),
                M(
                    "kits-trust.keyring.why",
                    "Without it the box cannot check a toolkit's packages, so it refuses every import from a USB drive.",
                ),
                M("kits-trust.keyring.fix", "Install the package debian-archive-keyring."),
                commands=["sudo apt install debian-archive-keyring"],
                evidence=[kits.DEBIAN_KEYRING],
            )
        )
    listed = kits.unchecked()
    if not listed:
        out.append(
            finding(
                "kits-freshness",
                M("kits-trust.freshness.title", "Toolkits imported without a freshness check"),
                "ok",
                M(
                    "kits-trust.freshness.none",
                    "Every toolkit in the cache had its freshness checked, or it has been since.",
                ),
            )
        )
        return out
    expired = [k["title"] for k in listed if k["state"] == "expired"]
    unknown = [k["title"] for k in listed if k["state"] == "unknown"]
    if expired:
        out.append(
            finding(
                "kits-freshness",
                M("kits-trust.freshness.title", "Toolkits imported without a freshness check"),
                "problem",
                M(
                    "kits-trust.freshness.expired",
                    {
                        "one": "The toolkit {kits} was imported without a freshness check, and its release had expired.",
                        "other": "The toolkits {kits} were imported without a freshness check, and their releases had expired.",
                    },
                    n=len(expired),
                    kits=expired,
                ),
                M(
                    "kits-trust.freshness.expired.why",
                    "An expired release can carry packages with problems Debian has fixed since.",
                ),
                M(
                    "kits-trust.freshness.expired.fix",
                    "On Toolkits, download the toolkit again while the box has an internet connection, or remove it.",
                ),
                go=go_kits,
            )
        )
    if unknown:
        out.append(
            finding(
                "kits-freshness-unknown",
                M("kits-trust.freshness.title", "Toolkits imported without a freshness check"),
                "warn",
                M(
                    "kits-trust.freshness.unknown",
                    {
                        "one": "The toolkit {kits} was imported without a freshness check. The clock cannot date it yet.",
                        "other": "The toolkits {kits} were imported without a freshness check. The clock cannot date them yet.",
                    },
                    n=len(unknown),
                    kits=unknown,
                ),
                M(
                    "kits-trust.freshness.unknown.why",
                    "A signature shows who made a release, not that it is the latest: a USB drive could carry an "
                    "older signed set.",
                ),
                M(
                    "kits-trust.freshness.unknown.fix",
                    "Give the box network time or set its clock on the Clock page. The doctor then checks the "
                    "releases' dates itself and clears this when they were current.",
                ),
                go=health.GO_CLOCK,
            )
        )
    return out


def step_deep_cis(ctx):
    return _deep_findings("debian-cis")


def step_deep_lynis(ctx):
    return _deep_findings("lynis")


def _deep_findings(source):
    """The deep audit's kept findings for one source, dated; a warning when it is old."""
    from irate_box.root import deepaudit

    rep = deepaudit.load()
    got = (rep or {}).get("sources", {}).get(source)
    if got and not current(got):
        got = None  # kept by an older version, in the old shape: as not run, until the deep audit runs again
    if not got:
        return [
            finding(
                f"{source}-none",
                named(source),
                "ok",
                M(
                    "deep.not-run",
                    "It runs in the deep audit: each week, or when you select Deep audit on this page.",
                ),
                notes=[
                    M(
                        "deep.duration",
                        "The deep audit takes about 8 minutes on a small board, and longer when the box is busy.",
                    )
                ],
                source=source,
            )
        ]
    ran = M("deep.when", "From the deep audit of {date}.", date=when(got["at"]))
    out = [dict(f) for f in got["findings"]]
    if time.time() - got["at"] > 14 * 86400:
        out.append(
            finding(
                f"{source}-old",
                named(source),
                "warn",
                M("deep.old", "The last deep audit is more than 14 days old."),
                M(
                    "deep.old.why",
                    "Settings can have changed since. The results below can be out of date.",
                ),
                M("deep.old.fix", "Run the deep audit again."),
                source=source,
            )
        )
    for f in out:
        f["notes"] = list(f.get("notes") or []) + [ran]
        _dress_cis(f)
    return out


CIS_SECTION_RE = re.compile(r"^cis-(\d+\.\d+)$")


def _dress_cis(f):
    """A CIS section's finding ("CIS 4.1: 22 checks not met") as what it is about, in plain words, and
    how it stands on a box like this (secdoctor_xref.CIS): a suggestion, or not for this board, and why.
    """
    # A check split out of its section because another source asks the same (cis-5.2.10_disable_root_login):
    # a suggestion as its section is, so beside a fine Security page line it is not a real item.
    one = re.match(r"^cis-(\d+\.\d+)[\d.]*_\w", f.get("id", ""))
    if one and f.get("source") == "debian-cis" and f["status"] != "ok" and not f.get("tier"):
        f["tier"] = (secdoctor_xref.cis(one.group(1)) or (None, "suggest"))[1]
        return
    m = CIS_SECTION_RE.match(f.get("id", ""))
    known = m and secdoctor_xref.cis(m.group(1))
    if not known or f["status"] == "ok":
        return
    title, tier, what, here = known
    f["title"] = M("cis.dressed.title", "{topic} (CIS {section})", topic=title, section=m.group(1))
    f["notes"] = [what] + list(f.get("notes") or [])
    f["fix"] = here
    cmd = secdoctor_xref.CIS_CMD.get(m.group(1))
    if cmd:
        f["commands"] = [cmd]
    f["tier"] = tier
    if m.group(1) == "1.9":
        f["about"] = {"kind": "setting", "key": "security-updates"}


def step_security_page(ctx):
    """The Security page's own scan (security.py, control/security.json), as a source of its own,
    so the joint report can say where it and the doctor (or nmap) agree."""
    try:
        scan = json.loads(_read(CONTROL / "security.json") or "")
    except ValueError:
        scan = None
    if not current(scan):  # none yet, or one an older version wrote: it runs again when the Security page opens
        return [
            finding(
                "security-page-none",
                M("page.title", "Security page scan"),
                "ok",
                M(
                    "page.not-run",
                    "It has not run yet. It runs when someone opens the Security page.",
                ),
                source="security-page",
            )
        ]
    ran = M(
        "page.when",
        "From the Security page's scan of {date}.",
        date=when(scan.get("at") or 0),
    )
    out = []
    for f in scan.get("findings", []):
        out.append(
            finding(
                f"page-{f['id']}",
                f["title"],
                f["status"],
                f["summary"],
                f.get("why"),
                f.get("fix"),
                notes=list(f.get("notes") or []) + [ran],
                commands=f.get("commands", []),
                evidence=f.get("evidence", []),
                source="security-page",
                about=secdoctor_xref.about("security-page", f["id"]),
            )
        )
    return out


IMPORTS = STATE / "security-imports"


def step_imports(ctx):
    """OpenVAS and nmap reports imported on the page (secimports.py), kept until replaced: OpenVAS's
    results by port, a problem from CVSS 7, a warning from 4; nmap's open ports, a warning for one
    the box was not listening on when the report came in; and either one old, or from before the
    box's ports changed, a warning of its own."""
    out = []
    try:
        now_ports = sorted(
            {
                f"{listener['proto']}/{listener['port']}"
                for listener in json.loads(_read(CONTROL / "security.json") or "{}").get("listeners", [])
            }
        )
    except (ValueError, KeyError, TypeError):
        now_ports = None
    for kind in ("openvas", "nmap"):
        try:
            rep = json.loads(_read(IMPORTS / f"{kind}.json", limit=4 << 20) or "")
        except ValueError:
            rep = None
        if not rep:
            continue
        stamp = rep.get("ran") or rep.get("imported") or 0
        ran = M("imports.when", "The scan ran on {date}.", date=when(stamp))
        again = M(
            "imports.again.fix",
            "Run {scanner} against the box again, and import its report.",
            scanner=kind,
        )
        by_port = {}
        for r in rep.get("results", []):
            if kind == "openvas" and r.get("cvss", 0) <= 0:
                continue
            by_port.setdefault((r["proto"], r["port"]), []).append(r)
        for (proto, port), rs in sorted(by_port.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            about = {"kind": "service", "key": f"{proto}/{port}"} if isinstance(port, int) else None
            where = f"{proto.upper()} {port}" if isinstance(port, int) else None
            if kind == "openvas":
                worst = max(r["cvss"] for r in rs)
                status = "problem" if worst >= 7 else "warn" if worst >= 4 else "ok"
                ranked = sorted(rs, key=lambda r: -r["cvss"])
                fix = next((r["solution"] for r in ranked if r.get("solution")), "")
                out.append(
                    finding(
                        f"openvas-{proto}-{port}",
                        (
                            named(where, M("imports.role.openvas", "OpenVAS"))
                            if where
                            else M("imports.openvas.box", "OpenVAS: the box as a whole")
                        ),
                        status,
                        M(
                            "imports.openvas.results",
                            {
                                "one": "OpenVAS reported {n} result here.",
                                "other": "OpenVAS reported {n} results here.",
                            },
                            n=len(rs),
                        ),
                        fix=(
                            M(
                                "imports.openvas.solution",
                                "OpenVAS suggests: {solution}",
                                solution=fix,
                            )
                            if fix
                            else None
                        ),
                        notes=[ran],
                        evidence=[f"{r['name']} (CVSS {r['cvss']:g})" for r in ranked[:5]],
                        source="openvas",
                        about=about,
                    )
                )
            else:
                seen = f"{proto}/{port}"
                listening = rep.get("box_ports")
                unexpected = listening is not None and seen not in listening
                svc = next((r["service"] for r in rs if r.get("service")), "")
                title = named(where or seen, M("imports.role.nmap", "nmap"))
                if unexpected:
                    out.append(
                        finding(
                            f"nmap-{proto}-{port}",
                            title,
                            "warn",
                            M(
                                "imports.nmap.unexpected",
                                "nmap found this port open, but the box was not listening on it "
                                "when the report was imported.",
                            ),
                            M(
                                "imports.nmap.unexpected.why",
                                "Something else answered on the box's address, or the box "
                                "listened there for a while only.",
                            ),
                            M(
                                "imports.nmap.unexpected.fix",
                                "Find out what answered. Run nmap again to see if the port is still open.",
                            ),
                            notes=[ran],
                            evidence=[svc] if svc else [],
                            source="nmap",
                            about=about,
                        )
                    )
                else:
                    out.append(
                        finding(
                            f"nmap-{proto}-{port}",
                            title,
                            "ok",
                            M(
                                "imports.nmap.expected",
                                "nmap found this port open, as the box serves it.",
                            ),
                            notes=[ran],
                            evidence=[svc] if svc else [],
                            source="nmap",
                            about=about,
                        )
                    )
        age = (time.time() - stamp) / 86400
        changed = now_ports is not None and rep.get("box_ports") is not None and sorted(rep["box_ports"]) != now_ports
        if changed:
            gained = sorted(set(now_ports) - set(rep["box_ports"]))
            lost = sorted(set(rep["box_ports"]) - set(now_ports))
            out.append(
                finding(
                    f"{kind}-ports-changed",
                    named(
                        kind,
                        M("imports.role.changed", "listener changes since the scan"),
                    ),
                    "warn",
                    M(
                        "imports.changed",
                        "The ports of the box have changed since the {scanner} report was imported.",
                        scanner=kind,
                    ),
                    M(
                        "imports.changed.why",
                        "The report does not cover the ports that the box opened since.",
                    ),
                    again,
                    notes=[ran],
                    evidence=[f"+ {p}" for p in gained] + [f"- {p}" for p in lost],
                    source=kind,
                )
            )
        elif age > 90:
            out.append(
                finding(
                    f"{kind}-old",
                    named(kind, M("imports.role.old", "scan age")),
                    "warn",
                    M(
                        "imports.old",
                        {
                            "one": "The {scanner} report is {n} day old.",
                            "other": "The {scanner} report is {n} days old.",
                        },
                        n=int(age),
                        scanner=kind,
                    ),
                    M(
                        "imports.old.why",
                        "The box and its software can have changed since.",
                    ),
                    again,
                    notes=[ran],
                    source=kind,
                )
            )
    if not out:
        out.append(
            finding(
                "imports-none",
                M("imports.title", "Imported scans"),
                "ok",
                M("imports.none", "No scan is imported yet."),
                notes=[
                    M(
                        "imports.none.how",
                        "From a computer on the hotspot, run OpenVAS (Greenbone) or nmap against the box, "
                        "and import its report here. That gives the view from outside, software that "
                        "debsecan cannot see included.",
                    ),
                    M(
                        "imports.scope",
                        "A scan from outside does not see every app, or programs that do not listen.",
                    ),
                ],
                source="openvas",
            )
        )
    return out


KIND_SOURCES = {"service": ("security-page", "nmap", "openvas")}


def freshness():
    """Each source's date and its data's date."""
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


def _parts(f):
    """What a finding says, part by part (finding.py), for the joint report's items and lines."""
    return {
        "summary": f.get("summary"),
        "why": f.get("why"),
        "fix": f.get("fix"),
        "notes": list(f.get("notes") or []),
        "evidence": list(f.get("evidence") or []),
        "commands": list(f.get("commands") or []),
    }


def msg_key(m):
    """A message's ID, or a plain string as it is: what sorting and matching use, never the words."""
    return m.get("id") if isinstance(m, dict) else str(m)


def joint(steps, freshness=None):
    """The joint report: every finding that is not fine, once. Findings about
    the same thing (a setting, a port, Debian's security updates) are merged into one item saying which
    sources agree, the worst status, and what each source said; the rest stand as items of their own. Each
    item carries where it is put right (secdoctor_xref.DO) and, for the deep audit's CIS sections, a
    tier: "suggest" or "not-here", counted apart from what is to fix or look at. Also: items only one
    source saw where others could have (worth a look, or a false positive); each source's counts, what
    it covers, and how fresh it is; and the counts after merging, which the page's badge shows. An item
    says what its leading line says (the worst; of those, the first with a fix), part by part.
    """
    rank = {"ok": 0, "warn": 1, "problem": 2}
    items, sources = {}, {}
    for st in steps:
        for f in st["findings"]:
            src = f.get("source", "doctor")
            c = sources.setdefault(src, {"problem": 0, "warn": 0, "ok": 0})
            c[f["status"]] = c.get(f["status"], 0) + 1
            if f.get("accepted"):
                continue
            # Looked up again for a finding kept from before the table knew it (the Security page's
            # ids are the page's own, without "page-").
            a = f.get("about") or secdoctor_xref.about(
                src,
                f["id"].removeprefix("page-") if src == "security-page" else f["id"],
            )
            if not a:
                if f["status"] == "ok":
                    continue
                a = {"kind": "finding", "key": f"{src}:{f['id']}"}
            key = f"{a['kind']}:{a['key']}"
            if a["kind"] == "setting" and a["key"] in secdoctor_xref.XREF:
                label = secdoctor_xref.title(a["key"])
            elif a["kind"] == "service":
                proto, _, port = a["key"].partition("/")
                label = (
                    M(
                        "joint.service",
                        "{title} ({proto} {port})",
                        title=f["title"],
                        proto=proto.upper(),
                        port=port,
                    )
                    if port
                    else f["title"]
                )
            else:
                label = f["title"]
            it = items.setdefault(
                key,
                {
                    "key": key,
                    "about": a,
                    "title": label,
                    "sources": [],
                    "status": "ok",
                    "titles": [],
                    "lines": [],
                    **_parts({}),
                    "lead": None,
                    "cmd": "",
                    "do": None,
                    "area": st.get("title", ""),
                    "tiers": [],
                    "checks": [],
                    "info": None,
                },
            )
            if src not in it["sources"]:
                it["sources"].append(src)
            it["titles"].append(f["title"])
            it["lines"].append(
                {
                    "source": src,
                    "id": f["id"],
                    "title": f["title"],
                    "status": f["status"],
                    **_parts(f),
                }
            )
            if f["status"] != "ok":
                # Information only (secdoctor_xref.INFO): nothing the administrator can change puts it right.
                why = secdoctor_xref.info(src, f["id"])
                if why:
                    it["info"] = it["info"] or why
                it["tiers"].append(f.get("tier") or ("info" if why else ""))
                # The deep audit's checks one by one, each with its own fix (deepaudit.cis_findings).
                it["checks"] += [dict(c, source=src) for c in f.get("checks") or []]
                # A finding with one check of its own: that check's command, or its words, are the item's.
                own = f.get("checks") or []
                it["cmd"] = it["cmd"] or (own[0].get("cmd", "") if len(own) == 1 else "")
                if not f.get("fix") and len(own) == 1 and own[0].get("say"):
                    f = dict(f, fix=own[0]["say"])
                it["do"] = it["do"] or secdoctor_xref.do_for(src, f["id"])
            lead = it["lead"]
            if (
                lead is None
                or rank[f["status"]] > rank[lead["status"]]
                or (f.get("fix") and not lead.get("fix") and f["status"] == lead["status"])
            ):
                if lead is None or rank[f["status"]] > rank[lead["status"]]:
                    it["area"] = st.get("title", "")
                it["lead"] = f
                it["status"] = max((it["status"], f["status"]), key=rank.get)
    # A source ran when it said something real, not only "not run yet" / "none imported".
    ran = {f.get("source", "doctor") for s in steps for f in s["findings"] if not f["id"].endswith("-none")}
    for it in items.values():
        it.update(_parts(it.pop("lead") or {}))
        # Who could have said the same: any port-seeing source for a port; for a setting, the
        # sources the cross-reference table lists for it.
        a = it["about"]
        could = (
            set(KIND_SOURCES["service"])
            if a["kind"] == "service"
            else (
                {k for k, v in secdoctor_xref.XREF.get(a["key"], {}).items() if isinstance(v, list)}
                if a["kind"] == "setting"
                else set()
            )
        )
        could &= ran
        it["alone"] = len(it["sources"]) == 1 and len(could - set(it["sources"])) > 0
        it["could_see"] = sorted(could - set(it["sources"]))
        # The Security page's lines in it: the page offers their buttons (from its scan at the time).
        it["page"] = [entry["id"].removeprefix("page-") for entry in it["lines"] if entry["source"] == "security-page"]
        # A tier only when every line not fine has one: a CIS suggestion merged with a real finding is real.
        tiers = it.pop("tiers")
        it["tier"] = (tiers[0] if len(set(tiers)) == 1 else "suggest") if tiers and all(tiers) else None
    # Together a problem (secdoctor_xref.COMPOUND): each named finding present and not ok.
    present = {(f.get("source", "doctor"), f["id"]): f for s in steps for f in s["findings"]}
    for c in secdoctor_xref.COMPOUND:
        if all((src, i) in present and present[(src, i)]["status"] != "ok" for src, i in c["needs"]):
            lines = [
                {
                    "source": src,
                    "id": i,
                    "title": present[(src, i)]["title"],
                    "status": present[(src, i)]["status"],
                    **_parts(present[(src, i)]),
                }
                for src, i in c["needs"]
            ]
            items[f"compound:{c['key']}"] = {
                "key": f"compound:{c['key']}",
                "about": {"kind": "compound", "key": c["key"]},
                "title": c["title"],
                "sources": sorted({s for s, _ in c["needs"]}),
                "status": "problem",
                "titles": [present[(s, i)]["title"] for s, i in c["needs"]],
                "lines": lines,
                **_parts({"summary": c["summary"], "why": c["why"], "fix": c["fix"]}),
                "cmd": "",
                "do": None,
                "area": M("joint.together", "Together"),
                "tier": None,
                "page": [i.removeprefix("page-") for _, i in c["needs"]],
                "alone": False,
                "could_see": [],
                "checks": [],
                "info": None,
            }
    tier_rank = {None: 0, "suggest": 1, "info": 2, "not-here": 3}
    order = {sid: n for n, (sid, *_rest) in enumerate(STEPS)}
    area_of = {msg_key(t): order.get(sid, 99) for sid, t, *_rest in STEPS}
    merged = sorted(
        (i for i in items.values() if i["status"] != "ok"),
        key=lambda i: (
            tier_rank[i["tier"]],
            -rank[i["status"]],
            area_of.get(msg_key(i["area"]), 99),
            i["key"],
        ),
    )
    agreed = [i for i in items.values() if i["status"] == "ok" and len(i["sources"]) > 1]
    real = [i for i in merged if not i["tier"]]
    after = {
        "problem": sum(1 for i in real if i["status"] == "problem"),
        "warn": sum(1 for i in real if i["status"] == "warn"),
        "suggest": sum(1 for i in merged if i["tier"] == "suggest"),
        "info": sum(1 for i in merged if i["tier"] == "info"),
        "not_here": sum(1 for i in merged if i["tier"] == "not-here"),
    }
    return {
        "items": merged,
        "agreed_ok": len(agreed),
        "sources": sources,
        "after": after,
        "coverage": {s: secdoctor_xref.COVERAGE.get(s, "") for s in sources},
        "freshness": freshness or {},
    }


def _local_headers(url):
    """A local request's status and headers (the box's own front), or None. Certificates unchecked:
    this asks what the front sends, not whether it is trusted. A GET whose body is never read: the
    hub answers HEAD with 501, which is not "not answering"."""
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
    """HTTPS: the certificate's expiry, that it
    covers the box's addresses, the CA's name constraints, the keys private, and, while HTTPS is
    on, no HSTS and port 80 still answering (the captive portal needs it)."""
    from irate_box.root import tls

    about = {"kind": "setting", "key": "tls"}
    st = tls.status()
    renew = [f"sudo {CODE}/irate-box tls renew"]
    if not st.get("set_up"):
        return [
            finding(
                "tls",
                M("tls.title", "HTTPS"),
                "warn",
                M(
                    "tls.none",
                    "The box has no certificate, so every page is plain HTTP.",
                ),
                M(
                    "tls.none.why",
                    "The administrator password crosses the WiFi unencrypted.",
                ),
                M(
                    "tls.none.fix",
                    "On Security, HTTPS, make the box's certificate. Then install it on your own devices from "
                    "/certificate.",
                ),
                ref="S2",
                about=about,
            )
        ]
    out = []
    c = st.get("cert") or {}
    days = (c.get("expires", 0) - time.time()) / 86400
    expiry_t = M("tls.expiry.title", "Certificate expiry")
    status = "problem" if days < 7 else "warn" if days < 30 else "ok"
    fix = (
        None
        if days >= 30
        else (
            M("tls.expiry.import", "On Security, HTTPS, import a renewed certificate.")
            if c.get("own")
            else M(
                "tls.expiry.renew",
                "On Security, HTTPS, renew the certificate. Or run this command.",
            )
        )
    )
    out.append(
        finding(
            "tls-expiry",
            expiry_t,
            status,
            (
                M("tls.expiry.expired", "The certificate has expired.")
                if days < 0
                else M(
                    "tls.expiry.left",
                    {
                        "one": "The certificate expires in {n} day.",
                        "other": "The certificate expires in {n} days.",
                    },
                    n=int(days),
                )
            ),
            (
                M(
                    "tls.expiry.why",
                    "Devices refuse an expired certificate, and HTTPS then stops working.",
                )
                if status != "ok"
                else None
            ),
            fix,
            notes=[
                (
                    M(
                        "tls.expiry.own",
                        "It is a certificate that an administrator imported. The box does not renew it.",
                    )
                    if c.get("own")
                    else M(
                        "tls.expiry.auto",
                        "The box renews its own certificate when two thirds of its life have passed.",
                    )
                )
            ],
            commands=renew if fix and not c.get("own") else [],
            about=about,
        )
    )
    covers_t = M("tls.covers.title", "Certificate names and addresses")
    if st.get("outside"):
        out.append(
            finding(
                "tls-covers",
                covers_t,
                "problem",
                M(
                    "tls.covers.outside",
                    "The box has addresses that its certificate authority does not cover.",
                ),
                M("tls.covers.outside.why", "Devices refuse HTTPS on those addresses."),
                M(
                    "tls.covers.outside.fix",
                    "On Security, HTTPS, make a new certificate authority for this network. Then "
                    "install it again on each device.",
                ),
                evidence=st["outside"],
                about=about,
            )
        )
    elif st.get("due"):
        out.append(
            finding(
                "tls-covers",
                covers_t,
                "warn",
                M(
                    "tls.covers.due",
                    "The certificate is due to be made again, because the box's names or addresses changed.",
                ),
                fix=M(
                    "tls.covers.due.fix",
                    "The uplink watchdog makes it again within six hours. To do it now, run this command.",
                ),
                commands=renew,
                evidence=[str(st["due"])],
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "tls-covers",
                covers_t,
                "ok",
                M(
                    "tls.covers.ok",
                    "The certificate covers the box's names and addresses.",
                ),
                evidence=c.get("names", []) + c.get("addresses", []),
                about=about,
            )
        )
    text = ""
    try:
        text = tls.openssl("x509", "-in", str(tls.CA_CERT), "-noout", "-text")
    except (RuntimeError, OSError):
        pass
    ca_t = M("tls.ca.title", "Certificate authority scope")
    if "X509v3 Name Constraints: critical" in text:
        out.append(
            finding(
                "tls-constraints",
                ca_t,
                "ok",
                M(
                    "tls.ca.limited",
                    "The certificate authority (CA) is limited to this box's names. A client that enforces the "
                    "limit rejects other sites.",
                ),
                notes=[M("tls.ca.not-all", "Not every client enforces the limit.")],
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "tls-constraints",
                ca_t,
                "problem",
                M(
                    "tls.ca.open",
                    "The certificate authority (CA) has no name constraints.",
                ),
                M(
                    "tls.ca.open.why",
                    "A device that installs it trusts it for any site.",
                ),
                M(
                    "tls.ca.open.fix",
                    "On Security, HTTPS, make a new certificate authority. Then install it again on each device.",
                ),
                about=about,
            )
        )
    private = st.get("keys_private") and tls.KEY.exists() and (tls.KEY.stat().st_mode & 0o007) == 0
    keys_t = M("tls.keys.title", "Certificate key permissions")
    if private:
        out.append(
            finding(
                "tls-keys",
                keys_t,
                "ok",
                M(
                    "tls.keys.ok",
                    "Only root can read the key of the certificate authority, and not every "
                    "account can read the server key.",
                ),
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "tls-keys",
                keys_t,
                "problem",
                M(
                    "tls.keys.open",
                    "Accounts that should not can read a key of the certificate.",
                ),
                M(
                    "tls.keys.why",
                    "With the CA key, someone can make certificates that the devices trust. With the server key, "
                    "someone can pass as the box.",
                ),
                M(
                    "tls.keys.fix",
                    "Make the keys readable by root, and the server key by its web server's group too.",
                ),
                commands=[
                    "sudo chmod 600 /etc/hub/tls/ca/ca.key",
                    "sudo chmod 640 /etc/hub/tls/server.key",
                ],
                about=about,
            )
        )
    if st.get("on"):
        got = _local_headers(f"https://127.0.0.1:{(st.get('ports') or {}).get('main', 443)}/")
        hsts_t = M("tls.hsts.title", "Strict-Transport-Security")
        if got is None:
            out.append(
                finding(
                    "tls-hsts",
                    hsts_t,
                    "warn",
                    M(
                        "tls.https.down",
                        "HTTPS is on, but the box's HTTPS port does not answer.",
                    ),
                    fix=M(
                        "tls.https.down.fix",
                        "Check the configuration of the web server, then reload it.",
                    ),
                    commands=["sudo nginx -t", "sudo systemctl reload nginx"],
                    about=about,
                )
            )
        elif any(k.lower() == "strict-transport-security" for k in got[1]):
            out.append(
                finding(
                    "tls-hsts",
                    hsts_t,
                    "problem",
                    M(
                        "tls.hsts.sent",
                        "The web server sends Strict-Transport-Security.",
                    ),
                    M(
                        "tls.hsts.why",
                        "A device that received the header uses only HTTPS for this box's name until the header "
                        "expires. Plain pages, such as the captive portal, then do not open on that device.",
                    ),
                    M(
                        "tls.hsts.fix",
                        "Remove the Strict-Transport-Security header from the web server's configuration.",
                    ),
                    about=about,
                )
            )
        else:
            out.append(
                finding(
                    "tls-hsts",
                    hsts_t,
                    "ok",
                    M(
                        "tls.hsts.none",
                        "The web server sends no Strict-Transport-Security, so plain HTTP keeps working.",
                    ),
                    about=about,
                )
            )
        plain = _local_headers(f"http://127.0.0.1:{tls.PLAIN_PORTS['main']}/")
        plain_t = M("tls.plain.title", "Plain HTTP")
        if st.get("transport") == "https":
            out.append(
                finding(
                    "tls-plain",
                    plain_t,
                    "problem" if plain else "ok",
                    (
                        M(
                            "tls.plain.unexpected",
                            "HTTP answers although it is disabled in the saved transport policy.",
                        )
                        if plain
                        else M(
                            "tls.plain.disabled",
                            "HTTP is disabled by the administrator. HTTP captive-portal discovery is unavailable.",
                        )
                    ),
                    about=about,
                )
            )
        elif plain and plain[0] < 500:
            out.append(
                finding(
                    "tls-plain",
                    plain_t,
                    "ok",
                    M(
                        "tls.plain.ok",
                        "Port 80 answers, as the sign-in sheet of the captive portal needs.",
                    ),
                    about=about,
                )
            )
        else:
            out.append(
                finding(
                    "tls-plain",
                    plain_t,
                    "problem",
                    M("tls.plain.down", "Port 80 does not answer."),
                    M(
                        "tls.plain.down.why",
                        "Phones on the hotspot get no sign-in sheet.",
                    ),
                    M("tls.plain.down.fix", "Check the state of the web server."),
                    commands=["systemctl status nginx"],
                    about=about,
                )
            )
    return out


MOSQUITTO = Path(os.environ.get("HUB_MOSQUITTO_DIR", "/etc/mosquitto"))


def step_mqtt(ctx):
    """The MQTT broker and the decoder bridge: never bridged to a
    broker outside the box (mqtt.meshtastic.org above all: the mesh's traffic would leave the box,
    and its downlink would come in); anonymous clients limited to msh/#; the bridge's channel keys
    private to the hub."""
    about = {"kind": "service", "key": "mosquitto"}
    confs = sorted(MOSQUITTO.glob("conf.d/*.conf")) + (
        [MOSQUITTO / "mosquitto.conf"] if (MOSQUITTO / "mosquitto.conf").exists() else []
    )
    if not confs:
        return [
            finding(
                "mqtt",
                M("mqtt.title", "MQTT broker"),
                "ok",
                M("mqtt.not-installed", "The MQTT broker is not installed."),
                about=about,
            )
        ]
    out = []
    lines = [
        (c.name, line.strip())
        for c in confs
        for line in (_read(c) or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    bridges = [f"{name}: {line}" for name, line in lines if re.match(r"(connection|address)\s", line)]
    bridge_t = M("mqtt.bridge.title", "Broker bridge")
    if bridges:
        out.append(
            finding(
                "mqtt-bridge",
                bridge_t,
                "problem",
                M(
                    "mqtt.bridge.on",
                    "The broker has a bridge to a broker outside the box.",
                ),
                M(
                    "mqtt.bridge.why",
                    "The mesh's traffic leaves the box, and what the other broker sends comes in to the radios.",
                ),
                M(
                    "mqtt.bridge.fix",
                    "Remove the connection and address lines of the bridge from /etc/mosquitto.",
                ),
                evidence=bridges[:3],
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "mqtt-bridge",
                bridge_t,
                "ok",
                M("mqtt.bridge.none", "The broker has no bridge to another broker."),
                notes=[
                    M(
                        "mqtt.bridge.radios",
                        "Mesh traffic can still leave through clients and radios that send it elsewhere.",
                    )
                ],
                about=about,
            )
        )
    acl = next(
        (line.split(None, 1)[1] for _, line in lines if line.startswith("acl_file ")),
        None,
    )
    anon = any(line == "allow_anonymous true" for _, line in lines)
    acl_text = _read(Path(acl)) if acl else None
    limited = (
        bool(acl_text)
        and "topic readwrite msh/#" in acl_text
        and not re.search(r"^\s*topic\s+(readwrite|write|read)\s+#\s*$", acl_text, re.M)
    )
    acl_t = M("mqtt.acl.title", "Anonymous topic access")
    if not anon:
        out.append(
            finding(
                "mqtt-acl",
                acl_t,
                "ok",
                M("mqtt.acl.none", "The broker refuses clients with no sign-in."),
                ref="S9",
                about=about,
            )
        )
    elif limited:
        out.append(
            finding(
                "mqtt-acl",
                acl_t,
                "ok",
                M(
                    "mqtt.acl.limited",
                    "Clients with no sign-in can publish and subscribe only under msh/#.",
                ),
                ref="S9",
                about=about,
            )
        )
    else:
        out.append(
            finding(
                "mqtt-acl",
                acl_t,
                "warn",
                M("mqtt.acl.open", "Clients with no sign-in are not limited to msh/#."),
                M(
                    "mqtt.acl.open.why",
                    "Anything on the network can use the broker to pass any message.",
                ),
                M(
                    "mqtt.acl.open.fix",
                    "Install the MQTT add-on again. That restores its access list.",
                ),
                evidence=[acl] if acl else [],
                ref="S9",
                about=about,
            )
        )
    keys = STATE / "mesh" / "channels.json"
    if keys.exists():
        keys_t = M("mqtt.keys.title", "Decoder key permissions")
        if (keys.stat().st_mode & 0o077) == 0:
            out.append(
                finding(
                    "mqtt-keys",
                    keys_t,
                    "ok",
                    M(
                        "mqtt.keys.ok",
                        "Only the hub can read the channel keys of the decoder.",
                    ),
                    about=about,
                )
            )
        else:
            out.append(
                finding(
                    "mqtt-keys",
                    keys_t,
                    "problem",
                    M(
                        "mqtt.keys.open",
                        "Accounts other than the hub can read the channel keys of the decoder.",
                    ),
                    M(
                        "mqtt.keys.why",
                        "The key of a private channel lets them read its messages.",
                    ),
                    M("mqtt.keys.fix", "Make the file readable by the hub only."),
                    commands=[f"sudo chmod 600 {keys}"],
                    evidence=[str(keys)],
                    about=about,
                )
            )
    return out


# --- the image underneath ------------------------------------------------------------------------
# What Armbian and mPWRD-OS ship that the hub inherited and nothing else here looks at. Sudo
# rules, root's groups, apt's periodic switch and SSH's defaults have steps and offers of their
# own (accounts, the Security page).
APT_DIR = Path(os.environ.get("HUB_APT_DIR", "/etc/apt"))
PROC_VERSION = Path(os.environ.get("HUB_PROC_VERSION", "/proc/version"))
NM_DIR = Path(os.environ.get("HUB_NM_DIR", "/etc/NetworkManager/system-connections"))
DEBIAN_KEYS = ("debian-archive-", "debian-ports-archive-")


def _apt_sources(apt_dir):
    """[(file, uri, suite, signed)] from sources.list, *.list (one line each: signed when the
    options carry signed-by) and *.sources (deb822: signed when the stanza has Signed-By).
    """
    out = []
    files = (
        ([apt_dir / "sources.list"] if (apt_dir / "sources.list").is_file() else [])
        + sorted((apt_dir / "sources.list.d").glob("*.list"))
        + sorted((apt_dir / "sources.list.d").glob("*.sources"))
        if apt_dir.is_dir()
        else []
    )
    for f in files:
        text = _read(f) or ""
        if f.suffix == ".sources":
            for stanza in re.split(r"\n\s*\n", text):
                fields = {
                    k.strip().lower(): v.strip()
                    for k, _, v in (
                        line.partition(":") for line in stanza.splitlines() if ":" in line and not line.startswith("#")
                    )
                }
                if fields.get("uris"):
                    out.append(
                        (
                            f.name,
                            fields["uris"].split()[0],
                            fields.get("suites", ""),
                            bool(fields.get("signed-by")),
                        )
                    )
        else:
            for line in text.splitlines():
                m = re.match(r"^\s*deb(?:-src)?\s+(?:\[([^\]]*)\]\s+)?(\S+)\s+(\S*)", line)
                if m:
                    out.append(
                        (
                            f.name,
                            m.group(2),
                            m.group(3),
                            "signed-by=" in (m.group(1) or ""),
                        )
                    )
    return out


def step_image(ctx):
    out = []
    # apt's trust: a key in trusted.gpg.d vouches for every repository, not its own.
    trusted = APT_DIR / "trusted.gpg.d"
    keys = (
        sorted(p.name for p in trusted.iterdir() if p.is_file() and not p.name.startswith(DEBIAN_KEYS))
        if trusted.is_dir()
        else []
    )
    sources = _apt_sources(APT_DIR)
    unsigned = [f"{f}: {uri}" for f, uri, _, signed in sources if not signed]
    apt_t = M("image.apt.title", "Repository signing-key scope")
    if keys or unsigned:
        out.append(
            finding(
                "image-apt-trust",
                apt_t,
                "warn",
                (
                    M(
                        "image.apt.keys",
                        "Keys in trusted.gpg.d can sign packages for any repository, Debian's included.",
                    )
                    if keys
                    else M(
                        "image.apt.unsigned",
                        "Some package sources name no key of their own (Signed-By).",
                    )
                ),
                M(
                    "image.apt.why",
                    "A key that leaks, or a repository that turns bad, can then sign packages that replace Debian's.",
                ),
                M(
                    "image.apt.fix",
                    "Move each key to /usr/share/keyrings, and name it in the Signed-By of its source. The key "
                    "then vouches for that repository only.",
                ),
                notes=(
                    [
                        M(
                            "image.apt.unsigned",
                            "Some package sources name no key of their own (Signed-By).",
                        )
                    ]
                    if keys and unsigned
                    else []
                ),
                evidence=_some(keys + unsigned),
            )
        )
    else:
        out.append(
            finding(
                "image-apt-trust",
                apt_t,
                "ok",
                M(
                    "image.apt.ok",
                    "Each package source names its own key, and trusted.gpg.d holds only Debian's keys.",
                ),
            )
        )
    daily = [
        f"{f}: {uri} {suite}".strip()
        for f, uri, suite, _ in sources
        if re.search(r"daily|nightly|snapshot", f"{uri} {suite}", re.I)
    ]
    if daily:
        out.append(
            finding(
                "image-apt-daily",
                M("image.daily.title", "Release channel"),
                "warn",
                M(
                    "image.daily",
                    "A package source follows a daily or nightly channel.",
                ),
                M(
                    "image.daily.why",
                    "The newest build of that project comes to the box at the next upgrade, before a "
                    "release has had wider testing.",
                ),
                M(
                    "image.daily.fix",
                    "Use a release channel (stable or beta), unless you want the daily builds.",
                ),
                evidence=_some(daily),
            )
        )
    # The kernel: a vendor build has no Debian security tracking (debsecan says so in its coverage; here with its age).
    ver = _read(PROC_VERSION, 4096) or ""
    built = re.search(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\w{3}) +(\d+) [\d:]+ \w+ (\d{4})", ver)
    release = ver.split()[2] if len(ver.split()) > 2 else "?"
    kernel_t = M("image.kernel.title", "Vendor kernel")
    if ver and ("vendor" in release or "armbian" in ver.lower()):
        made = f"{built.group(3)}-{built.group(1)}-{built.group(2).zfill(2)}" if built else None
        out.append(
            finding(
                "image-kernel",
                kernel_t,
                "warn",
                (
                    M(
                        "image.kernel.vendor",
                        "The box runs a vendor kernel, built on {date}.",
                        date=made,
                    )
                    if made
                    else M("image.kernel.vendor-undated", "The box runs a vendor kernel.")
                ),
                M(
                    "image.kernel.why",
                    "Debian's security tracker does not cover it, so debsecan cannot see its vulnerabilities. "
                    "Its fixes come only with a new image or kernel package from its maker.",
                ),
                M(
                    "image.kernel.fix",
                    "When the box is online, keep the kernel package current from its maker's repository.",
                ),
                commands=["apt list --upgradable 2>/dev/null | grep linux-image"],
                evidence=[release],
            )
        )
    elif ver:
        out.append(
            finding(
                "image-kernel",
                kernel_t,
                "ok",
                M("image.kernel.debian", "The kernel is not a vendor build."),
                evidence=[release],
            )
        )
    # Radios and daemons the hub does not use.
    bt_t = M("image.bluetooth.title", "Bluetooth")
    if _show("bluetooth.service", "ActiveState").get("ActiveState") == "active":
        out.append(
            finding(
                "image-bluetooth",
                bt_t,
                "warn",
                M(
                    "image.bluetooth.on",
                    "Bluetooth is running, and the hub does not use it.",
                ),
                M(
                    "image.bluetooth.why",
                    "It is a listener with a stack of its own, and anyone in radio range can reach it.",
                ),
                M(
                    "image.bluetooth.fix",
                    "If nothing on the box uses Bluetooth, disable it. It stops now and at boot, and you "
                    "can enable it again.",
                ),
            )
        )
    else:
        out.append(
            finding(
                "image-bluetooth",
                bt_t,
                "ok",
                M("image.bluetooth.off", "Bluetooth is not running."),
            )
        )
    # Access-point profiles the image left in NetworkManager, beside irate-box's own.
    aps, names = [], []
    for f in sorted(NM_DIR.glob("*.nmconnection")) if NM_DIR.is_dir() else []:
        text = _read(f, 65536) or ""
        if re.search(r"(?m)^mode=ap\s*$", text) and "irate-box" not in text:
            name = (re.search(r"(?m)^id=(.*)$", text) or [None, f.stem])[1]
            open_ = "[wifi-security]" not in text
            auto = not re.search(r"(?m)^autoconnect=false\s*$", text)
            aps.append(f"{name}: wifi-security={'no' if open_ else 'yes'} autoconnect={'yes' if auto else 'no'}")
            names.append(name)
    ap_t = M("image.ap.title", "Access-point profiles")
    if aps:
        out.append(
            finding(
                "image-ap-profiles",
                ap_t,
                "warn",
                M(
                    "image.ap.found",
                    {
                        "one": "NetworkManager has {n} access-point profile that is not the hub's.",
                        "other": "NetworkManager has {n} access-point profiles that are not the hub's.",
                    },
                    n=len(aps),
                ),
                M(
                    "image.ap.why",
                    "The image set them up. One can come up beside the hub's hotspot, or in its place.",
                ),
                M(
                    "image.ap.fix",
                    "Delete each profile that is not yours. The hub's hotspot is not among them.",
                ),
                commands=[f"sudo nmcli connection delete {shlex.quote(n)}" for n in names],
                evidence=aps,
            )
        )
    else:
        out.append(
            finding(
                "image-ap-profiles",
                ap_t,
                "ok",
                M(
                    "image.ap.none",
                    "NetworkManager has no access-point profile but the hub's own.",
                ),
            )
        )
    return out


APT_LISTS = Path(os.environ.get("HUB_APT_LISTS", "/var/lib/apt/lists"))


def _age_days(path):
    try:
        return (time.time() - Path(path).stat().st_mtime) / 86400
    except OSError:
        return None


def step_offline(ctx):
    """Offline readiness: the box is meant to work with no
    internet, so how stale what it carries is: the package lists, Debian's security tracker data,
    the kits' cache, the offline bundle against the installed version, the hub's own update state.
    """
    out = []
    lists, tracker = _age_days(APT_LISTS), _age_days(DEBSECAN_FEED / (_codename() or "x"))
    ages = [
        (
            M("offline.age.lists.never", "The package lists were never downloaded.")
            if lists is None
            else M(
                "offline.age.lists",
                {
                    "one": "The package lists are {n} day old.",
                    "other": "The package lists are {n} days old.",
                },
                n=int(lists),
            )
        ),
        (
            M(
                "offline.age.tracker.never",
                "The data of Debian's security tracker was never downloaded.",
            )
            if tracker is None
            else M(
                "offline.age.tracker",
                {
                    "one": "The data of Debian's security tracker is {n} day old.",
                    "other": "The data of Debian's security tracker is {n} days old.",
                },
                n=int(tracker),
            )
        ),
    ]
    stale = any(v is None or v > 30 for v in (lists, tracker))
    data_t = M("offline.lists.title", "Package data age")
    if stale:
        out.append(
            finding(
                "offline-lists",
                data_t,
                "warn",
                M(
                    "offline.lists.stale",
                    "Some of what the box knows about packages is more than 30 days old, or missing.",
                ),
                M(
                    "offline.lists.why",
                    "Without fresh data, the box cannot tell which updates and fixes exist.",
                ),
                M(
                    "offline.lists.fix",
                    "Let the box reach the internet now and then. Or bring the updates on a USB drive, on Toolkits.",
                ),
                notes=ages,
            )
        )
    else:
        out.append(
            finding(
                "offline-lists",
                data_t,
                "ok",
                M(
                    "offline.lists.fresh",
                    "What the box knows about packages is less than 30 days old.",
                ),
                notes=ages,
            )
        )
    cache_t = M("offline.kits.title", "Cache completeness")
    try:
        from irate_box.root import kits

        bad, error = kits.verify(), None
    except Exception as exc:  # noqa: BLE001
        bad, error = [], f"{type(exc).__name__}: {exc}"
    scope = M(
        "offline.kits.scope",
        "The check covers the files that each toolkit's manifest lists.",
    )
    if error:
        out.append(
            finding(
                "offline-kits",
                cache_t,
                "warn",
                M(
                    "offline.kits.unchecked",
                    "The doctor could not check the toolkit caches.",
                ),
                evidence=[error],
            )
        )
    elif bad:
        out.append(
            finding(
                "offline-kits",
                cache_t,
                "warn",
                M(
                    "offline.kits.damaged",
                    "Some files in the toolkit caches are missing, or do not match their manifest.",
                ),
                M(
                    "offline.kits.why",
                    "The toolkits that hold them cannot be installed with no internet connection.",
                ),
                M(
                    "offline.kits.fix",
                    "On Toolkits, refresh each toolkit named while the box has an internet connection.",
                ),
                notes=[scope],
                evidence=_some(bad, 4),
            )
        )
    else:
        out.append(
            finding(
                "offline-kits",
                cache_t,
                "ok",
                M("offline.kits.ok", "Each cached toolkit has all its files."),
                notes=[scope],
            )
        )
    try:
        kit = json.loads(_read(STATE / "kits" / "kit.json") or "null")
    except ValueError:
        kit = None
    ver_text = _read(CODE / "VERSION") or ""
    version = ver_text.split()[0] if ver_text.split() else "unknown"
    bundle_t = M("offline.bundle.title", "Offline kit")
    if not kit:
        out.append(
            finding(
                "offline-bundle",
                bundle_t,
                "warn",
                M("offline.bundle.none", "No offline kit is made yet."),
                M(
                    "offline.bundle.why",
                    "With the offline kit, you can set up another box with no internet connection.",
                ),
                M(
                    "offline.bundle.fix",
                    "On Updates and backup, Content export, make the offline kit. Keep a copy off the box.",
                ),
            )
        )
    else:
        current = version != "unknown" and version in str(kit.get("name", ""))
        made = M("offline.bundle.made", "It was made on {date}.", date=when(kit.get("at", 0)))
        if current:
            out.append(
                finding(
                    "offline-bundle",
                    bundle_t,
                    "ok",
                    M(
                        "offline.bundle.current",
                        "The offline kit is of the version installed here.",
                    ),
                    notes=[made],
                    evidence=[str(kit.get("name"))],
                )
            )
        else:
            out.append(
                finding(
                    "offline-bundle",
                    bundle_t,
                    "warn",
                    M(
                        "offline.bundle.old",
                        "The offline kit is of another version than the one installed here.",
                    ),
                    M(
                        "offline.bundle.old.why",
                        "A box set up from it does not run what this box runs.",
                    ),
                    M(
                        "offline.bundle.again",
                        "On Updates and backup, Content export, make the offline kit again.",
                    ),
                    notes=[made],
                    evidence=[str(kit.get("name")), f"VERSION {version}"],
                )
            )
    try:
        upd = json.loads(_read(CONTROL / "update.json") or "null")
    except ValueError:
        upd = None
    if upd and upd.get("fetched"):
        age = (time.time() - upd["fetched"]) / 86400
        then = (
            M("offline.update.current", "It was up to date then.")
            if upd.get("up_to_date")
            else M(
                "offline.update.available",
                "Version {version} was available then.",
                version=upd.get("available"),
            )
        )
        out.append(
            finding(
                "offline-update",
                M("offline.update.title", "Last update check"),
                "ok" if age <= 30 else "warn",
                M(
                    "offline.update.age",
                    {
                        "one": "The hub last checked for an update {n} day ago.",
                        "other": "The hub last checked for an update {n} days ago.",
                    },
                    n=int(age),
                ),
                (
                    None
                    if age <= 30
                    else M(
                        "offline.update.why",
                        "Fixes that came out since then are not known to the box.",
                    )
                ),
                (
                    None
                    if age <= 30
                    else M(
                        "offline.update.fix",
                        "When the box is online, check for updates on Updates.",
                    )
                ),
                notes=[then],
            )
        )
    return out


# --- reached from the internet --------------------------------------------------------------------
# The box is for its own networks. Its web pages are seen from the hub's own note of public visitors
# (hub/reach.py; the web servers keep no access logs), SSH from sshd's log, and IPv6 from the box's
# own addresses: a global one is reachable wherever the router's IPv6 firewall lets traffic in.
REACH_DAYS = 7
SSH_FROM = re.compile(r"\b(?:from|by)(?: (?:invalid |authenticating )?user \S+)? ([0-9A-Fa-f:.]+) port \d+")


def _public(addr):
    import ipaddress

    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def _ssh_public(days=REACH_DAYS):
    """{address: (accepted, other)} from sshd's log, public addresses only; None if unreadable."""
    try:
        r = subprocess.run(  # noqa: TID251
            (
                "journalctl",
                "-q",
                "--no-pager",
                "-o",
                "cat",
                "--since",
                f"-{days}d",
                "-u",
                "ssh.service",
                "-u",
                "sshd.service",
                "-u",
                "ssh.socket",
            ),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    seen = {}
    for line in r.stdout.splitlines():
        m = SSH_FROM.search(line)
        if m and _public(m.group(1)):
            ok, other = seen.get(m.group(1), (0, 0))
            seen[m.group(1)] = (ok + 1, other) if line.startswith("Accepted ") else (ok, other + 1)
    return seen


def _ssh_log_since(days=REACH_DAYS):
    """The date of sshd's oldest journal line within the window, or "" if none can be read."""
    try:
        p = subprocess.Popen(  # noqa: TID251
            (
                "journalctl",
                "-q",
                "--no-pager",
                "-o",
                "short-iso",
                "--since",
                f"-{days}d",
                "-u",
                "ssh.service",
                "-u",
                "sshd.service",
                "-u",
                "ssh.socket",
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        first = p.stdout.readline()
        p.kill()
        p.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", first)
    return m.group(1).replace("T", " ") if m else ""


def _global_v6():
    try:
        r = subprocess.run(  # noqa: TID251
            ("ip", "-6", "-o", "addr", "show", "scope", "global"),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in r.stdout.splitlines():
        f = line.split()
        if len(f) > 3 and f[2] == "inet6" and "deprecated" not in f and _public(f[3].split("/")[0]):
            out.append(f"{f[3].split('/')[0]} ({f[1]})")
    return out


def step_internet(ctx):
    out, now = [], time.time()
    try:
        nets = json.loads(_read(STATE / "public-visits.json") or "{}").get("nets", {})
        nets = {
            n: e
            for n, e in nets.items()
            if isinstance(e, dict) and isinstance(e.get("last"), (int, float)) and now - e["last"] < REACH_DAYS * 86400
        }
    except (ValueError, AttributeError):
        nets = {}
    web_t = M("reach.web.title", "Requests from public addresses")
    if nets:
        recent = sorted(nets.items(), key=lambda kv: kv[1]["last"], reverse=True)
        out.append(
            finding(
                "reach-web",
                web_t,
                "problem",
                M(
                    "reach.web.seen",
                    {
                        "one": "In the last {days} days, the hub answered requests from {n} public network.",
                        "other": "In the last {days} days, the hub answered requests from {n} public networks.",
                    },
                    n=len(recent),
                    days=REACH_DAYS,
                ),
                M(
                    "reach.web.why",
                    "Something lets the internet reach the box's pages: a port forward or DMZ host on the "
                    "router, or a proxy. Its sign-in forms can then be reached from the internet too.",
                ),
                M(
                    "reach.web.fix",
                    "If you did not mean that, remove the port forward or DMZ host from your router. To reach "
                    "the box from outside its network, use Tailscale on Access → Remote access.",
                ),
                evidence=_some(
                    f"{n} ×{e.get('count', '?')} {time.strftime('%Y-%m-%d %H:%M', time.localtime(e['last']))}"
                    for n, e in recent
                ),
            )
        )
    else:
        out.append(
            finding(
                "reach-web",
                web_t,
                "ok",
                M(
                    "reach.web.none",
                    "In the last {days} days, the hub answered no request from a public address.",
                    days=REACH_DAYS,
                ),
                notes=[
                    M(
                        "reach.web.scope",
                        "The hub sees the pages that it serves, not every static file.",
                    )
                ],
            )
        )
    ssh = _ssh_public()
    # How far back the evidence goes: Armbian keeps the journal
    # in RAM and trims it, so "the last 7 days" may be since the last boot.
    since = _ssh_log_since()
    span = (
        [
            M(
                "reach.ssh.since",
                "The log of sshd on this box goes back to {date}.",
                date=since,
            )
        ]
        if since
        else []
    )
    ssh_t = M("reach.ssh.title", "SSH connection attempts")
    if ssh is None:
        out.append(
            _cannot(
                "reach-ssh",
                ssh_t,
                M("check.cannot.ssh-log", "It cannot read the log of sshd."),
                root=True,
            )
        )
    elif ssh:
        accepted = sum(a for a, _ in ssh.values())
        out.append(
            finding(
                "reach-ssh",
                ssh_t,
                "problem",
                M(
                    "reach.ssh.seen",
                    {
                        "one": "In the last {days} days, sshd had connections from {n} public address.",
                        "other": "In the last {days} days, sshd had connections from {n} public addresses.",
                    },
                    n=len(ssh),
                    days=REACH_DAYS,
                ),
                M(
                    "reach.ssh.why",
                    "Anyone on the internet who reaches SSH can try passwords on it, if SSH accepts passwords.",
                ),
                M(
                    "reach.ssh.fix",
                    "Remove the port forward to port 22 from your router. While it stays, turn SSH password "
                    "sign-in off on Security.",
                ),
                notes=[
                    (
                        M(
                            "reach.ssh.accepted",
                            {
                                "one": "sshd accepted {n} sign-in from them.",
                                "other": "sshd accepted {n} sign-ins from them.",
                            },
                            n=accepted,
                        )
                        if accepted
                        else M("reach.ssh.refused", "sshd accepted no sign-in from them.")
                    )
                ]
                + span,
                evidence=_some(sorted(ssh)),
            )
        )
    else:
        out.append(
            finding(
                "reach-ssh",
                ssh_t,
                "ok",
                M(
                    "reach.ssh.none",
                    "In the last {days} days, sshd had no connection from a public address.",
                    days=REACH_DAYS,
                ),
                notes=span,
            )
        )
    v6 = _global_v6()
    v6_t = M("reach.ipv6.title", "Public IPv6 address")
    if v6:
        out.append(
            finding(
                "reach-ipv6",
                v6_t,
                "warn",
                M("reach.ipv6.found", "This box has a public IPv6 address."),
                M(
                    "reach.ipv6.why",
                    "A program that listens on all addresses can be reached from the internet, where the IPv6 "
                    "firewall of the router lets traffic in. Most home routers do not, by default.",
                ),
                M(
                    "reach.ipv6.fix",
                    "Check that the IPv6 firewall of your router blocks incoming connections.",
                ),
                evidence=v6,
            )
        )
    else:
        out.append(
            finding(
                "reach-ipv6",
                v6_t,
                "ok",
                M("reach.ipv6.none", "This box has no public IPv6 address."),
            )
        )
    return out


def step_firewall(ctx):
    """The hotspot firewall: the hotspot's traffic goes through a
    default-drop chain, or it does not."""
    from irate_box.root import firewall

    about = {"kind": "setting", "key": "firewall"}
    title = M("firewall.policy.title", "Hotspot firewall policy")
    loaded = firewall.loaded()
    hotspot_up = False
    try:
        hotspot_up = bool(json.loads(_read(ETC / "ap.json") or "{}").get("up"))
    except ValueError:
        pass
    if loaded is None:
        return [
            finding(
                "firewall",
                title,
                "warn",
                M(
                    "firewall.no-nftables",
                    "nftables is not installed, so the box's own hotspot firewall cannot run.",
                ),
                M(
                    "firewall.no-nftables.why",
                    "Unless another firewall on the box limits it, a guest on the hotspot reaches every "
                    "listener on the box.",
                ),
                M(
                    "firewall.no-nftables.fix",
                    "Update the box, which installs nftables. Then turn the hotspot firewall on, on Security.",
                ),
                about=about,
            )
        ]
    if loaded:
        return [
            finding(
                "firewall",
                title,
                "ok",
                M(
                    "firewall.loaded",
                    "The rules are loaded. A guest on {iface} reaches the hub, its apps, DNS, DHCP and the ports "
                    "that an administrator opened. The box drops the rest.",
                    iface=firewall.hotspot_iface(),
                ),
                notes=[
                    M(
                        "firewall.forward",
                        "The box forwards guest traffic to the internet only when guest internet access is "
                        "on, on Network.",
                    )
                ],
                about=about,
            )
        ]
    return [
        finding(
            "firewall",
            title,
            "problem" if hotspot_up else "warn",
            (
                M(
                    "firewall.off.up",
                    "The hotspot is up, and the hotspot firewall is off.",
                )
                if hotspot_up
                else M("firewall.off.down", "The hotspot firewall is off.")
            ),
            M(
                "firewall.off.reach",
                "A guest on the hotspot reaches every listener on the box: SSH, Syncthing, MQTT, Tailscale and "
                "the rest.",
            ),
            M("firewall.off.on", "On Security, Hotspot firewall, turn it on."),
            about=about,
        )
    ]


STEPS = [
    ("notes", M("step.notes", "Notes add-on"), "F1", step_notes),
    ("firewall", M("step.firewall", "Hotspot firewall"), "", step_firewall),
    ("front", M("step.front", "Web server in front"), "F2 F15 F24 F27", step_front),
    (
        "admin-gate",
        M("step.admin-gate", "Administration pages from inside the box"),
        "F27 F31 S3",
        step_admin_gate,
    ),
    ("web-addons", M("step.web-addons", "Web add-ons"), "", step_web_addons),
    ("origins", M("step.origins", "Application origins"), "S4", step_origins),
    (
        "folders",
        M("step.folders", "Links and root-written files in the hub's folders"),
        "F3 F4 F5 F13",
        step_folders,
    ),
    (
        "code",
        M("step.code", "Installed code and the restart list"),
        "F3 F6 F20",
        step_code,
    ),
    (
        "units",
        M("step.units", "Service sandboxing and the build unit"),
        "F8 F19",
        step_units,
    ),
    (
        "addons",
        M("step.addons", "Add-ons and app services"),
        "S1 S9 S12 F6",
        step_addons,
    ),
    (
        "cmdlines",
        M("step.cmdlines", "Secrets in process arguments"),
        "F9",
        step_cmdlines,
    ),
    ("secrets", M("step.secrets", "Secrets in files"), "S13 F30", step_secrets),
    ("git", M("step.git", "Git servers and pushed content"), "F17 F18", step_git),
    (
        "accounts",
        M("step.accounts", "sudo rules and system accounts"),
        "",
        step_accounts,
    ),
    (
        "accounts-hub",
        M("step.accounts-hub", "Hub accounts and sign-in"),
        "",
        step_accounts_hub,
    ),
    (
        "image",
        M("step.image", "The image underneath (Armbian, mPWRD-OS)"),
        "",
        step_image,
    ),
    ("kernel", M("step.kernel", "Kernel protections"), "F3 F9", step_kernel),
    (
        "debsecan",
        M(
            "step.debsecan",
            "Debian packages against Debian's security tracker (debsecan)",
        ),
        "",
        step_debsecan,
    ),
    ("kits-trust", M("step.kits-trust", "Toolkits from USB drives: keyring and freshness"), "M9 M10", step_kits_trust),
    (
        "debian-cis",
        M("step.debian-cis", "CIS benchmark (debian-cis, in the deep audit)"),
        "",
        step_deep_cis,
    ),
    ("lynis", M("step.lynis", "Lynis (in the deep audit)"), "", step_deep_lynis),
    (
        "security-page",
        M("step.security-page", "Security page scan"),
        "",
        step_security_page,
    ),
    ("imports", M("step.imports", "Imported scans (OpenVAS, nmap)"), "", step_imports),
    ("tls", M("step.tls", "HTTPS: the certificate and the web server"), "S2", step_tls),
    ("mqtt", M("step.mqtt", "MQTT broker and the mesh decoder"), "S9", step_mqtt),
    ("internet", M("step.internet", "Reached from the internet"), "", step_internet),
    ("offline", M("step.offline", "Offline readiness"), "", step_offline),
]

# What the box's state cannot show, so the report says so instead of implying a clean bill. The
# references are the security review's findings.
NOT_COVERED = [
    M(
        "not-covered.code",
        "The box's state cannot show flaws inside code paths. Code review and tests cover them: {refs}.",
        refs=[
            "F7",
            "F10",
            "F12",
            "F14",
            "F16",
            "F21",
            "F23",
            "F25",
            "F26",
            "F28",
            "F29",
        ],
    ),
    M(
        "not-covered.network",
        "Some checks need the code or the network, so the doctor does not do them: {refs}.",
        refs=["F11", "F22", "F30"],
    ),
    M(
        "not-covered.page",
        "The Security page's scan covers the password sent unencrypted, SSH, updates and the ports that answer: "
        "{refs}.",
        refs=["S2", "S5", "S6", "S9"],
    ),
    M("not-covered.npm", "npm audits do not run, because they need the network (S12)."),
]


# The files the code step reads, by the name its findings use.
HUB_SOURCES = {
    "server.py": "irate_box/hub/server.py",
    "health.py": "irate_box/root/health.py",
    "store.py": "irate_box/hub/store.py",
}


def _hub_source(name):
    rel = HUB_SOURCES[name]
    return _read(CODE / rel) or _read(Path(__file__).resolve().parents[2] / rel)


def make_context():
    kind, front, note = load_front()
    src = {n: _hub_source(n) for n in ("server.py", "health.py", "store.py")}
    server = src["server.py"]
    # F2 and F15 are asked of the running hub (probe_drains, probe_json_cap): a marker in the
    # code only says the line is there. The code's word stands
    # in when the hub does not answer.
    drains = probe_drains()
    caps = probe_json_cap()
    if server:
        if drains is None:
            drains = bool(re.search(r"(?m)^DRAINS_REQUEST_BODIES\s*=\s*True\b", server))
        if caps is None:
            m = re.search(r"def _read_payload\(self\):(.*?)(?=\n    def )", server, re.S)
            caps = bool(m and re.search(r"MAX_|limit|too large|413", m.group(1)))
    units = {u: _show(u, *UNIT_PROPS) for u in _our_units()}
    addons = load_addons()
    addon_props = {
        a["unit"]: _show(
            a["unit"],
            *UNIT_PROPS,
            "ActiveState",
            "UnitFileState",
            "ExecStart",
            "Environment",
        )
        for a in addons
    }
    return {
        "front": front,
        "front_kind": kind,
        "front_note": note,
        "src": src,
        "hub_drains_bodies": drains,
        "hub_caps_json": caps,
        "units": units,
        "addons": addons,
        "addon_props": addon_props,
        "requested": _requested_options(),
        "listening": _listening(),
    }


def run_step(sid, fn, ctx):
    try:
        return fn(ctx)
    except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
        return [
            finding(
                f"{sid}-error",
                M("step.failed.title", "Check failed"),
                "warn",
                M(
                    "step.failed",
                    "This check stopped with an error, so its results are missing.",
                ),
                M(
                    "step.failed.why",
                    "The cause can be the state of the box, or a flaw in the check itself.",
                ),
                M(
                    "step.failed.fix",
                    "Run the doctor again. If the error stays, report it with the text below.",
                ),
                evidence=[f"{type(exc).__name__}: {exc}"],
            )
        ]


def audit(progress=None):
    """The whole report. `progress(n, total, step, findings)` is called after each step."""
    try:
        ctx = make_context()
    except Exception as exc:  # noqa: BLE001 - no context still gives a report
        ctx = {
            "front": None,
            "front_kind": None,
            "front_note": f"setup failed: {exc}",
            "src": {},
            "hub_drains_bodies": None,
            "hub_caps_json": None,
            "units": {},
            "addons": [],
            "addon_props": {},
            "requested": None,
            "listening": None,
        }
    steps = []
    for n, (sid, title, ref, fn) in enumerate(STEPS, 1):
        found = run_step(sid, fn, ctx)
        steps.append({"id": sid, "title": title, "ref": ref, "findings": found})
        if progress:
            progress(n, len(STEPS), steps[-1])
    for f in (f for s in steps for f in s["findings"]):
        if not f.get("about") and f.get("source", "doctor") in (
            "doctor",
            "debian-cis",
            "lynis",
        ):
            f["about"] = secdoctor_xref.about(
                f.get("source", "doctor"),
                f["id"].removeprefix("cis-").removeprefix("lynis-"),
            )
    flat = [f for s in steps for f in s["findings"]]
    version = (_read(CODE / "VERSION") or "unknown").strip()
    previous = _previous()
    return {
        "at": time.time(),
        "version": version,
        "root": os.geteuid() == 0,
        "steps": steps,
        "joint": joint(steps, freshness()),
        "counts": {k: sum(1 for f in flat if f["status"] == k) for k in ("problem", "warn", "ok")},
        "not_covered": NOT_COVERED,
        "new": new_since(previous, flat),
        "previous_at": (previous or {}).get("at"),
    }


def _previous():
    """The last report, so this one can say what changed."""
    try:
        rep = json.loads(_read(REPORT, limit=8 << 20) or "null")
    except ValueError:
        return None
    return rep if isinstance(rep, dict) and isinstance(rep.get("steps"), list) else None


def new_since(previous, findings):
    """Ids of findings that are a problem or a warning now and were fine, or not there, in the
    last report: what changed, for a box nobody audits by hand every day (the doctor runs daily
    from irate-box-secdoctor.timer)."""
    if not previous:
        return []
    was = {
        f.get("id"): f.get("status", "ok")
        for s in previous["steps"]
        for f in s.get("findings", [])
        if isinstance(f, dict)
    }
    return sorted(f["id"] for f in findings if f["status"] != "ok" and was.get(f["id"], "ok") == "ok")


def write_report(report, path=REPORT):
    """The one thing the doctor writes: its report, root's, 0644 (safeio: a fresh file renamed
    over the old one, so a link planted at `path` is replaced, never written through).
    """
    from irate_box.root import safeio

    safeio.write(path, json.dumps(report, indent=2))


MARK = {"ok": "ok     ", "warn": "WARN   ", "problem": "PROBLEM"}


def print_report(report, only_bad=False, out=sys.stdout):
    """The report in English for the terminal: each step, each finding's parts on lines of their own,
    the evidence as it is, the commands to copy after a $."""
    from irate_box.msg import render

    for n, s in enumerate(report["steps"], 1):
        shown = [f for f in s["findings"] if not only_bad or f["status"] != "ok"]
        if only_bad and not shown:
            continue
        print(
            f"\n[{n}/{len(report['steps'])}] {render(s['title'])}" + (f"  ({s['ref']})" if s["ref"] else ""),
            file=out,
        )
        for f in shown:
            print(
                f"  {MARK[f['status']]} {render(f['title'])}" + (f"  [{f['ref']}]" if f.get("ref") else ""),
                file=out,
            )
            if f["status"] != "ok" or not only_bad:
                for part in (f.get("summary"), f.get("why"), *(f.get("notes") or [])):
                    if part:
                        print(f"          {render(part)}", file=out)
                for e in f.get("evidence") or []:
                    print(f"            · {e}", file=out)
            if f["status"] != "ok":
                if f.get("fix"):
                    print(f"          -> {render(f['fix'])}", file=out)
                for c in f.get("commands") or []:
                    print(f"             $ {c}", file=out)
    c = report["counts"]
    print(
        f"\n{c['problem']} problem(s), {c['warn']} warning(s), {c['ok']} ok."
        + ("" if report["root"] else " Not run as root: some checks could not read what they need."),
        file=out,
    )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd not in ("report", "summary", "json", "run"):
        sys.exit("usage: secdoctor.py [report | summary | json | run]")
    from irate_box.msg import render

    if cmd == "json":
        print(json.dumps(audit(), indent=2))
    elif cmd == "run":
        # The timer's: the audit, kept where /admin reads it, one line said.
        rep = audit()
        write_report(rep)
        c = rep["counts"]
        print(
            f"security doctor: {c['problem']} problem(s), {c['warn']} warning(s)"
            + (f", {len(rep['new'])} new since the last run" if rep["new"] else "")
        )
    else:
        rep = audit(
            progress=(
                (lambda n, t, s: print(f"[{n}/{t}] {render(s['title'])} ...", file=sys.stderr))
                if cmd == "report" and sys.stderr.isatty()
                else None
            )
        )
        print_report(rep, only_bad=(cmd == "summary"))
