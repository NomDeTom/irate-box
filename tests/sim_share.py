# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Guests' onward internet as a scale (root/share.py; Tom, 2026-10-08: "give options, and a sliding
scale"), contained ("Fix the network sharing issue with a containment plan … Give the admin the option
to disable various parts (with security doctor findings against them when they're too much)"), and
the floor with it as one ruleset (root/firewall.py), offline: each level's rules, each part of the
containment on and off, the guests' resolver reached only by the redirect, a guest's hardware address
found, the root helper's share-set and share-allow in a safe order with the system's commands
recorded, not run, and the Security doctor's findings and switches. python3 tests/sim_share.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="share-"))
STATE, ETC = T / "state", T / "etc"
for d in (STATE / "control" / "results", STATE / "control" / "requests", ETC, T / "sysctl", T / "units"):
    d.mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(STATE), HUB_ETC_DIR=str(ETC), HUB_RUN_DIR=str(T / "run"), HUB_SYSCTL_DIR=str(T / "sysctl"),
                  HUB_UNIT_DIR=str(T / "units"), HUB_AP_LEASES=str(T / "leases"), HUB_AP_DNSMASQ=str(T / "ap-dnsmasq.conf"))
sys.path.insert(0, str(REPO))
from irate_box.root import firewall, hub_control, security, share  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

ALL = {k: True for k in share.CONTAIN}
def S(level, **off):
    return {"level": level, "forward_was": None, "contain": dict(ALL, **{k: False for k in off})}
FLOOR = {"level": "apps", "services": []}

# --- the levels' rules ---
for lv in ("users-web", "sheet-web", "sheet-all", "open"):
    r = firewall.ruleset("ap0", FLOOR, S(lv))
    check(f"{lv}: the guests' addresses masqueraded on the way out", 'ip saddr 192.168.4.0/24 oifname != "ap0" masquerade' in r)
    check(f"{lv}: replies back in, anything else to the hotspot dropped", 'oifname "ap0" ct state established,related accept' in r and 'oifname "ap0" drop' in r)
for lv in ("users-web", "sheet-web", "sheet-all"):
    r = firewall.ruleset("ap0", FLOOR, S(lv))
    check(f"{lv}: a device not let out meets the hub (its plain web requests sent there)",
          'ether saddr != @out ip daddr != 192.168.4.1 tcp dport 80 dnat ip to 192.168.4.1' in r)
    check(f"{lv}: nothing else of it leaves", r.index('iifname "ap0" drop') > r.index("ether saddr @out"))
web = firewall.ruleset("ap0", FLOOR, S("sheet-web"))
check("web only: 80, 443 and QUIC for those let out, nothing more", 'ether saddr @out tcp dport { 80, 443 } accept' in web
      and 'ether saddr @out udp dport 443 accept' in web and 'ether saddr @out accept' not in web)
check("everything: all of it for those let out", 'iifname "ap0" ether saddr @out accept' in firewall.ruleset("ap0", FLOOR, S("sheet-all")))
check("open: no sheet, everyone out", "dnat" not in firewall.ruleset("ap0", FLOOR, S("open")) and 'iifname "ap0" accept' in firewall.ruleset("ap0", FLOOR, S("open")))
check("one table with the floor, made then deleted: loading twice is loading once", web.count("table inet irate_box") == 3 and "chain hotspot" in web and "chain pre" in web)

# --- the containment, each part on and off ---
check("lan: private, CGNAT and link-local destinations dropped before anyone is let out",
      web.index("ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10") < web.index("@out tcp dport") and "192.168.0.0/16" in web and "169.254.0.0/16" in web)
check("  off: not dropped", "100.64.0.0/10" not in firewall.ruleset("ap0", FLOOR, S("sheet-web", lan=1)))
check("tunnels: nothing into Tailscale, WireGuard or a container", 'oifname "tailscale*" drop' in web and 'oifname "docker*" drop' in web and 'oifname "wg*" drop' in web)
check("  off: forwarded there too", "tailscale" not in firewall.ruleset("ap0", FLOOR, S("sheet-web", tunnels=1)))
check("by_mac: the set holds hardware addresses", "type ether_addr" in web)
ipk = firewall.ruleset("ap0", FLOOR, S("sheet-web", by_mac=1))
check("  off: addresses, and the rules ask by address", "type ipv4_addr" in ipk and "ip saddr @out tcp dport" in ipk and "ether" not in ipk)
check("dns_hold: only those let out sent to the guests' resolver", 'iifname "ap0" ether saddr @out udp dport 53 ct mark set 0x00000153 redirect to :5354' in web
      and 'iifname "ap0" udp dport 53 ct mark' not in web)
