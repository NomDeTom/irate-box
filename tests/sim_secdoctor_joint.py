# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor, stage 3 (next-work plan step 31, security-doctor-plan §5, §6, §7.5–6),
offline: the cross-reference table (every doctor and Security page id in it still exists); the
Security page's scan as a source; imported OpenVAS and nmap reports, checked field by field, kept,
and turned into findings (CVSS bands, a port the box wasn't listening on, a report from before the
ports changed); and the joint report merging what several sources say about one thing, the items
only one source saw, the counts after merging. python3 tests/sim_secdoctor_joint.py"""
import json, os, re, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="joint-"))
(T / "control").mkdir()
os.environ.update(HUB_STATE_DIR=str(T), HUB_KITS_ROOT=str(T / "kits"))
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd, secdoctor_xref as xref  # noqa: E402
from irate_box.hub import secimports  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# The table's ids still exist where they can be checked.
doctor_src = (REPO / "irate_box/root/secdoctor.py").read_text()
page_src = (REPO / "irate_box/root/security.py").read_text()
for key, e in xref.XREF.items():
    for i in e.get("doctor", []):
        check(f"xref {key}: the doctor still has {i}", f'F("{i}"' in doctor_src, i)
    for i in e.get("security-page", []):
        name = i.removeprefix("kernel-")
        check(f"xref {key}: the Security page still has {i}", f'finding("{i}"' in page_src or (i.startswith("kernel-") and f'"{name}": {{"title"' in page_src), i)
check("a port is a service, from any source", xref.about("security-page", "port-tcp-80") == {"kind": "service", "key": "tcp/80"})
check("an unlisted check stands alone", xref.about("debian-cis", "1.4.1_install_tripwire") is None)

# The Security page's scan as a source.
scan = {"at": time.time(), "listeners": [{"proto": "tcp", "port": 80}, {"proto": "tcp", "port": 22}],
        "findings": [{"id": "port-tcp-80", "title": "the hub (nginx)", "status": "ok", "detail": "TCP 80.", "fix": ""},
                     {"id": "port-tcp-22", "title": "SSH", "status": "warn", "detail": "TCP 22.", "fix": "Limit it to the LAN."},
                     {"id": "kernel-links", "title": "Kernel link protections", "status": "problem", "detail": "off", "fix": "Turn them on"}]}
(T / "control" / "security.json").write_text(json.dumps(scan))
page = {f["id"]: f for f in sd.step_security_page({})}
check("the Security page's findings, from it, about what they are about", page["page-kernel-links"]["source"] == "security-page"
      and page["page-kernel-links"]["about"] == {"kind": "setting", "key": "kernel-links"} and page["page-port-tcp-22"]["about"] == {"kind": "service", "key": "tcp/22"})

# Imports: checked, kept with the box's ports at the time.
for bad, why in (({"kind": "qualys", "results": []}, "kind"), ({"kind": "nmap", "results": [{"port": 70000}]}, "port"),
                 ({"kind": "openvas", "results": [{"port": 80, "cvss": 11}]}, "cvss"), ({"kind": "openvas", "results": [{"port": 80, "cvss": 5, "cves": ["x"]}]}, "cves"),
                 ({"kind": "nmap", "results": [{"port": 80}] * 1001}, "results"), ({"kind": "nmap", "ran": "yesterday", "results": []}, "ran"),
                 ({"kind": "nmap", "results": ["80"]}, "object")):
    try:
        secimports.validate(bad); check(f"an import refused: {why}", False)
    except ValueError as exc:
        check(f"an import refused: {why}", why in str(exc), str(exc))
v = secimports.validate({"kind": "openvas", "results": [{"port": 80, "cvss": 5, "name": "x\x00y" * 200, "cves": ["CVE-2026-1234"]}]})
check("text is trimmed and control characters go", 190 <= len(v["results"][0]["name"]) <= 200 and "\x00" not in v["results"][0]["name"])
msg = secimports.save({"kind": "nmap", "ran": time.time() - 3600, "name": "scan.xml", "results": [
    {"port": 80, "proto": "tcp", "state": "open", "service": "http nginx"}, {"port": 22, "proto": "tcp", "state": "open", "service": "ssh"},
    {"port": 9999, "proto": "tcp", "state": "open", "service": ""}, {"port": 23, "proto": "tcp", "state": "closed"}]})
kept = json.loads((T / "security-imports" / "nmap.json").read_text())
check("kept: open ports only, with the box's ports at the time", [r["port"] for r in kept["results"]] == [80, 22, 9999] and kept["box_ports"] == ["tcp/22", "tcp/80"], kept)
secimports.save({"kind": "openvas", "ran": time.time() - 3600, "results": [
    {"port": 80, "proto": "tcp", "cvss": 7.5, "name": "nginx old", "solution": "Update nginx", "cves": ["CVE-2026-1234"]},
    {"port": 80, "proto": "tcp", "cvss": 5.0, "name": "TLS missing"}, {"port": 22, "proto": "tcp", "cvss": 2.1, "name": "SSH weak MAC"},
    {"port": "general", "proto": "tcp", "cvss": 4.3, "name": "TCP timestamps"}]})
imp = {f["id"]: f for f in sd.step_imports({})}
check("OpenVAS by port: CVSS 7 and over a problem, its fix the worst one's", imp["openvas-tcp-80"]["status"] == "problem" and imp["openvas-tcp-80"]["fix"] == "Update nginx"
      and "nginx old (CVSS 7.5); TLS missing (CVSS 5)" in imp["openvas-tcp-80"]["detail"])
check("  under 4: listed, fine", imp["openvas-tcp-22"]["status"] == "ok")
check("  a finding for the box as a whole has no port to merge on", imp["openvas-tcp-general"]["about"] is None and imp["openvas-tcp-general"]["status"] == "warn")
check("nmap: a port the box serves is fine; one it wasn't listening on is worth a look", imp["nmap-tcp-80"]["status"] == "ok" and imp["nmap-tcp-9999"]["status"] == "warn")
check("no ports changed: no warning about it", not any(k.endswith("ports-changed") for k in imp))
scan["listeners"].append({"proto": "tcp", "port": 8094})
(T / "control" / "security.json").write_text(json.dumps(scan))
imp = {f["id"]: f for f in sd.step_imports({})}
check("the box's ports changed since: said, for each report", imp["nmap-ports-changed"]["status"] == "warn" and "tcp/8094" in imp["nmap-ports-changed"]["detail"]
      and "openvas-ports-changed" in imp)

# The joint report.
doctor = [sd.F("kernel-links", "Link protections off", "problem", "", "Turn them on", about=xref.about("doctor", "kernel-links")),
          sd.F("front", "fine", "ok", ""), sd.F("git-public", "A public repo is open to push", "warn", "")]
steps = [{"findings": doctor}, {"findings": list(page.values())}, {"findings": list(imp.values())}]
j = sd.joint(steps, {"doctor": time.time()})
items = {i["about"]["key"]: i for i in j["items"]}
check("the doctor and the Security page on kernel links: one item, both sources, a problem", items["kernel-links"]["sources"] == ["doctor", "security-page"]
      and items["kernel-links"]["status"] == "problem" and items["kernel-links"]["title"].startswith("Kernel link protections"))
check("TCP 80: the page (fine), nmap (fine) and OpenVAS (a problem): one item, three sources, a problem", sorted(items["tcp/80"]["sources"]) == ["nmap", "openvas", "security-page"] and items["tcp/80"]["title"].endswith("(TCP 80)")
      and items["tcp/80"]["status"] == "problem" and items["tcp/80"]["fix"] == "Update nginx", items["tcp/80"])
check("TCP 9999: only nmap saw it, the Security page could have: listed apart", items["tcp/9999"]["alone"] and items["tcp/9999"]["could_see"] == ["openvas", "security-page"], items["tcp/9999"])
j2 = sd.joint([{"findings": [sd.F("debsecan-perl", "perl", "warn", "", source="debsecan", about={"kind": "package", "key": "perl"}),
                              sd.F("cis-5.2", "CIS 5.2", "warn", "", source="debian-cis", about={"kind": "setting", "key": "cis-5.2"}),
                              sd.F("imports-none", "Imported scans", "ok", "", source="openvas"),
                              sd.F("kernel-links", "Link protections off", "problem", "", about={"kind": "setting", "key": "kernel-links"})]}])
check("not 'alone': a package (only debsecan sees them), a CIS section (only debian-cis), or when the other source never ran",
      not any(i["alone"] for i in j2["items"]), [(i["about"]["key"], i["could_see"]) for i in j2["items"]])
check("everything fine on every side is not an item to look at", "tcp/22" not in items or items["tcp/22"]["status"] != "ok")
check("counts after merging: kernel links once, not twice", j["after"]["problem"] == 2 and sum(c["problem"] for c in j["sources"].values()) == 3, (j["after"], j["sources"]))
check("each source's coverage said", j["coverage"]["nmap"].startswith("Only which ports answer") and set(j["coverage"]) == set(j["sources"]))
rep = sd.audit()
check("the whole audit: the new steps in it, the joint report with freshness", {"security-page", "imports"} <= {s["id"] for s in rep["steps"]}
      and "nmap" in rep["joint"]["freshness"] and rep["joint"]["after"]["problem"] >= 1, rep["joint"]["freshness"])
check("the server sends the imports' summary and takes an import", "secimports.save(payload.get(\"report\"))" in (REPO / "irate_box/hub/server.py").read_text())
# Together a problem (stance review 2026-10-08 §2, secdoctor_xref.COMPOUND): each named finding present
# and not ok; a compound item in the joint report, a problem, with its own detail and fix.
pg = lambda i, st: sd.F(f"page-{i}", i, st, "", source="security-page")  # noqa: E731
jc = sd.joint([{"findings": [pg("sudo-nopasswd", "warn"), pg("ssh-password", "warn"), pg("group-disk", "ok")]}])
comp = [i for i in jc["items"] if i["about"]["kind"] == "compound"]
check("sudo-nopasswd and ssh-password both warn: one compound problem, with detail and fix",
      [c["about"]["key"] for c in comp] == ["sudo-and-passwords"] and comp[0]["status"] == "problem" and comp[0]["detail"] and comp[0]["fix"]
      and jc["after"]["problem"] == 1, comp)
jc = sd.joint([{"findings": [pg("sudo-nopasswd", "warn"), pg("ssh-password", "ok")]}])
check("  one of them fine: no compound item", not [i for i in jc["items"] if i["about"]["kind"] == "compound"])
check("every compound rule names findings that exist", all(f'finding("{i.removeprefix("page-")}"' in page_src or (i.startswith("page-group-") and '_finding(f"group-{group}"' in page_src) for c in xref.COMPOUND for s, i in c["needs"] if s == "security-page"),
      [i for c in xref.COMPOUND for s, i in c["needs"]])

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
