<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Network and the hotspot

*How the box stays on the network, and its own WiFi for guests. On the administration pages: System → Network.*

## Connection settings

**Connection settings** shows the WiFi networks that the box knows. Select **Add a network for the box to join** to add one. It also shows the wired port of the box.

## Network recovery

A watchdog checks the router every minute or two. If the link fails, the watchdog does recovery steps. It does a step only if the step before did not help:

1. **Reconnect** to the WiFi.
2. **Restart the network service.**
3. **Reset the radio.** The box reloads the driver of the WiFi chip, or uses another reset that the hardware supports.
4. **Reboot the box.** The box reboots at most three times a day. It does not reboot during an update or a build.

These settings control the watchdog:

- **Recovery timing (Pace)**: how soon the watchdog acts.
- **Maximum recovery action (Reach)**: the last step that the watchdog can do.
- **Sensitivity**: the number of missed checks that start recovery.
- **Guests on the hotspot**: whether the box protects the guests from a restart.

**Recovery actions** lets you turn off a step for one link. Use it for a radio that a restart makes unstable. The watchdog then goes from a reconnect directly to a radio reset. The Box doctor tells you when a step does not help on a link that fails often. It shows a button to leave the step out.

The watchdog also finds out why the link is down.

**The radio failed.** The driver is stalled or the WiFi interface is missing. Only a radio reset or a reboot helps. The setting **Failed radio** gives three choices:

- The failed radio waits its turn in the recovery steps.
- The box resets the radio at once.
- The box resets the radio at once. If the radio is not back after three minutes, the box restarts.

**The network is unavailable.** The network is out of range, the network does not let the box join, or the router does not answer. A reset or a reboot cannot help. The box only reconnects. Under **Network unavailable**, set **Retry interval**
(eager, steady, or sparing for a box on a battery) and **Retry period** (forever by default, or an hour, a day or a
week).

## Monitored interfaces

Each link (the WiFi, the cable or a USB adapter) can have a watchdog of its own. Set each link to **Monitored**, **Not monitored** or **As any other link**. Set **New interfaces and interfaces not listed here** to one of these:

- **Monitor default-route interface** (the default). The watchdog monitors the link that the box uses to reach its network.
- **Report all interfaces**. The watchdog monitors the link of the route. For every other link, it reports the problem and does nothing.
- **Monitor all interfaces**. Each link has its own watchdog.
- **Do not monitor**. The watchdog monitors only the links that you set above.

If no watchdog monitors a link and the link fails, nothing detects the failure and nothing retries. The Box doctor reports this.

The box identifies a link by its name and then by its hardware. A USB adapter can come back under another name. The adapter keeps its setting. The Box doctor reports this. When such a link is down, the Box doctor offers **Reconnect it**. NetworkManager stops its attempts to join a WiFi network after a few failed password checks. It then waits for a person to act.

## System-wide recovery actions

A restart of the network service drops every link. A reboot drops everything. By default, the box does these actions only when no path to the network remains. Example: a WiFi link fails and the cable works. The box reconnects the WiFi and resets its radio. It does not reboot the box.

You can let the box do these actions for the link of the route when another link is up. You can also let it do them for any link. The actions that you start yourself always run.

## Cable disconnected

When a wired link has no carrier, the cable is disconnected or the other end is off. No recovery action repairs this. By default, the box waits and shows a message. These settings are available:

- **Wait and report** (the default).
- **Reconnect periodically**, in case the port is stuck.
- **All recovery steps**: every recovery step up to the maximum recovery action.

When the cable returns without a link, the box reconnects at once. A box without a screen gets its cable back without a reboot.

**Wired-link recovery limit** sets how far the box goes on a cable. The default is a reconnect. If the box cannot read the carrier, it assumes that the cable is out. You can let the box also reset the network adapter (three minutes after the cable returns without a link, then at the recovery timing), restart the network service or reboot. The box never goes past the maximum recovery action.

The **Status** tab shows the link now and the last 72 hours. The chart of the Box doctor shows how far the watchdog went.

## Remote access with Tailscale

Tailscale is a network between your own devices over the internet. After setup, you can open the pages of the box from your phone or laptop in any place. Both devices need an internet connection. The box needs the internet for Tailscale only.

**Set it up (one time):**

1. Create a Tailscale account at tailscale.com.
2. Install Tailscale on the phone or laptop that you will use.
3. Sign in to Tailscale on that device.
4. On the box, open **Access → Remote access** and select **On**.
5. Open the box Terminal. Use its tile on the hub as an administrator, or use a shell over SSH.
6. Run `sudo tailscale up`. The command prints a link.
7. Open the link.
8. Sign in with the same Tailscale account. The box joins your Tailscale network.
9. Check **Remote access**. It shows "Tailscale is on" with the name and address of the box on your Tailscale network.

**Use it.** On your device, turn Tailscale on. Open `http://` and the Tailscale name or address of the box. The hub pages work as they do on the WiFi of the box. The rules for HTTPS and for administrator passwords also apply. Devices that connect to the box by USB are not available remotely.

**Switch it off** when you do not need it. Select **Remote access → Off**, or select **On for** a number of hours. Then it ends by itself. While it is off, you can switch it on again only from the network of the box.

If **Remote access** says "running, but not logged in to a Tailscale network", Tailscale on the box is not signed in. Repeat steps 5 to 8. Until you sign in, Tailscale cannot reach the box. Switch Remote access off if you do not use it.

## The hotspot

The hotspot is the WiFi network of the box. It is for people who have no other network. The first name is **Irate-Box**. The hotspot is off until you switch it on in Network → Hotspot. The page fits the hotspot to the radios of the box. When captive portal DNS is on, every web address on the hotspot leads to the hub.

- **Security**: **Open** (no password), **Enhanced Open** (no password, and the WiFi link is encrypted), **WPA3** (a password that you publish) or two networks, one open and one encrypted.
- **Hotspot firewall** (on the Security page): what guests can reach on the box. When the firewall is off, guests reach every service that listens on the hotspot, SSH included. When it is on, select **Hub and apps** or **Hub only**.
- **Guest internet access**: **Off** (the default), the web for signed-in users only, everyone after a welcome page (the web only, or everything), or everyone without a welcome page. A device has access for 12 hours at a time.