check("  off: everyone sent there", 'iifname "ap0" udp dport 53 ct mark set 0x00000153 redirect to :5354' in firewall.ruleset("ap0", FLOOR, S("sheet-web", dns_hold=1)))
check("  at open: everyone, whatever dns_hold says", 'iifname "ap0" udp dport 53 ct mark set' in firewall.ruleset("ap0", FLOOR, S("open")))
check("the guests' resolver: never asked directly (only through the redirect's mark), and through the floor when sent there",
      'iifname "ap0" udp dport 5354 ct mark != 0x00000153 drop' in web and 'iifname "ap0" tcp dport 5354 ct mark != 0x00000153 drop' in web
      and web.index("ct mark != 0x00000153 drop") < web.index("jump hotspot") and "5354 } accept" in web)

# --- the floor and sharing together ---
alone = firewall.ruleset("ap0", FLOOR, None)
check("the floor alone: nothing forwarded to or from the hotspot, no NAT, no set", 'iifname "ap0" drop' in alone and "chain pre" not in alone
      and "set out" not in alone and "5354" not in alone)
check("shared with no floor: no drop chain on what arrives, the resolver still guarded", "jump hotspot" not in firewall.ruleset("ap0", None, S("sheet-web"))
      and "5354 ct mark != " in firewall.ruleset("ap0", None, S("sheet-web")))
check("off counts as not shared", firewall.ruleset("ap0", FLOOR, S("off")) == alone)
unit = firewall.unit_text()
check("the boot unit loads the one file; stopped while sharing, forwarding goes off too", f"nft -f {firewall.RULES}" in unit
      and "share.nft" not in unit and f"if [ -f {share.SYSCTL} ]; then sysctl -q -w net.ipv4.ip_forward=0" in unit.split("ExecStop=")[1])

# --- addresses and hardware addresses ---
check("a guest's address", share.guest_address("192.168.4.23") == "192.168.4.23")
check("not the hub, not the network's ends, not another network, not a word",
      [share.guest_address(x) for x in ("192.168.4.1", "192.168.4.0", "192.168.4.255", "192.168.1.5", "x; rm")] == [None] * 5)
(T / "leases").write_text("1791500000 AA:bb:cc:00:11:22 192.168.4.23 phone 01:aa\n1791500000 de:ad:be:ef:00:01 192.168.4.24 * *\n")
check("a guest's hardware address from the DHCP leases, lower case", share.mac_of("192.168.4.23") == "aa:bb:cc:00:11:22")
check("  else from the neighbour table", share.mac_of("192.168.4.40", "192.168.4.40 dev ap0 lladdr 02:00:00:00:00:40 REACHABLE") == "02:00:00:00:00:40")
check("  else none", share.mac_of("192.168.4.41", "192.168.4.41 dev ap0 FAILED") is None)
u = share.guest_dns_unit("ap0")
check("the guests' resolver: dnsmasq on 5354, the hotspot only, no DHCP of its own", "--port=5354" in u and "--interface=ap0" in u and "dhcp" not in u.lower())
try:
    share.guest_dns_unit("ap0 --conf-file=/etc/shadow"); ok = False
except ValueError:
    ok = True
check("  an interface that is not one: refused", ok)

# --- the root helper ---
ran = []
def fake(*cmd, **kw):
    ran.append(list(cmd))
    if cmd[:3] == ("ip", "neigh", "show"):
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")
hub_control.run = fake
firewall.run = fake
msg = hub_control.share_set({"level": "sheet-web"})
public = json.loads((STATE / "control" / "share.json").read_text())
order = [" ".join(c[:3]) for c in ran]
check("share-set: one ruleset checked with nft -c, then loaded", any(c[:2] == ["nft", "-c"] for c in ran) and ["nft", "-f", str(firewall.RULES)] in ran, ran)
check("  the rules in before forwarding goes on, the guests' resolver after",
      order.index(f"nft -f {firewall.RULES}") < order.index("sysctl -q -w") < order.index(f"systemctl restart {share.GUEST_DNS_UNIT}"), order)
