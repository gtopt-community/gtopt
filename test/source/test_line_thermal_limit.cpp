// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// test_line_thermal_limit.cpp — thermal limit vs line losses.
//
// Covers `line_thermal_limit_mode` (`flow` / `sending` / `full`, see
// `LineThermalLimitMode` in line_enums.hpp) and the analytic loss cap at
// the rated flow (`rated_loss_cap` in line_losses.cpp):
//
//   * row presence per loss mode and the per-line → global → `flow`
//     resolution chain;
//   * a congested 2-bus case where `full` makes `f + ℓ ≤ tmax` bind, so
//     the delivered power drops by the loss;
//   * `sending` follows `loss_allocation_mode` (no-op under `receiver`,
//     equal to `full` under `sender`);
//   * `linear` tightens the flow bound to `tmax / (1 + a·λ)`;
//   * a negative-price receiver (loss dumping incentive) where the loss
//     column cap at the rated flow — tighter than `R·envelope²/V²` when a
//     decoupled envelope lies above the flow cap — and the `full` row
//     both bound the fictitious loss.

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <filesystem>
#include <optional>
#include <span>
#include <string>
#include <string_view>

#include <arrow/api.h>
#include <doctest/doctest.h>
#include <gtopt/array_index_traits.hpp>
#include <gtopt/line.hpp>
#include <gtopt/linear_interface.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace test_line_thermal_limit_ns
{

namespace
{

constexpr double kTmax = 100.0;  // MW
constexpr double kR = 10.0;  // Ω
constexpr double kV = 100.0;  // kV
constexpr double kLossK = kR / (kV * kV);  // 1e-3 → ℓ(100 MW) = 10 MW
constexpr double kCheap = 10.0;  // $/MWh at b1
constexpr double kDear = 100.0;  // $/MWh at b2
constexpr double kDemand = 150.0;  // MW at b2 (line congested)

struct Case
{
  std::string_view mode {"bidirectional"};
  std::string_view layout {};
  int segments {4};
  std::string_view allocation {};  ///< empty → default (receiver)
  std::string_view line_limit {};  ///< per-line override; empty → unset
  std::string_view global_limit {};  ///< model option; empty → unset
  double envelope {0.0};  ///< loss_envelope; 0 → unset
  double loss_cost_eps {0.1};
  /// Negative-price receiver: b2 hosts a must-take source (gcost < 0)
  /// and b1 a small demand, so dumping energy into losses pays.
  bool negative_price {false};
  /// Expandable line: installed 50 MW, one prohibitively expensive
  /// 100 MW module ⇒ a capacity column / capacity rows at 50 MW.
  bool expandable {false};
  /// Soft (normal) rating `tmax_normal_ab/ba` [MW]; 0 → unset.
  double tmax_normal {0.0};
  /// `overload_penalty` [$/MWh] above the normal rating.
  double overload_penalty {0.0};
  /// When set, write the solution (parquet, long form) here.
  std::string output_dir {};
  /// Static thermal limit `tmax_ab` / `tmax_ba` (and `capacity`) [MW].
  double tmax {kTmax};
  /// Reverse the congested direction: cheap unit at b2, demand and
  /// dear unit at b1, so the line is loaded B→A.
  bool reverse {false};
  /// Precomputed DLR rating [MW] of one critical span (`Line.dlr`);
  /// 0 → no DLR.
  double dlr_rating {0.0};
  /// `model_options.loss_segment_bound`; nullopt → default (on).
  std::optional<bool> segment_bound {};
};

struct Fixture
{
  System system;
  Simulation simulation;
  PlanningOptions opts;
  PlanningOptionsLP options;
  SimulationLP sim_lp;
  SystemLP sys_lp;

  explicit Fixture(const Case& c)
      : system {make_system(c)}
      , simulation {
            .block_array =
                {
                    {
                        .uid = Uid {1},
                        .duration = 1,
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
                        .uid = Uid {0},
                    },
                },
        }
      , opts {}
      , options(make_options(c))
      , sim_lp(simulation, options)
      , sys_lp(system, sim_lp, build_opts())
  {
  }

  [[nodiscard]] auto& lp() { return sys_lp.linear_interface(); }

private:
  static System make_system(const Case& c)
  {
    Line ln {
        .uid = Uid {1},
        .name = "l1",
        .bus_a = Uid {1},
        .bus_b = Uid {2},
        .voltage = kV,
        .resistance = kR,
        .line_losses_mode = OptName {std::string(c.mode)},
        .loss_segments = c.segments,
        .tmax_ba = c.tmax,
        .tmax_ab = c.tmax,
        .capacity = c.tmax,
    };
    if (c.dlr_rating > 0.0) {
      LineDlr dlr {
          .span_mode = "min",
      };
      dlr.critical_spans = {
          CriticalSpan {
              .uid = Uid {11},
              .name = "s1",
              .rating = OptSTBRealFieldSched {c.dlr_rating},
          },
      };
      ln.dlr = std::move(dlr);
    }
    if (!c.layout.empty()) {
      ln.loss_pwl_layout = std::string(c.layout);
    }
    if (!c.allocation.empty()) {
      ln.loss_allocation_mode = std::string(c.allocation);
    }
    if (!c.line_limit.empty()) {
      ln.line_thermal_limit_mode = std::string(c.line_limit);
    }
    if (c.envelope > 0.0) {
      ln.loss_envelope = OptTRealFieldSched {c.envelope};
    }
    ln.loss_cost_eps = c.loss_cost_eps;
    if (c.tmax_normal > 0.0) {
      ln.tmax_normal_ab = c.tmax_normal;
      ln.tmax_normal_ba = c.tmax_normal;
      ln.overload_penalty = c.overload_penalty;
    }
    if (c.expandable) {
      ln.capacity = 50.0;
      ln.expcap = 100.0;
      ln.expmod = 1.0;
      ln.annual_capcost = 1.0e9;
    }

    if (c.negative_price) {
      // Power flows b2 → b1: b2 has a -50 $/MWh source, b1 a 20 MW
      // demand and nothing else.  Every MW burnt as loss earns 50 $.
      return {
          .name = "ThermalLimitNeg",
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
              },
          .demand_array =
              {
                  {
                      .uid = Uid {1},
                      .name = "d1",
                      .bus = Uid {1},
                      .capacity = 20.0,
                  },
              },
          .generator_array =
              {
                  {
                      .uid = Uid {1},
                      .name = "g2",
                      .bus = Uid {2},
                      .gcost = -50.0,
                      .capacity = 500.0,
                  },
              },
          .line_array = {ln},
      };
    }
    // Congested direction: A→B (cheap g1 at b1, demand at b2) or, with
    // `reverse`, B→A (cheap g1 at b2, demand and dear g2 at b1).
    const Uid load_bus {c.reverse ? 1 : 2};
    const Uid cheap_bus {c.reverse ? 2 : 1};
    return {
        .name = "ThermalLimit2Bus",
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
            },
        .demand_array =
            {
                {
                    .uid = Uid {1},
                    .name = "d2",
                    .bus = load_bus,
                    .capacity = kDemand,
                },
            },
        .generator_array =
            {
                {
                    .uid = Uid {1},
                    .name = "g1",
                    .bus = cheap_bus,
                    .gcost = kCheap,
                    .capacity = 500.0,
                },
                {
                    .uid = Uid {2},
                    .name = "g2",
                    .bus = load_bus,
                    .gcost = kDear,
                    .capacity = 500.0,
                },
            },
        .line_array = {ln},
    };
  }

  PlanningOptionsLP make_options(const Case& c)
  {
    opts.model_options.use_single_bus = false;
    opts.model_options.use_kirchhoff = false;
    opts.model_options.scale_objective = 1.0;
    opts.model_options.demand_fail_cost = 1000.0;
    opts.model_options.loss_segment_bound = c.segment_bound;
    if (!c.global_limit.empty()) {
      opts.model_options.line_thermal_limit_mode = std::string(c.global_limit);
    }
    if (!c.output_dir.empty()) {
      opts.output_directory = c.output_dir;
      opts.output_format = DataFormat::parquet;
      opts.output_round_decimals = 10;
    }
    opts.lp_matrix_options.col_with_names = true;
    opts.lp_matrix_options.row_with_names = true;
    opts.lp_matrix_options.col_with_name_map = true;
    opts.lp_matrix_options.row_with_name_map = true;
    return PlanningOptionsLP(opts);
  }

  static LpMatrixOptions build_opts()
  {
    LpMatrixOptions bo;
    bo.col_with_names = true;
    bo.col_with_name_map = true;
    bo.row_with_names = true;
    bo.row_with_name_map = true;
    return bo;
  }
};

