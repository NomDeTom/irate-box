# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Scan reports imported on /admin → Security doctor: an
OpenVAS / Greenbone report (XML or CSV) or an nmap XML, run from a PC against the box.

The page reads the file in the owner's browser and sends only what it found: each result's port,
protocol and service, and for OpenVAS its CVSS score, name, summary, solution and CVEs. Neither the
hub nor root parses the untrusted XML. What arrives is checked here field by field, then kept, one
report per kind, until replaced, with when it ran, when it was imported, and the box's listening
ports at that moment, so the report can say "imported before the ports changed".

Kept in $HUB_STATE_DIR/security-imports/<kind>.json; the security doctor (root) reads them.
"""
import json
import os
import re
import time
from pathlib import Path

from irate_box import confine

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
DIR = STATE / "security-imports"
SECURITY_SCAN = STATE / "control" / "security.json"
KINDS = ("openvas", "nmap")
MAX_RESULTS = 1000
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
TEXT = {"name": 200, "service": 60, "summary": 600, "solution": 600, "host": 64}


def _text(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v or ""))[:n].strip()


def validate(report):
    """The report as kept, or ValueError: a known kind, at most MAX_RESULTS results, each field of
    the right type and length."""
    if not isinstance(report, dict) or report.get("kind") not in KINDS:
        raise ValueError(f"kind: one of {', '.join(KINDS)}")
    results = report.get("results")
    if not isinstance(results, list) or len(results) > MAX_RESULTS:
        raise ValueError(f"results: a list of up to {MAX_RESULTS}")
    ran = report.get("ran")
    if ran is not None and (not isinstance(ran, (int, float)) or not 946684800 <= ran <= time.time() + 86400):
        raise ValueError("ran: when the scan ran, as a Unix time")
    out = []
    for r in results:
        if not isinstance(r, dict):
            raise ValueError("each result is an object")
        port, proto = r.get("port"), r.get("proto", "tcp")
        if not (port in (None, "general") or (isinstance(port, int) and 0 < port < 65536)) or proto not in ("tcp", "udp"):
            raise ValueError("port: 1-65535 (or general), proto: tcp or udp")
        item = {"port": port, "proto": proto, "host": _text(r.get("host"), TEXT["host"]), "service": _text(r.get("service"), TEXT["service"])}
        if report["kind"] == "openvas":
            cvss = r.get("cvss", 0)
            if not isinstance(cvss, (int, float)) or not 0 <= cvss <= 10:
                raise ValueError("cvss: 0 to 10")
            cves = r.get("cves") or []
            if not isinstance(cves, list) or not all(isinstance(c, str) and CVE_RE.match(c) for c in cves[:50]):
                raise ValueError("cves: a list of CVE ids")
            item.update(cvss=float(cvss), name=_text(r.get("name"), TEXT["name"]), summary=_text(r.get("summary"), TEXT["summary"]),
                        solution=_text(r.get("solution"), TEXT["solution"]), cves=cves[:50])
        else:
            if r.get("state") not in ("open", "open|filtered"):
                continue
            item["state"] = r["state"]
        out.append(item)
    return {"kind": report["kind"], "name": _text(report.get("name"), 120), "ran": ran, "results": out}


def _box_ports():
    try:
        scan = json.loads(SECURITY_SCAN.read_text())
        return sorted({f"{l['proto']}/{l['port']}" for l in scan.get("listeners", [])})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(report):
    kept = validate(report)
    kept.update(imported=time.time(), box_ports=_box_ports())
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = confine.under(DIR, f".{kept['kind']}.json.tmp")
    tmp.write_text(json.dumps(kept))
    os.replace(tmp, confine.under(DIR, f"{kept['kind']}.json"))
    return f"{kept['kind']}: {len(kept['results'])} results kept; the next run of the doctor includes them"


def remove(kind):
    if kind not in KINDS:
        raise ValueError(f"kind: one of {', '.join(KINDS)}")
    confine.under(DIR, f"{kind}.json").unlink(missing_ok=True)
