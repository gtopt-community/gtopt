// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_direction.cpp — radial-line detection and one-way lines
// (`model_options.line_direction_mode`, `Line.flow_direction`).
//
// Three tiers:
//   1. pure graph: bridge search, injection classes, side verdicts,
//      user-constraint reference scan (`line_direction.hpp`);
//   2. LP: `off` and `report` keep today's LP; `structural` drops the
//      reverse columns of certified one-way lines and every column of
//      dead-end lines with the same optimum; forced `ab` / `ba`;
//      one-sided `tangent_signed_flow`; `node_angle` angle limits; user-
//      constraint protection; the negative-price stub (phantom loss);
//   3. small e2e: IEEE cases from `cases/`, `off` vs `structural`.

#include <algorithm>
#include <array>
#include <cmath>
#include <exception>
#include <filesystem>
#include <format>
#include <span>
#include <string>
#include <string_view>
#include <vector>

#include <daw/json/daw_json_link.h>
#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/json/json_parse_policy.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/line_direction.hpp>
#include <gtopt/line_enums.hpp>
#include <gtopt/line_lp.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;
using line_direction::BusInjection;
using line_direction::Edge;
using line_direction::SideClass;
using line_direction::Verdict;

namespace
{

constexpr BusInjection kLoad {.withdraw = true};
constexpr BusInjection kGen {.inject = true};
constexpr BusInjection kBoth {.both = true};
constexpr BusInjection kEmpty {};

}  // namespace

// ─── Tier 1: pure graph ────────────────────────────────────────────────

TEST_CASE("line_direction enums round-trip")  // NOLINT
{
  for (const auto& e : line_flow_direction_entries) {
    CHECK(enum_from_name<LineFlowDirection>(e.name) == e.value);
  }
  CHECK(enum_from_name<LineFlowDirection>("auto")
        == LineFlowDirection::automatic);
  for (const auto& e : line_direction_mode_entries) {
    CHECK(enum_from_name<LineDirectionMode>(e.name) == e.value);
  }
  CHECK_FALSE(enum_from_name<LineDirectionMode>("bogus").has_value());

  PlanningOptionsLP defaults {};
  CHECK(defaults.line_direction_mode() == LineDirectionMode::off);
  PlanningOptions popts;
  popts.model_options.line_direction_mode = OptName {"structural"};
  CHECK(PlanningOptionsLP {popts}.line_direction_mode()
        == LineDirectionMode::structural);
  popts.model_options.line_direction_mode = OptName {"nope"};
  CHECK(PlanningOptionsLP {popts}.line_direction_mode()
        == LineDirectionMode::off);
}

TEST_CASE("line_direction::find_radial — trees, rings, parallels")  // NOLINT
{
  SUBCASE("path graph: every edge is radial")
  {
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 2},
    };
    const auto r = line_direction::find_radial(3, e);
    CHECK(r[0]);
    CHECK(r[1]);
  }
  SUBCASE("ring: no edge is radial")
  {
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 2},
        {.bus_a = 2, .bus_b = 0},
    };
    const auto r = line_direction::find_radial(3, e);
    CHECK_FALSE(r[0]);
    CHECK_FALSE(r[1]);
    CHECK_FALSE(r[2]);
  }
  SUBCASE("ring with a stub: only the stub is radial")
  {
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 2},
        {.bus_a = 2, .bus_b = 0},
        {.bus_a = 2, .bus_b = 3},
    };
    const auto r = line_direction::find_radial(4, e);
    CHECK_FALSE(r[0]);
    CHECK(r[3]);
  }
  SUBCASE("two parallel circuits that are a cut form a radial corridor")
  {
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 0},
    };
    const auto r = line_direction::find_radial(2, e);
    CHECK(r[0]);
    CHECK(r[1]);
  }
  SUBCASE("expansion candidate closing a loop removes the bridge")
  {
    std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 2},
    };
    CHECK(line_direction::find_radial(3, e)[0]);
    e.push_back({.bus_a = 2, .bus_b = 0});  // candidate (ever active)
    CHECK_FALSE(line_direction::find_radial(3, e)[0]);
  }
  SUBCASE("self-loops and unknown buses are ignored")
  {
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 0},
        {.bus_a = 0, .bus_b = 9},
        {.bus_a = 0, .bus_b = 1},
    };
    const auto r = line_direction::find_radial(2, e);
    CHECK_FALSE(r[0]);
    CHECK_FALSE(r[1]);
    CHECK(r[2]);
  }
  SUBCASE("deep chain does not recurse")
  {
    constexpr std::size_t n = 20000;
    std::vector<Edge> e;
    e.reserve(n - 1);
    for (std::size_t i = 0; i + 1 < n; ++i) {
      e.push_back({.bus_a = i, .bus_b = i + 1});
    }
    const auto r = line_direction::find_radial(n, e);
    CHECK(std::ranges::all_of(r, [](bool b) { return b; }));
  }
}

