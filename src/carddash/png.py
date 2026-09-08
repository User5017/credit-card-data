"""PNG export of the post charts: the chart payload dict the page embeds, drawn with matplotlib's Agg backend.

One image per chart spec that carries `post: True` (one per panel), written to docs/img/<chart id>.png by
`carddash render`. The image is drawn from the same payload dict as the page (same rows, labels, unit, step
setting), so the numbers cannot diverge from the chart on the site. Bytes are stable across renders of unchanged
data: no pull date or generation time goes into the image, the size and dpi are fixed, and savefig drops the
'Software' text chunk that would otherwise carry the matplotlib version. Fonts are matplotlib's bundled DejaVu
Sans, so a different OS or freetype build can shift a few pixels once; after that the file only changes when the
data does.

Marks follow the page: 2 px lines, hairline grid, fixed series colors in the page's order, a legend for two or more
series, step charts drawn with drawstyle 'steps-pre' (each reading held across its own period, matching uPlot's
stepped align -1), y axis always including zero.
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

SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#8a63d2"]  # the page's --series-1..3 plus a fourth
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
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


def write_png(chart: dict, path: Path) -> Path:
    """Draw one chart payload (render._chart_payload) to `path`. Returns the path."""
    # offset arithmetic, not fromtimestamp: Windows rejects negative epochs and the G.19 series starts in 1968
    xs = [EPOCH + dt.timedelta(seconds=int(t)) for t in chart["data"][0]]
    fig, ax = plt.subplots(figsize=SIZE_IN, dpi=DPI)
    fig.patch.set_facecolor("#f9f9f7")
    ax.set_facecolor("#fcfcfb")
    lo, hi = 0.0, 0.0
    for i, (s, ys) in enumerate(zip(chart["series"], chart["data"][1:])):
        vals = [math.nan if v is None else float(v) for v in ys]
        finite = [v for v in vals if not math.isnan(v)]
        if finite:
            lo, hi = min(lo, min(finite)), max(hi, max(finite))
        kw = {"drawstyle": "steps-pre"} if chart["step"] else {}
        ax.plot(xs, vals, color=SERIES_COLORS[i % len(SERIES_COLORS)], linewidth=2, label=s["label"], **kw)
    pad = (hi - lo) * 0.06 or 1.0
    ax.set_ylim(lo - pad if lo < 0 else 0, hi + pad)
    ax.yaxis.set_major_formatter(_axis_formatter(chart["unit"]))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
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
        ax.legend(frameon=False, fontsize=9, labelcolor=INK2, loc="upper left")
    caption = textwrap.fill(chart.get("caption") or "", CAPTION_WIDTH)
    fig.text(0.01, 0.01, caption, fontsize=7.5, color=MUTED, va="bottom", ha="left")
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="png", dpi=DPI, metadata={"Software": None})
    plt.close(fig)
    return path
