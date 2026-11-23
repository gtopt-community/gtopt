# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""COES (Peru) operator profile.

Data sources are declared in ``sources.toml`` (see
:mod:`gtopt_operators.registry`); download them with
``gtopt-fetch coes ...``.
"""

from gtopt_operators.registry import OperatorInfo, load_registry

#: Identity of the operator (id, name, country, time zone).
OPERATOR: OperatorInfo = load_registry("coes").operator
