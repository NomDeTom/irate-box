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

It also tells **why** the link is down. When the **radio itself has failed** (its driver stuck, or the WiFi interface gone), only a radio reset or a reboot helps: **A failed radio** sets whether that waits its turn on the ladder, happens at once, or happens at once with a restart of the box if the radio isn't back three minutes later. When the **network is at fault** (out of range, not letting the box join, or the router silent), a reset or a reboot can't help, so the box only reconnects: **how often** (eager, steady, or sparing for a box on a battery) and **how long** (forever by default, or an hour, a day, a week).

**Which links it watches.** Each link (the WiFi, the cable, a USB adapter) can have a watchdog of its own. Set each one to
**Watched**, **Not watched**, or **As any other link**, and choose what any other link does: watched **while it holds the
route** (the default: the link the box reaches its network by), **looked at** (the route's link watched, every other one
looked at and its trouble said, nothing done for it), **watched**, or **not watched**. A link nobody watches is on its own:
if it fails, nothing notices or tries again. A link is known by its name, then by its hardware: a USB adapter that comes
back under another name (plugged into another port, or after another one) keeps its setting. The Box doctor says so, and when such a link is down it offers
**Reconnect it** (NetworkManager stops trying a WiFi network after its password check fails a few times, and waits for
someone).

**Steps that reach every link.** Restarting the network service drops every link, and a reboot drops everything, so by
default they are taken **only when no path to the network is left**: a WiFi that fails on a box whose cable works is reconnected and its
radio reset, but the box isn't rebooted for it. You can allow them **for the route's link** even with another up, or **for
any link**. A step you press yourself is always yours to take.

**A cable unplugged.** No carrier on a wired link means no cable, or the other end is off, and nothing the box does mends
that: by default it **waits, and says so**. It can instead **reconnect now and then**, or take **the whole ladder**. It
watches for the cable coming back: if the link doesn't come up with it, the box reconnects at once, so a box with no
screen gets its cable back without a reboot.

**How far it goes on a cable by itself** is reconnecting, by default: a lost wired link is most often a cable pulled or
the other end off, which no step mends, and when the carrier can't be read the box assumes the cable is out. Raise it to
let the box also reset the network adapter (three minutes after a cable comes back without a link, then as the pace
says), restart the network service, or reboot; it never goes past the reach above.

The **Status** tab shows the link now and the last 72 hours; the Box doctor's chart shows how far the watchdog went.

## Reaching the box from anywhere (Tailscale)

Tailscale is a private network between your own devices, over the internet. With it, you can open the box's pages from
your phone or laptop wherever you are, as if you were on its WiFi. The box needs the internet for this; nothing else on
the box does.

**Set it up** (once):

1. Make a Tailscale account at tailscale.com, and install Tailscale on the phone or laptop you will use. Sign in there.
2. On the box: **Admin → Access → Remote access**, choose **On**.
3. Open the box's **Terminal** (its tile on the hub page, as the admin; or a shell over SSH) and run `sudo tailscale up`. It prints a link.
4. Open that link and sign in with the same Tailscale account. The box joins your Tailscale network.
5. **Remote access** now says "Tailscale is on: this box is …", with its name and address on your Tailscale network.

**Use it:** on your device, with Tailscale on, open `http://` and the box's Tailscale name or address. Everything works
as on the box's WiFi, the admin pages included.

**Switch it off** when you don't need it: **Remote access → Off**, or **On for** a few hours so it ends by itself. While
it is off, it can only be switched on again from the box's own network.

If Remote access says "running, but not logged in to a Tailscale network", step 3 hasn't been done: nothing can reach
the box through Tailscale until it is. Switch it off if you don't use it.

## The hotspot

The box's own WiFi, for people with no other network (name **Irate-Box** to start). Every web address on it leads to the hub. It's off until you switch it on (Network → Hotspot); the page fits it to the box's radios.

- **Security**: **Open** (no password), **Enhanced Open** (no password, but encrypted), **WPA3** with a password you publish, or two networks, one open and one encrypted.
- **What guests reach on the box**: everything, the hub and its apps, or the hub only (Security page).
- **Guests reach the internet**: **Off** (the default), signed-in users for the web only, everyone after a welcome page (the web, or everything), or everyone with no welcome page. A device is let out for 12 hours at a time.