[[nodiscard]] int count_rows(const LinearInterface& li, std::string_view sub)
{
  int n = 0;
  for (const auto& [name, idx] : li.row_name_map()) {
    if (name.contains(sub)) {
      ++n;
    }
  }
  return n;
}

/// Sum of primal values of the columns whose name contains `sub` (and
/// not `_seg`, so PWL segment columns are excluded).
[[nodiscard]] double sum_cols(const LinearInterface& li,
                              std::span<const double> v,
                              std::string_view sub)
{
  double total = 0.0;
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(sub) && !name.contains("_seg")) {
      total += v[static_cast<std::size_t>(value_of(idx))];
    }
  }
  return total;
}

struct Result
{
  double fp;
  double fn;
  double lossp;
  double lossn;
  double g2;  ///< expensive / negative-price generator output
  double g1;  ///< cheap generator output (uid 1)
  double overload;  ///< overloadp + overloadn
  double obj;
};

[[nodiscard]] bool has_col(const LinearInterface& li, std::string_view sub)
{
  return std::ranges::any_of(
      li.col_name_map(),
      [sub](const auto& kv)
      { return kv.first.contains(sub) && !kv.first.contains("_seg"); });
}

/// Sum of the PWL segment columns of one direction (`piecewise_direct`
/// has no aggregator flow column).
[[nodiscard]] double sum_segs(const LinearInterface& li,
                              std::span<const double> v,
                              std::string_view sub)
{
  double total = 0.0;
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(sub)) {
      total += v[static_cast<std::size_t>(value_of(idx))];
    }
  }
  return total;
}

[[nodiscard]] Result solve(Fixture& fx)
{
  auto& li = fx.lp();
  auto r = li.resolve();
  REQUIRE(r.has_value());
  REQUIRE(r.value() == 0);
  const auto sol = li.get_col_sol_raw();
  const double fs = sum_cols(li, sol, "line_flows_");
  const bool direct =
      !has_col(li, "line_flowp_") && !has_col(li, "line_flows_");
  const double fp = direct ? sum_segs(li, sol, "line_flowp_seg_")
                           : sum_cols(li, sol, "line_flowp_");
  const double fn = direct ? sum_segs(li, sol, "line_flown_seg_")
                           : sum_cols(li, sol, "line_flown_");
  return {
      .fp = fp + std::max(fs, 0.0),
      .fn = fn + std::max(-fs, 0.0),
      .lossp = sum_cols(li, sol, "line_lossp_"),
      .lossn = sum_cols(li, sol, "line_lossn_"),
      .g2 = sum_cols(li, sol, "generator_generation_2_"),
      .g1 = sum_cols(li, sol, "generator_generation_1_"),
      .overload = sum_cols(li, sol, "line_overloadp_")
          + sum_cols(li, sol, "line_overloadn_"),
      .obj = li.get_obj_value(),
  };
}

