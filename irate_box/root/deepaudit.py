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
import shlex
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
# debian-cis's by the check's name whatever its number (the benchmark renumbers them).
FLOOR = "the box filters the hotspot alone (the floor, Security page): its other interfaces are the owner's network, left as found"
ACCEPTED = [
    ("debian-cis", r"^1\.1\.\d+(\.\d+)?_.*(partition|nodev|nosuid|noexec)$",
     "one partition: the box runs from an SD card, with no separate /var, /tmp or /home"),
    ("debian-cis", r"^[\d.]+_disable_http_server$", "the box is a web server: nginx (or Caddy) is the hub's front"),
    ("debian-cis", r"^[\d.]+_net_fw_default_policy_drop$", FLOOR),
    ("debian-cis", r"^[\d.]+_(nftables_not_installed_with_iptables|ufw_not_installed_with_nftables)$", "the floor is the hub's own nftables rules"),
    ("debian-cis", r"^[\d.]+_dnsmasq_is_disabled$", "dnsmasq gives the hotspot's guests their addresses and names"),
    ("lynis", r"^FIRE-(4512|4590)$", FLOOR),
    ("lynis", r"^FILE-6310$", "one partition: the box runs from an SD card, with no separate /var, /tmp or /home"),
]
# What the hub itself installs and uses (install.sh, the toolkits): never offered for removal.
HUB_KEEPS = {"nftables", "dnsmasq", "dnsmasq-base", "nginx", "caddy", "rsync", "git", "curl", "syncthing", "mosquitto", "nodejs"}


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
CIS_MSG = re.compile(r"(OK|KO|WARN)\{([^}]*)\}")


def parse_cis(text):
    """[(status, check, [messages])] from debian-cis --batch, and its summary line's numbers. A
    failed check keeps only what failed ("/tmp has no option nodev"), not what passed on the way."""
    checks = []
    for line in text.splitlines():
        m = CIS_LINE.match(line.strip())
        if m:
            said = CIS_MSG.findall(m.group(3))
            msgs = [t for s, t in said if s != "OK"] if m.group(1) != "OK" else []
            checks.append((m.group(1), m.group(2), msgs or [t for _, t in said]))
    summary = dict(re.findall(r"(\w+):([\d.]+)", next((l for l in text.splitlines() if l.startswith("AUDIT_SUMMARY")), "")))
    return checks, summary


# --- each check's own fix, from debian-cis's own script ------------------------------------------
# A failed check says what it wants and, for most, the exact setting its apply step would make
# (never run here: --audit-all only). Read from the pinned commit's script at audit time, so it is
# always that version's, and said as one command the owner may run by hand, or none.
CIS_VAR = re.compile(r"^([A-Z_]+)=(['\"]?)(.*?)\2\s*(?:#.*)?$")
CIS_PKG = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")


def _script_vars(text):
    """A check script's top-level assignments, the last non-empty one of each. A value built from a
    variable or a command is left out: it is not one fixed setting."""
    out = {}
    for line in text.splitlines():
        m = CIS_VAR.match(line)
        if m and m.group(3).strip() and not re.search(r"[$`]", m.group(3)):
            out[m.group(1)] = m.group(3).strip()
    return out


