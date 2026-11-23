# cen2gtopt test data

Small samples of the public data of the Chilean system operator
(Coordinador Eléctrico Nacional, CEN), used by the `cen2gtopt` tests to
check the readers and clients without network access.

| Path | Contents | Tests |
|------|----------|-------|
| `sip_fixtures/` | Recorded responses of the CEN's public data API (SIP): plants, real and online marginal costs, net demand, real generation, fuel costs, maintenance, limitations, operating states | `test_sip_client.py`, `test_sip_dispatch.py`, `test_operacion_client.py`, `test_emission_factors.py`, `test_marginal_units_core.py` |

These are fixtures for the readers, not cases to solve.

## Running the tests

```bash
cd scripts
pip install -e ".[dev]"
python -m pytest cen2gtopt/tests -q
```
