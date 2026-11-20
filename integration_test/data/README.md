# integration_test/data/

This directory used to hold `sienna_5bus.tar.zst` and
`sienna_14bus.tar.zst`, small extracts of the Sienna
[`PowerSystemsTestData`](https://github.com/Sienna-Platform/PowerSystemsTestData)
repository (formerly `NREL-Sienna/PowerSystemsTestData`).  That
repository is public but carries **no licence**, so gtopt no longer
redistributes the files.  They are fetched from upstream at test/run
time instead.

## How the data is obtained

[`scripts/sienna_to_gtopt/_bundle.py`](../../scripts/sienna_to_gtopt/_bundle.py)
contains the manifest:

| Bundle | Upstream files | Consumer |
|---|---|---|
| `sienna_5bus` | `5-Bus/{branch,bus,gen,reserves,storage,Hydro_Upstream_Input}.csv`, `5-Bus/{generator_mapping,user_descriptors,user_descriptors_var_cost}.yaml`, `5-Bus/modifier.jl`, `5-Bus/timeseries_pointers_{da,rt}.json`, `psy_data/data_5bus_pu.jl` | `scripts/sienna_to_gtopt` (all variants) |
| `sienna_14bus` | `psy_data/data_14bus_pu.jl` | `scripts/sienna_to_gtopt` |

- Upstream repository: `Sienna-Platform/PowerSystemsTestData`
- Pinned commit: `a2ecae9ff3cfe6d219744f1b10e4edd4eeecabc9`
- Each file is verified against the SHA-256 recorded in the manifest.

On first use, `fetch_bundle(name)` downloads the files from
`https://raw.githubusercontent.com/<repo>/<commit>/<path>`, checks the
hashes, and packs them into `sienna_<name>.tar.zst` (members
`sienna_<name>/<file>`, the same layout the vendored archives had) in
the cache directory:

1. `$GTOPT_SIENNA_DATA_DIR`, if set (e.g. pre-staged archives for
   air-gapped CI);
2. otherwise `$XDG_CACHE_HOME/gtopt/sienna/` or
   `~/.cache/gtopt/sienna/`.

`extract_bundle(name)` extracts the cached archive into a process-local
temp directory (`$TMPDIR` or `~/tmp`).  With `GTOPT_OFFLINE=1` (or no
network) a missing archive raises `BundleUnavailableError` and the
`sienna_to_gtopt` tests that need the data are skipped.

To prime the cache by hand:

```bash
cd scripts
python -c "from sienna_to_gtopt import fetch_bundle; fetch_bundle('5bus'); fetch_bundle('14bus')"
```

The 6.4 MB `PJM_5_BUS_7_DAYS.h5` time-series file from upstream `5-Bus/`
is not used.

## What stays in the repository

- The C++ tests `test/source/test_sienna_5bus_*_port.cpp` restate a few
  parameters inline (cited in each file) and do not read these files.
- `tools/sienna_goldens/*.json` are reference *results* produced by our
  own PowerSimulations.jl runs (dispatch, flows, objective, solver
  versions); they do not contain the input data files.

## Sister converters

`scripts/nrel118_to_gtopt/` (Sienna `118-Bus/`) and
`scripts/rts_gmlc_to_gtopt/` (GridMod RTS-GMLC) likewise download their
data on first use into `~/.cache/gtopt/<port>/`.
