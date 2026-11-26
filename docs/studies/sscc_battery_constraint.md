# CEN's BESS ancillary-services (SSCC) constraints in PCP: negative CMg and a native replacement

> Study note, November 2026.  Branch `feat/bess-reserve`.  Data: CEN PCP
> PLEXOS bundles (`DATOS*.zip.xz`, `RES*.zip.xz`) from `gtopt-cases-dev`
> (`chile/daily_scheduling/pcp_*`) and `support/plexos/perdidas_cen`
> (2026-05-25, 2026-11-03, 2026-11-09).  Everything here is about the
> *model* CEN feeds PLEXOS; no PLEXOS run times are reported.

## 1. What CEN implemented

Since the 2025-11-09 bundle, CEN's PCP model (`PRGdia_Full_Definitivo`)
carries a PLEXOS constraint category **`BESS SSCC Constraints`** plus an
auxiliary battery per reserve-providing BESS (category `CRCA AUX`).
CNE enabled BESS to provide secondary and tertiary frequency control
(CSF/CTF) in its 2025 SSCC regulatory report, which is consistent with the
timing (see §7).

| Bundle | BESS with the SSCC set | `_AUX` batteries |
|---|---:|---:|
| 2025-10-05, 2025-10-19 | 0 | 0 |
| 2025-11-09 … 2026-01-18 | 2 (MANZANO_FV, DON_HUMBERTO_FV) | 2 |
| 2026-02-15, 2026-03-15 | 3 | 3 |
| 2026-04-07 | 4 (+LA_CABANA_EO, TOCOPILLA) | 4 |
| 2026-04-12 … 2026-05-25 | 5 (+DEL_DESIERTO) | 5 |
| 2026-11-03, 2026-11-09 | 19 | 19 |

The structure never changed; only the list of batteries grew.  All rows
are in the base data (no scenario tag) with `Include in ST Schedule =
-1`, so they are active in every PCP run.  The reserves themselves are
switched on by the `C{P,S,T}F{Up,Down}_ON_BESS` scenarios of the model.

For a battery $B$ (example `BAT_TOCOPILLA`, 2026-05-17;
$\eta_c = \eta_d = 0.95$, capacity 580 MWh) the set is:

| PLEXOS constraint | Members and coefficients | Sense / RHS | Penalty |
|---|---|---|---|
| `UPStorageBound_B` | Battery B: Energy $-0.95$; Regulation Raise Reserve Provision $+1$ | $\le 0$ | hard |
| `DOWNStorageBound_B` | Battery B: Energy $+1$; Regulation Lower Reserve Provision $+0.95$ | $\le 580$ (= capacity) | hard |
| `B_CF_GEN_COMP` | Reserve `C{P,S,T}F_LW_BESS` → constraint: Provision Coefficient $a_z(t)$; Battery `B_AUX`: Generation $-1$ | Sense unset ⇒ $=0$ | hard |
| `B_CF_LOAD_COMP` | Reserve `C{P,S,T}F_RS_BESS`: Provision Coefficient $a_z(t)$; Battery `B_AUX`: Load $-1$ | $=0$ | hard |
| `CSF_{RS,LW}_MIN_BAT_B` | Battery B: Reserve Provision $+1$, Reserve Units $-5$ | $\ge 0$ | hard in PLEXOS |

`B_AUX` is a PLEXOS Battery at the same node: Capacity 99 999 MWh (9 999
in the first bundles), Max Power = Max Load = 1000 MW, Charge/Discharge
Efficiency 100 %, Initial SoC 50 %, `Simultaneous Charge and Discharge =
-1` (allowed), `End Effects Method = 1`.

The coefficients $a_z(t)$ come from `ReserveUsageTxCompensation.csv`
(data file `BESS_ReserveUsageTxCompensation`, 2-hour patterns `H1-2 …
H23-24`; the XML default is 0.3 for CSF and 0 for CPF/CTF).  A second
file, `SSCC_Activation_BESS.csv`, sets the Reserve property **Energy
Usage** (%) of the six `*_BESS` reserves.  For 2026-05-17, per pattern:
$a^{RS}_z(t) = u^{RS}_z(t)$ and $a^{LW}_z(t) = 1 - u^{LW}_z(t)$ (e.g.
`CSF_RS`: $u = 35\,\%$, $a = 0.35$; `CSF_LW`: $u = 81\,\%$, $a = 0.19$;
`CPF_RS`: $u = 50\,\%$, $a = 0.5$).  From 2026-05-25 a third file,
`Activation_Factors.csv`, appears (hourly factors per service).

