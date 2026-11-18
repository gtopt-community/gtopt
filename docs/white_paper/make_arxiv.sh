#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
# Build the arXiv submission package for the GTOpt white paper.
#
# arXiv compiles the TeX source itself and does not run BibTeX, so the
# package carries main.tex, the section files and the generated main.bbl
# (whose base name must match main.tex).  No PDF of the paper, no
# auxiliary files and no hidden files are included.
#
# Usage: bash make_arxiv.sh [output.tar.gz]
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-$here/gtopt-arxiv.tar.gz}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# 1. Full build (with BibTeX) to produce main.bbl.
mkdir -p "$work/build"
(cd "$here" && latexmk -pdf -interaction=nonstopmode -halt-on-error \
  -outdir="$work/build" main.tex >"$work/build.log" 2>&1) || {
  tail -40 "$work/build.log"; exit 1; }

# 2. Assemble the package.
pkg="$work/pkg"
mkdir -p "$pkg/sections"
cp "$here/main.tex" "$pkg/"
cp "$here"/sections/*.tex "$pkg/sections/"
cp "$work/build/main.bbl" "$pkg/"
if compgen -G "$here/figures/*.pdf" >/dev/null \
  || compgen -G "$here/figures/*.png" >/dev/null; then
  mkdir -p "$pkg/figures"
  cp "$here"/figures/*.{pdf,png} "$pkg/figures/" 2>/dev/null || true
fi

# 3. Check that the package compiles the way arXiv does (no BibTeX).
test_dir="$work/test"
cp -r "$pkg" "$test_dir"
(cd "$test_dir" && for _ in 1 2 3; do
  pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
done) || { tail -40 "$test_dir/main.log"; exit 1; }
if grep -qE "undefined|Citation .* undefined" "$test_dir/main.log"; then
  grep -E "undefined" "$test_dir/main.log" | head
  echo "error: unresolved references in the arXiv test build" >&2
  exit 1
fi

# Plain-text abstract for the arXiv metadata form (limit 1920 chars).
python3 - "$here/main.tex" "$here/abstract.txt" <<'PY'
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
a = s[s.index(r"\begin{abstract}") + 16 : s.index(r"\end{abstract}")]
t = re.sub(r"\s+", " ", a).strip().replace(r"\%", "%")
open(sys.argv[2], "w", encoding="utf-8").write(t + "\n")
print(f"abstract.txt: {len(t)} chars" + ("" if len(t) <= 1920 else " (TOO LONG)"))
PY

tar -C "$pkg" -czf "$out" .
cp "$test_dir/main.pdf" "${out%.tar.gz}.pdf"
echo "package: $out ($(du -h "$out" | cut -f1)); preview: ${out%.tar.gz}.pdf"
tar -tzf "$out"
