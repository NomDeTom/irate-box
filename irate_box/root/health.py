#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box doctor: is everything install.sh set up still there and working, and if not, why.

The update doctor (hub_control.py) looks at what an update needs from outside; this looks at
the box itself, for when something has stopped or an install did not finish:

  - the last install: did install.sh finish, at which step did it stop, how many problems
    (install.sh keeps /var/log/irate-box/install-state.json and install.log)
  - the root helper that /admin depends on (its path unit, a queue nobody is answering, the
    start limit that used to kill it)
  - every unit irate-box installs, add-ons included: missing, failed (and why: the start
    limit, an exit code, the journal's last lines), stopped, not started at boot
  - Kiwix in layers: the package, the unit, each book (a real ZIM? truncated? can
    kiwix-manage read it?), the library against the books on disk, and a crash loop
  - the uplink watchdog's report, the network inventory, the web server's config, whether
    the hub answers, the space left
  - crash watch (crashwatch.py): a box that stopped without shutting down, and what it was doing;
    a driver flooding the kernel log; the radio failing, and how far pre-emption goes; whether a
    frozen box restarts by itself

Every finding says what to do by hand ("fix", shell commands where they help) and, where it is
safe to do from a button, offers it ("actions", carried out by fix()). Nothing changes unless
asked. Three ways in:

    sudo /opt/irate-box/irate-box health            the report, with what to do
    sudo /opt/irate-box/irate-box health summary    problems only (install.sh's closing lines)
    sudo /opt/irate-box/irate-box health fix CHOICE one repair, as offered in the report
    /admin → Health → Box doctor (the clock: Box → Clock), the same, through hub_control.py

The shell is the way in when /admin itself is stuck (the root helper is what runs this for
the page). "rerun-install" is the root helper's own (it needs the update machinery). Stdlib only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from irate_box.root import rtc
from irate_box.library import zimcheck

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
LOGDIR = Path(os.environ.get("HUB_LOG_DIR", "/var/log/irate-box"))
INSTALL_STATE = LOGDIR / "install-state.json"
INSTALL_LOG = LOGDIR / "install.log"
ZIM = STATE / "zim"
QUARANTINE = ZIM / "quarantine"
LIBRARY = ZIM / "library.xml"
HUB_USER = os.environ.get("HUB_USER", "hub")
CONTROL = STATE / "control"
UNIT_DIR = Path("/etc/systemd/system")
# Units this page may restart or enable: irate-box's own and the add-ons'.
OUR_UNIT = re.compile(r"^(irate-box(-[a-z]+)*\.(service|socket|path|timer)|nginx\.service|caddy\.service|kiwix\.service|"
                      r"silverbullet\.service|syncthing@" + re.escape(HUB_USER) + r"\.service|mosquitto\.service|ngircd\.service|"
                      r"excalidraw-room\.service|ttyd\.service)$")
ADDON_UNITS = {"--with-notes": "silverbullet.service", "--with-sync": f"syncthing@{HUB_USER}.service",
               "--with-mqtt": "mosquitto.service", "--with-irc": "ngircd.service", "--with-collab": "excalidraw-room.service",
               "--with-term": "ttyd.service"}


def run(*cmd, timeout=60, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _f(fid, check, status, detail, fix="", actions=()):
    """status: ok | warn | problem. actions: [{choice, label, confirm?}]."""
    return {"id": fid, "check": check, "status": status, "detail": detail, "fix": fix, "actions": list(actions)}


def _act(choice, label, confirm=None):
    return {"choice": choice, "label": label, **({"confirm": confirm} if confirm else {})}


RERUN = _act("rerun-install", "Run the installer again",
             "Run install.sh again with this box's recorded options? Services restart once; nothing else changes.")


def install_options():
    try:
        return (ETC / "install-options").read_text().split("\n")
    except OSError:
        return []


def option(opts, name, default=None):
    return opts[opts.index(name) + 1] if name in opts and opts.index(name) + 1 < len(opts) else default


# --- the last install ---------------------------------------------------------------------------

def install_state():
    try:
        return json.loads(INSTALL_STATE.read_text())
    except (OSError, ValueError):
        return None


def check_install():
    st = install_state()
    if not st:
        return [_f("install", "Last install", "warn", "No record of an install (installs before 2026-10-02 kept none).",
                   "The next run of install.sh records one.")]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.get("started", 0)))
    if st.get("running") and _pid_alive(st.get("pid")):
        return [_f("install", "Last install", "warn", f"install.sh is running now (started {when}), at: {st.get('step')}.")]
    if st.get("running") or st.get("aborted"):
        where = st.get("step") or "an early step"
        if st.get("running"):
            cmd = " It was cut off (a power cut, a lost SSH session, or it was killed), so it left no reason."
        else:
            cmd = (f" What failed: {st['failed_command']}" + (f" (line {st['failed_line']})" if st.get("failed_line") else "")
                   + f", exit {st.get('exit')}.")
        return [_f("install", "Last install", "problem",
                   f"install.sh (started {when}) stopped during \"{where}\" and did not finish: the steps after it "
                   f"were not done.{cmd}",
                   f"The full output is in {INSTALL_LOG}. Fix what it says (the findings below may name it), then run "
                   "the installer again: this button, Updates → Install update, or sudo ./install.sh from the checkout.",
                   [RERUN])]
    problems = st.get("problems") or []
    if problems:
        return [_f("install", "Last install", "warn",
                   f"Finished {when}. At the time, {len(problems)} problem{'s' if len(problems) != 1 else ''}: " + "; ".join(problems)[:600],
                   f"History: the findings below say which are still there. The run's output is in {INSTALL_LOG}.")]
    return [_f("install", "Last install", "ok", f"Finished {when} with no problems.")]


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


# --- units --------------------------------------------------------------------------------------

def unit_props(unit):
    out = run("systemctl", "show", unit, "-p", "LoadState,ActiveState,SubState,Result,UnitFileState,NRestarts").stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def journal_tail(unit, n=6):
    out = run("journalctl", "-u", unit, "-n", str(n), "--no-pager", "-o", "cat").stdout.strip().splitlines()
    return [l[:200] for l in out if l.strip()][-n:]


def expected_units():
    """(unit, why) for everything install.sh would have set running."""
    opts = install_options()
    web = option(opts, "--web", "nginx") or "nginx"
    units = [("irate-box.service", "the hub itself"), (f"{web}.service", "the web server in front"),
             ("irate-box-git.socket", "the git servers"), ("irate-box-control.path", "the root helper /admin depends on"),
             ("irate-box-librarian.timer", "the librarian's schedule"), ("irate-box-ci.path", "builds on push")]
    if (UNIT_DIR / "irate-box-uplink.service").exists() or "irate-box-uplink" in _installed_text():
        units.append(("irate-box-uplink.service", "the uplink watchdog"))
    if (UNIT_DIR / "irate-box-crashwatch.service").exists() or "irate-box-crashwatch" in _installed_text():
        units.append(("irate-box-crashwatch.service", "crash watch"))
    for opt, unit in ADDON_UNITS.items():
        if opt in opts:
            units.append((unit, f"the {opt.removeprefix('--with-')} add-on"))
    if (UNIT_DIR / "kiwix.service").exists() or _books():
        units.append(("kiwix.service", "Kiwix (/wiki/)"))
    return units


def _installed_text():
    try:
        return (CODE / "install.sh").read_text()
    except OSError:
        return ""


def check_units():
    out = []
    for unit, why in expected_units():
        p = unit_props(unit)
        load, active, sub, result = p.get("LoadState"), p.get("ActiveState"), p.get("SubState"), p.get("Result")
        enabled = p.get("UnitFileState")
        title = f"{unit} ({why})"
        restart = _act(f"unit-restart:{unit}", "Start it again")
        if load == "not-found":
            out.append(_f(f"unit:{unit}", title, "problem", "The unit is not on the box any more.",
                          "install.sh writes it: run the installer again.", [RERUN]))
            continue
        if unit == "kiwix.service":
            continue  # check_kiwix looks at Kiwix in layers, the unit included
        if active == "failed" or result not in ("success", None, ""):
            why_failed = {"start-limit-hit": "it failed several times in a row and systemd stopped trying (the start limit)",
                          "exit-code": "it exited with an error", "signal": "it was killed",
                          "timeout": "it did not start in time", "core-dump": "it crashed",
                          "watchdog": "it stopped answering systemd's watchdog",
                          "unit-start-limit-hit": "the unit it starts hit its start limit, so this stopped too"}.get(result, result)
            tail = journal_tail(unit if not unit.endswith(".path") else unit.replace(".path", ".service"))
            out.append(_f(f"unit:{unit}", title, "problem", f"Failed: {why_failed}."
                          + (f" Last lines of its log: {' | '.join(tail)}" if tail else ""),
                          f"See why: journalctl -u {unit} -n 50. Then: systemctl reset-failed {unit}; systemctl "
                          f"{'start' if unit.endswith(('.path', '.timer', '.socket')) else 'restart'} {unit}. "
                          "If it fails again at once, the log says what it needs.", [restart]))
        elif active != "active":
            out.append(_f(f"unit:{unit}", title, "warn", f"Not running ({active}{'/' + sub if sub else ''}).",
                          f"systemctl start {unit}", [restart]))
        elif enabled not in ("enabled", "static", "indirect", "generated", "alias") and unit != "irate-box-uplink.service":
            out.append(_f(f"unit:{unit}", title, "warn", f"Running, but not started at boot ({enabled}).",
                          f"systemctl enable {unit}", [_act(f"unit-enable:{unit}", "Start it at boot")]))
        else:
            out.append(_f(f"unit:{unit}", title, "ok", f"Running{', restarted ' + p['NRestarts'] + ' times' if p.get('NRestarts', '0') not in ('0', '') else ''}."))
    # A timer's service: the timer stays "running" while every run of it fails, and a one-shot
    # service's failure shows only in its result (the librarian's did, unseen, 2026-10-06).
    for unit, why in expected_units():
        if not unit.endswith(".timer"):
            continue
        svc = unit[:-len(".timer")] + ".service"
        p = unit_props(svc)
        result = p.get("Result")
        if p.get("LoadState") == "not-found" or result in ("success", None, ""):
            continue
        tail = journal_tail(svc, 8)
        out.append(_f(f"unit:{svc}", f"{svc} (each run of {why})", "problem",
                      f"Its last run failed ({result})." + (f" Last lines of its log: {' | '.join(tail)}" if tail else ""),
                      f"See why: journalctl -u {svc} -n 80. The timer starts it again within the hour; "
                      f"systemctl reset-failed {svc} clears the mark once fixed.", [_act(f"unit-restart:{svc}", "Run it now")]))
    # The root helper: a request nobody has answered means /admin buttons go nowhere.
    reqs = sorted((CONTROL / "requests").glob("*.json"), key=lambda q: q.stat().st_mtime) if (CONTROL / "requests").is_dir() else []
    me = os.environ.get("HUB_CONTROL_RUNNING") == "1"
    if reqs and not me:
        age = time.time() - reqs[0].stat().st_mtime
        if age > 90:
            out.append(_f("helper-queue", "Root helper queue", "problem",
                          f"{len(reqs)} /admin request{'s' if len(reqs) != 1 else ''} waiting, the oldest for {int(age // 60)} min.",
                          "systemctl reset-failed irate-box-control.service irate-box-control.path; "
                          "systemctl start irate-box-control.path",
                          [_act("unit-restart:irate-box-control.path", "Start the root helper again")]))
    unit_text = (UNIT_DIR / "irate-box-control.service").read_text() if (UNIT_DIR / "irate-box-control.service").exists() else ""
    if unit_text and "StartLimitIntervalSec=0" not in unit_text:
        out.append(_f("helper-limit", "Root helper start limit", "warn",
                      "Installed before 2026-10-02: a burst of /admin clicks can trip systemd's start limit and leave "
                      "the root helper stopped until a reboot.", "Run the installer again (or update): it writes the fixed unit.",
                      [RERUN]))
    # Anything else that failed on the box: not ours to restart, but worth knowing when troubleshooting.
    failed = [l.split()[0] for l in run("systemctl", "--failed", "--no-legend", "--plain").stdout.splitlines() if l.split()]
    others = [u for u in failed if not OUR_UNIT.match(u)]
    if others:
        out.append(_f("other-failed", "Other failed units on the box", "warn", ", ".join(others[:8]),
                      "Not irate-box's; journalctl -u NAME says why. Often harmless on these images."))
    return out


# --- Kiwix ----------------------------------------------------------------------------------------

def _books():
    return sorted(ZIM.glob("*.zim")) if ZIM.is_dir() else []


def zim_header_problem(path):
    return zimcheck.header_problem(path)


def kiwix_reads(path):
    # As the hub, not root (F12): the books are the hub's, and may be anyone's download.
    return zimcheck.kiwix_problem(path, timeout=120, user=HUB_USER)


def readable_books():
    return [b for b in _books() if zim_header_problem(b) is None and kiwix_reads(b) is None]


def library_paths():
    try:
        text = LIBRARY.read_text(errors="replace")
    except OSError:
        return None
    # kiwix-manage writes paths relative to the library file.
    return [(LIBRARY.parent / p).resolve() for p in re.findall(r'<book [^>]*\bpath="([^"]+)"', text)]


def check_kiwix():
    books = _books()
    unit = UNIT_DIR / "kiwix.service"
    if not books and not unit.exists():
        return []
    out = []
    if not shutil.which("kiwix-serve") or not shutil.which("kiwix-manage"):
        out.append(_f("kiwix-package", "Kiwix package", "problem", "kiwix-tools is not installed, so /wiki/ cannot run.",
                      "apt-get install kiwix-tools, or run the installer again (it installs it when there are books).", [RERUN]))
    good, bad = [], []
    for b in books:
        why = zim_header_problem(b) or kiwix_reads(b)
        (bad if why else good).append((b, why))
    for b, why in bad:
        out.append(_f(f"zim:{b.name}", f"Book {b.name}", "problem", f"{b.stat().st_size >> 20} MB, {why}.",
                      f"Download or copy it again (Library → Books keeps sources current), or set it aside: "
                      f"mv {b} {QUARANTINE}/ — then rebuild the library.",
                      [_act(f"kiwix-quarantine:{b.name}", "Set it aside",
                            f"Move {b.name} to {QUARANTINE}/ and rebuild the library without it?")]))
    if good:
        out.append(_f("zim-ok", "Books", "ok", f"{len(good)} readable: " + ", ".join(b.name for b, _ in good[:6])
                      + ("…" if len(good) > 6 else "")))
    listed = library_paths()
    rebuild = _act("kiwix-rebuild", "Rebuild the library")
    if listed is None:
        if good:
            out.append(_f("library", "Kiwix library", "problem", f"No {LIBRARY.name}, though {len(good)} readable books are there.",
                          "Rebuild it.", [rebuild]))
    else:
        on_disk = {b.resolve() for b, _ in good}
        gone = [p for p in listed if not p.exists()]
        missing = [b for b in on_disk if b not in {p.resolve() for p in listed if p.exists()}]
        if gone or missing:
            out.append(_f("library", "Kiwix library", "problem",
                          (f"Lists {len(gone)} book{'s' if len(gone) != 1 else ''} no longer on disk ({', '.join(p.name for p in gone[:4])}). " if gone else "")
                          + (f"Leaves out {len(missing)} readable book{'s' if len(missing) != 1 else ''} ({', '.join(p.name for p in missing[:4])})." if missing else ""),
                          "Rebuild it from the books on disk.", [rebuild]))
        else:
            out.append(_f("library", "Kiwix library", "ok", f"Lists the {len(listed)} books on disk."))
    if unit.exists():
        p = unit_props("kiwix.service")
        if not good:
            if p.get("UnitFileState") != "enabled" and p.get("ActiveState") in ("inactive", "failed"):
                out.append(_f("kiwix-empty", "Kiwix (/wiki/)", "warn",
                              "Stopped until there is a readable book (the installer or this page stopped it, so it does not loop).",
                              "Add a book (Library → Books, a USB stick, or install.sh --zim), then rebuild the library: "
                              "that starts it again.", [_act("kiwix-rebuild", "Rebuild the library")]))
            else:
                out.append(_f("kiwix-empty", "Kiwix (/wiki/)", "problem",
                              "There is no readable book, so kiwix-serve cannot start: systemd keeps trying and then gives up "
                              f"({p.get('Result') or p.get('ActiveState')}).",
                              "Add a book (Library → Books, a USB stick, or install.sh --zim), then rebuild the library. "
                              "Until then, stop Kiwix so it does not loop: systemctl disable --now kiwix.",
                              [_act("kiwix-off", "Stop Kiwix until there are books")]))
        elif p.get("ActiveState") != "active" and p.get("Result") in ("success", "", None):
            out.append(_f("kiwix-down", "Kiwix (/wiki/)", "warn",
                          f"Stopped (not failed), with {len(good)} readable books"
                          + ("; not started at boot" if p.get("UnitFileState") != "enabled" else "") + ".",
                          "Turned off from this page or by hand. To bring /wiki/ back: rebuild the library (it starts Kiwix), "
                          "or systemctl enable --now kiwix.", [rebuild]))
        elif p.get("ActiveState") != "active":
            out.append(_f("kiwix-down", "Kiwix (/wiki/)", "problem",
                          f"Not running ({p.get('Result') or p.get('ActiveState')}), with {len(good)} readable books."
                          + (" Log: " + " | ".join(journal_tail("kiwix.service", 4)) if journal_tail("kiwix.service", 1) else ""),
                          "Rebuild the library, then start it: systemctl reset-failed kiwix; systemctl restart kiwix.",
                          [rebuild, _act("unit-restart:kiwix.service", "Start it again")]))
    elif good:
        out.append(_f("kiwix-unit", "Kiwix (/wiki/)", "problem", "Books are there but Kiwix is not set up (no kiwix.service).",
                      "Run the installer again: it sets Kiwix up whenever there are books.", [RERUN]))
    return out


def kiwix_rebuild():
    """library.xml from the readable books only. The old one stays if none can be read."""
    books = readable_books()
    if not books:
        return "No readable book, so the library was left as it was."
    new = LIBRARY.with_name("library.xml.new")
    new.unlink(missing_ok=True)

    def add(batch):
        # As librarian.rebuild_library: many books a run, a failing run halved until the
        # unreadable book is alone (one bad book makes kiwix-manage write nothing).
        if run("runuser", "-u", HUB_USER, "--", "kiwix-manage", str(new), "add", *map(str, batch), timeout=900).returncode == 0:
            return []
        if len(batch) == 1:
            return [batch[0].name]
        return add(batch[:len(batch) // 2]) + add(batch[len(batch) // 2:])
    skipped = []
    for i in range(0, len(books), 100):
        skipped += add(books[i:i + 100])
    if not new.exists():
        return "kiwix-manage made no library; the old one stays."
    # Already the hub's (kiwix-manage made it as the hub): no chown, which would follow a link
    # swapped in for it (F3). The rename replaces a link, never follows one.
    os.replace(new, LIBRARY)
    msg = f"Library rebuilt: {len(books) - len(skipped)} books" + (f" (skipped {', '.join(skipped)})" if skipped else "")
    if (UNIT_DIR / "kiwix.service").exists():
        run("systemctl", "reset-failed", "kiwix.service")
        run("systemctl", "enable", "--now", "kiwix.service")
        run("systemctl", "restart", "kiwix.service")
        msg += "; Kiwix started"
    return msg + "."


# --- the rest -------------------------------------------------------------------------------------

def check_uplink():
    if not (UNIT_DIR / "irate-box-uplink.service").exists():
        return []
    try:
        st = json.loads((CONTROL / "uplink.json").read_text())
    except (OSError, ValueError):
        return [_f("uplink-report", "Uplink watchdog report", "warn", "The watchdog has not written a report yet.",
                   "journalctl -u irate-box-uplink -n 30", [_act("unit-restart:irate-box-uplink.service", "Start it again")])]
    age = time.time() - st.get("at", 0)
    limit = 3 * max(st.get("settings", {}).get("check", 60), 60)
    if age > limit:
        return [_f("uplink-report", "Uplink watchdog report", "problem", f"Last report {int(age // 60)} min ago: it has stopped looking.",
                   "journalctl -u irate-box-uplink -n 30; systemctl restart irate-box-uplink",
                   [_act("unit-restart:irate-box-uplink.service", "Start it again")])]
    return [_f("uplink-report", "Uplink watchdog report", "ok", f"{st.get('state')} on {st.get('iface')}, "
               f"{st.get('chosen', {}).get('eagerness')}, {st.get('chosen', {}).get('forgiveness')}.")]


def check_inventory():
    p = CONTROL / "netinv.json"
    if not p.exists():
        return [_f("netinv", "Network inventory", "warn", "Not looked yet.", "", [_act("net-scan", "Look now")])]
    return []


def check_hotspot():
    """While the hotspot is meant to be on (root/ap.py's record): its interface there, in AP mode, on its
    planned channel (or the link's, when it follows); the access point's own service; dnsmasq."""
    from irate_box.root import ap
    rec = ap._load(ap.RECORD, {})
    if not rec.get("up"):
        return []
    plan = rec.get("plan") or {}
    iface = ap.ap_iface(plan) if plan else None
    out = []
    chans = ap.channels_now(run)
    want = chans.get(plan.get("uplink")) if plan.get("follows_uplink") else plan.get("channel")
    if iface not in chans:
        out.append(_f("hotspot-iface", "Hotspot interface", "problem", f"The hotspot should be on {iface}, which isn't there.",
                      "Network → the hotspot: switch it off and on again"))
    elif chans.get(iface) != want:
        out.append(_f("hotspot-iface", "Hotspot interface", "warn",
                      f"{iface} is on channel {chans.get(iface)}, not {want}" + (" (the WiFi link's)" if plan.get("follows_uplink") else "") + ".",
                      "Network → the hotspot: switch it off and on again"))
    else:
        out.append(_f("hotspot-iface", "Hotspot interface", "ok", f"{iface} on channel {want}: {plan.get('text', '')}"))
    if plan.get("backend") == "hostapd":
        if run("systemctl", "is-active", "--quiet", ap.HOSTAPD_UNIT).returncode:
            out.append(_f("hotspot-ap", "Hotspot access point (hostapd)", "problem", "hostapd isn't running.",
                          f"journalctl -u {ap.HOSTAPD_UNIT} -n 30", [_act(f"unit-restart:{ap.HOSTAPD_UNIT}", "Start it again")]))
    else:
        act = run("nmcli", "-t", "-f", "NAME", "connection", "show", "--active").stdout or ""
        if ap.CONNECTION not in act.split():
            out.append(_f("hotspot-ap", "Hotspot access point (NetworkManager)", "problem", f"The connection {ap.CONNECTION} isn't active.",
                          f"nmcli connection up {ap.CONNECTION}"))
    if run("systemctl", "is-active", "--quiet", ap.DNSMASQ_UNIT).returncode:
        out.append(_f("hotspot-dns", "Hotspot DHCP and names", "problem", "dnsmasq isn't running: guests get no address.",
                      f"journalctl -u {ap.DNSMASQ_UNIT} -n 30", [_act(f"unit-restart:{ap.DNSMASQ_UNIT}", "Start it again")]))
    if not rec.get("confirmed", True):
        out.append(_f("hotspot-confirm", "Hotspot waiting to be kept", "warn",
                      "It took the box's WiFi link: press Keep it from the hotspot, or the link comes back by itself.", ""))
    return out


def check_web():
    web = option(install_options(), "--web", "nginx") or "nginx"
    if web == "nginx" and shutil.which("nginx"):
        r = run("nginx", "-t")
    elif web == "caddy" and shutil.which("caddy"):
        r = run("caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile",
                env=dict(os.environ, HOME=os.environ.get("HOME", "/root")))
    else:
        return [_f("web-config", "Web server config", "problem", f"{web} is not installed.",
                   "Run the installer again (--web nginx or --web caddy).", [RERUN])]
    if r.returncode:
        lines = [l for l in (r.stderr + r.stdout).splitlines() if re.search(r"emerg|error|Error", l)][:3]
        return [_f("web-config", "Web server config", "problem", f"{web} rejects its configuration: " + " | ".join(lines)[:400],
                   "If the error is in irate-box's site, the installer rewrites it; if it is in the owner's own config, "
                   "fix that file and reload.", [RERUN])]
    return [_f("web-config", "Web server config", "ok", f"{web} accepts its configuration.")]


def check_hub():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=8) as r:
            json.load(r)
        return [_f("hub", "The hub", "ok", "Answers on 127.0.0.1:8000.")]
    except (OSError, ValueError) as exc:
        return [_f("hub", "The hub", "problem", f"Does not answer on 127.0.0.1:8000 ({exc}).",
                   "journalctl -u irate-box -n 50; systemctl restart irate-box",
                   [_act("unit-restart:irate-box.service", "Start it again")])]


def check_space():
    out = []
    for label, path, need in (("Space on the system card", Path("/"), 300), ("Space for the hub's state", STATE, 200)):
        if not path.exists():
            continue
        free = shutil.disk_usage(path).free >> 20
        if free < need:
            out.append(_f(f"space:{path}", label, "problem", f"{free} MB free in {path}.",
                          "Free some: old books (Library → Books), saved work, build runs (Git → Builds), "
                          "journalctl --vacuum-size=16M."))
    return out


def check_builds(now=None):
    """The builds (ci.py) and the Firmware Factory (factory.py): builds waiting over a day, a
    factory family failing every time here, and what builds take on the card."""
    now = time.time() if now is None else now
    ci_root, out = STATE / "ci", []
    waiting = []
    for d in (ci_root / "queue", STATE / "factory-held"):
        for p in d.glob("*.json") if d.is_dir() else []:
            try:
                job = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if not job.get("change") and now - float(job.get("queued", now)) > 86400:
                waiting.append(job)
    if waiting:
        held = (STATE / "factory-paused").exists()
        out.append(_f("builds-waiting", "Builds waiting", "warn",
                      f"{len(waiting)} build{'s have' if len(waiting) != 1 else ' has'} waited over a day"
                      + (" (the Firmware Factory is paused)." if held else "."),
                      "Firmware Factory: resume, or cancel what is no longer wanted." if held else
                      "Is the builder running (irate-box-ci)? A long build delays the rest: Git → Builds and the Firmware Factory say what is under way."))
    families = {}
    runs = ci_root / "runs" / "firmware-factory"
    for st in sorted(runs.glob("*/status.json"), key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0) if runs.is_dir() else []:
        try:
            r = json.loads(st.read_text())
        except (OSError, ValueError):
            continue
        if r.get("family") and r.get("state") in ("passed", "failed", "timed out"):
            families.setdefault(r["family"], []).append(r["state"])
    for fam, states in sorted(families.items()):
        if len(states) >= 2 and "passed" not in states:
            out.append(_f(f"builds-family:{fam}", f"Firmware builds: {fam}", "warn",
                          f"Every {fam} build here has failed ({len(states)} of them).",
                          "Open a failed one's log (Firmware Factory, Built): a toolchain PlatformIO has no build of for this board's "
                          "processor, or one it could not download, fails every target of the family."))
    used = 0
    for f in ci_root.rglob("*") if ci_root.is_dir() else []:
        try:
            if f.is_file() and not f.is_symlink():
                used += f.stat().st_size
        except OSError:
            pass
    total = shutil.disk_usage(STATE).total if STATE.exists() else 0
    if used and total:
        share = used / total
        out.append(_f("builds-disk", "What builds take", "warn" if share > 0.2 else "ok",
                      f"{used >> 20} MB ({share:.0%} of the card): runs, and the builder's tools and caches.",
                      "Git → Builds: delete old runs (the Firmware Factory keeps each target's two newest). "
                      "The builder's PlatformIO tools are in /var/lib/hub/ci/home." if share > 0.2 else ""))
    return out


# --- the clock ---------------------------------------------------------------------------------
# These boards have no clock that keeps time while they are off (the Lyra has no RTC at all),
# and the hub is meant to run offline. fake-hwclock restores the last time it saved, so a box
# that was off for two days boots two days behind; on the Lyra on 2026-09-30 that was 45.7 h,
# put right by chrony 18 s after boot only because the box was online. The hub's own expiry
# (hubclock.py) never reads the wall clock; logs, git commit dates, file times and certificate
# checks do. So: what keeps the time, what happened at this boot, is the time certainly wrong,
# and, offline, set it from the owner's browser.

NTP_DAEMONS = (("chrony.service", "chrony"), ("systemd-timesyncd.service", "systemd-timesyncd"),
               ("ntpsec.service", "ntpsec"), ("ntp.service", "ntpd"), ("openntpd.service", "OpenNTPD"))
STEP_RE = re.compile(r"(?:System clock was stepped by|clock was stepped by) (-?[\d.]+) seconds")


def _human_secs(secs):
    secs = abs(secs)
    if secs < 90:
        return f"{secs:.0f} s"
    if secs < 5400:
        return f"{secs / 60:.0f} min"
    if secs < 172800:
        return f"{secs / 3600:.1f} h"
    return f"{secs / 86400:.1f} days"


def clock_facts():
    show = dict(l.split("=", 1) for l in run("timedatectl", "show").stdout.splitlines() if "=" in l)
    daemon = next((name for unit, name in NTP_DAEMONS if unit_props(unit).get("ActiveState") == "active"), None)
    synced = show.get("NTPSynchronized") == "yes"
    sources = None
    if daemon == "chrony" and shutil.which("chronyc"):
        tracking = run("chronyc", "-n", "tracking").stdout
        if "Leap status     : Not synchronised" in tracking:
            synced = False
        sources = sum(1 for l in run("chronyc", "-n", "sources").stdout.splitlines() if l[:2] in ("^*", "^+", "^-"))
    rtcs = []
    for r in sorted(Path("/sys/class/rtc").glob("rtc*")):
        try:
            when = int((r / "since_epoch").read_text())  # the kernel's reading of the RTC, as UTC seconds
            rtcs.append({"name": (r / "name").read_text().strip(), "drift": time.time() - when})
        except (OSError, ValueError):
            rtcs.append({"name": r.name, "drift": None})
    fake = shutil.which("fake-hwclock") is not None
    data = Path("/etc/fake-hwclock.data")
    saver = fake and (unit_props("fake-hwclock-save.timer").get("ActiveState") == "active"
                      or Path("/etc/cron.hourly/fake-hwclock").exists())
    steps = [float(m.group(1)) for m in STEP_RE.finditer(
        run("journalctl", "-b", "--no-pager", "-o", "cat", "-u", "chrony", "-u", "chronyd", "-u", "systemd-timesyncd",
            "-u", "ntpsec", "-u", "ntp", "-u", "openntpd", timeout=60).stdout)]
    return {"daemon": daemon, "synced": synced, "sources": sources, "timezone": show.get("Timezone"),
            "rtcs": rtcs, "fake": fake, "fake_saved": data.stat().st_mtime if data.exists() else None,
            "fake_saver": saver, "steps": steps, "uptime": _uptime()}


def _uptime():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return 0.0


def latest_known_time():
    """The newest time the box has certainly reached: things irate-box wrote with the clock."""
    marks = []
    st = install_state() or {}
    for key in ("at", "started"):
        if isinstance(st.get(key), (int, float)):
            marks.append((st[key], "the last install"))
    # Root's own files only (F14): the hub could touch its clock.json, or plant files of its own.
    for path, what in ((CODE / "VERSION", "the installed code"), (CONTROL / "health.json", "the last health check"),
                       (CONTROL / "uplink.json", "the watchdog's report")):
        m = rtc.root_mtime(path)
        if m:
            marks.append((m, what))
    return max(marks) if marks else (None, None)


SET_CLOCK = _act("clock-set", "Set the clock from this browser",
                 "Set the box's clock to this device's time? Use it only if this device's own clock is right.")


def check_clock():
    c = clock_facts()
    now = time.time()
    tz = c["timezone"] or "unknown"
    out = []
    # Certainly wrong: earlier than something irate-box wrote with this same clock.
    newest, what = latest_known_time()
    if newest and now < newest - 300:
        out.append(_f("clock-behind", "Clock", "problem",
                      f"It says {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}, which is {_human_secs(newest - now)} "
                      f"before {what} was written: it is certainly behind.",
                      "Set it: the button sets it from this browser's clock; or sudo date -s 'YYYY-MM-DD HH:MM'. "
                      "Online, the network time service puts it right by itself.", [SET_CLOCK]))
    boot = ""
    if c["steps"]:
        total = sum(c["steps"])
        boot = (f" At this boot it started {_human_secs(total)} {'behind' if total > 0 else 'ahead'}"
                + (" (restored from fake-hwclock's last save: the box keeps no time while off)" if c["fake"] and not c["rtcs"] else "")
                + " and was put right once the network was reached.")
    out += check_module()
    if c["synced"]:
        by = (f"Set by {c['daemon']}" + (f" from {c['sources']} time servers" if c["sources"] else "") if c["daemon"]
              else "Synchronised, says the kernel (no time service seen here: a host or container may be keeping it)")
        out.append(_f("clock", "Clock", "ok", f"{by}; time zone {tz}.{boot}"))
        return out
    if rtc.FROM_RTC.exists():
        out.append(_f("clock", "Clock", "ok", f"Set from the clock module at this boot (no network time to check it "
                      f"against); time zone {tz}."))
        return out
    if c["rtcs"]:
        r = c["rtcs"][0]
        out.append(_f("clock", "Clock", "warn",
                      f"Kept by the hardware clock ({r['name']}), not checked against the network"
                      + (f" ({c['daemon']} has no server)" if c["daemon"] else " (no network time service)")
                      + f"; time zone {tz}. A hardware clock drifts, and a flat battery resets it.",
                      "If it looks wrong: the button sets it from this browser's clock.", [SET_CLOCK]))
        return out
    since = ""
    if c["fake_saved"]:
        since = f" fake-hwclock last saved it at {time.strftime('%Y-%m-%d %H:%M', time.localtime(c['fake_saved']))}."
    if c["fake"]:
        out.append(_f("clock", "Clock", "warn",
                      "Not set since this boot, and the box has no clock that runs while it is off: it was restored from "
                      "fake-hwclock's last save, so it is behind by however long the box was switched off."
                      + since + (f" {c['daemon']} is running but has no time server to ask (offline)." if c["daemon"] else "")
                      + ("" if c["fake_saver"] else " fake-hwclock is not saving the time regularly, so even that is old.")
                      + f" Time zone {tz}.",
                      "Online, it puts itself right. Offline: the button sets it from this browser's clock "
                      "(your phone or laptop knows the time), or sudo date -s 'YYYY-MM-DD HH:MM'.", [SET_CLOCK]))
    else:
        out.append(_f("clock", "Clock", "problem",
                      "No clock that runs while the box is off, no fake-hwclock to restore the last time, and not set from "
                      "the network: every boot starts at the image's build date or 1970."
                      + (f" {c['daemon']} is running but has no time server to ask." if c["daemon"] else ""),
                      "Install fake-hwclock (apt-get install fake-hwclock) so a boot at least starts where the last one "
                      "stopped; meanwhile the button sets the clock from this browser.", [SET_CLOCK]))
    return out


def check_module():
    """The I2C clock module (rtc.py): the one set up, or what a search found, or the offer to look."""
    st = rtc.status()
    if st:
        cfg = st["config"]
        where = f"{cfg['label']} on bus {cfg['bus']} at {cfg['addr']} ({'kernel driver' if cfg['mode'] == 'kernel' else 'own driver'})"
        remove = _act("rtc-remove", "Stop using it", "Stop using the clock module? The box goes back to fake-hwclock.")
        if st.get("unreachable"):
            return [_f("rtc", "Clock module", "problem", f"{where} does not answer: {st['why']}.",
                       "Check the module is seated and wired to that bus. If it was taken off on purpose, stop using it.",
                       [remove])]
        out = []
        if st["why"]:
            out.append(_f("rtc", "Clock module", "problem", f"{where}: {st['why']}.",
                          "Replace the battery if it keeps happening, then set the box's clock (it sets the module too).",
                          [SET_CLOCK, remove]))
        else:
            drift = st["drift"] or 0
            if abs(drift) > 5 and st["trusted"]:
                out.append(_f("rtc", "Clock module", "warn",
                              f"{where} is {_human_secs(drift)} {'behind' if drift > 0 else 'ahead'} of the system clock "
                              f"({st['trusted']}).", "Write the system time to it.",
                              [_act("rtc-save", "Set the module from the system clock")]))
            else:
                out.append(_f("rtc", "Clock module", "ok", f"{where}: within {_human_secs(drift)} of the system clock; it keeps "
                              "the time while the box is off."))
        for n in st.get("notes", []):
            out.append(_f("rtc-note", "Clock module", "warn", n.capitalize() + ".",
                          "Setting it up again turns on the battery switch; charging is never changed here."
                          if "INIEN" in n or "EOSC" in n else "Check which kind of cell the module has."))
        if st["units"].get("irate-box-rtc.service") != "active":
            out.append(_f("rtc-unit", "Clock module", "warn", "Its boot unit has not run, so the next boot may not read it.",
                          "systemctl start irate-box-rtc", [_act("unit-restart:irate-box-rtc.service", "Run it now")]))
        return out
    look = _act("rtc-find", "Look for a clock module")
    try:
        found = json.loads(rtc.FOUND.read_text())
    except (OSError, ValueError):
        if list(Path("/sys/class/rtc").glob("rtc*")):
            return []  # the kernel has a hardware clock already; nothing to offer unless asked
        return [_f("rtc", "Clock module", "ok", "No clock module set up. A battery-backed module on I2C (DS3231, RV-8803, "
                   "RX8130 and others) lets the box keep its time while it is off.", "", [look])]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(found["at"]))
    if not found["found"]:
        return [_f("rtc", "Clock module", "ok", f"None found on I2C (looked {when}"
                   + (")." if found["buses"] else "; no I2C bus is enabled on this board: that is a board setting, such as "
                      "an Armbian overlay, and it is yours to change)."), "", [look])]
    out = []
    for x in found["found"]:
        what = f"{x['label']} on bus {x['bus']} at {x['addr']}"
        if not x["supported"]:
            out.append(_f(f"rtc-found:{x['chip']}", "Clock module found", "warn",
                          f"{what}, but this kernel has no driver for it and irate-box has no driver of its own yet.", "", [look]))
            continue
        how = f"the kernel driver {x['kernel']}" if x["kernel"] else "irate-box's own driver (this kernel has none for it)"
        detail = f"{what}, not set up yet; it would run through {how}."
        if x["ambiguous"]:
            detail += " Both chips that use this address fit what it answered: choose the one fitted."
        out.append(_f(f"rtc-found:{x['chip']}:{x['bus']}", "Clock module found", "warn", detail,
                      "Setting it up adds a boot unit that sets the clock from it, and an hourly save.",
                      [_act(f"rtc-setup:{x['chip']}:{x['bus']}:{x['addr']}", f"Set it up as {x['label']}",
                            f"Use the {x['label']} on bus {x['bus']} at {x['addr']} as the box's clock?")]))
    return out


def set_clock(epoch):
    """Set the system clock from the owner's browser. Only while nothing better has set it,
    and only to a plausible time; then saved, so the next boot starts from here."""
    now = time.time()
    c = clock_facts()
    if c["synced"]:
        raise ValueError(f"{c['daemon'] or 'the network time service'} keeps the clock right already; it was left alone")
    newest, what = latest_known_time()
    if newest and epoch < newest - 300:
        raise ValueError(f"that time is before {what} was written, so this device's clock looks wrong; nothing changed")
    if epoch > now + 10 * 365 * 86400 or epoch < 1735689600:  # 2025-01-01
        raise ValueError("that time is not plausible; nothing changed")
    # Not years past anything the box has seen (F14): a forged request could otherwise move the
    # clock far forward, after which it is trusted, saved to the module, and hard to bring back.
    if epoch > max(newest or 0, now) + 2 * 365 * 86400:
        raise ValueError("that is more than two years past the newest time this box has seen; nothing changed")
    if abs(epoch - now) < 30:
        return "The box's clock already agrees with this device (within 30 s); nothing changed."
    r = run("date", "-u", "-s", f"@{int(epoch)}")
    if r.returncode:
        raise ValueError(f"date: {r.stderr.strip()}")
    saved = ""
    rtc.RUN.mkdir(parents=True, exist_ok=True)
    rtc.TRUSTED.write_text(str(int(epoch)))  # the owner set it: worth writing to a clock module from now on
    if rtc.load_config():
        try:
            rtc.save(force=True)
            saved += " The clock module is set too."
        except (rtc.RtcError, OSError) as exc:
            saved += f" The clock module could not be set: {exc}."
    if shutil.which("fake-hwclock"):
        run("fake-hwclock", "save")
        saved += " Saved with fake-hwclock, so the next boot starts from here."
    elif list(Path("/sys/class/rtc").glob("rtc*")) and shutil.which("hwclock"):
        run("hwclock", "--systohc", "--utc")
        saved += " Written to the hardware clock too."
    return (f"Clock moved {_human_secs(epoch - now)} {'forward' if epoch > now else 'back'}, to "
            f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(epoch))}.{saved}")


