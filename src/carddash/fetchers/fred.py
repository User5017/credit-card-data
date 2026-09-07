"""FRED fetcher: Fed G.19 consumer credit, H.8 bank card loans, charge-off/delinquency rates, SLOOS.

Two transports, same parse:
- with FRED_API_KEY set: the official observations API (JSON)
- without: the public fredgraph.csv endpoint, which needs no key

Series list and metadata come from crosswalks/series.csv (source == "fred").
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, period_end

SOURCE = "fred"
FREDGRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
API_URL = "https://api.stlouisfed.org/fred/series/observations"


def parse_fredgraph(text: str) -> pd.DataFrame:
    """fredgraph.csv -> DataFrame[date, value]. Missing values are '.', dropped."""
    df = pd.read_csv(io.StringIO(text), na_values=["."], dtype={0: str})
    if df.shape[1] != 2:
        raise ValueError(f"expected 2 columns from fredgraph, got {list(df.columns)}")
    date_col = df.columns[0]
    if date_col.lower() not in ("observation_date", "date"):
        raise ValueError(f"unexpected first column {date_col!r}")
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df[date_col], format="%Y-%m-%d"),
            "value": pd.to_numeric(df.iloc[:, 1], errors="coerce"),
        }
    )
    return out.dropna(subset=["value"]).reset_index(drop=True)


def parse_api_json(text: str) -> pd.DataFrame:
    """FRED API observations JSON -> DataFrame[date, value]."""
    payload = json.loads(text)
    obs = payload.get("observations")
    if obs is None:
        raise ValueError(f"no observations in API response: {str(payload)[:200]}")
    df = pd.DataFrame(obs)
    if df.empty:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "value": pd.Series(dtype="float64")})
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"], format="%Y-%m-%d"),
            "value": pd.to_numeric(df["value"].replace(".", None), errors="coerce"),
        }
    )
    return out.dropna(subset=["value"]).reset_index(drop=True)


def to_facts(obs: pd.DataFrame, meta: pd.Series, pulled_at: str) -> pd.DataFrame:
    """Observations for one series -> facts rows, using the series.csv metadata row."""
    ptype = meta["period_type"]
    scale = float(meta["scale"]) if meta["scale"] not in ("", None) else 1.0
    df = pd.DataFrame(
        {
            "metric": meta["metric"],
            "entity": meta["entity"],
            "entity_type": meta["entity_type"],
            "tier": meta["tier"],
            "period_end": [pd.Timestamp(period_end(d.date(), ptype)) for d in obs["date"]],
            "period_type": ptype,
            "value": obs["value"].astype("float64") * scale,
            "source": SOURCE,
            "pulled_at": pulled_at,
        }
    )
    return df[FACT_COLUMNS]


def _download(session, sid: str, raw_dir: Path, api_key: str | None) -> pd.DataFrame:
    raw_dir.mkdir(parents=True, exist_ok=True)
    if api_key:
        resp = session.get(
            API_URL,
            params={
                "series_id": sid,
                "api_key": api_key,
                "file_type": "json",
                "observation_start": "1900-01-01",
            },
        )
        resp.raise_for_status()
        (raw_dir / f"{sid}.json").write_text(resp.text, encoding="utf-8")
        return parse_api_json(resp.text)
    resp = session.get(FREDGRAPH_URL.format(sid=sid))
    resp.raise_for_status()
    if not resp.text.lstrip().lower().startswith(("observation_date", "date")):
        raise ValueError(f"{sid}: fredgraph returned something that is not a CSV (first bytes: {resp.text[:60]!r})")
    (raw_dir / f"{sid}.csv").write_text(resp.text, encoding="utf-8")
    return parse_fredgraph(resp.text)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    api_key = os.environ.get("FRED_API_KEY") or None
    frames = []
    for _, row in meta.iterrows():
        obs = _download(session, row["source_id"], raw_dir / "latest", api_key)
        if obs.empty:
            raise ValueError(f"{row['source_id']}: no observations")
        frames.append(to_facts(obs, row, pulled_at))
    return pd.concat(frames, ignore_index=True)
