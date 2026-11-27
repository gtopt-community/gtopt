// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_irrigation_rule_ladder.cpp
 * @brief     L0 of the irrigation test ladder: the agreement zone rules
 *
 * The RightBoundRule segments that plp2gtopt/gtopt_expand emit for the
 * CEN plp data (plplajam.dat / plpmaulen.dat), evaluated at the zone
 * boundaries with hand-computed values.  Each case cites the PLP
 * routine it encodes (CEN65 sources):
 *
 *  - Laja cushions 1200 / 170 / 530 / 3682 hm3, selected half-open:
 *    zone i iff VolUtil - start_i < width_i (genpdlajam.f:578-595);
 *    rights = base + sum factor_i * VolIncrem_i, plus the mixed
 *    transfer DerRiego += DerMixtoBase * (1 - FMixto) at INICIOTEMP
 *    (genpdlajam.f:624-649).
 *  - Maule ordinary reserve: normal regime iff VolUtil >= 129 + 452 =
 *    581 hm3 (genpdmaule.f:760-767), else IVMDOxF = PorcQNIDx *
 *    (VolUtil - 129) (genpdmaule.f:978-986).
 */

#include <doctest/doctest.h>
#include <gtopt/right_bound_rule.hpp>

using namespace gtopt;

namespace
{

/// laja_vol_der_riego as emitted for plplajam.dat: base 570, factors
/// 0 / 0.40 / 0.40 / 0.25, +30 mixed transfer above the first cushion.
RightBoundRule laja_riego_rule()
{
  return {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 570.0,
              },
              {
                  .volume = 1200.0,
                  .slope = 0.4,
                  .constant = 120.0,
              },
              {
                  .volume = 1370.0,
                  .slope = 0.4,
                  .constant = 120.0,
              },
              {
                  .volume = 1900.0,
                  .slope = 0.25,
                  .constant = 405.0,
              },
          },
      .cap = 5000.0,
  };
}

/// laja_vol_der_electrico: base 0, factors 0.05 / 0.05 / 0.40 / 0.65,
/// DerElectMax 1200.
RightBoundRule laja_elec_rule()
{
  return {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.05,
                  .constant = 0.0,
              },
              {
                  .volume = 1200.0,
                  .slope = 0.05,
                  .constant = 0.0,
              },
              {
                  .volume = 1370.0,
                  .slope = 0.4,
                  .constant = -479.5,
              },
              {
                  .volume = 1900.0,
                  .slope = 0.65,
                  .constant = -954.5,
              },
          },
      .cap = 1200.0,
  };
}

