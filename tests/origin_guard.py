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
text = tmpl.replace("@PORT@", "80").replace("@ADDON_PORT@", "8090").replace("@NOTES_PORT@", "8091")
servers = secdoctor._nginx_servers(text)
by_port = {tuple(p): b for p, b in servers}
hub, notes = by_port.get(("80",), ""), by_port.get(("8091",), "")
check("three server blocks: the hub, add-ons, notes", sorted(by_port) == [("80",), ("8090",), ("8091",)], sorted(by_port))
check("the hub's port does not proxy to SilverBullet", "silverbullet" not in re.sub(r"(?m)^\s*#.*$", "", hub))
check("/notes/ on the hub's port redirects to the notes port, and keeps the off switch",
      re.search(r"location /notes/ \{\s*if \(\$irate_box_off_notes\) \{ return 404; \}\s*return 302 \$scheme://\$host:8091\$request_uri;", hub) is not None)
check("the hub's port still refuses /notes/.shell, .proxy, .runtime", "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in hub)
check("the notes port: the same refusals", "location ~* ^/notes/\\.(shell|proxy|runtime) { return 403; }" in notes)
check("the notes port: the login switch and the socket",
      "auth_basic $irate_box_auth_notes;" in notes and "proxy_pass http://unix:/run/silverbullet/silverbullet.sock;" in notes
      and "include @ACCESS@;" in notes)
check("the notes port serves nothing but notes", "location / { return 404; }" in notes)
inst = (REPO / "install.sh").read_text()
check("install.sh fills in the notes port", "NOTES_PORT=8091" in inst and 's|@NOTES_PORT@|$NOTES_PORT|g' in inst)
check("the updater's nginx -t fills it in too", '"@NOTES_PORT@": "8091"' in (REPO / "irate_box/root/hub_control.py").read_text())
place = {k: next((tuple(p) for p, b in servers if re.search(pat, b)), None) for k, _, pat in secdoctor.OWN_ORIGIN}
check("the doctor sees notes on their own origin", place["notes"] == ("8091",), place)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
