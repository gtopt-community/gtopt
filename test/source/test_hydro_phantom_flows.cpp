// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_hydro_phantom_flows.cpp
 * @brief     Proof tests for fictitious ("phantom") hydro flows and
 *            artificial negative water values (review 2026-11-22).
 *
 * Each TEST_CASE builds the smallest system that exposes one mechanism
 * and CHECKs the behaviour the LP actually produces today.  Cases whose
 * title starts with "PHANTOM" or "NEG-DUAL" document a defect or an
 * artifact.
 */

#include <cmath>
#include <cstddef>
#include <optional>

#include <doctest/doctest.h>
#include <gtopt/flow_lp.hpp>
#include <gtopt/flow_right_lp.hpp>
#include <gtopt/junction_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>
#include <gtopt/waterway_lp.hpp>

using namespace gtopt;

namespace
{

const Simulation& one_block_sim()
{
  static const Simulation sim = {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
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
                  .uid = Uid {1},
              },
          },
  };
  return sim;
}

PlanningOptions base_opts()
{
  PlanningOptions opts;
  opts.model_options.use_single_bus = true;
  opts.model_options.demand_fail_cost = 1000.0;
  return opts;
}

double row_dual(LinearInterface& li, RowIndex row)
{
  return li.get_row_dual()[static_cast<std::size_t>(row)];
}

double col_sol(const LinearInterface& li, ColIndex col)
{
  return li.get_col_sol()[static_cast<std::size_t>(col)];
}

}  // namespace

// ─── P0: lp_reduction folds aperture-carrying inflows ──────────────────────
//
// `FlowLP::add_to_lp` folds a fixed-discharge Flow into the junction RHS
// under `lp_reduction` (no column).  `FlowLP::update_aperture` only walks
// `flow_cols`, so the aperture (backward pass) and sampled-realization
// (forward pass) hydrology never reaches a folded inflow: the clone keeps
// the BASE scenario's inflow.  plp2gtopt enables `lp_reduction` by default
// for sddp/cascade, so every reservoir/junction inflow of a converted PLP
// case is affected (case_fix2: 32/32 junction inflows folded, all 32 have
// aperture data).
namespace
{

struct ApertureProbe
{
  std::size_t n_flow_cols {};
  double ww_flow_base {};
  double ww_flow_aperture {};
};

ApertureProbe probe_aperture(bool lp_reduction, MethodType method)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "hydro",
          .bus = Uid {1},
          .gcost = 1.0,
          .capacity = 500.0,
      },
      {
          .uid = Uid {2},
          .name = "thermal",
          .bus = Uid {1},
          .gcost = 50.0,
          .capacity = 500.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 100.0,
      },
  };
  // j1 has NO drain: all inflow must leave through ww1, so the waterway
  // flow is an exact read-out of the inflow the LP actually sees.
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "j1",
          .drain = false,
      },
  };
  const STBRealFieldSched discharge {
      std::vector<std::vector<std::vector<double>>> {
          {{10.0}},  // scenario 1 (forward / base)
          {{30.0}},  // scenario 2 (aperture)
      },
  };
  const Array<Flow> flow_array = {
      {
          .uid = Uid {1},
          .name = "inflow",
          .direction = 1,
          .junction = Uid {1},
          .discharge = discharge,
      },
  };
  const Array<Waterway> waterway_array = {
      {
          .uid = Uid {1},
          .name = "ww1",
          .junction_a = Uid {1},
          .fmin = 0.0,
          .fmax = 100.0,
      },
  };
  const Array<Turbine> turbine_array = {
      {
          .uid = Uid {1},
          .name = "tur1",
          .waterway = Uid {1},
          .generator = Uid {1},
          .production_factor = 1.0,
      },
  };
  const Simulation simulation = {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 1.0,
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
                  .uid = Uid {1},
                  .probability_factor = 0.5,
              },
              {
                  .uid = Uid {2},
                  .probability_factor = 0.5,
              },
          },
  };
  const System system = {
      .name = "PhantomApertureFold",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .waterway_array = waterway_array,
      .flow_array = flow_array,
      .turbine_array = turbine_array,
  };

  auto opts = base_opts();
  opts.method = method;
  opts.model_options.lp_reduction = lp_reduction;
  const PlanningOptionsLP options(opts);
  SimulationLP sim_lp(simulation, options);
  SystemLP sys_lp(system, sim_lp);

  auto& li = sys_lp.linear_interface();
  REQUIRE(li.resolve().has_value());

  const auto& scenarios = sim_lp.scenes()[first_scene_index()].scenarios();
  const auto& base = scenarios[0];
  const auto& ap = scenarios[1];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];

  const auto& flow_lp = sys_lp.elements<FlowLP>()[0];
  const auto& ww_lp = sys_lp.elements<WaterwayLP>()[0];
  const auto ww_col = ww_lp.flow_cols_at(base, stage).at(make_uid<Block>(1));

  ApertureProbe out;
  out.n_flow_cols = flow_lp.flow_cols_at(base, stage).size();
  out.ww_flow_base = col_sol(li, ww_col);

  auto clone = li.clone();
  REQUIRE(flow_lp.update_aperture(
      clone,
      base,
      [&](StageUid st, BlockUid bl) -> std::optional<double>
      { return flow_lp.aperture_value(ap.uid(), st, bl); },
      stage));
  REQUIRE(clone.resolve().has_value());
  out.ww_flow_aperture = col_sol(clone, ww_col);
  return out;
}

}  // namespace

