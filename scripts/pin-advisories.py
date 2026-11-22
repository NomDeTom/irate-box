#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Watch the downloads irate-box pins for security fixes.

Reads every pinned version from the tree, then asks three sources about each one: the project's published GitHub
security advisories, OSV (for the npm packages), and the release notes of every version newer than the pin. A pin
that is behind and flagged gets one open issue, labelled pin-advisory; a later run updates that issue instead of
opening another, and closes it when the pin catches up. A pin that is merely behind, with no security wording,
gets no issue.

CI only: nothing on the box contacts these hosts. pdf.js has no pinned version in this tree (adapt_pdfjs.py takes
it from the tree it adapts), and Caddy is downloaded at its latest release, so neither is watched here.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
OSV = "https://api.osv.dev/v1/query"
LABEL = "pin-advisory"
SECURITY = re.compile(r"security|CVE-|GHSA-|vulnerab|XSS|injection|bypass", re.I)

# One line per pin: the tool, its GitHub project, and where its version is written. npm is set for the packages
# OSV knows by package name.
PINS = [
    {
        "name": "SilverBullet",
        "owner": "silverbulletmd",
        "repo": "silverbullet",
        "file": "install.sh",
        "var": "SB_VERSION",
    },
    {"name": "ttyd", "owner": "tsl0922", "repo": "ttyd", "file": "install.sh", "var": "TTYD_VERSION"},
    {
        "name": "ESP Web Tools",
        "owner": "esphome",
        "repo": "esp-web-tools",
        "file": "install.sh",
        "var": "EWT_VERSION",
        "npm": "esp-web-tools",
    },
    {"name": "FreeOCD Web", "owner": "FreeOCD", "repo": "freeocd-web", "file": "install.sh", "var": "FOCD_VERSION"},
    {
        "name": "jsdom",
        "owner": "jsdom",
        "repo": "jsdom",
        "file": "tests/jsdom/jsdom.sh",
        "var": "JSDOM_VERSION",
        "npm": "jsdom",
    },
    {
        "name": "axe-core",
        "owner": "dequelabs",
        "repo": "axe-core",
        "file": "tests/browser/a11y-audit.sh",
        "var": "AXE_VERSION",
        "npm": "axe-core",
    },
]


def read_pin(root, pin):
    text = (Path(root) / pin["file"]).read_text()
    match = re.search(rf"^{re.escape(pin['var'])}=(['\"]?)([^'\"\s#]+)\1", text, re.M)
    if not match:
        raise ValueError(f"{pin['file']}: {pin['var']} not found")
    return match.group(2)


def read_pins(root=REPO):
    return [dict(pin, version=read_pin(root, pin)) for pin in PINS]


def version_key(version):
    match = re.search(r"\d+(?:\.\d+)*", str(version))
    return tuple(int(part) for part in match.group(0).split(".")) if match else ()


def security_lines(release):
    body = release.get("body") or ""
    return [line.strip() for line in body.splitlines() if SECURITY.search(line)]


def newer_releases(releases, version):
    latest = max(
        (r for r in releases if version_key(r.get("tag_name"))), key=lambda r: version_key(r["tag_name"]), default=None
    )
    flagged = []
    for release in releases:
        if version_key(release.get("tag_name")) <= version_key(version):
            continue
        lines = security_lines(release)
        if lines:
            flagged.append(
                {
                    "tag": release.get("tag_name"),
                    "url": release.get("html_url"),
                    "published": release.get("published_at"),
                    "lines": lines,
                }
            )
    return (latest.get("tag_name") if latest else None), flagged


def assessment(pin, releases, advisories=(), osv=()):
    """What the three sources say about one pin: {behind, latest, releases, advisories, osv, flagged}."""
    latest, flagged = newer_releases(releases, pin["version"])
    advisories = [
        {"id": a.get("ghsa_id") or a.get("cve_id") or "?", "summary": a.get("summary") or "", "url": a.get("html_url")}
        for a in advisories
    ]
    osv = [
        {
            "id": v.get("id") or "?",
            "summary": v.get("summary") or "",
            "url": f"https://osv.dev/vulnerability/{v.get('id')}",
        }
        for v in osv
    ]
    behind = bool(latest and version_key(latest) > version_key(pin["version"]))
    return {
        "behind": behind,
        "latest": latest,
        "releases": flagged,
        "advisories": advisories,
        "osv": osv,
        "flagged": behind and bool(flagged or advisories or osv),
    }


