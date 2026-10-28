<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->
# Code style

Notes on how the code is written, where a choice is not obvious from the code itself. For the words the box shows to people, see [writing.md](writing.md).

## Shell files that are only sourced

A shell file that other scripts source (`. tests/jsdom/jsdom.sh`) and that is never run by itself:

- Has no shebang and is not executable. A shebang says the file can be run on its own, and this one cannot.
- Starts with `# shellcheck shell=bash`, then the SPDX lines. Without a shebang or this line, ShellCheck does not know the shell and fails the file (SC2148) in CI's Trunk check.
- Says `bash`, as the scripts that source it are bash scripts. It says `sh` only if a `#!/bin/sh` script sources it. Then it has to stay POSIX.

Each script that sources it puts `# shellcheck source=path/from/the/repository` on the line before. ShellCheck then follows the file and checks the variables it sets.

Every other shell script starts with a shebang naming its shell.
