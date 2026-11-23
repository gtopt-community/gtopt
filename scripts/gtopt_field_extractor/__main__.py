# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``python -m gtopt_field_extractor``: developer tool, not installed as a console script."""

import sys

from gtopt_field_extractor.gtopt_field_extractor import main

sys.exit(main())
