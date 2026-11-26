// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_kappa_mode.cpp
 * @brief     `kappa_mode` on every backend, and one kappa query per solve
 * @date      2026-11-25
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * LinearInterface reads kappa from the backend ONCE per solve (cached for
 * `get_kappa()`, `max_kappa` stats and the low-memory cache), and never
 * under `kappa_mode = off`.  `kappa_queries()` counts the backend calls.
 */

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <fstream>
#include <limits>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/kappa_mode.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/linear_problem.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/sddp_method.hpp>
#include <gtopt/solution_csv.hpp>
#include <gtopt/solver_backend.hpp>
#include <gtopt/solver_options.hpp>
#include <gtopt/solver_registry.hpp>
#include <unistd.h>

#include "log_capture.hpp"
#include "sddp_helpers.hpp"

using namespace gtopt;

namespace
{

constexpr auto kappa_backends = std::to_array<std::string_view>({
    "highs",
    "cplex",
    "gurobi",
    "mindopt",
    "clp",
    "cbc",
    "scip",
    "cuopt",
});

[[nodiscard]] bool have_kappa_backend(std::string_view name)
{
  auto& reg = SolverRegistry::instance();
  reg.load_all_plugins();
  return reg.has_solver(std::string {name});
}

/// min x + 2y  s.t.  x + y >= 1,  x - y <= 0.5,  x, y in [0, 10].
void add_kappa_lp(LinearInterface& li)
{
  const auto x = li.add_col(SparseCol {
      .uppb = 10.0,
      .cost = 1.0,
  });
  const auto y = li.add_col(SparseCol {
      .uppb = 10.0,
      .cost = 2.0,
  });
  SparseRow r1;
  r1[x] = 1.0;
  r1[y] = 1.0;
  r1.lowb = 1.0;
  r1.uppb = LinearProblem::DblMax;
  (void)li.add_row(r1);
  SparseRow r2;
  r2[x] = 1.0;
  r2[y] = -1.0;
  r2.lowb = -LinearProblem::DblMax;
  r2.uppb = 0.5;
  (void)li.add_row(r2);
}

/// Set / unset `$GTOPT_KAPPA_MODE` for one scope, restoring the old value.
class KappaEnvGuard
{
public:
  explicit KappaEnvGuard(const char* value)
  {
    // NOLINTNEXTLINE(concurrency-mt-unsafe)
    if (const char* old = std::getenv("GTOPT_KAPPA_MODE")) {
      m_old_ = old;
    }
    set(value);
  }
  KappaEnvGuard(const KappaEnvGuard&) = delete;
  KappaEnvGuard& operator=(const KappaEnvGuard&) = delete;
  KappaEnvGuard(KappaEnvGuard&&) = delete;
  KappaEnvGuard& operator=(KappaEnvGuard&&) = delete;
  ~KappaEnvGuard() { set(m_old_ ? m_old_->c_str() : nullptr); }

private:
  static void set(const char* value)
  {
    if (value == nullptr) {
      ::unsetenv("GTOPT_KAPPA_MODE");  // NOLINT(concurrency-mt-unsafe)
    } else {
      ::setenv("GTOPT_KAPPA_MODE", value, 1);  // NOLINT(concurrency-mt-unsafe)
    }
  }
  std::optional<std::string> m_old_;
};

[[nodiscard]] bool mentions_kappa(std::string text)
{
  std::ranges::transform(text,
                         text.begin(),
                         [](unsigned char c)
                         { return static_cast<char>(std::tolower(c)); });
  return text.contains("kappa");
}

}  // namespace

TEST_CASE("kappa_mode compiled default follows the build type")  // NOLINT
{
  // GTOPT_KAPPA_MODE_DEFAULT=auto: off in NDEBUG (release) builds,
  // estimate in Debug / CIFast; an explicit CMake value wins.
  const auto compiled = compiled_default_kappa_mode();
  if (k_kappa_mode_default_name == "auto") {
    CHECK(
        compiled
        == (k_kappa_mode_release_build ? KappaMode::off : KappaMode::estimate));
  } else {
    CHECK(enum_name(compiled) == k_kappa_mode_default_name);
  }
#ifdef NDEBUG
  CHECK(k_kappa_mode_release_build);
#else
  CHECK_FALSE(k_kappa_mode_release_build);
#endif
}

