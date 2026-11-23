// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_numerics_scaling_stress.cpp — deliberately ill-conditioned LP used to
// demonstrate (numerically, per solver backend) what gtopt's scaling
// machinery buys:
//
//   * per-element auto scaling   (`model_options.auto_scale`: reservoir
//     energy scale, `auto_scale_theta`, ...),
//   * `scale_objective`,
//   * row equilibration          (`lp_matrix_options.equilibration_method`).
//
// The fixture mixes, in ONE Kirchhoff network:
//   - a 3-reservoir Laja-like cascade (5586 / 300 / 0.5 hm3),
//   - batteries of 0.5 MWh and 0.01 MWh (7 decades below the reservoir),
//   - line reactances spanning 1e-5 .. 0.8 pu (tiny angle differences on
//     the stiff 1e-5 line),
//   - generator costs from 1e-3 to 4e2 $/MWh next to a 1e3 $/MWh fail cost,
//   - block durations from 0.25 h to 24 h.
//
// Test cases:
//   1. coefficient range / kappa / objective / primal residual, unscaled
//      vs scaled, on every available solver;
//   2. order sensitivity: permute the input order of the batteries and of
//      the equal-cost thermal units; objective and prices are
//      order-invariant, individual unit dispatch is not.
//
// Nothing here is faked: every assertion is a property that was observed
// to hold; where an hypothesis does NOT hold the test only documents the
// measured behaviour via MESSAGE().

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cmath>
#include <format>
#include <map>
#include <numeric>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

using namespace gtopt;

