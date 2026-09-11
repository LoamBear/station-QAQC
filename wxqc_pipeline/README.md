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
| **L3**   | Manual edits from a change log, `_L3` columns | human-in-the-loop, annual | `run_l3.py` / `edit_l3.py` |

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
  pipeline.py         run_level15, run_level2, export_columns, regrid_timestamps
  plots.py            interactive QC review plots (raw/L1.5/L2/L3 overlays)
  editor.py           interactive drag/click L3 editor -> manual_edits.csv rows
  config.py           config loaders + VarSpec
  diff.py             compare new output vs legacy output
config/<network>/     all network-specific knowledge lives here
  stations.csv
  variables.csv
  thresholds.csv      (your QC_L1rangecheck.csv, unchanged format)
  manual_edits.csv    the L3 change log
run_pipeline.py       driver: L1.5 + L2 for a network
run_l3.py             driver: apply the change log -> L3
edit_l3.py            driver: interactive editor for one station/group -> manual_edits.csv
selftest.py           synthetic end-to-end check
selftest_editor.py    non-interactive check of the editor's drag/click logic
```

## 2. Requirements

Python 3.10+, `pandas`, `numpy`, and `plotly` (for the QC review plots). `matplotlib`
is only needed if you use the interactive editor (`edit_l3.py`) — nothing else
imports it, so the rest of the pipeline runs fine without it. From the project
root, make sure `wxqc/` is on the path (running the drivers from the root
handles this).

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
| `sensor_group` | variables sharing this key are drawn on one QC plot instead of one each (e.g. `T`/`T2m`/`T10m`/`RH` each group their max/min/avg trio; `ST` groups all soil-temperature depths; `SM` groups all soil-moisture depths); blank = own plot |
| `sensor_role` | this variable's role within `sensor_group`: `min`, `max`, or `avg`. A group with all three gets a min ≤ avg ≤ max consistency check (see below); blank = not part of that check |
| `depends_on` | another `sensor_group` name; if any member of that group is Suspect at a timestamp, this variable is flagged Suspect too (see below), e.g. `RH` depending on `T` (same physical probe); blank = no dependency |
| `suspect_if_var` / `suspect_if_gt` | another variable's `value_col`; if its own **raw** value exceeds `suspect_if_gt`, this variable is flagged Suspect (see below), e.g. `T`/`T2m` flagged when snow depth buries the sensor; blank = no dependency |

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

Two guarantees hold regardless of `discontinuity_limit`: gaps longer than
`interp_limit` are never touched at all (left `NaN`, whatever their length), and
`interpolate(..., limit_area="inside")` never extrapolates past the series' own
first/last valid value in either direction — there's no value on one side to
interpolate *from*. Both hold in the plain `interp_limit`-only case too, and for
the `interpolate`/`interpolate_limit` manual-edit actions (`edits.py`), which use
the same construct.

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

### min/max/avg consistency check
Every other check in this engine looks at one column at a time — nothing compares
sibling variables from the same physical sensor, so an `avg` reading that's still
within its own valid range/ROC bounds could sail through unflagged even if it's
inconsistent with that same timestamp's `max`/`min`.

A `sensor_group` (see above) with all three `sensor_role`s present — `min`, `max`,
`avg` — gets checked at the end of every L1.5/L2 pass: wherever `avg` falls outside
`[min, max]`, or `min > max`, all three variables are flagged `S` (Suspect) at that
timestamp — including an `avg` that only ends up outside the bounds *after* being
gap-filled at L2, since this check runs after fill_short_gaps has already run.
Values are never altered — the engine has no independent way to know which of the
three is wrong, so it flags for review rather than guessing.

A sample missing **all three** isn't judged (that's just an ordinary missing
sample). A sample missing **some but not all** — a sibling that normally reports
for this station went missing just now, while another didn't — is flagged Suspect
on whichever ones are still present; the missing one(s) are already `S` from the
ordinary null-flagging. (A station that never reports one member of the trio at
all fails the `all(c in df.columns ...)` check entirely and the whole check is
skipped for that station — column-level absence isn't row-level incompleteness.)

Currently the `T`/`T2m`/`T10m`/`RH` groups use this; opt in another `sensor_group`
the same way `discontinuity_limit` is opted in — by filling in the config columns,
no engine code changes.

### cross-sensor dependency
Some variables come from a sensor that physically can't be trusted once another
one fails — e.g. RH is measured by the same probe as `T`, so a bad temperature
reading usually means the humidity reading from that instant is suspect too,
even if RH's own checks don't catch anything wrong with it.

Setting `depends_on` on a variable to another `sensor_group` name flags it `S`
(Suspect) at every timestamp where *any* member of that group is Suspect at this
level — including Suspect flags the min/max/avg consistency check itself just
added. Runs right after that check, at the end of every L1.5/L2 pass. Values are
never altered. Currently `RH_max_pct`/`RH_min_pct`/`RH_avg_pct` all depend on `T`;
wire up another pair the same way — no engine code changes.

### cross-variable value dependency
A related but distinct case: sometimes what makes a reading suspect isn't another
sensor's *provenance* but its *value* — e.g. deep snow can bury a near-surface
temperature sensor, so once snow depth passes some threshold, nearby temperature
readings are suspect regardless of whether they look internally consistent.

Setting `suspect_if_var`/`suspect_if_gt` on a variable flags it `S` (Suspect)
wherever that other variable's own **raw** reading exceeds the threshold. Runs
right after the `depends_on` pass, at the end of every L1.5/L2 pass. Deliberately
compares the raw value, not the QC'd `_L1`/`_L2` one: this describes a physical
site condition at that moment, independent of whether this level's own range
check later decided to reject that particular reading — and for some stations the
trigger variable's own range ceiling sits at or below the threshold, which would
make the QC'd value never exceed it at all (this was exactly the case for
`snowdepth_avg_mm` at several stations, whose own range ceiling sat at or below
the 2000mm trigger). Values are never altered.

Not currently wired to any variable. `T_max_C`/`T_min_C`/`T_avg_C` and
`T2m_max_C`/`T2m_min_C`/`T2m_avg_C` were flagged Suspect this way whenever raw
`snowdepth_avg_mm` exceeded 2000mm, but snow-depth review is manual and still
being refined — burial-driven temperature suspicion is judged at L3 instead for
now (`manual_edits.csv`), not asserted automatically at L1.5/L2. The mechanism
is untouched and ready to wire back up (`suspect_if_var=snowdepth_avg_mm`,
`suspect_if_gt=2000` on the relevant `variables.csv` rows) if that changes.

### timestamp continuity
Every positional check in this engine — `roc_check`'s `.diff()`, the run-length
gating in `fill_short_gaps` — silently assumes row *position* tracks elapsed
time: that row *i+1* is exactly one sample after row *i*. If the raw record has a
real outage that never got its own rows (rather than rows present with `NaN`
values), that assumption breaks: two readings hours or days apart end up
positionally adjacent, so `roc_check` can spuriously trip comparing across the
gap, and `fill_short_gaps` can interpolate a fabricated blend of two unrelated
moments into what looks like an ordinary short gap. (This is exactly what
happened at one station on 2023-05-21: a real ~31-hour gap with no rows at all
caused the next real reading to be ROC-flagged against a sample 31 hours
earlier, then "gap-filled" with a value blended from both sides of the outage.)

`run_level2` calls `regrid_timestamps` as its first step, before any checks run,
to fix this. It reindexes onto a continuous grid at the station's own sampling
interval (10 minutes) spanning the data's own timestamp range, inserting `NaN`
rows for every missing slot — so position tracks time again by the time
`fill_short_gaps`/`roc_check` see the data. `L1.5` is untouched (it keeps
whatever raw timestamps came in); `L2` and everything downstream (`L3`, plots)
see the gridded version. Two extra things come out of this:

- A `timestamp_generated` column (`True` for inserted rows, `False` for rows
  that were genuinely present) is carried through `export_columns` into the
  published `L2`/`L2QC` files, and shaded as an opaque gray band on the QC plots
  — visually distinct from the thinner "gap in the line" used for an ordinary
  missing value at a real timestamp.
- Any raw timestamp that isn't exactly on the 10-minute grid (e.g. `:07` instead
  of `:00`/`:10`/…) can't occupy a slot without corrupting the grid, so it's
  excluded from the regridded frame rather than silently colliding with a
  neighbor; `run_pipeline.py` prints a count per station if this happens. This
  is, in effect, already "masking out data that isn't on the 10-minute grid" —
  if you'd rather snap those timestamps to the nearest slot instead of excluding
  them, that's a one-line change to `regrid_timestamps`.

### thresholds.csv
Your existing `QC_L1rangecheck.csv` format, unchanged: rows are parameter keys
(`Tavg_max`, `Tavg_min`, `T_roc`, …), columns are stations. `range_key=Tavg`
reads `Tavg_min`/`Tavg_max`; `roc_key=T_roc` reads `T_roc` directly.

**`RH_roc`** is a rate-of-change limit shared by `RH_avg_pct`/`RH_max_pct`/`RH_min_pct`
(`roc_key=RH_roc` on all three in `variables.csv`), which previously had no ROC
check at all — only the `clamp_high_100` range action. Derived empirically, not
guessed: for each of the three variables independently, pulled 10-minute Δ
between consecutive **raw** readings (deliberately raw, not `_L2` — an already-QC'd
column risks circularity once a rate-of-change check exists that's nulled the very
tail being measured), fit an exponential to the well-populated P99–P99.9 range of
`|Δ|` (tens of thousands of points per station per variable), and found the point
where observed occurrences start *sustainedly* exceeding 2× that fit — the
signature of a second, heavier-tailed population (almost certainly sensor faults,
not real humidity swings) taking over from the genuine physical tail.

The three variables' own changepoints don't agree: `RH_min_pct` runs consistently
2–7 pct-pts higher than `RH_avg_pct`/`RH_max_pct` at every station (a genuinely
wider natural tail, not noise), so `RH_roc` is the **max of the three per
station** — the same one-shared-value-per-family pattern already used for
`T_roc`/`ST_roc`/`SM_roc`, and the safer direction to err: `RH_avg`/`RH_max`
never get over-flagged, at the cost of a somewhat looser check on them than
their own data alone would support. Values run 21.5–29.5 pct-pts/10min station
to station.

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
3. Append one row per edit to `config/<network>/manual_edits.csv` — by hand, or
   with `edit_l3.py` (below) for the visual/positional edits.
4. Run `run_l3.py`. L3 regenerates from L2 + the log; the log is your auditable,
   diffable record of every manual change.

### Interactive editor (`edit_l3.py`)
A desktop GUI for step 3 — generalizes the point-dragging snow-depth review
tool to any `sensor_group` (or single ungrouped variable), and instead of
writing a one-off correction file with no reasoning trail, every drag/click
becomes its own `manual_edits.csv` row: auditable and replayable, same as an
edit you typed in by hand.

```bash
python edit_l3.py     # edit the CONFIG block first: STATION, GROUP, OVERLAY_GROUPS, WATER_YEAR_RANGE
```

Uses the same visual language as the QC review plots (`VARIABLE_COLORS` for a
grouped variable's max/min/avg trio, etc.) so what you see matches what sent
you here. `OVERLAY_GROUPS` adds other `sensor_group`s as read-only subplots
underneath, sharing the x-axis, for cross-checking (e.g. reviewing `T` while
watching `snowdepth_avg_mm` for context).

Interactions (see `wxqc/editor.py` for the full model):

| Gesture | Effect |
|---|---|
| left-click-drag a point | moves it — queues a `clamp` edit |
| right-click a point | removes it — queues a `nan` edit |
| left-click-drag empty space | selects a window on the *active* variable, queues an `interpolate` edit across it |
| `1`–`9` | pick which variable in the group is active for window-interpolate |
| `z` | undo the most recently queued edit |
| `s` | save every queued edit to `manual_edits.csv`, clear the queue |

Only the visual/positional actions — `nan`, `clamp`, `interpolate` — are
produced this way; everything else `manual_edits.csv` supports (`offset`,
`scale`, `rolling_median`, `set_flag`, `interpolate_limit`, `nan_if_minute`,
`nan_if_gt`) is still added by hand. Every queued edit is replayed live with
the exact window-mask/action logic `apply_manual_edits` uses, so the plot
while editing is never an approximation of what saving will produce — it's the
same computation. `selftest_editor.py` drives this logic end-to-end
(simulated events, no display needed) and round-trips a save through
`apply_manual_edits` to confirm the two never diverge.

`edit_l3.py` reads real `Level_2QC` data from the OneDrive data folder but
reads/writes `config/<network>/{variables,manual_edits}.csv` from *this repo*
— consistent with every other config change this project: the repo is the
source of truth, and you sync `manual_edits.csv` to the live OneDrive config
yourself once you're happy with a batch of edits.

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
get no marker — the gap in the line is the signal. An opaque gray band marks
timestamp ranges `regrid_timestamps` inserted (see timestamp continuity, above)
— no data collection happened there at all, distinct from an ordinary missing
value at a real timestamp. These bands are drawn as **one combined SVG path**
(`_add_generated_regions`), not one Plotly shape per gap: a station whose real
record is fragmented into thousands of short gaps (rather than a few long ones)
turned `fig.add_vrect()`-per-gap into a multi-hour `write_html` hang — one
station-year alone had ~5,000 separate gap runs. The combined-path approach
renders any number of gaps as a single shape in well under a second.

Hover a marker to see *why* it's there — the reason (e.g. `range check (> max
100, clamped to 100)`, `rate-of-change (> 6/step)`, `gap too long to fill (9
samples > limit 6)`, `min/max/avg inconsistent (T)`, `depends on T (Suspect)`,
`manual edit: nan (frozen sensor)`) is re-derived by `explain.explain_flag`
from the same check logic pipeline.py actually ran, in the same order — no
inline reason-tracking needed in the checks themselves. This is best-effort:
a sensor handler's own internal logic (Geonor's spike clip, snow-depth
smoothing) is only attributed as `handler: <name>`, not its specific cause.
`plot_station`/`plot_variables` take an optional `thr=` (and `edits=` for an
L3 frame) to enable it; both `run_pipeline.py` and `run_l3.py` already pass
these.

Variables that share a `sensor_group` (variables.csv) land on one plot instead
of one each — e.g. `T_max_C`/`T_min_C`/`T_avg_C` are three statistics off the
same physical sensor, so they're one file (`T.html`); `RH` groups the same way;
`ST` puts every soil-temperature depth (`ST2`/`ST4`/`ST8`/`ST20`) on one plot,
and `SM` every soil-moisture depth (`SM4`/`SM8`) on another. On these grouped
plots color switches to distinguish the *variable* (a high-contrast color per
variable reads far better than dash alone), and solid/dash/dot/dash-dot
carries the level instead.

Open `index.html` and click a variable, or open a variable's HTML directly. Plots
are interactive (zoom into a storm, hover for values) via WebGL; plotly.js loads
from a CDN so files stay a few MB each. One file per variable (or sensor group)
is deliberate — a single combined file embeds every variable's full 10-minute
series and becomes too large for a browser. To plot ad hoc:

```python
from wxqc.plots import plot_variable, plot_variables, plot_station
plot_station(df, specs, "nep3", "plots/nep3")           # all variables/groups + index.html
plot_variables(df, [t_max_spec, t_min_spec, t_avg_spec], "nep3", "plots/nep3/T.html")
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
