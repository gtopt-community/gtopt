# UnitCommitment.jl benchmark instances

This directory holds 27 instances of the
[UnitCommitment.jl](https://github.com/ANL-CEEESA/UnitCommitment.jl)
benchmark library (Argonne National Laboratory), with the reference
solutions computed by UnitCommitment.jl itself.  They are used to check
GTOpt's unit commitment against an independent implementation.

## Files

For each instance `<name>`:

* `UnitCommitmentJl_<name>.json` or `.json.gz` — the instance in
  UnitCommitment.jl's JSON format, as distributed by that project;
* `UnitCommitmentJl_<name>_ucjl_golden.json` — UnitCommitment.jl's
  solution (dispatch and commitment per unit and hour), where available.

## Instances

| Group | Instances |
|-------|-----------|
| IEEE 14-bus | `case14_base`, `case14_congested`, `case14_contingency`, `case14_fixed`, `case14_flex`, `case14_interface`, `case14_profiled`, `case14_storage`, `case14_sub_hourly` |
| Larger networks | `case118_initcond` (IEEE 118-bus), `rts_gmlc` (RTS-GMLC), `tep_ieee24`, `tep_ieee24_uc` (IEEE 24-bus) |
| Markets | `market_da_simple`, `market_da_scenario` (day-ahead), `market_rt1_simple` … `market_rt4_simple` (real-time) |
| Prices and special cases | `lmp_simple_test_1` … `lmp_simple_test_4`, `aelmp_simple`, `base_with_storage`, `issue_0057`, `ucjl_0_3` |

## Converting and solving an instance

```bash
python3 tools/ucjl2gtopt.py tools/test_data/UnitCommitmentJl_case14_base.json \
  -o results/case14_base.json
cd results
gtopt case14_base.json --output-directory case14_base_out
```

`python3 tools/ucjl2gtopt.py --help` lists the options (for example
`--mip` for integer commitment).

## Running the tests

```bash
export GTOPT_BIN=$(python3 tools/get_gtopt_binary.py)
python3 -m pytest tools/test_ucjl2gtopt.py -q
```

For each instance with a golden file, the test converts it with
`--mip`, solves it with GTOpt and checks that the hourly aggregate
thermal dispatch matches UnitCommitment.jl within a tolerance, plus the
commitment of the units that UnitCommitment.jl's own tests pin.  The
objective and the status of every unit are not compared, because the
objectives include different terms and equal-cost integer solutions
are not unique.  The solve tests need a MIP solver plugin (CPLEX,
Gurobi or MindOpt) and are skipped otherwise.

The golden files are regenerated with Julia and UnitCommitment.jl:

```bash
for f in tools/test_data/UnitCommitmentJl_*.json{,.gz}; do
  stem=$(basename "$f" | sed 's|UnitCommitmentJl_||; s|\.json\.gz$||; s|\.json$||')
  julia tools/ucjl_solve_golden.jl "$f" \
    "tools/test_data/UnitCommitmentJl_${stem}_ucjl_golden.json"
done
```

## Licence

The instances come from UnitCommitment.jl (UChicago Argonne, LLC),
distributed under its BSD-style licence; see that repository.
