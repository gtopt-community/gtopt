# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""``gtopt-fetch``: common download CLI over the operator source registry.

* :mod:`gtopt_operators.fetch.cache` -- unified cache + ``MANIFEST.jsonl``
  (sha256) + ``sources.lock`` verification;
* :mod:`gtopt_operators.fetch.backends` -- ``cen_portal``, ``static_url``,
  ``manual`` / ``html_index``;
* :mod:`gtopt_operators.fetch.cli` -- the console script.
"""
