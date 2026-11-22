// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_irrigation_maule_ledger.cpp
 * @brief     L2 — Maule rights counters as remaining-rights ledgers
 * @date      2026-11-21
 * @copyright Copyright (c) 2024-2026 Marcelo Matus. Dual-licensed,
 *            see LICENSING.md
 *
 * PLP keeps the Maule electric/irrigation spending as UP-counters
 * (`IVMGEAF = prev + dur·q <= IVMDEAT`, genpdmaule.f:269-351).  gtopt
 * models them as REMAINING-rights depletion ledgers
 * (`remaining = cap − used`).  Both are primal-equivalent, but only the
 * ledger keeps the state's water value (the negated raw energy-balance
 * dual, cf. test_storage_dual_sign.cpp) NON-NEGATIVE when the right is
 * binding: one more hm³ of remaining right is worth money.  The
 * up-counter's value is NEGATIVE (one more hm³ already used costs
 * money) and an SDDP cut on it propagates a negative water value.
 */

#include <doctest/doctest.h>
#include <gtopt/linear_interface.hpp>
#include <gtopt/simulation_lp.hpp>
#include <gtopt/system_lp.hpp>
#include <gtopt/volume_right_lp.hpp>

using namespace gtopt;

namespace
{

// 1 bus, hydro (pf 2) + thermal 100 $/MWh, demand 50 MW for 24 h: the
// hydro wants 25 m3/s = 2.16 hm3; the right allows only 1 hm3.
constexpr Real kLedgerRemaining = 1.0;  // hm3 of right left (cap - used)
constexpr Real kLedgerUsed = 0.5;  // hm3 already used (PLP data ini value)

[[nodiscard]] Simulation make_ledger_day()
{
  return {
      .block_array =
          {
              {
                  .uid = Uid {1},
                  .duration = 24,
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
  };
}

[[nodiscard]] System make_ledger_system(VolumeRight right,
                                        std::string expression,
                                        Real hydro_gcost)
{
  return {
      .name = "MauleLedger",
      .bus_array =
          {
              {
                  .uid = Uid {1},
                  .name = "b1",
              },
          },
      .demand_array =
          {
              {
                  .uid = Uid {1},
                  .name = "d1",
                  .bus = Uid {1},
                  .lmax = 50.0,
                  .fcost = 1000.0,
                  .capacity = 50.0,
              },
          },
      .generator_array =
          {
              {
                  .uid = Uid {1},
                  .name = "hydro",
                  .bus = Uid {1},
                  .gcost = hydro_gcost,
                  .capacity = 200.0,
              },
              {
                  .uid = Uid {2},
                  .name = "thermal",
                  .bus = Uid {1},
                  .gcost = 100.0,
                  .capacity = 200.0,
              },
          },
      .junction_array =
          {
              {
                  .uid = Uid {1},
                  .name = "j_up",
              },
              {
                  .uid = Uid {2},
                  .name = "j_down",
                  .drain = true,
              },
          },
      .waterway_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ww",
                  .junction_a = Uid {1},
                  .junction_b = Uid {2},
                  .fmin = 0.0,
                  .fmax = 200.0,
              },
          },
      .reservoir_array =
          {
              {
                  .uid = Uid {1},
                  .name = "rsv",
                  .junction = Uid {1},
                  .capacity = 200.0,
                  .emin = 0.0,
                  .emax = 200.0,
                  .eini = 100.0,
              },
          },
      .turbine_array =
          {
              {
                  .uid = Uid {1},
                  .name = "tur",
                  .waterway = Uid {1},
                  .generator = Uid {1},
                  .production_factor = 2.0,
              },
          },
      .volume_right_array = {std::move(right)},
      .user_constraint_array =
          {
              {
                  .uid = Uid {1},
                  .name = "ledger_link",
                  .expression = std::move(expression),
                  .constraint_type = "raw",
              },
          },
  };
}

struct MauleLedgerResult
{
  Real turbined {0.0};
  Real value {0.0};  ///< water value = −(raw energy-balance dual)
};

[[nodiscard]] MauleLedgerResult solve_ledger(VolumeRight right,
                                             std::string expression,
                                             Real hydro_gcost = 0.0)
{
  const auto sim = make_ledger_day();
  const auto system =
      make_ledger_system(std::move(right), std::move(expression), hydro_gcost);
  const PlanningOptionsLP options;
  SimulationLP simulation_lp(sim, options);
  SystemLP system_lp(system, simulation_lp);
  auto&& lp = system_lp.linear_interface();
  const auto result = lp.resolve();
  REQUIRE(result.has_value());
  REQUIRE(result.value() == 0);

  const auto& scenarios = system_lp.scene().scenarios();
  const auto& stages = system_lp.phase().stages();
  const auto& vr_lp = system_lp.elements<VolumeRightLP>().front();
  const auto& rows = vr_lp.energy_rows_at(scenarios[0], stages[0]);
  const auto buid = stages[0].blocks().front().uid();
  const auto duals = lp.get_row_dual();

  const auto& sc = system_lp.system_context();
  const auto col = sc.find_ampl_col(
      "waterway", Uid {1}, "flow", scenarios[0].uid(), stages[0].uid(), buid);
  REQUIRE(col.has_value());
  return {
      .turbined = col ? lp.get_col_sol()[*col] : 0.0,
      .value = -duals[rows.at(buid)],
  };
}

}  // namespace

