"""Issuer monthly credit metrics from the 8-K filings they make with the SEC.

This is the only issuer-level source on the page that is monthly. Everything else that says anything about a
single lender is quarterly and late: the FDIC call report lands 55 to 58 days after quarter end and the Y-14
about 3.5 months, so the freshest issuer-level loss reading on the page can be four and a half months old.
These issuers publish their own numbers about two weeks after each month ends.

THREE ISSUERS LOADED, A FOURTH PARSED AND HELD BACK, AND NO TWO OF THEM FILE THE SAME SHAPE. Verified
2026-09-11 by reading every filing index in each issuer's `recent` block.

- Capital One Financial Corp, CIK 0000927628, entity ISSUER:CAPITAL_ONE. An 8-K under Item 7.01 around the
  15th to the 22nd of each month carrying Exhibit 99.1, "Monthly Charge-Off and Delinquency Metrics", for the
  month that just ended. ONE month per exhibit. Dollars in MILLIONS. Some months carry a second Item 7.01 8-K
  (an investor conference) with no exhibit; a filing is therefore identified by its exhibit, never by counting
  one per month. The exhibit name is `ex991<month><year>creditmetrics.htm`, truncated by EDGAR at no fixed
  width (`ex991april2026creditmetrics.htm` survives at 27 characters, `ex991august2025creditmetri.htm` is cut
  to 26), so it is matched on "credit" and never on "metrics", or February and December vanish.
- Synchrony Financial, CIK 0001601712, entity ISSUER:SYNCHRONY. Exhibit 99.1 is always named exactly
  `creditstatsfinancialtables.htm` (55 of 55 filings from 2022-01-28 to 2026-07-21), the only stable document
  name here. THIRTEEN months per exhibit, so the whole history costs about six downloads. Dollars in BILLIONS.
- Bread Financial Holdings, CIK 0001101215, entity ISSUER:BREAD_FINANCIAL. The exhibit is named for its month
  and truncated the same way Capital One's is, so it is matched on "creditstats", which also matches
  Synchrony's. Only 36 of its 307 Item 7.01 filings carry one and the earliest is 2023-07-27: before that the
  numbers were not filed this way, so Bread's history starts mid-2023 and that is a property of the source.
  TWO months per exhibit, the month and the same month a year earlier. Dollars in MILLIONS.
- American Express Co, CIK 0000004962. PARSED BUT NOT LOADED, see AXP_POPULATION_BREAK below: Amex changed
  the population it reports in May 2026 without restating the history, so its readings cannot be made into one
  series yet. There is NO separate exhibit: the statistics sit in the body of the 8-K itself
  (`axp-<filing date>.htm`), so a filing cannot be identified by a document name and is identified by its
  content instead, with the verdict cached so an earnings 8-K is downloaded at most once. THREE months per
  filing, each split into U.S. Consumer and U.S. Small Business. Dollars in BILLIONS.

THE DEFINITIONS ARE NOT THE SAME, which is the thing most likely to be read wrong off a chart. Every one of
these is the issuer's own measure of its own book, and the scope notes in crosswalks/series.csv say so per
series:
- Capital One: 30+ day PERFORMING delinquencies over period-end loans; charge-offs over average loans.
- Synchrony: over-30-day delinquencies over period-end receivables; charge-offs over average receivables
  including held for sale. Its monthly charge-off rate is saw-toothed because charge-off cycle dates fall
  differently in each calendar month (the exhibit prints the count of cycle dates per month for exactly this
  reason), so Synchrony also publishes an "adjusted" rate spreading recoveries evenly across the quarter. The
  unadjusted rate is the comparable one and is what `issuer_card_nco_rate` carries; the adjusted one is loaded
  beside it as `issuer_card_nco_rate_adj` and is labelled the non-GAAP measure it is.
- Bread: net principal losses over average loans; delinquency over period-end PRINCIPAL loans, a smaller
  denominator than its own end-of-period loan figure. Both are loaded so the rate can be recomputed.
- American Express: net write-off rate PRINCIPAL ONLY, explicitly excluding interest and fees, on a book that
  includes pay-in-full charge-card balances. Both push its rates far below everyone else's and neither means
  what a naive read of the chart would take it to mean.

Not loaded on purpose: the second table in the Amex filing is the American Express Credit Account Master Trust,
which holds only revolve-eligible balances and computes its write-off rate on end-of-period rather than average
balances. Amex's own filing says at length why the two are not comparable, so the trust table is skipped.

THE ITEM TAG IS NOT A FILTER. The obvious way to find these is Item 7.01, Regulation FD, which is what they
normally are, and it is wrong: Synchrony filed its July 2026 credit statistics on 2026-08-17 under Item 2.02
while every other month of the same exhibit went out under 7.01. Filtering on 7.01 drops the newest month and
leaves a source that looks permanently one month behind instead of failing, so every 8-K is considered and the
document itself decides.

User-Agent: the SEC's Akamai edge answers 403 to any User-Agent containing a URL in parentheses, which is the
shape of this pipeline's own ("carddash/0.1.0 (+https://github.com/...)"), with or without a contact address
appended. Verified 2026-09-11: the URL form is 403 on every SEC host and "carddash/0.1.0 <contact>" is 200. The
SEC asks callers to declare a contact address, so that is what this sends, from CARDDASH_CONTACT. It is still
this pipeline identifying itself honestly; it is not a browser string.

Raw storage: one document per FILING under raw_dir/<issuer>/<primary month>.htm, named for the newest month it
states. Capital One's stay at raw_dir/months/ where they already are. Finding a filing's document costs one
index.json request per 8-K, so re-reading every history every night would be hundreds of requests for a handful
of numbers. A run therefore parses what it already holds, works out which months are missing, and downloads
only the filings that can fill them plus a re-check of the newest two per issuer. That is the same shape as the
NCUA per-quarter extract and is the third documented deviation from "save the raw download under
raw_dir/latest/".
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

import pandas as pd

from ..schema import FACT_COLUMNS, last_day

SOURCE = "issuer_8k"
PERIOD_TYPE = "M"
ENTITY_TYPE = "issuer"
RECHECK_NEWEST = 2

# One metric per concept, and the issuer is the ENTITY. An issuer is not a metric name: that is what lets the
# same chart draw four lenders and the same view compare them.
M_NCO_RATE = "issuer_card_nco_rate"
M_NCO_RATE_ADJ = "issuer_card_nco_rate_adj"
M_DQ30_RATE = "issuer_card_dq30_rate"
M_LOANS_AVG = "issuer_card_loans_avg"
M_LOANS_EOP = "issuer_card_loans_eop"
M_LOANS_EOP_PRINCIPAL = "issuer_card_loans_eop_principal"
M_NCO_AMOUNT = "issuer_card_nco_amount"
M_DQ30_AMOUNT = "issuer_card_dq30_amount"

E_COF = "ISSUER:CAPITAL_ONE"
E_SYF = "ISSUER:SYNCHRONY"
E_BFH = "ISSUER:BREAD_FINANCIAL"
# American Express reports two segments on two BASES, and the basis is part of the population rather than a
# label on it, so it is part of the entity. Amex stopped reporting 'Card Member loans' in May 2026 and started
# reporting 'Card balances', which adds pay-in-full charge-card balances the old measure left out; the two
# overlap for February and March 2026 and disagree by about 14 percent on the same month. Carrying them as one
# series would invent a step. The retired pair therefore ends in March 2026 and never updates again, the way
# the FDIC merged-out charters do, and the current pair starts in February 2026.
E_AXP_CONSUMER = "ISSUER:AMEX_US_CONSUMER"                       # Card balances, February 2026 on
E_AXP_CONSUMER_LOANS = "ISSUER:AMEX_US_CONSUMER_LOANS"           # Card Member loans, retired March 2026
E_AXP_SMALL_BUSINESS = "ISSUER:AMEX_US_SMALL_BUSINESS"
E_AXP_SMALL_BUSINESS_LOANS = "ISSUER:AMEX_US_SMALL_BUSINESS_LOANS"

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MONTH_ABBR = [m[:3] for m in MONTHS]

FOOTNOTE_RE = re.compile(r"(?:\s*\((?:\d+|[a-z])\))+\s*$")
# 'July 31, 2026', 'Jun 30,2026' (the cell has a line break where the space would be)
DATE_RE = re.compile(r"([A-Z][a-z]{2,8})\.?\s*(\d{1,2})\s*,\s*(\d{4})")
MISSING = {"", "-", "--", "N/A", "NM", "NA", "—", "–"}

# Reading a document costs a request, so the walk back through a filing history stops at a floor rather than
# running to the end of EDGAR's `recent` block. Each floor is the month the issuer's own disclosure starts,
# established by reading the earliest filing that carries one; a filing whose month falls before its issuer's
# floor is never fetched. Months EARLIER than the floor are still kept when an exhibit happens to state them
# (Synchrony's thirteen-month table reaches back a year past the filing it came in), because the floor governs
# which filings are fetched, not which readings are believed.
PUNCT = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
         " ": " ", "�": "-"}


def _clean(text: str) -> str:
    """Unicode punctuation flattened to ASCII and runs of whitespace collapsed, footnotes left alone.

    The documents are ASCII with HTML entities, so a dash arrives as U+2013 or U+2014 and a non-breaking space
    as U+00A0 once the parser has resolved the references.
    """
    for bad, good in PUNCT.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text).strip()


def _norm(label: str) -> str:
    """A LABEL, cleaned and with its footnote markers removed.

    Every issuer here moves footnote markers around constantly and they carry no meaning for the data, so
    labels are compared after this and never before. Values are NOT put through it: a footnote marker and a
    negative number in accounting parentheses are written the same way, so stripping '(5)' off a label is
    right and stripping it off a value would turn minus five into nothing.
    """
    return FOOTNOTE_RE.sub("", _clean(label)).strip()


def _number(cell: str) -> float | None:
    """A numeric cell in the document's own units, or None when the document prints no value.

    Parentheses are the accounting minus sign: Synchrony's recovery adjustment prints '(0.1)'.
    """
    raw = _clean(cell)
    # A footnote marker can be stuck on the END of a VALUE: Amex printed '2.5%(b)' and '1.7%(b)' for November
    # and December 2023, where a settlement timing note applied to those two months, and a straight parse of
    # that is not a number, so both months went missing without a word until the gap check caught them. This
    # is not the same shape as '(0.1)', where the parentheses wrap the WHOLE cell and mean minus, so a cell
    # that opens with one is left alone.
    if not raw.startswith("("):
        note = FOOTNOTE_RE.search(raw)
        if note is not None and note.start() > 0:
            raw = raw[:note.start()].strip()
    raw = raw.replace(",", "").replace("$", "").replace("%", "").strip()
    if raw in MISSING:
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1].strip()
    try:
        value = float(raw)
    except ValueError:
        return None
    return -value if negative else value


def _month_end(text: str) -> dt.date | None:
    """The month end a date string names, whatever spelling of the month it uses."""
    m = DATE_RE.search(_norm(text))
    if not m:
        return None
    name = m.group(1).lower()
    if name in MONTHS:
        month = MONTHS.index(name) + 1
    elif name[:3] in MONTH_ABBR:
        month = MONTH_ABBR.index(name[:3]) + 1
    else:
        return None
    return last_day(int(m.group(3)), month)


class _Tables(HTMLParser):
    """Every table row as a list of cell texts. These are small documents; nothing here needs a DOM."""

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


def _rows(html: str) -> list[list[str]]:
    parser = _Tables()
    parser.feed(html)
    return [r for r in parser.rows if any(c for c in r)]


def _values(row: list[str]) -> list[float | None]:
    """The numbers in a row, with the currency and percent cells the filers put in their own columns dropped.

    The number of empty and symbol cells differs between rows in every one of these documents, so a row is
    read by dropping what is not a value rather than by column position.
    """
    out: list[float | None] = []
    for cell in row[1:]:
        text = _clean(cell)
        if text in ("", "$", "%"):
            continue
        out.append(_number(cell))
    return out


# ---------------------------------------------------------------------------------------------------------
# Capital One
# ---------------------------------------------------------------------------------------------------------

# The eight sub-headers, in the order the exhibit prints them. The parse asserts these exact strings, so a
# renamed or reordered column stops the run instead of quietly moving a number to another metric.
COF_SUBHEADERS = ["Average", "Period-End", "Amount", "Rate", "Amount", "Rate", "Amount", "Rate"]
COF_GROUPS = ["Loans Held for Investment", "Net Charge-Offs", "30+ Day Performing Delinquencies",
              "Nonperforming Loans"]
COF_FIELDS = {
    0: M_LOANS_AVG,
    1: M_LOANS_EOP,
    2: M_NCO_AMOUNT,
    3: M_NCO_RATE,
    4: M_DQ30_AMOUNT,
    5: M_DQ30_RATE,
}
COF_TITLE_RE = re.compile(r"month ended\s+([A-Za-z]+)\s+\d{1,2},\s*(\d{4})", re.I)

# The combined domestic card book, whatever it is called. For one month (June 2025, the first after the Discover
# acquisition closed) the exhibit split the section into 'Capital One Domestic', 'Discover Domestic' and a
# 'Domestic Card' total; before and after that it is a single 'Domestic' row which is already the combined book.
# The total is what is loaded, so the series does not step at the merger, which is the same choice the FDIC
# roll-up makes for ISSUER:CAPITAL_ONE.
COF_CARD_ROWS = ("Domestic Card", "Domestic")


def period_of(html: str) -> dt.date:
    """The month Capital One's exhibit says it covers, from its own title block."""
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    m = COF_TITLE_RE.search(text)
    if not m:
        raise ValueError("exhibit does not state the month it covers ('... month ended <Month> <d>, <yyyy>')")
    month_name, year = m.group(1).lower(), int(m.group(2))
    if month_name not in MONTHS:
        raise ValueError(f"unknown month in the exhibit title: {m.group(1)!r}")
    return last_day(year, MONTHS.index(month_name) + 1)


