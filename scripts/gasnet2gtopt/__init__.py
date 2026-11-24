# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gasnet2gtopt -- natural-gas network description to a gtopt planning.

Reads a ``*.gasnet.json`` description (``gas_network`` with
``gas_node_array``, ``gas_pipeline_array``, ``compressor_station_array``,
``gas_source_array``, ``gas_demand_array`` and
``generator_gas_link_array``, plus a ``power_system``) and writes a gtopt
planning JSON with the native gas elements (``GasNode``, ``GasSupply``,
``GasDemand``, ``GasPipe``, ``GasCompressor``, ``GasTurbine``).

``--legacy`` writes the 2026-11 prototype output instead (the same LP as
``DecisionVariable`` / ``UserConstraint``), for comparison.

Use as a module::

    python -m gasnet2gtopt cases/mini3.gasnet.json -o mini3/planning.json
"""

from gasnet2gtopt.common import BuildOptions
from gasnet2gtopt.legacy import build_legacy
from gasnet2gtopt.native import build_native

__all__ = ["BuildOptions", "build_legacy", "build_native"]
