<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Add-ons and books

*What more the box can do, and its offline library. On the admin pages: All apps → Add-ons, and Kiwix → Books.*

## Add-ons

Each is added or removed on its own, from the admin pages (the same installer runs; the hub restarts part-way) or with the installer's options:

- **Kiwix** (`--with-kiwix`): the offline library at `/wiki/`.
- **Notes** (`--with-notes`): a shared notebook at `/notes/`.
- **Syncthing** (`--with-sync`): file sync, behind the admin login.
- **MQTT broker** (`--with-mqtt`): for Meshtastic nodes and phones.
- **IRC** (`--with-irc`): a small chat server for any IRC app.
- **Terminal** (`--with-term`): a shell in the browser, behind the admin login.
- **Live collaboration** (`--with-collab`): drawing together in Excalidraw.

Removing one (`--remove NAME`, or **Remove**) stops it and takes its settings away; its data stays.

**Web add-ons** are small pages added from a catalogue with no installer (ELIZA, an MQTT explorer), served apart from the hub.

## Books

Kiwix reads books in the ZIM format: Wikipedia, medical references, guides. Where Kiwix is added, this guide is one of them.

Add books from **Suggested for an offline box**, a search of Kiwix's catalogue, another source (a URL, GitHub), or a USB stick (**Scan for USB sticks**). Each book's updates follow the same pattern as the box's: how often to look, and whether to flag, fetch or install what's newer.
