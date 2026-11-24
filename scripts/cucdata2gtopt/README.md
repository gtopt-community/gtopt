# cucdata2gtopt

Clustered unit-commitment benchmark of G. Morales-España and
D. A. Tejada-Arango (IEEE TPWRS 34(4) 2019,
[arXiv:1811.02622](https://arxiv.org/abs/1811.02622)) → gtopt
individual-unit UC cases (IEEE 39-bus / 118-bus).

```bash
cucdata2gtopt --system 39 --reserve 10 -o cuc/
```

The [CUC-data](https://github.com/datejada/CUC-data) files have no
licence: `_source.py` downloads them from a pinned commit (SHA-256
checked) into `~/.cache/gtopt/cucdata/`; nothing is vendored.

Full documentation: [`docs/scripts/cucdata2gtopt.md`](../../docs/scripts/cucdata2gtopt.md).
