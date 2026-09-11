"""New York Fed State Level Household Debt Statistics: card debt per capita and card delinquency, by state, annual.

Publication facts, verified 2026-09-11:
- One workbook at https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/area_report_by_year.xlsx,
  about 170 KB, direct GET with the pipeline's own User-Agent, no gate, no date in the name. Re-published once a
  year (the Info sheet cites 'State Level Household Debt Statistics 2003-2025, February 2026'), so a new year shows
  only as a content diff under the canonical snapshot name, and the whole history is revised with it.
- Twelve sheets: population, then a balance-per-capita sheet and a 90+ delinquency sheet for each of auto, credit
  card, mortgage and student loan, plus total. Loaded here: `creditcard` (credit card debt balance per capita,
  dollars) and `creditcard_delinq` (percent of credit card balances 90 or more days delinquent). The other loan
  types are in the file and not loaded; the quarterly report already carries them for the nation.
- Layout, the transpose of the quarterly report: a header row whose first cell is 'state' and whose remaining
  cells are 'Q4_YYYY', then one row per area. Areas are the 50 states, DC, Puerto Rico and a national 'allUS' row.
  Readings are the fourth quarter of each year, so period_type is A and period_end is 31 December.
- Puerto Rico stops after 2016 in both card sheets. That is the only permitted gap and only at the end of a row
  (ENDED_AREAS): the series ends in 2016 rather than being carried forward, and its series.csv rows carry a
  max_age_days that never expires so a finished line does not turn the source stale.
- Scope, from the Info sheet: the panel is 5 percent of consumers aged 18 and over with an Equifax credit file
  (1 percent for student loans, which are not loaded here). US territories other than Puerto Rico are excluded and
  the figures are subject to sampling variation, so the NY Fed warns that 'national and state totals here may not
  match those reported in the Quarterly Report'. Measured against the quarterly workbook's Q4 readings the gap is
  at most 0.34 points across all 23 years.
- No release page states these numbers, so this source ships without a golden entry, like cfpb_cct. Three checks
  stand in for one. On every fetch: the area list must be exactly the expected 53, the years must run from 2003
  with no gap, and the national row must lie inside the range across areas (a transposed or shifted read breaks
  that immediately). In the tests, on the checked-in fixture: `check_against_quarterly` requires the national card
  delinquency row to track the Quarterly Report's own series to ALLUS_TOLERANCE. That comparison needs facts from
  another source, which a fetcher never sees, so it is a fixture test rather than a live assertion. Documented in
  CLAUDE.md.

Outputs: raw_dir/latest/area_report_by_year.xlsx byte for byte, and one facts row per area, metric and year.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pandas as pd

from ..schema import FACT_COLUMNS

SOURCE = "nyfed_state"
LANDING_URL = "https://www.newyorkfed.org/microeconomics/databank.html"
FILE_URL = "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/area_report_by_year.xlsx"
RAW_NAME = "area_report_by_year.xlsx"
XLSX_MAGIC = b"PK\x03\x04"
PERIOD_TYPE = "A"
FIRST_YEAR = 2003
NATIONAL_ROW = "allUS"
NATIONAL_ENTITY = "CCP_ALL"
ALLUS_TOLERANCE = 0.6  # points; the measured gap against the quarterly report is at most 0.34 over 2003-2025

# sheet -> (metric, what the Info sheet calls it)
SHEETS = {
    "creditcard": ("state_card_debt_per_capita", "Credit Card Debt Balance per Capita"),
    "creditcard_delinq": ("state_card_dq90_rate_balances", "Percent of Credit Card Debt Balance 90+ Days Delinquent"),
}

# The 50 states, DC and Puerto Rico. A new or missing area fails the parse rather than quietly changing the panel.
AREAS = (
    "AK AL AR AZ CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO MS MT NC ND NE NH NJ NM NV NY OH "
    "OK OR PA PR RI SC SD TN TX UT VA VT WA WI WV WY"
).split()

# Areas the NY Fed has stopped publishing. Their cells are empty from some year on, which is allowed only as a
# trailing gap: the series ends there rather than being carried forward, and series.csv gives it a max_age_days
# that never expires so the source does not turn stale over a line that is finished on purpose. Puerto Rico's last
# reading is 2016; every column after it is blank in both card sheets.
ENDED_AREAS = {"PR"}

_YEAR_RE = re.compile(r"^Q4_(\d{4})$")


def looks_like_xlsx(content: bytes) -> bool:
    return content[:4] == XLSX_MAGIC


def parse_year(label: str) -> int:
    """'Q4_2003' -> 2003."""
    m = _YEAR_RE.match(str(label).strip())
    if not m:
        raise ValueError(f"not a Q4_YYYY column: {label!r}")
    return int(m.group(1))


def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell).strip()) if cell is not None else ""


def _num(value, where: str) -> float:
    bad = ValueError(f"{where}: expected a number, got {value!r}")
    if value is None or isinstance(value, bool):
        raise bad
    try:
        x = float(value) if isinstance(value, (int, float)) else float(str(value).strip().replace(",", ""))
    except ValueError:
        raise bad from None
    if not math.isfinite(x):
        raise bad
    return x


def read_sheet(rows: list[tuple], name: str) -> pd.DataFrame:
    """One sheet's rows -> frame indexed by area with one column per year.

    The header is the first row whose column A is 'state'. Years must run from FIRST_YEAR without a gap, and the
    areas must be exactly AREAS plus the national row.
    """
    head = next((i for i, r in enumerate(rows) if r and _norm(r[0]).lower() == "state"), None)
    if head is None:
        raise ValueError(f"{name}: no header row with 'state' in column A")
    header = rows[head]
    years: list[tuple[int, int]] = []  # (column index, year)
    for j, cell in enumerate(header[1:], start=1):
        text = _norm(cell)
        if text:
            years.append((j, parse_year(text)))
    if not years:
        raise ValueError(f"{name}: header has no Q4_YYYY columns")
    got = [y for _, y in years]
    if got != list(range(FIRST_YEAR, FIRST_YEAR + len(got))):
        raise ValueError(f"{name}: years are {got[:3]}...{got[-1:]}, expected {FIRST_YEAR} onwards with no gap")
    data: dict[str, list[float]] = {}
    for i, row in enumerate(rows[head + 1 :], start=head + 2):
        area = _norm(row[0]) if row else ""
        if not area:
            continue
        if area in data:
            raise ValueError(f"{name} row {i}: area {area!r} appears twice")
        cells = [row[j] if j < len(row) else None for j, _ in years]
        if area in ENDED_AREAS:
            blank = [k for k, c in enumerate(cells) if not _norm(c)]
            if blank and blank != list(range(len(cells) - len(blank), len(cells))):
                raise ValueError(
                    f"{name}: {area} is empty in {[f'Q4_{years[k][1]}' for k in blank][:4]}, which is not a trailing "
                    "run; only an area that stops being published may have blanks, and only at the end"
                )
            if len(blank) == len(cells):
                raise ValueError(f"{name}: {area} has no readings at all")
        data[area] = [
            float("nan") if (area in ENDED_AREAS and not _norm(c)) else _num(c, f"{name} row {i}, {area}, Q4_{y}")
            for c, (_, y) in zip(cells, years)
        ]
    expected = set(AREAS) | {NATIONAL_ROW}
    if set(data) != expected:
        raise ValueError(
            f"{name}: areas are not the expected {len(expected)}; "
            f"unexpected {sorted(set(data) - expected)}, missing {sorted(expected - set(data))}"
        )
    index = pd.Index([pd.Timestamp(f"{y}-12-31") for _, y in years], name="period_end")
    return pd.DataFrame({area: pd.Series(values, index=index) for area, values in data.items()})


def check_national_row(frame: pd.DataFrame, name: str) -> None:
    """The national row must sit inside the range across areas, every year: the parse is transposed otherwise."""
    states = frame[list(AREAS)]
    national = frame[NATIONAL_ROW]
    below = national < states.min(axis=1) - 1e-9
    above = national > states.max(axis=1) + 1e-9
    if bool(below.any() or above.any()):
        bad = frame.index[below | above][0].date()
        raise ValueError(f"{name}: the {NATIONAL_ROW} row is outside the range across states in {bad}")


def parse_workbook(path: Path, pulled_at: str) -> pd.DataFrame:
    """The workbook -> facts for both card sheets, every area plus the national row."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        missing = [s for s in SHEETS if s not in wb.sheetnames]
        if missing:
            raise ValueError(f"{path.name}: no sheet(s) {missing}; sheets are {wb.sheetnames}")
        frames = []
        for sheet, (metric, _) in SHEETS.items():
            rows = [tuple(r) for r in wb[sheet].iter_rows(values_only=True)]
            wide = read_sheet(rows, f"{path.name}/{sheet}")
            check_national_row(wide, f"{path.name}/{sheet}")
            for area in list(AREAS) + [NATIONAL_ROW]:
                entity = NATIONAL_ENTITY if area == NATIONAL_ROW else f"STATE:{area}"
                frames.append(
                    pd.DataFrame(
                        {
                            "metric": metric,
                            "entity": entity,
                            "entity_type": "aggregate" if area == NATIONAL_ROW else "state",
                            "tier": "all",
                            "period_end": wide.index,
                            "period_type": PERIOD_TYPE,
                            "value": wide[area].to_numpy(),
                            "source": SOURCE,
                            "pulled_at": pulled_at,
                        }
                    )[FACT_COLUMNS].dropna(subset=["value"])  # an ENDED_AREAS row simply stops
                )
        return pd.concat(frames, ignore_index=True)
    finally:
        wb.close()