/// laja_vol_der_mixto: DerMixtoBase 30 * FMixto, FMixto = 1 / 0 / 0 / 0.
RightBoundRule laja_mixto_rule()
{
  return {
      .reservoir = Uid {1},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 30.0,
              },
              {
                  .volume = 1200.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
              {
                  .volume = 1370.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
              {
                  .volume = 1900.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
          },
      .cap = 30.0,
  };
}

/// maule_vol_reserva_ord_{elec,riego}: PorcQNIDx * (V - 129) inside the
/// ordinary zone, 0 in the normal regime (V >= 581).
RightBoundRule maule_reserve_rule(double pct)
{
  return {
      .reservoir = Uid {2},
      .segments =
          {
              {
                  .volume = 0.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
              {
                  .volume = 129.0,
                  .slope = pct,
                  .constant = -129.0 * pct,
              },
              {
                  .volume = 581.0,
                  .slope = 0.0,
                  .constant = 0.0,
              },
          },
  };
}

}  // namespace

TEST_CASE("L0 Laja riego rule - zones and half-open boundaries")  // NOLINT
{
  const auto rule = laja_riego_rule();
  // Zone 1 (V < 1200): base only; the 30 hm3 stay in the mixed account
  // (FMixto = 1).
  CHECK(evaluate_bound_rule(rule, 0.0) == doctest::Approx(570.0));
  CHECK(evaluate_bound_rule(rule, 1199.999) == doctest::Approx(570.0));
  // 1200 belongs to zone 2 (half-open): 570 + 0.40*0 + 30 transfer.
  CHECK(evaluate_bound_rule(rule, 1200.0) == doctest::Approx(600.0));
  CHECK(evaluate_bound_rule(rule, 1200.001) == doctest::Approx(600.0004));
  // Zone 2 end / zone 3 start: 570 + 0.40*170 + 30 = 668.
  CHECK(evaluate_bound_rule(rule, 1369.999) == doctest::Approx(667.9996));
  CHECK(evaluate_bound_rule(rule, 1370.0) == doctest::Approx(668.0));
  // Zone 3 end / zone 4 start: 668 + 0.40*530 = 880.
  CHECK(evaluate_bound_rule(rule, 1899.999) == doctest::Approx(879.9996));
  CHECK(evaluate_bound_rule(rule, 1900.0) == doctest::Approx(880.0));
  // Zone 4: 880 + 0.25*(V - 1900).
  CHECK(evaluate_bound_rule(rule, 3000.0) == doctest::Approx(1155.0));
  CHECK(evaluate_bound_rule(rule, 5582.0) == doctest::Approx(1800.5));
}

TEST_CASE("L0 Laja electric rule - zones and DerElectMax cap")  // NOLINT
{
  const auto rule = laja_elec_rule();
  CHECK(evaluate_bound_rule(rule, 1000.0) == doctest::Approx(50.0));
  CHECK(evaluate_bound_rule(rule, 1199.999) == doctest::Approx(59.99995));
  CHECK(evaluate_bound_rule(rule, 1200.0) == doctest::Approx(60.0));
  // 60 + 0.05*170 = 68.5 ; 68.5 + 0.40*530 = 280.5
  CHECK(evaluate_bound_rule(rule, 1370.0) == doctest::Approx(68.5));
  CHECK(evaluate_bound_rule(rule, 1900.0) == doctest::Approx(280.5));
  // 280.5 + 0.65*1100 = 995.5
  CHECK(evaluate_bound_rule(rule, 3000.0) == doctest::Approx(995.5));
  // 280.5 + 0.65*3682 = 2673.8 -> MIN(DerElect, DerElectMax = 1200)
  CHECK(evaluate_bound_rule(rule, 5582.0) == doctest::Approx(1200.0));
}

TEST_CASE("L0 Laja mixed rule - transfer to riego above 1200")  // NOLINT
{
  const auto mixto = laja_mixto_rule();
  const auto riego = laja_riego_rule();
  CHECK(evaluate_bound_rule(mixto, 1199.999) == doctest::Approx(30.0));
  CHECK(evaluate_bound_rule(mixto, 1200.0) == doctest::Approx(0.0));
  CHECK(evaluate_bound_rule(mixto, 3000.0) == doctest::Approx(0.0));
  // The 30 hm3 move, never vanish: riego + mixto jumps only by the
  // zone-2 increment at the boundary (570 + 30 == 600 + 0).
  for (const double v : {1199.999, 1200.0}) {
    CAPTURE(v);
    const auto total =
        evaluate_bound_rule(riego, v) + evaluate_bound_rule(mixto, v);
    CHECK(total == doctest::Approx(600.0).epsilon(1e-6));
  }
}

TEST_CASE("L0 Maule ordinary reserve - 581 gate is half-open")  // NOLINT
{
  const auto riego = maule_reserve_rule(0.8);
  const auto elec = maule_reserve_rule(0.2);
  // Extraordinary reserve: nothing (PLP would compute a NEGATIVE
  // 0.8*(V-129) here; gtopt's flat segment keeps it at 0).
  CHECK(evaluate_bound_rule(riego, 100.0) == doctest::Approx(0.0));
  CHECK(evaluate_bound_rule(riego, 129.0) == doctest::Approx(0.0));
  // Ordinary zone: 80 / 20 % of (V - 129).
  CHECK(evaluate_bound_rule(riego, 400.0) == doctest::Approx(216.8));
  CHECK(evaluate_bound_rule(elec, 400.0) == doctest::Approx(54.2));
  CHECK(evaluate_bound_rule(riego, 580.999) == doctest::Approx(361.5992));
  CHECK(evaluate_bound_rule(elec, 580.999) == doctest::Approx(90.3998));
  // VolUtil >= 581 -> RegimenNormal: reserves annulled.
  CHECK(evaluate_bound_rule(riego, 581.0) == doctest::Approx(0.0));
  CHECK(evaluate_bound_rule(elec, 581.0) == doctest::Approx(0.0));
  CHECK(evaluate_bound_rule(riego, 581.001) == doctest::Approx(0.0));
}

TEST_CASE("L0 empty rule evaluates to its cap or zero")  // NOLINT
{
  const RightBoundRule no_cap {
      .reservoir = Uid {1},
  };
  CHECK(evaluate_bound_rule(no_cap, 1000.0) == doctest::Approx(0.0));
  const RightBoundRule with_cap {
      .reservoir = Uid {1},
      .cap = 42.0,
  };
  CHECK(evaluate_bound_rule(with_cap, 1000.0) == doctest::Approx(42.0));
}
