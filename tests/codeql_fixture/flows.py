# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The fixture for the CodeQL model pack (tests/codeql_model_check.sh). It is only read by
CodeQL, never imported or run. One flow per row of
.github/codeql/extensions/irate-box-sanitisers/sanitisers.model.yml goes through the shared check,
under each spelling the hub's code uses; every line marked `# control:` sends the same value to
the same place without the check, and must stay flagged. Anything else flagged is a model that
has stopped working."""

import logging
import os
from http.server import BaseHTTPRequestHandler

import irate_box.validate
from irate_box import validate
from irate_box import validate as checks

log = logging.getLogger("fixture")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # path-injection: the value is checked, so opening it is fine under every spelling
        open(validate.path(self.path, "/srv")).read()
        open(irate_box.validate.path(self.path, "/srv")).read()
        open(checks.path(self.path, "/srv")).read()
        os.stat(validate.path(self.path, "/srv", must_exist=True))
        open(self.path).read()  # control: path-injection

        # log-injection: the value is cleaned, so logging it is fine under every spelling
        log.info(validate.log_text(self.path))
        log.info(irate_box.validate.log_text(self.path))
        log.info("%s", checks.log_text(self.path))
        log.info(self.path)  # control: log-injection
