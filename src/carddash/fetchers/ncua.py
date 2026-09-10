"""NCUA 5300 Call Report quarterly data: credit union card lending, per credit union and for the whole industry.

Publication facts, verified 2026-09-08:
- One zip per quarter at https://ncua.gov/files/publications/analysis/call-report-data-YYYY-MM.zip (MM = 03, 06, 09,
  12), about 8 MB, direct link, no key. Inside: comma-delimited text files per call-report schedule (FS220.txt,
  FS220A.txt, ...), FOICU.txt with the credit union names, and AcctDesc.txt mapping account codes to schedules.
  File names vary in case between vintages (fs220A.txt in 2016, FS220A.txt in 2026); column names too
  (ACCT_396 vs Acct_680). Both are matched case-insensitively. The card fields sit in the same schedules from the
  2016 Q1 file to the 2026 Q1 file (checked on four vintages).
- Card fields (AcctDesc.txt): ACCT_396 unsecured credit card loans outstanding (FS220A), ACCT_680 and ACCT_681
  credit card charge-offs and recoveries YEAR-TO-DATE (FS220B), ACCT_045B total delinquent credit card loans, which
  is the 2-months-and-over buckets (FS220B), ACCT_521 the interest rate on credit card loans in basis points (FS220).
  Dollar amounts are in dollars, not thousands. ACCT_812 (unused card lines) went to zero in the modernised form
  and is not loaded.
- A quarter's file lands about two months after quarter end. The fetcher walks back from the current calendar
  quarter until a zip answers (MAX_WALK_BACK quarters), like the Philly Fed and NY Fed fetchers.
- Tracked credit unions: the four that are top-25 card issuers in the CFPB survey and outside the FDIC data
  (crosswalks/issuers.csv cannot match them): Navy Federal (5536), PenFed (227), BECU (62604), America First
  (24694). The industry total is the sum over federally insured credit unions (FOICU.txt CU_TYPE 1, federal, and
  2, federally insured state-chartered; type 3 is privately insured and excluded), which is the population the
  NCUA's Quarterly Credit Union Data Summary reports: its $86.0 billion of card balances and 2.04 percent card
  delinquency rate for 2026 Q1 reproduce from the file only with that filter.

Raw-snapshot deviation, documented in CLAUDE.md: the full history is 40-plus zips of 8 MB each, so a daily full pull
is not reasonable. Each quarter's extract (the tracked rows plus the industry sums, a few hundred bytes) is written
under raw_dir/quarters/YYYY-MM.csv and committed. A run downloads only quarters with no extract yet plus the newest
REFRESH_QUARTERS quarters (amended filings), and builds the facts from every extract. The result is the same full
history every run and the zips are never committed.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "ncua"
LANDING_URL = "https://ncua.gov/analysis/credit-union-corporate-call-report-data/quarterly-data"
ZIP_URL = "https://ncua.gov/files/publications/analysis/call-report-data-{year}-{month:02d}.zip"
ZIP_MAGIC = b"PK\x03\x04"
FIRST_QUARTER = (2016, 1)
MAX_WALK_BACK = 6
REFRESH_QUARTERS = 2  # the newest extracts are re-pulled every run, older ones are trusted
ENTITY_TYPE = "credit_union"
INDUSTRY = "NCUA_FICU"  # federally insured credit unions
INSURED_TYPES = {"1", "2"}

CREDIT_UNIONS = {
    "5536": "NAVY_FEDERAL",
    "227": "PENFED",
    "62604": "BECU",
    "24694": "AMERICA_FIRST",
}
# (schedule file, account column, metric, summed for the industry?)
FIELDS = [
    ("FS220A", "ACCT_396", "ncua_card_loans", True),
    ("FS220B", "ACCT_680", "ncua_card_charge_offs_ytd", True),
    ("FS220B", "ACCT_681", "ncua_card_recoveries_ytd", True),
    ("FS220B", "ACCT_045B", "ncua_card_delinquent_60plus", True),
    ("FS220", "ACCT_521", "ncua_card_rate", False),
]
EXTRACT_COLUMNS = ["cu_number", "entity", "cycle_date", "metric", "value"]

_QUARTER_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}


def quarter_of(date: dt.date) -> tuple[int, int]:
    return date.year, (date.month - 1) // 3 + 1


def quarter_end(year: int, q: int) -> dt.date:
    return last_day(year, _QUARTER_MONTH[q])


def previous_quarter(year: int, q: int) -> tuple[int, int]:
    return (year - 1, 4) if q == 1 else (year, q - 1)


def next_quarter(year: int, q: int) -> tuple[int, int]:
    return (year + 1, 1) if q == 4 else (year, q + 1)


def zip_url(year: int, q: int) -> str:
    return ZIP_URL.format(year=year, month=_QUARTER_MONTH[q])


def extract_name(year: int, q: int) -> str:
    return f"{year}-{_QUARTER_MONTH[q]:02d}.csv"


def quarters_between(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    out = [first]
    while out[-1] != last:
        out.append(next_quarter(*out[-1]))
    return out


# ---------- one zip -> one extract ----------


def _read_schedule(z: zipfile.ZipFile, name: str) -> tuple[dict[str, str], list[dict]]:
    """(upper-cased column -> real column, rows) for a schedule file, matched case-insensitively."""
    names = {n.lower(): n for n in z.namelist()}
    real = names.get(f"{name}.txt".lower())
    if real is None:
        raise ValueError(f"zip has no {name}.txt; files: {sorted(names.values())[:12]}")
    text = z.read(real).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"{real}: no header")
    cols = {c.strip().upper(): c for c in reader.fieldnames}
    for required in ("CU_NUMBER", "CYCLE_DATE"):
        if required not in cols:
            raise ValueError(f"{real}: no {required} column")
    return cols, list(reader)


def _num(text, where: str) -> float:
    t = (text or "").strip()
    if t == "":
        return 0.0  # a blank cell in a call report is a zero, the schedules are not sparse
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"{where}: not a number: {text!r}") from None


def _cycle(text: str) -> dt.date:
    """'3/31/2026 0:00:00' -> 2026-03-31."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    if not m:
        raise ValueError(f"unexpected CYCLE_DATE {text!r}")
    return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))


