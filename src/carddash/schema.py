"""The facts table: the one shape every source is reduced to.

One row per metric x entity x tier x period x source. Conventions:
- period_end is the LAST day of the period (month end, quarter end, the Wednesday for H.8 weekly data).
- Seasonal adjustment is part of the metric name (suffix _sa / _nsa) where the source offers both.
- entity is a prefixed id ("ALL_HOLDERS", "COMBANKS_TOP100", later "CERT:34404", "STATE:ME");
  entity_type says what kind of thing it is so views never parse prefixes.
- unit lives in crosswalks/series.csv (dim_series), not on the fact row.
"""

from __future__ import annotations

import calendar
import datetime as dt

import pandas as pd

FACT_COLUMNS = [
    "metric",
    "entity",
    "entity_type",
    "tier",
    "period_end",
    "period_type",
    "value",
    "source",
    "pulled_at",
]
KEY_COLUMNS = ["metric", "entity", "tier", "period_end", "period_type", "source"]
SERIES_KEY = ["metric", "entity", "tier", "period_type", "source"]

FACT_DTYPES = {
    "metric": "string",
    "entity": "string",
    "entity_type": "string",
    "tier": "string",
    "period_end": "datetime64[ns]",
    "period_type": "string",
    "value": "float64",
    "source": "string",
    "pulled_at": "string",
}

# D daily, W weekly, M monthly, Q quarterly, T every four months (NY Fed SCE), H semiannual, A annual
PERIOD_TYPES = {"D", "W", "M", "Q", "T", "H", "A"}
PERIOD_DAYS = {"D": 1, "W": 7, "M": 31, "Q": 92, "T": 123, "H": 184, "A": 366}
PERIOD_WORDS = {
    "D": "daily",
    "W": "weekly",
    "M": "monthly",
    "Q": "quarterly",
    "T": "every four months",
    "H": "semiannual",
    "A": "annual",
}

ENTITY_TYPES = {"aggregate", "issuer", "bank", "state", "age", "pce", "naics"}


def last_day(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def period_end(date: dt.date, period_type: str) -> dt.date:
    """Map any date inside a period to that period's last day."""
    if period_type in ("D", "W"):
        return date
    if period_type == "M":
        return last_day(date.year, date.month)
    if period_type == "Q":
        return last_day(date.year, ((date.month - 1) // 3) * 3 + 3)
    if period_type == "T":
        return last_day(date.year, ((date.month - 1) // 4) * 4 + 4)
    if period_type == "H":
        return last_day(date.year, 6 if date.month <= 6 else 12)
    if period_type == "A":
        return dt.date(date.year, 12, 31)
    raise ValueError(f"unknown period_type {period_type!r}")


_MONTHS_PER_PERIOD = {"M": 1, "Q": 3, "T": 4, "H": 6, "A": 12}


def shift_period(date: dt.date, period_type: str, n: int) -> dt.date:
    """The period end n periods after (n < 0: before) the period containing `date`.

    For a source whose own dating is off by a fixed number of periods (the SLOOS July survey asks about the quarter
    that just ended, FRED dates it to the quarter it was taken in). Weekly and daily series shift by whole weeks/days.
    """
    if period_type == "D":
        return date + dt.timedelta(days=n)
    if period_type == "W":
        return date + dt.timedelta(weeks=n)
    end = period_end(date, period_type)
    if n == 0:
        return end
    months = end.year * 12 + (end.month - 1) + _MONTHS_PER_PERIOD[period_type] * n
    return period_end(dt.date(months // 12, months % 12 + 1, 1), period_type)


def empty_facts() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=t) for c, t in FACT_DTYPES.items()})


def coerce_facts(df: pd.DataFrame) -> pd.DataFrame:
    """Force the canonical column set and dtypes. Missing columns raise."""
    missing = [c for c in FACT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"facts frame missing columns: {missing}")
    out = df[FACT_COLUMNS].copy()
    for c, t in FACT_DTYPES.items():
        if c == "period_end":
            out[c] = pd.to_datetime(out[c]).dt.normalize()
        elif c == "value":
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
        else:
            out[c] = out[c].astype(t)
    return out.reset_index(drop=True)
