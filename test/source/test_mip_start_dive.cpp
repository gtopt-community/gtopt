/**
 * @file      test_mip_start_dive.cpp
 * @brief     Propagation-dive MIP start: feasibility on UC violation families
 * @date      2026-11-23
 * @author    marcelo
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Small synthetic unit-commitment MIPs, one per constraint family that made
 * the legacy round → domain-rules start infeasible on the PCP weekly UC
 * (CPLEX "No solution found from 1 MIP starts"):
 *
 *   - combined-cycle configuration exclusivity  (u_A + u_B <= 1)
 *   - plant-config => unit                      (-cc + u_1 + u_2 >= 0)
 *   - min up time + an operational `u <= 0` row  (legacy run extension
 *                                                  breaks the outage row)
 *   - reserve requirement gated by u            (mixed rows)
 *
 * Each LP has a UNIQUE fractional relaxation, so the legacy candidate is
 * deterministic: the test proves it fails (its fixed-integer LP is
 * infeasible) and that the dive start is VERIFIED feasible.  The solver-free
 * part (`propagation_dive`, `violated_integer_rows`) is tested directly.
 */

#include <initializer_list>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/domain_rules.hpp>
#include <gtopt/json/json_monolithic_options.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/mip_start.hpp>
#include <gtopt/mip_start_dive.hpp>
#include <gtopt/monolithic_options.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace
{

/// Exact MIP solvers to exercise (never CLP: these are MIPs).
[[nodiscard]] std::vector<std::string> dive_mip_solvers()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  std::vector<std::string> out;
  for (const auto* name : {"highs", "cplex"}) {
    if (reg.has_solver(name) && reg.supports_mip(name)) {
      out.emplace_back(name);
    }
  }
  return out;
}

/// Tiny LP builder over `LinearProblem` (raw == physical: no scaling).
struct Builder
{
  LinearProblem lp {};
  int next_uid {0};  ///< unique row metadata (duplicates are rejected)

  ColIndex bin(double cost)
  {
    return lp.add_col(SparseCol {
        .lowb = 0.0,
        .uppb = 1.0,
        .cost = cost,
        .is_integer = true,
    });
  }

  ColIndex cont(double lo, double up, double cost)
  {
    return lp.add_col(SparseCol {
        .lowb = lo,
        .uppb = up,
        .cost = cost,
    });
  }

  void row(std::initializer_list<std::pair<ColIndex, double>> terms,
           double lo,
           double up,
           std::string_view cname = "row")
  {
    SparseRow r {
        .lowb = lo,
        .uppb = up,
        .class_name = "Test",
        .constraint_name = cname,
        .variable_uid = Uid {next_uid++},
    };
    for (const auto& [c, a] : terms) {
      r[c] = a;
    }
    (void)lp.add_row(std::move(r));
  }

  [[nodiscard]] FlatLinearProblem flat() { return lp.flatten({}); }
};

constexpr double kInf = SparseRow::DblMax;
constexpr double kPenalty = 1000.0;  // unserved-energy cost

/// Integer columns of a flat LP.
[[nodiscard]] std::vector<int> int_cols_of(const FlatLinearProblem& f)
{
  return {f.colint.begin(), f.colint.end()};
}

/// The legacy start (round 0.5 + default domain rules) of the solved,
/// relaxed `li`, and whether its fixed-integer LP is feasible.
[[nodiscard]] bool legacy_start_feasible(
    LinearInterface& li,
    std::span<const int> ints,
    std::span<const CommitmentRunInfo> commitments = {})
{
  const MipStartOptions opts {};
  const SolverOptions sopts {};
  MipStartContext ctx {
      .li = li,
      .relax_opts = sopts,
      .int_cols = ints,
      .opts = opts,
      .commitments = commitments,
      .injections = {},
  };
  const auto cand = detail::rounded_start_with_rules(ctx, "legacy");
  REQUIRE(!cand.empty());
  return solve_fixed_integer_lp(li, ints, cand, sopts).feasible;
}

