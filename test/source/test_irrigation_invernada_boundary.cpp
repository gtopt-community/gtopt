// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_irrigation_invernada_boundary.cpp
 * @brief     Tier 8.6b — PLP IQNINV/IQRNIN level rule of La Invernada
 * @date      2026-04-11
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * PLP genpdmaule.f:433-460 models the La Invernada level rule as two
 * HARD rows, active only in "deficit" stages (FijaMaule FInvernDesemb):
 *
 *   IQNINV:  qninv + filt                         = QAflInvern
 *   IQRNIN: -qidn + qninv - qhinv + qhnein + qhein = 0
 *
 * gtopt substitutes IQNINV into IQRNIN (no free-sign qninv column):
 *
 *   qidn + qhinv + filt = QAflInvern + qhnein + qhein
 *
 * with qhinv (storing natural inflow) priced CostoEmbalsar = 1500,
 * qhnein (not storing although allowed) priced 1000 and qhein (release
 * above the natural inflow) FREE.  Turbining is never taxed: the old
 * soft `invernada_balance` (5 FlowRights + priced slack on a balance
 * row) charged ~1000 $/m3/s on every turbined m3/s and kept CIPRESES
 * idle.  These tests pin the PLP semantics:
 *
 *   1. CIPRESES turbines (25 m3/s) when profitable, at zero agreement
 *      cost (release above inflow goes to the free qhein column);
 *   2. storing the natural inflow in a deficit stage costs 1500 $/m3/s·h;
 *   3. outside the deficit stages (rows not emitted) storing is free.
 *
 * The hydraulic fixture is a one-reservoir copy of the Tier 8.6 backbone
 * (test_irrigation_user_constraints.cpp) plus a natural-inflow Flow.
 */

#include <doctest/doctest.h>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

// ─────────────────────────────────────────────────────────────────────────
// Fixture — identical hydraulic backbone as Tier 8.6
// (test_irrigation_user_constraints.cpp lines 33-106).  Copy-pasted
// rather than shared-headered to minimize refactor blast radius.
// ─────────────────────────────────────────────────────────────────────────
struct BoundaryUCFixture
{
  Array<Bus> bus_array {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  Array<Generator> generator_array {
      {
          .uid = Uid {1},
          .name = "hydro",
          .bus = Uid {1},
          .gcost = 0.0,
          .capacity = 200.0,
      },
      {
          .uid = Uid {2},
          .name = "thermal",
          .bus = Uid {1},
          .gcost = 100.0,
          .capacity = 200.0,
      },
  };
  Array<Demand> demand_array {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .lmax = 50.0,
          .fcost = 1000.0,
          .capacity = 50.0,
      },
  };
  Array<Junction> junction_array {
      {
          .uid = Uid {1},
          .name = "j_up",
      },
      {
          .uid = Uid {2},
          .name = "j_down",
          .drain = true,
      },
  };
  Array<Waterway> waterway_array {
      {
          .uid = Uid {1},
          .name = "ww",
          .junction_a = Uid {1},
          .junction_b = Uid {2},
          .fmin = 0.0,
          .fmax = 200.0,
      },
  };
  Array<Reservoir> reservoir_array {
      {
          .uid = Uid {1},
          .name = "rsv",
          .junction = Uid {1},
          .capacity = 200.0,
          .emin = 0.0,
          .emax = 200.0,
          .eini = 100.0,
      },
  };
  /// Natural inflow of the reservoir (QAflInvern) [m3/s].
  Array<Flow> flow_array {
      {
          .uid = Uid {1},
          .name = "afl",
          .direction = 1,
          .junction = Uid {1},
          .discharge = 10.0,
      },
  };
  Array<Turbine> turbine_array {
      {
          .uid = Uid {1},
          .name = "tur",
          .waterway = Uid {1},
          .generator = Uid {1},
          .production_factor = 2.0,
      },
  };
};

[[nodiscard]] Simulation make_boundary_uc_simulation()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 24,
              },
          },
      .stage_array =
          {
              {
                  .uid = Uid {1},
                  .first_block = 0,
                  .count_block = 1,
              },
          },
      .scenario_array =
          {
              {
                  .uid = Uid {0},
              },
          },
  };
}

[[nodiscard]] System make_boundary_uc_system(const BoundaryUCFixture& fx,
                                             Array<VolumeRight> vrs,
                                             Array<FlowRight> frs,
                                             Array<UserConstraint> ucs,
                                             std::string name)
{
  return {
      .name = std::move(name),
      .bus_array = fx.bus_array,
      .demand_array = fx.demand_array,
      .generator_array = fx.generator_array,
      .junction_array = fx.junction_array,
      .waterway_array = fx.waterway_array,
      .flow_array = fx.flow_array,
      .reservoir_array = fx.reservoir_array,
      .turbine_array = fx.turbine_array,
      .flow_right_array = std::move(frs),
      .volume_right_array = std::move(vrs),
      .user_constraint_array = std::move(ucs),
  };
}

// The PLP La Invernada level-rule rights, as emitted by maule.tson
// (RelaxInvern = F).  `deficit` is qidn, `sin_deficit` qisd (gated
// to 0 in deficit stages), `embalsar` qhinv, `no_embalsar` qhnein,
// `holgura` qhein (qninv substituted, filtration omitted here).
[[nodiscard]] Array<FlowRight> make_invernada_rights(Real sin_deficit_fmax)
{
  constexpr Real kQmax = 200.0;
  return {
      {
          .uid = Uid {1},
          .name = "inv_deficit",
          .direction = 1,
          .fmax = kQmax,
      },
      {
          .uid = Uid {2},
          .name = "inv_sin_deficit",
          .direction = 1,
          .fmax = sin_deficit_fmax,
      },
      {
          .uid = Uid {4},
          .name = "inv_embalsar",
          .direction = 1,
          .fmax = kQmax,
          .target = 0.0,
          .uvalue = -1500.0,
      },
      {
          .uid = Uid {5},
          .name = "inv_no_embalsar",
          .direction = 1,
          .fmax = kQmax,
          .target = 0.0,
          .uvalue = -1000.0,
      },
      {
          .uid = Uid {6},
          .name = "inv_holgura",
          .direction = 1,
          .fmax = kQmax,
      },
  };
}

