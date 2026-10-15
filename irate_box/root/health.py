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
    a driver flooding the kernel log; the radio failing (noted there, repaired by the uplink watchdog);
    whether a frozen box restarts by itself

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

from irate_box.hub import jsonlog
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
# The uplink watchdog's report: in RAM on the box (uplink.status_path), in control/ elsewhere.
UPLINK_RUN = Path(os.environ.get("HUB_UPLINK_RUN", "/run/irate-box-uplink")) / "uplink.json"


def uplink_report():
    return UPLINK_RUN if UPLINK_RUN.parent.is_dir() else CONTROL / "uplink.json"
UNIT_DIR = Path("/etc/systemd/system")
# Units this page may restart or enable: irate-box's own and the add-ons'.
OUR_UNIT = re.compile(r"^(irate-box(-[a-z]+)*\.(service|socket|path|timer)|nginx\.service|caddy\.service|kiwix\.service|"
                      r"silverbullet\.service|syncthing@" + re.escape(HUB_USER) + r"\.service|mosquitto\.service|"
                      r"excalidraw-room\.service|ttyd\.service)$")
ADDON_UNITS = {"--with-notes": "silverbullet.service", "--with-sync": f"syncthing@{HUB_USER}.service",
               "--with-mqtt": "mosquitto.service", "--with-collab": "excalidraw-room.service",
               "--with-term": "ttyd.service"}   # and each declared service's (services.py)


def ours(unit):
    """irate-box's own unit, an add-on's, or a declared service's: the doctor may restart or enable it."""
    from irate_box.hub import services
    return bool(OUR_UNIT.match(unit or "")) or services.by_unit(unit) is not None


def watched_units():
    """{unit: package title} for the packages the box watches on their makers' channels (pkgwatch.py)."""
    try:
        from irate_box.root import pkgwatch
        return {d["unit"]: d.get("title") or d["package"] for d in pkgwatch.definitions().values() if d.get("unit")}
    except (OSError, ValueError, KeyError, ImportError):
        return {}


