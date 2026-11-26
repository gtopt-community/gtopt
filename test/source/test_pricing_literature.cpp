/**
 * @file      test_pricing_literature.cpp
 * @brief     UC pricing modes against published non-convex pricing examples
 * @date      2026-11-24
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * `model_options.pricing_mode` (fixed / relaxed / fast_start) checked
 * against small textbook examples from the non-convex pricing literature.
 * Parameters are typed in from the cited papers; prices / dispatch /
 * commitment prices are compared with the published values.  Where gtopt
 * has no exact equivalent the known relationship is pinned instead and the
 * comment says why — agreement is never faked.
 *
 * Mode ↔ literature vocabulary:
 *   fixed      = "restricted" / "IP" pricing (O'Neill et al. 2005), the
 *                restricted model of Gribik, Hogan & Pope (2007), the
 *                "LMP" model of Hua & Baldick (2017) / Eldridge et al.
 *   relaxed    = LP relaxation of the gtopt UC formulation (PJM "integer
 *                relaxation" when every unit is fast-start; Eldridge et
 *                al. "aCHP").  It equals the convex-hull (minimum-uplift)
 *                price whenever the relaxation of every unit is the convex
 *                hull of its own feasible set — always true for the
 *                single-period examples below, NOT for the multi-period
 *                ramp-constrained Hua & Baldick Example 2.
 *   fast_start = PJM / NYISO fast-start pricing: integers pinned except
 *                the u of fast-start units, relaxed to [0, 1].
 *
 * Benchmarks:
 *   1. Scarf's example as adapted by Hogan & Ring (2003) — the O'Neill et
 *      al. (2005) Smokestack / High-Tech economy plus a Med-Tech type with
 *      a minimum output; table of IP and minimum-uplift prices per demand.
 *   2. Gribik, Hogan & Pope (2007) two-plant example (figures only).
 *   3. PJM (2017) integer-relaxation fast-start pricing, Examples 1 and 2
 *      (the proposal behind FERC docket EL18-34).
 *   4. Hua & Baldick (2017) Examples 1 and 2 (LMP / CHP / CHPq).
 *   5. Eldridge, O'Neill & Hobbs (2018) replicated block-loaded example.
 *
 * The e2e cases `cases/uc_pricing_lit_*` reuse benchmarks 1, 3 and 4 and
 * `scripts/gtopt_pricing_uplift/tests/test_literature.py` checks the
 * published uplift figures.  Summary table: docs/unit-commitment.md §11.1.
 */

// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
#include <algorithm>
#include <array>
#include <cstddef>
#include <format>
#include <initializer_list>
#include <optional>
#include <ranges>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/bus_lp.hpp>
#include <gtopt/commitment_lp.hpp>
#include <gtopt/generator_lp.hpp>
#include <gtopt/json/json_planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/pricing_mode.hpp>

#include "solver_test_helpers.hpp"

using namespace gtopt;

// Named outer namespace: the unity build shares one TU with other tests
// whose anonymous-namespace helpers (e.g. `Solved`) would collide.
namespace pricing_lit
{

namespace
{

// ─────────────────────────────────────────────────────────────────────────
// Case builder: JSON text from small element snippets
// ─────────────────────────────────────────────────────────────────────────

[[nodiscard]] std::string join(const std::vector<std::string>& items)
{
  std::string out;
  for (const auto& [i, s] : std::views::enumerate(items)) {
    if (i > 0) {
      out += ",\n";
    }
    out += s;
  }
  return out;
}

[[nodiscard]] std::string num_array(const std::vector<double>& v)
{
  std::string out = "[";
  for (const auto& [i, x] : std::views::enumerate(v)) {
    if (i > 0) {
      out += ", ";
    }
    out += std::format("{}", x);
  }
  return out + "]";
}

/// Optional commitment members (unset → member omitted).
struct UcSpec
{
  std::string generator;
  double pmin {0.0};
  double noload {0.0};
  double startup {0.0};
  double initial_status {0.0};
  std::optional<double> ramp_up {};
  std::optional<double> startup_ramp {};
  std::optional<bool> fast_start {};
};

/// Piecewise-linear cost (via a unit-price fuel): cumulative breakpoints
/// and per-segment slopes [$/MWh].
struct Pwl
{
  std::vector<double> breaks;
  std::vector<double> slopes;
};

/// 1-MW secant linearisation of `C(p) = alpha·p + beta/2·p²` on
/// [0, pmax]: segments [k − ½, k + ½], so every integer MW is a segment
/// midpoint where the secant slope `alpha + beta/2·(a + b)` equals the
/// quadratic marginal cost `alpha + beta·k` exactly.
[[nodiscard]] Pwl secant_pwl(double alpha, double beta, double pmax)
{
  Pwl pwl;
  double a = 0.0;
  for (double b = 0.5; a < pmax; b += 1.0) {
    const double hi = (b < pmax) ? b : pmax;
    pwl.breaks.push_back(hi);
    pwl.slopes.push_back(alpha + (beta / 2.0 * (a + hi)));
    a = hi;
  }
  return pwl;
}

class CaseBuilder
{
public:
  explicit CaseBuilder(std::string name, std::vector<double> durations)
      : m_name_(std::move(name))
      , m_durations_(std::move(durations))
  {
  }

  CaseBuilder& bus(std::string_view name)
  {
    m_buses_.push_back(std::format(
        R"({{"uid": {}, "name": "{}"}})", m_buses_.size() + 1, name));
    return *this;
  }

  CaseBuilder& line(std::string_view name,
                    std::string_view a,
                    std::string_view b,
                    double tmax)
  {
    m_lines_.push_back(std::format(
        R"({{"uid": {}, "name": "{}", "bus_a": "{}", "bus_b": "{}", )"
        R"("reactance": 0.1, "tmax_ab": {}, "tmax_ba": {}}})",
        m_lines_.size() + 1,
        name,
        a,
        b,
        tmax,
        tmax));
    return *this;
  }

