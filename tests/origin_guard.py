# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Apps on origins of their own: notes are served only from their own
port, with the same login switch and refusals as before, and /notes/ on the hub's port only
redirects there; the doctor can tell which origin each app is on. python3 tests/origin_guard.py
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


tmpl = (REPO / "config" / "irate-box.nginx").read_text()
plain_files = {path.name: content for path, content in tls._plain_files("http", "allow").items()}


def expand(text):
    return re.sub(
        r"(?m)^\s*(?:include|import)\s+\S*/((?:nginx|caddy)-(?:http|policy)-[a-z]+\.inc);?\s*$",
        lambda match: plain_files[match[1]],
        text,
    )


tmpl = expand(tmpl)
text = (
    tmpl.replace("@PORT@", "80")
    .replace("@ADDON_PORT@", "8090")
    .replace("@NOTES_PORT@", "8091")
    .replace("@WIKI_PORT@", "8092")
    .replace("@GIT_PORT@", "8093")
    .replace("@NOTEBOOK_PORT@", "8094")
    .replace("@ADMIN_PORT@", "8095")
    .replace("@DRAW_PORT@", "8096")
    .replace("@MERMAID_PORT@", "8097")
    .replace("@SYNC_PORT@", "8098")
)
text = (
    text.replace("@TLS_PORT@", "443")
    .replace("@ADDON_TLS_PORT@", "8490")
    .replace("@NOTES_TLS_PORT@", "8491")
    .replace("@WIKI_TLS_PORT@", "8492")
    .replace("@GIT_TLS_PORT@", "8493")
    .replace("@NOTEBOOK_TLS_PORT@", "8494")
    .replace("@ADMIN_TLS_PORT@", "8495")
    .replace("@DRAW_TLS_PORT@", "8496")
    .replace("@MERMAID_TLS_PORT@", "8497")
    .replace("@SYNC_TLS_PORT@", "8498")
)
servers = secdoctor._nginx_servers(text)
by_port = {tuple(p): b for p, b in servers}
hub, notes = by_port.get(("80",), ""), by_port.get(("8091",), "")
check(
    "ten server blocks: the hub, add-ons, notes, books, cgit, notebooks, administration, Excalidraw, Mermaid, Syncthing",
    sorted(by_port)
    == [("80",), ("8090",), ("8091",), ("8092",), ("8093",), ("8094",), ("8095",), ("8096",), ("8097",), ("8098",)],
    sorted(by_port),
)
cgit = by_port.get(("8093",), "")
check(
    "the hub's port runs no cgit, but still serves clones and pushes",
    "cgit.cgi" not in hub and "git-http-backend" in hub and "auth_request /_irate_git_access;" in hub,
)
check(
    "cgit's pages on the hub's port redirect to its origin (its TLS twin over HTTPS)",
    "return 302 $scheme://$host:$irate_box_git_port$request_uri;" in hub,
)
check(
    "cgit's origin: its clone URLs name the hub's port",
    cgit.count("fastcgi_param HTTP_HOST $host:80;") == 2,
)
check(
    "cgit's origin: the hub's app bar may frame it",
    cgit.count("frame-ancestors 'self' $scheme://$host:$irate_box_hub_port $scheme://$host\"") == 2,
)
check(
    "cgit's origin: a clone or push sent here goes to the hub's port",
    re.search(
        r"git-receive-pack\)\$ \{\s*return 302 \$scheme://\$host:\$irate_box_hub_port\$request_uri;",
        cgit,
    )
    is not None,
)
# HTTPS: every origin has its TLS twin in the same server
# block (so the same routes), redirects keep to the scheme they came in on, and never HSTS.
for (port,), name in (
    (("80",), "main"),
    (("8090",), "addons"),
    (("8091",), "notes"),
    (("8092",), "wiki"),
    (("8093",), "git"),
    (("8094",), "notebook"),
    (("8095",), "admin-site"),
    (("8096",), "draw"),
    (("8097",), "mermaid"),
    (("8098",), "sync"),
):
    check(
        f"TLS twin: the {name} server block includes its own",
        f"include @TLS@/nginx-{name}.conf*;" in by_port.get((port,), ""),
    )
