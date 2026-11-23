// SPDX-License-Identifier: BSD-3-Clause
//
// Battery self-discharge default (model_options.battery_annual_standing_loss,
// 0.48 p.u./year = 4 %/month) and the battery power-envelope valid
// inequality (model_options.battery_power_envelope,
// p_c/Pc_max + p_d/Pd_max <= 1).
//
// The zig-zag / timing tests never rely on which degenerate vertex a
// solver returns: the battery schedule is FIXED through per-block
// pmin/pmax bounds and the objective of each fixed schedule is compared
// against a hand-computed value.
#include <algorithm>
#include <cmath>
#include <cstddef>
#include <optional>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/battery.hpp>
#include <gtopt/battery_lp.hpp>
#include <gtopt/demand.hpp>
#include <gtopt/generator.hpp>
#include <gtopt/junction.hpp>
#include <gtopt/planning_lp.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/reservoir.hpp>
#include <gtopt/reservoir_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>

using namespace gtopt;

namespace
{

/// Hourly drain of the default (0.48 p.u./year / 8760 h).
constexpr double k_h = 0.48 / 8760.0;

using Vec = std::vector<double>;
using Vec2 = std::vector<std::vector<double>>;

Simulation make_sim(const Vec& durations)
{
  Simulation sim;
  for (std::size_t i = 0; i < durations.size(); ++i) {
    sim.block_array.push_back(Block {
        .uid = Uid {static_cast<int>(i) + 1},
        .duration = durations[i],
    });
  }
  sim.stage_array = {
      {
          .uid = Uid {1},
          .first_block = 0,
          .count_block = durations.size(),
      },
  };
  sim.scenario_array = {
      {
          .uid = Uid {0},
      },
  };
  return sim;
}

struct RunOpts
{
  std::optional<double> battery_annual_standing_loss {};
  std::optional<bool> battery_power_envelope {};
};

struct BatRun
{
  double obj {0.0};
  std::size_t numrows {0};
  Vec c {};  ///< charge MW per block
  Vec d {};  ///< discharge MW per block
  Vec soc {};  ///< end-of-block SoC [MWh]

  [[nodiscard]] double sum_c() const
  {
    double s = 0.0;
    for (const double v : c) {
      s += v;
    }
    return s;
  }
  [[nodiscard]] double sum_d() const
  {
    double s = 0.0;
    for (const double v : d) {
      s += v;
    }
    return s;
  }
};

BatRun solve(const Vec& durations,
             const Array<Generator>& gens,
             const Array<Demand>& dems,
             const Battery& bat,
             const RunOpts& ro = {})
{
  System system = {
      .name = "BatSelfDischarge",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array = dems,
      .generator_array = gens,
      .battery_array = {bat},
  };
  // Unified battery → synthetic discharge Generator, charge Demand and
  // Converter (PlanningLP does this; a bare SystemLP does not).
  system.expand_batteries();

  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1.0e4;
  opts.model_options.use_single_bus = true;
  if (ro.battery_annual_standing_loss) {
    opts.model_options.battery_annual_standing_loss =
        ro.battery_annual_standing_loss;
  }
  if (ro.battery_power_envelope) {
    opts.model_options.battery_power_envelope = ro.battery_power_envelope;
  }
  const PlanningOptionsLP options {opts};
  // SimulationLP keeps a reference: the Simulation must outlive it.
  const Simulation sim = make_sim(durations);
  SimulationLP sim_lp(sim, options);
  SystemLP sys_lp(system, sim_lp);

  auto& li = sys_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  BatRun out;
  out.obj = li.get_obj_value();
  out.numrows = li.get_numrows();
  const auto& bat_lp = sys_lp.elements<BatteryLP>().front();
  const auto& sc0 = sys_lp.scene().scenarios().front();
  const auto& stg0 = sys_lp.phase().stages().front();
  const auto& sol = li.get_col_sol();
  const auto& finp = bat_lp.finp_cols_at(sc0, stg0);
  const auto& fout = bat_lp.fout_cols_at(sc0, stg0);
  const auto& ecols = bat_lp.energy_cols_at(sc0, stg0);
  for (const auto& block : stg0.blocks()) {
    const auto buid = block.uid();
    out.c.push_back(sol[finp.at(buid)]);
    out.d.push_back(sol[fout.at(buid)]);
    out.soc.push_back(bat_lp.to_physical(sol[ecols.at(buid)]));
  }
  return out;
}

/// Idle battery: charging/discharging is priced out so c = d = 0 and
/// the SoC only decays.
Battery idle_battery(std::optional<double> annual_standing_loss)
{
  Battery bat {
      .uid = Uid {1},
      .name = "bat",
      .bus = Uid {1},
      .input_efficiency = 1.0,
      .output_efficiency = 1.0,
      .emin = 0.0,
      .emax = 100.0,
      .eini = 100.0,
      .pmax_charge = 10.0,
      .pmax_discharge = 10.0,
      .discharge_cost = 1.0e3,
      .charge_cost = 1.0e3,
      .capacity = 100.0,
      .use_state_variable = true,
      .daily_cycle = false,
  };
  if (annual_standing_loss) {
    bat.annual_standing_loss = *annual_standing_loss;
  }
  return bat;
}

/// Built on call: a namespace-scope Array would be a throwing static
/// initializer.
[[nodiscard]] auto k_flat_gen() -> Array<Generator>
{
  return {
      {
          .uid = Uid {1},
          .name = "g1",
          .bus = Uid {1},
          .gcost = 100.0,
          .capacity = 1000.0,
      },
  };
}

/// Built on call: a namespace-scope Array would be a throwing static
/// initializer.
[[nodiscard]] auto k_flat_dem() -> Array<Demand>
{
  return {
      {
          .uid = Uid {1},
          .name = "d1",
          .bus = Uid {1},
          .lmax = 50.0,
      },
  };
}

}  // namespace

