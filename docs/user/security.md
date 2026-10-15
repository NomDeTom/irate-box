<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Security

*What the box protects, from whom, and the settings. On the administration pages: System → Security, and the Security doctor.*

The **Security** page lists what the box protects and what it does not protect. It holds the settings that you control. The settings are in groups: **What guests can reach**, **Sign-in and root**, and **The system**. A setting stays in its group when it is waiting and when it is set, so you can always change it back. The box does not open itself further without your approval.

Each setting has these states:

1. **Unsaved**: you selected a value. The box has not saved it.
2. **Saved**: you selected **Save**.

Each **Save** sends only the values selected on that page. A page tells you when a value selected on another page is still waiting.

- **HTTPS**: the box creates a certificate of its own. HTTPS encrypts the traffic between a browser and the box. Install the certificate on a device from `/certificate`. After you install it, the browser on that device trusts the box. You can also use a certificate of your own. Without HTTPS, other devices on the same WiFi can read what you type into a page.
- **Visitor data**: the box can count unique visitors. It uses salted hashes of addresses. It does not keep the addresses. A hash is not a count of unique people.
- **Listening on the network**: every port that the box answers on, what it is for, and whether it should be open.
- **Hotspot firewall** (in **What guests can reach**): what guests on the hotspot can reach (see [Network and the hotspot](network-and-hotspot.md)).

The **Security doctor** does not change your settings. A finding about a setting links to that setting on the Security page. The doctor does repairs that have one correct result and shows the result beside them. It runs checks from Debian and others (debian-cis, Lynis, the known issues of packages). It sorts the results into **Repaired**, **Suggestion**, **Not applicable** and **Accepted**. Accepting a risk does not repair it. You can import a scan from another system (OpenVAS, nmap).

For the administrator password, see [Setup](first-use.md) and [Accounts](accounts.md). Set the password over HTTPS or at the console. After the first account, the box refuses it over HTTP.
