# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Building blocks of the run_gtopt live dashboard (Rich).

``parse`` (log trackers) -> ``model.RunState`` (one frame's data) ->
``frame.render_frame`` (pure renderer built from ``panels``, ``views``,
``overlays``); ``summary`` renders what stays after the run; ``keys``
and ``term`` wrap the terminal.  The controller is
:class:`run_gtopt._tui.SolverDisplay`.
"""
