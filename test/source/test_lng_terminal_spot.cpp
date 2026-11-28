// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
/**
 * @file      test_lng_terminal_spot.cpp
 * @brief     LngTerminal send-out limits, contract delivery cost and
 *            optional spot cargoes (LP rows, columns and solutions)
 */
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include <doctest/doctest.h>
#include <gtopt/generator_lp.hpp>
#include <gtopt/lng_terminal.hpp>
#include <gtopt/lng_terminal_lp.hpp>
#include <gtopt/planning_options.hpp>
#include <gtopt/planning_options_lp.hpp>
#include <gtopt/simulation.hpp>
#include <gtopt/system.hpp>
#include <gtopt/system_lp.hpp>

#include "tmp_dir_helpers.hpp"

using namespace gtopt;

namespace
{

/// 1 bus; GAS (linked to the tank, VOM-only gcost) and an expensive
/// unlinked BACKUP; `nstages` stages of one block of `hours` each.
struct SpotCase
{
  Real demand {100.0};
  Real gas_cost {1.0};
  Real backup_cost {1000.0};
  Real heat_rate {1.0};
  Real eini {0.0};
  Real emax {1000.0};
  int nstages {2};
  Real hours {1.0};

  System system;
  Simulation simulation;

  void build()
  {
    system = System {
        .name = "spot",
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
                    .lmax = demand,
                },
            },
        .generator_array =
            {
                {
                    .uid = Uid {1},
                    .name = "gas",
                    .bus = Uid {1},
                    .gcost = gas_cost,
                    .capacity = 500.0,
                },
                {
                    .uid = Uid {2},
                    .name = "backup",
                    .bus = Uid {1},
                    .gcost = backup_cost,
                    .capacity = 500.0,
                },
            },
        .lng_terminal_array =
            {
                {
                    .uid = Uid {1},
                    .name = "gnl",
                    .emin = 0.0,
                    .emax = emax,
                    .eini = eini,
                    .annual_standing_loss = 0.0,
                    .use_state_variable = true,
                    .generators =
                        {
                            {
                                .generator = Uid {1},
                                .heat_rate = heat_rate,
                            },
                        },
                },
            },
    };
    simulation = Simulation {};
    for (int t = 0; t < nstages; ++t) {
      simulation.block_array.push_back(Block {
          .uid = Uid {t + 1},
          .duration = hours,
      });
      simulation.stage_array.push_back(Stage {
          .uid = Uid {t + 1},
          .first_block = static_cast<Size>(t),
          .count_block = 1,
      });
    }
    simulation.scenario_array = {
        {
            .uid = Uid {0},
        },
    };
  }

  [[nodiscard]] LngTerminal& tank()
  {
    return system.lng_terminal_array.front();
  }
};

struct Solved
{
  PlanningOptionsLP options;
  SimulationLP simulation_lp;
  SystemLP system_lp;

  explicit Solved(SpotCase& c)
      : options(make_options())
      , simulation_lp(c.simulation, options)
      , system_lp(c.system, simulation_lp)
  {
  }

  Solved(SpotCase& c, const std::filesystem::path& out)
      : options(make_options(out))
      , simulation_lp(c.simulation, options)
      , system_lp(c.system, simulation_lp)
  {
  }

  static PlanningOptionsLP make_options(const std::filesystem::path& out = {})
  {
    PlanningOptions opts;
    if (!out.empty()) {
      opts.output_directory = out.string();
      opts.output_format = DataFormat::csv;
      opts.output_compression = CompressionCodec::uncompressed;
    }
    opts.model_options.demand_fail_cost = 10'000.0;
    opts.model_options.scale_objective = 1.0;
    return PlanningOptionsLP {opts};
  }

  [[nodiscard]] auto& li() { return system_lp.linear_interface(); }
  [[nodiscard]] const auto& lng() const
  {
    return system_lp.elements<LngTerminalLP>().front();
  }
  [[nodiscard]] auto sol(ColIndex c) { return li().get_col_sol()[c]; }

