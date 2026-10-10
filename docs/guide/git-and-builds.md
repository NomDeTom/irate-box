<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Git, builds and firmware

*Repositories, mirrors, the Firmware Factory and the web flasher.*

## Git

The box keeps git repositories (`/git/` public, `/git-private/` behind the login): browse them, clone them, push to them as each one allows (Apps → Git). A repository can build on each push, its runs shown on the page.

**Mirrors** keep a copy of a repository from elsewhere (Meshtastic's firmware, say), the branches and release tags you choose, so builds work with no internet. Each mirror also holds all of its upstream's tags, without their files, and says if one moves.

## The Firmware Factory

Builds Meshtastic firmware on the box (Apps → Firmware Factory → Open the Firmware Factory): choose the source and the release, pick the boards, queue them. One build at a time; the queue can be paused. A finished build can be offered on the hub's front page, and **Publish to the web flasher**.

## The web flasher

At `/flasher/`: puts a published build on an ESP32 radio from the browser, over USB. It needs Chrome or Edge on a computer, and the page over HTTPS (the box's certificate installed). nRF52 and RP2040 radios take a UF2 file dragged onto the drive they show when plugged in: those are among the Factory's downloads.
