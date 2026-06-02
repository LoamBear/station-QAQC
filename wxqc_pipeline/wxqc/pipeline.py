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
"""
import pandas as pd

from .checks import range_check, roc_check, fill_short_gaps
from .flags import derive_flags
from .sensors import HANDLERS


def _simple_checks(s, sp, thr, station):
    if sp.range_key:
        lo, hi = thr.range(station, sp.range_key)
        s = range_check(s, lo, hi, sp.range_action)
    if sp.roc_key:
        s = roc_check(s, thr.value(station, sp.roc_key))
    return s


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
    return df


def run_level2(df, specs, thr, station):
    present = [sp for sp in specs if (sp.value_col + "_L1") in df.columns]

    new = {}
    for sp in present:
        if sp.handler:
            continue
        s = df[sp.value_col + "_L1"].astype("float64").copy()
        if sp.interp_limit:
            s = fill_short_gaps(s, sp.interp_limit)
        new[sp.value_col + "_L2"] = _simple_checks(s, sp, thr, station)
    if new:
        df = pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)

    for sp in present:
        if not sp.handler:
            continue
        s = df[sp.value_col + "_L1"].astype("float64").copy()
        if sp.interp_limit:
            s = fill_short_gaps(s, sp.interp_limit)
        df[sp.value_col + "_L2"] = HANDLERS[sp.handler](df, s, thr, station, level="L2")

    for sp in present:
        derive_flags(df, sp.value_col + "_L2", sp.flag_col + "_L2",
                     sp.value_col + "_L1", prior_flag_col=sp.flag_col + "_L1")
    return df


def export_columns(df, specs, suffix, extra=("stationid",)):
    """Select the published columns for a level: datetime, each value+flag that
    exists, plus any always-carried extras present in the frame."""
    ordered = ["datetime_PST"] if "datetime_PST" in df.columns else []
    for sp in specs:
        for c in (sp.value_col + suffix, sp.flag_col + suffix):
            if c in df.columns:
                ordered.append(c)
    ordered += [c for c in extra if c in df.columns]
    return df[ordered]
