<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Updates and backup

*Keeping the box current, and a copy of what's on it. On the admin pages: System → Updates, and System → Backup and transfer.*

## The box's own updates

The **This hub** card says what is installed and what is newer, with **Check now**, **Fetch** and **Install** first. Below them, folded, **Automatic updates** says in one line what the box does by itself; open it to choose:

- **How often to look**: by hand, every 6 or 24 hours, or weekly.
- **When something newer is found**: **Flag** it (said on the page and its badge), also **Fetch** it (downloaded and checked, ready for you), or also **Install** it, only between the hours you set and with nobody on the hub (it restarts).

Each update downloads only what changed. **What an update must carry** sets how much it must prove: **From the branch** (as it comes, its checks passed), **Merged on GitHub** (every new commit signed by GitHub), or **Signed releases only** (the newest release tag, signed with one of your own SSH keys). A version that fails is never installed; the Updates doctor says why.

## Debian's security updates

What is waiting and its three buttons first; the same **Automatic updates** choices, folded, for the operating system's security fixes: list them, also download them, or also install them (from Debian's security archive only).

## Packages from their makers

Programs the box watches for its makers' updates (meshtasticd, for one), with **Roll back** to the version before. Each card's channel and automatic updates are folded under one line saying what is saved. When a step fails, a link beside it leads to the Updates doctor.

## Backup

**Back up this box** makes a backup at one of three levels. The first two levels make a file to download:

- **Settings only**: the hub's settings, accounts, the apps' and add-ons' settings, the library's and mirrors' sources, mesh channels.
- **Settings and data**: also the shared notes, saved work, the board and shoutbox, dropped files, the box's own git repositories. People's own notebooks are not included: each keeps theirs with **My notebook**'s download.
- **Full image**: a copy of the whole card, written to a USB stick in parts. The image contains everything on the card, including books, firmware and private data. The box keeps running while it writes the image, so the files can differ in time.

The first two levels leave out books, firmware, mirrors, builds and caches. The box downloads them again. Syncthing's private keys are included only if you select them. A person who holds that file can pose as the box. Keep the file as you keep a password.

**Content export** makes a set for another box (books, toolkits, repositories, and this box's settings if you want), to download or write to a USB stick: to bring a box level, or start a new one.