namespace numerics_scaling_stress_ns
{
namespace
{

constexpr std::size_t kBlocks = 6;

/// Unit-order parameters of the n-th identical battery (n = 1..5) and of
/// the 0.01 MWh "tiny" battery (n = 6).
[[nodiscard]] Battery make_battery(int n, bool harsh)
{
  const bool tiny = (n == 6);
  const double tiny_size = harsh ? 1e-4 : 0.01;
  const double size = tiny ? tiny_size : 0.5;
  return Battery {
      .uid = Uid {n},
      .name = "bat" + std::to_string(n),
      .bus = Uid {3},
      .input_efficiency = 0.95,
      .output_efficiency = 0.95,
      .emin = 0.0,
      .emax = size,
      .eini = 0.0,
      .pmax_charge = size,
      .pmax_discharge = size,
      .capacity = size,
  };
}

/// Thermal cost ladder (uid = 10 + n, all at bus 2): five units whose costs
/// differ by 1e-9, 1e-7, 1e-5, 1e-3 $/MWh, one far-cheap and one far-dear unit.
/// The unique optimum is the merit order, so any permutation-dependent
/// dispatch is a numerical artefact, not degeneracy.
struct Thermal
{
  int uid;
  double cost;
  double cap;
};
constexpr std::array<Thermal, 7> kThermals = {{
    {.uid = 11, .cost = 35.0, .cap = 100.0},
    {.uid = 12, .cost = 35.0 + 1e-9, .cap = 100.0},
    {.uid = 13, .cost = 35.0 + 1e-7, .cap = 100.0},
    {.uid = 14, .cost = 35.0 + 1e-5, .cap = 100.0},
    {.uid = 15, .cost = 35.0 + 1e-3, .cap = 100.0},
    {.uid = 16, .cost = 2e-3, .cap = 150.0},  // != hydro 1e-3: no tie
    {.uid = 17, .cost = 400.0, .cap = 400.0},
}};

[[nodiscard]] Generator make_thermal(int n)
{
  const auto& t = kThermals[static_cast<std::size_t>(n - 1)];
  return Generator {
      .uid = Uid {t.uid},
      .name = "therm" + std::to_string(n),
      .bus = Uid {2},
      .gcost = t.cost,
      .capacity = t.cap,
  };
}

/// Build the stress planning.  `bat_perm` / `gen_perm` give the order in
/// which batteries (1..6) and thermal units (1..3) enter the arrays.
[[nodiscard]] Planning make_stress_planning(const std::vector<int>& bat_perm,
                                            const std::vector<int>& gen_perm,
                                            bool scaled,
                                            bool harsh)
{
  Array<Battery> batteries;
  for (const int n : bat_perm) {
    batteries.push_back(make_battery(n, harsh));
  }

  Array<Generator> generators = {
      {
          .uid = Uid {1},
          .name = "hydro_gen",
          .bus = Uid {1},
          .gcost = harsh ? 1e-6 : 1e-3,
          .capacity = 700.0,
      },
      {
          .uid = Uid {2},
          .name = "wind",
          .bus = Uid {2},
          .gcost = 0.1,
          .capacity = 200.0,
      },
      {
          .uid = Uid {20},
          .name = "mid_gen",
          .bus = Uid {2},
          .gcost = harsh ? 1e-6 : 1e-3,
          .capacity = 255.0,
      },
      {
          .uid = Uid {21},
          .name = "pond_gen",
          .bus = Uid {3},
          .gcost = harsh ? 1e-6 : 1e-3,
          .capacity = 108.0,
      },
      {
          .uid = Uid {3},
          .name = "peaker",
          .bus = Uid {4},
          .gcost = 400.0,
          .capacity = 300.0,
      },
  };
  for (const int n : gen_perm) {
    generators.push_back(make_thermal(n));
  }

  using Prof = std::vector<std::vector<double>>;
  const Array<Demand> demands = {
      {
          .uid = Uid {1},
          .name = "d3",
          .bus = Uid {3},
          .lmax = Prof {{300.0, 900.0, 1400.0, 450.0, 1300.0, 250.0}},
      },
      {
          .uid = Uid {2},
          .name = "d4",
          .bus = Uid {4},
          .lmax = Prof {{100.0, 250.0, 400.0, 150.0, 350.0, 80.0}},
      },
  };

  // Ring with a 5-decade reactance spread (per-unit, no voltage).
  const Array<Line> lines = {
      {
          .uid = Uid {1},
          .name = "l12",
          .bus_a = Uid {1},
          .bus_b = Uid {2},
          .reactance = harsh ? 2e-6 : 1e-5,
          .tmax_ba = 10000.0,
          .tmax_ab = 10000.0,
      },
      {
          .uid = Uid {2},
          .name = "l23",
          .bus_a = Uid {2},
          .bus_b = Uid {3},
          .reactance = harsh ? 1e-3 : 2e-3,
          .tmax_ba = 2000.0,
          .tmax_ab = 2000.0,
      },
      {
          .uid = Uid {3},
          .name = "l34",
          .bus_a = Uid {3},
          .bus_b = Uid {4},
          .reactance = 0.05,
          .tmax_ba = 1000.0,
          .tmax_ab = 1000.0,
      },
      {
          .uid = Uid {4},
          .name = "l41",
          .bus_a = Uid {4},
          .bus_b = Uid {1},
          .reactance = 0.8,
          .tmax_ba = 1000.0,
          .tmax_ab = 1000.0,
      },
  };

  // Hydro cascade modelled on Laja (gtopt_case_2y: ELTORO emax 5585.888 hm3,
  // flow_conversion_rate 0.0036, production_factor 4.8): head lake, medium
  // reservoir, tiny pondage, each turbine feeding a different bus.
  const Array<Junction> junctions = {
      {
          .uid = Uid {1},
          .name = "j_eltoro",
      },
      {
          .uid = Uid {2},
          .name = "j_mid",
      },
      {
          .uid = Uid {3},
          .name = "j_pond",
      },
      {
          .uid = Uid {4},
          .name = "j_dn",
          .drain = true,
      },
  };
  const Array<Waterway> waterways = {
      {
          .uid = Uid {1},
          .name = "ww1",
          .junction_a = Uid {1},
          .junction_b = Uid {2},
          .fmin = 0.0,
          .fmax = 162.0,
      },
      {
          .uid = Uid {2},
          .name = "ww2",
          .junction_a = Uid {2},
          .junction_b = Uid {3},
          .fmin = 0.0,
          .fmax = 170.0,
      },
      {
          .uid = Uid {3},
          .name = "ww3",
          .junction_a = Uid {3},
          .junction_b = Uid {4},
          .fmin = 0.0,
          .fmax = 180.0,
      },
  };
  const Array<Reservoir> reservoirs = {
      {
          .uid = Uid {1},
          .name = "eltoro",
          .junction = Uid {1},
          .capacity = 5585.888,
          .emin = 0.0,
          .emax = 5585.888,
          .eini = 1029.515,
          .flow_conversion_rate = harsh ? 3.6e-7 : 0.0036,
      },
      {
          .uid = Uid {2},
          .name = "mid",
          .junction = Uid {2},
          .capacity = 300.0,
          .emin = 0.0,
          .emax = 300.0,
          .eini = 150.0,
          .flow_conversion_rate = harsh ? 3.6e-7 : 0.0036,
      },
      {
          .uid = Uid {3},
          .name = "pond",
          .junction = Uid {3},
          .capacity = harsh ? 0.05 : 0.5,
          .emin = 0.0,
          .emax = harsh ? 0.05 : 0.5,
          .eini = harsh ? 0.025 : 0.25,
          .flow_conversion_rate = harsh ? 3.6e-7 : 0.0036,
      },
  };
  const Array<Turbine> turbines = {
      {
          .uid = Uid {1},
          .name = "tur1",
          .waterway = Uid {1},
          .generator = Uid {1},
          .production_factor = 4.8,
      },
      {
          .uid = Uid {2},
          .name = "tur2",
          .waterway = Uid {2},
          .generator = Uid {20},
          .production_factor = 1.5,
      },
      {
          .uid = Uid {3},
          .name = "tur3",
          .waterway = Uid {3},
          .generator = Uid {21},
          .production_factor = 0.6,
      },
  };

  Array<Block> blocks;
  const std::array<double, kBlocks> durations = harsh
      ? std::array<double, kBlocks> {0.01, 1.0, 168.0, 4.0, 0.5, 2.0}
      : std::array<double, kBlocks> {0.25, 1.0, 4.0, 24.0, 0.5, 2.0};
  for (std::size_t i = 0; i < kBlocks; ++i) {
    blocks.push_back(Block {
        .uid = Uid {static_cast<int>(i + 1)},
        .duration = durations[i],
    });
  }

  Simulation simulation = {
      .block_array = std::move(blocks),
      .stage_array = {{
          .uid = Uid {1},
          .first_block = 0,
          .count_block = kBlocks,
      }},
      .scenario_array = {{
          .uid = Uid {0},
      }},
  };

  PlanningOptions options;
  options.output_format = DataFormat::csv;
  options.output_compression = CompressionCodec::uncompressed;
  auto& mo = options.model_options;
  mo.demand_fail_cost = 1000.0;
  mo.use_single_bus = false;
  mo.use_kirchhoff = true;
  if (!scaled) {
    // Everything off: raw physical units reach the solver.
    mo.auto_scale = false;
    mo.scale_objective = 1.0;
    mo.scale_theta = 1.0;
  }
  // scaled: leave auto_scale / scale_objective (=1000) / scale_theta
  // (adaptive) at their defaults.

  System system = {
      .name = "numerics_stress",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
              {
                  .uid = Uid {2},
                  .name = "b2",
              },
              {
                  .uid = Uid {3},
                  .name = "b3",
              },
              {
                  .uid = Uid {4},
                  .name = "b4",
              },
          },
      .demand_array = demands,
      .generator_array = std::move(generators),
      .line_array = lines,
      .battery_array = std::move(batteries),
      .junction_array = junctions,
      .waterway_array = waterways,
      .reservoir_array = reservoirs,
      .turbine_array = turbines,
  };

