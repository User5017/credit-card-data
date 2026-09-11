"""Federal Reserve Distributional Financial Accounts (DFA): the household balance sheet by wealth, income and age group.

Publication facts, verified 2026-09-10:
- One zip at https://www.federalreserve.gov/releases/z1/dataviz/download/zips/dfa.zip, about 0.9 MB, direct link, no
  key, no gate. Inside: for each of six splits (networth, income, age, generation, education, race) a summary and a
  detail file, each in levels (millions of dollars) and in shares (percent), plus dictionary text files. Quarterly
  from 1989 Q3, dates written 'YYYY:Qn'. Loaded here: the three *-levels-detail.csv files for wealth percentile,
  income percentile and age, which carry Consumer credit, Deposits, Liabilities, Net worth and Household count for
  every group. Generation, education and race are in the zip and not loaded.
- Groups: wealth TopPt1, RemainingTop1, Next9, Next40, Bottom50 (percentiles of net worth); income pct99to100,
  pct80to99, pct60to80, pct40to60, pct20to40, pct00to20; age ageunder40, age40to54, age55to69, age70plus. Each split
  partitions every household, so the three splits sum to the same total per quarter (asserted, within rounding).
  The sum over groups is loaded as its own entity (DFA_ALL_HOUSEHOLDS), the one arithmetic done here.
- 'Consumer credit' is all consumer credit (cards, auto, student and other), the household sector's line in the
  Financial Accounts table B.101.h, which is the G.19 total (not seasonally adjusted) at the Z.1 vintage. The sum
  reproduces B.101.h line 23 in the release PDF (golden). 'Deposits' is checkable deposits and currency plus time
  deposits and short-term investments.
- The DFA is published about a week after each quarterly Z.1 release (Z.1 for 2026 Q1 on 2026-06-11, DFA on
  2026-06-18), roughly eleven weeks after quarter end. Every release re-publishes and revises the whole history
  with the Z.1 revisions, so the live golden sits on a year-end level and the newest quarter is fixture-only.

Outputs: raw_dir/latest/dfa.zip byte for byte, and one facts row per group, metric and quarter. Millions of dollars
become billions here (scale 0.001 in series.csv); household counts become millions.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "dfa"
LANDING_URL = "https://www.federalreserve.gov/releases/z1/dataviz/dfa/distribute/table/"
ZIP_URL = "https://www.federalreserve.gov/releases/z1/dataviz/download/zips/dfa.zip"
RAW_NAME = "dfa.zip"
ZIP_MAGIC = b"PK\x03\x04"
PERIOD_TYPE = "Q"
FIRST_QUARTER = (1989, 3)
ENTITY_ALL = "DFA_ALL_HOUSEHOLDS"
TOTAL_TOLERANCE = 2e-4  # the three splits must agree on every total within 0.02 percent (rounding to a million)

# file inside the zip -> Category value -> (entity, entity_type)
SPLITS = {
    "dfa-networth-levels-detail.csv": {
        "TopPt1": ("WEALTH:TOP0_1", "wealth"),
        "RemainingTop1": ("WEALTH:NEXT0_9", "wealth"),
        "Next9": ("WEALTH:NEXT9", "wealth"),
        "Next40": ("WEALTH:NEXT40", "wealth"),
        "Bottom50": ("WEALTH:BOTTOM50", "wealth"),
    },
    "dfa-income-levels-detail.csv": {
        "pct99to100": ("INCOME_PCT:99-100", "income"),
        "pct80to99": ("INCOME_PCT:80-99", "income"),
        "pct60to80": ("INCOME_PCT:60-80", "income"),
        "pct40to60": ("INCOME_PCT:40-60", "income"),
        "pct20to40": ("INCOME_PCT:20-40", "income"),
        "pct00to20": ("INCOME_PCT:0-20", "income"),
    },
    "dfa-age-levels-detail.csv": {
        "ageunder40": ("AGE:LT40", "age"),
        "age40to54": ("AGE:40-54", "age"),
        "age55to69": ("AGE:55-69", "age"),
        "age70plus": ("AGE:70PLUS", "age"),
    },
}
TOTAL_FROM = "dfa-networth-levels-detail.csv"  # the split whose sum becomes DFA_ALL_HOUSEHOLDS

# metric -> column header in the detail files. Millions of dollars and household counts in the file; the scale to
# billions and to millions of households is the series.csv row's, applied in parse_zip.
METRICS = {
    "dfa_consumer_credit": "Consumer credit",
    "dfa_deposits": "Deposits",
    "dfa_liabilities": "Liabilities",
    "dfa_net_worth": "Net worth",
    "dfa_households": "Household count",
}

_QUARTER_RE = re.compile(r"^(\d{4}):Q([1-4])$")
_QUARTER_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def looks_like_zip(content: bytes) -> bool:
    return content[:4] == ZIP_MAGIC


def parse_quarter(text: str) -> tuple[int, int]:
    """'2026:Q1' -> (2026, 1)."""
    m = _QUARTER_RE.match(str(text).strip())
    if not m:
        raise ValueError(f"not a YYYY:Qn quarter: {text!r}")
    return int(m.group(1)), int(m.group(2))


def quarter_end(year: int, q: int):
    return last_day(year, _QUARTER_MONTH[q])


def next_quarter(year: int, q: int) -> tuple[int, int]:
    return (year + 1, 1) if q == 4 else (year, q + 1)


def _num(value, where: str) -> float:
    t = str(value).strip() if value is not None else ""
    if not t:
        raise ValueError(f"{where}: empty value")
    try:
        return float(t)
    except ValueError as exc:
        raise ValueError(f"{where}: not a number: {value!r}") from exc


def read_split(text: str, name: str) -> dict[tuple[str, str], pd.DataFrame]:
    """One detail file -> {(entity, entity_type): frame indexed by quarter with one column per metric, in source
    units}. Every listed group must be present with consecutive quarters from FIRST_QUARTER, all ending on the
    same quarter; an unlisted group fails (the DFA adding a category is something to look at, not to drop)."""
    groups = SPLITS[name]
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lstrip("﻿") for h in (reader.fieldnames or [])]
    needed = ["Date", "Category"] + list(METRICS.values())
    missing = [c for c in needed if c not in header]
    if missing:
        raise ValueError(f"{name}: header lacks {missing}; header is {header}")
    by_group: dict[str, list[tuple[tuple[int, int], dict]]] = {}
    for raw in reader:
        row = {k.strip().lstrip("﻿"): v for k, v in raw.items() if k is not None}
        cat = row["Category"].strip()
        if cat not in groups:
            raise ValueError(f"{name}: unknown Category {cat!r}; known: {sorted(groups)}")
        yq = parse_quarter(row["Date"])
        by_group.setdefault(cat, []).append((yq, row))
    absent = [g for g in groups if g not in by_group]
    if absent:
        raise ValueError(f"{name}: no rows for {absent}")
    out: dict[tuple[str, str], pd.DataFrame] = {}
    ends = set()
    for cat, rows in by_group.items():
        rows.sort(key=lambda r: r[0])
        if rows[0][0] != FIRST_QUARTER:
            raise ValueError(f"{name}/{cat}: starts at {rows[0][0]}, expected {FIRST_QUARTER}")
        for (a, _), (b, _) in zip(rows, rows[1:]):
            if next_quarter(*a) != b:
                raise ValueError(f"{name}/{cat}: quarters jump from {a} to {b}")
        ends.add(rows[-1][0])
        frame = pd.DataFrame(
            {
                "period_end": [pd.Timestamp(quarter_end(*yq)) for yq, _ in rows],
                **{
                    metric: [_num(row[col], f"{name}/{cat}/{col}/{yq}") for yq, row in rows]
                    for metric, col in METRICS.items()
                },
            }
        ).set_index("period_end")
        out[groups[cat]] = frame
    if len(ends) != 1:
        raise ValueError(f"{name}: groups end on different quarters: {sorted(ends)}")
    return out


def _scales(meta: pd.DataFrame) -> dict[tuple[str, str], float]:
    """(metric, entity) -> scale from series.csv; every listed row is quarterly with tier all."""
    out = {}
    for _, row in meta.iterrows():
        if row["period_type"] != PERIOD_TYPE or row["tier"] != "all":
            raise ValueError(f"series.csv: {row['metric']}/{row['entity']} must be quarterly with tier all")
        out[(row["metric"], row["entity"])] = float(row["scale"]) if row["scale"] not in ("", None) else 1.0
    return out


def parse_zip(content: bytes, meta: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """The DFA zip -> facts: every group of the three splits plus the sum over groups (DFA_ALL_HOUSEHOLDS), scaled
    per the series.csv rows (source dfa). The three splits must agree on every total to TOTAL_TOLERANCE and end on
    the same quarter, and the file's series must be exactly the crosswalk's: a new DFA group or a renamed column is
    something to look at, not to load or drop quietly."""
    scales = _scales(meta)
    if not looks_like_zip(content):
        raise ValueError(f"not a zip (first bytes {content[:8]!r})")
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = set(z.namelist())
        absent = [n for n in SPLITS if n not in names]
        if absent:
            raise ValueError(f"zip lacks {absent}; has {sorted(names)[:8]}...")
        splits = {name: read_split(z.read(name).decode("utf-8-sig"), name) for name in SPLITS}
    totals = {name: sum(frames.values()) for name, frames in splits.items()}
    base = totals[TOTAL_FROM]
    for name, tot in totals.items():
        if not tot.index.equals(base.index):
            raise ValueError(f"{name}: quarters differ from {TOTAL_FROM}")
        rel = ((tot - base).abs() / base.abs().clip(lower=1.0)).max().max()
        if rel > TOTAL_TOLERANCE:
            raise ValueError(f"{name}: totals differ from {TOTAL_FROM} by {rel:.2%}, above {TOTAL_TOLERANCE:.2%}")
    frames = []
    entities = [(key, frame) for split in splits.values() for key, frame in split.items()]
    entities.append(((ENTITY_ALL, "aggregate"), base))
    got = {(metric, entity) for (entity, _), _ in entities for metric in METRICS}
    if got != set(scales):
        raise ValueError(
            f"series in the file but not in series.csv: {sorted(got - set(scales))[:5]}; "
            f"listed but not in the file: {sorted(set(scales) - got)[:5]}"
        )
    for (entity, entity_type), frame in entities:
        for metric in METRICS:
            scale = scales[(metric, entity)]
            frames.append(
                pd.DataFrame(
                    {
                        "metric": metric,
                        "entity": entity,
                        "entity_type": entity_type,
                        "tier": "all",
                        "period_end": frame.index,
                        "period_type": PERIOD_TYPE,
                        "value": frame[metric].to_numpy() * scale,
                        "source": SOURCE,
                        "pulled_at": pulled_at,
                    }
                )[FACT_COLUMNS]
            )
    return pd.concat(frames, ignore_index=True)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    resp = session.get(ZIP_URL)
    resp.raise_for_status()
    content = resp.content
    if not looks_like_zip(content):
        raise ValueError(f"{ZIP_URL}: not a zip (first bytes {content[:40]!r})")
    latest = raw_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / RAW_NAME).write_bytes(content)
    return parse_zip(content, meta, pulled_at)