TEST_CASE(  // NOLINT
    "PHANTOM lp_reduction: folded inflow vs aperture hydrology")
{
  SUBCASE("reference: no lp_reduction -> aperture inflow 30 is seen")
  {
    const auto r = probe_aperture(/*lp_reduction=*/false, MethodType::sddp);
    CHECK(r.n_flow_cols == 1);
    CHECK(r.ww_flow_base == doctest::Approx(10.0));
    CHECK(r.ww_flow_aperture == doctest::Approx(30.0));
  }

  SUBCASE("monolithic + lp_reduction: fold is harmless (no apertures)")
  {
    const auto r =
        probe_aperture(/*lp_reduction=*/true, MethodType::monolithic);
    CHECK(r.n_flow_cols == 0);
    CHECK(r.ww_flow_base == doctest::Approx(10.0));
    // update_aperture is a silent no-op on the folded inflow.
    CHECK(r.ww_flow_aperture == doctest::Approx(10.0));
  }

  SUBCASE("sddp + lp_reduction: inflow kept as a column, aperture seen")
  {
    // Fixed: under sddp/cascade the fold is disabled so update_aperture
    // can pin the aperture inflow (was: n_flow_cols 0, flow stuck at 10).
    const auto r = probe_aperture(/*lp_reduction=*/true, MethodType::sddp);
    CHECK(r.n_flow_cols == 1);
    CHECK(r.ww_flow_base == doctest::Approx(10.0));
    CHECK(r.ww_flow_aperture == doctest::Approx(30.0));
  }
}

// ─── P1: VolumeRight.right_reservoir direction sign (fixed) ───────────────
//
// volume_right.hpp documents `direction` as "+1 = supply (volume inflow to
// the balance), -1 = withdrawal (volume extraction)" with default -1.  The
// parent's energy row follows `E_b = E_{b-1} − (coef)·x` (a POSITIVE
// coefficient is an outflow), so the coupling must write
// `−direction·fcr·dur`.  Before the fix it wrote `+direction·fcr·dur` and a
// child "withdrawal" CREDITED the parent ledger (empty parent ended at +10).
namespace
{
struct VrDirResult
{
  double parent_efin;
  double child_efin;
};

VrDirResult solve_vr_direction(int direction)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .gcost = 10.0,
          .capacity = 100.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 10.0,
      },
  };
  // Parent ledger starts EMPTY.  Child must deliver 10 hm3 (fail 1e4).
  const Array<VolumeRight> volume_right_array = {
      {
          .uid = Uid {1},
          .name = "parent",
          .emax = 1000.0,
          .eini = 0.0,
          .fmax = 0.0,  // parent has no own extraction
      },
      {
          .uid = Uid {2},
          .name = "child",
          .right_reservoir = Uid {1},
          .direction = direction,
          .emax = 100.0,
          .eini = 100.0,
          .demand = 10.0,
          // 10 hm3 in one 1-h block at fcr 0.0036 -> 2777.78 m3/s
          .fmax = 10.0 / 0.0036,
          .fail_cost = 10000.0,
      },
  };
  const System system = {
      .name = "VRDirection",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .volume_right_array = volume_right_array,
  };

  const PlanningOptionsLP options(base_opts());
  SimulationLP sim_lp(one_block_sim(), options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  REQUIRE(li.resolve().has_value());
  REQUIRE(li.is_optimal());

  const auto& scenario = sim_lp.scenes()[first_scene_index()].scenarios()[0];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];
  const auto& parent = sys_lp.elements<VolumeRightLP>()[0];
  const auto& child = sys_lp.elements<VolumeRightLP>()[1];

  return {
      .parent_efin = col_sol(li, parent.efin_col_at(scenario, stage)),
      .child_efin = col_sol(li, child.efin_col_at(scenario, stage)),
  };
}
}  // namespace

