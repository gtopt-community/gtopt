// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Review proof tests (2026-11-22) — Maule agreement: fictitious flows
// and artefacts inherited from / introduced over the PLP formulation.
//
// All items are FIXED; each test asserts the corrected behaviour.
//
//   R1  maule_bocatoma_canelon: target 0 + negative uvalue + NO fmax.
//       FIXED: a target-0 usage cost defaults to an unbounded fmax
//       (PLP CostoCanelon prices an unbounded column,
//       genpdmaule.f:186-202) instead of fmax = target = 0, which
//       pinned the anchored BCanelon generation arc to zero.
//   R2  a global hydro_spill_cost (alias hydro_fail_cost, needed by the
//       pmin_flowright expander) activates fcost on every target-0
//       FlowRight with no own fcost -> fcost-only substitution caps the
//       flow at target = 0 -> the anchored rights partition (and so the
//       physical turbine) is pinned to zero.
//   R2b a global hydro_use_value rewards every target-0 accounting
//       FlowRight (no junction, no water) -> phantom profit.
//       FIXED: the global spill cost applies only to target > 0
//       blocks and the global use value only to rights that withdraw
//       water (junction_a) or have a target > 0; explicit fcost /
//       uvalue are unchanged.
//   R3  Res. 105: qa105 rewarded (ValorRiego105 1100) while the slack
//       district deficit costs only CostoRiegoNS105 1000 -> the LP
//       "delivers" qa105 with no water reaching the district.
//       FIXED: maule_agreement.py prices the shortfall at
//       CostoRiegoNS105 + ValorRiego105 (no reward on undelivered
//       water).
//   R4  maule_compensacion_elec: PLP FijaMaule sets UppMaule(IQMCEH)
//       = 0 outside the normal regime (genpdmaule.f:818 / :827).
//       FIXED: the template now carries the 581 regime gate.

#include <doctest/doctest.h>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/waterway_lp.hpp>

using namespace gtopt;

namespace
{

// One bus, 50 MW demand, free hydro (rsv -> j_up -> ww (pf 2) -> j_down
// drain) vs 100 $/MWh thermal, plus an optional dry junction `j_dry`
// (no inflow) used as a district offtake point.  One 24 h block.
[[nodiscard]] System make_system(Array<FlowRight> frs,
                                 Array<UserConstraint> ucs,
                                 std::string name,
                                 Real eini = 100.0)
{
  return {
      .name = std::move(name),
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = 50.0,
              },
          },
      .generator_array =
          {
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
          },
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_up",
              },
              {
                  .uid = Uid {2},
                  .name = "j_down",
                  .drain = true,
              },
              {
                  .uid = Uid {3},
                  .name = "j_dry",
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 200.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv",
                  .junction = Uid {1},
                  .capacity = 1000.0,
                  .emin = 0.0,
                  .emax = 1000.0,
                  .eini = eini,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 2.0,
              },
          },
      .flow_right_array = std::move(frs),
      .user_constraint_array = std::move(ucs),
  };
}

[[nodiscard]] Simulation make_sim()
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

struct Result
{
  std::vector<double> fr;  ///< FlowRight flow per element (array order)
  double ww {0.0};  ///< physical turbined flow
  double obj {0.0};
};

[[nodiscard]] Result solve(const System& system, PlanningOptions popts)
{
  popts.model_options.scale_objective = 1.0;
  popts.model_options.demand_fail_cost = 1000.0;
  const PlanningOptionsLP options(std::move(popts));
  const auto sim = make_sim();
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto& lp = system_lp.linear_interface();
  const auto rc = lp.resolve();
  REQUIRE(rc.has_value());
  REQUIRE(lp.is_optimal());
  const auto col_sol = lp.get_col_sol();
  const auto& s = system_lp.scene().scenarios()[0];
  const auto& t = system_lp.phase().stages()[0];
  const auto buid = t.blocks().front().uid();
  Result out;
  for (const auto& fr : system_lp.elements<FlowRightLP>()) {
    out.fr.push_back(col_sol[fr.flow_cols_at(s, t).at(buid)]);
  }
  const auto& ww = system_lp.elements<WaterwayLP>().front();
  out.ww = col_sol[ww.flow_cols_at(s, t).at(buid)];
  out.obj = lp.get_obj_value();
  return out;
}

}  // namespace

