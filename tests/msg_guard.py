# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Messages (irate_box/msg.py): every M(...) in the source is a whole sentence with a stable ID, and every
translation still fits the English it was made from. Read with ast, nothing imported.

  every ID a string literal of the form area.thing.case (lower case, dots and hyphens)
  the English a literal: a string, or a dict of strings by CLDR plural category with "other"; never an
    f-string or a + expression
  one ID, one English: the same ID used twice says the same thing
  every placeholder in the English passed as an argument, and every argument used; a plural passes n
  a finding (finding.py) says its summary, why and fix as messages, never as a sentence built in place
  i18n/<lang>.json: each translation's placeholders those of the English; a translation made from other
    English than the code's now is stale, and one whose ID is gone is unused (both warnings)

python3 tests/msg_guard.py  [--list: every ID and its English]"""
import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import ID_RE, PLACEHOLDER_RE  # noqa: E402

CATEGORIES = {"zero", "one", "two", "few", "many", "other"}
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def warn(name, items):
    if items:
        print(f"WARN {name}  {items}")


def callee(node):
    f = node.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def english(node):
    """The English of an M(...): a str, a {category: str} dict, or None when it is not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(v, ast.Constant) and isinstance(v.value, str)
                                          for k, v in zip(node.keys, node.values, strict=False)):
        return {k.value: v.value for k, v in zip(node.keys, node.values, strict=False)}
    return None


def placeholders(en):
    texts = en.values() if isinstance(en, dict) else [en]
    return {p for t in texts for p in PLACEHOLDER_RE.findall(t)}


def walk_outside_messages(node):
    """ast.walk, but not into an M(...) or named(...): what a message is given as its data (a percentage made with an
    f-string) is data, and the message's own English is checked as a message."""
    yield node
    if isinstance(node, ast.Call) and callee(node) in ("M", "named"):
        return
    for child in ast.iter_child_nodes(node):
        yield from walk_outside_messages(child)


messages = {}            # id -> (english, where)
bad_id, not_literal, two_english, args_wrong, plural_wrong, built = [], [], [], [], [], []
sources = sorted((REPO / "irate_box").rglob("*.py"))
for path in sources:
    rel = path.relative_to(REPO)
    tree = ast.parse(path.read_text(), str(rel))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = callee(node)
        where = f"{rel}:{node.lineno}"
        if name == "M" and rel.name != "msg.py":
            if len(node.args) != 2:
                not_literal.append(where)
                continue
            mid = node.args[0].value if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) else None
            en = english(node.args[1])
            if mid is None or not ID_RE.match(mid):
                bad_id.append(f"{where} {mid!r}")
            if en is None:
                not_literal.append(f"{where} {mid}")
                continue
            if isinstance(en, dict) and ("other" not in en or set(en) - CATEGORIES):
                plural_wrong.append(f"{where} {mid}: {sorted(en)}")
            passed = {k.arg for k in node.keywords}
            if None not in passed:   # **args: not checked here
                need = placeholders(en) | ({"n"} if isinstance(en, dict) else set())
                if need != passed:
                    args_wrong.append(f"{where} {mid}: says {sorted(placeholders(en))}, given {sorted(passed)}")
            if mid in messages and messages[mid][0] != en:
                two_english.append(f"{mid}: {messages[mid][1]} and {where}")
            messages.setdefault(mid, (en, where))
        elif name == "finding":
            # finding(fid, title, status, summary, why, fix, ...): the sentences are messages or data, never built here.
            said = node.args[3:6] + [k.value for k in node.keywords if k.arg in ("summary", "why", "fix", "title")] + node.args[1:2]
            for arg in said:
                for sub in walk_outside_messages(arg):
                    if isinstance(sub, ast.JoinedStr) or (isinstance(sub, ast.BinOp) and isinstance(sub.op, (ast.Add, ast.Mod))
                                                          and any(isinstance(x, (ast.Constant, ast.JoinedStr)) for x in (sub.left, sub.right))):
                        built.append(where)
                        break
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub is arg:
                        built.append(where)
                        break


def main():
    if "--list" in sys.argv:
        for mid, (en, where) in sorted(messages.items()):
            print(f"{mid}\t{json.dumps(en, ensure_ascii=False)}\t{where}")
        sys.exit(0)

    check(f"messages found ({len(messages)})", len(messages) > 50, len(messages))
    check("every ID is a literal of the form area.thing.case", not bad_id, bad_id)
    check("every English is a literal string, or a dict of literal plural forms", not not_literal, not_literal)
    check("one ID, one English", not two_english, two_english)
    check("every plural has other, and only CLDR categories", not plural_wrong, plural_wrong)
    check("every placeholder is passed, every argument is used, every plural passes n", not args_wrong, args_wrong)
    check("findings say their summary, why, fix and title as messages, not sentences built in place", not built, built)

    stale, unused, mismatched = [], [], []
    for cat in sorted((REPO / "i18n").glob("*.json")) if (REPO / "i18n").is_dir() else []:
        try:
            entries = json.loads(cat.read_text())
        except ValueError as exc:
            check(f"{cat.name} reads as JSON", False, exc)
            continue
        for mid, entry in entries.items():
            if mid not in messages:
                unused.append(f"{cat.name} {mid}")
                continue
            en = messages[mid][0]
            if entry.get("en") != en:
                stale.append(f"{cat.name} {mid}")
            if placeholders(entry.get("text", "")) != placeholders(en):
                mismatched.append(f"{cat.name} {mid}")
    check("every translation has the placeholders of its English", not mismatched, mismatched)
    warn("translations made from other English than the code's now (stale)", stale)
    warn("translations of IDs no longer in the code (unused)", unused)

    print("ok" if not fails else f"{fails} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