TEST_CASE("Battery self-discharge default - 4 %/month SoC decay")  // NOLINT
{
  SUBCASE("absent annual_standing_loss: one 730 h block loses exactly 4 %")
  {
    const auto r = solve({730.0}, k_flat_gen(), k_flat_dem(), idle_battery({}));
    REQUIRE(r.soc.size() == 1);
    CHECK(r.c[0] == doctest::Approx(0.0));
    CHECK(r.d[0] == doctest::Approx(0.0));
    // 100 · (1 − 0.48/8760 · 730) = 100 · (1 − 0.04)
    CHECK(r.soc[0] == doctest::Approx(96.0).epsilon(1e-9));
  }

  SUBCASE("absent annual_standing_loss: 730 hourly blocks compound the drain")
  {
    const auto r =
        solve(Vec(730, 1.0), k_flat_gen(), k_flat_dem(), idle_battery({}));
    REQUIRE(r.soc.size() == 730);
    const double expected = 100.0 * std::pow(1.0 - k_h, 730.0);  // 96.077
    CHECK(r.soc.back() == doctest::Approx(expected).epsilon(1e-9));
    CHECK(r.soc.front() == doctest::Approx(100.0 * (1.0 - k_h)));
  }

  SUBCASE("explicit annual_standing_loss = 0 is respected (no loss)")
  {
    const auto r =
        solve({730.0}, k_flat_gen(), k_flat_dem(), idle_battery(0.0));
    CHECK(r.soc[0] == doctest::Approx(100.0).epsilon(1e-9));
  }

  SUBCASE("planning-level override model_options.battery_annual_standing_loss")
  {
    // 0.12 p.u./year → 1 % over 730 h.
    const auto r = solve({730.0},
                         k_flat_gen(),
                         k_flat_dem(),
                         idle_battery({}),
                         {
                             .battery_annual_standing_loss = 0.12,
                         });
    CHECK(r.soc[0] == doctest::Approx(99.0).epsilon(1e-9));

    // Legacy behaviour restored with 0.
    const auto r0 = solve({730.0},
                          k_flat_gen(),
                          k_flat_dem(),
                          idle_battery({}),
                          {
                              .battery_annual_standing_loss = 0.0,
                          });
    CHECK(r0.soc[0] == doctest::Approx(100.0).epsilon(1e-9));
  }

  SUBCASE("explicit per-battery value wins over the planning override")
  {
    // 0.24 p.u./year → 2 % over 730 h, override (0) ignored.
    const auto r = solve({730.0},
                         k_flat_gen(),
                         k_flat_dem(),
                         idle_battery(0.24),
                         {
                             .battery_annual_standing_loss = 0.0,
                         });
    CHECK(r.soc[0] == doctest::Approx(98.0).epsilon(1e-9));
  }
}

TEST_CASE("Battery self-discharge default - Reservoir unaffected")  // NOLINT
{
  // A reservoir on an isolated junction cannot release water, so its
  // volume only changes through annual_standing_loss.  With
  // annual_standing_loss unset it must stay at eini (the battery default must
  // not leak into it).
  const System system = {
      .name = "RsvNoDefaultLoss",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array = k_flat_dem(),
      .generator_array = k_flat_gen(),
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j1",
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv",
                  .junction = Uid {1},
                  .capacity = 1000.0,
                  .emin = 0.0,
                  .emax = 1000.0,
                  .eini = 100.0,
                  .use_state_variable = true,
                  .daily_cycle = false,
              },
          },
  };
  PlanningOptions opts;
  opts.model_options.demand_fail_cost = 1.0e4;
  const PlanningOptionsLP options {opts};
  const Simulation sim = make_sim({730.0});
  SimulationLP sim_lp(sim, options);
  SystemLP sys_lp(system, sim_lp);
  auto& li = sys_lp.linear_interface();
  const auto result = li.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& rsv = sys_lp.elements<ReservoirLP>().front();
  const auto& sc0 = sys_lp.scene().scenarios().front();
  const auto& stg0 = sys_lp.phase().stages().front();
  const auto& ecols = rsv.energy_cols_at(sc0, stg0);
  REQUIRE(ecols.size() == 1);
  const auto& sol = li.get_col_sol();
  for ([[maybe_unused]] const auto& [buid, col] : ecols) {
    CHECK(rsv.to_physical(sol[col]) == doctest::Approx(100.0).epsilon(1e-9));
  }
}

