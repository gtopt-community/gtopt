# ldf_tiny — LinDistFlow distribution feeder

A 12.47/4.16 kV radial feeder (substation transformer, regulator bank
with optimised taps, a switch, a lateral, PV, a generator with var
limits, a battery, capacitor banks folded into bus shunts) solved with
`model_options.network_model = "lindistflow"` over 24 hourly blocks.

Generated from the hand-made OpenDSS script
`scripts/opendss2gtopt/tests/data/tiny_feeder.dss`:

```bash
opendss2gtopt scripts/opendss2gtopt/tests/data/tiny_feeder.dss \
    -o /tmp/ldf --network-model lindistflow --time-mode daily
# then rename /tmp/ldf/tiny.json to cases/ldf_tiny/ldf_tiny.json
# (system name "ldf_tiny")
gtopt cases/ldf_tiny/ldf_tiny.json -d out
gtopt2opendss cases/ldf_tiny/ldf_tiny.json -r out --block 12 --solve
```

`output/` holds the golden solution, `Bus/vsq_sol` (squared voltages)
and `Line/qflow_sol` (reactive flows) checked by the e2e test.