TEST_CASE(  // NOLINT
    "kappa_mode precedence: options > $GTOPT_KAPPA_MODE > compiled")
{
  {
    const KappaEnvGuard env {nullptr};
    const auto [mode, source] = effective_default_kappa_mode();
    CHECK(mode == compiled_default_kappa_mode());
    CHECK(source == KappaModeSource::compiled_default);
    CHECK(resolve_kappa_mode(std::nullopt) == compiled_default_kappa_mode());
    const auto text = describe_kappa_mode();
    CHECK(text.starts_with("kappa mode: "));
    CHECK(text.contains("compiled default"));
  }
  for (const auto& [text, want] :
       {
           std::pair {"off", KappaMode::off},
           std::pair {"estimate", KappaMode::estimate},
           std::pair {"exact", KappaMode::exact},
       })
  {
    CAPTURE(text);
    const KappaEnvGuard env {text};
    const auto [mode, source] = effective_default_kappa_mode();
    CHECK(mode == want);
    CHECK(source == KappaModeSource::environment);
    CHECK(resolve_kappa_mode(std::nullopt) == want);
    CHECK(describe_kappa_mode().contains("from $GTOPT_KAPPA_MODE"));
    // An explicit option beats the environment.
    for (const auto opt :
         {KappaMode::off, KappaMode::estimate, KappaMode::exact})
    {
      CHECK(resolve_kappa_mode(opt) == opt);
    }
  }
  {
    // An unparsable value falls back to the compiled default.
    const KappaEnvGuard env {"fast"};
    CHECK_FALSE(kappa_mode_from_env().has_value());
    CHECK(effective_default_kappa_mode().second
          == KappaModeSource::compiled_default);
  }
  {
    const KappaEnvGuard env {""};  // empty = unset
    CHECK_FALSE(kappa_mode_from_env().has_value());
  }
}

TEST_CASE("kappa_mode precedence reaches the LinearInterface solve")  // NOLINT
{
  if (!have_kappa_backend("highs")) {
    return;
  }
  const auto solve_queries = [](std::optional<KappaMode> opt)
  {
    LinearInterface li("highs");
    add_kappa_lp(li);
    REQUIRE(li.initial_solve(SolverOptions {
                                 .algorithm = LPAlgo::dual,
                                 .threads = 1,
                                 .kappa_mode = opt,
                             })
                .has_value());
    return li.kappa_queries();
  };
  {
    const KappaEnvGuard env {"off"};
    CHECK(solve_queries(std::nullopt) == 0);  // env off
    CHECK(solve_queries(KappaMode::estimate) == 1);  // option wins
  }
  {
    const KappaEnvGuard env {"exact"};
    CHECK(solve_queries(std::nullopt) == 1);
    CHECK(solve_queries(KappaMode::off) == 0);  // option wins
  }
  {
    const KappaEnvGuard env {nullptr};
    CHECK(solve_queries(std::nullopt)
          == (compiled_default_kappa_mode() == KappaMode::off ? 0U : 1U));
  }
}

