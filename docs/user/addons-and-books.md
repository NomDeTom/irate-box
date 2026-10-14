<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Add-ons and books

*Extra services and the offline library. On the administration pages: the page of each add-on (**Add** at the top, **Remove** under Maintenance), or All apps → Add-ons. For books: Kiwix → Books.*

## Add-ons

Add or remove each add-on on its own. Use the administration pages (the same installer runs, and the hub restarts during the install) or the options of the installer.

- **Kiwix** (`--with-kiwix`): the offline library at `/wiki/`.
- **Notes** (`--with-notes`): shared notes at `/notes/`. Everyone who has access can change them. If an administrator lets users have one, each signed-in user also has a private notebook. See [Accounts](accounts.md).
- **Syncthing** (`--with-sync`): file sync. Administrators sign in to use it.
- **MQTT broker** (`--with-mqtt`): for Meshtastic nodes and phones.
- **IRC** (`--with-irc`): a small chat server for any IRC app.
- **Terminal** (`--with-term`): a shell in the browser. Administrators sign in to use it.
- **Live collaboration** (`--with-collab`): drawing together in Excalidraw.

To remove an add-on, use `--remove NAME` or select **Remove**. The box stops the service and removes its settings. The data of the add-on stays on the box.

**Web add-ons** are small pages that you add from a catalogue. They need no installer. Examples are ELIZA and an MQTT explorer. The hub serves each web add-on from its own web origin, separate from the hub.

## Books

Kiwix reads books in the ZIM format. Examples are Wikipedia, medical references and guides. If Kiwix is installed, this guide is one of the books.

You can add books from these sources:

- **Suggested for an offline box**
- a search of the Kiwix catalogue
- another source (a URL or GitHub)
- a USB drive (select **Scan for USB drives**)

Each book follows an update schedule like the box does. Set **How often to look**. Set **When something newer is found**: **Flag** it, **Download** it or **Install** it. A book that you track is not the same as a book that you copied from a drive.
