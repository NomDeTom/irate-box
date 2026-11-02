# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The six kinds of security change in an update, the three levels, and the proposed defaults.

Shared by root (root/secchanges.py, which keeps the owner's choice) and the hub (the Updates
page, which shows it). Stdlib only.
"""

TOGGLES = ("root_actions", "secrets", "sandboxes", "served", "outbound", "root_code")
LEVELS = ("flag", "authorise", "console")
DEFAULTS = {
    "root_actions": "authorise",
    "secrets": "authorise",
    "sandboxes": "authorise",
    "served": "authorise",
    "outbound": "flag",
    "root_code": "flag",
}
