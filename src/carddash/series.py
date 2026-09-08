"""dim_series: hand-maintained metadata for every series, loaded from crosswalks/series.csv."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import SERIES_KEY

SERIES_COLUMNS = [
    "source",
    "source_id",
    "metric",
    "entity",
    "entity_type",
    "tier",
    "period_type",
    "unit",
    "scale",
    "sa",
    "display_name",
    "scope_note",
    "source_url",
    "vmin",
    "vmax",
    "max_age_days",
    "period_offset",  # periods to shift the source's own dating by (SLOOS: -1, the July survey asks about Q2)
]


def load_series(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in SERIES_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns {missing}")
    df["scale"] = pd.to_numeric(df["scale"].replace("", "1"))
    for c in ("vmin", "vmax", "max_age_days"):
        df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    df["sa"] = df["sa"].str.lower().isin({"true", "1", "yes", "y"})
    df["period_offset"] = pd.to_numeric(df["period_offset"].replace("", "0")).astype(int)
    dupes = df[df.duplicated(SERIES_KEY, keep=False)]
    if not dupes.empty:
        raise ValueError(f"duplicate series keys in {path}:\n{dupes[SERIES_KEY]}")
    return df


def series_for_source(meta: pd.DataFrame, source: str) -> pd.DataFrame:
    return meta[meta["source"] == source].reset_index(drop=True)


def series_index(meta: pd.DataFrame) -> dict[tuple, pd.Series]:
    """(metric, entity, tier, period_type, source) -> metadata row."""
    return {tuple(row[k] for k in SERIES_KEY): row for _, row in meta.iterrows()}
