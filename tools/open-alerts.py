#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The open code-scanning alerts on refs/heads/main (never a pull request's ref), set against the
dismissals a review has proposed. Not a CI gate: a person or a review session runs it after a merge.

    python3 tools/open-alerts.py --plan PLAN.md [--ref refs/heads/main]

Reads the plan's "Dismissals proposed" section: a line that starts `- #NNN` names the alert and
gives the reason after the first dash. Needs the `gh` CLI, signed in, run inside the repository.
Prints each open alert (number, rule, place) with the proposed reason, or UNEXPLAINED. Exits 1 when any open alert has no reason."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def open_alerts(ref):
    result = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/code-scanning/alerts?state=open&per_page=100&ref={ref}", "--paginate"],
        capture_output=True,
        text=True,
        check=True,
    )
    # --paginate prints one JSON array per page, back to back
    decoder, text, alerts, index = json.JSONDecoder(), result.stdout, [], 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        page, index = decoder.raw_decode(text, index)
        alerts.extend(page)
    return alerts


def proposed(plan_path):
    """alert number -> the reason, from the plan's "Dismissals proposed" section."""
    text = Path(plan_path).read_text()
    section = text.split("## Dismissals proposed", 1)
    reasons = {}
    for line in section[1].splitlines() if len(section) == 2 else []:
        found = re.match(r"- \*{0,2}#(\d+)\*{0,2}[^—-]*[—-]\s*(.+)", line)
        if found:
            reasons[int(found.group(1))] = found.group(2).strip()
    return reasons


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="the plan file with a Dismissals proposed section")
    parser.add_argument("--ref", default="refs/heads/main")
    arguments = parser.parse_args()
    reasons = proposed(arguments.plan)
    alerts = sorted(open_alerts(arguments.ref), key=lambda alert: alert["number"])
    unexplained = 0
    for alert in alerts:
        where = alert["most_recent_instance"]["location"]
        reason = reasons.get(alert["number"])
        unexplained += reason is None
        print(
            f"#{alert['number']} {alert['rule']['id']} {where['path']}:{where['start_line']}  {reason or 'UNEXPLAINED'}"
        )
    stale = sorted(set(reasons) - {alert["number"] for alert in alerts})
    print(f"{len(alerts)} open on {arguments.ref}; {unexplained} without a proposed reason")
    if stale:
        print(f"proposed for alerts that are no longer open: {', '.join(f'#{number}' for number in stale)}")
    return 1 if unexplained else 0


if __name__ == "__main__":
    sys.exit(main())
