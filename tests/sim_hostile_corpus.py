# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One table of hostile values against every validator and writer in irate_box/validate.py (item 87,
pattern P2). A validator refuses each value it must refuse, and returns each value it accepts
unchanged; a writer refuses or writes it so that the target format's own reading gives the value
back, on one line. A function added to validate.py that is not in the table below fails this test.
python3 tests/sim_hostile_corpus.py"""

import html
import inspect
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import hostile

from irate_box import validate as V

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


CONTROLS = set(hostile.CONTROLS)
PATHISH = {
    "dot dot",
    "a path out",
    "an absolute path",
    "a leading dash",
    "an option with a value",
    "an empty value",
    "an over-long value",
}
META = {"a user at a host", "a semicolon list", "a hash", "a quote and a backslash", "a space"}
ADDRESSES = {f"{why}, as an address" for why in hostile.NUMERIC_HOSTS}
BAD_URLS = set(hostile.BAD_URLS)
HOSTS = {f"{why}, as a host" for why in hostile.NUMERIC_HOSTS}  # a bare numeric text is a fine name
EVERYTHING = set(hostile.ALL)
NAMES_REFUSE = EVERYTHING - HOSTS  # what a strict name refuses: all but the bare numbers

SSID_REFUSES = CONTROLS | {"an empty value", "an over-long value"}  # an SSID is 1 to 32 bytes
TEXT_LINE = CONTROLS | {"an over-long value"}  # text may be empty

# name -> (function of one value, the rows it must refuse, whether what it accepts comes back unchanged)
VALIDATORS = {
    "ssid": (V.ssid, SSID_REFUSES, True),
    "hostname": (V.hostname, NAMES_REFUSE, True),
    "iface": (V.iface, NAMES_REFUSE, True),
    "name": (V.name, NAMES_REFUSE, True),
    "account": (V.account, NAMES_REFUSE, True),
    "package": (V.package, NAMES_REFUSE, True),
    "version": (V.version, NAMES_REFUSE, True),
    "repo": (V.repo, EVERYTHING, True),
    "branch": (V.branch, NAMES_REFUSE, True),
    "sha256": (V.sha256, EVERYTHING, True),
    "card": (V.card, EVERYTHING, True),
    "image": (V.image, EVERYTHING, True),
    "loader": (V.loader, NAMES_REFUSE, True),
    "country": (V.country, EVERYTHING, True),
    "unit": (V.unit, EVERYTHING, True),
    "fs_uuid": (V.fs_uuid, NAMES_REFUSE, True),
    "token": (V.token, EVERYTHING, True),
    "ip": (V.ip, EVERYTHING - {f"{why}, as a host" for why in ("link-local", "private")}, False),
    "text": (V.text, TEXT_LINE, True),
    "text (multiline)": (
        lambda value: V.text(value, multiline=True),
        (CONTROLS - {"a trailing newline", "CRLF and a header", "a tab"}) | {"an over-long value"},
        True,
    ),
    "argv": (lambda value: V.argv([value]), {"a trailing newline", "CRLF and a header", "a NUL"}, False),
    "url": (V.url, EVERYTHING, True),
    "local_path": (V.local_path, EVERYTHING - {"an absolute path"}, True),
    "header_value": (
        V.header_value,
        {"a trailing newline", "CRLF and a header", "a NUL", "a tab", "ESC [ (a 7-bit CSI)"},
        True,
    ),
}


def refuses(function, value):
    try:
        function(value)
    except ValueError:
        return True
    return False


for name, (function, must_refuse, identity) in VALIDATORS.items():
    for row, value in hostile.ALL.items():
        if row in must_refuse:
            check(f"{name}: refuses {row}", refuses(function, value), "accepted")
        elif identity and not refuses(function, value):
            check(f"{name}: what it accepts comes back unchanged ({row})", function(value) == value, function(value))
    for row, value in hostile.JOINERS.items():
        if name in ("ssid", "text", "text (multiline)"):
            check(f"{name}: allows {row}", function(value) == value)

# path: under a root, with real folders.
root = Path(tempfile.mkdtemp(prefix="hostile-")).resolve() / "root"
root.mkdir()
for row, value in hostile.ALL.items():
    if row in CONTROLS or row in {"dot dot", "a path out", "an absolute path", "an empty value"}:
        check(f"path: refuses {row}", refuses(lambda v: V.path(v, root), value), "accepted")
(root / "plain.txt").write_text("x")
(root / "link").symlink_to(root.parent)
check("path: a plain name", V.path("plain.txt", root) == root / "plain.txt")
check("path: a link out", refuses(lambda v: V.path(v, root), "link/x"))

# The writers: what the target format's own reading gives back.


def hostapd_back(value):
    line = V.hostapd_ssid(value)
    key, _, hexed = line.partition("=")
    return "\n" not in line and key == "ssid2" and bytes.fromhex(hexed).decode("utf-8") == value


def keyfile_ssid_back(value):
    line = V.keyfile_ssid(value)
    key, _, body = line.partition("=")
    return (
        "\n" not in line
        and key == "ssid"
        and bytes(int(part) for part in body.rstrip(";").split(";")).decode("utf-8") == value
    )


def glib_unescape(text):
    out, index = [], 0
    mapping = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    while index < len(text):
        if text[index] == "\\":
            out.append(mapping[text[index + 1]])
            index += 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def keyfile_value_back(value):
    written = V.keyfile_value(value)
    return "\n" not in written and "\r" not in written and glib_unescape(written) == value


def config_back(value):
    line = V.config_line("key", value)
    return "\n" not in line and "\r" not in line and line.partition("=")[0] == "key" and line.partition("=")[2] == value


def disposition_back(value):
    header = V.content_disposition(value)
    expected = value.rsplit("/", 1)[-1]
    match = re.search(r"filename\*=UTF-8''(\S+)$", header)
    return bool(match) and "\n" not in header and "\r" not in header and unquote(match.group(1)) == expected


def html_back(value):
    written = V.html_text(value)
    return (
        not any(char in written for char in "<>\"'")
        and html.unescape(written) == value
        and "&"
        not in written.replace("&amp;", "")
        .replace("&lt;", "")
        .replace("&gt;", "")
        .replace("&quot;", "")
        .replace("&#x27;", "")
    )


def log_clean(value):
    written = V.log_text(value)
    return (
        "\n" not in written
        and "\r" not in written
        and "\x1b" not in written
        and not V.has_hidden_character(written.replace("\t", ""))
    )


WRITERS = {
    "hostapd_ssid": (hostapd_back, SSID_REFUSES),
    "keyfile_ssid": (keyfile_ssid_back, SSID_REFUSES),
    "keyfile_value": (keyfile_value_back, {"a NUL"}),
    "config_line value": (config_back, {"a trailing newline", "CRLF and a header", "a NUL"}),
    "content_disposition": (disposition_back, {"a trailing newline", "CRLF and a header", "a NUL", "an empty value"}),
    "html_text": (html_back, set()),
    "log_text": (log_clean, set()),
}
WRITER_FUNCTIONS = {
    "hostapd_ssid": V.hostapd_ssid,
    "keyfile_ssid": V.keyfile_ssid,
    "keyfile_value": V.keyfile_value,
    "config_line value": lambda value: V.config_line("key", value),
    "content_disposition": V.content_disposition,
    "html_text": V.html_text,
    "log_text": V.log_text,
}
for name, (reads_back, must_refuse) in WRITERS.items():
    for row, value in {**hostile.ALL, **hostile.JOINERS}.items():
        if row in must_refuse:
            check(f"{name}: refuses {row}", refuses(WRITER_FUNCTIONS[name], value), "accepted")
        elif not refuses(WRITER_FUNCTIONS[name], value):
            check(f"{name}: the target format reads {row} back as it was, on one line", reads_back(value))
refused_key = [
    row
    for row, value in hostile.ALL.items()
    if row in CONTROLS | BAD_URLS | META and refuses(lambda v: V.config_line(v, "x"), value)
]
check(
    "config_line key: a key with a line break, a space or a quote is refused",
    {"a trailing newline", "CRLF and a header", "a NUL", "a space", "a quote and a backslash"} <= set(refused_key),
    refused_key,
)

# The predicate on its own.
check("has_hidden_character: a plain word is clean", not V.has_hidden_character("plain"))
for row, value in hostile.CONTROLS.items():
    check(f"has_hidden_character: {row}", V.has_hidden_character(value))
for row, value in hostile.JOINERS.items():
    check(f"has_hidden_character: {row} is allowed", not V.has_hidden_character(value))
check("has_hidden_character: newlines allowed on request", not V.has_hidden_character("a\nb\tc\r", newlines=True))
for row, value in hostile.FLAGS.items():
    check(f"ssid: allows {row}", V.ssid(value) == value, "refused")
    check(f"text: allows {row}", V.text(value) == value, "refused")
    check(f"has_hidden_character: {row} is allowed", not V.has_hidden_character(value))
    check(f"log_text: keeps {row} whole", V.log_text(value) == value)
for row, value in hostile.TAG_SMUGGLING.items():
    check(f"ssid: refuses {row}", refuses(V.ssid, value), "accepted")
    check(f"text: refuses {row}", refuses(V.text, value), "accepted")
    check(f"has_hidden_character: {row}", V.has_hidden_character(value))

# Every public function of validate.py is in a table, or here with its reason.
COVERED_ELSEWHERE = {
    "choice": "a member of a list, not text",
    "number": "an integer, not text",
    "path": "above, with real folders",
    "is_ip_spelling": "the numeric host rows, tests/sim_validate.py",
    "public_address": "tests/sim_validate.py",
    "public_url": "tests/sim_internet.py",
    "connect_public": "tests/sim_rebinding.py",
    "has_hidden_character": "above",
    "config_line": "above (the value and the key)",
}
public = {
    name
    for name, thing in vars(V).items()
    if inspect.isfunction(thing) and thing.__module__ == V.__name__ and not name.startswith("_")
}
covered = set(VALIDATORS) | set(WRITERS) | set(COVERED_ELSEWHERE) | {"text (multiline)", "config_line value", "argv"}
check("every public function of validate.py is in the table", public <= covered, sorted(public - covered))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
