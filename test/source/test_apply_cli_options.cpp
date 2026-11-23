/**
 * @file      test_apply_cli_options.cpp
 * @brief     Unit tests for apply_cli_options(Planning&, MainOptions)
 * @date      2026-11-22
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * Covers the CLI → Planning translation of the shortcut flags that had
 * no direct test: `--lp-dump-backward` pass merging (and its env-var
 * twin), `--no-scale`, `--memory-saving`, `--memory-quota` /
 * `--memory-limit` / swap limits, MIP-start flags, hot-start, recover,
 * reduction toggles and the solver shortcuts.
 */

#include <cstdlib>
#include <string>

#include <doctest/doctest.h>
#include <gtopt/main_options.hpp>
#include <gtopt/planning.hpp>

using namespace gtopt;

namespace
{

Planning apply(const MainOptions& opts, Planning planning = {})
{
  apply_cli_options(planning, opts);
  return planning;
}

}  // namespace

TEST_CASE("apply_cli_options - --lp-dump-backward pass merging")  // NOLINT
{
  SUBCASE("no passes configured -> 'backward'")
  {
    const auto p = apply(MainOptions {.lp_dump_backward = "dumpdir"});
    CHECK(p.options.lp_debug.value_or(false) == true);
    CHECK(p.options.log_directory.value_or("") == "dumpdir");
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("") == "backward");
  }

  SUBCASE("empty passes string -> 'backward'")
  {
    Planning base;
    base.options.sddp_options.lp_debug_passes = "";
    const auto p = apply(MainOptions {.lp_dump_backward = "d"}, base);
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("") == "backward");
  }

  SUBCASE("other passes get ',backward' appended")
  {
    Planning base;
    base.options.sddp_options.lp_debug_passes = "forward";
    const auto p = apply(MainOptions {.lp_dump_backward = "d"}, base);
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("")
          == "forward,backward");
  }

  SUBCASE("existing 'backward' token (any case, padded) is kept as is")
  {
    Planning base;
    base.options.sddp_options.lp_debug_passes = "forward, \tBackWard ";
    const auto p = apply(MainOptions {.lp_dump_backward = "d"}, base);
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("")
          == "forward, \tBackWard ");
  }

  SUBCASE("'all' already covers backward")
  {
    Planning base;
    base.options.sddp_options.lp_debug_passes = "ALL";
    const auto p = apply(MainOptions {.lp_dump_backward = "d"}, base);
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("") == "ALL");
  }

  SUBCASE("a token that merely contains 'backward' does not match")
  {
    Planning base;
    base.options.sddp_options.lp_debug_passes = "backwards,aperture";
    const auto p = apply(MainOptions {.lp_dump_backward = "d"}, base);
    CHECK(p.options.sddp_options.lp_debug_passes.value_or("")
          == "backwards,aperture,backward");
  }

  SUBCASE("GTOPT_DUMP_BACKWARD_LP env var is the fallback")
  {
    setenv("GTOPT_DUMP_BACKWARD_LP", "envdir", 1);  // NOLINT
    const auto p = apply(MainOptions {});
    unsetenv("GTOPT_DUMP_BACKWARD_LP");  // NOLINT
    CHECK(p.options.lp_debug.value_or(false) == true);
    CHECK(p.options.log_directory.value_or("") == "envdir");
  }

  SUBCASE("empty env var is ignored")
  {
    setenv("GTOPT_DUMP_BACKWARD_LP", "", 1);  // NOLINT
    const auto p = apply(MainOptions {});
    unsetenv("GTOPT_DUMP_BACKWARD_LP");  // NOLINT
    CHECK_FALSE(p.options.lp_debug.has_value());
    CHECK_FALSE(p.options.sddp_options.lp_debug_passes.has_value());
  }
}

TEST_CASE("apply_cli_options - SDDP recovery flags")  // NOLINT
{
  SUBCASE("hot start true/false")
  {
    CHECK(apply(MainOptions {.sddp_hot_start = true})
              .options.sddp_options.cut_recovery_mode
          == HotStartMode::replace);
    CHECK(apply(MainOptions {.sddp_hot_start = false})
              .options.sddp_options.cut_recovery_mode
          == HotStartMode::none);
  }

  SUBCASE("without --recover, JSON recovery_mode is forced to none")
  {
    Planning base;
    base.options.sddp_options.recovery_mode = RecoveryMode::full;
    CHECK(apply(MainOptions {}, base).options.sddp_options.recovery_mode
          == RecoveryMode::none);
    CHECK(apply(MainOptions {.recover = true}, base)
              .options.sddp_options.recovery_mode
          == RecoveryMode::full);
  }

  SUBCASE("--fcut-log and --sddp-aperture-chunk-size / min iterations")
  {
    const auto p = apply(MainOptions {
        .fcut_log = true,
        .sddp_min_iterations = 3,
        .sddp_aperture_chunk_size = -1,
    });
    CHECK(p.options.sddp_options.fcut_log.value_or(false) == true);
    CHECK(p.options.sddp_options.aperture_chunk_size.value_or(0) == -1);
    CHECK(p.options.sddp_options.min_iterations.value_or(0) == 3);
  }
}

