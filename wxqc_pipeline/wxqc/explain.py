"""
Reconstructs *why* a point is flagged Edited or Suspect, for QC-plot hover
annotations. Every check in this engine is a pure Series->Series transform
with no memory of what it did -- by the time derive_flags sees a changed or
missing value, it has no idea which check caused it. Rather than thread
reason-tracking through every check (which would complicate their clean,
single-purpose contracts), this module re-derives the answer: given the same
inputs a level's checks actually used, which one explains this point's
outcome. Checks are tried in the same order pipeline.py actually runs them,
so the first applicable reason is the one that really fired.

Best-effort, not exhaustive: a sensor handler's *sensor-specific* logic
(Geonor's spike clip, snow-depth's dynamic smoothing) isn't individually
attributed beyond "handler: <name>" -- reproducing that here would duplicate
logic that only sensors.py should own. And a value that was gap-filled and
*then* failed the re-applied range/ROC check at L2 (rare) is reported as a
gap reason, not a range/ROC one, since the pre-fill state is what's used to
diagnose an L2 value that's still missing.
"""
import numpy as np
import pandas as pd

from .checks import minmax_avg_inconsistent, minmax_avg_incomplete


def _range_reason(prior: pd.Series, lo: float, hi: float, action: str) -> pd.Series:
    """Reason strings for whichever side of range_check's `action` changed
    `prior` -- either nulled it (explains a missing value) or clamped it to a
    bound (explains a kept-but-Edited value) -- empty string elsewhere.
    `keep` never touches a value, so it never explains anything."""
    reason = pd.Series("", index=prior.index)
    over, under = prior > hi, prior < lo
    if action == "nan":
        reason[over] = f"range check (> max {hi:g})"
        reason[under] = f"range check (< min {lo:g})"
    elif action == "clamp_high_100":
        reason[over] = f"range check (> max {hi:g}, clamped to {hi:g})"
        reason[under] = f"range check (< min {lo:g})"
    elif action == "nan_or_zero":
        reason[over] = f"range check (> max {hi:g})"
        reason[under] = f"range check (< min {lo:g}, clamped to 0)"
    elif action == "clamp":
        reason[over] = f"range check (> max {hi:g}, clamped to {hi:g})"
        reason[under] = f"range check (< min {lo:g}, clamped to {lo:g})"
    return reason


def _roc_reason(prior: pd.Series, limit: float) -> pd.Series:
    reason = pd.Series("", index=prior.index)
    reason[prior.diff().abs() > limit] = f"rate-of-change (> {limit:g}/step)"
    return reason


def _gap_reason(prior: pd.Series, limit: int, roc_limit: float = None,
                discontinuity_limit: int = None) -> pd.Series:
    """Mirrors checks.fill_short_gaps' fillability logic exactly, but returns
    *why* a gap position was left missing instead of the filled series."""
    reason = pd.Series("", index=prior.index)
    isna = prior.isna()
    if not isna.any():
        return reason
    run_id = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(run_id).transform("size")

    too_long = isna & (run_len > limit)
    if too_long.any():
        reason[too_long] = ("gap too long to fill (" + run_len[too_long].astype(int).astype(str)
                            + f" samples > limit {limit})")

    fillable = isna & (run_len <= limit)
    if roc_limit is not None and fillable.any():
        jump = (prior.bfill() - prior.ffill()).abs()
        discontinuous = fillable & (jump > roc_limit * run_len)
        reason[discontinuous] = "discontinuity (edge jump exceeds rate-of-change budget)"
        fillable = fillable & ~discontinuous

    if discontinuity_limit is not None and fillable.any():
        notna = ~isna
        free_floating = notna & isna.shift(1, fill_value=True) & isna.shift(-1, fill_value=True)
        anchor_before_ff = free_floating.shift(1, fill_value=False)
        anchor_after_ff = free_floating.shift(-1, fill_value=False)
        run_has_ff_anchor = (anchor_before_ff | anchor_after_ff).groupby(run_id).transform("max")
        unanchored = fillable & (run_len > discontinuity_limit) & run_has_ff_anchor
        reason[unanchored] = "gap anchored on a free-floating point"

    return reason


