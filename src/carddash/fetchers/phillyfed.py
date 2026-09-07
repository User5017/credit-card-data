"""Philadelphia Fed Large Bank Credit Card Data (FR Y-14M) fetcher: two quarterly CSVs, one row per quarter.

Publication facts, verified 2026-09-07:
- Two files per release on the landing page, both carrying the FULL history from 2012Q3 in one row per quarter:
    /-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q1/26Q1-CreditCardBalances.csv
    /-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q1/26Q1-CreditCardOriginations.csv
  The URL embeds the release quarter, so the fetcher starts at the calendar quarter of the run date and walks back
  until the Balances file answers. Only the newest release is needed, because it contains every earlier quarter.
- A missing file comes back as HTTP 200 with an HTML 404 page (text/html), never as a 404 status. Discovery must look
  at the body, not the status code. Older Balances files stay online; older Originations files do not.
- Cells are formatted text: '$948.67', '$2,344.74', '9.25 %', '631', and the literal 'null' for a value the Fed did
  not publish (line-decrease share in 2012Q3). The latest file starts with a UTF-8 BOM, older ones do not.
- Rows: a header ('YRQTR' first), one row per quarter ('2026Q1'), then footer rows: a blank line, 'Source: ...',
  'Website: ...', 'Email: ...'. Anything else after the data is a format change and fails the parse.
- Score buckets are <660, 660-719 and >=720 (purchase volume per account, median original credit limit, the share of
  new accounts and commitments below 660). Delinquency, utilization and balances are published for the whole panel
  only. The bucket-to-tier mapping is documented in crosswalks/tiers.csv (lt660, 660_719, superprime).
- Panel: FR Y-14M credit card filers (bank holding companies with card balances above $5 billion or material to
  Tier 1 capital). The panel changes when a bank crosses the threshold, so the whole history can be restated; the
  loader's restatement detector covers that.
- Suggested citation on the page: Federal Reserve Bank of Philadelphia, Large Bank Credit Card and Mortgage Data.

Outputs: raw_dir/latest/CreditCardBalances.csv and CreditCardOriginations.csv (the release files, byte for byte,
under canonical names so a new quarter shows as a content diff) and one facts row per quarter per series. The
only derived series is the aggregate utilization rate (balances / commitments), which is how the Fed's own
Insights report defines it; everything else is a transcription of one column.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "phillyfed"
ENTITY = "Y14_CARD_FILERS"
LANDING_URL = "https://www.philadelphiafed.org/surveys-and-data/large-bank-credit-card-and-mortgage-data"
FILE_URL = "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/Y14/{year}/Q{q}/{yy}Q{q}-{file}.csv"
FILES = ("CreditCardBalances", "CreditCardOriginations")
FIRST_QUARTER = (2012, 3)
MAX_WALK_BACK = 8  # quarters; the Fed publishes about 3.5 months after quarter end

_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")
_FOOTER_PREFIXES = ("source:", "website:", "email:")
_QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}

# (file, header text as published, metric, tier). Header match is exact after whitespace normalization and
# case-folding. Every header listed here must exist; extra columns in the file are ignored.
COLUMN_SPEC = [
    # --- CreditCardBalances: the whole active-account portfolio, cycle end ---
    ("CreditCardBalances", "Total Balances ($Billions)", "y14_card_balances", "all"),
    ("CreditCardBalances", "Number of Accounts (Millions)", "y14_card_accounts", "all"),
    ("CreditCardBalances", "Total Commitments ($Billions)", "y14_card_commitments", "all"),
    ("CreditCardBalances", "Account Balance (Active Accounts Only) (50th percentile)", "y14_card_balance_active_p50", "all"),
    ("CreditCardBalances", "Account Balance (Active Accounts Only) (75th percentile)", "y14_card_balance_active_p75", "all"),
    ("CreditCardBalances", "Account Balance (Active Accounts Only) (90th percentile)", "y14_card_balance_active_p90", "all"),
    ("CreditCardBalances", "Current Credit Limit (50th Percentile)", "y14_card_credit_limit_p50", "all"),
    ("CreditCardBalances", "Current Credit Limit (75th Percentile)", "y14_card_credit_limit_p75", "all"),
    ("CreditCardBalances", "Current Credit Limit (90th Percentile)", "y14_card_credit_limit_p90", "all"),
    ("CreditCardBalances", "Current Credit Score (10th percentile)", "y14_card_credit_score_p10", "all"),
    ("CreditCardBalances", "Current Credit Score (25th percentile)", "y14_card_credit_score_p25", "all"),
    ("CreditCardBalances", "Current Credit Score (50th percentile)", "y14_card_credit_score_p50", "all"),
    ("CreditCardBalances", "Utilization (Active Accounts Only) (50th percentile)", "y14_card_utilization_active_p50", "all"),
    ("CreditCardBalances", "Utilization (Active Accounts Only) (75th percentile)", "y14_card_utilization_active_p75", "all"),
    ("CreditCardBalances", "Utilization (Active Accounts Only) (90th percentile)", "y14_card_utilization_active_p90", "all"),
    ("CreditCardBalances", "Percentage of Accounts with Credit Line Decrease", "y14_card_line_decrease_share", "all"),
    ("CreditCardBalances", "Percentage of Accounts with Credit Line Increase", "y14_card_line_increase_share", "all"),
    ("CreditCardBalances", "30+ Days Past Due Rates: Accounts Based", "y14_card_dq30_rate_accounts", "all"),
    ("CreditCardBalances", "30+ Days Past Due Rates: Balances Based", "y14_card_dq30_rate_balances", "all"),
    ("CreditCardBalances", "60+ Days Past Due Rates: Accounts Based", "y14_card_dq60_rate_accounts", "all"),
    ("CreditCardBalances", "60+ Days Past Due Rates: Balances Based", "y14_card_dq60_rate_balances", "all"),
    ("CreditCardBalances", "90+ Days Past Due Rates: Accounts Based", "y14_card_dq90_rate_accounts", "all"),
    ("CreditCardBalances", "90+ Days Past Due Rates: Balances Based", "y14_card_dq90_rate_balances", "all"),
    ("CreditCardBalances", "Share of Accounts Making the Minimum Payment", "y14_card_pay_minimum_share", "all"),
    ("CreditCardBalances", "Share of Accounts Making Greater Than the Minimum Payment but Less Than the Full Balance", "y14_card_pay_partial_share", "all"),
    ("CreditCardBalances", "Share of Accounts Making the Full Balance Payment", "y14_card_pay_full_share", "all"),
    ("CreditCardBalances", "Revolving Balances Only ($Billions)", "y14_card_revolving_balances", "all"),
    ("CreditCardBalances", "Net Charge-Off Rate", "y14_card_nco_rate", "all"),
    ("CreditCardBalances", "Average Purchase APR: General Purpose", "y14_card_purchase_apr_general_purpose", "all"),
    ("CreditCardBalances", "Average Purchase APR: Private Label", "y14_card_purchase_apr_private_label", "all"),
    ("CreditCardBalances", "Total Purchase Volume ($Billions)", "y14_card_purchase_volume", "all"),
    ("CreditCardBalances", "Average Purchase Volume by Credit Score Group: <660 Credit Score", "y14_card_purchase_volume_avg", "lt660"),
    ("CreditCardBalances", "Average Purchase Volume by Credit Score Group: 660-719 Credit Score", "y14_card_purchase_volume_avg", "660_719"),
    ("CreditCardBalances", "Average Purchase Volume by Credit Score Group: >=720 Credit Score", "y14_card_purchase_volume_avg", "superprime"),
    ("CreditCardBalances", "Percentage of Accounts Subject to Promotional APR: Percentage APR Accounts", "y14_card_promo_apr_share", "all"),
    # --- CreditCardOriginations: new commitments in the quarter ---
    ("CreditCardOriginations", "New Originations ($Billions)", "y14_card_originations", "all"),
    ("CreditCardOriginations", "Number of New Accounts (Millions)", "y14_card_new_accounts", "all"),
    ("CreditCardOriginations", "Original Credit Limit (50th percentile)", "y14_card_orig_credit_limit_p50", "all"),
    ("CreditCardOriginations", "Original Credit Limit (75th percentile)", "y14_card_orig_credit_limit_p75", "all"),
    ("CreditCardOriginations", "Original Credit Limit (90th percentile)", "y14_card_orig_credit_limit_p90", "all"),
    ("CreditCardOriginations", "Original Credit Score (10th percentile)", "y14_card_orig_credit_score_p10", "all"),
    ("CreditCardOriginations", "Original Credit Score (25th percentile)", "y14_card_orig_credit_score_p25", "all"),
    ("CreditCardOriginations", "Original Credit Score (50th percentile)", "y14_card_orig_credit_score_p50", "all"),
    ("CreditCardOriginations", "Average Original Purchase APR: General Purpose", "y14_card_orig_purchase_apr_general_purpose", "all"),
    ("CreditCardOriginations", "Average Original Purchase APR: Private Label", "y14_card_orig_purchase_apr_private_label", "all"),
    ("CreditCardOriginations", "Median Original Credit Limit by Credit Score Group: <660 Credit Score", "y14_card_orig_credit_limit_median", "lt660"),
    ("CreditCardOriginations", "Median Original Credit Limit by Credit Score Group: 660-719 Credit Score", "y14_card_orig_credit_limit_median", "660_719"),
    ("CreditCardOriginations", "Median Original Credit Limit by Credit Score Group: >=720 Credit Score", "y14_card_orig_credit_limit_median", "superprime"),
    ("CreditCardOriginations", "Percentage of New Accounts with <660 Credit Score", "y14_card_new_accounts_share", "lt660"),
    ("CreditCardOriginations", "Percentage of New Commitments with <660 Credit Score", "y14_card_new_commitments_share", "lt660"),
]
# The one computed series: aggregate utilization = total balances / total commitments, in percent. The Fed's
# Insights report quotes this number ("the overall rate of credit utilization ... 19.1 percent" for 2026Q1).
UTILIZATION_METRIC = "y14_card_utilization_rate"
_UTIL_NUM, _UTIL_DEN = "y14_card_balances", "y14_card_commitments"


# ---------- quarters ----------


def parse_quarter(label: str) -> tuple[int, int]:
    """'2026Q1' -> (2026, 1)."""
    m = _QUARTER_RE.match(label.strip())
    if not m:
        raise ValueError(f"not a quarter label: {label!r}")
    return int(m.group(1)), int(m.group(2))


def quarter_end(year: int, q: int) -> dt.date:
    return last_day(year, _QUARTER_END_MONTH[q])


def quarter_of(date: dt.date) -> tuple[int, int]:
    return date.year, (date.month - 1) // 3 + 1


def previous_quarter(year: int, q: int) -> tuple[int, int]:
    return (year - 1, 4) if q == 1 else (year, q - 1)


def file_url(year: int, q: int, file: str) -> str:
    return FILE_URL.format(year=year, q=q, yy=f"{year % 100:02d}", file=file)


# ---------- discovery ----------


def looks_like_release_csv(content: bytes) -> bool:
    """The site answers 200 + an HTML error page for a missing file, so the body decides, not the status."""
    head = content[:16].lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith((b'"YRQTR"', b"YRQTR"))


def discover_latest(session, today: dt.date, max_back: int = MAX_WALK_BACK) -> tuple[int, int, bytes]:
    """Newest quarter whose Balances file exists: try the current calendar quarter, walk back.

    Returns (year, quarter, balances_bytes) so the file is downloaded once.
    """
    year, q = quarter_of(today)
    tried = []
    for _ in range(max_back + 1):
        url = file_url(year, q, FILES[0])
        resp = session.get(url)
        resp.raise_for_status()
        if looks_like_release_csv(resp.content):
            return year, q, resp.content
        tried.append(f"{year}Q{q}")
        year, q = previous_quarter(year, q)
    raise ValueError(f"no {FILES[0]} file found for any of {tried} (URL pattern changed?)")


def download_release(session, year: int, q: int, latest_dir: Path, balances: bytes | None = None) -> dict[str, Path]:
    """Both files of one release into latest_dir under canonical names. Returns file -> path."""
    latest_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for file in FILES:
        if file == FILES[0] and balances is not None:
            content = balances
        else:
            resp = session.get(file_url(year, q, file))
            resp.raise_for_status()
            content = resp.content
        if not looks_like_release_csv(content):
            raise ValueError(f"{file} for {year}Q{q}: not a release CSV (starts with {content[:60]!r})")
        dest = latest_dir / f"{file}.csv"
        dest.write_bytes(content)
        out[file] = dest
    return out


# ---------- parsing ----------


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).lower()


def parse_cell(raw: str, header: str, label: str) -> float | None:
    """'$2,344.74' -> 2344.74, '9.25 %' -> 9.25, '631' -> 631.0, 'null' or '' -> None. Anything else fails."""
    s = raw.strip()
    if s == "" or s.lower() == "null":
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{label} {header!r}: cannot read {raw!r} as a number") from None


def parse_release_csv(content: bytes, name: str) -> pd.DataFrame:
    """One release CSV -> wide DataFrame indexed by quarter label, columns = header text as published.

    Values are floats (None where the Fed printed 'null'). Footer rows are checked, not skipped blindly.
    """
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or not rows[0] or _norm(rows[0][0]) != "yrqtr":
        raise ValueError(f"{name}: first header cell should be 'YRQTR', got {rows[0][:1] if rows else rows!r}")
    header = [h.strip() for h in rows[0]]
    if len(set(map(_norm, header))) != len(header):
        raise ValueError(f"{name}: duplicate header texts")
    records: dict[str, dict[str, float | None]] = {}
    in_footer = False
    for i, row in enumerate(rows[1:], start=2):
        first = row[0].strip() if row else ""
        rest_blank = all(not c.strip() for c in row[1:])
        if _QUARTER_RE.match(first) and not in_footer:
            if len(row) != len(header):
                raise ValueError(f"{name} line {i}: {len(row)} cells, header has {len(header)}")
            if first in records:
                raise ValueError(f"{name} line {i}: quarter {first} appears twice")
            records[first] = {h: parse_cell(c, h, f"{name} line {i}") for h, c in zip(header[1:], row[1:])}
            continue
        in_footer = True
        if (first == "" and rest_blank) or (_norm(first).startswith(_FOOTER_PREFIXES) and rest_blank):
            continue
        raise ValueError(f"{name} line {i}: unexpected row after the data: {row[:3]!r}")
    if not records:
        raise ValueError(f"{name}: no quarter rows")
    df = pd.DataFrame.from_dict(records, orient="index")
    df.index.name = "yrqtr"
    return df


def _wide_to_facts(wide: pd.DataFrame, file: str, pulled_at: str) -> list[pd.DataFrame]:
    spec = [(h, metric, tier) for f, h, metric, tier in COLUMN_SPEC if f == file]
    by_norm = {_norm(c): c for c in wide.columns}
    missing = [h for h, _, _ in spec if _norm(h) not in by_norm]
    if missing:
        raise ValueError(f"{file}: headers missing: {missing}. Found: {list(wide.columns)}")
    period_ends = [pd.Timestamp(quarter_end(*parse_quarter(q))) for q in wide.index]
    frames = []
    for h, metric, tier in spec:
        values = pd.to_numeric(wide[by_norm[_norm(h)]], errors="raise").astype("float64")
        frames.append(_series_frame(metric, tier, period_ends, values.to_numpy(), pulled_at))
    return frames


def _series_frame(metric: str, tier: str, period_ends, values, pulled_at: str) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "metric": metric,
            "entity": ENTITY,
            "entity_type": "aggregate",
            "tier": tier,
            "period_end": period_ends,
            "period_type": "Q",
            "value": values,
            "source": SOURCE,
            "pulled_at": pulled_at,
        }
    )
    return df.dropna(subset=["value"])[FACT_COLUMNS]  # 'null' cells simply have no fact row


def to_facts(balances: pd.DataFrame, originations: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """Both wide tables -> facts rows for every series in COLUMN_SPEC plus the utilization ratio."""
    frames = _wide_to_facts(balances, FILES[0], pulled_at) + _wide_to_facts(originations, FILES[1], pulled_at)
    facts = pd.concat(frames, ignore_index=True)
    num = facts[facts["metric"] == _UTIL_NUM].set_index("period_end")["value"]
    den = facts[facts["metric"] == _UTIL_DEN].set_index("period_end")["value"]
    ratio = (100.0 * num / den).dropna()
    if ratio.empty:
        raise ValueError("cannot compute utilization: balances and commitments share no quarter")
    util = _series_frame(UTILIZATION_METRIC, "all", list(ratio.index), ratio.to_numpy(), pulled_at)
    return pd.concat([facts, util], ignore_index=True)


def parse_release(paths: dict[str, Path], pulled_at: str, expected_quarter: tuple[int, int] | None = None) -> pd.DataFrame:
    """The two files of one release -> facts. If expected_quarter is given, each file must end on that quarter."""
    wide = {file: parse_release_csv(paths[file].read_bytes(), paths[file].name) for file in FILES}
    for file, df in wide.items():
        last = parse_quarter(df.index[-1])
        if expected_quarter is not None and last != expected_quarter:
            raise ValueError(f"{file}: last quarter in the file is {df.index[-1]}, the URL says {expected_quarter[0]}Q{expected_quarter[1]}")
        if parse_quarter(df.index[0]) != FIRST_QUARTER:
            raise ValueError(f"{file}: history starts at {df.index[0]}, expected {FIRST_QUARTER[0]}Q{FIRST_QUARTER[1]}")
    return to_facts(wide[FILES[0]], wide[FILES[1]], pulled_at)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    """Series come out of COLUMN_SPEC, not out of meta. The loader rejects any that series.csv does not list."""
    today = dt.datetime.strptime(pulled_at[:10], "%Y-%m-%d").date()
    year, q, balances = discover_latest(session, today)
    paths = download_release(session, year, q, raw_dir / "latest", balances)
    return parse_release(paths, pulled_at, expected_quarter=(year, q))
