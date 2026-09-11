"""
Interactive L3 editor -- a desktop GUI (matplotlib) for reviewing a sensor_group
(or single ungrouped variable) at L2, dragging points to correct them, and
recording every correction as a manual_edits.csv row -- the same auditable,
replayable change log every other L3 edit already goes through, instead of a
one-off correction with no reasoning trail. Generalizes the point-dragging
snow-depth review tool to any variable, using the same visual language as
plots.py's grouped QC plots (same VARIABLE_COLORS palette, same level styling)
so what you see here matches what you saw in the review plots that sent you
here. An optional set of read-only overlay groups can be shown in synced
subplots below the editable one, for cross-checking against another sensor
(e.g. reviewing T while watching snow depth for context).

Interactions, once launched:
  left-click-drag a point       -> moves it (queues a `clamp` edit at that
                                    timestamp, value = where you released)
  right-click a point           -> removes it (queues a `nan` edit)
  left-click-drag empty space   -> selects a time window on the *active*
                                    variable (bold in the title; switch with
                                    number keys) and queues an `interpolate`
                                    edit across it on release
  '1'..'9'                      -> set which variable in the group is active
                                    for window-interpolate
  'z'                           -> undo the most recently queued edit
  's'                           -> save all queued edits to manual_edits.csv
                                    and clear the queue

Every queued edit is replayed live against the L2 data (the exact same
window-mask/action logic `apply_manual_edits` uses) so what you see while
editing is what L3 will actually produce -- there is no separate "preview"
approximation to fall out of sync with the real thing. Pending (unsaved)
edits are shown in gold: a point marker for `clamp`/`nan`, a shaded span for
`interpolate`.

Only the visual/positional actions (`nan`, `clamp`, `interpolate`) are
produced here. Everything else `manual_edits.csv` supports (`offset`, `scale`,
`rolling_median`, `set_flag`, ...) is still added by hand, same as always --
these three are the ones that map onto a drag/click gesture.
"""
from datetime import date

import numpy as np
import pandas as pd

PENDING_COLOR = "#FFD700"  # gold, for a queued-but-unsaved edit


def water_year(ts) -> int:
    """NevCAN water-year convention used throughout this project: Oct(N-1)
    through Sep(N) is water year N."""
    ts = pd.Timestamp(ts)
    return ts.year + 1 if ts.month >= 10 else ts.year


def _edit_rows(pending, station, applied_by, applied_on=None):
    """Turn a list of pending-edit dicts into manual_edits.csv row dicts."""
    applied_on = applied_on or date.today().isoformat()
    rows = []
    for e in pending:
        start = pd.Timestamp(e["start"])
        end = e.get("end")
        rows.append({
            "station": station,
            "value_col": e["value_col"],
            "start": start.isoformat(sep=" "),
            "end": pd.Timestamp(end).isoformat(sep=" ") if end else "",
            "action": e["action"],
            "param": e.get("param", ""),
            "note": e.get("note", ""),
            "water_year": water_year(start),
            "applied_by": applied_by,
            "applied_on": applied_on,
        })
    return rows


def append_edits(pending, edits_path, station, applied_by, applied_on=None):
    """Append pending edits to manual_edits.csv, preserving its existing
    column order. Returns how many rows were written (0 if pending is
    empty -- the file is left untouched)."""
    rows = _edit_rows(pending, station, applied_by, applied_on)
    if not rows:
        return 0
    existing = pd.read_csv(edits_path, dtype=str, keep_default_na=False, na_values=[])
    new = pd.DataFrame(rows, columns=existing.columns)
    combined = pd.concat([existing, new], ignore_index=True)
    combined.to_csv(edits_path, index=False)
    return len(rows)


def _window_mask(x, start, end):
    m = x >= pd.Timestamp(start)
    if end:
        m &= x < pd.Timestamp(end)
    return m


def _replay(base_y, x, pending, value_col):
    """Recompute one variable's working series from its original L2 values
    plus every pending edit targeting it, in queued order -- the same
    operation `apply_manual_edits` performs, so the live preview never drifts
    from what saving will actually produce."""
    s = pd.Series(base_y, index=x.index, dtype="float64").copy()
    for e in pending:
        if e["value_col"] != value_col:
            continue
        m = _window_mask(x, e["start"], e.get("end"))
        if e["action"] == "nan":
            s[m] = np.nan
        elif e["action"] == "clamp":
            s[m] = float(e["param"])
        elif e["action"] == "interpolate":
            filled = s.interpolate(method="linear", limit_area="inside")
            s[m] = filled[m]
    return s.to_numpy()


