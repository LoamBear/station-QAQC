# wxqc — config-driven QC engine for weather-station networks

A small, reusable quality-control engine. The engine code knows **no variable
names**: every column name, threshold, and edit lives in per-network config
files, so the same code runs any network. Adding a network means adding a config
folder, not editing code — which is what makes this work across the many
networks you manage without a naming overhaul.

The pipeline mirrors your existing levels:

| Level | What it is | How it runs | Produced by |
|-------|------------|-------------|-------------|
| L1    | Raw, renamed measurements | (your existing pre-QC) | upstream |
| **L1.5** | Range + rate-of-change checks, `_L1` columns | automated | `run_pipeline.py` |
| **L2**   | Short-gap fill + re-checks + sensor logic, `_L2` columns | automated | `run_pipeline.py` |
| **L3**   | Manual edits from a change log, `_L3` columns | human-in-the-loop, annual | `run_l3.py` |

L1.5 and L2 are meant for the automated pipeline; L3 is the once-a-year manual
review.

---

## 1. Layout

```
wxqc/                 the engine (network-agnostic; you rarely touch this)
  checks.py           range_check, roc_check, fill_short_gaps, rolling_median
  sensors.py          Geonor accumulation, snow-depth dynamic smoothing (extension point)
  flags.py            O / E / S provenance derivation
  edits.py            manual change-log engine (L3)
  pipeline.py         run_level15, run_level2, export_columns
  config.py           config loaders + VarSpec
  diff.py             compare new output vs legacy output
config/<network>/     all network-specific knowledge lives here
  stations.csv
  variables.csv
  thresholds.csv      (your QC_L1rangecheck.csv, unchanged format)
  manual_edits.csv    the L3 change log
run_pipeline.py       driver: L1.5 + L2 for a network
run_l3.py             driver: apply the change log -> L3
selftest.py           synthetic end-to-end check
```

## 2. Requirements

Python 3.10+, `pandas`, `numpy`. From the project root, make sure `wxqc/` is on
the path (running the drivers from the root handles this).

## 3. Config files

### stations.csv
One row per station: `station_id, por_start, por_end, timezone`. The period of
record bounds the L3 export.

### variables.csv — the heart of a network's config
One row per variable. Columns:

| column | meaning |
|--------|---------|
| `value_col` | the data column name **in this network** |
| `flag_col`  | its companion flag column (engine appends `_L1`/`_L2`/`_L3`) |
| `range_key` | key into `thresholds.csv` → `<key>_min` / `<key>_max`; blank = no range check |
| `range_action` | `nan`, `clamp_high_100`, `nan_or_zero`, `clamp`, or `keep` |
| `roc_key` | single-value key in `thresholds.csv` (e.g. `T_roc`); blank = no ROC check |
| `interp_limit` | max gap length (samples) to linearly fill at L2; blank = no fill |
| `handler` | name in `sensors.HANDLERS` for sensor-specific logic; blank = none |

`range_action` presets, drawn from your scripts:
- `nan` — out of range → missing
- `clamp_high_100` — above max → 100, below min → missing (your RH rule)
- `nan_or_zero` — above max → missing, below min → 0 (your shortwave rule)
- `clamp` — pin to the bounds
- `keep` — record the range but don't enforce (e.g. wind direction)

### thresholds.csv
Your existing `QC_L1rangecheck.csv` format, unchanged: rows are parameter keys
(`Tavg_max`, `Tavg_min`, `T_roc`, …), columns are stations. `range_key=Tavg`
reads `Tavg_min`/`Tavg_max`; `roc_key=T_roc` reads `T_roc` directly.

### manual_edits.csv — the L3 change log
One row per edit:
`station, value_col, start, end, action, param, note, water_year, applied_by, applied_on`

`start`/`end` bound the window (**end exclusive**; blank end = "from start
onward"). Actions:

| action | effect | `param` |
|--------|--------|---------|
| `nan` | window → missing | — |
| `interpolate` | linear-interpolate across the window | — |
| `offset` | add to window | number |
| `scale` | multiply window (calibration) | number |
| `clamp` | set window to a constant | number |
| `rolling_median` | replace window with centered rolling median | window size (samples) |
| `set_flag` | set the flag column in the window | flag value, e.g. `S` |

Edits apply in file order, so later rows can build on earlier ones. L3 is
regenerated **deterministically** from L2 + this log every time.

## 4. Flags (provenance)

- **O** Observed — value unchanged from the raw measurement
- **E** Edited — value modified by QC (clamp, fill, calibration)
- **S** Suspect — value is missing, or a human marked it suspect

`E` and `S` are **sticky** by default: once edited or flagged, later levels keep
that provenance even if they leave the value alone. (Pass `sticky=("S",)` to
`derive_flags` to recover the legacy per-level behavior.)

## 5. Running it

```bash
# L1.5 + L2 for the whole network
python run_pipeline.py

# one station / a few water years: edit the CONFIG block at the top of the file
#   STATIONS = ["nep4"]
#   YEARS    = range(2025, 2026)

# L3 from the change log
python run_l3.py
```

Both drivers have a `CONFIG` block at the top for paths, station list, and year
range — no need to edit the engine.

### Annual L3 workflow
1. Run `run_pipeline.py` so L2QC is current.
2. Review the new water year (your plotting code still works on the `_L2`/`_L3`
   columns).
3. Append one row per edit to `config/<network>/manual_edits.csv`.
4. Run `run_l3.py`. L3 regenerates from L2 + the log; the log is your auditable,
   diffable record of every manual change.

## 6. Adding a new network
1. `cp -r config/nevcan config/<newnet>`.
2. Replace the four CSVs with the new network's names, thresholds, and stations.
3. If a sensor needs special logic not covered by the tabular checks, add a
   function to `sensors.py` and reference it by name in `variables.csv`.
4. Point the drivers at the new config (`NETWORK = "<newnet>"`).

No engine code changes.

## 7. Validating against your current outputs
```python
from wxqc.diff import compare_csv
print(compare_csv("Level_2/nep4_WY2025_L2.csv", "legacy/nep4_WY2025_L2.csv"))
# -> rows compared, columns with differing cells (and counts), columns unique to each
```
Use this to confirm the refactor reproduces — or intentionally changes — the old
results before trusting it.

## 8. Things that intentionally differ from the legacy scripts (please confirm)

1. **Flag fix.** `derive_flags` uses `.isna()`, so the `S` branch now actually
   fires. Consequence: a value **removed** by QC (→ NaN) is now `S` (missing),
   while a value **modified** (clamp/fill/calibration) is `E`. The old code
   marked everything `E` because the `S` branch was dead code. If you want
   removed-vs-missing distinguished further (e.g. a separate `M`), that's a
   one-line change.
2. **Per-level temperature thresholds.** Each temperature variable now range-
   checks against its own bounds. The legacy L2 block checked `T_max/T_min/T_avg`
   against `T2max` bounds and `T10m_*` against `T2avg` bounds (copy-paste).
3. **Gap fill.** `fill_short_gaps` fills interior gaps ≤ limit and leaves longer
   gaps and series edges as NaN — a clarified version of the
   `rolling(7)…shift(-6)` construct.
4. **Geonor spike clip** (`sensors.geonor_accumulate`) reproduces the legacy
   second-difference clip and is marked `CONFIRM` in the code.

Run `selftest.py` for a synthetic end-to-end check of all of the above.