TEST_CASE("PlanningLP logs and counts batteries taking the default")  // NOLINT
{
  Planning planning;
  planning.system.battery_array = {
      idle_battery({}),
      idle_battery(0.0),
      idle_battery({}),
  };
  CHECK(PlanningLP::log_battery_annual_standing_loss_default(planning) == 2);
  planning.system.battery_array.clear();
  CHECK(PlanningLP::log_battery_annual_standing_loss_default(planning) == 0);
}

// ─── Zig-zag degeneracy ─────────────────────────────────────────────────────
//
// 24 hourly blocks, flat price 100 $/MWh (one generator), demand 50 MW,
// lossless-efficiency battery (η_in = η_out = 1), eini = 0, 20 MW.
// With annual_standing_loss = 0 any charge/discharge cycling has zero cost, so
// the LP has a face of alternate optima.  The default self-discharge makes
// every stored MWh decay, so cycling becomes strictly costly.
namespace
{

Battery zigzag_battery(std::optional<double> annual_standing_loss,
                       OptTBRealFieldSched pmin_c = {},
                       OptTBRealFieldSched pmax_c = RealFieldSched2 {20.0},
                       OptTBRealFieldSched pmin_d = {},
                       OptTBRealFieldSched pmax_d = RealFieldSched2 {20.0})
{
  Battery bat {
      .uid = Uid {1},
      .name = "bat",
      .bus = Uid {1},
      .input_efficiency = 1.0,
      .output_efficiency = 1.0,
      .emin = 0.0,
      .emax = 100.0,
      .eini = 0.0,
      .pmax_charge = std::move(pmax_c),
      .pmax_discharge = std::move(pmax_d),
      .pmin_charge = std::move(pmin_c),
      .pmin_discharge = std::move(pmin_d),
      .capacity = 100.0,
      .use_state_variable = true,
      .daily_cycle = false,
  };
  if (annual_standing_loss) {
    bat.annual_standing_loss = *annual_standing_loss;
  }
  return bat;
}

/// Alternating pattern over 24 blocks: `on` in odd blocks (1, 3, ...)
/// when `odd`, else in even blocks.
Vec2 alternating(double on, bool odd)
{
  Vec row(24, 0.0);
  for (std::size_t i = 0; i < 24; ++i) {
    if ((i % 2 == 0) == odd) {
      row[i] = on;
    }
  }
  return {row};
}

}  // namespace