In math, per block $t$ (1 h), with $r^{\uparrow}\_{z}$, $r^{\downarrow}\_{z}$
the provisions of $B$ to the BESS reserve zones, $E_t$ its end-of-period
energy and $g^{x}\_t$, $\ell^{x}\_t$, $E^{x}\_t$ the AUX generation, load and
energy:

$$
\sum_z r^{\uparrow}_{z,t} \le \eta_d\,E_t, \qquad
E_t + \eta_c \sum_z r^{\downarrow}_{z,t} \le \overline{E}
\tag{1}
$$

$$
g^{x}_t = \sum_z a^{LW}_z(t)\, r^{\downarrow}_{z,t}, \qquad
\ell^{x}_t = \sum_z a^{RS}_z(t)\, r^{\uparrow}_{z,t}
\tag{2}
$$

$$
E^{x}_t = E^{x}_{t-1} + \ell^{x}_t - g^{x}_t, \quad 0 \le E^{x}_t \le 99\,999,
\quad E^{x}_0 = 49\,999.5
\tag{3}
$$

and $g^{x}_t - \ell^{x}_t$ enters the node balance of $B$'s bus.

## 2. Intended purpose

- (1) is the standard **energy-sufficiency** rule with a 1 h sustain time:
  a BESS offering $r^\uparrow$ must hold $r^\uparrow/\eta_d$ MWh, and one
  offering $r^\downarrow$ must have room for $\eta_c r^\downarrow$ MWh.
- (2)–(3) model the **energy of reserve activation** ("energy usage") and
  its **compensation**: a fraction of the provision is expected to be
  deployed; PLEXOS's Reserve `Energy Usage` counts deployed reserve in
  the energy balance, and CEN adds the AUX battery to inject/withdraw the
  same expected energy at the BESS bus so the deployment does not change
  the scheduled energy balance ("Tx compensation").  The AUX is a large,
  lossless, half-full tank so that it never binds.
- `CSF_*_MIN_BAT` imposes PLEXOS's 5 MW minimum provision when the BESS
  offers CSF.

The intent is reasonable.  The implementation via (2)–(3) is not: the AUX
tank is outside every physical balance, so (2) forces an injection
proportional to down reserve and a withdrawal proportional to up reserve
at the BESS bus, with energy that comes from (or goes to) nowhere.

## 3. Why it produces negative marginal costs

*Open question:* this section assumes PLEXOS's Reserve `Energy Usage`
has no effect on a battery's own Generation/Load (hypothesis H1, §9).  If
PLEXOS books it into the battery's SoC instead (H2), the AUX rows only
cancel that term at the bus and the derivation below does not apply as
written.

Write the bus balance with dual $\lambda$ and (2) with duals
$\mu^{\downarrow}, \mu^{\uparrow}$, with Lagrangian

$$
c^\top x - \lambda(\text{inj} - \text{wd} - D)
\;-\; \mu^{\downarrow}(a r^\downarrow - g^x) - \dots
$$

Stationarity for $g^x$ (cost 0, coefficient $+1$ in the bus row and in
(2)):

$$ -\lambda + \mu^{\downarrow} = 0 \;\Rightarrow\; \mu^{\downarrow} = \lambda. $$

For $r^\downarrow$ (offer cost $c^\downarrow$, coefficient $\gamma=1$ in the
requirement row with dual $\pi^\downarrow \ge 0$), when it is between its
bounds:

$$ c^\downarrow - \pi^\downarrow - a\,\mu^{\downarrow} = 0
\;\Rightarrow\; \boxed{\lambda = \dfrac{c^\downarrow - \pi^\downarrow}{a}} \tag{4}$$

