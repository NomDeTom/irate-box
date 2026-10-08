# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's deep audit (security-doctor-plan §3, §7.3–4): debian-cis's checks of the
CIS benchmark (--audit-all, never its fixes) and Lynis, each a few minutes on a small board (on
the Lyra, 2026-10-06: debian-cis 3 min for 241 checks, Lynis 4 min 40 s). Weekly by default and on
demand; its findings are kept in control/security-deep.json until the next one, and every regular
audit shows them as steps of their own (secdoctor.py), so they reach the joint report.

Both are run without being installed and without writing outside a scratch folder:
- debian-cis from its mirror (the security kit's git source), exported with git archive, its
  paths given in the environment (it reads /etc/default/cis-hardening otherwise)
- Lynis from the security kit: installed, or unpacked from its cache (the files checked against
  their manifest first), with its profile, plugins, log and report all in the scratch folder

The accepted-by-design list says once, for every source, what the box chooses on purpose.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from irate_box.root import secdoctor_xref

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
REPORT = CONTROL / "security-deep.json"
PROGRESS = CONTROL / "security-deep-progress.json"
GIT_ROOT = Path(os.environ.get("HUB_GIT_ROOT", STATE / "git"))
KITS_ROOT = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
CIS_UPSTREAM = "https://github.com/ovh/debian-cis"
TIMEOUT = 40 * 60

# What the box does on purpose (security-doctor-plan §5.3), by source and check id (a pattern).
ACCEPTED = [
    ("debian-cis", r"^1\.1\.\d+(\.\d+)?_.*(partition|nodev|nosuid|noexec)$",
     "one partition: the box runs from an SD card, with no separate /var, /tmp or /home"),
    ("debian-cis", r"^2\.2\.10_disable_http_server$", "the box is a web server: nginx (or Caddy) is the hub's front"),
    ("debian-cis", r"^3\.5\.4\.1\.1_net_fw_default_policy_drop$", "the box filters the hotspot alone (the floor, Security page): its other interfaces are the owner's network, left as found"),
    ("lynis", r"^FIRE-4512$", "the box filters the hotspot alone (the floor, Security page): its other interfaces are the owner's network, left as found"),
]


def accepted(source, check):
    return next((why for src, pat, why in ACCEPTED if src == source and re.match(pat, check)), None)


def _f(fid, title, status, detail, fix, source, about=None, why=None):
    """A finding in the shape secdoctor.F gives (kept here so this runs on its own)."""
    if why:
        status, detail = "ok", f"{detail} Accepted: {why}."
    return {"id": fid, "title": title, "status": status, "detail": detail, "fix": fix, "ref": "", "source": source,
            "about": about, "accepted": why}


def _progress(step, n, total):
    try:
        PROGRESS.write_text(json.dumps({"step": step, "n": n, "total": total, "at": time.time()}))
    except OSError:
        pass


# --- debian-cis -------------------------------------------------------------------------------

def _cis_mirror():
    for area in ("public", "private"):
        for repo in sorted((GIT_ROOT / area).glob("*.git")) if (GIT_ROOT / area).is_dir() else []:
            r = subprocess.run(["git", "-c", f"safe.directory={repo}", "--git-dir", str(repo), "config", "--get", "irate-box.mirror"],
                               capture_output=True, text=True)
            if r.stdout.strip().rstrip("/").lower() == CIS_UPSTREAM:
                return repo
    return None


CIS_LINE = re.compile(r"^(OK|KO|WARN) (\S+)\s+(.*)$")


def parse_cis(text):
    """[(status, check, [messages])] from debian-cis --batch, and its summary line's numbers."""
    checks = []
    for line in text.splitlines():
        m = CIS_LINE.match(line.strip())
        if m:
            msgs = re.findall(r"(?:OK|KO|WARN)\{([^}]*)\}", m.group(3))
            checks.append((m.group(1), m.group(2), msgs))
    summary = dict(re.findall(r"(\w+):([\d.]+)", next((l for l in text.splitlines() if l.startswith("AUDIT_SUMMARY")), "")))
    return checks, summary


def cis_findings(checks, summary, took=None):
    """Failed checks grouped by the benchmark's section (5.2: sshd; 4.1: auditd…), a warning
    each section; the accepted ones apart; the passes counted."""
    out, sections, acc, shared_found = [], {}, [], []
    for status, check, msgs in checks:
        if status == "OK":
            continue
        why = accepted("debian-cis", check)
        if why:
            acc.append((check, why))
            continue
        shared = secdoctor_xref.about("debian-cis", check)
        if shared:  # a question another source asks too: a finding of its own, for the joint report
            shared_found.append(_f(f"cis-{check}", f"CIS {check.split('_', 1)[0]}: {secdoctor_xref.title(shared['key'])}", "warn",
                          "; ".join(m for m in msgs if m)[:300] or "Not met.", "", "debian-cis", shared))
            continue
        sec = ".".join(check.split("_", 1)[0].split(".")[:2])
        sections.setdefault(sec, []).append((check, [m for m in msgs if m][:2]))
    for sec, items in sorted(sections.items(), key=lambda kv: [int(x) if x.isdigit() else 0 for x in kv[0].split(".")]):
        names = ", ".join(c.split("_", 1)[1] if "_" in c else c for c, _ in items[:10]) + ("…" if len(items) > 10 else "")
        first = next((m[0] for _, m in items if m), "")
        out.append(_f(f"cis-{sec}", f"CIS {sec}: {len(items)} check{'s' if len(items) != 1 else ''} not met", "warn",
                      f"{names}." + (f" For example: {first}." if first else ""),
                      "Each is a setting to look at, not an emergency: the benchmark is a general server's, not a hotspot's.",
                      "debian-cis", {"kind": "setting", "key": f"cis-{sec}"}))
    out += shared_found
    by_why = {}
    for check, why in acc:
        by_why.setdefault(why, []).append(check)
    for why, cs in by_why.items():
        out.append(_f(f"cis-accepted-{len(out)}", f"CIS: {len(cs)} check{'s' if len(cs) != 1 else ''} the box's design does not meet", "ok",
                      ", ".join(c.split("_", 1)[1] for c in cs[:10]) + ("…" if len(cs) > 10 else "") + ".", "", "debian-cis", why=why))
    out.append(_f("cis-summary", "The CIS benchmark (debian-cis --audit-all)", "ok",
                  f"{summary.get('PASSED_CHECKS', '?')} of {summary.get('RUN_CHECKS', '?')} checks pass "
                  f"({summary.get('CONFORMITY_PERCENTAGE', '?')} %)" + (f"; {round(took / 60, 1)} min" if took else "") + ".",
                  "", "debian-cis"))
    return out


def cis_pin():
    """The commit of debian-cis the shipped security kit pins (toolkits/security.json, root-owned
    code), or None. The mirror is fetched and tagged by the hub user, so root runs nothing from it
    but this commit (security stance review 2026-10-08, N2)."""
    from irate_box.hub import kitdefs
    for g in (kitdefs.shipped().get("security") or {}).get("git", []):
        if g.get("upstream", "").rstrip("/").lower() == CIS_UPSTREAM and re.fullmatch(r"[0-9a-f]{40}", str(g.get("pin", ""))):
            return g["pin"]
    return None


def run_cis(work, pin=None):
    repo = _cis_mirror()
    if not repo:
        return [_f("cis-missing", "debian-cis", "warn", "Its mirror is not on the box yet.",
                   "Library → Toolkits: keep the Security kit current; the librarian mirrors debian-cis.", "debian-cis")]
    pin = pin or cis_pin()
    if not pin:
        return [_f("cis-unpinned", "debian-cis", "warn", "The security kit names no commit of debian-cis, so it is not run: "
                   "root runs only the commit the hub's own code pins, never whatever the mirror holds.", "", "debian-cis")]
    have = subprocess.run(["git", "-c", f"safe.directory={repo}", "--git-dir", str(repo), "cat-file", "-e", f"{pin}^{{commit}}"],
                          capture_output=True, timeout=60)
    if have.returncode != 0:
        return [_f("cis-pin-missing", "debian-cis", "warn", f"Its mirror does not hold the pinned commit {pin[:12]}, so it is not run.",
                   "Library → Mirrors: update debian-cis (the kit pins it; an older mirror may need re-adding).", "debian-cis")]
    src = work / "debian-cis"
    src.mkdir()
    arch = subprocess.run(["git", "-c", f"safe.directory={repo}", "--git-dir", str(repo), "archive", "--format=tar", pin],
                          capture_output=True, timeout=300)
    if arch.returncode != 0:
        return [_f("cis-error", "debian-cis", "warn", f"Could not export it from its mirror: {arch.stderr.decode()[-200:]}", "", "debian-cis")]
    subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, check=True, timeout=300)
    env = dict(os.environ, CIS_LIB_DIR=str(src / "lib"), CIS_CHECKS_DIR=str(src / "bin" / "hardening"), CIS_CONF_DIR=str(src / "etc"),
               CIS_TMP_DIR=str(src / "tmp"), CIS_VERSIONS_DIR=str(src / "versions"), LC_ALL="C")
    (src / "tmp").mkdir(exist_ok=True)
    t = time.time()
    r = subprocess.run(["nice", "-n", "19", "bash", str(src / "bin" / "hardening.sh"), "--audit-all", "--batch"],
                       capture_output=True, text=True, timeout=TIMEOUT, env=env, cwd=str(src))
    checks, summary = parse_cis(r.stdout)
    if not checks:
        return [_f("cis-error", "debian-cis", "warn", "It ran but reported no checks: " + (r.stderr or r.stdout)[-300:], "", "debian-cis")]
    return cis_findings(checks, summary, time.time() - t)


