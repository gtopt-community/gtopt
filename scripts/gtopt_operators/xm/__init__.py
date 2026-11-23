# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""XM (Colombia) operator profile.

Data sources are declared in ``sources.toml`` (see
:mod:`gtopt_operators.registry`); download them with
``gtopt-fetch xm ...``.
"""

from gtopt_operators.registry import OperatorInfo, load_registry

#: Identity of the operator (id, name, country, time zone).
OPERATOR: OperatorInfo = load_registry("xm").operator
