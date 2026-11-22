# plp_min_irrigation — minimal PLP case for the irrigation e2e tests

Synthetic 12-stage (monthly, April..March) / 2-hydrology PLP case with the
smallest hydro topology that exercises both irrigation agreements:

- **Laja**: `ELTORO` (embalse) feeding the `RIEGZACO -> RieTucapel ->
  RieSaltos` district chain.
- **Maule**: `LMAULE` (volume 400 hm3, i.e. inside the ordinary reserve
  zone 129..581), `CIPRESES`, `PEHUENCHE`, `COLBUN` (+ two `plpextrac.dat`
  extractions to `CHIBURGO`, used by the cota-425 rule), `ARMERILLO` and
  the Res. 105 district chain `RieCMNA .. RieMolinosOtros`.
- One bus, 900 MW demand, one 100 $/MWh thermal and a 1500 $/MWh failure
  unit.

`plplajam.dat` and `plpmaulen.dat` are copied verbatim from the CEN
`plp_2y` case (agreement parameters), every other file is produced by
`generate_fixture.py`:

```bash
python generate_fixture.py scripts/cases/plp_min_irrigation
```

Used by `scripts/plp2gtopt/tests/test_irrigation_e2e.py`
(PLP -> plp2gtopt --expand-water-rights -> gtopt, strict PAMPL).
