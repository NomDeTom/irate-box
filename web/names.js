// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Made-up names, pirate-flavoured to match the box: an adjective and a noun. The shoutbox's handle ("Salty Parrot",
// well inside its 32 characters) and a new board's user name on Rockchip boards ("salty-parrot": lower case and
// hyphens, as a Linux user name must be; the longest, "peg-legged-quartermaster", is 24 of its 32).
(function () {
  'use strict';
  const ADJECTIVES = [
    'Salty', 'Rusty', 'Grumpy', 'Soggy', 'Barnacled', 'Weathered', 'Marooned',
    'Squinting', 'Drunken', 'Peg-legged', 'One-eyed', 'Windward', 'Leeward',
    'Scurvy', 'Mutinous', 'Becalmed', 'Shipwrecked', 'Tattooed', 'Bilge', 'Irate',
  ];
  const NOUNS = [
    'Parrot', 'Cutlass', 'Doubloon', 'Kraken', 'Compass', 'Anchor', 'Cannon',
    'Buccaneer', 'Deckhand', 'Bosun', 'Swab', 'Sextant', 'Galleon', 'Corsair',
    'Cabin Boy', 'Quartermaster', 'Lookout', 'Castaway', 'Spyglass', 'Grog',
  ];
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const display = () => `${pick(ADJECTIVES)} ${pick(NOUNS)}`;
  const username = () => display().toLowerCase().replace(/[^a-z0-9]+/g, '-');
  window.HubNames = { ADJECTIVES, NOUNS, display, username };
}());
