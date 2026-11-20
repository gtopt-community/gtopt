# psse2gtopt test fixtures — provenance

This directory holds the PSS®E `.raw` fixtures used by the
`psse2gtopt` test-suite.

## `synthetic.raw` — hand-crafted for this project (GTOpt licence)

A minimal, fully synthetic PSS®E revision-33 case authored for this
project.  It deliberately exercises every record type and edge case the
parser/writer must handle:

- PQ / PV / slack / **isolated** (IDE=4) buses,
- in-service and **out-of-service** loads, plus a **zero-MW** load,
- in-service / out-of-service generators and a **synchronous condenser**
  (`pmax = 0`),
- a normal branch, an out-of-service branch, and a **zero-reactance**
  branch (reactance-floor test),
- a **CZ=1** and a **CZ=2** two-winding transformer (impedance-base
  conversion test),
- a **three-winding** transformer (star-equivalent expansion test).

All expected values are asserted exactly in `test_raw_parser.py` /
`test_gtopt_writer.py`.

## `ieee14.raw`, `ieee39.raw` — generated from MATPOWER `case14` / `case39`

These are the classic **IEEE 14-bus** and **New England 39-bus** test
systems, written in PSS®E RAW format (revision 32 and revision 33
respectively) by the generator script
[`make_matpower_raw.py`](make_matpower_raw.py) in this directory.  They
are not copies of any third-party `.raw` file.

- **Data source:** the MATPOWER `case14` and `case39` cases
  (R. D. Zimmerman, C. E. Murillo-Sánchez et al., <https://matpower.org>)
  as shipped by pandapower (`pandapower.networks.case14()` /
  `case39()`, BSD-3-Clause, <https://github.com/e2nIEE/pandapower>).
  The underlying network data are public IEEE benchmark systems: the
  IEEE 14-bus system from the University of Washington Power Systems
  Test Case Archive, and the New England 39-bus system (Athay, Podmore
  and Virmani, 1979; Pai, 1989).
- **Conversion:** pandapower's `to_ppc` produces MATPOWER-style
  per-unit bus / generator / branch arrays; the script writes them as
  BUS, LOAD, FIXED SHUNT, GENERATOR, BRANCH and two-winding
  TRANSFORMER records.  See the script's docstring for the field
  mapping.
- **Regenerate** (from `scripts/`, deterministic for a given pandapower
  version):

  ```bash
  python psse2gtopt/tests/data/make_matpower_raw.py
  ```

  The expected counts and demand totals in `test_raw_parser.py` /
  `test_real_cases.py` are those of the MATPOWER cases (IEEE 14:
  14 buses, 5 generators, 11 loads, 259 MW; New England 39: 39 buses,
  10 generators, 21 loads, 6254.23 MW).

They provide real-world coverage that the synthetic fixture cannot:
both PSS/E format revisions, real per-unit impedances and MVA ratings,
and a non-trivial mesh topology that gtopt solves to optimality with
zero unserved load.

## Running the tests

```bash
cd scripts
pip install -e ".[dev]"
python -m pytest psse2gtopt/tests -q
```
