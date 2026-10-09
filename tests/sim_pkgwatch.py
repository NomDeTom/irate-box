# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Packages from their makers, watched (root/pkgwatch.py; Tom, 2026-10-08: meshtasticd on beta, alpha or
nightly, updated by itself or after a while; "There's tools in mpwrd-os for this as well - it needs to
adapt"), offline: the channel read the way mpwrd-menu reads it, each new build cached with when it was
first seen, watch / auto / aged, Update and Roll back, a build that doesn't match its index refused, and a
channel chosen here written the way mpwrd-menu writes it. apt and dpkg are stood in (dpkg's version
order is the real one when dpkg is here). python3 tests/sim_pkgwatch.py"""
import hashlib, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="pkgwatch-"))
IMG = T / "image"
(T / "etc").mkdir(); (T / "state" / "control").mkdir(parents=True)
(T / "os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\nVERSION_ID="13"\n')
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"), HUB_PKG_ROOT=str(T / "cache"),
                  HUB_APT_PREFS_DIR=str(T / "prefs.d"), HUB_OS_RELEASE=str(T / "os-release"), HUB_IMAGE_ROOT=str(IMG))
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control, pkgwatch  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# --- a stand-in apt: each channel's newest build; dpkg's record of what is installed ---
CHANNEL = {"beta": "2.7.26.61~obs54e0d8d~beta", "alpha": "2.8.1.64~obs8e6a88d~alpha", "daily": "2.8.1.752~obs790944a~unstable"}
state = {"installed": "2.8.0.696~obs5f198c4~unstable", "bad_sha": False}
ran = []
def deb_body(v):
    return f"meshtasticd {v}".encode()
def fake(cmd, timeout=0, cwd=None):
    ran.append(cmd)
    ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")  # noqa: E731
    if cmd[:2] == ["dpkg", "--print-architecture"]:
        return ok("armhf\n")
    if cmd[0] == "dpkg-query":
        return ok(f"install ok installed\t{state['installed']}") if state["installed"] else subprocess.CompletedProcess(cmd, 1, "", "")
    if cmd[:2] == ["dpkg", "--compare-versions"]:
        if shutil.which("dpkg"):
            return subprocess.run(cmd, capture_output=True, text=True)
        a, b = cmd[2], cmd[4]
        return subprocess.CompletedProcess(cmd, 0 if a > b else 1, "", "")
    if cmd[0] == "apt-get":
        opt = lambda k: next(c.split("=", 1)[1] for c in cmd if c.startswith(k + "="))  # noqa: E731
        if "update" in cmd:
            src = Path(opt("Dir::Etc::sourcelist")).read_text()
            ch = next(c for c in CHANNEL if f":/{c}/" in src)
            v = CHANNEL[ch]
            body = deb_body(v)
            sha = "0" * 64 if state["bad_sha"] else hashlib.sha256(body).hexdigest()
            Path(opt("Dir::State::Lists"), f"repo_{ch}_Packages").write_text(
                f"Package: meshtasticd\nVersion: {v}\nArchitecture: armhf\nFilename: ./armhf/meshtasticd_{v}_armhf.deb\nSHA256: {sha}\nSize: {len(body)}\n\n"
                "Package: meshtasticd\nVersion: 1.0\nArchitecture: arm64\nFilename: ./arm64/x.deb\nSHA256: 0\n")
            return ok()
        if "download" in cmd:
            v = cmd[-1].split("=", 1)[1]
            Path(cwd, f"meshtasticd_{v}_armhf.deb").write_bytes(deb_body(v))
            return ok()
        if "install" in cmd:
            state["installed"] = Path(cmd[-1]).read_bytes().decode().split(" ", 1)[1]
            return ok()
    if cmd[0] == "systemctl":
        return ok()
    raise AssertionError(cmd)
pkgwatch.run = fake
pkgwatch.dearmor = lambda armored: b"binary keyring of " + armored[:10]

d = pkgwatch.definitions().get("meshtasticd")
check("the manifest: meshtasticd, Meshtastic's three channels, the shipped key with its fingerprint, mpwrd-menu's convention",
      d and list(d["channels"]) == ["beta", "alpha", "daily"] and (REPO / d["key"]).is_file() and len(d["fingerprint"]) == 40
      and d["image"]["list"].endswith("network:Meshtastic:{channel}.list"), d)
check("this box's suite: Debian_13", pkgwatch.suite() == "Debian_13")

# --- the channel, read as mpwrd-menu reads it ---
lists = IMG / "etc/apt/sources.list.d"; lists.mkdir(parents=True)
(IMG / "usr/bin").mkdir(parents=True); (IMG / "usr/bin/mpwrd-menu").write_text("#!/bin/bash\n")
(lists / "network:Meshtastic:beta.list").write_text("deb http://x/ /\n")
(lists / "network:Meshtastic:daily.list").write_text("deb http://x/ /\n")
check("mpwrd-menu lists beta and daily (mixed): the channel from the installed build, daily", pkgwatch.settings("meshtasticd")["channel"] == "daily")
(lists / "network:Meshtastic:daily.list").unlink()
check("  one list: that channel, beta", pkgwatch.settings("meshtasticd")["channel"] == "beta")
(lists / "network:Meshtastic:beta.list").unlink()
state["installed"] = None
check("  none, nothing installed: the manifest's default, beta; watch by default, looked at daily", pkgwatch.settings("meshtasticd") == {"channel": "beta", "mode": "watch", "days": 7, "every": 24})
state["installed"] = "2.8.0.696~obs5f198c4~unstable"
(lists / "network:Meshtastic:beta.list").write_text("deb http://x/ /\n"); (lists / "network:Meshtastic:daily.list").write_text("deb http://x/ /\n")

# --- watch: the channel read, a new build cached, nothing installed ---
T0 = 1_800_000_000.0
msg = pkgwatch.check("meshtasticd", now=T0, log=lambda *a: None)
src = (T / "cache/meshtasticd/sources.list").read_text()
check("a check: the channel alone, signed by the shipped key, in an apt view of its own",
      src == f"deb [signed-by={REPO / d['key']}] https://download.opensuse.org/repositories/network:/Meshtastic:/daily/Debian_13/ /\n"
      and any("Dir::Etc::sourceparts=-" in c for c in ran if c[0] == "apt-get"), src)
seen = pkgwatch.seen("meshtasticd")
check("  the newest build for this board cached, its sha256 checked, when first seen kept", list(seen) == [CHANNEL["daily"]]
      and seen[CHANNEL["daily"]]["first_seen"] == T0 and "new on daily" in msg, (seen, msg))
pub = json.loads((T / "state/control/pkgwatch.json").read_text())["meshtasticd"]
check("  watching: nothing installed, the build said to be due, mpwrd-menu's two lists said",
      state["installed"].startswith("2.8.0.696") and pub["due"] == CHANNEL["daily"] and pub["image"]["channels"] == ["beta", "daily"], pub)
check("  a second check: nothing new", "nothing new" in pkgwatch.check("meshtasticd", now=T0 + 60, log=lambda *a: None))

# --- Update and Roll back, by hand ---
pkgwatch.install("meshtasticd", CHANNEL["daily"], log=lambda *a: None)
check("Update: installed from the cache, the one before remembered, the daemon restarted if running",
      state["installed"] == CHANNEL["daily"] and pkgwatch._record("meshtasticd")["previous"].startswith("2.8.0.696")
      and ["systemctl", "try-restart", "meshtasticd.service"] in ran)
try:
    pkgwatch.rollback("meshtasticd", log=lambda *a: None); ok = False
except ValueError as exc:
    ok = "not in the cache" in str(exc)
check("  Roll back: refused when the build before was never cached (installed before watching)", ok)

# --- the owner's channel and mode, the mpwrd-menu way ---
said = pkgwatch.set_settings("meshtasticd", "alpha", "auto", 7)
check("a channel chosen: mpwrd-menu's lists and keys for the others gone, alpha's written, the key from the box's own copy",
      sorted(p.name for p in lists.iterdir()) == ["network:Meshtastic:alpha.list"]
      and (lists / "network:Meshtastic:alpha.list").read_text() == "deb https://download.opensuse.org/repositories/network:/Meshtastic:/alpha/Debian_13/ /\n"
      and (IMG / "etc/apt/trusted.gpg.d/network_Meshtastic_alpha.gpg").read_bytes().startswith(b"binary keyring of -----BEGIN")
      and "mpwrd-menu (mPWRD-OS) and the box's apt now on alpha too" in said, said)
check("  automatic: apt left alone (one channel listed, apt upgrade gives the newest)", not (T / "prefs.d/irate-box-meshtasticd.pref").exists())
msg = pkgwatch.check("meshtasticd", now=T0 + 3600, log=lambda *a: None)
check("automatic, onto a channel behind what is installed (alpha 2.8.1.64 < nightly 2.8.1.752): no downgrade by itself",
      state["installed"] == "2.8.1.752~obs790944a~unstable" and "new on alpha" in msg and pkgwatch.due("meshtasticd") is None, msg)
CHANNEL["alpha"] = "2.8.2.10~obsddddddd~alpha"
msg = pkgwatch.check("meshtasticd", now=T0 + 7200, log=lambda *a: None)
check("  alpha passing it: its new build installed as it's seen", state["installed"] == CHANNEL["alpha"] and "installed" in msg, msg)
check("  and the one before cached, so Roll back works", pkgwatch.rollback("meshtasticd", log=lambda *a: None).startswith(f"meshtasticd {CHANNEL['daily']} installed")
      and state["installed"] == CHANNEL["daily"])

# --- after a while ---
pkgwatch.set_settings("meshtasticd", "daily", "aged", 7)
pref = (T / "prefs.d/irate-box-meshtasticd.pref").read_text()
check("after a while: apt upgrade held from Meshtastic's repositories for meshtasticd alone", "Package: meshtasticd" in pref
      and 'Pin: origin "download.opensuse.org"' in pref and "Pin-Priority: -1" in pref, pref)
CHANNEL["daily"] = "2.8.1.800~obsaaaaaaa~unstable"
pkgwatch.check("meshtasticd", now=T0 + 86400, log=lambda *a: None)
check("  a new nightly seen: kept, not installed for 7 days", state["installed"] == "2.8.1.752~obs790944a~unstable"
      and "2.8.1.800~obsaaaaaaa~unstable" in pkgwatch.seen("meshtasticd"))
CHANNEL["daily"] = "2.8.1.900~obsbbbbbbb~unstable"
pkgwatch.check("meshtasticd", now=T0 + 8 * 86400 + 1, log=lambda *a: None)
check("  a week on, with a newer one out since: the one that has been out 7 days installed, not the newest",
      state["installed"] == "2.8.1.800~obsaaaaaaa~unstable" and "2.8.1.900~obsbbbbbbb~unstable" in pkgwatch.seen("meshtasticd"), state)
pkgwatch.set_settings("meshtasticd", "daily", "watch", 7)
check("back to watch: apt's hold taken away", not (T / "prefs.d/irate-box-meshtasticd.pref").exists())

# --- flag: said, nothing downloaded; Fetch keeps it; Install fetches first ---
said = pkgwatch.set_settings("meshtasticd", "daily", "flag", 7)
check("flag chosen: said as downloading nothing", "nothing downloaded" in said, said)
V1 = CHANNEL["daily"] = "2.8.1.950~obseeeeeee~unstable"
ran.clear()
msg = pkgwatch.check("meshtasticd", now=T0 + 10 * 86400, log=lambda *a: None)
pub = json.loads(pkgwatch.PUBLIC.read_text())["meshtasticd"]
check("  a check: the new build flagged, not downloaded, not cached", "flagged" in msg and V1 not in pkgwatch.seen("meshtasticd")
      and not any("download" in c for c in ran) and V1 in pkgwatch.flagged("meshtasticd"), (msg, ran))
check("  published as newer and flagged, when first seen kept", pub["newer"] == V1 and pub["flagged"]["version"] == V1
      and pub["flagged"]["first_seen"] == T0 + 10 * 86400, pub)
pkgwatch.check("meshtasticd", now=T0 + 11 * 86400, log=lambda *a: None)
check("  a second check: still flagged from when it was first seen", pkgwatch.flagged("meshtasticd")[V1]["first_seen"] == T0 + 10 * 86400)
msg = hub_control.ACTIONS["pkg-fetch"]({"package": "meshtasticd"})
got = pkgwatch.seen("meshtasticd")
check("Fetch: downloaded and kept, its first sighting carried over, no longer flagged, nothing installed",
      V1 in got and got[V1]["first_seen"] == T0 + 10 * 86400 and not pkgwatch.flagged("meshtasticd")
      and state["installed"] == "2.8.1.800~obsaaaaaaa~unstable" and "new on daily" in msg, (msg, got.get(V1)))
V2 = CHANNEL["daily"] = "2.8.1.960~obsfffffff~unstable"
pkgwatch.check("meshtasticd", now=T0 + 12 * 86400, log=lambda *a: None)
check("  the next build flagged; Install of it fetches first, then installs",
      V2 in pkgwatch.flagged("meshtasticd") and pkgwatch.install("meshtasticd", V2, log=lambda *a: None).startswith(f"meshtasticd {V2} installed")
      and state["installed"] == V2 and V2 in pkgwatch.seen("meshtasticd") and not pkgwatch.flagged("meshtasticd"))
pkgwatch.set_settings("meshtasticd", "daily", "auto", 7)
V3 = CHANNEL["daily"] = "2.8.1.970~obsggggggg~unstable"
pkgwatch.check("meshtasticd", now=T0 + 13 * 86400, log=lambda *a: None, fetch=True)
check("  Fetch with Install chosen: kept, still not installed (only a check installs)", V3 in pkgwatch.seen("meshtasticd") and state["installed"] == V2)
pkgwatch.set_settings("meshtasticd", "daily", "watch", 7)

# --- refused ---
for bad in (("meshtasticd", "nightly", "auto", 7), ("meshtasticd", "beta", "always", 7), ("meshtasticd", "beta", "aged", 2), ("nosuch", "beta", "watch", 7)):
    try:
        pkgwatch.set_settings(*bad); ok = False
    except ValueError:
        ok = True
    check(f"settings {bad[1:]} for {bad[0]}: refused", ok)
for bad in ("9.9.9~beta", "1.0; reboot"):
    try:
        pkgwatch.install("meshtasticd", bad, log=lambda *a: None); ok = False
    except ValueError:
        ok = True
    check(f"install {bad!r}: refused (not cached, or not a version)", ok)
CHANNEL["daily"] = "2.9.0.1~obscccccc~unstable"; state["bad_sha"] = True
try:
    pkgwatch.check("meshtasticd", now=T0 + 9 * 86400, log=lambda *a: None); ok = False
except ValueError as exc:
    ok = "did not download whole" in str(exc)
check("a download that doesn't match its index: refused, not cached", ok and "2.9.0.1~obscccccc~unstable" not in pkgwatch.seen("meshtasticd"))
state["bad_sha"] = False

# --- the root helper's actions ---
ran.clear()
check("pkg-check through the root helper (by \"package\", every one on the box without)", "meshtasticd" in hub_control.ACTIONS["pkg-check"]({"action": "pkg-check"})
      or "new on daily" in hub_control.ACTIONS["pkg-check"]({"action": "pkg-check"}))
check("pkg-settings through it", "on beta" in hub_control.ACTIONS["pkg-settings"]({"package": "meshtasticd", "channel": "beta", "mode": "watch", "days": 3}))
# How often (item 36's pattern): kept per package, published for the librarian, the old settings without it as 24 h.
check("how often: 24 h when never chosen, kept when a save leaves it out", pkgwatch.settings("meshtasticd")["every"] == 24)
hub_control.ACTIONS["pkg-settings"]({"package": "meshtasticd", "channel": "beta", "mode": "watch", "days": 3, "every": 6})
check("  6 hours chosen, and published", pkgwatch.settings("meshtasticd")["every"] == 6
      and json.loads(pkgwatch.PUBLIC.read_text())["meshtasticd"]["settings"]["every"] == 6)
pkgwatch.set_settings("meshtasticd", "beta", "watch", 3)
check("  a save without it keeps it", pkgwatch.settings("meshtasticd")["every"] == 6)
for bad in (12, True, "6"):
    try:
        hub_control.ACTIONS["pkg-settings"]({"package": "meshtasticd", "channel": "beta", "mode": "watch", "days": 3, "every": bad}); ok = False
    except ValueError:
        ok = True
    check(f"  every {bad!r}: refused", ok)
import importlib
os.environ["HUB_STATE_DIR"] = str(T / "libstate")
(T / "libstate" / "control").mkdir(parents=True, exist_ok=True)
from irate_box.library import librarian as L
L = importlib.reload(L)
queued = []
L._queue_root = lambda req: queued.append(req) or f"r{len(queued)}"
(T / "libstate" / "control" / "pkgwatch.json").write_text(json.dumps({"meshtasticd": {"settings": {"every": 6}}, "other": {"settings": {"every": 0}}}))
L.PKGWATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
L.PKGWATCH_STATE.write_text(json.dumps({"queued": T0, "id": "old"}))
check("the librarian: a package whose 6 hours are up queued alone; Manual never", L.packages_step(now=T0 + 7 * 3600) == "check queued: meshtasticd (r1)"
      and queued == [{"action": "pkg-check", "package": "meshtasticd"}], queued)
check("  not again before its time", L.packages_step(now=T0 + 8 * 3600) is None)
check("  then again", L.packages_step(now=T0 + 13.5 * 3600) is not None and len(queued) == 2)
(T / "os-release").write_text('ID=ubuntu\nVERSION_ID="24.04"\nPRETTY_NAME="Ubuntu 24.04"\n')
try:
    pkgwatch.suite(); ok = False
except ValueError as exc:
    ok = "Ubuntu" in str(exc)
check("not Debian: said, nothing read", ok)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
