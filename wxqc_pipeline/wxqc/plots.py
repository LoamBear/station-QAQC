"""
QC review plots. For each variable (or group of variables from the same
physical sensor, e.g. max/min/avg -- see sensor_group in variables.csv), build
a small interactive HTML overlaying its trace at every level that exists (raw,
L1.5, L2, L3) so the operator can see exactly what each QC step changed.
Single-variable plots use color for the level (raw/L1.5/L2/L3); grouped plots
use color for the variable instead (a high-contrast color per variable reads
far better than dash style alone) and dash style for the level. Markers
indicate data *quality*, not removal -- an orange square where a value was
newly Edited (modified but kept), a pink diamond where a value was newly
flagged Suspect but kept (e.g. a manual set_flag edit, or a gap-fill that
reused a still-suspect sample). Missing (removed) points get no marker at all
-- the gap in the line already shows that. An opaque gray band marks
timestamp ranges regrid_timestamps inserted (see pipeline.py) -- a structural
gap where no data collection happened at all, as opposed to an ordinary
missing/removed value at a real timestamp. A per-station index.html links
them all.

One file per variable/group (not one giant file per station): a combined file
embeds every variable's full 10-minute series and balloons to >100 MB, which
chokes the browser. Per-variable files stay a few MB and open instantly, and
you open the one you want to review. Interactive (Plotly + WebGL) because a QC
review needs zoom; plotly.js loads from a CDN so files stay small.
"""
import os
import plotly.graph_objects as go

from .explain import explain_flag

EDITED_COLOR = "#ff7f0e"     # orange, for values modified but kept
SUSPECT_COLOR = "#e377c2"    # pink, for values kept but flagged Suspect
GENERATED_COLOR = "#888888"  # opaque gray band, for timestamps regrid_timestamps
                             # inserted -- no data collection happened at all

# level suffix -> (legend label, color). Color carries the level on a
# single-variable plot; on a grouped (multi-variable) plot, color is
# repurposed below to carry the variable instead -- dash carries level there.
LEVELS = [
    ("",    "raw",  "#b0b0b0"),
    ("_L1", "L1.5", "#1f77b4"),
    ("_L2", "L2",   "#2ca02c"),
    ("_L3", "L3",   "#9467bd"),
]

# level suffix -> dash style, used only on grouped plots (color is busy
# distinguishing the variable there instead).
LEVEL_DASH = {"": "dot", "_L1": "dash", "_L2": "solid", "_L3": "dashdot"}

# level suffix -> the suffix that fed it, for explain_flag (raw has no prior).
_PRIOR_SUFFIX = {LEVELS[i][0]: LEVELS[i - 1][0] for i in range(1, len(LEVELS))}

# Colorblind-safe palette (IBM Design Library set, verified for deuteranopia/
# protanopia/tritanopia at davidmathlogic.com/colorblind) for distinguishing
# variables within a grouped plot (e.g. max/min/avg from one sensor); cycles
# if a group has more members than this.
VARIABLE_COLORS = ["#D81B60", "#1E88E5", "#FFC107", "#004D40"]


def _present_levels(df, sp):
    out = []
    for suf, label, color in LEVELS:
        col = sp.value_col + suf
        if col in df.columns and df[col].notna().any():
            out.append((suf, label, color, col))
    return out


