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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
