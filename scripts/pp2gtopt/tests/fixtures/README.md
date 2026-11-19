# pp2gtopt test fixtures

Expected outputs of the pandapower converter, used as golden files.

| Path | Contents |
|------|----------|
| `entities/build_<element>.json` | The GTOpt arrays (buses, demands, generators, external-grid generator, lines, transformers, physical lines) that each step of the converter produces for the reference network of the tests |
| `ieee30b_golden.json` | The full conversion of pandapower's `case_ieee30`, which must match [`cases/ieee30b`](../../../../cases/ieee30b/README.md) |

## Running the tests

```bash
cd scripts
pip install -e ".[dev]"
python -m pytest pp2gtopt/tests -q
```

`test_entity_snapshots.py` and `test_golden_round_trip.py` compare
the converter output with these files; after a deliberate change,
regenerate them with `PYTEST_UPDATE_GOLDEN=1 python -m pytest
pp2gtopt/tests` and review the diff.  The tests need pandapower.