def _group_reason(df: pd.DataFrame, sp, specs, suffix: str) -> pd.Series:
    """Cross-variable reasons: min/max/avg consistency, depends_on, and
    suspect_if_var/suspect_if_gt -- the three passes pipeline.py runs after
    every per-variable check, in that order."""
    reason = pd.Series("", index=df.index)

    if sp.sensor_group and sp.sensor_role:
        siblings = {s.sensor_role: s for s in specs
                   if s.sensor_group == sp.sensor_group and s.sensor_role}
        lo, hi, avg = siblings.get("min"), siblings.get("max"), siblings.get("avg")
        if lo and hi and avg:
            lo_c, hi_c, avg_c = lo.value_col + suffix, hi.value_col + suffix, avg.value_col + suffix
            if all(c in df.columns for c in (lo_c, hi_c, avg_c)):
                inconsistent = minmax_avg_inconsistent(df[lo_c], df[hi_c], df[avg_c])
                incomplete = minmax_avg_incomplete(df[lo_c], df[hi_c], df[avg_c])
                reason[inconsistent] = f"min/max/avg inconsistent ({sp.sensor_group})"
                reason = reason.mask((reason == "") & incomplete,
                                     f"sibling missing ({sp.sensor_group})")

    if sp.depends_on:
        source_suspect = pd.Series(False, index=df.index)
        for s in specs:
            if s.sensor_group == sp.depends_on:
                fcol = s.flag_col + suffix
                if fcol in df.columns:
                    source_suspect |= (df[fcol] == "S")
        reason = reason.mask((reason == "") & source_suspect,
                             f"depends on {sp.depends_on} (Suspect)")

    if sp.suspect_if_var and sp.suspect_if_gt is not None and sp.suspect_if_var in df.columns:
        trigger = df[sp.suspect_if_var] > sp.suspect_if_gt
        reason = reason.mask((reason == "") & trigger,
                             f"{sp.suspect_if_var} > {sp.suspect_if_gt:g}")

    return reason


def _manual_edit_reason(df: pd.DataFrame, sp, edits: pd.DataFrame, station: str,
                        dt_col: str) -> pd.Series:
    """The manual_edits.csv row responsible for each L3 timestamp -- later
    rows overwrite earlier ones in the reason too, matching apply order."""
    reason = pd.Series("", index=df.index)
    rows = edits[(edits["station"] == station) & (edits["value_col"] == sp.value_col)]
    for _, e in rows.iterrows():
        m = (df[dt_col] >= pd.to_datetime(e["start"])) if e.get("start") else pd.Series(True, index=df.index)
        if e.get("end"):
            m &= df[dt_col] < pd.to_datetime(e["end"])
        note = e.get("note", "")
        reason[m] = f"manual edit: {e['action']}" + (f" ({note})" if note else "")
    return reason


def explain_flag(df: pd.DataFrame, sp, specs, thr, station: str, suffix: str,
                 prior_suffix: str, edits: pd.DataFrame = None,
                 dt_col: str = "datetime_PST") -> pd.Series:
    """Reason string for why `sp`'s flag at `suffix` (e.g. "_L2") is 'E' or
    'S' at each row -- empty string where neither applies. `prior_suffix` is
    the level suffix that fed this one ("" for raw -> _L1, "_L1" for
    _L1 -> _L2, "_L2" for _L2 -> _L3)."""
    value_col = sp.value_col + suffix
    prior_col = sp.value_col + prior_suffix
    if value_col not in df.columns:
        return pd.Series("", index=df.index)
    prior = df[prior_col] if prior_col in df.columns else pd.Series(np.nan, index=df.index)
    reason = pd.Series("", index=df.index)

    if suffix == "_L3":
        if edits is not None:
            reason = _manual_edit_reason(df, sp, edits, station, dt_col)
    elif sp.handler:
        cur = df[value_col]
        changed = (cur != prior) & ~(cur.isna() & prior.isna())
        reason[changed] = f"handler: {sp.handler}"
    else:
        if sp.range_key:
            lo, hi = thr.range(station, sp.range_key)
            reason = reason.mask(reason == "", _range_reason(prior, lo, hi, sp.range_action))
        if sp.roc_key:
            reason = reason.mask(reason == "", _roc_reason(prior, thr.value(station, sp.roc_key)))
        if suffix == "_L2" and sp.interp_limit:
            roc_limit = (thr.value(station, sp.roc_key)
                        if sp.discontinuity_limit and sp.roc_key else None)
            reason = reason.mask(reason == "", _gap_reason(
                prior, sp.interp_limit, roc_limit, sp.discontinuity_limit))

    reason = reason.mask(reason == "", _group_reason(df, sp, specs, suffix))
    reason = reason.mask((reason == "") & prior.isna(), "no data at prior level")
    return reason