TEST_CASE("apply_cli_options - model shortcuts")  // NOLINT
{
  SUBCASE("--no-scale disables every scaling knob")
  {
    Planning base;
    base.options.model_options.scale_objective = 1000.0;
    base.options.model_options.scale_theta = 10.0;
    const auto p = apply(MainOptions {.no_scale = true}, base);
    const auto& mo = p.options.model_options;
    CHECK(mo.scale_objective.value_or(0.0) == doctest::Approx(1.0));
    CHECK(mo.scale_theta.value_or(0.0) == doctest::Approx(1.0));
    CHECK(mo.scale_loss_link.value_or(0.0) == doctest::Approx(1.0));
    CHECK(mo.auto_scale.value_or(true) == false);
    CHECK(p.options.lp_matrix_options.equilibration_method
          == LpEquilibrationMethod::none);
  }

  SUBCASE("--no-scale=false leaves JSON scaling alone")
  {
    Planning base;
    base.options.model_options.scale_objective = 1000.0;
    const auto p = apply(MainOptions {.no_scale = false}, base);
    CHECK(p.options.model_options.scale_objective.value_or(0.0)
          == doctest::Approx(1000.0));
    CHECK_FALSE(p.options.model_options.auto_scale.has_value());
  }

  SUBCASE("--no-lp-reduction wins over --lp-reduction")
  {
    CHECK(apply(MainOptions {.lp_reduction = true})
              .options.model_options.lp_reduction.value_or(false)
          == true);
    CHECK(apply(MainOptions {.lp_reduction = true, .no_lp_reduction = true})
              .options.model_options.lp_reduction.value_or(true)
          == false);
  }

  SUBCASE("--allow-oversupply and --no-mip")
  {
    const auto p =
        apply(MainOptions {.allow_oversupply = true, .no_mip = true});
    CHECK(p.options.model_options.allow_oversupply.value_or(false) == true);
    CHECK(p.options.model_options.continuous_phases.value_or("") == "all");
  }
}

TEST_CASE("apply_cli_options - MIP-start flags")  // NOLINT
{
  SUBCASE("--mip-start creates the sub-object")
  {
    const auto p = apply(MainOptions {.mip_start_enable = true});
    REQUIRE(p.options.monolithic_options.mip_start.has_value());
    CHECK(p.options.monolithic_options.mip_start.value_or(MipStartOptions {})
              .enabled.value_or(false)
          == true);
  }

  SUBCASE("--root-basis-cache does not toggle 'enabled'")
  {
    const auto p = apply(MainOptions {.root_basis_cache_file = "root.bas"});
    REQUIRE(p.options.monolithic_options.mip_start.has_value());
    const auto ms =
        p.options.monolithic_options.mip_start.value_or(MipStartOptions {});
    CHECK(ms.root_basis_cache_file.value_or("") == "root.bas");
    CHECK_FALSE(ms.enabled.has_value());
  }

  SUBCASE("empty --root-basis-cache is ignored")
  {
    const auto p = apply(MainOptions {.root_basis_cache_file = ""});
    CHECK_FALSE(p.options.monolithic_options.mip_start.has_value());
  }

  SUBCASE("both flags on an existing mip_start keep its other fields")
  {
    Planning base;
    base.options.monolithic_options.mip_start.emplace();
    base.options.monolithic_options.mip_start->enabled = false;
    const auto p = apply(
        MainOptions {
            .mip_start_enable = true,
            .root_basis_cache_file = "r.bas",
        },
        base);
    const auto ms =
        p.options.monolithic_options.mip_start.value_or(MipStartOptions {});
    CHECK(ms.enabled.value_or(false) == true);
    CHECK(ms.root_basis_cache_file.value_or("") == "r.bas");
  }
}

