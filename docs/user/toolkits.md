<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Toolkits

*Software for jobs on the box. Install it without an internet connection. On the administration pages: System → Toolkits.*

A toolkit is a set of Debian packages for one kind of job. The toolkits are Building, Capture, Debugging, GPS time, Network, Radio, Recovery, Security, Debug symbols and System.

While the box is online, it downloads the packages of each toolkit into a local cache. When the cache of a toolkit is complete, you can install the toolkit without an internet connection. After an installation, the services of the toolkit stay stopped. The exceptions are on the card of each toolkit.

On the card of each toolkit:

- **Install** installs the toolkit.
- **Remove it again after** removes it after an hour, a day, a week or never. This is the default removal period.
- **Keep current** refreshes the cache automatically.
- **Roll back** returns to an earlier copy.

Select **+ Custom toolkit** to make your own toolkit from a list of packages.

## USB drive transfer

To move toolkits to a box that has no internet connection:

1. Copy the toolkits to a USB drive.
2. Import them from the drive on the other box.

The box checks the package lists against the signed indexes of Debian. It checks the hash of each package file against those lists.
