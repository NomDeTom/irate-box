<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Roadmap

What the box does now, and what is coming. Each feature has a status on this scale:

| Status | Means |
|---|---|
| **idea** | Named. Nothing is written about how. |
| **concept** | The options are written down. No design is chosen. |
| **plan** | A design is chosen and written down, ready to build. |
| **placeholder** | Something shows in the box (a tile, a page, a setting) but does not work yet. |
| **MVP** | Works for the main case. Known gaps and rough edges. |
| **basic** | Works for normal use and has tests. Some parts are still missing. |
| **full** | Complete for what it sets out to do, tested and documented. |

A feature moves up one step at a time. Update its row in the same change that moves it.

## Features now in the box

### The hub and its pages

| Feature | Status | Notes |
|---|---|---|
| Hub home page: app tiles, folders, status tiles, Join QR code, certificate tile | basic | |
| Accounts: sign-in, sign-up modes, one-time codes, administrators and users, script sign-in | basic | |
| Access and visibility for each app (Guests, Users, Administrators, Disabled) | basic | |
| Appearance: name, subtitle, accent colour, favicon, header picture, tile layout | basic | Missing: one default theme for everyone, a background picture, the first-use and sign-in page text, languages and units. |
| Themes and the theme picker (box palette, standard, increased contrast, E-ink, Cybercore) | basic | |
| Shoutbox and board, with reports and moderation | basic | |
| File drop | basic | |
| Device locks for saved items | basic | |
| Saved work across devices | basic | |
| User guide on the box, as pages and as a Kiwix book | basic | The guide still names some labels that have changed. |

### Apps

| Feature | Status | Notes |
|---|---|---|
| Whiteboard (Excalidraw), with live collaboration | basic | Collaboration needs the relay and an ARMv7 or newer board. |
| Diagram editor (Mermaid) | basic | |
| Offline library (Kiwix), with books from Kiwix, your own source or a USB drive | basic | |
| Shared notes (SilverBullet) | basic | |
| A notebook for each signed-in user | basic | Not encrypted. Backup choices are open (see the pipeline). |
| Serial terminal, ESP32 web flasher and FreeOCD | basic | Web Serial needs HTTPS. |
| Firmware Factory: Meshtastic builds on the box, published to the flasher | MVP | ESP32 builds on 32-bit ARM boards are not solved. |
| Git hosting (cgit), public and private areas, mirrors, build runs | basic | |
| Calculators and tools | basic | |
| IRC server (ngIRCd) | basic | No accounts and no encryption, by design. |
| MQTT broker for Meshtastic, the MQTT explorer and the decoder bridge | basic | |
| Mesh page (nodes heard) | basic | |
| Syncthing | basic | |
| Terminal (ttyd) | basic | |
| Web add-ons: a catalogue, each on an origin of its own (ELIZA, MQTT explorer) | basic | |
| Toolkits: sets of Debian packages, cached for use with no internet | basic | A page to operate the tools is in the pipeline. |

### Network

| Feature | Status | Notes |
|---|---|---|
| Hotspot, DHCP and DNS (dnsmasq), captive portal | basic | Android does not open captive portals over plain HTTP. |
| Network recovery: a watchdog for each link, recovery steps up to a reboot | basic | |
| Connection settings: box defaults, per interface, per connection | basic | |
| Roaming and joining WiFi networks | basic | |
| Firewall floor for hotspot guests | basic | |
| Internet for guests, at levels chosen by an administrator | basic | The hotspot's DNS names are not done. |
| HTTPS with the box's own certificate | basic | |
| Remote access with Tailscale | basic | |

### Running and looking after the box

| Feature | Status | Notes |
|---|---|---|
| Installer: one command, options, upgrade in place | basic | |
| Offline kit: set up a box with no internet | basic | Debian packages still come from apt or the OS image. |
| Debian package (`.deb`) | MVP | Built by CI. The package does not set up the box by itself. |
| Updates: check, download, verify, install, with automatic schedules | basic | |
| Package watch: packages from their publishers, with channels, delay and rollback | basic | |
| Debian security updates | basic | |
| Backup: settings, settings and data, full image; content export and import | basic | |
| Set up another box from this one | basic | |
| Box doctor | basic | |
| Security doctor, deep audit and CIS cross-reference | basic | |
| Update doctor | basic | |
| Overview and trends: memory, disk, services, uptime heatmaps | basic | |
| Clock: clock module (RTC) support | basic | Choosing the clock's source is in the pipeline. |
| Status light | basic | |
| Crash watch and card-write levels | basic | |

## Features in the pipeline

| Feature | Status | Notes |
|---|---|---|
| Messages with string IDs, ready for translation; one shape for every doctor finding | plan | |
| Notebooks encrypted at rest, and in the box's backup | concept | Choices to make: the encryption option, the key, backup in or out, a recovery key. |
| Choosing the clock's source (GPS, clock module, local NTP, internet NTP) | concept | |
| Hub as a Tor onion service, as an add-on | plan | Parked. |
| Remote access through an anonymous discovery service | concept | Parked. |
| Add-ons that state the oldest irate-box release they work with | idea | Needs signed release numbers. |
| Hosting the desktop Meshtastic app and the Android app as downloads | concept | |
| Bluetooth scanner, and control of BLE nodes | idea | |
| The board's own Cortex-M0 coprocessor, started from Linux | concept | Luckfox Lyra. |
| Meshtastic on that coprocessor | idea | |
| A page to operate the toolkits | concept | Reuses the toolkit cards. |
| Web Serial over HTTPS with a certificate that browsers trust (DNS-01) | idea | |
| An offline base map for the mesh page | idea | |
| Bigger versions for bigger boards | idea | |
| Security updates from a USB drive | idea | |
| The editors on an origin of their own | idea | |
| A box OS image built on Debian | MVP | |
| Package sources stated truly, with fallbacks | MVP | |
| Proving offline firmware builds for each board family | plan | Paused. |
| Network overview: a pointer to the Box doctor and every option in place | concept | Parked. |
| An audit of what information an administrator can see | idea | |
| Customising: languages and units | idea | |
| Lighter: less memory and fewer writes on small boards | idea | |