# --- Lynis ------------------------------------------------------------------------------------

def parse_lynis(text):
    """{warnings: [(test, text)], suggestions: [(test, text)], index} from Lynis's report file."""
    out = {"warnings": [], "suggestions": [], "index": None}
    for line in text.splitlines():
        k, _, v = line.partition("=")
        parts = v.split("|")
        if k == "warning[]" and parts:
            out["warnings"].append((parts[0], parts[1] if len(parts) > 1 else ""))
        elif k == "suggestion[]" and parts:
            out["suggestions"].append((parts[0], parts[1] if len(parts) > 1 else ""))
        elif k == "hardening_index":
            out["index"] = v
    return out


def lynis_findings(rep, took=None):
    out = []
    for test, text in rep["warnings"]:
        why = accepted("lynis", test)
        out.append(_f(f"lynis-{test}", f"Lynis {test}: {text}", "warn", text + ".", "See Lynis's notes for the test, from a shell: lynis show details " + test,
                      "lynis", secdoctor_xref.about("lynis", test) or {"kind": "setting", "key": f"lynis-{test}"}, why))
    tests = sorted({t for t, _ in rep["suggestions"]})
    out.append(_f("lynis-summary", "Lynis", "ok",
                  f"Hardening index {rep['index'] or '?'}; {len(rep['warnings'])} warning{'s' if len(rep['warnings']) != 1 else ''}, "
                  f"{len(rep['suggestions'])} suggestions (listed, not counted)" + (f"; {round(took / 60, 1)} min" if took else "") + ". "
                  + "; ".join(f"{t}: {x}" for t, x in rep["suggestions"][:6]) + ("…" if len(rep["suggestions"]) > 6 else ""), "", "lynis"))
    return out