TEST_CASE("line_direction::side_verdict and side_class")  // NOLINT
{
  CHECK(line_direction::side_class(kLoad) == SideClass::load);
  CHECK(line_direction::side_class(kGen) == SideClass::gen);
  CHECK(line_direction::side_class(kBoth) == SideClass::mixed);
  CHECK(line_direction::side_class(kEmpty) == SideClass::empty);
  CHECK(line_direction::side_class({.withdraw = true, .inject = true})
        == SideClass::mixed);

  using enum SideClass;
  CHECK(line_direction::side_verdict(gen, load) == Verdict::ab);
  CHECK(line_direction::side_verdict(mixed, load) == Verdict::ab);
  CHECK(line_direction::side_verdict(gen, mixed) == Verdict::ab);
  CHECK(line_direction::side_verdict(load, gen) == Verdict::ba);
  CHECK(line_direction::side_verdict(load, mixed) == Verdict::ba);
  CHECK(line_direction::side_verdict(mixed, gen) == Verdict::ba);
  CHECK(line_direction::side_verdict(mixed, mixed) == Verdict::both);
  CHECK(line_direction::side_verdict(empty, mixed) == Verdict::dead_end);
  CHECK(line_direction::side_verdict(mixed, empty) == Verdict::dead_end);
  CHECK(line_direction::side_verdict(load, load) == Verdict::dead_end);
  CHECK(line_direction::side_verdict(gen, gen) == Verdict::dead_end);
}