def run(*cmd, timeout=60, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _f(fid, check, status, detail, fix="", actions=(), go=None, glance=None):
    """status: ok | warn | problem. actions: [{choice, label, confirm?}]. go: where it is changed, for the page's
    link: {section: an element id that is always there, mark: the very setting inside it (drawn later, perhaps),
    where: the words}; the page opens the section and highlights the mark. glance: the finding in a few words, for
    the doctor's Summary (detail is the whole of it, below)."""
    out = {"id": fid, "check": check, "status": status, "detail": detail, "fix": fix, "actions": list(actions)}
    if go:
        out["go"] = go
    if glance:
        out["glance"] = glance
    return out


def _go(section, where, mark=None):
    return {"section": section, "where": where, **({"mark": mark} if mark else {})}


def _act(choice, label, confirm=None):
    return {"choice": choice, "label": label, **({"confirm": confirm} if confirm else {})}


RERUN = _act("rerun-install", "Run the installer again",
             "Run install.sh again with this box's recorded options? The installer can repair, upgrade and restart services.")


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
        return [_f("install", "Last install", "warn", "No record of an install (older installs kept none).",
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
                   f"The full output is in {INSTALL_LOG}. Fix what it says (the findings below can name it), then run "
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
    if (UNIT_DIR / "irate-box-led.service").exists() or "irate-box-led" in _installed_text():
        units.append(("irate-box-led.service", "the status light"))
    from irate_box.hub import services
    declared = {s["option"]: s["unit"] for s in services.declared() if s["option"] and s["unit"]}
    for opt, unit in {**declared, **ADDON_UNITS}.items():
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
    # service's failure shows only in its result.
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
                      "Installed by an older version: a burst of /admin clicks can trip systemd's start limit and leave "
                      "the root helper stopped until a reboot.", "Run the installer again (or update): it writes the fixed unit.",
                      [RERUN]))
    # Anything else that failed on the box: not ours to restart, but worth knowing when troubleshooting.
    failed = [l.split()[0] for l in run("systemctl", "--failed", "--no-legend", "--plain").stdout.splitlines() if l.split()]
    # Each failed unit that isn't irate-box's (the image's, or the owner's own), with its options, from the least
    # done: see why; accept it (often harmless on these images); start it again, asked first, as it isn't ours.
    # A package the box watches (Updates → Packages from their makers): known, so said as itself.
    watched = watched_units()
    idle = {i["unit"] for i in idle_units()}   # said by check_idle, with switching it off as the choice
    failed = [u for u in failed if u not in idle]
    for u in [u for u in failed if u in watched]:
        out.append(_f(f"watched-failed:{u}", f"{watched[u]}: its service failed", "warn",
                      f"{u} stopped with an error. The box watches this package's updates; whether it runs is the "
                      "package's own business. A build can fail on a board without the hardware it expects.",
                      f"See why: journalctl -u {u} -n 50. Then start it again, try another build on Updates → Packages "
                      "from their makers (Roll back, or another channel), or accept it if the board does not use it.",
                      [_act(f"other-restart:{u}", "Start it again", f"Start {u} again? The doctor clears its failed mark "
                            "and starts it.")]))
    # The image's LED service: it fails on boards whose light has no trigger file, and the box has a switch for it
    # (Recovery and monitoring, The image's LED service). Said as that, with that switch, not as a stranger to debug.
    if ARMBIAN_LED in failed and not ours(ARMBIAN_LED):
        failed = [u for u in failed if u != ARMBIAN_LED]
        out.append(_f(f"other-failed:{ARMBIAN_LED}", "The image's LED service failed", "warn",
                      f"{ARMBIAN_LED} puts the LEDs back as they were at the last shutdown. On this board it fails at every start: "
                      "the light has no trigger file, so the state it saved is one it then refuses. The box's own status light "
                      "does not need it.", "Switch it off: it stops failing. The same switch is on Recovery and monitoring.",
                      [_act("armbian-led:off", "Switch it off", f"Switch {ARMBIAN_LED} off (mask it)? You can switch it on again "
                            "on Recovery and monitoring.")],
                      go=_go("health-settings", "Recovery and monitoring", "hs-armbian-led")))
    for u in [u for u in failed if not ours(u) and u not in watched][:12]:
        out.append(_f(f"other-failed:{u}", f"{u} failed (not irate-box's)", "warn",
                      "A service of the image's or of your own stopped with an error. The hub does not use it.",
                      f"See why: journalctl -u {u} -n 50. Often harmless on these images: start it again, or select Accept to leave it.",
                      [_act(f"other-restart:{u}", "Start it again", f"Start {u} again? It is not part of irate-box. The doctor "
                            "only clears its failed mark and starts it.")]))
    return out


# --- what adds nothing here ----------------------------------------------------------------------
# Services of the image's that do nothing on this box and could cause trouble (a unit failing at every boot,
# a radio stack nobody uses): each said, with switching it off as the owner's choice and switching it back
# on as its undo; never done for the owner. What was switched off here is kept in IDLE_OFF.
IDLE_OFF = ETC / "idle-off.json"
NO_LORA = re.compile(r"No (?:sx\d+|lr\d+|rf95|llcc68|[a-z0-9]+) radio\b|No radio instance", re.I)


def _idle_off():
    try:
        d = json.loads(IDLE_OFF.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def idle_units():
    """[{unit, title, why}]: services here that do nothing for this box, as far as it can tell."""
    out = []
    p = unit_props("meshtasticd.service")
    if p.get("LoadState") == "loaded" and p.get("UnitFileState") == "enabled":
        log = run("journalctl", "-u", "meshtasticd.service", "-b", "--no-pager", "-o", "cat", "-n", "300").stdout
        if NO_LORA.search(log) and p.get("ActiveState") != "active":
            out.append({"unit": "meshtasticd.service", "title": "meshtasticd, with no LoRa radio",
                        "why": f"It finds no LoRa radio on this box ({NO_LORA.search(log).group(0)}) and stops, at every boot"
                               + (", until systemd gives up on it" if p.get("Result") == "start-limit-hit" else "") + "."})
    p = unit_props("ModemManager.service")
    if p.get("ActiveState") == "active":
        r = run("mmcli", "-L")
        if r.returncode == 0 and "No modems were found" in r.stdout:
            out.append({"unit": "ModemManager.service", "title": "ModemManager, with no modem",
                        "why": "It looks for mobile-data modems, and this box has none. It also probes serial ports, which can upset "
                               "a radio or a device on one."})
    p = unit_props("bluetooth.service")
    if p.get("ActiveState") == "active":
        r = run("bluetoothctl", "devices", "Paired", timeout=10)
        if r.returncode == 0 and not r.stdout.strip():
            out.append({"unit": "bluetooth.service", "title": "Bluetooth, with nothing paired",
                        "why": "Nothing is paired with the box, and nothing on the hub uses Bluetooth. On a radio that does WiFi and "
                               "Bluetooth both (as the AIC8800 does), it shares the chip with the WiFi."})
    return out


def check_idle():
    off = _idle_off()
    out = []
    for i in idle_units():
        if i["unit"] in off:
            continue
        out.append(_f(f"idle:{i['unit']}", f"{i['title']}: adds nothing here", "warn", i["why"],
                      "Switch it off if this box does not need it. It stops and does not start at boot. If you switch it off here, you can "
                      "switch it on again here.",
                      [_act(f"idle-off:{i['unit']}", "Switch it off", f"Switch {i['unit']} off, now and at boot? The doctor lists it "
                            "afterwards with a button to switch it back on.")]))
    for unit, rec in sorted(off.items()):
        out.append(_f(f"idle-off:{unit}", f"{unit}: switched off here", "ok",
                      f"Switched off on {time.strftime('%Y-%m-%d', time.localtime(rec.get('at', 0)))} because it added nothing here.",
                      "", [_act(f"idle-on:{unit}", "Switch it back on", f"Switch {unit} back on, as it was?")]))
    return out


def idle_switch(unit, on):
    """Switch an idle service off (kept in IDLE_OFF, as it was), or back on as it was."""
    if unit not in IDLE_UNITS:
        raise ValueError(f"{unit} is not one the doctor switches off")
    off = _idle_off()
    if on:
        rec = off.pop(unit, None)
        if rec is None:
            raise ValueError(f"{unit} was not switched off here")
        if rec.get("enabled"):
            run("systemctl", "enable", unit)
        if rec.get("active"):
            run("systemctl", "start", unit, timeout=120)
        msg = f"{unit}: switched back on, as it was"
    else:
        p = unit_props(unit)
        off[unit] = {"at": time.time(), "enabled": p.get("UnitFileState") == "enabled", "active": p.get("ActiveState") == "active"}
        r = run("systemctl", "disable", "--now", unit, timeout=120)
        if r.returncode:
            raise ValueError(f"{unit}: {r.stderr.strip()[-200:] or 'could not be switched off'}")
        run("systemctl", "reset-failed", unit)
        msg = f"{unit}: switched off. It does not start at boot."
    from irate_box.root import safeio
    safeio.write(IDLE_OFF, json.dumps(off, indent=1), 0o644)
    return msg


IDLE_UNITS = ("meshtasticd.service", "ModemManager.service", "bluetooth.service")


# --- Kiwix ----------------------------------------------------------------------------------------

def _books():
    return sorted(ZIM.glob("*.zim")) if ZIM.is_dir() else []


def zim_header_problem(path):
    return zimcheck.header_problem(path)


def kiwix_reads(path):
    # As the hub, not root: the books are the hub's, and may be anyone's download.
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
        out.append(_f(f"zim:{b.name}", f"Book {b.name}", "problem", f"{b.stat().st_size >> 20} MB. {why}",
                      f"Download or copy it again (Books keeps its sources current), or set it aside: "
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
                              "Add a book (Books, a USB drive, or install.sh --zim). Then rebuild the library. "
                              "that starts it again.", [_act("kiwix-rebuild", "Rebuild the library")]))
            else:
                out.append(_f("kiwix-empty", "Kiwix (/wiki/)", "problem",
                              "There is no readable book, so kiwix-serve cannot start: systemd keeps trying and then gives up "
                              f"({p.get('Result') or p.get('ActiveState')}).",
                              "Add a book (Books, a USB drive, or install.sh --zim). Then rebuild the library. "
                              "Until then, stop Kiwix so it does not loop: systemctl disable --now kiwix.",
                              [_act("kiwix-off", "Stop Kiwix until there are books")]))
        elif p.get("ActiveState") != "active" and p.get("Result") in ("success", "", None):
            out.append(_f("kiwix-down", "Kiwix (/wiki/)", "warn",
                          f"Stopped (not failed), with {len(good)} readable books"
                          + (", not started at boot" if p.get("UnitFileState") != "enabled" else "") + ".",
                          "Kiwix was stopped from this page or by hand. To bring /wiki/ back, rebuild the library (it starts Kiwix) "
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
        return "kiwix-manage made no library. The old library stays."
    # Already the hub's (kiwix-manage made it as the hub): no chown, which would follow a link
    # swapped in for it. The rename replaces a link, never follows one.
    os.replace(new, LIBRARY)
    msg = f"Library rebuilt: {len(books) - len(skipped)} books" + (f" (skipped {', '.join(skipped)})" if skipped else "")
    if (UNIT_DIR / "kiwix.service").exists():
        run("systemctl", "reset-failed", "kiwix.service")
        run("systemctl", "enable", "--now", "kiwix.service")
        run("systemctl", "restart", "kiwix.service")
        msg += ". Kiwix started"
    return msg + "."


# --- the rest -------------------------------------------------------------------------------------

def check_uplink():
    if not (UNIT_DIR / "irate-box-uplink.service").exists():
        return []
    try:
        st = json.loads(uplink_report().read_text())
    except (OSError, ValueError):
        return [_f("uplink-report", "Uplink watchdog report", "warn", "The watchdog has not written a report yet.",
                   "journalctl -u irate-box-uplink -n 30", [_act("unit-restart:irate-box-uplink.service", "Start it again")],
                   glance="No report yet.")]
    age = time.time() - st.get("at", 0)
    limit = 3 * max(st.get("settings", {}).get("check", 60), 60)
    if age > limit:
        return [_f("uplink-report", "Uplink watchdog report", "problem", f"Last report {int(age // 60)} min ago: it has stopped looking.",
                   "journalctl -u irate-box-uplink -n 30; systemctl restart irate-box-uplink",
                   [_act("unit-restart:irate-box-uplink.service", "Start it again")], glance=f"Stopped: last report {int(age // 60)} min ago.")]
    from irate_box.hub import uplink
    try:
        said = uplink.words(uplink.validate(st.get("chosen") or {}))
    except (ValueError, TypeError, KeyError):
        said = "settings not readable"
    state = {"up": "Up", "checking": "Checking", "down": "Down", "stalled": "Down, nothing left to try"}.get(st.get("state"), str(st.get("state")).capitalize())
    out = [_f("uplink-report", "Uplink watchdog report", "ok", f"{st.get('state')} on {st.get('iface')}, {said}.",
              glance=f"{state} on {st.get('iface')}.")]
    # Stalled: down, with nothing left the level allows that could help.
    stall = st.get("stall") if st.get("state") == "stalled" else None
    if stall:
        down = int((st.get("at", 0) - ((st.get("outage") or {}).get("since") or st.get("at", 0))) // 60)
        out.append(_f("uplink-stalled", "The link is down and the watchdog has stalled", "problem",
                      f"{st.get('iface')} down for {down} min. {stall.get('text', '')}",
                      (f"Network → Network recovery: {stall['label'][:1].upper() + stall['label'][1:]} now, "
                       "or select a level that goes that far." if stall.get("needs")
                       else "Look at the box by its console or a cable: nothing it can do by itself would help."),
                      go=_go("net-watchdog-card", "Network → Network recovery", "up-stall" if stall.get("needs") else "up-reach")))
    out += link_findings(st)
    out += roaming_findings(st, _json_or(CONTROL / "netinv.json"))
    out += metered_findings()
    out += step_findings(jsonlog.read(CONTROL / "uplink-ladder.json"), st.get("chosen") or {})
    return out


LINK_KIND = {"wifi": "WiFi", "ethernet": "cable", "other": "a link of another kind"}


def link_findings(st):
    """The links nobody watches: none at all; one that carries the box, or would (a connection set to start by itself on
    it), and isn't watched, louder when it is down too, with a reconnect; one the owner set not to be watched is theirs.
    And each watched link but the first that has stalled, and one of a kind the box knows no repair for."""
    inv = st.get("inventory")
    if not isinstance(inv, dict):
        return []   # a report from before the watchdog watched links one by one
    links, chosen = st.get("links") or {}, st.get("chosen") or {}
    off = {i for i, v in (chosen.get("watch") or {}).items() if v is False}
    watched = sorted(i for i, lk in links.items() if lk.get("watched"))
    go = lambda mark: _go("net-watchdog-card", "Network → Network recovery", mark)  # noqa: E731
    out = []
    if not watched and inv:
        out.append(_f("uplink-none-watched", "No link is watched", "problem",
                      f"The watchdog is watching {st.get('watching') or 'nothing'}. If the box's network fails, nothing will notice or "
                      "try again.", "Network → Network recovery: select which links it watches.", go=go("up-watch")))
    for i, lk in sorted(inv.items()):
        what = f"{i} ({LINK_KIND.get(lk.get('kind'), lk.get('kind'))}, {', '.join(lk.get('autoconnect') or []) or 'no connection set to start'})"
        if lk.get("look") and not lk.get("up") and lk.get("autoconnect"):
            out.append(_f(f"uplink-look-{i}", f"{i} is down, and only looked at", "warn",
                          f"{what} is down. The watchdog looks at it but does nothing for it, as set (every other link looked at "
                          "only). After a password check fails, NetworkManager stops and waits for someone.",
                          f"Reconnect it now; or on Network → Network recovery, watch {i} in full.",
                          [_act(f"uplink-reconnect:{i}", "Reconnect it")], go=go(f"up-watch-{i}")))
            continue
        if lk.get("watched") or (lk.get("as") or i) in off or not lk.get("autoconnect"):
            continue
        if lk.get("up"):
            out.append(_f(f"uplink-uncovered-{i}", f"{i} is not watched", "warn",
                          f"{what} is up, but nothing watches it: if it fails, nothing will notice or try again.",
                          f"Network → Network recovery: watch {i}, or set it not to be watched to say that is meant.",
                          go=go(f"up-watch-{i}")))
        else:
            out.append(_f(f"uplink-uncovered-{i}", f"{i} is down, and not watched", "problem",
                          f"{what} is down. NetworkManager has a connection for it set to start by itself, but nothing watches "
                          f"{i}, so nothing tries again. After a password check fails, NetworkManager stops and waits for someone.",
                          f"Reconnect it now; and on Network → Network recovery, watch {i}.",
                          [_act(f"uplink-reconnect:{i}", "Reconnect it")], go=go(f"up-watch-{i}")))
    for i in watched:
        lk = links[i]
        if lk.get("kind") == "other":
            out.append(_f(f"uplink-kind-{i}", f"{i}: watched, no repair known", "ok",
                          f"{i} is a link of a kind the watchdog knows no repair for (not WiFi, not a cable): its failures are logged, "
                          "and nothing more is done."))
        stall = lk.get("stall") if lk.get("state") == "stalled" and i != st.get("iface") else None
        if stall:
            out.append(_f(f"uplink-stalled-{i}", f"{i} is down and its watchdog has stalled", "problem" if stall.get("needs") else "warn",
                          f"{i}: {stall.get('text', '')}",
                          (f"Network → Network recovery: select a reach that goes as far as to {stall['label']}."
                           if stall.get("needs") else "Nothing the box can do by itself would help."), go=go("up-reach")))
    return out


# How well each step works, per connection, from the watchdog's ladder record: a step of its own (not
# one pressed by hand) "mended" an outage when it was the last taken before the link came back, within
# MEND_S, and the link held for HELD_S after (no drop or wedge, even at the moment it came back; a roam's
# flap minutes later is not held against it); a wedged driver "followed" it when one was seen within WEDGE_S
# and no other step came between (one wedge is charged to one step), and none was seen up to the step
# itself (a wedge logged in the same look as the step is what the step answered). Not judged: a step
# taken while the network was at fault (its cause: no step of the box's can bring a network back), and a
# repeat of the same step within REPEAT_S (the same evidence answered twice, as before the watchdog read
# evidence only from after a reset).
STEP_DAYS = 14
FLAKY_OUTAGES = 5      # a connection with fewer outages in STEP_DAYS is not judged
STEP_TRIES = 4         # nor a step tried fewer times
MEND_S, HELD_S, WEDGE_S, REPEAT_S = 300, 120, 180, 120
NET_CAUSES = ("absent", "join", "gateway")   # uplink.NET_CAUSES: the network at fault, not the box
STEP_SAID = {"reconnect": "reconnecting", "restart": "restarting the network service", "radio": "resetting the radio",
             "reboot": "rebooting"}
STEP_TICK = {"reconnect": "Reconnect", "restart": "Restart the network service", "radio": "Reset the radio", "reboot": "Reboot the box"}


def _no_misses(row):
    """A flap declared with no misses at all, as the watchdog once did in the look the link came back in:
    not a relapse."""
    return row.get("k") == "down" and str(row.get("t", "")).startswith("Dropped or missed 0 times")


def step_record(rows, now=None):
    """{iface: {"outages": n, "steps": {step: {"tries", "mended", "wedged"}}}} over the last STEP_DAYS."""
    now = time.time() if now is None else now
    since = now - STEP_DAYS * 86400
    by = {}
    for r in sorted((r for r in rows if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and r.get("i")), key=lambda r: r["at"]):
        by.setdefault(r["i"], []).append(r)
    out = {}
    for iface, rs in by.items():
        downs = [r["at"] for r in rs if r.get("k") == "down"]
        rec = {"outages": sum(1 for t in downs if t >= since), "steps": {}}
        cause, last = None, {}
        for n, r in enumerate(rs):
            k = r.get("k")
            if k == "down":
                cause = None
            elif k == "cause":
                cause = r.get("s")
            if k != "repair" or not r.get("s") or r["at"] < since or r.get("b") == "hand":
                continue
            again = r["at"] - last.get(r["s"], float("-inf")) <= REPEAT_S
            last[r["s"]] = r["at"]
            if cause in NET_CAUSES or again:
                continue
            later = rs[n + 1:]
            nxt = next((x for x in later if x.get("k") in ("repair", "up")), None)
            mended = False
            if nxt and nxt.get("k") == "up" and nxt["at"] - r["at"] <= MEND_S:
                back = nxt["at"]
                stale = {x["at"] for x in later if _no_misses(x)}   # that look's wedge was the evidence from before
                mended = not any(x is not nxt and x.get("k") in ("down", "wedged") and back <= x["at"] <= back + HELD_S
                                 and not _no_misses(x) and not (x.get("k") == "wedged" and x["at"] in stale) for x in later)
            # The first wedge after it, if no other step came first: charged to this step only.
            first = next((x for x in later if x.get("k") in ("wedged", "repair")), None)
            wedged = bool(first) and first.get("k") == "wedged" and 0 <= first["at"] - r["at"] <= WEDGE_S and \
                not any(x.get("k") == "wedged" and 0 <= r["at"] - x["at"] <= WEDGE_S for x in rs[:n])
            st = rec["steps"].setdefault(r["s"], {"tries": 0, "mended": 0, "wedged": 0})
            st["tries"] += 1
            st["mended"] += mended
            st["wedged"] += wedged
        out[iface] = rec
    return out


def step_findings(rows, chosen, now=None):
    """For each flaky connection: how well each step has worked, said; and a finding for a step that rarely mends an
    outage or that a wedged driver tends to follow, linked to where it is left out (Network's own steps setting)."""
    off = (chosen or {}).get("steps_off") or {}
    out = []
    for iface, rec in sorted(step_record(rows, now).items()):
        if rec["outages"] < FLAKY_OUTAGES:
            continue
        steps = rec["steps"]
        said = "; ".join(f"{STEP_SAID.get(s, s)} mended {v['mended']} of {v['tries']}" + (f", a wedged driver after it {v['wedged']} time{'s' if v['wedged'] != 1 else ''}" if v["wedged"] else "")
                         for s, v in sorted(steps.items(), key=lambda kv: list(STEP_SAID).index(kv[0]) if kv[0] in STEP_SAID else 9))
        out.append(_f(f"uplink-steps-{iface}", f"How well each step works on {iface}", "ok",
                      f"{rec['outages']} outages in {STEP_DAYS} days. " + (said + "." if said else "No step taken.")))
        for s, v in steps.items():
            if s in off.get(iface, []) or v["tries"] < STEP_TRIES or s == "reboot":
                continue
            poor = v["mended"] / v["tries"] <= 0.2
            if not poor and v["wedged"] < 2:
                continue
            why = f"{STEP_SAID.get(s, s)[:1].upper() + STEP_SAID.get(s, s)[1:]} mended {v['mended']} of the {v['tries']} outages it was tried on"
            if v["wedged"] >= 2:
                why += f", and the radio's driver wedged right after it {v['wedged']} times"
            out.append(_f(f"uplink-skip-{iface}-{s}", f"{iface}: {STEP_SAID.get(s, s)} isn't helping", "warn",
                          f"{why} (the last {STEP_DAYS} days). The watchdog could leave it out on {iface} and go on to the next step sooner.",
                          f"Network → Connection settings → Recovery actions: clear {STEP_TICK.get(s, s)} for {iface}.",
                          go=_go("up-steps", "Network → Recovery actions", f"up-step-{iface}-{s}")))
    return out


ROAMS_AN_HOUR = 6   # more than this, with the hotspot on the same radio, is worth a look


def _json_or(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def roaming_findings(st, inv):
    """Roaming often while the hotspot shares the radio: each roam moves the guests'
    channel too, and is a re-association on the radio. Said with the choices, never done for the owner."""
    radio = next((r for r in (inv or {}).get("radios", []) if r.get("iface") == st.get("iface") and r.get("roaming")), None)
    f = (radio or {}).get("roaming") or {}
    n, chosen = st.get("roams_hour") or 0, (st.get("chosen") or {}).get("roaming", "roam")
    if not f.get("hotspot_shares") or len(f.get("aps", [])) < 2 or chosen != "roam" or n <= ROAMS_AN_HOUR:
        return []
    return [_f("uplink-roaming", "Roaming often, with the hotspot on the same radio", "warn",
               f"{st.get('iface')} moved between access points {n} times in the last hour ({len(f['aps'])} share {f.get('ssid')}, "
               f"channels {', '.join(map(str, f.get('channels', [])))}); each move takes the hotspot's guests along, and is a moment off "
               "the network on a radio that copes worst with that.",
               "Network → Network recovery: select no background scans while the hotspot shares the radio, a lock to one access "
               "point, or do not count short roams as missed checks. Or, on your router, put the access points on one channel.")]


def metered_findings(now=None):
    """Scheduled updates and downloads waiting a long while because the box is on a metered network."""
    from irate_box.hub import netpolicy
    w = netpolicy.waiting()
    now = now or time.time()
    if not w or w.get("since") is None or now - float(w["since"]) < 3 * 86400:
        return []
    days = int((now - float(w["since"])) // 86400)
    return [_f("metered-wait", "Updates waiting for another network", "warn",
               f"For {days} days the box has been on a metered network ({w.get('connection') or w.get('iface')}, {w.get('how')}), "
               "so its scheduled updates and downloads (the hub's own, books, mirrors, toolkits) have waited.",
               "Updates: run one by hand, or on Network → WiFi set this network not metered (its own settings) if its data is not limited.")]


def check_inventory():
    """The network inventory's findings that lower the chance of the box staying on its network (its
    warnings and problems), each with its fix, and how old the look is when that matters."""
    p = CONTROL / "netinv.json"
    if not p.exists():
        return [_f("netinv", "Network inventory", "warn", "Not looked yet.", "", [_act("net-scan", "Look now")])]
    inv = _json_or(p)
    if not isinstance(inv, dict):
        return [_f("netinv", "Network inventory", "warn", "The last scan cannot be read.", "", [_act("net-scan", "Look again")])]
    age = time.time() - float(inv.get("at") or 0)
    when = f" (as of a look {int(age // 3600)} h ago)" if age > 6 * 3600 else ""
    out = [_f(f"net:{h['id']}", h["title"], h["status"], h["detail"] + when, h.get("fix", ""))
           for h in inv.get("hazards") or [] if h.get("status") in ("warn", "problem")]
    if age > 24 * 3600:
        out.append(_f("netinv", "Network inventory", "warn", f"The last look at the network was {int(age // 86400)} days ago.", "",
                      [_act("net-scan", "Look again")]))
    return out


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
        out.append(_f("hotspot-iface", "Hotspot interface", "problem", f"The hotspot should be on {iface}, but {iface} is not on the box.",
                      "Network → the hotspot: switch it off and on again"))
    elif chans.get(iface) != want:
        out.append(_f("hotspot-iface", "Hotspot interface", "warn",
                      f"{iface} is on channel {chans.get(iface)}, not {want}" + (" (the WiFi link's)" if plan.get("follows_uplink") else "") + ".",
                      "Network → the hotspot: switch it off and on again"))
    else:
        out.append(_f("hotspot-iface", "Hotspot interface", "ok", f"{iface} on channel {want}: {plan.get('text', '')}"))
    if plan.get("backend") == "hostapd":
        if run("systemctl", "is-active", "--quiet", ap.HOSTAPD_UNIT).returncode:
            out.append(_f("hotspot-ap", "Hotspot access point (hostapd)", "problem", "hostapd is not running.",
                          f"journalctl -u {ap.HOSTAPD_UNIT} -n 30", [_act(f"unit-restart:{ap.HOSTAPD_UNIT}", "Start it again")]))
    else:
        act = run("nmcli", "-t", "-f", "NAME", "connection", "show", "--active").stdout or ""
        if ap.CONNECTION not in act.split():
            out.append(_f("hotspot-ap", "Hotspot access point (NetworkManager)", "problem", f"The connection {ap.CONNECTION} is not active.",
                          f"nmcli connection up {ap.CONNECTION}"))
    if run("systemctl", "is-active", "--quiet", ap.DNSMASQ_UNIT).returncode:
        out.append(_f("hotspot-dns", "Hotspot DHCP and names", "problem", "dnsmasq is not running. New guests get no address.",
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
                   "If the error is in irate-box's site, the installer rewrites it. If the error is in an administrator's own configuration, "
                   "correct that file and reload.", [RERUN])]
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
                          "Free some: old books (Books), saved work, build runs (Git → Builds), "
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
    failing = {fam: len(states) for fam, states in sorted(families.items()) if len(states) >= 2 and "passed" not in states}
    if failing:
        said = ", ".join(f"{fam} ({n})" for fam, n in failing.items())
        out.append(_f("builds-families", "Firmware builds that always fail", "warn",
                      f"Every build here has failed for {'this family' if len(failing) == 1 else 'these families'}: {said}.",
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
        out.append(_f("builds-disk", "Space used by firmware builds", "warn" if share > 0.2 else "ok",
                      f"{used >> 20} MB ({share:.0%} of the card): runs, and the builder's tools and caches.",
                      "Git → Builds: delete old runs (the Firmware Factory keeps each target's two newest). "
                      "The builder's PlatformIO tools are in /var/lib/hub/ci/home." if share > 0.2 else ""))
    return out


# --- the clock ---------------------------------------------------------------------------------
# These boards have no clock that keeps time while they are off (the Lyra has no RTC at all),
# and the hub is meant to run offline. fake-hwclock restores the last time it saved, so a box
# that was off for two days boots two days behind, put right by chrony only when the box is
# online. The hub's own expiry
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
    # Root's own files only: the hub could touch its clock.json, or plant files of its own.
    for path, what in ((CODE / "VERSION", "the installed code"), (CONTROL / "health.json", "the last health check"),
                       (uplink_report(), "the watchdog's report")):
        m = rtc.root_mtime(path)
        if m:
            marks.append((m, what))
    return max(marks) if marks else (None, None)


SET_CLOCK = _act("clock-set", "Set the clock from this browser",
                 "Set the box's clock to this device's time? Use it only if this device's own clock is right.")


SOURCE_STATUS = {"in-use": "ok", "answering": "ok", "not-answering": "problem", "not-found": "warn", "unknown": "ok"}


def check_sources():
    """Each source chosen on the Clock page, as it stands: in use (or answering) fine, chosen but not answering a
    problem, chosen but not found (no GPS receiver, a name that does not resolve) a warning."""
    try:
        from irate_box.root import clock
        rec = clock.load()
        if not rec.get("service"):
            return []
        rows = clock.source_states(rec)
    except Exception:  # noqa: BLE001 - the clock's own finding still goes out
        return []
    said = {"in-use": "in use", "answering": "answering, but not used now (another source is better)",
            "not-answering": "selected, but not answering", "not-found": "selected, but not found",
            "unknown": "selected (systemd-timesyncd says only the server it uses now, and it is another)"}
    fixes = {"gps": "Connect the GPS receiver (USB or the UART), and give it a view of the sky.",
             "local": "Check that the server is on, on this network, and serves the time (UDP port 123). Or change it on the Clock page.",
             "pool": "The box needs the internet for the pool. Or select another source on the Clock page."}
    out = []
    for r in rows:
        st = SOURCE_STATUS[r["state"]]
        out.append(_f(f"clock-source:{r['source']}", f"Time source: {r['name']}", st, f"{r['name'][0].upper() + r['name'][1:]}: "
                      f"{said[r['state']]}.", fixes[r["source"]] if st != "ok" else ""))
    return out


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
                      f"before {what} was written, so the clock is behind.",
                      "Use the button to set it from this browser's clock. Or run sudo date -s 'YYYY-MM-DD HH:MM'. "
                      "If the box is online, the network time service sets the clock.", [SET_CLOCK]))
    boot = ""
    if c["steps"]:
        total = sum(c["steps"])
        boot = (f" At this boot it started {_human_secs(total)} {'behind' if total > 0 else 'ahead'}"
                + (" (restored from fake-hwclock's last save: the box keeps no time while off)" if c["fake"] and not c["rtcs"] else "")
                + " and was put right once the network was reached.")
    out += check_module()
    out += check_sources()
    if c["synced"]:
        by = (f"Set by {c['daemon']}" + (f" from {c['sources']} time servers" if c["sources"] else "") if c["daemon"]
              else "Synchronised, according to the kernel (no time service seen here. A host or container can keep the time)")
        out.append(_f("clock", "Clock", "ok", f"{by}. Time zone {tz}.{boot}"))
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
                      "Not set since this boot, and the box has no clock that runs while it is off. The time was restored from "
                      "the last fake-hwclock save, so it is behind by the time that the box was off."
                      + since + (f" {c['daemon']} is running but has no time server to ask (offline)." if c["daemon"] else "")
                      + ("" if c["fake_saver"] else " fake-hwclock is not saving the time regularly, so even that is old.")
                      + f" Time zone {tz}.",
                      "If the box is online, it sets the clock. If it is offline, use the button to set it from this browser's clock "
                      "(your phone or laptop knows the time). Or run sudo date -s 'YYYY-MM-DD HH:MM'.", [SET_CLOCK]))
    else:
        out.append(_f("clock", "Clock", "problem",
                      "No clock that runs while the box is off, no fake-hwclock to restore the last time, and not set from "
                      "the network: a boot can start at the image's build date or at 1970."
                      + (f" {c['daemon']} is running but has no time server to ask." if c["daemon"] else ""),
                      "Install fake-hwclock (apt-get install fake-hwclock) so a boot at least starts where the last one "
                      "stopped. Until then, use the button to set the clock from this browser.", [SET_CLOCK]))
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
                          "Setting it up again turns on the battery switch. This page never changes charging."
                          if "INIEN" in n or "EOSC" in n else "Check which kind of cell the module has."))
        if st["units"].get("irate-box-rtc.service") != "active":
            out.append(_f("rtc-unit", "Clock module", "warn", "Its boot unit has not run, so the next boot can fail to read it.",
                          "systemctl start irate-box-rtc", [_act("unit-restart:irate-box-rtc.service", "Run it now")]))
        return out
    look = _act("rtc-find", "Look for a clock module")
    try:
        found = json.loads(rtc.FOUND.read_text())
    except (OSError, ValueError):
        if list(Path("/sys/class/rtc").glob("rtc*")):
            return []  # the kernel has a hardware clock already; nothing to offer unless asked
        return [_f("rtc", "Clock module", "warn", "No clock module set up. A battery-backed module on I2C (DS3231, RV-8803, "
                   "RX8130 and others) lets the box keep its time while it is off.", "", [look])]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(found["at"]))
    if not found["found"]:
        return [_f("rtc", "Clock module", "warn", f"None found on I2C (scanned {when}"
                   + (")." if found["buses"] else ". No I2C bus is enabled on this board. An administrator can change this board setting, such as "
                      "an Armbian overlay)."), "", [look])]
    out = []
    for x in found["found"]:
        what = f"{x['label']} on bus {x['bus']} at {x['addr']}"
        if not x["supported"]:
            out.append(_f(f"rtc-found:{x['chip']}", "Clock module found", "warn",
                          f"{what}, but this kernel has no driver for it and irate-box has no driver of its own yet.", "", [look]))
            continue
        how = f"the kernel driver {x['kernel']}" if x["kernel"] else "irate-box's own driver (this kernel has none for it)"
        detail = f"{what}, not set up yet. It would run through {how}."
        if x["ambiguous"]:
            detail += " Both chips that use this address fit what it answered. Select the one that is fitted."
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
        raise ValueError(f"that time is before {what} was written, so this device's clock looks wrong. The clock did not change.")
    if epoch > now + 10 * 365 * 86400 or epoch < 1735689600:  # 2025-01-01
        raise ValueError("that time is not plausible. The clock did not change.")
    # Not years past anything the box has seen: a forged request could otherwise move the
    # clock far forward, after which it is trusted, saved to the module, and hard to bring back.
    if epoch > max(newest or 0, now) + 2 * 365 * 86400:
        raise ValueError("that is more than two years past the newest time this box has seen. The clock did not change.")
    if abs(epoch - now) < 30:
        return "The box's clock already agrees with this device (within 30 s). The clock did not change."
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


def check_usb_filesystems():
    """Which filesystems a USB stick may have for this box to read it (books, toolkits, imports)."""
    from irate_box.root import usbstick
    can = usbstick.readable()
    words = lambda names: ", ".join(dict.fromkeys(usbstick.FS_WORDS[n] for n in sorted(names, key=lambda n: list(usbstick.FS_WORDS).index(n))))  # noqa: E731
    yes = [n for n, ok in can.items() if ok]
    no = [n for n, ok in can.items() if not ok and not (n == "ntfs3" and can.get("ntfs")) and not (n == "ntfs" and can.get("ntfs3"))]
    return [_f("usb-filesystems", "USB drives this box can read", "ok",
               f"{words(yes)}." + (f" Not {words(no)}: this kernel has no driver for {'them' if len(no) > 1 else 'it'}." if no else ""),
               "Use a drive formatted with one of the filesystems above." if no else "")]


def check_crashwatch(now=None):
    """Crash watch (crashwatch.py): crashes, kernel floods, the radio's failures, the hang settings."""
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
                      "A driver that logs this much wears the storage that the log is synced to and hides the lines that matter. Crash watch "
                      "counts them and does not copy them.",
                      "Often a driver's debug output: look for its module's debug parameter, or a newer driver. "
                      "sudo dmesg | tail -50"))
    radios = ", ".join(f"{i} ({r.get('product') or r.get('driver') or '?'})" for i, r in (run.get("radios") or {}).items()) or "none found"
    # What crash watch saw; the old events of its own resets and reboots, from before it only recorded, said as they were.
    day = [e for e in st["events"] if e.get("at", 0) > now - 86400 and e.get("kind") in ("failed", "driver-reset", "recovered", "reset", "reboot")]
    detail = f"Watching: {radios}. Crash watch notes and files a failing radio; the uplink watchdog does the repairing."
    if day:
        detail += " In the last day: " + "; ".join(f"{time.strftime('%H:%M', time.localtime(e['at']))} {e['iface']} {e['kind']}: {e['text']}" for e in day[-4:]) + "."
    kept = [i for i in st.get("incidents", []) if i.get("at", 0) > now - 7 * 86400]
    if kept:
        detail += f" Kept as it happened: {len(kept)} this week, the last in {cw.CRASHES}/{kept[-1]['dir']}/ (snapshots.log, kernel.log)."
    failed = any(e.get("kind") in ("failed", "driver-reset") for e in day)
    glance = (f"Watching {', '.join(run.get('radios') or {}) or 'no radio'}" + ("; it failed in the last day" if failed else "")
              + (f"; {len(kept)} failure{'s' if len(kept) != 1 else ''} kept this week" if kept else "") + ".")
    out.append(_f("crash-radio", "The WiFi radio", "warn" if failed else "ok", detail,
                  "A radio that keeps failing is often power (a weak supply, a long USB lead) or its driver. "
                  "What is done when it fails: Network → Connection settings → Failed radio." if failed else "",
                  go=_go("net-watchdog-card", "Network → Failed radio", "up-wedge") if failed else None, glance=glance))
    wd, pending = st.get("watchdog_device"), st.get("watchdog_pending")
    on = [w for w, v in (("restarts after a kernel panic or lockup", s["panic"]), ("a watchdog restarts it if it freezes", s["watchdog"])) if v]
    said = "; ".join(on).capitalize() + "." if on else "Nothing restarts it: a frozen box waits for someone to pull the plug."
    if s["watchdog"] and pending:
        said += " The board's watchdog starts working after the next restart."
    elif wd == "switched-off" and not s["watchdog"]:
        said += " The board has its own watchdog, switched off in its device tree. Turning the watchdog on switches it on."
    elif not wd:
        said += " This kernel has no watchdog (neither the board's nor softdog)."
    out.append(_f("crash-hang", "A frozen box", "warn" if week and not on else "ok", said,
                  "Each restart is filed as a crash, with what the box was doing, as far as the box can record it. "
                  "An automatic restart can lose unsaved data and stop running jobs. Set on Recovery and monitoring.", go=_go("health-settings", "Recovery and monitoring", "hs-panic")))
    out.append(_f("crash-snapshots", "Crash watch's snapshots", "ok" if s["snapshots"] else "warn",
                  f"Every 30 s to {cw.SNAP}, synced: what a crash is explained by." if s["snapshots"] else
                  "Off: a crash leaves only what the journal synced last (on this image's RAM log, up to an hour gone).",
                  "Set on Recovery and monitoring.", go=_go("health-settings", "Recovery and monitoring", "hs-snapshots")))
    return out


