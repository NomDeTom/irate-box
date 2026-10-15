# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One finding, the shape every doctor reports in. Each part has one job, so the page can lay them out
the same way for every doctor, and a translation can replace each sentence whole:

  title     what was checked: a message, or msg.named(thing, role)
  status    ok | warn | problem
  summary   one sentence: what the check found
  why       why it matters: the consequence, or the way an attacker uses it (None when status is ok)
  fix       what to do, as an instruction (None when there is nothing to do, or the actions say it)
  notes     more sentences the reader needs, each a message: a limit of the check, the age of its data
  commands  shell commands to copy, exactly as typed: never translated, nothing added around them
  evidence  what the check saw, as data: names, paths, packages, log lines. Never translated
  actions   the buttons: {choice, label, confirm?}; label and confirm are messages

Every message is msg.M (or msg.named). Extra keys a page needs (a badge's numbers, a doctor's ref,
source and about) are passed through as they are. Stdlib only.
"""

from irate_box import msg

STATUSES = ("ok", "warn", "problem")


def finding(fid, title, status, summary, why=None, fix=None, *, notes=(), commands=(), evidence=(), actions=(), **extra):
    if status not in STATUSES:
        raise ValueError(f"finding {fid}: status {status!r}")
    return {"id": fid, "title": title, "status": status, "summary": summary, "why": why, "fix": fix,
            "notes": list(notes), "commands": list(commands), "evidence": [str(e) for e in evidence],
            "actions": list(actions), **extra}


def action(choice, label, confirm=None, **extra):
    """One button: label a message, confirm a message (the question asked first) or None."""
    return {"choice": choice, "label": label, **({"confirm": confirm} if confirm else {}), **extra}


def legacy(f):
    """The old shape's strings, in English, for code that still reads title, detail and fix as text (the
    Security doctor's import of the Security page's scan; the installer's summary). It goes when they do."""
    detail = msg.text(f.get("summary"), f.get("why"), *f.get("notes", ()))
    if f.get("evidence"):
        detail += f" ({', '.join(f['evidence'])})"
    fix = msg.text(f.get("fix"))
    if f.get("commands"):
        fix = msg.text(fix, *(f"`{c}`" for c in f["commands"]))
    return {"title": msg.render(f.get("title")), "detail": detail, "fix": fix}


def message_ids(f):
    """The IDs of what a finding says (summary, why, fix, notes): what a test checks, so a change of wording
    needs no change of test."""
    return {m["id"] for m in (f.get("summary"), f.get("why"), f.get("fix"), *f.get("notes", ())) if msg.is_message(m)}
