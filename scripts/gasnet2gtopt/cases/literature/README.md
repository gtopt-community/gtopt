# Literature examples

Worked examples and benchmarks from the literature cited in
`../../README.md`, used as unit, reference-LP and optional e2e tests
(`tests/test_literature.py`, `tests/test_literature_e2e.py`).  Only numeric
data is stored here; no text is copied from the sources.

Units used throughout: flow in km3/h (standard, 15 C / 1.01325 bar),
pressure in bar abs, `K` in bar^2/(km3/h)^2 for `p_a^2 - p_b^2 = K f|f|`.

| Conversion | Value |
|------------|-------|
| 1 Mm3/day | 41.6667 km3/h |
| Saldarriaga `Psi^2 = C^2 (rho_in^2 - rho_out^2)` | `K = 1 / C^2 / 41.6667^2` |
| GasLib flow `1000m_cube_per_hour` | 1 km3/h (no conversion) |
| GasModels mass flow kg/s | `q [km3/h] = 3.6 m / rho_std`, `rho_std = p_std M / (R T_std)` = 0.78669 kg/m3 (M = 0.0186 kg/mol) |
| GasModels pressure Pa | / 1e5 -> bar |

## Status

| Example | Source | Data | Status |
|---------|--------|------|--------|
| 4-node pipe chain | Saldarriaga PhD, Fig. 2.2(b) | complete | `saldarriaga_phd_fig2_2.gasnet.json` |
| Compressor fuel (2.6), (2.29), (2.70)-(2.72) | Saldarriaga PhD | equations + 3-5 % at beta = 2 | unit tests |
| GasLib-11 | Schmidt et al. 2017 | complete (CC BY 3.0) | `gaslib11_radial.gasnet.json` (valve V01 closed) |
| GasLib-24 | Schmidt et al. 2017 | complete | meshed (1 loop; resistor, shortPipe, controlValve): out of scope for the radial model |
| GasLib-40 | Schmidt et al. 2017 | complete | meshed (45 arcs / 40 nodes, 6 loops): out of scope for the radial model |
| Belgian network | De Wolf & Smeers 2000, via GasModels.jl `A1.m` | complete (BSD-3) | `belgian_a1_radial.gasnet.json` (parallels merged) |
| 3-bus electric-gas planning | Saldarriaga PhD, Tables 3.8-3.10 | partial; rest "available from the authors" | not reproducible |
| IEEE-24 + Belgian coupled | Saldarriaga PhD, Table 3.11 | partial | not reproducible |
| Distribution planning | Saldarriaga MSc 2013 | figures only | not reproducible |
| 4- and 22-node storage cases | Martinez 2022 | figures only | not reproducible |
| LNG chain | Saldarriaga et al. book 2021; Ibarra 2019 | no network / fuel data | not applicable |
| Coupled cases (Liu-Shahidehpour, Correa-Posada, Ordoudis) | journal papers | paywalled / not open | not reproducible |

## Saldarriaga PhD (UTP, Pereira, 2018)

C. A. Saldarriaga Cortes, *Planeamiento integrado de sistemas energeticos:
un enfoque estocastico multietapa*, PhD thesis, Universidad Tecnologica de
Pereira, 2018 (title page; repository record dated 2019),
<https://hdl.handle.net/11059/10887>.  (Printed page numbers.)

**Fig. 2.2(b), pp. 25-26, sec. 2.3.1.**  Three identical pipes in series,
`C = 0.32 Mm3/(day bar)`, node 1 at 90 bar; the figure gives the node
pressures 90 / 70 / 50 / 30 bar (node 4 at its 30 bar lower limit) and the
flows 18.1 / 15.7 / 12.8 Mm3/day.  Encoded as:

- `K = 1/0.32^2 = 9.766 bar^2/(Mm3/d)^2 = 0.005625 bar^2/(km3/h)^2`;
- demands are the flow differences implied by the published pressures:
  2.425 / 2.877 / 12.800 Mm3/d (the published flows are rounded to 0.1);
- 32 tangents up to 20 Mm3/d per pipe: the envelope under-states the
  squared-pressure drop by at most `K (F/2N)^2 = 9.766 (20/64)^2 = 0.95
  bar^2` per pipe, i.e. < 0.05 bar at node 4 -- the tolerance used.