TEST_CASE("line_direction::classify — injection classes")  // NOLINT
{
  SUBCASE("gen → hub → load chain: both lines one-way toward the load")
  {
    // bus0 gen, bus1 empty hub, bus2 load; line1 written 2→1 (ba).
    const std::vector<BusInjection> b {kGen, kEmpty, kLoad};
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 2, .bus_b = 1},
    };
    const auto c = line_direction::classify(b, e);
    CHECK(c[0].radial);
    CHECK(c[0].verdict == Verdict::ab);
    CHECK(c[1].verdict == Verdict::ba);
    CHECK(c[1].side_a == SideClass::load);
    const auto s = line_direction::summarize(c);
    CHECK(s.radial == 2);
    CHECK(s.one_way == 2);
  }
  SUBCASE("standalone storage on the stub: one-way only from a gen side")
  {
    const std::vector<Edge> e {{.bus_a = 0, .bus_b = 1}};
    // A generation-only side cannot absorb: the flow still leaves it.
    auto c = line_direction::classify(std::vector {kGen, kBoth}, e);
    CHECK(c[0].radial);
    CHECK(c[0].verdict == Verdict::ab);
    // Against a mixed side (gen + load) the storage can reverse it.
    const BusInjection gen_load {.withdraw = true, .inject = true};
    c = line_direction::classify(std::vector {gen_load, kBoth}, e);
    CHECK(c[0].verdict == Verdict::both);
    CHECK(line_direction::summarize(c).mixed == 1);
  }
  SUBCASE("generation and load on both sides make it mixed")
  {
    const BusInjection gen_load {.withdraw = true, .inject = true};
    const std::vector<Edge> e {{.bus_a = 0, .bus_b = 1}};
    CHECK(
        line_direction::classify(std::vector {gen_load, gen_load}, e)[0].verdict
        == Verdict::both);
    CHECK(line_direction::classify(std::vector {kLoad, gen_load}, e)[0].verdict
          == Verdict::ba);
  }
  SUBCASE("a bus with nothing behind the line is a dead end")
  {
    const std::vector<BusInjection> b {kBoth, kEmpty};
    const std::vector<Edge> e {{.bus_a = 0, .bus_b = 1}};
    const auto c = line_direction::classify(b, e);
    CHECK(c[0].verdict == Verdict::dead_end);
    CHECK(line_direction::summarize(c).dead_end == 1);
  }
  SUBCASE("load-only island: dead end (no feasible exchange but 0)")
  {
    const std::vector<BusInjection> b {kLoad, kLoad};
    const std::vector<Edge> e {{.bus_a = 0, .bus_b = 1}};
    CHECK(line_direction::classify(b, e)[0].verdict == Verdict::dead_end);
  }
  SUBCASE("subtree aggregation through a meshed block")
  {
    // Ring 0-1-2 (mixed: gen + load) with a stub chain 2-3-4; 3 empty,
    // 4 load.  Both stub lines flow away from the ring.
    const std::vector<BusInjection> b {kGen, kLoad, kEmpty, kEmpty, kLoad};
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 1, .bus_b = 2},
        {.bus_a = 2, .bus_b = 0},
        {.bus_a = 2, .bus_b = 3},
        {.bus_a = 4, .bus_b = 3},
    };
    const auto c = line_direction::classify(b, e);
    CHECK_FALSE(c[0].radial);
    CHECK(c[3].verdict == Verdict::ab);
    CHECK(c[4].verdict == Verdict::ba);
    CHECK(c[3].side_a == SideClass::mixed);
  }
  SUBCASE("parallel circuits: certified only when KVL-coupled")
  {
    const std::vector<BusInjection> b {kGen, kLoad};
    std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1, .kvl_coupled = true},
        {.bus_a = 1, .bus_b = 0, .kvl_coupled = true},
    };
    auto c = line_direction::classify(b, e);
    CHECK(c[0].verdict == Verdict::ab);
    CHECK(c[1].verdict == Verdict::ba);
    // An HVDC link (not KVL-coupled) in parallel: circulation possible.
    e[1].kvl_coupled = false;
    c = line_direction::classify(b, e);
    CHECK(c[0].radial);
    CHECK(c[0].verdict == Verdict::both);
    CHECK(c[1].verdict == Verdict::both);
  }
  SUBCASE("two islands are classified independently")
  {
    const std::vector<BusInjection> b {kGen, kLoad, kLoad, kGen};
    const std::vector<Edge> e {
        {.bus_a = 0, .bus_b = 1},
        {.bus_a = 2, .bus_b = 3},
    };
    const auto c = line_direction::classify(b, e);
    CHECK(c[0].verdict == Verdict::ab);
    CHECK(c[1].verdict == Verdict::ba);
  }
}

TEST_CASE("line_direction::collect_element_refs")  // NOLINT
{
  SUBCASE("quoted ids, case-insensitive type, sums")
  {
    line_direction::ElementRefs r;
    line_direction::collect_element_refs(
        R"(LINE("L1").flow + 2 * sum(line('uid:7', "L 3").flow) <= 5)",
        "line",
        r);
    CHECK_FALSE(r.all);
    REQUIRE(r.ids.size() == 3);
    CHECK(r.ids[0] == "L1");
    CHECK(r.ids[1] == "uid:7");
    CHECK(r.ids[2] == "L 3");
  }
  SUBCASE("line(all) and filters protect every line")
  {
    line_direction::ElementRefs r;
    line_direction::collect_element_refs(
        "sum(line(all).flow) <= 10", "line", r);
    CHECK(r.all);
    line_direction::ElementRefs r2;
    line_direction::collect_element_refs(
        "sum(line(all, type = \"dc\").flow) <= 1", "line", r2);
    CHECK(r2.all);
  }
  SUBCASE("other element types do not match")
  {
    line_direction::ElementRefs r;
    line_direction::collect_element_refs(
        R"(gas_line("x").flow + pipeline("y").flow + generator("g").generation)",
        "line",
        r);
    CHECK_FALSE(r.all);
    CHECK(r.ids.empty());
  }
  SUBCASE("bus theta refs")
  {
    line_direction::ElementRefs r;
    line_direction::collect_element_refs(
        R"(bus("b2").theta - bus("b3").theta <= 0.1)", "bus", r);
    CHECK(r.ids.size() == 2);
  }
}