WD_WORDS = {"hardware": "the board's", "softdog": "the kernel's softdog", "switched-off": "the board's, from the next restart"}


def setting_groups(s, wd):
    """The doctor's own settings as choice groups for its settings card: {key, label, desc, current, options:
    [{value, label, choice (health.fix's), confirm?}]}, the safest first. The watchdog only where there is one."""
    groups = [
        {"key": "snapshots", "label": "Crash watch's snapshots", "desc": "Every 30 s, what the box was doing, kept on its own storage: what explains a crash.",
         "current": "on" if s["snapshots"] else "off", "options": [
             {"value": "on", "label": "On", "choice": "crashwatch-snapshots:on"},
             {"value": "off", "label": "Off", "choice": "crashwatch-snapshots:off"}]},
        {"key": "panic", "label": "A kernel panic, oops or lockup", "desc": "Nothing in user space can act on a frozen kernel.",
         "current": "on" if s["panic"] else "off", "options": [
             {"value": "off", "label": "Stay stopped", "choice": "crashwatch-panic:off"},
             {"value": "on", "label": "Restart after 10 s", "choice": "crashwatch-panic:on",
              "confirm": "Restart the box 10 seconds after a kernel panic, oops or lockup, rather than leaving it frozen?"}]}]
    if wd or s["watchdog"]:
        groups.append({"key": "watchdog", "label": "A box that stops answering", "desc": f"A watchdog ({WD_WORDS.get(wd, 'the board' + chr(39) + 's')}) "
                       "that systemd keeps fed: a frozen system stops feeding it, and the box restarts.",
                       "current": "on" if s["watchdog"] else "off", "options": [
                           {"value": "off", "label": "Stay stopped", "choice": "crashwatch-watchdog:off"},
                           {"value": "on", "label": "Restart after a minute", "choice": "crashwatch-watchdog:on",
                            "confirm": "Let a watchdog restart the box when it stops answering for a minute? A box doing heavy work that "
                                       "starves systemd for that long would restart too." + (
                                " The board's watchdog is switched off in its device tree: this switches it on with a boot overlay, so it "
                                "starts working after the next restart." if wd == "switched-off" else "")}]})
    return groups


