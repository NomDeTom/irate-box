<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Toolkits

*Software for jobs on the box, installed with no internet. On the admin pages: System → Toolkits.*

A toolkit is a set of Debian packages for one kind of job: Building, Capture, Debugging, GPS time, Network, Radio, Recovery, Security, Debug symbols, System. While the box is online it keeps each one's packages in a local store; installing one then needs no internet. Their services are left stopped.

On each toolkit's card: **Install**, **Remove it again after** (an hour, a day, a week… or never), **Keep current**, and **Roll back** to an earlier copy. **+ Custom toolkit** makes your own from a list of packages.

**By USB stick**: copy toolkits to a stick for a box with no internet, or import them from one; every package is checked against Debian's signed lists.
