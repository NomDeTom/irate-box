<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Security

*What is and isn't protected, and the choices. On the admin pages: System → Security, and the Security doctor.*

The **Security** page says plainly what's protected and what isn't, and asks for the choices that are yours. Nothing that opens the box further is done without your yes.

- **HTTPS**: the box makes its own certificate; install it on your devices from `/certificate` and the padlock appears. Or use a certificate of your own. Without HTTPS, anything typed into a page can be read by anyone on the same WiFi.
- **What the box keeps about visitors**: whether it counts unique visitors (by a salted fingerprint, never kept as is).
- **Listening on the network**: every port the box answers on, what it is, and whether it should.
- **The hotspot's floor**: what guests on the hotspot reach (see [Network and the hotspot](network-and-hotspot.md)).

The **Security doctor** runs Debian's and others' checks (debian-cis, Lynis, the packages' known issues) and sorts what they find: **put right here**, **hardening suggestions**, **not for this box**, and what **you've accepted**. You can import a scan made elsewhere (OpenVAS, nmap).

The admin password: see [Setting up a new box](first-use.md) and [Accounts](accounts.md). It's set over HTTPS or at the console, never over plain HTTP after the first time.