TEST_CASE(  // NOLINT
    "kappa_mode off: monolithic + SDDP never query nor print kappa")
{
  const KappaEnvGuard env {"off"};
  const auto out_dir = std::filesystem::temp_directory_path()
      / std::format("gtopt_kappa_off_{}", ::getpid());
  std::filesystem::remove_all(out_dir);

  const auto before = LinearInterface::total_kappa_queries();
  std::vector<std::string> messages;
  {
    const test::LogCapture logs(200000);

    SUBCASE("SDDP, 2 iterations, solution.csv")
    {
      auto planning = make_3phase_hydro_planning();
      planning.options.solver_options.kappa_mode = KappaMode::off;
      planning.options.output_directory = out_dir.string();
      PlanningLP plp(std::move(planning));
      SDDPOptions sddp_opts;
      sddp_opts.max_iterations = 2;
      sddp_opts.enable_api = false;
      SDDPMethod sddp(plp, sddp_opts);
      REQUIRE(sddp.solve().has_value());
      plp.write_out();
      CHECK(sddp.global_max_kappa() == doctest::Approx(-1.0));
    }

    SUBCASE("monolithic resolve + write_out")
    {
      auto planning = make_3phase_hydro_planning();
      planning.options.solver_options.kappa_mode = KappaMode::off;
      planning.options.output_directory = out_dir.string();
      PlanningLP plp(std::move(planning));
      REQUIRE(
          plp.resolve(plp.options().monolithic_solver_options()).has_value());
      plp.write_out();
      for (const auto& phase_systems : plp.systems()) {
        for (const auto& sys : phase_systems) {
          CHECK_FALSE(sys.linear_interface().get_kappa().has_value());
          CHECK(sys.linear_interface().solver_stats().max_kappa
                == doctest::Approx(-1.0));
        }
      }
    }
    messages = logs.messages();
  }

  // Not queried: no backend kappa call anywhere in the process.
  CHECK(LinearInterface::total_kappa_queries() == before);
  // Not printed: neither in the logs nor in solution.csv.
  CHECK_FALSE(messages.empty());
  for (const auto& msg : messages) {
    CAPTURE(msg);
    CHECK_FALSE(mentions_kappa(msg));
  }
  std::ifstream csv(out_dir / "solution.csv");
  std::string header;
  REQUIRE(std::getline(csv, header));
  CHECK_FALSE(mentions_kappa(header));
  CHECK(header + "\n" == solution_csv_header(/*with_kappa=*/false));

  std::error_code ec;
  std::filesystem::remove_all(out_dir, ec);
}

TEST_CASE("solution.csv kappa columns only when kappa is on")  // NOLINT
{
  CHECK(std::string {solution_csv_header()}.contains(",kappa,max_kappa,"));
  CHECK(std::string {solution_csv_header(true)}
        == std::string {solution_csv_header()});
  CHECK_FALSE(mentions_kappa(std::string {solution_csv_header(false)}));
  const SolutionRow row {};
  CHECK(std::ranges::count(solution_csv_line(row, false), ',')
        == std::ranges::count(solution_csv_header(false), ','));
  CHECK(std::ranges::count(solution_csv_line(row, true), ',')
        == std::ranges::count(solution_csv_header(true), ','));
}

TEST_CASE("PlanningOptionsLP::kappa_enabled follows every pass")  // NOLINT
{
  const KappaEnvGuard env {nullptr};
  PlanningOptions base;
  base.solver_options.kappa_mode = KappaMode::off;
  CHECK_FALSE(PlanningOptionsLP {base}.kappa_enabled());
  auto with_mono = base;
  with_mono.monolithic_options.solver_options =
      SolverOptions {.kappa_mode = KappaMode::estimate};
  CHECK(PlanningOptionsLP {with_mono}.kappa_enabled());
  base.solver_options.kappa_mode = KappaMode::exact;
  CHECK(PlanningOptionsLP {base}.kappa_enabled());
  base.solver_options.kappa_mode.reset();
  CHECK(PlanningOptionsLP {base}.kappa_enabled()
        == (compiled_default_kappa_mode() != KappaMode::off));
}

TEST_CASE(
    "kappa_mode on every backend: one query per solve, none off")  // NOLINT
{
  for (const auto name : kappa_backends) {
    if (!have_kappa_backend(name)) {
      continue;
    }
    for (const std::optional<KappaMode> mode :
         {
             std::optional<KappaMode> {},
             std::optional<KappaMode> {KappaMode::estimate},
             std::optional<KappaMode> {KappaMode::exact},
             std::optional<KappaMode> {KappaMode::off},
         })
    {
      CAPTURE(name);
      CAPTURE(mode.has_value() ? static_cast<int>(*mode) : -1);
      LinearInterface li(name);
      add_kappa_lp(li);
      const SolverOptions opts {
          .algorithm = LPAlgo::dual,
          .threads = 1,
          .kappa_mode = mode,
      };
      REQUIRE(li.initial_solve(opts).has_value());
      CHECK(li.get_obj_value() == doctest::Approx(1.25));
      const bool off = mode == KappaMode::off;
      CHECK(li.kappa_queries() == (off ? 0U : 1U));

      // Repeated reads come from the solve-time cache.
      const auto k1 = li.get_kappa();
      const auto k2 = li.get_kappa();
      CHECK(li.kappa_queries() == (off ? 0U : 1U));
      CHECK(k1.has_value() == k2.has_value());
      if (off) {
        CHECK_FALSE(k1.has_value());
        CHECK(li.solver_stats().max_kappa == doctest::Approx(-1.0));
      } else if (k1.has_value()) {
        CHECK(k1.value_or(0.0) == doctest::Approx(k2.value_or(-1.0)));
        CHECK(li.solver_stats().max_kappa
              == doctest::Approx(k1.value_or(-1.0)));
      }

      // A re-solve reads kappa once more (zero times under off).
      REQUIRE(li.resolve(opts).has_value());
      (void)li.get_kappa();
      CHECK(li.kappa_queries() == (off ? 0U : 2U));
    }
  }
}

