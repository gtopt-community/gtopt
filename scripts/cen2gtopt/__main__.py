# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""python -m cen2gtopt → main.cli."""

from __future__ import annotations

import sys

from cen2gtopt.main import cli


if __name__ == "__main__":
    sys.exit(cli())
