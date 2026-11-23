// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Zero-impedance tie lines (`model_options.tiny_reactance_mode`).
//
// A line whose reactance is set but is 0 or has |X/V²| below
// `dc_line_reactance_threshold` is, by default (`tie`), a zero-impedance
// tie: KVL forces θ_a − θ_b = φ and the flow is free within ±tmax.  The
// legacy `dc` mode turns it into a free-flow DC link.  A line with no
// reactance at all is a DC link in both modes.
//
// Fixture: 3-bus triangle, generator (10 $/MWh) at b1, 150 MW demand at
// b3.  l12 and l23 have x = 0.05; l13 is the "short" (tmax = 100).
//
//   * tie: θ1 = θ3, so the angle drop along b1→b2→b3 is zero and, with
//     no injection at b2, KVL forces f12 = f23 = 0.  Only 100 MW reach b3
//     (through l13); 50 MW are curtailed at 1000 $/MWh:
//       obj = 100·10 + 50·1000 = 51 000.
//   * dc: l13 is a free-flow link (no cycle), the radial b1→b2→b3 path
//     carries the remaining 50 MW:  obj = 150·10 = 1 500.

#include <cmath>
#include <numbers>
#include <string>
#include <string_view>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/bus.hpp>
#include <gtopt/bus_island.hpp>
#include <gtopt/bus_lp.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/line.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>

using namespace gtopt;

namespace
{

constexpr std::string_view tie_triangle_json = R"json(
  {
    "options": {
      "annual_discount_rate": 0.0,
      "output_format": "csv",
      "output_compression": "uncompressed",
      "model_options": {
        "use_single_bus": false,
        "use_kirchhoff": true,
        "line_losses_mode": "none",
        "scale_objective": 1,
        "demand_fail_cost": 1000
      }
    },
    "simulation": {
      "block_array": [{"uid": 1, "duration": 1}],
      "stage_array": [{"uid": 1, "first_block": 0, "count_block": 1}],
      "scenario_array": [{"uid": 1, "probability_factor": 1}]
    },
    "system": {
      "name": "tie3",
      "bus_array": [
        {"uid": 1, "name": "b1"},
        {"uid": 2, "name": "b2"},
        {"uid": 3, "name": "b3"}
      ],
      "generator_array": [
        {"uid": 1, "name": "g1", "bus": "b1", "pmin": 0, "pmax": 300,
         "gcost": 10, "capacity": 300}
      ],
      "demand_array": [
        {"uid": 1, "name": "d3", "bus": "b3", "lmax": [[150.0]]}
      ],
      "line_array": [
        {"uid": 1, "name": "l12", "bus_a": "b1", "bus_b": "b2",
         "reactance": 0.05, "tmax_ab": 200, "tmax_ba": 200},
        {"uid": 2, "name": "l23", "bus_a": "b2", "bus_b": "b3",
         "reactance": 0.05, "tmax_ab": 200, "tmax_ba": 200},
        {"uid": 3, "name": "l13", "bus_a": "b1", "bus_b": "b3",
         "reactance": 1e-8, "tmax_ab": 100, "tmax_ba": 100}
      ]
    }
  }
)json";

struct TieCase
{
  std::string_view kirchhoff_mode {"node_angle"};
  std::string_view tiny_mode {};  ///< empty = default (tie)
  std::optional<double> short_reactance {1e-8};  ///< nullopt = unset
  double short_phase_shift_deg {0.0};
  bool all_ties {false};  ///< l12/l23 are ties too
  std::optional<double> ref_b1 {};  ///< reference_theta of b1 [rad]
  std::optional<double> ref_b3 {};  ///< reference_theta of b3 [rad]
};

struct TieResult
{
  double obj {0.0};
  int n_rows {0};
  std::vector<double> theta;  ///< per bus (node_angle only)
};

