"""Issuer monthly credit metrics from the 8-K exhibits they file with the SEC.

This is the only issuer-level source on the page that is monthly. Everything else that says anything about a
single lender is quarterly and late: the FDIC call report lands 55 to 58 days after quarter end and the Y-14
about 3.5 months, so the freshest issuer-level loss reading on the page can be four and a half months old.
Capital One publishes the same two numbers about two weeks after each month ends.

Publication facts, verified 2026-09-11:
- Capital One Financial Corp is CIK 0000927628. It files an 8-K under Item 7.01 (Regulation FD) around the 15th
  to the 22nd of each month carrying Exhibit 99.1, "Monthly Charge-Off and Delinquency Metrics", for the month
  that just ended. 18 consecutive months were checked and every one has the exhibit. Some months carry a second
  Item 7.01 8-K (an investor conference, say) with no exhibit; those are skipped, so a filing is identified by
  its exhibit, never by counting one per month.
- The exhibit is named `ex991<month><year>creditmetrics.htm`, but EDGAR truncates a document name at 26
  characters, so June 2026 is `ex991june2026creditmetrics.htm` while February 2026 is
  `ex991february2026creditmet.htm`. Match on "credit", never on "metrics".
- The exhibit states its own period in its title block ("As of and for the month ended July 31, 2026"), which is
  what dates the rows here. The file name is not trusted for dating; it only finds the file.
- Layout: a two-row header, "Loans Held for Investment / Net Charge-Offs / 30+ Day Performing Delinquencies /
  Nonperforming Loans" over "Average / Period-End / Amount / Rate(1) / Amount / Rate(2) / Amount / Rate(3)",
  then a "Credit Card:" section with a "Domestic" row and a "Consumer Banking:" section. Dollars are in
  millions; this scales to billions. Only the Credit Card / Domestic row is loaded.
- Cells carry the currency and percent symbols in their own table cells, and the number of empty cells differs
  between rows, so the parser drops empty, "$" and "%" cells and then requires exactly the eight values the
  eight verified sub-headers describe. A layout change fails the parse instead of shifting a column.

User-Agent: the SEC's Akamai edge answers 403 to any User-Agent containing a URL in parentheses, which is the
shape of this pipeline's own ("carddash/0.1.0 (+https://github.com/...)"), with or without a contact address
appended. Verified 2026-09-11: the URL form is 403 on every SEC host and "carddash/0.1.0 <contact>" is 200. The
SEC asks callers to declare a contact address, so that is what this sends, from CARDDASH_CONTACT. It is still
this pipeline identifying itself honestly; it is not a browser string.

Raw storage: one exhibit per month under raw_dir/months/YYYY-MM.htm, about 24 KB each, committed. Finding a
filing's exhibit costs one index.json request per 8-K, so re-reading the whole history every night would be
over a hundred requests a day for two numbers. A run therefore keeps the months it already has and fetches only
the ones it is missing plus a re-check of the newest two, which is the same shape as the NCUA per-quarter
extract and the third documented deviation from "save the raw download under raw_dir/latest/".
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "issuer_8k"
CIK = "0000927628"
ENTITY = "ISSUER:CAPITAL_ONE"
ENTITY_TYPE = "issuer"
PERIOD_TYPE = "M"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/927628"
LANDING_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000927628&type=8-K"
EXHIBIT_RE = re.compile(r"^ex99.*credit", re.I)
MILLIONS_TO_BILLIONS = 0.001
RECHECK_NEWEST = 2

# The eight sub-headers, in the order the exhibit prints them. The parse asserts these exact strings, so a
# renamed or reordered column stops the run instead of quietly moving a number to another metric.
SUBHEADERS = ["Average", "Period-End", "Amount", "Rate", "Amount", "Rate", "Amount", "Rate"]
GROUPS = ["Loans Held for Investment", "Net Charge-Offs", "30+ Day Performing Delinquencies", "Nonperforming Loans"]

# position among the cleaned eight values -> metric. Scale comes from crosswalks/series.csv like every other
# fetcher (the dollar columns are millions there, so 0.001; the rates are already percent), and a metric that is
# not in series.csv for this source is not emitted at all.
FIELDS = {
    0: "cof_card_loans_avg",
    1: "cof_card_loans_eop",
    2: "cof_card_nco_amount",
    3: "cof_card_nco_rate",
    4: "cof_card_dq30_amount",
    5: "cof_card_dq30_rate",
}

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
TITLE_RE = re.compile(r"month ended\s+([A-Za-z]+)\s+\d{1,2},\s*(\d{4})", re.I)
FOOTNOTE_RE = re.compile(r"(?:\s*\(\d+\))+\s*$")

# Capital One moves its footnote markers around constantly and they carry no meaning for the data. Five layouts
# appear across the 55 exhibits from January 2022 to July 2026: 'Domestic' with Rate(1)/(2)/(3) in 43 of them,
# 'Domestic(5)' in 8, 'Credit Card:(4)(5)' in 2, and one month each of 'Domestic' with Rate(2)/(3)/(4) and of
# the Discover split below. So every label is compared with its footnotes stripped.
def _plain(label: str) -> str:
    return FOOTNOTE_RE.sub("", label).strip()


# The combined domestic card book, whatever it is called. For one month (June 2025, the first after the Discover
# acquisition closed) the exhibit split the section into 'Capital One Domestic', 'Discover Domestic' and a
# 'Domestic Card' total; before and after that it is a single 'Domestic' row which is already the combined book.
# The total is what is loaded, so the series does not step at the merger, which is the same choice the FDIC
# roll-up makes for ISSUER:CAPITAL_ONE.
CARD_ROWS = ("Domestic Card", "Domestic")


class _Tables(HTMLParser):
    """Every table row as a list of cell texts. The exhibit is one small table; nothing here needs a DOM."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _sec_headers() -> dict[str, str]:
    contact = os.environ.get("CARDDASH_CONTACT", "").strip()
    if not contact:
        raise RuntimeError(
            "CARDDASH_CONTACT must be set to a contact address for SEC requests. The SEC requires a declaring "
            "contact and refuses this pipeline's normal User-Agent because it contains a URL."
        )
    if "://" in contact:
        raise RuntimeError("CARDDASH_CONTACT must be a contact address, not a URL: the SEC edge 403s URLs.")
    from .. import __version__

    return {"User-Agent": f"carddash/{__version__} {contact}", "Accept-Encoding": "gzip, deflate"}


