# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Apps on origins of their own (next-work plan step 11): notes are served only from their own
port, with the same login switch and refusals as before, and /notes/ on the hub's port only
redirects there; the doctor can tell which origin each app is on. python3 tests/origin_guard.py"""
import re, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

tmpl = (REPO / "config" / "irate-box.nginx").read_text()
text = tmpl.replace("@PORT@", "80").replace("@ADDON_PORT@", "8090").replace("@NOTES_PORT@", "8091").replace("@WIKI_PORT@", "8092").replace("@GIT_PORT@", "8093")
text = text.replace("@TLS_PORT@", "443").replace("@ADDON_TLS_PORT@", "8490").replace("@NOTES_TLS_PORT@", "8491").replace("@WIKI_TLS_PORT@", "8492").replace("@GIT_TLS_PORT@", "8493")
servers = secdoctor._nginx_servers(text)
by_port = {tuple(p): b for p, b in servers}
hub, notes = by_port.get(("80",), ""), by_port.get(("8091",), "")
check("five server blocks: the hub, add-ons, notes, books, cgit", sorted(by_port) == [("80",), ("8090",), ("8091",), ("8092",), ("8093",)], sorted(by_port))
cgit = by_port.get(("8093",), "")
check("the hub's port runs no cgit, but still serves clones and pushes", "cgit.cgi" not in hub and "git-http-backend" in hub
      and "auth_request /_irate_git_access;" in hub)
check("cgit's pages on the hub's port redirect to its origin (its TLS twin over HTTPS)", "return 302 $scheme://$host:$irate_box_git_port$request_uri;" in hub)
check("cgit's origin: its clone URLs name the hub's port", cgit.count("fastcgi_param HTTP_HOST $host:80;") == 2)
check("cgit's origin: the hub's app bar may frame it", cgit.count("frame-ancestors 'self' $scheme://$host:$irate_box_hub_port $scheme://$host\"") == 2)
check("cgit's origin: a clone or push sent here goes to the hub's port", re.search(r"git-receive-pack\)\$ \{\s*return 302 \$scheme://\$host:\$irate_box_hub_port\$request_uri;", cgit) is not None)
# HTTPS (step 15, certificates-plan stage 2): every origin has its TLS twin in the same server
# block (so the same routes), redirects keep to the scheme they came in on, and never HSTS.
for (port,), name in ((("80",), "main"), (("8090",), "addons"), (("8091",), "notes"), (("8092",), "wiki"), (("8093",), "git")):
    check(f"TLS twin: the {name} server block includes its own", f"include @TLS@/nginx-{name}.conf*;" in by_port.get((port,), ""))
maps = {k: (h, d) for k, h, d in re.findall(r"map \$scheme \$(irate_box_\w+_port)\s*\{\s*https (\d+);\s*default (\d+);", text)}
check("  each origin's port by scheme: http to its own, https to its twin", maps == {"irate_box_hub_port": ("443", "80"), "irate_box_addon_port": ("8490", "8090"),
      "irate_box_notes_port": ("8491", "8091"), "irate_box_wiki_port": ("8492", "8092"), "irate_box_git_port": ("8493", "8093")}, maps)
check("  no redirect between origins with a fixed port", not re.search(r"\$scheme://\$host:\d", text), re.findall(r"\$scheme://\$host:\d+", text))
caddy = (REPO / "config" / "Caddyfile").read_text()
directives = lambda t: "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("#"))  # noqa: E731
check("  never HSTS (port 80 must keep working for the captive portal)", "Strict-Transport-Security" not in directives(text)
      and "Strict-Transport-Security" not in directives(caddy))
head = (REPO / "config" / "cgit-head.html").read_text()
served = all(f"location = {f}" in cgit for f in ("/palette.css", "/cgit-hub.css", "/themes.js", "/cgit-hub.js")) and "location ^~ /git-static/" in cgit and "location ^~ /git-hl/" in cgit
check("cgit's origin serves everything its pages load", served and all(s in head for s in ("/palette.css", "/cgit-hub.css", "/themes.js", "/git-hl/", "/cgit-hub.js")))
wiki = by_port.get(("8092",), "")
check("the hub's port does not proxy to Kiwix", "127.0.0.1:8081" not in hub)
check("/wiki/ on the hub's port redirects to the books' port, and keeps the off switch",
      re.search(r"location /wiki/ \{\s*if \(\$irate_box_off_wiki\) \{ return 404; \}\s*return 302 \$scheme://\$host:\$irate_box_wiki_port\$request_uri;", hub) is not None)
check("the books' port: the login switch, Kiwix, and no-cache on book pages",
      wiki.count("auth_basic $irate_box_auth_wiki;") == 2 and "proxy_pass http://127.0.0.1:8081;" in wiki and 'add_header Cache-Control "no-cache" always;' in wiki)
check("the hub's port does not proxy to SilverBullet", "silverbullet" not in re.sub(r"(?m)^\s*#.*$", "", hub))
check("/notes/ on the hub's port redirects to the notes port, and keeps the off switch",
      re.search(r"location /notes/ \{\s*if \(\$irate_box_off_notes\) \{ return 404; \}\s*return 302 \$scheme://\$host:\$irate_box_notes_port\$request_uri;", hub) is not None)
check("the hub's port still refuses /notes/.shell, .proxy, .runtime", "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in hub)
check("the notes port: the same refusals", "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in notes)
check("the notes port: the login switch and the socket",
      "auth_basic $irate_box_auth_notes;" in notes and "proxy_pass http://unix:/run/silverbullet/silverbullet.sock;" in notes
      and "include @ACCESS@;" in notes)
check("the notes port serves nothing but notes", "location / { return 404; }" in notes)
inst = (REPO / "install.sh").read_text()
check("install.sh fills in the notes port", "NOTES_PORT=8091" in inst and 's|@NOTES_PORT@|$NOTES_PORT|g' in inst)
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check("the updater's nginx -t fills it in too, the TLS includes and ports among them", '"@NOTES_PORT@": "8091"' in hc and '"@TLS@": f"{tmp}/tls"' in hc and '"@GIT_TLS_PORT@": "8493"' in hc)
place = {k: next((tuple(p) for p, b in servers if re.search(pat, b)), None) for k, _, pat in secdoctor.OWN_ORIGIN}
check("the doctor sees notes, books and cgit on their own origins", place == {"notes": ("8091",), "wiki": ("8092",), "git": ("8093",)}, place)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