// ─── Tier 2: LP ────────────────────────────────────────────────────────

namespace
{

/// Chain  b1 (gen) —l12→ b2 (load) ←l32— b3 (load),  b2 —l24→ b4 (empty).
///  * l12: gen side b1 → one-way ab;
///  * l32: written b3→b2 with b3 load-only → one-way ba;
///  * l24: b4 has nothing → dead end.
/// `line_extra` is spliced into every line object (loss mode etc.).
std::string chain_json(std::string_view model_options,
                       std::string_view line_extra,
                       std::string_view l12_extra = "",
                       std::string_view system_extra = "",
                       int n_blocks = 2)
{
  std::string blocks;
  for (int b = 1; b <= n_blocks; ++b) {
    blocks +=
        std::format(R"({}{{"uid": {}, "duration": 1}})", b == 1 ? "" : ",", b);
  }
  const auto line =
      [&](int uid, std::string_view name, int a, int b, std::string_view extra)
  {
    return std::format(
        R"({{"uid": {}, "name": "{}", "bus_a": {}, "bus_b": {},
             "reactance": 0.1, "tmax_ab": 300, "tmax_ba": 300{}{}}})",
        uid,
        name,
        a,
        b,
        line_extra,
        extra);
  };
  return std::format(
      R"({{
  "options": {{
    "annual_discount_rate": 0.0,
    "output_format": "csv",
    "output_compression": "uncompressed",
    "model_options": {{
      "use_single_bus": false, "use_kirchhoff": true,
      "scale_objective": 1, "demand_fail_cost": 1000{}
    }}
  }},
  "simulation": {{
    "block_array": [{}],
    "stage_array": [{{"uid": 1, "first_block": 0, "count_block": {}}}],
    "scenario_array": [{{"uid": 1, "probability_factor": 1}}]
  }},
  "system": {{
    "name": "radial_chain",
    "bus_array": [
      {{"uid": 1, "name": "b1"}}, {{"uid": 2, "name": "b2"}},
      {{"uid": 3, "name": "b3"}}, {{"uid": 4, "name": "b4"}}
    ],
    "generator_array": [
      {{"uid": 1, "name": "g1", "bus": 1, "gcost": 10, "capacity": 500}}
    ],
    "demand_array": [
      {{"uid": 1, "name": "d2", "bus": 2, "lmax": 80}},
      {{"uid": 2, "name": "d3", "bus": 3, "lmax": 60}}
    ],
    "line_array": [
      {},
      {},
      {}
    ]{}
  }}
}})",
      model_options,
      blocks,
      n_blocks,
      line(1, "l12", 1, 2, l12_extra),
      line(2, "l32", 3, 2, ""),
      line(3, "l24", 2, 4, ""),
      system_extra);
}

struct LpRun
{
  int cols {};
  int rows {};
  double obj {};
  bool optimal {};
  /// per line uid 1..3: has a forward (flowp) / reverse (flown) /
  /// signed (flows) column in the first block.
  std::array<bool, 4> fwd {};
  std::array<bool, 4> rev {};
  std::array<bool, 4> sgn {};
  std::array<double, 4> flow_p {};  ///< primal of flowp (block 1)
  std::array<double, 4> flow_n {};  ///< primal of flown (block 1)
};

