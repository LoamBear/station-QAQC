"""
run_pipeline.py -- run the automated levels (L1.5 and L2) for a network.

Reads the pre-QC "Level 1" files (one per station / water year), produces the
L1.5 and L2 files plus their QC companions. L3 is a separate, manual step
(see run_l3.py).

Edit the CONFIG block for your environment, then:  python run_pipeline.py
"""
import os
from pathlib import Path

import pandas as pd

import wxqc

try:
    import local_settings
except ModuleNotFoundError:
    raise SystemExit(
        "Missing local_settings.py -- copy local_settings.example.py to "
        "local_settings.py (same folder) and fill in your own DATA_ROOT. "
        "It's gitignored, so your real data path never ends up in shared source."
    )


# ----------------------------- CONFIG ------------------------------------- #
NETWORK = "nevcan"
CONFIG_DIR = Path("config") / NETWORK

DATA_ROOT = Path(".")                      # base of your data tree
L1_IN_DIR = DATA_ROOT / "Level_1"          # pre-QC inputs:  {station}_WY{year}_L1.csv
L15_DIR = DATA_ROOT / "Level_1.5"          # outputs
L15_QC_DIR = DATA_ROOT / "Level_1.5QC"     # full working frame, feeds L2 + L3
L2_DIR = DATA_ROOT / "Level_2"
L2_QC_DIR = DATA_ROOT / "Level_2QC"        # full working frame, feeds L3

STATIONS = None                            # None = all in stations.csv, or e.g. ["nep4"]
YEARS = range(2012, 2026)                  # water years, end exclusive
DT_COL = "datetime_PST"
PLOTS = True                               # write QC review plots (raw/L1.5/L2) per station-year
PLOTS_DIR = DATA_ROOT / "plots"
# -------------------------------------------------------------------------- #
os.chdir(local_settings.DATA_ROOT)

def main():
    specs = wxqc.load_variables(CONFIG_DIR / "variables.csv")
    thr = wxqc.load_thresholds(CONFIG_DIR / "thresholds.csv")
    stations_df = wxqc.load_stations(CONFIG_DIR / "stations.csv")
    stations = STATIONS or list(stations_df["station_id"])
    stations_meta = stations_df.set_index("station_id")

    # sample_freq (a stations.csv column, e.g. "10min", "1H", "15min") is the
    # station's native logging interval, used to regrid L2 onto a continuous
    # timeline -- see regrid_timestamps. Falls back to "10min" (NevCAN's own
    # rate) so an older stations.csv without the column still runs.
    def sample_freq_for(station):
        if "sample_freq" in stations_meta.columns:
            return stations_meta.loc[station, "sample_freq"]
        return "10min"

    for d in (L15_DIR, L15_QC_DIR, L2_DIR, L2_QC_DIR):
        d.mkdir(parents=True, exist_ok=True)

    for station in stations:
        for year in YEARS:
            in_path = L1_IN_DIR / f"{station}_WY{year}_L1.csv"
            if not in_path.exists():
                continue
            df = pd.read_csv(in_path, parse_dates=[DT_COL])
            df["stationid"] = station

            # ---- L1.5 ----
            df = wxqc.run_level15(df, specs, thr, station)
            wxqc.export_columns(df, specs, "_L1").to_csv(
                L15_DIR / f"{station}_WY{year}_L1.5.csv", index=False)
            df.to_csv(L15_QC_DIR / f"{station}_WY{year}_L1.5QC.csv", index=False)

            # ---- L2 ----
            df = wxqc.run_level2(df, specs, thr, station, freq=sample_freq_for(station))
            wxqc.export_columns(df, specs, "_L2").to_csv(
                L2_DIR / f"{station}_WY{year}_L2.csv", index=False)
            df.to_csv(L2_QC_DIR / f"{station}_WY{year}_L2QC.csv", index=False)

            if PLOTS:
                wxqc.plot_station(df, specs, station, PLOTS_DIR / f"{station}_WY{year}", thr=thr)

            print(f"  {station} WY{year}: L1.5 + L2 written")


if __name__ == "__main__":
    main()
