# Input cases for the converters

This directory is a library of small cases in the input formats that
the Python tools convert to GTOpt: PLP `.dat` files for `plp2gtopt`,
Excel workbooks for `igtopt`, and LP files for `gtopt_check_lp`.  They
are used by the test suites under `scripts/*/tests/`.  The GTOpt cases
themselves (JSON, with reference results) are in
[`../../cases/`](../../cases/README.md).

## Running the tests that use them

```bash
cd scripts
pip install -e ".[dev]"
python -m pytest plp2gtopt/tests -q        # PLP cases
python -m pytest igtopt/tests -q           # Excel workbooks
python -m pytest gtopt_check_lp/tests -q   # LP files
```

The tests that also solve the converted case need the `gtopt` binary:
set `GTOPT_BIN` (or put `gtopt` on the `PATH`, see
[`tools/get_gtopt_binary.py`](../../tools/get_gtopt_binary.py)); without
it they are skipped.  `python -m pytest -m integration -q` runs only
those.

## Converting and solving a case by hand

```bash
plp2gtopt -i scripts/cases/plp_min_1bus -o results/plp_min_1bus
cd results/plp_min_1bus
gtopt plp_min_1bus.json --output-directory out
```

`plp2gtopt` writes the GTOpt JSON (named after the output directory),
the Parquet input files and a conversion report.  Its options
(hydrologies, stages, discount rate, …) are described in
[`docs/scripts-guide.md`](../../docs/scripts-guide.md).  For a workbook:

```bash
igtopt scripts/cases/igtopt_c0/system_c0.xlsx -j results/system_c0.json
```

## PLP cases

Each `plp_min_*` case is the smallest PLP data set that exercises one
feature of the converter; their `.dat` files follow the formats read by
PLP.  All are written for GTOpt.

| Case | What it covers | Tests |
|------|----------------|-------|
| `plp_min_1bus` | One bus, thermal units and a demand: the minimal complete PLP case | `plp2gtopt/tests/test_integration.py` and the writer, Excel and round-trip tests |
| `plp_min_2bus` | Two buses joined by a line | `test_integration.py`, `test_integration_solver.py` |
| `plp_min_battery` | One bus with a battery declared in `plpcenbat.dat` | `test_integration.py` |
| `plp_min_battery_entry` | One bus, three stages; a `plpcenbat.dat` battery whose `plpmance.dat` rows (central and `_LOAD` phantom) keep it out of service in stage 1 | `plp2gtopt/tests/test_battery_entry_mance.py` |
| `plp_min_ess_maness` | One bus, three stages; a `plpess.dat` ESS whose `plpmaness.dat` rows switch DC off in stage 1 and derate Emax in block 5, plus a `plpmance.dat` row of a thermal unit (kept in `Generator/pmax`) | `plp2gtopt/tests/test_battery_maness.py` |
| `plp_min_bess` | One bus with a BESS (`plpcenbat.dat` path, converted to a single GTOpt battery) | `test_integration.py`, `test_gtopt_writer.py` |
| `plp_min_ess` | One bus with an ESS declared in `plpess.dat` | `test_integration.py` |
| `plp_min_hydro` | A run-of-river hydro plant with two hydrologies (two scenarios) | `test_integration_hydro.py` |
| `plp_min_hydro_ms` | The run-of-river case over several stages | `test_integration_hydro.py` |
| `plp_min_reservoir` | A reservoir with its turbine and spillway | `test_integration_hydro.py`, `test_drop_spillway_waterway.py` |
| `plp_min_mance` | One bus with generator maintenance (`plpmance.dat`) | `test_integration_hydro.py` |
| `plp_min_manli` | Two buses with line maintenance (`plpmanli.dat`) | `test_integration_hydro.py` |
| `plp_min_cenre` | Only `plpcenre.dat` and `plpcenfi.dat`: head-dependent efficiency and filtration of reservoirs | `test_cenre_parser.py`, `test_cenfi_parser.py` |
| `plp_min_filemb` | Only `plpfilemb.dat`: reservoir filtration curves | `test_filemb_parser.py` |

Larger PLP examples:

| Case | What it is |
|------|------------|
| [`plp_hydro_4b`](plp_hydro_4b/README.md) | A four-bus hydrothermal system with a reservoir, a series turbine and a run-of-river plant, three monthly stages and three hydrologies. Converted and solved by `test_integration_solver.py`. |
| [`plp_bat_4b_24`](plp_bat_4b_24/README.md) | The PLP version of [`cases/bat_4b_24`](../../cases/bat_4b_24/), a four-bus system with a battery (ESS) and solar over 24 hours, with PLP's own results in `plp_output/` to compare against GTOpt (`gtopt_compare/tests`). |
| `plp_dat_ex` | Example `.dat` files of a larger system (247 plants), used to test each PLP parser on realistic data (`plp2gtopt/tests/conftest.py`). Not a complete case. |

## Excel and JSON cases for `igtopt`

| Case | What it is |
|------|------------|
| `igtopt_c0` | [`cases/c0`](../../cases/c0/) as an `igtopt` workbook, also used for the JSON → Excel → JSON round trip |
| `igtopt_bat4b24` | [`cases/bat_4b_24`](../../cases/bat_4b_24/) as a workbook |
| `igtopt_ieee57b` | [`cases/ieee_57b`](../../cases/ieee_57b/) as a workbook |
| `json_c0` | `c0` as a single JSON file, the reference for the round trip |

Tests: `igtopt/tests/test_igtopt.py` and `test_golden_round_trip.py`.

## LP files

`lp_infeasible` holds small LP files (`feasible_small.lp`,
`bad_bounds.lp`, `my_small_bad.lp`), also compressed with gzip, for the
infeasibility diagnostics of `gtopt_check_lp`
(`gtopt_check_lp/tests`).