LpRun build_and_solve(const std::string& json, bool solve = true)
{
  auto planning = daw::json::from_json<Planning>(json, StrictParsePolicy);
  const PlanningOptionsLP options_lp(planning.options);
  SimulationLP simulation_lp(planning.simulation, options_lp);
  SystemLP system_lp(planning.system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  LpRun r;
  r.cols = static_cast<int>(lp.get_numcols());
  r.rows = static_cast<int>(lp.get_numrows());
  if (solve) {
    auto res = lp.resolve();
    REQUIRE(res.has_value());
    r.optimal = lp.is_optimal();
    r.obj = lp.get_obj_value_raw();
  }
  const auto& scenario = simulation_lp.scenarios().front();
  const auto& stage = simulation_lp.stages().front();
  const auto buid = stage.blocks().front().uid();
  const auto sol = solve ? lp.get_col_sol_raw() : std::span<const double> {};
  for (const auto& line : system_lp.elements<LineLP>()) {
    std::size_t u = 0;
    for (std::size_t k = 1; k < r.fwd.size(); ++k) {
      if (line.uid() == Uid {static_cast<int>(k)}) {
        u = k;
      }
    }
    if (u == 0) {
      continue;
    }
    const auto& fp = line.flowp_cols_at(scenario, stage);
    const auto& fn = line.flown_cols_at(scenario, stage);
    const auto& fs = line.flows_cols_at(scenario, stage);
    if (const auto it = fp.find(buid); it != fp.end()) {
      r.fwd[u] = true;
      if (solve) {
        r.flow_p[u] = sol[it->second];
      }
    }
    if (const auto it = fn.find(buid); it != fn.end()) {
      r.rev[u] = true;
      if (solve) {
        r.flow_n[u] = sol[it->second];
      }
    }
    r.sgn[u] = fs.contains(buid);
  }
  return r;
}

constexpr std::string_view kPiecewise =
    R"(, "resistance": 1.0, "voltage": 100, "line_losses_mode": "piecewise",
       "loss_segments": 3)";

}  // namespace

TEST_CASE("line_direction LP — off and report keep today's LP")  // NOLINT
{
  const auto off = build_and_solve(chain_json("", kPiecewise));
  const auto off2 = build_and_solve(
      chain_json(R"(, "line_direction_mode": "off")", kPiecewise));
  const auto rep = build_and_solve(
      chain_json(R"(, "line_direction_mode": "report")", kPiecewise));
  CHECK(off.cols == off2.cols);
  CHECK(off.rows == off2.rows);
  CHECK(off.cols == rep.cols);
  CHECK(off.rows == rep.rows);
  CHECK(rep.obj == doctest::Approx(off.obj).epsilon(1e-12));
  // Today's LP: both directions on every line.
  for (std::size_t u = 1; u <= 3; ++u) {
    CHECK(off.fwd[u]);
    CHECK(off.rev[u]);
  }
}

TEST_CASE("line_direction LP — structural on piecewise losses")  // NOLINT
{
  constexpr int K = 3;
  constexpr int n_blocks = 2;
  const auto off = build_and_solve(chain_json("", kPiecewise));
  const auto st = build_and_solve(
      chain_json(R"(, "line_direction_mode": "structural")", kPiecewise));
  REQUIRE(off.optimal);
  REQUIRE(st.optimal);

  // Same optimum (no circulation pays at positive prices).
  CHECK(st.obj == doctest::Approx(off.obj).epsilon(1e-9));

  // l12 one-way ab: reverse column gone.  l32 one-way ba: forward gone.
  CHECK(st.fwd[1]);
  CHECK_FALSE(st.rev[1]);
  CHECK_FALSE(st.fwd[2]);
  CHECK(st.rev[2]);
  // l24 dead end: nothing at all.
  CHECK_FALSE(st.fwd[3]);
  CHECK_FALSE(st.rev[3]);
  CHECK_FALSE(st.sgn[3]);

  // Per block: each one-way line drops one direction (flow + loss + K
  // segments; link + loss rows); the dead end drops both.
  const int per_dir_cols = K + 2;
  const int per_dir_rows = 2;
  CHECK(off.cols - st.cols == n_blocks * 4 * per_dir_cols);
  CHECK(off.rows - st.rows == n_blocks * 4 * per_dir_rows);
}

