// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_numerical_stability.cpp
 * @brief     Numerical-stability monitor for the LP formulation
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Builds the LPs of six canonical cases under `cases/` (one per
 * formulation family) and asserts on the numbers the solver sees (the raw,
 * scaled LP: what `get_coeff_raw` / `get_obj_coeff` / `get_*_raw` return):
 *
 *   case                 family
 *   ieee_14b             DC OPF, Kirchhoff, binding line limits
 *   bat_4b_24            battery (SoC balance, charge/discharge)
 *   sddp_hydro_3phase    reservoir + turbine + waterway, 3 phases
 *   line_loss_envelope   piecewise line losses (no Kirchhoff)
 *   uc_small             unit commitment (MIP) + ramp limits
 *   c0                   multi-stage capacity expansion, Kirchhoff
 *
 * Structural metrics (no solve):
 *   - matrix range   max|a| / min|a| over the nonzeros, in decades;
 *   - objective, RHS and column-bound ranges (nonzero, finite entries);
 *   - tiny (|a| < 1e-9) and huge (|a| > 1e9) matrix entries: none allowed;
 *   - the scaling invariant: the default scaling never widens the matrix
 *     range compared with `--no-scale` (every scaling layer off).
 *
 * Conditioning (solve): kappa of every (scene, phase) cell on HiGHS and
 * CPLEX (whichever are loaded), with `solver_options.kappa_mode = exact`.
 * The largest log10(kappa) of the case must stay within `kKappaTolDecades` of
 * the committed per-solver baseline in `kKappaBaselines`.
 *
 * Updating the baselines (only when a formulation or scaling change is
 * intended to move them):
 *
 *   GTOPT_NUMSTAB_PRINT=1 ./gtoptTests -tc="numerical stability*" -s
 *
 * prints every measured value and the ready-to-paste `kKappaBaselines`
 * lines; replace the table below and say why in the commit message.
 * See docs/numerical-stability.md.
 */

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <limits>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/gtopt_json_io.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>

#ifndef GTOPT_TEST_CASES_DIR
#  error "GTOPT_TEST_CASES_DIR must point at the in-tree cases/ directory"
#endif

using namespace gtopt;

namespace numstab_ns
{
namespace
{

/// Entries at or above this magnitude are treated as infinite bounds.
constexpr double kInfinityCut = 1e19;
/// Matrix entries below / above these magnitudes are defects.
constexpr double kTinyCoeff = 1e-9;
constexpr double kHugeCoeff = 1e9;
/// Allowed growth of log10(kappa) over the committed baseline.
constexpr double kKappaTolDecades = 1.0;

/// Range of the nonzero, finite magnitudes of one LP section.
struct Range
{
  double lo {std::numeric_limits<double>::infinity()};
  double hi {0.0};
  std::size_t count {0};

  void add(double v) noexcept
  {
    const double a = std::abs(v);
    if (a == 0.0 || a >= kInfinityCut || !std::isfinite(a)) {
      return;
    }
    lo = std::min(lo, a);
    hi = std::max(hi, a);
    ++count;
  }

  void merge(const Range& other) noexcept
  {
    lo = std::min(lo, other.lo);
    hi = std::max(hi, other.hi);
    count += other.count;
  }

