# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Irate-Box. hub/ runs as the hub's user (and its watchdogs); library/ fetches and checks what
the hub serves; root/ is the root helper and the doctors, readable by root only on an install.
Nothing in hub/ or library/ imports from root/. Run a module as `python3 -m irate_box.hub.server`
from the checkout, or through ./irate-box."""
