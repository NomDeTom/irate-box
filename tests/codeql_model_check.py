#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Proves the CodeQL model pack (.github/codeql/extensions/irate-box-sanitisers) with the CodeQL
CLI. It builds two small databases and runs the path-injection and log-injection queries on each
with the pack:

  * "model": a stand-in irate_box/validate.py whose checks do nothing CodeQL can see, and the
    fixture flows (tests/codeql_fixture/flows.py.txt). A flow through a shared check can only be
    cleared by the pack's rows, so a respelled row, a renamed function or a kind no query reads
    leaves it flagged.
  * "real": the real irate_box/ and the same flows. Nothing inside irate_box/validate.py may be
    flagged (the checks are written in the shape CodeQL recognises), and what is still open
    elsewhere is listed.

In both, every flow through a check must be cleared and every line marked `# control: <kind>`
must stay flagged (so the queries themselves work).

It needs the CodeQL CLI (CODEQL=/path/to/codeql, or `codeql` on PATH, or ~/.local/opt/codeql/codeql)
and takes a few minutes and about 1.5 GB: run it through the tests lock, not in tests/run.sh.

    python3 tests/codeql_model_check.py [--model-dir DIR] [--package DIR] [--keep]

--model-dir is the folder that holds the pack folder; it defaults to .github/codeql/extensions.
Point it at a folder with the pack's old spelling to see the check fail; --package points it at
another copy of the irate_box folder (one with the old validate.path, say).
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACK = "irate-box/irate-box-sanitisers"
QUERIES = {
    "path-injection": "codeql/python-queries:Security/CWE-022/PathInjection.ql",
    "log-injection": "codeql/python-queries:Security/CWE-117/LogInjection.ql",
    # No model can clear a header flow (python-all reads no barrier kind for one); the shared writers
    # are written in the shape its query recognises, so none may be left open in the real code.
    "header-injection": "codeql/python-queries:Security/CWE-113/HeaderInjection.ql",
}
MEMORY = "1500"


def find_codeql():
    for candidate in (os.environ.get("CODEQL"), shutil.which("codeql"), str(Path.home() / ".local/opt/codeql/codeql")):
        if candidate and Path(candidate).is_file():
            return candidate
    sys.exit("No CodeQL CLI found: set CODEQL=/path/to/codeql.")


def run(command, folder):
    # Run in the working folder, away from the repository: the CLI also looks for packs under the
    # folder it is started in, and would find the repository's own copy of the pack there.
    result = subprocess.run(command, text=True, capture_output=True, cwd=folder)
    if result.returncode:
        sys.exit(
            f"{' '.join(command[:3])} failed ({result.returncode}):\n{result.stdout[-2000:]}\n{result.stderr[-3000:]}"
        )
    return result


def results(csv_path):
    with open(csv_path, newline="") as handle:
        return {(row[4].lstrip("/"), int(row[5])) for row in csv.reader(handle)}


def lay_out(destination, stub):
    """The fixture's files under `destination`: flows in fixture/, and with `stub` the stand-in
    irate_box package. They are kept as *.py.txt in the repository so that code scanning does not
    read the deliberate control flow as a finding; they are given back their .py here."""
    sources = {REPO / "tests/codeql_fixture/flows.py.txt": destination / "fixture/flows.py"}
    if stub:
        for found in (REPO / "tests/codeql_fixture/stub/irate_box").glob("*.py.txt"):
            sources[found] = destination / "irate_box" / found.name.removesuffix(".txt")
    for source, target in sources.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def analyse(codeql, work, label, source, model_dir, judged):
    """Build a database of `source`, run both queries with the pack, and judge the fixture's lines
    and, when `judged` is true, the real irate_box/validate.py. Returns the number of failures."""
    database = work / f"db-{label}"
    run(
        [
            codeql,
            "database",
            "create",
            str(database),
            "--language=python",
            f"--source-root={source}",
            "--threads=1",
            "-M",
            MEMORY,
            "--overwrite",
        ],
        work,
    )
    fixture_lines = (source / "fixture/flows.py").read_text().splitlines()
    failed = 0
    for kind, query in QUERIES.items():
        if kind == "header-injection" and not judged:
            continue
        output = work / f"{label}-{kind}.csv"
        run(
            [
                codeql,
                "database",
                "analyze",
                str(database),
                query,
                "--format=csv",
                f"--output={output}",
                "--rerun",
                "-q",
                "-M",
                MEMORY,
                "--threads=1",
                f"--additional-packs={model_dir}",
                f"--model-packs={PACK}",
            ],
            work,
        )
        found = results(output)
        expected = {
            ("fixture/flows.py", number)
            for number, line in enumerate(fixture_lines, 1)
            if re.search(rf"# control: {kind}\b", line)
        }
        flagged_fixture = {item for item in found if item[0] == "fixture/flows.py"}
        inside_validate = {item for item in found if item[0] == "irate_box/validate.py"} if judged else set()
        elsewhere = found - flagged_fixture - {item for item in found if item[0] == "irate_box/validate.py"}
        problems = []
        if expected - flagged_fixture:
            problems.append(f"control not flagged: {sorted(expected - flagged_fixture)}")
        if flagged_fixture - expected:
            problems.append(f"sanitised flow still flagged: {sorted(flagged_fixture - expected)}")
        if inside_validate:
            problems.append(f"flagged inside irate_box/validate.py: {sorted(inside_validate)}")
        if kind == "header-injection" and elsewhere:
            problems.append(f"an HTTP header still built from a user value: {sorted(elsewhere)}")
        print(
            f"{'FAIL' if problems else 'PASS'} {label}, {kind}: {len(expected)} control(s) flagged, "
            f"{len(flagged_fixture - expected)} sanitised flow(s) still flagged"
            + (f", {len(inside_validate)} inside validate.py" if judged else "")
        )
        for problem in problems:
            print(f"  {problem}")
        for where in sorted(elsewhere) if judged else []:
            print(f"  open elsewhere: {where[0]}:{where[1]}")
        failed += bool(problems)
    return failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", default=str(REPO / ".github/codeql/extensions"))
    parser.add_argument("--package", default=str(REPO / "irate_box"), help="the irate_box folder to analyse")
    parser.add_argument("--keep", action="store_true", help="keep the working folder and print where it is")
    arguments = parser.parse_args()
    codeql = find_codeql()
    model_dir = Path(arguments.model_dir).resolve()
    work = Path(tempfile.mkdtemp(prefix="irate-box-codeql."))
    try:
        failed = 0
        # 1. The stand-in module: only the model pack can clear a flow through it.
        stub = work / "src-stub"
        lay_out(stub, stub=True)
        failed += analyse(codeql, work, "model", stub, model_dir, judged=False)
        # 2. The real module: the same flows, and nothing flagged inside validate.py.
        real = work / "src-real"
        shutil.copytree(arguments.package, real / "irate_box", ignore=shutil.ignore_patterns("__pycache__"))
        lay_out(real, stub=False)
        failed += analyse(codeql, work, "real", real, model_dir, judged=True)
        return 1 if failed else 0
    finally:
        if arguments.keep:
            print(f"kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
