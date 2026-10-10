<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Setting up a new box

*For the owner: from a fresh install to a box that's yours. About ten minutes.*

## 1. Install

On the board, logged in over SSH:

```
git clone https://github.com/NomDeTom/irate-box && cd irate-box
sudo ./install.sh
```

That's all it needs. Extras (notes, file sync, the offline library, chat…) can be added now with `--with-…` options, or later from the admin pages: see [Add-ons and books](addons-and-books.md). When it finishes it prints the box's address.

## 2. Make yourself the admin, straight away

Until you do, **anyone on the same network can use the box's admin pages**. The hub's front page says so, with a **Set up this box** button.

Your admin password should never cross the network where others can read it. Pick one way:

**A. Over SSH (quickest).** You're already logged in to install, so run:

```
sudo /opt/irate-box/irate-box hub_control set-admin yourname
```

It asks for the password twice. Done.

**B. In the browser, over HTTPS.**

1. Open the box's admin pages (`/admin/`) → **Security** → **HTTPS**, and switch HTTPS on (making the box's certificate if it asks).
2. Open the box's certificate page (`/certificate`) on your phone or computer, and install the certificate as it shows you.
3. Open the set-up page over HTTPS (`https://`, the box's address, then `/admin/setup`), and choose a name and a password.

**C. In the browser, over plain HTTP.** The set-up page lets you, but only if you tick that you understand the password can be read on the network. Fine on a network only you use; not on a shared one. This is allowed only this first time.

## 3. Sign in

From now on, sign in where everyone does: **👤 Sign in** at the top of the hub. Once you're signed in as the admin, a **⚙️ Admin** button appears beside it. Guests never see it.

## 4. The rest, when you like

The admin pages open with a short list of **setup steps**: how guests reach the box, what it shows them, add-ons and books, then a few decisions (visitor counts, new accounts, HTTPS, the hotspot, remote access) and a backup. Each can be skipped and done later.

## If something goes wrong

- **Forgot the password?** Over SSH: `sudo /opt/irate-box/irate-box hub_control set-admin yourname` sets a new one.
- **Want to change it?** Over HTTPS (your account page), or over SSH as above. The box refuses an admin password change over plain HTTP.
- **Scripts that call the admin pages** (`curl -u admin:…`) need a login of their own, made only when you ask: `sudo /opt/irate-box/irate-box hub_control script-login` (and `--off` to remove it).
- **Installer problems:** the log is in `/var/log/irate-box/install.log`; running the installer again is safe. More in [When something goes wrong](troubleshooting.md).