TEST_CASE(  // NOLINT
    "PHANTOM VolumeRight right_reservoir direction sign matches the docs")
{
  SUBCASE("direction = -1 withdraws from the parent (cannot go below 0)")
  {
    const auto r = solve_vr_direction(-1);
    // The empty parent cannot fund the withdrawal: the child fails its
    // demand instead of creating accounting volume in the parent.
    CHECK(r.parent_efin == doctest::Approx(0.0));
    CHECK(r.child_efin == doctest::Approx(100.0));
  }
  SUBCASE("direction = +1 supplies the parent")
  {
    const auto r = solve_vr_direction(+1);
    CHECK(r.child_efin == doctest::Approx(90.0));
    CHECK(r.parent_efin == doctest::Approx(10.0));
  }
}

// ─── P2: Waterway.lossfactor is gated by the ELECTRIC line-loss mode ─────
//
// `SystemContext::stage_lossfactor` (used only by WaterwayLP) returns
// `max(lossfactor, 0)` when `line_losses_mode != none`, else 0.  Negative
// values are clamped (no water creation), but a hydraulic canal loss is
// silently dropped whenever electric line losses are disabled — the
// downstream junction then receives water the canal should have lost.
namespace
{
double canal_delivery(double lossfactor, bool line_losses)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "hydro",
          .bus = Uid {1},
          .gcost = 1.0,
          .capacity = 500.0,
      },
      {
          .uid = Uid {2},
          .name = "thermal",
          .bus = Uid {1},
          .gcost = 50.0,
          .capacity = 500.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 100.0,
      },
  };
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "j1",
          .drain = false,
      },
      {
          .uid = Uid {2},
          .name = "j2",
          .drain = false,
      },
  };
  const Array<Flow> flow_array = {
      {
          .uid = Uid {1},
          .name = "inflow",
          .direction = 1,
          .junction = Uid {1},
          .discharge = 10.0,
      },
  };
  const Array<Waterway> waterway_array = {
      {
          .uid = Uid {1},
          .name = "canal",
          .junction_a = Uid {1},
          .junction_b = Uid {2},
          .lossfactor = lossfactor,
          .fmin = 0.0,
          .fmax = 100.0,
      },
      {
          .uid = Uid {2},
          .name = "pen",
          .junction_a = Uid {2},
          .fmin = 0.0,
          .fmax = 100.0,
      },
  };
  const Array<Turbine> turbine_array = {
      {
          .uid = Uid {1},
          .name = "tur",
          .waterway = Uid {2},
          .generator = Uid {1},
          .production_factor = 1.0,
      },
  };
  const System system = {
      .name = "NegLossfactor",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .waterway_array = waterway_array,
      .flow_array = flow_array,
      .turbine_array = turbine_array,
  };
  auto opts = base_opts();
  opts.model_options.use_line_losses = line_losses;
  const PlanningOptionsLP options(opts);
  SimulationLP sim_lp(one_block_sim(), options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  REQUIRE(li.resolve().has_value());

  const auto& scenario = sim_lp.scenes()[first_scene_index()].scenarios()[0];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];
  const auto& wws = sys_lp.elements<WaterwayLP>();
  const double canal =
      col_sol(li, wws[0].flow_cols_at(scenario, stage).at(make_uid<Block>(1)));
  const double pen =
      col_sol(li, wws[1].flow_cols_at(scenario, stage).at(make_uid<Block>(1)));
  CHECK(canal == doctest::Approx(10.0));
  return pen;
}
}  // namespace

