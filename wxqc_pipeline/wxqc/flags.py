"""
Provenance flags.

  O = Observed  : value unchanged from the raw measurement
  E = Edited    : value was modified by QC at this level (or an earlier one)
  S = Suspect   : value is missing, or a human flagged it suspect

This is the single correct implementation used by every level. The legacy
scripts compared values with `== np.nan` (always False), so the null/"S" branch
never fired and unchanged-but-missing samples were mislabeled "E". Here we use
.isna() and treat NaN==NaN as unchanged.

By default E and S are *sticky*: once a sample has been edited or marked suspect
at any level, later levels keep that provenance even if they leave the value
alone. Pass sticky=("S",) to recover the legacy per-level behavior.
"""
import numpy as np
import pandas as pd

OBSERVED, EDITED, SUSPECT = "O", "E", "S"


def derive_flags(df, value_col, flag_col, prior_value_col,
                 prior_flag_col=None, sticky=(EDITED, SUSPECT)):
    if flag_col not in df.columns:
        if prior_flag_col and prior_flag_col in df.columns:
            df[flag_col] = df[prior_flag_col]
        else:
            df[flag_col] = OBSERVED

    val = df[value_col]
    prior = df[prior_value_col]

    null = val.isna()
    unchanged = (val == prior) | (val.isna() & prior.isna())

    this_level = np.where(unchanged, OBSERVED, EDITED)
    this_level = np.where(null, SUSPECT, this_level)

    sticky_mask = df[flag_col].isin(list(sticky))
    df[flag_col] = np.where(sticky_mask, df[flag_col], this_level)
    return df
