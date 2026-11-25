# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""pytest configuration for the ``tools/`` test suite.

Caps xdist ``-n auto`` to a single worker when the ambient default solver is a
GPU first-order backend (cuOpt).  The GPU-solving tests here (``ucjl2gtopt``)
spawn ``gtopt`` subprocesses that solve on the one GPU; under many parallel
xdist workers they oversubscribe it, starving the pinned CPLEX-MIP subprocesses
into suboptimal incumbents and loosening cuOpt's own PDLP convergence — flaky
failures that vanish when the suite runs serially (verified: ``-n 1`` and
``-p no:xdist`` are green under cuOpt).  Exact CPU solvers keep full ``-n auto``
parallelism, so normal CI is unaffected.
"""

from __future__ import annotations

import os

import pytest

# Kept in sync with tools/test_ucjl2gtopt.py::_FIRST_ORDER_SOLVERS and
# tools/validate_sddp_output.py::FIRST_ORDER_SOLVERS.
_FIRST_ORDER_SOLVERS = frozenset({"cuopt"})


def pytest_xdist_auto_num_workers(config) -> int | None:  # pylint: disable=unused-argument
    """xdist hook: resolve ``-n auto`` to 1 worker under a first-order solver."""
    if os.environ.get("GTOPT_SOLVER", "") in _FIRST_ORDER_SOLVERS:
        return 1
    return None


def pytest_configure(config) -> None:
    """Register the ``slow`` marker used by the long MIP cross-checks."""
    config.addinivalue_line(
        "markers",
        "slow: long-running solve (minutes); skipped unless GTOPT_RUN_SLOW=1 "
        "or selected with -m slow",
    )


def pytest_collection_modifyitems(config, items) -> None:
    """Skip ``slow`` tests by default instead of time-limiting their solves.

    Opt in with ``GTOPT_RUN_SLOW=1`` (nightly) or an explicit ``-m`` that
    names ``slow``.
    """
    if os.environ.get("GTOPT_RUN_SLOW", "") not in ("", "0"):
        return
    if "slow" in (config.getoption("markexpr", "") or ""):
        return
    skip_slow = pytest.mark.skip(
        reason="slow solve: set GTOPT_RUN_SLOW=1 or pass -m slow to run it"
    )
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