TEST_CASE("Maule review R1 - priced Canelon intake is priced, not forbidden")
{
  // maule.tson emits maule_bocatoma_canelon as
  //   {target: 0, uvalue: -costo_canelon[, fmax: arc qmax]}
  // and maule.tampl anchors it: canelon.flow = BCanelon_gen.flow.
  const auto ucs = Array<UserConstraint> {
      {
          .uid = Uid {21},
          .name = "canelon_anclaje",
          .expression = R"(flow_right("canelon").flow = waterway("ww").flow)",
          .constraint_type = "raw",
      },
  };

  SUBCASE("no fmax: target-0 usage cost defaults to an unbounded cap")
  {
    const auto frs = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "canelon",
            .direction = -1,
            .target = 0.0,
            .uvalue = -10.0,
        },
    };
    const auto r = solve(make_system(frs, ucs, "R1_no_fmax"), {});
    // 25 m3/s x 24 h x 10 $/(m3/s.h) = 6000; no thermal.
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.fr[0] == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.obj == doctest::Approx(6000.0).epsilon(1e-6));
  }

  SUBCASE("fmax = arc capacity: same priced dispatch")
  {
    const auto frs = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "canelon",
            .direction = -1,
            .fmax = 180.0,
            .target = 0.0,
            .uvalue = -10.0,
        },
    };
    const auto r = solve(make_system(frs, ucs, "R1_fmax"), {});
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.fr[0] == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.obj == doctest::Approx(6000.0).epsilon(1e-6));
  }

  SUBCASE("reward without fmax keeps the target as cap (no unbounded ray)")
  {
    // uvalue > 0 is NOT a usage cost: fmax still defaults to target.
    const auto frs = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "reward",
            .direction = -1,
            .target = 0.0,
            .uvalue = 80.0,
        },
    };
    const auto r = solve(make_system(frs, {}, "R1_reward"), {});
    CHECK(r.fr[0] == doctest::Approx(0.0));
  }

  SUBCASE("fcost with target 0 and no fmax keeps the target as cap")
  {
    const auto frs = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "floor",
            .direction = -1,
            .target = 0.0,
            .fcost = 50.0,
        },
    };
    const auto r = solve(make_system(frs, {}, "R1_fcost"), {});
    CHECK(r.fr[0] == doctest::Approx(0.0));
  }
}

TEST_CASE("Maule review R2 - global hydro costs stay out of target-0 rights")
{
  // Maule-shaped partition: q_elec (target 0, no own costs) is the only
  // right; anchored to the physical arc (maule_anclaje_particion).
  const auto frs = Array<FlowRight> {
      {
          .uid = Uid {1},
          .name = "q_elec",
          .direction = -1,
          .fmax = 200.0,
          .target = 0.0,
      },
  };
  const auto ucs = Array<UserConstraint> {
      {
          .uid = Uid {21},
          .name = "anclaje",
          .expression = R"(flow_right("q_elec").flow = waterway("ww").flow)",
          .constraint_type = "raw",
      },
  };

  SUBCASE("control: no global costs -> hydro serves the load")
  {
    const auto r = solve(make_system(frs, ucs, "R2_ctrl"), {});
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.obj == doctest::Approx(0.0).epsilon(1e-6));
  }

  SUBCASE("hydro_spill_cost (= hydro_fail_cost) set -> no effect on target 0")
  {
    PlanningOptions popts;
    popts.model_options.hydro_spill_cost = 10.0;
    const auto r = solve(make_system(frs, ucs, "R2_fail"), std::move(popts));
    // The global "unmet right" penalty prices a requirement; a target-0
    // right has none, so the anchored arc keeps serving the load.
    CHECK(r.fr[0] == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
    CHECK(r.obj == doctest::Approx(0.0).epsilon(1e-6));
  }

  SUBCASE("hydro_spill_cost still prices a target > 0 requirement")
  {
    const auto req = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "q_req",
            .direction = -1,
            .fmax = 200.0,
            .target = 30.0,
        },
    };
    const auto ucs_req = Array<UserConstraint> {
        {
            .uid = Uid {21},
            .name = "anclaje_req",
            .expression = R"(flow_right("q_req").flow = waterway("ww").flow)",
            .constraint_type = "raw",
        },
    };
    PlanningOptions popts;
    popts.model_options.hydro_spill_cost = 10.0;
    const auto r = solve(make_system(req, ucs_req, "R2_req"), std::move(popts));
    // Pinned at the anchor to the 25 m3/s the load absorbs; the 5 m3/s
    // shortfall is paid at 10 $/m3 x 3600 x 24 h, unless the spill of
    // the extra 5 m3/s through the turbine is cheaper — either way the
    // requirement is honoured or priced, never silently dropped.
    CHECK(r.fr[0] >= 25.0 - 1e-6);
    CHECK(r.obj >= -1e-6);
  }

  SUBCASE("hydro_use_value set -> accounting right with no water is not paid")
  {
    // An accounting-only FlowRight (no junction, nothing physical) —
    // like invernada_deficit / maule_gasto_normal_elec without anchor.
    const auto acc = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "q_acc",
            .direction = -1,
            .fmax = 200.0,
            .target = 0.0,
        },
    };
    PlanningOptions popts;
    popts.model_options.hydro_use_value = 1.0;  // $/m3
    const auto r = solve(make_system(acc, {}, "R2_use"), std::move(popts));
    // No phantom profit: the hydro still serves the load at zero cost.
    CHECK(r.obj >= -1e-6);
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
  }

  SUBCASE("hydro_use_value still values a withdrawing right")
  {
    // A right that withdraws water at the reservoir junction: the
    // global use value applies (it is real water).
    const auto wd = Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "q_wd",
            .junction_a = Uid {1},
            .direction = -1,
            .fmax = 2.0,
            .target = 0.0,
        },
    };
    PlanningOptions popts;
    popts.model_options.hydro_use_value = 1.0;  // $/m3
    const auto r = solve(make_system(wd, {}, "R2_wd"), std::move(popts));
    CHECK(r.fr[0] == doctest::Approx(2.0).epsilon(1e-6));
    CHECK(r.obj < 0.0);
  }
}