TEST_CASE("apply_cli_options - memory and pool limits")  // NOLINT
{
  SUBCASE("--memory-saving compress implies memory_emphasis")
  {
    const auto p = apply(MainOptions {.memory_saving = "compress"});
    CHECK(p.options.sddp_options.low_memory_mode.value_or(LowMemoryMode::off)
          == LowMemoryMode::compress);
    CHECK(p.options.solver_options.memory_emphasis.value_or(false) == true);
  }

  SUBCASE("--memory-saving off does not set memory_emphasis")
  {
    const auto p = apply(MainOptions {.memory_saving = "off"});
    CHECK(
        p.options.sddp_options.low_memory_mode.value_or(LowMemoryMode::compress)
        == LowMemoryMode::off);
    CHECK_FALSE(p.options.solver_options.memory_emphasis.has_value());
  }

  SUBCASE("explicit JSON memory_emphasis wins")
  {
    Planning base;
    base.options.solver_options.memory_emphasis = false;
    const auto p = apply(MainOptions {.memory_saving = "compress"}, base);
    CHECK(p.options.solver_options.memory_emphasis.value_or(true) == false);
  }

  SUBCASE("invalid --memory-saving throws")
  {
    CHECK_THROWS(apply(MainOptions {.memory_saving = "bogus"}));
  }

  SUBCASE("--memory-limit / --swap-limit / --swap-growth parse sizes")
  {
    const auto p = apply(MainOptions {
        .memory_limit = "2G",
        .swap_limit = "512M",
        .swap_growth = "64",
    });
    const auto& so = p.options.sddp_options;
    CHECK(so.pool_memory_limit_mb.value_or(0.0) == doctest::Approx(2048.0));
    CHECK(so.pool_max_process_swap_mb.value_or(0.0) == doctest::Approx(512.0));
    CHECK(so.pool_swap_growth_mb.value_or(0.0) == doctest::Approx(64.0));
  }

  SUBCASE("a valid --memory-quota takes precedence over --memory-limit")
  {
    const auto p = apply(MainOptions {
        .memory_limit = "1G",
        .memory_quota = 50.0,
    });
    const auto snap = MemoryMonitor::get_system_memory_snapshot();
    const auto lim = p.options.sddp_options.pool_memory_limit_mb.value_or(0.0);
    if (snap.total_mb > 0.0) {
      CHECK(lim == doctest::Approx(snap.total_mb * 0.5).epsilon(0.05));
    } else {
      CHECK(lim == doctest::Approx(1024.0));
    }
  }

  SUBCASE("an out-of-range --memory-quota falls back to --memory-limit")
  {
    const auto p = apply(MainOptions {
        .memory_limit = "1G",
        .memory_quota = 150.0,
    });
    CHECK(p.options.sddp_options.pool_memory_limit_mb.value_or(0.0)
          == doctest::Approx(1024.0));
  }

  SUBCASE("--cpu-factor")
  {
    const auto p = apply(MainOptions {.sddp_cpu_factor = 1.5});
    CHECK(p.options.sddp_options.pool_cpu_factor.value_or(0.0)
          == doctest::Approx(1.5));
  }
}

TEST_CASE("apply_cli_options - solver shortcuts")  // NOLINT
{
  const auto p = apply(MainOptions {
      .algorithm = LPAlgo::dual,
      .threads = 3,
      .naming_dialect = "plp",
      .mip_gap = 0.01,
      .time_limit = 60.0,
  });
  CHECK(p.options.solver_options.algorithm == LPAlgo::dual);
  CHECK(p.options.solver_options.threads == 3);
  CHECK(p.options.model_options.naming_dialect.value_or("") == "plp");
  CHECK(p.options.solver_options.mip_gap.value_or(0.0)
        == doctest::Approx(0.01));
  CHECK(p.options.solver_options.time_limit.value_or(0.0)
        == doctest::Approx(60.0));
}

TEST_CASE(
    "apply_cli_options - --lp-error is independent of --lp-debug")  // NOLINT
{
  SUBCASE("--lp-error alone reaches the planning")
  {
    const auto p = apply(MainOptions {.lp_error = true});
    CHECK(p.options.lp_error.value_or(false));
    CHECK_FALSE(p.options.lp_debug.has_value());
  }

  SUBCASE("--lp-debug alone keeps a JSON lp_error")
  {
    Planning base;
    base.options.lp_error = true;
    const auto p = apply(MainOptions {.lp_debug = true}, base);
    CHECK(p.options.lp_debug.value_or(false));
    CHECK(p.options.lp_error.value_or(false));
  }

  SUBCASE("--lp-error=false overrides a JSON lp_error")
  {
    Planning base;
    base.options.lp_error = true;
    const auto p = apply(MainOptions {.lp_error = false}, base);
    CHECK_FALSE(p.options.lp_error.value_or(true));
  }
}