  /// log10(max / min); 0 for an empty or single-valued section.
  [[nodiscard]] double decades() const noexcept
  {
    return count == 0 ? 0.0 : std::log10(hi / lo);
  }
};

/// Numerics of one case: the worst (scene, phase) cell per metric.
struct LpNumerics
{
  Range matrix {};
  Range objective {};
  Range rhs {};
  Range bounds {};
  std::size_t tiny {0};
  std::size_t huge {0};
  std::size_t cells {0};
  double max_cell_matrix_decades {0.0};
  std::optional<double> max_kappa {};
};

/// Per-case limits, in decades (log10 of max/min).  The defaults are the
/// documented project bounds; a case may only ask for less.
struct Limits
{
  double matrix {6.0};
  double objective {8.0};
  double rhs {8.0};
  double bounds {8.0};
};

struct CaseSpec
{
  std::string_view name;
  std::string_view json;  ///< path below cases/
  Limits limits {};
};

/// Six formulation families (see the file header).  The per-case limits
/// are ceil(measured + 1 decade), never above the documented bounds in
/// `Limits`.
/// Measured 2026-11-25 (decades, matrix / objective / rhs / bounds):
///   ieee_14b 0.97/1.70/1.70/1.67   bat_4b_24 1.78/1.70/1.70/2.48
///   sddp_hydro_3phase 0.00/2.30/0.00/2.00
///   line_loss_envelope 2.70/6.00/2.80/2.00
///   uc_small 1.90/1.70/1.90/2.08   c0 0.65/3.10/0.00/1.40
constexpr auto kCases = std::to_array<CaseSpec>({
    {
        .name = "ieee_14b",
        .json = "ieee_14b/ieee_14b.json",
        .limits =
            {
                .matrix = 2,
                .objective = 3,
                .rhs = 3,
                .bounds = 3,
            },
    },
    {
        .name = "bat_4b_24",
        .json = "bat_4b_24/bat_4b_24.json",
        .limits =
            {
                .matrix = 3,
                .objective = 3,
                .rhs = 3,
                .bounds = 4,
            },
    },
    {
        .name = "sddp_hydro_3phase",
        .json = "sddp_hydro_3phase/sddp_hydro_3phase.json",
        .limits =
            {
                .matrix = 1,
                .objective = 4,
                .rhs = 1,
                .bounds = 3,
            },
    },
    {
        .name = "line_loss_envelope",
        .json = "line_loss_envelope/line_loss_envelope.json",
        // Objective 6.0 decades: the line_flow_abs pin is
        // loss_flow_abs_cost = 0.001 $/MWh, 1e-6 in the LP under
        // scale_objective = 1000.  It was 9.0 while the pin was the
        // internal 1e-6 $/MWh constant (1e-9 in the LP).
        .limits =
            {
                .matrix = 4,
                .objective = 7,
                .rhs = 4,
                .bounds = 3,
            },
    },
    {
        .name = "uc_small",
        .json = "uc_small/uc_small.json",
        .limits =
            {
                .matrix = 3,
                .objective = 3,
                .rhs = 3,
                .bounds = 3,
            },
    },
    {
        .name = "c0",
        .json = "c0/system_c0.json",
        .limits =
            {
                .matrix = 2,
                .objective = 4,
                .rhs = 1,
                .bounds = 3,
            },
    },
});

/// Committed kappa baselines: log10(max kappa over the case's cells), per
/// solver, measured with kappa_mode = exact.  A (case, solver) pair with
/// no entry is reported but not asserted.
struct KappaBaseline
{
  std::string_view name;
  std::string_view solver;
  double log10_kappa;
};

// Recorded 2026-11-25 on HiGHS 1.16.0 and CPLEX 22.1 with
// kappa_mode = exact.  uc_small has none: a MIP reports no basis kappa.
constexpr auto kKappaBaselines = std::to_array<KappaBaseline>({
    {.name = "ieee_14b", .solver = "highs", .log10_kappa = 2.01},
    {.name = "bat_4b_24", .solver = "highs", .log10_kappa = 1.77},
    {.name = "sddp_hydro_3phase", .solver = "highs", .log10_kappa = 1.68},
    {.name = "line_loss_envelope", .solver = "highs", .log10_kappa = 2.75},
    {.name = "c0", .solver = "highs", .log10_kappa = 1.39},
    {.name = "ieee_14b", .solver = "cplex", .log10_kappa = 2.08},
    {.name = "bat_4b_24", .solver = "cplex", .log10_kappa = 1.52},
    {.name = "sddp_hydro_3phase", .solver = "cplex", .log10_kappa = 2.26},
    {.name = "line_loss_envelope", .solver = "cplex", .log10_kappa = 1.59},
    {.name = "c0", .solver = "cplex", .log10_kappa = 1.46},
});

[[nodiscard]] bool print_requested()
{
  const char* v = std::getenv("GTOPT_NUMSTAB_PRINT");  // NOLINT
  return v != nullptr && std::string_view {v} != "0";
}

/// Load a case; the input directory is made absolute so parquet inputs
/// resolve regardless of the test's working directory.
[[nodiscard]] Planning load_case(const CaseSpec& spec, bool no_scale)
{
  const auto path =
      std::filesystem::path {GTOPT_TEST_CASES_DIR} / std::string {spec.json};
  auto parsed = parse_planning_files({path.string()});
  REQUIRE_MESSAGE(parsed.has_value(), parsed.error_or(""));
  auto planning = std::move(*parsed);

  const auto case_dir = path.parent_path();
  auto& options = planning.options;
  options.input_directory =
      (case_dir / options.input_directory.value_or(".")).string();
  // Monolithic for every case: the structure of each (scene, phase) LP is
  // the same under SDDP, and one solve per cell is all kappa needs.
  // No status API: its monitor thread costs ~0.5 s per solve and would
  // write solver_status.json into the working directory.
  planning.options.sddp_options.api_enabled = false;
  REQUIRE(apply_set_options(planning, {"method=monolithic"}));

  if (spec.name == "uc_small") {
    // uc_small has no ramp limits; add them so the UC family is covered.
    for (auto& c : planning.system.commitment_array) {
      c.ramp_up = 30.0;
      c.ramp_down = 30.0;
    }
  }

  if (no_scale) {
    // Exactly what `--no-scale` does (main_options.hpp).
    auto& mo = options.model_options;
    mo.scale_objective = 1.0;
    mo.scale_theta = 1.0;
    mo.scale_loss_link = 1.0;
    mo.auto_scale = false;
    options.lp_matrix_options.equilibration_method =
        LpEquilibrationMethod::none;
  }
  return planning;
}

/// Raw-LP numerics of one cell.
void measure_cell(const LinearInterface& li, LpNumerics& out)
{
  const auto nrows = static_cast<std::size_t>(li.get_numrows());
  const auto ncols = static_cast<std::size_t>(li.get_numcols());

  Range cell_matrix;
  for (std::size_t r = 0; r < nrows; ++r) {
    for (std::size_t c = 0; c < ncols; ++c) {
      const double a = li.get_coeff_raw(RowIndex {static_cast<Index>(r)},
                                        ColIndex {static_cast<Index>(c)});
      if (a == 0.0) {
        continue;
      }
      cell_matrix.add(a);
      const double m = std::abs(a);
      out.tiny += m < kTinyCoeff ? 1U : 0U;
      out.huge += m > kHugeCoeff ? 1U : 0U;
    }
  }
  out.matrix.merge(cell_matrix);
  out.max_cell_matrix_decades =
      std::max(out.max_cell_matrix_decades, cell_matrix.decades());

  for (const double v : li.get_obj_coeff()) {
    out.objective.add(v);
  }
  for (const double v : li.get_row_low_raw()) {
    out.rhs.add(v);
  }
  for (const double v : li.get_row_upp_raw()) {
    out.rhs.add(v);
  }
  for (const double v : li.get_col_low_raw()) {
    out.bounds.add(v);
  }
  for (const double v : li.get_col_upp_raw()) {
    out.bounds.add(v);
  }
  ++out.cells;
}

void measure_all(const PlanningLP& plp, LpNumerics& out)
{
  for (const auto& phase_systems : plp.systems()) {
    for (const auto& system : phase_systems) {
      measure_cell(system.linear_interface(), out);
    }
  }
}

/// Build (and optionally solve) one case on one solver.
[[nodiscard]] LpNumerics run_case(const CaseSpec& spec,
                                  const std::string& solver,
                                  bool no_scale,
                                  bool solve)
{
  auto planning = load_case(spec, no_scale);
  LpMatrixOptions flat_opts;
  flat_opts.solver_name = solver;
  PlanningLP plp {std::move(planning), flat_opts};

  LpNumerics out;
  if (!solve) {
    measure_all(plp, out);
    return out;
  }

  SolverOptions so;
  so.kappa_mode = KappaMode::exact;
  const auto res = plp.resolve(so);
  REQUIRE_MESSAGE(res.has_value(),
                  std::format("{} on {}: solve failed", spec.name, solver));
  for (const auto& phase_systems : plp.systems()) {
    for (const auto& system : phase_systems) {
      const auto& li = system.linear_interface();
      CHECK_MESSAGE(li.is_optimal(),
                    std::format("{} on {}: cell not optimal (status {})",
                                spec.name,
                                solver,
                                li.get_status()));
      if (const auto k = li.get_kappa(); k.has_value() && *k > 0.0) {
        out.max_kappa = std::max(out.max_kappa.value_or(0.0), *k);
      }
    }
  }
  return out;
}

/// Solvers to build the structural metrics with: the default one.
[[nodiscard]] std::string structure_solver()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  const auto avail = reg.available_solvers();
  for (const auto* name : {"highs", "cplex"}) {
    if (std::ranges::contains(avail, std::string {name})) {
      return name;
    }
  }
  return avail.empty() ? std::string {} : std::string {reg.default_solver()};
}

/// Kappa is only baselined on HiGHS (the CI solver) and CPLEX.
[[nodiscard]] std::vector<std::string> kappa_solvers()
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  const auto avail = reg.available_solvers();
  std::vector<std::string> out;
  for (const auto* name : {"highs", "cplex"}) {
    if (std::ranges::contains(avail, std::string {name})) {
      out.emplace_back(name);
    }
  }
  return out;
}