check("  forwarding on (a sysctl.d file, and now), the resolver's unit written and enabled", "ip_forward = 1" in share.SYSCTL.read_text()
      and ["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"] in ran and (T / "units" / share.GUEST_DNS_UNIT).is_file()
      and ["systemctl", "enable", share.GUEST_DNS_UNIT] in ran)
check("  the level and the containment for /admin; contained by default", public == {"level": "sheet-web", "contain": ALL}
      and share.load()["forward_was"] is not None, public)
check("  said plainly", "once through the sign-in sheet" in msg, msg)
ran.clear()
check("share-allow: a guest let out by its hardware address, for 12 h", "aa:bb:cc:00:11:22" in hub_control.share_allow({"ip": "192.168.4.23"})
      and ["nft", "add", "element", "inet", "irate_box", "out", "{ aa:bb:cc:00:11:22 timeout 12h }"] in ran, ran)
try:
    hub_control.share_allow({"ip": "192.168.4.99"}); ok = False
except ValueError as exc:
    ok = "no hardware address" in str(exc)
check("  no hardware address known: refused, said", ok)
for bad in ({"ip": "192.168.1.9"}, {"ip": "192.168.4.1"}, {"ip": "1.2.3.4; reboot"}):
    try:
        hub_control.share_allow(bad); ok = False
    except ValueError:
        ok = True
    check(f"share-allow refused: {bad['ip']}", ok)

# --- the Security doctor: what is shared, each part of the containment, the way back ---
(T / "loaded").write_text("")
firewall.loaded = lambda: True
f = security.share_findings()
check("the doctor, contained: one ok finding naming the level and each part, a switch to turn each off",
      [x["status"] for x in f] == ["ok"] and "once through the sign-in sheet" in f[0]["detail"]
      and [a["choice"] for a in f[0]["actions"]] == [f"share-contain-{k}-off" for k in share.CONTAIN] and all(a["confirm"] for a in f[0]["actions"]), f)
ran.clear()
print(security.fix("share-contain-lan-off", None))
f = security.share_findings()
check("a part turned off: the rules rewritten without it, a warning of its own with the way back",
      "100.64.0.0/10" not in firewall.RULES.read_text() and share.load()["contain"]["lan"] is False
      and any(x["id"] == "guest-net-lan" and x["status"] == "warn" and x["actions"][0]["choice"] == "share-contain-lan-on" for x in f)
      and next(x for x in f if x["id"] == "guest-net")["status"] == "warn", f)
print(security.fix("share-contain-lan-on", None))
check("  and back on", "100.64.0.0/10" in firewall.RULES.read_text() and all(x["status"] == "ok" for x in security.share_findings()))
for bad in ("share-contain-moon-off", "share-contain-lan-maybe"):
    try:
        security.fix(bad, None); ok = False
    except ValueError:
        ok = True
    check(f"{bad}: refused", ok)

# --- off again, then open ---
hub_control.share_set({"level": "open"})
try:
    hub_control.share_allow({"ip": "192.168.4.23"}); ok = False
except ValueError:
    ok = True
check("share-allow refused at open (everyone is out already)", ok)
try:
    hub_control.share_set({"level": "everyone"}); ok = False
except ValueError:
    ok = True
check("share-set refused: a level not offered", ok)
ran.clear()
hub_control.share_set({"level": "off"})
order = [" ".join(c[:3]) for c in ran]
check("off: forwarding off first, then the resolver, then the rules (no floor: the table gone)",
      order.index("sysctl -q -w") < order.index(f"systemctl disable --now") < order.index("nft delete table")
      and not firewall.RULES.exists() and not share.SYSCTL.exists() and not (T / "units" / share.GUEST_DNS_UNIT).exists()
      and share.load()["level"] == "off" and share.load()["forward_was"] is None, order)
check("  the containment kept for next time", share.load()["contain"] == ALL)
check("  the doctor says nothing about sharing", security.share_findings() == [])
ran.clear()
security.fix("share-contain-dns_hold-off", None)
f = security.share_findings()
check("a part turned off while nothing is shared: kept, no rules touched, and the doctor says it will be off, with the way back",
      share.load()["contain"]["dns_hold"] is False and not any(c[0] == "nft" for c in ran) and len(f) == 1 and f[0]["status"] == "ok"
      and "will be off" in f[0]["detail"] and f[0]["actions"][0]["choice"] == "share-contain-dns_hold-on", f)
security.fix("share-contain-dns_hold-on", None)
check("  back on: the doctor says nothing again", security.share_findings() == [] and share.load()["contain"] == ALL)

import shutil
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