maps = {
    k: (h, d)
    for k, h, d in re.findall(
        r"map \$scheme \$(irate_box_\w+_port)\s*\{\s*https (\d+);\s*default (\d+);",
        text,
    )
}
check(
    "  each origin's port by scheme: http to its own, https to its twin",
    maps
    == {
        "irate_box_hub_port": ("443", "80"),
        "irate_box_addon_port": ("8490", "8090"),
        "irate_box_notes_port": ("8491", "8091"),
        "irate_box_wiki_port": ("8492", "8092"),
        "irate_box_git_port": ("8493", "8093"),
        "irate_box_notebook_port": ("8494", "8094"),
        "irate_box_admin_port": ("8495", "8095"),
        "irate_box_draw_port": ("8496", "8096"),
        "irate_box_mermaid_port": ("8497", "8097"),
        "irate_box_sync_port": ("8498", "8098"),
    },
    maps,
)
check(
    "  no redirect between origins with a fixed port",
    not re.search(r"\$scheme://\$host:\d", text),
    re.findall(r"\$scheme://\$host:\d+", text),
)
caddy = expand((REPO / "config" / "Caddyfile").read_text())
# A baseline CSP on the hub's origin, server-wide and wherever a location sets its own headers.
csp_line = "add_header Content-Security-Policy $irate_box_csp always;"
page_line = "add_header Content-Security-Policy $irate_box_page_csp always;"


def block_of(text, start):
    """One location of a server block: from its first line to its closing brace at the same depth."""
    i = text.index(start)
    return text[i : text.index("\n\t}", i)]