/// Raw LP value (dual / reduced cost) of the single row / column whose
/// name contains `sub`.
[[nodiscard]] double row_dual(LinearInterface& li, std::string_view sub)
{
  const auto duals = li.get_row_dual_raw();
  for (const auto& [name, idx] : li.row_name_map()) {
    if (name.contains(sub)) {
      return duals[static_cast<std::size_t>(value_of(idx))];
    }
  }
  FAIL("row not found: " << sub);
  return 0.0;
}

[[nodiscard]] double col_rc(LinearInterface& li, std::string_view sub)
{
  const auto rc = li.get_col_cost_raw();
  for (const auto& [name, idx] : li.col_name_map()) {
    if (name.contains(sub) && !name.contains("_seg")) {
      return rc[static_cast<std::size_t>(value_of(idx))];
    }
  }
  FAIL("col not found: " << sub);
  return 0.0;
}

/// Value of the single cell of a long-form output dataset
/// `<dir>/<Class>/<stem>.parquet` (nullopt when absent).
[[nodiscard]] std::optional<double> read_single_value(
    const std::filesystem::path& dataset)
{
  auto table = parquet_read_table(dataset / "scene=0" / "phase=0" / "part");
  if (!table.has_value() || (*table)->num_rows() != 1) {
    return std::nullopt;
  }
  const auto col = std::static_pointer_cast<arrow::DoubleArray>(
      (*table)->GetColumnByName("value")->chunk(0));
  return col->Value(0);
}

}  // namespace

TEST_CASE("line_thermal_limit_mode resolves and parses")  // NOLINT
{
  CHECK(enum_from_name<LineThermalLimitMode>("flow").value_or(
            LineThermalLimitMode::full)
        == LineThermalLimitMode::flow);
  CHECK(enum_from_name<LineThermalLimitMode>("sending").value_or(
            LineThermalLimitMode::flow)
        == LineThermalLimitMode::sending);
  CHECK(enum_from_name<LineThermalLimitMode>("full").value_or(
            LineThermalLimitMode::flow)
        == LineThermalLimitMode::full);
  CHECK_FALSE(enum_from_name<LineThermalLimitMode>("bogus").has_value());

  PlanningOptions po;
  CHECK(PlanningOptionsLP(po).line_thermal_limit_mode()
        == LineThermalLimitMode::flow);
  po.model_options.line_thermal_limit_mode = "full";
  CHECK(PlanningOptionsLP(po).line_thermal_limit_mode()
        == LineThermalLimitMode::full);

  Line ln;
  CHECK_FALSE(ln.line_thermal_limit_mode_enum().has_value());
  ln.line_thermal_limit_mode = "sending";
  CHECK(ln.line_thermal_limit_mode_enum().value_or(LineThermalLimitMode::flow)
        == LineThermalLimitMode::sending);
}

TEST_CASE("line_thermal_limit_mode adds rows only when requested")  // NOLINT
{
  SUBCASE("default (unset) adds nothing")
  {
    Fixture fx({});
    CHECK(count_rows(fx.lp(), "thermal") == 0);
  }
  SUBCASE("global full adds one row per direction")
  {
    Fixture fx({.global_limit = "full"});
    CHECK(count_rows(fx.lp(), "line_thermalp_") == 1);
    CHECK(count_rows(fx.lp(), "line_thermaln_") == 1);
  }
  SUBCASE("per-line flow overrides global full")
  {
    Fixture fx({.line_limit = "flow", .global_limit = "full"});
    CHECK(count_rows(fx.lp(), "thermal") == 0);
  }
  SUBCASE("per-line full overrides global flow")
  {
    Fixture fx({.line_limit = "full", .global_limit = "flow"});
    CHECK(count_rows(fx.lp(), "thermal") == 2);
  }
  SUBCASE("sending is a no-op under receiver allocation")
  {
    Fixture fx({.line_limit = "sending"});
    CHECK(count_rows(fx.lp(), "thermal") == 0);
  }
  SUBCASE("sending adds rows under split allocation")
  {
    Fixture fx({.allocation = "split", .line_limit = "sending"});
    CHECK(count_rows(fx.lp(), "thermal") == 2);
  }
  SUBCASE("every loss mode with a loss term gets its rows")
  {
    for (const auto* mode :
         {"tangent_signed_flow", "piecewise_direct", "piecewise"})
    {
      CAPTURE(mode);
      Fixture fx({.mode = mode, .line_limit = "full"});
      CHECK(count_rows(fx.lp(), "thermal") == 2);
    }
    Fixture tangent(
        {.mode = "piecewise", .layout = "tangent", .line_limit = "full"});
    CHECK(count_rows(tangent.lp(), "thermal") == 2);
  }
  SUBCASE("none and linear add no row")
  {
    Fixture none({.mode = "none", .line_limit = "full"});
    CHECK(count_rows(none.lp(), "thermal") == 0);
    Fixture lin({.mode = "linear", .line_limit = "full"});
    CHECK(count_rows(lin.lp(), "thermal") == 0);
  }
}

TEST_CASE("line_thermal_limit full binds on a congested line")  // NOLINT
{
  // Flow-only: the sending-end flow sits at tmax and the receiver gets
  // tmax − ℓ(tmax) = 90 MW.  With `full` the row f + ℓ ≤ tmax binds and
  // the delivered power drops below 90 MW by about another loss.
  for (const auto* mode :
       {"bidirectional", "tangent_signed_flow", "piecewise_direct"})
  {
    CAPTURE(mode);
    Fixture flow_fx({.mode = mode});
    const auto base = solve(flow_fx);
    Fixture full_fx({.mode = mode, .line_limit = "full"});
    const auto full = solve(full_fx);

    CHECK(base.fp == doctest::Approx(kTmax).epsilon(1e-6));
    CHECK(full.fp < kTmax - 1.0);

    if (std::string_view {mode} != "piecewise_direct") {
      const double loss_base = base.lossp + base.lossn;
      const double loss_full = full.lossp + full.lossn;
      // tangent_signed_flow books ½ of ℓ at each bus; the others book it
      // all at the receiver.  Either way f + ℓ ≤ tmax binds.
      CHECK(full.fp + loss_full == doctest::Approx(kTmax).epsilon(1e-6));
      CHECK(loss_full < loss_base);
      CHECK(loss_base <= (kLossK * kTmax * kTmax * 1.25) + 1e-9);
    }
    // Less power delivered over the line ⇒ the dear unit at b2 runs more.
    CHECK(full.g2 > base.g2 + 1.0);
  }
}

