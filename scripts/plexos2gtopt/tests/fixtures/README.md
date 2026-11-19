# plexos2gtopt test fixtures

Fixtures for the PLEXOS converter.  Together with `../data/` they form
its library of test cases.

| Path | Contents |
|------|----------|
| `entities/build_<element>_array.json` | The expected GTOpt arrays (buses, generators, lines, batteries, hydro, fuels, emissions, reserves, user constraints, …) that each `build_*` step of the converter produces from the synthetic test model built in the tests |
| `empty_case_golden.json` | The expected GTOpt JSON for an empty PLEXOS model: the default options the converter writes |
| [`../data/plexos_coad/`](../data/plexos_coad/README.md) | Two public PLEXOS XML models, IEEE 118-bus and RTS-96, from NREL's `plexos-coad` (BSD-3-Clause), used to check the XML reader on models produced by an independent workflow |

The tests do not need PLEXOS; they read and write files only.

## Running the tests

```bash
cd scripts
pip install -e ".[dev]"
python -m pytest plexos2gtopt/tests -q
```

`test_entity_snapshots.py` compares each `build_*` step with the files
in `entities/`, and `test_golden_round_trip.py` the empty case with
`empty_case_golden.json`.  When a deliberate change in the converter
modifies them, regenerate them with
`PYTEST_UPDATE_GOLDEN=1 python -m pytest plexos2gtopt/tests` and review
the diff.