def _insured(z: zipfile.ZipFile) -> set[str]:
    """Credit union numbers of the federally insured credit unions in the file (FOICU.txt CU_TYPE 1 or 2)."""
    cols, rows = _read_schedule(z, "FOICU")
    if "CU_TYPE" not in cols:
        raise ValueError("FOICU.txt: no CU_TYPE column")
    out = {r[cols["CU_NUMBER"]].strip() for r in rows if r[cols["CU_TYPE"]].strip() in INSURED_TYPES}
    if not out:
        raise ValueError("FOICU.txt: no federally insured credit unions")
    return out


def extract(content: bytes, year: int, q: int) -> pd.DataFrame:
    """One quarter's zip -> the extract frame (EXTRACT_COLUMNS): tracked credit unions plus the industry sums over
    federally insured credit unions. Every tracked credit union must be present, and every row's cycle date must be
    the quarter's end."""
    if content[:4] != ZIP_MAGIC:
        raise ValueError(f"{year} Q{q}: not a zip (starts with {content[:40]!r})")
    z = zipfile.ZipFile(io.BytesIO(content))
    end = quarter_end(year, q)
    insured = _insured(z)
    records = []
    for schedule, column, metric, summed in FIELDS:
        cols, rows = _read_schedule(z, schedule)
        if column not in cols:
            raise ValueError(f"{year} Q{q} {schedule}: no column {column}")
        col, cu_col, cyc_col = cols[column], cols["CU_NUMBER"], cols["CYCLE_DATE"]
        seen = set()
        total = 0.0
        for row in rows:
            cu = row[cu_col].strip()
            if _cycle(row[cyc_col]) != end:
                raise ValueError(f"{year} Q{q} {schedule}: credit union {cu} is dated {row[cyc_col]!r}, not {end}")
            value = _num(row[col], f"{year} Q{q} {schedule} {cu} {column}")
            if cu in insured:
                total += value
            if cu in CREDIT_UNIONS:
                if cu in seen:
                    raise ValueError(f"{year} Q{q} {schedule}: credit union {cu} appears twice")
                seen.add(cu)
                records.append({"cu_number": cu, "entity": f"NCUA:{CREDIT_UNIONS[cu]}", "cycle_date": end.isoformat(),
                                "metric": metric, "value": value})
        missing = set(CREDIT_UNIONS) - seen
        if missing:
            raise ValueError(f"{year} Q{q} {schedule}: tracked credit unions missing: {sorted(missing)}")
        if summed:
            records.append({"cu_number": "", "entity": INDUSTRY, "cycle_date": end.isoformat(), "metric": metric, "value": total})
    return pd.DataFrame.from_records(records, columns=EXTRACT_COLUMNS)


