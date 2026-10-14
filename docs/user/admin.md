<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Administration

*For the administrator: the pages under `/admin/`.*

Sign in as an administrator. Then select **⚙️ Admin**. The sidebar has these groups. Each group folds. On a phone, the sidebar is a menu at the top.

- **Overview**: what needs your attention.
  - It shows the memory, the storage card and the uptime of the box, and the last 72 hours of the network.
  - It lists the services with Start, Stop and Restart.
  - **Memory and disk history** (folded) shows the last day or week of both, with a point every five minutes.
  - **Trends**: administrators see a **▸ Trends** triangle on the memory and disk tile of the hub. It opens the last day below the tiles. Other users cannot open or request it.
  - **Setup steps** stays beside it until you hide it.
- **Apps**: one page for each app on the box. Examples are Excalidraw, the file drop, Kiwix, Git, Notes, the shoutbox, the board and the Firmware Factory.
  - Each page starts with the name of the app and its state. It shows **Open**, **Add** or the **Start** and **Stop** of its service where they apply.
  - Next are the settings and the content of the app.
  - The folded sections follow: **Access** (who can open the app), **Hub tile** (a summary, with a link to arrange it on Home tiles) and **Maintenance**.
  - **Maintenance** holds **App updates**, installation and **Remove**.
  - **All apps** lists every app on one line: installed, available and built in. It has a search. It also holds the installer for add-ons and the catalogue of web add-ons.
- **Hub layout**: what visitors see.
  - **Home tiles**: set the place, the size, the Visibility and the icon of each tile. Set what a locked tile does for a visitor who cannot open it. One **Save** keeps all of these.
  - **Folders**: the folders of tiles on the hub (Meshtastic, RF and LoRa, Calculators, Electronics). Each folder has a page for its Visibility and its contents.
  - **Status widgets**: the row of readouts.
  - **Appearance**: the name of the hub, its colours, its page widths and its emoji.
  - **Colours**: the box has one light palette and one dark palette. Presets set both.
  - The box checks the text and background pairs of each palette against the 4.5:1 contrast ratio of the accessibility guidelines. A pair below the ratio is listed with its ratio. The box saves it only after you confirm.
  - Visitors select light, dark or Auto for themselves. They see your colours as the **Box preset**. They can select **Standard**, **Increased contrast**, E-ink or Cybercore in the 🎨 menu.
- **Moderation**: **Reports** (content that users reported) and **User data**. Look at them and remove items.
- **System**: Accounts, Network, Security, the librarian, Toolkits, Clock, **Recovery and monitoring**, Updates, and Backup and transfer.
  - The librarian page shows what the librarian is doing and the schedule for books and apps. It shows the storage limit for books and apps. It links to the schedules of other updates and holds the GitHub token.
  - **Recovery and monitoring** sets what crash watch keeps, what restarts a frozen box, and **SD card write frequency**.
- **Doctors**: the Box doctor, the Security doctor and the Updates doctor. Each doctor checks one part of the box and lists the problems in plain words. Where a repair exists, the doctor shows a button for it.

Where more than one answer is sensible, a page shows the answers from the most restrictive to the least restrictive. The most restrictive answer is selected by default. Changes that need an optional risky setting need your approval.

The box does changes that need root rights (start services, install, update) with a root helper. The helper does one job at a time. The Box doctor shows the queue of the helper.