def _add_quality_markers(fig, df, sp, levels, x, thr=None, all_specs=None,
                         station=None, edits=None, name_prefix=""):
    """Add the Edited/Suspect marker traces for one variable's levels onto an
    existing figure. See module docstring for what each marker means.

    Tracking is on the marked state (flag + has-a-value), not the flag alone:
    a point that was removed at L1.5 (flag 'S', no value) and then gap-filled
    back in at L2 (still flag 'S', now has a value) counts as newly marked at
    L2, since that's the first level it had both. Removed (flag 'S', value
    NaN) points never get a marker: the gap in the line already says
    "missing".

    When thr/all_specs/station are given, each marker's hover text explains
    which check caused it (see explain.explain_flag) -- best-effort, so a
    reason of "" just means no hover text for that point rather than an error.
    """
    already_marked = None
    for suf, label, color, col in levels:
        if suf == "":
            continue
        fcol = sp.flag_col + suf
        if fcol not in df.columns:
            continue
        flag = df[fcol]
        is_marked = flag.isin(["E", "S"]) & df[col].notna()
        newly = is_marked if already_marked is None else (is_marked & ~already_marked)
        already_marked = is_marked if already_marked is None else (already_marked | is_marked)

        reason = None
        if thr is not None and all_specs is not None and station is not None:
            reason = explain_flag(df, sp, all_specs, thr, station, suf,
                                  _PRIOR_SUFFIX[suf], edits=edits)

        edited = newly & (flag == "E")
        if edited.any():
            fig.add_trace(go.Scattergl(
                x=x[edited], y=df.loc[edited, col],
                name=f"{name_prefix}edited @ {label}",
                mode="markers", marker=dict(color=EDITED_COLOR, symbol="square", size=6),
                text=reason[edited] if reason is not None else None,
                hovertemplate="%{y}<br>%{text}<extra></extra>" if reason is not None else None,
            ))

        suspect = newly & (flag == "S")
        if suspect.any():
            fig.add_trace(go.Scattergl(
                x=x[suspect], y=df.loc[suspect, col],
                name=f"{name_prefix}suspect @ {label}",
                mode="markers",
                marker=dict(color=SUSPECT_COLOR, symbol="diamond", size=6),
                text=reason[suspect] if reason is not None else None,
                hovertemplate="%{y}<br>%{text}<extra></extra>" if reason is not None else None,
            ))


def _add_generated_regions(fig, df, x_col):
    """Shade the background opaque across timestamp ranges regrid_timestamps
    inserted -- rows where no data collection happened at all (a structural
    gap in the raw record), distinct from the thinner "gap in the line" signal
    used for an ordinary missing/removed value at a real timestamp.

    Draws every run as one combined shape via a single SVG path, not one
    fig.add_vrect() call per run -- add_vrect's per-shape overhead is fine for
    a handful of long gaps but becomes minutes-to-hours of write_html time
    once a station's real data is fragmented into thousands of short ones
    (observed: one station-year with ~5,000 separate gap runs)."""
    if "timestamp_generated" not in df.columns:
        return
    gen = df["timestamp_generated"].fillna(False)
    if not gen.any():
        return
    x = df[x_col]
    run_id = (gen != gen.shift()).cumsum()
    spans = (
        x[gen].groupby(run_id[gen])
        .agg(["min", "max"])
    )
    path = " ".join(
        f"M{x0.isoformat()},0 L{x1.isoformat()},0 L{x1.isoformat()},1 L{x0.isoformat()},1 Z"
        for x0, x1 in zip(spans["min"], spans["max"])
    )
    fig.add_shape(
        type="path", path=path, xref="x", yref="paper",
        fillcolor=GENERATED_COLOR, opacity=0.25, line_width=0, layer="below",
    )