TEST_CASE("Maule review R3 - Res105 shortfall forfeits the delivery reward")
{
  // maule_resolucion_105: {target 0, uvalue +ValorRiego105 = 1100}, no
  // junction; district Melado: retiro at its junction (here j_dry: no
  // water) + deficit_Melado {target 0, uvalue -(CostoRiegoNS105 +
  // ValorRiego105) = -2100, as maule_agreement.py emits it};
  // dist row: retiro + deficit = share x qa105.
  const auto frs = Array<FlowRight> {
      {
          .uid = Uid {1},
          .name = "qa105",
          .direction = -1,
          .fmax = 200.0,
          .target = 0.0,
          .uvalue = 1100.0,
      },
      {
          .uid = Uid {2},
          .name = "retiro_dry",
          .junction_a = Uid {3},
          .direction = -1,
          .fmax = 200.0,
      },
      {
          .uid = Uid {3},
          .name = "deficit_dry",
          .direction = -1,
          .fmax = 200.0,
          .target = 0.0,
          .uvalue = -2100.0,
      },
  };
  const auto ucs = Array<UserConstraint> {
      {
          .uid = Uid {21},
          .name = "dist_dry",
          .expression =
              R"(flow_right("retiro_dry").flow + flow_right("deficit_dry").flow = 1.0 * flow_right("qa105").flow)",
          .constraint_type = "raw",
      },
  };
  const auto r = solve(make_system(frs, ucs, "R3_res105"), {});
  // With -1000 the delivery ran at fmax with zero water withdrawn, a
  // profit of (1100 - 1000) x 200 m3/s x 24 h = 480000 $.  Pricing the
  // shortfall at -(1000 + 1100) leaves no profit in booking undelivered
  // water: nothing is "delivered" without water.
  CHECK(r.fr[0] == doctest::Approx(0.0));
  CHECK(r.fr[1] == doctest::Approx(0.0));
  CHECK(r.fr[2] == doctest::Approx(0.0));
  CHECK(r.obj == doctest::Approx(0.0).epsilon(1e-6));
}

TEST_CASE("Maule review R4 - compensation right closed below the 581 gate")
{
  // Laguna del Maule at 50 hm3 (< 129 extraordinary reserve).  As
  // emitted now, both the ordinary electric right (129..581 window,
  // simplified here to its lower step) and the compensation right
  // (581 regime gate, genpdmaule.f:818 / :827) are gated.
  const auto gate_ord = RightBoundRule {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
              {
                  .volume = 129.0,
                  .slope = 0.0,
                  .constant = 30.0,
              },
          },
  };
  const auto gate_normal = RightBoundRule {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
              {
                  .volume = 581.0,
                  .slope = 0.0,
                  .constant = 30.0,
              },
          },
  };
  const auto make_rights = [&](bool comp_gated)
  {
    auto comp = FlowRight {
        .uid = Uid {2},
        .name = "q_comp_elec",
        .direction = -1,
        .fmax = 30.0,
        .target = 0.0,
    };
    if (comp_gated) {
      comp.bound_rule = gate_normal;
    }
    return Array<FlowRight> {
        {
            .uid = Uid {1},
            .name = "q_ord_elec",
            .direction = -1,
            .fmax = 30.0,
            .target = 0.0,
            .bound_rule = gate_ord,
        },
        comp,
    };
  };
  const auto ucs = Array<UserConstraint> {
      {
          .uid = Uid {21},
          .name = "anclaje",
          .expression =
              R"(flow_right("q_ord_elec").flow + flow_right("q_comp_elec").flow = waterway("ww").flow)",
          .constraint_type = "raw",
      },
  };

  SUBCASE("gated (template as emitted): no electric water below 129")
  {
    const auto r =
        solve(make_system(make_rights(true), ucs, "R4_gated", 50.0), {});
    CHECK(r.fr[0] == doctest::Approx(0.0));
    CHECK(r.fr[1] == doctest::Approx(0.0));
    CHECK(r.ww == doctest::Approx(0.0));
    CHECK(r.obj == doctest::Approx(120000.0).epsilon(1e-6));
  }

  SUBCASE("ungated (pre-fix shape): compensation drains the reserve")
  {
    const auto r =
        solve(make_system(make_rights(false), ucs, "R4_ungated", 50.0), {});
    CHECK(r.fr[1] == doctest::Approx(25.0).epsilon(1e-6));
  }

  SUBCASE("gated, lake above 581: compensation available")
  {
    const auto r =
        solve(make_system(make_rights(true), ucs, "R4_normal", 600.0), {});
    CHECK(r.ww == doctest::Approx(25.0).epsilon(1e-6));
  }
}