  [[nodiscard]] double gen(Uid g, int stage)
  {
    const auto& gens = system_lp.elements<GeneratorLP>();
    for (const auto& glp : gens) {
      if (glp.uid() == g) {
        const auto& s = simulation_lp.stages()[static_cast<size_t>(stage)];
        const auto& cols =
            glp.lookup_generation_cols(simulation_lp.scenarios().front(), s);
        const auto it = cols.find(s.blocks().front().uid());
        return it == cols.end() ? 0.0 : sol(it->second);
      }
    }
    return -1.0;
  }

  [[nodiscard]] ColIndex spot(int stage) const
  {
    const auto& sc = simulation_lp.scenarios().front();
    const auto& s = simulation_lp.stages()[static_cast<size_t>(stage)];
    return lng().spot_cols().at({sc.uid(), s.uid()});
  }

  bool solve()
  {
    const auto r = li().resolve();
    return r.has_value() && r.value() == 0;
  }
};

}  // namespace

TEST_CASE("LngTerminal new fields default to unset")  // NOLINT
{
  const LngTerminal t;
  CHECK_FALSE(t.enforce_sendout.has_value());
  CHECK_FALSE(t.delivery_cost.has_value());
  CHECK_FALSE(t.spot_cargo_volume.has_value());
  CHECK_FALSE(t.spot_cargo_max.has_value());
  CHECK_FALSE(t.spot_cost.has_value());
  CHECK_FALSE(t.spot_integer.has_value());
  CHECK_FALSE(t.spot_annual_max.has_value());
}

TEST_CASE("LngTerminal sendout_max bounds the linked draw")  // NOLINT
{
  SpotCase c;
  c.eini = 1000.0;
  c.build();
  c.tank().sendout_max = 60.0;  // m³/h → gas ≤ 60 MW at hr 1
  Solved s(c);
  REQUIRE(s.solve());

  const auto& rows = s.lng().sendout_rows();
  REQUIRE(rows.size() == 2);  // one block per stage, two stages
  CHECK(s.gen(Uid {1}, 0) == doctest::Approx(60.0));
  CHECK(s.gen(Uid {2}, 0) == doctest::Approx(40.0));

  // Row: [−inf.., 60] on Σ hr·P; coefficient = heat rate.
  const auto& sc = s.simulation_lp.scenarios().front();
  const auto& st = s.simulation_lp.stages().front();
  const auto row = rows.at({sc.uid(), st.uid()}).at(st.blocks().front().uid());
  CHECK(s.li().get_row_upp()[row] == doctest::Approx(60.0));
  CHECK(s.li().get_row_low()[row] == doctest::Approx(0.0));
}

TEST_CASE("LngTerminal sendout_min forces the linked draw")  // NOLINT
{
  SpotCase c;
  c.eini = 1000.0;
  c.gas_cost = 500.0;
  c.backup_cost = 10.0;  // the tank units are the expensive ones
  c.build();
  c.tank().sendout_min = 20.0;
  Solved s(c);
  REQUIRE(s.solve());
  CHECK(s.gen(Uid {1}, 0) == doctest::Approx(20.0));
  CHECK(s.gen(Uid {2}, 0) == doctest::Approx(80.0));
}

TEST_CASE("LngTerminal enforce_sendout=false keeps the former LP")  // NOLINT
{
  SpotCase base;
  base.eini = 1000.0;
  base.build();
  Solved s0(base);
  const auto rows0 = s0.li().get_numrows();
  const auto cols0 = s0.li().get_numcols();

  SpotCase off = base;
  off.tank().sendout_max = 60.0;
  off.tank().enforce_sendout = false;
  Solved s1(off);
  CHECK(s1.li().get_numrows() == rows0);
  CHECK(s1.li().get_numcols() == cols0);
  CHECK(s1.lng().sendout_rows().empty());
  REQUIRE(s1.solve());
  CHECK(s1.gen(Uid {1}, 0) == doctest::Approx(100.0));

  // Spot fields with no slots / volume add nothing either.
  SpotCase none = base;
  none.tank().spot_cargo_volume = 150.0;
  none.tank().spot_cost = 10.0;
  none.tank().spot_annual_max = 3.0;
  Solved s2(none);
  CHECK(s2.li().get_numrows() == rows0);
  CHECK(s2.li().get_numcols() == cols0);
  CHECK(s2.lng().spot_cols().empty());
}

