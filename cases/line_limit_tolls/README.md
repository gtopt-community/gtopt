# line_limit_tolls

A two-bus, one-line case that shows how the way a line's thermal limit
is written (`line_thermal_limit_mode`) changes the line's limit dual,
the split of the price difference into loss and congestion parts, and
the transmission payments built on them: the Chilean *ingreso tarifario*
(IT) and *peaje*, and the FTR/CRR congestion rent of the US ISOs.

All data are synthetic.

## Network

| Element | Bus | Data |
|---------|-----|------|
| `g1` | `b1` | 500 MW at 10 $/MWh |
| `g2` | `b2` | 100 MW at 30 $/MWh |
| `g3` | `b2` | 200 MW at 50 $/MWh |
| `dA` (user A) | `b1` | 50 MW in every block |
| `dB` (user B) | `b2` | 60 / 150 / 250 MW |
| `l12` | `b1`→`b2` | T = 100 MW, R = 10 Ω, V = 100 kV (c = R/V² = 10⁻³ MW⁻¹), `bidirectional`, K = 4 uniform segments, `loss_allocation_mode = split`, `loss_cost_eps = 0` |

The three blocks last 8000, 600 and 160 h (one year).  Block 1 is
uncongested; in blocks 2 and 3 the line is at its limit and `g2`
(block 2) or `g3` (block 3) sets the price at `b2`.

Four variants, one directory each, differ only in the line:

| Directory | Line |
|-----------|------|
| `lossless/` | `line_losses_mode = none` |
| `flow/` | losses, `line_thermal_limit_mode = flow`: f ≤ T |
| `sending/` | losses, `sending`: f + ℓ/2 ≤ T (the sending-end power) |
| `full/` | losses, `full`: f + ℓ ≤ T |

