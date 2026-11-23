# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Source registry of a power-system operator (``<op>/sources.toml``).

Every operator package under :mod:`gtopt_operators` ships one
``sources.toml`` describing *where* its public data lives -- never the
data itself and never a credential.  The file has four kinds of tables::

    schema_version = 1

    [operator]                 # who: id, name, country, time zone
    id = "cen"
    country = "cl"
    timezone = "America/Santiago"

    [auth.<name>]              # how to authenticate (env vars only)
    kind = "env"               # "env" | "none"
    env = ["CEN_USER_KEY"]     # first variable that is set wins
    param = "user_key"         # query parameter carrying the key

    [endpoint.<name>]          # base URLs used by the operator clients
    url = "https://..."
    auth = "<auth name>"

    [dataset.<name>]           # one downloadable dataset
    backend = "cen_portal"     # cen_portal | static_url | manual | html_index | rest_api
    ...

Dataset fields (all optional unless noted):

``backend`` (required)
    Download mechanism, see :data:`BACKENDS`.
``description``
    One-line human description.
``patterns``
    ``cen_portal``: file-name regexes; ``{D}`` stands for the
    ``YYYYMMDD`` date.  Matched case-insensitively against the whole
    name; the first pattern with a hit wins.
``categories``
    ``cen_portal``: accepted portal categories (empty = any).
``companions``
    ``cen_portal``: extra files of the same date fetched when present.
``unwrap``
    ``cen_portal``: when the selected file is a wrapper zip, extract
    these members (regexes with ``{D}``) next to it.
``date_rule``
    ``exact`` (default), ``latest_on_or_before`` or ``latest_before``.
``multi``
    ``cen_portal``: keep every match of the date (e.g. intra-day runs).
``url``
    ``static_url`` / ``manual`` / ``html_index``: URL template; ``{date}``
    is a :class:`datetime.date` (``{date:%Y%m%d}``) and ``{version}``
    the ``--version`` argument.
``page``
    Landing page where a human finds the file (manual datasets).
``filename``
    Local file name template (``static_url`` / ``manual``).
``terms``
    URL of the terms of use / copyright notice.
``redistributable``
    ``false`` when the source forbids re-publishing the data: it may be
    downloaded for local use, but never committed or published.
``consumer``
    Converter that reads the dataset (documentation only).
``auth``
    Name of an ``[auth.*]`` table needed by the backend.
``sha256``
    Optional pinned checksum, checked by ``gtopt-fetch verify``.

Operators shipped outside this repository register an extra
``sources.toml`` through the ``gtopt.operators`` entry-point group: the
entry point must resolve to an importable package that contains a
``sources.toml``; it overrides a built-in operator with the same id.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib

#: Schema version understood by this loader.
SCHEMA_VERSION = 1

#: Download backends known to :mod:`gtopt_operators.fetch`.
BACKENDS = ("cen_portal", "static_url", "manual", "html_index", "rest_api")

#: Date selection rules.
DATE_RULES = ("exact", "latest_on_or_before", "latest_before")

#: Auth kinds.  Secrets are only ever read from environment variables.
AUTH_KINDS = ("env", "none")

#: Operators bundled with gtopt (sub-packages of :mod:`gtopt_operators`).
BUILTIN_OPERATORS = ("cen", "amm", "eor", "coes", "xm", "aemo")

#: Entry-point group for operators shipped by other packages.
ENTRY_POINT_GROUP = "gtopt.operators"

_KNOWN_DATASET_KEYS = frozenset(
    {
        "backend",
        "description",
        "patterns",
        "categories",
        "companions",
        "unwrap",
        "date_rule",
        "multi",
        "url",
        "page",
        "filename",
        "terms",
        "redistributable",
        "consumer",
        "auth",
        "sha256",
        "notes",
    }
)


class RegistryError(ValueError):
    """A ``sources.toml`` is malformed or an id is unknown."""