def parse_exhibit(html: str) -> tuple[dt.date, dict[str, float]]:
    """Capital One's combined domestic card row, keyed by metric, in the exhibit's own units.

    Scaling is the caller's job, from crosswalks/series.csv. Raises if the layout is not one documented above.
    """
    period_end = period_of(html)
    rows = _rows(html)

    groups = [_norm(c) for c in next((r for r in rows if any(COF_GROUPS[0] in c for c in r)), []) if c]
    if groups != COF_GROUPS:
        raise ValueError(f"exhibit group headers changed: {groups!r}")
    header = next((r for r in rows if "Average" in r and "Period-End" in r), None)
    if header is None:
        raise ValueError("exhibit sub-header row not found")
    subs = [_norm(c) for c in header if c and not c.startswith("(Dollars")]
    if subs != COF_SUBHEADERS:
        raise ValueError(f"exhibit sub-headers changed: {subs!r}")

    # collect the card section's rows first, so the combined total can be preferred over the split components
    card: dict[str, list[str]] = {}
    section = None
    for row in rows:
        label = _norm(row[0].strip() if row else "")
        if label.endswith(":"):                    # 'Credit Card:(4)', 'Consumer Banking:'
            section = label[:-1].strip()
            continue
        if section != "Credit Card" or not label:
            continue
        card[label] = [c for c in row[1:] if _clean(c) not in ("", "$", "%")]

    for name in COF_CARD_ROWS:
        if name in card:
            cells = card[name]
            break
    else:
        raise ValueError(f"no combined domestic card row in the exhibit; rows seen: {sorted(card)!r}")
    if len(cells) != len(COF_SUBHEADERS):
        raise ValueError(f"card row {name!r} has {len(cells)} values, expected {len(COF_SUBHEADERS)}: {cells!r}")

    out: dict[str, float] = {}
    for i, metric in COF_FIELDS.items():
        value = _number(cells[i])
        if value is not None:
            out[metric] = value                    # the exhibit's own units: millions of dollars, and percent
    return period_end, out


