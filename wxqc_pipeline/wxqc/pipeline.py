"""
Automated-level drivers (L1.5 and L2).

L1.5 derives `_L1` columns from the raw measurements: range check, rate-of-change
check, sensor handler (if any), then provenance flags.

L2 derives `_L2` columns from `_L1`: short-gap interpolation, re-applied range
and ROC checks, sensor handler (if any), then provenance flags.

Both are pure config dispatch -- they loop the VarSpecs and call the generic
checks. No variable name appears in this file. Simple (non-handler) variables
are computed in a batch and joined at once; handler variables are computed after,
so a handler may read sibling columns produced earlier this level (the snow-depth
handler reads the L2 precip/temperature columns).

Before L2's checks run, `run_level2` regrids the frame onto a continuous
10-minute timeline (see `regrid_timestamps`) -- L1.5 keeps whatever raw
timestamps came in, so this is the one place row *position* is made to track
elapsed time, which every positional check (roc_check's .diff(), the run-length
gating in fill_short_gaps) silently assumes.

After per-variable flags are derived, three cross-variable passes run. First,
any sensor_group with a full min/max/avg trio (sensor_role in variables.csv) is
checked for internal consistency -- avg outside [min, max], min > max, or one
of the three missing while another is present. Violations are flagged Suspect
on all three variables; the readings themselves are never altered, since a QC
engine correcting one number against two others it hasn't independently
verified would be guessing. Second, any variable with a depends_on group (e.g.
RH depending on T, the same physical probe) is flagged Suspect wherever that
group is Suspect, including Suspect flags the consistency pass itself just
added. Third, any variable with a suspect_if_var/suspect_if_gt pair is flagged
Suspect wherever that other variable's own value exceeds the threshold (e.g.
T/T2m flagged Suspect whenever snow depth is deep enough to bury the sensor).
"""
import pandas as pd

from .checks import (range_check, roc_check, fill_short_gaps,
                     minmax_avg_inconsistent, minmax_avg_incomplete)
from .flags import derive_flags, SUSPECT
from .sensors import HANDLERS


def regrid_timestamps(df, dt_col="datetime_PST", freq="10min"):
    """Reindex df onto a continuous `freq` grid spanning its own timestamp
    range, inserting NaN rows for any missing slots. Returns
    (regridded_df, off_grid_df):

    - regridded_df gains a `timestamp_generated` column: True for rows that
      did not exist in the input (inserted to fill a gap), False for rows that
      were genuinely present. Every other column is NaN on generated rows.
    - off_grid_df holds any input rows whose timestamp is not exactly on the
      `freq` grid (e.g. :07 instead of :00/:10/...). They cannot occupy a slot
      without corrupting the grid's positional/temporal alignment, so they are
      excluded from regridded_df and returned separately for review.

    Without this, a real multi-hour data outage that never got its own rows is
    invisible to every check that assumes row position tracks elapsed time: it
    can both spuriously trip roc_check (comparing across the gap as if it were
    one step) and cause fill_short_gaps to interpolate a fabricated blend of
    two unrelated moments into what looks like an ordinary short gap.
    """
    df = df.sort_values(dt_col).reset_index(drop=True)
    on_grid = (df[dt_col] - df[dt_col].dt.floor(freq)) == pd.Timedelta(0)
    off_grid_df = df.loc[~on_grid].copy()
    grid_df = df.loc[on_grid].copy()

    if grid_df.empty:
        grid_df["timestamp_generated"] = pd.Series(dtype=bool)
        return grid_df, off_grid_df

    original_stamps = set(grid_df[dt_col])
    full_index = pd.date_range(grid_df[dt_col].min(), grid_df[dt_col].max(), freq=freq)
    grid_df = grid_df.set_index(dt_col).reindex(full_index)
    grid_df.index.name = dt_col
    grid_df["timestamp_generated"] = ~grid_df.index.isin(original_stamps)
    grid_df = grid_df.reset_index()
    return grid_df, off_grid_df


def _simple_checks(s, sp, thr, station):
    if sp.range_key:
        lo, hi = thr.range(station, sp.range_key)
        s = range_check(s, lo, hi, sp.range_action)
    if sp.roc_key:
        s = roc_check(s, thr.value(station, sp.roc_key))
    return s


def _apply_group_consistency(df, specs, suffix):
    """For every sensor_group with a min+max+avg trio (by sensor_role) whose
    columns all exist at this level, flag Suspect wherever avg falls outside
    [min, max], min > max, or one of the three is missing while another is
    present (a sibling that normally reports for this station went missing
    just now). Values are untouched."""
    groups = {}
    for sp in specs:
        if sp.sensor_group and sp.sensor_role:
            groups.setdefault(sp.sensor_group, {})[sp.sensor_role] = sp

    for group_specs in groups.values():
        lo, hi, avg = group_specs.get("min"), group_specs.get("max"), group_specs.get("avg")
        if not (lo and hi and avg):
            continue
        lo_col, hi_col, avg_col = lo.value_col + suffix, hi.value_col + suffix, avg.value_col + suffix
        if not all(c in df.columns for c in (lo_col, hi_col, avg_col)):
            continue
        bad = (minmax_avg_inconsistent(df[lo_col], df[hi_col], df[avg_col])
              | minmax_avg_incomplete(df[lo_col], df[hi_col], df[avg_col]))
        if not bad.any():
            continue
        for sp in (lo, hi, avg):
            fcol = sp.flag_col + suffix
            if fcol in df.columns:
                df.loc[bad, fcol] = SUSPECT
    return df