@dataclass(frozen=True, slots=True)
class OperatorInfo:
    """``[operator]`` table: identity of the operator."""

    id: str
    name: str
    country: str
    timezone: str
    homepage: str = ""
    geo: str = ""  # ISO country the endpoints are restricted to, if any


@dataclass(frozen=True, slots=True)
class AuthSpec:
    """``[auth.<name>]`` table: where a credential comes from."""

    name: str
    kind: str
    env: tuple[str, ...] = ()
    param: str = ""
    doc: str = ""


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    """``[endpoint.<name>]`` table: a base URL."""

    name: str
    url: str
    auth: str = ""
    origin: str = ""
    doc: str = ""


@dataclass(frozen=True, slots=True)
class DatasetSpec:  # pylint: disable=too-many-instance-attributes
    """``[dataset.<name>]`` table: one downloadable dataset."""

    name: str
    backend: str
    description: str = ""
    patterns: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    companions: tuple[str, ...] = ()
    unwrap: tuple[str, ...] = ()
    date_rule: str = "exact"
    multi: bool = False
    url: str = ""
    page: str = ""
    filename: str = ""
    terms: str = ""
    redistributable: bool = True
    consumer: str = ""
    auth: str = ""
    sha256: str = ""
    notes: str = ""

    def regexes(self, ymd: str | None) -> list[re.Pattern[str]]:
        """Compile :attr:`patterns`; ``{D}`` -> ``ymd`` or a date capture."""
        return [compile_pattern(p, ymd) for p in self.patterns]


@dataclass(frozen=True, slots=True)
class SourceRegistry:
    """Parsed ``sources.toml`` of one operator."""

    operator: OperatorInfo
    auth: dict[str, AuthSpec] = field(default_factory=dict)
    endpoints: dict[str, EndpointSpec] = field(default_factory=dict)
    datasets: dict[str, DatasetSpec] = field(default_factory=dict)
    path: Path | None = None

    def dataset(self, name: str) -> DatasetSpec:
        """Return dataset ``name`` or raise :class:`RegistryError`."""
        try:
            return self.datasets[name]
        except KeyError:
            known = ", ".join(sorted(self.datasets)) or "(none)"
            raise RegistryError(
                f"operator {self.operator.id!r} has no dataset {name!r}; known: {known}"
            ) from None

    def endpoint_url(self, name: str) -> str:
        """Return the URL of endpoint ``name``."""
        try:
            return self.endpoints[name].url
        except KeyError:
            raise RegistryError(
                f"operator {self.operator.id!r} has no endpoint {name!r}"
            ) from None


