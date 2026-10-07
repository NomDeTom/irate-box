# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Who may open each app: public, users, private or off (/admin, Apps and Add-ons).

  public    on the home page for everyone; the route is open (or, for the shell and the
            Syncthing page, still behind the admin login: "login_always")
  users     on the home page for anyone logged in to an account (accounts.py, step 16); the
            route asks the hub whether the visitor's session is one (nginx auth_request, Caddy
            forward_auth to /_irate/user), and sends anyone else to /account.html. Not for the
            login_always apps (the shell and Syncthing stay the admin's), nor yet local add-ons.
  private   not on the home page; the route asks for the admin login
  off       not on the home page; the route answers 404, and an add-on's service is stopped

The choice is root's (/etc/hub/access.json, written by the root helper), because it changes
the web server's config: for nginx, a file of `set` lines the site includes at server level
($irate_box_auth_<id>, $irate_box_off_<id>); for Caddy, one snippet per app imported where its
route is. The root helper leaves a copy in the hub's control folder, which the hub reads to
leave tiles and list entries out.

The apps that can be switched are the ones the web-server configs gate (ROUTED). Their
manifests' "access" part gives the default and the wording; an app without a manifest keeps
its default.

Local add-ons (manifests.py; from /admin, with no root) are switched the same way, keyed by
their id, and start off: adding one is not switching it on. They are all served by one
server of the web server's (the add-on origin, its own port), which reads three maps from one
generated file (addon_nginx_conf), or for Caddy one generated file of routes
(addon_caddy_routes). Stdlib only.
"""

import json
import re

MODES = ("public", "users", "private", "off")
# id -> (default, login always): what config/irate-box.nginx and config/Caddyfile gate.
ROUTED = {
    "draw": ("public", False), "mermaid": ("public", False), "serial": ("public", False),
    "tools": ("public", False), "flasher": ("public", False), "wiki": ("public", False),
    "git": ("public", False), "drop": ("public", False), "notes": ("public", False),
    "mqtt": ("public", False), "term": ("public", True), "sync": ("private", True),
    "themes": ("public", False),
}
REALM = "Irate-Box"


LOCAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


# The web flasher's page is opened from the guest's own disk: its requests are cross-site, so the
# session cookie (SameSite=Strict) never goes with them, and a users gate would refuse them all.
NOT_FOR_USERS = {"flasher"}


def users_allowed(i):
    """Whether an app can be for users: a built-in one that doesn't always ask for the admin login."""
    return i in ROUTED and not ROUTED[i][1] and i not in NOT_FOR_USERS


def defaults():
    return {i: d for i, (d, _) in ROUTED.items()}


def clean(data):
    """Known ids and modes; anything else falls back to the default. A local add-on's id (a
    plain name, not a built-in's) is kept as chosen: whether it is still installed is the
    caller's to know, and a choice for one that is not is harmless."""
    out = defaults()
    if isinstance(data, dict):
        for i, mode in data.items():
            if mode in MODES and (i in ROUTED or (isinstance(i, str) and LOCAL_ID_RE.match(i))) and (mode != "users" or users_allowed(i)):
                out[i] = mode
    return out


def read(path):
    try:
        return clean(json.loads(path.read_text()))
    except (OSError, ValueError):
        return defaults()


def mode_of(state, i):
    """An app's mode: its choice, or its default (off, for a local add-on never switched)."""
    return state.get(i, ROUTED[i][0] if i in ROUTED else "off")


def addon_csp(m):
    """A local add-on's Content-Security-Policy: its own origin, plus what its manifest says it
    connects to ({box} becomes the hub's host, $host to nginx). Built only from checked
    manifests (manifests.check_local), whose values cannot hold a quote or a semicolon."""
    # Each ws:// or http:// it may reach, also as wss:// or https://: served over HTTPS (step 15),
    # a page may not use the plain one (the MQTT explorer goes to wss://<box>/mqtt there).
    allowed = []
    for c in m.get("capabilities", {}).get("connect", []):
        allowed.append(c)
        for plain, secure in (("ws://", "wss://"), ("http://", "https://")):
            if c.startswith(plain):
                allowed.append(secure + c[len(plain):])
    connect = " ".join(dict.fromkeys(c.replace("{box}", "$host") for c in allowed))
    return ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'" + (" " + connect if connect else "")
            + "; frame-ancestors 'self' http://$host:* https://$host:*; base-uri 'none'; form-action 'self'")


