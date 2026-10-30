# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Checks an add-on's manifest declares ("checks", manifests.py), run by the Security doctor in a process of their
own (declared.py, secdoctor.run_declared): a file's owner and mode, a config file's live lines. Each that fails is a
point of its own, with the manifest's words; one that cannot read is a warning, never a pass; a pattern that
backtracks for ever is stopped after the time limit and said once. A manifest whose checks are not valid is refused
at load, and the doctor runs none of them. python3 tests/sim_declared_checks.py"""

import grp
import os
import pwd
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="declared-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
(T / "state").mkdir(parents=True)
(T / "etc").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests
from irate_box.msg import render
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


ME = pwd.getpwuid(os.getuid()).pw_name
GROUP = grp.getgrgid(os.getgid()).gr_name
conf = T / "irc.conf"
conf.write_text(
    "[Global]\n"
    "; [Operator]  (an operator left as a comment)\n"
    "MaxConnections = 64\n"
    "MaxConnectionsIP = 0\n"
    "DNS = No\n"
    "# CloakHost = %x.box\n"
)
os.chmod(conf, 0o640)


def c(cid, kind, **kw):
    return {"id": cid, "kind": kind, "status": kw.pop("status", "warn"), "title": cid, "says": f"{cid} says.", **kw}


CHECKS = [
    c("owner-ok", "file", path=str(conf), owner=ME, group=GROUP, max_mode="0640"),
    c("too-open", "file", path=str(conf), max_mode="0600", status="problem"),
    c("not-there", "file", path=str(T / "absent"), exists=False),
    c("should-be-there", "file", path=str(T / "absent")),
    c("operator", "config", path=str(conf), comments=[";", "#"], absent=r"^\s*\[Operator\]"),
    c("max-conn", "config", path=str(conf), comments=[";"], value={"key": r"^\s*MaxConnections\s*=\s*(\d+)", "min": 1}),
    c("max-conn-ip", "config", path=str(conf), value={"key": r"^\s*MaxConnectionsIP\s*=\s*(\d+)", "min": 1}),
    c("cloak", "config", path=str(conf), comments=["#"], present=r"^\s*CloakHost\s*=\s*\S", why="Why.", fix="Fix."),
    c("dns", "config", path=str(conf), ignore_case=True, value={"key": r"^\s*DNS\s*=\s*(\S+)", "equals": "no"}),
    c("unreadable", "config", path=str(T / "absent.conf"), present="x"),
]

# Valid as a manifest, beside a status with a unit.
m = {"id": "made-up", "order": 1, "status": {"name": "x", "port": 1, "unit": "made-up.service"}, "checks": CHECKS}
try:
    manifests._check(m, "test")
    valid = True
except manifests.ManifestError as exc:
    valid = str(exc)
check("a manifest with checks of each kind is valid", valid is True, valid)

got = {i["key"]: i for i in sd.run_declared({"id": "made-up", "checks": CHECKS})}
check(
    "the failing checks, and only those, are points",
    sorted(got) == ["cloak", "config", "max-conn-ip", "should-be-there", "too-open"],
    sorted(got),
)
check("  a point with the check's status", got["too-open"]["status"] == "problem", got["too-open"])
check("  its words from the manifest", render(got["cloak"]["summary"]) == "cloak says.", got["cloak"])
check(
    "  why and fix too, when the check gives them",
    render(got["cloak"]["why"]) == "Why." and render(got["cloak"]["fix"]) == "Fix.",
)
check("  the file and what was found as evidence", got["too-open"]["evidence"] == [str(conf), "mode 0640"], got)
check(
    "a commented-out line does not count: no operator, no cloak",
    "operator" not in got and "cloak" in got,
)
check("a value below its minimum fails; one at it passes", "max-conn-ip" in got and "max-conn" not in got)
check("equals, ignoring case when asked", "dns" not in got)
check(
    "a file it cannot read: one warning naming the checks it held back, not a pass",
    got["config"]["status"] == "warn" and "unreadable" in render(got["config"]["summary"]),
    got["config"],
)

# A pattern that backtracks for ever: stopped, said once, and the doctor goes on.
slow = [c("slow", "config", path=str(T / "slow.conf"), present=r"^(a+)+$")]
(T / "slow.conf").write_text("a" * 40 + "b\n")
got = sd.run_declared({"id": "made-up", "checks": slow}, timeout=1)
check(
    "a pattern that runs too long: one warning, results said missing",
    [(i["key"], i["status"]) for i in got] == [("checks", "warn")] and "too long" in render(got[0]["summary"]),
    got,
)


# Refused at load.
def refused(bad):
    try:
        manifests._check({**m, "checks": bad}, "test")
        return False
    except manifests.ManifestError:
        return True


check("refused: a relative path", refused([c("x", "file", path="etc/x")]))
check("refused: a path through ..", refused([c("x", "file", path="/etc/../root/x")]))
check("refused: an unknown kind", refused([c("x", "exec", path="/bin/sh")]))
check("refused: a check id used twice", refused([c("x", "file", path="/a"), c("x", "file", path="/b")]))
check("refused: a pattern that does not compile", refused([c("x", "config", path="/a", present="(")]))
check("refused: a pattern over 200 characters", refused([c("x", "config", path="/a", present="a" * 201)]))
check(
    "refused: a value key without one group",
    refused([c("x", "config", path="/a", value={"key": "a", "min": 1})]),
)
check("refused: both present and absent", refused([c("x", "config", path="/a", present="a", absent="b")]))
check("refused: more than 20 checks", refused([c(f"x{n}", "file", path="/a") for n in range(21)]))
try:
    manifests._check({"id": "y", "order": 1, "checks": CHECKS}, "test")
    no_unit = False
except manifests.ManifestError:
    no_unit = True
check("refused: checks on an add-on with no status.unit", no_unit)

# The doctor checks validity itself: it reads the manifests' JSON, not through the hub's loader.
got = sd.run_declared({"id": "made-up", "checks": [c("x", "file", path="relative")]})
check(
    "invalid checks given to the doctor: one warning, none run",
    [(i["key"], i["status"]) for i in got] == [("checks", "warn")] and "not valid" in render(got[0]["summary"]),
    got,
)

# The IRC server's checks, moved from the doctor's code into apps.d/65-irc.json: on the config install.sh writes,
# none; on a config with none of its settings, the five points the probe made, with its ids and words.
import json
import re

irc = json.loads((REPO / "apps.d/65-irc.json").read_text())
box = T / "box"
(box / "etc/ngircd").mkdir(parents=True)
written = re.search(r"cat >/etc/ngircd/irate-box.conf <<'EOF'\n(.*?)\nEOF\n", (REPO / "install.sh").read_text(), re.S)
(box / "etc/ngircd/irate-box.conf").write_text(written.group(1))
sd.DECLARED_ROOT = box
got = sd.run_declared({"id": "irc", "checks": irc["checks"]})
check("IRC: the config install.sh writes passes every check", got == [], got)
(box / "etc/ngircd/irate-box.conf").write_text("[Global]\n\tName = x\n[Operator]\n\tName = op\n")
got = {i["key"]: i for i in sd.run_declared({"id": "irc", "checks": irc["checks"]})}
check(
    "IRC: a config with none of it: the probe's five points, by its ids",
    sorted(got) == ["cloak", "dns", "max-conn", "max-conn-ip", "operator"],
    sorted(got),
)
said = {k: render(i["summary"]) for k, i in got.items()}
check(
    "  in the probe's words",
    said
    == {
        "operator": "The IRC server has an operator ([Operator]).",
        "max-conn": "It has no limit on the number of connections.",
        "max-conn-ip": "The IRC server has no limit on the connections from one address.",
        "cloak": "The IRC server shows the address of each client to every other user.",
        "dns": "The IRC server looks up the name of each client.",
    },
    said,
)
check("  with its commands", got["dns"]["commands"] == ["DNS = no", "Ident = no"], got["dns"])
(box / "etc/ngircd/irate-box.conf").unlink()
got = sd.run_declared({"id": "irc", "checks": irc["checks"]})
check("IRC: no config: one point, config, as the probe gave", [i["key"] for i in got] == ["config"], got)
sd.DECLARED_ROOT = Path("/")
check("the doctor no longer carries an IRC probe", not any(p == "ngircd" for p, _ in sd.ADDON_PROBES))

# Unit and web checks: what systemd and the add-on answer, read by the doctor, matched in the checks' process.
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from irate_box.root import health, webprobe


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/ok/" else 404)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Page)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
webprobe.front_address = lambda: ("127.0.0.1", PORT)
wiki = json.loads((REPO / "apps.d/30-wiki.json").read_text())
plain = "{ path=/usr/bin/kiwix-serve ; argv[]=/usr/bin/kiwix-serve --port 8081 /var/lib/hub/library.xml ; }"
blocked = plain.replace("--port", "--blockexternal --port")
shown = {}
sd._show = lambda unit, *props: {p: shown[p] for p in props if p in shown}
shown["ExecStart"] = plain
got = sd.run_declared({"id": "wiki", "unit": "kiwix.service", "checks": wiki["checks"]})
check(
    "Kiwix: without --blockexternal, the probe's point (external), in its words",
    [(i["key"], render(i["summary"])) for i in got] == [("external", "Kiwix runs without --blockexternal.")],
    got,
)
shown["ExecStart"] = blocked
check(
    "Kiwix: with it, nothing", sd.run_declared({"id": "wiki", "unit": "kiwix.service", "checks": wiki["checks"]}) == []
)
del shown["ExecStart"]
got = sd.run_declared({"id": "wiki", "unit": "kiwix.service", "checks": wiki["checks"]})
check("Kiwix: systemd not answering: one point it could not read", [i["key"] for i in got] == ["config"], got)
check("the doctor no longer carries a Kiwix probe", not any(p == "kiwix" for p, _ in sd.ADDON_PROBES))

web = [
    c("direct-ok", "http", via="direct", path="/ok/", expect=[200]),
    c("direct-missing", "http", via="direct", path="/gone/", expect=[200]),
    c("frame", "http", via="direct", path="/ok/", header="X-Frame-Options", match="^(DENY|SAMEORIGIN)$"),
    c("csp", "http", via="front", path="/ok/", header="Content-Security-Policy", match="default-src"),
    c("user", "unit", property="User", equals="kiwix"),
]
shown["User"] = "root"
got = {i["key"]: i for i in sd.run_declared({"id": "x", "unit": "x.service", "port": PORT, "checks": web})}
check("web and unit checks: the failing ones", sorted(got) == ["csp", "direct-missing", "user"], sorted(got))
check("  what was answered as evidence", got["direct-missing"]["evidence"] == ["direct /gone/", "404"], got)
check("  a header it lacks", got["csp"]["evidence"][1] == "200, no Content-Security-Policy", got)
check("  a property's value", got["user"]["evidence"] == ["x.service", "User=root"], got)

check("refused: a unit check with no test", refused([c("x", "unit", property="User")]))
check("refused: an http check with no expectation", refused([c("x", "http", via="front", path="/a/")]))
check("refused: an http check via elsewhere", refused([c("x", "http", via="other", path="/a/", expect=[200])]))
check("refused: a repair on a security check", refused([c("x", "unit", property="User", equals="a", repair="restart")]))

# A probe that cannot read what it was given: the add-on says so as a warn. It must not carry the whole
# add-on step down with it (a bad Location: port from one add-on is one add-on's affair).
real_front = webprobe.through_front
webprobe.through_front = lambda path, **kw: (_ for _ in ()).throw(ValueError("Port out of range 0-65535"))
one = [c("probe", "http", via="front", path="/ok/", expect=[200])]
try:
    got = sd.run_declared({"id": "x", "unit": "x.service", "port": PORT, "checks": one})
except Exception as exc:  # noqa: BLE001 - the point is what it raises, whatever it is
    got = f"raised {type(exc).__name__}: {exc}"
webprobe.through_front = real_front
check(
    "a probe that raises: its add-on's checks are said as missing, with the reason, and the run carries on",
    isinstance(got, list)
    and [i["key"] for i in got] == ["checks"]
    and got[0]["status"] == "warn"
    and got[0]["summary"]["id"] == "addon.declared.failed"
    and "Port out of range" in " ".join(got[0]["evidence"]),
    got,
)

# The Box doctor runs its own ("doctor": "box"), with the repair the manifest names.
box_checks = [
    c("answers", "http", via="direct", path="/gone/", expect=[200], doctor="box", repair="restart"),
    c("sec", "http", via="direct", path="/gone/", expect=[200]),
]
(T / "apps.d").mkdir()
(T / "apps.d/40-x.json").write_text(
    json.dumps(
        {"id": "x", "order": 40, "status": {"name": "X", "port": PORT, "unit": "x.service"}, "checks": box_checks}
    )
)
manifests.APPS_D = T / "apps.d"
health.unit_props = lambda unit: {"LoadState": "loaded"}
f = health.check_addon_declared()
check(
    "Box doctor: its own check fails, the security one is not its", [x["id"] for x in f] == ["addon-check:x-answers"], f
)
check("  with the repair the manifest names", [a["choice"] for a in f[0]["actions"]] == ["unit-restart:x.service"], f)
got = sd.run_declared({"id": "x", "unit": "x.service", "port": PORT, "checks": box_checks})
check("Security doctor: only its own", [i["key"] for i in got] == ["sec"], got)

# A local add-on (the hub writes its manifest): checks of its own files, read without following a link, and of its
# own pages through the web server; nothing else.
lm = {
    "id": "demo",
    "order": 70,
    "tile": {"icon": "x", "name": "Demo", "desc": "A demo"},
    "source": {"type": "git", "repo": "https://example.org/demo.git", "pin": "a" * 40},
    "needs": "index.html",
    "addon": {"title": "Demo", "summary": "A demo.", "consent": "Add it?"},
}
local_checks = [
    c("no-net", "config", path="index.html", absent=r"https?://(?!\{box\})", status="problem"),
    c("data-mode", "file", path="data/notes.json", max_mode="0644"),
    c("planted", "config", path="link.conf", present="x"),
    c("through", "config", path="sub/inner.conf", present="x"),
    c("page", "http", via="front", path="ok/", expect=[200]),
]


def local_refused(bad):
    try:
        manifests.check_local({**lm, "checks": bad}, "test")
        return False
    except manifests.ManifestError:
        return True


try:
    manifests.check_local({**lm, "checks": local_checks}, "test")
    ok_local = True
except manifests.ManifestError as exc:
    ok_local = str(exc)
check("local: a manifest with checks of its own files and pages is valid", ok_local is True, ok_local)
check(
    "local: carried into the full manifest", manifests.local({**lm, "checks": local_checks})["checks"] == local_checks
)
check("local refused: an absolute path", local_refused([c("x", "file", path="/etc/passwd")]))
check("local refused: a path through ..", local_refused([c("x", "file", path="../other/x")]))
check("local refused: a unit check", local_refused([c("x", "unit", property="User", equals="hub")]))
check(
    "local refused: a web check past the web server",
    local_refused([c("x", "http", via="direct", path="", expect=[200])]),
)
check(
    "local refused: a repair",
    local_refused([c("x", "file", path="a", doctor="box", repair="restart")]),
)

home = T / "addons/demo"
(home / "data").mkdir(parents=True)
(home / "index.html").write_text('<script src="https://cdn.example.com/x.js"></script>\n')
(home / "data/notes.json").write_text("{}")
os.chmod(home / "data/notes.json", 0o666)
(T / "secret.conf").write_text("x = secret\n")
(home / "link.conf").symlink_to(T / "secret.conf")
(T / "elsewhere").mkdir()
(T / "elsewhere/inner.conf").write_text("x\n")
(home / "sub").symlink_to(T / "elsewhere")


class LocalPage(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/addons/demo/ok/" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


lsrv = ThreadingHTTPServer(("127.0.0.1", 0), LocalPage)
threading.Thread(target=lsrv.serve_forever, daemon=True).start()
webprobe.front_address = lambda: ("127.0.0.1", lsrv.server_address[1])
points = sd.run_declared({"id": "demo", "local": True, "root": str(home), "checks": local_checks})
got = {i["key"]: i for i in points}
check("local: its page asking another website: a problem", got.get("no-net", {}).get("status") == "problem", got)
check("local: its data file writable by all: a point", "data-mode" in got, got)
check("local: its page through the web server answers: no point", "page" not in got, got)
unread = " ".join(render(i["summary"]) for i in points if i["key"] == "config")
check(
    "local: a link, as a file or as a folder on the way: not followed, said as not read",
    "planted" in unread and "through" in unread,
    unread,
)
check("  and nothing of what the link leads to in the evidence", "secret" not in json.dumps(points), points)
got = sd.run_declared({"id": "demo", "local": True, "root": str(home), "checks": [c("x", "file", path="/etc/passwd")]})
check(
    "local: an absolute path given to the doctor anyway: not valid, none run",
    [i["key"] for i in got] == ["checks"],
    got,
)

# The Box doctor runs a local add-on's own box checks too, with no repair (it has no unit).
(T / "state/apps.d").mkdir(parents=True, exist_ok=True)
(T / "state/addons/demo").mkdir(parents=True)
(T / "state/apps.d/demo.json").write_text(
    json.dumps({**lm, "checks": [c("works", "http", via="front", path="gone/", expect=[200], doctor="box")]})
)
f = [x for x in health.check_addon_declared() if x["id"].startswith("addon-check:demo")]
check(
    "Box doctor: a local add-on's own check, no repair",
    [(x["id"], x["actions"]) for x in f] == [("addon-check:demo-works", [])],
    f,
)

sys.exit(1 if fails else 0)
