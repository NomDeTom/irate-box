#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""git http-backend behind Caddy: the request body is taken in full first, as nginx does.

Caddy streams a push to the backend as it arrives, with no length (git sends large pushes
chunked). When it cuts one off at request_body's max_size, git http-backend reading a body of
unknown length meets the early end and spins at 100% CPU for good, with receive-pack and
unpack-objects waiting under it on the pack so far (found 2026-10-02: 66 MB held on the Lyra,
a whole core per refused push in a container, and each one holds one of fcgiwrap's two
workers). nginx reads the whole body before it calls the backend, so it never needs this.

Here a POST's body goes to a temporary file beside the repositories (on the card: /tmp may be
RAM), and only once it is complete does git http-backend run, on that file, with
CONTENT_LENGTH set. A body cut short then fails at once ("early EOF"); one that stalls for
IDLE seconds is dropped before git starts at all. A GET (a clone or fetch's first step) goes
straight to git http-backend. Stdlib only.
"""

import os
import select
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = "/usr/lib/git-core/git-http-backend"
IDLE = 60
# Above the web server's own cap (64 MB): a second line, should that ever be missing.
LIMIT = 80 * 2**20


def main():
    if os.environ.get("REQUEST_METHOD") != "POST":
        os.execv(BACKEND, [BACKEND])
    incoming = Path(os.environ.get("GIT_PROJECT_ROOT", "/var/lib/hub/git/public")).parent / ".incoming"
    incoming.mkdir(exist_ok=True)
    with tempfile.TemporaryFile(dir=incoming) as body:
        total = 0
        while True:
            ready, _, _ = select.select([0], [], [], IDLE)
            if not ready:
                return 1  # stalled: the client or the web server has gone
            chunk = os.read(0, 1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > LIMIT:
                return 1
            body.write(chunk)
        body.flush()
        body.seek(0)
        env = dict(os.environ, CONTENT_LENGTH=str(total))
        env.pop("HTTP_TRANSFER_ENCODING", None)
        return subprocess.run([BACKEND], stdin=body, env=env, close_fds=True).returncode


if __name__ == "__main__":
    sys.exit(main())