def compile_pattern(pattern: str, ymd: str | None) -> re.Pattern[str]:
    """Compile a file-name regex; ``{D}`` -> ``ymd`` or ``(\\d{8})``."""
    repl = re.escape(ymd) if ymd is not None else r"(\d{8})"
    return re.compile("^" + pattern.replace("{D}", repl) + "$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Parsing + validation
# ---------------------------------------------------------------------------


def _str_tuple(value: Any, where: str, errors: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    errors.append(f"{where}: expected a string or a list of strings")
    return ()


def validate_sources(data: dict[str, Any]) -> list[str]:
    """Return the list of schema errors of a parsed ``sources.toml``."""
    errors: list[str] = []
    _parse(data, errors)
    return errors


def _parse_operator(data: dict[str, Any], errors: list[str]) -> OperatorInfo:
    op = data.get("operator")
    if not isinstance(op, dict):
        errors.append("missing [operator] table")
        op = {}
    for key in ("id", "name", "country", "timezone"):
        if not isinstance(op.get(key), str) or not op.get(key):
            errors.append(f"[operator].{key} must be a non-empty string")
    return OperatorInfo(
        id=str(op.get("id", "")),
        name=str(op.get("name", "")),
        country=str(op.get("country", "")).lower(),
        timezone=str(op.get("timezone", "")),
        homepage=str(op.get("homepage", "")),
        geo=str(op.get("geo", "")),
    )


def _parse_auth(data: dict[str, Any], errors: list[str]) -> dict[str, AuthSpec]:
    out: dict[str, AuthSpec] = {}
    for name, tbl in (data.get("auth") or {}).items():
        where = f"[auth.{name}]"
        if not isinstance(tbl, dict):
            errors.append(f"{where} must be a table")
            continue
        unknown = set(tbl) - {"kind", "env", "param", "doc"}
        if unknown:
            # A literal secret ("key", "value", ...) must never live in
            # the registry: reject every field we do not know.
            errors.append(f"{where}: unknown field(s) {sorted(unknown)}")
        kind = tbl.get("kind", "")
        if kind not in AUTH_KINDS:
            errors.append(f"{where}.kind must be one of {AUTH_KINDS}")
        env = _str_tuple(tbl.get("env"), f"{where}.env", errors)
        if kind == "env" and not env:
            errors.append(f"{where}.env must name at least one variable")
        out[name] = AuthSpec(
            name=name,
            kind=str(kind),
            env=env,
            param=str(tbl.get("param", "")),
            doc=str(tbl.get("doc", "")),
        )
    return out


def _parse_endpoints(
    data: dict[str, Any], auth: dict[str, AuthSpec], errors: list[str]
) -> dict[str, EndpointSpec]:
    out: dict[str, EndpointSpec] = {}
    for name, tbl in (data.get("endpoint") or {}).items():
        where = f"[endpoint.{name}]"
        if not isinstance(tbl, dict) or not isinstance(tbl.get("url"), str):
            errors.append(f"{where}.url must be a string")
            continue
        if not tbl["url"].startswith("https://"):
            errors.append(f"{where}.url must be https")
        ref = str(tbl.get("auth", ""))
        if ref and ref not in auth:
            errors.append(f"{where}.auth references unknown [auth.{ref}]")
        out[name] = EndpointSpec(
            name=name,
            url=tbl["url"],
            auth=ref,
            origin=str(tbl.get("origin", "")),
            doc=str(tbl.get("doc", "")),
        )
    return out


def _parse_dataset(
    name: str, tbl: dict[str, Any], auth: dict[str, AuthSpec], errors: list[str]
) -> DatasetSpec:
    where = f"[dataset.{name}]"
    unknown = set(tbl) - _KNOWN_DATASET_KEYS
    if unknown:
        errors.append(f"{where}: unknown field(s) {sorted(unknown)}")
    backend = str(tbl.get("backend", ""))
    if backend not in BACKENDS:
        errors.append(f"{where}.backend must be one of {BACKENDS}")
    date_rule = str(tbl.get("date_rule", "exact"))
    if date_rule not in DATE_RULES:
        errors.append(f"{where}.date_rule must be one of {DATE_RULES}")
    patterns = _str_tuple(tbl.get("patterns"), f"{where}.patterns", errors)
    if backend == "cen_portal" and not patterns:
        errors.append(f"{where}: cen_portal datasets need patterns")
    for pat in patterns:
        try:
            compile_pattern(pat, "20000101")
        except re.error as exc:
            errors.append(f"{where}.patterns: bad regex {pat!r}: {exc}")
    url = str(tbl.get("url", ""))
    if backend == "static_url" and not url:
        errors.append(f"{where}: static_url datasets need url")
    if backend in ("manual", "html_index") and not (url or tbl.get("page")):
        errors.append(f"{where}: {backend} datasets need url or page")
    for key in ("url", "page", "terms"):
        val = str(tbl.get(key, ""))
        if val and not val.startswith("https://"):
            errors.append(f"{where}.{key} must be https")
    ref = str(tbl.get("auth", ""))
    if ref and ref not in auth:
        errors.append(f"{where}.auth references unknown [auth.{ref}]")
    redistributable = tbl.get("redistributable", True)
    if not isinstance(redistributable, bool):
        errors.append(f"{where}.redistributable must be a boolean")
    sha = str(tbl.get("sha256", ""))
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        errors.append(f"{where}.sha256 must be 64 lowercase hex digits")
    return DatasetSpec(
        name=name,
        backend=backend,
        description=str(tbl.get("description", "")),
        patterns=patterns,
        categories=_str_tuple(tbl.get("categories"), f"{where}.categories", errors),
        companions=_str_tuple(tbl.get("companions"), f"{where}.companions", errors),
        unwrap=_str_tuple(tbl.get("unwrap"), f"{where}.unwrap", errors),
        date_rule=date_rule,
        multi=bool(tbl.get("multi", False)),
        url=url,
        page=str(tbl.get("page", "")),
        filename=str(tbl.get("filename", "")),
        terms=str(tbl.get("terms", "")),
        redistributable=bool(redistributable),
        consumer=str(tbl.get("consumer", "")),
        auth=ref,
        sha256=sha,
        notes=str(tbl.get("notes", "")),
    )


def _parse(
    data: dict[str, Any], errors: list[str], path: Path | None = None
) -> SourceRegistry:
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION} (got {version!r})")
    unknown = set(data) - {"schema_version", "operator", "auth", "endpoint", "dataset"}
    if unknown:
        errors.append(f"unknown top-level table(s) {sorted(unknown)}")
    operator = _parse_operator(data, errors)
    auth = _parse_auth(data, errors)
    endpoints = _parse_endpoints(data, auth, errors)
    datasets: dict[str, DatasetSpec] = {}
    for name, tbl in (data.get("dataset") or {}).items():
        if not isinstance(tbl, dict):
            errors.append(f"[dataset.{name}] must be a table")
            continue
        datasets[name] = _parse_dataset(name, tbl, auth, errors)
    return SourceRegistry(
        operator=operator,
        auth=auth,
        endpoints=endpoints,
        datasets=datasets,
        path=path,
    )


def parse_sources(data: dict[str, Any], path: Path | None = None) -> SourceRegistry:
    """Validate and build a :class:`SourceRegistry` from parsed TOML."""
    errors: list[str] = []
    reg = _parse(data, errors, path)
    if errors:
        where = f" ({path})" if path else ""
        raise RegistryError(f"invalid sources.toml{where}:\n  " + "\n  ".join(errors))
    return reg


def load_sources_file(path: Path) -> SourceRegistry:
    """Parse and validate one ``sources.toml`` file."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_sources(data, path)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _package_sources(package: str) -> Path:
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise RegistryError(f"cannot locate operator package {package!r}")
    return Path(next(iter(spec.submodule_search_locations))) / "sources.toml"


def _entry_point_packages() -> dict[str, str]:
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        return {}
    return {ep.name: ep.value.split(":")[0] for ep in eps}


@cache
def operator_packages() -> dict[str, str]:
    """Map operator id -> package holding its ``sources.toml``."""
    out = {op: f"gtopt_operators.{op}" for op in BUILTIN_OPERATORS}
    out.update(_entry_point_packages())
    return out


def available_operators() -> list[str]:
    """Sorted ids of every registered operator."""
    return sorted(operator_packages())


@cache
def load_registry(operator: str) -> SourceRegistry:
    """Return the validated registry of ``operator`` (cached)."""
    packages = operator_packages()
    if operator not in packages:
        raise RegistryError(
            f"unknown operator {operator!r}; known: {', '.join(sorted(packages))}"
        )
    reg = load_sources_file(_package_sources(packages[operator]))
    if reg.operator.id != operator:
        raise RegistryError(
            f"{reg.path}: [operator].id is {reg.operator.id!r}, expected {operator!r}"
        )
    return reg


def endpoint_url(operator: str, name: str) -> str:
    """Shortcut: URL of endpoint ``name`` of ``operator``."""
    return load_registry(operator).endpoint_url(name)