TEST_CASE("line_direction LP — explicit Line.flow_direction")  // NOLINT
{
  SUBCASE("forced ab with the global mode off")
  {
    const auto off = build_and_solve(chain_json("", kPiecewise));
    const auto fa = build_and_solve(
        chain_json("", kPiecewise, R"(, "flow_direction": "ab")"));
    CHECK(fa.fwd[1]);
    CHECK_FALSE(fa.rev[1]);
    // Others untouched.
    CHECK(fa.rev[2]);
    CHECK(fa.fwd[3]);
    CHECK(fa.obj == doctest::Approx(off.obj).epsilon(1e-9));
  }
  SUBCASE("forced ba on a line that needs ab: demand is curtailed")
  {
    const auto off = build_and_solve(chain_json("", kPiecewise));
    const auto fb = build_and_solve(
        chain_json("", kPiecewise, R"(, "flow_direction": "ba")"));
    CHECK_FALSE(fb.fwd[1]);
    CHECK(fb.rev[1]);
    CHECK(fb.obj > off.obj + 1.0);  // b2/b3 served by demand_fail
  }
  SUBCASE("both overrides structural")
  {
    const auto st =
        build_and_solve(chain_json(R"(, "line_direction_mode": "structural")",
                                   kPiecewise,
                                   R"(, "flow_direction": "both")"));
    CHECK(st.fwd[1]);
    CHECK(st.rev[1]);
    CHECK_FALSE(st.fwd[2]);  // l32 still applied
  }
  SUBCASE("auto applies the certificate with the global mode off")
  {
    const auto au = build_and_solve(
        chain_json("", kPiecewise, R"(, "flow_direction": "auto")"));
    CHECK(au.fwd[1]);
    CHECK_FALSE(au.rev[1]);
    CHECK(au.rev[2]);  // l32 not auto → untouched
    CHECK(au.fwd[2]);
  }
}

TEST_CASE("line_direction LP — user-constraint protection")  // NOLINT
{
  constexpr std::string_view uc = R"(,
    "user_constraint_array": [
      {"uid": 1, "name": "cap_l12", "expression": "line(\"l12\").flow <= 200"}
    ])";
  const auto st = build_and_solve(chain_json(
      R"(, "line_direction_mode": "structural")", kPiecewise, "", uc));
  CHECK(st.fwd[1]);
  CHECK(st.rev[1]);  // protected: both directions kept
  CHECK_FALSE(st.fwd[2]);  // l32 still one-way
  CHECK_FALSE(st.rev[3]);  // l24 still dropped

  constexpr std::string_view uc_all = R"(,
    "user_constraint_array": [
      {"uid": 1, "name": "cap_all", "expression": "sum(line(all).flow) <= 900"}
    ])";
  const auto st_all = build_and_solve(chain_json(
      R"(, "line_direction_mode": "structural")", kPiecewise, "", uc_all));
  for (std::size_t u = 1; u <= 3; ++u) {
    CHECK(st_all.fwd[u]);
    CHECK(st_all.rev[u]);
  }
}

