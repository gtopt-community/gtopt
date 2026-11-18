# Publications

Papers describing GTOpt and its predecessor FESOP, related work by the
authors, and studies that use GTOpt.

To cite the software itself, use [`CITATION.cff`](CITATION.cff) (GitHub's
"Cite this repository" button).  If you publish work that uses GTOpt,
please open a pull request adding it to the last section.

## GTOpt and FESOP

**GTOpt: Open-Source Energy-System Optimization, Consistent from Minutes
to Decades.**
Marcelo Matus A., María del Pilar Buitrago Villada, Erick Sierra Baeza,
Carlos Enrique García Bujanda, Catalina López, Marcos Falcone Salazar and
Gonzalo Bustos Turu.
Preprint, 2026 (arXiv submission in preparation).
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

GTOpt is the open-source successor of **FESOP**, the expansion and
operation planning framework developed at Black Bear Engineering.

**Optimal Expansion and Reliable Renewable Energy Integration in Long-Term
Planning Using FESOP.**
María del Pilar Buitrago Villada, Carlos Enrique García Bujanda, Erick
Sierra Baeza and Marcelo Matus.
*2022 IEEE Kansas Power and Energy Conference (KPEC)*, Manhattan, KS,
USA, April 2022, pp. 1–5.
DOI: [10.1109/KPEC54747.2022.9814781](https://doi.org/10.1109/KPEC54747.2022.9814781)

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
- M. Matus, N. Cáceres, S. Puschel-Lovengreen and R. Moreno, "Chebyshev
  based continuous time power system operation approach," *2015 IEEE
  Power & Energy Society General Meeting*, pp. 1–5, 2015.
  DOI: [10.1109/PESGM.2015.7286570](https://doi.org/10.1109/PESGM.2015.7286570)

The full reference list of the formulation, including the methods GTOpt
builds on, is in
[`docs/formulation/mathematical-formulation.md`](docs/formulation/mathematical-formulation.md#9-references).

## Studies using GTOpt

*None listed yet — add yours with a pull request.*
