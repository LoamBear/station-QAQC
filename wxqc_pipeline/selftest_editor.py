"""Non-interactive check of wxqc/editor.py's logic: drives DragEditor through
simulated mouse/keyboard events (bypassing the real GUI event loop, which
needs a display) to exercise the exact same handler code a live drag/click
session would run. Uses the Agg backend, so this runs headless in CI.

Ends with a round-trip check: save the queued edits to a temp manual_edits.csv,
reload them with wxqc.load_manual_edits, replay them with apply_manual_edits
against a fresh copy of the L2 data, and confirm the result matches what the
editor's live preview showed *before* saving -- the tool's core promise is
that what you see while editing is what L3 will actually produce.
"""
import shutil
import tempfile
import types
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")

import pandas as pd
import wxqc
from wxqc.editor import DragEditor

SCRATCH = Path(tempfile.mkdtemp(prefix="wxqc_editor_selftest_"))
EDITS_PATH = SCRATCH / "editor_test_manual_edits.csv"

# a tiny, isolated manual_edits.csv to append to -- never touches the real one
pd.DataFrame(columns=[
    "station", "value_col", "start", "end", "action", "param",
    "note", "water_year", "applied_by", "applied_on",
]).to_csv(EDITS_PATH, index=False)

n = 50
idx = pd.date_range("2023-01-01", periods=n, freq="10min")
df = pd.DataFrame({"datetime_PST": idx, "stationid": "nep2"})
df["T_max_C_L2"] = 13.0
df["T_min_C_L2"] = 7.0
df["T_avg_C_L2"] = 10.0
# a real gap for the window-interpolate test to bridge (rows 31-32), with a
# known linear ramp either side so the interpolated result is predictable
df.loc[30, "T_max_C_L2"] = 10.0
df.loc[31:32, "T_max_C_L2"] = np.nan
df.loc[33, "T_max_C_L2"] = 22.0
# a couple of L1/raw columns too, so the static context lines have something
for suf in ("", "_L1"):
    df[f"T_max_C{suf}"] = df["T_max_C_L2"]
    df[f"T_min_C{suf}"] = df["T_min_C_L2"]
    df[f"T_avg_C{suf}"] = df["T_avg_C_L2"]

specs = [s for s in wxqc.load_variables("config/nevcan/variables.csv") if s.sensor_group == "T"]
assert len(specs) == 3, f"expected T_max_C/T_min_C/T_avg_C specs, got {[s.value_col for s in specs]}"

editor = DragEditor(df, specs, "nep2", EDITS_PATH, applied_by="test")


def click_at(row_idx, col, y_value=None):
    """Build a mock event that lands exactly on (or near) a given row's point
    for `col`, in both data space and the display (pixel) space _nearest_point
    actually picks in -- so hit-testing behaves exactly as it would for a real
    click there."""
    x_num = editor._x_as_num()[row_idx]
    y = editor.y[col][row_idx] if y_value is None else y_value
    px, py = editor.ax.transData.transform((x_num, y))
    return types.SimpleNamespace(inaxes=editor.ax, xdata=x_num, ydata=y, x=px, y=py, button=1, key=None)


def empty_click_at(row_idx):
    x_num = editor._x_as_num()[row_idx]
    y = 1000.0  # far from every series -> guaranteed no point hit
    px, py = editor.ax.transData.transform((x_num, y))
    return types.SimpleNamespace(inaxes=editor.ax, xdata=x_num, ydata=y, x=px, y=py, button=1, key=None)


# ---- 1) left-drag a point on T_avg_C -> clamp ---------------------------- #
press = click_at(10, "T_avg_C_L2")
editor._on_press(press)
assert editor._drag == ("T_avg_C_L2", 10), f"expected a drag to start on T_avg_C_L2[10], got {editor._drag}"
motion = click_at(10, "T_avg_C_L2", y_value=25.0)
editor._on_motion(motion)
assert editor.y["T_avg_C_L2"][10] == 25.0, "live y should track the drag before release"
release = click_at(10, "T_avg_C_L2", y_value=25.0)
editor._on_release(release)
assert editor._drag is None
assert len(editor.pending) == 1
e = editor.pending[0]
assert e["value_col"] == "T_avg_C" and e["action"] == "clamp" and float(e["param"]) == 25.0, e
assert editor.y["T_avg_C_L2"][10] == 25.0, "recompute after release should preserve the clamped value"
print("OK: point-drag -> clamp")