/// Run the full family check on `flat` for every MIP solver:
///   1. legacy candidate's fixed-integer LP is INFEASIBLE;
///   2. `apply_mip_start` (dive, default) yields a VERIFIED start;
///   3. the MIP then solves, with optimum <= the start's objective.
void check_family(const FlatLinearProblem& flat,
                  std::span<const CommitmentRunInfo> commitments,
                  bool pure_integer_violation,
                  const MipStartOptions& base = {})
{
  const auto ints = int_cols_of(flat);
  const auto solvers = dive_mip_solvers();
  if (solvers.empty()) {
    MESSAGE("no MIP solver available — skipping");
    return;
  }
  for (const auto& solver : solvers) {
    CAPTURE(solver);
    {
      LinearInterface li(solver, flat);
      (void)li.relax_integers();
      REQUIRE(li.resolve(SolverOptions {}).has_value());
      REQUIRE(li.is_optimal());
      CHECK_FALSE(legacy_start_feasible(li, ints, commitments));
    }
    LinearInterface li(solver, flat);
    MipStartOptions opts = base;
    opts.enabled = true;
    const auto rep =
        apply_mip_start(li, SolverOptions {}, opts, commitments, {}, &flat);
    REQUIRE(rep.has_value());
    CHECK(rep->relaxation_feasible);
    CHECK(rep->source == "dive");
    CHECK(rep->start_verified);
    CHECK(rep->injected);
    if (pure_integer_violation && base.dive.propagate.value_or(true)) {
      CHECK(rep->violated_int_rows_rounded > 0);
      CHECK(rep->violated_int_rows_dive == 0);
    }
    REQUIRE(rep->start_obj.has_value());
    CHECK(rep->start_obj.value_or(0.0) >= rep->relax_obj.value_or(0.0) - 1e-6);
    // Integrality is back and the MIP solves; a feasible start can never beat
    // the optimum.
    for (const int j : ints) {
      CHECK(li.is_integer(ColIndex {j}));
    }
    REQUIRE(li.resolve(SolverOptions {}).has_value());
    REQUIRE(li.is_optimal());
    CHECK(li.get_obj_value() <= rep->start_obj.value_or(0.0) + 1e-6);
  }
}

/// Exclusivity: configs A and B of one plant, u_A + u_B <= 1.  Zone 1
/// (demand 50) is served only by A, zone 2 only by B, p <= 100 u, so the
/// unique relaxation is u_A = u_B = 0.5 — rounding sets both ON.
[[nodiscard]] FlatLinearProblem exclusivity_lp()
{
  Builder b;
  const auto ua = b.bin(1.0);
  const auto ub = b.bin(1.0);
  const auto pa = b.cont(0.0, kInf, 1.0);
  const auto pb = b.cont(0.0, kInf, 1.0);
  const auto fa = b.cont(0.0, kInf, kPenalty);
  const auto fb = b.cont(0.0, kInf, kPenalty);
  b.row({{pa, 1.0}, {fa, 1.0}}, 50.0, 50.0, "balance");
  b.row({{pb, 1.0}, {fb, 1.0}}, 50.0, 50.0, "balance");
  b.row({{pa, 1.0}, {ua, -100.0}}, -kInf, 0.0, "pmax");
  b.row({{pb, 1.0}, {ub, -100.0}}, -kInf, 0.0, "pmax");
  b.row({{ua, 1.0}, {ub, 1.0}}, -kInf, 1.0, "config_exclusivity");
  return b.flat();
}

