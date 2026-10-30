#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Box doctor (one of three: docs/dev/doctors.md): is everything install.sh set up still there and working, and if not, why.

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
the page). "rerun-install" and "rerun-install-port-80" are the root helper's own (they need the update
machinery). Stdlib only.

Writing a check (irate_box/finding.py says what each part of a finding is for):

  - Two questions, both asked. What should be so: what the box chose (install-options, a setting, a
    record). What is so: asked of the system itself (ss, systemctl, a request, a file). The finding
    compares them. Never take the second from the first: check_port_80 once said "fine" whenever
    the recorded port was 80, without asking whether anything listened there.
  - A deliberate choice is not a fault. If the owner turned a thing off, that is ok, said plainly.
  - Status: ok when what is matches what should be; warn when the box works with less because of
    the owner's choice or another program; problem when something the box needs is broken.
  - One check_* function per thing checked, returning a list of findings, added to scan() below.
    Its finding id is stable kebab-case ("port-80"); its message ids are "<id>.<case>", with
    ".why" and ".fix" after the case they belong to.
  - Words, in plain English, short sentences, as statements, never questions:
      title    the thing checked, a noun phrase ("Port 80", "Web server configuration")
      summary  what was found, with the names and numbers seen
      why      what a person cannot do because of it (none when ok)
      fix      the instruction that fixes it: the installer's option, or the page and setting (go=)
    commands are exactly what to type; evidence is what the check saw (a path, an ss line).
  - A button (actions) only for a repair that is safe to do from the page. It acts at once; only
    the irreversible asks first. Nothing changes unless asked.
  - Testable offline: read the system only through run(), install_options() and the HUB_* paths,
    so a test can stand in for them. Each case gets a check in a tests/sim_*.py, by message id
    (finding.message_ids), not by wording; the test is listed in tests/run.sh.
  - The check's docstring says what it asks and, when a real box showed the need, which and when.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from irate_box.choices import CHOICE_RE
from irate_box.finding import action, english, finding
from irate_box.hub import jsonlog
from irate_box.library import zimcheck
from irate_box.msg import M, Refused, duration, named, render, sentences, when
from irate_box.root import rtc

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
# The Git add-on's removal marker (gitrepos.REMOVED, in the folder install.sh gives the repositories).
GIT_REMOVED = STATE / "git" / ".git-add-on-removed"
# Units this page may restart or enable: irate-box's own and the add-ons'.
OUR_UNIT = re.compile(
    r"^(irate-box(-[a-z]+)*\.(service|socket|path|timer)|nginx\.service|caddy\.service|kiwix\.service|"
    r"silverbullet\.service|syncthing@" + re.escape(HUB_USER) + r"\.service|mosquitto\.service|"
    r"excalidraw-room\.service|ttyd\.service)$"
)
ADDON_UNITS = {
    "--with-notes": "silverbullet.service",
    "--with-sync": f"syncthing@{HUB_USER}.service",
    "--with-mqtt": "mosquitto.service",
    "--with-collab": "excalidraw-room.service",
    "--with-term": "ttyd.service",
}  # and each declared service's (services.py)


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
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _go(section, where, mark=None):
    """Where a finding's setting is changed, for the page's link: an element id that is always there, the words for
    the button (a message), and the very setting inside it (drawn later, perhaps), which the page highlights."""
    return {"section": section, "where": where, **({"mark": mark} if mark else {})}


# The places a finding sends the reader, by the words on its button.
RECOVERY = M("go.recovery", "Recovery and monitoring")
NET_RECOVERY = M("go.net-recovery", "Network → Network recovery")
GO_BOOKS = _go("books", M("go.books", "Books"))
GO_UPDATES = _go("updates", M("go.updates", "Updates"))
GO_HOTSPOT = _go("network/hotspot", M("go.hotspot", "Network → Hotspot"))
GO_NETWORK = _go("network", M("go.network", "Network"))
GO_FACTORY = _go("factory", M("go.factory", "Firmware Factory"))
GO_BUILDS = _go("git", M("go.git", "Git"))
GO_CLOCK = _go("clock", M("go.clock", "Clock"))

RERUN = action(
    "rerun-install",
    M("install.rerun", "Run the installer again"),
    M(
        "install.rerun.confirm",
        "Run install.sh again with this box's recorded options? "
        "The installer can repair, upgrade and restart services.",
    ),
)


# The installer again with the hub on port 80 in place of the port it was moved to (the root helper's, as RERUN).
def rerun_port_80(port):
    return action(
        "rerun-install-port-80",
        M("install.rerun.port-80", "Move the hub to port 80"),
        M(
            "install.rerun.port-80.confirm",
            "Run install.sh again with --port 80 in place of --port {port}? The hub then opens at the box's address "
            "with no port, and addresses with :{port} stop working. The installer can restart services.",
            port=port,
        ),
    )


RESTART = M("unit.restart", "Start it again")
RUN_NOW = M("unit.run-now", "Run it now")


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
    title = M("install.title", "Last install")
    st = install_state()
    if not st:
        return [
            finding(
                "install",
                title,
                "warn",
                M("install.none", "There is no record of an install. Installs before this version kept none."),
                notes=[M("install.none.next", "The next run of the installer makes one.")],
            )
        ]
    started = when(st.get("started", 0))
    if st.get("running") and _pid_alive(st.get("pid")):
        return [
            finding(
                "install",
                title,
                "warn",
                M("install.running", "The installer is running now. It started on {when}.", when=started),
                evidence=[st.get("step") or ""],
            )
        ]
    if st.get("running") or st.get("aborted"):
        step = st.get("step") or M("install.early-step", "an early step")
        if st.get("running"):
            why = M(
                "install.cut-off",
                "It left no exit record. A power cut, a lost SSH session or a stopped process can do this.",
            )
            evidence = [str(INSTALL_LOG)]
        else:
            why = M("install.failed-command", "A command failed, with exit code {code}.", code=st.get("exit"))
            evidence = (
                [st.get("failed_command") or ""]
                + ([f"install.sh:{st['failed_line']}"] if st.get("failed_line") else [])
                + [str(INSTALL_LOG)]
            )
        return [
            finding(
                "install",
                title,
                "problem",
                M(
                    "install.stopped",
                    "The installer started on {when} and stopped during this step: {step}. "
                    "It did not do the steps after it.",
                    when=started,
                    step=step,
                ),
                why,
                M(
                    "install.stopped.fix",
                    "Read the installer's log and correct what it names. The findings below can name it too. "
                    "Then run the installer again.",
                ),
                notes=[
                    M(
                        "install.rerun.ways",
                        "You can also run it again from Updates, with Install update, or with sudo ./install.sh "
                        "in the checkout.",
                    )
                ],
                commands=[f"sudo tail -n 50 {INSTALL_LOG}"],
                evidence=evidence,
                actions=[RERUN],
            )
        ]
    problems = st.get("problems") or []
    if problems:
        return [
            finding(
                "install",
                title,
                "warn",
                M(
                    "install.problems",
                    {
                        "one": "The installer finished on {when} with {n} problem.",
                        "other": "The installer finished on {when} with {n} problems.",
                    },
                    n=len(problems),
                    when=started,
                ),
                notes=[M("install.problems.now", "The findings below say which ones are still there.")],
                evidence=[p[:200] for p in problems[:12]],
            )
        ]
    return [
        finding(
            "install", title, "ok", M("install.ok", "The installer finished on {when} with no problems.", when=started)
        )
    ]


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
    return [ln[:200] for ln in out if ln.strip()][-n:]


def expected_units():
    """(unit, role) for everything install.sh would have set running; the role a message."""
    opts = install_options()
    web = option(opts, "--web", "nginx") or "nginx"
    units = [
        ("irate-box.service", M("unit.role.hub", "hub server")),
        (f"{web}.service", M("unit.role.web", "web server")),
        ("irate-box-control.path", M("unit.role.helper", "root helper")),
        ("irate-box-librarian.timer", M("unit.role.librarian", "the librarian's schedule")),
        ("irate-box-ci.path", M("unit.role.ci", "builds on push")),
    ]
    if not GIT_REMOVED.exists():
        units.insert(2, ("irate-box-git.socket", M("unit.role.git", "git servers")))
    if (UNIT_DIR / "irate-box-uplink.service").exists() or "irate-box-uplink" in _installed_text():
        units.append(("irate-box-uplink.service", M("unit.role.uplink", "uplink watchdog")))
    if (UNIT_DIR / "irate-box-crashwatch.service").exists() or "irate-box-crashwatch" in _installed_text():
        units.append(("irate-box-crashwatch.service", M("unit.role.crashwatch", "crash watch")))
    if (UNIT_DIR / "irate-box-led.service").exists() or "irate-box-led" in _installed_text():
        units.append(("irate-box-led.service", M("unit.role.led", "status light")))
    from irate_box.hub import services

    declared = {s["option"]: s["unit"] for s in services.declared() if s["option"] and s["unit"]}
    for opt, unit in {**declared, **ADDON_UNITS}.items():
        if opt in opts:
            units.append((unit, M("unit.role.addon", "the {name} add-on", name=opt.removeprefix("--with-"))))
    if (UNIT_DIR / "kiwix.service").exists() or _books():
        units.append(("kiwix.service", M("unit.role.kiwix", "Kiwix, /wiki/")))
    return units


def _installed_text():
    try:
        return (CODE / "install.sh").read_text()
    except OSError:
        return ""


# Why a unit failed, from systemd's Result: each a sentence of its own.
FAILED_BECAUSE = {
    "start-limit-hit": M(
        "unit.failed.start-limit", "It failed several times in a row, and systemd stopped trying to start it."
    ),
    "exit-code": M("unit.failed.exit-code", "It stopped with an error."),
    "signal": M("unit.failed.signal", "A signal stopped it."),
    "timeout": M("unit.failed.timeout", "It did not start in time."),
    "core-dump": M("unit.failed.core-dump", "It crashed."),
    "watchdog": M("unit.failed.watchdog", "It stopped answering systemd's watchdog."),
    "unit-start-limit-hit": M(
        "unit.failed.trigger-limit", "The unit that it starts hit its start limit, so this unit stopped too."
    ),
}


