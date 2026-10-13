<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# When something goes wrong

*The doctors, the logs and the console.*

## The doctors

On the admin pages, under Doctors:

- **Box doctor**: is everything the installer set up still there and working: the services, the web server's configuration, the clock, the space, the network watchdog, crashes. Each problem says what to do; the safe repairs are buttons. **Look again** runs it now. At its top, **the box at a glance**: the last start of the box and why, the last repair, what restarts a frozen box, and how much it writes to its card. What crash watch keeps and what restarts a frozen box are chosen on **System → Recovery and monitoring**; a doctor shows what it found and links to where a choice is made, and does the repairs with one right answer itself. Below the findings, folded: the root helper's queue, each outage and the steps the network watchdog took, and the last install (open by itself when it went wrong).
- **Security doctor**: see [Security](security.md).
- **Updates doctor**: why an update didn't verify or install, with a link to where it is put right. Clearing the update cache and **Install anyway** (for a version that fetched but failed its checks, never one that isn't signed as you required) are under **Updates → Maintenance**.

The same Box doctor from the console, even when the admin pages can't be reached:

```
sudo /opt/irate-box/irate-box health summary
```

## The console

Over SSH, on the box:

- `sudo /opt/irate-box/irate-box hub_control set-admin NAME`: make an admin, or set a lost password.
- `sudo /opt/irate-box/irate-box hub_control script-login`: a login for scripts (`--off` removes it).
- `sudo /opt/irate-box/irate-box hub_control reset-password`: back to the first use (the admin pages free until an admin is made again).
- Running the installer again (`sudo ./install.sh`) is safe: it repairs and upgrades, keeping your settings and add-ons.
- `sudo ./uninstall.sh` removes it; your data goes too unless you add `--keep-state`, so take a backup first.

## Logs

- The installer: `/var/log/irate-box/install.log`
- A service: `journalctl -u irate-box -n 50` (the hub), `journalctl -u irate-box-uplink -n 50` (the network watchdog), `journalctl -u irate-box-led -n 50` (the status light)

## If the box stops answering

Its crash watch keeps notes on the card, and the Box doctor says after a restart when the box stopped without shutting down, with its last record.

## The status light

The box can blink one of the board's LEDs so you can tell from across the room how it is: **1 s on, 1 s off** while all
it reports is well; **off** when not (the network is down, or the hub isn't running); **quick flashes** while the network
is being repaired; **two short flashes** when the Box doctor has a problem to read; **steady on** while the box starts or
updates. A light stuck on or off, not changing, is a box that has frozen.

**Recovery and monitoring → The status light** chooses what it reports: **Leave it** (the light stays as the image set
it), **The box is alive**, **Alive and on its network**, or **Alive, on its network, nothing wrong**. Where the board has
more than one, **Which light** chooses it, and the Box doctor's **Flash** buttons blink each one fast to find it. A power
LED is never used. On a Raspberry Pi the green light is left showing the SD card working until you choose otherwise.

## The card

An SD card wears with every write. The box writes a file only when it changed, keeps its live reports in memory, and adds to its histories rather than rewriting them. **Recovery and monitoring → Writing to the card** chooses how much more it may lose in a crash to write less: **Record everything**, **Thrifty** (the default: the clock saved every 10 minutes and when the hub stops), **Gentle** (crash watch's snapshots without a sync) or **Card saver** (snapshots kept in memory, written every quarter of an hour or at once when something goes wrong). What people made is written as it changes at every stop. The page shows how much has been written since boot.