/// Plant-config => unit: the CC aggregate status cc requires a unit
/// (-cc + u1 + u2 >= 0).  cc serves 60 (cc = 0.6), each unit serves its own
/// 30 (u = 0.3) — rounding turns cc ON with no unit ON.
[[nodiscard]] FlatLinearProblem config_unit_lp()
{
  Builder b;
  const auto cc = b.bin(1.0);
  const auto u1 = b.bin(1.0);
  const auto u2 = b.bin(1.0);
  const auto pc = b.cont(0.0, kInf, 1.0);
  const auto p1 = b.cont(0.0, kInf, 1.0);
  const auto p2 = b.cont(0.0, kInf, 1.0);
  const auto fc = b.cont(0.0, kInf, kPenalty);
  const auto f1 = b.cont(0.0, kInf, kPenalty);
  const auto f2 = b.cont(0.0, kInf, kPenalty);
  b.row({{pc, 1.0}, {fc, 1.0}}, 60.0, 60.0, "balance");
  b.row({{p1, 1.0}, {f1, 1.0}}, 30.0, 30.0, "balance");
  b.row({{p2, 1.0}, {f2, 1.0}}, 30.0, 30.0, "balance");
  b.row({{pc, 1.0}, {cc, -100.0}}, -kInf, 0.0, "pmax");
  b.row({{p1, 1.0}, {u1, -100.0}}, -kInf, 0.0, "pmax");
  b.row({{p2, 1.0}, {u2, -100.0}}, -kInf, 0.0, "pmax");
  b.row({{cc, -1.0}, {u1, 1.0}, {u2, 1.0}}, 0.0, kInf, "config_unit");
  return b.flat();
}

/// Min up time 3 periods (4 periods, initially off) with an operational
/// outage row u2 <= 0.  Demand 50 at t0 only.  Unique relaxation:
/// u = (0.5, 1, 0, 0.5), v = (0.5, 0.5, 0, 0).  Rounding gives u = (1,1,0,1);
/// the legacy MinUpDownRule then extends the 2-period run through t2,
/// breaking the `u2 <= 0` row.
struct MinUpLp
{
  FlatLinearProblem flat;
  CommitmentRunInfo info;
};

[[nodiscard]] MinUpLp min_up_outage_lp()
{
  Builder b;
  constexpr int T = 4;
  std::vector<ColIndex> u;
  std::vector<ColIndex> v;
  std::vector<ColIndex> w;
  for (int t = 0; t < T; ++t) {
    u.push_back(b.bin(1.0));
    v.push_back(b.bin(0.1));
    w.push_back(b.bin(0.0));
  }
  const auto p0 = b.cont(0.0, kInf, 1.0);
  const auto f0 = b.cont(0.0, kInf, kPenalty);
  b.row({{p0, 1.0}, {f0, 1.0}}, 50.0, 50.0, "balance");
  b.row({{p0, 1.0}, {u[0], -100.0}}, -kInf, 0.0, "pmax");
  // Logic: u_t - u_{t-1} - v_t + w_t = 0, u_{-1} = 0.
  b.row({{u[0], 1.0}, {v[0], -1.0}, {w[0], 1.0}}, 0.0, 0.0, "logic");
  for (int t = 1; t < T; ++t) {
    b.row({{u[t], 1.0}, {u[t - 1], -1.0}, {v[t], -1.0}, {w[t], 1.0}},
          0.0,
          0.0,
          "logic");
  }
  // Min up (commitment_lp.cpp C6): Σ_{q=t}^{t+UT-1} u_q - UT·v_t >= 0.
  b.row({{u[0], 1.0}, {u[1], 1.0}, {u[2], 1.0}, {v[0], -3.0}},
        0.0,
        kInf,
        "min_up");
  b.row({{u[1], 1.0}, {u[2], 1.0}, {u[3], 1.0}, {v[1], -3.0}},
        0.0,
        kInf,
        "min_up");
  b.row({{u[2], 1.0}, {u[3], 1.0}, {v[2], -2.0}}, 0.0, kInf, "min_up");
  // Operational outage row (uc_operational.pampl shape): u2 <= 0.
  b.row({{u[2], 1.0}}, -kInf, 0.0, "operational");

  MinUpLp out {.flat = b.flat(), .info = {}};
  out.info.min_up_hours = 3.0;
  out.info.initial_status = 0.0;
  for (int t = 0; t < T; ++t) {
    out.info.status_cols.push_back(static_cast<int>(u[t]));
    out.info.startup_cols.push_back(static_cast<int>(v[t]));
    out.info.shutdown_cols.push_back(static_cast<int>(w[t]));
    out.info.durations.push_back(1.0);
  }
  return out;
}

