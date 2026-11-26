# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Allow ``python -m pp2gtopt``."""

import sys

from pp2gtopt.main import main


if __name__ == "__main__":
    sys.exit(main())