TEST_CASE("line_thermal_limit sending follows loss allocation")  // NOLINT
{
  Fixture recv({.line_limit = "sending"});
  Fixture flow({});
  Fixture sender_sending({.allocation = "sender", .line_limit = "sending"});
  Fixture sender_full({.allocation = "sender", .line_limit = "full"});
  Fixture split_sending({.allocation = "split", .line_limit = "sending"});

  const auto r_recv = solve(recv);
  const auto r_flow = solve(flow);
  const auto r_ss = solve(sender_sending);
  const auto r_sf = solve(sender_full);
  const auto r_split = solve(split_sending);

  CHECK(r_recv.fp == doctest::Approx(r_flow.fp));
  CHECK(r_ss.fp == doctest::Approx(r_sf.fp));
  CHECK(r_ss.fp + r_ss.lossp == doctest::Approx(kTmax).epsilon(1e-6));
  CHECK(r_split.fp + (0.5 * r_split.lossp)
        == doctest::Approx(kTmax).epsilon(1e-6));
}

TEST_CASE("line_thermal_limit full tightens linear flow bound")  // NOLINT
{
  Fixture fx({.mode = "linear", .line_limit = "full"});
  const auto r = solve(fx);
  // λ = R·fmax/V² = 0.1 ⇒ f ≤ 100 / 1.1.
  CHECK(r.fp == doctest::Approx(kTmax / 1.1).epsilon(1e-6));
}

TEST_CASE("line loss cap at rated flow bounds fictitious loss")  // NOLINT
{
  // Decoupled envelope 3× ABOVE the flow cap: the K = 3 segments are
  // 100 MW wide and unbounded (no overflow tier), so with ε = 0 and a
  // negative-cost source at b2 the LP burns energy as fictitious loss —
  // out-of-order segment fill and a phantom circulation fp/fn (b1 can
  // only absorb 20 MW, so the source output is 20 + lossp + lossn).
  // Each direction's loss column is now capped at the model's loss at
  // tmax (secant on the first segment = R·tmax²/V² = 10 MW) instead of
  // R·envelope²/V² = 90 MW, so the burnt energy is exactly 2 × 10 MW
  // (up to 2 × 50 MW before: steepest-segment slope 0.5 on fp, fn ≤ 100).
  // That is the legacy segment layout (`loss_segment_bound = false`);
  // the default ordered-fill segment bounds (segments 2, 3 capped at 0)
  // tighten it further but do not remove it: the phantom loss stays
  // positive and never exceeds the legacy one.
  Case neg {
      .segments = 3,
      .envelope = 3.0 * kTmax,
      .loss_cost_eps = 0.0,
      .negative_price = true,
      .segment_bound = false,
  };
  const double cap = kLossK * kTmax * kTmax;
  Fixture legacy_fx(neg);
  const auto legacy = solve(legacy_fx);
  CHECK(legacy.lossp + legacy.lossn
        == doctest::Approx(2.0 * cap).epsilon(1e-6));
  neg.segment_bound = true;
  Fixture fx(neg);
  const auto r = solve(fx);
  CHECK(r.lossp <= cap + 1e-6);
  CHECK(r.lossn <= cap + 1e-6);
  CHECK(r.lossp + r.lossn <= legacy.lossp + legacy.lossn + 1e-6);
  CHECK(r.lossp + r.lossn > 0.0);
  CHECK(r.fn <= kTmax + 1e-6);
  CHECK(r.fp <= kTmax + 1e-6);

  // `full`: f + ℓ ≤ tmax also holds per direction (here it does not
  // bind: the cap already limits the burnt loss).
  Case full = neg;
  full.line_limit = "full";
  Fixture fx_full(full);
  const auto rf = solve(fx_full);
  CHECK(rf.fn + rf.lossn <= kTmax + 1e-6);
  CHECK(rf.fp + rf.lossp <= kTmax + 1e-6);
  CHECK(rf.lossp + rf.lossn <= (2.0 * cap) + 1e-6);
}

TEST_CASE("line_thermal_limit full folds loss into capacity rows")  // NOLINT
{
  // Expandable line: the installed capacity (50 MW, below tmax) is the
  // binding limit through the capacity row, which under `full` carries
  // the loss too: capacity − f − ℓ ≥ 0.
  for (const auto* mode : {"bidirectional", "tangent_signed_flow"}) {
    CAPTURE(mode);
    Fixture flow_fx({.mode = mode, .expandable = true});
    const auto base = solve(flow_fx);
    Fixture full_fx({.mode = mode, .line_limit = "full", .expandable = true});
    const auto full = solve(full_fx);
    CHECK(base.fp == doctest::Approx(50.0).epsilon(1e-6));
    CHECK(full.fp + full.lossp + full.lossn
          == doctest::Approx(50.0).epsilon(1e-6));
    CHECK(full.fp < 50.0 - 0.5);
  }
}