TEST_CASE(  // NOLINT
    "PHANTOM Waterway lossfactor ignored when electric line losses are off")
{
  // Refuted: a negative lossfactor is clamped to 0 (no water created).
  CHECK(canal_delivery(-0.5, true) == doctest::Approx(10.0));
  // Reference: 20 % canal loss honoured with line losses on.
  CHECK(canal_delivery(0.2, true) == doctest::Approx(8.0));
  // BUG: same hydraulic loss silently dropped with line losses off.
  CHECK(canal_delivery(0.2, false) == doctest::Approx(10.0));
}

// ─── P1: FlowRight.junction_b no longer adds a hidden bypass (fixed) ──────
//
// Before the fix any FlowRight with junction_a + junction_b
// (per_block/stage_average) got a zero-cost, [0, +inf) `bypass` column
// a -> b that was NOT written to the output.  The carry-mode diversion
// RieSaltos_irrigation_right (plp2gtopt _inject_district_anchors) moved up
// to 828 m3/s through it (junction closure residual RieSaltos/LAJA_I).
// Now the arc is opt-in (`use_bypass`, or legacy `bypass_cost` /
// `bypass_fmax`), bounded by `bypass_fmax`, and reported as `bypass_sol`.
namespace
{
struct BypassProbe
{
  bool optimal {};
  bool has_bypass {};
  double served {};
  double bypass {};
};

BypassProbe probe_bypass(std::optional<bool> use_bypass,
                         std::optional<double> bypass_fmax)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .gcost = 10.0,
          .capacity = 100.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 10.0,
      },
  };
  // j1 has no drain and NO waterway: its only physical outlet is the
  // right itself, capped at fmax = 5 m3/s.  Inflow is 100 m3/s.
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "j1",
          .drain = false,
      },
      {
          .uid = Uid {2},
          .name = "j2",
          .drain = true,
      },
  };
  const Array<Flow> flow_array = {
      {
          .uid = Uid {1},
          .name = "inflow",
          .direction = 1,
          .junction = Uid {1},
          .discharge = 100.0,
      },
  };
  const Array<FlowRight> flow_right_array = {
      {
          .uid = Uid {1},
          .name = "carry",
          .junction_a = Uid {1},
          .fmax = 5.0,
          .junction_b = Uid {2},
          .use_bypass = use_bypass,
          .bypass_fmax = bypass_fmax,
          .consumptive = false,
      },
  };
  const System system = {
      .name = "FRBypass",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .flow_array = flow_array,
      .flow_right_array = flow_right_array,
  };
  const PlanningOptionsLP options(base_opts());
  SimulationLP sim_lp(one_block_sim(), options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  const auto res = li.resolve();

  const auto& scenario = sim_lp.scenes()[first_scene_index()].scenarios()[0];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];
  const auto& block = stage.blocks()[0];
  const auto& fr = sys_lp.elements<FlowRightLP>()[0];
  const auto bcol = fr.bypass_col_at(scenario, stage, block);

  BypassProbe out {
      .optimal = res.has_value() && li.is_optimal(),
      .has_bypass = bcol.has_value(),
  };
  if (out.optimal) {
    out.served =
        col_sol(li, fr.flow_cols_at(scenario, stage).at(make_uid<Block>(1)));
    out.bypass = bcol ? col_sol(li, *bcol) : 0.0;
  }
  return out;
}
}  // namespace