[[nodiscard]] std::optional<double> kappa_baseline(std::string_view name,
                                                   std::string_view solver)
{
  for (const auto& b : kKappaBaselines) {
    if (b.name == name && b.solver == solver) {
      return b.log10_kappa;
    }
  }
  return std::nullopt;
}

/// One row of the failure table.
[[nodiscard]] std::string row(std::string_view name,
                              std::string_view metric,
                              double value,
                              double limit)
{
  return std::format("{:<20} {:<16} {:>9.2f} {:>9.2f}  {}",
                     name,
                     metric,
                     value,
                     limit,
                     value <= limit + 1e-12 ? "ok" : "EXCEEDED");
}

/// Structural ranges of one case (default scaling vs --no-scale).
void check_structure(const CaseSpec& spec, const std::string& solver)
{
  CAPTURE(spec.name);
  const auto m = run_case(spec, solver, /*no_scale=*/false, /*solve=*/false);
  const auto ns = run_case(spec, solver, /*no_scale=*/true, /*solve=*/false);
  REQUIRE(m.cells > 0);
  REQUIRE(m.matrix.count > 0);

  const auto& lim = spec.limits;
  const auto checks = std::to_array<std::pair<std::string_view, double>>({
      {"matrix", m.matrix.decades()},
      {"objective", m.objective.decades()},
      {"rhs", m.rhs.decades()},
      {"bounds", m.bounds.decades()},
  });
  const auto limits = std::to_array({
      lim.matrix,
      lim.objective,
      lim.rhs,
      lim.bounds,
  });
  std::string table = std::format(
      "{:<20} {:<16} {:>9} {:>9}\n", "case", "metric [dec]", "value", "limit");
  bool all_ok = true;
  for (std::size_t i = 0; i < checks.size(); ++i) {
    table += row(spec.name, checks[i].first, checks[i].second, limits[i]);
    table += '\n';
    all_ok = all_ok && checks[i].second <= limits[i] + 1e-12;
    CHECK(checks[i].second <= limits[i] + 1e-12);
  }
  table += std::format("{:<20} {:<16} {:>9.2f} {:>9}  (reference)\n",
                       spec.name,
                       "matrix no-scale",
                       ns.matrix.decades(),
                       "-");
  table += std::format("{:<20} {:<16} {:>9} {:>9}  {}\n",
                       spec.name,
                       "tiny/huge |a|",
                       std::format("{}/{}", m.tiny, m.huge),
                       "0/0",
                       m.tiny + m.huge == 0 ? "ok" : "EXCEEDED");

  // No tiny or huge matrix entries.
  CHECK(m.tiny == 0);
  CHECK(m.huge == 0);
  all_ok = all_ok && m.tiny == 0 && m.huge == 0;

  // Scaling invariant: the default scaling keeps or shrinks the range of
  // the worst cell compared with --no-scale.
  const bool scaling_ok =
      m.max_cell_matrix_decades <= ns.max_cell_matrix_decades + 1e-9;
  CHECK(scaling_ok);
  all_ok = all_ok && scaling_ok;

  if (!all_ok || print_requested()) {
    MESSAGE("LP numerics (decades = log10 max/min):\n" << table);
  }
}

