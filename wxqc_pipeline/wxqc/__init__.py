"""
wxqc -- a config-driven, naming-agnostic QC engine for weather-station networks.

The engine knows no variable names. Each network supplies a config folder
(stations.csv, variables.csv, thresholds.csv, manual_edits.csv); the same engine
code runs L1.5 and L2 automatically and applies the L3 manual change log.
"""
from .config import (
    VarSpec, Thresholds,
    load_thresholds, load_variables, load_stations, load_manual_edits,
)
from .pipeline import run_level15, run_level2, export_columns, regrid_timestamps
from .edits import apply_manual_edits
from .flags import derive_flags, OBSERVED, EDITED, SUSPECT
from . import checks, sensors, diff
from .plots import plot_station, plot_variable, plot_variables
from .editor import edit_l3, DragEditor, append_edits, water_year
from .explain import explain_flag

__all__ = [
    "VarSpec", "Thresholds",
    "load_thresholds", "load_variables", "load_stations", "load_manual_edits",
    "run_level15", "run_level2", "export_columns", "regrid_timestamps",
    "apply_manual_edits", "derive_flags",
    "OBSERVED", "EDITED", "SUSPECT",
    "checks", "sensors", "diff",
    "plot_station", "plot_variable", "plot_variables",
    "edit_l3", "DragEditor", "append_edits", "water_year",
    "explain_flag",
]
