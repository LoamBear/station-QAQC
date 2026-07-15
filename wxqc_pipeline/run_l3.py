"""
run_l3.py -- apply the manual change log to produce Level 3, per station.

This is the human-in-the-loop annual step. It concatenates a station's L2QC
files, trims to the period of record, applies every manual_edits.csv row for
that station (in file order), re-derives flags, and writes Level 3.

Annual workflow:
  1. Run run_pipeline.py so L2QC is current.
  2. Review plots, decide on edits for the new water year.
  3. Append rows to config/<network>/manual_edits.csv (one row per edit).
  4. Run this script; L3 regenerates deterministically from L2 + the log.
"""
import glob
from pathlib import Path

import pandas as pd

import wxqc

# ----------------------------- CONFIG ------------------------------------- #
NETWORK = "nevcan"
CONFIG_DIR = Path("config") / NETWORK
DATA_ROOT = Path(".")
L2_QC_DIR = DATA_ROOT / "Level_2QC"
L3_DIR = DATA_ROOT / "Level_3"
STATIONS = None                            # None = all, or e.g. ["nep1"]
DT_COL = "datetime_PST"
PLOTS = True                               # write QC review plots (raw/L1.5/L2/L3) per station
# -------------------------------------------------------------------------- #


def main():
    specs = wxqc.load_variables(CONFIG_DIR / "variables.csv")
    edits = wxqc.load_manual_edits(CONFIG_DIR / "manual_edits.csv")
    stations_df = wxqc.load_stations(CONFIG_DIR / "stations.csv")
    stations = STATIONS or list(stations_df["station_id"])
    por = stations_df.set_index("station_id")

    L3_DIR.mkdir(parents=True, exist_ok=True)

    for station in stations:
        files = sorted(glob.glob(str(L2_QC_DIR / f"{station}_*_L2QC.csv")))
        if not files:
            continue
        df = pd.concat(
            [pd.read_csv(f, parse_dates=[DT_COL]) for f in files],
            ignore_index=True,
        ).sort_values(DT_COL)

        start, end = por.loc[station, "por_start"], por.loc[station, "por_end"]
        df = df[(df[DT_COL] >= start) & (df[DT_COL] <= end)].reset_index(drop=True)

        df = wxqc.apply_manual_edits(df, edits, specs, station, dt_col=DT_COL)

        wxqc.export_columns(df, specs, "_L3").to_csv(
            L3_DIR / f"{station}_L3.csv", index=False)

        if PLOTS:
            wxqc.plot_station(df, specs, station, L3_DIR / "plots" / station)

        print(f"  {station}: L3 written ({len(df)} rows)")


if __name__ == "__main__":
    main()