TEST_CASE("LngTerminal delivery_cost is a constant of the objective")  // NOLINT
{
  SpotCase c;
  c.build();
  c.tank().delivery = 300.0;  // m³ per stage, burnt by the gas unit
  Solved s0(c);
  REQUIRE(s0.solve());
  const auto obj0 = s0.li().get_obj_value();

  c.tank().delivery_cost = 7.0;  // $/m³
  Solved s1(c);
  REQUIRE(s1.solve());
  // Two stages × 300 m³ × 7 $/m³; same dispatch.
  CHECK(s1.li().get_obj_value() - obj0 == doctest::Approx(2 * 300.0 * 7.0));
  CHECK(s1.gen(Uid {1}, 0) == doctest::Approx(s0.gen(Uid {1}, 0)));
}

TEST_CASE("LngTerminal spot cargo LP column and tank inflow")  // NOLINT
{
  SpotCase c;
  c.build();
  c.tank().spot_cargo_volume = 150.0;
  c.tank().spot_cargo_max = 1.0;
  c.tank().spot_cost = 10.0;
  Solved s(c);

  REQUIRE(s.lng().spot_cols().size() == 2);
  const auto col = s.spot(0);
  CHECK(s.li().get_col_upp()[col] == doctest::Approx(1.0));
  CHECK_FALSE(s.li().is_integer(col));
  // Inflow coefficient in the first block's tank balance: −V.
  const auto& sc = s.simulation_lp.scenarios().front();
  const auto& st = s.simulation_lp.stages().front();
  const auto erow =
      s.lng().energy_rows_at(sc, st).at(st.blocks().front().uid());
  CHECK(s.li().get_coeff(erow, col) == doctest::Approx(-150.0));
  CHECK(s.lng().spot_annual_rows().empty());

  // Continuous: 200 MWh of gas needed in total → 200/150 cargoes.
  REQUIRE(s.solve());
  CHECK(s.sol(s.spot(0)) + s.sol(s.spot(1)) == doctest::Approx(200.0 / 150.0));
  CHECK(s.gen(Uid {2}, 0) + s.gen(Uid {2}, 1) == doctest::Approx(0.0));
}

TEST_CASE("LngTerminal integer spot cargoes need room in the tank")  // NOLINT
{
  // eini 80, emax 100, cargo 150, demand 100 MW for 1 h per stage.
  // Stage 1: 80 + 150·n − g ≤ 100 with g ≤ 100 → n = 0 (no room);
  // the tank gives 80, backup 20.  Stage 2: empty tank, one cargo:
  // 150 − 100 = 50 left.
  SpotCase c;
  c.eini = 80.0;
  c.emax = 100.0;
  c.build();
  c.tank().spot_cargo_volume = 150.0;
  c.tank().spot_cargo_max = 1.0;
  c.tank().spot_cost = 10.0;
  c.tank().spot_integer = true;
  Solved s(c);
  CHECK(s.li().is_integer(s.spot(0)));
  REQUIRE(s.solve());
  CHECK(s.sol(s.spot(0)) == doctest::Approx(0.0));
  CHECK(s.sol(s.spot(1)) == doctest::Approx(1.0));
  CHECK(s.gen(Uid {1}, 0) == doctest::Approx(80.0));
  CHECK(s.gen(Uid {2}, 0) == doctest::Approx(20.0));
  CHECK(s.gen(Uid {1}, 1) == doctest::Approx(100.0));
}