LED_SHOW_LABEL = {"leave": "Leave it", "alive": "The box is alive", "network": "Alive and on its network",
                  "all": "Alive, on its network, nothing wrong"}
ARMBIAN_LED = "armbian-led-state.service"


def led_groups(ls, armbian=None):
    """The status light's choices for the settings card: what it shows, and which LED (when there is a choice).
    armbian: the image's LED state service ("failed", "masked", or None when it is fine or not there)."""
    if not ls or not ls.get("found"):
        return []
    found, chosen = ls["found"], ls["settings"]
    show = ls["settings"].get("show") or ls.get("suggested") or "leave"
    groups = [{"key": "led-show", "label": "The status light",
               "desc": f"What {ls.get('led') or 'the light'} shows: 1 s on, 1 s off while all it reports is well; off when not; "
                       "quick flashes while the network is being repaired, two short flashes when the Box doctor has a problem "
                       "to read, steady on while the box starts or updates."
                       + (" Left alone by default: on this board it shows the SD card working." if ls.get("suggested") == "leave" else ""),
               "current": show, "options": [{"value": v, "label": LED_SHOW_LABEL[v], "choice": f"led-show:{v}"}
                                            for v in ("leave", "alive", "network", "all")]}]
    if len(found) > 1 or chosen.get("led", "auto") != "auto":
        groups.append({"key": "led-which", "label": "Which light", "desc": "The board's LEDs that could be the status light "
                       "(never a power LED). The Box doctor's \"Flash it\" shows which is which.",
                       "current": chosen.get("led", "auto"),
                       "options": [{"value": "auto", "label": f"The box's pick ({ls.get('led') or 'none'})" if chosen.get("led", "auto") == "auto"
                                    else "The box's pick", "choice": "led-led:auto"}]
                       + [{"value": n, "label": n, "choice": f"led-led:{n}"} for n in found]})
    if armbian:
        groups.append({"key": "armbian-led", "label": "The image's LED service",
                       "desc": f"{ARMBIAN_LED} puts the LEDs back as they were at the last shutdown. On this board it "
                               "fails at every start: the light has no trigger file, so the state it saved is one it then refuses. "
                               "Switching it off stops the failure. Switching it on again restores the failure.",
                       "current": "off" if armbian == "masked" else "on", "options": [
                           {"value": "on", "label": "As the image has it", "choice": "armbian-led:on"},
                           {"value": "off", "label": "Switched off", "choice": "armbian-led:off"}]})
    return groups


