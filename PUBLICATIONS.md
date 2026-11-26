# Publications

Papers describing GTOpt and its predecessors (PLP, FESOP), the methods
by the authors that GTOpt implements, applications, presentations, the
third-party references GTOpt builds on or is validated against, and studies
that use GTOpt.  Metadata is checked on Crossref; items without a DOI come
from the first author's ResearchGate profile.

To cite the software itself, use [`CITATION.cff`](CITATION.cff) (GitHub's
"Cite this repository" button).  If you publish work that uses GTOpt,
please open a pull request adding it to the last section.

## White paper

**GTOpt: Open-Source Energy-System Optimization, Consistent from Minutes
to Decades.**
Marcelo Matus A., María del Pilar Buitrago Villada, Erick Sierra Baeza,
Carlos Enrique García Bujanda, Catalina López, Marcos Falcone Salazar and
Gonzalo Bustos Turu.
*engrXiv* preprint, 2026.
DOI: [10.31224/8872](https://doi.org/10.31224/8872) ·
PDF: [`docs/white_paper/main.pdf`](docs/white_paper/main.pdf) ·
LaTeX source and build instructions: [`docs/white_paper/`](docs/white_paper/) ·
Licence: CC BY 4.0.

> The reference paper for this repository: the complete formulation, the
> monolithic, SDDP and cascade methods and their coupling across time
> scales, the software architecture, validation on public test systems,
> and a comparison with the Chilean daily schedule computed by the system
> operator with PLEXOS.

```bibtex
@misc{matus2026gtopt,
  author       = {Matus A., Marcelo and
                  Buitrago Villada, Mar{\'i}a del Pilar and
                  Sierra Baeza, Erick and
                  Garc{\'i}a Bujanda, Carlos Enrique and
                  L{\'o}pez, Catalina and
                  Falcone Salazar, Marcos and
                  Bustos Turu, Gonzalo},
  title        = {{GTOpt}: Open-Source Energy-System Optimization,
                  Consistent from Minutes to Decades},
  year         = {2026},
  howpublished = {engrXiv preprint},
  doi          = {10.31224/8872},
  url          = {https://github.com/gtopt-community/gtopt/tree/master/docs/white_paper},
}
```

## FESOP

GTOpt is the open-source successor of **FESOP**, the expansion and
operation planning framework developed at Black Bear Engineering.

**Optimal Expansion and Reliable Renewable Energy Integration in Long-Term
Planning Using FESOP.**
María del Pilar Buitrago Villada, Carlos Enrique García Bujanda, Erick
Sierra Baeza and Marcelo Matus.
*2022 IEEE Kansas Power and Energy Conference (KPEC)*, Manhattan, KS,
USA, April 2022, pp. 1–5.
DOI:
[10.1109/KPEC54747.2022.9814781](https://doi.org/10.1109/KPEC54747.2022.9814781)
·
Open access: [ResearchGate](https://www.researchgate.net/publication/358423681_Optimal_Expansion_and_Reliable_Renewable_Energy_Integration_in_Long-Term_Planning_Using_FESOP)

> Presents the FESOP framework on which GTOpt is based and applies it to
> the optimal expansion of the Aysén electric system (Chile) over a
> 30-year horizon with four demand-growth scenarios, including renewable
> integration with spinning-reserve requirements and battery storage.

```bibtex
@inproceedings{buitrago2022fesop,
  author    = {Buitrago Villada, Mar{\'i}a del Pilar and
               Garc{\'i}a Bujanda, Carlos Enrique and
               Sierra Baeza, Erick and
               Matus, Marcelo},
  title     = {Optimal Expansion and Reliable Renewable Energy Integration
               in Long-Term Planning Using {FESOP}},
  booktitle = {2022 IEEE Kansas Power and Energy Conference (KPEC)},
  address   = {Manhattan, KS, USA},
  pages     = {1--5},
  year      = {2022},
  doi       = {10.1109/KPEC54747.2022.9814781},
}
```

## Methods behind GTOpt models

Papers by the authors whose methods GTOpt implements.

### Chebyshev continuous-time operation

**Chebyshev Based Continuous Time Power System Operation Approach.**
M. Matus, N. Cáceres, S. Puschel-Lovengreen and R. Moreno.
*2015 IEEE Power & Energy Society General Meeting*, pp. 1–5, 2015.
DOI:
[10.1109/PESGM.2015.7286570](https://doi.org/10.1109/PESGM.2015.7286570)
·
Open access: [ResearchGate][rg-cheby]

> Represents the operating trajectories of the power system as
> continuous-time functions sampled at Chebyshev nodes instead of
> piecewise-constant hourly blocks, so ramps and fast load and renewable
> variations are captured with far fewer time points.  In GTOpt it is the
> basis of the point-sampled stages and the typical-week Chebyshev
> sampling used in expansion planning
> ([`docs/methods/typical-week-sampling.md`](docs/methods/typical-week-sampling.md)).

```bibtex
@inproceedings{matus2015chebyshev,
  author    = {Matus, M. and
               C{\'a}ceres, N. and
               Puschel-Lovengreen, S. and
               Moreno, R.},
  title     = {Chebyshev Based Continuous Time Power System Operation
               Approach},
  booktitle = {2015 IEEE Power \& Energy Society General Meeting},
  pages     = {1--5},
  year      = {2015},
  doi       = {10.1109/PESGM.2015.7286570},
}
```

### Dynamic line rating: critical spans

**Identification of Critical Spans for Monitoring Systems in Dynamic
Thermal Rating.**
M. Matus, D. Sáez, M. Favley, C. Suazo-Martínez, J. Moya,
G. Jiménez-Estévez, R. Palma-Behnke, G. Olguín and P. Jorquera.
*IEEE Transactions on Power Delivery*, vol. 27, no. 2, pp. 1002–1009,
April 2012.
DOI:
[10.1109/TPWRD.2012.2185254](https://doi.org/10.1109/TPWRD.2012.2185254)
·
Open access: [ResearchGate][rg-dlr],
[PDF (Universidad de Chile)][uchile-dlr]

> Computes the IEEE 738 thermal rating of every span of an overhead line
> from local weather (wind speed and direction, temperature, solar
> radiation) and identifies the few critical spans that limit the line,
> where dynamic-thermal-rating monitors should be installed.  It is the
> basis of GTOpt's dynamic line rating input, built by `dlr2gtopt`
> ([`docs/scripts/dlr2gtopt.md`](docs/scripts/dlr2gtopt.md)).

```bibtex
@article{matus2012critical,
  author  = {Matus, M. and
             S{\'a}ez, D. and
             Favley, M. and
             Suazo-Mart{\'i}nez, C. and
             Moya, J. and
             Jim{\'e}nez-Est{\'e}vez, G. and
             Palma-Behnke, R. and
             Olgu{\'i}n, G. and
             Jorquera, P.},
  title   = {Identification of Critical Spans for Monitoring Systems in
             Dynamic Thermal Rating},
  journal = {IEEE Transactions on Power Delivery},
  volume  = {27},
  number  = {2},
  pages   = {1002--1009},
  year    = {2012},
  doi     = {10.1109/TPWRD.2012.2185254},
}
```

[rg-cheby]: https://www.researchgate.net/publication/273758902_Chebyshev_Based_Continuous_Time_Power_System_Operation_Approach
[rg-dlr]: https://www.researchgate.net/publication/254057820_Identification_of_Critical_Spans_for_Monitoring_Systems_in_Dynamic_Thermal_Rating
[uchile-dlr]: https://www.cec.uchile.cl/~dsaez/wp-content/uploads/2011/09/06163401.pdf

### Hydrothermal coordination with irrigation constraints

**Optimizing Hydrothermal Scheduling with Non-Convex Irrigation
Constraints: Case on the Chilean Electricity System.**
E. Pereira-Bonvallet, S. Püschel-Løvengreen, M. Matus and R. Moreno.
*Energy Procedia*, vol. 87, pp. 132–140, 2016.
DOI:
[10.1016/j.egypro.2015.12.342](https://doi.org/10.1016/j.egypro.2015.12.342)

> Hydrothermal coordination with the non-convex irrigation agreements of
> Chilean reservoirs, solved inside SDDP.  Used in GTOpt's SDDP
> hydrothermal coordination
> ([`docs/methods/sddp.md`](docs/methods/sddp.md) §8.1) and the irrigation
> agreements ([`docs/irrigation-agreements.md`](docs/irrigation-agreements.md)).

```bibtex
@article{pereira2016hydrothermal,
  author  = {Pereira-Bonvallet, Eduardo and
             P{\"u}schel-L{\o}vengreen, Sebastian and
             Matus, Marcelo and
             Moreno, Rodrigo},
  title   = {Optimizing Hydrothermal Scheduling with Non-Convex Irrigation
             Constraints: Case on the Chilean Electricity System},
  journal = {Energy Procedia},
  volume  = {87},
  pages   = {132--140},
  year    = {2016},
  doi     = {10.1016/j.egypro.2015.12.342},
}
```

### SDDP with binary and linear security constraints

**Binary and Linear Security Constraints in SDDP: Modeling Ralco
Reservoir Operation to Avoid Landslide Events.**
M. Matus, C. Toro and S. Püschel-Løvengreen.
*International Conference on Stochastic Programming (ICSP)*, 2016.
Full text: [ResearchGate][rg-matus]

> Reservoir operating rules with binary and linear security constraints
> inside SDDP.  Used in GTOpt's SDDP reservoir operating rules
> ([`docs/methods/sddp.md`](docs/methods/sddp.md)).

### Irrigation agreements in the Laja basin

**Modeling Integrated Water Resource Allocation for Agriculture and
Economical Energy Production in the Laja Lake Basin Considering New
Irrigation Policies.**
M. Matus, R. Sepúlveda, E. Sierra et al.
*4th International Conference on Energy and Environment (ICEE)*, 2019.
Full text: [ResearchGate][rg-matus]

> Joint water allocation for irrigation and hydropower under the Laja
> agreement.  Used in GTOpt's irrigation agreements
> ([`docs/irrigation-agreements.md`](docs/irrigation-agreements.md)).

[rg-matus]: https://www.researchgate.net/profile/Marcelo-Matus-2/research

## Applications by the authors

- M. Matus Acuña, G. Bustos-Turu, T. Cox Oettinger, M. Zamora Zapata,
  W. Calderón-Muñoz, M. Buitrago-Villada, C. García Bujanda,
  L. Reyes-Chamorro, L. Vera Ortega, F. Peña-Cortés and E. Fernández Soto,
  "Assessing Sustainable Electromobility Futures in a Multi-Model Platform
  with Grid and Urban Planning: A Case Study in Chile," *Journal of
  Sustainable Development of Energy, Water and Environment Systems*,
  vol. 14, no. 3, 1140723, 2026.
  DOI: [10.13044/j.sdewes.d14.0723](https://doi.org/10.13044/j.sdewes.d14.0723)
- E. Sierra, C. Benavides and M. Matus, "Cost-Benefit Analysis of Small
  Distributed Generation in the National Electric System," *IEEE CHILECON*,
  pp. 1–6, 2019.
  DOI: [10.1109/CHILECON47746.2019.8987678](https://doi.org/10.1109/CHILECON47746.2019.8987678)
- C. Benavides, M. Matus, E. Sierra, R. Sepúlveda, A. M. Ruz and
  F. Gallardo, "Value contribution of solar plants to the Chilean electric
  system," *AIP Conference Proceedings*, vol. 2126, 170001, 2019
  (SolarPACES 2018).
  DOI: [10.1063/1.5117671](https://doi.org/10.1063/1.5117671)
- C. Benavides, R. Alvarez, R. Torres, R. Moreno, M. Matus, D. Muñoz,
  J. M. Gonzalez, G. Jiménez-Estévez and R. Palma-Behnke, "Capacity
  payment allocation in hydrothermal power systems with high shares of
  renewable energies," *E3S Web of Conferences*, vol. 140, 11008, 2019.
  DOI: [10.1051/e3sconf/201914011008](https://doi.org/10.1051/e3sconf/201914011008)
- L. Vargas, T. González, M. Gutiérrez, P. Guzmán and M. Matus, "Geothermal
  energy in electricity markets and decarbonisation scenarios: the Chilean
  case," *IOP Conference Series: Earth and Environmental Science*, vol. 188,
  012035, 2018.
  DOI: [10.1088/1755-1315/188/1/012035](https://doi.org/10.1088/1755-1315/188/1/012035)
- M. Matus and G. Bustos-Turu, "Renewable energy integration into the
  planning and operation of urban transport systems," *1st Workshop in
  Strategies for Solar Energy and Basic Sciences Research*, SERC, 2014.
- R. Rebolledo, S. Ríos, R. Trigo and M. Matus, "Stochastic modeling of
  street lamps operation," *IEEE Transactions on Power Systems*, vol. 11,
  no. 3, pp. 1482–1488, 1996.
  DOI: [10.1109/59.535689](https://doi.org/10.1109/59.535689)

The full reference list of the formulation, including the methods GTOpt
builds on, is in
[`docs/formulation/mathematical-formulation.md`](docs/formulation/mathematical-formulation.md#9-references).

## Presentations

Slides: [ResearchGate][rg-matus].

| Date | Presentation | Topic |
|------|--------------|-------|
| 2022-08 | M. Matus and M. P. Buitrago Villada, "MTPES Sesión 1," MTPES | expansion planning |
| 2019-05 | M. Matus, C. Matamala, E. Osses, …, B. Silva, "Accelerating coal displacement by scaling integrated solar districts in high solar radiation regions and boosting carbon revenues global impact," Applied Energy A+B Symposium (AEAB2019), MIT | applications |
| 2019-05 | M. Matus, "Modeling integrated water resource allocation for agriculture and economical energy production in the Laja Lake basin considering new irrigation policies," ICEE 2019 | irrigation agreements |
| 2018-10 | M. Matus and C. Benavides, "Value contribution of solar plants to the Chilean electric system," SolarPACES 2018 | applications |
| 2018-05 | D. Azócar, C. Gajardo, M. Matus, …, R. Sepúlveda, "Aplicación de la formulación de Chebyshev a la coordinación hidrotérmica de corto, mediano y largo plazo: resultados caso de estudio SEN 2018–2025," Seminario Centro de Energía (closing of the project on continuous-time operation with the Chebyshev pseudo-spectral representation) | Chebyshev, hydrothermal |
| 2018-01 | M. Matus and R. Sepúlveda, "Programación de mercados eléctricos mediante formulación en tiempo continuo en el espacio de Chebyshev," Seminario ECODIE | Chebyshev |
| 2017-03 | M. Matus and R. Sepúlveda, "Herramientas para la planificación hidrotérmica. Modelos de corto y largo plazo, caso Colombia," Consejo Nacional de Operación, Bogotá | hydrothermal planning |
| 2017-01 | M. Matus and R. Sepúlveda, "LNG integration in electricity market and the complementary operation with hydro energy," Curso-Seminario ECODIE | LNG, hydrothermal |
| 2016-07 | M. Matus, "PLP v2.0: programación de largo plazo de sistemas hidroeléctricos," Seminario CIGRE | PLP / SDDP |
| 2015-09 | M. Matus, R. Moreno, R. Palma-Behnke, …, S. Püschel-Løvengreen, "Hydrothermal coordination model with endogenous irrigation constraints: application to the Chilean Central Interconnected System," 5th Int. Workshop on Hydro Scheduling in Competitive Electricity Markets, Trondheim | hydrothermal, irrigation |
| 2015-09 | E. Pereira-Bonvallet, S. Püschel-Løvengreen, M. Matus and R. Moreno, "Optimizing hydrothermal scheduling with non-convex irrigation constraints: case on the Chilean electricity system," same workshop | hydrothermal, irrigation |
| 2015-09 | C. Matamala, E. Pereira-Bonvallet and M. Matus, "Operación económica y coordinación hidrotérmica del sistema SIC–SING," ECODIE | hydrothermal |

## Third-party references

Papers, standards, datasets and tools by other authors that GTOpt implements,
builds on or is validated against, grouped by area.  The same references, with
the context in which each is used, are in the white paper bibliography
([`docs/white_paper/references.bib`](docs/white_paper/references.bib)) and in
the formulation reference list
([`docs/formulation/mathematical-formulation.md`](docs/formulation/mathematical-formulation.md#9-references)).

### Hydrothermal scheduling and SDDP

Methods behind the `sddp` and `cascade` planning methods, and the SDDP
benchmarks GTOpt is validated against.

- M. V. F. Pereira and L. M. V. G. Pinto, "Multi-Stage Stochastic Optimization
  Applied to Energy Planning," *Math. Programming*, vol. 52, pp. 359–375, 1991.
  DOI: [10.1007/BF01582895](https://doi.org/10.1007/BF01582895)
- A. Shapiro, "Analysis of Stochastic Dual Dynamic Programming Method," *Eur. J.
  Oper. Res.*, vol. 209, no. 1, pp. 63–72, 2011. DOI:
  [10.1016/j.ejor.2010.08.007](https://doi.org/10.1016/j.ejor.2010.08.007)
- A. B. Philpott and V. L. de Matos, "Dynamic Sampling Algorithms for
  Multi-Stage Stochastic Programs with Risk Aversion," *Eur. J. Oper. Res.*,
  vol. 218, no. 2, pp. 470–483, 2012. DOI:
  [10.1016/j.ejor.2011.10.056](https://doi.org/10.1016/j.ejor.2011.10.056)
- G. Infanger and D. P. Morton, "Cut Sharing for Multistage Stochastic Linear
  Programs with Interstage Dependency," *Math. Program.*, vol. 75, no. 2, pp.
  241–256, 1996. DOI: [10.1007/BF02592154](https://doi.org/10.1007/BF02592154)
- J. R. Birge and F. Louveaux, "Introduction to Stochastic Programming,"
  Springer, 2011. DOI:
  [10.1007/978-1-4614-0237-4](https://doi.org/10.1007/978-1-4614-0237-4)
- G. Leclercq, M. F. Anjos and D. Aloise, "Batch Learning SDDP for Long-Term
  Hydrothermal Planning," *IEEE Trans. Power Syst.*, vol. 38, no. 5, pp.
  4845–4857, 2023. DOI:
  [10.1109/TPWRS.2023.3244389](https://doi.org/10.1109/TPWRS.2023.3244389)
- J. Fletcher, T. W. K. Mak and C. T. Maravelias, "Stochastic Dual Dynamic
  Programming for Optimal Power Flow Problems under Uncertainty," *Eur. J. Oper.
  Res.*, 2024. DOI:
  [10.1016/j.ejor.2024.09.045](https://doi.org/10.1016/j.ejor.2024.09.045)
- O. Dowson and L. Kapelevich, "SDDP.jl: A Julia Package for Stochastic Dual
  Dynamic Programming," *INFORMS J. Comput.*, vol. 33, no. 1, pp. 27–33, 2021.
  DOI: [10.1287/ijoc.2020.0987](https://doi.org/10.1287/ijoc.2020.0987)
- PSR, "PSRClassesInterface.jl: sample SDDP cases," 2024. [link][psrclasses]
- L. Cambier, "FAST: Finally an SDDP Toolbox," 2017. [link][cambier_fast]

### Unit commitment and electricity pricing

Tight UC formulations implemented in GTOpt, the pricing and uplift methods, and
the UC benchmarks used for validation.

- G. Morales-España, J. M. Latorre and A. Ramos, "Tight and Compact MILP
  Formulation for the Thermal Unit Commitment Problem," *IEEE Trans. Power
  Syst.*, vol. 28, no. 4, pp. 4897–4908, 2013. DOI:
  [10.1109/TPWRS.2013.2251373](https://doi.org/10.1109/TPWRS.2013.2251373)
- G. Morales-España, J. M. Latorre and A. Ramos, "Tight and Compact MILP
  Formulation of Start-Up and Shut-Down Ramping in Unit Commitment," *IEEE
  Trans. Power Syst.*, vol. 28, no. 2, pp. 1288–1296, 2013. DOI:
  [10.1109/TPWRS.2012.2222938](https://doi.org/10.1109/TPWRS.2012.2222938)
- G. Morales-España, C. Gentile and A. Ramos, "Tight MIP Formulations of the
  Power-Based Unit Commitment Problem," *OR Spectrum*, vol. 37, no. 4, pp.
  929–950, 2015. DOI:
  [10.1007/s00291-015-0400-4](https://doi.org/10.1007/s00291-015-0400-4)
- G. Morales-España, R. Baldick, J. García-González and A. Ramos,
  "Power-Capacity and Ramp-Capability Reserves for Wind Integration in
  Power-Based UC," *IEEE Trans. Sustain. Energy*, vol. 7, no. 2, pp. 614–624,
  2016. DOI:
  [10.1109/TSTE.2015.2498399](https://doi.org/10.1109/TSTE.2015.2498399)
- G. Morales-España, L. Ramírez-Elizondo and B. F. Hobbs, "Hidden Power System
  Inflexibilities Imposed by Traditional Unit Commitment Formulations," *Applied
  Energy*, vol. 191, pp. 223–238, 2017. DOI:
  [10.1016/j.apenergy.2017.01.089](https://doi.org/10.1016/j.apenergy.2017.01.089)
- G. Morales-España and D. A. Tejada-Arango, "Modeling the Hidden Flexibility of
  Clustered Unit Commitment," *IEEE Trans. Power Syst.*, vol. 34, no. 4, pp.
  3294–3296, 2019. DOI:
  [10.1109/TPWRS.2019.2908051](https://doi.org/10.1109/TPWRS.2019.2908051)
- D. A. Tejada-Arango and G. Morales-España, "CUC-data: Data of the Clustered
  Unit Commitment Case Studies,". [link][cucdata]
- R. Philipsen, G. Morales-España, M. de Weerdt and L. de Vries, "Trading Power
  Instead of Energy in Day-Ahead Electricity Markets," *Applied Energy*, vol.
  233–234, pp. 802–815, 2019. DOI:
  [10.1016/j.apenergy.2018.09.205](https://doi.org/10.1016/j.apenergy.2018.09.205)
- B. Knueven, J. Ostrowski and J.-P. Watson, "On Mixed-Integer Programming
  Formulations for the Unit Commitment Problem," *INFORMS Journal on Computing*,
  vol. 32, no. 4, pp. 857–876, 2020. DOI:
  [10.1287/ijoc.2019.0944](https://doi.org/10.1287/ijoc.2019.0944)
- J. M. Morales et al., "Integrating Renewables in Electricity Markets:
  Operational Problems," Springer, 2013. DOI:
  [10.1007/978-1-4614-9411-9](https://doi.org/10.1007/978-1-4614-9411-9)
- L. Badesa, F. Teng and G. Strbac, "Pricing Inertia and Frequency Response with
  Diverse Dynamics in a Mixed-Integer Second-Order Cone Programming
  Formulation," *Applied Energy*, vol. 260, pp. 114334, 2020. DOI:
  [10.1016/j.apenergy.2019.114334](https://doi.org/10.1016/j.apenergy.2019.114334)
- R. P. O'Neill et al., "Efficient Market-Clearing Prices in Markets with
  Nonconvexities," *European J. Oper. Res.*, vol. 164, no. 1, pp. 269–285, 2005.
  DOI: [10.1016/j.ejor.2003.12.011](https://doi.org/10.1016/j.ejor.2003.12.011)
- P. R. Gribik, W. W. Hogan and S. L. Pope, "Market-Clearing Electricity Prices
  and Energy Uplift," Harvard University, John F. Kennedy School of Government,
  2007.
- B. Hua and R. Baldick, "A Convex Primal Formulation for Convex Hull Pricing,"
  *IEEE Trans. Power Syst.*, vol. 32, no. 5, pp. 3814–3823, 2017. DOI:
  [10.1109/TPWRS.2016.2637718](https://doi.org/10.1109/TPWRS.2016.2637718)
- A. S. Xavier et al., "UnitCommitment.jl: A Julia/JuMP Optimization Package for
  Security-Constrained Unit Commitment (Version 0.4)," 2024. DOI:
  [10.5281/zenodo.4269874](https://doi.org/10.5281/zenodo.4269874)
- National Renewable Energy Laboratory, "Sienna: PowerSimulations.jl,
  PowerSystemCaseBuilder.jl and PowerSystemsTestData,". [link][powersimulations]
- J. D. Lara et al., "PowerSystems.jl — A power system data management package
  for large scale modeling," *SoftwareX*, vol. 15, pp. 100747, 2021. DOI:
  [10.1016/j.softx.2021.100747](https://doi.org/10.1016/j.softx.2021.100747)

### Continuous-time operation and quadrature

Background of the point-sampled stages and the Chebyshev typical-week sampling.

- M. Parvania and A. Scaglione, "Unit Commitment With Continuous-Time Generation
  and Ramping Trajectory Models," *IEEE Trans. Power Syst.*, vol. 31, no. 4, pp.
  3169–3178, 2016. DOI:
  [10.1109/TPWRS.2015.2479644](https://doi.org/10.1109/TPWRS.2015.2479644)
- C. W. Clenshaw and A. R. Curtis, "A Method for Numerical Integration on an
  Automatic Computer," *Numerische Mathematik*, vol. 2, no. 1, pp. 197–205,
  1960. DOI: [10.1007/BF01386223](https://doi.org/10.1007/BF01386223)
- J. Waldvogel, "Fast Construction of the Fejér and Clenshaw–Curtis Quadrature
  Rules," *BIT Numerical Mathematics*, vol. 46, no. 1, pp. 195–202, 2006. DOI:
  [10.1007/s10543-006-0045-4](https://doi.org/10.1007/s10543-006-0045-4)
- L. N. Trefethen, "Is Gauss Quadrature Better than Clenshaw–Curtis?," *SIAM
  Review*, vol. 50, no. 1, pp. 67–87, 2008. DOI:
  [10.1137/060659831](https://doi.org/10.1137/060659831)

### Natural gas and LNG

Gas network, LNG supply-chain and gas–electric models implemented in GTOpt, and
the gas benchmarks used for validation.

- C. A. Saldarriaga Cortés, "Planeamiento Integrado de Sistemas Energéticos: Un
  Enfoque Estocástico Multietapa," PhD thesis, Universidad Tecnológica de
  Pereira, Doctorado en Ingeniería, 2018. [link][saldarriaga2018phd]
- C. A. Saldarriaga Cortés, "Modelos de optimización para la integración de los
  sectores de electricidad y gas natural," MSc thesis, Universidad Tecnológica
  de Pereira, Maestría en Ingeniería Eléctrica, 2013. [link][saldarriaga2013msc]
- C. A. Saldarriaga, R. A. Hincapié and H. Salazar, "A Holistic Approach for
  Planning Natural Gas and Electricity Distribution Networks," *IEEE Trans.
  Power Syst.*, vol. 28, no. 4, pp. 4052–4063, 2013. DOI:
  [10.1109/TPWRS.2013.2268859](https://doi.org/10.1109/TPWRS.2013.2268859)
- C. Saldarriaga-Cortés, H. Salazar, R. Moreno and G. Jiménez-Estévez,
  "Stochastic Planning of Electricity and Gas Networks: An Asynchronous Column
  Generation Approach," *Applied Energy*, vol. 233–234, pp. 1065–1077, 2019.
  DOI:
  [10.1016/j.apenergy.2018.09.148](https://doi.org/10.1016/j.apenergy.2018.09.148)
- A. M. Martínez Sánchez, "Gestión óptima de almacenamientos de tipo estacional
  en sistemas integrados," PhD thesis, Universidad Tecnológica de Pereira,
  Doctorado en Ingeniería, 2022. [link][martinez2022storage]
- J. E. Ibarra Vadillo, "Planeamiento holístico de la cadena de suministro de
  GNL considerando su impacto en los sectores de electricidad y transporte de
  carga pesada: un enfoque multiobjetivo," MSc thesis, Universidad Tecnológica
  de Pereira, Maestría en Ingeniería Eléctrica, 2019. [link][ibarra2019gnl]
- Á. Restrepo et al., "Gas Natural Licuado: Una Opción para la Transición
  Energética de Colombia," Universidad Tecnológica de Pereira, 2021. DOI:
  [10.22517/9789587225921](https://doi.org/10.22517/9789587225921)
- D. De Wolf and Y. Smeers, "The Gas Transmission Problem Solved by an Extension
  of the Simplex Algorithm," *Management Science*, vol. 46, no. 11, pp.
  1454–1465, 2000. DOI:
  [10.1287/mnsc.46.11.1454.12087](https://doi.org/10.1287/mnsc.46.11.1454.12087)
- E. S. Menon, "Gas Pipeline Hydraulics," CRC Press, 2005. DOI:
  [10.1201/9781420038224](https://doi.org/10.1201/9781420038224)
- A. Tomasgard, F. Rømo, M. Fodstad and K. Midthun, "Optimization Models for the
  Natural Gas Value Chain," *Geometric Modelling, Numerical Simulation, and
  Optimization*, pp. 521–558, 2007. DOI:
  [10.1007/978-3-540-68783-2_16](https://doi.org/10.1007/978-3-540-68783-2_16)
- R. Z. Ríos-Mercado and C. Borraz-Sánchez, "Optimization Problems in Natural
  Gas Transportation Systems: A State-of-the-Art Review," *Applied Energy*, vol.
  147, pp. 536–555, 2015. DOI:
  [10.1016/j.apenergy.2015.03.017](https://doi.org/10.1016/j.apenergy.2015.03.017)
- C. Borraz-Sánchez et al., "Convex Relaxations for Gas Expansion Planning,"
  *INFORMS J. Comput.*, vol. 28, no. 4, pp. 645–656, 2016. DOI:
  [10.1287/ijoc.2016.0697](https://doi.org/10.1287/ijoc.2016.0697)
- C. Liu, M. Shahidehpour, Y. Fu and Z. Li, "Security-Constrained Unit
  Commitment With Natural Gas Transmission Constraints," *IEEE Trans. Power
  Syst.*, vol. 24, no. 3, pp. 1523–1536, 2009. DOI:
  [10.1109/TPWRS.2009.2023262](https://doi.org/10.1109/TPWRS.2009.2023262)
- C. M. Correa-Posada and P. Sánchez-Martín, "Integrated Power and Natural Gas
  Model for Energy Adequacy in Short-Term Operation," *IEEE Trans. Power Syst.*,
  vol. 30, no. 6, pp. 3347–3355, 2015. DOI:
  [10.1109/TPWRS.2014.2372013](https://doi.org/10.1109/TPWRS.2014.2372013)
- R. Bent et al., "Joint Electricity and Natural Gas Transmission Planning With
  Endogenous Market Feedbacks," *IEEE Trans. Power Syst.*, vol. 33, no. 6, pp.
  6397–6409, 2018. DOI:
  [10.1109/TPWRS.2018.2849958](https://doi.org/10.1109/TPWRS.2018.2849958)
- C. Ordoudis, P. Pinson and J. M. Morales, "An Integrated Market for
  Electricity and Natural Gas Systems with Stochastic Power Producers,"
  *European J. Oper. Res.*, vol. 272, no. 2, pp. 642–654, 2019. DOI:
  [10.1016/j.ejor.2018.06.036](https://doi.org/10.1016/j.ejor.2018.06.036)
- C. Ordoudis, P. Pinson and J. M. Morales, "chrord/EL-NG-Data: Electronic
  Companion – An Integrated Market for Electricity and Natural Gas Systems with
  Stochastic Producers," 2018. DOI:
  [10.5281/zenodo.1174979](https://doi.org/10.5281/zenodo.1174979)
- A. Ratha et al., "Affine Policies for Flexibility Provision by Natural Gas
  Networks to Power Systems," *Electric Power Syst. Res.*, vol. 189, pp. 106565,
  2020. DOI:
  [10.1016/j.epsr.2020.106565](https://doi.org/10.1016/j.epsr.2020.106565)
- A. Ratha et al., "Affine Policies for Flexibility Provision by Natural Gas
  Networks to Power Systems (online appendix and data)," 2020. DOI:
  [10.5281/zenodo.3755120](https://doi.org/10.5281/zenodo.3755120)
- D. Hu and S. M. Ryan, "Stochastic vs. Deterministic Scheduling of a Combined
  Natural Gas and Power System with Uncertain Wind Energy," *Int. J. Electr.
  Power Energy Syst.*, vol. 108, pp. 303–313, 2019. DOI:
  [10.1016/j.ijepes.2018.12.047](https://doi.org/10.1016/j.ijepes.2018.12.047)
- D. Hu, "Datasets for Reliability Unit Commitment to Manage Natural Gas Cost in
  Power System Operations," 2019. DOI:
  [10.17632/ynb88g67xy.1](https://doi.org/10.17632/ynb88g67xy.1)
- M. Schmidt et al., "GasLib—A Library of Gas Network Instances," *Data*, vol.
  2, no. 4, pp. 40, 2017. DOI:
  [10.3390/data2040040](https://doi.org/10.3390/data2040040)
- M. E. Pfetsch et al., "Validation of Nominations in Gas Network Optimization:
  Models, Methods, and Solutions," *Optim. Methods Softw.*, vol. 30, no. 1, pp.
  15–53, 2015. DOI:
  [10.1080/10556788.2014.888426](https://doi.org/10.1080/10556788.2014.888426)
- R. Bent et al., "GasModels.jl: Steady-State Gas Network Optimization in
  Julia/JuMP,". [link][gasmodels]
- R. Bent, K. Sundar and B. Tasseff, "GasPowerModels.jl: Joint Optimization of
  Natural Gas and Power Transmission Networks in Julia/JuMP,".
  [link][gaspowermodels]

### Power flow, OPF and transmission expansion

DC power flow and loss approximations used by GTOpt, and the transmission-
expansion literature it builds on.

- B. Stott, J. Jardim and O. Alsac, "DC Power Flow Revisited," *IEEE Trans.
  Power Syst.*, vol. 24, no. 3, pp. 1290–1300, 2009. DOI:
  [10.1109/TPWRS.2009.2021235](https://doi.org/10.1109/TPWRS.2009.2021235)
- C. Coffrin and P. Van Hentenryck, "A Linear-Programming Approximation of AC
  Power Flows," *INFORMS Journal on Computing*, vol. 26, no. 4, pp. 718–734,
  2014. DOI: [10.1287/ijoc.2014.0594](https://doi.org/10.1287/ijoc.2014.0594)
- D. K. Molzahn and I. A. Hiskens, "A Survey of Relaxations and Approximations
  of the Power Flow Equations," *Found. Trends Electr. Energy Syst.*, vol. 4,
  no. 1–2, pp. 1–221, 2019. DOI:
  [10.1561/3100000012](https://doi.org/10.1561/3100000012)
- S. Frank, I. Steponavice and S. Rebennack, "Operations Research in Optimal
  Power Flow: A Guide to Recent and Emerging Developments," *Eur. J. Oper.
  Res.*, vol. 300, no. 3, pp. 893–914, 2022. DOI:
  [10.1016/j.ejor.2021.10.003](https://doi.org/10.1016/j.ejor.2021.10.003)
- R. Romero, A. Monticelli, A. Garcia and S. Haffner, "Test Systems and
  Mathematical Models for Transmission Network Expansion Planning," *IEE
  Proc.—Gener. Transm. Distrib.*, vol. 149, no. 1, pp. 27–36, 2002. DOI:
  [10.1049/ip-gtd:20020026](https://doi.org/10.1049/ip-gtd:20020026)
- S. Lumbreras and A. Ramos, "The New Challenges to Transmission Expansion
  Planning. Survey of Recent Practice and Literature Review," *Electr. Power
  Syst. Res.*, vol. 134, pp. 19–29, 2016. DOI:
  [10.1016/j.epsr.2015.10.013](https://doi.org/10.1016/j.epsr.2015.10.013)
- M. Mahdavi, C. S. Antunez, M. Ajalli and R. Romero, "Transmission Expansion
  Planning: Literature Review and Classification," *IEEE Syst. J.*, vol. 13, no.
  3, pp. 3129–3140, 2019. DOI:
  [10.1109/JSYST.2018.2871793](https://doi.org/10.1109/JSYST.2018.2871793)
- R. Hemmati, R.-A. Hooshmand and A. Khodabakhshian, "Comprehensive Review of
  Generation and Transmission Expansion Planning," *IET Gener. Transm.
  Distrib.*, vol. 7, no. 9, pp. 955–964, 2013. DOI:
  [10.1049/iet-gtd.2013.0031](https://doi.org/10.1049/iet-gtd.2013.0031)

### Storage expansion

Storage co-optimization models compared with GTOpt's storage expansion.

- R. S. Go, F. D. Munoz and J.-P. Watson, "Assessing the Economic Value of
  Co-Optimized Grid-Scale Energy Storage Investments in Supporting High
  Renewable Portfolio Standards," *Appl. Energy*, vol. 183, pp. 902–913, 2016.
  DOI:
  [10.1016/j.apenergy.2016.08.134](https://doi.org/10.1016/j.apenergy.2016.08.134)
- A. W. Frazier, A. Botterud, S. Ericson and L. Markel, "Integrated Expansion
  Planning of Electric Energy Generation, Transmission, and Storage for Handling
  High Shares of Wind and Solar Power Generation," *arXiv preprint*, 2023.

### Dynamic line rating

The conductor thermal model used by `dlr2gtopt`.

- IEEE, "IEEE Standard for Calculating the Current-Temperature Relationship of
  Bare Overhead Conductors," IEEE Std 738-2012, IEEE, 2013.

### Test systems and benchmark data

Public test systems GTOpt cases are built from and validated on.

- R. Christie, "Power Systems Test Case Archive," University of Washington,
  1993. [link][pstca]
- "IEEE 30 Bus Test Case," University of Washington, 1993. [link][ieee30bus]
- "IEEE 57 Bus Test Case," University of Washington, 1993. [link][ieee57bus]
- P. M. Anderson and A. A. Fouad, "Power Systems Control and Stability,"
  Wiley–IEEE Press, 2002.
- C. Grigg et al., "The IEEE Reliability Test System-1996," *IEEE Transactions
  on Power Systems*, vol. 14, no. 3, pp. 1010–1020, 1999. DOI:
  [10.1109/59.780914](https://doi.org/10.1109/59.780914)
- C. Barrows et al., "The IEEE Reliability Test System: A Proposed 2019 Update,"
  *IEEE Transactions on Power Systems*, vol. 35, no. 1, pp. 119–127, 2020. DOI:
  [10.1109/TPWRS.2019.2925557](https://doi.org/10.1109/TPWRS.2019.2925557)
- I. Peña, C. B. Martinez-Anido and B.-M. Hodge, "An Extended IEEE 118-Bus Test
  System With High Renewable Penetration," *IEEE Transactions on Power Systems*,
  vol. 33, no. 1, pp. 281–289, 2018. DOI:
  [10.1109/TPWRS.2017.2695963](https://doi.org/10.1109/TPWRS.2017.2695963)
- S. Babaeinejadsarookolaee et al., "The Power Grid Library for Benchmarking AC
  Optimal Power Flow Algorithms," 2019. [link][pglibopf2019]
- R. D. Zimmerman and C. E. Murillo-Sánchez, "MATPOWER test case
  `t\_case30\_userfcns`: DC OPF with Zonal Reserves and Interface Limits,".
  [link][matpower_case30]

### Open-source tools compared or interfaced

Tools whose cases GTOpt converts or whose results it is compared with, and the
open-source solvers it ships plugins for.

- R. D. Zimmerman, C. E. Murillo-Sánchez and R. J. Thomas, "MATPOWER:
  Steady-State Operations, Planning, and Analysis Tools for Power Systems
  Research and Education," *IEEE Transactions on Power Systems*, vol. 26, no. 1,
  pp. 12–19, 2011. DOI:
  [10.1109/TPWRS.2010.2051168](https://doi.org/10.1109/TPWRS.2010.2051168)
- L. Thurner et al., "pandapower — An Open-Source Python Tool for Convenient
  Modeling, Analysis, and Optimization of Electric Power Systems," *IEEE Trans.
  Power Syst.*, vol. 33, no. 6, pp. 6510–6521, 2018. DOI:
  [10.1109/TPWRS.2018.2829021](https://doi.org/10.1109/TPWRS.2018.2829021)
- T. Brown, J. Hörsch and D. Schlachtberger, "PyPSA: Python for Power System
  Analysis," *J. Open Res. Softw.*, vol. 6, no. 1, pp. 4, 2018. DOI:
  [10.5334/jors.188](https://doi.org/10.5334/jors.188)
- J. D. Jenkins and N. A. Sepulveda, "Enhanced Decision Support for a Changing
  Electricity Landscape: The GenX Configurable Electricity Resource Capacity
  Expansion Model," MIT Energy Initiative, 2017.
- Sandia National Laboratories, "QuESt Planning: Open-Source Capacity Expansion
  Planning Tool for Energy Storage Systems," 2024. [link][sandia2024quest]
- IDAES Consortium, "IDAES-GTEP: Flexible Environments for Generation and
  Transmission Expansion Planning," 2024. [link][idaes_gtep2024]
- Q. Huangfu and J. A. J. Hall, "Parallelizing the Dual Revised Simplex Method,"
  *Math. Programming Comput.*, vol. 10, no. 1, pp. 119–142, 2018. DOI:
  [10.1007/s12532-017-0130-5](https://doi.org/10.1007/s12532-017-0130-5)
- J. J. Forrest and R. Lougee-Heimer, "CBC User Guide," *Emerging Theory,
  Methods, and Applications*, pp. 257–277, 2005. DOI:
  [10.1287/educ.1053.0020](https://doi.org/10.1287/educ.1053.0020)

### System-operator data

Public studies and model data of system operators used for the real-system cases
and comparisons.

- Coordinador Eléctrico Nacional, "Programa de Coordinación de la Operación
  (PCP): Bases del Modelo, and PLP Programación de Mediano y Largo Plazo,".
  [link][cen_pcp]
- Administrador del Mercado Mayorista and Ente Operador Regional, "Programación
  de Largo Plazo and Programas de Despacho of the Guatemalan System,".
  [link][amm_eor]
- Comité de Operación Económica del Sistema Interconectado Nacional, "Estudio de
  Verificación del Margen de Reserva Firme Objetivo 2026–2029: SDDP Data,".
  [link][coes_mrfo]
- Australian Energy Market Operator, "2026 Integrated System Plan (ISP): Model
  Inputs," 2026. [link][aemo2026isp]
- SEM Committee, "SEM-25-010: SEM PLEXOS Model Validation 2024–2032 and Backcast
  Report," 2025. [link][sem2025plexos]

[aemo2026isp]: https://www.aemo.com.au/energy-systems/major-publications/integrated-system-plan-isp/2026-integrated-system-plan-isp
[amm_eor]: https://www.amm.org.gt
[cambier_fast]: https://github.com/leopoldcambier/FAST
[cen_pcp]: https://www.coordinador.cl
[coes_mrfo]: https://www.coes.org.pe
[cucdata]: https://github.com/datejada/CUC-data
[gasmodels]: https://github.com/lanl-ansi/GasModels.jl
[gaspowermodels]: https://github.com/lanl-ansi/GasPowerModels.jl
[ibarra2019gnl]: https://hdl.handle.net/11059/11373
[idaes_gtep2024]: https://github.com/IDAES/idaes-gtep
[ieee30bus]: https://labs.ece.uw.edu/pstca/pf30/pg_tca30bus.htm
[ieee57bus]: https://labs.ece.uw.edu/pstca/pf57/pg_tca57bus.htm
[martinez2022storage]: https://hdl.handle.net/11059/14312
[matpower_case30]: https://github.com/MATPOWER/matpower
[pglibopf2019]: https://github.com/power-grid-lib/pglib-opf
[powersimulations]: https://github.com/NREL-Sienna
[psrclasses]: https://github.com/psrenergy/PSRClassesInterface.jl
[pstca]: https://labs.ece.uw.edu/pstca/
[saldarriaga2013msc]: https://hdl.handle.net/11059/3805
[saldarriaga2018phd]: https://hdl.handle.net/11059/10887
[sandia2024quest]: https://www.sandia.gov/ess/
[sem2025plexos]: https://www.semcommittee.com/publications/sem-25-010-sem-plexos-model-validation-2024-2032-and-backcast-report

## Studies using GTOpt

Studies made with GTOpt, with their questions, method, findings and what
they changed in GTOpt, are described in
[`docs/studies/README.md`](docs/studies/README.md):

- Line losses in CEN's daily schedule (PCP), with the Coordinador
  Eléctrico Nacional, 2026.

Add yours with a pull request.