check(
    "a baseline CSP on the hub's origin: server-wide, and beside every Cache-Control the hub block sets",
    "set $irate_box_csp \"object-src 'none'; base-uri 'none'; frame-ancestors 'self'\";" in hub
    # the hub's own pages (/, /index.html, its static files, drop, themes, the App store) have the stricter one;
    # the App store's location sets no Cache-Control of its own (the hub says it per response, the files long-cached)
    and hub.count(csp_line) + hub.count(page_line) == hub.count("add_header Cache-Control") + 1 + 3,
    (hub.count(csp_line), hub.count(page_line), hub.count("add_header Cache-Control")),
)
check(
    "  the hub's own pages run only the scripts it serves (script-src 'self'), the apps beside them the baseline",
    "set $irate_box_page_csp \"object-src 'none'; base-uri 'none'; frame-ancestors 'self'; script-src 'self'\";" in hub
    and hub.count(page_line) == 6  # index, /, the statics, drop, themes, the App store's own pages
    and "irate_box_page_csp" in block_of(hub, "\tlocation = / {")
    and csp_line in block_of(hub, "\tlocation ^~ /tools/ {")
    and "irate_box_page_csp" not in block_of(hub, "\tlocation ^~ /tools/ {"),
)
check(
    "  every header set of the hub's block says nosniff and no-referrer",
    hub.count("add_header Referrer-Policy no-referrer always;") == hub.count(csp_line) + hub.count(page_line),
    (hub.count("add_header Referrer-Policy no-referrer always;"), hub.count(csp_line) + hub.count(page_line)),
)
check(
    "  and on Caddy",
    "header @irate_box_not_page Content-Security-Policy \"object-src 'none'; base-uri 'none'; frame-ancestors 'self'\""
    in caddy
    and "header @irate_box_page Content-Security-Policy \"object-src 'none'; base-uri 'none'; frame-ancestors 'self'; "
    "script-src 'self'\""
    in caddy,
)
check(
    "no inline script in the hub's pages (script-src 'self' would refuse it)",
    not [
        f.name
        for f in (REPO / "web").glob("*.html")
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", f.read_text(), re.I)
        or re.search(r"\son[a-z]+\s*=", f.read_text(), re.I)
    ],
)
admin = by_port.get(("8095",), "")
# The administration pages: an origin of their own. The hub's port only redirects.
check(
    "/admin on the hub's port redirects to the administration port (its TLS twin over HTTPS), path and query kept",
    "location = /admin { return 302 $scheme://$host:$irate_box_admin_port/admin/; }" in hub
    and "location /admin/ { return 302 $scheme://$host:$irate_box_admin_port$request_uri; }" in hub
    and "auth_basic" not in re.search(r"location /admin/ \{[^}]*\}", hub)[0],
)
check(
    "  and only the administration port sends the hub the front's secret",
    # /admin/ and the Store pane's thumbnails
    "include @FRONT@;" not in hub and admin.count("include @FRONT@;") == 2 and text.count("include @FRONT@;") == 2,
    text.count("include @FRONT@;"),
)
check(
    "  the admin pages' Hub link (/hub) goes to the hub's home on its port, in nginx and Caddy; their / stays the admin pages",
    "location = /hub { return 302 $scheme://$host:$irate_box_hub_port/; }" in admin
    and "location = / { return 302 /admin/; }" in admin
    and "redir /hub {scheme}://{host}:{irate_box_hub_port}/ 302" in caddy
    and all(
        'href="/hub"' in (REPO / "web" / f).read_text() and 'href="/"' not in (REPO / "web" / f).read_text()
        for f in (
            "admin.html",
            "admin-setup.html",
            "admin-mesh.html",
            "admin-factory.html",
            "admin-rockchip.html",
            "admin-pins.html",
        )
    ),
)
check(
    "  every other origin clears the header, whatever a client sent",
    all(block.count('proxy_set_header X-Irate-Front ""') >= 1 for port, block in by_port.items() if port != ("8095",)),
)
check(
    "  the administration port: the login or an admin session, first use, then the hub; the thumbnails the same",
    re.search(
        r"location \^~ /admin/ \{\s*set \$irate_box_admin_auth \"Irate-Box\";\s*if \(-f @UNCLAIMED@\)[^\n]*\n\s*auth_basic \$irate_box_admin_auth;"
        r"\s*auth_basic_user_file \$irate_box_logins;\s*(#[^\n]*\n\s*)*include @ACCESS@\.d/gate-admin\.conf\*;",
        admin,
    )
    is not None
    and re.search(r"location ~ \^/api/saves/\[\^/\]\+/thumb\$ \{.*?limit_except GET.*?gate-admin", admin, re.S)
    is not None,
)
check(
    "  it proxies only to the hub, and sends the pages of the hub there; no cookie is stripped, none is needed",
    set(re.findall(r"proxy_pass (\S+);", admin))
    == {"http://irate_box_hub/_irate/admin", "http://irate_box_hub", "http://127.0.0.1:8000"}
    and "location ~* \\.html$ { return 302 $scheme://$host:$irate_box_hub_port$request_uri; }" in admin
    and "try_files $uri @irate_box_hub_origin;" in admin,
    set(re.findall(r"proxy_pass (\S+);", admin)),
)
check(
    "  an unauthenticated visitor is sent to the hub's sign-in, which sends them back",
    "location @irate_box_login { return 302 $scheme://$host:$irate_box_hub_port/account.html?next=$uri; }" in admin,
)
check(
    "  the old 'admin over HTTPS only' include is gone: the policy include covers the whole origin",
    "nginx-admin.conf" not in text and "location = /http-admin-warning" in admin,
)


def directives(t):
    return "\n".join(line for line in t.splitlines() if not line.lstrip().startswith("#"))


