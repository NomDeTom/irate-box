<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# What an administrator can see

*The audit behind [What an administrator can see](../user/what-the-admin-can-see.md). Read against `main` @ `c6a159b`. It lists what the administrator pages show and what the same person, at the console or with the box's storage, can read. It is an inventory, not a promise: change a page or a path and change this file.*

## The two readers

Two different people are called "the administrator" in the pages:

- **The hub administrator** signs in at `/admin` with the box's admin login or an account with the admin role. They see what the hub serves through the pages and the routes behind them.
- **Root** has the box's console or SSH, or a copy of its storage. Root can read every file on the card, whatever the pages show. The hub administrator is not root; the pages do not offer them a shell.

Nothing below is encrypted at rest. The only encryption is in transit (HTTPS) and, for a few secrets, the file's mode and owner. Client-side encryption of notebooks is designed but not built.

## What the administrator pages show

| Page | What it shows | Where it comes from | Who can read it on disk | Kept |
| --- | --- | --- | --- | --- |
| Accounts | Each account's name, state (user, admin, asked, disabled), when it was created, when it was last seen, who made it, and whether it signs in over HTTPS | `$STATE/accounts.json` (`accounts.public`) | `hub` (0600) and root | Until the account is removed; `seen` moves at each sign-in |
| Moderation → Shoutbox, Board | Messages and threads, with the name of a signed-in author where the app shows one | `$STATE/messages.json`, `$STATE/board.json` | `hub` and root | Shoutbox 24 h of powered-on time; board 7 days after the last reply |
| Moderation → File drop | Names and details of uploaded files, to delete them | `$STATE/drop/` | `hub` and root | The retention set on Saved work |
| Saved work | The shared gallery and collaboration saves, their sizes, and the storage settings | `$STATE/store/` | `hub` and root | The gallery's retention, or until the storage limit |
| User data → Notes (SilverBullet) | **Download** of the shared notebook as a zip: every page and attachment | `$STATE/notes/` | `hub` and root | Until removed in the notebook |
| User data → Git | The box's repositories and their settings | `$STATE/git/` | `hub` (the git server) and root | Until removed |
| Network → Hardware / Overview | Devices seen: addresses, hostnames, the interface, the access point | `$STATE/control/netinv.json`, NetworkManager, `/proc/net/arp` | `hub` (the scan) and root | The scan replaces the last |
| People (Overview) | Two counts: different devices today and this week | `/run/irate-box-visitors/counts.json` | Anyone who can read `/run` (0644), root | RAM only; gone at reboot |
| Page loads (Overview) | Which paths were loaded and how often; no person | `$STATE/page-history.json`, `/run/irate-box/pagelog/pages.log` | `hub` and root | A rolling history |
| Updates → Scheduled runs | Whether the librarian's runs finished, and the errors they recorded | `$STATE/library/last-run.json` | `hub` and root | The last runs |
| Box doctor, Security doctor, Updates doctor, Housecall | Readings, paths, hashes, package names, unit states, and the findings' evidence | `$STATE/control/*.json`, `/proc`, systemd, apt | `hub` (the reports) and root | The last report of each |
| Backup | A settings archive, a settings-and-data archive, or a full card image | `$STATE/` (the first two), the card (the image) | `hub` builds the first two; root writes the image | Until the download is deleted |

The administrator pages do **not** offer:

- password hashes, session cookies, or another account's password;
- the private notebook of another login (its page downloads only the signed-in user's own);
- the TLS server key, the CA key, the signing keys, `front-secret.env`, or `admin-password`;
- the hub's GitHub token (the librarian's settings page shows whether one is kept, not the token).

## What the backups carry

This is where the page and root differ most.

- **Settings archive** (`level=settings`): top-level files and the settings folders. It includes `accounts.json` (the password hashes, not the passwords), the librarian's sources and settings, the add-ons' settings, and the hub's settings. An administrator can download it.
- **Settings and shared data** (`level=data`): the same, plus the shared notes (`notes/`), the gallery and collaboration saves (`store/`), the file drop (`drop/`), the shoutbox and board, service history, the mesh nodes, and the git repositories. An administrator can download it.
- **Full storage image** (`backup-image`, root's): the whole card, including the private notebooks (`notebooks/`), the notes, the drop, the accounts, and every secret root keeps under `/etc/hub` and `/var/lib/irate-box-root` (the CA and server keys, the front secret, the signing config, the retain key). It is written to a USB drive and needs root to start. Whoever holds the image can pose as the box and read every user's data. The page says it contains everything; it does not say root's keys are in it too.

## What is unencrypted at rest

All of it: the shared notes, the private notebooks, the gallery, the drop, the board and shoutbox, the accounts file, the git repositories, the logs and the doctors' reports. File modes and owners decide who can read through the operating system, not the content. On this box:

- `$STATE` is `hub:hub`, and the hub process runs as `hub`. Everything under it is readable by that one account, so the private notebook folders are not isolated from each other by the operating system; the per-login folder is a name, not a permission.
- `$STATE/accounts.json` is `0600 hub`, so only the hub and root read it. Its hashes are scrypt, not plain text.
- `/etc/hub` is root's, 0710 root, with each secret 0600 or 0640 for the web server's group. The hub reads only the files made readable to it (`front-secret.env` is root-only; the hub sees the front's proof in the request, not the file).
- `/var/lib/irate-box-root` is root's (0700), with the retain key, the protected copy and the security record.
- `/run/irate-box-visitors/counts.json` is 0644: two numbers, no address.
- The retain feature's removable copy (a USB drive the owner adds) copies the shared notes, saved work, messages and drop in plain text; only the keys are encrypted there. The stick is as readable as the card.

## Gaps and options

1. **Private notebooks are not private at rest.** The folder separation is by name; the hub user reads every notebook, and a full image or a root shell reads them all. Item 123 in the work plan is the designed fix; it is not built. Until then the pages say "private" and the storage says otherwise.
2. **The settings backup carries the password hashes.** The settings archive is meant to be small and restorable; it also carries `accounts.json`. Options: leave the hashes out and require accounts to be recreated, or encrypt the archive with a passphrase shown once. Either changes a restore.
3. **The full image is root-equivalent, and the page does not say so.** It carries the CA key, the server key, the front secret and the retain key. A short warning beside **Write backup image** is the small fix; keeping the keys out of the image is not possible while it is the card.
4. **A shared notebook has one owner: everyone.** The SilverBullet download is the whole notebook, by design. A per-user notebook is the alternative (item 123's work); the guide says the shared notebook is shared.
5. **Sign-in metadata is shown, not the credential.** Names, roles, last seen and who invited whom are on Accounts. That is enough to see who uses the box and when, even without their files. Options: show only the current state, or make the last-seen time a choice.
6. **The doctors keep paths and hashes.** Housecall can name another box's files; the security doctor can name a listener's unit. They do not keep file contents. A report is not a copy, but it can describe a user's files.
7. **No role below administrator.** Every administrator sees every row above. A second role (moderator, or an account that may only see accounts) is a design change, not a small fix.

## What was checked

The page list comes from `web/admin.html` and the routes in `irate_box/hub/server.py`; the data paths and modes from `accounts.py`, `pagehistory.py`, `watchlog.py`, `visitors.py`, `store.py`, `notebooks.py`, `backup.py`, and the install's `$STATE` and `/etc/hub` layout. The backups' contents come from `backup.py`'s `DATA` and `kind_of`. The account fields are `accounts.public`; the notebook download is the signed-in user's own (`/api/notebook/download`), and the admin's notebook page sets the mode and limits, not another user's files.
