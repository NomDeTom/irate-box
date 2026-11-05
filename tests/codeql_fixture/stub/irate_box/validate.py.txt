# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A stand-in for irate_box/validate.py in the second fixture database
(tests/codeql_model_check.py). Its functions do nothing CodeQL can see as a check, so a flow
through them is cleared only by the model pack. In the real module `path` and `log_text` are
written in shapes CodeQL recognises, which would hide a model that has stopped working. The names
match the real module's, which tests/sim_codeql_model.py checks."""


def path(value, root, what="The path", must_exist=False, allow_symlink=False):
    return value


def log_text(value, what="The log line", most=300):
    return value
