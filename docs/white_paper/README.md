# GTOpt White Paper — LaTeX Source

**GTOpt: Open-Source Energy-System Optimization, Consistent from Minutes
to Decades.**  The reference paper for the repository: formulation,
solution methods, multi-scale coupling, software architecture,
validation, and the comparison with the Chilean daily schedule (CEN /
PLEXOS).

- **Status**: preprint; the publication venue will be announced in
  `PUBLICATIONS.md` in the repository root.

## Licence

The text and figures of the paper (`main.tex`, `sections/`,
`references.bib`, `figures/`, `diagrams/`, `tables/`
and the compiled `main.pdf`) are licensed under the
[Creative Commons Attribution 4.0 International licence](https://creativecommons.org/licenses/by/4.0/)
(CC BY 4.0): they may be shared and adapted for any purpose, provided
the paper is cited.  The build script (`CMakeLists.txt`)
follows the licence of the GTOpt core
(AGPL-3.0-or-later or the GTOpt Commercial License, see
`LICENSING.md` in the repository root).

If the paper is later published in a journal, the version in this
directory remains the authors' preprint; the journal's typeset version
is not distributed here.

The author biographies are only for the journal version: set
`\journalversiontrue` in `main.tex` to include
`sections/biographies.tex`.

## Directory Structure

```
docs/white_paper/
├── main.tex                  # Main document (includes all sections)
├── references.bib            # BibTeX bibliography
├── .latexmkrc                # Build configuration
├── README.md                 # This file
├── sections/
│   ├── introduction.tex      # Section I
│   ├── literature_review.tex # Section II (State of the Art)
│   ├── formulation.tex       # Section III (Mathematical Formulation)
│   ├── solution_methods.tex  # Section IV (Solution Methods)
│   ├── architecture.tex      # Section V (Software Architecture)
│   ├── validation.tex        # Section VI (Validation & Benchmarks)
│   ├── case_studies.tex      # Section VII (CEN daily schedule vs PLEXOS)
│   └── conclusions.tex       # Section VIII (Conclusions)
├── figures/                  # Raster images (PNG, PDF)
│   └── .gitkeep
├── diagrams/                 # Source diagrams (TikZ, Mermaid)
│   └── .gitkeep
└── tables/                   # Standalone table files (if needed)
    └── .gitkeep
```

## Compilation

### Using tectonic (recommended — no TeX distribution needed)

Tectonic is a self-contained LaTeX engine that auto-downloads packages
(IEEEtran, BibTeX styles, fonts) on first run.  No TeX Live or other
distribution is required.

```bash
# Install tectonic (one-time)
curl -fsSL https://drop-sh.fullyjustified.net | sh -s -- --prefix ~/.local

# Build the PDF
cd docs/white_paper
tectonic main.tex
```

### Using latexmk (alternative — requires TeX Live)
```bash
# Install TeX Live (Ubuntu/Debian)
sudo apt-get install -y texlive-latex-base texlive-latex-recommended \
  texlive-latex-extra texlive-publishers texlive-bibtex-extra \
  texlive-science texlive-pictures texlive-plain-generic \
  texlive-fonts-recommended cm-super latexmk

cd docs/white_paper
latexmk -pdf main.tex
```

### Using CMake
```bash
cmake -S docs/white_paper -B build-paper
cmake --build build-paper
# PDF at build-paper/main.pdf
```

CMake auto-detects tectonic (preferred) or latexmk.

### Manual compilation
```bash
pdflatex main
bibtex main
pdflatex main
pdflatex main
```

### On Overleaf
1. Upload the entire `docs/white_paper/` directory as a new project
2. Set `main.tex` as the main document
3. Overleaf will auto-detect the IEEEtran class and compile

## Switching to an Alternative Journal Template

### For Applied Energy (Elsevier)
Replace `\documentclass[journal]{IEEEtran}` with:
```latex
\documentclass[review,3p]{elsarticle}
```
And change `\bibliographystyle{IEEEtran}` to:
```latex
\bibliographystyle{elsarticle-num}
```

### For SoftwareX (Elsevier)
Same as Applied Energy, but limit the paper to ~3000 words and focus
on software description rather than mathematical formulation.

## Notes for Collaborators

- Each section is in a separate `.tex` file under `sections/`
- Add figures to `figures/` (PDF or PNG preferred for Overleaf)
- Use `\label{}` and `\ref{}` for cross-references
- Use `\cite{}` for citations (entries in `references.bib`)
- `case_studies.tex` holds the comparison with the CEN daily schedule;
  report results only (no computing times of commercial tools)
