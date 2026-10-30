<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# What an administrator can see

*Who runs this box, and what they can see about you and your work.*

An administrator sets up the box and runs it. The administrator pages are for that person. They are not a window into everything, but they are more than nothing.

## Your account

An administrator can see:

- your account name, and whether you are a user or an administrator;
- when the account was made, and who made it;
- when you last signed in, and whether you signed in over HTTPS.

An administrator cannot see your password. The box keeps a hash of it, not the password itself. Nobody can read a password back from it.

## What you put on the box

- The shared notebook is one notebook for everyone who may open it. An administrator can download all of it, with every page and attachment, as a zip.
- Your private notebook, if the box gives you one, is kept apart by name while it runs. It is not encrypted on the box. The administrator pages do not offer another person's notebook. A person with the box's console or root access, or a full copy of the box's storage, can read it.
- Saved work, the gallery, drawings and the file drop are shared. An administrator can see the files, their sizes, and delete them.
- The shoutbox and the board are public to the box. An administrator can read them and delete messages. Your account name is shown with a message if you were signed in when you posted it.
- Files in the box's git repositories can be read by whoever may open the repository. An administrator manages the repositories.
- Full backups can include these files. The settings-and-data backup includes the shared notes, saved work, the file drop, the shoutbox and board, and the git repositories. It leaves out private notebooks. The full card image includes everything, including private notebooks.

## What the box says about people

- How many devices used the box today and this week. This is a count, not a list: the box counts the devices from short-lived hashes and keeps only the two numbers.
- Which pages were loaded, and how often. This is a path, not a person.
- Which devices were seen on the network: their addresses and names, for the person running the network.
- The doctors' reports: settings, file paths, hashes and service states. They do not keep the contents of your files.

## What is not encrypted

Nothing on the box's storage is encrypted at rest yet. HTTPS protects a password on its way to the box. It does not protect the files once they are on the card. Treat the box's storage, and especially a full image of it, as readable by whoever has it.

The box's second-copy setting can also copy the shared notes, saved work, messages and file drop to a USB drive. That copy is plain text too: only the keys are encrypted there. A lost stick is as readable as a lost card.

The administrator can make the box safer for everyone: ask them to keep the box's storage with care, and to use a full image only on a drive they keep as they would keep a password.

## If you are the administrator

The complete list, with the files and modes, is in the developer notes (What an administrator can see).