/// Reserve gated by u: three units, r_i <= 30 u_i, r_i <= 12 (ramp), and a
/// HARD requirement Σ r >= 36 — every unit must be ON, but the unique
/// relaxation is u_i = 0.4, which rounds to all OFF (no reserve at all).
[[nodiscard]] FlatLinearProblem reserve_lp()
{
  Builder b;
  std::vector<ColIndex> u;
  std::vector<ColIndex> r;
  for (int i = 0; i < 3; ++i) {
    u.push_back(b.bin(10.0));
    r.push_back(b.cont(0.0, 12.0, 0.0));
  }
  for (int i = 0; i < 3; ++i) {
    b.row({{r[i], 1.0}, {u[i], -30.0}}, -kInf, 0.0, "reserve_gate");
  }
  b.row({{r[0], 1.0}, {r[1], 1.0}, {r[2], 1.0}}, 36.0, kInf, "requirement");
  return b.flat();
}

}  // namespace

TEST_CASE("propagation_dive enforces exclusivity and config rows")  // NOLINT
{
  SUBCASE("exclusivity: both targets ON -> one forced OFF")
  {
    const auto flat = exclusivity_lp();
    const auto ints = int_cols_of(flat);
    const auto n = static_cast<std::size_t>(flat.ncols);
    std::vector<double> target(n, 0.0);
    std::vector<double> prio(n, 0.0);
    for (const int j : ints) {
      target[static_cast<std::size_t>(j)] = 1.0;  // rounded 0.5 -> 1
    }
    CHECK(
        violated_integer_rows(flat, flat.rowlb, flat.rowub, ints, target, 1e20)
            .size()
        == 1);
    const auto out = propagation_dive(DiveInput {
        .flat = &flat,
        .col_low = flat.collb,
        .col_upp = flat.colub,
        .row_low = flat.rowlb,
        .row_upp = flat.rowub,
        .int_cols = ints,
        .target = target,
        .priority = prio,
        .infinity = 1e20,
    });
    CHECK(out.deferred == 0);
    CHECK(out.forced >= 1);
    CHECK(violated_integer_rows(
              flat, flat.rowlb, flat.rowub, ints, out.values, 1e20)
              .empty());
    double on = 0.0;
    for (const int j : ints) {
      on += out.values[static_cast<std::size_t>(j)];
    }
    CHECK(on == doctest::Approx(1.0));
  }

  SUBCASE("live LP with columns appended after flatten")
  {
    // E.g. a future-cost alpha column added after the flat snapshot: the
    // extra columns have no flat entries and keep their bounds/targets.
    const auto flat = exclusivity_lp();
    auto ints = int_cols_of(flat);
    const auto n = static_cast<std::size_t>(flat.ncols);
    std::vector<double> col_low(flat.collb.begin(), flat.collb.end());
    std::vector<double> col_upp(flat.colub.begin(), flat.colub.end());
    col_low.push_back(0.0);  // extra continuous column
    col_upp.push_back(1e20);
    col_low.push_back(0.0);  // extra integer column
    col_upp.push_back(1.0);
    ints.push_back(static_cast<int>(n + 1));
    std::vector<double> target(n + 2, 0.0);
    const std::vector<double> prio(n + 2, 0.0);
    for (const int j : ints) {
      target[static_cast<std::size_t>(j)] = 1.0;
    }
    target[n] = 7.5;
    const auto out = propagation_dive(DiveInput {
        .flat = &flat,
        .col_low = col_low,
        .col_upp = col_upp,
        .row_low = flat.rowlb,
        .row_upp = flat.rowub,
        .int_cols = ints,
        .target = target,
        .priority = prio,
        .infinity = 1e20,
    });
    REQUIRE(out.values.size() == n + 2);
    CHECK(out.values[n] == doctest::Approx(7.5));
    CHECK(out.values[n + 1] == doctest::Approx(1.0));
    CHECK(violated_integer_rows(
              flat, flat.rowlb, flat.rowub, ints, out.values, 1e20)
              .empty());
  }

  SUBCASE("mixed reserve rows force every unit ON at the root")
  {
    const auto flat = reserve_lp();
    const auto ints = int_cols_of(flat);
    const auto n = static_cast<std::size_t>(flat.ncols);
    const std::vector<double> target(n, 0.0);  // rounded 0.4 -> 0
    const std::vector<double> prio(n, 0.0);
    const auto out = propagation_dive(DiveInput {
        .flat = &flat,
        .col_low = flat.collb,
        .col_upp = flat.colub,
        .row_low = flat.rowlb,
        .row_upp = flat.rowub,
        .int_cols = ints,
        .target = target,
        .priority = prio,
        .infinity = 1e20,
    });
    CHECK(out.forced == 3);
    for (const int j : ints) {
      CHECK(out.values[static_cast<std::size_t>(j)] == doctest::Approx(1.0));
    }
  }

  SUBCASE("min-up + outage: an integral relaxation cell that cannot hold")
  {
    // u1 = 1 is integral in the relaxation, yet no integer start can keep it
    // (any run through t1 needs 3 periods, t2 is out) — the dive drops it.
    const auto m = min_up_outage_lp();
    const auto& flat = m.flat;
    const auto ints = int_cols_of(flat);
    const auto n = static_cast<std::size_t>(flat.ncols);
    std::vector<double> target(n, 0.0);
    std::vector<double> prio(n, 0.0);
    target[static_cast<std::size_t>(m.info.status_cols[1])] = 1.0;
    prio[static_cast<std::size_t>(m.info.status_cols[1])] = 1.0;
    const auto out = propagation_dive(DiveInput {
        .flat = &flat,
        .col_low = flat.collb,
        .col_upp = flat.colub,
        .row_low = flat.rowlb,
        .row_upp = flat.rowub,
        .int_cols = ints,
        .target = target,
        .priority = prio,
        .infinity = 1e20,
    });
    CHECK(out.values[static_cast<std::size_t>(m.info.status_cols[1])]
          == doctest::Approx(0.0));
    CHECK(out.deferred == 0);  // flipped or forced by root propagation
    CHECK(violated_integer_rows(
              flat, flat.rowlb, flat.rowub, ints, out.values, 1e20)
              .empty());
  }
}