class DragEditor:
    """Interactive point/window editor for one sensor_group (or single
    variable) at L2, with optional read-only overlay groups for cross-
    checking. See module docstring for the interaction model. Construct via
    `edit_l3()` rather than directly."""

    PICK_RADIUS_PX = 12  # display-space pixels; independent of data units/scale

    def __init__(self, df, group_specs, station, edits_path,
                overlay_specs=(), dt_col="datetime_PST", applied_by=""):
        import matplotlib.pyplot as plt
        from .plots import LEVELS, VARIABLE_COLORS
        self._plt = plt
        self._LEVELS = LEVELS
        self._VARIABLE_COLORS = VARIABLE_COLORS

        self.df = df.reset_index(drop=True)
        self.group_specs = list(group_specs)
        self.overlay_specs = list(overlay_specs)
        self.station = station
        self.edits_path = edits_path
        self.applied_by = applied_by

        self.x = self.df[dt_col]
        self.cols = [sp.value_col + "_L2" for sp in self.group_specs]
        self._base_y = {c: self.df[c].to_numpy(dtype="float64", copy=True) for c in self.cols}
        self.y = {c: arr.copy() for c, arr in self._base_y.items()}
        self.active_idx = 0

        self.pending = []       # queued, unsaved edits
        self._drag = None       # in-progress point drag: (col, row_idx)
        self._select = None     # in-progress window select: mpl-date float
        self._select_patch = None
        self._pending_extra = []  # axvspan patches for pending interpolate windows

        n_rows = 1 + len(self.overlay_specs)
        self.fig, axes = plt.subplots(
            n_rows, 1, sharex=True,
            figsize=(13, 4 + 2 * len(self.overlay_specs)),
        )
        self.ax = axes if n_rows == 1 else axes[0]
        self.overlay_axes = [] if n_rows == 1 else list(np.atleast_1d(axes[1:]))

        self._draw_static()
        self._draw_points()
        self._draw_overlays()
        self._connect()
        self._update_title()

    # ---- drawing --------------------------------------------------------- #
    def _draw_static(self):
        multi = len(self.cols) > 1
        for i, sp in enumerate(self.group_specs):
            var_color = self._VARIABLE_COLORS[i % len(self._VARIABLE_COLORS)]
            for suf, label, lvl_color in self._LEVELS:
                col = sp.value_col + suf
                if col not in self.df.columns or self.df[col].isna().all():
                    continue
                ls = {"": ":", "_L1": "--", "_L2": "-", "_L3": "-."}[suf]
                color = var_color if multi else lvl_color
                lw = 1.4 if suf == "_L2" else 0.9
                alpha = 0.9 if suf == "_L2" else 0.5
                name = f"{sp.value_col} {label}" if multi else label
                self.ax.plot(self.x, self.df[col], ls, lw=lw, color=color,
                            alpha=alpha, label=name)
        self.ax.set_ylabel(" / ".join(sp.value_col for sp in self.group_specs))
        self.ax.legend(loc="upper right", fontsize=8, ncol=2)

    def _draw_points(self):
        self.point_artists = {}
        for i, col in enumerate(self.cols):
            color = (self._VARIABLE_COLORS[i % len(self._VARIABLE_COLORS)]
                    if len(self.cols) > 1 else self._LEVELS[2][2])
            artist, = self.ax.plot(self.x, self.y[col], "o", ms=4, color=color, zorder=5)
            self.point_artists[col] = artist
        self.pending_artist, = self.ax.plot([], [], "o", ms=8, mfc=PENDING_COLOR,
                                            mec="black", zorder=6)

    def _draw_overlays(self):
        for ax, sp in zip(self.overlay_axes, self.overlay_specs):
            col = sp.value_col + "_L2"
            if col in self.df.columns:
                ax.plot(self.x, self.df[col], "-", lw=1, color="#555555")
            ax.set_ylabel(sp.value_col)

    def _update_title(self):
        active = self.group_specs[self.active_idx].value_col
        self.fig.suptitle(
            f"{self.station} — {', '.join(sp.value_col for sp in self.group_specs)}  "
            f"| active for window-interpolate: {active}  |  pending: {len(self.pending)}\n"
            f"drag point=clamp   right-click point=nan   drag empty=interpolate window   "
            f"1-9=pick active   s=save   z=undo",
            fontsize=9,
        )
        self.fig.canvas.draw_idle()

    # ---- live preview ------------------------------------------------ #
    def _recompute(self):
        for col, sp in zip(self.cols, self.group_specs):
            self.y[col] = _replay(self._base_y[col], self.x, self.pending, sp.value_col)
        for col, artist in self.point_artists.items():
            artist.set_ydata(self.y[col])
        self._redraw_pending()
        self._update_title()

    def _redraw_pending(self):
        for artist in self._pending_extra:
            artist.remove()
        self._pending_extra = []
        px, py = [], []
        for e in self.pending:
            col = e["value_col"] + "_L2"
            if col not in self.y:
                continue
            if e["action"] == "interpolate":
                span = self.ax.axvspan(pd.Timestamp(e["start"]), pd.Timestamp(e["end"]),
                                       color=PENDING_COLOR, alpha=0.2)
                self._pending_extra.append(span)
            else:
                m = _window_mask(self.x, e["start"], e.get("end")).to_numpy()
                idx = np.flatnonzero(m)
                px.extend(self.x.iloc[idx])
                py.extend(self.y[col][idx])
        self.pending_artist.set_data(px, py)

    # ---- queueing ------------------------------------------------------- #
    def _queue(self, value_col, start, end, action, param, note=""):
        self.pending.append({
            "value_col": value_col, "start": start, "end": end,
            "action": action, "param": param, "note": note,
        })
        self._recompute()

    def undo(self):
        if self.pending:
            self.pending.pop()
            self._recompute()

    def save(self):
        n = append_edits(self.pending, self.edits_path, self.station, self.applied_by)
        print(f"  saved {n} edit(s) to {self.edits_path}")
        # Fold the now-saved edits into the baseline before clearing the queue:
        # _recompute() always rebuilds from _base_y + pending, so without this
        # the view would snap back to the pre-edit values the instant pending
        # goes empty -- even though those edits are now permanently logged.
        for col in self.cols:
            self._base_y[col] = self.y[col].copy()
        self.pending = []
        self._recompute()
        return n

    # ---- hit-testing ------------------------------------------------- #
    def _nearest_point(self, event):
        """Return (col, row_idx) for the closest point across every variable
        in the group, in *display* (pixel) space so the pick radius doesn't
        depend on the data's own scale -- or None if nothing is within
        PICK_RADIUS_PX."""
        if event.x is None or event.y is None:
            return None
        click = np.array([event.x, event.y])
        best, best_dist = None, self.PICK_RADIUS_PX
        x_num = self._x_as_num()
        for col in self.cols:
            ys = self.y[col]
            valid = ~np.isnan(ys)
            if not valid.any():
                continue
            pts_data = np.column_stack([x_num[valid], ys[valid]])
            pts_disp = self.ax.transData.transform(pts_data)
            d = np.hypot(pts_disp[:, 0] - click[0], pts_disp[:, 1] - click[1])
            j = int(np.argmin(d))
            if d[j] < best_dist:
                best_dist = d[j]
                best = (col, int(np.flatnonzero(valid)[j]))
        return best

    def _x_as_num(self):
        import matplotlib.dates as mdates
        return mdates.date2num(self.x.dt.to_pydatetime())

    def _next_timestamp(self, idx):
        return (self.x.iloc[idx + 1] if idx + 1 < len(self.x)
               else self.x.iloc[idx] + (self.x.iloc[idx] - self.x.iloc[idx - 1]))

    # ---- event handlers ------------------------------------------------- #
    def _connect(self):
        self.fig.canvas.mpl_connect("button_press_event", self._on_press)
        self.fig.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.fig.canvas.mpl_connect("button_release_event", self._on_release)
        self.fig.canvas.mpl_connect("key_press_event", self._on_key)

    def _on_press(self, event):
        if event.inaxes is not self.ax:
            return
        hit = self._nearest_point(event)
        if event.button == 3 and hit:                  # right-click a point -> nan
            col, idx = hit
            value_col = col[:-3]
            self._queue(value_col, self.x.iloc[idx], self._next_timestamp(idx), "nan", "")
            return
        if event.button == 1 and hit:                   # left-drag a point -> clamp
            self._drag = hit
            return
        if event.button == 1 and event.xdata is not None:   # empty space -> window select
            self._select = event.xdata

    def _on_motion(self, event):
        if self._drag is not None and event.ydata is not None:
            col, idx = self._drag
            self.y[col][idx] = event.ydata
            self.point_artists[col].set_ydata(self.y[col])
            self.fig.canvas.draw_idle()
        elif self._select is not None and event.xdata is not None:
            if self._select_patch is not None:
                self._select_patch.remove()
            lo, hi = sorted([self._select, event.xdata])
            self._select_patch = self.ax.axvspan(lo, hi, color="#999999", alpha=0.25)
            self.fig.canvas.draw_idle()

    def _on_release(self, event):
        if self._drag is not None:
            col, idx = self._drag
            self._drag = None
            value_col = col[:-3]
            self._queue(value_col, self.x.iloc[idx], self._next_timestamp(idx),
                       "clamp", round(float(self.y[col][idx]), 4))
            return
        if self._select is not None:
            x0 = self._select
            self._select = None
            if self._select_patch is not None:
                self._select_patch.remove()
                self._select_patch = None
            if event.xdata is None:
                self.fig.canvas.draw_idle()
                return
            self._queue_window(x0, event.xdata)

    def _queue_window(self, x0_num, x1_num):
        import matplotlib.dates as mdates
        lo_num, hi_num = sorted([x0_num, x1_num])
        x_num = self._x_as_num()
        i0 = int(np.searchsorted(x_num, lo_num, side="left"))
        i1 = int(np.searchsorted(x_num, hi_num, side="right")) - 1
        i0, i1 = max(0, i0), min(len(self.x) - 1, i1)
        if i1 <= i0:
            self.fig.canvas.draw_idle()
            return
        sp = self.group_specs[self.active_idx]
        start = self.x.iloc[i0]
        end = self._next_timestamp(i1)
        self._queue(sp.value_col, start, end, "interpolate", "", note="drag-selected window")

    def _on_key(self, event):
        if event.key == "z":
            self.undo()
        elif event.key == "s":
            self.save()
        elif event.key and event.key.isdigit():
            i = int(event.key) - 1
            if 0 <= i < len(self.group_specs):
                self.active_idx = i
                self._update_title()