check(
    "  never HSTS (port 80 must keep working for the captive portal)",
    "Strict-Transport-Security" not in directives(text) and "Strict-Transport-Security" not in directives(caddy),
)
head = (REPO / "config" / "cgit-head.html").read_text()
served = (
    all(f"location = {f}" in cgit for f in ("/palette.css", "/cgit-hub.css", "/themes.js", "/cgit-hub.js"))
    and "location ^~ /git-static/" in cgit
    and "location ^~ /git-hl/" in cgit
)
check(
    "cgit's origin serves everything its pages load",
    served
    and all(
        s in head
        for s in (
            "/palette.css",
            "/cgit-hub.css",
            "/themes.js",
            "/git-hl/",
            "/cgit-hub.js",
        )
    ),
)
wiki = by_port.get(("8092",), "")
check("the hub's port does not proxy to Kiwix", "127.0.0.1:8081" not in hub)
check(
    "/wiki/ on the hub's port redirects to the books' port, and keeps the off switch",
    re.search(
        r"location /wiki/ \{\s*if \(\$irate_box_off_wiki\) \{ return 404; \}\s*return 302 \$scheme://\$host:\$irate_box_wiki_port\$request_uri;",
        hub,
    )
    is not None,
)
check(
    "the books' port: the login switch, Kiwix, and no-cache on book pages",
    wiki.count("auth_basic $irate_box_auth_wiki;") == 2
    and "proxy_pass http://127.0.0.1:8081;" in wiki
    and 'add_header Cache-Control "no-cache" always;' in wiki,
)
check(
    "the hub's port does not proxy to SilverBullet",
    "silverbullet" not in re.sub(r"(?m)^\s*#.*$", "", hub),
)
check(
    "/notes/ on the hub's port redirects to the notes port, and keeps the off switch",
    re.search(
        r"location /notes/ \{\s*if \(\$irate_box_off_notes\) \{ return 404; \}\s*return 302 \$scheme://\$host:\$irate_box_notes_port\$request_uri;",
        hub,
    )
    is not None,
)
check(
    "the hub's port still refuses /notes/.shell, .proxy, .runtime",
    "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in hub,
)
check(
    "the notes port: the same refusals",
    "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in notes,
)
check(
    "the notes port: the login switch and the socket",
    "auth_basic $irate_box_auth_notes;" in notes
    and "proxy_pass http://unix:/run/silverbullet/silverbullet.sock;" in notes
    and "include @ACCESS@;" in notes,
)
check("the notes port serves nothing but notes", "location / { return 404; }" in notes)

# Excalidraw, Mermaid and Syncthing's web page: an origin each. The hub's port redirects.
draw, mermaid_, syncb = (by_port.get((p,), "") for p in ("8096", "8097", "8098"))
for app, port in (
    ("draw", "irate_box_draw_port"),
    ("mermaid", "irate_box_mermaid_port"),
    ("sync", "irate_box_sync_port"),
):
    check(
        f"/{app}/ on the hub's port redirects to its origin, path and query kept, and keeps the off switch",
        re.search(
            rf"location /{app}/ \{{\s*if \(\$irate_box_off_{app}\) \{{ return 404; \}}\s*return 302 \$scheme://\$host:\${port}\$request_uri;",
            hub,
        )
        is not None
        and f"location = /{app} {{ return 302 $scheme://$host:${port}/{app}/; }}" in hub,
    )
