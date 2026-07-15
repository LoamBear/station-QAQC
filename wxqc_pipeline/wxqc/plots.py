"""
QC review plots. For each variable, build a small interactive HTML overlaying its
trace at every level that exists (raw, L1.5, L2, L3) so the operator can see
exactly what each QC step changed, plus red markers on the samples QC removed.
A per-station index.html links them all.

One file per variable (not one giant file per station): a combined file embeds
every variable's full 10-minute series and balloons to >100 MB, which chokes the
browser. Per-variable files stay a few MB and open instantly, and you open the
one you want to review. Interactive (Plotly + WebGL) because a QC review needs
zoom; plotly.js loads from a CDN so files stay small.
"""
import os
import plotly.graph_objects as go

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


def plot_variable(df, sp, station, out_path, x_col="datetime_PST", flag_level="_L2"):
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

    fcol = sp.flag_col + flag_level
    if fcol in df.columns and sp.value_col in df.columns:
        removed = (df[fcol] == "S") & df[sp.value_col].notna()
        if removed.any():
            fig.add_trace(go.Scattergl(
                x=x[removed], y=df.loc[removed, sp.value_col],
                name=f"removed @ {flag_level.lstrip('_') or 'L2'}",
                mode="markers", marker=dict(color="#d62728", symbol="x", size=5),
            ))

    fig.update_layout(
        title=f"{station} — {sp.value_col}",
        height=560, hovermode="x unified",
        legend=dict(orientation="h", y=-0.16),
        margin=dict(t=60),
    )
    fig.write_html(out_path, include_plotlyjs="cdn", full_html=True)
    return out_path


def plot_station(df, specs, station, out_dir, x_col="datetime_PST", flag_level="_L2"):
    """Write one HTML per usable variable into out_dir, plus an index.html linking
    them. Returns the index path."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for sp in specs:
        if not _present_levels(df, sp):
            continue
        fname = sp.value_col.replace("/", "_") + ".html"
        p = plot_variable(df, sp, station, os.path.join(out_dir, fname),
                          x_col=x_col, flag_level=flag_level)
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
<p>Raw &rarr; L1.5 &rarr; L2 &rarr; L3 overlaid per variable. Red &times; = removed by QC.</p>
<ul>{links}</ul>"""
    index_path = os.path.join(out_dir, "index.html")
    with open(index_path, "w") as f:
        f.write(index)
    return index_path