So wherever the BESS down provision is marginal, **the bus price equals
minus the down-reserve price divided by $a$**.  With $a \approx 0.2$–$0.3$
the reserve price is amplified 3–5 times, with the opposite sign.  When the
bus is not isolated, $\lambda$ is set by the system; then (4) says the BESS
gets a subsidy $a\lambda$ per MW of down reserve, so it maxes out
$r^\downarrow$ to harvest free AUX energy, which depresses $\pi^\downarrow$
and injects energy into the bus.  Symmetrically, up reserve costs $a\lambda$
of AUX withdrawal; at a negative-price bus that becomes a reward (a
fictitious sink).

Negative prices appear where the forced AUX injection cannot be disposed
of: an export-constrained BESS pocket at night (no solar to curtail), or
any bus where the only flexible resource is the BESS itself.  This is the
"M3" mechanism of `studies/pcp_loss_eval` (a reserve that forces an
injection), but produced by a fictitious device rather than by a real
unit.

### Evidence in the PLEXOS solutions

- **2026-05-17** (no negative day-1 CMg in PLEXOS): `B_CF_GEN_COMP` is
  binding in every hour with price $= -\lambda_{bus}$ (Tocopilla110 h1:
  $\lambda = 86.87$, CF price $-81.95 = -86.87/1.057$, the marginal loss
  factor).  The AUX injections track the BESS charging (DON_HUMBERTO h5:
  AUX Generation $16.79$ = battery Load $16.79$): the BESS charges with
  energy created by its own down-reserve offer.  `DEL_DESIERTO_AUX`
  generates 33–38 MW all day while the BESS offers its full 100 MW of
  `CSF_LW`.  Over the week the five AUX tanks release **9.3 GWh**
  ($E^x$: 49 974 → 46 540 MWh for DEL_DESIERTO alone).
- **2026-11-03** (19 BESS): the AUX tanks **absorb 7.8 GWh** (up reserve
  dominates).  `Futuro033_CRCA*` (ANDES 2B/3/4) is priced at $-3.06$ to
  $-3.20$ in hours 20–24 while the rest of the north is at $+15$; there the
  AUX loads equal the batteries' discharge (ANDES_3 h20: discharge 44.05 =
  AUX load 44.05) — the BESS discharges into a fictitious load to back its
  up-reserve offer, and `B_CF_*_COMP` carry duals of
  $\pm 3.19 = \mp\lambda$.
- Day-1 negative prices elsewhere (system-wide −0.37 in 11-03 midday,
  −1 to −10 in the 2025–26 bundles, −132 at Pan de Azúcar on 05-25)
  predate the SSCC set (2025-10-05 already has −7.1), so the SSCC rows are
  **one** negative-price driver, localised at BESS buses/pockets, not the
  only one.

### Minimal reproduction (gtopt)

`test/source/test_bess_reserve_native.cpp`, first case.  One bus, demand
50 MW served by a 50 MW must-run unit (no disposal), a 50 $/MWh unit
$g_1$ that can also offer down reserve (by running, $g_1 \ge r$), a BESS
50/50 MW, 100 MWh, SoC 50, and a 40 MW down-reserve requirement.  The CEN
variant adds the AUX battery and `aux.discharge = 0.5·r_bess^dn`.

| | BESS $r^\downarrow$ | $g_1$ | BESS charge | Cost | $\lambda$ |
|---|---:|---:|---:|---:|---:|
| Native rows (§4) | 40 | 0 | 0 | 0 | $\in[0, 50]$, $\ge 0$ |
| CEN AUX ($a = 0.5$) | 20 | 20 | 30 | 1 000 | **−100** |

With the CEN row the AUX injects 10 MW, the battery has to store it, and
that charging eats the headroom it needed for the reserve, so the
expensive unit has to run to provide reserve and the bus price drops to
$-100$ $/MWh.  The same battery provides all 40 MW for free under the
native rows.

## 4. Native replacement in gtopt

New opt-in Battery fields (`reserve_sustain_hours` $=T$ [h],
`reserve_discharge_credit`), see `include/gtopt/battery.hpp` and
`docs/formulation/mathematical-formulation.md` §5.9.  When $T$ is set, the
generic per-provision generator rows ($g + r^\uparrow \le \overline P$,
$g - r^\downarrow \ge 0$, the latter forcing a BESS to *discharge* to
offer down reserve) are replaced by rows over the sum of all provisions of
the battery:

