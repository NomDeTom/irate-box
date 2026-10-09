# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Who may open each app: public, users, private or off (/admin, Apps and Add-ons).

  public    on the home page for everyone; the route is open (or, for the shell and the
            Syncthing page, still behind the admin login: "login_always")
  users     on the home page for anyone logged in to an account (accounts.py, step 16); the
            route asks the hub whether the visitor's session is one (nginx auth_request, Caddy
            forward_auth to /_irate/user), and sends anyone else to /account.html. Not for the
            login_always apps (the shell and Syncthing stay the admin's). A local add-on may be
            for users too (item 6, current-and-next-actions): nginx has no per-id location ready
            for it (ids aren't known at template time, unlike the built-ins' own locations), so
            addon_gates() writes one location per add-on in users mode, generated fresh each
            time, instead of asking the hub on every add-on request whether this one needs it.
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
    """Whether an app can be for users: a built-in one that doesn't always ask for the admin
    login, or a local add-on (never the shell or Syncthing, which stay the admin's and aren't
    local add-ons anyway)."""
    if i in ROUTED:
        return not ROUTED[i][1] and i not in NOT_FOR_USERS
    return isinstance(i, str) and bool(LOCAL_ID_RE.match(i))


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
        lines.append(f'\t{m["id"]} {"off" if mode_of(state, m["id"]) in ("public", "users") else chr(34) + REALM + chr(34)};')
    lines += ["}", "map $irate_box_addon $irate_box_addon_csp {", "\tdefault \"default-src 'none'\";"]
    for m in local:
        lines.append(f'\t{m["id"]} "{addon_csp(m)}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


def addon_gates(state, local):
    """{file name: text} for the add-on gates directory (item 6, current-and-next-actions): one
    nginx location per local add-on in users mode. The add-on server's one shared regex location
    (addon_nginx_conf's maps) handles every id at request time, so there is nowhere ready-made to
    ask the hub only for the ones that need it; instead each add-on in users mode gets its own
    location here, generated fresh whenever access.json changes, matched before the shared one
    (the template includes this directory first) so a public or off add-on never pays for the
    extra round trip."""
    out = {}
    for m in local:
        if mode_of(state, m["id"]) != "users":
            continue
        out[f"users-{m['id']}.conf"] = (
            f"# Generated by irate-box (hub_control.py) from /etc/hub/access.json: {m['id']} is users.\n"
            f"location ~ ^/{m['id']}/ {{\n"
            "\tauth_request /_irate_user;\n"
            "\terror_page 401 = @irate_box_login;\n"
            "\tlocation ~ (^|/)(\\.|irate-box-bundle\\.json$) { return 404; }\n"
            f"\tadd_header Content-Security-Policy \"{addon_csp(m)}\" always;\n"
            "\tadd_header X-Content-Type-Options nosniff always;\n"
            "\tadd_header Referrer-Policy no-referrer always;\n"
            "\tadd_header Cache-Control \"no-cache\" always;\n"
            "\ttry_files $uri $uri/index.html =404;\n"
            "}\n")
    return out


def addon_caddy_routes(state, local, login_hash, directive="basic_auth"):
    """The add-on site's routes for Caddy: one handle per add-on that is not off (the login for a
    private one, the hub's session check for one in users mode), then 404 for everything else."""
    out = ["# Generated by irate-box (hub_control.py) from /etc/hub/access.json and the local add-ons. Edits are overwritten."]
    for m in local:
        mode = mode_of(state, m["id"])
        if mode == "off":
            continue
        out.append(f"handle /{m['id']}/* {{")
        if mode == "private":
            out.append(f"\t{directive} {{\n\t\tadmin {login_hash}\n\t}}")
        elif mode == "users":
            out.append("\tforward_auth 127.0.0.1:8000 {\n\t\turi /_irate/user?redirect=1\n\t}")
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
                        "note": "", "kind": "local", "unit": False, "users": users_allowed(m["id"])})
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