check(
    "the hub's port serves none of them: no static build, no relay, no Syncthing",
    "alias @APPS@/draw/" not in hub
    and "alias @APPS@/mermaid/" not in hub
    and "3002" not in hub
    and "8384" not in re.sub(r"(?m)^\s*#.*$", "", hub),
)
check(
    "Excalidraw's origin: the static build and its login switch, the relay, the store and gallery without the "
    "session cookie, the hub's app bar may frame it, the link home goes to the hub",
    "alias @APPS@/draw/;" in draw
    and "auth_basic $irate_box_auth_draw;" in draw
    and "include @ACCESS@.d/gate-draw.conf*;" in draw
    and "proxy_pass http://127.0.0.1:3002;" in draw
    and draw.count("proxy_set_header Cookie $irate_box_app_cookie;") == 2
    and draw.count("client_max_body_size 50m;") == 2
    and "frame-ancestors 'self' $scheme://$host:$irate_box_hub_port $scheme://$host" in draw
    and "location = / { return 302 $scheme://$host:$irate_box_hub_port/; }" in draw
    and "location = /hub-return.js" in draw
    and "location / { return 404; }" in draw,
)
check(
    "Mermaid's origin: the same, without the relay",
    "alias @APPS@/mermaid/;" in mermaid_
    and "include @ACCESS@.d/gate-mermaid.conf*;" in mermaid_
    and "3002" not in mermaid_
    and mermaid_.count("proxy_set_header Cookie $irate_box_app_cookie;") == 2
    and "frame-ancestors 'self' $scheme://$host:$irate_box_hub_port $scheme://$host" in mermaid_
    and "location / { return 404; }" in mermaid_,
)
check(
    "Syncthing's origin: the box's own login or an admin session, the loopback Host, no hub cookie, nothing else",
    'auth_basic "Irate-Box";' in syncb
    and "include @ACCESS@.d/gate-admin.conf*;" in syncb
    and "proxy_set_header Host 127.0.0.1:8384;" in syncb
    and "proxy_set_header Cookie $irate_box_app_cookie;" in syncb
    and "proxy_pass http://127.0.0.1:8384/;" in syncb
    and "location / { return 404; }" in syncb
    and "location @irate_box_login { return 302 $scheme://$host:$irate_box_hub_port/account.html?next=$uri; }" in syncb,
)
check(
    "  the hub's app bar may frame it: Syncthing's own SAMEORIGIN dropped, the hub's port named",
    "proxy_hide_header X-Frame-Options;" in syncb
    and "frame-ancestors 'self' $scheme://$host:$irate_box_hub_port $scheme://$host" in syncb,
)
check(
    "none of the three origins sends the hub the front's secret, and each clears the header",
    all("include @FRONT@" not in b and 'proxy_set_header X-Irate-Front ""' in b for b in (draw, mermaid_, syncb)),
)
inst = (REPO / "install.sh").read_text()
check(
    "install.sh fills in the notes port",
    "NOTES_PORT=8091" in inst
    and 'HUB_NOTES_PORT="$NOTES_PORT"' in inst
    and '"$CODE/irate-box" nginx_front "$@"' in inst,
)
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "the updater's nginx -t fills it in too, the TLS includes and ports among them",
    '"@NOTES_PORT@": "8091"' in hc and '"@TLS@": f"{tmp}/tls"' in hc and '"@GIT_TLS_PORT@": "8493"' in hc,
)
# (as installed: the front's secret file has its path)
installed = secdoctor._nginx_servers(text.replace("@FRONT@", "/etc/nginx/irate-box-front.conf"))
place = {k: next((tuple(p) for p, b in installed if re.search(pat, b)), None) for k, _, pat in secdoctor.OWN_ORIGIN}
check(
    "the doctor sees notes, books, cgit, the administration pages, Excalidraw, Mermaid and Syncthing on their own origins",
    place
    == {
        "notes": ("8091",),
        "wiki": ("8092",),
        "git": ("8093",),
        "admin": ("8095",),
        "draw": ("8096",),
        "mermaid": ("8097",),
        "sync": ("8098",),
    },
    place,
)
# The hub's session cookie never reaches an app on a sibling port.
books, sync = by_port.get(("8092",), ""), hub
check(
    "the hub's session cookie is stripped for notes, books, cgit and Syncthing",
    "map $http_cookie $irate_box_app_cookie" in text
    and notes.count("proxy_set_header Cookie $irate_box_app_cookie;") == 1
    and books.count("proxy_set_header Cookie $irate_box_app_cookie;") == 2
    and cgit.count("fastcgi_param HTTP_COOKIE $irate_box_app_cookie;") == 2
    and re.search(
        r"location /sync/ \{.*?proxy_set_header Cookie \$irate_box_app_cookie;.*?proxy_pass http://127\.0\.0\.1:8384/;",
        by_port.get(("8098",), ""),
        re.S,
    )
    is not None,
)
caddy = expand((REPO / "config" / "Caddyfile").read_text())
check(
    "  and on Caddy",
    caddy.count('header_up Cookie "irate_session=[^;]*;?\\s*" ""') == 9,
    caddy.count("header_up Cookie"),
)
# Caddy, the fallback: the same origins. Each site with its snippet in place.
csites = {tuple(p): b for p, b in secdoctor._caddy_servers(secdoctor.parse_caddy(caddy))}
chub, cnotes, cwiki, cgit_ = (csites.get((p,), "") for p in ("80", "8091", "8092", "8093"))
check(
    "Caddy: nine sites: the hub, add-ons, notes, books, cgit, administration, Excalidraw, Mermaid, Syncthing",
    sorted(csites) == [("80",), ("8090",), ("8091",), ("8092",), ("8093",), ("8095",), ("8096",), ("8097",), ("8098",)],
    sorted(csites),
)
check(
    "Caddy: the hub's port runs no cgit, Kiwix or SilverBullet, but still serves clones and pushes",
    "cgit.cgi" not in chub
    and "127.0.0.1:8081" not in chub
    and "silverbullet" not in chub
    and "git-http-guard.py" in chub
    and "uri /internal/git-access?soft=1" in chub,
)
for app in ("wiki", "notes", "git"):
    check(
        f"Caddy: /{app}/ on the hub's port keeps the off switch, then redirects to its origin by scheme",
        re.search(
            rf"import \S+/{app}-off\.caddy\*\n[^{{}}]*?redir \{{scheme\}}://\{{host\}}:\{{irate_box_{app}_port\}}\S* 302",
            chub,
        )
        is not None,
    )
    check(
        "  and its port by scheme: https to its TLS twin",
        re.search(
            rf"map \{{scheme\}} \{{irate_box_{app}_port\}}\nhttps \{{\$HUB_{app.upper()}_TLS_PORT:849\d\}}\ndefault \{{\$HUB_{app.upper()}_PORT:809\d\}}",
            chub,
        )
        is not None,
    )