TEST_CASE("line_direction LP — one-sided tangent_signed_flow")  // NOLINT
{
  // K = 4 tangents at ±fmax/4, ±3fmax/4, L = 1 secant: two-sided
  // = 3 cols (f, ℓ, v) + 7 rows (4 tangents, 2 abs, chord); one-sided
  // = 2 cols + 3 rows (2 live tangents, chord on f).
  constexpr std::string_view tsf =
      R"(, "resistance": 1.0, "voltage": 100,
         "line_losses_mode": "tangent_signed_flow", "loss_segments": 4)";
  const std::string mo = R"(, "loss_flow_abs_cost": 0)";
  const auto off = build_and_solve(chain_json(mo, tsf));
  const auto fa =
      build_and_solve(chain_json(mo, tsf, R"(, "flow_direction": "ab")"));
  CHECK(off.cols - fa.cols == 2 * 1);
  CHECK(off.rows - fa.rows == 2 * 4);
  CHECK(fa.obj == doctest::Approx(off.obj).epsilon(1e-9));

  SUBCASE("L = 2 secants keep v columns with one equality row")
  {
    const std::string mo2 =
        R"(, "loss_flow_abs_cost": 0, "loss_secant_segments": 2)";
    const auto off2 = build_and_solve(chain_json(mo2, tsf));
    const auto fa2 =
        build_and_solve(chain_json(mo2, tsf, R"(, "flow_direction": "ab")"));
    CHECK(off2.cols == fa2.cols);
    CHECK(off2.rows - fa2.rows == 2 * (2 + 1));  // 2 tangents + 1 abs row
    CHECK(fa2.obj == doctest::Approx(off2.obj).epsilon(1e-9));
  }
  SUBCASE("SOS2 ladder keeps the live-side breakpoints only")
  {
    const std::string mo3 = R"(, "loss_flow_abs_cost": 0,
        "loss_secant_segments": 2, "loss_use_sos2": true)";
    // Needs a backend with native SOS2 (CPLEX / Gurobi); others throw
    // at assembly, which is not what this test is about.
    try {
      const auto off3 = build_and_solve(chain_json(mo3, tsf), /*solve=*/false);
      const auto fa3 =
          build_and_solve(chain_json(mo3, tsf, R"(, "flow_direction": "ab")"),
                          /*solve=*/false);
      // 2L+1 = 5 λ columns → L+1 = 3.
      CHECK(off3.cols - fa3.cols == 2 * 2);
    } catch (const std::exception& e) {
      MESSAGE("SOS2 subcase skipped: " << std::string {e.what()});
    }
  }
}

TEST_CASE(  // NOLINT
    "line_direction LP — node_angle keeps the angle limit of radial lines")
{
  // Lossless chain under node_angle with a binding angle bound: l12
  // carries 140 MW but |θ1 − θ2| ≤ 2·theta_max caps it (x = 0.1), so
  // part of the load is curtailed.  structural must NOT drop the KVL
  // rows of radial lines (they carry that angle limit): same optimum,
  // only the reverse / dead-end flow columns disappear.
  const std::string na = R"(, "kirchhoff_mode": "node_angle")";
  const auto off = build_and_solve(chain_json(na, ""));
  const auto st = build_and_solve(chain_json(
      std::string {na} + R"(, "line_direction_mode": "structural")", ""));
  REQUIRE(off.optimal);
  REQUIRE(st.optimal);
  CHECK(off.obj > 2800.0 + 1.0);  // the angle limit binds
  CHECK(st.obj == doctest::Approx(off.obj).epsilon(1e-9));
  CHECK(st.cols < off.cols);
  CHECK(st.rows < off.rows);  // the dead end's KVL rows
  CHECK_FALSE(st.fwd[3]);
}