def check_units():
    out = []
    for unit, role in expected_units():
        p = unit_props(unit)
        load, active, sub, result = p.get("LoadState"), p.get("ActiveState"), p.get("SubState"), p.get("Result")
        enabled = p.get("UnitFileState")
        title = named(unit, role)
        restart = action(f"unit-restart:{unit}", RESTART)
        if load == "not-found":
            out.append(
                finding(
                    f"unit:{unit}",
                    title,
                    "problem",
                    M("unit.missing", "The unit is not on the box any more."),
                    fix=M("unit.missing.fix", "Run the installer again. It writes the unit."),
                    actions=[RERUN],
                )
            )
            continue
        if unit == "kiwix.service":
            continue  # check_kiwix looks at Kiwix in layers, the unit included
        if active == "failed" or result not in ("success", None, ""):
            tail = journal_tail(unit if not unit.endswith(".path") else unit.replace(".path", ".service"))
            verb = "start" if unit.endswith((".path", ".timer", ".socket")) else "restart"
            out.append(
                finding(
                    f"unit:{unit}",
                    title,
                    "problem",
                    FAILED_BECAUSE.get(result) or M("unit.failed.other", "It failed: {result}.", result=result),
                    fix=M(
                        "unit.failed.fix",
                        "Read its log to find the cause. Then clear the failure and start it again. "
                        "If it fails again at once, the log says what it needs.",
                    ),
                    commands=[
                        f"journalctl -u {unit} -n 50",
                        f"systemctl reset-failed {unit}",
                        f"systemctl {verb} {unit}",
                    ],
                    evidence=tail,
                    actions=[restart],
                )
            )
        elif active != "active":
            out.append(
                finding(
                    f"unit:{unit}",
                    title,
                    "warn",
                    M("unit.stopped", "It is not running."),
                    fix=M("unit.stopped.fix", "Start it."),
                    commands=[f"systemctl start {unit}"],
                    evidence=[f"{active}/{sub}" if sub else str(active)],
                    actions=[restart],
                )
            )
        elif (
            enabled not in ("enabled", "static", "indirect", "generated", "alias")
            and unit != "irate-box-uplink.service"
        ):
            out.append(
                finding(
                    f"unit:{unit}",
                    title,
                    "warn",
                    M("unit.not-enabled", "It is running, but it does not start at boot."),
                    fix=M("unit.not-enabled.fix", "Enable it at boot."),
                    commands=[f"systemctl enable {unit}"],
                    evidence=[str(enabled)],
                    actions=[action(f"unit-enable:{unit}", M("unit.enable", "Enable at boot"))],
                )
            )
        else:
            restarts = int(p.get("NRestarts") or 0) if str(p.get("NRestarts") or "0").isdigit() else 0
            out.append(
                finding(
                    f"unit:{unit}",
                    title,
                    "ok",
                    M("unit.running", "Running.")
                    if not restarts
                    else M(
                        "unit.running-restarted",
                        {
                            "one": "Running. systemd restarted it {n} time.",
                            "other": "Running. systemd restarted it {n} times.",
                        },
                        n=restarts,
                    ),
                )
            )
    # A timer's service: the timer stays "running" while every run of it fails, and a one-shot
    # service's failure shows only in its result.
    for unit, _ in expected_units():
        if not unit.endswith(".timer"):
            continue
        svc = unit[: -len(".timer")] + ".service"
        p = unit_props(svc)
        result = p.get("Result")
        if p.get("LoadState") == "not-found" or result in ("success", None, ""):
            continue
        out.append(
            finding(
                f"unit:{svc}",
                named(svc, M("unit.role.timer-run", "run by {timer}", timer=unit)),
                "problem",
                M("unit.timer-failed", "Its last run failed."),
                fix=M(
                    "unit.timer-failed.fix",
                    "Read its log to find the cause. The timer runs it again within the hour. "
                    "When it works again, clear its failure mark.",
                ),
                commands=[f"journalctl -u {svc} -n 80", f"systemctl reset-failed {svc}"],
                evidence=[str(result)] + journal_tail(svc, 8),
                actions=[action(f"unit-restart:{svc}", RUN_NOW)],
            )
        )
    # The root helper: a request nobody has answered means /admin buttons go nowhere.
    reqs = (
        sorted((CONTROL / "requests").glob("*.json"), key=lambda q: q.stat().st_mtime)
        if (CONTROL / "requests").is_dir()
        else []
    )
    me = os.environ.get("HUB_CONTROL_RUNNING") == "1"
    if reqs and not me:
        age = time.time() - reqs[0].stat().st_mtime
        if age > 90:
            out.append(
                finding(
                    "helper-queue",
                    M("helper-queue.title", "Root helper queue"),
                    "problem",
                    M(
                        "helper-queue.waiting",
                        {
                            "one": "{n} request from the administration pages is waiting. It has waited {waited}.",
                            "other": "{n} requests from the administration pages are waiting. "
                            "The oldest has waited {waited}.",
                        },
                        n=len(reqs),
                        waited=duration(age // 60 * 60),
                    ),
                    M(
                        "helper-queue.why",
                        "Until the root helper answers, the buttons on the administration pages do nothing.",
                    ),
                    M("helper-queue.fix", "Restart the root helper."),
                    commands=[
                        "systemctl reset-failed irate-box-control.service irate-box-control.path",
                        "systemctl start irate-box-control.path",
                    ],
                    actions=[
                        action(
                            "unit-restart:irate-box-control.path", M("helper-queue.restart", "Restart the root helper")
                        )
                    ],
                )
            )
    unit_text = (
        (UNIT_DIR / "irate-box-control.service").read_text()
        if (UNIT_DIR / "irate-box-control.service").exists()
        else ""
    )
    if unit_text and "StartLimitIntervalSec=0" not in unit_text:
        out.append(
            finding(
                "helper-limit",
                M("helper-limit.title", "Root helper start limit"),
                "warn",
                M("helper-limit.old", "An older version installed the root helper's unit."),
                M(
                    "helper-limit.why",
                    "Many requests at once from the administration pages can trip systemd's start limit. "
                    "The root helper then stays stopped until a reboot.",
                ),
                M(
                    "helper-limit.fix",
                    "Run the installer again, or update the box. Either one writes the corrected unit.",
                ),
                actions=[RERUN],
            )
        )
    # Anything else that failed on the box: not ours to restart, but worth knowing when troubleshooting.
    failed = [
        ln.split()[0] for ln in run("systemctl", "--failed", "--no-legend", "--plain").stdout.splitlines() if ln.split()
    ]
    # Each failed unit that isn't irate-box's (the image's, or the owner's own), with its options, from the least
    # done: see why; accept it; start it again, asked first, as it isn't ours.
    # A package the box watches (Updates → Packages from their makers): known, so said as itself.
    watched = watched_units()
    idle = {i["unit"] for i in idle_units()}  # said by check_idle, with switching it off as the choice
    failed = [u for u in failed if u not in idle]
    for u in [u for u in failed if u in watched]:
        out.append(
            finding(
                f"watched-failed:{u}",
                M("watched-failed.title", "{package}: its service failed", package=watched[u]),
                "warn",
                M("watched-failed.stopped", "{unit} stopped with an error.", unit=u),
                M(
                    "watched-failed.why",
                    "The box watches this package's updates. Whether it runs is the package's own business. "
                    "A build can fail on a board that lacks the hardware it expects.",
                ),
                M(
                    "watched-failed.fix",
                    "Read its log. Then start it again, or try another build on Updates, Packages from their "
                    "makers, with Roll back or another channel. If the board does not use it, accept it.",
                ),
                commands=[f"journalctl -u {u} -n 50"],
                actions=[
                    action(
                        f"other-restart:{u}",
                        RESTART,
                        M(
                            "watched-failed.restart.confirm",
                            "Start {unit} again? The doctor clears its failed mark and starts it.",
                            unit=u,
                        ),
                    )
                ],
                go=GO_UPDATES,
            )
        )
    # The image's LED service: it fails on boards whose light has no trigger file, and the box has a switch for it
    # (Recovery and monitoring, The image's LED service). Refused as that, with that switch, not as a stranger to debug.
    if ARMBIAN_LED in failed and not ours(ARMBIAN_LED):
        failed = [u for u in failed if u != ARMBIAN_LED]
        out.append(
            finding(
                f"other-failed:{ARMBIAN_LED}",
                M("armbian-led.title", "The image's LED restore service failed"),
                "note",
                M(
                    "armbian-led.failed",
                    "{unit} fails at every start on this board. The box's status light is separate, and works.",
                    unit=ARMBIAN_LED,
                ),
                ARMBIAN_LED_WHY,
                M(
                    "armbian-led.fix",
                    "Switch it off, so that it stops failing. The same switch is on Recovery and monitoring.",
                ),
                actions=[
                    action(
                        "armbian-led:off",
                        M("armbian-led.off", "Switch it off"),
                        M(
                            "armbian-led.off.confirm",
                            "Switch {unit} off (mask it)? You can switch it on again on Recovery and monitoring.",
                            unit=ARMBIAN_LED,
                        ),
                    )
                ],
                go=_go("health-settings", RECOVERY, "hs-armbian-led"),
            )
        )
    for u in [u for u in failed if not ours(u) and u not in watched][:12]:
        out.append(
            finding(
                f"other-failed:{u}",
                M("other-failed.title", "{unit} failed", unit=u),
                "warn",
                M(
                    "other-failed.stopped",
                    "A service of the image, or one of your own, stopped with an error. The hub does not use it.",
                ),
                M("other-failed.why", "On these images, that can be harmless, or it can be something that you need."),
                M(
                    "other-failed.fix",
                    "Read its log. Then start it again, or select Accept to leave it as it is. "
                    "Accept does not repair it.",
                ),
                commands=[f"journalctl -u {u} -n 50"],
                actions=[
                    action(
                        f"other-restart:{u}",
                        RESTART,
                        M(
                            "other-failed.restart.confirm",
                            "Start {unit} again? It is not part of irate-box. "
                            "The doctor only clears its failed mark and starts it.",
                            unit=u,
                        ),
                    )
                ],
            )
        )
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
    """[{unit, title, summary, why, evidence}]: services here that, as far as the box can tell, do nothing for it."""
    out = []
    p = unit_props("meshtasticd.service")
    if p.get("LoadState") == "loaded" and p.get("UnitFileState") == "enabled":
        log = run("journalctl", "-u", "meshtasticd.service", "-b", "--no-pager", "-o", "cat", "-n", "300").stdout
        if NO_LORA.search(log) and p.get("ActiveState") != "active":
            out.append(
                {
                    "unit": "meshtasticd.service",
                    "title": M("idle.meshtasticd.title", "meshtasticd: no supported radio detected"),
                    "summary": M(
                        "idle.meshtasticd.gave-up",
                        "It finds no LoRa radio on this box and stops at every boot, until systemd stops trying.",
                    )
                    if p.get("Result") == "start-limit-hit"
                    else M("idle.meshtasticd", "It finds no LoRa radio on this box and stops at every boot."),
                    "why": None,
                    "evidence": [NO_LORA.search(log).group(0)],
                }
            )
    p = unit_props("ModemManager.service")
    if p.get("ActiveState") == "active":
        r = run("mmcli", "-L")
        if r.returncode == 0 and "No modems were found" in r.stdout:
            out.append(
                {
                    "unit": "ModemManager.service",
                    "title": M("idle.modemmanager.title", "ModemManager: no modem detected"),
                    "summary": M(
                        "idle.modemmanager", "It looks for mobile-data modems, and it found none on this box."
                    ),
                    "why": M(
                        "idle.modemmanager.why",
                        "It also probes serial ports, which can disturb a radio or another device on one.",
                    ),
                    "evidence": ["mmcli -L: No modems were found"],
                }
            )
    p = unit_props("bluetooth.service")
    if p.get("ActiveState") == "active":
        r = run("bluetoothctl", "devices", "Paired", timeout=10)
        if r.returncode == 0 and not r.stdout.strip():
            out.append(
                {
                    "unit": "bluetooth.service",
                    "title": M("idle.bluetooth.title", "Bluetooth: no paired devices"),
                    "summary": M(
                        "idle.bluetooth", "Nothing is paired with the box, and nothing on the hub uses Bluetooth."
                    ),
                    "why": M(
                        "idle.bluetooth.why",
                        "On a radio that does both WiFi and Bluetooth, as the AIC8800 does, "
                        "Bluetooth shares the chip with the WiFi.",
                    ),
                    "evidence": [],
                }
            )
    return out


def check_idle():
    off = _idle_off()
    out = []
    for i in idle_units():
        if i["unit"] in off:
            continue
        out.append(
            finding(
                f"idle:{i['unit']}",
                i["title"],
                "note",
                i["summary"],
                i["why"],
                M("idle.fix", "If this box does not need it, switch it off. You can switch it on again here."),
                evidence=i["evidence"],
                actions=[
                    action(
                        f"idle-off:{i['unit']}",
                        M("idle.off", "Disable it"),
                        M(
                            "idle.off.confirm",
                            "Disable {unit}? It stops now and does not start at boot. "
                            "The doctor then lists it with a button to restore it.",
                            unit=i["unit"],
                        ),
                    )
                ],
            )
        )
    for unit, rec in sorted(off.items()):
        out.append(
            finding(
                f"idle-off:{unit}",
                M("idle-off.title", "{unit}: disabled here", unit=unit),
                "ok",
                M("idle-off.when", "Disabled here on {at}.", at=when(rec.get("at", 0))),
                actions=[
                    action(
                        f"idle-on:{unit}",
                        M("idle.on", "Restore it"),
                        M("idle.on.confirm", "Restore {unit} to its previous state?", unit=unit),
                    )
                ],
            )
        )
    return out


def idle_switch(unit, on):
    """Switch an idle service off (kept in IDLE_OFF, as it was), or back on as it was."""
    if unit not in IDLE_UNITS:
        raise Refused(M("repair.idle.not-idle", "{unit} is not a service that the Box doctor switches off.", unit=unit))
    off = _idle_off()
    if on:
        rec = off.pop(unit, None)
        if rec is None:
            raise Refused(
                M("repair.idle.not-off", "The box did not disable {unit}. There is nothing to restore.", unit=unit)
            )
        if rec.get("enabled"):
            run("systemctl", "enable", unit)
        if rec.get("active"):
            run("systemctl", "start", unit, timeout=120)
        msg = M("repair.idle.on", "The box restored {unit} to its previous state.", unit=unit)
    else:
        p = unit_props(unit)
        off[unit] = {
            "at": time.time(),
            "enabled": p.get("UnitFileState") == "enabled",
            "active": p.get("ActiveState") == "active",
        }
        r = run("systemctl", "disable", "--now", unit, timeout=120)
        if r.returncode:
            raise Refused(
                M(
                    "repair.idle.off-failed",
                    "The box could not stop and disable {unit}: {detail}",
                    unit=unit,
                    detail=r.stderr.strip()[-200:],
                )
                if r.stderr.strip()
                else M("repair.idle.off-failed.quiet", "The box could not stop and disable {unit}.", unit=unit)
            )
        run("systemctl", "reset-failed", unit)
        msg = M("repair.idle.off", "The box stopped and disabled {unit}. It does not start at boot.", unit=unit)
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


def check_kiwix(quick=False):
    """The books: the header always, Kiwix's own read of each only in the full run (kiwix-manage opens every book:
    a few seconds for a handful of books, more with a larger library). The quick run is the usual one, the full one
    weekly (run_report)."""
    books = _books()
    unit = UNIT_DIR / "kiwix.service"
    if not books and not unit.exists():
        return []
    out = []
    if not shutil.which("kiwix-serve") or not shutil.which("kiwix-manage"):
        out.append(
            finding(
                "kiwix-package",
                M("kiwix-package.title", "Kiwix package"),
                "problem",
                M("kiwix-package.missing", "kiwix-tools is not installed, so /wiki/ cannot run."),
                fix=M(
                    "kiwix-package.fix",
                    "Install kiwix-tools, or run the installer again. The installer installs it when there are books.",
                ),
                commands=["apt-get install kiwix-tools"],
                actions=[RERUN],
            )
        )
    good, bad = [], []
    for b in books:
        why = zim_header_problem(b) or (None if quick else kiwix_reads(b))
        (bad if why else good).append((b, why))
    for b, why in bad:
        out.append(
            finding(
                f"zim:{b.name}",
                M("zim.title", "Book: {name}", name=b.name),
                "problem",
                M("zim.unreadable", "Kiwix cannot read this book ({mb} MB).", mb=b.stat().st_size >> 20),
                fix=M(
                    "zim.unreadable.fix",
                    "Download or copy it again. Books keeps its sources up to date. "
                    "Or quarantine it: the box moves it to {folder} and rebuilds the library without it.",
                    folder=str(QUARANTINE),
                ),
                evidence=[why, str(b)],
                actions=[
                    action(
                        f"kiwix-quarantine:{b.name}",
                        M("zim.quarantine", "Quarantine the book"),
                        M(
                            "zim.quarantine.confirm",
                            "Move {name} to {folder} and rebuild the library without it?",
                            name=b.name,
                            folder=str(QUARANTINE),
                        ),
                    )
                ],
                go=GO_BOOKS,
            )
        )
    if good:
        names = [b.name for b, _ in good]
        out.append(
            finding(
                "zim-ok",
                M("zim-ok.title", "Books"),
                "ok",
                M("zim-ok.count", {"one": "{n} book can be read.", "other": "{n} books can be read."}, n=len(good)),
                notes=[M("zim-ok.quick", "Kiwix's own read of each book is checked in the full run, once a week.")]
                if quick
                else [],
                evidence=names[:6] + (["…"] if len(names) > 6 else []),
            )
        )
    listed = library_paths()
    rebuild = action("kiwix-rebuild", M("library.rebuild", "Rebuild the library"))
    lib_title = M("library.title", "Kiwix library")
    if listed is None:
        if good:
            out.append(
                finding(
                    "library",
                    lib_title,
                    "problem",
                    M(
                        "library.missing",
                        {
                            "one": "There is no {file}, though {n} readable book is there.",
                            "other": "There is no {file}, though {n} readable books are there.",
                        },
                        n=len(good),
                        file=LIBRARY.name,
                    ),
                    fix=M("library.rebuild.fix", "Rebuild the library from the books on disk."),
                    actions=[rebuild],
                )
            )
    else:
        on_disk = {b.resolve() for b, _ in good}
        gone = [p for p in listed if not p.exists()]
        missing = [b for b in on_disk if b not in {p.resolve() for p in listed if p.exists()}]
        if gone or missing:
            said = (
                [
                    M(
                        "library.gone",
                        {
                            "one": "It lists {n} book that is no longer on disk.",
                            "other": "It lists {n} books that are no longer on disk.",
                        },
                        n=len(gone),
                    )
                ]
                if gone
                else []
            )
            said += (
                [
                    M(
                        "library.left-out",
                        {"one": "It leaves out {n} readable book.", "other": "It leaves out {n} readable books."},
                        n=len(missing),
                    )
                ]
                if missing
                else []
            )
            out.append(
                finding(
                    "library",
                    lib_title,
                    "problem",
                    said[0],
                    fix=M("library.rebuild.fix", "Rebuild the library from the books on disk."),
                    notes=said[1:],
                    evidence=[p.name for p in gone[:4]] + [p.name for p in missing[:4]],
                    actions=[rebuild],
                )
            )
        else:
            out.append(
                finding(
                    "library",
                    lib_title,
                    "ok",
                    M(
                        "library.ok",
                        {"one": "It lists the {n} book on disk.", "other": "It lists the {n} books on disk."},
                        n=len(listed),
                    ),
                )
            )
    kiwix = M("kiwix.title", "Kiwix (/wiki/)")
    add_book = M(
        "kiwix.empty.fix",
        "Add a book from Books, from a USB drive or with install.sh --zim. "
        "Then rebuild the library, which starts Kiwix again.",
    )
    if unit.exists():
        p = unit_props("kiwix.service")
        if not good:
            if p.get("UnitFileState") != "enabled" and p.get("ActiveState") in ("inactive", "failed"):
                out.append(
                    finding(
                        "kiwix-empty",
                        kiwix,
                        "warn",
                        M(
                            "kiwix.empty-stopped",
                            "Kiwix is stopped until there is a readable book. The installer or this page "
                            "stopped it, so that it does not restart in a loop.",
                        ),
                        fix=add_book,
                        actions=[rebuild],
                        go=GO_BOOKS,
                    )
                )
            else:
                out.append(
                    finding(
                        "kiwix-empty",
                        kiwix,
                        "problem",
                        M(
                            "kiwix.empty-looping",
                            "There is no readable book, so kiwix-serve cannot start. "
                            "systemd tries again and again, and then stops.",
                        ),
                        fix=add_book,
                        notes=[M("kiwix.empty.stop", "Until then, stop Kiwix, so that it does not restart in a loop.")],
                        commands=["systemctl disable --now kiwix"],
                        evidence=[p.get("Result") or p.get("ActiveState") or ""],
                        actions=[action("kiwix-off", M("kiwix.off", "Stop Kiwix until there are books"))],
                        go=GO_BOOKS,
                    )
                )
        elif p.get("ActiveState") != "active" and p.get("Result") in ("success", "", None):
            out.append(
                finding(
                    "kiwix-down",
                    kiwix,
                    "warn",
                    M(
                        "kiwix.stopped",
                        {
                            "one": "Kiwix is stopped, not failed, with {n} readable book.",
                            "other": "Kiwix is stopped, not failed, with {n} readable books.",
                        },
                        n=len(good),
                    ),
                    M("kiwix.stopped.why", "Someone stopped it, from this page or by hand."),
                    M("kiwix.stopped.fix", "To bring /wiki/ back, rebuild the library. That starts Kiwix."),
                    notes=[M("kiwix.not-at-boot", "It does not start at boot.")]
                    if p.get("UnitFileState") != "enabled"
                    else [],
                    commands=["systemctl enable --now kiwix"],
                    actions=[rebuild],
                )
            )
        elif p.get("ActiveState") != "active":
            out.append(
                finding(
                    "kiwix-down",
                    kiwix,
                    "problem",
                    M(
                        "kiwix.down",
                        {
                            "one": "Kiwix is not running, with {n} readable book.",
                            "other": "Kiwix is not running, with {n} readable books.",
                        },
                        n=len(good),
                    ),
                    fix=M("kiwix.down.fix", "Rebuild the library, then start Kiwix again."),
                    commands=["systemctl reset-failed kiwix", "systemctl restart kiwix"],
                    evidence=[p.get("Result") or p.get("ActiveState") or ""] + journal_tail("kiwix.service", 4),
                    actions=[rebuild, action("unit-restart:kiwix.service", RESTART)],
                )
            )
    elif good:
        out.append(
            finding(
                "kiwix-unit",
                kiwix,
                "problem",
                M("kiwix.no-unit", "Books are there, but Kiwix is not set up: there is no kiwix.service."),
                fix=M("kiwix.no-unit.fix", "Run the installer again. It sets up Kiwix whenever there are books."),
                actions=[RERUN],
            )
        )
    return out


def kiwix_rebuild():
    """library.xml from the readable books only. The old one stays if none can be read."""
    books = readable_books()
    if not books:
        return M("repair.kiwix.no-book", "There is no readable book, so the library did not change.")
    new = LIBRARY.with_name("library.xml.new")
    new.unlink(missing_ok=True)

    def add(batch):
        # As librarian.rebuild_library: many books a run, a failing run halved until the
        # unreadable book is alone (one bad book makes kiwix-manage write nothing).
        if (
            run(
                "runuser", "-u", HUB_USER, "--", "kiwix-manage", str(new), "add", *map(str, batch), timeout=900
            ).returncode
            == 0
        ):
            return []
        if len(batch) == 1:
            return [batch[0].name]
        return add(batch[: len(batch) // 2]) + add(batch[len(batch) // 2 :])

    skipped = []
    for i in range(0, len(books), 100):
        skipped += add(books[i : i + 100])
    if not new.exists():
        return M("repair.kiwix.not-made", "kiwix-manage made no library. The old library stays in use.")
    # Already the hub's (kiwix-manage made it as the hub): no chown, which would follow a link
    # swapped in for it. The rename replaces a link, never follows one.
    os.replace(new, LIBRARY)
    said = [
        M(
            "repair.kiwix.rebuilt",
            {
                "one": "The box rebuilt the library with {n} book.",
                "other": "The box rebuilt the library with {n} books.",
            },
            n=len(books) - len(skipped),
        ),
        M(
            "repair.kiwix.skipped",
            {
                "one": "It left out {n} book that kiwix-manage cannot read: {books}.",
                "other": "It left out {n} books that kiwix-manage cannot read: {books}.",
            },
            n=len(skipped),
            books=skipped,
        )
        if skipped
        else None,
    ]
    if (UNIT_DIR / "kiwix.service").exists():
        run("systemctl", "reset-failed", "kiwix.service")
        run("systemctl", "enable", "--now", "kiwix.service")
        run("systemctl", "restart", "kiwix.service")
        said.append(M("repair.kiwix.started", "Kiwix is started."))
    return sentences(*said)


# --- the rest -------------------------------------------------------------------------------------

UPLINK_STATE = {
    "up": M("uplink.state.up", "Up"),
    "checking": M("uplink.state.checking", "Checking"),
    "down": M("uplink.state.down", "Down"),
    "stalled": M("uplink.state.stalled", "Down, with nothing left to try"),
}
NM_WAITS = M("uplink.nm-waits", "After a password check fails, NetworkManager stops trying and waits for someone.")
NOTHING_HELPS = M(
    "uplink.nothing-helps", "Nothing that the box can do by itself would help. Reach it by its console or a cable."
)


def check_uplink():
    if not (UNIT_DIR / "irate-box-uplink.service").exists():
        return []
    title = M("uplink-report.title", "Uplink watchdog report")
    restart = action("unit-restart:irate-box-uplink.service", RESTART)
    try:
        st = json.loads(uplink_report().read_text())
    except (OSError, ValueError):
        none = M("uplink-report.none", "The watchdog has not written a report yet.")
        return [
            finding(
                "uplink-report",
                title,
                "warn",
                none,
                commands=["journalctl -u irate-box-uplink -n 30"],
                actions=[restart],
                glance=M("uplink-report.none.glance", "No report yet."),
            )
        ]
    age = time.time() - st.get("at", 0)
    limit = 3 * max(st.get("settings", {}).get("check", 60), 60)
    if age > limit:
        old = duration(age // 60 * 60)
        return [
            finding(
                "uplink-report",
                title,
                "problem",
                M("uplink-report.old", "Its last report is {age} old.", age=old),
                M("uplink-report.old.why", "The watchdog has probably stopped checking the link."),
                M("uplink-report.old.fix", "Read its log, then restart it."),
                commands=["journalctl -u irate-box-uplink -n 30", "systemctl restart irate-box-uplink"],
                actions=[restart],
                glance=M("uplink-report.old.glance", "Its last report is {age} old.", age=old),
            )
        ]
    from irate_box.hub import uplink

    try:
        said = uplink.words(uplink.validate(st.get("chosen") or {}))
    except (ValueError, TypeError, KeyError):
        said = None
    state = UPLINK_STATE.get(st.get("state")) or named(str(st.get("state")))
    now = M("uplink-report.ok", "{state} on {iface}.", state=state, iface=st.get("iface"))
    out = [
        finding(
            "uplink-report",
            title,
            "ok",
            now,
            notes=[
                M("uplink-report.settings", "Its settings: {settings}.", settings=said)
                if said
                else M("uplink-report.settings-unreadable", "Its settings cannot be read.")
            ],
            glance=now,
        )
    ]
    # Stalled: down, with nothing left the level allows that could help.
    stall = st.get("stall") if st.get("state") == "stalled" else None
    if stall:
        down = (st.get("at", 0) - ((st.get("outage") or {}).get("since") or st.get("at", 0))) // 60 * 60
        out.append(
            finding(
                "uplink-stalled",
                M("uplink-stalled.title", "The link is down and the watchdog has stalled"),
                "problem",
                M(
                    "uplink-stalled.down",
                    "{iface} has been down for {down}.",
                    iface=st.get("iface"),
                    down=duration(down),
                ),
                fix=M(
                    "uplink-stalled.fix",
                    "The step that could help is to {step}. On Network, Network recovery, run it now, "
                    "or select a maximum action that goes that far.",
                    step=stall["label"],
                )
                if stall.get("needs")
                else NOTHING_HELPS,
                notes=[stall["text"]] if stall.get("text") else [],
                go=_go("net-watchdog-card", NET_RECOVERY, "up-stall" if stall.get("needs") else "up-reach"),
            )
        )
    out += link_findings(st)
    out += roaming_findings(st, _json_or(CONTROL / "netinv.json"))
    out += metered_findings()
    out += step_findings(jsonlog.read(CONTROL / "uplink-ladder.json"), st.get("chosen") or {})
    return out


def link_findings(st):
    """The links nobody watches: none at all; one that carries the box, or would (a connection set to start by itself on
    it), and isn't watched, louder when it is down too, with a reconnect; one the owner set not to be watched is theirs.
    And each watched link but the first that has stalled, and one of a kind the box knows no repair for."""
    inv = st.get("inventory")
    if not isinstance(inv, dict):
        return []  # a report from before the watchdog watched links one by one
    links, chosen = st.get("links") or {}, st.get("chosen") or {}
    off = {i for i, v in (chosen.get("watch") or {}).items() if v is False}
    watched = sorted(i for i, lk in links.items() if lk.get("watched"))
    go = lambda mark: _go("net-watchdog-card", NET_RECOVERY, mark)  # noqa: E731
    reconnect = lambda i: action(f"uplink-reconnect:{i}", M("uplink.reconnect", "Reconnect {iface}", iface=i))  # noqa: E731
    out = []
    if not watched and inv:
        out.append(
            finding(
                "uplink-none-watched",
                M("uplink-none.title", "No link is watched"),
                "problem",
                M("uplink-none.watched", "The watchdog watches no link."),
                M(
                    "uplink-none.why",
                    "If the box's network fails, the watchdog does not notice it or try again. "
                    "NetworkManager can still try again by itself.",
                ),
                M("uplink-none.fix", "On Network, Network recovery, select which links the watchdog watches."),
                evidence=[st["watching"]] if st.get("watching") else [],
                go=go("up-watch"),
            )
        )
    for i, lk in sorted(inv.items()):
        evidence = [i, str(lk.get("kind"))] + list(lk.get("autoconnect") or [])
        if lk.get("look") and not lk.get("up") and lk.get("autoconnect"):
            out.append(
                finding(
                    f"uplink-look-{i}",
                    M("uplink-look.title", "{iface} is down, and only looked at", iface=i),
                    "warn",
                    M(
                        "uplink-look.down",
                        "{iface} is down. The watchdog looks at it but does nothing for it, as set.",
                        iface=i,
                    ),
                    NM_WAITS,
                    M(
                        "uplink-look.fix",
                        "Reconnect it now. Or, on Network, Network recovery, watch {iface} in full.",
                        iface=i,
                    ),
                    evidence=evidence,
                    actions=[reconnect(i)],
                    go=go(f"up-watch-{i}"),
                )
            )
            continue
        if lk.get("watched") or (lk.get("as") or i) in off or not lk.get("autoconnect"):
            continue
        if lk.get("up"):
            out.append(
                finding(
                    f"uplink-uncovered-{i}",
                    M("uplink-uncovered.title", "{iface} is not watched", iface=i),
                    "warn",
                    M("uplink-uncovered.up", "{iface} is up, but the watchdog does not watch it.", iface=i),
                    M("uplink-uncovered.why", "If it fails, the watchdog does not notice it or try again."),
                    M(
                        "uplink-uncovered.up.fix",
                        "On Network, Network recovery, watch {iface}. Or set it not to be watched, "
                        "to say that this is intended.",
                        iface=i,
                    ),
                    evidence=evidence,
                    go=go(f"up-watch-{i}"),
                )
            )
        else:
            out.append(
                finding(
                    f"uplink-uncovered-{i}",
                    M("uplink-uncovered-down.title", "{iface} is down, and not watched", iface=i),
                    "problem",
                    M(
                        "uplink-uncovered.down",
                        "{iface} is down. NetworkManager has a connection for it that starts by itself, "
                        "but the watchdog does not watch {iface}, so it does not try again.",
                        iface=i,
                    ),
                    NM_WAITS,
                    M(
                        "uplink-uncovered.down.fix",
                        "Reconnect it now. Then, on Network, Network recovery, watch {iface}.",
                        iface=i,
                    ),
                    evidence=evidence,
                    actions=[reconnect(i)],
                    go=go(f"up-watch-{i}"),
                )
            )
    for i in watched:
        lk = links[i]
        if lk.get("kind") == "other":
            out.append(
                finding(
                    f"uplink-kind-{i}",
                    M("uplink-kind.title", "{iface}: watched, no repair known", iface=i),
                    "ok",
                    M(
                        "uplink-kind.other",
                        "{iface} is not WiFi and not a cable. The watchdog knows no repair for it, so it only "
                        "records its failures.",
                        iface=i,
                    ),
                )
            )
        stall = lk.get("stall") if lk.get("state") == "stalled" and i != st.get("iface") else None
        if stall:
            out.append(
                finding(
                    f"uplink-stalled-{i}",
                    M("uplink-stalled-link.title", "{iface} is down and its watchdog has stalled", iface=i),
                    "problem" if stall.get("needs") else "warn",
                    M(
                        "uplink-stalled-link.down",
                        "{iface} is down, and the steps that you selected for it are used up.",
                        iface=i,
                    ),
                    fix=M(
                        "uplink-stalled-link.fix",
                        "The step that could help is to {step}. On Network, Network recovery, "
                        "select a maximum action that goes that far.",
                        step=stall["label"],
                    )
                    if stall.get("needs")
                    else NOTHING_HELPS,
                    notes=[stall["text"]] if stall.get("text") else [],
                    go=go("up-reach"),
                )
            )
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
FLAKY_OUTAGES = 5  # a connection with fewer outages in STEP_DAYS is not judged
STEP_TRIES = 4  # nor a step tried fewer times
MEND_S, HELD_S, WEDGE_S, REPEAT_S = 300, 120, 180, 120
NET_CAUSES = ("absent", "join", "gateway")  # uplink.NET_CAUSES: the network at fault, not the box
STEP_SAID = {
    "reconnect": "reconnecting",
    "restart": "restarting the network service",
    "radio": "resetting the radio",
    "reboot": "rebooting",
}
STEP_TICK = {
    "reconnect": "Reconnect",
    "restart": "Restart the network service",
    "radio": "Reset the radio",
    "reboot": "Reboot the box",
}
# The same steps for the page: their checkbox labels on Network's recovery actions, and a finding's title for each.
STEP_LABEL = {
    "reconnect": M("step.reconnect", "Reconnect"),
    "restart": M("step.restart", "Restart the network service"),
    "radio": M("step.radio", "Reset the radio"),
    "reboot": M("step.reboot", "Reboot the box"),
}
STEP_POOR = {
    "reconnect": lambda i: M("uplink-skip.reconnect.title", "{iface}: reconnecting does not help", iface=i),
    "restart": lambda i: M(
        "uplink-skip.restart.title", "{iface}: restarting the network service does not help", iface=i
    ),
    "radio": lambda i: M("uplink-skip.radio.title", "{iface}: resetting the radio does not help", iface=i),
}


def _no_misses(row):
    """A flap declared with no misses at all, as the watchdog once did in the look the link came back in:
    not a relapse."""
    return row.get("k") == "down" and str(row.get("t", "")).startswith("Dropped or missed 0 times")


def step_record(rows, now=None):
    """{iface: {"outages": n, "steps": {step: {"tries", "mended", "wedged"}}}} over the last STEP_DAYS."""
    now = time.time() if now is None else now
    since = now - STEP_DAYS * 86400
    by = {}
    for r in sorted(
        (r for r in rows if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and r.get("i")),
        key=lambda r: r["at"],
    ):
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
            later = rs[n + 1 :]
            nxt = next((x for x in later if x.get("k") in ("repair", "up")), None)
            mended = False
            if nxt and nxt.get("k") == "up" and nxt["at"] - r["at"] <= MEND_S:
                back = nxt["at"]
                stale = {x["at"] for x in later if _no_misses(x)}  # that look's wedge was the evidence from before
                mended = not any(
                    x is not nxt
                    and x.get("k") in ("down", "wedged")
                    and back <= x["at"] <= back + HELD_S
                    and not _no_misses(x)
                    and not (x.get("k") == "wedged" and x["at"] in stale)
                    for x in later
                )
            # The first wedge after it, if no other step came first: charged to this step only.
            first = next((x for x in later if x.get("k") in ("wedged", "repair")), None)
            wedged = (
                bool(first)
                and first.get("k") == "wedged"
                and 0 <= first["at"] - r["at"] <= WEDGE_S
                and not any(x.get("k") == "wedged" and 0 <= r["at"] - x["at"] <= WEDGE_S for x in rs[:n])
            )
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
        order = sorted(steps.items(), key=lambda kv: list(STEP_SAID).index(kv[0]) if kv[0] in STEP_SAID else 9)
        lines = [
            M(
                "steps.line-wedged",
                {
                    "one": "{step}: mended {mended} of {tries}. The radio's driver wedged soon after it {n} time.",
                    "other": "{step}: mended {mended} of {tries}. The radio's driver wedged soon after it {n} times.",
                },
                n=v["wedged"],
                step=STEP_LABEL.get(s, s),
                mended=v["mended"],
                tries=v["tries"],
            )
            if v["wedged"]
            else M(
                "steps.line",
                "{step}: mended {mended} of {tries}.",
                step=STEP_LABEL.get(s, s),
                mended=v["mended"],
                tries=v["tries"],
            )
            for s, v in order
        ]
        out.append(
            finding(
                f"uplink-steps-{iface}",
                M("steps.title", "How well each step works on {iface}", iface=iface),
                "ok",
                M(
                    "steps.outages",
                    {"one": "{n} outage in the last {days} days.", "other": "{n} outages in the last {days} days."},
                    n=rec["outages"],
                    days=STEP_DAYS,
                ),
                notes=lines or [M("steps.none", "The watchdog took no step.")],
            )
        )
        for s, v in steps.items():
            if s in off.get(iface, []) or v["tries"] < STEP_TRIES or s not in STEP_POOR:
                continue
            poor = v["mended"] / v["tries"] <= 0.2
            if not poor and v["wedged"] < 2:
                continue
            out.append(
                finding(
                    f"uplink-skip-{iface}-{s}",
                    STEP_POOR[s](iface),
                    "warn",
                    M(
                        "uplink-skip.mended",
                        "It mended {mended} of the {tries} outages that it was tried on, in the last {days} days.",
                        mended=v["mended"],
                        tries=v["tries"],
                        days=STEP_DAYS,
                    ),
                    M(
                        "uplink-skip.why",
                        "The watchdog could leave it out on {iface} and go on to the next step sooner.",
                        iface=iface,
                    ),
                    M(
                        "uplink-skip.fix",
                        "On Network, Connection settings, Recovery actions, clear {step} for {iface}.",
                        step=STEP_LABEL[s],
                        iface=iface,
                    ),
                    notes=[
                        M(
                            "uplink-skip.wedged",
                            "The radio's driver wedged soon after it {n} times. That can be a coincidence.",
                            n=v["wedged"],
                        )
                    ]
                    if v["wedged"] >= 2
                    else [],
                    go=_go("up-steps", M("go.recovery-actions", "Network → Recovery actions"), f"up-step-{iface}-{s}"),
                )
            )
    return out


ROAMS_AN_HOUR = 6  # more than this, with the hotspot on the same radio, is worth a look


def _json_or(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def roaming_findings(st, inv):
    """Roaming often while the hotspot shares the radio: each roam moves the guests'
    channel too, and is a re-association on the radio. Refused with the choices, never done for the owner."""
    radio = next(
        (r for r in (inv or {}).get("radios", []) if r.get("iface") == st.get("iface") and r.get("roaming")), None
    )
    f = (radio or {}).get("roaming") or {}
    n, chosen = st.get("roams_hour") or 0, (st.get("chosen") or {}).get("roaming", "roam")
    if not f.get("hotspot_shares") or len(f.get("aps", [])) < 2 or chosen != "roam" or n <= ROAMS_AN_HOUR:
        return []
    return [
        finding(
            "uplink-roaming",
            M("roaming.title", "Roaming often, with the hotspot on the same radio"),
            "warn",
            M(
                "roaming.often",
                "{iface} moved between access points {n} times in the last hour.",
                iface=st.get("iface"),
                n=n,
            ),
            M(
                "roaming.why",
                "Each move takes the hotspot's guests along, and is a moment off the network on a radio "
                "that copes worst with that.",
            ),
            M(
                "roaming.fix",
                "On Network, Network recovery, select No background scans while the hotspot shares the radio, "
                "or Lock to one access point. You can also select Do not count short roams as missed checks.",
            ),
            notes=[
                M(
                    "roaming.aps",
                    "{count} access points share {ssid}, on channels {channels}.",
                    count=len(f["aps"]),
                    ssid=f.get("ssid"),
                    channels=list(f.get("channels", [])),
                ),
                M(
                    "roaming.router",
                    "On your router, you can also put the access points on one channel. "
                    "That can help, but it does not suit every network.",
                ),
            ],
            go=_go("net-watchdog-card", NET_RECOVERY),
        )
    ]


def metered_findings(now=None):
    """Scheduled updates and downloads waiting a long while because the box is on a metered network."""
    from irate_box.hub import netpolicy

    w = netpolicy.waiting()
    now = now or time.time()
    if not w or w.get("since") is None or now - float(w["since"]) < 3 * 86400:
        return []
    days = int((now - float(w["since"])) // 86400)
    return [
        finding(
            "metered-wait",
            M("metered.title", "Updates waiting for another network"),
            "warn",
            M(
                "metered.waiting",
                {
                    "one": "The box has been on a metered network for {n} day.",
                    "other": "The box has been on a metered network for {n} days.",
                },
                n=days,
            ),
            M("metered.why", "Its scheduled updates and downloads wait: the hub's own, books, mirrors and toolkits."),
            M(
                "metered.fix",
                "Run an update by hand on Updates. Or, if this network's data is not limited, set it as not metered "
                "in its own settings on Network.",
            ),
            evidence=[str(x) for x in (w.get("connection") or w.get("iface"), w.get("how")) if x],
            go=GO_UPDATES,
        )
    ]


def check_inventory():
    """The network inventory's findings that lower the chance of the box staying on its network (its
    warnings and problems), each with its fix, and how old the look is when that matters."""
    p = CONTROL / "netinv.json"
    title = M("netinv.title", "Network inventory")
    if not p.exists():
        return [
            finding(
                "netinv",
                title,
                "warn",
                M("netinv.none", "The box has not scanned the network yet."),
                actions=[action("net-scan", M("netinv.scan", "Scan the network"))],
            )
        ]
    inv = _json_or(p)
    if not isinstance(inv, dict):
        return [
            finding(
                "netinv",
                title,
                "warn",
                M("netinv.unreadable", "The last scan cannot be read."),
                actions=[action("net-scan", M("netinv.scan-again", "Scan again"))],
            )
        ]
    age = time.time() - float(inv.get("at") or 0)
    old = (
        [
            M(
                "netinv.age",
                {"one": "This is from a scan {n} hour ago.", "other": "This is from a scan {n} hours ago."},
                n=int(age // 3600),
            )
        ]
        if age > 6 * 3600
        else []
    )
    # The inventory's own findings (netinv.py), passed on as they are; an inventory from before it wrote them
    # has only its English hazards.
    found = inv.get("findings")
    if found is not None:
        out = [
            dict(f, id=f"net:{f['id']}", notes=[*(f.get("notes") or ()), *old])
            for f in found
            if f.get("status") in ("warn", "problem")
        ]
    else:
        out = [
            finding(f"net:{h['id']}", h["title"], h["status"], h["detail"], fix=h.get("fix") or None, notes=old)
            for h in inv.get("hazards") or []
            if h.get("status") in ("warn", "problem")
        ]
    if age > 24 * 3600:
        out.append(
            finding(
                "netinv",
                title,
                "warn",
                M(
                    "netinv.old",
                    {
                        "one": "The last scan of the network was {n} day ago.",
                        "other": "The last scan of the network was {n} days ago.",
                    },
                    n=int(age // 86400),
                ),
                actions=[action("net-scan", M("netinv.scan-again", "Scan again"))],
            )
        )
    return out


MODPROBE_D = Path(os.environ.get("HUB_MODPROBE_DIR", "/etc/modprobe.d"))
AIC_MODULE = "aic8800_fdrv"
# Driver options meant only for tests: each slows or disturbs the driver, or floods the kernel log.
AIC_TEST_OPTIONS = re.compile(
    r"\b(fake_cmd_timeout|timeout_reads|log_hw_regs|disconnect_wait_ms|idle_watch_ms|idle_nudge)="
)
# The driver's log levels beyond its own default (error, info, firmware: 0x203): trace, debug, data and steering,
# which tests turn on at run time and which fill the kernel's log.
AIC_VERBOSE_BITS = 0x3C
# The driver's stall watch (aic8800_fdrv's stall_watch), as the page says it.
AIC_STALL_WATCH = {0: "off", 1: "log only", 2: "reset at once"}


def _aic_clients(radio):
    """The AIC8800's WiFi client interfaces: {iface: power saving on (True/False), or None if unknown}, from iw."""
    from irate_box.hub import netinv

    out = {}
    r = run("iw", "dev", timeout=10)
    if r.returncode != 0:
        return out
    for iface, d in netinv.parse_iw_dev(r.stdout).items():
        if d.get("type") != "managed":
            continue
        if "aic8800" not in os.path.realpath(radio.SYS / "class/net" / iface / "device/driver"):
            continue
        ps = run("iw", "dev", iface, "get", "power_save", timeout=10)
        said = ps.stdout.strip().lower() if ps.returncode == 0 else ""
        out[iface] = said.endswith(": on") if said.endswith((": on", ": off")) else None
    return out


def _aic_powersave_notes(clients):
    """Power saving's state on each AIC8800 client, its latency as measured, and its power left unconfirmed."""
    notes = [
        M("wifi-stall.powersave.on", "Power saving is on for {iface}.", iface=iface)
        if on
        else M("wifi-stall.powersave.off", "Power saving is off for {iface}.", iface=iface)
        for iface, on in clients.items()
        if on is not None
    ]
    if notes:
        notes.append(
            M(
                "wifi-stall.powersave.cost",
                "With power saving on, an AIC8800 client answers about 6 ms slower on average, with spikes to about "
                "50 ms. As a hotspot it costs nothing measurable. How much power it saves has not been confirmed.",
            )
        )
    return notes


def check_wifi_stalls():
    """An AIC8800 radio (its driver loaded): the firmware's known stall, and whether this box is set to ride it out.
    Its firmware can stop answering while its hotspot is busy during the WiFi link's join; a driver with the fix
    resets the chip and the link is back in about 15 s. The uplink watchdog then starts the hotspot again: at
    once when the driver reports its reset (recovered_at), else from the kernel journal's line, else at its next
    check. Whether it can see the reset; test options left on the driver; and what limits the stall."""
    from irate_box.root import radio

    mod = radio.SYS / "module" / AIC_MODULE
    if not mod.exists():
        return []
    out = []
    params = mod / "parameters"
    title = M("wifi-stall.title", "AIC8800 WiFi: the firmware's known stall")
    limits = M(
        "wifi-stall.limits",
        "What prevents it: the hotspot on the WiFi link's channel, which the box keeps on this radio. Fewer roams "
        "while guests use the hotspot (the Network page's roaming setting) make it rarer still.",
    )
    why = M(
        "wifi-stall.why",
        "The radio's firmware can stop answering when its hotspot is busy on another channel while the WiFi link "
        "joins (a roam or a reconnect). On a test box it happened about 3 times in 100 joins under load. "
        "Each time, the two were on different channels. It never happened in 161 joins on the same channel.",
    )
    notes = _aic_powersave_notes(_aic_clients(radio))
    try:
        recoveries = int((params / "recoveries").read_text().strip())
        at = int((params / "recovered_at").read_text().strip())
    except (OSError, ValueError):
        recoveries = at = None
    try:
        watch = int((params / "stall_watch").read_text().strip())
    except (OSError, ValueError):
        watch = None
    if recoveries is not None:
        evidence = [f"recoveries {recoveries}, recovered_at {at}"] + (
            [f"stall_watch {watch} ({AIC_STALL_WATCH.get(watch, 'unknown')})"] if watch is not None else []
        )
        out.append(
            finding(
                "wifi-stall",
                title,
                "ok",
                M(
                    "wifi-stall.reported",
                    "The driver resets the chip when its firmware stops answering, and tells the watchdog, which then "
                    "starts the hotspot again. It has reset the chip {n} times since it loaded.",
                    n=recoveries,
                ),
                why,
                limits,
                notes=notes,
                evidence=evidence,
            )
        )
    else:
        kernel = run("journalctl", "-k", "-b", "-q", "--no-pager", "-n", "1", timeout=20)
        seen = kernel.returncode == 0 and bool(kernel.stdout.strip())
        out.append(
            finding(
                "wifi-stall",
                title,
                "ok" if seen else "warn",
                M(
                    "wifi-stall.journal",
                    "The driver does not report its own resets. The watchdog learns of them from the kernel's log, and "
                    "otherwise starts the hotspot again at its next check.",
                )
                if seen
                else M(
                    "wifi-stall.unseen",
                    "The driver does not report its own resets, and the kernel's log is not in the journal. So the "
                    "watchdog cannot tell its reset from a fault, and starts the hotspot again only at its next check.",
                ),
                why,
                M(
                    "wifi-stall.fix",
                    "A driver that reports its resets (recovered_at among its parameters) lets the watchdog act at once. "
                    "{limits}",
                    limits=limits,
                ),
                notes=notes,
                commands=["ls /sys/module/aic8800_fdrv/parameters/", "journalctl -k -b -n 5"],
            )
        )
    out += _aic_channel(title, why)
    left = []
    for conf in sorted(MODPROBE_D.glob("*.conf")):
        try:
            text = conf.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if not line.lstrip().startswith("#") and "aic8800" in line and AIC_TEST_OPTIONS.search(line):
                left.append(f"{conf}: {line.strip()}")
    # A log level raised at run time (as tests do) lasts until the driver loads again: no file says so.
    try:
        level = int((params / "aicwf_dbg_level").read_text().strip())
    except (OSError, ValueError):
        level = None
    if level is not None and level & AIC_VERBOSE_BITS:
        left.append(f"aicwf_dbg_level {level} (now: trace, debug, data or steering on)")
    if left:
        out.append(
            finding(
                "wifi-stall:test-options",
                M("wifi-stall.test-options.title", "AIC8800 WiFi: test options left on the driver"),
                "warn",
                M("wifi-stall.test-options", "The WiFi driver loads with options meant only for tests."),
                M(
                    "wifi-stall.test-options.why",
                    "They slow the driver at a fault, or fill the kernel's log so that it keeps less.",
                ),
                M(
                    "wifi-stall.test-options.fix",
                    "Remove the file, or those options from it, and load the driver again or restart the box.",
                ),
                evidence=left,
            )
        )
    return out


def _aic_channel(title, why):
    """The AIC8800's hotspot on another channel than the WiFi link on the same radio: the condition of every stall
    measured (2026-10-22). A plan from before the box knew it (apmode, known_radios.json), or a hotspot started by
    hand, can leave it there."""
    from irate_box.root import ap

    rec = ap._load(ap.RECORD, {})
    plan = rec.get("plan") or {}
    if not rec.get("up") or plan.get("kind") not in ("own-channel", "follow"):
        return []
    chans = ap.channels_now(run)
    hotspot, link = chans.get(ap.ap_iface(plan)), chans.get(plan.get("uplink"))
    if hotspot is None or link is None or hotspot == link:
        return []
    return [
        finding(
            "wifi-stall:channel",
            title,
            "warn",
            M(
                "wifi-stall.channel",
                "The hotspot is on channel {hotspot} and the WiFi link on channel {link}, on the same radio.",
                hotspot=hotspot,
                link=link,
            ),
            why,
            M(
                "wifi-stall.channel.fix",
                "On Network, Hotspot, switch the hotspot off and on again: it then follows the WiFi link's channel.",
            ),
            go=GO_HOTSPOT,
        )
    ]


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
    title = M("hotspot-iface.title", "Hotspot interface")
    again = M("hotspot.toggle.fix", "On Network, Hotspot, switch the hotspot off and on again.")
    if iface not in chans:
        out.append(
            finding(
                "hotspot-iface",
                title,
                "problem",
                M(
                    "hotspot-iface.missing",
                    "The hotspot should be on {iface}, but {iface} is not on the box.",
                    iface=iface,
                ),
                fix=again,
                go=GO_HOTSPOT,
            )
        )
    elif chans.get(iface) != want:
        out.append(
            finding(
                "hotspot-iface",
                title,
                "warn",
                M(
                    "hotspot-iface.channel-follows",
                    "{iface} is on channel {now}, not on the WiFi link's channel {want}.",
                    iface=iface,
                    now=chans.get(iface),
                    want=want,
                )
                if plan.get("follows_uplink")
                else M(
                    "hotspot-iface.channel",
                    "{iface} is on channel {now}, not {want}.",
                    iface=iface,
                    now=chans.get(iface),
                    want=want,
                ),
                fix=again,
                go=GO_HOTSPOT,
            )
        )
    else:
        out.append(
            finding(
                "hotspot-iface",
                title,
                "ok",
                M("hotspot-iface.ok", "{iface} is on channel {channel}.", iface=iface, channel=want),
                notes=[plan["text"]] if plan.get("text") else [],
            )
        )
    if plan.get("backend") == "hostapd":
        if run("systemctl", "is-active", "--quiet", ap.HOSTAPD_UNIT).returncode:
            out.append(
                finding(
                    "hotspot-ap",
                    M("hotspot-ap.title-hostapd", "Hotspot access point (hostapd)"),
                    "problem",
                    M("hotspot-ap.hostapd", "hostapd is not running."),
                    M("hotspot-ap.why", "Devices cannot join the hotspot."),
                    commands=[f"journalctl -u {ap.HOSTAPD_UNIT} -n 30"],
                    actions=[action(f"unit-restart:{ap.HOSTAPD_UNIT}", RESTART)],
                )
            )
    else:
        act = run("nmcli", "-t", "-f", "NAME", "connection", "show", "--active").stdout or ""
        if ap.CONNECTION not in act.split():
            out.append(
                finding(
                    "hotspot-ap",
                    M("hotspot-ap.title-nm", "Hotspot access point (NetworkManager)"),
                    "problem",
                    M("hotspot-ap.nm", "The connection {name} is not active.", name=ap.CONNECTION),
                    M("hotspot-ap.why", "Devices cannot join the hotspot."),
                    M("hotspot-ap.nm.fix", "Bring the connection up."),
                    commands=[f"nmcli connection up {ap.CONNECTION}"],
                )
            )
    if run("systemctl", "is-active", "--quiet", ap.DNSMASQ_UNIT).returncode:
        out.append(
            finding(
                "hotspot-dns",
                M("hotspot-dns.title", "Hotspot DHCP and names"),
                "problem",
                M("hotspot-dns.stopped", "dnsmasq is not running."),
                M(
                    "hotspot-dns.why",
                    "New guests get no address. A device that has one already can keep it until it expires.",
                ),
                commands=[f"journalctl -u {ap.DNSMASQ_UNIT} -n 30"],
                actions=[action(f"unit-restart:{ap.DNSMASQ_UNIT}", RESTART)],
            )
        )
    if not rec.get("confirmed", True):
        out.append(
            finding(
                "hotspot-confirm",
                M("hotspot-confirm.title", "Hotspot waiting to be kept"),
                "warn",
                M("hotspot-confirm.waiting", "The hotspot took the box's WiFi link."),
                fix=M(
                    "hotspot-confirm.fix",
                    "From a device on the hotspot, select Keep it. If nobody does, "
                    "the box's WiFi link comes back by itself.",
                ),
            )
        )
    return out


def check_hotspot_per_account():
    """While the hotspot is on: whether it could give each account a WiFi password of its own (ap.per_account).
    Information for the owner's choice, never a problem."""
    from irate_box.hub import hotspot
    from irate_box.root import ap

    rec = ap._load(ap.RECORD, {})
    if not rec.get("up"):
        return []
    got = ap.per_account(rec.get("plan") or {}, hotspot.load(), hotspot.capabilities())
    return [
        finding(
            "hotspot-per-account",
            M("hotspot-per-account.title", "WiFi passwords for each account"),
            "ok",
            got["said"],
            notes=got["notes"],
        )
    ]


def check_web():
    web = option(install_options(), "--web", "nginx") or "nginx"
    title = M("web-config.title", "Web server configuration")
    if web == "nginx" and shutil.which("nginx"):
        r = run("nginx", "-t")
    elif web == "caddy" and shutil.which("caddy"):
        r = run(
            "caddy",
            "validate",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
            env=dict(os.environ, HOME=os.environ.get("HOME", "/root")),
        )
    else:
        return [
            finding(
                "web-config",
                title,
                "problem",
                M("web-config.missing", "{web} is not installed.", web=web),
                fix=M("web-config.missing.fix", "Run the installer again, with --web nginx or --web caddy."),
                actions=[RERUN],
            )
        ]
    if r.returncode:
        lines = [ln[:200] for ln in (r.stderr + r.stdout).splitlines() if re.search(r"emerg|error|Error", ln)][:3]
        return [
            finding(
                "web-config",
                title,
                "problem",
                M("web-config.rejected", "{web} rejects its configuration.", web=web),
                fix=M(
                    "web-config.rejected.fix",
                    "If the error is in irate-box's site, run the installer again, which writes the "
                    "site again. If the error is in your own configuration, correct that file and "
                    "reload {web}.",
                    web=web,
                ),
                evidence=lines,
                actions=[RERUN],
            )
        ]
    return [finding("web-config", title, "ok", M("web-config.ok", "{web} accepts its configuration.", web=web))]


def port_80_holder():
    """The program listening on port 80 ("" when none does), or None when ss cannot say."""
    r = run("ss", "-Hltnp", "sport = :80")
    if r.returncode:
        return None
    found = re.search(r'users:\(\("([^"]+)"', r.stdout)
    return found.group(1) if found else ("?" if r.stdout.strip() else "")


def check_port_80():
    """Two questions: should the hub serve plain HTTP on port 80, and does it? It should unless the administrator
    turned plain HTTP off (Security, HTTPS) or the installer moved the hub to another port because port 80 was
    taken (updates keep the move, so it stays even once port 80 is free again: the Lyra, 2026-10-21, where
    http://box/ opened nothing)."""
    from irate_box.root import tls

    title = M("port-80.title", "Port 80")
    opts = install_options()
    port = option(opts, "--port", "80")
    web = option(opts, "--web", "nginx") or "nginx"
    holder = port_80_holder()
    if tls.settings()["transport"] == "https":
        return [
            finding(
                "port-80", title, "ok", M("port-80.https-only", "Plain HTTP is off, so the hub does not use port 80.")
            )
        ]
    if port == "80":
        if holder in (web, None):
            return [finding("port-80", title, "ok", M("port-80.ok", "The hub serves port 80."))]
        return [
            finding(
                "port-80",
                title,
                "problem",
                (
                    M("port-80.silent", "Nothing serves port 80, where the hub should be.")
                    if holder == ""
                    else M("port-80.taken", "{holder} has port 80, where the hub should be.", holder=holder)
                ),
                M(
                    "port-80.silent.why",
                    "The box's address with http:// opens nothing of the hub's, and phones on the hotspot get no "
                    "sign-in sheet.",
                ),
                (
                    M("port-80.silent.fix", "Check {web}, then start it again.", web=web)
                    if holder == ""
                    else M(
                        "port-80.taken.fix",
                        "Stop {holder}, then run the installer again. Or run it with --take-port-80, which stops {holder} for you.",
                        holder=holder,
                    )
                ),
                commands=[f"systemctl status {web}", "sudo ss -ltnp 'sport = :80'"],
                evidence=[f"ss: {holder or 'nothing'} listens on port 80"],
            )
        ]
    why = M(
        "port-80.why",
        "The box's address with http:// and no port does not open the hub: visitors must add :{port}. "
        "Phones on the hotspot get no sign-in sheet, which needs port 80.",
        port=port,
    )
    if holder == "":
        return [
            finding(
                "port-80",
                title,
                "problem",
                M(
                    "port-80.free",
                    "The hub is on port {port}, and nothing serves port 80.",
                    port=port,
                ),
                why,
                M(
                    "port-80.free.fix",
                    "The installer moved the hub when port 80 was taken, and port 80 is free now. "
                    "Move the hub to port 80: the installer runs again with --port 80. Updates then keep port 80.",
                ),
                commands=[f"sudo {CODE}/install.sh --port 80"],
                evidence=[f"--port {port} in {ETC / 'install-options'}", "ss: nothing listens on port 80"],
                actions=[rerun_port_80(port)],
            )
        ]
    return [
        finding(
            "port-80",
            title,
            "warn",
            (
                M(
                    "port-80.held.same",
                    "Another site in {web} serves port 80, so the hub is on port {port}.",
                    web=web,
                    port=port,
                )
                if holder == web
                else M(
                    "port-80.held",
                    "{holder} serves port 80, so the hub is on port {port}.",
                    holder=holder or "?",
                    port=port,
                )
            ),
            why,
            (
                M(
                    "port-80.held.same.fix",
                    "To give the hub port 80, move that site to another port, then run the installer again with --port 80.",
                )
                if holder == web
                else M(
                    "port-80.held.fix",
                    "To give the hub port 80, run the installer again with --take-port-80. That stops {holder}. "
                    "The Security page or uninstall.sh restores it.",
                    holder=holder or "?",
                )
            ),
            evidence=[f"--port {port} in {ETC / 'install-options'}", f"ss: {holder or 'unknown'} listens on port 80"],
        )
    ]


def _front_silent():
    """The web server's plain port when nothing answers HTTP there at all, else None. Then an add-on asked through it
    cannot say anything of its own: its finding names the web server, with no offer to restart the add-on (on a Lyra,
    Kiwix, Syncthing and MQTT each offered a restart while nothing served port 80)."""
    from irate_box.root import webprobe

    host, port = webprobe.front_address()
    return None if webprobe.ask(host, port, "GET", "/")["http"] else port


def _front_silent_finding(key, title, port):
    return finding(
        key,
        title,
        "warn",
        M(
            "addon-front.unchecked",
            "Not checked: the web server does not answer on port {port}, so nothing behind it can.",
            port=port,
        ),
        fix=M("addon-front.unchecked.fix", "See the web server's own findings here: Port 80 and its service."),
    )


def check_addon_front():
    """Each running web add-on (a manifest's status with a port, a route and a unit), asked both ways: straight on
    its own web listener (status.web_port, else status.port), and through the web server as a guest's browser
    would (webprobe.through_front); a WebSocket route with the handshake. Which side fails says where the fault is:
    both answer, fine; it answers but the web server doesn't pass it on, the web server's route to it (run the
    installer again, no restart of the add-on); it doesn't answer itself, the add-on (start it again). While the
    web server answers nothing at all, a note that names it instead. Whether the web server keeps its sign-in is
    the Security doctor's."""
    from irate_box.hub import manifests
    from irate_box.root import webprobe

    try:
        found = manifests.load()
    except (manifests.ManifestError, OSError):
        return []
    out = []
    silent = _front_silent()
    for m in found:
        st = m.get("status") or {}
        if not (st.get("port") and st.get("path") and st.get("unit")):
            continue
        unit = st["unit"].replace("@hub.", f"@{HUB_USER}.")
        if unit_props(unit).get("ActiveState") != "active":
            continue  # not running: the units check says so
        key = f"addon-front:{m['id']}"
        title = named(st.get("name") or m["id"], M("addon-front.role", "through the web server"))
        if silent is not None:
            out.append(_front_silent_finding(key, title, silent))
            continue
        websocket = bool(st.get("websocket"))
        how = "GET (WebSocket handshake)" if websocket else "GET"
        own_port = st.get("web_port") or st["port"]
        r, url = webprobe.through_front(st["path"], websocket=websocket)
        front_said = f"{how} {url}: {r['status'] if r['http'] else r['error']}"
        if webprobe.answers(r, websocket):
            out.append(
                finding(
                    key, title, "ok", M("addon-front.up", "It answers through the web server."), evidence=[front_said]
                )
            )
            continue
        direct = webprobe.ask("127.0.0.1", own_port, "GET", st["path"], websocket=websocket)
        own_said = f"{how} 127.0.0.1:{own_port}{st['path']}: {direct['status'] if direct['http'] else direct['error']}"
        if webprobe.answers(direct, websocket):
            out.append(
                finding(
                    key,
                    title,
                    "problem",
                    M(
                        "addon-front.route",
                        "It answers on its own port {port}, but the web server does not pass requests on to it.",
                        port=own_port,
                    ),
                    M("addon-front.down.why", "Its page shows an error, or nothing, to everyone who opens it."),
                    M(
                        "addon-front.route.fix",
                        "Run the installer again, so the web server's settings for it are written again.",
                    ),
                    evidence=[front_said, own_said],
                    actions=[RERUN],
                )
            )
            continue
        out.append(
            finding(
                key,
                title,
                "problem",
                M(
                    "addon-front.down",
                    "It runs, but it answers neither on its own port {port} nor through the web server.",
                    port=own_port,
                ),
                M(
                    "addon-front.down.why",
                    "Its page shows an error, or nothing, to everyone who opens it.",
                ),
                M("addon-front.down.fix", "Start it again. If that does not help, run the installer again."),
                evidence=[front_said, own_said],
                actions=[action(f"unit-restart:{unit}", RESTART), RERUN],
            )
        )
    return out


def check_addon_declared():
    """The checks an installed add-on's manifest declares for the Box doctor ("doctor": "box": does it work), as
    the Security doctor runs its own (secdoctor.run_declared): each that fails a finding, with the repair the
    manifest names from this doctor's own (restart its unit, or run the installer again)."""
    from irate_box.hub import manifests
    from irate_box.root import secdoctor

    try:
        found = manifests.load()
        found += manifests.load_local(builtin=found)[0]
    except (manifests.ManifestError, OSError):
        return []
    out = []
    front = {}
    for m in found:
        st = m.get("status") or {}
        if not any(c.get("doctor") == "box" for c in m.get("checks") or ()):
            continue
        if m.get("local"):
            # A local add-on: installed when its folder is there; no unit, so no repair.
            if not Path(st["local_dir"]).is_dir():
                continue
            a = {"id": m["id"], "local": True, "root": st["local_dir"], "checks": m["checks"]}
            unit = None
        else:
            unit = st["unit"].replace("@hub.", f"@{HUB_USER}.")
            if unit_props(unit).get("LoadState") != "loaded":
                continue  # not installed
            a = {"id": m["id"], "unit": unit, "port": st.get("port"), "checks": m["checks"]}
        title = named(st.get("name") or m["id"], M("addon-check.role", "add-on"))
        via_front = {c.get("id") for c in m["checks"] if c.get("kind") == "http" and c.get("via") == "front"}
        for i in secdoctor.run_declared(a, doctor="box"):
            if i["key"] in via_front:
                if "port" not in front:  # asked once, and only when a check through it failed
                    front["port"] = _front_silent()
                if front["port"] is not None:
                    # Asked through a web server that answers nothing: the cause is that, not the add-on.
                    out.append(_front_silent_finding(f"addon-check:{m['id']}-{i['key']}", title, front["port"]))
                    continue
            repair = (
                {"restart": action(f"unit-restart:{unit}", RESTART), "rerun-install": RERUN}.get(i.get("repair"))
                if unit
                else None
            )
            out.append(
                finding(
                    f"addon-check:{m['id']}-{i['key']}",
                    title,
                    i["status"],
                    i["summary"],
                    i["why"],
                    i["fix"],
                    commands=i["commands"],
                    evidence=i["evidence"],
                    actions=[repair] if repair else [],
                )
            )
    return out


def check_git_addon():
    """Two questions, once Git is removed (the marker): was it meant to go, and are its servers gone? A removal
    cut short leaves the units or cgit's files behind. While Git is on, check_units asks after its socket."""
    if not GIT_REMOVED.exists():
        return []
    title = M("git-addon.title", "Git add-on")
    left = [str(UNIT_DIR / u) for u in ("irate-box-git.socket", "irate-box-git.service") if (UNIT_DIR / u).exists()]
    left += [
        str(STATE / "git" / f"cgitrc-{a}") for a in ("public", "private") if (STATE / "git" / f"cgitrc-{a}").exists()
    ]
    if not left:
        return [
            finding(
                "git-addon",
                title,
                "ok",
                M("git-addon.removed", "Git is removed from this box. The repositories and the mirrors stay on disk."),
            )
        ]
    return [
        finding(
            "git-addon",
            title,
            "warn",
            M("git-addon.half", "Git is removed, but some of its servers' files are still on the box."),
            M(
                "git-addon.half.why",
                "A removal that was cut short leaves them. They do nothing while Git's access is off, but an update "
                "or a check can trip over them.",
            ),
            M(
                "git-addon.half.fix",
                "Finish the removal: it leaves the repositories as they are. Or add Git back: run install.sh again "
                "with --with-git.",
            ),
            commands=[f"sudo {CODE}/irate-box gitaddon remove --yes"],
            evidence=left,
        )
    ]


def check_hub():
    title = M("hub.title", "Hub server")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/status", timeout=8) as r:  # The hub on loopback. # nosec B310
            json.load(r)
        return [finding("hub", title, "ok", M("hub.ok", "It answers on 127.0.0.1:8000."))]
    except (OSError, ValueError) as exc:
        return [
            finding(
                "hub",
                title,
                "problem",
                M("hub.down", "It does not answer on 127.0.0.1:8000."),
                M("hub.down.why", "The hub and the administration pages cannot open while it does not answer."),
                M("hub.down.fix", "Read its log, then start it again."),
                commands=["journalctl -u irate-box -n 50", "systemctl restart irate-box"],
                evidence=[str(exc)[:200]],
                actions=[action("unit-restart:irate-box.service", RESTART)],
            )
        ]


def _midnight(now):
    local = time.localtime(now)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def _power_failures(events, since):
    """The supply's failures, one entry each. A UPS says "on battery" first and "battery low" after it: those are
    one failure still happening, not two, and counting the pair as two while missing the case where the failure
    began before the window and is still on is both wrong at once. So the events are walked in order, a failure
    runs from a power-fail (or a battery-low that came with no power-fail, after the events rolled over) to the
    power coming back, and it is in the window when its last event is. The last event's words are kept, being
    what the supply says now."""
    out, live = [], None
    for e in sorted((e for e in events if isinstance(e.get("at"), (int, float))), key=lambda e: e["at"]):
        kind = e.get("kind")
        if kind in ("power-fail", "battery-low"):
            if live is None:
                live = {"at": e["at"], "kind": kind, "text": e.get("text") or "", "back": False}
                out.append(live)
            else:
                live.update(
                    at=e["at"], kind=kind, text=e.get("text") or live["text"], back=False
                )  # a low battery is the same failure, later
        elif kind == "power-restored" and live is not None:
            live["back"] = True
            live = None
    return [f for f in out if f["at"] >= since]


def check_power(now=None):
    """The supply: an INA2xx's bus voltage or the Pi's rpi_volt/get_throttled, and the power-fail hook (NUT or a pin)
    as crashwatch records them (Part 3, step 2). An undervoltage is a count and its times; a power failure is a
    problem, and so is one still happening: a UPS that ran the box down off the mains keeps saying its battery is
    low, and that is the same failure, not a new one. The last 24 hours, not since midnight, so a failure late at
    night stays in view."""
    from irate_box.root import crashwatch as cw

    now = time.time() if now is None else now
    settings = cw.load_settings()
    threshold = settings.get("undervoltage_mv", cw.UNDERVOLTAGE_MV)
    try:
        reading = cw.power_now(threshold)
        events = cw.power_events()
    except (OSError, ValueError):
        return []
    window = [e for e in events if isinstance(e.get("at"), (int, float)) and e["at"] >= now - 86400]
    fails = _power_failures(events, now - 86400)
    under = [e for e in window if e.get("kind") == "undervoltage"]
    title = M("power.title", "Power supply")
    go = _go("health-settings", RECOVERY, "hs-undervoltage")
    if fails:
        latest = max(fails, key=lambda e: e["at"])
        owner = cw.shutdown_owner(now)
        notes = [
            M(
                "power.failed.last",
                "The last one was {when}: {what}.",
                when=when(latest["at"]),
                what=latest.get("text") or "",
            )
        ]
        if not latest.get("back"):
            notes.append(
                M(
                    "power.battery.still",
                    "The box is running on its battery still.",
                )
                if latest.get("kind") == "battery-low"
                else M("power.failed.still", "The power has not come back since.")
            )
        if owner:
            notes.insert(
                0,
                M(
                    "power.manager",
                    "The shutdown is {manager}'s. The box logs and syncs. That program shuts it down.",
                    manager=owner,
                ),
            )
        fix = (
            M(
                "power.failed.fix-manager",
                "Check the supply and its cable. {manager} manages the shutdown. The box logs and syncs, and it shuts "
                "the box down.",
                manager=owner,
            )
            if owner
            else M(
                "power.failed.fix",
                "Check the supply and its cable. The setting When the power fails can sync the disks or shut the box "
                "down when the battery is low.",
            )
        )
        return [
            finding(
                "power",
                title,
                "problem",
                M(
                    "power.failed",
                    {
                        "one": "The power failed {n} time in the last 24 hours.",
                        "other": "The power failed {n} times in the last 24 hours.",
                    },
                    n=len(fails),
                ),
                M(
                    "power.failed.why",
                    "A power cut can lose the newest writes, and a card that loses writes can damage the files the "
                    "box keeps.",
                ),
                fix,
                notes=notes,
                evidence=[render(when(e["at"])) for e in fails[-10:]],
                go=go,
            )
        ]
    if not settings.get("undervoltage", True):
        return [
            finding(
                "power",
                title,
                "ok",
                M("power.off", "Undervoltage watching is off."),
                notes=[M("power.off.note", "It was turned off. A power-fail signal is still watched.")],
                go=go,
            )
        ]
    if under or reading["undervoltage"]:
        latest = max((e["at"] for e in under), default=now)
        summary = (
            M("power.low", "The supply is below {mv} mV now.", mv=threshold)
            if reading["undervoltage"]
            else M(
                "power.dropped",
                {
                    "one": "The supply dropped below {mv} mV {n} time in the last 24 hours.",
                    "other": "The supply dropped below {mv} mV {n} times in the last 24 hours.",
                },
                mv=threshold,
                n=len(under),
            )
        )
        return [
            finding(
                "power",
                title,
                "problem" if reading["undervoltage"] else "warn",
                summary,
                M(
                    "power.dropped.why",
                    "A supply that sags can reset a radio, corrupt a write, or make the box stop. A weak supply or a "
                    "thin cable is the usual cause.",
                ),
                M(
                    "power.dropped.fix",
                    "Use the supply the board asks for, with a short, thick cable. This finding counts each drop and "
                    "keeps the times.",
                ),
                notes=[M("power.dropped.last", "The last one was {when}.", when=when(latest))] if under else [],
                evidence=[render(when(e["at"])) for e in under[-10:]],
                go=go,
            )
        ]
    if not reading["measured"]:
        return [
            finding(
                "power",
                title,
                "ok",
                M("power.not-measured", "This board's supply is not measured."),
                notes=[
                    M(
                        "power.not-measured.note",
                        "A Raspberry Pi reports it as rpi_volt. A Lyra needs an INA219 or INA226 on I2C. Until then, an "
                        "undervoltage cannot be seen here.",
                    )
                ],
                go=go,
            )
        ]
    if reading.get("mv") is None:
        return [
            finding(
                "power",
                title,
                "ok",
                M("power.ok-rpi", "The firmware reports no undervoltage on this supply."),
                evidence=[reading["source"] or ""],
                go=go,
            )
        ]
    return [
        finding(
            "power",
            title,
            "ok",
            M(
                "power.ok",
                "The supply is {mv} mV, above the {threshold} mV threshold.",
                mv=reading["mv"],
                threshold=threshold,
            ),
            evidence=[reading["source"] or ""],
            go=go,
        )
    ]


SMALL_VOLUME = 1024  # MB: a volume of the state's own below this (the Lyra's NAND: 223 MB) is judged by its size


def _own_volume(path):
    """Whether path is on another filesystem than the root: the hub's state moved to the NAND, say."""
    try:
        return path.stat().st_dev != Path("/").stat().st_dev
    except OSError:
        return False


def check_space():
    out = []
    for fid_title, path, need in (
        (M("space.system.title", "Space on the system card"), Path("/"), 300),
        (M("space.state.title", "Space for the hub's state"), STATE, 200),
    ):
        if not path.exists():
            continue
        usage = shutil.disk_usage(path)
        free, size = usage.free >> 20, usage.total >> 20
        own = path != Path("/") and _own_volume(path)
        if own and size < SMALL_VOLUME:
            need = max(16, size // 4)  # the state's own small volume: a quarter of it, not the card's minimum
        if free < need:
            out.append(
                finding(
                    f"space:{path}",
                    fid_title,
                    "problem",
                    M("space.low", "Only {mb} MB is free.", mb=free),
                    M(
                        "space.low.why",
                        "With less than {need} MB free, installs, updates and logs can fail.",
                        need=need,
                    ),
                    M(
                        "space.low.fix.own",
                        "The hub's state is on a volume of its own ({size} MB, on the NAND when the box state is "
                        "kept there). Its records, histories and, if kept there, the journal fill it. Books, builds "
                        "and the file drop are on the SD card. Make the journal smaller.",
                        size=size,
                    )
                    if own and size < SMALL_VOLUME
                    else M(
                        "space.low.fix",
                        "Delete what is no longer needed: old books on Books, saved work, or build runs on Git, Builds. "
                        "You can also make the journal smaller.",
                    ),
                    commands=["journalctl --vacuum-size=16M"],
                    evidence=[str(path)],
                    go=GO_BOOKS,
                )
            )
    return out


# SD card writes: the hub's hourly record (cardhistory.py), read as data; per-service I/O accounting, so the record
# can say which service writes; a journal drop-in that syncs more often than journald's default; an architecture
# apt indexes with nothing installed for it (every package list and apt cache rebuild for it, for nothing).
GO_CARD_WRITES = _go("card-writes", M("go.card-writes", "Recovery → SD card writes"))
SYSTEMD_ETC = Path(os.environ.get("HUB_SYSTEMD_ETC", "/etc/systemd"))
IO_ACCOUNTING = SYSTEMD_ETC / "system.conf.d" / "50-irate-box-io.conf"
IO_ACCOUNTING_STATE = ETC / "io-accounting.json"
CARD_HIGH = 1 << 30  # bytes a day: above this, the doctor names what writes
JOURNAL_SYNC_DEFAULT = 300  # seconds: journald's SyncIntervalSec when nothing sets it


def _card_rows():
    from irate_box.hub import cardhistory
    from irate_box.root import safeio

    try:
        text = safeio.read_request(cardhistory.FILE, limit=4 << 20)
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("at"), (int, float)) and isinstance(row.get("card"), int):
            rows.append(row)
    return rows


def _seconds(text):
    """A systemd time span ("5s", "5min", "1h", "300") in seconds, or None."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(us|ms|s|sec|m|min|h|hr)?\s*", text or "")
    if not m:
        return None
    unit = {"us": 1e-6, "ms": 1e-3, None: 1, "s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}[m[2]]
    return float(m[1]) * unit


def _foreign_unused():
    """Architectures dpkg was told to add (--add-architecture) with no package installed for them."""
    foreign = run("dpkg", "--print-foreign-architectures").stdout.split()
    if not foreign:
        return []
    have = set(run("dpkg-query", "-W", "-f=${Architecture}\n").stdout.split())
    return [a for a in foreign if re.fullmatch(r"[a-z0-9-]{1,20}", a) and a not in have]


SERVICE_HEAVY = 100 << 20  # a service writing more than this a day to the card (averaged over the week) is named
MOUNTINFO = Path(os.environ.get("HUB_MOUNTINFO", "/proc/self/mountinfo"))


def _mount_of(path):
    """(mount point, filesystem type, source) of the mount that holds path (a str or a Path), from mountinfo; None if
    unknown."""
    path, best = str(path), None
    try:
        lines = MOUNTINFO.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        point = fields[4].replace("\\040", " ")
        if (path == point or path.startswith(point.rstrip("/") + "/")) and (best is None or len(point) > len(best[0])):
            best = (point, tail[0], tail[1])
    return best


def _on_card(path, card_name):
    """Whether path is on the SD card: its mount's source is the card or one of its partitions (not RAM, not the
    onboard flash, not zram)."""
    found = _mount_of(path)
    return bool(found and card_name and found[2].startswith(f"/dev/{card_name}"))


def _unit_writes(unit, card_name):
    """(confined, [card paths]): whether the unit can write only where it says (ProtectSystem=strict), and of the
    places it says, those on the SD card (its writable paths; its state, log and cache folders; a bind into RAM is
    not one)."""
    out = run(
        "systemctl",
        "show",
        unit,
        "-p",
        "ProtectSystem,ReadWritePaths,StateDirectory,LogsDirectory,CacheDirectory,BindPaths",
        timeout=10,
    ).stdout
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    confined = props.get("ProtectSystem") == "strict"
    bound = {}
    for pair in props.get("BindPaths", "").split():
        src, _, rest = pair.partition(":")
        bound[rest.split(":")[0] or src] = src
    paths = [p.lstrip("-") for p in props.get("ReadWritePaths", "").split()]
    for key, root in (("StateDirectory", "/var/lib"), ("LogsDirectory", "/var/log"), ("CacheDirectory", "/var/cache")):
        paths += [f"{root}/{name}" for name in props.get(key, "").split()]
    on_card = []
    for path in paths:
        if path in bound and not _on_card(bound[path], card_name):
            continue  # the unit's own view of it is RAM (a bind, as EWS's cache)
        under = [d for d in bound if d != path and d.startswith(path.rstrip("/") + "/")]
        if _on_card(path, card_name):
            on_card.append(path + (f" (but {', '.join(under)} in RAM)" if under else ""))
    return confined, on_card


def _unit_gone(unit):
    """Whether systemd no longer knows the unit (a transient run that has ended, or one removed): its past writes are
    in the week's total, but there is nothing left to confine or move."""
    return "LoadState=not-found" in run("systemctl", "show", unit, "-p", "LoadState", timeout=10).stdout


def _service_writes_findings(summary, card_name):
    """The services that write most to the card, each with where it can write there (from its unit)."""
    from irate_box.hub import cardhistory

    out = []
    days = max(1, min(7, len(summary["days"])))
    for unit, week in summary["week_units"]:
        per_day = week // days
        if per_day <= SERVICE_HEAVY:
            continue
        if unit == cardhistory.JOBS:
            out.append(
                finding(
                    "card-writer:jobs",
                    M("card-writer.title", "Writes of {unit}", unit=M("card-writer.jobs", "jobs that ended")),
                    "warn" if per_day > CARD_HIGH else "note",
                    M(
                        "card-writer.jobs.rate",
                        "Jobs that run and end (timers' short runs) write about {mb} MB a day to the SD card.",
                        mb=per_day >> 20,
                    ),
                    M(
                        "card-writer.jobs.why",
                        "A job that ends is not counted by name: look at the box's timers (systemctl list-timers) for one that runs often.",
                    ),
                    go=GO_CARD_WRITES,
                )
            )
            continue
        if unit == cardhistory.KERNEL or not unit.endswith(".service"):
            continue
        if _unit_gone(unit):
            continue  # found on box 181: an A/B trial's transient unit, gone for days, still "writing 294 MB a day"
        confined, paths = _unit_writes(unit, card_name)
        if confined:
            where = M(
                "card-writer.where",
                "It can write on the SD card only to {paths}.",
                paths=", ".join(paths) or "none of its own",
            )
        else:
            where = M(
                "card-writer.anywhere",
                "It can write anywhere on the SD card: its unit does not set ProtectSystem=strict.",
            )
        addon = next((m for m in _manifests() if (m.get("status") or {}).get("unit") == unit), None)
        notes = []
        if addon is not None and "writes" not in addon:
            notes.append(
                M(
                    "card-writer.no-writes",
                    "Its add-on's manifest says nothing about where it writes (no writes block).",
                )
            )
        # Each of its card paths can be kept in RAM, the owner judging what is throwaway (asked first).
        acts = [
            action(
                f"card-ram:{unit}:{i}",
                M("card-writer.ram", "Keep {path} in RAM", path=path.split(" (")[0]),
                M(
                    "card-writer.ram.confirm",
                    "Keep {path} in RAM for {unit}? What it holds is lost at each restart of the service or the box, and "
                    "made again by the service. Only for a cache, a download or an export: not for a database or "
                    "anything kept. The service restarts.",
                    path=path.split(" (")[0],
                    unit=unit,
                ),
            )
            for i, path in enumerate(paths[:8])
            if confined
        ]
        out.append(
            finding(
                f"card-writer:{unit}",
                M("card-writer.title", "Writes of {unit}", unit=unit),
                "warn",
                M("card-writer.rate", "{unit} writes about {mb} MB a day to the SD card.", unit=unit, mb=per_day >> 20),
                where,
                M(
                    "card-writer.fix",
                    "If what it writes there is a cache, a download or an export, it can be kept in RAM instead: an "
                    "add-on says which of its paths can be, in its manifest's writes block.",
                ),
                evidence=paths,
                notes=notes,
                actions=acts,
                go=GO_CARD_WRITES,
            )
        )
    out += _service_ram_findings()
    return out


def _manifests():
    try:
        from irate_box.hub import manifests

        return manifests.load()
    except Exception:  # noqa: BLE001 - a broken manifest is the doctor's own finding elsewhere
        return []


def _service_ram_findings():
    """A path declared to be in RAM (an add-on's manifest, or the owner's choice) that is not, for its service."""
    from irate_box.root import servicewrites

    out = []
    for unit, paths in servicewrites.declared(_manifests()).items():
        if run("systemctl", "show", unit, "-p", "LoadState", "--value", timeout=10).stdout.strip() != "loaded":
            continue
        if servicewrites.in_effect(unit, paths):
            continue
        out.append(
            finding(
                f"card-ram:{unit}",
                M("card-ram.title", "Paths in RAM for {unit}", unit=unit),
                "warn",
                M("card-ram.off", "{unit}'s paths to be kept in RAM are on the SD card now.", unit=unit),
                M("card-ram.why", "They are declared throwaway, so writing them to the card wears it for nothing."),
                M("card-ram.fix", "Put them in RAM: the box writes the service's drop-in and restarts it."),
                evidence=paths,
                actions=[action("card-ram-apply", M("card-ram.apply", "Put them in RAM"))],
                go=GO_CARD_WRITES,
            )
        )
    return out


def _card_life_note(per_day):
    """How many times a year the card is written over at this rate: an estimate, as SD cards publish no rating."""
    from irate_box.hub import cardhistory

    found = cardhistory.card()
    size = cardhistory.size(found[0]) if found else None
    if not size or not per_day:
        return []
    return [
        M(
            "card-writes.life",
            "At this rate the card ({gb} GB) is written over about {times} times a year. SD cards publish no "
            "endurance rating. Consumer cards are commonly good for a few hundred to a few thousand times over.",
            gb=round(size / 1e9),  # as cards are labelled
            times=f"{per_day * 365 / size:.1f}",
        )
    ]


def flash_read():
    """The flash's UBI volume read whole (it writes nothing): its read errors counted, the time it took."""
    vol = next(iter(sorted(Path("/dev").glob("ubi[0-9]*_0"))), None)
    if vol is None:
        raise Refused(M("repair.flash-read.none", "No UBI volume is on the onboard flash."))
    start = time.time()
    r = run("dd", f"if={vol}", "of=/dev/null", "bs=128k", "conv=noerror", "status=none", timeout=1800)
    errors = len(re.findall(r"Input/output error", r.stderr or ""))
    if errors:
        return M("repair.flash-read.errors", "The flash was read whole: {n} blocks could not be read.", n=errors)
    return M("repair.flash-read.ok", "The flash was read whole in {s} s, with no errors.", s=int(time.time() - start))


def _recommended_findings():
    """The recommended settings for writes and recovery this box does not have, and its owner has not chosen
    otherwise: listed, with one repair for those root keeps; the SD card writes level pointed to on its page."""
    from irate_box.root import recommended

    try:
        diffs = recommended.differences()
        level = recommended.level_differs()
    except Exception:  # noqa: BLE001 - the doctor goes on without this one
        return []
    if not diffs and not level:
        return []
    lines = [f"{render(label)} (now: {now})" for _, label, _, now in diffs]
    if level:
        lines.append(f"the SD card writes level {recommended.LEVEL} (Recovery → SD card writes)")
    return [
        finding(
            "recommended",
            M("rec.title", "Recommended settings"),
            "warn",
            M(
                "rec.summary",
                {
                    "one": "One setting differs from the recommended set for writes and recovery.",
                    "other": "{n} settings differ from the recommended set for writes and recovery.",
                },
                n=len(lines),
            ),
            M(
                "rec.why",
                "The recommended set writes less to the SD card and brings an unattended box back by itself: what a "
                "day of measuring and the Lyra's unclean stops have shown. A setting you selected yourself is not listed.",
            ),
            M("rec.fix", "Use the recommended settings, or change any of them on Recovery."),
            evidence=lines,
            actions=[
                action(
                    "recommended-apply",
                    M("rec.apply", "Use the recommended settings"),
                    M(
                        "rec.apply.confirm",
                        "Use the recommended settings? The box changes: {list}. The hardware watchdog can need a restart to take "
                        "effect. The onboard flash, if used, is formatted first, and only a blank one is used.",
                        list=", ".join(render(label) for _, label, _, _ in diffs) or "nothing root keeps",
                    ),
                )
            ]
            if diffs
            else [],
            go=GO_CARD_WRITES,
        )
    ]


# --- lost writes: the card that says it saved a file and returns 0xFF for it ----------------------------------
# Box 190's card lost 55 files, each read back whole as 0xFF, with its size and mtime kept: a card genuine by
# f3probe and badblocks that drops its newest writes at a power cut. The records the box rewrites every few
# minutes are the evidence, where they live on the card; where none does (a Lyra with nand-logs' state on), an
# optional 4 KB test file, rewritten every 10 minutes. Both are read past the cache, because the page cache can
# hold the copy the box believes it wrote.
SYSTEMD_UNITS = SYSTEMD_ETC / "system"
DOCTOR_SERVICE = "irate-box-boxdoctor.service"
DOCTOR_TIMER = "irate-box-boxdoctor.timer"
LOSTWRITES_SERVICE = "irate-box-lostwrites.service"
CANARY_SERVICE = "irate-box-canary.service"
CANARY_TIMER = "irate-box-canary.timer"
LOST_WRITES_REPORT = CONTROL / "lost-writes.json"
RECORD_FILES = (
    "memory-history.json",
    "service-history.json",
    "page-history.json",
    "card-history.jsonl",
    "clock.json",
    "boot_id",
)
CANARY_MAGIC = b"irate-box-canary 1\n"
CANARY_SIZE = 4096
CANARY_HEADER = len(CANARY_MAGIC) + 12 + 64 + 1  # the magic, the sequence and time, the SHA-256 in hex, a newline
BOOK_CHECK_DAYS = 7


def _boot_id():
    """This boot's id: /proc's, or the hub's own, so a finding from an older boot is not shown as this one's."""
    said = os.environ.get("HUB_BOOT_ID")
    if said:
        return said
    for path in (Path("/proc/sys/kernel/random/boot_id"), STATE / "boot_id"):
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def _reads_all_ff(path, limit=8 << 20):
    """Whether the file reads back as nothing but 0xFF: read past the cache, because the page cache may hold the
    copy the box believes it wrote."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except (AttributeError, OSError):
            pass
        seen, left = False, limit
        while left > 0:
            chunk = os.read(fd, min(1 << 20, left))
            if not chunk:
                break
            seen = True
            if chunk.count(0xFF) != len(chunk):
                return False
            left -= len(chunk)
        return seen
    finally:
        os.close(fd)


def records_on_card():
    """The records the box rewrites often that live on the SD card (by its mounts); [] where none does."""
    from irate_box.hub import cardhistory

    found = cardhistory.card()
    if not found:
        return []
    name = found[0]
    return [p for p in (STATE / filename for filename in RECORD_FILES) if p.exists() and _on_card(p, name)]


def _canary_path():
    from irate_box.root import safeio

    return safeio.ROOT_STATE / "canary.bin"


def _chosen():
    from irate_box.root import recommended

    return recommended.chosen()


def canary_on():
    return _chosen().get("canary") == "on"


def _canary_seq(path):
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if len(data) < CANARY_HEADER or data[: len(CANARY_MAGIC)] != CANARY_MAGIC:
        return 0
    try:
        return struct.unpack(">Q", data[len(CANARY_MAGIC) : len(CANARY_MAGIC) + 8])[0]
    except struct.error:
        return 0


def _write_synced(path, data, mode=0o600):
    """A file in place atomically, fsynced before the rename and the folder after it: a write the card must keep
    or return as 0xFF."""
    path = Path(path)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    tmp = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dfd)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        os.fsync(dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    finally:
        os.close(dfd)


def _canary_intact(path):
    """Whether the test file is the one written: its checksum matching what follows it (a file read past the cache
    by _reads_all_ff first, so this read is the card's)."""
    data = path.read_bytes()
    if len(data) != CANARY_SIZE or data[: len(CANARY_MAGIC)] != CANARY_MAGIC:
        return False
    start = len(CANARY_MAGIC) + 12
    return hashlib.sha256(data[CANARY_HEADER:]).hexdigest().encode() == data[start : start + 64]


def canary_tick():
    """The test file's turn: 4 KB with a sequence number and a checksum, fsynced. Only where the owner turned it on."""
    if not canary_on():
        return False
    path = _canary_path()
    from irate_box.root import safeio

    safeio.mkdir(path.parent, mode=0o700)
    payload = os.urandom(CANARY_SIZE - CANARY_HEADER)
    digest = hashlib.sha256(payload).hexdigest().encode()
    body = CANARY_MAGIC + struct.pack(">QI", _canary_seq(path) + 1, int(time.time())) + digest + b"\n" + payload
    _write_synced(path, body)
    return True


def lost_writes_check():
    """The finding for a card that lost its newest writes: a record on it, or the test file, reading as 0xFF."""
    bad = []
    for path in records_on_card():
        try:
            if _reads_all_ff(path):
                bad.append(path)
        except OSError:
            continue
    canary = _canary_path()
    canary_bad = False
    if canary_on() and canary.exists():
        try:
            canary_bad = _reads_all_ff(canary) or not _canary_intact(canary)
        except OSError:
            canary_bad = False
    if not bad and not canary_bad:
        return []
    names = ", ".join(p.name for p in bad) or canary.name
    notes = []
    if canary_bad and bad:
        notes.append(M("lost-writes.canary", "The test file on the card reads back as 0xFF too."))
    housecall = CODE / "irate_box/root/housecall.py"
    if housecall.exists():
        notes.append(
            M(
                "lost-writes.housecall",
                "The Housecall doctor can examine the whole card and name every file that it damaged.",
            )
        )
    return [
        finding(
            "card-lost-writes",
            M("lost-writes.title", "Lost writes"),
            "problem",
            M("lost-writes.found", "The SD card returned nothing but 0xFF for {names}.", names=names),
            M(
                "lost-writes.why",
                "The card loses its newest writes at a power cut, even writes it has said are saved. Other files "
                "written since can be lost the same way.",
            ),
            M(
                "lost-writes.fix",
                "Replace the SD card, and copy the box's settings and the data you keep onto the new one.",
            ),
            notes=notes,
            evidence=[str(p) for p in bad] + ([str(canary)] if canary_bad else []),
            commands=[f"sudo {CODE}/irate-box housecall --this-card"] if housecall.exists() else [],
            go=GO_CARD_WRITES,
        )
    ]


def boot_check():
    """At every boot, before the hub: the records on the card and the test file, read past the cache. The finding
    is kept for the doctor, so a record rewritten a minute later does not hide what the boot found."""
    found = lost_writes_check()
    from irate_box.root import safeio

    safeio.mkdir(CONTROL, mode=0o755)
    safeio.write(LOST_WRITES_REPORT, json.dumps({"at": time.time(), "boot": _boot_id(), "findings": found}, indent=1))
    return found


def check_lost_writes():
    """The boot's finding where it is this boot's; otherwise the check now (a box whose boot unit is not installed)."""
    try:
        data = json.loads(LOST_WRITES_REPORT.read_text())
    except (OSError, ValueError):
        return lost_writes_check()
    if not isinstance(data, dict) or data.get("boot") != _boot_id():
        return lost_writes_check()
    return [f for f in data.get("findings") or [] if isinstance(f, dict)]


def check_writes_kept():
    """A file the box wrote and the card did not keep, even after a second write: the checked writer's report
    (Part 2, step 2). A good write of the same path clears its entry. Root's report is a problem; the hub's alone is
    a warning, because the hub can write its own report."""
    from irate_box import durable

    latest = {}
    for report, source in (
        (durable.ROOT_STATE / "write-check.json", "root"),
        (durable.STATE / "write-check.json", "hub"),
    ):
        try:
            said = json.loads(report.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(said, list):
            continue
        for entry in said:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            at = entry.get("at") if isinstance(entry.get("at"), (int, float)) else 0
            if entry["path"] not in latest or at >= latest[entry["path"]][0]:
                latest[entry["path"]] = (at, entry, source)
    if not latest:
        return []
    root = [value for value in latest.values() if value[2] == "root"]
    hub = [value for value in latest.values() if value[2] == "hub"]
    housecall = CODE / "irate_box/root/housecall.py"
    notes = []
    if housecall.exists():
        notes.append(
            M(
                "writes-kept.housecall",
                "The Housecall doctor can examine the whole card and name every file it damaged.",
            )
        )
    if hub and not root:
        notes.insert(
            0,
            M(
                "writes-kept.hub",
                "The hub reported these. A hub's own report is not proof that the card is failing.",
            ),
        )
    return [
        finding(
            "card-writes-kept",
            M("writes-kept.title", "Writes the card did not keep"),
            "problem" if root else "warn",
            M(
                "writes-kept.found",
                {
                    "one": "The card did not keep {n} file, even after a second write.",
                    "other": "The card did not keep {n} files, even after a second write.",
                },
                n=len(latest),
            ),
            M(
                "writes-kept.why",
                "The box wrote the file, read it back from the card, and what came back was not what it wrote. A card "
                "that does this loses data at the next power cut.",
            ),
            M(
                "writes-kept.fix",
                "Replace the SD card. The Housecall doctor can examine the whole card first.",
            ),
            notes=notes,
            commands=[f"sudo {CODE}/irate-box housecall --this-card"] if housecall.exists() else [],
            evidence=sorted(latest),
            go=GO_CARD_WRITES,
        )
    ]


def check_protected():
    """The second copy's repairs, losses and files it could not copy (retain.py, Part 2 step 4). The current boot's
    only; repairs at two boots running are the card verdict."""
    from irate_box.root import retain

    try:
        items = json.loads((retain.ROOT / "protected-findings.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    current = retain._boot_id()
    items = [i for i in items if isinstance(i, dict) and i.get("path") and i.get("boot") == current]
    repairs = [i for i in items if "restored from" in str(i.get("action"))]
    lost = [i for i in items if "no good copy" in str(i.get("action"))]
    uncopied = [i for i in items if "not copied" in str(i.get("action"))]
    streak = retain.streak()
    housecall = CODE / "irate_box/root/housecall.py"
    notes = []
    if housecall.exists():
        notes.append(
            M("protected.housecall", "The Housecall doctor can examine the whole card and name every file it damaged.")
        )
    if any(str(i.get("path", "")).endswith("accounts.json") for i in repairs + lost):
        notes.append(
            M(
                "protected.accounts",
                "The accounts and their sessions come back as the copy had them. A password or a session changed "
                "since then is the older one.",
            )
        )
    if lost or streak >= 2:
        status = "problem"
        if lost:
            summary = M(
                "protected.lost",
                {
                    "one": "The box could not put {n} damaged file back: there is no good copy.",
                    "other": "The box could not put {n} damaged files back: there is no good copy.",
                },
                n=len(lost),
            )
            why = M(
                "protected.lost.why",
                "The file is part of the box's settings or keys, and the copy is damaged too. The box works with "
                "less until it is put back by hand.",
            )
            fix = M(
                "protected.lost.fix",
                "Put the file back by hand, or install again. Then replace the SD card, and examine it with the "
                "Housecall doctor.",
            )
            evidence = [str(i["path"]) for i in lost]
        else:
            summary = M(
                "protected.twice",
                "The box put damaged files back at {n} boots running: the card is losing writes.",
                n=streak,
            )
            why = M(
                "protected.twice.why",
                "A card that loses writes damages the files the box keeps again and again. A copy can put them "
                "back, but the card will lose the next ones too.",
            )
            fix = M(
                "protected.twice.fix", "Replace the SD card. The Housecall doctor can examine the whole card first."
            )
            evidence = [str(i["path"]) for i in repairs]
    elif repairs:
        status = "warn"
        summary = M(
            "protected.repaired",
            {
                "one": "The box put {n} damaged file back at the last boot.",
                "other": "The box put {n} damaged files back at the last boot.",
            },
            n=len(repairs),
        )
        why = M(
            "protected.repaired.why",
            "The card returned wrong contents for the file, and the box restored it from its copy before the "
            "network and the hub started.",
        )
        fix = M(
            "protected.repaired.fix",
            "Look at what was repaired. A second boot with repairs means the card is losing writes: replace it.",
        )
        evidence = [str(i["path"]) for i in repairs]
    elif uncopied:
        status = "warn"
        summary = M(
            "protected.uncopied",
            {
                "one": "The box found {n} damaged file while copying and did not copy it.",
                "other": "The box found {n} damaged files while copying and did not copy them.",
            },
            n=len(uncopied),
        )
        why = M(
            "protected.uncopied.why",
            "The file read back as 0xFF or zero. Copying it would keep the damage, so the box did not.",
        )
        fix = M(
            "protected.uncopied.fix",
            "Replace the SD card. The Housecall doctor can examine the whole card first.",
        )
        evidence = [str(i["path"]) for i in uncopied]
    else:
        return []
    return [
        finding(
            "protected-files",
            M("protected.title", "Settings and keys restored at boot"),
            status,
            summary,
            why,
            fix,
            notes=notes,
            commands=[f"sudo {CODE}/irate-box housecall --this-card"] if housecall.exists() else [],
            evidence=evidence[:20],
            go=GO_CARD_WRITES,
        )
    ]


def timer_on():
    return (SYSTEMD_UNITS / DOCTOR_TIMER).exists()


def lostwrites_on():
    return (SYSTEMD_UNITS / LOSTWRITES_SERVICE).exists()


def canary_installed():
    return (SYSTEMD_UNITS / CANARY_TIMER).exists()


def _unit(name, text):
    SYSTEMD_UNITS.mkdir(parents=True, exist_ok=True)
    from irate_box.root import safeio

    safeio.write(SYSTEMD_UNITS / name, text)


def unit_text(name):
    """A unit of the Box doctor's own (install.sh writes them, and set_timer/lostwrites/canary here)."""
    from irate_box.root import safeio

    code, state = CODE, STATE
    root_state = safeio.ROOT_STATE
    sandbox = (
        "ProtectSystem=strict\n"
        f"ReadWritePaths={state}/control {root_state}\n"
        "PrivateTmp=yes\nNoNewPrivileges=yes\nProtectHome=read-only\nProtectProc=invisible\n"
    )
    if name == DOCTOR_SERVICE:
        return (
            "[Unit]\nDescription=Irate-Box Box doctor: the box's own checks, its report for /admin\n"
            "After=local-fs.target\n\n[Service]\nType=oneshot\n"
            f"ExecStart={code}/irate-box health run\nNice=19\nIOSchedulingClass=idle\n" + sandbox
        )
    if name == DOCTOR_TIMER:
        return (
            "[Unit]\nDescription=Irate-Box Box doctor, every 6 hours\n\n[Timer]\n"
            "OnBootSec=30min\nOnUnitActiveSec=6h\nRandomizedDelaySec=1h\n\n[Install]\n"
            "WantedBy=timers.target\n"
        )
    if name == LOSTWRITES_SERVICE:
        return (
            "[Unit]\nDescription=Irate-Box: the card's often-written files, read at boot (lost writes)\n"
            # After nand-state.service: on a Lyra with the state on its NAND, the hub's folder is the flash's only
            # once that has run; before it, the card's stale copy beneath would be read and the report hidden.
            # Normal priority: it holds the network and the hub back, and reads only a few small files.
            "DefaultDependencies=no\nAfter=local-fs.target nand-state.service\n"
            "Before=network.target irate-box.service\n\n"
            f"[Service]\nType=oneshot\nExecStart={code}/irate-box health boot-check\n"
            + sandbox
            + "\n[Install]\nWantedBy=multi-user.target\n"
        )
    if name == CANARY_SERVICE:
        return (
            "[Unit]\nDescription=Irate-Box: a test file for lost writes on the SD card\n\n"
            f"[Service]\nType=oneshot\nExecStart={code}/irate-box health canary\nNice=19\n"
            "IOSchedulingClass=idle\nProtectSystem=strict\n"
            f"ReadWritePaths={root_state}\nPrivateTmp=yes\nNoNewPrivileges=yes\nProtectHome=yes\nProtectProc=invisible\n"
        )
    if name == CANARY_TIMER:
        return (
            "[Unit]\nDescription=Irate-Box: the test file for lost writes, every 10 minutes\n\n[Timer]\n"
            "OnBootSec=2min\nOnUnitActiveSec=10min\n\n[Install]\nWantedBy=timers.target\n"
        )
    raise ValueError(f"no unit text for {name}")


def _system(*args):
    r = run("systemctl", *args, timeout=120)
    if r.returncode:
        raise Refused(
            M(
                "health-timer.systemctl",
                "systemctl {args} failed: {detail}",
                args=" ".join(args),
                detail=(r.stderr or r.stdout).strip()[-160:],
            )
        )


def set_timer(on):
    """The Box doctor's timer: written and started, or gone (and nothing of it left)."""
    if on:
        _unit(DOCTOR_SERVICE, unit_text(DOCTOR_SERVICE))
        _unit(DOCTOR_TIMER, unit_text(DOCTOR_TIMER))
        _system("daemon-reload")
        _system("enable", "--now", DOCTOR_TIMER)
        return M("health-timer.set-on", "The Box doctor now checks the box every 6 hours.")
    run("systemctl", "disable", "--now", DOCTOR_TIMER, timeout=120)
    (SYSTEMD_UNITS / DOCTOR_TIMER).unlink(missing_ok=True)
    (SYSTEMD_UNITS / DOCTOR_SERVICE).unlink(missing_ok=True)
    _system("daemon-reload")
    return M("health-timer.set-off", "The Box doctor's timer is gone. It runs when you open its page.")


def set_lostwrites(on):
    """The boot check of the card's often-written files: written and started, or gone."""
    if on:
        _unit(LOSTWRITES_SERVICE, unit_text(LOSTWRITES_SERVICE))
        _system("daemon-reload")
        _system("enable", "--now", LOSTWRITES_SERVICE)
        return M("lost-writes.set-on", "The box now reads its often-written files at every boot.")
    run("systemctl", "disable", "--now", LOSTWRITES_SERVICE, timeout=120)
    (SYSTEMD_UNITS / LOSTWRITES_SERVICE).unlink(missing_ok=True)
    LOST_WRITES_REPORT.unlink(missing_ok=True)
    _system("daemon-reload")
    return M("lost-writes.set-off", "The box no longer checks its often-written files at boot.")


def set_canary(on):
    """The optional test file: its timer written and started, or gone, with the file."""
    if on:
        _unit(CANARY_SERVICE, unit_text(CANARY_SERVICE))
        _unit(CANARY_TIMER, unit_text(CANARY_TIMER))
        _system("daemon-reload")
        _system("enable", "--now", CANARY_TIMER)
        return M("canary.set-on", "The box now writes a 4 KB test file every 10 minutes and checks it at boot.")
    run("systemctl", "disable", "--now", CANARY_TIMER, timeout=120)
    (SYSTEMD_UNITS / CANARY_TIMER).unlink(missing_ok=True)
    (SYSTEMD_UNITS / CANARY_SERVICE).unlink(missing_ok=True)
    _canary_path().unlink(missing_ok=True)
    _system("daemon-reload")
    return M("canary.set-off", "The test file for lost writes is gone.")


def install_units():
    """install.sh: the doctor's timer and its boot check (the recommended set, and switched off only by the owner's
    choice), and the test file where the owner turned it on. A unit the owner turned off is removed, so an update
    does not bring it back."""
    from irate_box.root import safeio

    safeio.mkdir(safeio.ROOT_STATE, mode=0o700)  # the units may write only into it
    chosen = _chosen()
    set_timer(chosen.get("health-timer") != "off")
    set_lostwrites(chosen.get("lost-writes") != "off")
    set_canary(chosen.get("canary") == "on")


def check_card_writes(now=None):
    from irate_box.hub import cardhistory

    now = time.time() if now is None else now
    out = []
    summary = cardhistory.summarize(_card_rows(), now)
    full_days = summary["days"][:-1][-3:]  # the last three whole days
    if len(full_days) == 3:
        per_day = sum(d["card"] for d in full_days) // 3
        top = [f"{u}: {w >> 20} MB" for u, w in summary["week_units"][:3]]
        high = per_day > CARD_HIGH
        out.append(
            finding(
                "card-writes",
                M("card-writes.title", "SD card writes"),
                "warn" if high else "ok",
                M("card-writes.rate", "About {mb} MB a day is written to the SD card.", mb=per_day >> 20),
                M(
                    "card-writes.high.why",
                    "An SD card wears with every write. Above {gb} GB a day, a card can wear out within a year or two.",
                    gb=CARD_HIGH >> 30,
                )
                if high
                else None,
                M(
                    "card-writes.high.fix",
                    "The services that wrote most this week are listed below. "
                    "The settings on Recovery → SD card writes reduce what the box writes.",
                )
                if high
                else None,
                evidence=top,
                notes=_card_life_note(per_day),
                go=GO_CARD_WRITES,
            )
        )
    if not cardhistory.accounting():
        out.append(
            finding(
                "card-accounting",
                M("card-accounting.title", "Writes by service"),
                "warn",
                M("card-accounting.off", "The box does not count the SD card writes of each service."),
                M(
                    "card-accounting.why",
                    "Without these counts, the record of SD card writes cannot say which service writes most.",
                ),
                M(
                    "card-accounting.fix",
                    "Switch on per-service I/O accounting. The kernel then counts each service's writes. "
                    "It does not slow the services down.",
                ),
                actions=[
                    action(
                        "card-accounting-on",
                        M("card-accounting.on", "Count writes by service"),
                        M(
                            "card-accounting.on.confirm",
                            "Switch on per-service I/O accounting? The box adds {file} and reloads systemd. "
                            "No service restarts.",
                            file=str(IO_ACCOUNTING),
                        ),
                    )
                ],
            )
        )
    elif not cardhistory.memory_controller():
        out.append(_memcg_finding())
    found = cardhistory.card()
    out += _service_writes_findings(summary, found[0] if found else None)
    out += _flash_findings()
    out += _recommended_findings()
    for conf in sorted((SYSTEMD_ETC / "journald.conf.d").glob("*.conf")):
        try:
            text = conf.read_text(errors="replace")
        except OSError:
            continue
        m = re.search(r"^\s*SyncIntervalSec\s*=\s*(\S+)", text, re.M)
        secs = _seconds(m[1]) if m else None
        if secs is not None and secs < JOURNAL_SYNC_DEFAULT:
            out.append(
                finding(
                    f"journal-sync:{conf.name}",
                    M("journal-sync.title", "Journal synced often"),
                    "warn",
                    M(
                        "journal-sync.said",
                        "{file} makes the journal sync to the SD card every {value}. journald's default is 5 minutes.",
                        file=str(conf),
                        value=m[1],
                    ),
                    M(
                        "journal-sync.why",
                        "Each sync writes to the SD card. A test setting left in place can sync thousands of times a day.",
                    ),
                    M(
                        "journal-sync.fix",
                        "If nothing on this box needs it, remove the file and restart the journal.",
                    ),
                    commands=[f"sudo rm {conf}", "sudo systemctl restart systemd-journald"],
                    evidence=[str(conf)],
                )
            )
    for arch in _foreign_unused():
        out.append(
            finding(
                f"arch-unused:{arch}",
                M("arch-unused.title", "Package lists for {arch}", arch=arch),
                "warn",
                M(
                    "arch-unused.said",
                    "apt downloads package lists for {arch}, but no package for {arch} is installed.",
                    arch=arch,
                ),
                M(
                    "arch-unused.why",
                    "Each list update and each rebuild of apt's cache handles every package twice, "
                    "and writes both to the SD card.",
                ),
                M(
                    "arch-unused.fix",
                    "Remove the architecture. Add it again with dpkg --add-architecture {arch} "
                    "if a package for it is needed later.",
                    arch=arch,
                ),
                actions=[
                    action(
                        f"arch-remove:{arch}",
                        M("arch-unused.remove", "Remove {arch}", arch=arch),
                        M(
                            "arch-unused.remove.confirm",
                            "Remove the architecture {arch}? apt stops downloading its package lists.",
                            arch=arch,
                        ),
                    )
                ],
            )
        )
    return out


# The system's write settings (Recovery → SD card writes, "System write settings"): each one a file of
# irate-box's, written when an option other than the first is chosen and removed again by the first.
APT_CONF_D = Path(os.environ.get("HUB_APT_CONF_D", "/etc/apt/apt.conf.d"))
APT_CACHE_CONF = APT_CONF_D / "50irate-box-cache"
APT_LANG_CONF = APT_CONF_D / "50irate-box-languages"
APT_RAM = Path("/run/irate-box-apt")
APT_CACHE_DIR = Path(os.environ.get("HUB_APT_CACHE_DIR", "/var/cache/apt"))
APT_RAM_UNIT = "run-irate\\x2dbox\\x2dapt.mount"  # systemd-escape -p --suffix=mount /run/irate-box-apt
JOURNAL_CONF = SYSTEMD_ETC / "journald.conf.d" / "50-irate-box.conf"
SYSCTL_D = Path(os.environ.get("HUB_SYSCTL_D", "/etc/sysctl.d"))
WRITEBACK_CONF = SYSCTL_D / "50-irate-box-writeback.conf"
COMMIT_UNIT = "irate-box-commit.service"
FSTAB = Path(os.environ.get("HUB_FSTAB", "/etc/fstab"))
WRITEBACK = {"vm.dirty_expire_centisecs": (3000, 6000), "vm.dirty_writeback_centisecs": (500, 1500)}
COMMIT_LONGER = 60  # seconds: ext4 commits its journal at most this often with the longer write-back delay


def _apt_languages():
    out = run("apt-config", "dump").stdout
    return re.findall(r'^Acquire::Languages:: "([^"]+)";', out, re.M)


def _journal_storage():
    """Where the journal is kept now: "volatile" (RAM) or "persistent" (the card), as journald reads its settings."""
    if JOURNAL_CONF.exists():
        m = re.search(r"^Storage=(\w+)", JOURNAL_CONF.read_text(), re.M)
        if m:
            return "volatile" if m[1] == "volatile" else "persistent"
    said = run("systemd-analyze", "cat-config", "systemd/journald.conf").stdout
    found = re.findall(r"^\s*Storage\s*=\s*(\w+)", said, re.M)
    storage = found[-1] if found else "auto"
    if storage == "auto":  # persistent when /var/log/journal exists
        return "persistent" if Path("/var/log/journal").exists() else "volatile"
    return "volatile" if storage in ("volatile", "none") else "persistent"


def card_groups():
    """The system's write settings as choice groups, as setting_groups(): the first option is the system's own."""
    from irate_box.hub import cardhistory

    choice = lambda key, value: f"card-set:{key}:{value}"  # noqa: E731
    groups = [
        {
            "key": "apt_cache",
            "label": M("cs.apt-cache", "apt's cache"),
            "desc": M(
                "cs.apt-cache.desc",
                "apt rebuilds its cache whole, about 50 to 150 MB, whenever its package lists change. "
                "In memory, it is never written to the SD card. It uses that much memory until the next restart.",
            ),
            "current": "memory" if APT_CACHE_CONF.exists() else "card",
            "options": [
                {"value": "card", "label": M("cs.on-card", "On the SD card"), "choice": choice("apt_cache", "card")},
                {
                    "value": "memory",
                    "label": M("cs.in-memory", "In memory"),
                    "choice": choice("apt_cache", "memory"),
                    "confirm": M(
                        "cs.apt-cache.confirm",
                        "Keep apt's cache in memory? It uses up to 192 MB of memory, which holds the cache of a "
                        "box with two architectures. apt rebuilds it after each restart, which takes a few seconds.",
                    ),
                },
            ],
        },
        {
            "key": "journal",
            "label": M("cs.journal", "System journal"),
            "desc": M(
                "cs.journal.desc",
                "The system's log. In memory, nothing of it is written to the SD card, but a restart or a crash loses it.",
            ),
            "current": "flash" if NAND_STATE.exists() else "memory" if _journal_storage() == "volatile" else "card",
            "options": [
                {"value": "card", "label": M("cs.on-card", "On the SD card"), "choice": choice("journal", "card")},
                {
                    "value": "memory",
                    "label": M("cs.in-memory", "In memory"),
                    "choice": choice("journal", "memory"),
                    "confirm": M(
                        "cs.journal.confirm",
                        "Keep the system journal in memory (up to 24 MB)? A restart or a crash loses it, "
                        "and with it the log of what led up to a crash.",
                    ),
                },
                *(
                    [
                        {
                            "value": "flash",
                            "label": M("cs.on-flash", "On the onboard flash"),
                            "choice": choice("journal", "flash"),
                            "confirm": M(
                                "cs.journal.flash.confirm",
                                "Keep the system journal, the other logs and crash watch's records on the onboard "
                                "flash? The box formats the flash first, which erases what is on it.",
                            ),
                        }
                    ]
                    if _nand()[0]
                    else []
                ),
            ],
        },
        *(
            [
                {
                    "key": "box_state",
                    "label": M("cs.box-state", "Often-written state"),
                    "desc": M(
                        "cs.box-state.desc",
                        "The hub's settings and records, and the system's own (the network's, the timers', the "
                        "clock's): on the SD card, or on the onboard flash with the logs. Books, firmware, kits and "
                        "uploads stay on the card either way.",
                    ),
                    "current": "flash" if _box_state_on() else "card",
                    "options": [
                        {
                            "value": "card",
                            "label": M("cs.on-card", "On the SD card"),
                            "choice": choice("box_state", "card"),
                        },
                        {
                            "value": "flash",
                            "label": M("cs.on-flash", "On the onboard flash"),
                            "choice": choice("box_state", "flash"),
                            "confirm": M(
                                "cs.box-state.flash.confirm",
                                "Keep the box's often-written state on the onboard flash? The SD card is then written "
                                "only when content is added or the box updates. The hub stops for a moment while its "
                                "state moves.",
                            ),
                        },
                    ],
                }
            ]
            if NAND_STATE.exists()
            else []
        ),
        {
            "key": "writeback",
            "label": M("cs.writeback", "Write-back delay"),
            "desc": M(
                "cs.writeback.desc",
                "How long changes wait in memory before they go to the SD card. A longer delay lets repeated "
                "changes to a file reach the card once.",
            ),
            "current": "longer" if WRITEBACK_CONF.exists() else "standard",
            "options": [
                {
                    "value": "standard",
                    "label": M("cs.writeback.standard", "Standard (30 s)"),
                    "choice": choice("writeback", "standard"),
                },
                {
                    "value": "longer",
                    "label": M("cs.writeback.longer", "Longer (60 s)"),
                    "choice": choice("writeback", "longer"),
                    "confirm": M(
                        "cs.writeback.confirm",
                        "Use the longer write-back delay? A power cut can then lose up to a minute of changes "
                        "that no program saved explicitly.",
                    ),
                },
            ],
        },
    ]
    if NAND_STATE.exists():
        groups.append(
            {
                "key": "flash_crc",
                "label": M("cs.flash-crc", "Check the onboard flash's data on read"),
                "desc": M(
                    "cs.flash-crc.desc",
                    "UBIFS can check each piece of data it reads against its checksum. A damaged read is then an "
                    "error, not quietly used data. It costs some processor time on each read from the flash.",
                ),
                "current": "on" if nand_crc_on() else "off",
                "options": [
                    {
                        "value": "on",
                        "label": M("cs.flash-crc.on", "Checked"),
                        "choice": choice("flash_crc", "on"),
                    },
                    {
                        "value": "off",
                        "label": M("cs.flash-crc.off", "Not checked"),
                        "choice": choice("flash_crc", "off"),
                    },
                ],
            }
        )
    languages = _apt_languages()
    if APT_LANG_CONF.exists() or [lang for lang in languages if lang != "none"]:
        groups.insert(
            1,
            {
                "key": "apt_languages",
                "label": M("cs.languages", "Package descriptions"),
                "desc": M(
                    "cs.languages.desc",
                    "The descriptions that apt show prints, downloaded with the package lists ({mb} MB or more "
                    "on Debian) and kept in apt's cache.",
                    mb=30,
                ),
                "current": "none" if APT_LANG_CONF.exists() else "download",
                "options": [
                    {
                        "value": "download",
                        "label": M("cs.languages.download", "Downloaded"),
                        "choice": choice("apt_languages", "download"),
                    },
                    {
                        "value": "none",
                        "label": M("cs.languages.none", "Not downloaded"),
                        "choice": choice("apt_languages", "none"),
                    },
                ],
            },
        )
    groups.append(
        {
            "key": "card_accounting",
            "label": M("cs.accounting", "Writes by service"),
            "desc": M(
                "cs.accounting.desc",
                "Whether the kernel counts the SD card writes of each service, so the record can say which one "
                "writes most. It does not slow the services down.",
            ),
            "current": "on" if cardhistory.accounting() else "off",
            "options": [
                {
                    "value": "on",
                    "label": M("cs.accounting.on", "Counted"),
                    "choice": choice("card_accounting", "on"),
                },
                {
                    "value": "off",
                    "label": M("cs.accounting.off", "Not counted"),
                    "choice": choice("card_accounting", "off"),
                },
            ],
        }
    )
    return groups


def hardening_groups():
    """The invasive hardening choices (root/hardening.py), drawn beside the write settings."""
    from irate_box.root import hardening

    return hardening.groups()


def check_hardening():
    """The filesystem and A/B choices recorded, and how far the A/B boot files are ready."""
    from irate_box.root import hardening

    return hardening.findings()


def _systemctl(*args):
    r = run("systemctl", *args, timeout=120)
    if r.returncode:
        raise Refused(
            M(
                "repair.card-set.systemctl",
                "systemctl {args} failed: {detail}",
                args=" ".join(args),
                detail=r.stderr.strip()[-160:],
            )
        )


def _root_commit():
    """The commit= of / in fstab, or ext4's default of 5 seconds."""
    for line in FSTAB.read_text(errors="replace").splitlines():
        f = line.split()
        if len(f) >= 4 and not f[0].startswith("#") and f[1] == "/":
            m = re.search(r"(?:^|,)commit=(\d+)", f[3])
            return int(m[1]) if m else 5
    return 5


def card_set(key, value):
    """One system write setting applied, now and for every boot."""
    unit_dir = SYSTEMD_ETC / "system"
    if key == "card_accounting":
        return card_accounting_on() if value == "on" else card_accounting_off()
    if key == "apt_cache" and value == "memory":
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / APT_RAM_UNIT).write_text(
            "# irate-box (SD card writes): RAM for apt's cache, so it is never written to the card.\n"
            "[Unit]\nDescription=RAM for apt's cache (irate-box)\n\n"
            f"[Mount]\nWhat=tmpfs\nWhere={APT_RAM}\nType=tmpfs\nOptions=mode=0755,nosuid,nodev,noexec,size=192M\n\n"
            "[Install]\nWantedBy=local-fs.target\n"
        )
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", APT_RAM_UNIT)
        APT_CACHE_CONF.write_text(
            "// irate-box (SD card writes): apt's cache in RAM, never written to the card. Remove to undo.\n"
            f'Dir::Cache::pkgcache "{APT_RAM}/pkgcache.bin";\nDir::Cache::srcpkgcache "";\n'
        )
        for old in ("pkgcache.bin", "srcpkgcache.bin"):
            (APT_CACHE_DIR / old).unlink(missing_ok=True)
        return M("repair.card-set.apt-memory", "apt's cache is now kept in memory.")
    if key == "apt_cache" and value == "card":
        APT_CACHE_CONF.unlink(missing_ok=True)
        if (unit_dir / APT_RAM_UNIT).exists():
            run("systemctl", "disable", "--now", APT_RAM_UNIT, timeout=120)
            (unit_dir / APT_RAM_UNIT).unlink()
            _systemctl("daemon-reload")
        return M("repair.card-set.apt-card", "apt's cache is kept on the SD card again.")
    if key == "apt_languages":
        if value == "none":
            APT_LANG_CONF.write_text(
                "// irate-box (SD card writes): no package descriptions downloaded. Remove to undo.\n"
                'Acquire::Languages "none";\n'
            )
            return M(
                "repair.card-set.languages-none",
                "apt no longer downloads package descriptions. It removes those it has at its next update.",
            )
        APT_LANG_CONF.unlink(missing_ok=True)
        return M("repair.card-set.languages-download", "apt downloads package descriptions again.")
    if key == "journal" and value == "flash":
        JOURNAL_CONF.unlink(missing_ok=True)  # nand-logs sets the journal's own storage
        if not NAND_STATE.exists():
            if _nand()[0] != "free":
                raise Refused(M("repair.card-flash.none", "This box has no free onboard flash."))
            _nand_tool("on")
        return M("repair.card-set.journal-flash", "The logs and crash watch's records are now on the onboard flash.")
    if key == "journal":
        if NAND_STATE.exists():
            _nand_tool("off")
        JOURNAL_CONF.parent.mkdir(parents=True, exist_ok=True)
        JOURNAL_CONF.write_text(
            "# irate-box (SD card writes): where the system journal is kept. Remove to undo.\n[Journal]\n"
            + ("Storage=volatile\nRuntimeMaxUse=24M\n" if value == "memory" else "Storage=persistent\n")
        )
        _systemctl("restart", "systemd-journald")
        return (
            M("repair.card-set.journal-memory", "The system journal is now kept in memory.")
            if value == "memory"
            else M("repair.card-set.journal-card", "The system journal is now kept on the SD card.")
        )
    if key == "box_state":
        if value == "flash":
            if not NAND_STATE.exists():
                raise Refused(
                    M("repair.card-state.no-logs", "Put the logs on the onboard flash first (System journal).")
                )
            _nand_tool("state", "on")
            return M(
                "repair.card-set.state-flash",
                "The box's often-written state is on the onboard flash now: the SD card is written when content is "
                "added or the box updates.",
            )
        if _box_state_on():
            _nand_tool("state", "off")
        return M("repair.card-set.state-card", "The box's often-written state is on the SD card again.")
    if key == "flash_crc":
        if not NAND_STATE.exists():
            raise Refused(M("repair.card-crc.none", "The onboard flash is not in use on this box."))
        _nand_tool("crc", value)
        return (
            M("repair.card-set.crc-on", "UBIFS now checks the flash's data on each read.")
            if value == "on"
            else M("repair.card-set.crc-off", "UBIFS no longer checks the flash's data on each read.")
        )
    if key == "filesystem" or key == "ab":
        from irate_box.root import hardening

        return hardening.set_filesystem(value) if key == "filesystem" else hardening.set_ab(value)
    if key == "writeback":
        longer = value == "longer"
        if longer:
            SYSCTL_D.mkdir(parents=True, exist_ok=True)
            WRITEBACK_CONF.write_text(
                "# irate-box (SD card writes): changes wait longer in memory before they go to the card. Remove to undo.\n"
                + "".join(f"{k} = {v[1]}\n" for k, v in WRITEBACK.items())
            )
        else:
            WRITEBACK_CONF.unlink(missing_ok=True)
        for k, v in WRITEBACK.items():
            run("sysctl", "-q", "-w", f"{k}={v[1] if longer else v[0]}")
        commit = max(_root_commit(), COMMIT_LONGER) if longer else _root_commit()
        unit = SYSTEMD_ETC / "system" / COMMIT_UNIT
        if longer and commit > _root_commit():
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(
                "# irate-box (SD card writes): the root filesystem's journal committed at most every "
                f"{commit} s.\n[Unit]\nDescription=Longer write-back delay for / (irate-box)\n"
                "After=local-fs.target\n\n[Service]\nType=oneshot\n"
                f"ExecStart=/bin/mount -o remount,commit={commit} /\n\n[Install]\nWantedBy=multi-user.target\n"
            )
            _systemctl("daemon-reload")
            _systemctl("enable", COMMIT_UNIT)
        elif unit.exists():
            run("systemctl", "disable", COMMIT_UNIT, timeout=120)
            unit.unlink()
            _systemctl("daemon-reload")
        run("mount", "-o", f"remount,commit={commit}", "/", timeout=60)
        return (
            M("repair.card-set.writeback-longer", "Changes now wait up to a minute before they go to the SD card.")
            if longer
            else M("repair.card-set.writeback-standard", "The standard write-back delay applies again.")
        )
    raise Refused(M("repair.card-set.unknown", "{key} is not a write setting of the box.", key=key))


# Raspberry Pi kernels leave the memory controller off; without it a service's buffered writes count as the kernel's.
RPI_CMDLINE = Path(os.environ.get("HUB_RPI_CMDLINE", "/boot/firmware/cmdline.txt"))
MEMCG = "cgroup_enable=memory"


def _memcg_finding():
    pending = RPI_CMDLINE.exists() and MEMCG in RPI_CMDLINE.read_text().split()
    return finding(
        "card-memcg",
        M("card-accounting.title", "Writes by service"),
        "warn",
        M("card-memcg.pending", "The kernel counts each service's SD card writes after the next restart.")
        if pending
        else M("card-memcg.off", "The kernel counts most SD card writes as its own, not as a service's."),
        M(
            "card-memcg.why",
            "Without the kernel's memory controller, the writes a service leaves to the kernel are not counted "
            "as the service's, so the record cannot say which service writes most.",
        ),
        None
        if pending
        else M(
            "card-memcg.fix.rpi",
            "Switch on the memory controller. It takes effect at the next restart and uses a little memory.",
        )
        if RPI_CMDLINE.exists()
        else M(
            "card-memcg.fix",
            "Add {option} to the kernel's command line in this board's boot configuration, then restart.",
            option=MEMCG,
        ),
        actions=[]
        if pending or not RPI_CMDLINE.exists()
        else [
            action(
                "card-memcg-on",
                M("card-memcg.on", "Switch on the memory controller"),
                M(
                    "card-memcg.on.confirm",
                    "Add {option} to {file}? It takes effect at the next restart.",
                    option=MEMCG,
                    file=str(RPI_CMDLINE),
                ),
            )
        ],
    )


def card_memcg_on():
    text = RPI_CMDLINE.read_text()
    if MEMCG not in text.split():
        lines = text.split("\n")
        lines[0] = lines[0].rstrip() + f" {MEMCG}"
        tmp = RPI_CMDLINE.with_name(RPI_CMDLINE.name + ".irate-box.tmp")
        tmp.write_text("\n".join(lines))
        os.replace(tmp, RPI_CMDLINE)
    return M("repair.card-memcg.on", "The kernel counts each service's writes after the next restart.")


# The onboard raw NAND flash (tools/nand-logs.sh): free, it can hold the logs; in use, its wear as UBI counts it.
NAND_TOOL = CODE / "tools" / "nand-logs.sh"
NAND_STATE = Path(os.environ.get("HUB_NAND_STATE", "/etc/nand-logs.conf"))
PROC_MTD = Path(os.environ.get("HUB_PROC_MTD", "/proc/mtd"))
UBI_SYS = Path(os.environ.get("HUB_UBI_SYS", "/sys/class/ubi"))
MTD_SYS = Path(os.environ.get("HUB_MTD_SYS", "/sys/class/mtd"))
# Raw NAND chips the box knows, by maker (the kernel's SPI NAND probe line) and geometry (MTD's sysfs: size, erase
# block, page, spare bytes), with their rated program/erase cycles from their datasheets.
FLASH_PARTS = {
    ("winbond", 256 << 20, 128 << 10, 2048, 128): {
        "part": "W25N02KV",
        "cycles": 60000,
        "also": "100,000 if it is the 1.8 V W25N02KW",
        "source": "Winbond W25N02KVxxIR/U datasheet, revision G (2021-06-25): 60,000 erase/program cycles, 10-year retention",
    },
}
FLASH_WARN_SHARE = 0.5  # past this share of the rated cycles, the flash is a warning


def _box_state_on():
    """Whether nand-logs keeps the box's often-written state on the flash (its "state on")."""
    try:
        return "state=1" in NAND_STATE.read_text().split()
    except OSError:
        return False


def _nand():
    """("on", None) when nand-logs keeps the logs on the flash, ("free", MiB) when a flash is free, else (None, None)."""
    if NAND_STATE.exists():
        return "on", None
    if not NAND_TOOL.exists() or not PROC_MTD.exists():
        return None, None
    said = run("bash", "-p", str(NAND_TOOL), "check", timeout=60).stdout
    m = re.search(r'^mtd\d+ "[^"]*": (\d+) MiB raw NAND', said, re.M)
    return ("free", int(m[1])) if m and re.search(r"^\s+free\.", said, re.M) else (None, None)


def _flash_part():
    """The chip under the flash nand-logs uses: {part, cycles, also, source} from FLASH_PARTS, or None."""
    try:
        n = next(line.split("=", 1)[1] for line in NAND_STATE.read_text().split() if line.startswith("mtd="))
        mtd = MTD_SYS / f"mtd{int(n)}"
        geometry = tuple(int((mtd / f).read_text()) for f in ("size", "erasesize", "writesize", "oobsize"))
    except (OSError, ValueError, StopIteration):
        return None
    probe = run(
        "journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat", "--grep", "SPI NAND was found", timeout=30
    ).stdout
    maker = re.search(r"(\w+) SPI NAND was found", probe or "")
    if not maker:
        return None
    return FLASH_PARTS.get((maker[1].lower(), *geometry))


def _flash_rate(now=None):
    """Erases a day of the flash's most-worn block, from the record's first and last counts a day or more apart."""
    rows = [r for r in _card_rows() if isinstance(r.get("flash"), dict) and isinstance(r["flash"].get("max_ec"), int)]
    if len(rows) < 2 or rows[-1]["at"] - rows[0]["at"] < 86400:
        return None, None
    first, last = rows[0], rows[-1]
    days = (last["at"] - first["at"]) / 86400
    return (last["flash"]["max_ec"] - first["flash"]["max_ec"]) / days, last["flash"].get("bad", 0) - first[
        "flash"
    ].get("bad", 0)


def nand_crc_on():
    """Whether the UBIFS volume is mounted with chk_data_crc (nand-logs' setting): data nodes checked on read."""
    try:
        return "crc=1" in NAND_STATE.read_text().split()
    except OSError:
        return False


def _nand_mtd():
    """The MTD number nand-logs uses, from its state file, or None."""
    try:
        return int(next(line.split("=", 1)[1] for line in NAND_STATE.read_text().split() if line.startswith("mtd=")))
    except (OSError, ValueError, StopIteration):
        return None


def _nand_metrics():
    """The chip's own counters and UBI's, or None: {mtd, corrected, ecc_failures, max_ec, bad}."""
    n = _nand_mtd()
    if n is None:
        return None
    mtd, ubi = MTD_SYS / f"mtd{n}", None
    for candidate in sorted(UBI_SYS.glob("ubi[0-9]*")):
        try:
            if (candidate / "mtd_num").read_text().strip() == str(n):
                ubi = candidate
                break
        except OSError:
            continue
    out = {"mtd": n}
    for key, path in (
        ("corrected", mtd / "corrected_bits"),
        ("ecc_failures", mtd / "ecc_failures"),
        ("max_ec", (ubi / "max_ec") if ubi else None),
        ("bad", (ubi / "bad_peb_count") if ubi else None),
    ):
        try:
            out[key] = int(path.read_text())
        except (AttributeError, OSError, ValueError):
            out[key] = None
    return out


NAND_HEALTH = "nand-health.json"


def _nand_previous():
    from irate_box.root import safeio

    try:
        data = json.loads((safeio.ROOT_STATE / NAND_HEALTH).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _nand_remember(metrics):
    from irate_box.root import safeio

    try:
        safeio.mkdir(safeio.ROOT_STATE, mode=0o700)
        safeio.write(safeio.ROOT_STATE / NAND_HEALTH, json.dumps(metrics, indent=1, sort_keys=True))
    except OSError:
        pass


def _nand_crc_errors():
    """The kernel log's UBIFS data checksum errors this boot, the last few lines (a read of the flash's data that
    UBIFS refused: chk_data_crc must be on for these)."""
    said = run("journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat", "--grep", "UBIFS", timeout=30).stdout
    return [line.strip()[:200] for line in said.splitlines() if "CRC" in line][-3:]


def _flash_findings():
    state, mib = _nand()
    if state == "free":
        return [
            finding(
                "card-flash",
                M("card-flash.title", "Onboard flash"),
                "warn",
                M(
                    "card-flash.free",
                    "This box has {mb} MB of onboard flash that nothing uses. It can hold the logs instead of the SD card.",
                    mb=mib,
                ),
                M(
                    "card-flash.why",
                    "The flash takes many more writes than an SD card, and the box spreads them over all of it.",
                ),
                M(
                    "card-flash.fix",
                    "On Recovery → SD card writes, set System journal to On the onboard flash. This formats the flash.",
                ),
                go=GO_CARD_WRITES,
            )
        ]
    if state == "on":
        ubi = next((u for u in sorted(UBI_SYS.glob("ubi[0-9]*")) if (u / "max_ec").exists()), None)
        try:
            max_ec, bad = int((ubi / "max_ec").read_text()), int((ubi / "bad_peb_count").read_text())
        except (OSError, ValueError, TypeError):
            max_ec = bad = None
        part = _flash_part()
        rate, bad_grew = _flash_rate()
        notes, evidence, status = [], [], "ok"
        if part and max_ec is not None:
            evidence.append(part["source"])
            share = max_ec / part["cycles"]
            if rate:
                years = (part["cycles"] - max_ec) / rate / 365
                notes.append(
                    M(
                        "card-flash.life",
                        "It is rated for {cycles} erases a block ({part}, or {also}). At {rate} a day, about {years} years "
                        "are left.",
                        cycles=part["cycles"],
                        part=part["part"],
                        also=part["also"],
                        rate=f"{rate:.1f}",
                        years=f"{years:.0f}",
                    )
                )
            else:
                notes.append(
                    M(
                        "card-flash.life.counting",
                        "It is rated for {cycles} erases a block ({part}, or {also}). Its rate shows once a day of counts "
                        "is recorded.",
                        cycles=part["cycles"],
                        part=part["part"],
                        also=part["also"],
                    )
                )
            if share > FLASH_WARN_SHARE:
                status = "warn"
        if bad_grew:
            status = "warn"
            notes.append(M("card-flash.bad-grew", "Its bad blocks grew by {n} in the record's days.", n=bad_grew))
        metrics = _nand_metrics()
        if metrics:
            previous = _nand_previous()
            corrected, ecc = metrics.get("corrected"), metrics.get("ecc_failures")
            was = previous.get("corrected")
            if corrected is not None:
                evidence.append(f"mtd{metrics['mtd']}: {corrected} corrected bit(s)")
                if isinstance(was, int) and corrected > was:
                    status = "warn"
                    notes.append(
                        M(
                            "card-flash.corrected-grew",
                            "The bits the chip has had to correct grew by {n} since the last check.",
                            n=corrected - was,
                        )
                    )
            if ecc:
                status = "problem"
                notes.append(
                    M(
                        "card-flash.ecc",
                        "The flash had {n} read(s) whose errors the chip could not correct. Data on it can be lost.",
                        n=ecc,
                    )
                )
            bad_before = previous.get("bad")
            if bad is not None and isinstance(bad_before, int) and bad > bad_before:
                status = "warn"
                notes.append(
                    M(
                        "card-flash.bad-grew-check",
                        "Its bad blocks grew by {n} since the last check.",
                        n=bad - bad_before,
                    )
                )
            crc = _nand_crc_errors()
            if crc:
                status = "problem"
                notes.append(
                    M(
                        "card-flash.crc",
                        "The kernel's log has UBIFS data checksum errors this boot: data on the flash was read "
                        "wrong. It was caught, not silently used.",
                    )
                )
                evidence += crc
            _nand_remember(metrics)
        if NAND_STATE.exists() and not nand_crc_on():
            notes.append(
                M(
                    "card-flash.crc-off",
                    "UBIFS does not check its data on read. Switch on Check the onboard flash's data on read.",
                )
            )
        return [
            finding(
                "card-flash",
                M("card-flash.title", "Onboard flash"),
                status,
                M(
                    "card-flash.on",
                    {
                        "one": "The logs are on the onboard flash. Its most-erased block has been erased once. "
                        "Bad blocks: {bad}.",
                        "other": "The logs are on the onboard flash. Its most-erased block has been erased {n} times. "
                        "Bad blocks: {bad}.",
                    },
                    n=max_ec,
                    bad=bad,
                )
                if max_ec is not None
                else M("card-flash.on.no-counts", "The logs are on the onboard flash."),
                notes=notes,
                evidence=evidence,
                actions=[
                    action("flash-read", M("card-flash.read", "Read the whole flash (a test that writes nothing)"))
                ],
                go=GO_CARD_WRITES,
            )
        ]
    return []


def _nand_tool(*verb):
    r = run("bash", "-p", str(NAND_TOOL), *verb, timeout=900)
    if r.returncode:
        raise Refused(
            M(
                "repair.card-flash.failed",
                "nand-logs.sh {verb} failed: {detail}",
                verb=" ".join(verb),
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )


def _io_accounting_configured():
    """Whether a systemd configuration file other than irate-box's asks for per-service I/O accounting: the owner's
    own drop-in, or the image's. The last DefaultIOAccounting= wins, as systemd reads them."""
    files = [SYSTEMD_ETC / "system.conf", *sorted((SYSTEMD_ETC / "system.conf.d").glob("*.conf"))]
    on = False
    for path in files:
        if path == IO_ACCOUNTING:
            continue
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            key, _, value = line.strip().partition("=")
            if key.strip() == "DefaultIOAccounting":
                on = value.strip().lower() == "yes"
    return on


def card_accounting_on():
    """Per-service I/O accounting: systemd's default, in a file of irate-box's, applied to running services too.
    The setting before irate-box's file is recorded, so off can leave the owner's own choice alone."""
    IO_ACCOUNTING.parent.mkdir(parents=True, exist_ok=True)
    if not IO_ACCOUNTING_STATE.exists():
        IO_ACCOUNTING_STATE.write_text(json.dumps({"before": "on" if _io_accounting_configured() else "off"}) + "\n")
    IO_ACCOUNTING.write_text(
        "# irate-box (Box doctor, SD card writes): per-service I/O accounting, so the box can say which service\n"
        "# writes to the card. Remove this file and run systemctl daemon-reexec to undo.\n"
        "[Manager]\nDefaultIOAccounting=yes\n"
    )
    # daemon-reload reads the manager's settings; daemon-reexec applies them to the services already running.
    for verb in ("daemon-reload", "daemon-reexec"):
        r = run("systemctl", verb, timeout=120)
        if r.returncode:
            raise Refused(M("repair.card-accounting.failed", "systemctl {verb} failed.", verb=verb))
    return M("repair.card-accounting.on", "The box now counts each service's SD card writes.")


def card_accounting_off():
    """Remove irate-box's drop-in only; an administrator's own setting is left as it was."""
    try:
        before = json.loads(IO_ACCOUNTING_STATE.read_text()).get("before")
    except (OSError, ValueError):
        before = "off"
    IO_ACCOUNTING.unlink(missing_ok=True)
    for verb in ("daemon-reload", "daemon-reexec"):
        r = run("systemctl", verb, timeout=120)
        if r.returncode:
            raise Refused(M("repair.card-accounting.failed", "systemctl {verb} failed.", verb=verb))
    IO_ACCOUNTING_STATE.unlink(missing_ok=True)
    if _io_accounting_configured():
        return M(
            "repair.card-accounting.off.own",
            "irate-box stopped asking to count each service's writes. Another setting still keeps it on.",
        )
    if before == "on":
        return M(
            "repair.card-accounting.off.before",
            "irate-box stopped asking to count each service's writes. The setting that had it on before is gone.",
        )
    return M("repair.card-accounting.off", "The box no longer counts each service's SD card writes.")


def arch_remove(arch):
    if arch not in _foreign_unused():
        raise Refused(
            M("repair.arch.not-unused", "{arch} is not an added architecture with nothing installed.", arch=arch)
        )
    r = run("dpkg", "--remove-architecture", arch, timeout=60)
    if r.returncode:
        raise Refused(
            M("repair.arch.failed", "dpkg could not remove {arch}: {detail}", arch=arch, detail=r.stderr.strip()[-160:])
        )
    return M("repair.arch.removed", "The box no longer downloads package lists for {arch}.", arch=arch)


# SD card health. Consumer SD cards report no health of their own (only some industrial ones answer the vendor
# command CMD56), so the check reads what every card and Linux keep: the card's identity and age, what has been
# written to its filesystems over their life (ext4's lifetime_write_kbytes), the kernel's I/O errors for it,
# ext4's error count, a root filesystem fallen back to read-only, and on eMMC the device's own wear estimate. Two
# tests the owner starts, both read-only: a quick read-speed test (kept, so a card that slows down shows), and a
# whole-card read in a unit of its own, which finds the places that can no longer be read.
SD_MAKERS = {
    0x01: "Panasonic",
    0x02: "Kioxia (Toshiba)",
    0x03: "SanDisk",
    0x1B: "Samsung",
    0x1D: "ADATA",
    0x27: "Phison",
    0x28: "Lexar",
    0x31: "Silicon Power",
    0x41: "Kingston",
    0x74: "Transcend",
    0x76: "Patriot",
    0x82: "Sony",
}
CARD_ERROR_RE = re.compile(
    r"(I/O error, dev {dev}|Buffer I/O error on dev {dev}|{host}: .*(timeout|error|CRC|reset)|EXT4-fs error \(device {dev})"
)
CARD_SCAN_UNIT = "irate-box-card-scan"
CARD_SPEED_MB = 256  # read by the speed test, from the start of the card


def _card_health_file():
    from irate_box.root import safeio

    return safeio.ROOT_STATE / "card-health.json"


def _card_tests():
    try:
        return json.loads(_card_health_file().read_text())
    except (OSError, ValueError):
        return {}


def _card_tests_save(rec):
    path = _card_health_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, path)


def _sysfs(path, default=""):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def card_info():
    """The card under / as Linux sees it: {dev, type, maker, name, date, size, written, errors, host, emmc: {...}}."""
    from irate_box.hub import cardhistory

    found = cardhistory.card()
    if not found:
        return None
    dev = found[0]
    block = cardhistory.SYS / "class" / "block" / dev
    device = block / "device"
    try:
        manfid = int(_sysfs(device / "manfid", "-1"), 16)
    except ValueError:
        manfid = -1
    written = 0
    for part in cardhistory.SYS.glob(f"fs/ext4/{dev}*"):
        try:
            written += int(_sysfs(part / "lifetime_write_kbytes", "0")) * 1024
        except ValueError:
            pass
    ext4_errors = 0
    for part in cardhistory.SYS.glob(f"fs/ext4/{dev}*"):
        try:
            ext4_errors += int(_sysfs(part / "errors_count", "0"))
        except ValueError:
            pass
    host = Path(os.path.realpath(device)).parent.name.split(":")[0] if device.exists() else ""
    return {
        "dev": dev,
        "type": _sysfs(device / "type"),
        "manfid": manfid,
        "maker": SD_MAKERS.get(manfid),
        "oemid": _sysfs(device / "oemid"),
        "name": _sysfs(device / "name"),
        "date": _sysfs(device / "date"),
        "size": cardhistory.size(dev),
        "written": written,
        "ext4_errors": ext4_errors,
        "host": host,
        "life_time": _sysfs(device / "life_time"),
        "pre_eol": _sysfs(device / "pre_eol_info"),
    }


def _card_kernel_errors(info):
    said = run("journalctl", "-k", "-b", "--no-pager", "-o", "cat", timeout=60).stdout
    pattern = re.compile(
        CARD_ERROR_RE.pattern.format(dev=re.escape(info["dev"]), host=re.escape(info["host"] or "mmc-")), re.I
    )
    return [line.strip()[:200] for line in said.splitlines() if pattern.search(line)]


def _root_read_only():
    for line in (PROC / "mounts").read_text().splitlines() if (PROC / "mounts").exists() else []:
        f = line.split()
        if len(f) >= 4 and f[1] == "/":
            return "ro" in f[3].split(",")
    return False


def check_card_health():
    info = card_info()
    if not info or info["type"] not in ("SD", "MMC"):
        return []
    tests = _card_tests()
    errors = _card_kernel_errors(info)
    read_only = _root_read_only()
    times = info["written"] / info["size"] if info["size"] else None
    maker = info["maker"] or f"maker ID 0x{info['manfid'] & 0xFF:02x}"
    evidence = [
        f"{info['type']} {info['name']}, {info['size'] // 10**9 if info['size'] else '?'} GB, made {info['date'] or '?'}",
        f"written over its life: {info['written'] >> 30} GB"
        + (f" ({times:.1f} times its size)" if times is not None else ""),
    ]
    speeds = tests.get("speed") or []
    if speeds:
        evidence.append(
            f"read speed: {speeds[-1]['mbps']} MB/s on {time.strftime('%Y-%m-%d', time.localtime(speeds[-1]['at']))}"
            + (f" (first test: {speeds[0]['mbps']} MB/s)" if len(speeds) > 1 else "")
        )
    scan = tests.get("scan")
    if scan and scan.get("done"):
        evidence.append(
            f"whole-card read on {time.strftime('%Y-%m-%d', time.localtime(scan['done']))}: "
            + (f"{scan['bad']} unreadable places" if scan.get("bad") else "every part read")
        )
    evidence += errors[-3:]
    emmc_worn = info["type"] == "MMC" and (
        info["pre_eol"] in ("0x02", "0x03") or any(v in ("0x09", "0x0a", "0x0b") for v in info["life_time"].split())
    )
    bad = bool(errors) or read_only or bool(scan and scan.get("bad")) or emmc_worn
    status = "problem" if bad else "warn" if info["ext4_errors"] or info["manfid"] in (0, -1) else "ok"
    summary = (
        M(
            "card-health.read-only",
            "The system's filesystem has fallen back to read-only. That is often a sign of a failing card.",
        )
        if read_only
        else M(
            "card-health.unreadable",
            "The whole-card read found {n} places that can no longer be read.",
            n=scan["bad"],
        )
        if scan and scan.get("bad")
        else M(
            "card-health.errors", "The kernel reported {n} errors from the card since the box started.", n=len(errors)
        )
        if errors
        else M("card-health.emmc-worn", "The storage reports that it is near the end of its life.")
        if emmc_worn
        else M(
            "card-health.ext4-errors",
            "The filesystem on the card has recorded {n} errors.",
            n=info["ext4_errors"],
        )
        if info["ext4_errors"]
        else M(
            "card-health.counterfeit",
            "The card has no maker's ID. Counterfeit cards often have none, and can hold less than they say.",
        )
        if info["manfid"] in (0, -1)
        else M(
            "card-health.fine",
            "{maker} card. No errors. It does not report its own wear: the record shows what is written to it.",
            maker=maker,
        )
    )
    running = run("systemctl", "is-active", CARD_SCAN_UNIT).stdout.strip() == "active"
    notes = []
    if running:
        notes.append(
            M(
                "card-health.scan-running",
                "The whole-card read is running (started {at}).",
                at=when((scan or {}).get("started", 0)),
            )
        )
    actions = [
        action(
            "card-speed-test",
            M("card-health.speed", "Test the read speed"),
            M(
                "card-health.speed.confirm",
                "Read {mb} MB from the card to measure its speed? It takes a few seconds and writes nothing.",
                mb=CARD_SPEED_MB,
            ),
        )
    ]
    if not running:
        actions.append(
            action(
                "card-scan",
                M("card-health.scan", "Read the whole card"),
                M(
                    "card-health.scan.confirm",
                    "Read every part of the card to find places that can no longer be read? It writes nothing, "
                    "but it takes from a quarter of an hour to an hour or more, and slows the box while it runs.",
                ),
            )
        )
    return [
        finding(
            "card-health",
            M("card-health.title", "SD card health"),
            status,
            summary,
            M(
                "card-health.why",
                "A failing card loses data and can stop the box from starting. Errors and unreadable places are "
                "the signs that show before it fails.",
            )
            if status != "ok"
            else None,
            M(
                "card-health.fix",
                "Copy what you need off the box, and move the system to a new card soon. "
                "A high-endurance card lasts longer.",
            )
            if status == "problem"
            else None,
            notes=notes,
            evidence=evidence,
            actions=actions,
        )
    ]


def card_speed_test():
    info = card_info()
    if not info:
        raise Refused(M("repair.card-speed.no-card", "The box could not find the card it starts from."))
    start = time.monotonic()
    r = run(
        "dd",
        f"if=/dev/{info['dev']}",
        "of=/dev/null",
        "bs=4M",
        f"count={CARD_SPEED_MB // 4}",
        "iflag=direct",
        "status=none",
        timeout=600,
    )
    secs = max(time.monotonic() - start, 0.001)
    if r.returncode:
        raise Refused(
            M("repair.card-speed.failed", "The card could not be read: {detail}", detail=r.stderr.strip()[-200:])
        )
    mbps = round(CARD_SPEED_MB / secs, 1)
    rec = _card_tests()
    rec["speed"] = [*(rec.get("speed") or [])[-19:], {"at": time.time(), "mbps": mbps}]
    _card_tests_save(rec)
    return M("repair.card-speed.done", "The card read {mb} MB at {speed} MB/s.", mb=CARD_SPEED_MB, speed=mbps)


def card_scan_start():
    """The whole-card read, in a unit of its own (the root helper does one job at a time; this takes long)."""
    if run("systemctl", "is-active", CARD_SCAN_UNIT).stdout.strip() == "active":
        raise Refused(M("repair.card-scan.running", "The whole-card read is already running."))
    rec = _card_tests()
    rec["scan"] = {"started": time.time()}
    _card_tests_save(rec)
    r = run(
        "systemd-run",
        f"--unit={CARD_SCAN_UNIT}",
        "--collect",
        "-p",
        "Nice=19",
        "-p",
        "IOSchedulingClass=idle",
        f"{CODE}/irate-box",
        "health",
        "card-scan",
        timeout=60,
    )
    if r.returncode:
        raise Refused(
            M("repair.card-scan.failed", "The whole-card read did not start: {detail}", detail=r.stderr.strip()[-200:])
        )
    return M(
        "repair.card-scan.started", "The whole-card read has started. The Box doctor shows the result when it ends."
    )


def card_scan():
    """Every block of the card read once (dd, carrying on past an unreadable one); the count of unreadable ones."""
    info = card_info()
    rec = _card_tests()
    out = subprocess.run(  # noqa: TID251
        ["dd", f"if=/dev/{info['dev']}", "of=/dev/null", "bs=1M", "iflag=direct", "conv=noerror", "status=none"],
        capture_output=True,
        text=True,
    )
    bad = sum(1 for line in out.stderr.splitlines() if "Input/output error" in line)
    rec["scan"] = {**(rec.get("scan") or {}), "done": time.time(), "bad": bad}
    _card_tests_save(rec)
    return bad


BUILDS_BYTES = "builds-bytes.json"


def _builds_bytes(ci_root, quick):
    """What the builds take, walked in the full run and kept for the quick ones: on a Lyra the walk over
    /var/lib/hub/ci (231 280 files, 14 GB of the builder's caches) was 132.6 s of a 143.5 s run."""
    from irate_box.root import safeio

    kept = safeio.ROOT_STATE / BUILDS_BYTES
    if quick:
        try:
            return int(json.loads(kept.read_text())["bytes"])
        except (OSError, ValueError, KeyError, TypeError):
            return 0
    used = 0
    for f in ci_root.rglob("*") if ci_root.is_dir() else []:
        try:
            if f.is_file() and not f.is_symlink():
                used += f.stat().st_size
        except OSError:
            pass
    try:
        safeio.mkdir(safeio.ROOT_STATE, mode=0o700)
        safeio.write(kept, json.dumps({"at": time.time(), "bytes": used}))
    except OSError:
        pass
    return used


def check_builds(now=None, quick=False):
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
        out.append(
            finding(
                "builds-waiting",
                M("builds-waiting.title", "Builds waiting"),
                "warn",
                M(
                    "builds-waiting.count",
                    {
                        "one": "{n} build has waited for more than a day.",
                        "other": "{n} builds have waited for more than a day.",
                    },
                    n=len(waiting),
                ),
                fix=M(
                    "builds.paused.fix",
                    "On the Firmware Factory, resume the queue, or cancel what is no longer wanted.",
                )
                if held
                else M(
                    "builds-waiting.fix",
                    "Check that the builder, irate-box-ci, is running. A long build delays the rest. "
                    "Git, Builds and the Firmware Factory show what is running.",
                ),
                notes=[M("builds.paused", "The Firmware Factory is paused.")] if held else [],
                commands=[] if held else ["systemctl status irate-box-ci"],
                go=GO_FACTORY if held else GO_BUILDS,
            )
        )
    families = {}
    runs = ci_root / "runs" / "firmware-factory"
    for st in (
        sorted(runs.glob("*/status.json"), key=lambda p: int(p.parent.name) if p.parent.name.isdigit() else 0)
        if runs.is_dir()
        else []
    ):
        try:
            r = json.loads(st.read_text())
        except (OSError, ValueError):
            continue
        if r.get("family") and r.get("state") in ("passed", "failed", "timed out"):
            families.setdefault(r["family"], []).append(r["state"])
    failing = {
        fam: len(states) for fam, states in sorted(families.items()) if len(states) >= 2 and "passed" not in states
    }
    if failing:
        out.append(
            finding(
                "builds-families",
                M("builds-families.title", "Firmware builds that always fail"),
                "warn",
                M(
                    "builds-families.failing",
                    {
                        "one": "Every build here has failed for this family.",
                        "other": "Every build here has failed for these families.",
                    },
                    n=len(failing),
                ),
                M(
                    "builds-families.why",
                    "A possible cause: PlatformIO has no toolchain for this board's processor, or could not "
                    "download one. That fails every target of the family. Only the log can confirm it.",
                ),
                M("builds-families.fix", "On the Firmware Factory, under Built, open the log of a failed build."),
                evidence=[f"{fam} ({n})" for fam, n in failing.items()],
                go=GO_FACTORY,
            )
        )
    used = _builds_bytes(ci_root, quick)
    # The builds' own filesystem: with nand-logs' state on, the hub's folder is the onboard flash's and the builds
    # stay on the card, so the hub's folder's size gave a Lyra "6100% of the card".
    total = shutil.disk_usage(ci_root if ci_root.is_dir() else STATE).total if STATE.exists() else 0
    if used and total:
        share = used / total
        big = share > 0.2
        out.append(
            finding(
                "builds-disk",
                M("builds-disk.title", "Space used by firmware builds"),
                "warn" if big else "note",
                M(
                    "builds-disk.used",
                    "Builds use {mb} MB, {share} of the card: runs, and the builder's tools and caches.",
                    mb=used >> 20,
                    share=f"{share:.0%}",
                ),
                fix=M(
                    "builds-disk.fix",
                    "On Git, Builds, delete old runs. The Firmware Factory keeps the two newest of each target.",
                )
                if big
                else None,
                notes=[
                    M(
                        "builds-disk.tools",
                        "The builder's PlatformIO tools are in {folder}.",
                        folder=str(ci_root / "home"),
                    )
                ]
                if big
                else [],
                go=GO_BUILDS if big else None,
            )
        )
    return out


# --- the clock ---------------------------------------------------------------------------------
# These boards have no clock that keeps time while they are off (the Lyra has no RTC at all),
# and the hub is meant to run offline. fake-hwclock restores the last time it saved, so a box
# that was off for two days boots two days behind, put right by chrony only when the box is
# online. The hub's own expiry
# (hubclock.py) never reads the wall clock; logs, git commit dates, file times and certificate
# checks do. So: what keeps the time, what happened at this boot, is the time certainly wrong,
# and, offline, set it from the owner's browser.

NTP_DAEMONS = (
    ("chrony.service", "chrony"),
    ("systemd-timesyncd.service", "systemd-timesyncd"),
    ("ntpsec.service", "ntpsec"),
    ("ntp.service", "ntpd"),
    ("openntpd.service", "OpenNTPD"),
)
STEP_RE = re.compile(r"(?:System clock was stepped by|clock was stepped by) (-?[\d.]+) seconds")


def clock_facts():
    show = dict(ln.split("=", 1) for ln in run("timedatectl", "show").stdout.splitlines() if "=" in ln)
    daemon = next((name for unit, name in NTP_DAEMONS if unit_props(unit).get("ActiveState") == "active"), None)
    synced = show.get("NTPSynchronized") == "yes"
    sources = None
    if daemon == "chrony" and shutil.which("chronyc"):
        tracking = run("chronyc", "-n", "tracking").stdout
        if "Leap status     : Not synchronised" in tracking:
            synced = False
        sources = sum(1 for ln in run("chronyc", "-n", "sources").stdout.splitlines() if ln[:2] in ("^*", "^+", "^-"))
    rtcs = []
    for r in sorted(Path("/sys/class/rtc").glob("rtc*")):
        try:
            when = int((r / "since_epoch").read_text())  # the kernel's reading of the RTC, as UTC seconds
            rtcs.append({"name": (r / "name").read_text().strip(), "drift": time.time() - when})
        except (OSError, ValueError):
            rtcs.append({"name": r.name, "drift": None})
    fake = shutil.which("fake-hwclock") is not None
    data = Path("/etc/fake-hwclock.data")
    saver = fake and (
        unit_props("fake-hwclock-save.timer").get("ActiveState") == "active"
        or Path("/etc/cron.hourly/fake-hwclock").exists()
    )
    steps = [
        float(m.group(1))
        for m in STEP_RE.finditer(
            run(
                "journalctl",
                "-b",
                "--no-pager",
                "-o",
                "cat",
                "-u",
                "chrony",
                "-u",
                "chronyd",
                "-u",
                "systemd-timesyncd",
                "-u",
                "ntpsec",
                "-u",
                "ntp",
                "-u",
                "openntpd",
                timeout=60,
            ).stdout
        )
    ]
    return {
        "daemon": daemon,
        "synced": synced,
        "sources": sources,
        "timezone": show.get("Timezone"),
        "rtcs": rtcs,
        "fake": fake,
        "fake_saved": data.stat().st_mtime if data.exists() else None,
        "fake_saver": saver,
        "steps": steps,
        "uptime": _uptime(),
    }


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
    for path, what in (
        (CODE / "VERSION", "the installed code"),
        (CONTROL / "health.json", "the last health check"),
        (uplink_report(), "the watchdog's report"),
    ):
        m = rtc.root_mtime(path)
        if m:
            marks.append((m, what))
    return max(marks) if marks else (None, None)


SET_CLOCK = action(
    "clock-set",
    M("clock.set", "Set the clock from this browser"),
    M(
        "clock.set.confirm",
        "Set the box's clock to this device's time? Use it only if this device's own clock is right.",
    ),
)
# What latest_known_time() names, for the page.
KNOWN_TIME = {
    "the last install": M("clock.mark.install", "the last install"),
    "the installed code": M("clock.mark.code", "the installed code"),
    "the last health check": M("clock.mark.health", "the last health check"),
    "the watchdog's report": M("clock.mark.uplink", "the watchdog's report"),
}


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
    said = {
        "in-use": M("clock-source.in-use", "In use."),
        "answering": M(
            "clock-source.answering", "It answers, but the box does not use it now, because another source is better."
        ),
        "not-answering": M("clock-source.not-answering", "Selected, but it does not answer."),
        "not-found": M("clock-source.not-found", "Selected, but not found."),
        "unknown": M(
            "clock-source.unknown",
            "Selected. systemd-timesyncd names only the server that it uses now, and that is another one.",
        ),
    }
    fixes = {
        "gps": M(
            "clock-source.gps.fix", "Connect the GPS receiver, by USB or the UART, and give it a view of the sky."
        ),
        "module": M(
            "clock-source.module.fix",
            "Check the clock module's finding above, and that irate-box-rtc-feed.service runs.",
        ),
        "local": M(
            "clock-source.local.fix",
            "Check that the server is on, on this network, and serves the time on UDP port 123. "
            "Or change it on the Clock page.",
        ),
        "pool": M(
            "clock-source.pool.fix",
            "The box needs the internet for the pool. Or select another source on the Clock page.",
        ),
    }
    out = []
    for r in rows:
        st = SOURCE_STATUS[r["state"]]
        out.append(
            finding(
                f"clock-source:{r['source']}",
                M("clock-source.title", "Time source: {name}", name=r["name"]),
                st,
                said[r["state"]],
                fix=fixes[r["source"]] if st != "ok" else None,
                go=GO_CLOCK if st != "ok" else None,
            )
        )
    return out


def check_clock():
    c = clock_facts()
    now = time.time()
    tz = M("clock.tz", "Time zone: {tz}.", tz=c["timezone"] or "unknown")
    title = M("clock.title", "Clock")
    out = []
    # Probably wrong: earlier than something irate-box wrote with this same clock.
    newest, what = latest_known_time()
    if newest and now < newest - 300:
        out.append(
            finding(
                "clock-behind",
                title,
                "problem",
                M(
                    "clock.behind",
                    "The clock says {now}. That is {gap} before {what} was written, so the clock is probably behind.",
                    now=when(now),
                    gap=duration(newest - now),
                    what=KNOWN_TIME.get(what, what),
                ),
                fix=M(
                    "clock.behind.fix",
                    "Set it from this browser's clock with the button, or with sudo date -s and the right time. "
                    "If the box is online, the network time service sets it.",
                ),
                actions=[SET_CLOCK],
            )
        )
    boot = []
    if c["steps"]:
        total = sum(c["steps"])
        boot.append(
            M(
                "clock.boot-behind",
                "At this boot it started {gap} behind, and was corrected when the box reached the network.",
                gap=duration(total),
            )
            if total > 0
            else M(
                "clock.boot-ahead",
                "At this boot it started {gap} ahead, and was corrected when the box reached the network.",
                gap=duration(total),
            )
        )
        if c["fake"] and not c["rtcs"]:
            boot.append(
                M(
                    "clock.boot.fake",
                    "It came from fake-hwclock's last save, because the box keeps no time while it is off.",
                )
            )
    out += check_module()
    out += check_sources()
    daemon_idle = (
        [M("clock.daemon-offline", "{daemon} runs, but it has no time server to ask.", daemon=c["daemon"])]
        if c["daemon"]
        else []
    )
    if c["synced"]:
        by = (
            M(
                "clock.synced-by-n",
                {"one": "{daemon} sets it from {n} time server.", "other": "{daemon} sets it from {n} time servers."},
                n=c["sources"],
                daemon=c["daemon"],
            )
            if c["daemon"] and c["sources"]
            else M("clock.synced-by", "{daemon} sets it.", daemon=c["daemon"])
            if c["daemon"]
            else M(
                "clock.synced-kernel",
                "The kernel says that it is synchronised. No time service runs here. A host or a container can keep the time.",
            )
        )
        out.append(finding("clock", title, "ok", by, notes=[tz] + boot))
        return out
    if rtc.FROM_RTC.exists():
        out.append(
            finding(
                "clock",
                title,
                "ok",
                M(
                    "clock.from-module",
                    "The clock module set it at this boot. No network time was available to check it against.",
                ),
                notes=[tz],
            )
        )
        return out
    if c["rtcs"]:
        out.append(
            finding(
                "clock",
                title,
                "warn",
                M(
                    "clock.hw",
                    "The hardware clock ({name}) keeps it. Nothing checks it against the network.",
                    name=c["rtcs"][0]["name"],
                ),
                M("clock.hw.why", "A hardware clock drifts, and a flat battery resets it."),
                M("clock.hw.fix", "If it looks wrong, set it from this browser's clock with the button."),
                notes=(daemon_idle or [M("clock.no-daemon", "No network time service runs.")]) + [tz],
                actions=[SET_CLOCK],
            )
        )
        return out
    if c["fake"]:
        notes = (
            [M("clock.fake.saved", "fake-hwclock last saved it on {at}.", at=when(c["fake_saved"]))]
            if c["fake_saved"]
            else []
        ) + daemon_idle
        if not c["fake_saver"]:
            notes.append(
                M("clock.fake.no-saver", "fake-hwclock does not save the time regularly, so even that time is old.")
            )
        out.append(
            finding(
                "clock",
                title,
                "warn",
                M(
                    "clock.fake",
                    "Nothing has set the clock since this boot, and the box has no clock that runs while it is off. "
                    "The time came from the last fake-hwclock save.",
                ),
                M("clock.fake.why", "So it is behind by the time that the box was off."),
                M(
                    "clock.fake.fix",
                    "If the box is online, it sets the clock. If it is offline, set it from this browser's clock "
                    "with the button. Your phone or laptop knows the time.",
                ),
                notes=notes + [tz],
                actions=[SET_CLOCK],
            )
        )
    else:
        out.append(
            finding(
                "clock",
                title,
                "problem",
                M(
                    "clock.none",
                    "The box has no clock that runs while it is off, no fake-hwclock to restore the last time, "
                    "and no network time.",
                ),
                M("clock.none.why", "A boot can start at an old time, such as the image's build date."),
                M(
                    "clock.none.fix",
                    "Install fake-hwclock, so that a boot starts at least where the last one stopped. "
                    "Until then, set the clock from this browser with the button.",
                ),
                notes=daemon_idle,
                commands=["apt-get install fake-hwclock"],
                actions=[SET_CLOCK],
            )
        )
    return out


def check_module():
    """The I2C clock module (rtc.py): the one set up, or what a search found, or the offer to look."""
    st = rtc.status()
    title = M("rtc.title", "Clock module")
    if st:
        cfg = st["config"]
        at = {"chip": cfg["label"], "bus": cfg["bus"], "addr": cfg["addr"]}
        driver = (
            M("rtc.driver-kernel", "It runs through the kernel driver.")
            if cfg["mode"] == "kernel"
            else M("rtc.driver-own", "It runs through irate-box's own driver.")
        )
        if cfg.get("by") == "found":
            driver = sentences(
                driver,
                M("rtc.found-itself", "The box found it and set it up by itself on {at}.", at=when(cfg.get("set_up"))),
            )
        remove = action(
            "rtc-remove",
            M("rtc.remove", "Stop using it"),
            M("rtc.remove.confirm", "Stop using the clock module? The box goes back to fake-hwclock."),
        )
        if st.get("unreachable"):
            return [
                finding(
                    "rtc",
                    title,
                    "problem",
                    M("rtc.unreachable", "The {chip} on bus {bus} at {addr} does not answer.", **at),
                    fix=M(
                        "rtc.unreachable.fix",
                        "Check that the module is seated and wired to that bus. "
                        "If someone removed it on purpose, stop using it.",
                    ),
                    notes=[driver],
                    evidence=[st["why"]],
                    actions=[remove],
                )
            ]
        out = []
        if st["why"]:
            out.append(
                finding(
                    "rtc",
                    title,
                    "problem",
                    M("rtc.problem", "The {chip} on bus {bus} at {addr} has a problem.", **at),
                    fix=M(
                        "rtc.problem.fix",
                        "If it keeps happening, check the battery and replace it. "
                        "Then set the box's clock, which sets the module too.",
                    ),
                    notes=[driver],
                    evidence=[st["why"]],
                    actions=[SET_CLOCK, remove],
                )
            )
        else:
            drift = st["drift"] or 0
            if abs(drift) > 5 and st["trusted"]:
                out.append(
                    finding(
                        "rtc",
                        title,
                        "warn",
                        M(
                            "rtc.drift-behind",
                            "The {chip} on bus {bus} at {addr} is {gap} behind the system clock.",
                            gap=duration(drift),
                            **at,
                        )
                        if drift > 0
                        else M(
                            "rtc.drift-ahead",
                            "The {chip} on bus {bus} at {addr} is {gap} ahead of the system clock.",
                            gap=duration(drift),
                            **at,
                        ),
                        fix=M("rtc.save.fix", "Write the system time to it."),
                        notes=[driver],
                        evidence=[str(st["trusted"])],
                        actions=[action("rtc-save", M("rtc.save", "Set the module from the system clock"))],
                    )
                )
            else:
                out.append(
                    finding(
                        "rtc",
                        title,
                        "ok",
                        M(
                            "rtc.ok",
                            "The {chip} on bus {bus} at {addr} is within {gap} of the system clock. It keeps the time "
                            "while the box is off.",
                            gap=duration(drift),
                            **at,
                        ),
                        notes=[driver],
                    )
                )
        for n in st.get("notes", []):
            out.append(
                finding(
                    "rtc-note",
                    title,
                    "warn",
                    M("rtc.note", "The module reports a setting to check."),
                    fix=M(
                        "rtc.note.battery-switch",
                        "Setting it up again turns on the battery switch. This page never changes charging.",
                    )
                    if "INIEN" in n or "EOSC" in n
                    else M("rtc.note.cell", "Check which kind of cell the module has."),
                    evidence=[n],
                )
            )
        if st["units"].get("irate-box-rtc.service") != "active":
            out.append(
                finding(
                    "rtc-unit",
                    title,
                    "warn",
                    M("rtc-unit.not-run", "Its boot unit has not run, so the next boot can fail to read the module."),
                    commands=["systemctl start irate-box-rtc"],
                    actions=[action("unit-restart:irate-box-rtc.service", RUN_NOW)],
                )
            )
        return out
    look = action("rtc-look", M("rtc.find", "Look for a clock module"))
    try:
        found = json.loads(rtc.FOUND.read_text())
    except (OSError, ValueError):
        if list(Path("/sys/class/rtc").glob("rtc*")):
            return []  # the kernel has a hardware clock already; nothing to offer unless asked
        return [
            finding(
                "rtc",
                title,
                "warn",
                M("rtc.none", "No clock module is set up."),
                notes=[
                    M(
                        "rtc.none.what",
                        "A battery-backed module on I2C, such as a DS3231, RV-8803 or RX8130, lets the box keep "
                        "its time while it is off.",
                    )
                ],
                actions=[look],
            )
        ]
    if not found["found"]:
        return [
            finding(
                "rtc",
                title,
                "warn",
                M("rtc.not-found", "The box found no clock module on I2C. It looked on {at}.", at=when(found["at"])),
                notes=[]
                if found["buses"]
                else [
                    M(
                        "rtc.no-bus",
                        "No I2C bus is enabled on this board. An administrator can enable one "
                        "with a board setting, such as an Armbian overlay.",
                    )
                ],
                actions=[look],
            )
        ]
    out = []
    for x in found["found"]:
        at = {"chip": x["label"], "bus": x["bus"], "addr": x["addr"]}
        if not x["supported"]:
            out.append(
                finding(
                    f"rtc-found:{x['chip']}",
                    M("rtc-found.title", "Clock module found"),
                    "warn",
                    M(
                        "rtc-found.unsupported",
                        "There is a {chip} on bus {bus} at {addr}. This kernel has no driver for it, "
                        "and irate-box has no driver of its own for it yet.",
                        **at,
                    ),
                    actions=[look],
                )
            )
            continue
        notes = [
            M("rtc-found.kernel", "It would run through the kernel driver {driver}.", driver=x["kernel"])
            if x["kernel"]
            else M("rtc-found.own", "It would run through irate-box's own driver, because this kernel has none for it.")
        ]
        if x["ambiguous"]:
            notes.append(
                M(
                    "rtc-found.ambiguous",
                    "Both chips that use this address fit its answer. Select the one that is fitted.",
                )
            )
        if x.get("time_lost"):
            notes.append(
                M(
                    "rtc-found.time-lost",
                    "Its time is not valid yet. Setting it up sets its time from the system clock.",
                )
            )
        out.append(
            finding(
                f"rtc-found:{x['chip']}:{x['bus']}",
                M("rtc-found.title", "Clock module found"),
                "warn",
                M("rtc-found.supported", "There is a {chip} on bus {bus} at {addr}. It is not set up yet.", **at),
                fix=M(
                    "rtc-found.fix",
                    "Setting it up adds a boot unit that sets the clock from the module, and saves the time "
                    "to it every hour.",
                ),
                notes=notes,
                evidence=[x["time_lost"]] if x.get("time_lost") else [],
                actions=[
                    action(
                        f"rtc-setup:{x['chip']}:{x['bus']}:{x['addr']}",
                        M("rtc-found.setup", "Set it up as {chip}", chip=x["label"]),
                        M("rtc-found.setup.confirm", "Use the {chip} on bus {bus} at {addr} as the box's clock?", **at),
                    )
                ],
            )
        )
    return out


def set_clock(epoch):
    """Set the system clock from the owner's browser. Only while nothing better has set it,
    and only to a plausible time; then saved, so the next boot starts from here."""
    now = time.time()
    c = clock_facts()
    if c["synced"]:
        raise Refused(
            M(
                "repair.clock.synced",
                "{daemon} already keeps the clock correct. The clock did not change.",
                daemon=c["daemon"],
            )
            if c["daemon"]
            else M(
                "repair.clock.synced-service",
                "The network time service already keeps the clock correct. The clock did not change.",
            )
        )
    newest, what = latest_known_time()
    if newest and epoch < newest - 300:
        raise Refused(
            M(
                "repair.clock.before-known",
                "That time is before {what} was written, so the clock of this device looks wrong. "
                "The clock did not change.",
                what=KNOWN_TIME.get(what, what),
            )
        )
    if epoch > now + 10 * 365 * 86400 or epoch < 1735689600:  # 2025-01-01
        raise Refused(M("repair.clock.implausible", "That time is not plausible. The clock did not change."))
    # Not years past anything the box has seen: a forged request could otherwise move the
    # clock far forward, after which it is trusted, saved to the module, and hard to bring back.
    if epoch > max(newest or 0, now) + 2 * 365 * 86400:
        raise Refused(
            M(
                "repair.clock.too-far",
                "That time is more than two years after the newest time that this box has seen. "
                "The clock did not change.",
            )
        )
    if abs(epoch - now) < 30:
        return M(
            "repair.clock.agrees",
            "The clock of the box already agrees with this device, within {gap}. The clock did not change.",
            gap=duration(30),
        )
    r = run("date", "-u", "-s", f"@{int(epoch)}")
    if r.returncode:
        raise Refused(M("repair.clock.date-failed", "The command date failed: {detail}", detail=r.stderr.strip()))
    saved = []
    rtc.RUN.mkdir(parents=True, exist_ok=True)
    rtc.TRUSTED.write_text(str(int(epoch)))  # the owner set it: worth writing to a clock module from now on
    if rtc.load_config():
        try:
            rtc.save(force=True)
            saved.append(M("repair.clock.module-set", "The clock module is set too."))
        except (rtc.RtcError, OSError) as exc:
            saved.append(
                M("repair.clock.module-failed", "The box could not set the clock module: {detail}", detail=str(exc))
            )
    if shutil.which("fake-hwclock"):
        run("fake-hwclock", "save")
        saved.append(M("repair.clock.fake-hwclock", "fake-hwclock saved the time, so the next boot starts from it."))
    elif list(Path("/sys/class/rtc").glob("rtc*")) and shutil.which("hwclock"):
        run("hwclock", "--systohc", "--utc")
        saved.append(M("repair.clock.hwclock", "The box wrote the time to the hardware clock too."))
    return sentences(
        M(
            "repair.clock.forward",
            "The clock moved forward by {gap}, to {time}.",
            gap=duration(epoch - now),
            time=when(epoch),
        )
        if epoch > now
        else M(
            "repair.clock.back",
            "The clock moved back by {gap}, to {time}.",
            gap=duration(now - epoch),
            time=when(epoch),
        ),
        *saved,
    )


def check_usb_filesystems():
    """Which filesystems a USB stick may have for this box to read it (books, toolkits, imports)."""
    from irate_box.root import usbstick

    can = usbstick.readable()

    def words(names):
        return list(
            dict.fromkeys(usbstick.FS_WORDS[n] for n in sorted(names, key=lambda n: list(usbstick.FS_WORDS).index(n)))
        )

    yes = [n for n, ok in can.items() if ok]
    no = words(
        [
            n
            for n, ok in can.items()
            if not ok and not (n == "ntfs3" and can.get("ntfs")) and not (n == "ntfs" and can.get("ntfs3"))
        ]
    )
    return [
        finding(
            "usb-filesystems",
            M("usb-fs.title", "USB drives this box can read"),
            "ok",
            M("usb-fs.can", "This box can read {names}.", names=words(yes)),
            fix=M("usb-fs.fix", "Use a drive formatted with one of the filesystems that it can read.") if no else None,
            notes=[
                M(
                    "usb-fs.cannot",
                    {
                        "one": "It cannot read {names}: this kernel has no driver for it.",
                        "other": "It cannot read {names}: this kernel has no driver for them.",
                    },
                    n=len(no),
                    names=no,
                )
            ]
            if no
            else [],
        )
    ]


def check_crashwatch(now=None):
    """Crash watch (crashwatch.py): crashes, kernel floods, the radio's failures, the hang settings."""
    from irate_box.root import crashwatch as cw

    now = now or time.time()
    if not (UNIT_DIR / "irate-box-crashwatch.service").exists() and not cw.DIR.exists():
        return []
    st = cw.status()
    s, out = st["settings"], []
    week = [c for c in st["crashes"] if c.get("detected", 0) > now - 7 * 86400]
    title = M("crash-last.title", "The box stopped without shutting down")
    if week:
        last = week[-1]
        lines = [ln.strip()[:200] for ln in last.get("last", "").splitlines()[:4] if ln.strip()]
        ev = [f"{e['kind']}: {e['text']}"[:200] for e in last.get("events", [])[-2:]]
        notes = [M("crash-last.files", "Its folder holds snapshots.log and journal-previous-boot.log.")]
        if not lines:
            notes.insert(0, M("crash-last.no-snapshot", "There is no snapshot: snapshots were off."))
        out.append(
            finding(
                "crash-last",
                title,
                "warn" if now - last["detected"] < 86400 else "ok",
                M(
                    "crash-last.count",
                    {
                        "one": "{n} time this week. The box found the last one {ago} ago.",
                        "other": "{n} times this week. The box found the last one {ago} ago.",
                    },
                    n=len(week),
                    ago=duration(now - last["detected"]),
                ),
                M("crash-last.why", "A hang, a crash or a loss of power can each cause this."),
                None
                if s["watchdog"] or s["panic"]
                else M(
                    "crash-last.fix", "If it froze, the settings for a frozen box can restart it by itself next time."
                ),
                notes=notes,
                commands=[f"sudo ls {cw.CRASHES}/{last['dir']}/"],
                evidence=lines + ev,
                go=None if s["watchdog"] or s["panic"] else _go("health-settings", RECOVERY, "hs-panic"),
            )
        )
    else:
        out.append(finding("crash-last", title, "ok", M("crash-last.none", "Not in the last week.")))
    run = st["running"]
    fl = run.get("floods") or {}
    if fl:
        k, n = max(fl.items(), key=lambda x: x[1])
        window = run.get("flood_window", 3600)
        out.append(
            finding(
                "crash-flood",
                M("crash-flood.title", "The kernel log"),
                "warn",
                M(
                    "crash-flood.lines",
                    "The kernel logged {n} lines like this one in {window}. That is about {rate} lines a second.",
                    n=n,
                    window=duration(window),
                    rate=f"{n / max(window, 30):.1f}",
                ),
                M(
                    "crash-flood.why",
                    "A driver that logs this much wears the storage that the log is written to, and hides the "
                    "lines that matter. Crash watch counts them and does not copy them.",
                ),
                M(
                    "crash-flood.fix",
                    "This is often a driver's debug output. Look for its module's debug parameter, or a newer driver.",
                ),
                commands=["sudo dmesg | tail -50"],
                evidence=[k[:200]],
            )
        )
    radios = [f"{i} ({r.get('product') or r.get('driver') or '?'})" for i, r in (run.get("radios") or {}).items()]
    # What crash watch saw; the old events of its own resets and reboots, from before it only recorded, said as they were.
    day = [
        e
        for e in st["events"]
        if e.get("at", 0) > now - 86400 and e.get("kind") in ("failed", "driver-reset", "recovered", "reset", "reboot")
    ]
    kept = [i for i in st.get("incidents", []) if i.get("at", 0) > now - 7 * 86400]
    failures = [e for e in day if e.get("kind") in ("failed", "driver-reset")]
    failed = bool(failures)
    notes = [M("crash-radio.role", "Crash watch records a failing radio. The uplink watchdog does the repairs.")]
    if kept:
        notes.append(
            M(
                "crash-radio.kept",
                {
                    "one": "This week, {n} failure is kept as it happened. The last one is in {folder}.",
                    "other": "This week, {n} failures are kept as they happened. The last one is in {folder}.",
                },
                n=len(kept),
                folder=f"{cw.CRASHES}/{kept[-1]['dir']}/",
            )
        )
    names = list(run.get("radios") or {}) or [M("crash-radio.no-radio", "no radio")]
    glance = {
        (False, False): M("crash-radio.glance", "Watching {radios}.", radios=names),
        (True, False): M("crash-radio.glance-failed", "Watching {radios}. It failed in the last day.", radios=names),
        (False, True): M(
            "crash-radio.glance-kept",
            {
                "one": "Watching {radios}. {n} failure kept this week.",
                "other": "Watching {radios}. {n} failures kept this week.",
            },
            n=len(kept),
            radios=names,
        ),
        (True, True): M(
            "crash-radio.glance-failed-kept",
            {
                "one": "Watching {radios}. It failed in the last day. {n} failure kept this week.",
                "other": "Watching {radios}. It failed in the last day. {n} failures kept this week.",
            },
            n=len(kept),
            radios=names,
        ),
    }[(failed, bool(kept))]
    out.append(
        finding(
            "crash-radio",
            M("crash-radio.title", "The WiFi radio"),
            "warn" if len(failures) > 1 else "note" if failed else "ok",
            M("crash-radio.watching", "Crash watch watches {radios}.", radios=radios)
            if radios
            else M("crash-radio.none", "Crash watch finds no radio to watch."),
            M(
                "crash-radio.why",
                "A radio that keeps failing often has a power problem, such as a weak supply or a long USB lead, "
                "or a driver problem.",
            )
            if failed
            else None,
            M(
                "crash-radio.fix",
                "Set what the box does when the radio fails on Network, Connection settings, Failed radio.",
            )
            if failed
            else None,
            notes=notes,
            evidence=[
                f"{time.strftime('%H:%M', time.localtime(e['at']))} {e['iface']} {e['kind']}: {e['text']}"[:200]
                for e in day[-4:]
            ],
            go=_go("net-watchdog-card", M("go.failed-radio", "Network → Failed radio"), "up-wedge") if failed else None,
            glance=glance,
        )
    )
    wd, pending = st.get("watchdog_device"), st.get("watchdog_pending")
    said = ([M("crash-hang.panic", "The box restarts after a kernel panic or lockup.")] if s["panic"] else []) + (
        [M("crash-hang.watchdog", "A watchdog restarts the box if it freezes.")] if s["watchdog"] else []
    )
    if s["watchdog"] and pending:
        said.append(M("crash-hang.pending", "The board's watchdog starts working after the next restart."))
    elif wd == "switched-off" and not s["watchdog"]:
        said.append(
            M(
                "crash-hang.switched-off",
                "The board has its own watchdog, switched off in its device tree. "
                "Turning on the watchdog switches it on.",
            )
        )
    elif not wd:
        said.append(M("crash-hang.no-watchdog", "This kernel has no watchdog: neither the board's own nor softdog."))
    on = s["panic"] or s["watchdog"]
    out.append(
        finding(
            "crash-hang",
            M("crash-hang.title", "A frozen box"),
            "warn" if week and not on else "ok",
            said[0]
            if on
            else M("crash-hang.none", "Nothing restarts it. A frozen box waits until someone switches it off and on."),
            M(
                "crash-hang.why",
                "An automatic restart can lose unsaved data and stop running jobs. The box files each restart as a "
                "crash, with what it was doing, as far as it could record it.",
            ),
            M("crash-hang.fix", "Set it on Recovery and monitoring."),
            notes=said[1:] if on else said,
            go=_go("health-settings", RECOVERY, "hs-panic"),
        )
    )
    out.append(
        finding(
            "crash-snapshots",
            M("crash-snap.title", "Crash watch's snapshots"),
            "ok" if s["snapshots"] else "warn",
            M(
                "crash-snap.on",
                "Every 30 s, to {path}, written to storage at once. They can explain a crash.",
                path=str(cw.SNAP),
            )
            if s["snapshots"]
            else M("crash-snap.off", "Off. A crash leaves only what the journal last wrote to storage."),
            None
            if s["snapshots"]
            else M("crash-snap.off.why", "This image keeps its journal in RAM, so that can be up to an hour old."),
            None if s["snapshots"] else M("crash-snap.fix", "Turn them on on Recovery and monitoring."),
            go=_go("health-settings", RECOVERY, "hs-snapshots"),
        )
    )
    return out


WD_WORDS = {
    "hardware": M("wd.hardware", "the board's own"),
    "softdog": M("wd.softdog", "the kernel's softdog"),
    "switched-off": M("wd.switched-off", "the board's own, from the next restart"),
}
ON, OFF = M("chip.on", "On"), M("chip.off", "Off")
STAY_STOPPED = M("hs.stay-stopped", "Stay stopped")


def setting_groups(s, wd):
    """The doctor's own settings as choice groups for its settings card: {key, label, desc, current, options:
    [{value, label, choice (health.fix's), confirm?}]}, the safest first. The watchdog only where there is one."""
    groups = [
        {
            "key": "snapshots",
            "label": M("hs.snapshots", "Crash watch's snapshots"),
            "desc": M(
                "hs.snapshots.desc",
                "Every 30 s, what the box was doing, kept on its own storage. It can explain a crash.",
            ),
            "current": "on" if s["snapshots"] else "off",
            "options": [
                {"value": "on", "label": ON, "choice": "crashwatch-snapshots:on"},
                {"value": "off", "label": OFF, "choice": "crashwatch-snapshots:off"},
            ],
        },
        {
            "key": "panic",
            "label": M("hs.panic", "A kernel panic, oops or lockup"),
            "desc": M(
                "hs.panic.desc",
                "Nothing in user space can act on a frozen kernel. An automatic restart can lose unsaved data "
                "and stop running jobs.",
            ),
            "current": "on" if s["panic"] else "off",
            "options": [
                {"value": "off", "label": STAY_STOPPED, "choice": "crashwatch-panic:off"},
                {
                    "value": "on",
                    "label": M("hs.panic.on", "Restart after 10 s"),
                    "choice": "crashwatch-panic:on",
                    "confirm": M(
                        "hs.panic.confirm",
                        "The box restarts 10 seconds after a kernel panic, oops or lockup, rather than staying frozen. A restart loses unsaved data and stops running jobs.",
                    ),
                },
            ],
        },
    ]
    if wd or s["watchdog"]:
        groups.append(
            {
                "key": "watchdog",
                "label": M("hs.watchdog", "A box that stops answering"),
                "desc": M(
                    "hs.watchdog.desc",
                    "A watchdog ({which}) that systemd keeps fed. A frozen system stops feeding it, "
                    "and the box restarts.",
                    which=WD_WORDS.get(wd, WD_WORDS["hardware"]),
                ),
                "current": "on" if s["watchdog"] else "off",
                "options": [
                    {"value": "off", "label": STAY_STOPPED, "choice": "crashwatch-watchdog:off"},
                    {
                        "value": "on",
                        "label": M("hs.watchdog.on", "Restart after a minute"),
                        "choice": "crashwatch-watchdog:on",
                        "confirm": M(
                            "hs.watchdog.confirm-overlay",
                            "A watchdog restarts the box when it stops answering for a minute. A box doing heavy work that starves systemd for that long restarts too. The board's watchdog is off in its device tree: this switches it on with a boot overlay, from the next restart.",
                        )
                        if wd == "switched-off"
                        else M(
                            "hs.watchdog.confirm",
                            "Let a watchdog restart the box when it stops answering for a minute? A box doing heavy "
                            "work that starves systemd for that long would restart too.",
                        ),
                    },
                ],
            }
        )
    groups += [
        {
            "key": "undervoltage",
            "label": M("hs.undervoltage", "Undervoltage"),
            "desc": M(
                "hs.undervoltage.desc",
                "Watch the supply where the board measures it, and keep each drop with its time. A Raspberry Pi "
                "reports it. A Lyra needs an INA219 or INA226 on I2C.",
            ),
            "current": "on" if s["undervoltage"] else "off",
            "options": [
                {"value": "on", "label": ON, "choice": "crashwatch-undervoltage:on"},
                {"value": "off", "label": OFF, "choice": "crashwatch-undervoltage:off"},
            ],
        },
        {
            "key": "undervoltage-mv",
            "label": M("hs.undervoltage-mv", "The supply's low point"),
            "desc": M(
                "hs.undervoltage-mv.desc",
                "The voltage below which the supply counts as low. Match the rail the sensor watches.",
            ),
            "current": str(s["undervoltage_mv"]),
            "options": [
                {
                    "value": "4630",
                    "label": M("hs.uv-5v", "5 V rail (4 630 mV)"),
                    "choice": "crashwatch-undervoltage-mv:4630",
                },
                {
                    "value": "3000",
                    "label": M("hs.uv-3v3", "3.3 V rail (3 000 mV)"),
                    "choice": "crashwatch-undervoltage-mv:3000",
                },
                {
                    "value": "11000",
                    "label": M("hs.uv-12v", "12 V rail (11 000 mV)"),
                    "choice": "crashwatch-undervoltage-mv:11000",
                },
            ],
        },
        {
            "key": "power-action",
            "label": M("hs.power-action", "When the power fails"),
            "desc": M(
                "hs.power-action.desc",
                "A power-fail line or a UPS says the power is going. Log it, sync the disks, or shut the box down "
                "while the hold-up lasts. If NUT's upsmon or apcupsd is managing the shutdown, the box asks it to, "
                "rather than doing it twice. A hook file is removed when the power is back.",
            ),
            "current": s["power_action"],
            "options": [
                {"value": "log", "label": M("hs.power-log", "Log it"), "choice": "crashwatch-power-action:log"},
                {
                    "value": "sync",
                    "label": M("hs.power-sync", "Sync the disks"),
                    "choice": "crashwatch-power-action:sync",
                },
                {
                    "value": "shutdown",
                    "label": M("hs.power-shutdown", "Shut down"),
                    "choice": "crashwatch-power-action:shutdown",
                    "confirm": M(
                        "hs.power-shutdown.confirm",
                        "A power failure shuts the box down at once. It comes back when the power does.",
                    ),
                },
            ],
        },
    ]
    # The Box doctor's own schedule and its checks for a card that loses writes (notes: box-doctor-hardening-plan,
    # Part 1, steps 0 and 2). All three are the recommended set's, and gone when switched off.
    groups += [
        {
            "key": "health-timer",
            "label": M("health-timer.title", "Box doctor checks the box by itself"),
            "desc": M(
                "health-timer.desc",
                "Every 6 hours the Box doctor checks the box without anyone opening its page, and keeps what it "
                "finds where the page reads it. Once a week it opens every book in Kiwix: that full run is slower. "
                "The checks run at low priority, in the background.",
            ),
            "current": "on" if timer_on() else "off",
            "options": [
                {"value": "on", "label": M("health-timer.on", "Every 6 hours"), "choice": "health-timer:on"},
                {"value": "off", "label": M("health-timer.off", "Only when asked"), "choice": "health-timer:off"},
            ],
        },
        {
            "key": "lost-writes",
            "label": M("lost-writes.setting.title", "Watch the card for lost writes"),
            "desc": M(
                "lost-writes.setting.desc",
                "At every boot, before the services start, the box reads the files it writes often past the cache. "
                "One that reads back as 0xFF means the card lost a write at a power cut. It writes nothing.",
            ),
            "current": "on" if lostwrites_on() else "off",
            "options": [
                {"value": "on", "label": M("lost-writes.setting.on", "At every boot"), "choice": "lost-writes:on"},
                {"value": "off", "label": M("lost-writes.setting.off", "Do not check"), "choice": "lost-writes:off"},
            ],
        },
    ]
    if not records_on_card():
        groups.append(
            {
                "key": "canary",
                "label": M("canary.title", "A test file for lost writes"),
                "desc": M(
                    "canary.desc",
                    "This card holds no record that the box rewrites often, so there is nothing to notice a lost "
                    "write from. The box writes a 4 KB test file every 10 minutes and checks it at boot. It adds "
                    "about 2 to 7 MB a day of writes to the card.",
                ),
                "current": "on" if canary_installed() else "off",
                "options": [
                    {"value": "off", "label": M("canary.off", "Off"), "choice": "canary:off"},
                    {
                        "value": "on",
                        "label": M("canary.on", "Every 10 minutes"),
                        "choice": "canary:on",
                        "confirm": M(
                            "canary.confirm",
                            "Write a 4 KB test file to the card every 10 minutes, so the box can tell whether the "
                            "card loses writes at a power cut? It adds about 2 to 7 MB a day of writes.",
                        ),
                    },
                ],
            }
        )
    from irate_box.root import retain

    groups.append(
        {
            "key": "retain",
            "label": M("retain.setting.title", "A second copy of the settings and keys"),
            "desc": M(
                "retain.setting.desc",
                "The box keeps a copy of the small settings and keys it needs to come back after damage, and checks "
                "them at every boot. Switching it off removes the copy and the check.",
            ),
            "current": "on" if retain.installed() else "off",
            "options": [
                {"value": "on", "label": ON, "choice": "retain:on"},
                {
                    "value": "off",
                    "label": OFF,
                    "choice": "retain:off",
                    "confirm": M(
                        "retain.setting.off.confirm",
                        "Remove the second copy of the settings and keys, and stop the boot check? The copy holds "
                        "the CA key and the WiFi secrets, so it is deleted here and in every extra place.",
                    ),
                },
            ],
        }
    )
    for path, label, rule in retain.store_candidates():
        encoded = retain.encode_store(path)
        groups.append(
            {
                "key": f"retain-store-{encoded}",
                "label": M("retain.store.title", "A second copy on {place}", place=label),
                "desc": (
                    M(
                        "retain.store.desc.keys",
                        "Keep the settings and keys here as well as on the card, the keys included.",
                    )
                    if rule == "plain"
                    else M(
                        "retain.store.desc.usb",
                        "Keep the settings and keys here as well as on the card. A drive's copy takes the CA key "
                        "and the WiFi secrets only when Keys in a removable copy is Encrypt them, and only encrypted.",
                    )
                    if rule == "encrypted"
                    else M(
                        "retain.store.desc.none",
                        "Keep the settings here as well as on the card. The keys are left out.",
                    )
                ),
                "current": "on" if path in retain.extra_stores() else "off",
                "options": [
                    {"value": "on", "label": ON, "choice": f"retain-store:on:{encoded}"},
                    {
                        "value": "off",
                        "label": OFF,
                        "choice": f"retain-store:off:{encoded}",
                        "confirm": M(
                            "retain.store.off.confirm",
                            "Stop keeping the copy at {place}, and delete the copies irate-box made there?",
                            place=label,
                        ),
                    },
                ],
            }
        )
    from irate_box.root import readonly

    groups += readonly.groups()
    groups += [
        {
            "key": "retain-keys",
            "label": M("retain.keys.title", "Keys in a removable copy"),
            "desc": M(
                "retain.keys.desc",
                "Keys, passwords, the accounts and the hotspot's passphrase are left out of a USB drive's copy, or "
                "encrypted there with a key kept on the card. The onboard flash's copy takes them as they are: it is "
                "the board's own.",
            ),
            "current": retain.keys_mode(),
            "options": [
                {
                    "value": "leave-out",
                    "label": M("retain.keys.leave", "Leave them out"),
                    "choice": "retain-keys:leave-out",
                },
                {
                    "value": "encrypt",
                    "label": M("retain.keys.encrypt", "Encrypt them"),
                    "choice": "retain-keys:encrypt",
                },
            ],
        },
        {
            "key": "retain-data",
            "label": M("retain.data.title", "The administrator's data in a removable copy"),
            "desc": M(
                "retain.data.desc",
                "Keep notes, saved work, messages and the drop in a USB drive's copy, up to this size. It is a "
                "plaintext backup, not the boot check's copy, and not encrypted like the keys. 0 leaves the data out.",
            ),
            "current": str(retain.data_mb()),
            "options": [
                {"value": "0", "label": M("retain.data.none", "None"), "choice": "retain-data:0"},
                {"value": "64", "label": M("retain.data.mb", "{mb} MB", mb="64"), "choice": "retain-data:64"},
                {"value": "256", "label": M("retain.data.mb", "{mb} MB", mb="256"), "choice": "retain-data:256"},
                {"value": "1024", "label": M("retain.data.mb", "{mb} MB", mb="1024"), "choice": "retain-data:1024"},
            ],
        },
    ]
    from irate_box.root import bootkey

    if bootkey.supported():
        groups.append(
            {
                "key": "bootkey",
                "label": M("bootkey.setting.title", "The BOOT key as a shutdown button"),
                "desc": M(
                    "bootkey.setting.desc",
                    "On the Lyra, the BOOT key is read by nothing while the box runs. Held for three seconds, it "
                    "can shut the box down cleanly. A short press does nothing, and holding it at power-on still "
                    "gives Loader mode.",
                ),
                "current": "on" if bootkey.installed() else "off",
                "options": [
                    {"value": "off", "label": OFF, "choice": "bootkey:off"},
                    {
                        "value": "on",
                        "label": M("bootkey.setting.on", "Hold for three seconds"),
                        "choice": "bootkey:on",
                    },
                ],
            }
        )
    return groups


LED_SHOW_LABEL = {
    "leave": M("led.show.leave", "Leave it"),
    "alive": M("led.show.alive", "The box is alive"),
    "network": M("led.show.network", "Alive and on its network"),
    "all": M("led.show.all", "Alive, on its network, nothing wrong"),
}
ARMBIAN_LED = "armbian-led-state.service"
ARMBIAN_LED_WHY = M(
    "armbian-led.why",
    "It puts the LEDs back as they were at the last shutdown. This board's light has no trigger file, "
    "so the service refuses the state that it saved. The box's own status light does not need it.",
)


def led_groups(ls, armbian=None):
    """The status light's choices for the settings card: what it shows, and which LED (when there is a choice).
    armbian: the image's LED state service ("failed", "masked", or None when it is fine or not there)."""
    if not ls or not ls.get("found"):
        return []
    found, chosen = ls["found"], ls["settings"]
    show = ls["settings"].get("show") or ls.get("suggested") or "leave"
    led = ls.get("led") or M("led.the-light", "the light")
    groups = [
        {
            "key": "led-show",
            "label": M("hs.led-show", "The status light"),
            "desc": M(
                "hs.led-show.desc-leave",
                "When all that it reports is well, {led} is on for 1 s and off for 1 s. When something "
                "is not well, it is off. While the network is being repaired, it flashes quickly. When the "
                "Box doctor has a problem to read, it gives two short flashes. While the box starts or "
                "updates, it stays on. By default the box leaves it alone, because on this board it shows "
                "the SD card working.",
                led=led,
            )
            if ls.get("suggested") == "leave"
            else M(
                "hs.led-show.desc",
                "When all that it reports is well, {led} is on for 1 s and off for 1 s. When something is not "
                "well, it is off. While the network is being repaired, it flashes quickly. When the Box doctor has "
                "a problem to read, it gives two short flashes. While the box starts or updates, it stays on.",
                led=led,
            ),
            "current": show,
            "options": [
                {"value": v, "label": LED_SHOW_LABEL[v], "choice": f"led-show:{v}"}
                for v in ("leave", "alive", "network", "all")
            ],
        }
    ]
    if len(found) > 1 or chosen.get("led", "auto") != "auto":
        groups.append(
            {
                "key": "led-which",
                "label": M("hs.led-which", "Which light"),
                "desc": M(
                    "hs.led-which.desc",
                    "The board's LEDs that could be the status light, never a power LED. "
                    "The Box doctor's Flash buttons show which is which.",
                ),
                "current": chosen.get("led", "auto"),
                "options": [
                    {
                        "value": "auto",
                        "label": M(
                            "hs.led-which.auto-named",
                            "The box's pick ({led})",
                            led=ls.get("led") or M("led.none-picked", "none"),
                        )
                        if chosen.get("led", "auto") == "auto"
                        else M("hs.led-which.auto", "The box's pick"),
                        "choice": "led-led:auto",
                    }
                ]
                + [{"value": n, "label": n, "choice": f"led-led:{n}"} for n in found],
            }
        )
    if armbian:
        groups.append(
            {
                "key": "armbian-led",
                "label": M("hs.armbian-led", "The image's LED service"),
                "desc": M(
                    "hs.armbian-led.desc",
                    "{unit} fails at every start on this board. Switching it off stops the failure. "
                    "Switching it on again brings the failure back.",
                    unit=ARMBIAN_LED,
                ),
                "current": "off" if armbian == "masked" else "on",
                "options": [
                    {"value": "on", "label": M("hs.armbian-led.on", "As the image has it"), "choice": "armbian-led:on"},
                    {"value": "off", "label": M("hs.armbian-led.off", "Switched off"), "choice": "armbian-led:off"},
                ],
            }
        )
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
    title = M("led.title", "Status light")
    if not ls.get("found"):
        return [
            finding(
                "led",
                title,
                "ok",
                M(
                    "led.none",
                    "This board has no LED that the box can use as a status light. The box never takes a power LED.",
                ),
                glance=M("led.none.glance", "No LED on this board."),
            )
        ]
    name, show, run_ = ls.get("led"), ls.get("show"), ls.get("running") or {}
    flash = [action(f"led-flash:{n}", M("led.flash", "Flash {led}", led=n)) for n in list(ls["found"])[:4]]
    go = _go("health-settings", RECOVERY, "hs-led-show")
    set_there = M("led.set.fix", "Set it on Recovery and monitoring.")
    if show == "leave":
        picked = name or M("led.no-pick", "no LED selected")
        return [
            finding(
                "led",
                title,
                "ok",
                M("led.leave", "Left as the image set it: {led}.", led=picked),
                notes=[M("led.leave.can", "The box can blink it while everything is well.")],
                fix=set_there,
                actions=flash,
                go=go,
                glance=M("led.leave.glance", "Left as the image set it ({led}).", led=picked),
            )
        ]
    unit_on = (active or unit_props)("irate-box-led.service").get("ActiveState") == "active"
    if not unit_on:
        return [
            finding(
                "led",
                title,
                "problem",
                M(
                    "led.unit-down",
                    "The status light's service is not running, so {led} does not report the state of the box.",
                    led=name,
                ),
                fix=M("led.unit-down.fix", "Read its log, then start it again."),
                commands=["journalctl -u irate-box-led -n 30"],
                actions=[action("unit-restart:irate-box-led.service", RESTART)],
                glance=M("led.unit-down.glance", "{led}: its service is not running.", led=name),
            )
        ]
    how = (
        M("led.driven-no-triggers", "The box drives it. This kernel has no LED triggers, so nothing else can blink it.")
        if not (ls["found"].get(name) or {}).get("triggers")
        else M("led.driven", "The box drives it, so it stops when the box stops.")
    )
    shows = LED_SHOW_LABEL.get(show) or show
    return [
        finding(
            "led",
            title,
            "ok",
            M("led.ok", "{led} is set to: {show}.", led=name, show=shows),
            notes=[
                M("led.now", "Now: {now}.", now=run_["words"])
                if run_.get("words")
                else M("led.not-reported", "It has not reported yet."),
                how,
            ],
            fix=set_there,
            actions=flash,
            go=go,
            glance=M("led.ok.glance", "{led}: {show}.", led=name, show=shows),
        )
    ]


RFKILL_LABEL = {
    "on": M("hs.rfkill.on", "On at every start"),
    "off": M("hs.rfkill.off", "Off at every start"),
    "leave": M("hs.rfkill.leave", "As the image has it"),
}
RFKILL_SAID = {
    "on": lambda radio: M("rfkill.ok.on", "{radio} switched on at every start", radio=radio),
    "off": lambda radio: M("rfkill.ok.off", "{radio} switched off at every start", radio=radio),
    "leave": lambda radio: M("rfkill.ok.leave", "{radio} left as the image has it", radio=radio),
}


def rfkill_status():
    try:
        from irate_box.root import rfkill

        return rfkill.status()
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


def rfkill_groups(st):
    """The radios' choices for the settings card: one per radio the board has a switch for."""
    if not st:
        return []
    from irate_box.root import rfkill

    s, line = st["settings"], st.get("power_line")
    groups = []
    for radio in rfkill.RADIOS:
        if not any(sw["radio"] == radio for sw in st["switches"]):
            continue
        desc = [
            M(
                "hs.rfkill.desc",
                "The box switches {radio} on at every start unless you select otherwise here. "
                "As the image has it: the box leaves its rfkill switches alone.",
                radio=rfkill.RADIO_WORDS[radio],
            )
        ]
        if line and radio == "wifi":
            desc.append(
                M(
                    "hs.rfkill.power-line",
                    "On this board, {switch} is the WiFi chip's power. Off takes Bluetooth off too.",
                    switch=line,
                )
            )
        if line and radio == "bluetooth" and s["wifi"] == "off":
            desc.append(
                M(
                    "hs.rfkill.bluetooth-chip-off",
                    "WiFi is set to off, which takes the chip off, so Bluetooth stays off whatever is set here.",
                )
            )
        groups.append(
            {
                "key": f"rfkill-{radio}",
                "label": M("hs.rfkill", "{radio} radio", radio=rfkill.RADIO_WORDS[radio]),
                "desc": sentences(*desc),
                "current": s[radio],
                "options": [
                    {"value": v, "label": RFKILL_LABEL[v], "choice": f"rfkill:{radio}:{v}"} for v in rfkill.CHOICES
                ],
            }
        )
    return groups


def check_rfkill(st=None):
    """The radios' rfkill switches: one blocked or unblocked against its radio's setting, or held by the board."""
    st = rfkill_status() if st is None else st
    if not st:
        return []
    from irate_box.root import rfkill

    ours = [sw for sw in st["switches"] if sw["radio"]]
    if not ours:
        return []
    title = M("rfkill.title", "Radio switches")
    go = _go("health-settings", RECOVERY, f"hs-rfkill-{ours[0]['radio']}")
    hard = [sw for sw in ours if sw["hard"]]
    odds = [sw for sw in ours if rfkill.at_odds(sw, st["settings"])]
    out = []
    if odds:

        def said(sw):
            what = M("rfkill.blocked", "blocked") if sw["soft"] else M("rfkill.unblocked", "unblocked")
            if sw["power_line"]:
                return M("rfkill.odd.line", "{name} (the WiFi chip's power) is {what}", name=sw["name"], what=what)
            return M(
                "rfkill.odd",
                "{name} ({radio}) is {what}",
                name=sw["name"],
                radio=rfkill.RADIO_WORDS[sw["radio"]],
                what=what,
            )

        wifi_off = any(sw["radio"] == "wifi" and sw["soft"] for sw in odds)
        out.append(
            finding(
                "rfkill",
                title,
                "problem" if wifi_off else "warn",
                M(
                    "rfkill.odds",
                    "Not as set: {list}.",
                    list=[said(sw) for sw in odds],
                ),
                M(
                    "rfkill.odds.why",
                    "The box sets each radio's switches at every start. Something changed them since: the image's "
                    "own tools, a program, or a person.",
                ),
                M("rfkill.odds.fix", "Set them again, or select otherwise on Recovery and monitoring."),
                commands=["rfkill list"],
                actions=[action("rfkill-apply", M("rfkill.apply", "Set them again"))],
                go=go,
                glance=M("rfkill.odds.glance", "A radio is not as set."),
            )
        )
    if hard:
        out.append(
            finding(
                "rfkill:hard",
                title,
                "warn",
                M(
                    "rfkill.hard",
                    "A switch on the board holds a radio off: {names}. Software cannot release it.",
                    names=", ".join(sw["name"] for sw in hard),
                ),
                fix=M("rfkill.hard.fix", "Look for a radio switch or jumper on the board or its case."),
                commands=["rfkill list"],
            )
        )
    if out:
        return out
    s, chosen = st["settings"], st.get("chosen") or []
    present = [r for r in rfkill.RADIOS if any(sw["radio"] == r for sw in ours)]
    return [
        finding(
            "rfkill",
            title,
            "ok",
            M(
                "rfkill.ok",
                "As set: {list}.",
                list=[RFKILL_SAID[s[r]](rfkill.RADIO_WORDS[r]) for r in present],
            ),
            notes=[]
            if set(present) <= set(chosen)
            else [
                M(
                    "rfkill.default",
                    "An administrator has not selected a setting yet, so the box switches them on at every start.",
                )
            ],
            go=go,
            glance=M("rfkill.ok.glance", "Radios as set."),
        )
    ]


def check_watchers(now=None):
    """What the watchers saw and did in the last day, in one time line (watchlog): the uplink
    watchdog's outages, causes and steps, crash watch's radio failures, the driver's own resets, and
    why each boot happened. Refused so the owner can follow one incident from start to end."""
    from irate_box.hub import watchlog

    now = now or time.time()
    day = watchlog.read(since=now - 86400, kinds=watchlog.NOTABLE)
    title = M("watchers.title", "What the watchers did")
    if not day:
        return [
            finding(
                "watchers",
                title,
                "ok",
                M("watchers.none", "Nothing recorded in the last day: no outage, no radio failure and no restart."),
            )
        ]
    boots = [e for e in day if e.get("kind") == "boot"]
    steps = [e for e in day if e.get("kind") == "repair"]
    return [
        finding(
            "watchers",
            title,
            "ok",
            M(
                "watchers.boots",
                {
                    "one": "In the last day, the box started {n} time.",
                    "other": "In the last day, the box started {n} times.",
                },
                n=len(boots),
            ),
            notes=[
                M(
                    "watchers.repairs",
                    {"one": "The watchdog made {n} repair.", "other": "The watchdog made {n} repairs."},
                    n=len(steps),
                ),
                M("watchers.latest", "The latest events, oldest first, are below. Every watcher writes to one log."),
            ],
            commands=[f"sudo tail -n 100 {watchlog.LOG}"],
            evidence=[watchlog.line(e).split(" ", 1)[1][:200] for e in day[-12:]],
        )
    ]


# The watchdogs that look after the box itself, and how they say they're alive: each writes a status with its time,
# crash watch every 5 minutes, the uplink watchdog at each of its checks. (unit, its status, how many seconds of
# silence mean it is stuck.)
PROC = Path(os.environ.get("HUB_PROC", "/proc"))


def _watchdogs():
    from irate_box.hub import uplink
    from irate_box.root import crashwatch

    try:
        every = int((json.loads(uplink.status_path().read_text()).get("settings") or {}).get("check") or 60)
    except (OSError, ValueError, AttributeError, TypeError):
        every = 60
    return [
        ("irate-box-crashwatch.service", M("alive.crashwatch", "Crash watch"), crashwatch.STATUS, 3 * 300),
        (
            "irate-box-uplink.service",
            M("alive.uplink", "The uplink watchdog"),
            uplink.status_path(),
            max(300, 3 * every),
        ),
    ]


def _stuck_in_kernel(pid):
    """The process and its children waiting in the kernel and not to be woken (state D), each as
    "name (pid) in wchan": the sign of a lock that something holds and never lets go."""
    out = []
    try:
        procs = [d for d in os.listdir(PROC) if d.isdigit()]
    except OSError:
        return out
    for d in procs:
        try:
            stat = (PROC / d / "stat").read_text()
        except OSError:
            continue
        name, rest = stat[stat.find("(") + 1 : stat.rfind(")")], stat[stat.rfind(")") + 2 :].split()
        if len(rest) < 2 or (d != str(pid) and rest[1] != str(pid)) or rest[0] != "D":
            continue
        try:
            wchan = (PROC / d / "wchan").read_text().strip() or "?"
        except OSError:
            wchan = "?"
        out.append(f"{name} ({d}) waiting in the kernel at {wchan}")
    return out


def _running_for(unit):
    """Seconds since the unit last became active (systemd's monotonic clock against the uptime); 0 if unknown."""
    try:
        since = int(run("systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value", unit, timeout=10).stdout)
        up = float((PROC / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0
    return max(0.0, up - since / 1e6) if since else 0


def check_watchdogs_alive(now=None):
    """A watchdog can hang behind a lock in the kernel (seen on a Pi: a WiFi driver's scan held the
    radio's lock, crash watch waited on it holding the network's, and all that touched the network queued behind).
    Its service then still reads active, and the hardware watchdog is still fed: nothing notices. Each watchdog
    writes its time; one active but silent for three of its rounds is stuck. Files and systemctl only, with time
    limits: this check must not wait on the same lock."""
    now = now or time.time()
    out = []
    for unit, name, status, quiet in _watchdogs():
        active = run("systemctl", "is-active", "--quiet", unit, timeout=10).returncode == 0
        if not active:
            continue  # stopped or failed: check_units says so
        try:
            at = float(json.loads(status.read_text()).get("at") or 0)
        except (OSError, ValueError, AttributeError, TypeError):
            at = 0
        if at and now - at < quiet:
            continue
        if not at and _running_for(unit) < quiet:
            continue  # just started: its first report is still to come
        pid = run("systemctl", "show", "-p", "MainPID", "--value", unit, timeout=10).stdout.strip()
        stuck = _stuck_in_kernel(pid) if pid.isdigit() and pid != "0" else []
        silent = (
            M("alive.silent", "{name} runs, but has not reported since {at}.", name=name, at=when(at))
            if at
            else M("alive.never", "{name} runs, but has not reported since it started.", name=name)
        )
        out.append(
            finding(
                f"alive:{unit.removesuffix('.service')}",
                M("alive.title", "{name}: running, but silent", name=name),
                "problem",
                silent,
                M(
                    "alive.why",
                    "While it waits, it does not watch. A stuck network lock also stops SSH and new network "
                    "connections, and the hardware watchdog does not restart the box.",
                ),
                M(
                    "alive.fix",
                    "Restart the box. If the restart does not finish, switch the box off and on. Then look in the "
                    "kernel's log of that boot for a blocked task.",
                ),
                notes=[M("alive.in-kernel", "It is waiting in the kernel, as below: a lock that is not released.")]
                if stuck
                else [],
                commands=["journalctl -k -b -1 | grep -A 20 'blocked for more than'"],
                evidence=stuck or [str(status)],
            )
        )
    return out


# --- package files and irate-box's own tree: damage dpkg and git can name -------------------------------
# The weekly check (notes: box-doctor-hardening-plan Part 1, step 1). A package's files never change, and irate-box's
# tree is held against the updater's checkout at the installed commit. Read only, at the doctor's low priority; after a
# boot the startup packages alone, so a run from /admin stays quick. Box 190's card was not caught by this: none of its
# 55 files was a package file or under /opt/irate-box, but the check is the cheapest whole-system reading of the
# box's files, and when it does find damage it names the card.
UPDATE_SRC = Path(os.environ.get("HUB_UPDATE_DIR", "/var/cache/irate-box/src"))
PACKAGE_CHECK = CONTROL / "package-check.json"
# What the box boots and serves with: install.sh's own package list and nginx, the base system, and the network.
STARTUP_PACKAGES = (
    "systemd",
    "libc6",
    "dpkg",
    "apt",
    "python3",
    "curl",
    "ca-certificates",
    "git",
    "unzip",
    "fcgiwrap",
    "cgit",
    "python3-markdown",
    "libjs-highlight.js",
    "iw",
    "dnsmasq-base",
    "nftables",
    "procps",
    "gpgv",
    "nginx",
    "openssl",
    "network-manager",
    "wpasupplicant",
    "hostapd",
)


def _installed_commit():
    """The commit in VERSION (git describe output: abc1234, or v1.2-3-gabc1234, maybe -dirty), or None."""
    try:
        first = (CODE / "VERSION").read_text().split()[0].removesuffix("-dirty")
    except (OSError, IndexError):
        return None
    match = re.search(r"-g([0-9a-f]{7,40})$", first) or re.match(r"^([0-9a-f]{7,40})$", first)
    return match.group(1) if match else None


def _dep_names(text):
    """The package names a Depends field names, one for each alternative group (the first alternative)."""
    out = []
    for clause in text.split(","):
        name = re.split(r"[\s:(]", clause.split("|")[0].strip(), maxsplit=1)[0]
        if name:
            out.append(name)
    return out


def startup_packages():
    """The startup packages and their dependencies, recursively, as dpkg records them. Missing packages are left
    out: a box served by Caddy has no nginx, and one without an add-on's package is still healthy."""
    said = run("dpkg-query", "-W", "-f=${Package}\t${Pre-Depends}\t${Depends}\n", timeout=120)
    if said.returncode:
        raise OSError((said.stderr or said.stdout or "dpkg-query failed").strip()[-200:])
    deps = {}
    for line in (said.stdout or "").splitlines():
        name, _, rest = line.partition("\t")
        if name:
            deps[name] = _dep_names(rest)
    want, seen = [p for p in STARTUP_PACKAGES if p in deps], set()
    while want:
        name = want.pop()
        if name in seen:
            continue
        seen.add(name)
        want.extend(d for d in deps.get(name, ()) if d in deps and d not in seen)
    if not seen:
        raise OSError("dpkg-query named none of the startup packages")
    return sorted(seen)


def _dpkg_verify(packages=()):
    """dpkg --verify's findings as (path, conffile): the files whose recorded checksum does not match. A line whose
    nine-character marker is all "?" is one dpkg could not check (unreadable or unrecorded), not damage. A "5" is a
    checksum mismatch, and an "m" at the front a missing file."""
    said = run("dpkg", "--verify", *packages, timeout=900)
    if said.returncode not in (0, 1):
        raise OSError((said.stderr or said.stdout or "dpkg --verify failed").strip()[-200:])
    out = []
    for line in (said.stdout or "").splitlines():
        marker, _, rest = line.partition(" ")
        if not marker or not rest:
            continue
        conffile = rest.startswith("c ")
        path = rest[2:].strip() if conffile else rest.strip()
        if path and (marker.startswith("m") or "5" in marker):
            out.append({"path": path, "conffile": conffile})
    return out


def _git_blob(path):
    """The blob SHA-1 git names a file by: its header needs the size before the data."""
    sha = hashlib.sha1(usedforsecurity=False)
    sha.update(f"blob {path.stat().st_size}\0".encode())
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def _git_tree():
    """irate-box's files at the installed commit, as {relative path: blob SHA-1}, from the updater's checkout; or
    (None, message) when the commit or the checkout is not there to check against."""
    commit = _installed_commit()
    if not commit:
        return None, M("package-tree.no-version", "The installed commit is not recorded in {path}.", path=str(CODE))
    full = run("git", "-C", str(UPDATE_SRC), "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", timeout=60)
    if full.returncode != 0 or not full.stdout.strip():
        return None, M(
            "package-tree.no-checkout",
            "The updater's checkout does not have the installed commit {commit}.",
            commit=commit,
        )
    said = run("git", "-C", str(UPDATE_SRC), "ls-tree", "-r", "-z", commit, timeout=120)
    if said.returncode:
        return None, M(
            "package-tree.no-tree",
            "The updater's checkout could not list the installed commit {commit}: {why}",
            commit=commit,
            why=(said.stderr or said.stdout or "").strip()[-200:],
        )
    out = {}
    for entry in (said.stdout or "").split("\0"):
        meta, _, relative = entry.partition("\t")
        fields = meta.split()
        if len(fields) >= 3 and fields[1] == "blob":
            out[relative] = fields[2]
    return out, None


def _housecall_note():
    """The Housecall doctor's fuller examination, where that doctor is installed."""
    if (CODE / "irate_box/root/housecall.py").exists():
        return M(
            "packages.housecall",
            "The Housecall doctor can examine the whole card and name every file it damaged.",
        )
    return None


ARMBIAN_RELEASE = Path(os.environ.get("HUB_ARMBIAN_RELEASE", "/etc/armbian-release"))
# Files changed on every box by design, whatever the card: generated by the system, or set by the box itself.
CHANGED_BY_DESIGN = re.compile(
    r"^/(usr/)?lib/modules/[^/]+/modules\.[a-z.]+$"  # depmod's indexes, made again at every module install
    r"|^/etc/systemd/journald\.conf$"  # the journal's settings: irate-box's own (SD card writes)
)
# On Armbian (and images built on it, as mPWRD-OS), the files its build and its log-in-RAM setup always change.
ARMBIAN_BY_DESIGN = re.compile(
    r"^/etc/(armbian-release|os-release|issue|issue\.net)$"  # its release, written over base-files'
    r"|^/usr/share/doc/base-files/changelog\.gz$"
    r"|^/usr/src/linux-headers-[^/]+/(\.config|include/generated/.*|scripts/.*)$"  # its headers, built on the board
    r"|^/etc/logrotate\.conf$|^/etc/logrotate\.d/[^/]+$"  # armbian-ramlog's rotation, set in every file
    r"|^/etc/default/useradd$|^/etc/initramfs-tools/initramfs\.conf$"  # its image's own settings
)


def _by_design(path):
    return bool(CHANGED_BY_DESIGN.match(path) or (ARMBIAN_RELEASE.exists() and ARMBIAN_BY_DESIGN.match(path)))


def _package_findings(damaged, scope):
    """The findings for dpkg --verify's list. A file that reads back as 0xFF is damage; one that reads otherwise
    was changed, by its package's own build or by someone editing it, and only the Housecall doctor can tell which.
    A conffile is the same question, and dpkg already names it apart."""
    broken, changed, conffiles, expected = [], [], [], 0
    for item in damaged:
        path = Path(item["path"])
        if _by_design(item["path"]):
            try:
                if path.exists() and _reads_all_ff(path):
                    broken.append(item)  # changed by design, but this is a card's 0xFF: damage
                    continue
            except OSError:
                pass
            expected += 1
            continue
        if item["conffile"]:
            conffiles.append(item)
            continue
        try:
            if not path.exists():
                changed.append(item)  # removed by the board's own build, or by someone: not a card's 0xFF
            elif _reads_all_ff(path):
                broken.append(item)
            else:
                changed.append(item)
        except OSError:
            changed.append(item)  # unreadable: not the 0xFF a card returns
    out = []
    if broken:
        out.append(
            finding(
                "package-files",
                M("packages.title", "Package files"),
                "problem",
                M(
                    "packages.damaged",
                    {
                        "one": "The card returns wrong contents for {n} system file: it can be failing.",
                        "other": "The card returns wrong contents for {n} system files: it can be failing.",
                    },
                    n=len(broken),
                ),
                M(
                    "packages.damaged.why",
                    "A package's files never change. When one does, the box's copy is damaged, or something wrote "
                    "over it. A card that loses writes does this.",
                ),
                M(
                    "packages.damaged.fix",
                    "Replace the SD card, then reinstall the damaged packages. The Housecall doctor can examine the "
                    "whole card first.",
                ),
                notes=[n for n in (_housecall_note(),) if n],
                commands=[f"sudo {CODE}/irate-box housecall --this-card"]
                if (CODE / "irate_box/root/housecall.py").exists()
                else [],
                evidence=[r["path"] for r in broken],
                go=GO_CARD_WRITES,
            )
        )
    if changed:
        out.append(
            finding(
                "package-changed",
                M("packages.changed.title", "Package files that changed"),
                "warn",
                M(
                    "packages.changed",
                    {
                        "one": "{n} package file has changed since its package was installed: edited, generated by "
                        "the system, removed, or damaged.",
                        "other": "{n} package files have changed since their packages were installed: edited, "
                        "generated by the system, removed, or damaged.",
                    },
                    n=len(changed),
                ),
                M(
                    "packages.changed.why",
                    "A package's checksum cannot tell an edit or a generated file from damage. The files this image "
                    "always changes are left out, so any change listed is worth a look.",
                ),
                M(
                    "packages.changed.fix",
                    "Check the files listed. If none is a change you made, the Housecall doctor can tell damage from "
                    "a generated file.",
                ),
                notes=[n for n in (_housecall_note(),) if n],
                commands=[f"sudo {CODE}/irate-box housecall --this-card"]
                if (CODE / "irate_box/root/housecall.py").exists()
                else [],
                evidence=[r["path"] for r in changed],
            )
        )
    if conffiles:
        out.append(
            finding(
                "package-conffiles",
                M("conffiles.title", "Configuration files from packages"),
                "warn",
                M(
                    "conffiles.changed",
                    {
                        "one": "{n} configuration file has changed since its package was installed: edited, or damaged.",
                        "other": "{n} configuration files have changed since their packages were installed: "
                        "edited, or damaged.",
                    },
                    n=len(conffiles),
                ),
                M(
                    "conffiles.changed.why",
                    "A package's checksum cannot tell an edit from damage: both change the file. The box's own "
                    "changes are expected. Any other change is worth a look.",
                ),
                M(
                    "conffiles.changed.fix",
                    "Check each file against its package's version, and put the package's own version back if the "
                    "change was not yours.",
                ),
                evidence=[r["path"] for r in conffiles],
            )
        )
    if expected:
        left_out = M(
            "packages.by-design",
            {
                "one": "{n} more file changed as this image always changes it, and is not counted.",
                "other": "{n} more files changed as this image always changes them, and are not counted.",
            },
            n=expected,
        )
        for f in out:
            f["notes"] = [*f.get("notes", []), left_out]
    if not out:
        out.append(
            finding(
                "package-files",
                M("packages.title", "Package files"),
                "ok",
                M(
                    "packages.ok-startup",
                    "The startup packages' files match their recorded checksums ({n} packages checked).",
                    n=len(scope),
                )
                if scope
                else M("packages.ok", "Every package file matches its recorded checksum."),
                notes=[left_out] if expected else [],
            )
        )
    return out


def _tree_findings():
    """irate-box's own files against the updater's checkout: damage, or a hand edit, the same to git."""
    refs, why = _git_tree()
    if refs is None:
        return [
            finding(
                "irate-box-tree",
                M("package-tree.title", "irate-box's own files"),
                "warn",
                why,
                M(
                    "package-tree.unchecked.why",
                    "The box cannot check its own files against the copy the update came from.",
                ),
                M(
                    "package-tree.unchecked.fix",
                    "Run the updates check, and install the same update again if the checkout is missing.",
                ),
                evidence=[str(UPDATE_SRC)],
                go=GO_UPDATES,
            )
        ]
    bad, missing = [], []
    for relative, want in refs.items():
        try:
            got = _git_blob(CODE / relative)
        except OSError:
            missing.append(relative)
            continue
        if got != want:
            bad.append(relative)
    if not bad and not missing:
        return [
            finding(
                "irate-box-tree",
                M("package-tree.title", "irate-box's own files"),
                "ok",
                M("package-tree.ok", "irate-box's files match the installed commit ({n} files).", n=len(refs)),
            )
        ]
    return [
        finding(
            "irate-box-tree",
            M("package-tree.title", "irate-box's own files"),
            "problem",
            M(
                "package-tree.damaged",
                {
                    "one": "{n} of irate-box's files differs from the installed commit, or is missing.",
                    "other": "{n} of irate-box's files differ from the installed commit, or are missing.",
                },
                n=len(bad) + len(missing),
            ),
            M(
                "package-tree.damaged.why",
                "The box's code should be exactly the copy the update came from. A difference is damage, unless "
                "someone edited the code by hand.",
            ),
            M(
                "package-tree.damaged.fix",
                "Install the same update again. If the files are damaged, replace the SD card and check the whole "
                "card with the Housecall doctor first.",
            ),
            notes=[n for n in (_housecall_note(),) if n],
            commands=[f"sudo {CODE}/irate-box housecall --this-card"]
            if (CODE / "irate_box/root/housecall.py").exists()
            else [],
            evidence=[f"{p} (missing)" for p in missing] + bad,
            go=GO_UPDATES,
        )
    ]


def _package_cache():
    try:
        data = json.loads(PACKAGE_CHECK.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_package_cache(findings):
    from irate_box.root import safeio

    safeio.mkdir(CONTROL, mode=0o755)
    safeio.write(PACKAGE_CHECK, json.dumps({"at": time.time(), "boot": _boot_id(), "findings": findings}, indent=1))


def check_package_files(quick=False):
    """Package files and irate-box's own tree (notes: box-doctor-hardening-plan Part 1, step 1). The weekly full run
    verifies every package and the tree; a quick run, after a boot, verifies the startup packages and their
    dependencies once, keeping that boot's answer for the runs from /admin. Both read only, and the timer's run is
    already at low priority and idle I/O, so neither delays a service."""
    if quick:
        cached = _package_cache()
        if cached.get("boot") == _boot_id() and isinstance(cached.get("findings"), list):
            return cached["findings"]
        packages = startup_packages()
        found = _package_findings(_dpkg_verify(packages), packages)
        _write_package_cache(found)
        return found
    found = _package_findings(_dpkg_verify(), ()) + _tree_findings()
    _write_package_cache(found)
    return found


def scan(quick=False):
    """Every check. quick is the usual run: the builds' size kept from the last full run rather than walked (the
    Lyra's slow step: 132.6 of a 143.5 s run), and the books' headers without opening each in Kiwix. Each check's
    time is kept, so a slow one is named on the box (Part 1, step 0 of notes: the plan to bring the Lyra's 151 s run
    down)."""
    findings, timings = [], {}
    for check in (
        check_install,
        check_hub,
        check_clock,
        check_units,
        check_kiwix,
        check_web,
        check_port_80,
        check_git_addon,
        check_addon_front,
        check_addon_declared,
        check_uplink,
        check_inventory,
        check_hotspot,
        check_hotspot_per_account,
        check_wifi_stalls,
        check_space,
        check_card_writes,
        check_card_health,
        check_lost_writes,
        check_writes_kept,
        check_protected,
        check_builds,
        check_crashwatch,
        check_power,
        check_led,
        check_rfkill,
        check_watchers,
        check_watchdogs_alive,
        check_idle,
        check_usb_filesystems,
        check_package_files,
        check_hardening,
    ):
        start = time.monotonic()
        try:
            findings += check(quick=quick) if check in (check_kiwix, check_builds, check_package_files) else check()
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            findings.append(
                finding(
                    f"check:{check.__name__}",
                    M("check-failed.title", "Check: {name}", name=check.__name__.removeprefix("check_")),
                    "warn",
                    M("check-failed.error", "The check itself failed."),
                    evidence=[repr(exc)[:300]],
                )
            )
        timings[check.__name__.removeprefix("check_")] = round(time.monotonic() - start, 3)
    return {
        "at": time.time(),
        "findings": findings,
        "settings": doctor_settings(),
        "clock": clock_choice(),
        "timings": timings,
        "quick": quick,
    }


def _previous_report():
    try:
        data = json.loads((CONTROL / "health.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def run_report(full=None):
    """A run of the doctor, the timer's or /admin's: the quick checks, and the full one (the builds walked, the
    books opened in Kiwix) once a week. Written where /admin reads it, with when the last full run was."""
    previous = _previous_report()
    now = time.time()
    full_at = previous.get("full_at") if isinstance(previous.get("full_at"), (int, float)) else None
    if full is None:
        full = full_at is None or now - full_at > BOOK_CHECK_DAYS * 86400
    data = scan(quick=not full)
    data["full_at"] = now if full else full_at
    from irate_box.root import safeio

    safeio.mkdir(CONTROL, mode=0o755)
    safeio.write(CONTROL / "health.json", json.dumps(data, indent=2))
    return data


def run_check(full=None):
    """The timer's run (run_report), with the finding counts and the slowest checks in one line, for the journal."""
    data = run_report(full)
    full = not data.get("quick")
    counts = {s: sum(1 for f in data["findings"] if f["status"] == s) for s in ("problem", "warn")}
    slow = sorted(data["timings"].items(), key=lambda kv: kv[1], reverse=True)[:3]
    print(
        f"box doctor: {counts['problem']} problem(s), {counts['warn']} warning(s)"
        + (", full run" if full else "")
        + "".join(f", {name} {secs:.1f} s" for name, secs in slow if secs >= 1)
    )
    return data


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
        return {
            "snapshots": s["snapshots"],
            "panic": s["panic"],
            "watchdog": s["watchdog"],
            "watchdog_device": wd,
            "led": {"name": ls["led"], "show": ls["show"]} if ls else None,
            "groups": setting_groups(s, wd)
            + led_groups(ls, armbian_led_state() if ls else None)
            + rfkill_groups(rfkill_status()),
            "card_groups": card_groups(),
            "hardening_groups": hardening_groups(),
        }
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


# --- repairs ---------------------------------------------------------------------------------------


def fix(choice):
    if not CHOICE_RE.match(choice):
        raise Refused(M("repair.unknown", "The Box doctor has no repair called {choice}.", choice=choice))
    kind, _, arg = choice.partition(":")
    if kind == "clock-sources":
        from irate_box.root import clock

        return clock.apply(choice)
    if kind == "clock-undo":
        from irate_box.root import clock

        return clock.undo()
    if kind.startswith("crashwatch-"):
        from irate_box.root import crashwatch, recommended

        said = crashwatch.set_option(kind[len("crashwatch-") :], arg)
        recommended.record(kind, arg)  # the owner's own choice: never offered back
        return said
    if kind in ("health-timer", "lost-writes", "canary"):
        from irate_box.root import recommended

        on = arg == "on"
        said = {"health-timer": set_timer, "lost-writes": set_lostwrites, "canary": set_canary}[kind](on)
        recommended.record(kind, arg)  # the owner's own choice: never offered back
        return said
    if kind == "recommended-apply":
        from irate_box.root import recommended

        done = recommended.apply_all()
        return M(
            "rec.applied",
            {"one": "One recommended setting applied.", "other": "{n} recommended settings applied."},
            n=len(done),
        )
    if kind in ("led-show", "led-led"):
        from irate_box.root import led

        return led.set_option(kind[len("led-") :], arg)
    if kind == "led-flash":
        from irate_box.root import led

        return led.flash(arg)
    if kind == "rfkill":
        from irate_box.root import rfkill

        radio, _, value = arg.partition(":")
        return rfkill.set_option(radio, value)
    if kind == "rfkill-apply":
        from irate_box.root import rfkill

        changed = rfkill.apply()
        failed = [sw["name"] for sw, _, error in changed if error]
        if failed:
            return M("repair.rfkill.failed", "These switches could not be changed: {names}.", names=", ".join(failed))
        return M("repair.rfkill.done", "Every radio switch is as set.")
    if kind == "armbian-led":
        if not armbian_led_state() and arg == "off":
            raise Refused(
                M(
                    "repair.armbian-led.not-failing",
                    "{unit} is not failing here. There is nothing to disable.",
                    unit=ARMBIAN_LED,
                )
            )
        r = run("systemctl", "mask" if arg == "off" else "unmask", ARMBIAN_LED)
        if r.returncode:
            raise Refused(
                M(
                    "repair.systemctl-failed",
                    "systemctl could not change {unit}: {detail}",
                    unit=ARMBIAN_LED,
                    detail=r.stderr.strip()[-200:],
                )
            )
        if arg == "off":
            run("systemctl", "reset-failed", ARMBIAN_LED)
            return M(
                "repair.armbian-led.off",
                "{unit} is disabled (masked). To unmask it, select As the image has it.",
                unit=ARMBIAN_LED,
            )
        return M("repair.armbian-led.on", "{unit} is as the image has it again.", unit=ARMBIAN_LED)
    if kind == "other-restart":
        failed = [
            ln.split()[0]
            for ln in run("systemctl", "--failed", "--no-legend", "--plain").stdout.splitlines()
            if ln.split()
        ]
        if (
            arg not in failed
            or ours(arg)
            or not re.match(r"^[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]*\.(service|socket|timer|mount)$", arg)
        ):
            raise Refused(
                M(
                    "repair.other.not-failed",
                    "{unit} is not a failed unit that the Box doctor can start again.",
                    unit=arg,
                )
            )
        run("systemctl", "reset-failed", arg)
        r = run("systemctl", "start", arg)
        if r.returncode == 0:
            return M("repair.unit.started", "The box started {unit}.", unit=arg)
        raise Refused(
            M("repair.unit.start-failed", "{unit} failed to start: {detail}", unit=arg, detail=r.stderr.strip()[-200:])
            if r.stderr.strip()
            else M("repair.unit.start-failed.quiet", "{unit} failed to start again.", unit=arg)
        )
    if kind in ("unit-restart", "unit-enable"):
        if not ours(arg):
            raise Refused(
                M(
                    "repair.unit.not-ours",
                    "{unit} is not a unit of irate-box. The Box doctor restarts only its own units.",
                    unit=arg,
                )
            )
        if kind == "unit-enable":
            r = run("systemctl", "enable", arg)
            if r.returncode:
                raise Refused(
                    M(
                        "repair.unit.enable-failed",
                        "The box could not enable {unit} at boot: {detail}",
                        unit=arg,
                        detail=r.stderr.strip()[-200:],
                    )
                )
            return M("repair.unit.enabled", "{unit} is enabled at boot. It starts at the next boot.", unit=arg)
        run("systemctl", "reset-failed", arg)
        if arg == "irate-box-control.path":
            run("systemctl", "reset-failed", "irate-box-control.service")
        verb = "start" if arg.endswith((".path", ".timer", ".socket")) else "restart"
        r = run("systemctl", verb, arg, timeout=120)
        time.sleep(2)
        state = unit_props(arg).get("ActiveState")
        if r.returncode or state != "active":
            tail = journal_tail(arg, 3)
            raise Refused(
                M(
                    "repair.unit.not-running",
                    "{unit} failed to start. Its state is {state}. The end of its log: {log}",
                    unit=arg,
                    state=state,
                    log=" | ".join(tail),
                )
                if tail
                else M(
                    "repair.unit.not-running.no-log",
                    "{unit} failed to start. Its state is {state}. Read its log with the command journalctl -u {unit}.",
                    unit=arg,
                    state=state,
                )
            )
        return M("repair.unit.running", "{unit} is running again.", unit=arg)
    if kind == "kiwix-quarantine":
        src = ZIM / arg
        if not arg.endswith(".zim") or "/" in arg or not src.is_file():
            raise Refused(M("repair.kiwix.not-a-book", "{book} is not a book in {folder}.", book=arg, folder=str(ZIM)))
        # As the hub: both folders are the hub's, so root has no business there. Done as
        # root, a quarantine/ the hub had made a link was chowned to it, wherever it led.
        hub = ("runuser", "-u", HUB_USER, "--") if os.geteuid() == 0 else ()
        r = run(*hub, "mkdir", "-p", "--", str(QUARANTINE))
        if r.returncode == 0:
            r = run(*hub, "mv", "-n", "-T", "--", str(src), str(QUARANTINE / arg))
        if r.returncode or src.exists():
            raise Refused(
                M(
                    "repair.kiwix.not-moved",
                    "The box could not move {book} to {folder}: {detail}",
                    book=arg,
                    folder=f"{QUARANTINE}/",
                    detail=(r.stderr or "").strip()[-160:],
                )
            )
        moved = M(
            "repair.kiwix.quarantined",
            "Book quarantined: the box moved {book} to {folder}.",
            book=arg,
            folder=f"{QUARANTINE}/",
        )
        if readable_books():
            return sentences(moved, kiwix_rebuild())
        LIBRARY.unlink(missing_ok=True)
        run("systemctl", "disable", "--now", "kiwix.service")
        return sentences(
            moved,
            M(
                "repair.kiwix.emptied",
                "No readable book is left. The box emptied the library and stopped Kiwix until someone adds a book.",
            ),
        )
    if kind == "clock-set":
        return set_clock(int(arg))
    if kind in ("idle-off", "idle-on"):
        return idle_switch(arg, kind == "idle-on")
    if kind == "card-accounting-on":
        return card_accounting_on()
    if kind == "arch-remove":
        return arch_remove(arg)
    if kind == "card-speed-test":
        return card_speed_test()
    if kind == "flash-read":
        return flash_read()
    if kind == "card-scan":
        return card_scan_start()
    if kind == "card-memcg-on":
        return card_memcg_on()
    if kind == "bootkey":
        from irate_box.root import bootkey

        return bootkey.install_units(arg == "on")
    if kind == "retain":
        from irate_box.root import retain

        return retain.install_units(arg == "on")
    if kind == "retain-store":
        from irate_box.root import retain

        on, _, encoded = arg.partition(":")
        path = Path(retain.decode_store(encoded))
        # Only a place this box found itself, or one already in use (to switch off): the choice names a folder, and
        # root writes the copy there, the keys included only where that place's own rule allows them, so the folder
        # is never taken on the hub's word.
        found = {candidate: rule for candidate, _label, rule in retain.store_candidates()}
        if on == "on" and path not in found:
            raise Refused(M("retain.store.unknown", "That place is not one this box offers for the copy."))
        if on == "off" and path not in found and path not in retain.extra_stores():
            raise Refused(M("retain.store.unknown", "That place is not one this box offers for the copy."))
        return retain.set_store(str(path), on == "on", keys=found.get(path, "none"))
    if kind == "readonly":
        from irate_box.root import readonly

        return readonly.enable(arg == "on")
    if kind == "retain-keys":
        from irate_box.root import retain

        return retain.set_keys(arg)
    if kind == "retain-data":
        from irate_box.root import retain

        return retain.set_data_mb(arg)
    if kind == "card-set":
        from irate_box.root import recommended

        key, _, value = arg.partition(":")
        said = card_set(key, value)
        recommended.record(f"card-set:{key}", value)  # the owner's own choice: never offered back
        return said
    if kind in ("card-ram", "card-ram-off"):
        from irate_box.hub import cardhistory
        from irate_box.root import servicewrites

        unit, _, idx = arg.rpartition(":")
        found = cardhistory.card()
        if kind == "card-ram":
            paths = _unit_writes(unit, found[0] if found else None)[1]
        else:
            paths = servicewrites.owner().get(unit, [])
        if int(idx) >= len(paths):
            raise Refused(
                M(
                    "service-ram.gone",
                    "That path is not one of {unit}'s any more: look at the Box doctor again.",
                    unit=unit,
                )
            )
        return servicewrites.set_owner(unit, paths[int(idx)].split(" (")[0], on=kind == "card-ram")
    if kind == "card-ram-apply":
        from irate_box.root import servicewrites

        done = servicewrites.apply()
        return M("service-ram.applied", "The services' paths in RAM are in place ({n} changed).", n=len(done))
    if kind == "uplink-reconnect":
        # A link nobody watches, parked by NetworkManager (a password check failed and nobody answered): connected
        # by hand, which also lifts NetworkManager's stop on it.
        if not (Path("/sys/class/net") / arg).exists():
            raise Refused(M("repair.link.unknown", "{iface} is not a link on this box.", iface=arg))
        r = run("nmcli", "-w", "45", "dev", "connect", arg, timeout=60)
        out = (r.stdout + r.stderr).strip()
        if r.returncode:
            raise Refused(
                M(
                    "repair.link.failed",
                    "{iface} did not connect. NetworkManager said: {detail}",
                    iface=arg,
                    detail=out[-200:],
                )
                if out
                else M("repair.link.failed.quiet", "{iface} did not connect. nmcli failed.", iface=arg)
            )
        return (
            M(
                "repair.link.connected",
                "{iface} is connected. NetworkManager said: {detail}",
                iface=arg,
                detail=out[-160:],
            )
            if out
            else M("repair.link.connected.quiet", "{iface} is connected.", iface=arg)
        )
    if kind == "uplink-skip":
        from irate_box.hub import uplink

        iface, _, step = arg.partition(":")
        if step not in uplink.STEPS or not uplink.IFACE_RE.match(iface) or iface == "auto":
            raise Refused(M("repair.step.unknown", "{choice} does not name a link and a recovery action.", choice=arg))
        chosen = uplink.load_settings()
        steps = set(chosen.get("steps_off", {}).get(iface, [])) | {step}
        chosen.setdefault("steps_off", {})[iface] = [x for x in uplink.STEPS if x in steps]
        uplink.save_settings(chosen)  # the watchdog takes it on its next look
        return M(
            "repair.step.off",
            "Recovery action disabled for {iface}: {step}. The watchdog no longer tries it there.",
            iface=iface,
            step=STEP_LABEL.get(step, step),
        )
    try:
        if choice == "rtc-find":
            f = rtc.find()
            if f["found"]:
                return M(
                    "repair.rtc.found",
                    "The search found {modules}.",
                    modules=[
                        M(
                            "repair.rtc.found-one",
                            "the {chip} on bus {bus} at {addr}",
                            chip=x["label"],
                            bus=x["bus"],
                            addr=x["addr"],
                        )
                        for x in f["found"]
                    ],
                )
            return (
                M("repair.rtc.none", "The search found no clock module.")
                if f["buses"]
                else M("repair.rtc.no-bus", "The search found no clock module. No I2C bus is enabled.")
            )
        if choice == "rtc-look":
            return rtc.look(asked=True)
        if kind == "rtc-setup":
            chip, bus, addr = arg.split(":")
            return rtc.setup(chip, int(bus), addr)
        if choice == "rtc-save":
            return rtc.save()
        if choice == "rtc-remove":
            return rtc.remove()
    except rtc.RtcError as exc:
        raise Refused(exc.message) from exc
    if kind == "kiwix-rebuild":
        return kiwix_rebuild()
    if kind == "kiwix-off":
        run("systemctl", "disable", "--now", "kiwix.service")
        run("systemctl", "reset-failed", "kiwix.service")
        return M(
            "repair.kiwix.off",
            "The box stopped Kiwix and disabled it at boot. A library rebuilt with a readable book starts it again.",
        )
    raise Refused(M("repair.unknown", "The Box doctor has no repair called {choice}.", choice=choice))


# --- plain text ------------------------------------------------------------------------------------

MARK = {"ok": "ok     ", "note": "note   ", "warn": "review ", "problem": "problem"}


def report(data, problems_only=False):
    lines = []
    for f in data["findings"]:
        if problems_only and f["status"] in ("ok", "note"):
            continue
        said = english(f)
        lines.append(f"{MARK[f['status']]}  {said['title']}: {said['detail']}")
        if said["fix"] and f["status"] != "ok":
            lines.append(f"         what to do: {said['fix']}")
        for a in f["actions"]:
            if a["choice"] == "clock-set":
                lines.append(
                    f"         or: sudo {CODE}/irate-box health fix clock-set:$(date -d 'YYYY-MM-DD HH:MM' +%s)   (from a terminal: give the right time)"
                )
            elif not a["choice"].startswith("rerun-install"):
                lines.append(f"         or: sudo {CODE}/irate-box health fix {a['choice']}   ({render(a['label'])})")
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
    elif argv == ["card-scan"]:  # the whole-card read, in its own unit (card_scan_start)
        bad = card_scan()
        print(f"unreadable places: {bad}")
    elif argv == ["run"]:  # the timer's (irate-box-boxdoctor.timer)
        run_check()
    elif argv == ["run-full"]:
        run_check(full=True)
    elif argv == ["boot-check"]:  # the boot's (irate-box-lostwrites.service)
        found = boot_check()
        print(f"lost writes: {len(found)} finding(s)")
    elif argv == ["canary"]:  # the test file's turn (irate-box-canary.timer)
        canary_tick()
    elif argv == ["install-units"]:  # install.sh: the units, as the owner's choices leave them
        try:
            install_units()
        except ValueError as exc:
            sys.exit(f"health.py install-units: {exc}")
    elif argv == ["--json"]:
        print(json.dumps(scan(), indent=2))
    elif len(argv) == 2 and argv[0] == "fix":
        if argv[1] == "rerun-install":
            sys.exit(
                "Run the installer from the checkout instead: sudo ./install.sh (it reuses this box's recorded options)."
            )
        if argv[1] == "rerun-install-port-80":
            sys.exit("Run the installer from the checkout instead: sudo ./install.sh --port 80 (updates then keep it).")
        try:
            print(render(fix(argv[1])))
        except ValueError as exc:
            sys.exit(f"health.py fix: {exc}")
    else:
        sys.exit(
            "usage: health.py [summary | --json | run | run-full | boot-check | canary | install-units | fix CHOICE]"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