def cis_check_info(src, check):
    """{what, cmd? | say?} for a debian-cis check from its script in the exported source src: its
    DESCRIPTION, and a command for the kinds it sets plainly (sysctl keys, sshd options, a package
    to install or remove, a file's owner and mode), or another file's settings in words. {} when
    the script is not there."""
    name = check.split("_", 1)[1] if "_" in check else check
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        return {}
    try:
        text = (src / "bin" / "hardening" / f"{name}.sh").read_text(errors="replace")
    except OSError:
        return {}
    v = _script_vars(text)
    info = {"what": v.get("DESCRIPTION", "")}
    m = re.search(r"^apply\(\) \{(.*?)^\}", text, re.S | re.M)
    body = m.group(1) if m else ""
    q = shlex.quote
    conf = f"60-cis-{name.replace('_', '-')}.conf"
    lines = lambda pairs, sep: " ".join(q(k + sep + val) for k, _, val in (p.partition("=") for p in pairs))  # noqa: E731
    sysctl = v.get("SYSCTL_PARAMS", "").split()
    if not sysctl and v.get("SYSCTL_PARAM") and v.get("SYSCTL_EXP_RESULT"):   # the one-key form (ASLR, core dumps)
        sysctl = [f"{v['SYSCTL_PARAM']}={v['SYSCTL_EXP_RESULT']}"]
    opts = v.get("OPTIONS", "").split()
    pkgs = (v.get("PACKAGE", "") + " " + v.get("PACKAGES", "")).split()
    if sysctl and all("=" in p for p in sysctl):
        info["cmd"] = f"printf '%s\\n' {lines(sysctl, ' = ')} | sudo tee /etc/sysctl.d/{conf} && sudo sysctl --system"
    elif v.get("FILE") == "/etc/ssh/sshd_config" and opts and all("=" in o for o in opts):
        info["cmd"] = f"printf '%s\\n' {lines(opts, ' ')} | sudo tee /etc/ssh/sshd_config.d/{conf} && sudo sshd -t && sudo systemctl reload ssh"
    elif opts and v.get("FILE", "").startswith("/"):
        # Another file's settings (login.defs, journald.conf…): said, as their formats differ.
        info["say"] = f"In {v['FILE']}, set: " + ", ".join(opts) + "."
    elif pkgs and not v.get("FILE") and all(CIS_PKG.match(p) for p in pkgs) and "apt_install" in body:
        # Only a check that is just the package: one whose package is a step before a setting is not.
        info["cmd"] = "sudo apt-get install " + " ".join(pkgs)
    elif pkgs and not v.get("FILE") and all(CIS_PKG.match(p) for p in pkgs) and re.search(r"apt-get (-y )?(purge|remove)|apt_remove", body):
        keep = sorted(HUB_KEEPS & set(pkgs))
        if keep:
            info["say"] = f"The hub uses {', '.join(keep)}: leave it installed."
        else:
            info["cmd"] = "sudo apt-get purge " + " ".join(pkgs)
    elif v.get("FILE", "").startswith("/") and re.fullmatch(r"[0-7]{3,4}", v.get("PERMISSIONS", "")):
        own = f"{v.get('USER') or 'root'}:{v.get('GROUP') or 'root'}"
        info["cmd"] = f"sudo chown {q(own)} {q(v['FILE'])} && sudo chmod {v['PERMISSIONS']} {q(v['FILE'])}"
    return info


def cis_findings(checks, summary, took=None, info=None):
    """Failed checks grouped by the benchmark's section (5.2: sshd; 4.1: auditd…), a warning
    each section; the accepted ones apart; the passes counted. Each finding keeps its checks one
    by one (checks: {check, msgs, what, cmd}), info(check) giving each one's what and command."""
    out, sections, acc, shared_found = [], {}, [], []
    one = lambda check, msgs: {"check": check, "msgs": [m for m in msgs if m][:4], **((info and info(check)) or {})}  # noqa: E731
    for status, check, msgs in checks:
        if status == "OK":
            continue
        why = accepted("debian-cis", check)
        if why:
            acc.append((check, why))
            continue
        shared = secdoctor_xref.about("debian-cis", check)
        if shared:  # a question another source asks too: a finding of its own, for the joint report
            f = _f(f"cis-{check}", f"CIS {check.split('_', 1)[0]}: {secdoctor_xref.title(shared['key'])}", "warn",
                   "; ".join(m for m in msgs if m)[:300] or "Not met.", "", "debian-cis", shared)
            f["checks"] = [one(check, msgs)]
            shared_found.append(f)
            continue
        sec = ".".join(check.split("_", 1)[0].split(".")[:2])
        sections.setdefault(sec, []).append((check, [m for m in msgs if m][:2], one(check, msgs)))
    for sec, items in sorted(sections.items(), key=lambda kv: [int(x) if x.isdigit() else 0 for x in kv[0].split(".")]):
        names = ", ".join(c.split("_", 1)[1] if "_" in c else c for c, _, _ in items[:10]) + ("…" if len(items) > 10 else "")
        first = next((m[0] for _, m, _ in items if m), "")
        f = _f(f"cis-{sec}", f"CIS {sec}: {len(items)} check{'s' if len(items) != 1 else ''} not met", "warn",
               f"{names}." + (f" For example: {first}." if first else ""),
               "Each is a setting to look at, not an emergency: the benchmark is a general server's, not a hotspot's.",
               "debian-cis", {"kind": "setting", "key": f"cis-{sec}"})
        f["checks"] = [c for _, _, c in items]
        out.append(f)
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
    return cis_findings(checks, summary, time.time() - t, info=lambda c: cis_check_info(src, c))


# --- Lynis ------------------------------------------------------------------------------------

def _dash(x):
    """Lynis's report says "-" for nothing, and "text:" before a solution in words."""
    x = (x or "").strip()
    return "" if x == "-" else x.removeprefix("text:")


