# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""dlr2gtopt -- dynamic line rating (DLR) inputs for gtopt's ``Line.dlr``.

Towers -> spans -> per-span weather -> IEEE 738 ampacity -> critical spans
(Matus et al., IEEE TPWRD 27(2):1002-1009, 2012,
DOI 10.1109/TPWRD.2012.2185254) -> the ``dlr`` object of a gtopt Line with
per-block weather or rating schedules.

Use as a module::

    python -m dlr2gtopt --help
"""

from dlr2gtopt._critical import equidistant, greedy, pearson, residual
from dlr2gtopt._emit import RatingParams, emit, span_ampacity, span_rating_mw
from dlr2gtopt._ieee738 import CONDUCTORS, Conductor, ampacity, ampacity_to_mw
from dlr2gtopt._spans import Span, Tower, build_spans
from dlr2gtopt._weather import RegularGrid, Weather

__all__ = [
    "CONDUCTORS",
    "Conductor",
    "RatingParams",
    "RegularGrid",
    "Span",
    "Tower",
    "Weather",
    "ampacity",
    "ampacity_to_mw",
    "build_spans",
    "emit",
    "equidistant",
    "greedy",
    "pearson",
    "residual",
    "span_ampacity",
    "span_rating_mw",
]
