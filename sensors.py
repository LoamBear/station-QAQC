"""
Sensor-specific handlers -- the network's "gnarly" logic that does not fit a
tabular check. A variable references one of these by name in variables.csv
(the `handler` column). A network that lacks a given sensor simply never
references its handler.

Each handler has the signature:
    handler(df, s, thr, station, level) -> pd.Series
where `s` is the working series for the variable at this level and `df` gives
access to sibling columns (e.g. the Geonor std-dev, precip, temperature).

These were ported from the legacy NevCAN L1.5/L2 scripts. Two spots reproduce
behavior that looked unintentional in the originals -- they are marked CONFIRM
so you can decide before this goes to production.
"""
import numpy as np
import pandas as pd

from .checks import range_check, roc_check, rolling_median


def _col(df, *candidates):
    """Return the first candidate column that exists, else None."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def geonor_accumulate(df, s, thr, station, level):
    """Geonor accumulated precipitation cleanup.

    L1.5: drop noisy samples (hz std-dev > 2), drop sub-minimum values, take the
    incremental difference, zero out spikes, re-accumulate, backfill short gaps.
    L2 and later: pass through (already accumulated).
    """
    if level != "L1":
        return s

    p_min, _p_max = thr.range(station, "P")
    std_col = _col(df, "P_geonor_hzstdv")

    g = s.copy()
    if std_col is not None:
        g[df[std_col] > 2] = np.nan          # noisy weighing-gauge reading
    g[g < p_min] = np.nan
    g = g.diff()
    # CONFIRM: the legacy code clipped spikes on g.diff() (a *second* difference).
    # Reproduced as-is; switch to g directly if that was not intended.
    g[g.diff() > 10] = 0.0
    g[g.diff() < -5] = 0.0
    g = g.cumsum()
    g = g.interpolate(method="linear", limit_area="inside", limit=144)
    return g


def snowdepth_dynamic(df, s, thr, station, level):
    """Snow-depth cleanup.

    L1.5: apply the depth offset, then range + rate-of-change checks.
    L2: dynamic smoothing -- the smoothing window tightens when recent precip or
        warm temperatures mean the surface is actually changing, and widens to
        kill diurnal flutter when it is not.
    """
    if level == "L1":
        offset = thr.value(station, "snowdepth_offset")
        lo, hi = thr.range(station, "snowdepth")
        roc = thr.value(station, "snowdepth_roc")
        s = s - offset
        s = range_check(s, lo, hi, "nan")
        s = roc_check(s, roc)
        return s

    # ---- L2 dynamic smoothing ----
    raw = s.copy()
    auto6 = rolling_median(raw, 36)
    auto12 = rolling_median(raw, 72)
    auto24 = rolling_median(raw, 144)

    precip_col = _col(df, "Precip_mm_L2", "Precip_mm_L1")
    temp_col = _col(df, "T_avg_C_L2", "T_avg_C_L1")

    out = auto24.copy()
    if precip_col is not None:
        p = df[precip_col]
        out = out.where(~(p.diff(144) > 0), auto12)
        out = out.where(~(p.diff(72) > 0), auto6)
        out = out.where(~(p.diff(12) > 0), raw)
    out = out.where(~(out.diff(36) > 50), raw)
    if temp_col is not None:
        max24 = df[temp_col].rolling(144, center=True, min_periods=1).max()
        out = out.where(~(max24 > 5), auto24)   # warm day -> no real accumulation
    out = out.interpolate(method="linear", limit_area="inside", limit=144)
    return out


# Name -> function. This is the extension point: add a network's sensor logic
# here and reference it from variables.csv.
HANDLERS = {
    "geonor_accumulate": geonor_accumulate,
    "snowdepth_dynamic": snowdepth_dynamic,
}
