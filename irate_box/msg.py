# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What the box says to a person, as data: each message a whole sentence in English, with a stable ID
that its translations are kept under (i18n/<lang>.json, later). Stdlib only.

    M("root-password.none", "Root has no password.")
    M("unit.failed", "The service stopped: {reason}.", reason=why)
    M("updates.waiting", {"one": "{n} security update is waiting.",
                          "other": "{n} security updates are waiting."}, n=len(pkgs))

A message whose words depend on a case is several messages, one per case: the code selects one, and
never builds a sentence from parts. Placeholders are named. A plural is a dict of whole sentences by
CLDR category (English: one, other), selected by the argument n. A list argument stays a list: it is
joined for the reader's language when shown. An argument can be another message.

Nothing renders a message until it reaches the reader: the page in the reader's language (admin-widgets.js,
AW.say), the terminal in English (render). tests/msg_guard.py checks every M(...) in the source. A moment is
an argument made by when(epoch), a length of time one made by duration(seconds): each said as the reader's
language says it.

A module refuses with Refused(M(...)): a ValueError whose English is its text, and whose message the root helper's
answer keeps beside it (hub_control.answer).
"""

import math
import re
import time

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class M(dict):
    """One message: {"id", "en", "args"}. A dict, so a finding that holds one goes to JSON as it is."""

    __slots__ = ()

    def __init__(self, mid, en, **args):
        super().__init__(id=mid, en=en)
        if args:
            self["args"] = args

    def __str__(self):
        # Put into an English string (a log line, code not converted yet), a message reads as its English.
        return render(self)


class Said(Exception):
    """A failure with the reason as a message: str() is its English, and .message the message itself, which an
    answer keeps for the page."""

    def __init__(self, message):
        super().__init__(render(message))
        self.message = message


class Refused(Said, ValueError):
    """A request refused, with the reason as a message: a ValueError, so the callers that catch one still do."""


def is_message(v):
    return isinstance(v, dict) and "id" in v and "en" in v


# A finding's title names what was checked, in one of three forms: a message of its own (a fixed finding:
# "Root's password"), or the thing's own name, alone or with its role: "kiwix.service (library)",
# "wlan0 (hotspot)". The name is data and never translated; the role is a message.
def named(name, role=None):
    if role is None:
        return M("title.named", "{name}", name=name)
    return M("title.named-role", "{name} ({role})", name=name, role=role)


def plural_en(n):
    return "one" if n == 1 else "other"


def listing(items):
    """English: "a", "a and b", "a, b and c" (as Intl.ListFormat("en-GB") joins them on the page)."""
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}" if items else ""


def when(epoch):
    """A moment as an argument: shown as people say it in the reader's language ("14 October at 06:29")."""
    return {"time": epoch}


# A length of time: said in the largest unit that holds it whole ("5 minutes", "36 hours", "90 seconds"), or else
# rounded in the unit its size suits ("67 minutes", "1.5 hours"). AW.say in admin-widgets.js picks the same.
UNITS = ((86400, "day", 172800, 1), (3600, "hour", 5400, 1), (60, "minute", 90, 0), (1, "second", 0, 0))


def sentences(*ms):
    """Several messages as one result, each a sentence or more, said one after the other (None left out): what an
    action did, step by step. A message itself, so a result or a refusal can be one."""
    return M("text.sentences", "{all}", all={"sentences": [m for m in ms if m]})


def duration(seconds):
    """A length of time as an argument: shown in the reader's language, in a unit that suits it ("5 minutes")."""
    return {"seconds": seconds}


def duration_unit(seconds):
    """(value, unit) for a length of time: the value rounded as it is said."""
    s = abs(seconds)
    for size, unit, _, _ in UNITS:
        if s >= size and s % size == 0 and s // size < 100:
            return int(s // size), unit
    size, unit, _, places = next(u for u in UNITS if s >= u[2])
    v = math.floor(s / size * 10**places + 0.5) / 10**places
    return (int(v) if v == int(v) else v), unit


def _arg(v):
    if is_message(v):
        return render(v)
    if isinstance(v, dict) and "time" in v:
        return time.strftime("%-d %B at %H:%M", time.localtime(v["time"]))
    if isinstance(v, dict) and "sentences" in v:
        return " ".join(t for t in map(_arg, v["sentences"]) if t)
    if isinstance(v, dict) and "seconds" in v:
        value, unit = duration_unit(v["seconds"])
        return f"{value:,} {unit}{'' if value == 1 else 's'}"
    if isinstance(v, (list, tuple)):
        return listing([_arg(x) for x in v])
    return str(v)


def render(m):
    """A message in English, for the terminal and the logs. A plain string (code not yet converted) as it is."""
    if m is None:
        return ""
    if not is_message(m):
        return _arg(m)
    args = m.get("args") or {}
    text = m["en"]
    if isinstance(text, dict):
        text = text.get(plural_en(args.get("n")), text["other"])
    return PLACEHOLDER_RE.sub(lambda mo: _arg(args[mo.group(1)]) if mo.group(1) in args else mo.group(0), text)


def held(m):
    """A message and every message in it (its arguments, the sentences of a result), outermost first: what a test
    reads (their IDs and arguments), so a change of wording needs no change of test."""
    if is_message(m):
        yield m
        for v in (m.get("args") or {}).values():
            yield from held(v)
    elif isinstance(m, dict) and "sentences" in m:
        for v in m["sentences"]:
            yield from held(v)
    elif isinstance(m, (list, tuple)):
        for v in m:
            yield from held(v)


def ids(m):
    """The IDs of a message and of the messages in it."""
    return [x["id"] for x in held(m)]


def args_of(m, mid):
    """The arguments of the message with this ID, in a message or in it; None if it is not there."""
    return next((x.get("args", {}) for x in held(m) if x["id"] == mid), None)


def text(*ms):
    """Several messages as one paragraph (each is a sentence or more); None and empty ones left out."""
    return " ".join(t for t in (render(m) for m in ms) if t)


# English-only helpers for terminal output and logs, and for code that is not converted yet. A message for
# a person takes its plural as a dict of whole sentences instead.
def count(n, one, other=None):
    """ "1 update", "3 updates"."""
    return f"{n} {one if n == 1 else other or one + 's'}"


def ago(seconds):
    """ "5 minutes ago", "3 days ago"."""
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            return f"{count(int(seconds // size), unit)} ago"
    return "just now"
