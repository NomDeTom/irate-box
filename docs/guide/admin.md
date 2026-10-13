<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# The admin pages

*For the owner: where everything is, at `/admin/`.*

Sign in as the admin and press **⚙️ Admin**. The sidebar has these groups (each folds; on a phone it's a menu at the top):

- **Overview**: what needs your attention, the box's memory, card and uptime, the network's last 72 hours, and the services with Start, Stop and Restart. **Memory and disk history** (folded) draws the last day or week of both, every five minutes. Signed in with an admin account, the hub page's memory and disk tile has a **▸ Trends** triangle too, opening the last day below the tiles; nobody else sees it, or can ask for it. **Setup steps** sits beside it until you hide it.
- **Apps**: a page for each app on the box (Excalidraw, the file drop, Kiwix, Git, Notes, the shoutbox and board, the Firmware Factory and the rest). Each starts with what it is and its state, with **Open**, **Add** or its service's **Start**/**Stop** where they apply; then its own settings and content; then, folded, **Who can open it**, its **Hub tile** (a summary, with a link to arrange it on Home tiles) and **Maintenance** (its updates, its installation and **Remove**). **All apps** lists them all, one line each (installed, available and built in, with a search), and holds the add-ons' installer and the web add-ons' catalogue.
- **Hub layout**: what visitors see. **Home tiles** arranges the home page's tiles: each one's place, size, who sees it, what it does for someone who may not open it, and its icon, all kept with one **Save**; **Folders** lists the hub's folders of tiles (Meshtastic, RF & LoRa, Calculators, Electronics), each with its own page for who sees it and what's in it; **Status widgets** the row of readouts; **Appearance** the hub's name, its colours, page widths and emoji. **Colours** keeps a light palette and a dark one, each its own (presets set both); every pair of words and background is checked against the accessibility guidelines' 4.5:1, and one that falls short is named with its ratio and saved only once you say so. Visitors still choose light, dark or Auto for themselves, and see these colours as the **Box preset** unless they pick **Standard**, **Increased contrast**, E-ink or Cybercore in the 🎨 menu.
- **Moderation**: what people reported, and everything people made, to look through or remove.
- **System**: Accounts & users, Network, Security, the librarian (what it is doing, the schedule for books and apps, the room they may take, the other updates' schedules with links to where each is set, and a GitHub token), Toolkits, Clock, **Recovery and monitoring** (what crash watch keeps, what restarts a frozen box, and how hard the box writes to its card), Updates, Backup and transfer.
- **Doctors**: the Box doctor, the Security doctor and the Updates doctor. Each looks at its part of the box, says what's wrong in plain words, and offers the fix as a button where there is one.

A choice is never made for you: where there's more than one sensible answer, the page shows them from the safest to the most open, the safest already chosen.

Changes that need the box's own powers (starting services, installing, updates) go to a small helper on the box that does one job at a time. The Box doctor shows its queue.