$$
p^{d}_t - p^{c}_t + \textstyle\sum r^\uparrow_t \le \overline{P}^{d},
\qquad
p^{c}_t - \kappa\,p^{d}_t + \textstyle\sum r^\downarrow_t \le \overline{P}^{c}
\tag{5}
$$

$$
E_t - \tfrac{T}{\eta_d}\textstyle\sum r^\uparrow_t \ge \underline{E},
\qquad
E_t + T\,\eta_c \textstyle\sum r^\downarrow_t \le \overline{E}
\tag{6}
$$

(5) is the net-power headroom (a charging BESS offers up reserve by
cutting its charge); $\kappa = 0$ by default (charge-side down headroom).
(6) is exactly CEN's (1) with a parameter $T$.  The power envelope
$p^c/\overline P^c + p^d/\overline P^d \le 1$ and self-discharge are
unchanged and compose with these rows ($E_t$ is post-loss).

**Proposition.** Under (5)–(6) no reserve variable appears in a bus
balance, so $\pi$ reaches $\lambda$ only through $p^c$, $p^d$, $E$.  The
up rows add $+\mu$ to the reduced cost of $p^d$ and $-\mu$ to that of
$p^c$ and make stored energy more valuable: they penalise injection and
reward withdrawal, so they can only raise $\lambda$.  With $\kappa = 0$ the
down headroom only limits $p^c$, never rewarding an injection.  Hence if
the bus has a free-disposal resource (curtailable injection, a unit above
its minimum, …) not involved in reserve rows, a feasible $\varepsilon$
decrease of net injection exists at zero cost and $\lambda \ge 0$.  A
negative $\lambda$ can only come from a pre-existing non-disposable
injection, or from a *physical* shortage of down capability (binding
$E_t + T\eta_c r^\downarrow \le \overline E$ or $\kappa=1$) in a pocket
without disposal, the same situation in which a thermal unit's
down-reserve must-run prices negative.  Under (2)–(3) instead, (4) ties
$\lambda$ to $-\pi^\downarrow/a$ whenever the BESS provision is marginal,
regardless of its physical state, and the AUX tank creates/destroys
energy; no free-disposal argument applies because $g^x$ cannot be reduced
without reducing a required reserve.

