// SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-gtopt-commercial
//
// Batched MathJax 3 TeX validator for tools/check_latex_math.py.
//
// stdin : JSON array of {"id": int, "doc": str, "tex": str, "display": bool}
// stdout: JSON array of {"id": int, "error": str} -- one entry per span
//         that MathJax cannot typeset (spans that parse are omitted).
//
// Packages: those of a stock MathJax 3 page such as GitHub's -- the
// always-loaded ones plus everything its autoload extension pulls in on
// first use (bussproofs excepted).  noerrors / noundefined are left out
// so errors surface; textmacros, physics, setoptions are not autoloaded,
// hence not available on GitHub either (\text{a_b} is literal text
// there).  Macros defined with \newcommand / \def persist within one
// "doc" (one Markdown file = one page), like on GitHub.

import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import "mathjax-full/js/input/tex/AllPackages.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
import { STATE } from "mathjax-full/js/core/MathItem.js";

const PACKAGES = [
  "base",
  "ams",
  "newcommand",
  "configmacros",
  "action",
  "amscd",
  "bbox",
  "boldsymbol",
  "braket",
  "cancel",
  "cases",
  "centernot",
  "color",
  "colortbl",
  "empheq",
  "enclose",
  "extpfeil",
  "gensymb",
  "html",
  "mathtools",
  "mhchem",
  "upgreek",
  "unicode",
  "verb",
];

RegisterHTMLHandler(liteAdaptor());

function newDocument() {
  const tex = new TeX({
    packages: PACKAGES,
    formatError: (_jax, err) => {
      throw err;
    },
  });
  return mathjax.document("", { InputJax: tex });
}

function readStdin() {
  const chunks = [];
  return new Promise((resolve, reject) => {
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString()));
    process.stdin.on("error", reject);
  });
}

const items = JSON.parse(await readStdin());
const out = [];
let current = null;
let doc = null;
for (const item of items) {
  if (doc === null || item.doc !== current) {
    current = item.doc;
    doc = newDocument();
  }
  try {
    doc.convert(item.tex, { display: item.display, end: STATE.COMPILE });
  } catch (err) {
    const msg = (err && err.message ? err.message : String(err))
      .replace(/\s+/g, " ")
      .trim();
    out.push({ id: item.id, error: msg });
  }
}
process.stdout.write(JSON.stringify(out));