# ---- 2) right-click a point on T_max_C -> nan ----------------------------- #
rc = click_at(20, "T_max_C_L2")
rc.button = 3
editor._on_press(rc)
assert len(editor.pending) == 2
e = editor.pending[1]
assert e["value_col"] == "T_max_C" and e["action"] == "nan", e
assert np.isnan(editor.y["T_max_C_L2"][20]), "nan'd point should show as missing in the live preview"
print("OK: right-click -> nan")

# ---- 3) drag across empty space -> interpolate window on the active var -- #
assert editor.active_idx == 0  # T_max_C is group_specs[0]
p0 = empty_click_at(30)
editor._on_press(p0)
assert editor._select is not None and editor._drag is None
m1 = empty_click_at(33)
editor._on_motion(m1)
r1 = empty_click_at(33)
editor._on_release(r1)
assert editor._select is None
assert len(editor.pending) == 3
e = editor.pending[2]
assert e["value_col"] == "T_max_C" and e["action"] == "interpolate", e
# rows 31/32 were a real NaN gap ramping 10.0 (row30) -> 22.0 (row33); linear
# interpolation across 3 steps should land at 14.0 and 18.0
assert np.allclose(editor.y["T_max_C_L2"][31:33], [14.0, 18.0]), \
    f"expected the gap bridged to [14.0, 18.0], got {editor.y['T_max_C_L2'][31:33]}"
print("OK: drag-empty -> interpolate window bridges a real gap correctly")

# ---- 4) switch active variable with a number key, then window-select again #
key2 = types.SimpleNamespace(inaxes=editor.ax, xdata=None, ydata=None, x=None, y=None, button=None, key="2")
editor._on_key(key2)
assert editor.active_idx == 1  # T_min_C is group_specs[1]
p0 = empty_click_at(40)
editor._on_press(p0)
r1 = empty_click_at(43)
editor._on_release(r1)
assert len(editor.pending) == 4
e = editor.pending[3]
assert e["value_col"] == "T_min_C", "window-interpolate should target the newly active variable"
print("OK: number key switches the active variable for window-interpolate")

# ---- 5) undo removes the most recent pending edit and replays the rest --- #
before = len(editor.pending)
undone = editor.pending[-1]
editor._on_key(types.SimpleNamespace(inaxes=editor.ax, xdata=None, ydata=None, x=None, y=None, button=None, key="z"))
assert len(editor.pending) == before - 1
assert undone not in editor.pending
print("OK: undo removes the last queued edit")

# ---- 6) save -> appended to manual_edits.csv, pending cleared ------------ #
pending_before_save = [dict(p) for p in editor.pending]
n_saved = editor.save()
assert n_saved == len(pending_before_save)
assert editor.pending == []
saved = pd.read_csv(EDITS_PATH, dtype=str, keep_default_na=False, na_values=[])
assert len(saved) == n_saved, "all queued edits should be appended (file started empty)"
assert (saved["station"] == "nep2").all()
assert (saved["applied_by"] == "test").all()
print(f"OK: save appended {n_saved} row(s) to manual_edits.csv")
print(saved[["value_col", "start", "end", "action", "param", "water_year"]].to_string(index=False))

# ---- 7) round trip: replayed edits == what the live preview showed ------- #
fresh = df.copy()
edits = wxqc.load_manual_edits(EDITS_PATH)
out = wxqc.apply_manual_edits(fresh, edits, specs, "nep2")
for sp in specs:
    l2, l3 = sp.value_col + "_L2", sp.value_col + "_L3"
    expected = editor.y[l2]  # the editor's own live-preview array at save time
    actual = out[l3].to_numpy()
    both_nan = np.isnan(expected) & np.isnan(actual)
    assert np.allclose(expected[~both_nan], actual[~both_nan], equal_nan=False) and \
        (np.isnan(expected) == np.isnan(actual)).all(), \
        f"{sp.value_col}: apply_manual_edits result doesn't match the editor's live preview"
print("OK: apply_manual_edits reproduces exactly what the editor previewed live")

print("ALL EDITOR SELFTESTS PASSED")