The expected-deployment energy (CEN's intent in (2)) can be added later as
an *SoC* term,
$E_t = \dots - \Delta t\,(u^\uparrow r^\uparrow/\eta_d - u^\downarrow \eta_c r^\downarrow)$,
never as a bus injection.  Its up part only raises
prices; its down part puts energy into the SoC and should be used with
care (or $u^\downarrow = 0$) if price safety is required.  Not implemented
here.

Unit tests (`test_bess_reserve_native.cpp`, all hand-computed; the
reported objective adds back the zone's served-reserve revenue so it reads
"operating cost + shortage penalty"): CEN AUX vs native (above); legacy
rows untouched when $T$ unset; up headroom credits curtailing a charge
(r = 60 with a 40 MW inverter by charging 20 MW: cost 6 000 vs 205 000
with the generic row); up SoC sufficiency with $T = 2$, $\eta_d = 0.8$
(E = 150, charge 100, cost 7 500) and with self-discharge
($p^c = 100 + 50\cdot0.48/8760$); down SoC room (SoC 180/200 serving 10 MW
leaves room for only 30 of a 50 MW requirement, shortage 20, versus the
full 50 with $T = 0$); charge-only vs discharge-credit headroom (20 vs 50
MW with a 20 MW charger discharging 100 MW).

## 5. gtopt's previous handling of the CEN set

`plexos2gtopt` (before this branch, and still with the default
`--bess-reserve uc`):

- `UPStorageBound` / `DOWNStorageBound` → hard UserConstraints (correct).
- `B_CF_GEN_COMP` / `B_CF_LOAD_COMP`: the AUX battery is dropped and its
  Generation/Load are redirected to the **main** battery as
  `-(discharge + charge) + a·r = 0` (`=` with `--plexos-legacy`, `<=`
  otherwise; commits `a638dadf`, `3a6c008e`), with the XML default
  coefficient (0.3) instead of the CSV per-pattern values.  This is a
  different model from PLEXOS: with `=` it forces battery throughput
  proportional to reserve (cycling losses), with `<=` it makes reserve
  available only when the battery is moving (a reward for cycling).
- `SSCC_Activation_BESS.csv` (PLEXOS `Energy Usage`) is mapped to the
  provision's **requirement-contribution factor** (`dr/ur_provision_factor`),
  so a BESS counts only 20–80 % of its provision toward the requirement;
  in PLEXOS the provision counts gross.
- Our PCP runs therefore do not reproduce the PLEXOS mechanism; see §6.

`--bess-reserve native` (new): sets `Battery.reserve_sustain_hours` from
the storage-bound coefficients ($T = \eta_d k_r/k_e$, here 1 h), makes the
four rows inactive provenance stubs, keeps `CSF_*_MIN_BAT`, and counts
BESS SSCC provisions at gross value.  Code: `scripts/plexos2gtopt/
_bess_reserve.py`, tests `scripts/plexos2gtopt/tests/
test_bess_reserve_native.py`.

## 6. Real PCP cases

Conversions with the worktree converter (`--no-check --write-out all
--plexos-legacy --no-battery-efin-pin --line-losses-mode piecewise
--loss-cost-eps 0`, plus `--bess-reserve uc` or `native`), solved as LP
relaxations (`--no-mip`, CPLEX, 8 threads).  `uc` is the previous mapping
(§5); `native` is the new primitive.  Energies are duration-weighted.

| | 2026-05-17 uc | 2026-05-17 native | 2026-11-03 uc | 2026-11-03 native |
|---|---:|---:|---:|---:|
| SSCC BESS | 5 | 5 | 19 | 19 |
| objective | 440 975 983 | 440 933 717 (−0.01 %) | 21 567 047 | 20 477 301 (−5.05 %) |
| SSCC BESS discharge / charge [MWh] | 0 / 0 | 952 / 755 | 0 / 0 | 18 591 / 12 152 |
| SSCC BESS down / up provision [MWh] | 0 / 2 927 | 72 / 2 571 | 0 / 132 644 | 3 306 / 98 614 |
| (bus, block) with λ < 0, all buses | 189 | 187 | 16 024 | 15 131 |
| (bus, block) with λ < 0, SSCC BESS buses | 8 | 7 | 1 230 | 1 183 |
| min λ at SSCC BESS buses [$/MWh] | −0.35 | −0.35 | −0.35 | −0.35 |
| duration-weighted mean λ, all buses | 68.24 | 67.70 | 22.72 | 21.57 |

Findings:

1. **The old `uc` mapping freezes the SSCC batteries.**  The redirected
   rows `-(p^d + p^c) + 0.3 r^{dn}_{CSF} = 0` and `-(p^c + p^d) + 0.3
   r^{up}_{CSF} = 0` force $r^{dn}\_{CSF} = r^{up}\_{CSF}$ and tie throughput
   to reserve; the LP's cheapest point is $p^c = p^d = 0$, $r\_{CSF} = 0$.
   Every SSCC BESS sits idle for the whole week on both dates and offers
   only primary (CPF) up reserve.  This is neither PLEXOS's model (where
   the AUX tank makes the BESS *move* and creates energy) nor a physical
   one.
2. **gtopt does not reproduce PLEXOS's negative CMg**, with either
   mapping.  The minimum price at the 19 SSCC BESS buses is −0.28…−0.35
   $/MWh in both modes: the system-wide loss/curtailment floor that
   predates the SSCC set, not the $(c_{dn} - \pi_{dn})/a$ pin.  On
   2026-11-03 PLEXOS prices Futuro033_CRCA2B/3/4 at −3.06…−3.20 in hours
   20–24 (§3); gtopt's minimum there is −0.28 in both runs.  The `uc`
   mapping cannot produce the pin because it injects no AUX energy; the
   native rows cannot produce it by construction (§4).
