# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
"""Per-mode minimum-data validation."""

from __future__ import annotations


from gtopt_marginal_units.constants import EXIT_INPUT_ERROR
from gtopt_marginal_units.main import cli


def test_feed_mode_requires_feed_arg(tmp_path):
    code = cli(
        [
            "--input-kind",
            "feed-parquet",
            "--mode",
            "real",
            "--out",
            str(tmp_path / "out.parquet"),
        ]
    )
    assert code == EXIT_INPUT_ERROR


def test_real_reconstruct_with_gtopt_kind_rejected(tmp_path):
    code = cli(
        [
            "--input-kind",
            "gtopt-dir",
            "--mode",
            "real-reconstruct",
            "--out",
            str(tmp_path / "out.parquet"),
        ]
    )
    assert code == EXIT_INPUT_ERROR


def test_simulated_with_feed_kind_rejected(tmp_path):
    feed = tmp_path / "feed.parquet"
    feed.mkdir()
    code = cli(
        [
            "--input-kind",
            "feed-parquet",
            "--mode",
            "simulated",
            "--feed",
            str(feed),
            "--out",
            str(tmp_path / "out.parquet"),
        ]
    )
    assert code == EXIT_INPUT_ERROR


def test_dispatch_without_uid_columns_raises_input_error(tmp_path):
    """A non-empty generation_sol with no recognisable uid layout must be
    rejected up front instead of yielding ``Cells(dispatch=None)``."""
    import pandas as pd  # noqa: PLC0415
    import pytest  # noqa: PLC0415

    from gtopt_marginal_units._gtopt_reader import (  # noqa: PLC0415
        cells_from_gtopt_output,
    )
    from gtopt_marginal_units.errors import InputValidationError  # noqa: PLC0415

    out = tmp_path / "out"
    (out / "Generator").mkdir(parents=True)
    pd.DataFrame({"scenario": [1], "stage": [1], "block": [1], "g10": [50.0]}).to_csv(
        out / "Generator/generation_sol.csv", index=False
    )

    with pytest.raises(InputValidationError, match="uid"):
        cells_from_gtopt_output(out)
