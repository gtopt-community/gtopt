# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_check_topology -- preflight network topology analyzer.

Public API
----------
The main entry point is :func:`gtopt_check_topology.main.main` (registered as
the ``gtopt_check_topology`` console script).

Sub-modules
-----------
:mod:`._analyzer` -- pure-data graph analysis (islands, cycles, bridges,
                     danger score, SOS1 candidates)
:mod:`._temporal` -- schedule-aware per-stage model (topology periods,
                     inter-zone lines, expected-islands check)
:mod:`._render`   -- rich-based console renderer and JSON report writer
:mod:`.main`      -- argparse CLI
"""

from gtopt_check_topology._analyzer import (
    DC_REACTANCE_TOL,
    AnalysisReport,
    CycleInfo,
    LineRef,
    NetworkGraph,
    analyse_planning,
    build_network_graph,
    cycle_danger_score,
    resolve_planning,
)
from gtopt_check_topology._temporal import StageTopology, TopologyPeriod
from gtopt_check_topology._render import (
    render_console_report,
    report_to_json,
    sos1_candidates_payload,
)
from gtopt_check_topology.main import main

__all__ = [
    "DC_REACTANCE_TOL",
    "AnalysisReport",
    "CycleInfo",
    "LineRef",
    "NetworkGraph",
    "analyse_planning",
    "build_network_graph",
    "cycle_danger_score",
    "main",
    "StageTopology",
    "TopologyPeriod",
    "render_console_report",
    "resolve_planning",
    "report_to_json",
    "sos1_candidates_payload",
]