  return Planning {
      .options = std::move(options),
      .simulation = std::move(simulation),
      .system = std::move(system),
  };
}

/// Everything we measure for one (solver, scaled?, permutation) run.
struct Measure
{
  bool solved {false};
  double obj {0.0};  ///< physical objective
  double min_abs {0.0};  ///< post-equilibration coefficient range
  double max_abs {0.0};
  std::optional<double> kappa {};
  double max_row_viol {0.0};  ///< max absolute physical row violation
  double max_rel_row_viol {0.0};  ///< ... relative to (1 + |bound|)
  std::vector<double> row_dual {};  ///< physical row duals
  std::map<std::string, double> col_sol {};  ///< by column name
  std::map<std::string, double> obj_lp {};  ///< LP objective coeff

  [[nodiscard]] double decades() const
  {
    return min_abs > 0.0 ? std::log10(max_abs / min_abs) : 0.0;
  }
};

[[nodiscard]] Measure solve_case(const std::string& solver,
                                 bool scaled,
                                 bool solver_native_scaling,
                                 const std::vector<int>& bat_perm,
                                 const std::vector<int>& gen_perm,
                                 bool harsh = false)
{
  auto planning = make_stress_planning(bat_perm, gen_perm, scaled, harsh);

  LpMatrixOptions flat_opts;
  flat_opts.solver_name = solver;
  flat_opts.compute_stats = OptBool {true};
  flat_opts.col_with_names = true;
  flat_opts.col_with_name_map = true;
  flat_opts.equilibration_method =
      scaled ? LpEquilibrationMethod::ruiz : LpEquilibrationMethod::none;

  SolverOptions so;
  so.presolve = false;  // keep presolve from re-scaling behind our back
  so.algorithm = LPAlgo::dual;
  if (!solver_native_scaling) {
    so.scaling = SolverScaling::none;
  }

  PlanningLP plp(std::move(planning), flat_opts);
  const auto res = plp.resolve(so);

  Measure m;
  m.solved = res.has_value();
  if (!m.solved) {
    return m;
  }
  auto& li = plp.system(SceneIndex {0}, PhaseIndex {0}).linear_interface();
  m.obj = li.get_obj_value();
  m.min_abs = li.lp_stats_min_abs();
  m.max_abs = li.lp_stats_max_abs();
  m.kappa = li.get_kappa();

  const auto sol = li.get_col_sol();
  const auto rlo = li.get_row_low();
  const auto rup = li.get_row_upp();
  const auto nr = static_cast<std::size_t>(li.get_numrows());
  const auto nc = static_cast<std::size_t>(li.get_numcols());
  for (std::size_t r = 0; r < nr; ++r) {
    double act = 0.0;
    for (std::size_t c = 0; c < nc; ++c) {
      const double a = li.get_coeff(RowIndex {static_cast<Index>(r)},
                                    ColIndex {static_cast<Index>(c)});
      if (a != 0.0) {
        act += a * sol[c];
      }
    }
    const double lo = rlo[r];
    const double up = rup[r];
    double viol = 0.0;
    double ref = 0.0;
    if (act < lo) {
      viol = lo - act;
      ref = std::abs(lo);
    } else if (act > up) {
      viol = act - up;
      ref = std::abs(up);
    }
    m.max_row_viol = std::max(m.max_row_viol, viol);
    m.max_rel_row_viol = std::max(m.max_rel_row_viol, viol / (1.0 + ref));
  }

  const auto dual = li.get_row_dual();
  m.row_dual.assign(dual.begin(), dual.end());
  for (const auto& [name, idx] : li.col_name_map()) {
    m.col_sol[name] = sol[idx];
    m.obj_lp[name] = li.get_obj_coeff()[static_cast<std::size_t>(idx)];
  }
  return m;
}

[[nodiscard]] std::vector<std::string> solvers_to_test()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  std::vector<std::string> out;
  for (const auto& name : reg.available_solvers()) {
    // cuOpt is a first-order GPU solver (no exact vertex duals); MindOpt
    // is excluded from degenerate-fixture sweeps elsewhere in the suite.
    if (name == "cuopt" || name == "mindopt") {
      continue;
    }
    out.push_back(name);
  }
  return out;
}