def parse_cof(html: str) -> dict[dt.date, dict[tuple[str, str], float]]:
    period_end, values = parse_exhibit(html)
    return {period_end: {(E_COF, metric): v for metric, v in values.items()}}


# ---------------------------------------------------------------------------------------------------------
# Synchrony
# ---------------------------------------------------------------------------------------------------------

# Synchrony has used TWO vocabularies for the same five rows. Until some point between July 2024 and January
# 2025 it spelled each rate out as its own definition; since then it uses the short names and states the
# definitions in the footnotes instead. Both are loaded, and the long forms are worth keeping in view because
# they are the issuer's own statement of what the rates divide by.
SYF_ROWS = {
    "Period-end loan receivables": M_LOANS_EOP,
    "Average loan receivables, including held for sale": M_LOANS_AVG,
    "30+ delinquency rate": M_DQ30_RATE,
    "30+ days past due as a % of period-end loan receivables": M_DQ30_RATE,
    "Net charge-off rate": M_NCO_RATE,
    "Net charge-offs (annualized) as a % of average loan receivables, including held for sale": M_NCO_RATE,
    "Adjusted net charge-off rate": M_NCO_RATE_ADJ,
    "Adjusted net charge-offs as a % of average loan receivables, including held for sale": M_NCO_RATE_ADJ,
}
SYF_REQUIRED = {M_LOANS_EOP, M_LOANS_AVG, M_DQ30_RATE, M_NCO_RATE, M_NCO_RATE_ADJ}
SYF_TITLE = "MONTHLY CHARGE-OFF AND DELINQUENCY STATISTICS"