  CaseBuilder& gen(std::string_view name,
                   std::string_view bus,
                   double pmax,
                   double gcost,
                   const std::optional<Pwl>& pwl = {})
  {
    std::string extra;
    if (pwl) {
      if (m_fuels_.empty()) {
        m_fuels_.emplace_back(R"({"uid": 1, "name": "unit_fuel", "price": 1})");
      }
      extra = std::format(R"(, "fuel": "unit_fuel", "pmax_segments": {}, )"
                          R"("heat_rate_segments": {})",
                          num_array(pwl->breaks),
                          num_array(pwl->slopes));
    }
    m_gens_.push_back(std::format(
        R"({{"uid": {}, "name": "{}", "bus": "{}", "pmin": 0, "pmax": {}, )"
        R"("gcost": {}, "capacity": {}{}}})",
        m_gens_.size() + 1,
        name,
        bus,
        pmax,
        gcost,
        pmax,
        extra));
    return *this;
  }

  CaseBuilder& demand(std::string_view name,
                      std::string_view bus,
                      const std::vector<double>& load)
  {
    m_demands_.push_back(
        std::format(R"({{"uid": {}, "name": "{}", "bus": "{}", "lmax": [{}]}})",
                    m_demands_.size() + 1,
                    name,
                    bus,
                    num_array(load)));
    return *this;
  }

  CaseBuilder& commitment(const UcSpec& uc)
  {
    std::string extra;
    if (uc.ramp_up) {
      extra += std::format(R"(, "ramp_up": {})", *uc.ramp_up);
    }
    if (uc.startup_ramp) {
      extra += std::format(R"(, "startup_ramp": {})", *uc.startup_ramp);
    }
    if (uc.fast_start) {
      extra += std::format(R"(, "fast_start": {})", *uc.fast_start);
    }
    m_cmts_.push_back(std::format(
        R"({{"uid": {}, "name": "{}_uc", "generator": "{}", "pmin": {}, )"
        R"("noload_cost": {}, "startup_cost": {}, "initial_status": {}, )"
        R"("initial_hours": 10{}}})",
        m_cmts_.size() + 1,
        uc.generator,
        uc.generator,
        uc.pmin,
        uc.noload,
        uc.startup,
        uc.initial_status,
        extra));
    return *this;
  }

  [[nodiscard]] std::string json() const
  {
    std::vector<std::string> blocks;
    for (const auto& [i, d] : std::views::enumerate(m_durations_)) {
      blocks.push_back(
          std::format(R"({{"uid": {}, "duration": {}}})", i + 1, d));
    }
    std::string sys = std::format(
        R"("name": "{}", "bus_array": [{}], "generator_array": [{}], )"
        R"("demand_array": [{}], "commitment_array": [{}])",
        m_name_,
        join(m_buses_),
        join(m_gens_),
        join(m_demands_),
        join(m_cmts_));
    if (!m_lines_.empty()) {
      sys += std::format(R"(, "line_array": [{}])", join(m_lines_));
    }
    if (!m_fuels_.empty()) {
      sys += std::format(R"(, "fuel_array": [{}])", join(m_fuels_));
    }
    return std::format(
        R"({{"options": {{"annual_discount_rate": 0.0, )"
        R"("output_format": "csv", "output_compression": "uncompressed", )"
        R"("model_options": {{"use_single_bus": false, "use_kirchhoff": )"
        R"(true, "scale_objective": 1, "demand_fail_cost": 10000}}}}, )"
        R"("simulation": {{"block_array": [{}], "stage_array": [{{"uid": 1, )"
        R"("first_block": 0, "count_block": {}, "active": 1, )"
        R"("chronological": true}}], "scenario_array": [{{"uid": 1, )"
        R"("probability_factor": 1}}]}}, "system": {{{}}}}})",
        join(blocks),
        m_durations_.size(),
        sys);
  }

private:
  std::string m_name_;
  std::vector<double> m_durations_;
  std::vector<std::string> m_buses_;
  std::vector<std::string> m_lines_;
  std::vector<std::string> m_gens_;
  std::vector<std::string> m_demands_;
  std::vector<std::string> m_cmts_;
  std::vector<std::string> m_fuels_;
};

// ─────────────────────────────────────────────────────────────────────────
// Solve + read back (single scene / phase / stage)
// ─────────────────────────────────────────────────────────────────────────

struct Solved
{
  double obj {0.0};
  std::vector<std::vector<double>> lmp;  ///< [bus][block]
  std::vector<std::vector<double>> gen;  ///< [generator][block]
  std::vector<std::vector<double>> u;  ///< [commitment][block]
  std::vector<std::vector<double>> gen_rc;  ///< reduced cost of p
  std::vector<std::vector<double>> u_rc;  ///< reduced cost of u
  PricingOutcome pricing {};

  [[nodiscard]] double gen_total(std::size_t first,
                                 std::size_t count,
                                 std::size_t block = 0) const
  {
    double s = 0.0;
    for (std::size_t g = first; g < first + count; ++g) {
      s += gen[g][block];
    }
    return s;
  }

  [[nodiscard]] int committed(std::size_t first,
                              std::size_t count,
                              std::size_t block = 0) const
  {
    int n = 0;
    for (std::size_t c = first; c < first + count; ++c) {
      n += (u[c][block] > 0.5) ? 1 : 0;
    }
    return n;
  }