TEST_CASE(  // NOLINT
    "PHANTOM FlowRight junction_b bypass arc is opt-in, bounded, visible")
{
  SUBCASE(
      "bare junction_b: no hidden arc, the over-full junction is "
      "infeasible")
  {
    const auto r = probe_bypass(std::nullopt, std::nullopt);
    CHECK_FALSE(r.has_bypass);
    // 100 m3/s in, at most 5 out: no invisible path may absorb 95.
    CHECK_FALSE(r.optimal);
  }
  SUBCASE("use_bypass=false disables even a legacy bypass_fmax")
  {
    const auto r = probe_bypass(false, 1000.0);
    CHECK_FALSE(r.has_bypass);
    CHECK_FALSE(r.optimal);
  }
  SUBCASE("explicit use_bypass: visible pass-through")
  {
    const auto r = probe_bypass(true, std::nullopt);
    REQUIRE(r.has_bypass);
    REQUIRE(r.optimal);
    CHECK(r.served <= 5.0 + 1e-9);
    CHECK(r.served + r.bypass == doctest::Approx(100.0));
  }
  SUBCASE("bypass_fmax bounds the arc (implicit opt-in)")
  {
    const auto ok = probe_bypass(std::nullopt, 95.0);
    REQUIRE(ok.has_bypass);
    REQUIRE(ok.optimal);
    CHECK(ok.bypass == doctest::Approx(95.0));
    // 5 + 90 < 100: the bound is enforced, no water disappears.
    const auto tight = probe_bypass(true, 90.0);
    CHECK(tight.has_bypass);
    CHECK_FALSE(tight.optimal);
  }
}

// ─── NEG-DUAL: forced-outflow junction under a negative LMP ───────────────
//
// A run-of-river junction with no spill path (drain=false, only a turbine
// waterway) must turbine all its inflow.  When the LMP is negative the
// forced energy has negative value and the junction balance dual turns
// negative (≈ production_factor · LMP).  A free drain bounds it at 0.
namespace
{
double ror_dual(bool drain, double marginal_gcost)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "hydro",
          .bus = Uid {1},
          .gcost = 0.0,
          .capacity = 500.0,
      },
      {
          .uid = Uid {2},
          .name = "marginal",
          .bus = Uid {1},
          .gcost = marginal_gcost,
          .capacity = 500.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 50.0,
      },
  };
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "j1",
          .drain = drain,
      },
  };
  const Array<Flow> flow_array = {
      {
          .uid = Uid {1},
          .name = "inflow",
          .direction = 1,
          .junction = Uid {1},
          .discharge = 10.0,
      },
  };
  const Array<Waterway> waterway_array = {
      {
          .uid = Uid {1},
          .name = "pen",
          .junction_a = Uid {1},
          .fmin = 0.0,
          .fmax = 100.0,
      },
  };
  const Array<Turbine> turbine_array = {
      {
          .uid = Uid {1},
          .name = "tur",
          .waterway = Uid {1},
          .generator = Uid {1},
          .production_factor = 2.0,
      },
  };
  const System system = {
      .name = "RorNegLmp",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .waterway_array = waterway_array,
      .flow_array = flow_array,
      .turbine_array = turbine_array,
  };
  auto opts = base_opts();
  opts.model_options.scale_objective = 1.0;
  const PlanningOptionsLP options(opts);
  SimulationLP sim_lp(one_block_sim(), options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  REQUIRE(li.resolve().has_value());
  const auto& scenario = sim_lp.scenes()[first_scene_index()].scenarios()[0];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];
  const auto& j = sys_lp.elements<JunctionLP>()[0];
  return row_dual(li,
                  j.balance_rows_at(scenario, stage).at(make_uid<Block>(1)));
}
}  // namespace

TEST_CASE("NEG-DUAL forced run-of-river junction under negative LMP")  // NOLINT
{
  const double pos = ror_dual(/*drain=*/false, +10.0);
  const double neg = ror_dual(/*drain=*/false, -10.0);
  const double neg_drain = ror_dual(/*drain=*/true, -10.0);
  // Positive LMP: water worth pf·LMP = 20 (sign convention reference).
  CHECK(std::abs(pos) == doctest::Approx(20.0));
  // Negative LMP: same magnitude, OPPOSITE sign -> negative water value.
  CHECK(neg == doctest::Approx(-pos));
  // A free drain caps the downside at zero.
  CHECK(neg_drain == doctest::Approx(0.0));
}