def parse_syf(html: str) -> dict[dt.date, dict[tuple[str, str], float]]:
    """Synchrony's thirteen-month table. Dollars are already billions; the rates are percent.

    The exhibit carries a second table, the count of charge-off cycle dates in each calendar month, whose
    header row is a pair of years rather than dates and whose row labels are month names. It is skipped by
    requiring a header of full dates and by matching row labels exactly.
    """
    rows = _rows(html)
    if not any(SYF_TITLE in _norm(c) for row in rows for c in row):
        raise ValueError(f"not a Synchrony monthly statistics exhibit: {SYF_TITLE!r} is not in it")

    periods: list[dt.date] | None = None
    for row in rows:
        dates = [_month_end(c) for c in row if _norm(c)]
        if len(dates) >= 2 and all(d is not None for d in dates):
            periods = [d for d in dates if d is not None]
            break
    if periods is None:
        raise ValueError("Synchrony exhibit has no row of month-end dates")

    out: dict[dt.date, dict[tuple[str, str], float]] = {p: {} for p in periods}
    seen: set[str] = set()
    for row in rows:
        label = _norm(row[0] if row else "")
        metric = SYF_ROWS.get(label)
        if metric is None:
            continue
        seen.add(metric)
        values = _values(row)
        if len(values) != len(periods):
            raise ValueError(
                f"Synchrony row {label!r} has {len(values)} values against {len(periods)} dated columns")
        for period, value in zip(periods, values):
            if value is not None:
                out[period][(E_SYF, metric)] = value

    missing = SYF_REQUIRED - seen
    if missing:
        raise ValueError(f"Synchrony exhibit is missing metrics this parser requires: {sorted(missing)!r}")
    return {p: v for p, v in out.items() if v}


# ---------------------------------------------------------------------------------------------------------
# Bread Financial
# ---------------------------------------------------------------------------------------------------------

BFH_ROWS = {
    "End-of-period credit card and other loans": M_LOANS_EOP,
    "Average credit card and other loans": M_LOANS_AVG,
    "Net principal losses": M_NCO_AMOUNT,
    # Bread renamed this row from 'Net loss rate' to 'Net principal loss rate' in early 2026. Same row, same
    # place in the same table, and the older name is the one most of the history carries.
    "Net principal loss rate": M_NCO_RATE,
    "Net loss rate": M_NCO_RATE,
    "30 days + delinquencies - principal": M_DQ30_AMOUNT,
    "Period ended credit card and other loans - principal": M_LOANS_EOP_PRINCIPAL,
    "Delinquency rate": M_DQ30_RATE,
}
BFH_REQUIRED = {M_LOANS_EOP, M_LOANS_AVG, M_NCO_RATE, M_DQ30_RATE}
# 'For the three months ended September 30, 2023' beside 'For the month ended September 30, 2023'. Matches an
# aggregate column and not 'themonth ended', which is how the monthly header arrives once the line break
# between 'the' and 'month' is collapsed.
BFH_AGGREGATE_RE = re.compile(r"\b(three|six|nine|twelve|\d+)\s+months\s+ended", re.I)


def parse_bfh(html: str) -> dict[dt.date, dict[tuple[str, str], float]]:
    """Bread's performance update: two small tables, each the month and the same month a year earlier.

    The loss table is headed 'For the month ended ...' and the delinquency table 'As of ...', so the dated
    header is re-read whenever one appears rather than assumed once for the document.

    IN A QUARTER-END MONTH THE SECOND COLUMN IS NOT A MONTH. For March, June, September and December the loss
    table drops the year-ago column and prints 'For the three months ended <the same date>' beside the
    monthly one, so the header states the SAME date twice and only the first of the two is a month. Reading
    the header as dates alone puts the quarter's figure on the month: September 2023 would be loaded as a
    6.9 percent net loss rate, which is the quarter, when the month was 6.7. Columns whose header says
    'N months ended' are therefore dropped, and a header that still repeats a month afterwards is an error
    rather than a last-write-wins.
    """
    rows = _rows(html)
    periods: list[dt.date | None] = []
    out: dict[dt.date, dict[tuple[str, str], float]] = {}
    found: set[str] = set()

    for row in rows:
        cells = [c for c in row if _clean(c)]
        dates = [_month_end(c) for c in cells]
        if len(cells) >= 2 and all(d is not None for d in dates):
            columns = [None if BFH_AGGREGATE_RE.search(_clean(c)) else d for c, d in zip(cells, dates)]
            months = [d for d in columns if d is not None]
            if not months:
                raise ValueError(f"Bread header has no monthly column: {[_clean(c) for c in cells]!r}")
            if len(set(months)) != len(months):
                raise ValueError(f"Bread header states a month twice: {[_clean(c) for c in cells]!r}")
            periods = columns
            for p in months:
                out.setdefault(p, {})
            continue
        label = _norm(row[0] if row else "")
        metric = BFH_ROWS.get(label)
        if metric is None or not periods:
            continue
        values = _values(row)
        if len(values) != len(periods):
            raise ValueError(f"Bread row {label!r} has {len(values)} values against {len(periods)} columns")
        found.add(metric)
        for period, value in zip(periods, values):
            if period is not None and value is not None:
                out[period][(E_BFH, metric)] = value

    if not periods:
        raise ValueError("Bread exhibit has no dated header row")
    missing = BFH_REQUIRED - found
    if missing:
        raise ValueError(f"Bread exhibit is missing metrics this parser requires: {sorted(missing)!r}")
    return {p: v for p, v in out.items() if v}


