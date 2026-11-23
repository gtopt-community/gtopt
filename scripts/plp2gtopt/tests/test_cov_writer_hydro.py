"""Edge branches of :class:`plp2gtopt._writer_hydro.HydroMixin` helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from plp2gtopt import inflow_model
from plp2gtopt.gtopt_writer import GTOptWriter


def _writer(parsed: dict[str, Any]) -> GTOptWriter:
    return GTOptWriter(SimpleNamespace(parsed_data=parsed))  # type: ignore[arg-type]


def _pasada(num: int, name: str, afluent: float) -> dict[str, Any]:
    return {"number": num, "name": name, "afluent": afluent, "efficiency": 2.0}


class _Aflce:
    def __init__(self, names: set[str]) -> None:
        self.names = names

    def get_item_by_name(self, name: str) -> Any:
        return {"name": name} if name in self.names else None


def test_flow_turbines_early_returns() -> None:
    opts: dict[str, Any] = {"_pasada_hydro_names": {"P"}}
    w = _writer({})
    w.process_flow_turbines(opts)
    w = _writer({"central_parser": SimpleNamespace(centrals_of_type={})})
    w.process_flow_turbines(opts)
    w = _writer({"central_parser": SimpleNamespace(centrals_of_type={"pasada": []})})
    w.process_flow_turbines(opts)
    assert "flow_array" not in w.planning["system"]


def test_flow_turbines_ar1_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    models = iter([{"phi": 0.5}, None])
    monkeypatch.setattr(
        inflow_model, "estimate_ar1_from_aflce", lambda _i, _b: next(models)
    )
    cot = {
        "pasada": [
            _pasada(1, "OUT", 3.0),  # not routed to the hydro path
            _pasada(2, "ZERO", 0.0),  # no aflce, zero afluent -> skipped
            _pasada(3, "A", 0.0),
            _pasada(4, "B", 0.0),
            _pasada(5, "C", 4.0),  # scalar afluent, not in aflce
        ]
    }
    w = _writer(
        {
            "central_parser": SimpleNamespace(centrals_of_type=cot),
            "aflce_parser": _Aflce({"A", "B"}),
        }
    )
    w.process_flow_turbines(
        {
            "_pasada_hydro_names": {"ZERO", "A", "B", "C"},
            "_aflce_emitted_uids": {3, 4},
            "inflow_model": "ar1",
        }
    )
    flows = {f["name"]: f for f in w.planning["system"]["flow_array"]}
    assert set(flows) == {"A", "B", "C"}
    assert flows["A"]["inflow_model"] == {"phi": 0.5}
    assert "inflow_model" not in flows["B"]
    assert flows["C"]["discharge"] == pytest.approx(4.0)
    turbines = w.planning["system"]["turbine_array"]
    assert {t["name"] for t in turbines} == {"A", "B", "C"}


def test_inject_seepage_ref_variants() -> None:
    w = _writer({})
    cfg: dict[str, Any] = {}
    w._inject_seepage_ref(cfg, "LAKE")  # pylint: disable=protected-access
    assert not cfg  # no seepage element -> static fallback
    w.planning["system"]["reservoir_seepage_array"] = [
        {"name": "S1", "reservoir": "LAKE", "slope": -0.5, "constant": 1.0},
        {
            "name": "S2",
            "reservoir": "POND",
            "segments": [
                {"volume": 0.0, "slope": 0.01, "constant": 1.0},
                {"volume": 50.0, "slope": 0.02, "constant": 2.0},
                {"volume": 500.0, "slope": 0.03, "constant": 3.0},
            ],
        },
    ]
    w.planning["system"]["reservoir_array"] = [
        {"name": "LAKE", "emax": "emax"},
        {"name": "POND", "emax": 100.0},
    ]
    w._inject_seepage_ref(cfg, "LAKE")  # pylint: disable=protected-access
    assert cfg == {"seepage_ref": "S1", "seepage_flow_ub": pytest.approx(1.0)}
    w._inject_seepage_ref(cfg, "POND")  # pylint: disable=protected-access
    assert cfg["seepage_ref"] == "S2"
    assert cfg["seepage_flow_ub"] == pytest.approx(4.0)


def test_scenario_mean_series() -> None:
    f = GTOptWriter._scenario_mean_series  # pylint: disable=protected-access
    assert f(None) is None
    assert f({"first": [1.0], "by_scenario": []}) == [1.0]
    assert f({"first": [], "by_scenario": [[1.0, 2.0], [3.0, 4.0, 5.0]]}) == [
        2.0,
        3.0,
    ]
    assert f((1.0, 2.0)) == [1.0, 2.0]


@pytest.mark.parametrize("spec", [None, False, "/nonexistent/whitelist.csv"])
def test_pmin_flowright_opt_out_and_bad_spec(spec: Any) -> None:
    w = _writer({})
    w.process_pmin_flowright({"pmin_as_flowright": spec})
    assert "flow_right_array" not in w.planning["system"]
