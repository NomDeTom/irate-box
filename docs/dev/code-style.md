<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Code style

Notes on how the code is written, where a choice is not obvious from the code itself. For the words the box shows to people, see [writing.md](writing.md).

## Shell script first lines

Which shell a script names depends on who runs it:

| The script | Its first line | Now |
|---|---|---|
| Run as root, and long or using bash's arrays and tests | `#!/bin/bash -p` | `install.sh`, `uninstall.sh`, `tools/nand-logs.sh` |
| Run as root, small, and plain POSIX | `#!/bin/sh` | `scripts/*-apply.sh`, `irate_box/root/content-import.sh`, `irate-box`, the package's `postinst`, `postrm` and `irate-box-setup` |
| Tests and developer tools | `#!/bin/bash` | `tests/`, `packaging/deb/build.sh` |
| Only sourced | `# shellcheck shell=bash`, below | `tests/jsdom/jsdom.sh` |

A script that departs from this says why in a comment under its first line.

Never `#!/usr/bin/env bash`. It finds bash on `PATH`, and a script run as root does not take its programs from `PATH`.

`-p` is bash's privileged mode. Without it, bash runs the file named in `BASH_ENV` before the script and defines any functions exported in its environment. It also takes `SHELLOPTS`, `BASHOPTS`, `CDPATH` and `GLOBIGNORE` from there. With `-p` it ignores all of these, so whoever started the script cannot run code in it as root. `sudo` and systemd units already start with a clean environment. `-p` keeps the script safe when it is started some other way, such as `su` without `-`, or `sudo -E`.

`sh` is dash on Debian. It reads no startup file when it is not interactive and imports no functions, so it gives the least to an environment someone else set. It has no arrays, `[[ ]]` or `PIPESTATUS`. A small script that comes to need them moves to `#!/bin/bash -p`. Working around their absence brings the quoting mistakes a root script can least afford.

A shebang applies only when the file is run as a program. Code that starts a root script with `bash` passes `-p` itself, as `hub_control.py`, `health.py` and `irate-box-setup` do: `bash -p install.sh`. A command a person types with `sudo` (`sudo bash uninstall.sh`) need not, as `sudo` clears the environment first.

## Shell files that are only sourced

A shell file that other scripts source (`. tests/jsdom/jsdom.sh`) and that is never run by itself:

- Has no shebang and is not executable. A shebang says the file can be run on its own, and this one cannot.
- Starts with `# shellcheck shell=bash`, then the SPDX lines. Without a shebang or this line, ShellCheck does not know the shell and fails the file (SC2148) in CI's Trunk check.
- Says `bash`, as the scripts that source it are bash scripts. It says `sh` only if a `#!/bin/sh` script sources it. Then it has to stay POSIX.

Each script that sources it puts `# shellcheck source=path/from/the/repository` on the line before. ShellCheck then follows the file and checks the variables it sets.
