"""Census Monthly Retail Trade Survey: the keyless 'mrtssales92-present.xlsx' workbook, sales by kind of business.

Publication facts, verified 2026-09-08:
- https://www.census.gov/retail/mrts/www/mrtssales92-present.xlsx, about 440 KB, downloads directly with this
  pipeline's User-Agent. The API needs a key even at low volume; this file does not.
- One sheet per year, named '1992' to the current year, newest first. Each sheet: a title, a units line
  ('Estimates are shown in millions of dollars'), a header row with 'NAICS Code' and 'Kind of Business', then a
  row of month labels ('Jan. 2026', 'May 2026', 'Jun. 2026(p)', then 'CY CUM'/'PY CUM' or 'TOTAL' columns that are
  ignored), a 'NOT ADJUSTED' block of rows, then an 'ADJUSTED(2)' block with the same rows seasonally adjusted.
  Rows are keyed by the NAICS code in column A, except the totals, which have no code and are matched by label.
- Values are millions of dollars of monthly sales (not annual rates): the fetcher scales to billions. '(S)' marks a
  suppressed cell (drinking places, full-service restaurants); a kept row with one fails the parse.
- The newest month is preliminary '(p)' and is revised the next month. The advance estimate for the month after
  the newest one is in the MARTS release, not in this file, so the workbook trails the headlines by one month.
  Published mid-month, about six weeks after the month it adds (June 2026 appeared with the August 14 release).
- Goldens come from the advance release PDF's Table 1 (checks/golden.yaml), whose not-adjusted columns print the
  prior months' levels as revised.

Outputs: raw_dir/latest/mrtssales92-present.xlsx (byte for byte) and one facts row per month per series. Nothing is
computed here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from ..schema import FACT_COLUMNS, last_day

SOURCE = "census"
LANDING_URL = "https://www.census.gov/retail/sales.html"
FILE_URL = "https://www.census.gov/retail/mrts/www/mrtssales92-present.xlsx"
RAW_NAME = "mrtssales92-present.xlsx"
XLSX_MAGIC = b"PK\x03\x04"
PERIOD_TYPE = "M"
FIRST_MONTH = (1992, 1)
MILLIONS_TO_BILLIONS = 0.001

MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
_MONTH_RE = re.compile(r"^([A-Z][a-z]{2})\.?\s+(\d{4})\s*(\(p\))?$")
_SHEET_RE = re.compile(r"^(19|20)\d\d$")

# (row key, entity): a NAICS code as it appears in column A, or a label for the code-less total rows
ROWS = {
    "Retail and food services sales, total": "NAICS:44X72",
    "445": "NAICS:445",  # food and beverage stores
    "447": "NAICS:447",  # gasoline stations
    "448": "NAICS:448",  # clothing and clothing accessories stores
    "454": "NAICS:454",  # nonstore retailers
    "722": "NAICS:722",  # food services and drinking places
}
BLOCKS = {"NOT ADJUSTED": "retail_sales_nsa", "ADJUSTED(2)": "retail_sales_sa"}


def looks_like_xlsx(content: bytes) -> bool:
    return content[:4] == XLSX_MAGIC


def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell).strip()) if cell is not None else ""


def parse_month_label(text: str) -> tuple[int, int] | None:
    """'Jun. 2026(p)' -> (2026, 6); 'CY CUM' -> None."""
    m = _MONTH_RE.match(_norm(text))
    if not m or m.group(1) not in MONTHS:
        return None
    return int(m.group(2)), MONTHS[m.group(1)]


def _row_key(row: tuple) -> str | None:
    code = _norm(row[0]) if row else ""
    if code:
        code = code.replace(".0", "") if re.fullmatch(r"\d+\.0", code) else code
        return code
    label = _norm(row[1]) if len(row) > 1 else ""
    return label or None


def parse_sheet(rows: list[tuple], name: str) -> list[dict]:
    """One year sheet -> records [metric, entity, (year, month), value in millions] for the rows in ROWS,
    from both blocks. Every kept row must appear exactly once per block."""
    header_i = next((i for i, r in enumerate(rows[:12]) if any(parse_month_label(c) for c in r if c is not None)), None)
    if header_i is None:
        raise ValueError(f"{name}: no row of month labels in the first 12 rows")
    months = {j: parse_month_label(c) for j, c in enumerate(rows[header_i]) if c is not None and parse_month_label(c)}
    if not months:
        raise ValueError(f"{name}: no month columns")
    block = None
    seen: dict[tuple[str, str], int] = {}
    out = []
    for i, row in enumerate(rows[header_i + 1:], start=header_i + 2):
        label = _norm(row[1]) if len(row) > 1 else ""
        if label in BLOCKS and not _norm(row[0]):
            block = BLOCKS[label]
            continue
        key = _row_key(row)
        if key not in ROWS or block is None:
            continue
        entity = ROWS[key]
        seen[(block, entity)] = seen.get((block, entity), 0) + 1
        for j, ym in months.items():
            cell = row[j] if j < len(row) else None
            if isinstance(cell, bool) or not isinstance(cell, (int, float)):
                raise ValueError(f"{name} row {i} ({key!r}, {block}): {ym[0]}-{ym[1]:02d} is {cell!r}, not a number")
            out.append({"metric": block, "entity": entity, "ym": ym, "value": float(cell)})
    expected = {(b, e) for b in BLOCKS.values() for e in ROWS.values()}
    bad = {k: n for k, n in seen.items() if n != 1}
    missing = expected - set(seen)
    if bad or missing:
        raise ValueError(f"{name}: rows seen more than once {bad}, missing {sorted(missing)}")
    return out


def read_year_sheets(path: Path) -> dict[int, list[tuple]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        out = {}
        for sheet in wb.sheetnames:
            if _SHEET_RE.match(sheet):
                out[int(sheet)] = [tuple(r) for r in wb[sheet].iter_rows(values_only=True)]
        if not out:
            raise ValueError(f"{path.name}: no year sheets; found {wb.sheetnames}")
        return out
    finally:
        wb.close()


def parse_release(path: Path, pulled_at: str) -> pd.DataFrame:
    sheets = read_year_sheets(path)
    records = []
    for year in sorted(sheets):
        records.extend(parse_sheet(sheets[year], f"{path.name} sheet {year}"))
    df = pd.DataFrame.from_records(records)
    df["period_end"] = [pd.Timestamp(last_day(y, m)) for y, m in df["ym"]]
    dupes = df[df.duplicated(["metric", "entity", "period_end"], keep=False)]
    if not dupes.empty:
        raise ValueError(f"{path.name}: {len(dupes)} duplicate (metric, entity, month) rows")
    for (metric, entity), grp in df.groupby(["metric", "entity"]):
        d = sorted(grp["period_end"])
        if d[0] != pd.Timestamp(last_day(*FIRST_MONTH)):
            raise ValueError(f"{metric} {entity}: history starts {d[0].date()}, expected {last_day(*FIRST_MONTH)}")
        for a, b in zip(d, d[1:]):
            if (b.year * 12 + b.month) - (a.year * 12 + a.month) != 1:
                raise ValueError(f"{metric} {entity}: months jump from {a.date()} to {b.date()}")
    out = pd.DataFrame(
        {
            "metric": df["metric"],
            "entity": df["entity"],
            "entity_type": "naics",
            "tier": "all",
            "period_end": df["period_end"],
            "period_type": PERIOD_TYPE,
            "value": df["value"].astype("float64") * MILLIONS_TO_BILLIONS,
            "source": SOURCE,
            "pulled_at": pulled_at,
        }
    )
    return out[FACT_COLUMNS].sort_values(["metric", "entity", "period_end"]).reset_index(drop=True)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    """Series come out of ROWS and BLOCKS, not out of meta. The loader rejects any that series.csv does not list."""
    resp = session.get(FILE_URL)
    resp.raise_for_status()
    if not looks_like_xlsx(resp.content):
        raise ValueError(f"{FILE_URL}: not an xlsx (starts with {resp.content[:40]!r})")
    latest = raw_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    dest = latest / RAW_NAME
    dest.write_bytes(resp.content)
    return parse_release(dest, pulled_at)