[[nodiscard]] std::string fmt_kappa(const std::optional<double>& k)
{
  return k ? std::format("{:.2e}", *k) : std::string {"n/a"};
}

}  // namespace
}  // namespace numerics_scaling_stress_ns

using namespace numerics_scaling_stress_ns;

namespace numerics_scaling_stress_ns
{
namespace
{

/// Column family = name up to the first "_<digit>" (e.g. "battery_energy").
[[nodiscard]] std::string family_of(const std::string& name)
{
  for (std::size_t i = 0; i + 1 < name.size(); ++i) {
    if (name[i] == '_'
        && std::isdigit(static_cast<unsigned char>(name[i + 1])) != 0)
    {
      return name.substr(0, i);
    }
  }
  return name;
}

/// Integer parsed from the start of *text*, or -1 when there is none.
[[nodiscard]] int parse_int(std::string_view text)
{
  int v = -1;
  std::from_chars(text.data(), text.data() + text.size(), v);
  return v;
}

/// Second token (the element uid) of a column name.
[[nodiscard]] int uid_of(const std::string& name)
{
  const auto f = family_of(name).size();
  return f < name.size() ? parse_int(std::string_view {name}.substr(f + 1))
                         : -1;
}

/// Columns that describe the DISPATCH of the permuted units: every battery
/// column and the generation of the three equal-cost thermal units.
[[nodiscard]] bool is_dispatch_col(const std::string& name)
{
  const auto fam = family_of(name);
  if (fam.starts_with("battery_")) {
    return !fam.ends_with("eini");
  }
  const auto uid = uid_of(name);
  return fam.starts_with("generator_") && uid >= 11 && uid <= 17;
}

/// Max |delta| over dispatch columns, matched BY NAME (uid), not position.
[[nodiscard]] double per_unit_spread(const Measure& a,
                                     const Measure& b,
                                     bool thermal)
{
  double d = 0.0;
  for (const auto& [name, va] : a.col_sol) {
    if (!is_dispatch_col(name)
        || family_of(name).starts_with("generator_") != thermal)
    {
      continue;
    }
    if (const auto it = b.col_sol.find(name); it != b.col_sol.end()) {
      d = std::max(d, std::abs(va - it->second));
    }
  }
  return d;
}

/// Aggregate of each (family, block) over the identical group (all
/// batteries; all thermals).  Invariant to how identical units share it.
[[nodiscard]] std::map<std::string, double> group_aggregate(const Measure& m)
{
  std::map<std::string, double> agg;
  for (const auto& [name, v] : m.col_sol) {
    if (is_dispatch_col(name)) {
      agg[family_of(name) + name.substr(name.rfind('_'))] += v;
    }
  }
  return agg;
}

[[nodiscard]] double aggregate_spread(const Measure& a, const Measure& b)
{
  const auto ga = group_aggregate(a);
  const auto gb = group_aggregate(b);
  double d = 0.0;
  for (const auto& [k, v] : ga) {
    if (const auto it = gb.find(k); it != gb.end()) {
      d = std::max(d, std::abs(v - it->second));
    }
  }
  return d;
}

[[nodiscard]] double dual_spread(const Measure& a, const Measure& b)
{
  auto x = a.row_dual;
  auto y = b.row_dual;
  std::ranges::sort(x);
  std::ranges::sort(y);
  double d = 0.0;
  for (std::size_t i = 0; i < std::min(x.size(), y.size()); ++i) {
    d = std::max(d, std::abs(x[i] - y[i]));
  }
  return d;
}

[[nodiscard]] double rel(double a, double b)
{
  return std::abs(a - b) / std::max(1.0, std::abs(b));
}

}  // namespace
}  // namespace numerics_scaling_stress_ns