# ---------------------------------------------------------------------------------------------------------
# American Express
# ---------------------------------------------------------------------------------------------------------

# Amex renamed every one of these rows in the 2026-05-15 filing, and the rename was not only a rename: see
# AXP_POPULATION_BREAK below.
# The section label already says which basis the filing is on, so it is the whole of the mapping: no date
# cutoff is hard-coded anywhere, and if Amex ever reverts the wording the rows follow the wording.
AXP_SECTIONS = {
    "U.S. Consumer Card balances": E_AXP_CONSUMER,
    "U.S. Consumer Card Member loans": E_AXP_CONSUMER_LOANS,
    "U.S. Small Business Card balances": E_AXP_SMALL_BUSINESS,
    "U.S. Small Business Card Member loans": E_AXP_SMALL_BUSINESS_LOANS,
}
AXP_ROWS = {
    "Total Card balances": M_LOANS_EOP,
    "Total loans": M_LOANS_EOP,
    "Average Card balances": M_LOANS_AVG,
    "Average loans": M_LOANS_AVG,
    "30 days past due as a % of total": M_DQ30_RATE,
    "30 days past due loans as a % of total": M_DQ30_RATE,
    "Net write-off rate - principal only": M_NCO_RATE,
}
AXP_MARKER = "Net write-off rate"

# HOW AMERICAN EXPRESS'S TWO POPULATIONS ARE CARRIED (decided 2026-09-11, task 46).
#
# The break below is real and cannot be closed from the filings, so it is not closed: each basis is its own
# entity and the two are drawn as two lines that overlap for the two months Amex reported both. The reader sees
# the size of the break instead of a step in one line, which is the only honest way to show it and is also the
# most informative, because the gap between the lines IS the pay-in-full balance that the old measure omitted.
#
# Between the filings of 2026-04-15 and 2026-05-15 Amex stopped reporting "Card Member loans" and started
# reporting "Card balances", and the population changed with the words: the new measure includes pay-in-full
# charge-card balances that the old one left out. It is a step of about 14 percent in the level and it moves
# the delinquency rate the other way, because the added balances are almost never past due:
#
#     U.S. Consumer, March 2026     old basis (filed 2026-04-15)   $97.5bn    30 days past due 1.4%
#     U.S. Consumer, March 2026     new basis (filed 2026-05-15)   $110.8bn   30 days past due 1.3%
#
# Amex restated only the two months that overlap, not the history, so the break cannot be closed from the
# filings: the newest statement of January 2026 is $97.2bn on the old basis and of February 2026 is $107.4bn
# on the new one. Splicing them into one series would put a fake 10 percent jump in the balances and a fake
# improvement in the delinquency rate at exactly the moment the page is meant to be read most carefully, which
# is the same class of error as the collapsed denominator task 41 removed. Loading only the new basis leaves
# five months, which is not a series.
#
# The retired pair (ISSUER:AMEX_US_*_LOANS) ends in March 2026 and will never gain another month, so its
# series.csv rows carry max_age_days 36500 and say so, exactly as the FDIC merged-out charters do. Nothing
# hard-codes the changeover date: the section label in each filing says which basis it is on, so the rows
# follow the document.
AXP_POPULATION_BREAK = dt.date(2026, 2, 28)


def looks_like_axp_metrics(html: str) -> bool:
    """Content test, so an Amex earnings 8-K is recognised without being parsed.

    It reads the PARSED table text, not the raw HTML. Testing the raw HTML looks cheaper and is wrong: Amex
    splits the row label across tags in some filings, so a substring search for 'Net write-off rate' answers
    False for a filing that plainly carries the table (verified on the 2026-05-15 and 2026-06-15 filings,
    both of which do).
    """
    return any(AXP_MARKER in _norm(cell) for row in _rows(html) for cell in row)