def parse_lynis(text):
    """From Lynis's report file: its warnings and suggestions ({test, text, details, solution}),
    the settings two tests report one by one (details[]: {test, service, field, value, prefval,
    desc}; KRNL-6000's sysctl keys, SSH-7408's sshd options), and the hardening index."""
    out = {"warnings": [], "suggestions": [], "details": [], "index": None}
    for line in text.splitlines():
        k, _, v = line.partition("=")
        parts = v.split("|") + ["", "", ""]
        if k in ("warning[]", "suggestion[]") and parts[0]:
            out["warnings" if k == "warning[]" else "suggestions"].append(
                {"test": parts[0], "text": parts[1], "details": _dash(parts[2]), "solution": _dash(parts[3])})
        elif k == "details[]" and parts[0]:
            d = {"test": parts[0], "service": parts[1]}
            for kv in parts[2].split(";"):
                name, sep, val = kv.partition(":")
                if sep and name in ("desc", "field", "value", "prefval"):
                    d[name] = val
            out["details"].append(d)
        elif k == "hardening_index":
            out["index"] = v
    return out


# The two tests that report each setting apart: their details[] are the findings, not their one line.
LYNIS_BY_SETTING = ("KRNL-6000", "SSH-7408")


def lynis_findings(rep, took=None):
    """A warning each for Lynis's warnings. Its suggestions, and the settings it reports one by one,
    where the cross-reference table knows what they are about (secdoctor_xref): a suggestion each,
    about that, so they merge with what the other sources say (tiered as that CIS section is, or as
    a suggestion). The rest listed in its summary, not counted."""
    out, listed, ids = [], [], set()

    def fid(base):
        n, i = base, 1
        while n in ids:
            i += 1
            n = f"{base}-{i}"
        ids.add(n)
        return n

    def suggestion(check, title, detail, fix, about):
        sec = about["key"].removeprefix("cis-") if about["key"].startswith("cis-") else None
        f = _f(fid(f"lynis-{check.replace(':', '-')}"), title, "warn", detail, fix, "lynis", about)
        f["tier"] = ((secdoctor_xref.cis(sec) or (None, "suggest"))[1]) if sec else "suggest"
        return f

    for w in rep["warnings"]:
        why = accepted("lynis", w["test"])
        out.append(_f(fid(f"lynis-{w['test']}"), f"Lynis {w['test']}: {w['text']}", "warn",
                      w["text"] + (f" ({w['details']})" if w["details"] else "") + ".",
                      w["solution"] or f"See Lynis's notes for the test, from a shell: lynis show details {w['test']}",
                      "lynis", secdoctor_xref.about("lynis", w["test"]) or {"kind": "setting", "key": f"lynis-{w['test']}"}, why))
    for d in rep["details"]:
        if d["test"] not in LYNIS_BY_SETTING or not d.get("field"):
            continue
        check = f"{d['test']}:{d['field']}"
        said = f"{d['field']} is {d.get('value') or 'not set'}; Lynis prefers {d.get('prefval') or '?'}"
        a = secdoctor_xref.about("lynis", check)
        if a:
            out.append(suggestion(check, f"Lynis {d['test']}: {d.get('desc') or d['field']}", said + ".", "", a))
        else:
            listed.append(f"{d['test']}: {said}")
    for s in rep["suggestions"]:
        if s["test"] in LYNIS_BY_SETTING and any(d["test"] == s["test"] for d in rep["details"]):
            continue  # its settings, one by one, are above
        why = accepted("lynis", s["test"])
        a = secdoctor_xref.about("lynis", s["test"])
        if why or not a:
            listed.append(f"{s['test']}: {s['text']}" + (" (accepted: the box's design)" if why else ""))
            continue
        out.append(suggestion(s["test"], f"Lynis {s['test']}: {s['text']}",
                              s["text"] + (f" ({s['details']})" if s["details"] else "") + ".", s["solution"], a))
    n = len(rep["suggestions"])
    out.append(_f("lynis-summary", "Lynis", "ok",
                  f"Hardening index {rep['index'] or '?'}; {len(rep['warnings'])} warning{'s' if len(rep['warnings']) != 1 else ''}, "
                  f"{n} suggestion{'s' if n != 1 else ''}" + (f"; {round(took / 60, 1)} min" if took else "") + ". "
                  + (f"Not matched to an item (listed, not counted): {'; '.join(listed[:8])}" + ("…" if len(listed) > 8 else "") + "." if listed else ""),
                  "", "lynis"))
    out[-1]["listed"] = listed
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
    counts = {s: sum(1 for f in v["findings"] if f["status"] == "warn" and not f.get("tier")) for s, v in sources.items()}
    return f"deep audit: {round(report['took'] / 60, 1)} min; to look at: " + ", ".join(f"{s} {c}" for s, c in counts.items())


def load():
    try:
        return json.loads(REPORT.read_text())
    except (OSError, ValueError):
        return None
