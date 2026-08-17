"""
QC review plots. For each variable, build a small interactive HTML overlaying its
trace at every level that exists (raw, L1.5, L2, L3) so the operator can see
exactly what each QC step changed: markers indicate data *quality*, not removal --
an orange square where a value was newly Edited (modified but kept), a pink
diamond where a value was newly flagged Suspect but kept (e.g. a manual set_flag
edit, or a gap-fill that reused a still-suspect sample). Missing (removed) points
get no marker at all -- the gap in the line already shows that. A per-station
index.html links them all.

One file per variable (not one giant file per station): a combined file embeds
every variable's full 10-minute series and balloons to >100 MB, which chokes the
browser. Per-variable files stay a few MB and open instantly, and you open the
one you want to review. Interactive (Plotly + WebGL) because a QC review needs
zoom; plotly.js loads from a CDN so files stay small.
"""
import os
import plotly.graph_objects as go

EDITED_COLOR = "#ff7f0e"   # orange, for values modified but kept
SUSPECT_COLOR = "#e377c2"  # pink, for values kept but flagged Suspect

# level suffix -> (legend label, color)
LEVELS = [
    ("",    "raw",  "#b0b0b0"),
    ("_L1", "L1.5", "#1f77b4"),
    ("_L2", "L2",   "#2ca02c"),
    ("_L3", "L3",   "#9467bd"),
]


def _present_levels(df, sp):
    out = []
    for suf, label, color in LEVELS:
        col = sp.value_col + suf
        if col in df.columns and df[col].notna().any():
            out.append((suf, label, color, col))
    return out


def plot_variable(df, sp, station, out_path, x_col="datetime_PST"):
    """Write one interactive HTML for a single variable. Returns the path, or None
    if the variable has no data at any level."""
    levels = _present_levels(df, sp)
    if not levels:
        return None
    x = df[x_col]

    fig = go.Figure()
    for suf, label, color, col in levels:
        fig.add_trace(go.Scattergl(
            x=x, y=df[col], name=label, mode="lines",
            line=dict(color=color, width=1),
        ))

    # Quality markers, per level, for points newly *marked* -- flagged 'E' or
    # 'S' *and* carrying a value -- at that level, not already marked at a
    # prior one. Tracking is on the marked state (flag + has-a-value), not the
    # flag alone: a point that was removed at L1.5 (flag 'S', no value) and
    # then gap-filled back in at L2 (still flag 'S', now has a value) counts as
    # newly marked at L2, since that's the first level it had both. Removed
    # (flag 'S', value NaN) points never get a marker: the gap in the line
    # already says "missing".
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

        edited = newly & (flag == "E")
        if edited.any():
            fig.add_trace(go.Scattergl(
                x=x[edited], y=df.loc[edited, col],
                name=f"edited @ {label}",
                mode="markers", marker=dict(color=EDITED_COLOR, symbol="square", size=6),
            ))

        suspect = newly & (flag == "S")
        if suspect.any():
            fig.add_trace(go.Scattergl(
                x=x[suspect], y=df.loc[suspect, col],
                name=f"suspect @ {label}",
                mode="markers",
                marker=dict(color=SUSPECT_COLOR, symbol="diamond", size=6),
            ))

    fig.update_layout(
        title=f"{station} — {sp.value_col}",
        height=560, hovermode="x unified",
        legend=dict(orientation="h", y=-0.16),
        margin=dict(t=60),
    )
    fig.write_html(out_path, include_plotlyjs="cdn", full_html=True)
    return out_path


def plot_station(df, specs, station, out_dir, x_col="datetime_PST"):
    """Write one HTML per usable variable into out_dir, plus an index.html linking
    them. Returns the index path."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for sp in specs:
        if not _present_levels(df, sp):
            continue
        fname = sp.value_col.replace("/", "_") + ".html"
        p = plot_variable(df, sp, station, os.path.join(out_dir, fname), x_col=x_col)
        if p:
            written.append((sp.value_col, fname))

    links = "\n".join(
        f'<li><a href="{fn}">{name}</a></li>' for name, fn in written
    )
    index = f"""<!doctype html><meta charset="utf-8">
<title>{station} — QC review</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:40rem}}
h1{{font-size:1.3rem}} li{{margin:.25rem 0}} a{{text-decoration:none}}</style>
<h1>{station} — QC review</h1>
<p>Raw &rarr; L1.5 &rarr; L2 &rarr; L3 overlaid per variable. Orange &#9632; = value
edited (modified but kept) by QC at that level. Pink &#9670; = value kept but
flagged Suspect. Missing (removed) points get no marker &mdash; look for the gap
in the line.</p>
<ul>{links}</ul>"""
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w") as f:
        f.write(index)
    return index_path