TEST_CASE("kappa_mode exact vs estimate on every backend")  // NOLINT
{
  for (const auto name : kappa_backends) {
    if (!have_kappa_backend(name)) {
      continue;
    }
    CAPTURE(name);
    std::optional<double> est;
    std::optional<double> exact;
    for (const auto mode : {KappaMode::estimate, KappaMode::exact}) {
      LinearInterface li(name);
      add_kappa_lp(li);
      REQUIRE(li.initial_solve(SolverOptions {
                                   .algorithm = LPAlgo::dual,
                                   .threads = 1,
                                   .kappa_mode = mode,
                               })
                  .has_value());
      (mode == KappaMode::exact ? exact : est) = li.get_kappa();
    }
    // Backends without a kappa query report neither.
    CHECK(est.has_value() == exact.has_value());
    // HiGHS's estimate is Hager's lower bound of the exact 1-norm value;
    // other solvers' estimates may land on either side.
    if (est && exact && name == "highs") {
      CHECK(est.value_or(0.0) <= (exact.value_or(0.0) * (1.0 + 1e-6)) + 1e-9);
    }
  }
}

TEST_CASE("finite_kappa rejects solver infinity sentinels")  // NOLINT
{
  // CPLEX answers CPX_KAPPA with status 0 and its huge-value sentinel
  // 1e75 when it cannot bound the basis condition (CEN PCP 2025-10-19
  // relaxed solve).  That is no kappa.
  CHECK(is_infinite_kappa(1e75));
  CHECK_FALSE(finite_kappa(1e75).has_value());
  CHECK_FALSE(finite_kappa(k_kappa_infinite).has_value());
  CHECK_FALSE(
      finite_kappa(std::numeric_limits<double>::infinity()).has_value());
  CHECK_FALSE(
      finite_kappa(std::numeric_limits<double>::quiet_NaN()).has_value());
  CHECK(is_infinite_kappa(std::numeric_limits<double>::quiet_NaN()));
  CHECK_FALSE(finite_kappa(-1.0).has_value());

  // Real condition numbers pass through, huge ones included: CPLEX
  // itself reports 2.3e16 (CPX_KAPPA) / 2.5e16 (CPX_EXACT_KAPPA) on that
  // LP's final basis and 2.6e26 after a primal re-solve from it -
  // numerically singular bases the high-kappa warning must still flag.
  CHECK(finite_kappa(1.0).value_or(-1.0) == doctest::Approx(1.0));
  CHECK(finite_kappa(1.81e7).value_or(-1.0) == doctest::Approx(1.81e7));
  CHECK(finite_kappa(2.33e16).value_or(-1.0) == doctest::Approx(2.33e16));
  CHECK(finite_kappa(2.59e26).value_or(-1.0) == doctest::Approx(2.59e26));
  CHECK(finite_kappa(1e74).value_or(-1.0) == doctest::Approx(1e74));
  CHECK_FALSE(is_infinite_kappa(2.59e26));
}