TEST_CASE("numerics stress - scaling shrinks range and keeps solution")
{
  const std::vector<int> bats = {1, 2, 3, 4, 5, 6};
  const std::vector<int> gens = {1, 2, 3, 4, 5, 6, 7};
  for (const auto& solver : solvers_to_test()) {
    CAPTURE(solver);
    for (const bool harsh : {false, true}) {
      CAPTURE(harsh);
      for (const bool native : {false, true}) {
        CAPTURE(native);
        const auto raw =
            solve_case(solver, /*scaled=*/false, native, bats, gens, harsh);
        const auto sc =
            solve_case(solver, /*scaled=*/true, native, bats, gens, harsh);
        MESSAGE(std::format(
            "TABLE {:>6} harsh={:d} native={:d} | unscaled: obj={:.9g} "
            "range=[{:.1e},{:.1e}] dec={:.2f} kappa={} viol={:.1e} | scaled: "
            "obj={:.9g} dec={:.2f} kappa={} viol={:.1e}",
            solver,
            harsh,
            native,
            raw.obj,
            raw.min_abs,
            raw.max_abs,
            raw.decades(),
            fmt_kappa(raw.kappa),
            raw.max_rel_row_viol,
            sc.obj,
            sc.decades(),
            fmt_kappa(sc.kappa),
            sc.max_rel_row_viol));
        REQUIRE(raw.solved);
        REQUIRE(sc.solved);
        // The scaled matrix is better equilibrated.
        CHECK(sc.decades() < raw.decades() - (harsh ? 0.5 : 1.0));
        CHECK(sc.max_abs <= 1.0 + 1e-9);
        // Both solve the same physical problem.
        CHECK(rel(sc.obj, raw.obj) < 1e-6);
        CHECK(sc.max_rel_row_viol < 1e-6);
      }
    }
  }
}