def edit_l3(df, specs, station, group, edits_path, overlay_groups=(),
           dt_col="datetime_PST", applied_by="", water_year_range=None):
    """Launch the interactive editor for one sensor_group (or a single
    ungrouped value_col) at a station.

    `group` / entries in `overlay_groups` may each be a sensor_group name
    (e.g. "T") or a bare value_col for an ungrouped variable (e.g.
    "snowdepth_avg_mm"). `water_year_range`, if given, is
    `(start_water_year, end_water_year)` (inclusive) to trim the view to
    before launching -- editing a whole multi-year record at once in one
    matplotlib window is unusable. Returns the DragEditor instance.
    """
    import matplotlib.pyplot as plt

    def _resolve(name):
        members = [sp for sp in specs if sp.sensor_group == name]
        if members:
            return members
        match = [sp for sp in specs if sp.value_col == name]
        if not match:
            raise ValueError(f"no sensor_group or variable named {name!r}")
        return match

    group_specs = _resolve(group)
    overlay_specs = [sp for name in overlay_groups for sp in _resolve(name)]

    df = df.sort_values(dt_col).reset_index(drop=True)
    if water_year_range:
        wy0, wy1 = water_year_range
        wy = df[dt_col].apply(water_year)
        df = df[(wy >= wy0) & (wy <= wy1)].reset_index(drop=True)

    editor = DragEditor(df, group_specs, station, edits_path,
                        overlay_specs=overlay_specs, dt_col=dt_col,
                        applied_by=applied_by)
    plt.show()
    return editor