def parse_axp(html: str) -> dict[dt.date, dict[tuple[str, str], float]]:
    """Amex's three months, for the two card segments it reports separately.

    The filing carries a SECOND dated table below this one for the American Express Credit Account Master
    Trust, which is a different population on a different calculation and is deliberately not loaded. It is
    cut off by stopping at the first dated header whose columns are ranges ('July 1, 2026 through July 31,
    2026') rather than single dates, which is how the trust table heads its columns.
    """
    rows = _rows(html)
    periods: list[dt.date] = []
    entity: str | None = None
    out: dict[dt.date, dict[tuple[str, str], float]] = {}
    found: set[tuple[str, str]] = set()

    for row in rows:
        cells = [c for c in row if _norm(c)]
        texts = [_norm(c) for c in cells]
        dates = [_month_end(c) for c in texts]
        dated = [d for d in dates if d is not None]
        if len(dated) >= 2 and len(dated) >= len(cells) - 1:
            if any("through" in t.lower() for t in texts):
                break                              # the Lending Trust table starts here; everything below is its
            periods = dated
            for p in periods:
                out.setdefault(p, {})
            entity = None
            continue
        label = _norm(row[0] if row else "")
        section = label[:-1].strip() if label.endswith(":") else label
        if section in AXP_SECTIONS:
            entity = AXP_SECTIONS[section]
            continue
        metric = AXP_ROWS.get(label)
        if metric is None or entity is None or not periods:
            continue
        values = _values(row)
        if len(values) != len(periods):
            raise ValueError(f"Amex row {label!r} has {len(values)} values against {len(periods)} columns")
        for period, value in zip(periods, values):
            if value is not None:
                out[period][(entity, metric)] = value
                found.add((entity, metric))

    if not periods:
        raise ValueError("Amex filing has no dated header row of month ends")
    # A filing is on ONE basis, so it carries two of the four entities: one consumer, one small business. The
    # check is that both segments are there with every metric, not that all four entities are, or every filing
    # would fail for lacking the basis it is not written on.
    entities = {e for e, _ in found}
    if len(entities) != 2 or not any("SMALL_BUSINESS" in e for e in entities) \
            or not any("SMALL_BUSINESS" not in e for e in entities):
        raise ValueError(f"Amex filing does not carry one consumer and one small business section: "
                         f"{sorted(entities)!r}")
    missing = {(e, m) for e in entities for m in AXP_ROWS.values()} - found
    if missing:
        raise ValueError(f"Amex filing is missing rows this parser requires: {sorted(missing)!r}")
    return {p: v for p, v in out.items() if v}


# ---------------------------------------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------------------------------------

EXHIBIT_CREDIT_RE = re.compile(r"^ex99.*credit", re.I)
EXHIBIT_CREDITSTATS_RE = re.compile(r"creditstats", re.I)
AXP_PRIMARY_RE = re.compile(r"^axp-\d{8}\.htm$", re.I)


def _pick_by(pattern: re.Pattern[str]) -> Callable[[list[str]], str | None]:
    def pick(names: list[str]) -> str | None:
        hits = [n for n in names if pattern.search(n)]
        return hits[0] if hits else None
    return pick


@dataclass(frozen=True)
class Issuer:
    key: str
    cik: str
    name: str
    entities: tuple[str, ...]
    pick: Callable[[list[str]], str | None]
    parse: Callable[[str], dict[dt.date, dict[tuple[str, str], float]]]
    raw_subdir: str
    first_month: dt.date          # the fetch floor: no filing for a month before this is ever downloaded
    confirm: Callable[[str], bool] | None = None   # content test when the name cannot identify the document

    @property
    def archive(self) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}"

    @property
    def submissions(self) -> str:
        return f"https://data.sec.gov/submissions/CIK{self.cik}.json"


ISSUERS: tuple[Issuer, ...] = (
    Issuer(key="cof", cik="0000927628", name="Capital One", entities=(E_COF,),
           pick=_pick_by(EXHIBIT_CREDIT_RE), parse=parse_cof, raw_subdir="months",
           first_month=dt.date(2021, 2, 28)),
    Issuer(key="syf", cik="0001601712", name="Synchrony", entities=(E_SYF,),
           pick=_pick_by(EXHIBIT_CREDITSTATS_RE), parse=parse_syf, raw_subdir="syf",
           first_month=dt.date(2021, 12, 31)),
    # The floor is July 2023 rather than June because Bread's FIRST credit stats exhibit, filed 2023-07-27 for
    # June 2023, carries no HTML table at all (the parser finds zero rows in it). Every exhibit from the
    # 2023-08-15 filing on is the two-table layout parsed here. June 2023 is therefore not available and the
    # floor says so, rather than the run failing on it every night.
    Issuer(key="bfh", cik="0001101215", name="Bread Financial", entities=(E_BFH,),
           pick=_pick_by(EXHIBIT_CREDITSTATS_RE), parse=parse_bfh, raw_subdir="bfh",
           first_month=dt.date(2023, 7, 31)),
    # Amex has no exhibit to name, so its filings are found by content and the verdict is cached. The floor is
    # January 2023 rather than earlier: each filing carries three months and its own 8-K body is 70 to 100 KB,
    # so reaching further back costs raw storage faster than it buys history.
    Issuer(key="axp", cik="0000004962", name="American Express",
           entities=(E_AXP_CONSUMER, E_AXP_CONSUMER_LOANS, E_AXP_SMALL_BUSINESS, E_AXP_SMALL_BUSINESS_LOANS),
           pick=_pick_by(AXP_PRIMARY_RE), parse=parse_axp, raw_subdir="axp",
           first_month=dt.date(2023, 1, 31), confirm=looks_like_axp_metrics),
)

HELD_BACK: tuple[Issuer, ...] = ()
BY_KEY = {i.key: i for i in ISSUERS + HELD_BACK}

# Kept so the module still answers to the single-issuer names the first version used.
CIK = BY_KEY["cof"].cik
ENTITY = E_COF
LANDING_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000927628&type=8-K"


# ---------------------------------------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------------------------------------

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


def _eight_k_filings(session, issuer: Issuer, headers: dict[str, str]) -> list[tuple[str, str]]:
    """(filing date, accession) for EVERY 8-K the issuer filed, newest first.

    The obvious filter here is Item 7.01, Regulation FD, which is what these disclosures normally are, and it
    is WRONG. Synchrony filed its July 2026 credit statistics on 2026-08-17 under Item 2.02 while every other
    month of the same exhibit went out under 7.01, so an Item 7.01 filter drops the newest month and leaves a
    source that looks permanently one month behind instead of failing. The item tag is the filer's
    description of the filing, not a property of the document, so it is not used. Filings are cheap to pass
    over: one is only looked at when it could carry a month that is missing, and its document list is cached.

    Only the `recent` block is read, which carries several years of filings for each of these four. Older ones
    sit in the archive files named in `filings.files`; load those too if the history is ever wanted back
    further than the floors in ISSUERS.
    """
    sub = json.loads(_get(session, issuer.submissions, headers))
    recent = sub["filings"]["recent"]
    found = [(filed, acc) for form, filed, acc
             in zip(recent["form"], recent["filingDate"], recent["accessionNumber"]) if form == "8-K"]
    return sorted(found, reverse=True)


