"""
edit_l3.py -- interactive point/window editor for one sensor_group (or single
variable) at one station, recording every correction as a manual_edits.csv row.

Opens a matplotlib window: drag a point to move it (clamp), right-click a
point to remove it (nan), drag across empty space to select a window and
interpolate across it, number keys pick which variable is "active" for
window-interpolate when the group has more than one, 's' saves everything
queued to manual_edits.csv, 'z' undoes the last queued edit. See
wxqc/editor.py for the full interaction model -- every queued edit is
replayed live with the same logic apply_manual_edits uses, so what you see is
what saving will actually produce.

Reads real L2QC data from your data root but reads/writes
config/<network>/{variables,manual_edits}.csv from *this repo* -- the repo is
the source of truth for config; sync manual_edits.csv to your live config
yourself once you're happy with a batch of edits, same as every other config
change.

Edit the CONFIG block, then:  python edit_l3.py
"""
import glob
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
REPO_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = REPO_ROOT / "config" / NETWORK        # source of truth: this repo
DATA_ROOT = Path(local_settings.DATA_ROOT)
L2_QC_DIR = DATA_ROOT / "Level_2QC"                # real data: read-only

STATION = "nep1"
GROUP = "T"                              # a sensor_group name, or a bare value_col
OVERLAY_GROUPS = ["snowdepth_avg_mm"]    # read-only cross-check context; [] for none
WATER_YEAR_RANGE = (2023, 2023)          # (start, end) inclusive water years, or None for the whole record
APPLIED_BY = "bb"
DT_COL = "datetime_PST"
# -------------------------------------------------------------------------- #


def main():
    specs = wxqc.load_variables(CONFIG_DIR / "variables.csv")
    edits_path = CONFIG_DIR / "manual_edits.csv"

    files = sorted(glob.glob(str(L2_QC_DIR / f"{STATION}_*_L2QC.csv")))
    if not files:
        raise SystemExit(f"no Level_2QC files found for {STATION} in {L2_QC_DIR}")
    df = pd.concat(
        [pd.read_csv(f, parse_dates=[DT_COL], low_memory=False) for f in files],
        ignore_index=True,
    )

    wxqc.edit_l3(df, specs, STATION, GROUP, edits_path,
                overlay_groups=OVERLAY_GROUPS, dt_col=DT_COL,
                applied_by=APPLIED_BY, water_year_range=WATER_YEAR_RANGE)


if __name__ == "__main__":
    main()