TEST_CASE("dive MIP start: CC configuration exclusivity")  // NOLINT
{
  check_family(exclusivity_lp(), {}, /*pure_integer_violation=*/true);
}

TEST_CASE("dive MIP start: plant config requires a unit")  // NOLINT
{
  check_family(config_unit_lp(), {}, /*pure_integer_violation=*/true);
}

TEST_CASE("dive MIP start: min up time with operational u <= 0")  // NOLINT
{
  const auto m = min_up_outage_lp();
  const std::vector<CommitmentRunInfo> comm {m.info};
  check_family(m.flat, comm, /*pure_integer_violation=*/true);
}

TEST_CASE("dive MIP start: reserve requirement gated by u")  // NOLINT
{
  check_family(reserve_lp(), {}, /*pure_integer_violation=*/false);
}

TEST_CASE("dive MIP start: elastic repair without propagation")  // NOLINT
{
  // No propagation: the dive degrades to round + rules, and the elastic LP
  // repair must lift the reserve units back ON.
  MipStartOptions base;
  base.dive.propagate = false;
  check_family(reserve_lp(), {}, /*pure_integer_violation=*/false, base);
}

TEST_CASE("dive MIP start: repair disabled injects an unverified start")
// NOLINT
{
  MipStartOptions base;
  base.dive.propagate = false;
  base.dive.repair_rounds = 0;
  // Zero elastic rounds ⇒ no trouble cells ⇒ no sub-MIP: the unverified
  // candidate is injected for backend repair.
  const auto flat = reserve_lp();
  for (const auto& solver : dive_mip_solvers()) {
    CAPTURE(solver);
    LinearInterface li(solver, flat);
    MipStartOptions opts = base;
    opts.enabled = true;
    const auto rep = apply_mip_start(li, SolverOptions {}, opts, {}, {}, &flat);
    REQUIRE(rep.has_value());
    CHECK_FALSE(rep->start_verified);
    CHECK(rep->repair_rounds == 0);
    CHECK(rep->injected);
  }
}

