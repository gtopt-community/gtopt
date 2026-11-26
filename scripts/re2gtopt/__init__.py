# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""re2gtopt: wind and PV plant generation profiles for gtopt.

Weather -> per-plant hourly output (wind: hub height, air density,
smoothed farm power curve, wakes, losses, cap; PV: pvlib decomposition,
transposition, tracking, temperature, soiling, DC/AC clipping), sized to
each plant's reported Pmax, calibrated on observed generation with
curtailment treated as censoring, aligned hour by hour with the
``dlr2gtopt`` typical year, mapped to blocks and emitted as gtopt
``GeneratorProfile`` schedules.  See README.md.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