TEST_CASE("line_thermal_limit band binds on flow + a*loss")  // NOLINT
{
  // Normal rating 80 MW, hard 100 MW, overload penalty 5 $/MWh: the dear
  // unit (100 $/MWh) makes it worth running into the band.  The band row
  // is `flow + a·loss − overload ≤ 80`, so the overload slack equals
  // the loaded quantity above 80 MW and pays 5 $/MWh.
  constexpr double kNormal = 80.0;
  constexpr double kPenalty = 5.0;

  SUBCASE("flow mode: band on the flow only (historical)")
  {
    Fixture fx({.tmax_normal = kNormal, .overload_penalty = kPenalty});
    const auto r = solve(fx);
    CHECK(r.fp == doctest::Approx(kTmax).epsilon(1e-6));
    CHECK(r.overload == doctest::Approx(r.fp - kNormal).epsilon(1e-6));
  }

  for (const auto* mode :
       {"bidirectional", "tangent_signed_flow", "piecewise_direct", "linear"})
  {
    CAPTURE(mode);
    Fixture fx({.mode = mode,
                .line_limit = "full",
                .tmax_normal = kNormal,
                .overload_penalty = kPenalty});
    CHECK(count_rows(fx.lp(), "line_overloadp_") == 1);
    CHECK(count_rows(fx.lp(), "line_overloadn_") == 1);
    const auto r = solve(fx);
    const std::string_view m {mode};
    // Loaded quantity f + ℓ (linear: ℓ = λ·f with λ = 0.1).
    double loaded = r.fp + r.lossp + r.lossn;
    if (m == "linear") {
      loaded = 1.1 * r.fp;
    }
    if (m != "piecewise_direct") {
      CHECK(loaded == doctest::Approx(kTmax).epsilon(1e-6));
      CHECK(r.overload == doctest::Approx(loaded - kNormal).epsilon(1e-6));
      // Objective = energy + overload penalty + ε on the loss columns
      // (+ the loss_flow_abs_cost pin on Σ v = |f| for
      // tangent_signed_flow).
      const double eps_loss = (m == "linear") ? 0.0 : 0.1 * (loaded - r.fp);
      const double pin = (m == "tangent_signed_flow")
          ? PlanningOptionsLP::default_loss_flow_abs_cost * std::abs(r.fp)
          : 0.0;
      CHECK(r.obj
            == doctest::Approx((kCheap * r.g1) + (kDear * r.g2)
                               + (kPenalty * r.overload) + eps_loss + pin)
                   .epsilon(1e-6));
    } else {
      // No loss column: the band carries Σ(1 + lf_k)·seg_k, so the slack
      // exceeds the bare flow excess by the loss.
      CHECK(r.overload > r.fp - kNormal + 1.0);
      CHECK(r.overload == doctest::Approx(kTmax - kNormal).epsilon(1e-6));
    }
  }

  SUBCASE("flow mode keeps the historical scope")
  {
    // tangent_signed_flow has no flowp/flown column: under `flow` it has
    // never carried the band (unchanged); `full` adds it.
    Fixture flow_fx({.mode = "tangent_signed_flow",
                     .tmax_normal = kNormal,
                     .overload_penalty = kPenalty});
    CHECK(count_rows(flow_fx.lp(), "line_overload") == 0);
  }

  SUBCASE("sending under split uses half the loss")
  {
    Fixture fx({.allocation = "split",
                .line_limit = "sending",
                .tmax_normal = kNormal,
                .overload_penalty = kPenalty});
    const auto r = solve(fx);
    CHECK(r.overload
          == doctest::Approx(r.fp + (0.5 * r.lossp) - kNormal).epsilon(1e-6));
  }
}

TEST_CASE("line_thermal_limit duals: thermal row vs flow bound")  // NOLINT
{
  // bidirectional, K = 4 segments over a decoupled 120 MW envelope
  // (30 MW wide, slopes c·30·(2k−1)): both operating points (f = 100
  // under `flow`, f ≈ 91.6 under `full`) lie inside the top segment,
  // ℓ' = c·30·7 = 0.21.  Relaxing the limit by 1 MW is worth
  //   flow: V = π2·(1 − ℓ') − π1 − ε·ℓ'
  //   full: V / (1 + ℓ')                          (thermalp row dual)
  // because under `full` 1 MW more of f + ℓ admits only 1/(1 + ℓ') MW of
  // flow.  Under `flow` the rent sits on the flowp column bound — and,
  // since the loss column is capped at exactly ℓ(tmax), possibly partly
  // on the loss column bound (dual degeneracy): the bound pair moves
  // together as (1, ℓ'), so rc(flowp) + ℓ'·rc(lossp) = −V.  The row
  // dual carries the same sign as that reduced cost (both are shadow
  // prices of a binding upper limit in a minimisation).
  //
  // Pinned on the legacy segment layout (`loss_segment_bound = false`):
  // the ordered-fill bound caps the top segment at tmax − 90 = 10 MW,
  // a third bound that moves with the pair and takes part of the rent.
  constexpr double kEnvelope = 120.0;
  constexpr double kSlope = kLossK * 30.0 * 7.0;
  constexpr double kEps = 0.1;
  Fixture flow_fx({.envelope = kEnvelope, .segment_bound = false});
  REQUIRE(flow_fx.lp().resolve().value_or(-1) == 0);
  const double pi1 = row_dual(flow_fx.lp(), "bus_balance_1_");
  const double pi2 = row_dual(flow_fx.lp(), "bus_balance_2_");
  const double value = (pi2 * (1.0 - kSlope)) - pi1 - (kEps * kSlope);
  const double rc_flow = col_rc(flow_fx.lp(), "line_flowp_")
      + (kSlope * col_rc(flow_fx.lp(), "line_lossp_"));
  CAPTURE(pi1);
  CAPTURE(pi2);
  CAPTURE(rc_flow);
  CHECK(pi1 == doctest::Approx(kCheap));
  CHECK(pi2 == doctest::Approx(kDear));
  CHECK(rc_flow == doctest::Approx(-value).epsilon(1e-6));

  Fixture full_fx({.line_limit = "full", .envelope = kEnvelope});
  REQUIRE(full_fx.lp().resolve().value_or(-1) == 0);
  const double d_full = row_dual(full_fx.lp(), "line_thermalp_");
  CAPTURE(d_full);
  CHECK(d_full == doctest::Approx(-value / (1.0 + kSlope)).epsilon(1e-6));
  // The flow column is strictly inside its bounds: no reduced cost.
  CHECK(col_rc(full_fx.lp(), "line_flowp_") == doctest::Approx(0.0));
  // The idle B→A row does not bind.
  CHECK(row_dual(full_fx.lp(), "line_thermaln_") == doctest::Approx(0.0));
}