def check_against_quarterly(facts: pd.DataFrame, quarterly: pd.DataFrame) -> None:
    """The national card delinquency row must track the Quarterly Report's own Q4 readings.

    The two come from different draws of the same panel and the NY Fed warns they need not match, so this is a
    tolerance check that the sheet was read the right way round, not a golden. A silent transpose, a shifted
    column or a percent/dollar mix-up all break it by far more than ALLUS_TOLERANCE.
    """
    mine = facts[(facts["metric"] == "state_card_dq90_rate_balances") & (facts["entity"] == NATIONAL_ENTITY)]
    mine = mine.set_index("period_end")["value"]
    theirs = quarterly[
        (quarterly["metric"] == "hhdc_card_dq90_rate_balances") & (quarterly["entity"] == "CCP_ALL")
    ].set_index("period_end")["value"]
    shared = mine.index.intersection(theirs.index)
    if len(shared) < 10:
        raise ValueError(f"only {len(shared)} year-ends overlap the quarterly report; expected the whole history")
    gap = (mine[shared] - theirs[shared]).abs()
    if gap.max() > ALLUS_TOLERANCE:
        worst = gap.idxmax().date()
        raise ValueError(
            f"the national card delinquency row is {gap.max():.2f} points from the Quarterly Report in {worst}, "
            f"above the {ALLUS_TOLERANCE} point tolerance"
        )


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    resp = session.get(FILE_URL)
    resp.raise_for_status()
    content = resp.content
    if not looks_like_xlsx(content):
        raise ValueError(f"{FILE_URL}: not an xlsx (first bytes {content[:40]!r})")
    latest = raw_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    dest = latest / RAW_NAME
    dest.write_bytes(content)
    facts = parse_workbook(dest, pulled_at)
    cols = ["metric", "entity", "tier", "period_type"]
    got = {tuple(r) for r in facts[cols].drop_duplicates().itertuples(index=False, name=None)}
    listed = {tuple(r) for r in meta[cols].itertuples(index=False, name=None)}
    if got != listed:
        raise ValueError(
            f"series in the file but not in series.csv: {sorted(got - listed)[:5]}; "
            f"listed but not in the file: {sorted(listed - got)[:5]}"
        )
    return facts
