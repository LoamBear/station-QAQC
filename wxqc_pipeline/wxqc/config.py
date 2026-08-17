"""
Config loading. All network-specific knowledge lives in CSVs; this module turns
them into objects the engine consumes. Nothing here is NevCAN-specific.
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class VarSpec:
    value_col: str                 # raw column name in this network's data
    flag_col: str                  # companion flag column name
    range_key: str = ""            # key into thresholds.csv (-> key_min / key_max); "" = no range check
    range_action: str = "nan"      # nan | clamp_high_100 | nan_or_zero | clamp | keep
    roc_key: str = ""              # key into thresholds.csv (single value); "" = no ROC check
    interp_limit: Optional[int] = None   # max gap (samples) to fill at L2; None = no fill
    discontinuity_limit: Optional[int] = None  # max gap (samples) eligible for the ROC-scaled
                                    # discontinuity check (needs roc_key); None = feature off,
                                    # falls back to interp_limit's plain length-based fill
    handler: str = ""              # name in sensors.HANDLERS; "" = none


class Thresholds:
    """Wraps the station-columned threshold table (your QC_L1rangecheck.csv):
    rows are parameter keys (Tavg_max, Tavg_min, T_roc, ...), columns are stations.
    """
    def __init__(self, df: pd.DataFrame):
        self.df = df

    def value(self, station: str, key: str) -> float:
        return float(self.df.loc[key, station])

    def range(self, station: str, key: str):
        return (float(self.df.loc[f"{key}_min", station]),
                float(self.df.loc[f"{key}_max", station]))


def load_thresholds(path) -> Thresholds:
    return Thresholds(pd.read_csv(path, index_col="parameter"))


def load_variables(path):
    raw = pd.read_csv(path).fillna("")
    specs = []
    for _, r in raw.iterrows():
        il = str(r["interp_limit"]).strip()
        dl = str(r.get("discontinuity_limit", "")).strip()
        specs.append(VarSpec(
            value_col=r["value_col"],
            flag_col=r["flag_col"],
            range_key=str(r["range_key"]).strip(),
            range_action=(str(r["range_action"]).strip() or "nan"),
            roc_key=str(r["roc_key"]).strip(),
            interp_limit=(int(float(il)) if il not in ("", "nan") else None),
            discontinuity_limit=(int(float(dl)) if dl not in ("", "nan") else None),
            handler=str(r["handler"]).strip(),
        ))
    return specs


def load_stations(path) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["por_start", "por_end"])


def load_manual_edits(path) -> pd.DataFrame:
    # keep_default_na=False so the literal action string "nan" is not turned into
    # a missing value by the CSV parser.
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    return df
