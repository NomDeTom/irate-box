<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Troubleshooting

*The doctors, the logs and the console.*

## The doctors

On the administration pages, open Doctors.

**Box doctor.** It checks that everything the installer set up is still present and working. It checks the services, the web server configuration, the clock, the storage space, the network watchdog and crashes. Its page has these parts:

- **Summary**: the last start of the box and the reason for it, the last repair, what restarts a frozen box and how much the box writes to its storage card.
- **Findings**: each problem and what to do about it.
- **Repairs**: buttons for the safe repairs. Select **Look again** to run the checks now.
- **Settings links**: a finding links to the page where you change the setting. You set what crash watch keeps and what restarts a frozen box in **System → Recovery and monitoring**. The doctor does the repairs that have one correct result.
- **History** (folded): the queue of the root helper, each outage, the steps that the network watchdog took, and the last install. The last install opens by itself when it failed.

The clock checks have their own page in the Clock section.

**Security doctor.** See [Security](security.md).

**Updates doctor.** It shows why an update did not verify or install. It links to the page where you can correct the problem. **Install anyway** is for a version that downloaded but failed its checks. It does not install a version that does not have the signature that you require. The update cache and **Install anyway** are in **Updates → Maintenance**.

To run the Box doctor from the console, also when the administration pages are not available, run this command:

```
sudo /opt/irate-box/irate-box health summary
```

## The console

Use SSH to sign in to the box. These commands are available:

- `sudo /opt/irate-box/irate-box hub_control set-admin NAME`: create an administrator account, or set a new password.
- `sudo /opt/irate-box/irate-box hub_control script-login`: create a script login (the `--off` option removes it).
- `sudo /opt/irate-box/irate-box hub_control reset-password`: reset the initial setup. After this command, anyone on the network can open the administration pages until you create an administrator account again. Run it only if you accept this.
- `sudo ./install.sh`: run the installer again. The installer repairs and upgrades the box. It keeps your settings and add-ons. It can restart services. Read the log in `/var/log/irate-box/install.log` afterward.
- `sudo ./uninstall.sh`: remove the box. This also deletes your data unless you add `--keep-state`. Make a backup first.

## Logs

- The installer: `/var/log/irate-box/install.log`
- The hub: `journalctl -u irate-box -n 50`
- The network watchdog: `journalctl -u irate-box-uplink -n 50`
- The status LED: `journalctl -u irate-box-led -n 50`

## If the box stops answering

Crash watch keeps crash evidence on the storage card. After a restart, the Box doctor tells you if the box stopped without a shutdown. It shows the last record.

## The status LED

The box can blink one LED of the board. The LED shows the state of the box from across the room. In the mode **Alive, on its network, nothing wrong**, the patterns are:

- **1 second on, 1 second off**: all the items that the box reports are well.
- **Off**: the network is down or the hub is not running.
- **Quick flashes**: the network is being repaired.
- **Two short flashes**: the Box doctor has a problem to read, or another link that the watchdog monitors is down.
- **Steady on**: the box is starting or updating.

A steady or off LED can mean that the box froze. It can also mean that you selected a mode that does not report that state, that reporting is disabled, or that the LED has a hardware fault.

Select the mode in **Recovery and monitoring → The status light**:

- **Leave it**: the LED stays as the image set it.
- **The box is alive**
- **Alive and on its network**
- **Alive, on its network, nothing wrong**

If the board has more than one LED, **Which light** selects the LED. The **Flash** buttons of the Box doctor make each LED blink fast, so you can identify it. The box never uses a power LED. On a Raspberry Pi, the green LED shows the activity of the SD card until you select another mode.

## SD card write frequency

An SD card wears with each write. The box writes a file only when it changed. It keeps its live reports in memory. It adds to its histories and does not rewrite them.

Select the write level in **Recovery and monitoring → SD card writes**. A lower level writes less and can lose more after a crash:

- **Frequent writes**: crash watch snapshots are written and synced every 30 seconds. The clock is saved every minute.
- **Reduced clock writes** (the default): the snapshots are the same. The clock is saved every 10 minutes and when the hub stops. After a crash, the clock can be up to 10 minutes behind.
- **Buffered writes**: the snapshots are written without a sync. A hang loses the last two minutes of snapshots. The clock is saved every 30 minutes.
- **In-memory snapshots**: the snapshots are kept in memory. The box writes them every 15 minutes, or at once when crash watch sees a problem. The clock is saved every hour. A hang loses up to 15 minutes of snapshots.

User data is written when it changes, at every level. The page shows how much the box has written since the last boot.