3. **Native releases the batteries without creating negative prices.**
   On 2026-11-03 the 19 BESS cycle 18.6 / 12.2 GWh, provide 3.3 GWh of
   down reserve (0 before) and shift up reserve to other units; the
   negative-price count falls 6 % (16 024 → 15 131), the mean price falls
   1.15 $/MWh and the objective falls 5.05 %.  Part of the net discharge
   (6.4 GWh) is the 8.4 GWh initial SoC drawn down, because these runs
   leave the end SoC free (`--no-battery-efin-pin`).  Valued at the mean
   price that is about 0.14 M of the 1.09 M objective drop, so most of
   the gain comes from operating flexibility.  The energy budget closes
   (≈ 176 MWh residual over 30 GWh throughput, consistent with
   self-discharge).  On 2026-05-17 (5 BESS, 249 MWh initial SoC) the
   effect is marginal (−0.01 %).
4. The comparison with PLEXOS is qualitative (sign and location of
   prices, BESS behaviour): the gtopt runs are LP relaxations with gtopt's
   loss and UC mappings, so absolute prices differ from the PLEXOS
   solution.  A faithful PLEXOS reproduction would need an explicit AUX
   battery in the `uc` mapping (recommendation 1).

## 7. Public material

- CNE, *Informe regulatorio de Servicios Complementarios* (May 2025):
  https://www.cne.cl/wp-content/uploads/2025/05/Informe-regulatorio-SSCC.pdf
- CNE, *Anexo Informe regulatorio SSCC* (June 2025; BESS enabled for CSF
  and CTF, eight facilities, 550 MW, PLEXOS storage modelling of the
  operation programme):
  https://www.cne.cl/wp-content/uploads/2025/06/Anexo-Informe-regulatorio-SSCC.pdf
- AFRY estimate of the BESS-in-SSCC impact and the new "Costo
  Oportunidad BESS" charge:
  https://www.energiaestrategica.com/afry-proyecta-reduccion-en-costos-de-operacion-y-sistemicos-con-la-habilitacion-bess-en-mercado-de-servicios-complementarios/

- Coordinador, *Informe SSCC 2026* (Dec 2025; CSF minimum delivery
  15 min, CTF up to 1 h):
  https://www.coordinador.cl/wp-content/uploads/2025/12/2025.12.23-Informe_SSCC_2026-Definitivo.pdf
- Coordinador, *Reglas SSCC en tiempo real* (GO-SEN, Feb 2026):
  https://www.coordinador.cl/wp-content/uploads/2026/02/Informe_GO-SEN_Reglas_SSCCv6_20260224.pdf
- PLEXOS help, `Reserve.EnergyUsage`, `Reserve.Duration`,
  `ReserveBatteries.MaxResponse` / `MaxChargeResponse` (portal pages not
  directly readable; content taken from search-engine excerpts and from
  the property schema embedded in the CEN XML, PLEXOS 10.000 R08).

The CNE report and annex were read in full.  The CNE annex states that
activation factors were modelled by having the BESS "deliver part of
their reserve as energy" and warns about zero-marginal-cost injections in
the solar block.

## 8. Recommendations

1. For gtopt runs of CEN cases, use `--bess-reserve native`; keep `uc`
   only for PLEXOS-reproduction tests (and fix its AUX mapping if a
   faithful reproduction is needed: emit the AUX battery instead of
   redirecting to the main battery, with the per-pattern coefficients).
2. Suggest to CEN replacing `B_CF_*_COMP` + `B_AUX` by (5)–(6) (PLEXOS can
   express both with Battery Generation/Load/Energy and Reserve Provision
   coefficients) and, if expected deployment energy matters, an SoC
   term instead of a bus injection.
3. Follow-ups in gtopt: commitment-gated headroom ($\overline P u$), an
   optional SoC energy-usage term, start-of-block SoC check, and the
   pending `provision_BAT_*` naming bug on 2026-11 bundles.

## 9. Open question: does `Energy Usage` reach the BESS SoC? (H1 vs H2)

What PLEXOS offers natively (property schema in the CEN XML, PLEXOS
10.000 R08, plus help excerpts):