PREEMPT_WORDS = {"off": "Off", "warn": "Warn", "radio": "Reset the radio", "reboot": "Reset, then restart the box"}


def check_crashwatch(now=None):
    """Crash watch (crashwatch.py): crashes, kernel floods, the radio and pre-emption, the hang settings."""
    from irate_box.root import crashwatch as cw
    now = now or time.time()
    if not (UNIT_DIR / "irate-box-crashwatch.service").exists() and not cw.DIR.exists():
        return []
    st = cw.status()
    s, out = st["settings"], []
    week = [c for c in st["crashes"] if c.get("detected", 0) > now - 7 * 86400]
    if week:
        last = week[-1]
        ago = _human_secs(now - last["detected"])
        lines = [l.strip() for l in last.get("last", "").splitlines()[:4] if l.strip()]
        ev = "; ".join(f"{e['kind']}: {e['text']}" for e in last.get("events", [])[-2:])
        out.append(_f("crash-last", "The box stopped without shutting down", "warn" if now - last["detected"] < 86400 else "ok",
                      f"{len(week)} time{'s' if len(week) != 1 else ''} this week, the last found {ago} ago (a hang, a crash or the "
                      f"power going). Its last snapshot: " + (" | ".join(lines) or "none: snapshots were off") + (f". Before it: {ev}." if ev else "."),
                      f"sudo ls {cw.CRASHES}/{last['dir']}/  (snapshots.log, journal-previous-boot.log)"
                      + ("" if s["watchdog"] or s["panic"] else ". If it froze, the hang settings below restart it by itself next time.")))
    else:
        out.append(_f("crash-last", "The box stopped without shutting down", "ok", "Not in the last week."))
    run = st["running"]
    fl = run.get("floods") or {}
    if fl:
        k, n = max(fl.items(), key=lambda x: x[1])
        rate = n / max(run.get("flood_window", 3600), 30)
        out.append(_f("crash-flood", "The kernel log", "warn",
                      f"Flooded: {n} lines like \"{k}\" in the last {_human_secs(run.get('flood_window', 3600))}, about {rate:.1f} a second. "
                      "A driver logging that much wears the storage the log is synced to and hides the lines that matter; crash watch "
                      "counts them rather than copying them.",
                      "Often a driver's debug output: look for its module's debug parameter, or a newer driver. "
                      "sudo dmesg | tail -50"))
    radios = ", ".join(f"{i} ({r.get('product') or r.get('driver') or '?'})" for i, r in (run.get("radios") or {}).items()) or "none found"
    day = [e for e in st["events"] if e.get("at", 0) > now - 86400 and e.get("kind") in ("failed", "reset", "reboot", "held")]
    level = s["preempt"]
    acts = [_act(f"crashwatch-preempt:{lv}", f"Pre-emption: {PREEMPT_WORDS[lv]}",
                 "Restart the box by itself when the radio fails and a reset doesn't bring it back? Within the uplink "
                 "watchdog's guards: never with guests on the hotspot (unless it says otherwise), during a build or an "
                 "update, within 15 minutes of starting, or past its daily cap." if lv == "reboot" else None)
            for lv in cw.PREEMPT if lv != level]
    detail = (f"Watching: {radios}. Pre-emption: {PREEMPT_WORDS[level]}" + {
        "off": " (a failing radio isn't looked for).", "warn": " (a failing radio is noted and said here, nothing more).",
        "radio": " (a failing radio is reset at once: its USB device unbound and bound again).",
        "reboot": " (a failing radio is reset, and the box restarted if it isn't back within 3 minutes)."}[level])
    if day:
        detail += " In the last day: " + "; ".join(f"{time.strftime('%H:%M', time.localtime(e['at']))} {e['iface']} {e['kind']}: {e['text']}" for e in day[-4:])
    out.append(_f("crash-radio", "The WiFi radio", "warn" if day else "ok", detail,
                  "A radio that keeps failing is often power (a weak supply, a long USB lead) or its driver.", acts))
    wd = st.get("watchdog_device")
    hang = []
    hang.append(_act("crashwatch-panic:" + ("off" if s["panic"] else "on"),
                     "Kernel panic: don't restart" if s["panic"] else "Restart on a kernel panic or lockup",
                     None if s["panic"] else "Restart the box 10 seconds after a kernel panic, oops or lockup, rather than leaving it frozen?"))
    if wd:
        hang.append(_act("crashwatch-watchdog:" + ("off" if s["watchdog"] else "on"),
                         "Watchdog: off" if s["watchdog"] else f"A watchdog ({'the board' if wd == 'hardware' else 'softdog'}) restarts a frozen box",
                         None if s["watchdog"] else "Let a watchdog restart the box when it stops answering for a minute? A box doing heavy work "
                                                    "that starves systemd for that long would restart too."))
    on = [w for w, v in (("restarts after a kernel panic or lockup", s["panic"]), ("a watchdog restarts it if it freezes", s["watchdog"])) if v]
    out.append(_f("crash-hang", "A frozen box", "ok",
                  ("; ".join(on).capitalize() + "." if on else "Nothing restarts it: a frozen box waits for someone to pull the plug.")
                  + ("" if wd else " This kernel has no watchdog (neither the board's nor softdog)."),
                  "Each restart is filed as a crash, with what the box was doing, so nothing is lost by allowing it.", hang))
    out.append(_f("crash-snapshots", "Crash watch's snapshots", "ok" if s["snapshots"] else "warn",
                  f"Every 30 s to {cw.SNAP}, synced: what a crash is explained by." if s["snapshots"] else
                  "Off: a crash leaves only what the journal synced last (on this image's RAM log, up to an hour gone).", "",
                  [_act("crashwatch-snapshots:" + ("off" if s["snapshots"] else "on"), "Snapshots: off" if s["snapshots"] else "Snapshots: on")]))
    return out


