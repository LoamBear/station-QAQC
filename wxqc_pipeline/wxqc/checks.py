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
def fill_short_gaps(s: pd.Series, limit: int) -> pd.Series:
    """Linearly interpolate interior NaN runs of length <= `limit`. Longer gaps
    are left as NaN, and leading/trailing NaNs are never filled (can't
    extrapolate linearly).

    This is a clarified reimplementation of the legacy
    `rolling(7).apply(all-isnan).shift(-6)` construct, which had edge quirks.
    The behavior is: fill small gaps, leave big ones alone.
    """
    s = s.copy()
    isna = s.isna()
    if not isna.any():
        return s
    run_id = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(run_id).transform("size")
    fillable = isna & (run_len <= limit)
    filled = s.interpolate(method="linear", limit_area="inside")
    s[fillable] = filled[fillable]
    return s


# ---- smoothing ------------------------------------------------------------ #
def rolling_median(s: pd.Series, window: int, center: bool = True) -> pd.Series:
    return s.rolling(window, center=center, min_periods=1).median()
