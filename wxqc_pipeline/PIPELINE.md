# NevCAN wxqc — QAQC Pipeline Map

Text map of the automated QAQC pipeline: what runs today, and what's planned or dormant.
See `README.md` for the full narrative writeup this summarizes.

```text
NevCAN wxqc — QAQC PIPELINE MAP
Legend:  ●  live now      ○  planned / dormant (not currently wired)

CONFIG (drives everything below — no engine code names a variable)
  stations.csv      — station_id, por_start/end, timezone, sample_freq
                       (native logging interval, e.g. "10min"/"1H"/"15min" —
                       controls the Stage 2 regrid, per station)
  variables.csv     — per-variable: range/roc keys, interp/discontinuity limits,
                       handler, sensor_group/role, depends_on, suspect_if_var/gt
  thresholds.csv    — per-station min/max/roc values, keyed by variables.csv
  manual_edits.csv  — the L3 change log (one row per human edit)
  │
  ▼
RAW   Level_1/{station}_WY{year}_L1.csv   (10-min for NevCAN, per station-year —
                                            stations.csv's sample_freq elsewhere)
  │
  ▼
STAGE 1 — L1.5  (run_level15 — per variable, then cross-variable)
  ●  range_check    — variables.csv range_action × thresholds.csv min/max
                       nan · clamp_high_100 (RH) · nan_or_zero (SW) · clamp · keep
  ●  roc_check      — thresholds.csv *_roc, wherever roc_key is set
                       T_roc · BP_roc · ST_roc · SM_roc · snowdepth_roc · RH_roc
  ●  sensor handler — geonor_accumulate (precip spike clip) · snowdepth_dynamic
  ── cross-variable passes (run once, after every variable above) ──
  ●  group consistency    — avg ∉ [min,max] ⇒ flag the T/T2m/T10m/RH trio Suspect
  ●  group incompleteness — some-but-not-all of a trio missing ⇒ Suspect
  ●  depends_on           — RH ⇐ T Suspect (same physical probe)
  ○  suspect_if_var/gt    — DORMANT: was T/T2m ⇐ raw snowdepth_avg_mm > 2000mm;
                             mechanism intact, removed from config — burial
                             suspicion is judged manually at L3 for now
  │
  ▼   Level_1.5/  +  Level_1.5QC/
STAGE 2 — regrid_timestamps  (first step of run_level2)
  ●  reindex onto a continuous grid at the station's own sample_freq
     (stations.csv — 10min for NevCAN); real gaps → NaN rows,
     timestamp_generated=True (fixes positional-adjacency bugs in ROC/gap-fill)
  ●  off-grid raw stamps excluded from the grid, counted per station
  ○  PLANNED: snap off-grid stamps to the nearest slot instead of excluding
  │
  ▼
STAGE 3 — L2  (run_level2)
  ●  fill_short_gaps         — linear-interpolate interior gaps ≤ interp_limit;
                                edges and longer gaps stay NaN. interp_limit=6
                                for nearly every variable, so a gap of 6+
                                readings is never filled, for any of them —
                                this is the general rule.
  ●  discontinuity-aware fill — an ADDITIONAL magnitude gate layered on top
                                of the rule above (not instead of it): jump vs
                                prior/next > roc_limit×gap_length ⇒ stays NaN,
                                flagged Suspect for review; + free-floating-
                                anchor guard on longer gaps. Live on:
                                BP_avg_mbar only (discontinuity_limit=3).
     ○  PLANNED: opt more variables into this additional gate over time
  ●  re-run Stage-1 checks on the filled series (range → ROC → handler →
     group consistency → depends_on → suspect_if_var, same order)
  │
  ▼   Level_2/  +  Level_2QC/
STAGE 4 — L3  (run_l3.py — human-in-the-loop, annual)
  ●  apply_manual_edits() — replay manual_edits.csv in file order,
     deterministic (L3 regenerates from L2 + the log every time)
       nan · interpolate · interpolate_limit · nan_if_minute · nan_if_gt ·
       offset · scale · clamp · rolling_median · set_flag
  ●  authored by hand (CSV) or via the edit_l3.py desktop GUI
       drag = clamp · right-click = nan · drag-empty = interpolate
  ○  PLANNED: GUI coverage for offset/scale/rolling_median/set_flag
              (hand-edited CSV only, today)
  ○  PLANNED: snow-depth-driven T/T2m suspicion judged HERE once snow-depth
              processing itself matures — today it's fully manual, no
              automated hook at all (see SNOW DEPTH below)
  │
  ▼   Level_3/{station}_L3.csv
FLAGS (every stage)   ●  O observed · E edited · S suspect — sticky by default
                          once set, later levels keep the provenance
  ○  PLANNED: split "removed" from "missing" into its own M flag (currently
              both collapse to S)
  │
  ▼
QC REVIEW PLOTS
  ●  plot_station / plot_variables — one HTML per sensor_group + index.html
  ●  raw → L1.5 → L2 (pipeline run) or + L3 (annual run)
  ●  Edited/Suspect quality markers; generated-timestamp band drawn as one
     combined SVG shape (thousands of gaps render in <1s, not hours)
  ●  hover explains *why* — explain.explain_flag re-derives the reason from
     the same check logic pipeline.py actually ran, in the same order
     (best-effort: a handler's internal logic only attributes as "handler: x")

VALIDATION
  ●  wxqc.diff.compare_csv(new, legacy) — confirms intended vs. accidental drift

────────────────────────────────────────────────────────────────────────
THRESHOLD-DERIVATION METHOD   (built for RH_roc — a reusable recipe, not
                                engine code)
  1. Δ between consecutive RAW readings, 10 min apart — raw, not QC'd, so a
     check can't be measured against a tail it's already censored
  2. Fit an exponential to the |Δ| exceedance curve over the well-populated
     P99–P99.9 range
  3. Extrapolate; find where observed occurrences sustainedly exceed 2× the
     fit → the boundary between genuine physical tail and sensor-fault
     population
  4. One shared value per station/family = max across sibling variables
     (RH: avg/max/min's own changepoints — min ran consistently widest)
  ○  PLANNED: apply this same statistical method to (re-)derive T_roc /
     BP_roc / ST_roc / SM_roc / snowdepth_roc, which currently come from
     legacy/domain values rather than this approach

────────────────────────────────────────────────────────────────────────
SNOW DEPTH  (acknowledged as the least-mature part of the pipeline)
  ●  snowdepth_dynamic handler + plain range check — that's the entire
     automated treatment today
  ○  PLANNED: incorporate logic from the legacy interactive scripts
     (config/nevcan/legacy_reference/NevCAN_snowdepth_L3*.ipy) — reviewed
     this session, not yet ported
  ○  PLANNED: re-wire suspect_if_var once snow-depth QC is trusted enough
     to drive other variables' flags again
```
