"""Edge-case tests for ts2gtopt: interval inference, conservation check,
hourly-output reconstruction and CLI error paths."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from ts2gtopt.main import main
from ts2gtopt.ts2gtopt import (
    _infer_interval_hours,
    _read_output_table,
    build_hour_block_map,
    energy_conservation_check,
    make_horizon,
    reconstruct_output_hours,
)


def _main(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    code = exc.value.code
    return 0 if code is None else int(code)


def _hourly_csv(path: Path, year: int = 2023, hours: int = 8760) -> Path:
    idx = pd.date_range(f"{year}-01-01", periods=hours, freq="h")
    pd.DataFrame({"datetime": idx, "uid:1": 1.0}).to_csv(path, index=False)
    return path


# ── interval inference ──────────────────────────────────────────────────


def test_infer_interval_hours_all_nat() -> None:
    df = pd.DataFrame({"t": [None, None, None]})
    assert _infer_interval_hours(df, "t") == 1.0


def test_infer_interval_hours_quarter_hourly() -> None:
    df = pd.DataFrame({"t": pd.date_range("2023-01-01", periods=4, freq="15min")})
    assert _infer_interval_hours(df, "t") == pytest.approx(0.25)


# ── energy conservation ─────────────────────────────────────────────────


def test_energy_conservation_zero_original_energy() -> None:
    original = pd.DataFrame({"uid:1": [0.0, 0.0], "uid:2": [0.0, 0.0]})
    sched = pd.DataFrame(
        {"uid:1": [0.0], "uid:2": [5.0], "_duration": [2.0]},
    )
    ratios = energy_conservation_check(original, sched, interval_hours=1.0)
    assert ratios["uid:1"] == 1.0
    assert ratios["uid:2"] == float("inf")


# ── output-hour reconstruction ──────────────────────────────────────────


def _hour_map() -> list[dict]:
    horizon = make_horizon(2023, n_stages=1, n_blocks=2, interval_hours=1.0)
    return build_hour_block_map(horizon, year=2023)


def test_read_output_table_unreadable_and_missing_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet")
    assert _read_output_table(bad) is None
    nocols = tmp_path / "x.csv"
    pd.DataFrame({"a": [1]}).to_csv(nocols, index=False)
    assert _read_output_table(nocols) is None


def test_reconstruct_skips_unusable_tables(tmp_path: Path) -> None:
    out = tmp_path / "output"
    gen = out / "Generator"
    gen.mkdir(parents=True)
    # only index columns -> no uid columns
    pd.DataFrame({"scenario": [1], "stage": [1], "block": [1]}).to_csv(
        gen / "index_only.csv", index=False
    )
    # (stage, block) not present in the hour map -> empty merge
    pd.DataFrame({"scenario": [1], "stage": [9], "block": [9], "uid:1": [1.0]}).to_csv(
        gen / "unmatched.csv", index=False
    )
    (gen / "broken.parquet").write_bytes(b"junk")
    # a good parquet table is still reconstructed
    pd.DataFrame(
        {"scenario": [1], "stage": [1], "block": [1], "uid:1": [2.0]}
    ).to_parquet(gen / "good.parquet", index=False)
    written = reconstruct_output_hours(out, _hour_map(), output_format="parquet")
    assert list(written) == ["Generator/good.parquet"]
    hourly = pd.read_parquet(written["Generator/good.parquet"])
    assert set(hourly["uid:1"]) == {2.0}


def test_reconstruct_rejects_bad_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output_format must be"):
        reconstruct_output_hours(tmp_path, [], output_format="xlsx")


# ── CLI ─────────────────────────────────────────────────────────────────


def test_cli_requires_input(capsys) -> None:
    assert _main(["-y", "2023"]) == 2
    assert "required: FILE" in capsys.readouterr().err


def test_cli_planning_requires_year(tmp_path: Path, capsys) -> None:
    csv = _hourly_csv(tmp_path / "ts.csv")
    planning = tmp_path / "p.json"
    planning.write_text("{}")
    assert _main([str(csv), "-P", str(planning)]) == 2
    assert "--planning requires --year" in capsys.readouterr().err


def test_cli_year_taken_from_horizon_file(tmp_path: Path) -> None:
    csv = _hourly_csv(tmp_path / "ts.csv")
    horizon = make_horizon(2023, n_stages=2, n_blocks=2, interval_hours=1.0)
    horizon["year"] = 2023
    hfile = tmp_path / "h.json"
    hfile.write_text(json.dumps(horizon))
    out = tmp_path / "out"
    assert _main([str(csv), "-H", str(hfile), "-o", str(out)]) == 0
    assert list(out.rglob("*.parquet")) or list(out.rglob("*.csv"))


def test_cli_conversion_error_exits_one(tmp_path: Path, caplog) -> None:
    missing = tmp_path / "missing.csv"
    assert _main([str(missing), "-y", "2023", "-o", str(tmp_path / "o")]) == 1
    assert caplog.records


def test_write_bat4b_case_end_to_end(tmp_path: Path) -> None:
    """Unit-level (non-integration) run of the bat_4b annual case writer."""
    from ts2gtopt.cases.bat4b_annual import (  # noqa: PLC0415  pylint: disable=import-outside-toplevel
        write_bat4b_case,
    )

    json_path = write_bat4b_case(tmp_path, 2023)
    case = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_path.name == "bat_4b_2023.json"
    assert case["options"]["input_directory"] == "input"
    assert len(case["simulation"]["stage_array"]) == 12
    assert case["hour_block_map"]
    for rel in ("Demand/lmax", "Generator/pmax", "GeneratorProfile/profile"):
        assert (tmp_path / "input" / f"{rel}.parquet").is_file()
    profile = pd.read_parquet(
        tmp_path / "input" / "GeneratorProfile" / "profile.parquet"
    )
    assert "uid:3" not in profile.columns
