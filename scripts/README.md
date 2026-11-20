# gtopt-scripts

Python tools that ship with [GTOpt](https://github.com/gtopt-community/gtopt),
the open-source optimizer for generation, transmission and storage
planning and operation.

- **Converters** to GTOpt cases: `plp2gtopt` (PLP), `plexos2gtopt`
  (PLEXOS), `sddp2gtopt` (PSR SDDP), `psse2gtopt` (PSS®E), `pp2gtopt`
  (pandapower / MATPOWER), `ts2gtopt`, `igtopt` (Excel).
- **Checks and comparison**: `gtopt_check_json`, `gtopt_check_lp`,
  `gtopt_check_output`, `gtopt_compare`.
- **Running and visualisation**: `run_gtopt`, `gtopt_monitor`,
  `gtopt_diagram`, `gtopt2pp`.

Install the wheel attached to a
[GitHub release](https://github.com/gtopt-community/gtopt/releases), or
from a checkout:

```bash
pip install ./scripts
```

The `gtopt` solver binary is in the same release; see the
[documentation](https://github.com/gtopt-community/gtopt#readme).
Real cases ready to run are in
[gtopt-cases](https://github.com/gtopt-community/gtopt-cases).

License: AGPL-3.0-or-later (see
[LICENSING.md](https://github.com/gtopt-community/gtopt/blob/master/LICENSING.md)).