TEST_CASE("LngTerminal spot_annual_max caps cargoes per year")  // NOLINT
{
  SpotCase c;
  c.build();
  c.tank().spot_cargo_volume = 150.0;
  c.tank().spot_cargo_max = 1.0;
  c.tank().spot_cost = 10.0;
  c.tank().spot_integer = true;
  c.tank().spot_annual_max = 1.0;
  Solved s(c);
  // Both 1-h stages are in planning year 0: one row holding both cols.
  REQUIRE(s.lng().spot_annual_rows().size() == 1);
  const auto row = s.lng().spot_annual_rows().begin()->second;
  CHECK(s.li().get_coeff(row, s.spot(0)) == doctest::Approx(1.0));
  CHECK(s.li().get_coeff(row, s.spot(1)) == doctest::Approx(1.0));
  CHECK(s.li().get_row_upp()[row] == doctest::Approx(1.0));
  REQUIRE(s.solve());
  CHECK(s.sol(s.spot(0)) + s.sol(s.spot(1)) == doctest::Approx(1.0));
  // 150 m³ burnt, the other 50 MWh from backup.
  CHECK(s.gen(Uid {2}, 0) + s.gen(Uid {2}, 1) == doctest::Approx(50.0));
}

TEST_CASE("LngTerminal spot_annual_max: one row per planning year")  // NOLINT
{
  SpotCase c;
  c.nstages = 3;
  c.hours = 8760.0;  // one stage per year
  c.demand = 0.01;
  c.build();
  c.tank().spot_cargo_volume = 150.0;
  c.tank().spot_cargo_max = 2.0;
  c.tank().spot_cost = 10.0;
  c.tank().spot_annual_max = 1.0;
  Solved s(c);
  CHECK(s.lng().spot_annual_rows().size() == 3);

  CHECK(LngTerminalLP::spot_year_of(s.simulation_lp.stages()[0]) == 0);
  CHECK(LngTerminalLP::spot_year_of(s.simulation_lp.stages()[2]) == 2);
}

TEST_CASE("LngTerminal spot is a PAMPL variable")  // NOLINT
{
  SpotCase c;
  c.build();
  c.tank().spot_cargo_volume = 150.0;
  c.tank().spot_cargo_max = 1.0;
  c.tank().spot_cost = 10.0;
  // Force a cargo in stage 2 although stage 1 alone would do.
  c.system.user_constraint_array = {
      {
          .uid = Uid {1},
          .name = "min_spot",
          .expression = "lng_terminal('gnl').spot >= 0.5",
      },
  };
  Solved s(c);
  REQUIRE(s.solve());
  CHECK(s.sol(s.spot(0)) >= 0.5 - 1e-6);
  CHECK(s.sol(s.spot(1)) >= 0.5 - 1e-6);
}

TEST_CASE("LngTerminal energy_dual output is the tank shadow value")  // NOLINT
{
  // The tank holds 50 m³ for 200 MWh of demand over two 1-h blocks: one
  // more m³ replaces 1 MWh of BACKUP (1000) by GAS (1) at heat rate 1, so
  // the tank shadow value is 999 $/m³ in both blocks.  The raw LP dual of
  // the balance row is -999 (minimisation); the output publishes +999.
  SpotCase c;
  c.eini = 50.0;
  c.build();
  const auto out = gtopt::test::unique_test_dir("lng_energy_dual");
  Solved s(c, out);
  REQUIRE(s.solve());

  const auto duals = s.li().get_row_dual();
  const auto& sc = s.simulation_lp.scenarios().front();
  for (const auto& st : s.simulation_lp.stages()) {
    const auto& rows = s.lng().energy_rows_at(sc, st);
    for (const auto& b : st.blocks()) {
      CHECK(duals[rows.at(b.uid())] == doctest::Approx(-999.0));
    }
  }

  s.system_lp.write_out();
  std::vector<double> values;
  for (const auto& entry :
       std::filesystem::recursive_directory_iterator(out / "LngTerminal"))
  {
    if (!entry.is_regular_file()
        || !entry.path().filename().string().starts_with("energy_dual"))
    {
      continue;
    }
    std::ifstream in(entry.path());
    std::string line;
    std::getline(in, line);  // header
    while (std::getline(in, line)) {
      const auto pos = line.find_last_of(',');
      values.push_back(std::stod(line.substr(pos + 1)));
    }
  }
  REQUIRE(values.size() == 2);
  for (const auto v : values) {
    CHECK(v == doctest::Approx(999.0));
  }
  std::filesystem::remove_all(out);
}
