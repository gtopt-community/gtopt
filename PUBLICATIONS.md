# Publications

Papers describing GTOpt and its predecessor FESOP, related work by the
authors, and studies that use GTOpt.

To cite the software itself, use [`CITATION.cff`](CITATION.cff) (GitHub's
"Cite this repository" button).  If you publish work that uses GTOpt,
please open a pull request adding it to the last section.

## White paper

**GTOpt: Open-Source Energy-System Optimization, Consistent from Minutes
to Decades.**
Marcelo Matus A., María del Pilar Buitrago Villada, Erick Sierra Baeza,
Carlos Enrique García Bujanda, Catalina López, Marcos Falcone Salazar and
Gonzalo Bustos Turu.
Preprint, 2026.
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
  note         = {Preprint},
  howpublished = {\url{https://github.com/gtopt-community/gtopt/tree/master/docs/white_paper}},
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

## Related work by the authors

- E. Pereira-Bonvallet, S. Puschel-Lovengreen, M. Matus and R. Moreno,
  "Optimizing Hydrothermal Scheduling with Non-Convex Irrigation
  Constraints: Case on the Chilean Electricity System," *Energy Procedia*,
  vol. 87, pp. 132–138, 2016.
  DOI: [10.1016/j.egypro.2015.12.342](https://doi.org/10.1016/j.egypro.2015.12.342)
- C. Benavides, M. Matus, E. Sierra, R. Sepúlveda, A. M. Ruz and
  F. Gallardo, "Value contribution of solar plants to the Chilean electric
  system," *AIP Conference Proceedings*, vol. 2126, 120008, 2019
  (SolarPACES 2018).
  DOI: [10.1063/1.5117671](https://doi.org/10.1063/1.5117671)
- C. Benavides, R. Alvarez, R. Torres, R. Moreno, M. Matus, D. Muñoz,
  J. M. Gonzalez, G. Jiménez-Estévez and R. Palma-Behnke, "Capacity
  payment allocation in hydrothermal power systems with high shares of
  renewable energies," *E3S Web of Conferences*, vol. 140, 11008, 2019.
  DOI: [10.1051/e3sconf/201914011008](https://doi.org/10.1051/e3sconf/201914011008)

The full reference list of the formulation, including the methods GTOpt
builds on, is in
[`docs/formulation/mathematical-formulation.md`](docs/formulation/mathematical-formulation.md#9-references).

## Studies using GTOpt

*None listed yet — add yours with a pull request.*
