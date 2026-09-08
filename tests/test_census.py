"""Census retail trade fetcher: the checked-in workbook, both blocks, month labels, the advance-release numbers."""

from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import Workbook

from carddash.fetchers import census
from carddash.series import series_for_source
from conftest import CENSUS_FIXTURE, PULLED_AT


def test_advance_release_table_reproduces(census_facts):
    """Advance Monthly Sales, July 2026 (2026-08-14), Table 1, not adjusted: 454 May 134,767, Jun 138,020; 722 May 110,590."""
    nsa = census_facts[census_facts["metric"] == "retail_sales_nsa"]
    nonstore = nsa[nsa["entity"] == "NAICS:454"].set_index("period_end")["value"]
    assert abs(nonstore[pd.Timestamp("2026-05-31")] - 134.767) < 1e-6
    assert abs(nonstore[pd.Timestamp("2026-06-30")] - 138.020) < 1e-6
    food = nsa[nsa["entity"] == "NAICS:722"].set_index("period_end")["value"]
    assert abs(food[pd.Timestamp("2026-05-31")] - 110.590) < 1e-6
    sa_total = census_facts[(census_facts["metric"] == "retail_sales_sa") & (census_facts["entity"] == "NAICS:44X72")]
    assert abs(sa_total["value"].iloc[-1] - 768.072) < 1e-6 and sa_total["period_end"].max() == pd.Timestamp("2026-06-30")


def test_series_shape(census_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, census.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in census_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 12
    assert census_facts["entity_type"].unique().tolist() == ["naics"]
    counts = census_facts.groupby(["metric", "entity"])["period_end"].count()
    assert (counts == counts.iloc[0]).all() and counts.iloc[0] == 12 * 34 + 6  # Jan 1992 to Jun 2026
    assert census_facts["period_end"].min() == pd.Timestamp("1992-01-31")
    assert (census_facts["period_end"] == census_facts["period_end"] + pd.offsets.MonthEnd(0)).all()
    assert census_facts["pulled_at"].unique().tolist() == [PULLED_AT]


@pytest.mark.parametrize("label, expected", [
    ("Jan. 2026", (2026, 1)), ("May 2026", (2026, 5)), ("Jun. 2026(p)", (2026, 6)), ("Jun. 2026 (p)", (2026, 6)),
    ("CY CUM", None), ("TOTAL", None), ("Kind of Business", None),
])
def test_month_labels(label, expected):
    assert census.parse_month_label(label) == expected


def _sheet(months, block_rows):
    rows = [["Estimates of Monthly Retail and Food Services Sales by Kind of Business: 2020"],
            ["[Estimates are shown in millions of dollars]"], [],
            ["NAICS  Code", "Kind of Business"], ["", ""] + months]
    for block, items in block_rows:
        rows.append(["", block])
        rows.extend(items)
    return rows


def _workbook(tmp_path, sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    path = tmp_path / "mrtssales92-present.xlsx"
    wb.save(path)
    return path


def _items(v):
    return [["", "Retail and food services sales, total", v, v + 1], [445, "Food and beverage stores", 1, 1],
            [447, "Gasoline stations", 1, 1], [448, "Clothing", 1, 1], [454, "Nonstore retailers", 2, 2],
            [7224, "Drinking places", "(S)", "(S)"], [722, "Food services and drinking places", 3, 3]]


def test_synthetic_sheet_parses_both_blocks_and_ignores_unlisted_rows():
    rows = _sheet(["Jan. 2020", "Feb. 2020", "TOTAL"], [("NOT ADJUSTED", _items(100)), ("ADJUSTED(2)", _items(200))])
    recs = census.parse_sheet(rows, "t")
    assert len(recs) == 2 * 6 * 2  # two blocks, six rows, two months
    total_sa = [r for r in recs if r["metric"] == "retail_sales_sa" and r["entity"] == "NAICS:44X72"]
    assert [r["value"] for r in total_sa] == [200.0, 201.0]


def test_synthetic_sheet_fails_on_suppressed_missing_or_duplicate_rows():
    items = _items(100)
    items[4][2] = "(S)"
    with pytest.raises(ValueError, match="not a number"):
        census.parse_sheet(_sheet(["Jan. 2020"], [("NOT ADJUSTED", items), ("ADJUSTED(2)", _items(1))]), "t")
    with pytest.raises(ValueError, match="missing"):
        census.parse_sheet(_sheet(["Jan. 2020"], [("NOT ADJUSTED", _items(1)), ("ADJUSTED(2)", _items(1)[:-1])]), "t")
    with pytest.raises(ValueError, match="more than once"):
        census.parse_sheet(_sheet(["Jan. 2020"], [("NOT ADJUSTED", _items(1) + [[454, "Nonstore", 1]]), ("ADJUSTED(2)", _items(1))]), "t")


def test_release_requires_consecutive_months_from_1992(tmp_path):
    ok = {"1992": _sheet([f"{m}. 1992" if m != "May" else "May 1992" for m in
                          ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]],
                         [("NOT ADJUSTED", [r[:2] + [1.0] * 12 for r in _items(1)]), ("ADJUSTED(2)", [r[:2] + [1.0] * 12 for r in _items(1)])])}
    df = census.parse_release(_workbook(tmp_path, ok), PULLED_AT)
    assert len(df) == 12 * 12 and df["value"].unique().tolist() == [0.001]
    bad = {"1993": [[str(c).replace("1992", "1993") if isinstance(c, str) else c for c in r] for r in ok["1992"]]}
    with pytest.raises(ValueError, match="history starts"):
        census.parse_release(_workbook(tmp_path, bad), PULLED_AT)


def test_not_an_xlsx_is_rejected():
    assert not census.looks_like_xlsx(b"<!DOCTYPE html>")
    assert census.looks_like_xlsx(CENSUS_FIXTURE.read_bytes()[:4] + b"x")