TEST_CASE(  // NOLINT
    "Maule ledger - remaining-rights ledger keeps a non-negative value")
{
  // eini = cap − used = 1 hm3; extraction = turbined flow.
  const VolumeRight ledger {
      .uid = Uid {1},
      .name = "maule_vol_gasto_elec_anual",
      .emin = 0.0,
      .emax = 10.0,
      .eini = kLedgerRemaining,
      .fmax = 1000.0,
      .use_state_variable = true,
  };
  const auto r = solve_ledger(
      ledger,
      R"(volume_right("maule_vol_gasto_elec_anual").extraction = waterway("ww").flow)");
  // Binding right: 1 hm3 / (24 h · 0.0036) = 11.574 m3/s.
  CHECK(r.turbined == doctest::Approx(kLedgerRemaining / (24.0 * 0.0036)));
  CHECK(r.value >= 0.0);
  CHECK(r.value > 1e-9);
}

TEST_CASE(  // NOLINT
    "Maule ledger - PLP up-counter form carries a negative value")
{
  // The same right as an up-counter: eini = used, saving = turbined,
  // energy <= cap.  Primal-equivalent, but the binding cap prices the
  // counter state NEGATIVE — the form gtopt must not emit.
  const VolumeRight counter {
      .uid = Uid {1},
      .name = "maule_used_counter",
      .emin = 0.0,
      .emax = kLedgerUsed + kLedgerRemaining,
      .eini = kLedgerUsed,
      .fmax = 0.0,
      .saving_rate = 1000.0,
      .use_state_variable = true,
  };
  const auto r = solve_ledger(
      counter,
      R"(volume_right("maule_used_counter").saving = waterway("ww").flow)");
  CHECK(r.turbined == doctest::Approx(kLedgerRemaining / (24.0 * 0.0036)));
  CHECK(r.value < 0.0);
}

// ── Free disposal (VolumeRight::forfeit) ────────────────────────────
//
// plp_2y SDDP showed negative energy duals on the Maule ledgers, mostly
// at the cap (`remaining == emax`, right unused) and then everywhere
// upstream through the cuts (100 % of the cut coefficients on
// maule_vol_gasto_riego_temp were negative).  Two sources:
//   (a) a degenerate cap vertex (cap and zero-extraction bound both
//       binding: any dual in [-c, 0] is optimal);
//   (b) a cap below the incoming volume (`emax < eini`), which FORCES
//       extraction: the unique dual is then negative.
// With free disposal both vanish: dropping unused rights is free.

namespace
{

// Hydro dearer than thermal: using the right costs (200 - 100) $/MWh.
constexpr Real kUneconomicHydro = 200.0;

[[nodiscard]] VolumeRight make_capped_ledger(Real eini, Real emax, bool forfeit)
{
  return {
      .uid = Uid {1},
      .name = "ledger",
      .emin = 0.0,
      .emax = emax,
      .eini = eini,
      .fmax = 1000.0,
      .forfeit = forfeit,
      .use_state_variable = true,
  };
}

constexpr auto kLink =
    R"(volume_right("ledger").extraction = waterway("ww").flow)";

}  // namespace

TEST_CASE(  // NOLINT
    "Maule ledger - a cap below the incoming volume forces costly use")
{
  // eini 1 hm3 > emax 0.5 hm3: without disposal 0.5 hm3 MUST be
  // turbined at a 100 $/MWh loss; the unique value of one more hm3 of
  // right is -(200 - 100) * pf 2 / 0.0036 = -55 555.6 $/hm3.
  const auto r = solve_ledger(
      make_capped_ledger(1.0, 0.5, /*forfeit=*/false), kLink, kUneconomicHydro);
  CHECK(r.turbined == doctest::Approx(0.5 / (24.0 * 0.0036)));
  CHECK(r.value == doctest::Approx(-100.0 * 2.0 / 0.0036));
}

TEST_CASE(  // NOLINT
    "Maule ledger - forfeit drops the excess for free, value >= 0")
{
  const auto r = solve_ledger(
      make_capped_ledger(1.0, 0.5, /*forfeit=*/true), kLink, kUneconomicHydro);
  CHECK(r.turbined == doctest::Approx(0.0));
  CHECK(r.value >= 0.0);
  CHECK(r.value == doctest::Approx(0.0));
}

TEST_CASE(  // NOLINT
    "Maule ledger - forfeit keeps the unused-at-cap value non-negative")
{
  // eini == emax, right unused (degenerate cap vertex).
  const auto r = solve_ledger(
      make_capped_ledger(1.0, 1.0, /*forfeit=*/true), kLink, kUneconomicHydro);
  CHECK(r.turbined == doctest::Approx(0.0));
  CHECK(r.value >= 0.0);
}

TEST_CASE(  // NOLINT
    "Maule ledger - forfeit is primal-neutral on a binding right")
{
  // The binding case of the first test: disposal is never used.
  const auto with = solve_ledger(
      make_capped_ledger(kLedgerRemaining, 10.0, /*forfeit=*/true), kLink);
  CHECK(with.turbined == doctest::Approx(kLedgerRemaining / (24.0 * 0.0036)));
  CHECK(with.value > 1e-9);
}
