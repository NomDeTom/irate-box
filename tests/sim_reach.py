# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Reached from the internet: the hub's note of public visitors (hub/reach.py) and
the security doctor's step that reads it, sshd's log and the box's IPv6 addresses (secdoctor
step_internet), against a throwaway state folder and stand-in commands. python3 tests/sim_reach.py"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
T = Path(tempfile.mkdtemp(prefix="reach-"))
os.environ.update(HUB_STATE_DIR=str(T))
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
from irate_box.hub import reach  # noqa: E402
from irate_box.root import secdoctor as sd  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

check("not public: private, loopback, link-local, Tailscale's 100.64/10, ULA, rubbish",
      all(reach.public_net(a) is None for a in ("192.168.1.5", "10.0.0.1", "172.16.3.4", "127.0.0.1", "169.254.1.1",
                                               "100.101.102.103", "fd7a:115c:a1e0::1", "fe80::1", "::1", "not-an-ip", "")))
check("public: kept as its /24 or /48, never the whole address", reach.public_net("203.0.113.77") is None  # documentation range: not global
      and reach.public_net("81.2.69.160") == "81.2.69.0/24" and reach.public_net("2a00:1450:4009:81f::200e") == "2a00:1450:4009::/48"
      and reach.public_net("::ffff:81.2.69.160") == "81.2.69.0/24")
t0 = 1_800_000_000
for i in range(5):
    reach.note("81.2.69.160", now=t0 + i)
reach.note("192.168.1.5", now=t0 + 10)
d = json.loads(reach.FILE.read_text())
check("a public visitor noted (written once in the minute, counted in memory), a private one not",
      list(d["nets"]) == ["81.2.69.0/24"] and d["nets"]["81.2.69.0/24"]["count"] == 1, d)
reach.note("81.2.69.161", now=t0 + 61)
d = json.loads(reach.FILE.read_text())
check("  a minute on: written again, with the count so far", d["nets"]["81.2.69.0/24"]["count"] == 6 and d["nets"]["81.2.69.0/24"]["last"] == t0 + 61, d)
reach.FILE.write_text("{not json"); reach._seen = None
reach.note("81.2.69.160", now=t0 + 200)
check("  a torn file: started afresh, never an error", "81.2.69.0/24" in json.loads(reach.FILE.read_text())["nets"])

# The doctor's step, with sshd's log and ip's output stood in.
SSH_LOG, IP6 = [""], [""]
real_run = subprocess.run
def fake_run(args, **kw):
    if args[0] == "journalctl":
        return subprocess.CompletedProcess(args, 0, SSH_LOG[0], "")
    if args[0] == "ip":
        return subprocess.CompletedProcess(args, 0, IP6[0], "")
    return real_run(args, **kw)
sd.subprocess.run = fake_run
sd.STATE = T
def step():
    return {f["id"]: f for f in sd.step_internet({})}
reach.FILE.write_text(json.dumps({"nets": {"81.2.69.0/24": {"first": time.time() - 3600, "last": time.time() - 60, "count": 12}}}))
SSH_LOG[0] = "\n".join([
    "Accepted publickey for lyra from 192.168.1.10 port 51234 ssh2",
    "Invalid user admin from 45.33.32.156 port 40000",
    "Failed password for invalid user admin from 45.33.32.156 port 40000 ssh2",
    "Connection closed by authenticating user root 2a00:1450:4009:81f::200e port 5555 [preauth]",
    "Failed password for root from 100.101.102.103 port 1 ssh2"])
IP6[0] = ("2: wlan0    inet6 2a02:c7c:1234:5600::7/64 scope global dynamic mngtmpaddr \\       valid_lft 86000sec preferred_lft 14000sec\n"
          "2: wlan0    inet6 fd00::7/64 scope global dynamic \\       valid_lft forever preferred_lft forever\n")
f = step()
check("web: public visits in the last 7 days: a problem, by network, with what to do", f["reach-web"]["status"] == "problem"
      and "81.2.69.0/24 (12," in f["reach-web"]["detail"] and "Tailscale" in f["reach-web"]["fix"], f["reach-web"])
check("ssh: public addresses heard (not the LAN's, nor Tailscale's), none let in: a problem", f["reach-ssh"]["status"] == "problem"
      and "2 public addresses" in f["reach-ssh"]["detail"] and "none let in" in f["reach-ssh"]["detail"]
      and "100.101" not in f["reach-ssh"]["detail"] and "192.168" not in f["reach-ssh"]["detail"], f["reach-ssh"])
check("ipv6: a public address (not the ULA): a warning, the router's firewall named", f["reach-ipv6"]["status"] == "warn"
      and "2a02:c7c:1234:5600::7 (wlan0)" in f["reach-ipv6"]["detail"] and "fd00" not in f["reach-ipv6"]["detail"], f["reach-ipv6"])
reach.FILE.write_text(json.dumps({"nets": {"81.2.69.0/24": {"first": 1, "last": time.time() - 8 * 86400, "count": 3}}}))
SSH_LOG[0] = "Accepted publickey for lyra from 192.168.1.10 port 51234 ssh2"
IP6[0] = ""
f = step()
check("nothing in the last 7 days (an older visit), the LAN only, no public IPv6: all ok",
      [f[k]["status"] for k in ("reach-web", "reach-ssh", "reach-ipv6")] == ["ok", "ok", "ok"], f)
check("the step is in the doctor's list", any(sid == "internet" for sid, *_ in sd.STEPS))
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
