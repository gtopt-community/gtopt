# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_check_solvers — discover and validate gtopt LP solver plugins.

Public API
----------
The main entry point is
:func:`gtopt_check_solvers.gtopt_check_solvers.main` (registered as the
``gtopt_check_solvers`` console script).

Sub-modules
-----------
:mod:`._solver_tests`  — LP solver test-suite definition and runner

The gtopt binary is discovered via :mod:`gtopt_shared.gtopt_binary`.
"""

from .gtopt_check_solvers import main

__all__ = ["main"]