TEST_CASE("dive MIP start: sub-MIP repair when elastic rounds run out")
// NOLINT
{
  // Without propagation the legacy rules extend the min-up run through the
  // outage (an `operational` u <= 0 row stays violated).  With no elastic
  // rounds, the bounded sub-MIP over the columns of the violated
  // pure-integer rows (+ short-row neighbours) must repair it.
  const auto m = min_up_outage_lp();
  const std::vector<CommitmentRunInfo> comm {m.info};
  MipStartOptions base;
  base.dive.propagate = false;
  base.dive.repair_rounds = 0;
  for (const auto& solver : dive_mip_solvers()) {
    CAPTURE(solver);
    LinearInterface li(solver, m.flat);
    MipStartOptions opts = base;
    opts.enabled = true;
    const auto rep =
        apply_mip_start(li, SolverOptions {}, opts, comm, {}, &m.flat);
    REQUIRE(rep.has_value());
    CHECK(rep->start_verified);
    CHECK(rep->repair_rounds == 0);
    CHECK(rep->sub_mip_used);
  }

  SUBCASE("one elastic round: verified, by the round or by the sub-MIP")
  {
    // Which one depends on the elastic LP vertex the backend returns.
    base.dive.repair_rounds = 1;
    for (const auto& solver : dive_mip_solvers()) {
      CAPTURE(solver);
      LinearInterface li(solver, m.flat);
      MipStartOptions opts = base;
      opts.enabled = true;
      const auto rep =
          apply_mip_start(li, SolverOptions {}, opts, comm, {}, &m.flat);
      REQUIRE(rep.has_value());
      CHECK(rep->start_verified);
      CHECK(rep->repair_rounds == 1);
    }
  }
}

TEST_CASE("dive MIP start: dive.enabled=false keeps the legacy generator")
// NOLINT
{
  MipStartOptions opts;
  opts.dive.enabled = false;
  CHECK(make_mip_start_generator(opts)->name() == "warmstart");
  opts.dive.enabled = true;
  CHECK(make_mip_start_generator(opts)->name() == "dive");
}

TEST_CASE("MipStartDive JSON round-trip and merge")  // NOLINT
{
  constexpr std::string_view json = R"({
    "enabled": true,
    "dive": {
      "enabled": true,
      "propagate": false,
      "verify": true,
      "repair_rounds": 5,
      "sub_mip": false,
      "sub_mip_time_limit": 30.0,
      "penalty": 250.0
    }
  })";
  auto opts = daw::json::from_json<MipStartOptions>(json);
  CHECK(opts.dive.enabled.value_or(false));
  CHECK_FALSE(opts.dive.propagate.value_or(true));
  CHECK(opts.dive.verify.value_or(false));
  CHECK(opts.dive.repair_rounds.value_or(0) == 5);
  CHECK_FALSE(opts.dive.sub_mip.value_or(true));
  CHECK(opts.dive.sub_mip_time_limit.value_or(0.0) == doctest::Approx(30.0));
  CHECK(opts.dive.penalty.value_or(0.0) == doctest::Approx(250.0));

  const auto back =
      daw::json::from_json<MipStartOptions>(daw::json::to_json(opts));
  CHECK(back.dive.repair_rounds.value_or(0) == 5);
  CHECK(back.dive.penalty.value_or(0.0) == doctest::Approx(250.0));

  MipStartOptions over;
  over.dive.repair_rounds = 1;
  over.dive.propagate = true;
  opts.merge(std::move(over));
  CHECK(opts.dive.repair_rounds.value_or(0) == 1);
  CHECK(opts.dive.propagate.value_or(false));
  CHECK(opts.dive.penalty.value_or(0.0) == doctest::Approx(250.0));
}
