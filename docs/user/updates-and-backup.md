<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Updates and backup

*Update the box and make a backup copy of its data. On the administration pages: System → Updates, and System → Backup and transfer.*

## The box's own updates

The **This hub** card says what is installed and what is newer, with **Check now**, **Download** and **Install** first. Below them, folded, **Automatic updates** says in one line what the box does by itself; open it to select a setting:

- **How often to look**: by hand, every 6 or 24 hours, or weekly.
- **When something newer is found**: select **Flag**, **Download** or **Install**.
  - **Flag** shows the new version on the page and on its badge.
  - **Download** also downloads the new version and checks it. The version is then ready for you.
  - **Install** also installs the new version. This happens only in the hours that you set, and only when nobody is on the hub. The hub restarts.

Each update downloads only the files that changed. Some dependencies can download in full. **Update verification** sets what the box must verify before it installs:

- **Branch updates**: the update as it comes, with its checks passed.
- **Merged on GitHub**: every new commit has a GitHub signature.
- **Signed releases only**: the newest release tag has a signature from one of your own SSH keys.

A GitHub signature does not prove who merged the commit. A version that fails the required signature check is not installed. Some other failed checks can be overridden with **Install anyway**. The Updates doctor says why a check failed.

## Debian's security updates

The page first shows the waiting updates and three buttons. The folded **Automatic updates** section has the same settings for the security fixes of the operating system. The box can list the fixes, download them, or install them. It uses the Debian security archive only.

The box keeps a small copy of the packages it boots and serves with, at the version that is installed, so a damaged file can be put back without the internet. After each update it removes the other downloaded `.deb` files. The copy is a few tens of MB. If a package's version is no longer on Debian's mirror, only the copy can reinstall it.

## Packages from their makers

The box watches some programs for updates from their publishers. An example is meshtasticd. **Roll back** returns to the version before. The release channel and the automatic update policy of each card are folded. A line on the card says what is saved. If a step fails, a link next to it opens the Updates doctor.

## Backup

**Back up this box** makes a backup at one of three levels. The first two levels make a file to download.

- **Settings archive**: the settings of the hub, the accounts, the settings of apps and add-ons, the sources of the library and the mirrors, and the mesh channels.
- **Settings and shared-data archive**: also the shared notes, saved work, the board and shoutbox, dropped files, the box's own git repositories. The private notebooks of users are not included. Each user keeps a copy with the **My notebook** download.
- **Full storage image**: a copy of the whole card. The box writes it to a USB drive in parts. It does not make a file to download. The image contains everything on the card, including books, firmware and private data. The box keeps running while it writes the image, so the parts can differ in time.

The first two levels leave out books, firmware, mirrors, builds and caches. The box downloads them again. The box includes the private keys of Syncthing only if you select them. The backup file also holds account and mesh channel secrets. A person who has the file can pose as the box. Keep the file as secret as a password.

**Content export** makes a set of content to move to another box. It holds books, toolkits and repositories. It holds the settings of this box if you select them. You can download the set or write it to a USB drive. Use it to transfer settings and content to a new box. A content export is not a full backup.