def armbian_led_state():
    """The image's LED state service: "failed" or "masked" (worth a choice), else None."""
    if not (Path("/usr/lib/systemd/system") / ARMBIAN_LED).exists() and not (UNIT_DIR / ARMBIAN_LED).exists():
        return None
    props = unit_props(ARMBIAN_LED)
    if props.get("LoadState") == "masked":
        return "masked"
    return "failed" if props.get("ActiveState") == "failed" else None


def led_status():
    try:
        from irate_box.root import led
        return led.status()
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


def check_led(ls=None, active=None):
    """The status light: which LED, what it shows and how; a light the owner asked for that isn't driven."""
    ls = led_status() if ls is None else ls
    if not ls:
        return []
    if not ls.get("found"):
        return [_f("led", "Status light", "ok", "This board has no LED the box could use as a status light (power LEDs are "
                   "never taken).", glance="No LED on this board.")]
    name, show, run_ = ls.get("led"), ls.get("show"), ls.get("running") or {}
    flash = [_act(f"led-flash:{n}", f"Flash {n}") for n in list(ls["found"])[:4]]
    if show == "leave":
        return [_f("led", "Status light", "ok", f"Left as the image set it ({name or 'no LED picked'}). The box can blink it while "
                   "everything is well.", "Set on Recovery and monitoring.", flash, go=_go("health-settings", "Recovery and monitoring", "hs-led-show"),
                   glance=f"Left as the image set it ({name or 'no LED picked'}).")]
    unit_on = (active or unit_props)("irate-box-led.service").get("ActiveState") == "active"
    if not unit_on:
        return [_f("led", "Status light", "problem", f"{name} should show the state of the box, but its service is not running: the "
                   "light says nothing.", "journalctl -u irate-box-led -n 30",
                   [_act("unit-restart:irate-box-led.service", "Start it again")], glance=f"{name}: its service is not running.")]
    how = ("driven by the box: this kernel has no LED triggers, so nothing else can blink it" if not (ls["found"].get(name) or {}).get("triggers")
           else "driven by the box, so it stops when the box does")
    return [_f("led", "Status light", "ok", f"{name}: {LED_SHOW_LABEL.get(show, show).lower()}; now {run_.get('words', 'not reported yet')} "
               f"({how}).", "Set on Recovery and monitoring.", flash, go=_go("health-settings", "Recovery and monitoring", "hs-led-show"),
               glance=f"{name}: {LED_SHOW_LABEL.get(show, show).lower()}.")]


