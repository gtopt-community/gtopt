/**
 * @file      test_mip_start_options_merge.cpp
 * @brief     MipStartOptions::merge (and its nested sections) overlay rules
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * The `mip_start` block reaches the solver after the JSON options, the
 * `--set` overrides and the per-method overlays are merged.  Each nested
 * section has its own merge(); a field forgotten there silently drops a
 * user override.  These tests merge a fully populated overlay into an
 * empty base and a partial overlay into a populated base, field by field.
 */

#include <utility>

#include <doctest/doctest.h>
#include <gtopt/monolithic_options.hpp>

using namespace gtopt;

namespace
{

MipStartOptions full_overlay()
{
  return MipStartOptions {
      .enabled = true,
      .relax =
          {
              .solver_options =
                  SolverOptions {
                      .optimal_eps = 1e-7,
                  },
              .check = true,
              .report_saturated = true,
              .on_infeasible = RelaxInfeasibleAction::warn,
          },
      .round =
          {
              .threshold = 0.4,
          },
      .domain_rules =
          {
              .min_up_down = true,
              .commitment_logic = false,
              .peak_injection =
                  {
                      .enabled = true,
                      .peak_window =
                          {
                              .start = 18.0,
                              .end = 22.0,
                          },
                      .solar_window =
                          {
                              .start = 9.0,
                              .end = 16.0,
                          },
                  },
          },
      .scip_repair =
          {
              .enabled = true,
          },
      .inject =
          {
              .effort = MipStartEffort::repair,
          },
      .from_file = "start.json",
      .dump_file = "dump.json",
      .seed_solution_file = "seed.sol",
      .skip_relaxation = true,
      .root_basis_cache_file = "root.bas",
  };
}

}  // namespace

TEST_CASE("MipStartOptions merge copies every overlay field")  // NOLINT
{
  MipStartOptions base {};
  base.merge(full_overlay());

  CHECK(base.enabled.value_or(false));
  CHECK(base.relax.check.value_or(false));
  CHECK(base.relax.report_saturated.value_or(false));
  CHECK(base.relax.on_infeasible.value_or(RelaxInfeasibleAction::stop)
        == RelaxInfeasibleAction::warn);
  REQUIRE(base.relax.solver_options.has_value());
  CHECK(base.relax.solver_options.value_or(SolverOptions {})
            .optimal_eps.value_or(0.0)
        == doctest::Approx(1e-7));
  CHECK(base.round.threshold.value_or(-1.0) == doctest::Approx(0.4));
  CHECK(base.domain_rules.min_up_down.value_or(false));
  CHECK_FALSE(base.domain_rules.commitment_logic.value_or(true));

  const auto& peak = base.domain_rules.peak_injection;
  CHECK(peak.enabled.value_or(false));
  CHECK(peak.peak_window.start.value_or(-1.0) == doctest::Approx(18.0));
  CHECK(peak.peak_window.end.value_or(-1.0) == doctest::Approx(22.0));
  CHECK(peak.solar_window.start.value_or(-1.0) == doctest::Approx(9.0));
  CHECK(peak.solar_window.end.value_or(-1.0) == doctest::Approx(16.0));

  CHECK(base.scip_repair.enabled.value_or(false));
  CHECK(base.inject.effort.value_or(MipStartEffort::no_check)
        == MipStartEffort::repair);
  CHECK(base.from_file.value_or("") == "start.json");
  CHECK(base.dump_file.value_or("") == "dump.json");
  CHECK(base.seed_solution_file.value_or("") == "seed.sol");
  CHECK(base.skip_relaxation.value_or(false));
  CHECK(base.root_basis_cache_file.value_or("") == "root.bas");
}

TEST_CASE(
    "MipStartOptions merge keeps base fields the overlay omits")  // NOLINT
{
  MipStartOptions base = full_overlay();

  MipStartOptions overlay {
      .relax =
          {
              .solver_options =
                  SolverOptions {
                      .feasible_eps = 1e-6,
                  },
              .on_infeasible = RelaxInfeasibleAction::feasopt,
          },
      .domain_rules =
          {
              .peak_injection =
                  {
                      .peak_window =
                          {
                              .end = 23.0,
                          },
                  },
          },
      .dump_file = "other.json",
  };
  base.merge(std::move(overlay));

  // Overridden by the overlay.
  CHECK(base.relax.on_infeasible.value_or(RelaxInfeasibleAction::stop)
        == RelaxInfeasibleAction::feasopt);
  CHECK(base.domain_rules.peak_injection.peak_window.end.value_or(-1.0)
        == doctest::Approx(23.0));
  CHECK(base.dump_file.value_or("") == "other.json");

  // Nested solver options are merged, not replaced.
  const auto relax_opts = base.relax.solver_options.value_or(SolverOptions {});
  CHECK(relax_opts.optimal_eps.value_or(0.0) == doctest::Approx(1e-7));
  CHECK(relax_opts.feasible_eps.value_or(0.0) == doctest::Approx(1e-6));

  // Untouched by the overlay.
  CHECK(base.enabled.value_or(false));
  CHECK(base.round.threshold.value_or(-1.0) == doctest::Approx(0.4));
  CHECK(base.domain_rules.peak_injection.peak_window.start.value_or(-1.0)
        == doctest::Approx(18.0));
  CHECK(base.domain_rules.peak_injection.solar_window.end.value_or(-1.0)
        == doctest::Approx(16.0));
  CHECK(base.inject.effort.value_or(MipStartEffort::no_check)
        == MipStartEffort::repair);
  CHECK(base.from_file.value_or("") == "start.json");
  CHECK(base.root_basis_cache_file.value_or("") == "root.bas");
}

TEST_CASE("MonolithicOptions merge routes mip_start")  // NOLINT
{
  SUBCASE("base without mip_start takes the overlay block")
  {
    MonolithicOptions base {};
    MonolithicOptions overlay {
        .mip_start = full_overlay(),
    };
    base.merge(std::move(overlay));
    REQUIRE(base.mip_start.has_value());
    CHECK(base.mip_start.value_or(MipStartOptions {})
              .round.threshold.value_or(-1.0)
          == doctest::Approx(0.4));
  }

  SUBCASE("both set: nested merge keeps base-only fields")
  {
    MonolithicOptions base {
        .mip_start =
            MipStartOptions {
                .from_file = "base.json",
            },
    };
    MonolithicOptions overlay {
        .mip_start =
            MipStartOptions {
                .enabled = true,
            },
    };
    base.merge(std::move(overlay));
    const auto ms = base.mip_start.value_or(MipStartOptions {});
    CHECK(ms.enabled.value_or(false));
    CHECK(ms.from_file.value_or("") == "base.json");
  }

  SUBCASE("overlay without mip_start leaves base untouched")
  {
    MonolithicOptions base {
        .mip_start =
            MipStartOptions {
                .enabled = false,
            },
    };
    base.merge(MonolithicOptions {});
    REQUIRE(base.mip_start.has_value());
    CHECK_FALSE(
        base.mip_start.value_or(MipStartOptions {}).enabled.value_or(true));
  }
}