def _lynis_command(work):
    if Path("/usr/sbin/lynis").exists():
        return ["/usr/sbin/lynis"], "/etc/lynis/default.prf", "/etc/lynis/plugins", "/usr/share/lynis"
    pool = KITS_ROOT / "pool"
    found = sorted(pool.glob("lynis_*.deb")) if pool.is_dir() else []
    if not found:
        return None, "Lynis is not on the box, and the security kit's cache doesn't hold it", None, None
    from irate_box.root import kits
    if any(found[-1].name in p for p in kits.verify()):
        return None, f"{found[-1].name} in the security kit's cache fails its check", None, None
    subprocess.run(["dpkg-deb", "-x", str(found[-1]), str(work / "lynis")], check=True, timeout=120)
    base = work / "lynis"
    return [str(base / "usr" / "sbin" / "lynis")], str(base / "etc" / "lynis" / "default.prf"), str(base / "etc" / "lynis" / "plugins"), \
        str(base / "usr" / "share" / "lynis")


def run_lynis(work):
    cmd, profile, plugins, cwd = _lynis_command(work)
    if cmd is None:
        return [_f("lynis-missing", "Lynis", "warn", f"Not run: {profile}.",
                   "Library → Toolkits: refresh the Security kit while online (its cache is enough).", "lynis")]
    report, log = work / "lynis-report.dat", work / "lynis.log"
    t = time.time()
    subprocess.run(["nice", "-n", "19", *cmd, "audit", "system", "--quick", "--no-colors", "--profile", profile, "--plugindir", plugins,
                    "--logfile", str(log), "--report-file", str(report)],
                   capture_output=True, text=True, timeout=TIMEOUT, cwd=cwd, env=dict(os.environ, LC_ALL="C"))
    try:
        rep = parse_lynis(report.read_text(errors="replace"))
    except OSError:
        tail = (log.read_text(errors="replace")[-300:] if log.exists() else "")
        return [_f("lynis-error", "Lynis", "warn", "It ran but wrote no report. " + tail, "", "lynis")]
    return lynis_findings(rep, time.time() - t)


# --- the deep audit -----------------------------------------------------------------------------

def deep(log=print):
    """Both, one after the other, into control/security-deep.json. Returns a line."""
    from irate_box.root import safeio
    work = Path(tempfile.mkdtemp(prefix="deep-audit-"))
    os.chmod(work, 0o700)
    started = time.time()
    sources = {}
    try:
        for n, (name, fn) in enumerate((("debian-cis", run_cis), ("lynis", run_lynis)), 1):
            _progress(name, n, 2)
            t = time.time()
            try:
                found = fn(work)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                found = [_f(f"{name}-error", name, "warn", f"{type(exc).__name__}: {exc}"[:300], "", name)]
            sources[name] = {"at": time.time(), "took": time.time() - t, "findings": found}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        PROGRESS.unlink(missing_ok=True)
    report = {"at": started, "took": time.time() - started, "sources": sources}
    safeio.write(REPORT, json.dumps(report, indent=1))
    counts = {s: sum(1 for f in v["findings"] if f["status"] == "warn") for s, v in sources.items()}
    return f"deep audit: {round(report['took'] / 60, 1)} min; to look at: " + ", ".join(f"{s} {c}" for s, c in counts.items())


def load():
    try:
        return json.loads(REPORT.read_text())
    except (OSError, ValueError):
        return None