**Compressor, eqs. (2.5)-(2.6) p. 27, (2.29) p. 31, (2.70)-(2.72) p. 38.**
No numeric example; the tests check the printed partial derivatives
against numeric derivatives of (2.70), that (2.71) and (2.72) are the same
plane, that our pi-plane (`gasnet2gtopt.saldarriaga_coeffs`) matches it,
and the "3-5 % of the compressed gas at nominal conditions" statement
(p. 31, with nominal beta = 2 of Fig. 2.7), which brackets
`gamma in [0.173, 0.289]` for `alpha = 0.2308`.

## GasLib (`gaslib/`)

M. Schmidt et al., "GasLib -- A Library of Gas Network Instances", *Data*
2(4):40, 2017, doi:10.3390/data2040040; data from
<https://gaslib.zib.de> (`GasLib-{11,24,40}-v1-20211130`), licensed CC BY
3.0.  As the file headers require, cite: M. E. Pfetsch et al., "Validation
of Nominations in Gas Network Optimization: Models, Methods, and
Solutions", ZIB-Report 12-41, 2012.  The `.net` / `.scn` files are copied
unmodified.

GasLib-11 (nomination_1: entries 160 / 140 / 0, exits 100 / 120 / 80
km3/h) is radial once valve `V01_N01_N03` is closed (closing it is a valid
GasLib control).  Pipe constants: Darcy-Weisbach with the Nikuradse
friction factor `lambda = (2 log10(D/k) + 1.138)^-2` (0.013725 for D = 500
mm, k = 0.1 mm), Papay `z` at the mean bound pressure 55 bar (0.8819),
T = 10 C, M = 18.5674 g/mol, rho_std = 0.785 kg/m3; this K is 0.9 % above
our Weymouth K (E = 0.92).  Compressor max ratio = pressureOutMax /
pressureInMin = 70 / 40.  No published solution exists for this
nomination; the tests check the flows forced by the tree, feasibility of
every pressure bound, and the exact Weymouth drop on the LP flows.

## Belgian network (`belgian/`)

D. De Wolf and Y. Smeers, "The gas transmission problem solved by an
extension of the simplex algorithm", *Management Science* 46(11):1454-1465,
2000.  Data as encoded in GasModels.jl
(<https://github.com/lanl-ansi/GasModels.jl>, `examples/data/matgas/A1.m`,
BSD-3 licence, `belgian/LICENSE-GasModels.md`); the file is copied
unmodified.

Radial reduction (`scripts/literature.py`):

- the 5 parallel pipe pairs (1/2, 3/4, 12/13, 14/15, 101/111) become one
  pipe each with `1/sqrt(K_eq) = sum 1/sqrt(K_i)`, the two Voeren
  compressors one station: 24 nodes, 19 pipes, 4 compressors, a tree;
- expansion candidates (`ne_pipe`) and the 2 junctions only they reach
  are dropped;
- `K` from the GasModels resistance `p_i^2 - p_j^2 = lambda L a^2 /
  (D A^2) m|m|` (a = 317.35 m/s; De Wolf-Smeers friction `lambda = 0.007-0.0086`,
  0.007 for the large pipes), converted with rho_std above; the result is 0.564
  times our Weymouth K for the 890 mm pipes;
- the fixed receipts / deliveries take their nominal values; Zeebrugge
  (the only dispatchable receipt) closes the balance at 127.55 kg/s.

Under this nomination the base network cannot keep Blaregnies at its 50 bar
floor (A1 is an expansion-planning instance): the LP sheds 6.39 km3/h
there, the only shortfall.  De Wolf & Smeers' published solution is not
openly available, so the tests check the structure, the merged constants,
the shedding pattern and the exact-Weymouth consistency.

## Files

| File | Content |
|------|---------|
| `saldarriaga_phd_fig2_2.gasnet.json` | hand-encoded chain |
| `gaslib11_radial.gasnet.json`, `belgian_a1_radial.gasnet.json` | generated by `python scripts/literature.py` (a test checks they are current) |
| `expected.json` | reference-LP results (`python tests/make_literature_expected.py`) |
| `gaslib/`, `belgian/` | original data, unmodified, with their licences |