def nginx_gates(state, admin_login=True, accounts_ready=False):
    """{file name: text} for @ACCESS@.d/: the admin gate, and one per app in users or private mode.
    admin_login: whether the box's own admin login is on (hub_control.py admin_login).
    accounts_ready: an admin account can sign in (accounts.admin_ready). Then the browser is never asked
    for the box's own login (Tom, 2026-10-09: "get rid of the admin-specific login"): without an admin
    session it is sent to the standard sign-in. A client that sends the box's own login with its request
    (a script's curl -u) still gets in, as nginx checks that before the 401 is turned into the redirect."""
    head = "# Generated by irate-box (hub_control.py) from /etc/hub/access.json. Edits are overwritten.\n"
    admin = head + NGINX_ADMIN_GATE + ("" if admin_login and not accounts_ready else "error_page 401 = @irate_box_login;\n")
    out = {"gate-admin.conf": admin}
    for i in ROUTED:
        mode = state.get(i, ROUTED[i][0])
        if mode == "users":
            out[f"gate-{i}.conf"] = head + NGINX_GATE
        elif mode == "private" and not ROUTED[i][1]:
            out[f"gate-{i}.conf"] = admin
    return out


# Caddy has no "satisfy any". The admin's routes (/admin, the shell, Syncthing) keep their own
# basic_auth in the Caddyfile, inside a route (written order) that first drops any X-Irate-Session
# a guest sent, then imports admin-gate.caddy: the hub is asked about the session cookie and, for an
# admin account's, answers X-Irate-Session: admin, copied onto the request; the basic_auth that
# follows is skipped for it. With no admin-gate.caddy (a dev checkout, an older helper) the login
# alone applies. With the box's own login off, the Caddyfile's hashes are one nobody knows
# (hub_control.py admin_login) and the check is a hard one: anyone else is sent to log in.
CADDY_SESSION_HEADER = "X-Irate-Session"


def caddy_admin_gate(admin_login=True, accounts_ready=False):
    """admin-gate.caddy: the hub asked about the visitor's session. admin_login: whether the box's
    own login is on (then the hub only says; off, it also sends anyone else to log in). With it on and an
    admin account ready (accounts_ready), anyone without an admin session is sent to sign in too, unless
    the request carries a login of its own (basic=1: a script's), which the basic_auth after this checks."""
    head = "# Generated by irate-box (hub_control.py): an admin account's session stands in for the login.\n"
    q = "soft=1" if admin_login and not accounts_ready else "redirect=1&basic=1" if admin_login else "redirect=1"
    return head + ("forward_auth 127.0.0.1:8000 {\n"
                   f"\turi /_irate/admin?{q}\n"
                   f"\tcopy_headers {CADDY_SESSION_HEADER}\n}}\n")


def caddy_admin_route(login_hash, directive="basic_auth", admin_login=True):
    """A private app's gate: as the admin's routes (the session, else the login), whole here."""
    basic = (f"\t@irate_box_basic not header {CADDY_SESSION_HEADER} admin\n"
             f"\t{directive} @irate_box_basic {{\n\t\tadmin {login_hash}\n\t}}\n") if admin_login else ""
    gate = "".join("\t" + line + "\n" for line in caddy_admin_gate(admin_login).splitlines()[1:])
    return (f"route {{\n\trequest_header -{CADDY_SESSION_HEADER}\n{gate}{basic}"
            f"\trequest_header -{CADDY_SESSION_HEADER}\n}}\n")


def caddy_snippets(state, login_hash, directive="basic_auth", admin_login=True, accounts_ready=False):
    """{file name: snippet} for the Caddyfile's per-route imports. <id>.caddy: nothing, the admin's
    gate (caddy_admin_route), the users' check or a 404; <id>-off.caddy: the 404 only (routes that
    always ask for the login, or must stay open); admin-gate.caddy for the admin's own routes.
    `directive` is "basicauth" on Caddy 2.6, as install.sh rewrites it there."""
    out = {"admin-gate.caddy": caddy_admin_gate(admin_login, accounts_ready)}
    for i in ROUTED:
        mode = state.get(i, ROUTED[i][0])
        head = f"# Generated by irate-box (hub_control.py) from /etc/hub/access.json: {i} is {mode}.\n"
        gate = ("respond 404\n" if mode == "off" else
                caddy_admin_route(login_hash, directive, admin_login) if mode == "private" and not ROUTED[i][1] else
                "forward_auth 127.0.0.1:8000 {\n\turi /_irate/user?redirect=1\n}\n" if mode == "users" else "")
        out[f"{i}.caddy"] = head + gate
        out[f"{i}-off.caddy"] = head + ("respond 404\n" if mode == "off" else "")
    return out
