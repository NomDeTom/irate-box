# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The CodeQL model pack tests itself (item 87, pattern P4). Every row of
.github/codeql/extensions/irate-box-sanitisers/sanitisers.model.yml must name a top-level package
that imports, a `Member[...]` path that walks to a real function, and a barrier kind that the
python-all queries read. A row spelled the way #688 spelled it (the type `irate_box.validate`) or
with a kind no query reads (an HTTP header kind) is refused, so a respelled row, a renamed function
or a dead kind fails here and not silently. tests/codeql_model_check.py then proves the rows with
the CodeQL CLI (needs CodeQL; not run here). python3 tests/sim_codeql_model.py"""

import importlib
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
PACK_DIR = REPO / ".github/codeql/extensions/irate-box-sanitisers"

# The barrier kinds codeql/python-all 7.2.7 reads: every `ModelOutput::barrierNode(this, "...")` in
# semmle/python/security/dataflow/*Customizations.qll. None is a header kind, so a model for an HTTP
# header value can not clear anything.
KINDS_READ = {
    "code-injection",
    "command-injection",
    "html-injection",
    "js-injection",
    "log-injection",
    "path-injection",
    "request-forgery",
    "sql-injection",
    "unsafe-deserialization",
    "url-redirection",
}

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def rows_of(text):
    """The rows under `data:` of a barrierModel extension, as lists of three strings. Reads only
    the shape this pack uses, and refuses anything else rather than skipping it."""
    rows, current, in_data = [], None, False
    for line in text.splitlines():
        line = re.sub(r"\s+#.*$", "", line.rstrip())
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.fullmatch(r"\s*data:", line):
            in_data = True
            continue
        if not in_data:
            continue
        start = re.fullmatch(r"\s*- - (\S.*)", line)
        more = re.fullmatch(r"\s{4,}- (\S.*)", line)
        if start:
            current = [start.group(1)]
            rows.append(current)
        elif more and current is not None:
            current.append(more.group(1))
        else:
            raise ValueError(f"unexpected line under data: {line!r}")
    return rows


def problems_in(rows):
    """What is wrong with each row: the type, the walk to a function, the kind."""
    problems = []
    for row in rows:
        if len(row) != 3:
            problems.append(f"{row}: a row is a type, a path and a kind")
            continue
        type_, access, kind = row
        if kind not in KINDS_READ:
            problems.append(f"{row}: no python-all query reads the barrier kind {kind!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", type_):
            problems.append(f"{row}: the type must be a top-level package, not {type_!r}")
            continue
        try:
            package = importlib.import_module(type_)
        except ImportError as exc:
            problems.append(f"{row}: the type does not import ({exc})")
            continue
        steps = access.split(".")
        if not steps or steps[-1] != "ReturnValue":
            problems.append(f"{row}: the path must end in ReturnValue")
            continue
        thing = package
        for step in steps[:-1]:
            found = re.fullmatch(r"Member\[([A-Za-z_][A-Za-z0-9_]*)\]", step)
            if not found:
                problems.append(f"{row}: the step {step!r} is not Member[name]")
                break
            member = found.group(1)
            if not hasattr(thing, member) and hasattr(thing, "__path__"):
                try:  # a module of a package is a member once something has imported it
                    importlib.import_module(f"{thing.__name__}.{member}")
                except ImportError:
                    pass
            if not hasattr(thing, member):
                problems.append(f"{row}: {getattr(thing, '__name__', thing)} has no {member}")
                break
            thing = getattr(thing, found.group(1))
        else:
            if not callable(thing):
                problems.append(f"{row}: the path does not end at a function")
    return problems


pack_text = (PACK_DIR / "codeql-pack.yml").read_text()
model_text = (PACK_DIR / "sanitisers.model.yml").read_text()

check(
    "pack: the name is lower case with hyphens",
    re.search(r"^name: [a-z0-9-]+/[a-z0-9-]+$", pack_text, re.M) is not None,
)
check("pack: it is a library for python-all", "library: true" in pack_text and "codeql/python-all" in pack_text)
check("pack: it lists the model file", "sanitisers.model.yml" in pack_text)
check(
    "model: it extends barrierModel of python-all",
    "pack: codeql/python-all" in model_text and "extensible: barrierModel" in model_text,
)

rows = rows_of(model_text)
check("model: it has rows", len(rows) >= 2, rows)
check(
    "model: the path and log checks are modelled",
    {(row[1], row[2]) for row in rows if len(row) == 3}
    >= {
        ("Member[validate].Member[path].ReturnValue", "path-injection"),
        ("Member[validate].Member[log_text].ReturnValue", "log-injection"),
    },
    rows,
)
shipped = problems_in(rows)
check("model: every row names a package, a function and a kind that is read", not shipped, shipped)

# The bugs this guards against, put back: each must be found.
BAD = {
    "the type spelled irate_box.validate (#688)": ["irate_box.validate", "Member[path].ReturnValue", "path-injection"],
    "a header kind that no query reads (#688)": [
        "irate_box",
        "Member[validate].Member[content_disposition].ReturnValue",
        "http-response-splitting",
    ],
    "the other header kind (#688)": [
        "irate_box",
        "Member[validate].Member[content_disposition].ReturnValue",
        "header-injection",
    ],
    "a function that was renamed": ["irate_box", "Member[validate].Member[path_old].ReturnValue", "path-injection"],
    "a module that was renamed": ["irate_box", "Member[checks].Member[path].ReturnValue", "path-injection"],
    "a package that does not exist": ["not_irate_box", "Member[validate].Member[path].ReturnValue", "path-injection"],
    "a path that ends at a module": ["irate_box", "Member[validate].ReturnValue", "path-injection"],
    "a path without ReturnValue": ["irate_box", "Member[validate].Member[path]", "path-injection"],
}
for name, row in BAD.items():
    check(f"refused: {name}", bool(problems_in([row])))
check(
    "the fixture has one control per kind",
    all(
        f"# control: {kind}" in (REPO / "tests/codeql_fixture/flows.py").read_text()
        for kind in {row[2] for row in rows}
    ),
)

# The stand-in module of the fixture has the real functions' names and signatures.
import importlib.util
import inspect

from irate_box import validate as real_validate

spec = importlib.util.spec_from_file_location("stub_validate", REPO / "tests/codeql_fixture/stub/irate_box/validate.py")
stub_validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub_validate)
for function in ("path", "log_text"):
    check(
        f"stub: {function} has the real signature",
        inspect.signature(getattr(stub_validate, function)) == inspect.signature(getattr(real_validate, function)),
    )

# When the CodeQL CLI is here, the kinds above are compared with the queries' own source.
codeql = os.environ.get("CODEQL") or str(Path.home() / ".local/opt/codeql/codeql")
python_all = sorted((Path(codeql).parent / "qlpacks/codeql/python-all").glob("*/semmle/python/security/dataflow"))
if python_all:
    seen = set()
    for source in python_all[-1].glob("*Customizations.qll"):
        for group in re.findall(r"ModelOutput::barrierNode\(this,\s*([^)]*)\)", source.read_text()):
            seen.update(re.findall(r'"([a-z-]+)"', group))
    check("kinds: the list above is what python-all reads", seen == KINDS_READ, f"{sorted(seen ^ KINDS_READ)}")
else:
    print("SKIP kinds: no CodeQL packs here to compare with")

print(f"failures: {fails}" if fails else "ok")
sys.exit(1 if fails else 0)
