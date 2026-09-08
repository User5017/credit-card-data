"""PNG export of the post charts: the chart payload dict the page embeds, drawn with matplotlib's Agg backend.

One image per chart spec that carries `post: True` (one per panel), written to docs/img/<chart id>.png by
`carddash render`. The image is drawn from the same payload dict as the page (same rows, labels, unit, step
setting, band, benchmark, recession shading), and shows the page's default window (the last five years) with the
caption saying so. Bytes are stable across renders of unchanged data: no pull date or generation time goes into the
image, the size and dpi are fixed, and savefig drops the 'Software' text chunk that would otherwise carry the
matplotlib version. Fonts are matplotlib's bundled DejaVu Sans, so a different OS or freetype build can shift a few
pixels once; after that the file only changes when the data does.

Marks follow the page: 2 px lines, hairline grid, fixed series colors in the page's order, a legend for two or more
series, step charts drawn with drawstyle 'steps-pre' (each reading held across its own period, matching uPlot's
stepped align -1), y axis including zero unless the chart says otherwise, benchmark dashed grey, NBER recessions
as light bands.
"""

from __future__ import annotations

import datetime as dt
import math
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

# the page's --series-1..8 (light theme)
SERIES_COLORS = ["#2a78d6", "#c9541c", "#0f8a5e", "#7a4fd0", "#6b6a66", "#b5176a", "#8d5524", "#0e7c8a"]
BENCHMARK_COLOR = "#6b6a66"
INK, INK2, MUTED, GRID, BAND, RECESSION = "#0b0b0b", "#52514e", "#6f6d67", "#e1e0d9", "#2a78d6", "#0b0b0b"
SIZE_IN = (8.0, 4.5)  # 1200 x 675 px at 150 dpi
DPI = 150
CAPTION_WIDTH = 120
EPOCH = dt.date(1970, 1, 1)


def _axis_formatter(unit: str):
    if unit == "pct":
        return lambda x, _pos: f"{x:g}%"
    if unit == "usd_bn":
        return lambda x, _pos: f"${x:,.0f}bn"
    return lambda x, _pos: f"{x:,.0f}"


def write_png(chart: dict, path: Path, recessions: list[list[int]] | None = None, years: int | None = None) -> Path:
    """Draw one chart payload (render._chart_payload) to `path`. Returns the path."""
    # offset arithmetic, not fromtimestamp: Windows rejects negative epochs and the G.19 series starts in 1968
    xs = [EPOCH + dt.timedelta(seconds=int(t)) for t in chart["data"][0]]
    window_note = ""
    if years and xs and (xs[-1] - xs[0]).days > years * 365.25 * 1.1:
        start = xs[-1] - dt.timedelta(days=int(years * 365.25))
        keep = [i for i, d in enumerate(xs) if d >= start]
        xs = [xs[i] for i in keep]
        series_data = [[ys[i] for i in keep] for ys in chart["data"][1:]]
        window_note = f" · last {years} years shown, full history on the page"
    else:
        series_data = chart["data"][1:]
    fig, ax = plt.subplots(figsize=SIZE_IN, dpi=DPI)
    fig.patch.set_facecolor("#f9f9f7")
    ax.set_facecolor("#fcfcfb")
    lo, hi = math.inf, -math.inf
    plotted = []
    for i, (s, ys) in enumerate(zip(chart["series"], series_data)):
        vals = [math.nan if v is None else float(v) for v in ys]
        finite = [v for v in vals if not math.isnan(v)]
        if finite:
            lo, hi = min(lo, min(finite)), max(hi, max(finite))
        plotted.append(vals)
        if s.get("benchmark"):
            ax.plot(xs, vals, color=BENCHMARK_COLOR, linewidth=1, linestyle="--", label=s["label"])
            continue
        kw = {"drawstyle": "steps-pre"} if chart["step"] else {}
        if s.get("dash"):
            kw["linestyle"] = "--"
        ax.plot(xs, vals, color=SERIES_COLORS[i % len(SERIES_COLORS)], linewidth=2, label=s["label"], **kw)
    if chart.get("band") and len(plotted) >= max(chart["band"]):
        a, b = (plotted[k - 1] for k in chart["band"])
        ax.fill_between(xs, a, b, step="pre" if chart["step"] else None, color=BAND, alpha=0.12, linewidth=0)
    if not math.isfinite(lo):
        lo, hi = 0.0, 1.0
    if chart.get("y_zero", True):
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    pad = (hi - lo) * 0.06 or 1.0
    ax.set_ylim(lo - pad if (lo < 0 or not chart.get("y_zero", True)) else 0, hi + pad)
    if recessions and xs:
        for start, end in recessions:
            s0, s1 = EPOCH + dt.timedelta(seconds=int(start)), EPOCH + dt.timedelta(seconds=int(end))
            if s1 >= xs[0] and s0 <= xs[-1]:
                ax.axvspan(max(s0, xs[0]), min(s1, xs[-1]), color=RECESSION, alpha=0.06, linewidth=0)
    ax.yaxis.set_major_formatter(_axis_formatter(chart["unit"]))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y" if not window_note else "%b %Y"))
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_title(chart["title"], loc="left", fontsize=12, fontweight="bold", color=INK, pad=10)
    ax.set_ylabel(chart["unit_label"], fontsize=9, color=INK2)
    if len(chart["series"]) > 1:
        ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="best")
    caption = textwrap.fill((chart.get("caption") or "") + window_note, CAPTION_WIDTH)
    fig.text(0.01, 0.01, caption, fontsize=7.5, color=MUTED, va="bottom", ha="left")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", dpi=DPI, metadata={"Software": None})
    plt.close(fig)
    return path