def plot_variables(df, specs, station, out_path, x_col="datetime_PST", title=None,
                   thr=None, all_specs=None, edits=None):
    """Write one interactive HTML overlaying one or more variables (e.g. the
    max/min/avg from a single physical sensor). With a single variable, color
    carries the level (raw/L1.5/L2/L3), same as always. With more than one,
    color is repurposed to carry the variable instead -- a high-contrast color
    per variable is far easier to tell apart at a glance than dash style would
    be -- and dash carries the level instead. Returns the path, or None if none
    of the variables have data at any level.

    thr/all_specs/edits are optional and, when given, add a hover-text reason
    to each Edited/Suspect marker explaining which check caused it (see
    explain.explain_flag). all_specs should be the *full* station variable
    list (not just this plot's group), since a reason can point at another
    sensor_group (e.g. RH depends_on T); it defaults to `specs` when omitted,
    which is only correct for a plot whose group has no outside dependency."""
    x = df[x_col]
    per_var = [(sp, _present_levels(df, sp)) for sp in specs]
    per_var = [(sp, levels) for sp, levels in per_var if levels]
    if not per_var:
        return None
    multi = len(per_var) > 1
    reason_specs = all_specs if all_specs is not None else specs

    fig = go.Figure()
    _add_generated_regions(fig, df, x_col)
    for i, (sp, levels) in enumerate(per_var):
        var_color = VARIABLE_COLORS[i % len(VARIABLE_COLORS)]
        prefix = f"{sp.value_col} " if multi else ""
        for suf, label, color, col in levels:
            line_color = var_color if multi else color
            dash = LEVEL_DASH[suf] if multi else "solid"
            fig.add_trace(go.Scattergl(
                x=x, y=df[col], name=f"{prefix}{label}", mode="lines",
                line=dict(color=line_color, width=1, dash=dash),
            ))
        _add_quality_markers(fig, df, sp, levels, x, thr=thr, all_specs=reason_specs,
                             station=station, edits=edits, name_prefix=prefix)

    fig.update_layout(
        title=title or f"{station} — {', '.join(sp.value_col for sp, _ in per_var)}",
        height=560, hovermode="x unified",
        legend=dict(orientation="h", y=-0.16),
        margin=dict(t=60),
    )
    fig.write_html(out_path, include_plotlyjs="cdn", full_html=True)
    return out_path


def plot_variable(df, sp, station, out_path, x_col="datetime_PST",
                  thr=None, all_specs=None, edits=None):
    """Write one interactive HTML for a single variable. Returns the path, or None
    if the variable has no data at any level."""
    return plot_variables(df, [sp], station, out_path, x_col=x_col,
                          thr=thr, all_specs=all_specs, edits=edits)


def plot_station(df, specs, station, out_dir, x_col="datetime_PST",
                 thr=None, edits=None):
    """Write one HTML per usable variable (or sensor_group of variables) into
    out_dir, plus an index.html linking them. Returns the index path.

    Pass thr (and edits, for an L3 frame) to annotate Edited/Suspect markers
    with why each point was flagged -- see plot_variables."""
    os.makedirs(out_dir, exist_ok=True)

    groups = {}
    for sp in specs:
        key = sp.sensor_group or sp.value_col
        groups.setdefault(key, []).append(sp)

    written = []
    for key, group_specs in groups.items():
        fname = key.replace("/", "_") + ".html"
        p = plot_variables(df, group_specs, station, os.path.join(out_dir, fname), x_col=x_col,
                           thr=thr, all_specs=specs, edits=edits)
        if p:
            label = " / ".join(sp.value_col for sp in group_specs)
            written.append((label, fname))

    links = "\n".join(
        f'<li><a href="{fn}">{name}</a></li>' for name, fn in written
    )
    index = f"""<!doctype html><meta charset="utf-8">
<title>{station} — QC review</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:40rem}}
h1{{font-size:1.3rem}} li{{margin:.25rem 0}} a{{text-decoration:none}}</style>
<h1>{station} — QC review</h1>
<p>Raw &rarr; L1.5 &rarr; L2 &rarr; L3 overlaid per variable. Variables from the
same sensor (e.g. max/min/avg) share one plot: color distinguishes the
variable there, solid/dash/dot/dash-dot distinguishes the level instead. Orange
&#9632; = value edited (modified but kept) by QC at that level. Pink &#9670; =
value kept but flagged Suspect. Hover a marker to see which check flagged it.
Missing (removed) points get no marker &mdash; look for the gap in the line.
Gray shaded bands mark timestamps that did not exist in the raw record at all
(inserted to keep the 10-minute grid continuous), not just a missing value at
a real timestamp.</p>
<ul>{links}</ul>"""
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w") as f:
        f.write(index)
    return index_path
