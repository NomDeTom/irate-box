# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Root's name for the box's durable writes: irate_box.durable.

The helper moved out of root/ so the hub can use it too (notes: box-doctor-hardening-plan Part 2, steps 2 and 4).
Everything root calls is re-exported here, so `from irate_box.root import safeio` still means the same thing.
"""

from irate_box.durable import (  # noqa: F401
    DIR_FLAGS,
    ROOT_STATE,
    STATE,
    LinkRefused,
    NotOurs,
    _dir_fd,
    _new,
    _read_back,
    _syncfs,
    _syncfs_all,
    _write_synced,
    create,
    mkdir,
    open_new,
    own_dir,
    read_own,
    read_request,
    report_path,
    root_dir,
    write,
    write_batch,
    write_checked,
)