def scan():
    findings = []
    for check in (check_install, check_hub, check_clock, check_units, check_kiwix, check_web, check_uplink, check_inventory,
                  check_hotspot, check_space, check_builds, check_crashwatch):
        try:
            findings += check()
        except Exception as exc:  # one broken check must not hide the others
            findings.append(_f(f"check:{check.__name__}", check.__name__.replace("check_", "Check: "), "warn",
                               f"The check itself failed: {exc!r}"))
    return {"at": time.time(), "findings": findings}


# --- repairs ---------------------------------------------------------------------------------------

CHOICE_RE = re.compile(r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]+$|^(kiwix-rebuild|kiwix-off)$"
                       r"|^crashwatch-(snapshots|panic|watchdog):(on|off)$|^crashwatch-preempt:(off|warn|radio|reboot)$"
                       r"|^clock-set:\d{10}$|^(rtc-find|rtc-save|rtc-remove)$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$")


def fix(choice):
    if not CHOICE_RE.match(choice):
        raise ValueError(f"{choice} is not something the doctor does")
    kind, _, arg = choice.partition(":")
    if kind.startswith("crashwatch-"):
        from irate_box.root import crashwatch
        return crashwatch.set_option(kind[len("crashwatch-"):], arg)
    if kind in ("unit-restart", "unit-enable"):
        if not OUR_UNIT.match(arg):
            raise ValueError(f"{arg} is not irate-box's to restart")
        if kind == "unit-enable":
            r = run("systemctl", "enable", arg)
            return f"{arg}: will start at boot" if r.returncode == 0 else f"{arg}: {r.stderr.strip()}"
        run("systemctl", "reset-failed", arg)
        if arg == "irate-box-control.path":
            run("systemctl", "reset-failed", "irate-box-control.service")
        verb = "start" if arg.endswith((".path", ".timer", ".socket")) else "restart"
        r = run("systemctl", verb, arg, timeout=120)
        time.sleep(2)
        state = unit_props(arg).get("ActiveState")
        if r.returncode or state != "active":
            tail = journal_tail(arg, 3)
            raise ValueError(f"{arg} did not start ({state}). " + (" | ".join(tail) if tail else f"journalctl -u {arg}"))
        return f"{arg}: running again"
    if kind == "kiwix-quarantine":
        src = ZIM / arg
        if not arg.endswith(".zim") or "/" in arg or not src.is_file():
            raise ValueError(f"{arg} is not a book in {ZIM}")
        # As the hub (F4): both folders are the hub's, so root has no business there. Done as
        # root, a quarantine/ the hub had made a link was chowned to it, wherever it led.
        hub = ("runuser", "-u", HUB_USER, "--") if os.geteuid() == 0 else ()
        r = run(*hub, "mkdir", "-p", "--", str(QUARANTINE))
        if r.returncode == 0:
            r = run(*hub, "mv", "-n", "-T", "--", str(src), str(QUARANTINE / arg))
        if r.returncode or src.exists():
            raise ValueError(f"{arg} could not be moved to {QUARANTINE}/: {(r.stderr or '').strip()[-160:]}")
        msg = f"{arg} moved to {QUARANTINE}/. "
        if readable_books():
            return msg + kiwix_rebuild()
        LIBRARY.unlink(missing_ok=True)
        run("systemctl", "disable", "--now", "kiwix.service")
        return msg + "No readable book is left, so the library was emptied and Kiwix stopped until one is added."
    if kind == "clock-set":
        return set_clock(int(arg))
    try:
        if choice == "rtc-find":
            f = rtc.find()
            names = ", ".join(f"{x['label']} (bus {x['bus']}, {x['addr']})" for x in f["found"])
            return (f"Found: {names}." if names else "No clock module found" + ("." if f["buses"] else ": no I2C bus is enabled."))
        if kind == "rtc-setup":
            chip, bus, addr = arg.split(":")
            return rtc.setup(chip, int(bus), addr)
        if choice == "rtc-save":
            return rtc.save()
        if choice == "rtc-remove":
            return rtc.remove()
    except rtc.RtcError as exc:
        raise ValueError(str(exc))
    if kind == "kiwix-rebuild":
        return kiwix_rebuild()
    if kind == "kiwix-off":
        run("systemctl", "disable", "--now", "kiwix.service")
        run("systemctl", "reset-failed", "kiwix.service")
        return "Kiwix stopped and kept from starting at boot; rebuilding the library with a readable book starts it again."
    raise ValueError(choice)


