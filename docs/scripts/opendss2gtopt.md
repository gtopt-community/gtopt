# opendss2gtopt / gtopt2opendss

Convert [OpenDSS](https://opendss.epri.com/) distribution circuits to
gtopt plannings, and write gtopt cases (with their solved dispatch) back
as OpenDSS scripts so an AC power flow validates gtopt's result.

- [What it does](#what-it-does)
- [Element mapping](#element-mapping)
- [Expansion studies](#expansion-studies)
- [Balanced equivalent](#balanced-equivalent)
- [gtopt2opendss and AC validation](#gtopt2opendss-and-ac-validation)
- [Validation results](#validation-results)
- [Test feeders](#test-feeders)
- [Limitations](#limitations)

## What it does

`opendss2gtopt` reads an OpenDSS master script with its own parser (no
OpenDSS engine is needed): `!` / `//` / `/* */` comments, `~` / `more`
continuations, `Redirect` / `Compile` (paths relative to the including
file, case-insensitive fallback), `New` / `Edit` with named and
positional properties, `like=`, `Class.name.prop=value` assignments,
`Set` options and in-line RPN values such as `XHL=(8 1000 /)`.  The
phase-coordinate circuit is reduced to gtopt's balanced single-phase
equivalent and written as a planning JSON plus a side-car
`<name>_phases.json` with the per-phase data (phase/node lists, phase
impedance and capacitance matrices, per-phase loads, transformer
windings, the OpenDSS elements merged into each branch).

```bash
opendss2gtopt --info IEEE13Nodeckt.dss                 # summary
opendss2gtopt IEEE13Nodeckt.dss -o ieee13              # DC OPF case
opendss2gtopt IEEE13Nodeckt.dss -o ieee13 \
    --network-model lindistflow --time-mode daily      # distribution case
```

| Option | Default | Effect |
|--------|---------|--------|
| `--network-model` | `dc` | `lindistflow` adds bus voltage limits, reactive power, taps, capacitor banks and line charging (see the formulation's distribution section) |
| `--scaling` | `phase-count` | n-phase branch impedance: `phase-count` = 3/n × Z1, `pos-seq` = Z1 |
| `--time-mode` | `snapshot` | `daily` = 24 hourly blocks from the elements' `daily` (else `yearly`) LoadShapes |
| `--line-rating` | `norm` | branch limit from `normamps`, `emergamps` or `none` |
| `--fixed-taps` | off | keep regulator taps fixed (no tap variables) |
| `--vmin` / `--vmax` | circuit `normvminpu`/`normvmaxpu`, else 0.95 / 1.05 | bus voltage limits [pu] |
| `--import-cost` | 50 | substation energy price [$/MWh] |
| `--der-cost` | 0 | generator / PV energy cost [$/MWh] |
| `--demand-fail-cost` | 1000 | unserved-energy penalty [$/MWh] |
| `--loss-segments` | 16 with `lindistflow` | piecewise segments of the quadratic line losses (one segment, gtopt's default, is the secant at the full rating and overstates the losses of long rural feeders several-fold) |
| `--no-line-charging` | off | drop the line shunt capacitance |
| `--no-source-impedance` | off | slack directly on the source bus |
| `--study` | `none` | `vad` = VAD sizing study, `hosting` = PV hosting capacity ([expansion studies](#expansion-studies)) |
| `--growth` | 1.5 | `vad`: demand growth of the dimensioning year |
| `--mip` | off | integer expansion modules |
| `--bess-usd-kwh` | 400 | `vad`: BESS overnight cost (assumption) |
| `--hosting-buses` | feeder end | `hosting`: PV candidate buses |

## Element mapping

| OpenDSS | gtopt | Notes |
|---------|-------|-------|
| `Circuit` / `Vsource` | slack bus (`reference_voltage` = `pu`, `reference_theta` = 0) + `source` Generator | Thevenin impedance from `R1`/`X1` or `MVAsc3`/`x1r1` as a `source_impedance` line from `<bus>_src` |
| `Line` + `LineCode` | `Line` (`resistance`/`reactance` Ω at `voltage` kV) | sequence data, `rmatrix`/`xmatrix`/`cmatrix`, `kron=y`, units conversion between line and code; rating `√3·kV·normamps` |
| `Line` + `LineGeometry` + `WireData` | `Line` | modified Carson impedances and potential coefficients (Kersting ch. 4-5) with Kron reduction of the neutrals |
| `Line switch=y` | `Line type=switch` | the OpenDSS 1 mΩ switch model, no rating |
| line `C1`/`cmatrix` | Bus `shunt_q` (lindistflow) | half of `ω·C1·V²` at each end |
| `Transformer` (2 winding) | `Line type=transformer` | `%R` + `XHL` on the winding-1 kVA base, ohm referred to `bus_b`; `tap_ratio = tap₁/tap₂`; rating `normhkVA` |
| `Transformer` (3 winding) | three lines to a star bus | star impedances from `XHL`/`XHT`/`XLT` |
| single-phase units between the same buses (`bank=`) | one equivalent line | parallel impedance, summed rating |
| `RegControl` | `tap_ratio_min`/`tap_ratio_max` on the regulator line | taps optimised by gtopt (continuous, exact) under lindistflow |
| `Load` | `Demand` (`lmax`, `power_factor`) | kW + kvar / pf / kVA (last one set wins, as in OpenDSS), LoadShape profile |
| `Generator` | `Generator` | fixed pf, or `qmin`/`qmax` from `minkvar`/`maxkvar` for model 3 |
| `PVSystem` | `Generator type=pv` | `Pmpp × irradiance × %Pmpp`, `kvarMax`, daily profile |
| `Storage` | `Battery` | kWh, kW (`%Charge`/`%Discharge`), efficiencies, `%reserve`, `%stored` |
| `Capacitor` | Bus `shunt_q` (lindistflow) | rating at 1 pu of the bus base; switched banks at their present state |
| `LoadShape` | per-block `lmax` / `generator_profile` | in-line `mult`/`pmult` arrays |
| Fuse, Relay, Recloser, SwtControl, InvControl, monitors, meters | — | ignored (control loops are replaced by optimisation) |

## Expansion studies

`--study vad` turns the LinDistFlow case (24 hourly blocks of the peak
day) into the sizing core of a Chilean VAD study: demand grown by
`--growth`, the NTD voltage band (±6 %) on every bus, a parallel-circuit
candidate on every line section (reconductoring / new circuit), the
substation transformer upgrade in modules, and a BESS non-wires
alternative at the feeder end.  Capex is annualised at one rate, the regulated
VAD rate (6 %, LGSE art. 182 bis) set as the social
`simulation.annual_discount_rate` with no per-candidate `wacc`, over the
asset lives of the VAD bases; COyM is a fixed O&M
share, energy and losses are priced at the PNP and unserved energy at
the long-duration outage cost (`opendss2gtopt/data/vad_params.json`,
public sources with URLs).  `--study hosting` adds zero-cost PV
candidates; the LP builds PV until a voltage or thermal limit binds.
`--mip` makes every module integer.  See
[distribution expansion](../methods/distribution-expansion.md).

## Balanced equivalent

gtopt's distribution model is single-phase-equivalent: one bus voltage,
total three-phase P and Q per branch.  A branch with `n` phases gets the
positive-sequence impedance `Z1 = mean(Zii) − mean(Zij)` (OpenDSS's
`MakePosSeq` rule).  With the default `phase-count` scaling `Z1` is
multiplied by `3/n`: a single-phase lateral carrying `P` then has the
per-unit voltage drop of its loaded phase (`2·R₁₁·P/V_ph² =
2·(3R₁₁)·P/V_LL²`).  Bus base voltages are propagated from the source
through lines and transformer windings (single-phase wye windings are
line-to-neutral) and snapped to `Set voltagebases`.

## gtopt2opendss and AC validation

`gtopt2opendss` writes a balanced three-phase OpenDSS image of a gtopt
case: the slack bus is the `Vsource`; a line with equal bus kV and no tap
is a `Line` (`r1 = r0`, `x1 = x0`, no charging); a transformer, a tapped
line or a kV change is a two-winding wye-wye `Transformer` whose per-unit
impedance reproduces the line's ohms at `bus_b` (`taps = [1, 1/tap]`);
demands are constant-PQ loads, generators fixed generators, capacitors
and bus shunts `Capacitor`s; `Set ControlMode=OFF`.  With `--results`
the solved `load`, `generation`, `qgen`, capacitor `reactive`, line
`tap` and expanded capacity of `--block` are used.  `--solve` runs the
AC power flow with [`opendssdirect.py`](https://pypi.org/project/opendssdirect.py/)
(install the `opendss` extra: `pip install "./scripts[opendss]"`) and,
for a LinDistFlow run, prints gtopt's voltage error against OpenDSS.

```bash
gtopt2opendss ieee13/ieee13.json -o ieee13.dss
gtopt2opendss ieee13/ieee13.json -r ieee13/out --block 1 --solve
```

## Validation results

Balanced equivalent against the original phase-coordinate OpenDSS
solution (regulator taps set to the bank average of the original
solution; `opendss2gtopt/tests/test_ac_feeders.py`):

| Feeder | max \|ΔV\| [pu] | mean \|ΔV\| [pu] | losses original → equivalent [kW] |
|--------|-------------|--------------|-----------------------------|
| hand-made `tiny_feeder.dss` | 0.0045 | 0.0011 | 18.9 → 17.3 |
| IEEE 4-bus Y-Y balanced | 0.0001 | 0.0000 | 569.2 → 567.7 |
| IEEE 13-bus | 0.0295 | 0.0045 | 112.4 → 99.7 |
| IEEE 34-bus (Mod 1) | 0.0103 | 0.0017 | 273.5 → 277.5 |
| IEEE 123-bus | 0.0189 | 0.0035 | 96.0 → 89.7 |

The balanced 4-bus case validates the impedance and transformer
modelling (0.01 % voltage error, 0.3 % loss error); on the unbalanced
feeders the residual is the unbalance itself (worst on single-phase
laterals: 611 on the 13-bus, 85/59 on the 123-bus), plus the original's
constant-impedance / constant-current load models that the equivalent
keeps constant-PQ.

## Test feeders

The IEEE feeders are not committed (their redistribution terms are not
stated).  `opendss2gtopt.feeders.feeder_path("ieee13")` reads them from
`$GTOPT_OPENDSS_FEEDERS` (a directory laid out like OpenDSS's
`Distrib/IEEETestCases`) or downloads them once into
`~/.cache/gtopt/opendss-feeders` from the
[DSS-Extensions mirror](https://github.com/dss-extensions/electricdss-tst);
`GTOPT_OFFLINE=1` forbids the download and the AC tests skip.
`opendss2gtopt/tests/data/tiny_feeder.dss` is a hand-made feeder written
for these tests.

## Limitations

- Balanced single-phase equivalent only; the per-phase data in
  `<name>_phases.json` is kept for a future unbalanced model.
- Load models other than constant PQ (models 2-8, ZIP, CVR) are written
  as constant PQ; voltage-dependent behaviour is not represented.
- Control loops (RegControl bands and LDC, CapControl settings, InvControl
  volt-var / volt-watt) are replaced by gtopt's optimal taps, capacitor
  steps and reactive dispatch; time delays and deadbands are dropped.
- File-based LoadShapes (`mult=(file=...)`, `csvfile`), series
  capacitors, transformers with more than three windings and AutoTrans
  are not converted (a warning is logged).
- Line charging and the slack's Thevenin impedance are kept; transformer
  no-load losses and magnetising branches are not.