def check_watchers(now=None):
    """What the watchers saw and did in the last day, in one time line (watchlog): the uplink
    watchdog's outages, causes and steps, crash watch's radio failures, the driver's own resets, and
    why each boot happened. Said so the owner can follow one incident from start to end."""
    from irate_box.hub import watchlog
    now = now or time.time()
    day = watchlog.read(since=now - 86400, kinds=watchlog.NOTABLE)
    if not day:
        return [_f("watchers", "What the watchers did", "ok", "Nothing in the last day: no outage, no radio failure, no restart.")]
    boots = [e for e in day if e.get("kind") == "boot"]
    steps = [e for e in day if e.get("kind") == "repair"]
    lines = [watchlog.line(e).split(" ", 1)[1] for e in day[-12:]]
    head = (f"In the last day: {len(boots)} start{'s' if len(boots) != 1 else ''} of the box, "
            f"{len(steps)} repair{'s' if len(steps) != 1 else ''} by the watchdog. The latest, oldest first: ")
    return [_f("watchers", "What the watchers did", "ok", head + " | ".join(lines),
               f"All of it, every watcher in one place: sudo tail -n 100 {watchlog.LOG}")]


def scan():
    findings = []
    for check in (check_install, check_hub, check_clock, check_units, check_kiwix, check_web, check_uplink, check_inventory,
                  check_hotspot, check_space, check_builds, check_crashwatch, check_led, check_watchers, check_idle, check_usb_filesystems):
        try:
            findings += check()
        except Exception as exc:  # one broken check must not hide the others
            findings.append(_f(f"check:{check.__name__}", check.__name__.replace("check_", "Check: "), "warn",
                               f"The check itself failed: {exc!r}"))
    return {"at": time.time(), "findings": findings, "settings": doctor_settings(), "clock": clock_choice()}


