<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Git, builds and firmware

*Repositories, mirrors, the Firmware Factory and the web flasher.*

## Git

The box keeps Git repositories. Guests can open the repositories at `/git/`. The repositories at `/git-private/` need a sign-in. Select Apps → Git to browse, clone and push. Each repository sets who can read it and who can push to it. A repository can build on each push. The page shows the runs.

A **mirror** keeps a copy of a repository from another server, for example the Meshtastic firmware. It keeps the branches and release tags that you select. Mirrors help builds that have no internet connection. Dependencies from other sources are not included.

A mirror also keeps the tag names of the source repository, without the files of the tags. The box warns you when a tag moves.

## The Firmware Factory

The Firmware Factory builds Meshtastic firmware on the box. Select Apps → Firmware Factory → Open the Firmware Factory. Then do these steps:

1. Select the source.
2. Select the revision.
3. Select the boards.
4. Add the targets to the queue.

The box does one build at a time. You can pause the queue. The current build continues while the queue is paused. You can offer a finished build on the front page of the hub. Select **Publish to web flasher** to make it available in the web flasher.

## The web flasher

The web flasher is at `/flasher/`. It puts a published build on an ESP32 radio over USB. It also offers the cached releases of Meshtastic. Do these steps:

1. Use Chrome or Edge on a computer.
2. Open the page over HTTPS. Install the certificate of the box first.
3. Connect the radio with a USB data cable.
4. Select the serial port.
5. Read the erase warning on the page.
6. Select **Install**.

For nRF52 and RP2040 radios, use a UF2 file. Put the radio in its bootloader mode. The radio then shows a drive. Drag the file onto the drive. The downloads of the Factory include these files.