[[nodiscard]] auto solve_tie_case(const TieCase& tc) -> TieResult
{
  auto planning =
      daw::json::from_json<Planning>(tie_triangle_json, StrictParsePolicy);
  auto& mo = planning.options.model_options;
  mo.kirchhoff_mode = OptName {std::string {tc.kirchhoff_mode}};
  if (!tc.tiny_mode.empty()) {
    mo.tiny_reactance_mode = OptName {std::string {tc.tiny_mode}};
  }
  auto& lines = planning.system.line_array;
  if (tc.short_reactance.has_value()) {
    lines[2].reactance = *tc.short_reactance;
  } else {
    lines[2].reactance.reset();
  }
  if (tc.short_phase_shift_deg != 0.0) {
    lines[2].phase_shift_deg = tc.short_phase_shift_deg;
  }
  if (tc.all_ties) {
    lines[0].reactance = 0.0;
    lines[1].reactance = 0.0;
  }
  auto& buses = planning.system.bus_array;
  buses[0].reference_theta = tc.ref_b1;
  buses[2].reference_theta = tc.ref_b3;

  PlanningLP planning_lp(std::move(planning));
  auto result = planning_lp.resolve();
  REQUIRE(result.has_value());

  auto&& systems = planning_lp.systems();
  REQUIRE(!systems.empty());
  REQUIRE(!systems.front().empty());
  const auto& sys_lp = systems.front().front();
  const auto& li = sys_lp.linear_interface();

  TieResult out {
      .obj = li.get_obj_value(),
      .n_rows = static_cast<int>(li.get_numrows()),
      .theta = {},
  };

  const auto& sim_lp = sys_lp.system_context().simulation();
  const auto& scenario = sim_lp.scenarios().front();
  const auto& stage = sim_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto sol = li.get_col_sol();
  for (const auto& bus_lp : sys_lp.elements<BusLP>()) {
    const auto col = bus_lp.lookup_theta_col(scenario, stage, buid);
    out.theta.push_back(col ? sol[*col] : std::nan(""));
  }
  return out;
}

constexpr double tie_obj = (100.0 * 10.0) + (50.0 * 1000.0);
constexpr double dc_obj = 150.0 * 10.0;

}  // namespace

TEST_CASE("zero-impedance tie — node_angle")  // NOLINT
{
  SUBCASE("tiny reactance → tie: θ1 = θ3, KVL forces the parallel path off")
  {
    const auto r = solve_tie_case({});
    CHECK(r.obj == doctest::Approx(tie_obj).epsilon(1e-6));
    REQUIRE(r.theta.size() == 3);
    CHECK(r.theta[0] == doctest::Approx(r.theta[2]));
    // f12 = f23 = 0 ⇒ θ2 equals the other two as well.
    CHECK(r.theta[1] == doctest::Approx(r.theta[0]));
  }

  SUBCASE("explicit reactance 0 → tie, identical to the tiny case")
  {
    const auto r = solve_tie_case({.short_reactance = 0.0});
    CHECK(r.obj == doctest::Approx(tie_obj).epsilon(1e-6));
    REQUIRE(r.theta.size() == 3);
    CHECK(r.theta[0] == doctest::Approx(r.theta[2]));
  }

  SUBCASE("tiny_reactance_mode = dc → legacy free-flow DC link")
  {
    const auto tie = solve_tie_case({});
    const auto r = solve_tie_case({.tiny_mode = "dc"});
    CHECK(r.obj == doctest::Approx(dc_obj).epsilon(1e-6));
    // One KVL row fewer (the l13 row is dropped).
    CHECK(r.n_rows == tie.n_rows - 1);
  }

  SUBCASE("reactance 0 with tiny_reactance_mode = dc → DC link")
  {
    const auto r = solve_tie_case({.tiny_mode = "dc", .short_reactance = 0.0});
    CHECK(r.obj == doctest::Approx(dc_obj).epsilon(1e-6));
  }

  SUBCASE("reactance unset → DC link in tie mode too")
  {
    const auto r = solve_tie_case({.short_reactance = std::nullopt});
    CHECK(r.obj == doctest::Approx(dc_obj).epsilon(1e-6));
  }

  SUBCASE("phase shift is kept: θ3 − θ1 = −φ")
  {
    constexpr double phi_deg = 2.0;
    const auto r = solve_tie_case({.short_phase_shift_deg = phi_deg});
    REQUIRE(r.theta.size() == 3);
    CHECK(r.theta[2] - r.theta[0]
          == doctest::Approx(-phi_deg * std::numbers::pi / 180.0));
  }

  SUBCASE("loop made only of ties is consistent")
  {
    const auto r = solve_tie_case({.all_ties = true});
    // All three buses share one angle; flows are free, nothing curtailed.
    CHECK(r.obj == doctest::Approx(dc_obj).epsilon(1e-6));
    REQUIRE(r.theta.size() == 3);
    CHECK(r.theta[0] == doctest::Approx(r.theta[1]));
    CHECK(r.theta[0] == doctest::Approx(r.theta[2]));
  }
}

