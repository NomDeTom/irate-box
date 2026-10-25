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

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

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
                M("armbian-led.title", "The image's LED service failed"),
                "warn",
                M("armbian-led.failed", "{unit} fails at every start on this board.", unit=ARMBIAN_LED),
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
                "warn",
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


def check_kiwix():
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
        why = zim_header_problem(b) or kiwix_reads(b)
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


def check_addon_front():
    """Each running web add-on (a manifest's status with a port, a route and a unit), asked through the web server
    as a guest's browser would (webprobe.through_front): does it answer there. A 502, 503 or 504, or nothing,
    is a problem, with a restart. Whether the web server keeps its sign-in is the Security doctor's."""
    from irate_box.hub import manifests
    from irate_box.root import webprobe

    try:
        found = manifests.load()
    except (manifests.ManifestError, OSError):
        return []
    out = []
    for m in found:
        st = m.get("status") or {}
        if not (st.get("port") and st.get("path") and st.get("unit")):
            continue
        unit = st["unit"].replace("@hub.", f"@{HUB_USER}.")
        if unit_props(unit).get("ActiveState") != "active":
            continue  # not running: the units check says so
        title = named(st.get("name") or m["id"], M("addon-front.role", "through the web server"))
        r, url = webprobe.through_front(st["path"])
        if not r["http"] or r["status"] in (502, 503, 504):
            out.append(
                finding(
                    f"addon-front:{m['id']}",
                    title,
                    "problem",
                    M(
                        "addon-front.down",
                        "It runs, but it does not answer through the web server.",
                    ),
                    M(
                        "addon-front.down.why",
                        "Its page shows an error, or nothing, to everyone who opens it.",
                    ),
                    M("addon-front.down.fix", "Start it again. If that does not help, run the installer again."),
                    evidence=[f"GET {url}: {r['status'] if r['http'] else r['error']}"],
                    actions=[action(f"unit-restart:{unit}", RESTART), RERUN],
                )
            )
        else:
            out.append(
                finding(
                    f"addon-front:{m['id']}",
                    title,
                    "ok",
                    M("addon-front.up", "It answers through the web server."),
                    evidence=[f"GET {url}: {r['status']}"],
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
        for i in secdoctor.run_declared(a, doctor="box"):
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


def check_space():
    out = []
    for fid_title, path, need in (
        (M("space.system.title", "Space on the system card"), Path("/"), 300),
        (M("space.state.title", "Space for the hub's state"), STATE, 200),
    ):
        if not path.exists():
            continue
        free = shutil.disk_usage(path).free >> 20
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
        big = share > 0.2
        out.append(
            finding(
                "builds-disk",
                M("builds-disk.title", "Space used by firmware builds"),
                "warn" if big else "ok",
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
    look = action("rtc-find", M("rtc.find", "Look for a clock module"))
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
    failed = any(e.get("kind") in ("failed", "driver-reset") for e in day)
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
            "warn" if failed else "ok",
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


def scan():
    findings = []
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
        check_builds,
        check_crashwatch,
        check_led,
        check_watchers,
        check_watchdogs_alive,
        check_idle,
        check_usb_filesystems,
    ):
        try:
            findings += check()
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
        return {
            "snapshots": s["snapshots"],
            "panic": s["panic"],
            "watchdog": s["watchdog"],
            "watchdog_device": wd,
            "led": {"name": ls["led"], "show": ls["show"]} if ls else None,
            "groups": setting_groups(s, wd) + led_groups(ls, armbian_led_state() if ls else None),
        }
    except Exception:  # noqa: BLE001 - the report goes out without it
        return None


# --- repairs ---------------------------------------------------------------------------------------

CHOICE_RE = re.compile(
    r"^(unit-restart|unit-enable|kiwix-quarantine):[A-Za-z0-9@._-]+$|^(kiwix-rebuild|kiwix-off)$"
    r"|^other-restart:[A-Za-z0-9@_][A-Za-z0-9@_.:\\-]{0,200}$"
    r"|^crashwatch-(snapshots|panic|watchdog):(on|off)$"
    r"|^clock-set:\d{10}$|^(rtc-find|rtc-save|rtc-remove)$|^rtc-setup:[a-z0-9]{3,12}:\d{1,3}:0x[0-9a-f]{2}$"
    r"|^clock-sources:(image|chrony):[01]:[01]:[01]:[01]:(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})$|^clock-undo$"
    r"|^uplink-skip:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}:(reconnect|restart|radio|reboot)$"
    r"|^idle-(off|on):(meshtasticd|ModemManager|bluetooth)\.service$"
    r"|^led-show:(leave|alive|network|all)$|^led-(led|flash):[A-Za-z0-9:._@-]{1,64}$|^armbian-led:(on|off)$"
    r"|^uplink-reconnect:[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$"
)


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
        from irate_box.root import crashwatch

        return crashwatch.set_option(kind[len("crashwatch-") :], arg)
    if kind in ("led-show", "led-led"):
        from irate_box.root import led

        return led.set_option(kind[len("led-") :], arg)
    if kind == "led-flash":
        from irate_box.root import led

        return led.flash(arg)
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

MARK = {"ok": "ok     ", "warn": "note   ", "problem": "problem"}


def report(data, problems_only=False):
    lines = []
    for f in data["findings"]:
        if problems_only and f["status"] == "ok":
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
        sys.exit("usage: health.py [summary | --json | fix CHOICE]")


if __name__ == "__main__":
    main(sys.argv[1:])