def _apply_group_dependency(df, specs, suffix):
    """For every variable with a depends_on group, flag it Suspect wherever any
    member of that group is Suspect at this level. Values are untouched."""
    by_group = {}
    for sp in specs:
        if sp.sensor_group:
            by_group.setdefault(sp.sensor_group, []).append(sp)

    for sp in specs:
        if not sp.depends_on:
            continue
        fcol = sp.flag_col + suffix
        if fcol not in df.columns:
            continue
        source_suspect = pd.Series(False, index=df.index)
        for source in by_group.get(sp.depends_on, []):
            source_fcol = source.flag_col + suffix
            if source_fcol in df.columns:
                source_suspect |= (df[source_fcol] == SUSPECT)
        if source_suspect.any():
            df.loc[source_suspect, fcol] = SUSPECT
    return df


def _apply_value_dependency(df, specs, suffix):
    """For every variable with a suspect_if_var/suspect_if_gt pair, flag it
    Suspect wherever that other variable's own *raw* value exceeds the
    threshold -- e.g. T/T2m flagged Suspect whenever snow depth is deep enough
    to bury the sensor. Deliberately compares against the raw reading, not the
    QC'd _L1/_L2 value: this describes a physical site condition, independent
    of whether this level's own range check later decided to reject that
    particular reading -- and for some stations the range ceiling on the
    trigger variable sits at or below suspect_if_gt, which would make the
    QC'd value never exceed it at all. Values are untouched."""
    for sp in specs:
        if not sp.suspect_if_var or sp.suspect_if_gt is None:
            continue
        fcol = sp.flag_col + suffix
        if fcol not in df.columns or sp.suspect_if_var not in df.columns:
            continue
        trigger = df[sp.suspect_if_var] > sp.suspect_if_gt
        if trigger.any():
            df.loc[trigger, fcol] = SUSPECT
    return df


def _fill_gaps(s, sp, thr, station):
    if not sp.interp_limit:
        return s
    roc_limit = (thr.value(station, sp.roc_key)
                if sp.discontinuity_limit and sp.roc_key else None)
    return fill_short_gaps(s, sp.interp_limit, roc_limit=roc_limit,
                           discontinuity_limit=sp.discontinuity_limit)


def run_level15(df, specs, thr, station):
    present = [sp for sp in specs if sp.value_col in df.columns]

    # pass 1: simple variables, joined in one shot
    new = {}
    for sp in present:
        if sp.handler:
            continue
        s = df[sp.value_col].astype("float64").copy()
        new[sp.value_col + "_L1"] = _simple_checks(s, sp, thr, station)
    if new:
        df = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)

    # pass 2: handler variables (may read sibling columns)
    for sp in present:
        if not sp.handler:
            continue
        s = df[sp.value_col].astype("float64").copy()
        df[sp.value_col + "_L1"] = HANDLERS[sp.handler](df, s, thr, station, level="L1")

    # flags
    for sp in present:
        derive_flags(df, sp.value_col + "_L1", sp.flag_col + "_L1",
                     sp.value_col, prior_flag_col=None)
    df = _apply_group_consistency(df, specs, "_L1")
    df = _apply_group_dependency(df, specs, "_L1")
    df = _apply_value_dependency(df, specs, "_L1")
    return df


def run_level2(df, specs, thr, station, dt_col="datetime_PST"):
    df, off_grid = regrid_timestamps(df, dt_col=dt_col)
    if "stationid" in df.columns:
        df["stationid"] = station
    if len(off_grid):
        print(f"  {station}: {len(off_grid)} off-grid timestamp(s) excluded from the L2 regrid")

    present = [sp for sp in specs if (sp.value_col + "_L1") in df.columns]

    new = {}
    for sp in present:
        if sp.handler:
            continue
        s = df[sp.value_col + "_L1"].astype("float64").copy()
        s = _fill_gaps(s, sp, thr, station)
        new[sp.value_col + "_L2"] = _simple_checks(s, sp, thr, station)
    if new:
        df = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)

    for sp in present:
        if not sp.handler:
            continue
        s = df[sp.value_col + "_L1"].astype("float64").copy()
        s = _fill_gaps(s, sp, thr, station)
        df[sp.value_col + "_L2"] = HANDLERS[sp.handler](df, s, thr, station, level="L2")

    for sp in present:
        derive_flags(df, sp.value_col + "_L2", sp.flag_col + "_L2",
                     sp.value_col + "_L1", prior_flag_col=sp.flag_col + "_L1")
    df = _apply_group_consistency(df, specs, "_L2")
    df = _apply_group_dependency(df, specs, "_L2")
    df = _apply_value_dependency(df, specs, "_L2")
    return df


def export_columns(df, specs, suffix, extra=("stationid", "timestamp_generated")):
    """Select the published columns for a level: datetime, each value+flag that
    exists, plus any always-carried extras present in the frame."""
    ordered = ["datetime_PST"] if "datetime_PST" in df.columns else []
    for sp in specs:
        for c in (sp.value_col + suffix, sp.flag_col + suffix):
            if c in df.columns:
                ordered.append(c)
    ordered += [c for c in extra if c in df.columns]
    return df[ordered]