def marker(pin):
    return f"<!-- {LABEL}:{pin['name'].lower().replace(' ', '-')} -->"


def issue_title(pin, said):
    return f"{pin['name']} {pin['version']} is behind {said['latest']} with security fixes"


def issue_body(pin, said):
    lines = [
        marker(pin),
        f"The tree pins {pin['name']} {pin['version']}. The latest release is {said['latest']}.",
        "Update the pin. This issue closes on the next run when the pin has moved past the fix.",
    ]
    if said["releases"]:
        lines += ["", "## Newer releases with security wording"]
        for release in said["releases"]:
            lines.append(f"### [{release['tag']}]({release['url']})")
            lines += [f"- {line}" for line in release["lines"]]
    if said["advisories"]:
        lines += ["", "## Published GitHub security advisories"]
        for advisory in said["advisories"]:
            lines.append(f"- [{advisory['id']}]({advisory['url']}): {advisory['summary']}")
    if said["osv"]:
        lines += ["", "## OSV"]
        for entry in said["osv"]:
            lines.append(f"- [{entry['id']}]({entry['url']}): {entry['summary']}")
    return "\n".join(lines) + "\n"


def _request(url, token=None, body=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "irate-box-pin-advisories"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as answer:  # nosec B310 - the hosts are constants above
        return json.loads(answer.read().decode() or "{}")


def github(path, token=None, body=None, method=None):
    if method:
        # A PATCH/POST with an explicit verb: Request takes the method from data alone for POST.
        request = urllib.request.Request(
            API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "irate-box-pin-advisories",
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as answer:  # nosec B310 - the hosts are constants above
            return json.loads(answer.read().decode() or "{}")
    return _request(API + path, token=token, body=body)


def collect(pin, token=None):
    """The three sources for one pin. A source that refuses (a rate limit, a permission) does not hide the others."""
    said = {"releases": [], "advisories": [], "osv": []}
    try:
        said["releases"] = github(f"/repos/{pin['owner']}/{pin['repo']}/releases?per_page=100", token)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"{pin['name']}: releases: {exc}", file=sys.stderr)
    try:
        said["advisories"] = github(f"/repos/{pin['owner']}/{pin['repo']}/security-advisories", token)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"{pin['name']}: advisories: {exc}", file=sys.stderr)
    if pin.get("npm"):
        try:
            said["osv"] = _request(
                OSV,
                body={"version": pin["version"], "package": {"name": pin["npm"], "ecosystem": "npm"}},
            ).get("vulns", [])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            print(f"{pin['name']}: OSV: {exc}", file=sys.stderr)
    return assessment(pin, said["releases"], said["advisories"], said["osv"])


def sync(repo, token, pins, collect_pin=collect):
    """One issue per behind-and-flagged pin; update it on later runs, close it when the pin catches up."""
    open_issues = github(f"/repos/{repo}/issues?state=open&labels={LABEL}&per_page=100", token)
    by_marker = {i.get("body", "").splitlines()[0]: i for i in open_issues if i.get("body")}
    for pin in pins:
        said = collect_pin(pin, token)
        existing = by_marker.get(marker(pin))
        if said["flagged"]:
            body, title = issue_body(pin, said), issue_title(pin, said)
            if existing:
                github(
                    f"/repos/{repo}/issues/{existing['number']}", token, {"title": title, "body": body}, method="PATCH"
                )
                print(f"{pin['name']}: issue #{existing['number']} updated")
            else:
                opened = github(
                    f"/repos/{repo}/issues", token, {"title": title, "body": body, "labels": [LABEL]}, method="POST"
                )
                print(f"{pin['name']}: issue #{opened.get('number')} opened")
        elif existing:
            github(
                f"/repos/{repo}/issues/{existing['number']}",
                token,
                {"state": "closed", "state_reason": "completed"},
                method="PATCH",
            )
            print(f"{pin['name']}: issue #{existing['number']} closed")
        else:
            print(f"{pin['name']}: {pin['version']}, nothing to open")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        sys.exit("GITHUB_REPOSITORY is not set")
    sync(repo, token, read_pins())


if __name__ == "__main__":
    main()
