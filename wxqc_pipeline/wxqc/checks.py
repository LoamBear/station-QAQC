"""
Generic QC checks. Every function operates on a pandas Series and returns a new
Series; nothing here knows any column name. This is what makes the engine reusable
across networks with different naming conventions -- the per-network config supplies
the column names and thresholds, these functions just do the work.
"""
import numpy as np
import pandas as pd


# ---- range checks --------------------------------------------------------- #
# `action` strings come straight from variables.csv. Add new presets here; the
# rest of the pipeline does not need to change.

def range_check(s: pd.Series, lo: float, hi: float, action: str = "nan") -> pd.Series:
    s = s.copy()
    over = s > hi
    under = s < lo
    if action == "nan":                 # out of range -> missing
        s[over | under] = np.nan
    elif action == "clamp_high_100":    # RH: above max -> 100, below min -> missing
        s[over] = 100.0
        s[under] = np.nan
    elif action == "nan_or_zero":       # SW radiation: above max -> missing, below min -> 0
        s[over] = np.nan
        s[under] = 0.0
    elif action == "clamp":             # pin to the bounds instead of removing
        s[over] = hi
        s[under] = lo
    elif action == "keep":              # range recorded but not enforced
        pass
    else:
        raise ValueError(f"unknown range action: {action!r}")
    return s


# ---- rate-of-change check ------------------------------------------------- #
def roc_check(s: pd.Series, limit: float) -> pd.Series:
    """Flag a sample as missing when it jumps more than `limit` from the prior
    sample. Matches the legacy `.diff()` semantics (the point *after* the jump
    is removed)."""
    s = s.copy()
    d = s.diff()
    s[d.abs() > limit] = np.nan
    return s


# ---- gap interpolation ---------------------------------------------------- #
def fill_short_gaps(s: pd.Series, limit: int, roc_limit: float = None,
                    discontinuity_limit: int = None) -> pd.Series:
    """Linearly interpolate interior NaN runs of length <= `limit`. Longer gaps
    are left as NaN, and leading/trailing NaNs are never filled (can't
    extrapolate linearly).

    This is a clarified reimplementation of the legacy
    `rolling(7).apply(all-isnan).shift(-6)` construct, which had edge quirks.
    The behavior is: fill small gaps, leave big ones alone.

    If `roc_limit` is given, a gap (of any length up to `limit`) is also
    excluded from filling (left as NaN, which flows into the 'S' Suspect flag)
    when the jump between the values just before and just after it exceeds
    `roc_limit * gap_length` -- i.e. a step change too big to be explained by
    the variable's own rate-of-change threshold, most likely a sensor
    discontinuity rather than a real gap to bridge. `roc_limit=None` (the
    default) reproduces plain length-gated fill.

    If `discontinuity_limit` is also given (< `limit`), gaps longer than it
    face one more requirement: neither boundary value may itself be a
    "free-floating" sample -- a single valid reading flanked by NaN on both
    sides. Such a lone point isn't a trustworthy anchor for a several-sample
    fill; if a gap is bounded by one, it's left for review instead.
    """
    s = s.copy()
    isna = s.isna()
    if not isna.any():
        return s
    run_id = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(run_id).transform("size")
    fillable = isna & (run_len <= limit)

    if roc_limit is not None and fillable.any():
        jump = (s.bfill() - s.ffill()).abs()
        discontinuous = fillable & (jump > roc_limit * run_len)
        fillable = fillable & ~discontinuous

    if discontinuity_limit is not None and fillable.any():
        notna = ~isna
        free_floating = notna & isna.shift(1, fill_value=True) & isna.shift(-1, fill_value=True)
        anchor_before_ff = free_floating.shift(1, fill_value=False)
        anchor_after_ff = free_floating.shift(-1, fill_value=False)
        run_has_ff_anchor = (anchor_before_ff | anchor_after_ff).groupby(run_id).transform("max")
        unanchored = fillable & (run_len > discontinuity_limit) & run_has_ff_anchor
        fillable = fillable & ~unanchored

    filled = s.interpolate(method="linear", limit_area="inside")
    s[fillable] = filled[fillable]
    return s


# ---- smoothing ------------------------------------------------------------ #
def rolling_median(s: pd.Series, window: int, center: bool = True,
                   min_frac: float = 0.5) -> pd.Series:
    min_periods = max(1, int(round(window * min_frac)))
    return s.rolling(window, center=center, min_periods=min_periods).median()


# ---- cross-variable consistency -------------------------------------------- #
def minmax_avg_inconsistent(min_s: pd.Series, max_s: pd.Series, avg_s: pd.Series) -> pd.Series:
    """Boolean mask, True wherever min/max/avg readings from the same sensor at
    the same timestamp don't make physical sense: avg outside [min, max], or
    min > max. A sample missing any of the three can't be judged and is never
    flagged here. Values are never touched -- the caller decides what to do
    with the flagged positions (this engine flags them Suspect, doesn't alter
    the readings)."""
    have_all = min_s.notna() & max_s.notna() & avg_s.notna()
    bad = (avg_s < min_s) | (avg_s > max_s) | (min_s > max_s)
    return bad.fillna(False) & have_all


def minmax_avg_incomplete(min_s: pd.Series, max_s: pd.Series, avg_s: pd.Series) -> pd.Series:
    """Boolean mask, True wherever at least one of min/max/avg is missing while
    at least one other is present -- a partial reading from a sensor_group
    whose columns all structurally exist for this station (the caller only
    runs this where that's true), so a missing member here means a sibling
    that normally reports went missing just now, not that the group is simply
    absent from this network. A timestamp where all three are missing isn't
    flagged -- that's an ordinary missing sample, not a partial one."""
    present = pd.concat({"min": min_s.notna(), "max": max_s.notna(), "avg": avg_s.notna()}, axis=1)
    return present.any(axis=1) & ~present.all(axis=1)
