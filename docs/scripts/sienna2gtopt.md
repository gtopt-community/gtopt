# sienna2gtopt — NREL-Sienna PowerSimulations.jl test cases → GTOpt

Ports small test cases of NREL-Sienna
[PowerSimulations.jl](https://github.com/NREL-Sienna/PowerSimulations.jl)
(systems of PowerSystemCaseBuilder.jl) to GTOpt planning JSON, for
cross-checks of GTOpt against a peer implementation: hydro cascades,
monitored lines, HVDC links, batteries, pumped storage, interruptible
loads, time-varying fuel prices and reserve products with operating
reserve demand curves (ORDC).

`sienna_to_gtopt` is a **deprecated alias** (renamed in 1.2.0): it
prints a note on stderr and runs `sienna2gtopt` unchanged.

Package README with the mapping details, the test assertions and the
data-convention notes:
[`scripts/sienna2gtopt/README.md`](../../scripts/sienna2gtopt/README.md).

---

## Quick start

```bash
sienna2gtopt cascading_hydro -o cascade.json
sienna2gtopt monitored_line  -o ml.json
sienna2gtopt hvdc            -o hvdc.json
sienna2gtopt battery_degeneracy --subcase c        # -> battery_degeneracy_c.json
sienna2gtopt reserves --golden reserves_uc.json --ordc auto -o res.json
gtopt hvdc.json --solver highs
```

The command prints a one-line element summary
(`buses=… gens=… lines=… … => FILE`).

## Options

```text
sienna2gtopt VARIANT [-o FILE] [--bundle 5bus] [--subcase b]
             [--golden FILE] [--ordc auto] [--source-version X]
```

| Option | Default | Meaning |
|--------|---------|---------|
| `VARIANT` | required | see the table below |
| `-o, --output FILE` | `<variant>.json` (`battery_degeneracy_<subcase>.json`; `reserves`: the golden's slug) | output JSON |
| `--bundle {5bus,14bus}` | `5bus` | Sienna data bundle; every variant reads the 5-bus CSV files, so only `5bus` converts today (`14bus` downloads but stops on the missing `bus.csv`) |
| `--subcase {b,c,d,e,f}` | `b` | `battery_degeneracy` only |
| `--golden FILE` | — | `reserves` only (required there): a reserve golden written by `tools/sienna_solve_golden.jl` |
| `--ordc {auto,stacked,omit}` | `auto` | `reserves` only: ORDC as stacked priced reserve zones, omitted, or `auto` (stacked when the golden modelled it) |
| `--source-version X` | detected | PowerSystemsTestData commit to assume (supported and tested: `a2ecae9…`) |

### Variants

| Variant | Sienna system | Exercises |
|---------|---------------|-----------|
| `cascading_hydro` | `c_sys5_hy_cascading_turbine_energy` | junctions, waterways, reservoirs and turbines in a three-plant cascade (single bus) |
| `monitored_line` | `c_sys5_ml` | `Line.enforce_level`: one monitored line, the others unenforced |
| `hvdc` | `c_sys5_dc` | a DC line (no reactance, no Kirchhoff row) |
| `battery_degeneracy` | `batt_test_case_{b..f}_sys` | single-bus battery with asymmetric efficiencies, per-subcase initial charge and costs |
| `pumped_storage` | `c_sys5_phes_ed` | pumped storage modelled as a GTOpt battery |
| `interruptible_load` | `c_sys5_il` | `Demand.fcost` for a price-sensitive load |
| `fuel_cost_ts` | `c_sys5_re_fuel_cost` | time-varying `Fuel.price` |
| `wecc_240` | — | a **synthetic** scalability fixture with the WECC 240-bus sizes (240 buses, 100 generators, 240 lines), not the WECC data |
| `reserves` | `c_sys5_uc`, `c_sys5_uc_re` with reserves | unit commitment, range reserves and ORDC; reads the golden's `system_data`, not the CSV bundle |

For the `reserves` mapping (range reserves → `ReserveZone` +
`ReserveProvision` with `shared_headroom`, ORDC steps → one priced zone
per step, and the objective offset between the two models) see
[Reserve benchmarks](../../scripts/sienna2gtopt/README.md#reserve-benchmarks-reserves-variant)
in the package README.

## Data

`PowerSystemsTestData` has no licence, so GTOpt does not ship it.  On
first use the files are downloaded from a pinned upstream commit,
verified against SHA-256 checksums and packed into
`~/.cache/gtopt/sienna/sienna_<bundle>.tar.zst` (or
`$XDG_CACHE_HOME/gtopt/sienna`).

- `GTOPT_SIENNA_DATA_DIR=DIR` — use pre-built `sienna_<bundle>.tar.zst`
  archives from `DIR` (offline / air-gapped hosts).
- `GTOPT_OFFLINE=1` — forbid downloads; a missing bundle is an error
  (exit code 2).

A bundle whose files all match the manifest checksums is the pinned
commit; anything else is undetectable and `--source-version` decides.

## See also

- [`rts_gmlc_to_gtopt`](rts_gmlc_to_gtopt.md) and
  [`nrel118_to_gtopt`](nrel118_to_gtopt.md) — emission benchmarks.
- `tools/test_sienna_vs_gtopt.py` — end-to-end comparison against the
  PowerSimulations.jl goldens.
