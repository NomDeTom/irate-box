# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A download's name cannot split its header, and the build and factory names are matched whole.
python3 tests/sim_downloads.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import ci, factory

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ValueError as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


plain = V.content_disposition("irate-box-1.2.3.tar.gz")
check(
    "download: a plain name",
    plain == "attachment; filename=\"irate-box-1.2.3.tar.gz\"; filename*=UTF-8''irate-box-1.2.3.tar.gz",
    plain,
)
quoted = V.content_disposition('a"b\\c.zip')
check("download: quotes and a backslash are replaced in the plain name", 'filename="a_b_c.zip"' in quoted, quoted)
check("download: the real name is still in filename*", "filename*=UTF-8''a%22b%5Cc.zip" in quoted, quoted)
unicode = V.content_disposition("Café.zip")
check(
    "download: a non-ASCII name is ASCII in the plain name and UTF-8 in filename*",
    'filename="Caf_.zip"' in unicode and "Caf%C3%A9.zip" in unicode,
    unicode,
)
check("download: a path becomes its own name", 'filename="passwd"' in V.content_disposition("../../etc/passwd"))
for bad, why in (("a\rb", "CR"), ("a\nb", "LF"), ("a\0b", "NUL"), ("", "empty")):
    refused(f"download: a name with {why} refused", V.content_disposition, bad)

# --- ci and the factory: a trailing newline is a second line ---
source_ci = (REPO / "irate_box/hub/ci.py").read_text()
source_factory = (REPO / "irate_box/hub/factory.py").read_text()
for module, source, names in (
    ("ci", source_ci, ("NAME_RE", "RUN_RE", "ENV_RE", "FAMILY_RE", "ARTIFACT_RE", "TAG_RE")),
    (
        "factory",
        source_factory,
        ("REF_RE", "ENV_RE", "VERSION_RE", "FILE_RE", "TAG_RE", "ARTIFACT_RE", "ci.ENV_RE", "ci.FAMILY_RE"),
    ),
):
    for name in names:
        check(f"{module}.{name} is matched whole", f"{name}.match(" not in source)
for pattern, value in (
    (ci.RUN_RE, "repo/1\n"),
    (ci.NAME_RE, "repo\n"),
    (factory.REF_RE, "main\n"),
    (factory.FILE_RE, "file.bin\n"),
    (factory.VERSION_RE, "1.2.3.abcdef0\n"),
    (factory.ENV_RE, "env\n"),
):
    check(f"{pattern.pattern}: a trailing newline refused", pattern.fullmatch(value) is None)
check("ci.run_file: a run with a newline is no run", ci.run_file("repo/1\n", "log.txt") is None)
check("factory.public_file: a run with a newline is no run", factory.public_file("1\n", "x.bin") is None)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