/// Kappa of one case on every baselined solver.
void check_kappa(const CaseSpec& spec)
{
  std::string table = std::format("{:<20} {:<8} {:>9} {:>9} {:>9}\n",
                                  "case",
                                  "solver",
                                  "log10 k",
                                  "baseline",
                                  "limit");
  std::string paste;
  bool all_ok = true;
  for (const auto& solver : kappa_solvers()) {
    CAPTURE(spec.name);
    CAPTURE(solver);
    const auto m = run_case(spec, solver, /*no_scale=*/false, /*solve=*/true);
    if (!m.max_kappa.has_value()) {
      table += std::format("{:<20} {:<8} {:>9}\n", spec.name, solver, "n/a");
      continue;
    }
    const double lk = std::log10(*m.max_kappa);
    paste += std::format(
        "    {{.name = \"{}\", .solver = \"{}\", .log10_kappa = {:.2f}}},\n",
        spec.name,
        solver,
        lk);
    const auto base = kappa_baseline(spec.name, solver);
    if (!base.has_value()) {
      table += std::format(
          "{:<20} {:<8} {:>9.2f} {:>9}\n", spec.name, solver, lk, "none");
      continue;
    }
    const double limit = *base + kKappaTolDecades;
    const bool ok = lk <= limit;
    all_ok = all_ok && ok;
    table += std::format("{:<20} {:<8} {:>9.2f} {:>9.2f} {:>9.2f}  {}\n",
                         spec.name,
                         solver,
                         lk,
                         *base,
                         limit,
                         ok ? "ok" : "EXCEEDED");
    CHECK(lk <= limit);
  }

  if (!all_ok || print_requested()) {
    MESSAGE("kappa (log10, exact when available):\n"
            << table << "\nbaseline lines:\n"
            << paste);
  }
}