// ─── NEG-DUAL: seepage forced into a capacity-capped filtration junction ──
//
// ReservoirSeepage pins q_filt = c + slope·efin on a waterway into a
// filtration junction.  When that junction has no drain and its outlet
// is capped (case_fix2: FILT_CIPRESES, outlet fmax 20.1), the cap becomes
// an artificial ceiling efin <= (cap − c)/slope on the reservoir and the
// filtration junction dual turns negative (19 cells in case_fix2, down to
// -409).
namespace
{
struct SeepProbe
{
  double efin {};
  double jf_dual {};
};

SeepProbe seep_probe(bool filt_drain)
{
  const Array<Bus> bus_array = {
      {
          .uid = Uid {1},
          .name = "b1",
      },
  };
  const Array<Generator> generator_array = {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .gcost = 10.0,
          .capacity = 100.0,
      },
  };
  const Array<Demand> demand_array = {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .capacity = 10.0,
      },
  };
  const Array<Junction> junction_array = {
      {
          .uid = Uid {1},
          .name = "jr",
          .drain = true,
      },
      {
          .uid = Uid {2},
          .name = "jf",
          .drain = filt_drain,
      },
  };
  const Array<Waterway> waterway_array = {
      {
          .uid = Uid {1},
          .name = "filt",
          .junction_a = Uid {1},
          .junction_b = Uid {2},
          .fmin = 0.0,
      },
      {
          .uid = Uid {2},
          .name = "filt_out",
          .junction_a = Uid {2},
          .fmin = 0.0,
          .fmax = 10.0,
      },
  };
  // eini 150, soft terminal target 150 (efin_cost 1000).  Seepage
  // q = 0.1·efin  ->  with the 10 m3/s outlet cap: efin <= 100.
  const Array<Reservoir> reservoir_array = {
      {
          .uid = Uid {1},
          .name = "rsv",
          .junction = Uid {1},
          .emin = 0.0,
          .emax = 500.0,
          .eini = 150.0,
          .efin = 150.0,
          .efin_cost = 1000.0,
          .fmin = -1000.0,
          .fmax = 1000.0,
          .flow_conversion_rate = 1.0,
      },
  };
  const Array<ReservoirSeepage> seepage_array = {
      {
          .uid = Uid {1},
          .name = "seep",
          .waterway = Uid {1},
          .reservoir = Uid {1},
          .slope = 0.1,
          .constant = 0.0,
      },
  };
  const System system = {
      .name = "SeepCap",
      .bus_array = bus_array,
      .demand_array = demand_array,
      .generator_array = generator_array,
      .junction_array = junction_array,
      .waterway_array = waterway_array,
      .reservoir_array = reservoir_array,
      .reservoir_seepage_array = seepage_array,
  };
  auto opts = base_opts();
  opts.model_options.scale_objective = 1.0;
  const PlanningOptionsLP options(opts);
  SimulationLP sim_lp(one_block_sim(), options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  REQUIRE(li.resolve().has_value());
  REQUIRE(li.is_optimal());
  const auto& scenario = sim_lp.scenes()[first_scene_index()].scenarios()[0];
  const auto& stage = sim_lp.phases()[first_phase_index()].stages()[0];
  const auto& rsv = sys_lp.elements<ReservoirLP>()[0];
  const auto& jf = sys_lp.elements<JunctionLP>()[1];
  return SeepProbe {
      .efin = col_sol(li, rsv.efin_col_at(scenario, stage)),
      .jf_dual = row_dual(
          li, jf.balance_rows_at(scenario, stage).at(make_uid<Block>(1))),
  };
}
}  // namespace

TEST_CASE(  // NOLINT
    "NEG-DUAL seepage into a capped filtration junction caps the reservoir")
{
  const auto capped = seep_probe(/*filt_drain=*/false);
  const auto free_drain = seep_probe(/*filt_drain=*/true);
  // Free filtration outlet: the seepage itself drains the reservoir,
  // efin = 150 − 0.1·efin  ->  efin = 150/1.1.
  CHECK(free_drain.efin == doctest::Approx(150.0 / 1.1));
  CHECK(free_drain.jf_dual == doctest::Approx(0.0));
  // Capped outlet: efin forced down to (10 − 0)/0.1 = 100 hm3 although
  // the reservoir has 500 hm3 of room, a free spill at jr and the target
  // is worth 1000/hm3.
  CHECK(capped.efin == doctest::Approx(100.0));
  // Shadow price of the cap: forced filtration water has NEGATIVE value.
  // (plp2gtopt now gives such seepage junctions a free drain —
  // JunctionWriter._ensure_seepage_outlets — i.e. the `free_drain` case.)
  CHECK(capped.jf_dual < -1.0);
}