def clock_choice():
    """The clock's sources as chosen on the Clock page (clock.py), for its card; None if it could not be read."""
    try:
        from irate_box.root import clock
        return clock.status()
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


def doctor_settings():
    """The doctor's own settings, as they are now, for its settings card: crash watch's snapshots, and what
    restarts a frozen box (a panic, the watchdog), with whether this board has a watchdog to use."""
    try:
        from irate_box.root import crashwatch as cw
        s = cw.load_settings()
        wd = cw.watchdog_device() if os.geteuid() == 0 or os.environ.get("HUB_SYSFS") else None
        ls = led_status()
        return {"snapshots": s["snapshots"], "panic": s["panic"], "watchdog": s["watchdog"], "watchdog_device": wd,
                "led": {"name": ls["led"], "show": ls["show"]} if ls else None,
                "groups": setting_groups(s, wd) + led_groups(ls, armbian_led_state() if ls else None)}
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


# --- repairs ---------------------------------------------------------------------------------------

CHOICE_RE = re.compile(r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]+$|^(kiwix-rebuild|kiwix-off)$"
                       r"|^other-restart:[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]{0,200}$"
                       r"|^crashwatch-(snapshots|panic|watchdog):(on|off)$"
                       r"|^clock-set:\d{10}$|^(rtc-find|rtc-save|rtc-remove)$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$"
                       r"|^clock-sources:(image|chrony):[01]:[01]:(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})$|^clock-undo$"
                       r"|^uplink-skip:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}:(reconnect|restart|radio|reboot)$"
                       r"|^idle-(off|on):(meshtasticd|ModemManager|bluetooth)\.service$"
                       r"|^led-show:(leave|alive|network|all)$|^led-(led|flash):[A-Za-z0-9:._@-]{1,64}$|^armbian-led:(on|off)$"
                       r"|^uplink-reconnect:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$")


