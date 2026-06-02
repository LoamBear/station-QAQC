"""
Manual-edit engine (Level 3).

L3 is a human-in-the-loop annual review. Instead of hand-written np.where lines,
each edit is a row in manual_edits.csv:

    station, value_col, start, end, action, param, note, water_year, applied_by, applied_on

`start`/`end` bound the edit window (end exclusive; leave end blank for "from
start onward"). Actions and their `param`:

    nan             -> set window to missing                     (param ignored)
    interpolate     -> linear-interpolate across the window      (param ignored)
    offset          -> add param to the window                   (param = number)
    scale           -> multiply window by param                  (param = number; calibration)
    clamp           -> set window to the constant param          (param = number)
    rolling_median  -> replace window with centered rolling med  (param = window size, samples)
    set_flag        -> set the flag column in the window to param (param = 'S', etc.)

Edits are applied in file order, so later rows can build on earlier ones. L3 is
regenerated deterministically from L2 + this log; appending a water year's review
just adds rows.
"""
import numpy as np
import pandas as pd

from .flags import derive_flags


def _window_mask(df, dt_col, start, end):
    m = pd.Series(True, index=df.index)
    if start:
        m &= df[dt_col] >= pd.to_datetime(start)
    if end:
        m &= df[dt_col] < pd.to_datetime(end)
    return m


def apply_manual_edits(df, edits, specs, station,
                       suffix="_L3", prior_suffix="_L2", dt_col="datetime_PST"):
    flag_of = {sp.value_col: sp.flag_col for sp in specs}

    # Seed every _L3 value and flag column from _L2 in one join so untouched
    # variables carry through unchanged.
    seed = {}
    for sp in specs:
        v3, v2 = sp.value_col + suffix, sp.value_col + prior_suffix
        f3, f2 = sp.flag_col + suffix, sp.flag_col + prior_suffix
        if v2 in df.columns and v3 not in df.columns:
            seed[v3] = df[v2]
        if f2 in df.columns and f3 not in df.columns:
            seed[f3] = df[f2]
    if seed:
        df = pd.concat([df, pd.DataFrame(seed, index=df.index)], axis=1)

    station_edits = edits[edits["station"] == station]
    for _, e in station_edits.iterrows():
        col = e["value_col"] + suffix
        if col not in df.columns:
            prior = e["value_col"] + prior_suffix
            df[col] = df[prior] if prior in df.columns else np.nan

        m = _window_mask(df, dt_col, e.get("start", ""), e.get("end", ""))
        action, param = e["action"], e.get("param", "")

        if action == "nan":
            df.loc[m, col] = np.nan
        elif action == "interpolate":
            filled = df[col].interpolate(method="linear", limit_area="inside")
            df.loc[m, col] = filled[m]
        elif action == "offset":
            df.loc[m, col] = df.loc[m, col] + float(param)
        elif action == "scale":
            df.loc[m, col] = df.loc[m, col] * float(param)
        elif action == "clamp":
            df.loc[m, col] = float(param)
        elif action == "rolling_median":
            w = int(float(param))
            med = df[col].rolling(w, center=True, min_periods=1).median()
            df.loc[m, col] = med[m]
        elif action == "set_flag":
            fcol = flag_of.get(e["value_col"], "") 
            if fcol:
                fcol = fcol + suffix
                if fcol not in df.columns:
                    df[fcol] = df[flag_of[e["value_col"]] + prior_suffix]
                df.loc[m, fcol] = param  # protected by sticky flags below
        else:
            raise ValueError(f"unknown manual-edit action: {action!r}")

    # Re-derive provenance against L2. set_flag values are 'S'/'E' and survive
    # because those flags are sticky by default.
    for sp in specs:
        v3, v2 = sp.value_col + suffix, sp.value_col + prior_suffix
        if v3 in df.columns and v2 in df.columns:
            derive_flags(df, v3, sp.flag_col + suffix, v2, sp.flag_col + prior_suffix)
    return df