TEST_CASE("Battery zig-zag degeneracy removed by self-discharge")  // NOLINT
{
  const Vec hours(24, 1.0);
  constexpr double base = 24.0 * 50.0 * 100.0;  // 120 000 $

  SUBCASE("free optimum: no cycling with the default")
  {
    const auto lossless =
        solve(hours, k_flat_gen(), k_flat_dem(), zigzag_battery(0.0));
    CHECK(lossless.obj == doctest::Approx(base).epsilon(1e-9));

    const auto r = solve(hours, k_flat_gen(), k_flat_dem(), zigzag_battery({}));
    CHECK(r.obj == doctest::Approx(base).epsilon(1e-9));
    // With μ > 0 any stored MWh decays → the unique optimum keeps the
    // SoC at 0 in every block.  With η = 1 a same-block c = d ≤ P/2
    // (inside the power envelope) changes nothing and stays degenerate,
    // so only the per-block NET flow is unique (CLP returns c = d = 70
    // MWh in total, HiGHS / CPLEX return 0).
    for (std::size_t i = 0; i < r.c.size(); ++i) {
      CAPTURE(i);
      CHECK(r.soc[i] == doctest::Approx(0.0).epsilon(1e-9));
      CHECK(r.c[i] - r.d[i] == doctest::Approx(0.0).epsilon(1e-9));
    }
  }

  SUBCASE("fixed zig-zag (charge 20 MW odd blocks, discharge even blocks)")
  {
    // Charge pinned to 20 MW in blocks 1, 3, ..., 23; discharge free
    // (≤ 20 MW) only in blocks 2, 4, ..., 24.
    const auto chg = alternating(20.0, /*odd=*/true);
    const auto dis = alternating(20.0, /*odd=*/false);

    const auto zz0 = solve(hours,
                           k_flat_gen(),
                           k_flat_dem(),
                           zigzag_battery(0.0, chg, chg, {}, dis));
    // Lossless: the zig-zag is cost-neutral → ties the optimum.
    CHECK(zz0.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(zz0.sum_c() == doctest::Approx(240.0));
    CHECK(zz0.sum_d() == doctest::Approx(240.0));

    const auto zz = solve(hours,
                          k_flat_gen(),
                          k_flat_dem(),
                          zigzag_battery({}, chg, chg, {}, dis));
    // Default: each 20 MWh cycle loses 20·h before it is discharged
    // (one hour of decay), replaced at 100 $/MWh.
    // Δ = 12 cycles × 20 MWh × h × 100 $/MWh = 24 000·h = 1.3150685 $.
    const double expected_delta = 12.0 * 20.0 * k_h * 100.0;
    CHECK(zz.obj - base == doctest::Approx(expected_delta).epsilon(1e-6));
    CHECK(zz.sum_d()
          == doctest::Approx(12.0 * 20.0 * (1.0 - k_h)).epsilon(1e-9));
    MESSAGE("zig-zag self-loss cost with the default: " << zz.obj - base
                                                        << " $");
  }

  SUBCASE("fixed simultaneous charge+discharge at η = 1 is NOT removed")
  {
    // Charge pinned to 10 MW every block (hard Demand.lmin, rating 20) and SoC
    // pinned to 0 (emax = 0) ⇒ discharge = 10 MW in the same block.  The
    // SoC never moves, so self-discharge (which acts on the SoC) cannot
    // price it: documented limitation.  The envelope (10/20 + 10/20 = 1)
    // admits it too — only binaries forbid it.  `pmin_discharge` is a
    // commitment-gated floor (relaxed u), not a hard one, hence the SoC
    // pin.
    const Vec2 ten {Vec(24, 10.0)};
    auto make = [&](std::optional<double> loss)
    {
      auto b = zigzag_battery(loss, ten, 20.0, {}, 20.0);
      b.emax = 0.0;
      return b;
    };
    const auto s0 = solve(hours, k_flat_gen(), k_flat_dem(), make(0.0));
    const auto s = solve(hours, k_flat_gen(), k_flat_dem(), make({}));
    CHECK(s0.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(s.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(s.sum_c() == doctest::Approx(240.0));
    CHECK(s.sum_d() == doctest::Approx(240.0));
  }
}

TEST_CASE(
    "Battery charge-timing degeneracy removed by self-discharge")  // NOLINT
{
  // Blocks 1, 2: cheap 10 $/MWh; block 3: peak 100 $/MWh.  Battery
  // 20 MW / 20 MWh, η = 1, eini = 0, discharge allowed only in block 3.
  const Vec hours(3, 1.0);
  const Array<Generator> gens = {
      {
          .uid = Uid {1},
          .name = "g_cheap",
          .bus = Uid {1},
          .pmax = Vec2 {{1000.0, 1000.0, 0.0}},
          .gcost = 10.0,
          .capacity = 1000.0,
      },
      {
          .uid = Uid {2},
          .name = "g_peak",
          .bus = Uid {1},
          .gcost = 100.0,
          .capacity = 1000.0,
      },
  };
  const OptTBRealFieldSched dis = Vec2 {{0.0, 0.0, 20.0}};
  const OptTBRealFieldSched early = Vec2 {{20.0, 0.0, 0.0}};
  const OptTBRealFieldSched late = Vec2 {{0.0, 20.0, 0.0}};
  const auto make =
      [&](std::optional<double> loss, const OptTBRealFieldSched& chg)
  {
    auto b = zigzag_battery(loss, chg, chg, {}, dis);
    b.emax = 20.0;
    b.capacity = 20.0;
    return b;
  };
  // 2·50·10 + 20·10 + 30·100
  constexpr double base = 4200.0;

  SUBCASE("lossless: early and late charging tie")
  {
    const auto e = solve(hours, gens, k_flat_dem(), make(0.0, early));
    const auto l = solve(hours, gens, k_flat_dem(), make(0.0, late));
    CHECK(e.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(l.obj == doctest::Approx(base).epsilon(1e-9));
  }

  SUBCASE("default: charging late is strictly better")
  {
    const auto e = solve(hours, gens, k_flat_dem(), make({}, early));
    const auto l = solve(hours, gens, k_flat_dem(), make({}, late));
    // late: 20 MWh decays one hour → 20·h replaced at 100 $/MWh.
    CHECK(l.obj - base == doctest::Approx(2000.0 * k_h).epsilon(1e-6));
    // early: decays two hours → 20·(1 − (1 − h)²).
    CHECK(
        e.obj - base
        == doctest::Approx(2000.0 * ((2.0 * k_h) - (k_h * k_h))).epsilon(1e-6));
    // Δ = 2000·h·(1 − h) ≈ 0.10958 $.
    CHECK(e.obj - l.obj
          == doctest::Approx(2000.0 * k_h * (1.0 - k_h)).epsilon(1e-6));

    // Free charging (≤ 20 MW in blocks 1–2): the optimum charges late.
    auto free_bat = make({}, OptTBRealFieldSched {20.0});
    free_bat.pmin_charge.reset();
    const auto f = solve(hours, gens, k_flat_dem(), free_bat);
    CHECK(f.c[0] == doctest::Approx(0.0).epsilon(1e-9));
    CHECK(f.c[1] == doctest::Approx(20.0));
    CHECK(f.obj == doctest::Approx(l.obj).epsilon(1e-9));
  }
}

// ─── Free-energy dumping and the power envelope ─────────────────────────────
//
// A subsidised generator (gcost −5 $/MWh, ≤ 80 MW) faces 50 MW of demand:
// every extra MWh produced earns 5 $, but must be absorbed by the
// battery.  With η_in = η_out = 0.9 the LP dumps energy by charging and
// discharging in the SAME block (losses (1/0.9 − 0.9)·d burn the
// surplus).  Self-discharge does NOT remove this (it only acts on the
// tiny SoC); the power envelope p_c/200 + p_d/200 ≤ 1 cuts it to the
// envelope.  TODO: only binaries (u_c + u_d ≤ 1) remove it entirely.
namespace
{

constexpr double k_eta = 0.9;
constexpr double k_pmax = 200.0;

Battery dump_battery(std::optional<double> loss)
{
  Battery bat {
      .uid = Uid {1},
      .name = "bat",
      .bus = Uid {1},
      .input_efficiency = k_eta,
      .output_efficiency = k_eta,
      .emin = 0.0,
      .emax = 10.0,
      .eini = 0.0,
      .pmax_charge = k_pmax,
      .pmax_discharge = k_pmax,
      .capacity = 10.0,
      .use_state_variable = true,
      .daily_cycle = false,
  };
  if (loss) {
    bat.annual_standing_loss = *loss;
  }
  return bat;
}

/// Built on call: a namespace-scope Array would be a throwing static
/// initializer.
[[nodiscard]] auto k_subsidised_gen() -> Array<Generator>
{
  return {
      {
          .uid = Uid {1},
          .name = "g_neg",
          .bus = Uid {1},
          .gcost = -5.0,
          .capacity = 80.0,
      },
  };
}

}  // namespace

TEST_CASE(
    "Battery free-energy dumping vs self-discharge and envelope")  // NOLINT
{
  const Vec hours(24, 1.0);
  constexpr double a = k_eta;
  constexpr double b = 1.0 / k_eta;
  constexpr double emax = 10.0;

  SUBCASE("without the envelope: full 30 MW surplus dumped every block")
  {
    for (const std::optional<double> loss :
         {std::optional<double> {0.0}, std::optional<double> {}})
    {
      const auto r = solve(hours,
                           k_subsidised_gen(),
                           k_flat_dem(),
                           dump_battery(loss),
                           {
                               .battery_power_envelope = false,
                           });
      // Generator at 80 MW in all 24 blocks.
      CHECK(r.obj == doctest::Approx(-5.0 * 80.0 * 24.0).epsilon(1e-6));
      CHECK(r.sum_c() - r.sum_d() == doctest::Approx(720.0).epsilon(1e-6));
      // a·Σc − b·Σd = e_end + L with Σc = Σd + 720, e_end ∈ [0, emax]
      // and the self-loss L ∈ [0, 24 h · emax · μ]:
      // Σd ∈ [(648 − emax − L_max)/(b − a), 648/(b − a)] ≈ [3022, 3069.5]
      // MWh.  Where inside the range a solver lands is a degenerate
      // vertex choice (CLP: 3022.05 with the default loss).
      const double l_max = loss ? 0.0 : 24.0 * emax * k_h;
      CHECK(r.sum_d() >= (((a * 720.0) - emax - l_max) / (b - a)) - 1e-3);
      CHECK(r.sum_d() <= (a * 720.0 / (b - a)) + 1e-3);
      MESSAGE("no envelope, loss=" << std::string(loss ? "0" : "default")
                                   << ": discharge cycled " << r.sum_d()
                                   << " MWh, obj " << r.obj);
    }
  }

  SUBCASE("with the envelope (default): dumping cut to the envelope")
  {
    // Per block, with surplus s and c = d + s, c + d ≤ P (= 200):
    // stored = a·s − (b − a)·d ≥ s·(a + (b − a)/2) − (b − a)·P/2, and
    // Σ stored ≤ emax (+ tiny self-loss) ⇒
    //   Σs ≤ (24·(b − a)·P/2 + emax) / (a + (b − a)/2) = 513.83 MWh.
    const double sum_s_max =
        ((24.0 * (b - a) * k_pmax / 2.0) + emax) / (a + ((b - a) / 2.0));
    const double obj_expected = -5.0 * ((24.0 * 50.0) + sum_s_max);

    const auto r0 =
        solve(hours, k_subsidised_gen(), k_flat_dem(), dump_battery(0.0));
    CHECK(r0.sum_c() - r0.sum_d() == doctest::Approx(sum_s_max).epsilon(1e-6));
    CHECK(r0.obj == doctest::Approx(obj_expected).epsilon(1e-6));
    for (std::size_t i = 0; i < r0.c.size(); ++i) {
      CHECK(r0.c[i] + r0.d[i] <= k_pmax + 1e-6);
    }

    // Default self-discharge barely changes it (SoC ≤ 10 MWh).
    const auto r =
        solve(hours, k_subsidised_gen(), k_flat_dem(), dump_battery({}));
    CHECK(r.obj == doctest::Approx(obj_expected).epsilon(1e-4));
    CHECK(r.obj <= r0.obj + 1e-9);

    // Partial simultaneous operation is still used (honest limitation):
    // Σd = (24·P − Σs)/2 ≈ 2143 MWh of discharge while charging.
    CHECK(
        r0.sum_d()
        == doctest::Approx(((24.0 * k_pmax) - sum_s_max) / 2.0).epsilon(1e-6));
    MESSAGE("envelope: surplus absorbed "
            << r0.sum_c() - r0.sum_d() << " MWh (vs 720), discharge cycled "
            << r0.sum_d() << " MWh, obj " << r0.obj);
  }
}

TEST_CASE("Battery power envelope rows present by default")  // NOLINT
{
  const Vec hours(24, 1.0);
  const auto on = solve(hours, k_flat_gen(), k_flat_dem(), zigzag_battery({}));
  const auto off = solve(hours,
                         k_flat_gen(),
                         k_flat_dem(),
                         zigzag_battery({}),
                         {
                             .battery_power_envelope = false,
                         });
  // One `Converter/power_envelope` row per block.
  CHECK(on.numrows == off.numrows + 24);
  CHECK(on.obj == doctest::Approx(off.obj).epsilon(1e-9));

  SUBCASE("skipped when one direction is off (pmax_charge = 0)")
  {
    const auto r = solve(hours,
                         k_flat_gen(),
                         k_flat_dem(),
                         zigzag_battery({}, {}, 0.0, {}, 20.0));
    const auto r_off = solve(hours,
                             k_flat_gen(),
                             k_flat_dem(),
                             zigzag_battery({}, {}, 0.0, {}, 20.0),
                             {
                                 .battery_power_envelope = false,
                             });
    CHECK(r.numrows == r_off.numrows);
  }
}

// ─── User-observed case: zig-zag inside a 0 $/MWh window ────────────────────
//
// One bus, ONE generator with a per-block gcost schedule — 0 $/MWh for
// hours 08–18 (blocks 8..17), 270 $/MWh for 18–23 (blocks 18..22),
// 40 $/MWh at night (blocks 23, 0..7) — capacity ample, flat 100 MW
// demand, one 50 MW / 200 MWh (4 h) battery with η_in = η_out = 0.95,
// eini = 0.  Charging is allowed only in the 0-price window (blocks
// 8..17), discharging outside it (plus the pinned zig-zag hours).
//
// Reference schedule: free charging in the window (the LP fills the
// battery to 200 MWh by 18:00) and free discharge at the peak.
// Zig-zag schedule: blocks 8, 10, 12, 14 charge 50 MW (pinned), blocks
// 9, 11, 13, 15 discharge 20 MW (pinned), blocks 16–17 free top-up.
namespace
{

constexpr std::size_t k_nb = 24;
constexpr double k_ueta = 0.95;
constexpr double k_up = 50.0;
constexpr double k_ue = 200.0;

bool in_window(std::size_t i)
{
  return i >= 8 && i <= 17;
}

Vec2 price_profile(double window_price)
{
  Vec row(k_nb, 40.0);
  for (std::size_t i = 0; i < k_nb; ++i) {
    if (in_window(i)) {
      row[i] = window_price;
    } else if (i >= 18 && i <= 22) {
      row[i] = 270.0;
    }
  }
  return {row};
}

Array<Generator> user_gen(double window_price)
{
  return {
      {
          .uid = Uid {1},
          .name = "g",
          .bus = Uid {1},
          .gcost = price_profile(window_price),
          .capacity = 1000.0,
      },
  };
}

/// Built on call: a namespace-scope Array would be a throwing static
/// initializer.
[[nodiscard]] auto k_user_dem() -> Array<Demand>
{
  return {
      {
          .uid = Uid {1},
          .name = "d",
          .bus = Uid {1},
          .lmax = 100.0,
      },
  };
}

Battery user_battery(std::optional<double> loss,
                     bool zigzag,
                     bool window_discharge = false)
{
  Vec pmin_c(k_nb, 0.0);
  Vec pmax_c(k_nb, 0.0);
  Vec pmax_d(k_nb, k_up);
  Vec emax(k_nb, k_ue);
  for (std::size_t i = 0; i < k_nb; ++i) {
    if (in_window(i)) {
      pmax_c[i] = k_up;
      pmax_d[i] = window_discharge ? k_up : 0.0;
    }
  }
  if (zigzag) {
    // Charge 50 MW (hard floor) in 8, 10, 12, 14; discharge exactly
    // 20 MW in 9, 11, 13, 15 — forced by capping the SoC at the value it
    // reaches after a 20 MW discharge (`pmin_discharge` is only a
    // commitment-gated floor, so the SoC cap is the hard pin).
    const double h = loss.value_or(0.48) / 8760.0;
    double e = 0.0;
    for (std::size_t i = 8; i <= 15; ++i) {
      if (i % 2 == 0) {
        pmin_c[i] = k_up;
        e = (e * (1.0 - h)) + (k_ueta * k_up);
      } else {
        pmax_c[i] = 0.0;
        pmax_d[i] = 20.0;
        e = (e * (1.0 - h)) - (20.0 / k_ueta);
        emax[i] = e;
      }
    }
  }
  Battery bat {
      .uid = Uid {1},
      .name = "bess",
      .bus = Uid {1},
      .input_efficiency = k_ueta,
      .output_efficiency = k_ueta,
      .emin = 0.0,
      .emax = Vec2 {emax},
      .eini = 0.0,
      .pmax_charge = Vec2 {pmax_c},
      .pmax_discharge = Vec2 {pmax_d},
      .pmin_charge = Vec2 {pmin_c},
      .capacity = k_ue,
      .use_state_variable = true,
      .daily_cycle = false,
  };
  if (loss) {
    bat.annual_standing_loss = *loss;
  }
  return bat;
}

/// Extra peak cost of the default self-discharge: SoC = 200 MWh at 18:00,
/// greedy 50 MW discharge (earliest = least decay), lost delivery × 270.
double peak_decay_cost()
{
  double e = k_ue;
  double delivered = 0.0;
  for (int b = 18; b <= 22; ++b) {
    e *= 1.0 - k_h;
    const double d = std::min(k_up, e * k_ueta);
    e -= d / k_ueta;
    delivered += d;
  }
  return ((k_ueta * k_ue) - delivered) * 270.0;
}

double window_discharge(const BatRun& r)
{
  double s = 0.0;
  for (std::size_t i = 0; i < r.d.size(); ++i) {
    if (in_window(i)) {
      s += r.d[i];
    }
  }
  return s;
}

}  // namespace

TEST_CASE("User case: zig-zag in a 0 $/MWh window")  // NOLINT
{
  const Vec hours(k_nb, 1.0);
  // Lossless reference: night 9 h × 100 MW × 40 + peak (500 − 0.95·200)
  // × 270 = 36 000 + 83 700.
  constexpr double base = 36000.0 + ((500.0 - (k_ueta * k_ue)) * 270.0);
  // Zig-zag cycles 4 × 20 MWh through the battery inside the window.
  constexpr double zz_mwh = 80.0;

  SUBCASE("a) annual_standing_loss = 0, no envelope: zig-zag ties the optimum")
  {
    const RunOpts ro {
        .battery_power_envelope = false,
    };
    const auto ref = solve(hours,
                           user_gen(0.0),
                           k_user_dem(),
                           user_battery(0.0, /*zigzag=*/false),
                           ro);
    const auto zz = solve(hours,
                          user_gen(0.0),
                          k_user_dem(),
                          user_battery(0.0, /*zigzag=*/true),
                          ro);
    CHECK(ref.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(zz.obj == doctest::Approx(base).epsilon(1e-9));
    CHECK(window_discharge(zz) == doctest::Approx(zz_mwh));
    CHECK(zz.soc[17] == doctest::Approx(k_ue));
  }

  SUBCASE("b) 4 %/month default: STILL a tie at exactly 0 $/MWh")
  {
    // The extra self-loss of the zig-zag trajectory is replaced by free
    // energy before 18:00 — both schedules reach SoC = 200 MWh at 18:00
    // and are identical afterwards.  Self-discharge does NOT break this
    // degeneracy when the marginal window energy costs exactly 0.
    const RunOpts ro {
        .battery_power_envelope = false,
    };
    const auto ref = solve(hours,
                           user_gen(0.0),
                           k_user_dem(),
                           user_battery({}, /*zigzag=*/false),
                           ro);
    const auto zz = solve(hours,
                          user_gen(0.0),
                          k_user_dem(),
                          user_battery({}, /*zigzag=*/true),
                          ro);
    CHECK(ref.soc[17] == doctest::Approx(k_ue));
    CHECK(zz.soc[17] == doctest::Approx(k_ue));
    CHECK(zz.obj == doctest::Approx(ref.obj).epsilon(1e-10));
    // The default only costs the decay during the peak discharge.
    CHECK(ref.obj - base == doctest::Approx(peak_decay_cost()).epsilon(1e-6));
    MESSAGE("default self-loss cost during the peak: " << ref.obj - base
                                                       << " $");
  }

  SUBCASE("c) envelope: alternating zig-zag remains a tie")
  {
    // One direction per hour never touches c/50 + d/50 ≤ 1.
    const auto ref = solve(
        hours, user_gen(0.0), k_user_dem(), user_battery({}, /*zigzag=*/false));
    const auto zz = solve(
        hours, user_gen(0.0), k_user_dem(), user_battery({}, /*zigzag=*/true));
    CHECK(zz.obj == doctest::Approx(ref.obj).epsilon(1e-10));
    for (std::size_t i = 0; i < k_nb; ++i) {
      CHECK((ref.c[i] / k_up) + (ref.d[i] / k_up) <= 1.0 + 1e-9);
    }
  }

  SUBCASE("d1) small window VOM (1 $/MWh) breaks the tie")
  {
    // Zig-zag delivers 80 MWh that had to be charged as
    // 80/(0.95·0.95) MWh: extra 80·(1/0.9025 − 1) = 8.6427 MWh at 1 $.
    const double expected = zz_mwh * ((1.0 / (k_ueta * k_ueta)) - 1.0);
    const auto ref = solve(hours,
                           user_gen(1.0),
                           k_user_dem(),
                           user_battery(0.0, /*zigzag=*/false));
    const auto zz = solve(
        hours, user_gen(1.0), k_user_dem(), user_battery(0.0, /*zigzag=*/true));
    CHECK(zz.obj - ref.obj == doctest::Approx(expected).epsilon(1e-6));

    // With the default the zig-zag also holds energy longer → a little
    // more self-loss, also priced at 1 $/MWh.
    const auto ref_l = solve(
        hours, user_gen(1.0), k_user_dem(), user_battery({}, /*zigzag=*/false));
    const auto zz_l = solve(
        hours, user_gen(1.0), k_user_dem(), user_battery({}, /*zigzag=*/true));
    CHECK(zz_l.obj - ref_l.obj > expected - 1e-6);
    CHECK(zz_l.obj - ref_l.obj == doctest::Approx(expected).epsilon(1e-2));
    MESSAGE("window VOM 1 $/MWh: zig-zag penalty lossless "
            << zz.obj - ref.obj << " $, default " << zz_l.obj - ref_l.obj
            << " $");
  }

  SUBCASE("d2) battery throughput cost (discharge_cost) breaks the tie")
  {
    // discharge_cost = 1 $/MWh: the zig-zag discharges 80 extra MWh.
    auto ref_b = user_battery({}, /*zigzag=*/false);
    auto zz_b = user_battery({}, /*zigzag=*/true);
    ref_b.discharge_cost = 1.0;
    zz_b.discharge_cost = 1.0;
    const auto ref = solve(hours, user_gen(0.0), k_user_dem(), ref_b);
    const auto zz = solve(hours, user_gen(0.0), k_user_dem(), zz_b);
    CHECK(zz.obj - ref.obj == doctest::Approx(zz_mwh).epsilon(1e-6));
  }

  SUBCASE("solver's own vertex on the degenerate case (report only)")
  {
    // Free schedule, annual_standing_loss = 0, envelope off: which vertex does
    // the configured solver return?  Not asserted — the face is degenerate.
    const auto r =
        solve(hours,
              user_gen(0.0),
              k_user_dem(),
              user_battery(0.0, /*zigzag=*/false, /*window_discharge=*/true),
              {
                  .battery_power_envelope = false,
              });
    double window_c = 0.0;
    for (std::size_t i = 0; i < k_nb; ++i) {
      if (in_window(i)) {
        window_c += r.c[i];
      }
    }
    CHECK(r.obj == doctest::Approx(base).epsilon(1e-9));
    MESSAGE("solver vertex: window charge "
            << window_c << " MWh (minimum " << k_ue / k_ueta
            << "), window discharge " << window_discharge(r) << " MWh");
  }
}