# --- plain text ------------------------------------------------------------------------------------

MARK = {"ok": "ok     ", "warn": "note   ", "problem": "problem"}


def report(data, problems_only=False):
    lines = []
    for f in data["findings"]:
        if problems_only and f["status"] == "ok":
            continue
        lines.append(f"{MARK[f['status']]}  {f['check']}: {f['detail']}")
        if f["fix"] and f["status"] != "ok":
            lines.append(f"         what to do: {f['fix']}")
        for a in f["actions"]:
            if a["choice"] == "clock-set":
                lines.append(f"         or: sudo {CODE}/irate-box health fix clock-set:$(date -d 'YYYY-MM-DD HH:MM' +%s)   (from a terminal: give the right time)")
            elif a["choice"] != "rerun-install":
                lines.append(f"         or: sudo {CODE}/irate-box health fix {a['choice']}   ({a['label']})")
    return "\n".join(lines)


def main(argv):
    if os.geteuid() != 0:
        sys.exit("run as root: sudo ./irate-box health [summary | fix CHOICE]")
    if not argv:
        print(report(scan()))
    elif argv == ["summary"]:
        text = report(scan(), problems_only=True)
        if text:
            print(text)
    elif argv == ["--json"]:
        print(json.dumps(scan(), indent=2))
    elif len(argv) == 2 and argv[0] == "fix":
        if argv[1] == "rerun-install":
            sys.exit("Run the installer from the checkout instead: sudo ./install.sh (it reuses this box's recorded options).")
        try:
            print(fix(argv[1]))
        except ValueError as exc:
            sys.exit(f"health.py fix: {exc}")
    else:
        sys.exit("usage: health.py [summary | --json | fix CHOICE]")


if __name__ == "__main__":
    main(sys.argv[1:])
