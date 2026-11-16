# SPDX-License-Identifier: GPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Entry point for ``python -m gtopt_check_fingerprint``."""

import sys

from ._cli import main

sys.exit(main())