def fix(choice):
    if not CHOICE_RE.match(choice):
        raise ValueError(f"{choice} is not something the doctor does")
    kind, _, arg = choice.partition(":")
    if kind == "clock-sources":
        from irate_box.root import clock
        return clock.apply(choice)
    if kind == "clock-undo":
        from irate_box.root import clock
        return clock.undo()
    if kind.startswith("crashwatch-"):
        from irate_box.root import crashwatch
        return crashwatch.set_option(kind[len("crashwatch-"):], arg)
    if kind in ("led-show", "led-led"):
        from irate_box.root import led
        return led.set_option(kind[len("led-"):], arg)
    if kind == "led-flash":
        from irate_box.root import led
        return led.flash(arg)
    if kind == "armbian-led":
        if not armbian_led_state() and arg == "off":
            raise ValueError(f"{ARMBIAN_LED} is not failing here. There is nothing to switch off.")
        r = run("systemctl", "mask" if arg == "off" else "unmask", ARMBIAN_LED)
        if r.returncode:
            raise ValueError(f"{ARMBIAN_LED}: {r.stderr.strip()[-200:]}")
        if arg == "off":
            run("systemctl", "reset-failed", ARMBIAN_LED)
        return f"{ARMBIAN_LED}: " + ("switched off (masked). Switch it on here to unmask it" if arg == "off" else "as the image has it again")
    if kind == "other-restart":
        failed = [l.split()[0] for l in run("systemctl", "--failed", "--no-legend", "--plain").stdout.splitlines() if l.split()]
        if arg not in failed or ours(arg) or not re.match(r"^[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]*\.(service|socket|timer|mount)$", arg):
            raise ValueError(f"{arg} is not a failed unit of the box's to start again")
        run("systemctl", "reset-failed", arg)
        r = run("systemctl", "start", arg)
        return f"{arg}: started" if r.returncode == 0 else f"{arg}: {r.stderr.strip()[-200:] or 'failed again'}"
    if kind in ("unit-restart", "unit-enable"):
        if not ours(arg):
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
        # As the hub: both folders are the hub's, so root has no business there. Done as
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
    if kind in ("idle-off", "idle-on"):
        return idle_switch(arg, kind == "idle-on")
    if kind == "uplink-reconnect":
        # A link nobody watches, parked by NetworkManager (a password check failed and nobody answered): connected
        # by hand, which also lifts NetworkManager's stop on it.
        if not (Path("/sys/class/net") / arg).exists():
            raise ValueError(f"{arg} is not a link on this box")
        r = run("nmcli", "-w", "45", "dev", "connect", arg, timeout=60)
        out = (r.stdout + r.stderr).strip()
        if r.returncode:
            raise ValueError(f"{arg} did not connect: {out[-200:] or 'nmcli failed'}")
        return f"{arg}: {out[-160:] or 'connected'}"
    if kind == "uplink-skip":
        from irate_box.hub import uplink
        iface, _, step = arg.partition(":")
        if step not in uplink.STEPS or not uplink.IFACE_RE.match(iface) or iface == "auto":
            raise ValueError(f"{arg} is not a connection and a step")
        chosen = uplink.load_settings()
        steps = set(chosen.get("steps_off", {}).get(iface, [])) | {step}
        chosen.setdefault("steps_off", {})[iface] = [x for x in uplink.STEPS if x in steps]
        uplink.save_settings(chosen)  # the watchdog takes it on its next look
        return f"{iface}: the watchdog leaves out {STEP_SAID.get(step, step)} there from now on"
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
        return "Kiwix stopped and disabled at boot. Rebuilding the library with a readable book starts it again."
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
