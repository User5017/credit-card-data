"""CFPB Consumer Credit Trends, credit cards: originations, new credit lines by credit score and age, inquiries.

Publication facts, verified 2026-09-10:
- Plain CSVs under https://files.consumerfinance.gov/data/consumer-credit-trends/credit-cards/, no key, no gate,
  the pipeline's own User-Agent accepted. Each has 'month' (an index, 0 = January 2000), 'date' (YYYY-MM) and a
  seasonally adjusted and an unadjusted column; the two group files add a group column. Months must be consecutive.
- Source: the CFPB Consumer Credit Panel, a 1-in-48 deidentified sample of credit records from one of the
  nationwide bureaus, so the counts cover every lender that reports to that bureau, not just the large banks of
  the Y-14. Counts are scaled to the population. The CFPB says the last six months are not final.
- Loaded here: num_data_CRC (number of cards originated), vol_data_CRC (total credit line on those cards),
  volume_data_Score_Level_CRC (credit line by credit score group: deep subprime under 580, subprime 580-619,
  near-prime 620-659, prime 660-719, super-prime 720 and up, the CFPB's FICO 8 convention in crosswalks/tiers.csv),
  volume_data_Age_Group_CRC (credit line by age: under 30, 30-44, 45-64, 65 and older), inq_data_CRC (consumers
  with a card inquiry, index January 2010 = 100) and crt_data_CRC (the credit tightness index: the rate of consumers
  with a card inquiry that did not lead to a new account, also indexed to January 2010 = 100, not a share: it runs
  from 71 to 113). The by-neighborhood-income file stops in April 2025 and
  the per-group number-of-cards files do not exist, so neither is loaded.
- Originations run to January 2026 as of the August 2026 update (about seven months behind), inquiries to May
  2026, tightness to March 2026. The page is updated roughly monthly with no fixed day.
- The group files do not sum exactly to the total file: each is scaled and seasonally adjusted on its own, so the
  five score groups come to within 5 percent of the total and the four age groups within 3 percent. The fetcher
  requires the sums to stay within GROUP_SUM_TOLERANCE of the total every month, which is what catches a dropped
  or duplicated group; it is a sanity check, not an identity.
- No release page states these numbers. The CFPB's biennial Consumer Credit Card Market Report counts originations
  from issuer data (89 million new accounts in 2024 against 83 million here) and does not reconcile to the panel,
  so this source ships without a golden entry, with the cross-file sum checks and fixture tests instead. Documented
  in CLAUDE.md as the one exception to the golden rule.

Outputs: raw_dir/latest/<file>.csv byte for byte for each of the six files, and one facts row per month per series.
Counts become millions and dollars billions here; the two indexes are transcribed as published.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "cfpb_cct"
LANDING_URL = "https://www.consumerfinance.gov/data-research/consumer-credit-trends/credit-cards/"
BASE_URL = "https://files.consumerfinance.gov/data/consumer-credit-trends/credit-cards/"
PERIOD_TYPE = "M"
ENTITY_ALL = "CFPB_CCP_ALL"
GROUP_SUM_TOLERANCE = 0.06  # every month, the group files must sum to within 6 percent of the total file
MONTH_INDEX_ORIGIN = (2000, 1)  # the 'month' column counts months from January 2000

SCORE_GROUPS = {
    "Deep subprime": "deep_subprime",
    "Subprime": "subprime",
    "Near-prime": "near_prime",
    "Prime": "prime",
    "Super-prime": "superprime",
}
AGE_GROUPS = {
    "Younger than 30": "AGE:LT30",
    "Age 30-44": "AGE:30-44",
    "Age 45-64": "AGE:45-64",
    "Age 65 and older": "AGE:65PLUS",
}

# file -> how to read it. 'columns' maps a value column to a metric; a group file also names its group column
# and, per group value, the (entity, entity_type, tier) the rows go to. 'total_of' names the total file a group
# file must sum to (per value column, same position). Scales (counts to millions, dollars to billions) are the
# series.csv rows', applied in parse_files.
FILES = {
    "num_data_CRC.csv": {
        "columns": {"num": "cct_card_originations_sa", "num_unadj": "cct_card_originations_nsa"},
    },
    "vol_data_CRC.csv": {
        "columns": {"vol": "cct_card_new_lines_sa", "vol_unadj": "cct_card_new_lines_nsa"},
    },
    "volume_data_Score_Level_CRC.csv": {
        "columns": {"vol": "cct_card_new_lines_sa", "vol_unadj": "cct_card_new_lines_nsa"},
        "group_column": "credit_score_group",
        "groups": {label: (ENTITY_ALL, "aggregate", tier) for label, tier in SCORE_GROUPS.items()},
        "total_of": "vol_data_CRC.csv",
    },
    "volume_data_Age_Group_CRC.csv": {
        "columns": {"vol": "cct_card_new_lines_sa", "vol_unadj": "cct_card_new_lines_nsa"},
        "group_column": "age_group",
        "groups": {label: (entity, "age", "all") for label, entity in AGE_GROUPS.items()},
        "total_of": "vol_data_CRC.csv",
    },
    "inq_data_CRC.csv": {
        "columns": {"inquiry_index": "cct_card_inquiry_index_sa", "unadjusted_inquiry_index": "cct_card_inquiry_index_nsa"},
    },
    "crt_data_CRC.csv": {
        "columns": {"tightness_index": "cct_card_tightness_sa", "unadjusted_credit_tightness_index": "cct_card_tightness_nsa"},
    },
}

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_month(text: str) -> tuple[int, int]:
    """'2026-01' -> (2026, 1)."""
    m = _DATE_RE.match(str(text).strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"not a YYYY-MM month: {text!r}")
    return int(m.group(1)), int(m.group(2))


def month_index(year: int, month: int) -> int:
    return (year - MONTH_INDEX_ORIGIN[0]) * 12 + (month - MONTH_INDEX_ORIGIN[1])


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _num(value, where: str) -> float:
    t = str(value).strip() if value is not None else ""
    if not t:
        raise ValueError(f"{where}: empty value")
    try:
        return float(t)
    except ValueError as exc:
        raise ValueError(f"{where}: not a number: {value!r}") from exc


def read_file(text: str, name: str) -> dict[str, pd.DataFrame]:
    """One CSV -> {group label (or '' for a total file): frame indexed by month end with one column per value
    column, in source units}. Header by name, months consecutive per group, the month index consistent with the
    date, every listed group present and no unlisted one."""
    spec = FILES[name]
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lstrip("﻿") for h in (reader.fieldnames or [])]
    group_col = spec.get("group_column")
    needed = ["month", "date"] + list(spec["columns"]) + ([group_col] if group_col else [])
    missing = [c for c in needed if c not in header]
    if missing:
        raise ValueError(f"{name}: header lacks {missing}; header is {header}")
    by_group: dict[str, list[tuple[tuple[int, int], dict]]] = {}
    for raw in reader:
        row = {k.strip().lstrip("﻿"): v for k, v in raw.items() if k is not None}
        ym = parse_month(row["date"])
        if int(_num(row["month"], f"{name}/month")) != month_index(*ym):
            raise ValueError(f"{name}: month index {row['month']} does not match date {row['date']}")
        label = row[group_col].strip() if group_col else ""
        if group_col and label not in spec["groups"]:
            raise ValueError(f"{name}: unknown {group_col} {label!r}; known: {sorted(spec['groups'])}")
        by_group.setdefault(label, []).append((ym, row))
    if group_col:
        absent = [g for g in spec["groups"] if g not in by_group]
        if absent:
            raise ValueError(f"{name}: no rows for {absent}")
    elif "" not in by_group:
        raise ValueError(f"{name}: no rows")
    out: dict[str, pd.DataFrame] = {}
    for label, rows in by_group.items():
        rows.sort(key=lambda r: r[0])
        for (a, _), (b, _) in zip(rows, rows[1:]):
            if next_month(*a) != b:
                raise ValueError(f"{name}/{label or 'total'}: months jump from {a} to {b}")
        out[label] = pd.DataFrame(
            {
                "period_end": [pd.Timestamp(last_day(*ym)) for ym, _ in rows],
                **{col: [_num(row[col], f"{name}/{label}/{col}/{ym}") for ym, row in rows] for col in spec["columns"]},
            }
        ).set_index("period_end")
    return out


def _scales(meta: pd.DataFrame) -> dict[tuple[str, str, str], float]:
    """(metric, entity, tier) -> scale from series.csv; every listed row is monthly."""
    out = {}
    for _, row in meta.iterrows():
        if row["period_type"] != PERIOD_TYPE:
            raise ValueError(f"series.csv: {row['metric']}/{row['entity']}/{row['tier']} must be monthly")
        out[(row["metric"], row["entity"], row["tier"])] = float(row["scale"]) if row["scale"] not in ("", None) else 1.0
    return out


def parse_files(texts: dict[str, str], meta: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """All six files -> facts, scaled per the series.csv rows (source cfpb_cct). Group files must sum to their total
    file within GROUP_SUM_TOLERANCE every month they share, every month of a group file must exist in the total
    file, and the files' series must be exactly the crosswalk's."""
    absent = [n for n in FILES if n not in texts]
    if absent:
        raise ValueError(f"missing files {absent}")
    scales = _scales(meta)
    parsed = {name: read_file(texts[name], name) for name in FILES}
    got = set()
    for name, spec in FILES.items():
        for label in parsed[name]:
            entity, _, tier = spec["groups"][label] if label else (ENTITY_ALL, "aggregate", "all")
            got.update((metric, entity, tier) for metric in spec["columns"].values())
    if got != set(scales):
        raise ValueError(
            f"series in the files but not in series.csv: {sorted(got - set(scales))[:5]}; "
            f"listed but not in the files: {sorted(set(scales) - got)[:5]}"
        )
    frames = []
    for name, spec in FILES.items():
        groups = parsed[name]
        if "total_of" in spec:
            total = parsed[spec["total_of"]][""]
            summed = sum(groups.values())
            if not summed.index.isin(total.index).all():
                raise ValueError(f"{name}: has months the total file {spec['total_of']} lacks")
            aligned = total.loc[summed.index]
            for col in spec["columns"]:
                rel = ((summed[col] - aligned[col]).abs() / aligned[col]).max()
                if rel > GROUP_SUM_TOLERANCE:
                    raise ValueError(
                        f"{name}/{col}: groups sum to {rel:.1%} away from {spec['total_of']}, above {GROUP_SUM_TOLERANCE:.0%}"
                    )
        for label, frame in groups.items():
            entity, entity_type, tier = spec["groups"][label] if label else (ENTITY_ALL, "aggregate", "all")
            for col, metric in spec["columns"].items():
                frames.append(
                    pd.DataFrame(
                        {
                            "metric": metric,
                            "entity": entity,
                            "entity_type": entity_type,
                            "tier": tier,
                            "period_end": frame.index,
                            "period_type": PERIOD_TYPE,
                            "value": frame[col].to_numpy() * scales[(metric, entity, tier)],
                            "source": SOURCE,
                            "pulled_at": pulled_at,
                        }
                    )[FACT_COLUMNS]
                )
    return pd.concat(frames, ignore_index=True)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    latest = raw_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    texts = {}
    for name in FILES:
        resp = session.get(BASE_URL + name)
        resp.raise_for_status()
        text = resp.text
        if not text.lstrip().lower().startswith("month,date"):
            raise ValueError(f"{name}: not the expected CSV (first bytes {text[:60]!r})")
        (latest / name).write_text(text, encoding="utf-8", newline="\n")
        texts[name] = text
    return parse_files(texts, meta, pulled_at)