TEST_CASE(  // NOLINT
    "line_direction LP — negative-price stub: no phantom loss when one-way")
{
  // b1 has a 300 MW must-run unit (soft pmin, 100 $/MWh shortfall) and
  // only 80 + 60 MW of load downstream.  With both directions the LP can
  // burn the surplus as fictitious loss by circulating flow on the
  // lossy lines (at loss_cost_eps per MWh); one-way lines cannot, so
  // the shortfall is paid instead (higher, honest cost).
  constexpr std::string_view lossy =
      R"(, "resistance": 10.0, "voltage": 100, "line_losses_mode": "piecewise",
         "loss_segments": 3)";
  auto json_for = [&](std::string_view mo)
  {
    auto j = chain_json(mo, lossy);
    const std::string from = R"("gcost": 10, "capacity": 500})";
    const std::string to =
        R"("gcost": 0, "capacity": 500, "pmin": 300, "pmin_fcost": 100})";
    j.replace(j.find(from), from.size(), to);
    return j;
  };
  const auto off = build_and_solve(json_for(""));
  const auto st =
      build_and_solve(json_for(R"(, "line_direction_mode": "structural")"));
  REQUIRE(off.optimal);
  REQUIRE(st.optimal);
  double circ_off = 0.0;
  for (std::size_t u = 1; u <= 3; ++u) {
    circ_off += std::min(off.flow_p[u], off.flow_n[u]);
    // Structural: no line carries both directions at once.
    CHECK(std::min(st.flow_p[u], st.flow_n[u]) == doctest::Approx(0.0));
  }
  // Off: the surplus is burned as fictitious loss by two-way flow on
  // the radial lines l32 / l24 (both directions loaded in one block).
  CHECK(circ_off > 1.0);
  CHECK(std::min(off.flow_p[2], off.flow_n[2])
            + std::min(off.flow_p[3], off.flow_n[3])
        > 1.0);
  // Structural: l12 has no reverse column, l32 no forward one, the
  // dead-end l24 is not built.
  CHECK_FALSE(st.rev[1]);
  CHECK_FALSE(st.fwd[2]);
  CHECK_FALSE(st.fwd[3]);
  CHECK_FALSE(st.rev[3]);
  // The phantom-loss sink is gone, so the must-run shortfall is paid:
  // the objective rises (off ≈ 32, structural ≈ 3520.6 here).  This is
  // the expected, physical answer, not a regression.
  CHECK(st.obj > off.obj + 1000.0);
}

// ─── Tier 3: small e2e on IEEE cases ───────────────────────────────────

#ifdef GTOPT_TEST_CASES_DIR
namespace
{

struct CaseRun
{
  double obj {};
  int cols {};
  int rows {};
};

CaseRun solve_case(std::string_view dir,
                   std::string_view file,
                   std::string_view mode,
                   std::string_view kirchhoff = "")
{
  const auto path = std::filesystem::path {GTOPT_TEST_CASES_DIR} / dir / file;
  auto parsed = parse_planning_files({path.string()});
  REQUIRE_MESSAGE(parsed.has_value(), parsed.error_or(""));
  auto planning = std::move(*parsed);
  planning.options.sddp_options.api_enabled = false;
  planning.options.model_options.line_direction_mode =
      OptName {std::string {mode}};
  if (!kirchhoff.empty()) {
    planning.options.model_options.kirchhoff_mode =
        OptName {std::string {kirchhoff}};
  }
  PlanningLP plp {std::move(planning), LpMatrixOptions {}};
  const auto res = plp.resolve(SolverOptions {});
  REQUIRE(res.has_value());
  const auto& li = plp.systems().front().front().linear_interface();
  REQUIRE(li.is_optimal());
  return {
      .obj = li.get_obj_value(),
      .cols = static_cast<int>(li.get_numcols()),
      .rows = static_cast<int>(li.get_numrows()),
  };
}

}  // namespace

TEST_CASE("line_direction e2e — IEEE cases, off vs structural")  // NOLINT
{
  struct Case
  {
    std::string_view dir;
    std::string_view file;
  };
  for (const auto& c :
       {
           Case {.dir = "ieee_9b", .file = "ieee_9b.json"},
           Case {.dir = "ieee30b", .file = "ieee30b.json"},
           Case {.dir = "ieee_118b", .file = "ieee_118b.json"},
       })
  {
    CAPTURE(c.dir);
    const auto off = solve_case(c.dir, c.file, "off");
    const auto st = solve_case(c.dir, c.file, "structural");
    CHECK(st.obj == doctest::Approx(off.obj).epsilon(1e-7));
    CHECK(st.cols <= off.cols);
    const auto off_na = solve_case(c.dir, c.file, "off", "node_angle");
    const auto st_na = solve_case(c.dir, c.file, "structural", "node_angle");
    CHECK(st_na.obj == doctest::Approx(off_na.obj).epsilon(1e-7));
    CHECK(st_na.rows <= off_na.rows);
  }
}
#endif
