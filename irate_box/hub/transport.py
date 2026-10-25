# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Which requests for root an administrator may make only over HTTPS. A session cookie sent over plain HTTP can be
copied by anyone listening on an open hotspot; with it, these would let them choose the code root installs, the
security settings, or what the web server exposes. Read by the hub (server.py, before it queues a request) and by
root (hub_control.py, before it acts on one), so a route the hub forgets is still refused.

A request for root says how it came (`over`):
  https     through the web server, over HTTPS
  http      through the web server, over plain HTTP
  loopback  straight to the hub with no web server in front (the hub run bare, in development)
  hub       not from a request at all: the hub's own work
Stdlib only."""

# root's POLICY_ACTIONS (hub_control.py) except two: admin-gate, which takes nothing from the request (it writes the
# gates from the accounts as they are), and share-allow, which a guest's agreement on the hotspot's sheet asks for
# (over plain HTTP, as every guest page), within the level the administrator chose. Plus installing an update and
# adding or removing an add-on, which run code as root. tests/api_https_gate.py checks the rest are all here.
HTTPS_ONLY = frozenset(
    (
        "security-policy",
        "security-fix",
        "update-signing",
        "update-force-install",
        "update-set-source",
        "update-install",
        "tls-make",
        "tls-switch",
        "tls-import",
        "tls-box",
        "tls-admin-only",
        "tls-settings",
        "access",
        "admin-login",
        "share-set",
        "addon",
    )
)
TRUSTED = ("https", "loopback", "hub")


def allowed(action, over):
    """Whether root may act on `action` from a request that came `over` this way."""
    return action not in HTTPS_ONLY or over in TRUSTED