`python tolls.py` reads `<mode>/output/` (run `gtopt
<mode>/line_limit_tolls.json -d <mode>/output --write-out all`) and
prints the tables below.  CTest runs the four variants against the
goldens (`line_limit_tolls_<mode>`), and
`test/source/test_line_thermal_limit.cpp` ("duals: LMP decomposition and
tolls") asserts the relations derived here.

## The dual and the LMP difference

Write P_s = f + a·ℓ for the power that enters the line at the sending
bus 1 and P_r = f − (1−a)·ℓ for the power it delivers at bus 2, with
a = ½ (split allocation) and s = dℓ/df the marginal loss at the
operating point.  One more MW of f is worth

  V = π₂·(1 − (1−a)·s) − π₁·(1 + a·s).

If the limit is h(f) ≤ T, its multiplier is μ = V / h′(f):

| Mode | h(f) | μ | Loss part of π₂ − π₁ |
|------|------|---|----------------------|
| lossless | f | π₂ − π₁ | 0 |
| `flow` | f | V | s·((1−a)·π₂ + a·π₁) |
| `sending` | f + a·ℓ | V / (1 + a·s) = π₂·r − π₁ | π₂·(1 − r) |
| `full` | f + ℓ | V / (1 + s) | (π₂ − π₁) − μ |

with r = dP_r/dP_s = (1 − (1−a)·s) / (1 + a·s), the MW delivered per
MW injected at the sending end.  Only `sending` gives a dual that does
not depend on how the loss is booked between the buses (`a`): it is the
textbook split π₂ − π₁ = μ + π₂·(1 − r), congestion plus marginal loss
referred to the sending-end injection (Litvinov et al. 2004).  Under the
default `receiver` allocation (a = 0) `flow` and `sending` are the same
row.

The tariff income of the line, IT = π₂·P_r − π₁·P_s, splits exactly into
an FTR-style congestion rent μ·T and a loss rent.  For `sending`, with
P_s = T at the limit,

  IT = μ·T + π₂·(P_r − r·P_s),

and for any mode IT = V·f + (s·f − ℓ)·((1−a)·π₂ + a·π₁).  Both
identities are asserted in `test_line_thermal_limit.cpp`.

Under `flow` the limit f ≤ T is implied by the segment bounds (the K
widths add up to T), so no row or column of the output carries μ:
`flow_cost` is empty for this line.  `tolls.py` computes it as V.  Under
`sending` / `full` it is `thermalp_dual` (reported ≤ 0).

## Results (HiGHS 1.16)

Per block (blocks 2 and 3 congested; π in $/MWh, flows in MW):

| Mode | Blk | π₁ | π₂ | f | ℓ | P_s | P_r | μ | Loss part |
|------|-----|----|----|---|---|-----|-----|---|-----------|
| lossless | 2 | 10 | 30 | 100 | 0 | 100 | 100 | 20.00 | 0 |
| lossless | 3 | 10 | 50 | 100 | 0 | 100 | 100 | 40.00 | 0 |
| `flow` | 1 | 10 | 11.33 | 62.0 | 4.0 | 64.0 | 60.0 | 0 | 1.33 |
| `flow` | 2 | 10 | 30 | 100 | 10.0 | **105.0** | 95.0 | 16.50 | 3.50 |
| `flow` | 3 | 10 | 50 | 100 | 10.0 | **105.0** | 95.0 | 34.75 | 5.25 |
| `sending` | 2 | 10 | 30 | 95.40 | 9.20 | **100.0** | 90.80 | 15.17 | 4.83 |
| `sending` | 3 | 10 | 50 | 95.40 | 9.20 | **100.0** | 90.80 | 31.95 | 8.05 |
| `full` | 2 | 10 | 30 | 91.49 | 8.51 | 95.74 | 87.23 | 14.04 | 5.96 |
| `full` | 3 | 10 | 50 | 91.49 | 8.51 | 95.74 | 87.23 | 29.57 | 10.43 |

(Block 1 is the same in the three lossy modes; its 1.33 $/MWh spread is
all loss.)  `flow` lets the sending end take 105 MW on a 100 MW line;
`sending` holds it at 100 MW; `full` at 95.7 MW.

Per year, with an assumed **VATT = 7.5 M$/yr** (a plausible AVI + COMA
for a 100 MW, 220 kV, ~100 km line; not data), and the peaje = VATT − IT
charged as a uniform cargo único per MWh withdrawn (user A 438 GWh,
user B 610 GWh):

| Mode | μ blk 2 / 3 [$/MWh] | IT [k$] | Congestion rent μ·T [k$] | Loss rent [k$] | Peaje [k$] | Cargo [$/MWh] | User A [k$] | User B [k$] |
|------|------|------|------|------|------|------|------|------|
| lossless | 20.00 / 40.00 | 1840.0 | 1840.0 | 0 | 5660.0 | 5.401 | 2365.5 | 3294.5 |
| `flow` | 16.50 / 34.75 | 1992.0 | 1546.0 | 446.0 | 5508.0 | 5.256 | 2302.0 | 3206.0 |
| `sending` | 15.17 / 31.95 | 1920.9 | 1421.6 | 499.3 | 5579.1 | 5.324 | 2331.7 | 3247.4 |
| `full` | 14.04 / 29.57 | 1860.4 | 1315.7 | 544.7 | 5639.6 | 5.381 | 2357.0 | 3282.6 |

Why the modes differ:

- **Dispatch.**  The more of the loss sits inside the limit, the less
  cheap energy crosses the line (delivered 95.0 → 90.8 → 87.2 MW in the
  congested blocks), so IT falls (1.99 → 1.92 → 1.86 M\$) and the peaje
  the users pay rises (5.51 → 5.58 → 5.64 M\$).
- **Attribution.**  The same IT is split differently.  μ is the value of
  one more MW of the *limited quantity*; a limit that contains more of
  the loss has a smaller μ (and a larger loss part).  The FTR-fundable
  congestion rent μ·T drops 15 % from `flow` to `full` while the loss
  rent grows 22 %.  `flow` understates the loss rent: half of the
  loss (under split allocation) is outside the limit and is paid as
  "congestion".
- **Lossless baseline.**  μ = π₂ − π₁ and IT = μ·T exactly; the loss
  rent is zero by construction.

Which mode to use: `sending`.  A thermal rating is a current limit,
and in a DC model (V ≈ 1 pu) the current at the sending end is the
sending-end power (`cases/ieee9b_line_limit` checks this against an AC
OPF).  Its dual is the congestion part of the price difference with
the loss referred to the sending-end injection, independent of how the
loss is booked between the buses.  This also fits Chilean practice:
the IT is computed with *measured* energies at both ends of the segment
and real marginal costs that include marginal losses (NT de
Coordinación y Operación, art. 3-44), so its total is fixed by prices
and physical flows; how the model writes the limit matters through the
dispatch and prices it produces, and through the split of IT into the
loss ("toll") and congestion components that art. 114 bis asks the
Coordinador to separate.  A model that lets the sending end exceed the
rating (`flow` with split or tangent allocation) overstates both the
transfer and the congestion component.

## How the money is used (summary of the sources)

- **Chile.**  Each segment earns its VATT = AVI + COMA (LGSE arts. 103,
  114) [1].  The *ingreso tarifario real* is the result of applying the
  real marginal costs to the segment's injections and withdrawals
  (art. 114) [1]; the NT computes it every 15 minutes from measured
  energies as the value delivered at one end minus the value taken at
  the other, with marginal costs that carry marginal-loss penalty
  factors, so it includes the loss surplus and the congestion rent [5].
  Since Ley 20.936 (2016) [2] the national and zonal systems are paid
  by end users through a *cargo único* that tops up the real IT to the
  VATT: per half-year, (½·VATT − IT of the previous half-year) /
  projected energy (art. 115; DS 10/2019 arts. 138, 148) [1][3].
  Before 2016 (Ley 19.940) [4] the trunk *peaje* was VATT − expected
  IT, paid 80 % by generators and 20 % by withdrawals inside the AIC.
  Art. 114 bis splits the real IT into a toll part at a reference
  (marginal-loss) level and a congestion part, reassigned to affected
  generators when works are late [1][6].  (Our inference: the loss
  surplus lowers the cargo único exactly like the congestion rent.)
- **US ISOs.**  PJM, MISO and CAISO price LMP = energy + congestion +
  marginal loss; ERCOT has no marginal-loss component [7].  Congestion
  charges (Σ μ·limit in a DC-OPF) fund FTR/CRR payouts (revenue
  adequacy) [8][9].  Marginal losses are about twice the average losses
  under quadratic losses; the over-collection is refunded to load (PJM
  Manual 28 §9.4 Transmission Loss Credits; CAISO marginal-loss offset
  to measured demand) [8][10].
- **Europe.**  Congestion income of market coupling (zonal price
  difference × cross-zonal flow, CACM art. 2(16), 73) [11] is earmarked
  first for firmness and cross-zonal capacity (Reg. 2019/943 art. 19)
  [12].  Losses are not priced in the zonal day-ahead market, except
  linear loss factors on some HVDC links [13]; the ITC mechanism
  compensates TSOs for losses of transit flows (Reg. 838/2010) [14].
- **Theory.**  Nodal spot prices and the merchandising surplus
  (Schweppe et al. 1988) [15]; contract networks and FTRs funded by
  congestion rents (Hogan 1992) [16]; marginal revenues recover only a
  small part of network cost, the "revenue reconciliation" problem that
  makes a residual charge (here the peaje) necessary (Pérez-Arriaga et
  al. 1995; Rubio-Odériz & Pérez-Arriaga 2000) [17][18]; LMP loss
  component and reference-bus dependence (Litvinov et al. 2004) [19].

## References

DOIs checked against Crossref; URLs opened.

1. DFL 4/2006, Ley General de Servicios Eléctricos, arts. 103, 114,
   114 bis, 115. https://www.bcn.cl/leychile/navegar?idNorma=258171
2. Ley 20.936 (2016), new transmission system and independent
   coordinator, transitory art. 25.
   https://www.bcn.cl/leychile/navegar?idNorma=1092695
3. DS 10/2019, Reglamento de calificación, valorización, tarificación y
   remuneración de las instalaciones de transmisión, arts. 138, 148.
   https://www.bcn.cl/leychile/navegar?idNorma=1146553
4. Ley 19.940 (2004), arts. 71-29, 71-30.
   https://www.bcn.cl/leychile/navegar?idNorma=222380
5. CNE, Norma Técnica de Coordinación y Operación (2021), def. 23,
   arts. 2-25, 3-44.
   https://www.cne.cl/wp-content/uploads/2021/08/NT-de-Coordinacion-y-Operacion-del-SEN.pdf
6. Coordinador Eléctrico Nacional, Minuta reasignación de ingresos
   tarifarios por art. 114° bis (2023).
   https://www.coordinador.cl/wp-content/uploads/2023/04/Minuta-aplicacion-114bis-ver.3.pdf
7. ERCOT, Study of the Benefits of Marginal Losses (2018).
   https://www.ercot.com/files/docs/2018/06/29/Study_of_the_Benefits_of_Marginal_Losses_FINAL.pdf
8. PJM Manual 28, Operating Agreement Accounting, rev. 104, §9.4.
   https://www.pjm.com/-/media/documents/manuals/m28.ashx
9. PJM Manual 06, Financial Transmission Rights, §8.5.
   https://www.pjm.com/-/media/documents/manuals/m06.ashx
10. CAISO, settlement configuration guide CC 8404, Day-Ahead Energy and
    Marginal Losses Offset.
11. Commission Regulation (EU) 2015/1222 (CACM), arts. 2(16), 73.
    https://eur-lex.europa.eu/eli/reg/2015/1222/oj
12. Regulation (EU) 2019/943, art. 19.
    https://eur-lex.europa.eu/eli/reg/2019/943/oj
13. A. Tosatto, T. Weckesser, S. Chatzivasileiadis, "Market Integration
    of HVDC Lines: Internalizing HVDC Losses in Market Clearing", IEEE
    Trans. Power Syst. 35(1), 451–461, 2020. DOI 10.1109/TPWRS.2019.2932184.
14. Commission Regulation (EU) 838/2010, Annex Part A.
    https://eur-lex.europa.eu/eli/reg/2010/838/oj
15. F. C. Schweppe, M. C. Caramanis, R. D. Tabors, R. E. Bohn, *Spot
    Pricing of Electricity*, Kluwer, 1988. DOI 10.1007/978-1-4613-1683-1.
16. W. W. Hogan, "Contract networks for electric power transmission",
    J. Regulatory Economics 4(3), 211–242, 1992. DOI 10.1007/BF00133621.
17. I. J. Pérez-Arriaga, F. J. Rubio, J. F. Puerta, J. Arceluz, J. Marín,
    "Marginal pricing of transmission services: an analysis of cost
    recovery", IEEE Trans. Power Syst. 10(1), 546–553, 1995.
    DOI 10.1109/59.373981.
18. F. J. Rubio-Odériz, I. J. Pérez-Arriaga, "Marginal pricing of
    transmission services: a comparative analysis of network cost
    allocation methods", IEEE Trans. Power Syst. 15(1), 448–454, 2000.
    DOI 10.1109/59.852158.
19. E. Litvinov, T. Zheng, G. Rosenwald, P. Shamsollahi, "Marginal Loss
    Modeling in LMP Calculation", IEEE Trans. Power Syst. 19(2),
    880–888, 2004. DOI 10.1109/TPWRS.2004.825894.
20. H. Li, L. Tesfatsion, "ISO Net Surplus Collection and Allocation in
    Wholesale Power Markets Under LMP", IEEE Trans. Power Syst. 26(2),
    627–641, 2011. DOI 10.1109/TPWRS.2010.2059052.
