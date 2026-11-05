# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The pages' text and log lines: HTML text goes through the escaping writer, a program's last
line loses terminal escapes and control characters, and the pages show logs as text.
python3 tests/sim_pages_logs.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.root import hub_control

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


check("HTML: markup escaped", V.html_text("<b>&\"'") == "&lt;b&gt;&amp;&quot;&#x27;")
check("HTML: a quote may be left alone where an attribute is not used", V.html_text('a"b', quote=False) == 'a"b')
check("HTML: text kept as text", V.html_text("Café & tea") == "Café &amp; tea")

check("log: a terminal colour removed", V.log_text("\x1b[31mred\x1b[0m") == "red")
check("log: a terminal title removed", V.log_text("\x1b]0;title\x07text") == "text")
check("log: a title ended by ESC backslash removed", V.log_text("\x1b]0;title\x1b\\text") == "text")
_started = __import__("time").time()
V.log_text("\x1b]" * 20000 + "x")
V.log_text("\x1b]0;" * 10000)
check(
    "log: thousands of unfinished titles are read in linear time",
    __import__("time").time() - _started < 2,
    __import__("time").time() - _started,
)
check(
    "log: an 8-bit CSI and a bidi override removed",
    V.log_text("a\x9b31mred\u202eevil") == "aredevil",
    V.log_text("a\x9b31mred\u202eevil"),
)
check(
    "log: an 8-bit title and a line separator",
    V.log_text("a\x9d0;t\x9cb\u2028c\x85d") == "ab c d",
    V.log_text("a\x9d0;t\x9cb\u2028c\x85d"),
)
check(
    "log: an emoji sequence keeps its joiners", V.log_text("\U0001f468\u200d\U0001f469") == "\U0001f468\u200d\U0001f469"
)
check("log: CR and LF become spaces", V.log_text("a\rb\nc") == "a b c")
check("log: NUL and other controls removed", V.log_text("a\x00b\x08c") == "abc")
check("log: a tab kept", V.log_text("a\tb") == "a\tb")
check("log: length bounded", V.log_text("x" * 400, "The line", 10) == "x" * 10)

check("program answer: the last line", hub_control._last_line("first\nsecond\n") == "second")
check("program answer: escapes and controls cleaned", hub_control._last_line("x\n\x1b[31mred\x1b[0m\x00\n") == "red")
check("program answer: nothing printed is ?", hub_control._last_line("") == "?")
check("program answer: only controls is ?", hub_control._last_line("\x00\x08") == "?")
check("program answer: length bounded", hub_control._last_line("y" * 500, 12) == "y" * 12)

server = (REPO / "irate_box/hub/server.py").read_text()
check("server: HTML text goes through validate.html_text", "validate.html_text(" in server)
check("server: no direct html.escape", "html.escape(" not in server and "import html\n" not in server)

admin = (REPO / "web/admin.js").read_text()
for name in ("upd.log.textContent", "sec.log.textContent", "hl.log.textContent", "ado.log.textContent"):
    check(f"pages: {name} shows a log as text", name in admin)
for page, name in (("web/board.js", "esc(t.title)"), ("web/shoutbox.js", "esc(m.name)")):
    check(f"{page}: user text escaped", name in (REPO / page).read_text())

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