check(
    "Caddy: /git-private/'s web view on the hub's port redirects too",
    chub.count("redir {scheme}://{host}:{irate_box_git_port}{http.request.orig_uri} 302") == 2,
)
check(
    "Caddy: the hub's port still refuses /notes/.shell, .proxy, .runtime",
    "@capability path_regexp (?i)^/notes/\\.(shell|proxy|runtime)" in chub,
)
check(
    "Caddy: the notes port: the refusals, the login switch, the socket, nothing else",
    "@capability path_regexp (?i)^/notes/\\.(shell|proxy|runtime)" in cnotes
    and "/notes.caddy*" in cnotes
    and "reverse_proxy unix//run/silverbullet/silverbullet.sock" in cnotes
    and cnotes.rstrip().endswith("respond 404"),
)
check(
    "Caddy: the books' port: the login switch, Kiwix, nothing else",
    "/wiki.caddy*" in cwiki and "reverse_proxy 127.0.0.1:8081" in cwiki and cwiki.rstrip().endswith("respond 404"),
)
check(
    "Caddy: cgit's origin: public and private views, the hub's app bar may frame it, clone URLs name the hub's port",
    cgit_.count("env SCRIPT_FILENAME /usr/lib/cgit/cgit.cgi") == 2
    and "/git.caddy*" in cgit_
    and "admin-gate.caddy*" in cgit_
    and cgit_.count("frame-ancestors 'self' {scheme}://{host}:{irate_box_hub_port} {scheme}://{host}\"") == 2
    and cgit_.count("env HTTP_HOST {host}") == 2,
)
check(
    "Caddy: cgit's origin: a clone or push sent here goes to the hub's port",
    "redir @smart {scheme}://{host}:{irate_box_hub_port}{uri} 302" in cgit_ and "git-http-guard.py" not in cgit_,
)
check(
    "Caddy: cgit's origin serves everything its pages load",
    "@cgit_assets path /palette.css /cgit-hub.css /themes.js /cgit-hub.js /themes/*" in cgit_
    and "handle_path /git-static/*" in cgit_
    and "handle_path /git-hl/*" in cgit_,
)
check(
    "Caddy: no redirect between origins with a fixed port",
    not re.search(r"redir .*://\{host\}:\d", caddy),
)
cadmin = csites.get(("8095",), "")
check(
    "Caddy: /admin on the hub's port redirects to the administration port, and only that site sends the secret",
    "redir /admin {scheme}://{host}:{irate_box_admin_port}/admin/ 302" in chub
    and "redir /admin/* {scheme}://{host}:{irate_box_admin_port}{uri} 302" in chub
    and "HUB_FRONT_SECRET" not in chub
    and cadmin.count("header_up X-Irate-Front {env.HUB_FRONT_SECRET}") == 3  # first use, /admin/*, the thumbnails
    and "request_header -X-Irate-Front" in cadmin
    and "request_header -X-Irate-Front" in chub,
)
check(
    "  the administration site: the login or an admin session, first use, the thumbnails, the hub's pages sent to the hub",
    "handle /admin/*" in cadmin
    and "admin-gate.caddy*" in cadmin
    and "@unclaimed file" in cadmin
    and "path_regexp ^/api/saves/[^/]+/thumb$" in cadmin
    and "redir {scheme}://{host}:{irate_box_hub_port}{uri} 302" in cadmin
    and "{$HUB_ADMIN_PORT:8095}" in chub,
)
cdraw, cmermaid, csync = (csites.get((p,), "") for p in ("8096", "8097", "8098"))
check(
    "Caddy: Excalidraw, Mermaid and Syncthing on origins of their own; the hub's port redirects, keeping the off switch",
    all(
        re.search(
            rf"import \S+/{a}-off\.caddy\*\n[^{{}}]*?redir \{{scheme\}}://\{{host\}}:\{{irate_box_{a}_port\}}\{{uri\}} 302",
            chub,
        )
        is not None
        for a in ("draw", "mermaid", "sync")
    )
    and all(
        f"redir /{a} {{scheme}}://{{host}}:{{irate_box_{a}_port}}/{a}/ 302" in chub for a in ("draw", "mermaid", "sync")
    )
    and "HUB_DRAW_ROOT" not in chub
    and "HUB_MERMAID_ROOT" not in chub
    and "127.0.0.1:8384" not in chub
    and "127.0.0.1:3002" not in chub
    and "/draw.caddy*" in cdraw
    and "reverse_proxy 127.0.0.1:3002" in cdraw
    and "/mermaid.caddy*" in cmermaid
    and "127.0.0.1:3002" not in cmermaid
    and "reverse_proxy 127.0.0.1:8384" in csync
    and "admin-gate.caddy*" in csync
    and "header_up Host {upstream_hostport}" in csync
    and all(
        "frame-ancestors 'self' {scheme}://{host}:{irate_box_hub_port} {scheme}://{host}" in c
        for c in (cdraw, cmermaid, csync)
    )
    and "header_down -X-Frame-Options" in csync
    and all("HUB_FRONT_SECRET" not in c and "request_header -X-Irate-Front" in c for c in (cdraw, cmermaid, csync)),
)
for name in ("notes", "wiki", "git", "admin", "draw", "mermaid", "sync"):
    check(
        f"Caddy: the {name} origin has a TLS twin",
        f'"{name}"' in (REPO / "irate_box/root/tls.py").read_text().split("_front_files", 1)[1].split("out = {}", 1)[0],
    )
