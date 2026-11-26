# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""TLS certificate verification for operator HTTP clients.

Every operator client verifies server certificates.  The value handed to
``requests`` (``verify=``) comes from :func:`tls_verify`:

* ``$GTOPT_CA_BUNDLE`` set -> that CA bundle file (corporate proxies,
  private CAs);
* ``$GTOPT_INSECURE_TLS`` = ``1``/``true``/``yes``/``on`` -> verification
  OFF (a warning is logged; for local debugging only);
* otherwise ``True`` (the certifi / system bundle), or -- when the caller
  names extra intermediate certificates a server fails to send -- a
  bundle made of certifi's roots plus those intermediates.

The extra-intermediate case exists for servers with an incomplete chain
(the leaf certificate arrives without its intermediate).  Shipping the
public intermediate keeps verification ON without asking every user to
build a CA bundle by hand.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

_LOG = logging.getLogger("gtopt_operators.tls")

#: Environment variable naming a CA bundle file used instead of certifi's.
ENV_CA_BUNDLE = "GTOPT_CA_BUNDLE"
#: Environment variable that disables certificate verification.
ENV_INSECURE = "GTOPT_INSECURE_TLS"

_TRUE = frozenset({"1", "true", "yes", "on"})


def _cache_dir(environ: Mapping[str, str]) -> Path:
    base = environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "gtopt" / "tls"


def bundle_with_intermediates(
    extra: Sequence[Path], environ: Mapping[str, str] | None = None
) -> str:
    """Return a CA bundle path holding certifi's roots plus *extra* PEMs.

    The bundle is written once per content (the file name carries its
    sha256) under ``$XDG_CACHE_HOME/gtopt/tls`` with an atomic rename.
    """
    import certifi  # pylint: disable=import-outside-toplevel

    env = os.environ if environ is None else environ
    parts = [Path(certifi.where()).read_bytes()]
    parts += [Path(p).read_bytes() for p in extra]
    blob = b"\n".join(parts)
    digest = hashlib.sha256(blob).hexdigest()[:16]
    out_dir = _cache_dir(env)
    out = out_dir / f"ca-bundle-{digest}.pem"
    if out.is_file() and out.read_bytes() == blob:
        return str(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ca-bundle-", dir=out_dir)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return str(out)


def tls_verify(
    environ: Mapping[str, str] | None = None,
    *,
    extra_intermediates: Sequence[Path] = (),
) -> bool | str:
    """Return the ``verify=`` value for ``requests`` (see module doc)."""
    env = os.environ if environ is None else environ
    bundle = env.get(ENV_CA_BUNDLE, "").strip()
    if bundle:
        return bundle
    if env.get(ENV_INSECURE, "").strip().lower() in _TRUE:
        _LOG.warning(
            "TLS certificate verification DISABLED by $%s -- use only for "
            "local debugging",
            ENV_INSECURE,
        )
        return False
    if extra_intermediates:
        return bundle_with_intermediates(extra_intermediates, env)
    return True
