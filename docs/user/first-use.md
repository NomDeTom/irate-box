<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Setup

*For the administrator: install the box and create the administrator account.*

## 1. Install

Before you start, you need a supported board, a network connection and an SSH session on the board.

1. Sign in to the board over SSH.
2. Run these commands:

```
git clone https://github.com/NomDeTom/irate-box && cd irate-box
sudo ./install.sh
```

The installer adds the box. It can add extras at the same time with `--with-…` options. Extras are notes, file sync, the offline library and chat. You can also add them later from the administration pages. See [Add-ons and books](addons-and-books.md). When the installer finishes, it prints the address of the box.

## 2. Create the administrator account immediately

Until you create it, every device on the same network can open the set-up page. It asks for a **setup code** that the
installer prints at the end, so only someone at the console can make the first account. The front page of the hub shows
the warning and a **Set up this box** button.

If the code is gone, print it again at the console:

```sh
sudo /opt/irate-box/irate-box hub_control setup-code
```

Do not send the administrator password over a network where other devices can read it. Select one method.

**A. Over SSH.** You are already signed in for the install. This method does not ask for the setup code, because the
console is the proof. Run this command:

```
sudo /opt/irate-box/irate-box hub_control set-admin yourname
```

The command asks for the password two times.

**B. In the browser, over HTTPS.**

1. Open the administration pages (`/admin/`).
2. Select **Security**, then **HTTPS**.
3. Enable HTTPS. If the page asks, create the certificate of the box.
4. On your phone or computer, open the certificate page (`/certificate`).
5. Install the certificate as the page shows.
6. Open the setup page over HTTPS: `https://`, the address of the box, then `/admin/setup`.
7. Enter the setup code, a name and a password.

HTTPS encrypts the password between your browser and the box. Other devices on the network cannot read it.

**C. In the browser, over HTTP.** The setup page accepts this method only for the first account, and still asks for
the setup code. Tick the box that says you understand the risk. Without HTTPS, other devices on the same network can
read the code and the password. Use this method only on a network that nobody else uses.

## 3. Sign in

Select **👤 Sign in** at the top of the hub. After you sign in as an administrator, the **⚙️ Admin** button appears next to it. Guests do not see this button.

## 4. Complete the setup

The administration pages open with a list of **setup steps**. The steps are:

1. Set the administrator password.
2. Set how guests reach the box, including the hotspot firewall.
3. Set the WiFi country.
4. Review what the box exposes.
5. Add add-ons.
6. Add books.
7. Decide the security settings: visitor counts, signed-in names, account registration, locked tiles, HTTP and HTTPS, the root helper's policy, hotspot security, guest internet access, SSH password sign-in, remote access, Cockpit and the terminal.
8. Make a backup.

You can skip a step and do it later.

## Troubleshooting

- **You forgot the password.** Run `sudo /opt/irate-box/irate-box hub_control set-admin yourname` over SSH. The command sets a new password.
- **The setup code is gone.** Run `sudo /opt/irate-box/irate-box hub_control setup-code` at the console while the box has no administrator account. Once an account exists there is no setup code.
- **You want to change the password.** Use your account page over HTTPS, or the SSH command above. The box refuses an administrator password change over HTTP.
- **A script calls the administration pages** (`curl -u admin:…`). The script needs a script login. Create it with `sudo /opt/irate-box/irate-box hub_control script-login`. Remove it with the `--off` option. See [Accounts](accounts.md).
- **The installer fails.** Read the log in `/var/log/irate-box/install.log`. You can run the installer again: it repairs the installation and upgrades it, and it keeps your settings and add-ons. See [Troubleshooting](troubleshooting.md).
