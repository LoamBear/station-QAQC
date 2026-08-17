# wxqc — config-driven QC engine for weather-station networks

A small, reusable quality-control engine. The engine code knows **no variable
names**: every column name, threshold, and edit lives in per-network config
files, so the same code runs any network. Adding a network means adding a config
folder, not editing code — which is what makes this work across the many
networks you manage without a naming overhaul.

The pipeline mirrors existing levels:

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
  plots.py            interactive QC review plots (raw/L1.5/L2/L3 overlays)
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

Python 3.10+, `pandas`, `numpy`, and `plotly` (for the QC review plots). From the project root, make sure `wxqc/` is on
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
| `discontinuity_limit` | turns on the discontinuity-aware fill (needs `roc_key`); gaps longer than this also require non-"free-floating" anchors (see below); blank = off, plain `interp_limit` fill applies |
| `handler` | name in `sensors.HANDLERS` for sensor-specific logic; blank = none |

`range_action` presets, drawn from your scripts:
- `nan` — out of range → missing
- `clamp_high_100` — above max → 100, below min → missing (your RH rule)
- `nan_or_zero` — above max → missing, below min → 0 (your shortwave rule)
- `clamp` — pin to the bounds
- `keep` — record the range but don't enforce (e.g. wind direction)

### Discontinuity-aware gap fill
`fill_short_gaps` (used at L2) normally fills any interior gap up to `interp_limit`
samples by straight-line interpolation. That's wrong when the two sides of the gap
are genuinely different readings (e.g. a sensor swap/recalibration), not just a
dropout — interpolating draws a fake ramp between two disconnected regimes.

Setting `discontinuity_limit` on a variable (needs `roc_key` too) turns on a
magnitude check across the *whole* `interp_limit` window: for any gap up to
`interp_limit` samples, the jump between the value just before and just after it
is compared to `roc_threshold * gap_length`. Within that budget, the gap fills
normally; over it, it's a discontinuity and stays `NaN`, which flows into the `S`
(Suspect) flag automatically for review.

Gaps *longer* than `discontinuity_limit` (but still within `interp_limit`) face one
more requirement: neither boundary value may itself be "free-floating" — a single
valid reading flanked by `NaN` on both sides (e.g. sandwiched between two other
rejected gaps). A lone point like that isn't a trustworthy anchor for a several-
sample fill, so such gaps are left for review even if the raw jump looks small.

This is opt-in per variable (blank = off, falls back to plain `interp_limit` fill)
so it can be rolled out to more variables incrementally; currently only
`BP_avg_mbar` uses it (`interp_limit=6`, `discontinuity_limit=3`).

### thresholds.csv
Your existing `QC_L1rangecheck.csv` format, unchanged: rows are parameter keys
(`Tavg_max`, `Tavg_min`, `T_roc`, …), columns are stations. `range_key=Tavg`
reads `Tavg_min`/`Tavg_max`; `roc_key=T_roc` reads `T_roc` directly.

### manual_edits.csv — the L3 change log
One row per edit:
`station, value_col, start, end, action, param, note, water_year, applied_by, applied_on`

`start`/`end` bound the window (**end exclusive**; blank end = "from start
onward"; blank start **and** end = the whole series). Actions:

| action | effect | `param` |
|--------|--------|---------|
| `nan` | window → missing | — |
| `interpolate` | linear-interpolate across the window | — |
| `interpolate_limit` | linear-interpolate gaps ≤ `param` samples within the window; longer gaps untouched — a systemic gap-length rule (usually whole-series) rather than a one-off dated fix | max gap length (samples) |
| `nan_if_minute` | within the window, null samples at minute `param` past every hour (e.g. a recurring noisy reading) | minute-of-hour, 0–59 |
| `nan_if_gt` | within the window, null samples whose value exceeds `param` (a station-specific sanity ceiling) | number |
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

## 7. QC review plots

Both `run_pipeline.py` and `run_l3.py` have a `PLOTS` flag (on by default). When
set, they write interactive HTML plots so whoever runs the pipeline can see how
each variable changed at every step.

- `run_pipeline.py` writes `plots/<station>_WY<year>/` — one HTML per variable
  (plus an `index.html`), overlaying **raw → L1.5 → L2**.
- `run_l3.py` writes `Level_3/plots/<station>/` — the same, now including the
  **L3** trace so the annual review shows all four levels together.

Markers show data *quality*, not removal: each level that newly flags a point
`'E'` or `'S'` (not already flagged at a prior level) gets its own marker, so you
can see which step first raised it — an orange square where the value was
**Edited** (modified but kept), a pink diamond where it was kept but flagged
**Suspect** (e.g. a manual `set_flag` edit, or a gap-fill that reused an
already-suspect sample). Points that were actually removed (`'S'` with no value)
get no marker — the gap in the line is the signal.

Open `index.html` and click a variable, or open a variable's HTML directly. Plots
are interactive (zoom into a storm, hover for values) via WebGL; plotly.js loads
from a CDN so files stay a few MB each. One file per variable is deliberate — a
single combined file embeds every variable's full 10-minute series and becomes too
large for a browser. To plot ad hoc:

```python
from wxqc.plots import plot_variable, plot_station
plot_station(df, specs, "nep3", "plots/nep3")   # all variables + index.html
```

`df` is the full working frame (raw + `_L1` + `_L2`, and `_L3` after
`apply_manual_edits`); the `Level_1.5QC` / `Level_2QC` files retain those columns
for re-plotting later.

## 8. Validating against your current outputs
```python
from wxqc.diff import compare_csv
print(compare_csv("Level_2/nep4_WY2025_L2.csv", "legacy/nep4_WY2025_L2.csv"))
# -> rows compared, columns with differing cells (and counts), columns unique to each
```
Use this to confirm the refactor reproduces — or intentionally changes — the old
results before trusting it.

## 9. Things that intentionally differ from the legacy scripts (please confirm)

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