TEST_CASE("line_thermal_limit duals: LMP decomposition and tolls")  // NOLINT
{
  // Split allocation (a = ½): sending-end power P_s = f + ℓ/2, delivered
  // power P_r = f − ℓ/2.  Envelope 120 MW, K = 4 ⇒ 30 MW segments; every
  // operating point (f = 100 / 95.4 / 91.6) lies in the top segment with
  // marginal loss s = c·30·7 = 0.21.  π1 = 10 (g1), π2 = 100 (g2) in all
  // modes, so 1 MW more line flow is worth
  //   V = π2·(1 − (1−a)·s) − π1·(1 + a·s)
  // and the limit dual is V / h'(f) for the limited quantity h:
  //   lossless  h = f          y = −(π2 − π1)
  //   flow      h = f          rc(f) + s·rc(ℓ) = −V   (column bounds)
  //   sending   h = f + a·ℓ    y = −V/(1 + a·s) = −(π2·r − π1),
  //                            r = dP_r/dP_s = (1 − (1−a)s)/(1 + a·s)
  //   full      h = f + ℓ      y = −V/(1 + s)
  // `sending` is the congestion part of the LMP difference measured at
  // the sending end: π2 − π1 = μ + π2·(1 − r), whatever the allocation.
  // The tariff income IT = π2·P_r − π1·P_s splits into the FTR-style
  // congestion rent μ·T plus the loss rent π2·(P_r − r·P_s).
  constexpr double kEnvelope = 120.0;
  constexpr double kS = kLossK * 30.0 * 7.0;
  constexpr double kA = 0.5;
  const double value =
      (kDear * (1.0 - ((1.0 - kA) * kS))) - (kCheap * (1.0 + (kA * kS)));
  const double ratio = (1.0 - ((1.0 - kA) * kS)) / (1.0 + (kA * kS));

  SUBCASE("lossless: dual equals the LMP difference")
  {
    Fixture fx({.mode = "none", .loss_cost_eps = 0.0});
    REQUIRE(fx.lp().resolve().value_or(-1) == 0);
    const double pi1 = row_dual(fx.lp(), "bus_balance_1_");
    const double pi2 = row_dual(fx.lp(), "bus_balance_2_");
    CHECK(pi1 == doctest::Approx(kCheap));
    CHECK(pi2 == doctest::Approx(kDear));
    CHECK(col_rc(fx.lp(), "line_flowp_") == doctest::Approx(-(pi2 - pi1)));
  }

  SUBCASE("flow: the rent sits on the column bounds")
  {
    // Legacy segment layout (`loss_segment_bound = false`), as in the
    // "thermal row vs flow bound" test: the ordered-fill bound caps the
    // top segment at tmax - 90 MW and takes part of the rent.
    Fixture fx({
        .allocation = "split",
        .envelope = kEnvelope,
        .loss_cost_eps = 0.0,
        .segment_bound = false,
    });
    const auto r = solve(fx);
    CHECK(r.fp == doctest::Approx(kTmax));
    const double rc =
        col_rc(fx.lp(), "line_flowp_") + (kS * col_rc(fx.lp(), "line_lossp_"));
    CHECK(rc == doctest::Approx(-value).epsilon(1e-6));
  }

  for (const auto* mode : {"sending", "full"}) {
    CAPTURE(mode);
    const bool sending = std::string_view {mode} == "sending";
    Fixture fx({.allocation = "split",
                .line_limit = mode,
                .envelope = kEnvelope,
                .loss_cost_eps = 0.0});
    const auto r = solve(fx);
    const double pi1 = row_dual(fx.lp(), "bus_balance_1_");
    const double pi2 = row_dual(fx.lp(), "bus_balance_2_");
    const double y = row_dual(fx.lp(), "line_thermalp_");
    CHECK(pi1 == doctest::Approx(kCheap));
    CHECK(pi2 == doctest::Approx(kDear));
    const double share = sending ? kA : 1.0;
    CHECK(y == doctest::Approx(-value / (1.0 + (share * kS))).epsilon(1e-6));
    const double p_send = r.fp + (kA * r.lossp);
    const double p_recv = r.fp - ((1.0 - kA) * r.lossp);
    CHECK(r.fp + (share * r.lossp) == doctest::Approx(kTmax).epsilon(1e-6));
    const double income = (pi2 * p_recv) - (pi1 * p_send);
    if (sending) {
      // Physical sending-end limit: μ = π2·r − π1, and
      // IT = μ·T + π2·(P_r − r·P_s)   (congestion rent + loss rent).
      CHECK(-y == doctest::Approx((pi2 * ratio) - pi1).epsilon(1e-6));
      CHECK(
          income
          == doctest::Approx((-y * kTmax) + (pi2 * (p_recv - (ratio * p_send))))
                 .epsilon(1e-6));
    }
    // Generic identity: IT = V·f + (s·f − ℓ)·((1−a)·π2 + a·π1).
    CHECK(income
          == doctest::Approx((value * r.fp)
                             + (((kS * r.fp) - r.lossp)
                                * (((1.0 - kA) * pi2) + (kA * pi1))))
                 .epsilon(1e-6));
  }
}