def _step(earlier: dt.date, later: dt.date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def _add_months(date: dt.date, n: int) -> dt.date:
    total = (date.year * 12 + date.month - 1) + n
    return last_day(total // 12, total % 12 + 1)


def _contiguous_tail(months: list[dt.date]) -> set[dt.date]:
    """The unbroken run of months ending at the newest one the document states.

    This is what a document proves about coverage, and it is what the download planner may skip on. It matters
    because the documents differ: Synchrony's thirteen months are contiguous and all count, while Bread prints
    a month and the same month a YEAR earlier, so only the newer one counts and the older one must not stop
    the filing that reports it from being fetched.
    """
    if not months:
        return set()
    ordered = sorted(months, reverse=True)
    tail = {ordered[0]}
    for earlier in ordered[1:]:
        if _step(earlier, min(tail)) == 1:
            tail.add(earlier)
        else:
            break
    return tail


def _index(session, issuer: Issuer, acc: str, index_dir: Path, headers: dict[str, str],
           refresh: bool) -> list[str]:
    """A filing's document names. Cached: a filing's document list never changes once it is filed."""
    nodash = acc.replace("-", "")
    path = index_dir / f"{nodash}.json"
    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        return cached if isinstance(cached, list) else cached["names"]
    listing = json.loads(_get(session, f"{issuer.archive}/{nodash}/index.json", headers))
    names = [it["name"] for it in listing["directory"]["item"]]
    path.write_text(json.dumps(names), encoding="utf-8")
    return names


def _load_local(issuer: Issuer, raw_dir: Path) -> dict[str, dict[dt.date, dict[tuple[str, str], float]]]:
    """Every document already on disk for this issuer, parsed. No network."""
    folder = raw_dir / issuer.raw_subdir
    out: dict[str, dict[dt.date, dict[tuple[str, str], float]]] = {}
    for path in sorted(folder.glob("*.htm")):
        try:
            parsed = issuer.parse(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{issuer.name} raw file {path.name}: {exc}") from exc
        newest = max(parsed)
        if f"{newest:%Y-%m}" != path.stem:
            raise ValueError(f"{path.name} states {newest:%Y-%m} as its newest month, which is not its name")
        out[path.stem] = parsed
    return out


def _fetch_issuer(session, issuer: Issuer, raw_dir: Path, headers: dict[str, str],
                  no_metrics: dict[str, list[str]]) -> int:
    """Download the filings needed to fill the months this issuer is missing. Returns how many were fetched."""
    folder = raw_dir / issuer.raw_subdir
    folder.mkdir(parents=True, exist_ok=True)
    index_dir = raw_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    local = _load_local(issuer, raw_dir)
    covered: set[dt.date] = set()
    for parsed in local.values():
        covered |= _contiguous_tail(list(parsed))

    filings = _eight_k_filings(session, issuer, headers)
    skip = set(no_metrics.get(issuer.key, []))
    fetched = 0
    for n, (filed, acc) in enumerate(filings):
        filed_date = dt.date.fromisoformat(filed)
        target = _add_months(last_day(filed_date.year, filed_date.month), -1)
        if target < issuer.first_month:
            break                                   # the floor: older filings are never fetched
        if target in covered and n >= RECHECK_NEWEST:
            continue
        if acc in skip and n >= RECHECK_NEWEST:
            continue                                # known to carry no metrics; checked once, never again
        names = _index(session, issuer, acc, index_dir, headers, refresh=n < RECHECK_NEWEST)
        document = issuer.pick(names)
        if document is None:
            continue                                # an Item 7.01 filing with no metrics document
        nodash = acc.replace("-", "")
        url = f"{issuer.archive}/{nodash}/{document}"
        html = _get(session, url, headers).decode("utf-8", "replace")
        if issuer.confirm is not None and not issuer.confirm(html):
            no_metrics.setdefault(issuer.key, []).append(acc)
            continue                                # an earnings 8-K: remembered, so it is downloaded once
        try:
            parsed = issuer.parse(html)             # fail here rather than writing a file we cannot read
        except ValueError as exc:                   # say WHICH document, or the next person reads 97 of them
            raise ValueError(f"{issuer.name} filing of {filed}: {exc} ({url})") from exc
        newest = max(parsed)
        (folder / f"{newest:%Y-%m}.htm").write_text(html, encoding="utf-8")
        covered |= _contiguous_tail(list(parsed))
        fetched += 1
    return fetched


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    headers = _sec_headers()
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "latest").mkdir(parents=True, exist_ok=True)

    verdict_path = raw_dir / "index" / "no_metrics.json"
    (raw_dir / "index").mkdir(parents=True, exist_ok=True)
    no_metrics = json.loads(verdict_path.read_text(encoding="utf-8")) if verdict_path.exists() else {}

    for issuer in ISSUERS:
        _fetch_issuer(session, issuer, raw_dir, headers, no_metrics)
    verdict_path.write_text(json.dumps(no_metrics, indent=1, sort_keys=True), encoding="utf-8")

    df = facts_from_raw(raw_dir, meta, pulled_at)
    # the newest document of each issuer is copied to raw_dir/latest/ so a run leaves one like every other source
    for issuer in ISSUERS:
        folder = raw_dir / issuer.raw_subdir
        newest = max(folder.glob("*.htm"), key=lambda p: p.stem, default=None)
        if newest is not None:
            (raw_dir / "latest" / f"{issuer.key}_monthly_credit_metrics.htm").write_text(
                newest.read_text(encoding="utf-8"), encoding="utf-8")
    return df


def facts_from_raw(raw_dir: Path, meta: pd.DataFrame, pulled_at: str,
                   require_consecutive: bool = True, issuers: tuple[Issuer, ...] = ISSUERS) -> pd.DataFrame:
    """Every document under raw_dir as facts rows, scaled by crosswalks/series.csv.

    Separate from fetch() so the fixture tests run the whole parse-and-scale path with no network. Scale is
    per (metric, entity) because the issuers do not print the same units: Capital One and Bread state dollars
    in millions, Synchrony and American Express in billions.

    Where two documents state the same month, the one filed later wins, which is the same
    newest-vintage-wins rule every other source here follows. Bread makes this routine rather than rare: each
    of its exhibits prints the month AND the same month a year earlier, so most months are stated twice and
    the later statement is the one loaded.
    """
    wanted = {(r["metric"], r["entity"]):
              (float(r["scale"]) if str(r["scale"]).strip() not in ("", "nan", "None") else 1.0)
              for _, r in meta.iterrows()}
    readings: dict[tuple[str, str, dt.date], float] = {}
    for issuer in issuers:
        local = _load_local(issuer, raw_dir)
        mine: dict[tuple[str, str, dt.date], float] = {}
        for stem in sorted(local):                  # oldest document first, so the later filing overwrites
            for period_end, values in local[stem].items():
                for (entity, metric), value in values.items():
                    if (metric, entity) not in wanted:
                        continue                    # only series declared in crosswalks/series.csv are emitted
                    mine[(metric, entity, period_end)] = value * wanted[(metric, entity)]
        # require_consecutive is off only for the fixtures, which are deliberately a handful of awkward
        # months rather than a history: trimming them to a contiguous run would throw most of them away.
        readings.update(_trim_to_unbroken_run(issuer, mine) if require_consecutive else mine)
    rows = [{"metric": metric, "entity": entity, "entity_type": ENTITY_TYPE, "tier": "all",
             "period_end": period_end, "period_type": PERIOD_TYPE, "value": value,
             "source": SOURCE, "pulled_at": pulled_at}
            for (metric, entity, period_end), value in readings.items()]
    if not rows:
        raise ValueError("no monthly credit metrics documents were parsed")
    if require_consecutive:
        by_series: dict[tuple[str, str], set[dt.date]] = {}
        for metric, entity, period_end in readings:
            by_series.setdefault((entity, metric), set()).add(period_end)
        for (entity, metric), months in by_series.items():
            _require_consecutive(f"{entity} {metric}", sorted(months))
    return pd.DataFrame(rows)[FACT_COLUMNS]


def _trim_to_unbroken_run(issuer: Issuer, readings: dict[tuple[str, str, dt.date], float]
                          ) -> dict[tuple[str, str, dt.date], float]:
    """One issuer's series, each cut back to the unbroken run of months ending at its newest reading.

    Series here do not all start together, and that is fine: a chart can carry one line that begins in 2021
    and another that begins in 2023. What is NOT fine is a HOLE inside a line, because the page spans gaps and
    would draw straight through it as though the months in between had been measured.

    Bread is why this exists. Its exhibits print the month and the same month a year earlier, so a year of
    extra history arrives free from the year-ago columns, EXCEPT in the quarter-end months, where the loss
    table spends that column on the quarter instead. Its delinquency series is therefore unbroken back to July
    2022 while its loss series has four holes before July 2023. Rather than publish a line with holes in it,
    the loss series starts where it becomes continuous and the delinquency series keeps its longer history.

    THE TRIM IS BOUNDED, because a hole has two possible causes and only one of them is harmless. Before an
    issuer's first_month the readings are whatever its multi-month documents happened to reach back to, and a
    hole there means the source never published that month in a monthly form: trimming is right. From
    first_month on, every one of these issuers files every month, so a hole means a filing was MISSED, and
    silently shortening the series would hide the failure and quietly drop years of history. That raises.
    """
    months: dict[tuple[str, str], list[dt.date]] = {}
    for metric, entity, period_end in readings:
        months.setdefault((metric, entity), []).append(period_end)
    keep: set[tuple[str, str, dt.date]] = set()
    for (metric, entity), dates in months.items():
        tail = _contiguous_tail(dates)
        stranded = sorted(set(dates) - tail)
        late = [d for d in stranded if d >= issuer.first_month]
        if late:
            raise ValueError(
                f"{issuer.name} {entity} {metric}: a gap inside the series strands {late[0]} to {late[-1]} "
                f"before the run that starts {min(tail)}. This issuer files every month from "
                f"{issuer.first_month}, so that is a filing this run failed to read, not a month that was "
                f"never published")
        keep |= {(metric, entity, d) for d in tail}
    return {k: v for k, v in readings.items() if k in keep}


def _month_from_name(name: str) -> str | None:
    m = re.match(r"^ex99\d*([a-z]+)(\d{4})credit", name, re.I)
    if not m or m.group(1).lower() not in MONTHS:
        return None
    return f"{int(m.group(2)):04d}-{MONTHS.index(m.group(1).lower()) + 1:02d}"


def _require_consecutive(entity: str, months: list[dt.date]) -> None:
    """These issuers file every month. A hole means a filing was missed, not that a month did not happen."""
    for earlier, later in zip(months, months[1:]):
        if _step(earlier, later) != 1:
            raise ValueError(f"gap in {entity}'s monthly metrics between {earlier} and {later}")