def read_extract(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"cu_number": str, "entity": str, "cycle_date": str, "metric": str}, keep_default_na=False)
    missing = [c for c in EXTRACT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    df["value"] = pd.to_numeric(df["value"], errors="raise")
    return df


def write_extract(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df[EXTRACT_COLUMNS].to_csv(path, index=False, lineterminator="\n")


# ---------- extracts -> facts ----------


def to_facts(extracts: list[pd.DataFrame], meta: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """Every quarter's extract -> facts rows for the series in meta (source ncua), scaled per series.csv. Quarters
    must be consecutive per series."""
    df = pd.concat(extracts, ignore_index=True)
    scale = {(r["metric"], r["entity"]): (float(r["scale"]) if r["scale"] not in ("", None) else 1.0) for _, r in meta.iterrows()}
    frames = []
    for (metric, entity), grp in df.groupby(["metric", "entity"], sort=True):
        if (metric, entity) not in scale:
            continue  # the industry rate is never summed and has no series row; the loader rejects anything else
        grp = grp.sort_values("cycle_date")
        dates = [dt.date.fromisoformat(d) for d in grp["cycle_date"]]
        if len(set(dates)) != len(dates):
            raise ValueError(f"{metric} {entity}: a quarter appears twice")
        for a, b in zip(dates, dates[1:]):
            if quarter_of(b) != next_quarter(*quarter_of(a)):
                raise ValueError(f"{metric} {entity}: quarters jump from {a} to {b}")
        frames.append(
            pd.DataFrame(
                {
                    "metric": metric,
                    "entity": entity,
                    "entity_type": ENTITY_TYPE if entity != INDUSTRY else "aggregate",
                    "tier": "all",
                    "period_end": [pd.Timestamp(d) for d in dates],
                    "period_type": "Q",
                    "value": grp["value"].astype("float64").to_numpy() * scale[(metric, entity)],
                    "source": SOURCE,
                    "pulled_at": pulled_at,
                }
            )[FACT_COLUMNS]
        )
    if not frames:
        raise ValueError("no extract rows match series.csv")
    return pd.concat(frames, ignore_index=True)


# ---------- discovery and fetch ----------


def _try_zip(session, year: int, q: int) -> bytes | None:
    resp = session.get(zip_url(year, q))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    if resp.content[:4] != ZIP_MAGIC:
        return None  # an HTML "not found" page served with 200
    return resp.content


def discover_latest(session, today: dt.date) -> tuple[int, int, bytes]:
    """Walk back from the current quarter until a zip answers."""
    year, q = quarter_of(today)
    tried = []
    for _ in range(MAX_WALK_BACK):
        content = _try_zip(session, year, q)
        if content is not None:
            return year, q, content
        tried.append(f"{year}Q{q}")
        year, q = previous_quarter(year, q)
    raise ValueError(f"no NCUA call report zip found for {', '.join(tried)}")


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    today = dt.datetime.strptime(pulled_at[:10], "%Y-%m-%d").date()
    year, q, content = discover_latest(session, today)
    quarters = quarters_between(FIRST_QUARTER, (year, q))
    refresh = set(quarters[-REFRESH_QUARTERS:])
    qdir = raw_dir / "quarters"
    extracts = []
    cache = {(year, q): content}
    for yq in quarters:
        path = qdir / extract_name(*yq)
        if path.exists() and yq not in refresh:
            extracts.append(read_extract(path))
            continue
        blob = cache.get(yq)
        if blob is None:
            blob = _try_zip(session, *yq)
            if blob is None:
                raise ValueError(f"{yq[0]} Q{yq[1]}: zip missing although a later quarter exists")
        df = extract(blob, *yq)
        write_extract(df, path)
        extracts.append(df)
    return to_facts(extracts, meta[meta["source"] == SOURCE], pulled_at)