TEST_CASE("line_thermal_limit duals are written to the Line output")  // NOLINT
{
  const auto dir =
      std::filesystem::temp_directory_path() / "gtopt_thermal_limit_out";
  std::filesystem::remove_all(dir);
  std::filesystem::create_directories(dir);

  SUBCASE("thermalp_dual / thermaln_dual")
  {
    Fixture fx({.line_limit = "full", .output_dir = dir.string()});
    REQUIRE(fx.lp().resolve().value_or(-1) == 0);
    const double raw = row_dual(fx.lp(), "line_thermalp_");
    fx.sys_lp.write_out();
    // scale_objective = 1, duration 1, probability 1 ⇒ physical = raw.
    const auto v = read_single_value(dir / "Line" / "thermalp_dual.parquet");
    REQUIRE(v.has_value());
    CHECK(v.value_or(0.0) == doctest::Approx(raw).epsilon(1e-9));
    CHECK(std::filesystem::exists(dir / "Line" / "thermaln_dual.parquet"));
    CHECK_FALSE(
        std::filesystem::exists(dir / "Line" / "capacityp_dual.parquet"));
  }
  SUBCASE("flow mode writes no thermal duals")
  {
    Fixture fx({.output_dir = dir.string()});
    REQUIRE(fx.lp().resolve().value_or(-1) == 0);
    fx.sys_lp.write_out();
    CHECK_FALSE(
        std::filesystem::exists(dir / "Line" / "thermalp_dual.parquet"));
  }
  SUBCASE("capacityp_dual on an expandable line")
  {
    Fixture fx({.expandable = true, .output_dir = dir.string()});
    REQUIRE(fx.lp().resolve().value_or(-1) == 0);
    const double raw = row_dual(fx.lp(), "line_capacityp_");
    CHECK(std::abs(raw) > 1.0);  // installed 50 MW binds
    fx.sys_lp.write_out();
    const auto v = read_single_value(dir / "Line" / "capacityp_dual.parquet");
    REQUIRE(v.has_value());
    CHECK(v.value_or(0.0) == doctest::Approx(raw).epsilon(1e-9));
  }
  std::filesystem::remove_all(dir);
}

// ── Literature benchmark: the sending-end limit (PowerModels DCPLL) ──
//
// PowerModels' DC-with-line-losses model (DCPLL; Coffrin, Bent, Sundar,
// Ng, Lubin, PSCC 2018, DOI 10.23919/PSCC.2018.8442948; formulation
// from Coffrin, Van Hentenryck, Bent, PESGM 2012, DOI
// 10.1109/PESGM.2012.6345342) carries one power variable per END of a
// branch, `p_fr` and `p_to`, and bounds EACH of them by the rating
// (`variable_branch_power_real`, src/core/variable.jl, flow bounds from
// `ref_calc_branch_flow_bounds`; loss relation `p_fr + p_to ≥ r·p_fr²`
// in `constraint_ohms_yt_to`, src/form/dcp.jl).  The loss makes the
// sending end the larger of the two, so the sending-end bound is the one
// that binds — gtopt's `line_thermal_limit_mode = sending`:
//
//     f_dir + a·ℓ_dir ≤ T_dir,   a = share of ℓ booked at the sender
//                                    (receiver 0, split ½, sender 1).
//
// This test runs the congested 2-bus line in both directions under the
// full matrix {flow, sending, full} × {receiver, split, sender} and
// pins the relations between the three limits.

