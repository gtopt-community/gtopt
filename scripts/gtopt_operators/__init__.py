# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""gtopt_operators -- per-operator profiles and data-source registries.

One sub-package per power-system operator (``cen``, ``amm``, ``eor``,
``coes``, ``xm``, ``aemo``).  Each ships a ``sources.toml`` with the
public URLs of its datasets and the *names* of the environment variables
holding any API key (see :mod:`gtopt_operators.registry`).  The common
download CLI is ``gtopt-fetch`` (:mod:`gtopt_operators.fetch`).
"""

from gtopt_operators.registry import (
    BUILTIN_OPERATORS,
    DatasetSpec,
    OperatorInfo,
    RegistryError,
    SourceRegistry,
    available_operators,
    endpoint_url,
    load_registry,
)

__all__ = [
    "BUILTIN_OPERATORS",
    "DatasetSpec",
    "OperatorInfo",
    "RegistryError",
    "SourceRegistry",
    "available_operators",
    "endpoint_url",
    "load_registry",
]