namespace numerics_scaling_stress_ns
{
namespace
{

struct ThermalCol
{
  double val {0.0};
  double lp_cost {0.0};
};

/// (uid, block 1..kBlocks) -> dispatch and LP objective coefficient.
using ThermalMap = std::map<std::pair<int, int>, ThermalCol>;

[[nodiscard]] ThermalMap thermal_map(const Measure& m)
{
  ThermalMap out;
  for (const auto& [name, v] : m.col_sol) {
    const auto uid = uid_of(name);
    if (family_of(name).starts_with("generator_") && uid >= 11 && uid <= 17) {
      const int blk =
          parse_int(std::string_view {name}.substr(name.rfind('_') + 1));
      out[{uid, blk}] = ThermalCol {
          .val = v,
          .lp_cost = m.obj_lp.at(name),
      };
    }
  }
  return out;
}

/// LP-unit cost gap below which the solver cannot (reliably) tell two
/// units apart: dual feasibility tolerance is ~1e-7 (1e-9 tightest).
constexpr double kTolDual = 1e-7;
constexpr double kTolDualTight = 1e-9;
/// "Clearly above tolerance": three decades over the loosest tolerance.
constexpr double kClearGap = 1e-4;
constexpr double kDispTol = 1e-6;

struct PairStat
{
  int inversions {0};
  double lp_gap_blk3 {0.0};
};

struct Merit
{
  int inversions {0};
  int clear_inversions {0};  ///< pairs whose LP-unit gap >= kClearGap
  double max_inverted_phys_gap {0.0};
  std::map<std::pair<int, int>, PairStat> pair {};  ///< (cheap,dear) uids
};

/// Count merit-order inversions: a dearer thermal dispatched while a
/// cheaper one still has headroom, same block, same bus.
[[nodiscard]] Merit merit_order(const ThermalMap& tm)
{
  Merit r;
  for (int blk = 1; std::cmp_less_equal(blk, kBlocks); ++blk) {
    for (const auto& a : kThermals) {
      for (const auto& b : kThermals) {
        if (!(b.cost > a.cost)) {
          continue;
        }
        const auto ia = tm.find({a.uid, blk});
        const auto ib = tm.find({b.uid, blk});
        if (ia == tm.end() || ib == tm.end()) {
          continue;
        }
        auto& ps = r.pair[{a.uid, b.uid}];
        if (blk == 3) {
          ps.lp_gap_blk3 = std::abs(ib->second.lp_cost - ia->second.lp_cost);
        }
        if (ib->second.val > kDispTol && ia->second.val < a.cap - kDispTol) {
          ++r.inversions;
          ++ps.inversions;
          r.max_inverted_phys_gap =
              std::max(r.max_inverted_phys_gap, b.cost - a.cost);
          if (std::abs(ib->second.lp_cost - ia->second.lp_cost) >= kClearGap) {
            ++r.clear_inversions;
          }
        }
      }
    }
  }
  return r;
}

/// Max |delta| of per-unit thermal dispatch vs the reference run, restricted
/// to (unit, block) cells whose LP cost gap to every other thermal is at
/// least `min_gap` in the REFERENCE run (i.e. cells with a unique optimum
/// the solver can resolve).  min_gap = 0 means all cells.
[[nodiscard]] double resolved_spread(const ThermalMap& ref,
                                     const ThermalMap& run,
                                     double min_gap)
{
  double d = 0.0;
  for (const auto& [key, c] : ref) {
    bool isolated = true;
    for (const auto& t : kThermals) {
      if (t.uid == key.first) {
        continue;
      }
      if (const auto it = ref.find({t.uid, key.second});
          it != ref.end() && std::abs(it->second.lp_cost - c.lp_cost) < min_gap)
      {
        isolated = false;
      }
    }
    if (isolated) {
      if (const auto it = run.find(key); it != run.end()) {
        d = std::max(d, std::abs(it->second.val - c.val));
      }
    }
  }
  return d;
}

[[nodiscard]] const char* gap_class(double lp_gap)
{
  if (lp_gap < kTolDualTight) {
    return "below 1e-9";
  }
  if (lp_gap < kTolDual) {
    return "below 1e-7";
  }
  return "resolvable";
}

}  // namespace
}  // namespace numerics_scaling_stress_ns

