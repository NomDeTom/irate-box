# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""No new shell in the code: a subprocess is given an argument list, never a string, and never
shell=True. Read with ast; nothing imported. python3 tests/subprocess_guard.py"""

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNNERS = {"run", "Popen", "call", "check_call", "check_output"}
ALWAYS_A_STRING = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
}


def dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def is_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return is_string(node.left) or is_string(node.right)
    return False


def main():
    failures = []
    for path in sorted((REPO / "irate_box").rglob("*.py")):
        tree = ast.parse(path.read_text(), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            if name in {".".join(pair) for pair in ALWAYS_A_STRING} or name == "asyncio.create_subprocess_shell":
                failures.append(f"{path.relative_to(REPO)}:{node.lineno}: {name} takes a command string")
                continue
            module, _, func = name.rpartition(".")
            if module not in ("subprocess", "asyncio") or func not in RUNNERS:
                continue
            shell = next((k.value for k in node.keywords if k.arg == "shell"), None)
            if isinstance(shell, ast.Constant) and shell.value is True:
                failures.append(f"{path.relative_to(REPO)}:{node.lineno}: {name}(shell=True)")
            command = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "args"), None)
            if command is not None and is_string(command):
                failures.append(f"{path.relative_to(REPO)}:{node.lineno}: {name} is given a command string")
    for failure in failures:
        print("FAIL " + failure)
    print(f"failures: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