namespace
{

constexpr int kLitSegments = 16;
constexpr double kLitDelta = 0.01;  // MW, finite-difference step

[[nodiscard]] double sender_share(std::string_view allocation)
{
  if (allocation == "split") {
    return 0.5;
  }
  if (allocation == "sender") {
    return 1.0;
  }
  return 0.0;
}

/// Flow and loss of the loaded direction.
struct Loaded
{
  double f;
  double loss;
  double obj;
};

[[nodiscard]] Loaded loaded(const Result& r, bool reverse)
{
  return reverse ? Loaded {.f = r.fn, .loss = r.lossn, .obj = r.obj}
                 : Loaded {.f = r.fp, .loss = r.lossp, .obj = r.obj};
}

[[nodiscard]] Case lit_case(std::string_view allocation,
                            std::string_view limit,
                            bool reverse,
                            double tmax = kTmax,
                            double dlr_rating = 0.0)
{
  return Case {
      .mode = "piecewise",
      .segments = kLitSegments,
      .allocation = allocation,
      .line_limit = limit,
      // Fixed PWL range, so a perturbed tmax (finite differences) keeps
      // the same segments.
      .envelope = kTmax,
      .tmax = tmax,
      .reverse = reverse,
      .dlr_rating = dlr_rating,
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "line_thermal_limit literature: sending-end limit, all modes, both "
    "directions")
{
  for (const bool reverse : {false, true}) {
    for (const std::string_view alloc : {"receiver", "split", "sender"}) {
      CAPTURE(reverse);
      CAPTURE(alloc);
      const double a = sender_share(alloc);
      const std::string_view dir_row =
          reverse ? "line_thermaln_" : "line_thermalp_";

      Fixture flow_fx(lit_case(alloc, "flow", reverse));
      Fixture send_fx(lit_case(alloc, "sending", reverse));
      Fixture full_fx(lit_case(alloc, "full", reverse));
      const auto flow = loaded(solve(flow_fx), reverse);
      const auto send = loaded(solve(send_fx), reverse);
      const auto full = loaded(solve(full_fx), reverse);

      // The line is congested and carries a real loss in every case.
      CHECK(flow.loss > 1.0);
      CHECK(send.loss > 1.0);
      CHECK(full.loss > 1.0);

      // `flow`: the flow variable itself sits at the limit.
      CHECK(flow.f == doctest::Approx(kTmax).epsilon(1e-9));

      // (1) `sending`: the sending-end power f + a·ℓ equals tmax.
      CHECK(send.f + (a * send.loss) == doctest::Approx(kTmax).epsilon(1e-9));

      // (2) Under `receiver` allocation the flow variable already IS the
      // sending-end power: `flow` and `sending` are the same LP optimum.
      if (alloc == "receiver") {
        CHECK(count_rows(send_fx.lp(), "thermal") == 0);
        CHECK(send.f == doctest::Approx(flow.f).epsilon(1e-9));
        CHECK(send.obj == doctest::Approx(flow.obj).epsilon(1e-9));
      }

      // (3) `full`: f + ℓ = tmax.  The flow is below `sending` by
      // ℓ_full − a·ℓ_send (≈ (1 − a)·ℓ), equal under `sender`.
      CHECK(full.f + full.loss == doctest::Approx(kTmax).epsilon(1e-9));
      CHECK(send.f - full.f
            == doctest::Approx(full.loss - (a * send.loss)).epsilon(1e-9));
      // Since ℓ_send ≥ ℓ_full (more flow), the gap is at most
      // (1 − a)·ℓ_full.
      if (a < 1.0) {
        CHECK(full.f < send.f - (0.5 * (1.0 - a) * full.loss));
        CHECK(send.f - full.f <= ((1.0 - a) * full.loss) + 1e-9);
        CHECK(send.loss >= full.loss - 1e-9);
      } else {
        CHECK(full.f == doctest::Approx(send.f).epsilon(1e-9));
        CHECK(full.obj == doctest::Approx(send.obj).epsilon(1e-9));
      }

      // (4) Objective ordering: tighter limit, dearer dispatch.
      CHECK(full.obj >= send.obj - 1e-7);
      CHECK(send.obj >= flow.obj - 1e-7);
      if (a < 1.0) {
        CHECK(full.obj > send.obj + 1.0);
      }
      if (a > 0.0) {
        CHECK(send.obj > flow.obj + 1.0);
      }

      // (5) Duals: the thermal-row dual of the loaded direction is the
      // objective sensitivity ∂z/∂tmax (finite difference on a lowered
      // tmax, same PWL segments); the idle direction's row is slack.
      for (const std::string_view limit : {"sending", "full"}) {
        if (limit == "sending" && a == 0.0) {
          continue;  // no row (receiver: sending == flow)
        }
        CAPTURE(limit);
        Fixture base(lit_case(alloc, limit, reverse));
        Fixture low(lit_case(alloc, limit, reverse, kTmax - kLitDelta));
        const auto rb = solve(base);
        const auto rl = solve(low);
        const double dual = row_dual(base.lp(), dir_row);
        const double fd = (rb.obj - rl.obj) / kLitDelta;
        CAPTURE(dual);
        CAPTURE(fd);
        CHECK(dual < -1.0);
        CHECK(dual == doctest::Approx(fd).epsilon(1e-6));
        CHECK(row_dual(base.lp(), reverse ? "line_thermalp_" : "line_thermaln_")
              == doctest::Approx(0.0));
      }
    }
  }
}

TEST_CASE(  // NOLINT
    "line_thermal_limit literature: DLR rating follows the sending-end "
    "limit")
{
  // Same matrix with a precomputed DLR rating (80 MW, one critical span,
  // `span_mode = min`) below the static 100 MW: the DLR rating caps the
  // same loaded quantity as tmax, f + a·ℓ ≤ T_dlr.
  constexpr double kRating = 80.0;
  for (const bool reverse : {false, true}) {
    for (const std::string_view alloc : {"receiver", "split", "sender"}) {
      CAPTURE(reverse);
      CAPTURE(alloc);
      const double a = sender_share(alloc);
      Fixture flow_fx(lit_case(alloc, "flow", reverse, kTmax, kRating));
      Fixture send_fx(lit_case(alloc, "sending", reverse, kTmax, kRating));
      const auto flow = loaded(solve(flow_fx), reverse);
      const auto send = loaded(solve(send_fx), reverse);
      CHECK(flow.f == doctest::Approx(kRating).epsilon(1e-9));
      CHECK(send.f + (a * send.loss) == doctest::Approx(kRating).epsilon(1e-9));
      CHECK(send.obj >= flow.obj - 1e-7);
    }
  }
}

TEST_CASE(  // NOLINT
    "line_thermal_limit literature: DLR rating under full mode")
{
  // `full` with a DLR rating: f + ℓ = T_dlr in both directions under
  // every allocation (the CEN PCP report: piecewise, B→A, split).
  constexpr double kRating = 80.0;
  for (const bool reverse : {false, true}) {
    for (const std::string_view alloc : {"receiver", "split", "sender"}) {
      CAPTURE(reverse);
      CAPTURE(alloc);
      Fixture send_fx(lit_case(alloc, "sending", reverse, kTmax, kRating));
      Fixture full_fx(lit_case(alloc, "full", reverse, kTmax, kRating));
      const auto send = loaded(solve(send_fx), reverse);
      const auto full = loaded(solve(full_fx), reverse);
      CHECK(full.f + full.loss == doctest::Approx(kRating).epsilon(1e-9));
      CHECK(full.f <= send.f + 1e-7);
      CHECK(full.obj >= send.obj - 1e-7);
      CHECK(
          row_dual(full_fx.lp(), reverse ? "line_thermaln_" : "line_thermalp_")
          < -1.0);

      // The reported configuration: 3 segments and the mode set
      // globally (`model_options.line_thermal_limit_mode = full`).
      auto cen = lit_case(alloc, "", reverse, kTmax, kRating);
      cen.segments = 3;
      cen.global_limit = "full";
      Fixture cen_fx(cen);
      const auto rc = loaded(solve(cen_fx), reverse);
      CHECK(rc.f + rc.loss == doctest::Approx(kRating).epsilon(1e-9));
      CHECK(rc.f + (0.5 * rc.loss) < kRating - (0.25 * rc.loss));
    }
  }
}

}  // namespace test_line_thermal_limit_ns