ctx = {"front_kind": "caddy", "front": secdoctor.parse_caddy(caddy)}
seen = {f["id"]: f["status"] for f in secdoctor.step_origins(ctx)}
check(
    "the doctor sees notes, books, cgit and the administration pages on their own origins under Caddy too",
    seen
    == {
        "origin-notes": "ok",
        "origin-wiki": "ok",
        "origin-git": "ok",
        "origin-admin": "ok",
        "origin-draw": "ok",
        "origin-mermaid": "ok",
        "origin-sync": "ok",
    },
    seen,
)
# The front's second secret (server.py VIA_SECRET, review V2 L10): on every request to the hub, beside the headers it
# vouches for, and on no other app's.
raw = (REPO / "config" / "irate-box.nginx").read_text()
lines = raw.splitlines()
xff = [i for i, line in enumerate(lines) if line.strip() == "proxy_set_header X-Forwarded-For $remote_addr;"]
check(
    "nginx: the second secret's map included once, at the top level",
    raw.count("include @VIA@;") == 1 and raw.index("include @VIA@;") < raw.index("server {"),
)
check(
    "nginx: every block that sets X-Forwarded-For sets the second secret beside it",
    xff and all(lines[i + 1].strip() == "proxy_set_header X-Irate-Via $irate_box_via;" for i in xff),
    [i + 1 for i in xff if lines[i + 1].strip() != "proxy_set_header X-Irate-Via $irate_box_via;"],
)
check(
    "install.sh: the map gives it to the hub's upstream alone (volatile: never kept from an auth_request)",
    "map $proxy_host $irate_box_via {" in inst
    and '"\\tvolatile;\\n"' in inst
    and 'irate_box_hub "{via_secret}"' in inst
    and "'\\tdefault \"\";\\n'" in inst
    and 'HUB_NGINX_VIA="$NGINX_VIA"' in inst
    and "HUB_VIA_SECRET=" in inst,
)
check("the updater's nginx -t fills in a stand-in map", '"@VIA@"' in hc)
dropin = inst.split("caddy.service.d/irate-box.conf <<-EOF", 1)[1].split("\tEOF", 1)[0]
check(
    "Caddy's drop-in starts it without --environ (the package's prints the secrets to the journal)",
    "\t\tExecStart=\n" in dropin
    and "run --config /etc/caddy/Caddyfile" in dropin
    and not any("--environ" in line for line in dropin.splitlines() if "ExecStart=" in line),
    dropin,
)
check(
    "  secrets already in Caddy's journal are made again; the server key's group follows the front",
    "journalctl -q -u caddy" in inst
    and 'rm -f "$ETC/front-secret.env"' in inst
    and 'chgrp caddy "$ETC/tls/server.key"' in inst
    and 'chgrp root "$ETC/tls/server.key"' in inst,
)
from irate_box.hub import access