namespace
{

/// Three-period unit commitment built as a MIP and LP-relaxed, the shape
/// of the `--no-mip` PCP solve: status u (integer), startup v, shutdown
/// w, logic u[t] - u[t-1] - v[t] + w[t] = 0, the status rows v <= u and
/// w <= 1 - u, the exclusion v + w <= 1, Pmin u <= p <= Pmax u and a
/// demand row with a costly slack.
void add_relaxed_uc_lp(LinearInterface& li)
{
  constexpr auto demand = std::to_array({30.0, 80.0, 10.0});
  constexpr double pmin = 40.0;
  constexpr double pmax = 100.0;
  std::optional<ColIndex> u_prev;
  for (const double d : demand) {
    const auto u = li.add_col(SparseCol {
        .uppb = 1.0,
        .cost = 5.0,
        .is_integer = true,
    });
    const auto v = li.add_col(SparseCol {
        .uppb = 1.0,
        .cost = 50.0,
    });
    const auto w = li.add_col(SparseCol {
        .uppb = 1.0,
        .cost = 1.0,
    });
    const auto p = li.add_col(SparseCol {
        .uppb = pmax,
        .cost = 2.0,
    });
    const auto fail = li.add_col(SparseCol {
        .uppb = d,
        .cost = 1000.0,
    });

    SparseRow logic;  // u[t] - u[t-1] - v[t] + w[t] = 0 (u[-1] = 0)
    logic[u] = 1.0;
    if (u_prev.has_value()) {
      logic[*u_prev] = -1.0;
    }
    logic[v] = -1.0;
    logic[w] = 1.0;
    logic.lowb = 0.0;
    logic.uppb = 0.0;
    (void)li.add_row(logic);

    SparseRow startup;  // v <= u
    startup[v] = 1.0;
    startup[u] = -1.0;
    startup.lowb = -LinearProblem::DblMax;
    startup.uppb = 0.0;
    (void)li.add_row(startup);

    SparseRow shutdown;  // w + u <= 1
    shutdown[w] = 1.0;
    shutdown[u] = 1.0;
    shutdown.lowb = -LinearProblem::DblMax;
    shutdown.uppb = 1.0;
    (void)li.add_row(shutdown);

    SparseRow exclusion;  // v + w <= 1
    exclusion[v] = 1.0;
    exclusion[w] = 1.0;
    exclusion.lowb = -LinearProblem::DblMax;
    exclusion.uppb = 1.0;
    (void)li.add_row(exclusion);

    SparseRow pmax_row;  // p - Pmax u <= 0
    pmax_row[p] = 1.0;
    pmax_row[u] = -pmax;
    pmax_row.lowb = -LinearProblem::DblMax;
    pmax_row.uppb = 0.0;
    (void)li.add_row(pmax_row);

    SparseRow pmin_row;  // p - Pmin u >= 0
    pmin_row[p] = 1.0;
    pmin_row[u] = -pmin;
    pmin_row.lowb = 0.0;
    pmin_row.uppb = LinearProblem::DblMax;
    (void)li.add_row(pmin_row);

    SparseRow balance;  // p + fail >= d
    balance[p] = 1.0;
    balance[fail] = 1.0;
    balance.lowb = d;
    balance.uppb = LinearProblem::DblMax;
    (void)li.add_row(balance);

    u_prev = u;
  }
}

}  // namespace

TEST_CASE(  // NOLINT
    "kappa of a relaxed MIP model is finite or absent, never a sentinel")
{
  for (const auto name : kappa_backends) {
    if (!have_kappa_backend(name)) {
      continue;
    }
    for (const auto algo : {LPAlgo::dual, LPAlgo::primal, LPAlgo::barrier}) {
      for (const auto mode : {KappaMode::estimate, KappaMode::exact}) {
        CAPTURE(name);
        CAPTURE(static_cast<int>(algo));
        CAPTURE(static_cast<int>(mode));
        LinearInterface li(name);
        add_relaxed_uc_lp(li);
        (void)li.relax_integers();
        REQUIRE(li.initial_solve(SolverOptions {
                                     .algorithm = algo,
                                     .threads = 1,
                                     .kappa_mode = mode,
                                 })
                    .has_value());
        REQUIRE(li.is_optimal());
        const auto kappa = li.get_kappa();
        if (kappa.has_value()) {
          CHECK(*kappa >= 0.0);
          CHECK(*kappa < k_kappa_infinite);
          CHECK_FALSE(is_infinite_kappa(*kappa));
          CHECK(li.solver_stats().max_kappa
                == doctest::Approx(kappa.value_or(-1.0)));
        } else {
          CHECK(li.solver_stats().max_kappa == doctest::Approx(-1.0));
        }
        // CPLEX ends every LP solve here (barrier with its default
        // crossover too) on a basis: a plausible finite kappa.
        if (name == "cplex") {
          CHECK(kappa.has_value());
          CHECK(kappa.value_or(0.0) >= 1.0);
          CHECK(kappa.value_or(k_kappa_infinite) < 1e6);
        }
      }
    }
  }
}