def _get(session, url: str, headers: dict[str, str]) -> bytes:
    r = session.get(url, headers=headers)
    r.raise_for_status()
    return r.content


def period_of(html: str) -> dt.date:
    """The month the exhibit says it covers, from its own title block."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = TITLE_RE.search(text)
    if not m:
        raise ValueError("exhibit does not state the month it covers ('... month ended <Month> <d>, <yyyy>')")
    month_name, year = m.group(1).lower(), int(m.group(2))
    if month_name not in MONTHS:
        raise ValueError(f"unknown month in the exhibit title: {m.group(1)!r}")
    return last_day(year, MONTHS.index(month_name) + 1)


def parse_exhibit(html: str) -> tuple[dt.date, dict[str, float]]:
    """The combined domestic card row, keyed by metric, in the exhibit's own units (millions, and percent).

    Scaling is the caller's job, from crosswalks/series.csv. Raises if the layout is not one documented above.
    """
    period_end = period_of(html)
    parser = _Tables()
    parser.feed(html)
    rows = [r for r in parser.rows if any(c for c in r)]

    groups = [_plain(c) for c in next((r for r in rows if any(GROUPS[0] in c for c in r)), []) if c]
    if groups != GROUPS:
        raise ValueError(f"exhibit group headers changed: {groups!r}")
    header = next((r for r in rows if "Average" in r and "Period-End" in r), None)
    if header is None:
        raise ValueError("exhibit sub-header row not found")
    subs = [_plain(c) for c in header if c and not c.startswith("(Dollars")]
    if subs != SUBHEADERS:
        raise ValueError(f"exhibit sub-headers changed: {subs!r}")

    # collect the card section's rows first, so the combined total can be preferred over the split components
    card: dict[str, list[str]] = {}
    section = None
    for row in rows:
        label = _plain(row[0].strip() if row else "")
        if label.endswith(":"):                    # 'Credit Card:(4)', 'Consumer Banking:'
            section = label[:-1].strip()
            continue
        if section != "Credit Card" or not label:
            continue
        card[label] = [c for c in row[1:] if c and c not in ("$", "%")]

    for name in CARD_ROWS:
        if name in card:
            values = card[name]
            break
    else:
        raise ValueError(f"no combined domestic card row in the exhibit; rows seen: {sorted(card)!r}")
    if len(values) != len(SUBHEADERS):
        raise ValueError(f"card row {name!r} has {len(values)} values, expected {len(SUBHEADERS)}: {values!r}")

    out: dict[str, float] = {}
    for i, metric in FIELDS.items():
        raw = values[i].replace(",", "").replace("$", "").replace("%", "").strip()
        if raw in ("N/A", "NM", "-", "—"):
            continue
        out[metric] = float(raw)                   # the exhibit's own units: millions of dollars, and percent
    return period_end, out


def _item_701_filings(session, headers: dict[str, str]) -> dict[str, str]:
    """Filing date -> accession for every 8-K filed under Item 7.01.

    Only the `recent` block is read, which carries about a hundred Item 7.01 filings and so several years of
    monthly metrics. Older filings sit in the archive files named in `filings.files`; load those too if the
    history is ever wanted back further.
    """
    sub = json.loads(_get(session, SUBMISSIONS_URL, headers))
    recent = sub["filings"]["recent"]
    found: dict[str, str] = {}
    for form, filed, acc, items in zip(recent["form"], recent["filingDate"], recent["accessionNumber"], recent["items"]):
        if form != "8-K" or "7.01" not in (items or ""):
            continue
        found[filed] = acc
    return found


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    headers = _sec_headers()
    months_dir = raw_dir / "months"
    months_dir.mkdir(parents=True, exist_ok=True)
    # a filing's document list never changes once filed, so it is cached: after the first run only the one or
    # two new filings a month cost an index request, instead of one per 8-K every night
    index_dir = raw_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "latest").mkdir(parents=True, exist_ok=True)

    filings = _item_701_filings(session, headers)
    have = {p.stem for p in months_dir.glob("*.htm")}
    # newest first, so the re-check covers the freshest filings
    ordered = sorted(filings.items(), reverse=True)

    fetched = 0
    for n, (filed, acc) in enumerate(ordered):
        nodash = acc.replace("-", "")
        idx_path = index_dir / f"{nodash}.json"
        # a filing's document list never changes once filed, so it is cached; the newest two are re-read
        if idx_path.exists() and n >= RECHECK_NEWEST:
            names = json.loads(idx_path.read_text(encoding="utf-8"))
        else:
            listing = json.loads(_get(session, f"{ARCHIVE}/{nodash}/index.json", headers))
            names = [it["name"] for it in listing["directory"]["item"]]
            idx_path.write_text(json.dumps(names), encoding="utf-8")
        exhibits = [nm for nm in names if EXHIBIT_RE.match(nm)]
        if not exhibits:
            continue  # an Item 7.01 filing with no metrics exhibit: an investor conference, say
        # cheap skip only: EDGAR's truncation of a document name is not a fixed width, so the name is never
        # trusted to date a row (the exhibit's own title block does that) and is used purely to avoid a download
        guess = _month_from_name(exhibits[0])
        if guess and guess in have and n >= RECHECK_NEWEST:
            continue
        html = _get(session, f"{ARCHIVE}/{nodash}/{exhibits[0]}", headers).decode("utf-8", "replace")
        period_end, _ = parse_exhibit(html)  # fail here rather than writing a file we cannot read
        (months_dir / f"{period_end:%Y-%m}.htm").write_text(html, encoding="utf-8")
        fetched += 1

    df = facts_from_months(months_dir, meta, pulled_at)
    # the newest exhibit is copied to raw_dir/latest/ so the run leaves one like every other source
    newest = max(df["period_end"])
    (raw_dir / "latest" / "monthly_credit_metrics.htm").write_text(
        (months_dir / f"{newest:%Y-%m}.htm").read_text(encoding="utf-8"), encoding="utf-8")
    return df


def facts_from_months(months_dir: Path, meta: pd.DataFrame, pulled_at: str, require_consecutive: bool = True):
    """Every exhibit under months_dir as facts rows, scaled by crosswalks/series.csv.

    Separate from fetch() so the fixture test can run the whole parse-and-scale path with no network. Each file
    must be named for the month its own title block states, which is checked here rather than trusted.
    """
    wanted = {r["metric"]: (float(r["scale"]) if str(r["scale"]).strip() not in ("", "nan", "None") else 1.0)
              for _, r in meta.iterrows()}
    rows: list[dict] = []
    for path in sorted(months_dir.glob("*.htm")):
        period_end, values = parse_exhibit(path.read_text(encoding="utf-8"))
        if f"{period_end:%Y-%m}" != path.stem:
            raise ValueError(f"{path.name} states the month ended {period_end}, which is not its own name")
        for metric, value in values.items():
            if metric not in wanted:
                continue                           # only series declared in crosswalks/series.csv are emitted
            rows.append({
                "metric": metric, "entity": ENTITY, "entity_type": ENTITY_TYPE, "tier": "all",
                "period_end": period_end, "period_type": PERIOD_TYPE, "value": value * wanted[metric],
                "source": SOURCE, "pulled_at": pulled_at,
            })
    if not rows:
        raise ValueError("no monthly credit metrics exhibits were parsed")
    if require_consecutive:
        _require_consecutive(sorted({r["period_end"] for r in rows}))
    return pd.DataFrame(rows)[FACT_COLUMNS]


def _month_from_name(name: str) -> str | None:
    m = re.match(r"^ex99\d*([a-z]+)(\d{4})credit", name, re.I)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f"{int(m.group(2)):04d}-{MONTHS.index(m.group(1).lower()) + 1:02d}"


def _require_consecutive(months: list[dt.date]) -> None:
    """Capital One files every month. A hole means a filing was missed, not that a month did not happen."""
    for earlier, later in zip(months, months[1:]):
        step = (later.year - earlier.year) * 12 + (later.month - earlier.month)
        if step != 1:
            raise ValueError(f"gap in the monthly metrics between {earlier} and {later}")
