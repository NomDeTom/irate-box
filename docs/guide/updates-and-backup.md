<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Updates and backup

*Keeping the box current, and a copy of what's on it. On the admin pages: System → Updates and backup.*

## The box's own updates

The **This hub** card checks for a newer version, fetches it, checks it, and installs it. For each, you choose:

- **How often to look**: by hand, every 6 or 24 hours, or weekly.
- **When something newer is found**: **Flag** it (said on the page and its badge), also **Fetch** it (downloaded and checked, ready for you), or also **Install** it, only between the hours you set and with nobody on the hub (it restarts).

Each update downloads only what changed. **What an update must carry** sets how much it must prove: **From the branch** (as it comes, its checks passed), **Merged on GitHub** (every new commit signed by GitHub), or **Signed releases only** (the newest release tag, signed with one of your own SSH keys). A version that fails is never installed; the Updates doctor says why.

## Debian's security updates

The same choices for the operating system's security fixes: list them, also download them, or also install them (from Debian's security archive only).

## Packages from their makers

Programs the box watches for its makers' updates (meshtasticd, for one), with **Roll back** to the version before.

## Backup

**Back up this box** makes a file to download:

- **Settings only**: the hub's settings, accounts, the apps' and add-ons' settings, the library's and mirrors' sources, mesh channels.
- **Settings and data**: also notes, saved work, the board and shoutbox, dropped files, the box's own git repositories.
- **Full image**: the whole card, written to a USB stick in parts.

Books, firmware, mirrors, builds and caches are never in a backup: they're fetched again. Syncthing's private keys go in only if you tick it; whoever holds that file can pose as the box, so keep it like a password.

**Content export** makes a set for another box (books, toolkits, repositories, and this box's settings if you want), to download or write to a USB stick: to bring a box level, or start a new one.
