# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
r"""Every value checked with a pattern is matched whole (item 87, pattern P1). In Python `$` also
matches before a trailing newline, so `re.match(r"^[a-z]+$", "abc\\n")` is a match and a name or a
password with one extra line passes. Read with ast; nothing imported from irate_box.

Under irate_box/**/*.py, fail on

  * a pattern with a `$` anchor (no re.MULTILINE) used with `.match(` or `.search(`: use
    `fullmatch`, or end the pattern with `\\Z`;
  * a `.match(` whose result gates a value (`if not X.match(...): raise/return/continue`) with a
    pattern that has no end anchor at all, so a prefix of a bad value passes.

A site that reads multi-line tool output, or looks at the start of a line on purpose, is not
validation: it carries `# parse: <what is read>` on a line of the call or of the pattern's
definition, and the guard leaves it alone.

python3 tests/regex_anchor_guard.py"""

import ast
import re
import sys
from pathlib import Path

try:
    from re import _parser as sre_parse
except ImportError:  # Python before 3.11
    import sre_parse

REPO = Path(__file__).resolve().parents[1]
MARKER = re.compile(r"#\s*parse:\s*\S")
FLAG_NAMES = {
    "M": re.M,
    "MULTILINE": re.M,
    "I": re.I,
    "IGNORECASE": re.I,
    "S": re.S,
    "DOTALL": re.S,
    "X": re.X,
    "VERBOSE": re.X,
    "A": re.A,
    "ASCII": re.A,
    "U": re.U,
    "UNICODE": re.U,
}


def flags_of(node):
    """The flags a pattern is compiled with, as far as they are written out in the source."""
    if node is None:
        return 0
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re":
        return FLAG_NAMES.get(node.attr, 0)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return flags_of(node.left) | flags_of(node.right)
    return 0


def constant_text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = constant_text(node.left), constant_text(node.right)
        return None if left is None or right is None else left + right
    return None


def anchors_of(pattern, flags):
    r"""(has `$`, has `\\Z`) for a pattern, not counting a `$` that MULTILINE turns into a line end."""
    try:
        compiled = re.compile(pattern, flags)
        tree = sre_parse.parse(pattern, compiled.flags)
    except re.error:
        return False, False
    found = set()

    def walk(items):
        for item in items:
            if isinstance(item, tuple) and len(item) == 2 and str(item[0]) == "AT":
                found.add(str(item[1]))
            elif isinstance(item, (tuple, list, sre_parse.SubPattern)):
                walk(item)

    walk(tree)
    multiline = bool(compiled.flags & re.M)
    return ("AT_END" in found and not multiline), ("AT_END_STRING" in found or ("AT_END" in found and not multiline))


def compile_call(node):
    """(pattern text, flags) when `node` is re.compile(<constant>, ...), else None."""
    if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "re.compile" and node.args):
        return None
    text = constant_text(node.args[0])
    if text is None:
        return None
    flag_node = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "flags"), None)
    return text, flags_of(flag_node)


class Module:
    def __init__(self, path):
        self.path = path
        self.relative = str(path.relative_to(REPO))
        self.source = path.read_text()
        self.lines = self.source.splitlines()
        self.tree = ast.parse(self.source, str(path))
        self.patterns = {}  # last name -> [(pattern, flags, first line, last line)]
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                compiled = compile_call(node.value)
                if name and compiled:
                    self.patterns.setdefault(name, []).append((*compiled, node.lineno, node.end_lineno))

    def marked(self, first, last):
        return any(MARKER.search(self.lines[number - 1]) for number in range(first, last + 1))


def scan():
    """Every use of a pattern that breaks a rule: (message, module, call node, method, pattern)."""
    modules = [Module(path) for path in sorted((REPO / "irate_box").rglob("*.py"))]
    everywhere = {}
    for module in modules:
        for name, entries in module.patterns.items():
            everywhere.setdefault(name, []).extend((module, *entry) for entry in entries)

    found, checked = [], 0

    def lookup(module, name):
        own = [(module, *entry) for entry in module.patterns.get(name, [])]
        return own or everywhere.get(name, [])

    for module in modules:
        gates = {}  # id of a .match call -> the `if` that refuses on it
        for node in ast.walk(module.tree):
            if isinstance(node, ast.If):
                tests = node.test.values if isinstance(node.test, ast.BoolOp) else [node.test]
                refuses = any(isinstance(inner, (ast.Raise, ast.Return, ast.Continue)) for inner in node.body)
                for test in tests:
                    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and refuses:
                        gates[id(test.operand)] = node
        for node in ast.walk(module.tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("match", "search")
            ):
                continue
            receiver, method = node.func.value, node.func.attr
            sites = []  # (module, pattern, flags, first line, last line) of where the pattern is written
            if ast.unparse(receiver) == "re" and node.args:
                text = constant_text(node.args[0])
                flag_node = (
                    node.args[2]
                    if len(node.args) > 2
                    else next((k.value for k in node.keywords if k.arg == "flags"), None)
                )
                if text is not None:
                    sites.append((module, text, flags_of(flag_node), node.lineno, node.end_lineno))
                elif isinstance(node.args[0], (ast.Name, ast.Attribute)):
                    last = node.args[0].id if isinstance(node.args[0], ast.Name) else node.args[0].attr
                    sites.extend(lookup(module, last))
            elif compile_call(receiver):
                sites.append((module, *compile_call(receiver), receiver.lineno, receiver.end_lineno))
            elif isinstance(receiver, (ast.Name, ast.Attribute)):
                last = receiver.id if isinstance(receiver, ast.Name) else receiver.attr
                sites.extend(lookup(module, last))
            for owner, pattern, flags, first, last_line in sites:
                checked += 1
                dollar, end_any = anchors_of(pattern, flags)
                called_at = f"{module.relative}:{node.lineno}"
                if module.marked(node.lineno, node.end_lineno):
                    continue
                if owner.marked(first, last_line):
                    continue
                if dollar:
                    found.append(
                        (
                            f"{called_at}: .{method}( with a `$` pattern {pattern!r}: use fullmatch, or end it with \\Z",
                            module,
                            node,
                            method,
                            pattern,
                        )
                    )
                elif id(node) in gates and method == "match" and not end_any:
                    found.append(
                        (
                            f"{called_at}: a refusal on .match( with a pattern that has no end anchor, {pattern!r}",
                            module,
                            node,
                            method,
                            pattern,
                        )
                    )
    return found, checked


def main():
    found, checked = scan()
    messages = sorted({entry[0] for entry in found})
    for message in messages:
        print("FAIL " + message)
    print(f"checked {checked} uses of patterns; failures: {len(messages)}")
    return 1 if messages else 0


if __name__ == "__main__":
    sys.exit(main())
