# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Updates doctor's Add-ons and sources section (hub_control.addons_findings): a source's error
(fresh and older than three intervals), a source with no successful check, a newer release the policy
would install, the exceptions that stay quiet, and a packaged add-on's waiting packages. The librarian's
state and apt are stood in. python3 tests/sim_updates_addons.py"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="upaddons-"))
for d in ("state/control", "etc", "cache"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_RUN_DIR=str(T / "run"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_WEB_SERVER="nginx",
)
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args, message_ids
from irate_box.root import hub_control as H

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


now = time.time()
sources = [
    {"name": "good", "enabled": True},
    {"name": "error-fresh", "enabled": True},
    {"name": "error-old", "enabled": True},
    {"name": "stale", "enabled": True},
    {"name": "waiting", "enabled": True},
    {"name": "off", "enabled": False},
    {"name": "deferred", "enabled": True},
    {"name": "sharp", "enabled": True},
    {"name": "recent-fail", "enabled": True},
]
status = {
    "good": {"last_check": iso(3600), "last_ok": iso(3600), "current": {"version": "1"}, "latest": {"version": "1"}},
    "error-fresh": {"error": "GitHub said no", "error_since": iso(3600), "last_ok": iso(7200)},
    "error-old": {"error": "still no", "error_since": iso(4 * 86400), "last_ok": iso(5 * 86400)},
    "stale": {"last_check": iso(4 * 86400), "last_ok": iso(4 * 86400)},
    "waiting": {
        "last_check": iso(3600),
        "last_ok": iso(3600),
        "current": {"version": "1"},
        "latest": {"version": "2", "label": "v2"},
    },
    "deferred": {"deferred": iso(3600)},
    "sharp": {"error": "old failure", "error_since": iso(4 * 86400), "last_ok": iso(5 * 86400)},
}
cfg = {"policy": {"check_every_hours": 24, "auto_install": 2}, "sources": sources}
(T / "state" / "library").mkdir(parents=True)
(T / "state" / "library" / "last-run.json").write_text(
    '{"last_scheduled": {"errors": ["recent-fail: error: GitHub API rate limit reached"]}}'
)
apt = """Inst ngircd [26] (27 Debian:13/stable [armhf])
Inst mosquitto [2] (3 Debian-Security:13/stable-security [armhf])"""
addons = {
    "irc": {"addon": {"title": "IRC server", "packages": ["ngircd"]}},
    "mqtt": {"addon": {"title": "MQTT broker", "packages": ["mosquitto"]}},
    "off-addon": {"addon": {"title": "Off add-on", "packages": ["ngircd"]}},
}


def apt_run(*args, **kwargs):
    return type("R", (), {"stdout": apt, "returncode": 0})()


with (
    mock.patch.object(H.librarian, "load_config", lambda: cfg),
    mock.patch.object(H.librarian, "load_status", lambda: status),
    mock.patch.object(H, "run", apt_run),
    mock.patch.object(H.manifests, "addons", lambda: addons),
    mock.patch.object(H.access, "read", lambda path: {}),
    mock.patch.object(H.access, "mode_of", lambda state, i: "off" if i == "off-addon" else "public"),
    mock.patch.object(
        H.manifests,
        "load_local",
        lambda: ([{"id": "local1", "addon": {"title": "Local add-on"}}], []),
    ),
):
    (T / "state" / "addons-catalogue.json").write_text(
        '{"local1": {"status": "held", "held": ["permission: connect"]}}'
    )
    found = {f["id"]: f for f in H.addons_findings(now)}
    check(
        "a fresh source error: a warning",
        found["source-error-fresh-error"]["status"] == "warn",
        found.get("source-error-fresh-error"),
    )
    check(
        "  failing for more than three intervals: a problem",
        found["source-error-old-error"]["status"] == "problem",
        found.get("source-error-old-error"),
    )
    check(
        "  no successful check for three intervals: a warning",
        found["source-stale-stale"]["status"] == "warn",
        found.get("source-stale-stale"),
    )
    check(
        "  a newer release the policy would install: a warning",
        found["source-waiting-waiting"]["status"] == "warn",
        found.get("source-waiting-waiting"),
    )
    check(
        "  a disabled source and a deferred one stay quiet",
        "source-off-error" not in found and "source-off-stale" not in found and "source-deferred-stale" not in found,
        sorted(found),
    )
    check(
        "  a package waits for a packaged add-on, and a security update is named",
        found["addon-irc-packages"]["status"] == "warn"
        and found["addon-mqtt-packages"]["status"] == "warn"
        and message_args(found["addon-mqtt-packages"], "update.addons.packages")["packages"] == ["mosquitto"]
        and "update.addons.packages.security" in message_ids(found["addon-mqtt-packages"]),
        found.get("addon-mqtt-packages"),
    )
    check("  an add-on switched off says nothing", "addon-off-addon-packages" not in found, sorted(found))
    check(
        "  a held local add-on says so once, with what changed",
        found["addon-local1-held"]["status"] == "warn"
        and found["addon-local1-held"]["evidence"] == ["permission: connect"],
        found.get("addon-local1-held"),
    )

    cfg_off = {"policy": {"check_every_hours": 0, "auto_install": 2}, "sources": sources}
    with mock.patch.object(H.librarian, "load_config", lambda: cfg_off):
        quiet = {f["id"]: f for f in H.addons_findings(now)}
    check(
        "checks off: no stale finding",
        "source-stale-stale" not in quiet and "source-stale-stale" not in quiet,
        sorted(quiet),
    )

    clean = {"policy": {"check_every_hours": 24, "auto_install": 2}, "sources": [{"name": "good", "enabled": True}]}
    with (
        mock.patch.object(H.librarian, "load_config", lambda: clean),
        mock.patch.object(
            H.librarian, "load_status", lambda: {"good": {"last_check": iso(3600), "last_ok": iso(3600)}}
        ),
        mock.patch.object(H, "run", lambda *a, **k: type("R", (), {"stdout": "", "returncode": 0})()),
        mock.patch.object(H.manifests, "addons", lambda: {}),
        mock.patch.object(H.manifests, "load_local", lambda: ([], [])),
    ):
        clean_found = H.addons_findings(now)
    check(
        "a clean set: one ok line",
        len(clean_found) == 1
        and clean_found[0]["status"] == "ok"
        and "update.addons.ok" in message_ids(clean_found[0]),
        clean_found,
    )

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
