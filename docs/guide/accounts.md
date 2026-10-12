<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Accounts

*Who can sign in, and how. On the admin pages: System → Accounts & users.*

The box works without accounts: guests use the apps as they are, and the admin signs in. Accounts are for people who should have more: apps only users can open, their own settings, files they can lock.

## Sign-up

- **Off** (the default): no accounts but the admins'.
- **Open**: anyone can make one, usable at once (at most five an hour from one address).
- **Application**: anyone can ask; you accept or refuse.
- **Admin assigned**: only you make them.

To make one yourself: **Make user** or **Make admin**. The box gives a one-time code (good for a day); the person sets their own password with it on the account page (**Set your password with a code**). **Reset password** on an account gives a new code.

## Passwords over plain HTTP

**Prevented** (passwords only over HTTPS), **Warning** (the default: allowed, and the page says it can be read off the air) or **Permitted**. An admin's password changes only over HTTPS, or at the console, whatever this says.

## The account page

Everyone's, at `/account.html`: change your password; whether others see your name in "here now"; your name's colour; whether your files in the drop are locked by default; who sees what you post; your own notebook; log out.

## Notebooks

Beside the shared notes, each signed-in person can have a notebook of their own that no one else can open: **My notebook** on the account page opens it, or downloads it as a zip. It runs only while its owner uses it: it starts when opened, and stops at sign-out or half an hour after it was last used. About 40 MB of memory each while running.

On the admin pages, **Accounts → Notebooks**: **Shared notes only**, **By sign-up** (the default: a notebook for each login wherever people may sign up) or **Also one per login**; how many may run at once (5 by default); and how much memory to keep free, below which no more start. A notebook's pages stay on the box when it stops, when notebooks are switched off, and when its account is deleted. They are not in the box's backup: each person keeps their own with the download.

## The box's own login, for scripts

A separate login (user `admin`) for scripts that call the admin pages, made only when you ask, at the console: `sudo /opt/irate-box/irate-box hub_control script-login`. Its password is kept in `/etc/hub/admin-password`, readable only by root. `--off` removes it.