def addon_nginx_conf(state, local):
    """The add-on server's maps, at http level: per add-on id, whether it is off, the login realm
    (off for public), and its CSP. An id with no entry is off."""
    lines = ["# Generated by irate-box (hub_control.py) from /etc/hub/access.json and the local add-ons. Edits are overwritten.",
             "map $irate_box_addon $irate_box_addon_off {", '\tdefault "1";']
    for m in local:
        lines.append(f'\t{m["id"]} "{"1" if mode_of(state, m["id"]) == "off" else ""}";')
    lines += ["}", "map $irate_box_addon $irate_box_addon_auth {", f'\tdefault "{REALM}";']
    for m in local:
        lines.append(f'\t{m["id"]} {"off" if mode_of(state, m["id"]) == "public" else chr(34) + REALM + chr(34)};')
    lines += ["}", "map $irate_box_addon $irate_box_addon_csp {", "\tdefault \"default-src 'none'\";"]
    for m in local:
        lines.append(f'\t{m["id"]} "{addon_csp(m)}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def addon_caddy_routes(state, local, login_hash, directive="basic_auth"):
    """The add-on site's routes for Caddy: one handle per add-on that is not off (the login for a
    private one), then 404 for everything else."""
    out = ["# Generated by irate-box (hub_control.py) from /etc/hub/access.json and the local add-ons. Edits are overwritten."]
    for m in local:
        mode = mode_of(state, m["id"])
        if mode == "off":
            continue
        out.append(f"handle /{m['id']}/* {{")
        if mode == "private":
            out.append(f"\t{directive} {{\n\t\tadmin {login_hash}\n\t}}")
        out.append(f'\theader Content-Security-Policy "{addon_csp(m).replace("$host", "{host}")}"')
        # Its own files only; never the bundle record or anything dotted.
        out.append("\t@hidden path_regexp (^|/)(\\.|irate-box-bundle\\.json$)\n\trespond @hidden 404")
        out.append("\tfile_server")
        out.append("}")
    out.append("handle {\n\trespond 404\n}")
    return "\n".join(out) + "\n"


def apps(manifests):
    """The switchable apps for /admin, in manifest order: [{id, title, default, login, note,
    kind (app, addon or builtin: where /admin shows it), unit (off stops a service), users (can be
    for users)}]."""
    out = []
    for m in manifests:
        if m.get("local"):
            out.append({"id": m["id"], "title": m["addon"]["title"], "default": "off", "login": False,
                        "note": "", "kind": "local", "unit": False, "users": False})
            continue
        if m["id"] not in ROUTED:
            continue
        a = m.get("access", {})
        title = (m.get("tile") or {}).get("name") or (m.get("addon") or {}).get("title") \
            or (m.get("status") or {}).get("name") or m["id"]
        kind = "app" if m.get("install") else "addon" if m.get("addon") else "builtin"
        out.append({"id": m["id"], "title": a.get("title", title), "default": ROUTED[m["id"]][0],
                    "login": ROUTED[m["id"]][1], "note": a.get("note", ""), "kind": kind,
                    "unit": bool((m.get("status") or {}).get("control")), "users": users_allowed(m["id"])})
    return out


def var(i):
    return re.sub(r"[^a-z0-9]", "_", i)


def nginx_conf(state):
    """The site's include: every variable the template reads, for every routed app."""
    lines = ["# Generated by irate-box (hub_control.py) from /etc/hub/access.json. Edits are overwritten."]
    for i in ROUTED:
        mode = state.get(i, ROUTED[i][0])
        lines.append(f'set $irate_box_auth_{var(i)} {"off" if mode in ("public", "users") else chr(34) + REALM + chr(34)};')
        lines.append(f'set $irate_box_off_{var(i)} "{"1" if mode == "off" else ""}";')
    return "\n".join(lines) + "\n"


# The gate an app in users mode gets, inside its location: the session check, and anyone without
# a session sent to log in. The template includes @ACCESS@.d/gate-<id>.conf* in each such
# location: a wildcard, so no file (public, private, off) is no gate, and a helper that knows
# nothing of gates still passes the site (nginx -t) when it checks an update.
NGINX_GATE = "auth_request /_irate_user;\nerror_page 401 = @irate_box_login;\n"
# The admin's routes (/admin, the shell, Syncthing: gate-admin.conf) and the private apps: the box's
# own login (basic auth) or an admin account's session, either (satisfy any). With the box's own
# login switched off, anyone without an admin session is sent to log in, not asked for a password
# that can't work.
NGINX_ADMIN_GATE = "satisfy any;\nauth_request /_irate_admin;\n"


def nginx_gates(state, admin_login=True):
    """{file name: text} for @ACCESS@.d/: the admin gate, and one per app in users or private mode.
    admin_login: whether the box's own admin login is on (hub_control.py admin_login)."""
    head = "# Generated by irate-box (hub_control.py) from /etc/hub/access.json. Edits are overwritten.\n"
    admin = head + NGINX_ADMIN_GATE + ("" if admin_login else "error_page 401 = @irate_box_login;\n")
    out = {"gate-admin.conf": admin}
    for i in ROUTED:
        mode = state.get(i, ROUTED[i][0])
        if mode == "users":
            out[f"gate-{i}.conf"] = head + NGINX_GATE
        elif mode == "private" and not ROUTED[i][1]:
            out[f"gate-{i}.conf"] = admin
    return out


def caddy_snippets(state, login_hash, directive="basic_auth"):
    """{file name: snippet} for the Caddyfile's per-route imports. <id>.caddy: nothing, the login
    or a 404; <id>-off.caddy: the 404 only (routes that always ask for the login, or must stay
    open). `directive` is "basicauth" on Caddy 2.6, as install.sh rewrites it there."""
    out = {}
    for i in ROUTED:
        mode = state.get(i, ROUTED[i][0])
        head = f"# Generated by irate-box (hub_control.py) from /etc/hub/access.json: {i} is {mode}.\n"
        gate = ("respond 404\n" if mode == "off" else
                f"{directive} {{\n\tadmin {login_hash}\n}}\n" if mode == "private" and not ROUTED[i][1] else
                "forward_auth 127.0.0.1:8000 {\n\turi /_irate/user?redirect=1\n}\n" if mode == "users" else "")
        out[f"{i}.caddy"] = head + gate
        out[f"{i}-off.caddy"] = head + ("respond 404\n" if mode == "off" else "")
    return out
