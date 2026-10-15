<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Accounts

*Who can sign in, and how. On the administration pages: System → Accounts.*

The box works without accounts. Guests use the apps as the administrator set them up. Accounts give users more: apps that only users can open, their own settings and file locks.

## Account registration

Select one of these settings:

- **Off** (the default): the box has guests and administrators only.
- **Open**: anyone can create an account. The account works at once. One address can create at most five accounts in one hour.
- **Approval**: anyone can request an account. An administrator accepts or refuses the request.
- **Administrator creates**: only administrators create accounts.

To create an account yourself, select **Create user account** or **Create administrator account**. The box shows a one-time code. The code is valid for 24 hours. The person enters the code on the account page, in **Set your password with a code**, and sets a password. To give an account a new code, select **Reset password** on the account.

## Passwords over HTTP

The setting **Password submission over HTTP** has three values:

- **Blocked**: the box accepts passwords only over HTTPS.
- **Warning** (the default): the box accepts passwords over HTTP. The page warns that other devices on the network can read them.
- **Accepted**: the box accepts passwords over HTTP and shows no warning.

HTTP does not encrypt traffic. An administrator password changes only over HTTPS or at the console. This rule applies for every value of the setting.

## The account page

Every user has an account page at `/account.html`. Use it to:

- change your password
- show or hide your name in "here now"
- set the colour of your name
- lock your files in the file drop by default
- set who sees what you post
- open your notebook
- sign out

## Notebooks

Each signed-in user can have a notebook that no other user can open. Select **My notebook** on the account page to open the notebook or download it as a zip file. The box does not encrypt the notebook. The box keeps it away from other users, and administrators can read the files of the box.

A notebook runs only while its user uses it. It starts when the user opens it. It stops at sign-out or 30 minutes after the last use. A running notebook uses about 40 MB of memory.

Open **Accounts → Notebooks** on the administration pages. These settings are there:

- **Shared notebook only**, **Private notebooks when registration is enabled** (the default) or **Private notebooks for all accounts**. The default gives each account a notebook where people can create an account.
- The number of notebooks that can run at once (5 by default).
- The free memory that must remain. Below this amount, no more notebooks start.

The pages of a notebook stay on the box when the notebook stops, when private notebooks are disabled, and when the account is deleted. The Settings only and Settings and data backups do not include notebooks. Each user keeps a copy with the download. The Full image backup includes them.

## Script login

A script login is a separate account (user `admin`) for scripts that call the administration pages. It does not exist until you create it. Create it at the console:

```
sudo /opt/irate-box/irate-box hub_control script-login
```

The box keeps the password in `/etc/hub/admin-password`. Only root can read this file. Use the `--off` option to remove the script login. Scripts that call the box over HTTP send the password in plain text. Use HTTPS for requests over the network.
