"""
Validation helper: compare a newly generated level file against a legacy one and
report which columns/rows differ. Use this to confirm the refactor reproduces
(or intentionally changes) the old outputs before trusting it.
"""
import numpy as np
import pandas as pd


def compare_csv(new_path, old_path, key="datetime_PST", atol=1e-6):
    a = pd.read_csv(new_path, parse_dates=[key]).set_index(key)
    b = pd.read_csv(old_path, parse_dates=[key]).set_index(key)
    common = sorted(set(a.columns) & set(b.columns))
    idx = a.index.intersection(b.index)
    a, b = a.loc[idx, common], b.loc[idx, common]

    report = []
    for c in common:
        if pd.api.types.is_numeric_dtype(a[c]) and pd.api.types.is_numeric_dtype(b[c]):
            both_nan = a[c].isna() & b[c].isna()
            diff = (~np.isclose(a[c], b[c], atol=atol, equal_nan=True)) & ~both_nan
        else:
            diff = (a[c].astype(str) != b[c].astype(str)) & ~(a[c].isna() & b[c].isna())
        n = int(diff.sum())
        if n:
            report.append((c, n))

    only_new = sorted(set(a.columns) - set(b.columns))
    only_old = sorted(set(b.columns) - set(a.columns))
    return {
        "rows_compared": len(idx),
        "columns_with_diffs": report,        # [(column, n_cells_differing), ...]
        "columns_only_in_new": only_new,
        "columns_only_in_old": only_old,
    }
