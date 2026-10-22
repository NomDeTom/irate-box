# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Run as root only: the control helper, the security switches and the three doctors
(health.py: Box, secdoctor.py: Security, hub_control.py: Updates; see docs/dev/doctors.md).
install.sh makes this folder readable by root alone; nothing outside it imports from it."""
