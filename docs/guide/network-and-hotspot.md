<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Network and the hotspot

*How the box stays online, and its own WiFi for guests. On the admin pages: System → Network.*

## The box's access

**The box's access** shows its WiFi (the networks it knows; **Add a network for the box to join**) and its wired port.

## Staying on the network

A watchdog checks the router every minute or two. If the link drops, it climbs a ladder of repairs, each only if the one before didn't help:

1. **Reconnect** to the WiFi.
2. **Restart the network service.**
3. **Reset the radio** (reload its driver: a fresh start for the WiFi chip).
4. **Reboot the box** (at most three times a day, never during an update or a build).

You choose the **pace** (how soon it acts), the **reach** (how far up the ladder it may go), how many missed checks put it on the ladder, and, with guests on the hotspot, whether to protect them from a restart. **Steps it may take, for each connection** lets you untick a step for one link: a radio that a restart unsettles can go from a reconnect straight to a radio reset. The Box doctor says when a step isn't helping on a link that drops often, with a button to leave it out.

The **Status** tab shows the link now and the last 72 hours; the Box doctor's chart shows how far the watchdog went.

## The hotspot

The box's own WiFi, for people with no other network (name **Irate-Box** to start). Every web address on it leads to the hub. It's off until you switch it on (Network → Hotspot); the page fits it to the box's radios.

- **Security**: **Open** (no password), **Enhanced Open** (no password, but encrypted), **WPA3** with a password you publish, or two networks, one open and one encrypted.
- **What guests reach on the box**: everything, the hub and its apps, or the hub only (Security page).
- **Guests reach the internet**: **Off** (the default), signed-in users for the web only, everyone after a welcome page (the web, or everything), or everyone with no welcome page. A device is let out for 12 hours at a time.