/// Both checks of the case named `name`.
void check_case(std::string_view name)
{
  const auto solver = structure_solver();
  if (solver.empty()) {
    MESSAGE("no LP solver plugin available; skipping");
    return;
  }
  const auto* spec = std::ranges::find(kCases, name, &CaseSpec::name);
  REQUIRE(spec != kCases.end());
  check_structure(*spec, solver);
  check_kappa(*spec);
}

}  // namespace
}  // namespace numstab_ns

using namespace numstab_ns;

TEST_CASE("numerical stability - ieee_14b (Kirchhoff network)")  // NOLINT
{
  check_case("ieee_14b");
}

TEST_CASE("numerical stability - bat_4b_24 (battery)")  // NOLINT
{
  check_case("bat_4b_24");
}

TEST_CASE("numerical stability - sddp_hydro_3phase (hydro)")  // NOLINT
{
  check_case("sddp_hydro_3phase");
}

TEST_CASE("numerical stability - line_loss_envelope (losses)")  // NOLINT
{
  check_case("line_loss_envelope");
}

TEST_CASE("numerical stability - uc_small (UC + ramps)")  // NOLINT
{
  check_case("uc_small");
}

TEST_CASE("numerical stability - c0 (capacity expansion)")  // NOLINT
{
  check_case("c0");
}