/// Anchor (always active): qidn + qisd = turbined flow of CIPRESES.
/// With `level_rule` the hard IQRNIN row (IQNINV substituted) is added.
[[nodiscard]] Array<UserConstraint> make_invernada_ucs(bool level_rule)
{
  Array<UserConstraint> ucs {
      {
          .uid = Uid {1},
          .name = "invernada_anclaje",
          .expression =
              R"(flow_right("inv_deficit").flow + flow_right("inv_sin_deficit").flow = waterway("ww").flow)",
          .constraint_type = "raw",
      },
  };
  if (level_rule) {
    ucs.push_back({
        .uid = Uid {3},
        .name = "invernada_nivel",
        .expression =
            R"(flow_right("inv_deficit").flow + flow_right("inv_embalsar").flow = flow("afl").flow + flow_right("inv_no_embalsar").flow + flow_right("inv_holgura").flow)",
        .constraint_type = "raw",
    });
  }
  return ucs;
}

struct InvernadaSolve
{
  Real obj {0.0};
  Real turbined {0.0};
  Real embalsar {0.0};
  Real holgura {0.0};
};

[[nodiscard]] InvernadaSolve solve_invernada(Real demand_mw,
                                             Real sin_deficit_fmax,
                                             bool level_rule)
{
  BoundaryUCFixture fx;
  fx.demand_array[0].lmax = demand_mw;
  const auto system =
      make_boundary_uc_system(fx,
                              {},
                              make_invernada_rights(sin_deficit_fmax),
                              make_invernada_ucs(level_rule),
                              "Tier8_6b_Invernada_level_rule");
  const PlanningOptionsLP options;
  const auto sim = make_boundary_uc_simulation();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto&& lp = system_lp.linear_interface();

  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& sc = system_lp.system_context();
  const auto& scenarios = system_lp.scene().scenarios();
  const auto& stages = system_lp.phase().stages();
  REQUIRE(!scenarios.empty());
  REQUIRE(!stages.empty());
  REQUIRE(!stages[0].blocks().empty());
  const auto sc_uid = scenarios[0].uid();
  const auto st_uid = stages[0].uid();
  const auto bk_uid = stages[0].blocks().front().uid();
  const auto sol = lp.get_col_sol();
  const auto value = [&](std::string_view cls, Uid uid)
  {
    const auto col = sc.find_ampl_col(cls, uid, "flow", sc_uid, st_uid, bk_uid);
    REQUIRE(col.has_value());
    return sol[*col];
  };
  return {
      .obj = lp.get_obj_value_raw(),
      .turbined = value("waterway", Uid {1}),
      .embalsar = value("flow_right", Uid {4}),
      .holgura = value("flow_right", Uid {6}),
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Tier 8.6b - Invernada turbines when profitable in a deficit stage")
{
  // Demand 50 MW, pf 2 -> 25 m3/s of turbining displaces 100 $/MWh
  // thermal.  Inflow 10 < 25: the 15 m3/s released above the natural
  // inflow land on the FREE qhein (holgura) column, so the agreement
  // adds no cost and the LP objective is 0 (all-hydro dispatch).
  const auto r = solve_invernada(
      /*demand_mw=*/50.0, /*sin_deficit_fmax=*/0.0, /*level_rule=*/true);
  CHECK(r.turbined == doctest::Approx(25.0));
  CHECK(r.embalsar == doctest::Approx(0.0));
  CHECK(r.holgura == doctest::Approx(15.0));
  CHECK(r.obj == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "Tier 8.6b - Invernada storing the natural inflow costs CostoEmbalsar")
{
  // No demand: the turbine cannot run (no spill outlet), so the whole
  // 10 m3/s inflow is stored -> qhinv = 10 priced 1500 $/(m3/s)/h.
  // Raw objective = 1500 * 10 * 24 h / scale_objective(1000) = 360.
  const auto r = solve_invernada(
      /*demand_mw=*/0.0, /*sin_deficit_fmax=*/0.0, /*level_rule=*/true);
  CHECK(r.turbined == doctest::Approx(0.0));
  CHECK(r.embalsar == doctest::Approx(10.0));
  CHECK(r.obj == doctest::Approx(360.0));
}

TEST_CASE(  // NOLINT
    "Tier 8.6b - Invernada storing is free outside the deficit stages")
{
  // Non-deficit stage: IQRNIN is not emitted (stage domain)
  // and qisd is open, so storing everything costs nothing.
  const auto r = solve_invernada(
      /*demand_mw=*/0.0, /*sin_deficit_fmax=*/200.0, /*level_rule=*/false);
  CHECK(r.turbined == doctest::Approx(0.0));
  CHECK(r.obj == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "Tier 8.6b - Invernada turbining is never taxed (no soft balance)")
{
  // Profitable turbining with the level rule active and inactive must
  // give the same zero objective: the agreement never prices releases.
  const auto with_rule = solve_invernada(50.0, 0.0, true);
  const auto without_rule = solve_invernada(50.0, 200.0, false);
  CHECK(with_rule.obj == doctest::Approx(without_rule.obj));
  CHECK(with_rule.turbined == doctest::Approx(without_rule.turbined));
}
