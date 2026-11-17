# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""python -m gtopt_marginal_units → main.cli."""

from __future__ import annotations

import sys

from gtopt_marginal_units.main import cli


if __name__ == "__main__":
    sys.exit(cli())