- **Power headroom** — native: Reserve→Batteries `Max Response`
  (discharge side), `Max Charge Response` (charge side), `Total
  Response`.  CEN uses the first two (from `CFdata` CSVs).  In the
  2026-05-17 solution a BESS charging at 112.6 MW provides 152.7 MW of
  up reserve, i.e. reducing charge is credited.
- **Energy sufficiency** — `Reserve.Duration` [s] ("time over which the
  required response must be maintained", since 6.208) exists; whether it
  acts on Battery objects in 10.0 R08 could not be confirmed (the help
  text is hydro-oriented).  CEN does not use it and writes
  `UP/DOWNStorageBound` (1 h) by hand.
- **Deployed energy** — `Reserve.Energy Usage` is native and CEN fills it
  from `SSCC_Activation_BESS.csv` (only the six `*_BESS` reserves use
  it).  Per the help, it enters the energy balance as
  $\text{Gen} + EU\cdot\text{Prov}$ (raise) and
  $\text{Gen} - (1-EU)\cdot\text{Prov}$ (lower).  For generators the help
  says reported Generation already includes it; for batteries it is not
  documented.

The `B_CF_*_COMP` coefficients are exactly that native `Energy Usage`
term with the opposite sign, and the rows are per battery (the AUX
tracks the main battery's own provision: e.g. DEL_DESIERTO_AUX h1 =
0.33·54.09 + 0.19·78.5 = 32.76, not the pool total).  Two readings:

- **H1** — `Energy Usage` has no effect on Battery Generation/Load.  The
  AUX then injects/withdraws fictitious energy at the bus: the mechanism
  of §3 and what `--bess-reserve uc` reproduces.
- **H2** — PLEXOS books `Energy Usage` into the battery's Generation/Load
  (hence its SoC).  The AUX then only cancels that term at the bus, and
  the whole design amounts to "activation energy goes to the SoC, not to
  the bus"; §3's $\lambda = (c^\downarrow - \pi^\downarrow)/a$ does not
  apply as written and the `uc` reproduction is not faithful.

Evidence from the 2026-05-17 PLEXOS solution: battery SoC satisfies
$\Delta E = \text{Load}\,\eta_c - \text{Gen}/\eta_d$ exactly and node and
system balances close with the AUX included — compatible with both.  The
pattern leans to H2: in all 508 hours with an active AUX the BESS has
flow, in 308 of the 320 hours without reserve it is idle, and in 167
hours its net is exactly the negative of the AUX; against it, 8 hours
have the opposite sign and 3 hours fall below the minimum charge level.

**Decisive test** (requires PLEXOS): re-solve with `Energy Usage = 0` on
the `*_BESS` reserves, or without the CF rows, and compare battery
dispatch and nodal prices.

Independently of H1/H2, the native gtopt rows (§4) keep reserve out of
every bus balance and cannot create negative prices.  A price-safe
formulation for CEN is to drop `Energy Usage`, the CF rows and the AUX,
and widen the energy bound (raise:
$\eta_d E \ge (1 + u\,\Delta t)\,r^\uparrow$, analogously for lower).
Recommendation 2 of §8 should wait for the H1/H2 test.

## 10. gtopt primitives for the full CEN BESS set

Besides the native rows of §4, gtopt now has the two pieces the CEN
objects need (formulation §5.9, "Per-side response caps" and "Expected
deployment energy", with the object-by-object mapping table):

- `ReserveProvision.urmax_response` / `urmax_charge_response` (and
  `dr…`): the PLEXOS `Max Response` / `Max Charge Response` CSVs
  (`CSF_B_MRU.csv`, `CSF_B_MRD.csv`), applied to a split
  $r = r^{\text{dis}} + r^{\text{chg}}$ with each side's own headroom.
- `ReserveProvision.ur_energy_usage` / `dr_energy_usage` (default from
  `Battery.reserve_energy_usage_up/down`): the `CF_*_COMP` coefficient of
  each reserve (0.3 for CSF, 0 for CPF/CTF on 2026-11-07) charged to the
  battery's own SoC instead of an AUX bus injection.  This is H2 of §9 made explicit
  and price-safe; leaving it unset is the "drop Energy Usage" option.
