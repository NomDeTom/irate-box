# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The joint report (secdoctor.joint) over the Lyra's own saved audit (tests/fixtures/lyra-secdoctor-2026-10-09.json,
its /admin/security of 2026-10-09 01:57, scrubbed): nothing the sources said is lost; what several say about one thing
is one item; every item ends in something to do; the CIS sections tiered as on the page; the counts the page's badge
shows. When the cross-reference table grows (secdoctor_xref), the counts here move on purpose. python3 tests/sim_secdoctor_lyra.py"""
import json, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

fx = json.loads((REPO / "tests" / "fixtures" / "lyra-secdoctor-2026-10-09.json").read_text())
j = sd.joint(fx["steps"])
items = {i["key"]: i for i in j["items"]}
said = {(l["source"], l["id"]) for i in j["items"] for l in i["lines"]}
loose = [(f.get("source", "doctor"), f["id"]) for s in fx["steps"] for f in s["findings"]
         if f["status"] != "ok" and not f.get("accepted") and (f.get("source", "doctor"), f["id"]) not in said]
check("nothing lost: every finding not fine is in an item (before #164 most of the doctor's were only counted)", not loose, loose)
merged = {k: sorted(i["sources"]) for k, i in items.items() if len(i["sources"]) > 1}
check("what the doctor and the Security page both say is one item each", all(merged.get(k) == ["doctor", "security-page"] for k in
      ("setting:firewall", "service:tcp/1883", "setting:kernel-info", "setting:sudo-all", "setting:apt-trust")), merged)
import copy  # noqa: E402
steps = copy.deepcopy(fx["steps"])
for s in steps:
    for f in list(s["findings"]):
        if f["id"] in ("addon-mqtt", "page-port-tcp-1883", "port-tcp-1883"):
            g = json.loads(json.dumps(f).replace("mqtt", "irc").replace("1883", "6667"))
            s["findings"].append(g)
ji = {i["key"]: i for i in sd.joint(steps)["items"]}
irc = ji.get("service:tcp/6667", {})
check("IRC: the doctor's add-on finding and the page's port 6667 as one item, pointing at Add-ons",
      sorted(irc.get("sources", [])) == ["doctor", "security-page"] and (irc.get("do") or {}).get("go") == "addons"
      and "IRC" in (irc.get("do") or {}).get("say", ""), {k: v.get("sources") for k, v in ji.items() if "6667" in k or "irc" in k})
check("  CIS 1.9 with the page's security updates: one item, a suggestion (the page found none waiting)",
      merged.get("setting:security-updates") == ["debian-cis", "security-page"] and items["setting:security-updates"]["tier"] == "suggest")
check("  the page's automatic updates as the table names them (it was an item of its own)", "setting:unattended" in items
      and items["setting:unattended"]["title"] == "Automatic security updates" and "finding:security-page:page-unattended" not in items, sorted(items))
nothing = [k for k, i in items.items() if not (i["do"] or i["fix"] or i["cmd"] or i["page"])]
check("every item ends in something to do: a button, a place, a command or the words", not nothing, nothing)
tiers = {k.removeprefix("setting:cis-"): i["tier"] for k, i in items.items() if k.startswith("setting:cis-")}
check("every CIS section tiered; the ones this board can't do said so", all(tiers.values())
      and {s for s, t in tiers.items() if t == "not-here"} == {"1.5", "1.7", "4.1", "99.1", "99.3", "99.4"}, tiers)
check("the counts after merging (the badge): 1 to fix, 20 to look at, 19 suggestions, 6 not for this board",
      j["after"] == {"problem": 1, "warn": 20, "suggest": 19, "not_here": 6}, j["after"])
check("the one to fix is the floor", [k for k, i in items.items() if i["status"] == "problem" and not i["tier"]] == ["setting:firewall"])

# The deep audit as it ran on the Lyra after #166 (tests/fixtures/lyra-security-deep-2026-10-09.json, 10:46): its
# CIS checks one by one, Lynis matched by test and by sshd option. Read as the doctor reads its kept report,
# beside the doctor's and the Security page's own findings above.
import os, shutil, tempfile  # noqa: E402
T = Path(tempfile.mkdtemp(prefix="secdeep-"))
(T / "control").mkdir()
shutil.copy(REPO / "tests/fixtures/lyra-security-deep-2026-10-09.json", T / "control" / "security-deep.json")
from irate_box.root import deepaudit  # noqa: E402
deepaudit.REPORT = T / "control" / "security-deep.json"
import time as _t  # noqa: E402
_now = _t.time
_t.time = lambda: json.loads(deepaudit.REPORT.read_text())["at"] + 3600   # an hour after it, not "old"
cis, lyn = sd._deep_findings("debian-cis"), sd._deep_findings("lynis")
_t.time = _now
steps = [s for s in fx["steps"] if not any(f.get("source") in ("debian-cis", "lynis") for f in s["findings"])]
steps += [{"title": "The CIS benchmark", "findings": cis}, {"title": "Lynis", "findings": lyn}]
j2 = sd.joint(steps)
it2 = {i["key"]: i for i in j2["items"]}
check("Lynis's suggestions merge with the CIS sections they ask about (5.2: sshd's options one by one)",
      sorted(it2["setting:cis-5.2"]["sources"]) == ["debian-cis", "lynis"] and len(it2["setting:cis-5.2"]["checks"]) >= 10, it2["setting:cis-5.2"]["sources"])
check("a CIS check split out (root login) beside a fine Security page line: a suggestion, not to look at",
      it2["setting:ssh-root-login"]["tier"] == "suggest" and sorted(it2["setting:ssh-root-login"]["sources"]) == ["debian-cis", "security-page"], it2.get("setting:ssh-root-login"))
check("  beside the doctor's own warning (sudo ALL): a real item still", it2["setting:sudo-all"]["tier"] is None and "doctor" in it2["setting:sudo-all"]["sources"])
nothing2 = [k for k, i in it2.items() if not (i["do"] or i["fix"] or i["cmd"] or i["page"])]
check("every item ends in something to do: a check's own command is the item's (ASLR, core dumps)", not nothing2
      and it2["setting:aslr"]["do"]["cmd"].startswith("echo kernel.randomize_va_space=2"), nothing2)
check("the CIS checks keep a command each where their script says one plainly", sum(1 for i in it2.values() for c in i["checks"] if c.get("cmd")) >= 40)
check("the counts with the Lyra's deep audit: 1 to fix, 20 to look at (as before), the rest suggestions or not for this board",
      (j2["after"]["problem"], j2["after"]["warn"]) == (1, 20), j2["after"])
shutil.rmtree(T, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
