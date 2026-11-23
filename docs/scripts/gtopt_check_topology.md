# gtopt_check_topology — Preflight Network Topology Analyser

Checks the electrical network of a gtopt planning JSON before solving:
islands (static and per stage), KVL loop quality, bridges, DC links,
negative R/X, voltage mixing and a few Tier-2 data checks.

---

## Usage

```bash
gtopt_check_topology case.json
gtopt_check_topology case.json --quiet --no-color
gtopt_check_topology case.json --expect-zones CL,PE
gtopt_check_topology case.json --expect-islands 1 --json-out topo.json
gtopt_check_topology case.json --strict-direction-lines sd.json
```

| Option | Default | Meaning |
|--------|---------|---------|
| `--json-out PATH` | — | Write the full report as JSON |
| `--strict-direction-lines PATH` | — | Write the lines on dangerous KVL loops |
| `--danger-threshold N` | 5 | Cycle danger-score cut-off |
| `--max-cycle-size N` | 20 | Skip fundamental cycles with more buses |
| `--case-dir DIR` | JSON's dir | Base directory for `options.input_directory` |
| `--expect-islands N` | — | Fail when a stage has more than N islands with elements |
| `--expect-zones Z1,Z2` | — | Same with N = number of zones, one island per zone |
| `--detail-max-buses N` | 12 | List the elements of islands with at most N buses |
| `--quiet` | off | Only print flagged checks |
| `--include-benign-loops` | off | Also list the benign cycles |
| `--no-color` | off | Plain output |

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | No fatal error (warnings do not change the exit code) |
| 1 | The planning file cannot be read |
| 2 | Fatal data error: a negative reactance or resistance, or an island with demand and no generator |
| 3 | `--expect-islands` / `--expect-zones` failed |

An island with no demand and no generation (for example a lone
auxiliary bus) is not an error.

---

## Scheduled fields

Line, bus and element fields can be given in any form gtopt accepts. The
tool reads each one per stage:

- a scalar, e.g. `"reactance": 0.05`
- an inline array per stage (`[1, 1, 0]`) or per stage and block
  (`[[100, 90], [100, 80]]`)
- a file name such as `"reactance": "cl_reactance"`. The tool reads
  `<input_directory>/Line/cl_reactance.parquet` (or `.csv`). The file is
  in long format: `[scenario,] [stage,] [block,] uid, value`.
  `"Other@name"` reads from the `Other/` directory instead.
  `input_directory` comes from `options` and is relative to the folder
  of the case JSON.

Blocks and scenarios within one stage are reduced to a single value:
the minimum for reactance and resistance, the maximum for `tmax` and
capacities. If a file or a uid cannot be found, the tool uses the
default value and lists it under **T5. Unresolved schedule
references**. The reader is `gtopt_shared.field_sched.StageSchedResolver`,
which other preflight tools can reuse.

A line **connects its two buses in a stage** when all of these hold:

- `Line.active` is non-zero in that stage
- `Bus.active` is non-zero for both buses
- the line is not closed in both directions, i.e. not
  `tmax_ab == tmax_ba == 0` for the whole stage. An unset `tmax` falls
  back to `capacity`; if that is unset too, the line counts as
  unbounded, as in the LP.

### Static checks on the union topology

Cycles, bridges, stubs, DC links, the A1/A2 islands and parallel
circuits are computed on the **union topology**: every line in service
in at least one stage.

Time-varying values become one representative number per line, taken
over the stages where the line is in service:

- reactance and resistance: the value with the smallest magnitude (the
  strongest KVL coupling)
- `tmax`: the largest `tmax_ab`

A line with near-zero reactance in that representative value is treated
as a DC link. Lines that are never in service are left out and listed
under **T4**.

Negative R/X is flagged if it appears in **any** stage.

---

## Per-stage topology (T1–T3)

### T1. Topology periods

Connectivity is computed for every stage. Consecutive stages are grouped
into one period while two things stay the same:

- how the buses are split into islands
- which islands carry elements

The report prints only these periods, e.g. `stages 25-29 (5)`. For each
period, every island that carries elements is listed with:

- its zone label
- its number of buses
- its number of elements and its MW by kind (generator `pmax`, demand
  `lmax`, battery `pmax_discharge`, falling back to `capacity`)

For small islands (`--detail-max-buses`) every element is listed with
its type and its turbine or converter tag. Islands without elements are
summarised on one line as benign.

An element counts as **effective** in a stage when it is active and its
capacity is not 0 MW. An element with expansion fields (`expcap`,
`expmod`, `capmax`) counts even at 0 MW. An active 0-MW element that
cannot expand (e.g. a placeholder candidate) is counted as *inert* and
does not make its island carry elements.

**Zones.** The zone of a bus is its `zone`, `country`, `area` or
`region` field. If none is set, the tool uses a 2–4 letter name prefix
followed by `_` (`CL_Kimal500` → `CL`).

### T2. Inter-zone lines

T2 lists every line whose two buses are in different zones, with its
`tmax` and the stages where it is in service. For example, an HVDC link
that is active from 2026 shows `in service stages 25-108`.

### T3. Expected islands

- `--expect-islands N` fails (exit 3) when a period has more than `N`
  islands with effective elements. The islands beyond the `N` largest
  are reported as offenders.
- `--expect-zones CL,PE` keeps the largest island with elements for each
  listed zone. Every other island with elements is an offender. Once a
  link merges the two zones into one island, that island counts for
  both zones.

---

### T4–T6. Other per-stage findings

- **T4**: lines never in service in any stage.
- **T5**: file references that could not be resolved.
- **T6**: elements that are active, with capacity, in stages where their
  bus is inactive. This usually means a bus was remapped or merged onto
  a bus with a narrower `active` schedule.

## JSON report

`--json-out` contains every static section (`islands`, `dangerous_cycles`,
`bridges`, `dc_lines`, ...) plus three more keys:

- `stage_topology`: the periods with their islands, `inter_zone_lines`,
  `elements_on_inactive_bus` and `expectation`
- `never_in_service`
- `unresolved_fields`