TEST_CASE("zero-impedance tie — cycle_basis")  // NOLINT
{
  SUBCASE("tie is a cycle edge with x = 0")
  {
    const auto r = solve_tie_case({.kirchhoff_mode = "cycle_basis"});
    CHECK(r.obj == doctest::Approx(tie_obj).epsilon(1e-6));
  }

  SUBCASE("explicit reactance 0 → tie")
  {
    const auto r = solve_tie_case(
        {.kirchhoff_mode = "cycle_basis", .short_reactance = 0.0});
    CHECK(r.obj == doctest::Approx(tie_obj).epsilon(1e-6));
  }

  SUBCASE("cycle_basis and node_angle agree for a phase-shifting tie")
  {
    const auto na = solve_tie_case({.short_phase_shift_deg = 1.0});
    const auto cb = solve_tie_case(
        {.kirchhoff_mode = "cycle_basis", .short_phase_shift_deg = 1.0});
    CHECK(cb.obj == doctest::Approx(na.obj).epsilon(1e-6));
  }

  SUBCASE("tiny_reactance_mode = dc → no cycle, legacy free flow")
  {
    const auto r =
        solve_tie_case({.kirchhoff_mode = "cycle_basis", .tiny_mode = "dc"});
    CHECK(r.obj == doctest::Approx(dc_obj).epsilon(1e-6));
  }

  SUBCASE("all-tie loop: consistent and inconsistent phase shifts")
  {
    const auto ok =
        solve_tie_case({.kirchhoff_mode = "cycle_basis", .all_ties = true});
    CHECK(ok.obj == doctest::Approx(dc_obj).epsilon(1e-6));
    // Net phase shift ≠ 0 around a loop of ties is contradictory: the
    // cycle row is dropped (with a warning) instead of making the LP
    // infeasible.
    const auto bad = solve_tie_case({.kirchhoff_mode = "cycle_basis",
                                     .short_phase_shift_deg = 5.0,
                                     .all_ties = true});
    CHECK(bad.obj == doctest::Approx(dc_obj).epsilon(1e-6));
  }
}