  /// O'Neill et al. commitment ("start-up") price of commitment @p c on
  /// generator @p g with capacity @p pmax: the reduced cost of the pinned
  /// u PLUS the capacity rent of the unit.  gtopt states the capacity
  /// twice — the generator column bound p ≤ pmax and the commitment row
  /// p ≤ pmax·u — so a solver may put the rent θ on either (degenerate
  /// dual); O'Neill's single row p ≤ M·z gives π = F − θ·M.  Summing the
  /// two placements, rc_u + pmax·min(rc_p, 0), is invariant.
  [[nodiscard]] double commitment_price(std::size_t c,
                                        std::size_t g,
                                        double pmax,
                                        std::size_t block = 0) const
  {
    return u_rc[c][block] + (pmax * std::min(gen_rc[g][block], 0.0));
  }
};

[[nodiscard]] Solved solve_case(const std::string& json,
                                std::string_view mode,
                                const std::string& solver,
                                OptBool transition_status_rows = {})
{
  auto planning = daw::json::from_json<Planning>(json);
  planning.options.lp_matrix_options.solver_name = solver;
  planning.options.model_options.pricing_mode = std::string {mode};
  planning.options.model_options.transition_status_rows =
      transition_status_rows;
  PlanningLP planning_lp(std::move(planning));
  REQUIRE(planning_lp.resolve().has_value());

  auto& system = planning_lp.system(SceneIndex {0}, PhaseIndex {0});
  auto& li = system.linear_interface();
  REQUIRE(li.is_optimal());
  const auto& scenario = planning_lp.simulation().scenarios().front();
  const auto& stage = planning_lp.simulation().stages().front();

  Solved r;
  r.obj = li.get_obj_value();
  r.pricing = li.last_pricing();
  const auto duals = li.get_row_dual();
  const auto sol = li.get_col_sol();
  const auto rcs = li.get_col_cost();

  for (const auto& bus : system.elements<BusLP>()) {
    auto& dst = r.lmp.emplace_back();
    for (const auto& row :
         bus.balance_rows_at(scenario, stage) | std::views::values)
    {
      dst.push_back(duals[row]);
    }
  }
  for (const auto& g : system.elements<GeneratorLP>()) {
    auto& dst = r.gen.emplace_back();
    auto& drc = r.gen_rc.emplace_back();
    for (const auto& col :
         g.generation_cols_at(scenario, stage) | std::views::values)
    {
      dst.push_back(sol[col]);
      drc.push_back(rcs[col]);
    }
  }
  for (const auto& c : system.elements<CommitmentLP>()) {
    auto& du = r.u.emplace_back();
    auto& drc = r.u_rc.emplace_back();
    const auto* ucols = c.find_status_cols(scenario, stage);
    REQUIRE(ucols != nullptr);
    for (const auto& col : *ucols | std::views::values) {
      du.push_back(sol[col]);
      drc.push_back(rcs[col]);
    }
  }
  return r;
}

/// Exact MIP backends that recover fix-integer duals on UC fixtures
/// (same filter as test_pricing_mode.cpp: cbc / unlicensed gurobi out).
[[nodiscard]] std::vector<std::string> pricing_solvers()
{
  std::vector<std::string> out;
  for (auto& s : solver_test::exact_mip_solvers()) {
    if (s != "cbc" && s != "gurobi") {
      out.push_back(std::move(s));
    }
  }
  return out;
}

template<typename Body>
void for_each_solver(const Body& body,
                     std::initializer_list<std::string_view> skip = {})
{
  const auto solvers = pricing_solvers();
  if (solvers.empty()) {
    MESSAGE("No suitable MIP solver available — skipping");
    return;
  }
  for (const auto& solver : solvers) {
    if (std::ranges::contains(skip, std::string_view {solver})) {
      continue;
    }
    CAPTURE(solver);
    if (!solver_test::run_or_skip_license([&] { body(solver); })) {
      MESSAGE("skipped " << solver << " (license)");
    }
  }
}

constexpr std::string_view k_fixed = "fixed";
constexpr std::string_view k_relaxed = "relaxed";
constexpr std::string_view k_fast_start = "fast_start";

// ─────────────────────────────────────────────────────────────────────────
// 1. Scarf's example (O'Neill et al. 2005) adapted by Hogan & Ring (2003)
// ─────────────────────────────────────────────────────────────────────────

/// Hogan & Ring, "On Minimum-Uplift Pricing for Electricity Markets",
/// Harvard EPG, 19 Mar 2003, "An Example" + appended table (adapted Scarf
/// example). https://lmpmarketdesign.com/papers/Hogan_Ring_minuplift_031903.pdf
/// The two original types are Scarf (1994) as used by O'Neill, Sotkiewicz,
/// Hobbs, Rothkopf & Stewart, "Efficient market-clearing prices in markets
/// with nonconvexities", EJOR 164(1):269-285, 2005,
/// doi:10.1016/j.ejor.2003.12.011 (Tables 1-3).
///
///   type        count  capacity  min  start-up (fixed) $  marginal $
///   Smokestack    6       16      0        53                 3
///   High Tech     5        7      0        30                 2
///   Med Tech      5        6      2         0                 7
///
/// Single period, fixed demand.  Each unit is one generator + commitment;
/// the fixed cost is the no-load cost of the (1 h) period, the unit is
/// initially on so u is the only cost-bearing commitment column (its
/// reduced cost in the fixed pass is then O'Neill's start-up price).
[[nodiscard]] std::string scarf_json(double demand)
{
  CaseBuilder b {"scarf_hogan_ring", {1.0}};
  b.bus("b1").demand("load", "b1", {demand});
  const auto add = [&](std::string_view type,
                       int count,
                       double cap,
                       double pmin,
                       double fixed,
                       double mc)
  {
    for (int i = 1; i <= count; ++i) {
      const auto name = std::format("{}{}", type, i);
      b.gen(name, "b1", cap, mc);
      b.commitment(UcSpec {
          .generator = name,
          .pmin = pmin,
          .noload = fixed,
          .initial_status = 1.0,
      });
    }
  };
  add("ss", 6, 16.0, 0.0, 53.0, 3.0);
  add("ht", 5, 7.0, 0.0, 30.0, 2.0);
  add("mt", 5, 6.0, 2.0, 0.0, 7.0);
  return b.json();
}

/// Capacity of Scarf unit @p c (6 Smokestack, 5 High Tech, 5 Med Tech).
[[nodiscard]] constexpr double scarf_cap(std::size_t c) noexcept
{
  if (c < 6) {
    return 16.0;
  }
  return (c < 11) ? 7.0 : 6.0;
}

/// One row of Hogan & Ring's appended table (columns: demand, Smokestack
/// number / output, High Tech number / output, Med Tech output, total
/// cost, IP commodity price, IP uplift, MinUP commodity price, MinUP
/// uplift).  Rows chosen where both the IP price and the convex-hull price
/// are unique (a unit strictly inside its range; demand not at a
/// convex-hull kink 35 / 131) and the least-cost commitment is unique.
struct ScarfRow
{
  double demand;
  int n_ss;
  double out_ss;
  int n_ht;
  double out_ht;
  double out_mt;
  double cost;
  double ip_price;
  double ip_uplift;
  double minup_price;
  double minup_uplift;  ///< see the Python tier (gtopt_pricing_uplift)
};

// Positional rows mirror the published table's columns.
// NOLINTBEGIN(modernize-use-designated-initializers)
// clang-format off
constexpr std::array scarf_rows {
  ScarfRow {1,   0, 0,  1, 1,  0,  32,  2, 30,   6.286, 25.714},
  ScarfRow {10,  0, 0,  1, 7,  3,  65,  7, -5,   6.286, 2.143},
  ScarfRow {22,  1, 15, 1, 7,  0,  142, 3, 76,   6.286, 3.714},
  ScarfRow {40,  1, 16, 3, 21, 3,  254, 7, -26,  6.312, 2.438},
  ScarfRow {61,  3, 47, 2, 14, 0,  388, 3, 205,  6.312, 3.875},
  ScarfRow {88,  4, 64, 3, 21, 3,  557, 7, -59,  6.312, 2.438},
  ScarfRow {145, 6, 96, 5, 35, 14, 924, 7, -91,  7.000, 0.000},
};
// clang-format on
// NOLINTEND(modernize-use-designated-initializers)

/// Exact convex-hull price of the adapted economy: the Lagrangian dual
/// maximiser.  Supply at price λ is 35 (all High Tech) on
/// (30/7 + 2, 53/16 + 3), 131 (+ all Smokestack) on (6.3125, 7).
[[nodiscard]] double scarf_ch_price(double demand)
{
  if (demand < 35.0) {
    return 2.0 + (30.0 / 7.0);  // 6.2857 — High Tech average cost
  }
  if (demand < 131.0) {
    return 3.0 + (53.0 / 16.0);  // 6.3125 — Smokestack average cost
  }
  return 7.0;  // Med Tech marginal cost
}

}  // namespace

TEST_CASE("Scarf / Hogan-Ring: IP and minimum-uplift prices")  // NOLINT
{
  for_each_solver(
      [](const std::string& solver)
      {
        for (const auto& row : scarf_rows) {
          CAPTURE(row.demand);
          const auto json = scarf_json(row.demand);

          // ── fixed = O'Neill et al. IP pricing ──
          const auto fx = solve_case(json, k_fixed, solver);
          // Least-cost commitment and dispatch = the published table.
          CHECK(fx.obj == doctest::Approx(row.cost));
          CHECK(fx.committed(0, 6) == row.n_ss);
          CHECK(fx.committed(6, 5) == row.n_ht);
          CHECK(fx.gen_total(0, 6) == doctest::Approx(row.out_ss));
          CHECK(fx.gen_total(6, 5) == doctest::Approx(row.out_ht));
          CHECK(fx.gen_total(11, 5) == doctest::Approx(row.out_mt));
          // IP commodity price: match.
          CHECK(fx.lmp[0][0] == doctest::Approx(row.ip_price));
          // IP "uplift" = Σ commitment (start-up) prices of the committed
          // units (see `Solved::commitment_price`): match.
          double ip_uplift = 0.0;
          for (std::size_t c = 0; c < 16; ++c) {
            if (fx.u[c][0] > 0.5) {
              ip_uplift += fx.commitment_price(c, c, scarf_cap(c));
            }
          }
          CHECK(ip_uplift == doctest::Approx(row.ip_uplift));

          // ── relaxed = convex-hull (minimum-uplift) price ──
          // Single period: each unit's LP relaxation is the convex hull of
          // its own set, so the LP-relaxation dual IS the convex-hull
          // price.  Published MinUP prices are rounded to 3 decimals.
          const auto rx = solve_case(json, k_relaxed, solver);
          CHECK(rx.obj == doctest::Approx(row.cost));  // dispatch = MIP
          CHECK(rx.lmp[0][0] == doctest::Approx(scarf_ch_price(row.demand)));
          CHECK(rx.lmp[0][0] == doctest::Approx(row.minup_price).epsilon(1e-3));
          CHECK(rx.pricing.applied == PricingMode::relaxed);
          CHECK(rx.pricing.relaxed_columns == 16);

          // ── fast_start: no min up/down → every unit is fast-start by
          // the auto rule, so fast_start pricing == relaxed pricing ──
          const auto fs = solve_case(json, k_fast_start, solver);
          CHECK(fs.lmp[0][0] == doctest::Approx(rx.lmp[0][0]));
          CHECK(fs.pricing.applied == PricingMode::fast_start);
          CHECK(fs.pricing.relaxed_columns == 16);
        }
      });
}

TEST_CASE(
    "O'Neill et al. 2005 Table 3 dual price set I at demand 61")  // NOLINT
{
  // O'Neill et al. Table 3, set I (valid for every demand 55..70): the
  // commodity price is 3, the start-up price of every committed
  // Smokestack 53 and of every committed High Tech 23 (= 30 − 7·(3 − 2)).
  for_each_solver(
      [](const std::string& solver)
      {
        const auto fx = solve_case(scarf_json(61.0), k_fixed, solver);
        CHECK(fx.lmp[0][0] == doctest::Approx(3.0));
        for (std::size_t c = 0; c < 11; ++c) {
          CAPTURE(c);
          if (fx.u[c][0] > 0.5) {
            CHECK(fx.commitment_price(c, c, scarf_cap(c))
                  == doctest::Approx(c < 6 ? 53.0 : 23.0));
          }
        }
      });
}

// ─────────────────────────────────────────────────────────────────────────
// 2. Gribik, Hogan & Pope (2007) two-plant example
// ─────────────────────────────────────────────────────────────────────────

namespace
{

/// Gribik, Hogan & Pope, "Market-Clearing Electricity Prices and Energy
/// Uplift", Harvard EPG working paper, 31 Dec 2007, plants A and B of the
/// table on p. 3.
/// https://lmpmarketdesign.com/papers/Gribik_Hogan_Pope_Price_Uplift_123107.pdf
///
///   plant A: no fixed cost, 65 $/MWh on 0-100 MW, 110 $/MWh on 100-200 MW
///   plant B: fixed cost 6000 $, 40 $/MWh on 0-100 MW, 90 $/MWh on 100-200
///
/// A has no commitment (two generators, one per cost step); B is one
/// committable generator with a two-segment piecewise cost.  The paper
/// publishes the restricted / dispatchable / convex-hull curves as FIGURES
/// only, so the expected values below are derived from the paper's data
/// and checked against the figure descriptions (§ "Uplift Payments": no
/// uplift below 100 MW; restricted = convex hull above 300 MW).
[[nodiscard]] std::string ghp_json(double load)
{
  CaseBuilder b {"ghp_two_plant", {1.0}};
  b.bus("b1").demand("load", "b1", {load});
  b.gen("a1", "b1", 100.0, 65.0).gen("a2", "b1", 100.0, 110.0);
  b.gen("b",
        "b1",
        200.0,
        0.0,
        Pwl {.breaks = {100.0, 200.0}, .slopes = {40.0, 90.0}});
  b.commitment(
      UcSpec {.generator = "b", .noload = 6000.0, .initial_status = 1.0});
  return b.json();
}

struct GhpRow
{
  double load;
  double cost;
  bool b_on;
  double restricted;  ///< restricted-model MC (fixed)
  double convex_hull;  ///< convex-hull MC (relaxed)
  double dispatchable;  ///< GHP "dispatchable" model MC (NOT gtopt)
};

// Positional rows mirror the published table's columns.
// NOLINTBEGIN(modernize-use-designated-initializers)
// clang-format off
constexpr std::array ghp_rows {
  GhpRow {50,  3250,  false, 65,  65,  65},
  GhpRow {150, 12000, false, 110, 95,  70},
  GhpRow {190, 15850, true,  65,  95,  70},
  GhpRow {250, 21000, true,  90,  95,  110},
  GhpRow {350, 31000, true,  110, 110, 120},
};
// clang-format on
// NOLINTEND(modernize-use-designated-initializers)

}  // namespace

TEST_CASE("Gribik-Hogan-Pope 2007 two-plant example")  // NOLINT
{
  // Restricted MC: the marginal cost of the optimal commitment — volatile
  // (65 → 110 → 65 → 90 → 110 as A-only gives way to A&B at 177.8 MW).
  // Convex hull: B's cost envelope from the origin is the chord to full
  // output, (6000 + 4000 + 9000)/200 = 95 $/MWh, so MC_h = 65 | 95 | 110.
  // GHP's "dispatchable" model spreads the fixed cost pro rata over the
  // full capacity (+30 $/MWh on each of B's steps: 70 / 120) — a looser
  // relaxation.  gtopt's `relaxed` is NOT that model: the commitment
  // gates each heat-rate kink row by u (`p − s_k ≤ P̄_k·u`), i.e. the
  // perspective of B's cost, which is exactly its convex envelope.  So
  // gtopt relaxed = GHP convex hull, ≠ GHP dispatchable (pinned below).
  for_each_solver(
      [](const std::string& solver)
      {
        for (const auto& row : ghp_rows) {
          CAPTURE(row.load);
          const auto json = ghp_json(row.load);
          const auto fx = solve_case(json, k_fixed, solver);
          CHECK(fx.obj == doctest::Approx(row.cost));
          CHECK((fx.u[0][0] > 0.5) == row.b_on);
          CHECK(fx.lmp[0][0] == doctest::Approx(row.restricted));

          const auto rx = solve_case(json, k_relaxed, solver);
          CHECK(rx.obj == doctest::Approx(row.cost));
          CHECK(rx.lmp[0][0] == doctest::Approx(row.convex_hull));
          if (row.dispatchable != row.convex_hull) {
            CHECK(rx.lmp[0][0] != doctest::Approx(row.dispatchable));
          }
          // B has no min up/down: fast-start by the auto rule.
          const auto fs = solve_case(json, k_fast_start, solver);
          CHECK(fs.lmp[0][0] == doctest::Approx(row.convex_hull));
        }
      });
}

// ─────────────────────────────────────────────────────────────────────────
// 3. PJM (2017) integer-relaxation fast-start pricing examples
// ─────────────────────────────────────────────────────────────────────────

namespace
{

/// PJM Interconnection, "Proposed Enhancements to Energy Price
/// Formation", 15 Nov 2017, Appendix B "Integer Relaxation Model and
/// Examples", Tables 2-7 (the proposal FERC took up in docket EL18-34).
/// Original location (pjm.com special reports, since moved):
/// https://www.pjm.com/-/media/library/reports-notices/special-reports/20171115-proposed-enhancements-to-energy-price-formation.ashx
/// announcement:
/// https://insidelines.pjm.com/pjm-proposes-enhancements-to-electric-energy-price-formation/
///
/// Three-bus triangle, every reactance 0.1 p.u.  G1 @ bus 1, G2 @ bus 2,
/// G3 and G4 @ bus 3.  G2 / G4 are block-loaded at 100 MW (pmin = pmax)
/// with a 100 $ start-up; G1 / G3 are flexible with quadratic cost
/// alpha·p + beta/2·p² (beta = 0.1 $/MWh/MW is the MC slope).  gtopt has
/// no quadratic cost: G1 / G3 use the 1-MW secant PWL of `secant_pwl`,
/// whose slope equals the quadratic MC at every integer MW — all the
/// published operating points are integers, so prices match exactly
/// except where a unit sits at its pmax (see Example 2).
struct PjmSpec
{
  double load1;
  double load3;
  std::string_view limited_a;  ///< the one finite-limit line
  std::string_view limited_b;
  double limit;
  double g1_pmax, g1_alpha, g2_alpha, g3_pmax, g3_alpha, g4_alpha;
};

[[nodiscard]] std::string pjm_json(const PjmSpec& s)
{
  constexpr double big = 9999.0;
  CaseBuilder b {"pjm_2017", {1.0}};
  b.bus("b1").bus("b2").bus("b3");
  const auto lim = [&](std::string_view x, std::string_view y)
  { return (x == s.limited_a && y == s.limited_b) ? s.limit : big; };
  b.line("l12", "b1", "b2", lim("b1", "b2"))
      .line("l13", "b1", "b3", lim("b1", "b3"))
      .line("l23", "b2", "b3", lim("b2", "b3"));
  b.gen("g1", "b1", s.g1_pmax, 0.0, secant_pwl(s.g1_alpha, 0.1, s.g1_pmax));
  b.gen("g2", "b2", 100.0, s.g2_alpha);
  b.gen("g3", "b3", s.g3_pmax, 0.0, secant_pwl(s.g3_alpha, 0.1, s.g3_pmax));
  b.gen("g4", "b3", 100.0, s.g4_alpha);
  b.commitment(UcSpec {.generator = "g2", .pmin = 100.0, .startup = 100.0});
  b.commitment(UcSpec {.generator = "g4", .pmin = 100.0, .startup = 100.0});
  b.demand("d1", "b1", {s.load1}).demand("d3", "b3", {s.load3});
  return b.json();
}

/// MindOpt 2.3.0 dies with SIGSEGV inside libmindoptpower (its own MIP
/// worker thread, no gtopt frame on the stack) on these piecewise-cost
/// MIPs; CPLEX and HiGHS solve them.  Third-party crash, not a gtopt bug.
constexpr std::initializer_list<std::string_view> pjm_skip {"mindopt"};

// PJM Table 2 (Example 1) and Table 5 (Example 2).
constexpr PjmSpec pjm_ex1 {
    .load1 = 600.0,
    .load3 = 100.0,
    .limited_a = "b1",
    .limited_b = "b3",
    .limit = 50.0,
    .g1_pmax = 500.0,
    .g1_alpha = 20.0,
    .g2_alpha = 75.0,
    .g3_pmax = 1000.0,
    .g3_alpha = 20.0,
    .g4_alpha = 40.0,
};
constexpr PjmSpec pjm_ex2 {
    .load1 = 650.0,
    .load3 = 100.0,
    .limited_a = "b1",
    .limited_b = "b2",
    .limit = 100.0,
    .g1_pmax = 450.0,
    .g1_alpha = 30.0,
    .g2_alpha = 10.0,
    .g3_pmax = 250.0,
    .g3_alpha = 20.0,
    .g4_alpha = 10.0,
};

}  // namespace

TEST_CASE("PJM 2017 fast-start pricing Example 1")  // NOLINT
{
  // Published (Tables 3 / 4): dispatch G1 475, G2 100, G3 125, G4 0.
  // Restricted LMP (17.50 congestion split): 67.50 / 50.00 / 32.50.
  // Integer relaxation: 117 / 76 / 35 — G2 sets bus 2 at 75 + 100/100 and
  // bus 1 = 2·76 − 35 (line 1-3 binding).  G2 / G4 are fast-start by the
  // auto rule (no min up/down), so fast_start == PJM's pricing run; with
  // no other integers, relaxed is the same LP.
  const auto json = pjm_json(pjm_ex1);
  for_each_solver(
      [&](const std::string& solver)
      {
        for (const auto mode : {k_fixed, k_fast_start, k_relaxed}) {
          CAPTURE(mode);
          const auto r = solve_case(json, mode, solver);
          CHECK(r.gen[0][0] == doctest::Approx(475.0));
          CHECK(r.gen[1][0] == doctest::Approx(100.0));
          CHECK(r.gen[2][0] == doctest::Approx(125.0));
          CHECK(r.gen[3][0] == doctest::Approx(0.0));
          if (mode == k_fixed) {
            CHECK(r.lmp[0][0] == doctest::Approx(67.5));
            CHECK(r.lmp[1][0] == doctest::Approx(50.0));
            CHECK(r.lmp[2][0] == doctest::Approx(32.5));
          } else {
            CHECK(r.lmp[0][0] == doctest::Approx(117.0));
            CHECK(r.lmp[1][0] == doctest::Approx(76.0));
            CHECK(r.lmp[2][0] == doctest::Approx(35.0));
            CHECK(r.pricing.relaxed_columns == 2);
          }
        }
      },
      pjm_skip);
}

TEST_CASE("PJM 2017 fast-start pricing Example 2")  // NOLINT
{
  // Published (Tables 6 / 7): dispatch G1 450, G2 100, G3 100, G4 100.
  // Restricted LMP 75 / −15 / 30; integer relaxation 69.80 / 11.00 / 40.40.
  const auto json = pjm_json(pjm_ex2);
  for_each_solver(
      [&](const std::string& solver)
      {
        for (const auto mode : {k_fixed, k_fast_start, k_relaxed}) {
          CAPTURE(mode);
          const auto r = solve_case(json, mode, solver);
          CHECK(r.gen[0][0] == doctest::Approx(450.0));
          CHECK(r.gen[1][0] == doctest::Approx(100.0));
          CHECK(r.gen[2][0] == doctest::Approx(100.0));
          CHECK(r.gen[3][0] == doctest::Approx(100.0));
          if (mode == k_fixed) {
            // Dual-degenerate: G1 sits at its 450 MW pmax while line 1-2
            // binds, so any λ1 ≥ MC(450) supports the dispatch (G1 earns
            // the capacity rent).  PJM reports the bottom of the range,
            // 75; gtopt's bottom is the secant slope of the last PWL
            // segment [449.5, 450], 74.975.  Bus 3 (G3 at 100 MW, inside a
            // segment) is unique; the network ties the rest: λ1 + λ2 = 2λ3.
            CHECK(r.lmp[2][0] == doctest::Approx(30.0));
            CHECK(r.lmp[0][0] >= 74.975 - 1e-6);
            CHECK(r.lmp[0][0] + r.lmp[1][0]
                  == doctest::Approx(2.0 * r.lmp[2][0]));
          } else {
            CHECK(r.lmp[0][0] == doctest::Approx(69.8));
            CHECK(r.lmp[1][0] == doctest::Approx(11.0));
            CHECK(r.lmp[2][0] == doctest::Approx(40.4));
          }
        }
      },
      pjm_skip);
}

// ─────────────────────────────────────────────────────────────────────────
// 4. Hua & Baldick (2017) Examples 1 and 2
// ─────────────────────────────────────────────────────────────────────────

namespace
{

/// B. Hua and R. Baldick, "A Convex Primal Formulation for Convex Hull
/// Pricing", IEEE Trans. Power Syst. 32(5):3814-3823, 2017,
/// doi:10.1109/TPWRS.2016.2637718, arXiv:1605.05002 §5.1-5.2.
///
/// Example 1 (Table 1, after Schiro et al. 2015 + start-up costs): one
/// period, 35 MW; both units off initially.
///   unit 1: start-up 100, 50 $/MWh, 10..50 MW
///   unit 2: start-up 100, 10 $/MWh, block-loaded 50 MW
[[nodiscard]] std::string hb1_json(std::optional<bool> fs1 = {},
                                   std::optional<bool> fs2 = {})
{
  CaseBuilder b {"hua_baldick_ex1", {1.0}};
  b.bus("b1").demand("load", "b1", {35.0});
  b.gen("unit1", "b1", 50.0, 50.0).gen("unit2", "b1", 50.0, 10.0);
  b.commitment(UcSpec {
      .generator = "unit1",
      .pmin = 10.0,
      .startup = 100.0,
      .fast_start = fs1,
  });
  b.commitment(UcSpec {
      .generator = "unit2",
      .pmin = 50.0,
      .startup = 100.0,
      .fast_start = fs2,
  });
  return b.json();
}

/// Example 2 (Tables 3-5, from MISO "Convex Hull Workshop 3", 2010):
/// three hours, demand 70 / 100 / 170, both units off initially.
///   unit 1: no-load 0,   60 $/MWh, 0..100 MW, ramp 120 MW/h
///   unit 2: no-load 600, 56 $/MWh, 0..100 MW, ramp  60 MW/h
/// The start-up ramp is taken equal to the ramp rate (the paper's "ramping
/// constraints require unit 2 to commit at t = 2" needs it ≤ 60 MW).
[[nodiscard]] std::string hb2_json()
{
  CaseBuilder b {"hua_baldick_ex2", {1.0, 1.0, 1.0}};
  b.bus("b1").demand("load", "b1", {70.0, 100.0, 170.0});
  b.gen("unit1", "b1", 100.0, 60.0).gen("unit2", "b1", 100.0, 56.0);
  b.commitment(UcSpec {
      .generator = "unit1",
      .ramp_up = 120.0,
      .startup_ramp = 120.0,
  });
  b.commitment(UcSpec {
      .generator = "unit2",
      .noload = 600.0,
      .ramp_up = 60.0,
      .startup_ramp = 60.0,
  });
  return b.json();
}

}  // namespace

TEST_CASE("Hua-Baldick 2017 Example 1: LMP / CHP / CHPq")  // NOLINT
{
  // Published (Table 2): LMP π = 50 (U1 100, U2 1900); CHP π = 12
  // (U1 1430, U2 0); CHPq π = 52 (unit 2 excluded; U1 30).
  // fixed   = LMP: unit 1 alone at 35 MW is marginal → 50.  match.
  // relaxed = CHP: one period, the LP relaxation is the convex hull; the
  //           block-loaded unit 2 at 10 + 100/50 = 12 sets it.  match.
  // fast_start with only unit 1 fast-start: unit 2 stays pinned OFF, so
  //           the pricing LP is the problem without unit 2 — exactly
  //           CHPq (and Eldridge et al.'s rCHP, 0 ≤ u ≤ u*):
  //           50 + 100/50 = 52.  match.
  // Uplift figures: scripts/gtopt_pricing_uplift/tests/test_literature.py.
  for_each_solver(
      [](const std::string& solver)
      {
        const auto check_dispatch = [](const Solved& r)
        {
          CHECK(r.gen[0][0] == doctest::Approx(35.0));
          CHECK(r.gen[1][0] == doctest::Approx(0.0));
          CHECK(r.obj == doctest::Approx((50.0 * 35.0) + 100.0));
        };
        const auto lmp = solve_case(hb1_json(), k_fixed, solver);
        check_dispatch(lmp);
        CHECK(lmp.lmp[0][0] == doctest::Approx(50.0));

        const auto chp = solve_case(hb1_json(), k_relaxed, solver);
        check_dispatch(chp);
        CHECK(chp.lmp[0][0] == doctest::Approx(12.0));

        // Auto rule (no min up/down): both units fast-start == relaxed.
        const auto fs_all = solve_case(hb1_json(), k_fast_start, solver);
        CHECK(fs_all.lmp[0][0] == doctest::Approx(12.0));

        const auto chpq =
            solve_case(hb1_json(true, false), k_fast_start, solver);
        check_dispatch(chpq);
        CHECK(chpq.lmp[0][0] == doctest::Approx(52.0));
        CHECK(chpq.pricing.relaxed_columns == 1);
      });
}

TEST_CASE("Hua-Baldick 2017 Example 2: ramp-coupled convex hull")  // NOLINT
{
  // Published (Table 5): LMP (60, 60, 60), U2 = 560 make-whole;
  // aCHP1 (60, 60, 64); exact CHP = aCHP2 (60, 60, 65.6).
  //
  // fixed = LMP: unit 1 is marginal in every hour.  match.
  //
  // relaxed ≠ CHP — expected difference.  With ramping the convex hull of
  // a unit's multi-period set is NOT its LP relaxation; H&B need extra
  // valid inequalities (T = 2) to reach 65.6.  gtopt's relaxation is the
  // 3-binary UC with ramp rows p_t − p_{t−1} ≤ RU·u_{t−1} + SU·v_t, which
  // is weaker in hour 3 even than H&B's aCHP1 description.
  //
  // Without the transition status rows (v ≤ u, w ≤ 1 − u;
  // `transition_status_rows = false`, the formulation before 2026-11-25)
  // the relaxation runs both units at fractional commitment (u1 = 0.854
  // in hours 1-2, u2 = 0.146 / 0.7 in hours 2-3) and its hour-3 dual is
  // 820/13 = 63.0769 $/MWh.  Verified independently: the same LP written
  // out by hand in scipy/HiGHS (objective 20569.23) gives the same duals
  // (60, 60, 820/13).  With the rows (default) the relaxation is tighter
  // and the hour-3 dual is 314/5 = 62.8 (LP objective 20596; scipy
  // agrees).  The first-block ramp-up row uses v[0]
  // (p[0] − SU·v[0] ≤ 0): with only the status rows and the former
  // constant RHS p[0] ≤ SU it was 501/8 = 62.625 (objective 20583.75);
  // duals need not move monotonically with a tighter LP.  Either way:
  // LMP 60 < gtopt relaxed < aCHP1 64 < CHP 65.6.
  for_each_solver(
      [](const std::string& solver)
      {
        const auto check_dispatch = [](const Solved& r)
        {
          // Table 4: p1 = 70 / 40 / 70, p2 = 0 / 60 / 100, x2 = 0 / 1 / 1.
          CHECK(r.gen[0][0] == doctest::Approx(70.0));
          CHECK(r.gen[0][1] == doctest::Approx(40.0));
          CHECK(r.gen[0][2] == doctest::Approx(70.0));
          CHECK(r.gen[1][0] == doctest::Approx(0.0));
          CHECK(r.gen[1][1] == doctest::Approx(60.0));
          CHECK(r.gen[1][2] == doctest::Approx(100.0));
          CHECK(r.u[1][1] == doctest::Approx(1.0));
          CHECK(r.obj
                == doctest::Approx((60.0 * 180.0) + (56.0 * 160.0)
                                   + (600.0 * 2.0)));
        };
        const auto lmp = solve_case(hb2_json(), k_fixed, solver);
        check_dispatch(lmp);
        for (std::size_t t = 0; t < 3; ++t) {
          CHECK(lmp.lmp[0][t] == doctest::Approx(60.0));
        }

        const auto rx = solve_case(hb2_json(), k_relaxed, solver);
        check_dispatch(rx);
        CHECK(rx.lmp[0][0] == doctest::Approx(60.0));
        CHECK(rx.lmp[0][1] == doctest::Approx(60.0));
        CHECK(rx.lmp[0][2] == doctest::Approx(314.0 / 5.0));
        CHECK(rx.lmp[0][2] < 64.0);  // weaker than aCHP1 …
        CHECK(rx.lmp[0][2] < 65.6);  // … and than the exact CHP

        // The formulation without the transition status rows.
        const auto rx_old =
            solve_case(hb2_json(), k_relaxed, solver, OptBool {false});
        check_dispatch(rx_old);
        CHECK(rx_old.lmp[0][0] == doctest::Approx(60.0));
        CHECK(rx_old.lmp[0][1] == doctest::Approx(60.0));
        CHECK(rx_old.lmp[0][2] == doctest::Approx(820.0 / 13.0));

        const auto fs = solve_case(hb2_json(), k_fast_start, solver);
        for (std::size_t t = 0; t < 3; ++t) {
          CHECK(fs.lmp[0][t] == doctest::Approx(rx.lmp[0][t]));
        }
      });
}

// ─────────────────────────────────────────────────────────────────────────
// 5. Eldridge, O'Neill & Hobbs (2018) replicated block-loaded example
// ─────────────────────────────────────────────────────────────────────────

namespace
{

/// B. Eldridge, R. O'Neill and B. F. Hobbs, "Pricing in Day-Ahead
/// Electricity Markets with Near-Optimal Unit Commitment", EPRG Working
/// Paper 1840, Univ. of Cambridge, Nov 2018, Appendix A.2, Tables 8-9
/// (N = 0 self-commitments).
/// https://www.jbs.cam.ac.uk/wp-content/uploads/2023/12/eprg-wp1840.pdf
///
/// Five each of Gen1 (block-loaded 25 MW at 15 $/MWh, no start-up cost),
/// Gen2 (0..25 MW at 10) and Gen3 (0..25 MW at 25); demand 225 + ε.  The
/// convex Gen2 / Gen3 fleets are one 125 MW generator each (identical
/// LP).  ε = 0.5 MW.  (The single-copy example of Appendix A.1 is not used:
/// its Table 6 cost data, Table 7 costs and the text's (25 − 11)·25 − 100
/// do not agree with each other.)
[[nodiscard]] std::string eoh_json()
{
  CaseBuilder b {"eldridge_oneill_hobbs_a2", {1.0}};
  b.bus("b1").demand("load", "b1", {225.5});
  for (int i = 1; i <= 5; ++i) {
    const auto name = std::format("gen1_{}", i);
    b.gen(name, "b1", 25.0, 15.0);
    b.commitment(UcSpec {.generator = name, .pmin = 25.0});
  }
  b.gen("gen2", "b1", 125.0, 10.0).gen("gen3", "b1", 125.0, 25.0);
  return b.json();
}

}  // namespace

TEST_CASE("Eldridge-O'Neill-Hobbs 2018 replicated example")  // NOLINT
{
  // Published (Table 9, N < 5): four Gen1 committed, Gen2 125 MW, Gen3 ε,
  // cost 2750 (+ 25·ε), LMP 25 $/MWh.  fixed: match.
  // relaxed (their aCHP) is not tabulated for this example.  Derived: the
  // block-loaded Gen1 becomes divisible at 15 $/MWh, so λ = 15, and with
  // no fixed cost the relaxation has no duality gap (LP value 2750 + 15·ε).
  for_each_solver(
      [](const std::string& solver)
      {
        const auto fx = solve_case(eoh_json(), k_fixed, solver);
        CHECK(fx.committed(0, 5) == 4);
        CHECK(fx.gen[5][0] == doctest::Approx(125.0));
        CHECK(fx.gen[6][0] == doctest::Approx(0.5));
        CHECK(fx.obj == doctest::Approx(2750.0 + (25.0 * 0.5)));
        CHECK(fx.lmp[0][0] == doctest::Approx(25.0));

        const auto rx = solve_case(eoh_json(), k_relaxed, solver);
        CHECK(rx.obj == doctest::Approx(fx.obj));
        CHECK(rx.lmp[0][0] == doctest::Approx(15.0));
      });
}

}  // namespace pricing_lit