TEST_CASE("numerics stress - input order with a thermal cost ladder")
{
  // 8 reorderings of the 6 batteries and the 7 thermal units; the first is
  // the reference.
  const std::vector<std::vector<int>> bat_perms = {
      {1, 2, 3, 4, 5, 6},
      {6, 5, 4, 3, 2, 1},
      {3, 1, 6, 2, 5, 4},
      {2, 3, 4, 5, 6, 1},
      {5, 6, 1, 2, 3, 4},
      {4, 2, 6, 1, 3, 5},
      {1, 6, 2, 5, 3, 4},
      {6, 4, 2, 1, 5, 3},
  };
  const std::vector<std::vector<int>> gen_perms = {
      {1, 2, 3, 4, 5, 6, 7},
      {7, 6, 5, 4, 3, 2, 1},
      {3, 1, 6, 2, 7, 4, 5},
      {2, 3, 4, 5, 6, 7, 1},
      {5, 4, 3, 2, 1, 7, 6},
      {4, 2, 6, 1, 3, 5, 7},
      {1, 6, 2, 5, 3, 7, 4},
      {6, 7, 4, 1, 5, 2, 3},
  };
  for (const auto& solver : solvers_to_test()) {
    CAPTURE(solver);
    for (const bool harsh : {false, true}) {
      CAPTURE(harsh);
      for (const bool scaled : {false, true}) {
        CAPTURE(scaled);
        std::vector<Measure> runs;
        std::vector<ThermalMap> tms;
        for (std::size_t k = 0; k < bat_perms.size(); ++k) {
          runs.push_back(solve_case(solver,
                                    scaled,
                                    /*solver_native_scaling=*/false,
                                    bat_perms[k],
                                    gen_perms[k],
                                    harsh));
          REQUIRE(runs.back().solved);
          tms.push_back(thermal_map(runs.back()));
        }
        double obj_s = 0.0;
        double all_s = 0.0;
        double resolved_s = 0.0;
        double bat_s = 0.0;
        double agg_s = 0.0;
        double dual_s = 0.0;
        int inv_total = 0;
        int inv_clear = 0;
        double inv_gap = 0.0;
        std::map<std::pair<int, int>, PairStat> pairs;
        for (std::size_t k = 0; k < runs.size(); ++k) {
          const auto mo = merit_order(tms[k]);
          inv_total += mo.inversions;
          inv_clear += mo.clear_inversions;
          inv_gap = std::max(inv_gap, mo.max_inverted_phys_gap);
          for (const auto& [pr, ps] : mo.pair) {
            pairs[pr].inversions += ps.inversions;
            pairs[pr].lp_gap_blk3 = ps.lp_gap_blk3;
          }
          if (k == 0) {
            continue;
          }
          obj_s = std::max(obj_s, rel(runs[k].obj, runs[0].obj));
          all_s = std::max(all_s, resolved_spread(tms[0], tms[k], 0.0));
          resolved_s =
              std::max(resolved_s, resolved_spread(tms[0], tms[k], kClearGap));
          bat_s = std::max(
              bat_s, per_unit_spread(runs[k], runs[0], /*thermal=*/false));
          agg_s = std::max(agg_s, aggregate_spread(runs[k], runs[0]));
          dual_s = std::max(dual_s, dual_spread(runs[k], runs[0]));
        }
        MESSAGE(std::format(
            "MERIT {:>6} harsh={:d} scaled={:d} | kappa={} dec={:.2f} "
            "obj_rel={:.1e} inversions={} clear_inversions={} "
            "largest_inverted_cost_gap={:.1e} thermal_spread_all={:.2e} "
            "thermal_spread_resolved={:.2e} battery_spread={:.2e} "
            "aggregate={:.2e} duals={:.2e}",
            solver,
            harsh,
            scaled,
            fmt_kappa(runs[0].kappa),
            runs[0].decades(),
            obj_s,
            inv_total,
            inv_clear,
            inv_gap,
            all_s,
            resolved_s,
            bat_s,
            agg_s,
            dual_s));
        for (const auto& [pr, ps] : pairs) {
          const auto phys =
              kThermals[static_cast<std::size_t>(pr.second - 11)].cost
              - kThermals[static_cast<std::size_t>(pr.first - 11)].cost;
          // Adjacent ladder pairs only (keeps the table short).
          if (pr.second - pr.first == 1 && pr.second <= 15) {
            MESSAGE(std::format(
                "GAP   {:>6} harsh={:d} scaled={:d} | {}>{} phys_gap={:.1e} "
                "lp_gap@block3={:.1e} ({}) inversions_in_8_runs={}",
                solver,
                harsh,
                scaled,
                pr.first,
                pr.second,
                phys,
                ps.lp_gap_blk3,
                gap_class(ps.lp_gap_blk3),
                ps.inversions));
          }
        }
        // Sanity: the optimal value is order invariant.
        CHECK(obj_s < 1e-6);
        // Cost gaps clearly above tolerance in the solver's units: the
        // merit order is respected and the dispatch of every resolvable
        // (unit, block) cell is permutation invariant.
        CHECK(inv_clear == 0);
        CHECK(resolved_s < 1e-6);
      }
    }
  }
}