TEST_CASE("zero-impedance tie — island detection and reference pinning")
{
  const auto make_opts = [](std::string_view tiny_mode)
  {
    PlanningOptions opts;
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = true;
    opts.model_options.kirchhoff_mode = OptName {"node_angle"};
    opts.model_options.tiny_reactance_mode = OptName {std::string {tiny_mode}};
    return PlanningOptionsLP {std::move(opts)};
  };
  const auto make_buses = []
  {
    return Array<Bus> {
        {.uid = Uid {1}, .name = "b1"},
        {.uid = Uid {2}, .name = "b2"},
        {.uid = Uid {3}, .name = "b3"},
    };
  };
  // b1—b2 regular AC line; b2—b3 zero reactance; b1—b3 tiny reactance.
  const Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "ac",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = 0.1,
      },
      {
          .uid = Uid {2},
          .name = "zero",
          .bus_a = Uid {2},
          .bus_b = Uid {3},
          .reactance = 0.0,
      },
  };
  const Array<Line> tiny_only = {
      {
          .uid = Uid {3},
          .name = "tiny",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
          .reactance = 1e-9,
      },
  };
  const Array<Line> unset_only = {
      {
          .uid = Uid {4},
          .name = "hvdc",
          .bus_a = Uid {1},
          .bus_b = Uid {3},
      },
  };

  const auto count_refs = [](const Array<Bus>& buses)
  {
    return std::ranges::count_if(
        buses, [](const Bus& b) { return b.reference_theta.has_value(); });
  };

  SUBCASE("tie mode: zero-reactance tie joins the island")
  {
    auto buses = make_buses();
    CHECK(detect_islands_and_fix_references(buses, lines, make_opts("tie"))
          == 1);
    CHECK(count_refs(buses) == 1);
  }

  SUBCASE("dc mode: zero-reactance line does not couple angles")
  {
    auto buses = make_buses();
    CHECK(detect_islands_and_fix_references(buses, lines, make_opts("dc"))
          == 2);
    CHECK(count_refs(buses) == 2);  // b3 gets its own reference
  }

  SUBCASE("tiny-reactance tie connects; unset reactance does not")
  {
    auto buses = make_buses();
    CHECK(detect_islands_and_fix_references(buses, tiny_only, make_opts("tie"))
          == 2);  // {b1, b3} + {b2}
    auto buses_dc = make_buses();
    CHECK(detect_islands_and_fix_references(
              buses_dc, unset_only, make_opts("tie"))
          == 3);
  }
}

TEST_CASE(  // NOLINT
    "zero-impedance tie — conflicting reference_theta pins fall back to DC")
{
  // Both ends of the tie l13 are reference buses.  A tie forces
  // θ1 − θ3 = φ; when the pins contradict that, keeping the tie would
  // make the LP infeasible, so the line falls back to a DC link (no KVL
  // row) — exactly the explicit `tiny_reactance_mode = "dc"` LP.
  for (const std::string_view kmode : {"node_angle", "cycle_basis"}) {
    CAPTURE(kmode);
    const TieCase conflicting {
        .kirchhoff_mode = kmode,
        .ref_b1 = 0.0,
        .ref_b3 = 0.1,
    };
    auto as_dc = conflicting;
    as_dc.tiny_mode = "dc";

    const auto fallback = solve_tie_case(conflicting);
    const auto dc = solve_tie_case(as_dc);
    CHECK(fallback.obj == doctest::Approx(dc.obj));
    CHECK(fallback.n_rows == dc.n_rows);
  }
}

TEST_CASE(  // NOLINT
    "zero-impedance tie — reference pins consistent with the phase shift")
{
  // φ = 5° on the tie: pins θ1 = 0, θ3 = −φ satisfy θ3 − θ1 + φ = 0, so
  // the tie is kept (one more row than the DC fallback of the same
  // pins shifted off the tie relation) and the LP stays feasible.
  constexpr double phi_deg = 5.0;
  const double phi_rad = phi_deg * std::numbers::pi / 180.0;

  const TieCase consistent {
      .short_phase_shift_deg = phi_deg,
      .ref_b1 = 0.0,
      .ref_b3 = -phi_rad,
  };
  auto conflicting = consistent;
  conflicting.ref_b3 = phi_rad;  // wrong sign: contradicts the tie

  const auto kept = solve_tie_case(consistent);
  const auto dropped = solve_tie_case(conflicting);
  CHECK(kept.n_rows > dropped.n_rows);
  REQUIRE(kept.theta.size() == 3);
  CHECK(kept.theta[0] == doctest::Approx(0.0));
}