every = access.clean({app: "users" for app in access.ROUTED} | {"my-addon": "users"})
generated = "\n".join(
    [caddy, access.addon_caddy_routes(every, [{"id": "my-addon", "capabilities": {}}], "HASH")]
    + list(access.caddy_snippets(every, "HASH").values())
    + list(access.caddy_snippets(access.clean({app: "private" for app in access.ROUTED}), "HASH").values())
    + [access.caddy_admin_gate(a, b, c) for a in (True, False) for b in (True, False) for c in (True, False)]
    + [
        content
        for transport in ("http", "discovery")
        for path, content in tls._plain_files(transport, "warning").items()
        if path.name.startswith("caddy")
    ]
)


def caddy_proxies(text):
    """Each reverse_proxy or forward_auth: its upstream line and its block, if it has one."""
    out = []
    for match in re.finditer(r"(?m)^\s*(reverse_proxy|forward_auth)\b([^\n{]*)(\{?)", text):
        body, depth, i = "", 1, match.end()
        if match[3]:
            while depth and i < len(text):
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            body = text[match.end() : i]
        out.append((match[2].strip(), body))
    return out


proxies = caddy_proxies(generated)
to_hub = [(up, body) for up, body in proxies if up.endswith("127.0.0.1:8000")]
check(
    "Caddy: every reverse_proxy and forward_auth to the hub sends the second secret",
    len(to_hub) > 20 and all("header_up X-Irate-Via {env.HUB_VIA_SECRET}" in body for _, body in to_hub),
    [up for up, body in to_hub if "X-Irate-Via" not in body],
)
check(
    "Caddy: no other upstream gets it",
    all("HUB_VIA_SECRET" not in body for up, body in proxies if not up.endswith("127.0.0.1:8000")),
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
