"""ZIP / case JSON build + parse helpers for the GTOpt GUI service.

Exports:

* ``_sanitize_value`` — coerce a single value to a JSON-safe type.
* ``_df_to_rows``     — convert a Parquet/CSV DataFrame to GUI rows.
* ``_build_case_json``— stitch options + system + simulation + time-series.
* ``_build_zip``      — serialise a case dict + assets into a ZIP.
* ``_parse_uploaded_zip`` — round-trip a ZIP back into the case dict.
* ``_parse_results_zip``  — extract results parquet/csv → JSON-friendly dict.

Imported by :mod:`guiservice.app` and re-exported for back-compat with
existing tests (``from guiservice.app import _build_case_json``).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import math
import os
import re
import zipfile
from base64 import b64decode, b64encode
from typing import TYPE_CHECKING, Any

import pandas as pd

# Dual-form import: ``from guiservice._schemas`` works when imported as a
# package (tests, ``from guiservice.app import …`` callers); the bare
# ``from _schemas`` fallback works when ``app.py`` is launched as a
# script with ``cwd=guiservice/`` by the ``gtopt_gui`` launcher.
# The ``TYPE_CHECKING`` guard hides the fallback branch from mypy so the
# symbol isn't flagged as ``no-redef``.
if TYPE_CHECKING:
    from guiservice._schemas import ELEMENT_TO_ARRAY_KEY, nest_model_options
else:
    try:
        from guiservice._schemas import ELEMENT_TO_ARRAY_KEY, nest_model_options
    except ImportError:
        from _schemas import ELEMENT_TO_ARRAY_KEY, nest_model_options

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------


def _sanitize_value(x):
    """Convert a single value to a JSON-safe Python type.

    Numpy scalars are converted via ``.item()``.  Float ``NaN`` and
    ``Inf`` values become ``None`` so they serialize as JSON ``null``
    instead of the non-standard ``NaN`` / ``Infinity`` tokens.
    """
    val = x.item() if hasattr(x, "item") else x
    if isinstance(val, float) and not math.isfinite(val):
        return None
    return val


def _df_to_rows(df):
    """Convert a DataFrame to a list of rows preserving column types.

    Using ``df.values.tolist()`` coerces all columns to a single numpy
    dtype (e.g. float64 when int and float columns are mixed), which
    turns integer values into floats.  Converting per-row via
    ``itertuples`` keeps each value in its native Python type.

    Float ``NaN`` and ``Inf`` values are replaced with ``None`` to
    produce valid JSON (the JSON specification does not allow ``NaN``).
    """
    return [[_sanitize_value(x) for x in row] for row in df.itertuples(index=False, name=None)]


def unmapped_system_arrays(system_raw: dict) -> dict:
    """``*_array`` entries of a planning ``system`` the GUI has no form for."""
    mapped = set(ELEMENT_TO_ARRAY_KEY.values())
    return {
        key: value
        for key, value in system_raw.items()
        if key.endswith("_array") and key not in mapped
    }


def _build_case_json(case_data):
    """Build the gtopt-compatible JSON from the GUI case data."""
    options = case_data.get("options", {})
    simulation = case_data.get("simulation", {})
    system_elements = case_data.get("system", {})

    system_section = {"name": case_data.get("case_name", "case")}
    # System arrays the GUI has no form for (loaded from a planning file)
    # are passed through untouched instead of being dropped.
    for array_key, items in (case_data.get("system_extra") or {}).items():
        if items:
            system_section[array_key] = items
    for elem_type, array_key in ELEMENT_TO_ARRAY_KEY.items():
        items = system_elements.get(elem_type, [])
        if items:
            cleaned = []
            for item in items:
                entry = {}
                for k, v in item.items():
                    if v is None or v == "":
                        continue
                    entry[k] = v
                cleaned.append(entry)
            system_section[array_key] = cleaned

    result = {}
    if options:
        # Model options belong under options.model_options.
        result["options"] = nest_model_options(options)
    if simulation:
        result["simulation"] = simulation
    result["system"] = system_section
    return result


def _build_zip(case_data):
    """Build a ZIP file containing the full case configuration.

    This is used when creating cases from the GUI editor.  When a case
    was loaded via upload, ``submit_solve`` forwards the original ZIP
    instead of calling this function.
    """
    case_name = case_data.get("case_name", "case")
    case_json = _build_case_json(case_data)

    input_dir = case_data.get("options", {}).get("input_directory", case_name)
    input_format = case_data.get("options", {}).get("input_format", "csv")

    # Ensure the JSON has input_directory matching the data file prefix
    # so that gtopt can find the data files after zip extraction.
    if "options" not in case_json:
        case_json["options"] = {}
    case_json["options"]["input_directory"] = input_dir

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Main JSON config
        zf.writestr(
            f"{case_name}.json",
            json.dumps(case_json, indent=2),
        )

        # External data files
        data_files = case_data.get("data_files", {})
        for file_path, file_content in data_files.items():
            if input_format == "parquet":
                raw_b64 = file_content.get("_raw_parquet_b64")
                if raw_b64:
                    zf.writestr(
                        f"{input_dir}/{file_path}.parquet",
                        b64decode(raw_b64),
                    )
                else:
                    df = pd.DataFrame(file_content["data"], columns=file_content["columns"])
                    parquet_buf = io.BytesIO()
                    df.to_parquet(parquet_buf, index=False)
                    zf.writestr(
                        f"{input_dir}/{file_path}.parquet",
                        parquet_buf.getvalue(),
                    )
            else:
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(file_content["columns"])
                for row in file_content["data"]:
                    writer.writerow(row)
                zf.writestr(
                    f"{input_dir}/{file_path}.csv",
                    output.getvalue(),
                )

    buf.seek(0)
    return buf


def _parse_uploaded_zip(zip_bytes):
    """Parse an uploaded ZIP file into case data structure.

    The original ZIP bytes are preserved (base64-encoded) so that
    ``submit_solve`` can forward them to the webservice unchanged.
    """
    case_data: dict[str, Any] = {
        "case_name": "uploaded_case",
        "options": {},
        "simulation": {},
        "system": {},
        "data_files": {},
    }

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        json_files = [n for n in zf.namelist() if n.endswith(".json")]
        if not json_files:
            return case_data

        # Parse the main JSON
        main_json_name = json_files[0]
        case_data["case_name"] = os.path.splitext(os.path.basename(main_json_name))[0]

        raw = json.loads(zf.read(main_json_name))
        case_data["options"] = raw.get("options", {})
        case_data["simulation"] = raw.get("simulation", {})

        # Parse system elements
        system_raw = raw.get("system", {})
        for elem_type, array_key in ELEMENT_TO_ARRAY_KEY.items():
            if array_key in system_raw:
                case_data["system"][elem_type] = system_raw[array_key]
        case_data["system_extra"] = unmapped_system_arrays(system_raw)

        # Parse data files (CSV and Parquet) into JSON for GUI display;
        # original ZIP bytes are preserved separately for submission.
        for name in zf.namelist():
            if name.endswith(".csv") and not name.endswith(".json"):
                rel = name
                # Strip input_directory prefix if present
                input_dir = case_data["options"].get("input_directory", "")
                if input_dir and rel.startswith(input_dir + "/"):
                    rel = rel[len(input_dir) + 1 :]
                if rel.endswith(".csv"):
                    rel = rel[:-4]
                try:
                    content = zf.read(name).decode("utf-8")
                    reader = csv.reader(io.StringIO(content))
                    rows = list(reader)
                    if rows:
                        case_data["data_files"][rel] = {
                            "columns": rows[0],
                            "data": rows[1:],
                        }
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to parse CSV file %s: %s", name, e)

            elif name.endswith(".parquet"):
                rel = name
                input_dir = case_data["options"].get("input_directory", "")
                if input_dir and rel.startswith(input_dir + "/"):
                    rel = rel[len(input_dir) + 1 :]
                if rel.endswith(".parquet"):
                    rel = rel[:-8]
                try:
                    raw_bytes = zf.read(name)
                    df = pd.read_parquet(io.BytesIO(raw_bytes))
                    case_data["data_files"][rel] = {
                        "columns": list(df.columns),
                        "data": _df_to_rows(df),
                        "_raw_parquet_b64": b64encode(raw_bytes).decode("ascii"),
                    }
                except Exception as e:  # pylint: disable=broad-exception-caught
                    logger.warning("Failed to parse Parquet file %s: %s", name, e)

        # Store the system file name for passthrough submission
        case_data["_system_file"] = main_json_name

    # Preserve the original ZIP so submit can forward it unmodified
    case_data["_uploaded_zip_b64"] = b64encode(zip_bytes).decode("ascii")

    return case_data


def _parse_solution_rows(rows: list[list[str]]) -> dict[str, str]:
    """Flatten ``solution.csv`` records into ``{column: value}``.

    Mirrors :func:`gtopt_shared.solution_csv.parse_solution_rows` (kept
    inline so the GUI service does not require the scripts package): the
    current columnar layout (header + one row per ``(scene, phase)`` cell;
    the first data row is returned) and the legacy ``key,value`` layout.
    """
    rows = [r for r in rows if r and any(f.strip() for f in r)]
    if not rows:
        return {}
    header = [f.strip() for f in rows[0]]
    if "obj_value" in header and len(header) > 2:
        if len(rows) < 2:
            return {}
        return {name: value.strip() for name, value in zip(header, rows[1], strict=False) if name}
    return {r[0].strip(): r[1].strip() for r in rows if len(r) >= 2}


# ---------------------------------------------------------------------------
# Results parsing
# ---------------------------------------------------------------------------

# gtopt writes one CSV shard per (scene, phase): ``<stem>_s<N>_p<M>.csv``.
_SHARD_SUFFIX_RE = re.compile(r"_s\d+_p\d+$")
_CSV_EXTS = (".csv.gz", ".csv.zst", ".csv")
_LOG_FILES = ("gtopt_terminal.log", "stdout.log", "stderr.log")
# Hive partition columns pandas/pyarrow may materialise when a whole
# ``<stem>.parquet/`` dataset is read; they duplicate scenario/stage.
_PARTITION_COLS = ("scene", "phase")
_INDEX_ORDER = ("scenario", "stage", "block")


def _natural_key(text: str) -> list[Any]:
    """Sort key ordering ``_s2_p0`` before ``_s10_p0``."""
    return [int(tok) if tok.isdigit() else tok for tok in re.split(r"(\d+)", text)]


def _output_key(name: str) -> str | None:
    """Map a ZIP member path to its ``<Class>/<stem>`` output key.

    Handles the three layouts gtopt has produced:

    * legacy single file:  ``<Class>/<stem>.{csv,csv.gz,parquet}``
    * CSV shards:          ``<Class>/<stem>_s<N>_p<M>.{csv,csv.gz,csv.zst}``
    * hive Parquet:        ``<Class>/<stem>.parquet/scene=N/phase=M/part.parquet``

    Returns ``None`` for members that are not output tables.
    """
    parts = [p for p in name.split("/") if p]
    if not parts:
        return None
    # Hive-partitioned dataset: a *directory* component ends in .parquet.
    for i, part in enumerate(parts[:-1]):
        if part.endswith(".parquet"):
            stem = part[: -len(".parquet")]
            parent = parts[i - 1] if i > 0 else ""
            return f"{parent}/{stem}" if parent else stem
    base = parts[-1]
    for ext in (*_CSV_EXTS, ".parquet"):
        if base.endswith(ext):
            stem = _SHARD_SUFFIX_RE.sub("", base[: -len(ext)])
            parent = parts[-2] if len(parts) > 1 else ""
            return f"{parent}/{stem}" if parent else stem
    return None


def _decode_csv_member(name: str, raw: bytes) -> list[list[str]] | None:
    """Decompress (if needed) and parse a CSV member into rows."""
    if name.endswith(".csv.gz"):
        raw = gzip.decompress(raw)
    elif name.endswith(".csv.zst"):
        try:
            import zstandard  # pylint: disable=import-outside-toplevel
        except ImportError:
            logger.warning("Skipping %s: the 'zstandard' module is not installed", name)
            return None
        raw = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read()
    return list(csv.reader(io.StringIO(raw.decode("utf-8"))))


def _is_long_columns(columns: list[Any]) -> bool:
    """True for gtopt's long output layout (``uid`` + ``value`` columns,
    no per-element ``uid:N`` columns)."""
    names = {str(c) for c in columns}
    return "uid" in names and "value" in names and not any(c.startswith("uid:") for c in names)


def _uid_label(uid: Any) -> str:
    if isinstance(uid, float) and uid.is_integer():
        uid = int(uid)
    return f"uid:{uid}"


def _pivot_long(df: pd.DataFrame) -> pd.DataFrame | None:
    """Pivot a long ``[<index cols>, uid, value]`` table to wide ``uid:N``.

    gtopt omits zero-valued rows in the long layout, so (index, uid) cells
    that are absent are filled with ``0``; explicit nulls in ``value``
    stay null.  Returns ``None`` when there is no index column to pivot on.
    """
    df = df.drop(columns=[c for c in _PARTITION_COLS if c in df.columns])
    idx = [c for c in _INDEX_ORDER if c in df.columns]
    idx += [c for c in df.columns if c not in idx and c not in ("uid", "value")]
    if not idx:
        return None
    for col in (*idx, "uid", "value"):
        converted = pd.to_numeric(df[col], errors="coerce")
        # Keep non-numeric index labels as-is; empty CSV cells → NaN.
        if col == "value" or converted.notna().sum() == df[col].notna().sum():
            df[col] = converted
    df = df.dropna(subset=["uid"])
    df = df.drop_duplicates(subset=[*idx, "uid"], keep="first")
    wide = df.set_index([*idx, "uid"])["value"].unstack("uid", fill_value=0)
    wide.columns = [_uid_label(c) for c in wide.columns]
    return wide.reset_index()


def _merge_output_entries(entries: list[tuple[list[Any], list[list[Any]]]]) -> dict[str, Any]:
    """Combine the (columns, rows) tables collected for one output key.

    * one legacy wide table → returned untouched;
    * long tables (any number of scene/phase shards) → concatenated and
      pivoted to the wide ``uid:N`` layout the plotting code expects;
    * several wide shards → concatenated (union of columns).
    """
    if len(entries) == 1 and not _is_long_columns(entries[0][0]):
        cols, data = entries[0]
        return {"columns": list(cols), "data": data}

    frames = [pd.DataFrame(data, columns=cols) for cols, data in entries]
    df = pd.concat(frames, ignore_index=True, sort=False)
    if all(_is_long_columns(cols) for cols, _ in entries):
        wide = _pivot_long(df)
        if wide is not None:
            return {"columns": list(wide.columns), "data": _df_to_rows(wide)}
    df = df.astype(object).where(df.notna(), None)
    return {"columns": list(df.columns), "data": _df_to_rows(df)}


def _parse_results_zip(zip_bytes):
    """Parse a results ZIP into viewable data.

    Output tables are keyed ``<Class>/<stem>`` (e.g.
    ``Generator/generation_sol``).  Both the legacy wide layout (one
    ``uid:N`` column per element) and the current long layout
    (``scenario,stage,block,uid,value``, split into per-(scene, phase)
    CSV shards or a hive-partitioned Parquet dataset) are accepted; long
    tables are returned pivoted to the wide layout.
    """
    results: dict[str, Any] = {"solution": {}, "outputs": {}, "terminal_output": ""}
    collected: dict[str, list[tuple[str, list[Any], list[list[Any]]]]] = {}

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            # Collect terminal / log files so they can be shown in the GUI
            base = os.path.basename(name)
            if base in _LOG_FILES:
                try:
                    content = zf.read(name).decode("utf-8", errors="replace")
                    if content.strip():
                        if results["terminal_output"]:
                            results["terminal_output"] += "\n"
                        results["terminal_output"] += content
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                continue

            key = _output_key(name)
            if key is None:
                continue
            try:
                if name.endswith(".parquet"):
                    df = pd.read_parquet(io.BytesIO(zf.read(name)))
                    cols: list[Any] = list(df.columns)
                    data: list[list[Any]] = _df_to_rows(df)
                else:
                    rows = _decode_csv_member(name, zf.read(name))
                    if not rows:
                        continue
                    cols, data = rows[0], rows[1:]
                    if base in ("solution.csv", "solution.csv.gz"):
                        results["solution"] = _parse_solution_rows(rows)
                        continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning("Failed to parse results file %s: %s", name, exc)
                continue
            collected.setdefault(key, []).append((name, cols, data))

    for key, entries in collected.items():
        entries.sort(key=lambda e: _natural_key(e[0]))
        try:
            results["outputs"][key] = _merge_output_entries([(c, d) for _, c, d in entries])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to merge results for %s: %s", key, exc)

    return results
