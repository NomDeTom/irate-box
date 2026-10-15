# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A refusal or a failure fit to show the person, as a message (irate_box/msg.py): its English is the
exception's text, so a log line, the terminal and a test that reads str(exc) see what they did before; the
message itself (its ID and arguments) goes to the page, which says it in the reader's language.

    raise Refused(M("hotspot.ssid.long", "The hotspot name is longer than 32 bytes."))

The hub answers one as {"error": English, "message": message} (answer); accounts.AccountError and the
librarian's LibrarianError are of the same kind. Stdlib only.
"""

from irate_box.msg import Refused, Said, is_message, render  # noqa: F401 (Refused and Said for the hub's modules)


def answer(exc):
    """What the pages read for a failure: error, its English, as every page expects; message, the message
    itself, when the failure has one (a ValueError from Python's own parsing has none)."""
    m = getattr(exc, "message", None)
    return {"error": str(exc), "message": m} if is_message(m) else {"error": str(exc)}


def say(m):
    """A refusal made in place, as the pages read it."""
    return {"error": render(m), "message": m}
