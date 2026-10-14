<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The user lifecycle

This page describes the life of a box, from installation to removal, and who does what at each stage. Use it to decide where a new feature belongs, what its default should be, and which stage must explain it.

## Who uses the box

| Role | Who | How they reach the box |
|---|---|---|
| **Guest** | Anyone on the hotspot or on the box's network, with no account | The hub's pages, and the apps whose Access is Guests |
| **User** | A person with an account who is signed in | The same, and the apps whose Access is Users. A notebook of their own, if the box offers notebooks. |
| **Administrator** | A person with an administrator account | Everything above, the administration pages, the terminal and Syncthing |
| **Root at the console** | Whoever can open a root shell on the board, usually over SSH | The installer, `hub_control` console commands, recovery |

The administrator is usually the person who installed the box, but does not need to be.

## 1. Install

The person installing needs a board with Armbian, mPWRD-OS (an Armbian build) or Raspberry Pi OS, and a root shell on it. There are three ways in:

- **`install.sh`** from a clone of the repository, with options (`--with-notes`, `--with-mqtt`, `--zim FILE` and others). Running it again upgrades in place.
- **The Debian package**, then `sudo irate-box-setup` with the same options.
- **The offline kit**, for a box with no internet: made on any machine with `install.sh --make-offline-bundle DIR`, or by an existing box (Backup → Set up another box), then `sudo ./setup.sh` on the new box.

After installation the code is in `/opt/irate-box`, the state in `/var/lib/hub`, the configuration in `/etc/hub`. nginx serves the hub on port 80 (Caddy if asked for, or if it was already there).

**State of a new box.** Restrictions are few, so that setup is easy:

| Setting | On a new box |
|---|---|
| Administrator account | None. The box is in first use (stage 2). |
| Hotspot firewall | Off. Devices on the hotspot can reach every service on the box. |
| Guest internet access | Off. Devices on the hotspot reach the box and nothing beyond it. |
| Account registration | Off. Only administrators can sign in. |
| HTTPS | The box makes its own certificate authority. Each device installs it once from `/certificate`. |
| SSH, Tailscale, Cockpit and other services of the OS image | As found. The box does not change them until an administrator decides. |

## 2. First use: the administrator account

Until an administrator account exists, **anyone on the network can open the administration pages**. The hub's front page shows this warning, and `/admin` sends every visitor to `/admin/setup`.

The first administrator account is created in one of these ways:

- On `/admin/setup` over HTTPS.
- At the console: `sudo /opt/irate-box/irate-box hub_control set-admin NAME`. The password never crosses the network.
- On `/admin/setup` over plain HTTP, after the person confirms that other devices can read the password. This is allowed for the first password only. After that, an administrator password changes only over HTTPS or at the console.

Creating the account ends first use. The administrator then signs in where everyone signs in.

## 3. The setup tour

The administration pages then guide the administrator through the setup steps (`web/admin-tour.js`). Each step opens the page where its setting lives and highlights it. The administrator keeps the default ("Keep this, next") or changes the setting there. The box runs on the defaults until each step is done, and the Overview counts the steps left.

The steps, in order:

1. **Administrator password**: set at first use, changeable here.
2. **How guests reach the box**: through the network that the box is on, or through its own hotspot.
3. **What the box exposes**: what a device on the network can reach, and the settings that change it.
4. **Add-ons**: the extras this box needs.
5. **Books**: the offline library.
6. **The security decisions**, each with its default stated: visitor counts, signed-in name visibility, account registration, locked tiles, HTTPS, hotspot security, guest internet access, SSH password sign-in, remote access (Tailscale), Cockpit, the terminal. Steps for things the image does not have (Tailscale, Cockpit) show only where they exist.
7. **Take a backup**, and keep the file off the box.

A new setting that changes what guests or users can reach, or that weakens the box's security, must be a step here, with its default stated. The hotspot firewall is not yet a step of its own. It shows only among the findings of step 3.

## 4. Everyday use

**Guests** join the hotspot or the box's network. On the hotspot, the captive portal opens the hub; on other networks they open the box's address. They use the apps open to guests, read the library, post on the shoutbox and the board, and report content.

**Users** sign in. How they get an account depends on the registration setting:

| Registration | How an account is made |
|---|---|
| Off | No user accounts. Only administrators sign in. Accounts made earlier cannot sign in until registration is on again. |
| Open | Anyone on the network creates one. |
| Apply | Anyone asks. An administrator accepts or refuses. |
| Assigned | An administrator creates it and gives a one-time code. The user sets the password with the code. |

Signed in, a user reaches the apps for users, keeps saved work across devices, and has a notebook if the box offers them.

**Administrators** decide who reaches each app (Access and Visibility), moderate reported content, manage accounts, add or remove add-ons, books, toolkits and firmware, and change the box's appearance.

## 5. Keeping the box running

Most of this happens without the administrator:

- **Network recovery** watches each link and repairs it, one step at a time, up to a reboot if the settings allow it.
- **Crash watch** records what happened before a freeze or a crash.
- **The status light** shows the box's state without a screen.
- **Updates** are checked, downloaded and verified on a schedule. They install automatically or when the administrator selects Install, as set. Package watch and Debian security updates work the same way.

The administrator looks in when something is wrong:

- **The Box doctor, the Security doctor and the Update doctor** each list problems, with a repair where one is safe.
- **The Overview** shows memory, disk, services and their history.
- **Backups**: settings only, settings and data, or a full image of the card.

## 6. Recovery

| Problem | What to do |
|---|---|
| Lost administrator password | At the console: `sudo /opt/irate-box/irate-box hub_control set-admin NAME`. This sets a new password, for the same name or a new administrator. |
| No way into the administration pages at all | At the console: `sudo /opt/irate-box/irate-box hub_control reset-password`. The box returns to first use (stage 2). |
| The box lost its network | Network recovery acts by itself. If it cannot, the Box doctor says why. |
| An update failed | The Update doctor says why. Running `install.sh` again repairs an installation. |

## 7. Moving to another box

- **Set up another box** (Backup page) makes an offline kit from this box: its code, apps, the release files it keeps and, if wanted, its books.
- **Content export** makes an archive of the settings and data. On the other box, `import.sh` from the archive brings them in. It asks before it replaces anything.

## 8. Removing irate-box

`sudo ./uninstall.sh` removes the services, code, apps, configuration, user data and the hub user. Take a backup first.

- `--keep-state` keeps the user data in `/var/lib/hub`.
- `--purge-packages` also removes the packages the installer added.

The box can differ from its state before the install: packages, joined networks and netplan settings can change.
