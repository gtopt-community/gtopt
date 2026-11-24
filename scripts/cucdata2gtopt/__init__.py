# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""cucdata2gtopt -- the clustered-UC benchmark of Morales-Espana & Tejada-Arango.

Converts the CUC-data workbooks (https://github.com/datejada/CUC-data,
"Modeling the hidden flexibility of clustered unit commitment", IEEE TPWRS
34(4) 2019) into the gtopt individual-unit UC case of the paper's Table I.
The data carry no licence: they are downloaded from a pinned commit into a
local cache on demand and never vendored (see :mod:`cucdata2gtopt._source`).

Use as a module::

    python -m cucdata2gtopt --system 39 --reserve 10 -o out/
"""

from cucdata2gtopt._converter import (
    FORMULATIONS,
    CaseOptions,
    build_case,
    case_name,
    default_reserve_pct,
    published_scale,
    unit_constraints,
    write_case,
)
from cucdata2gtopt._model import UnitParams, derive_unit
from cucdata2gtopt._reader import CucData, read_workbook

__all__ = [
    "FORMULATIONS",
    "CaseOptions",
    "CucData",
    "UnitParams",
    "build_case",
    "case_name",
    "default_reserve_pct",
    "derive_unit",
    "published_scale",
    "read_workbook",
    "unit_constraints",
    "write_case",
]
