"""NY Fed monthly SCE fetcher: the checked-in workbook, the four sheets, month continuity, the release-page numbers."""

from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import Workbook

from carddash.fetchers import nyfed_sce_monthly as sce
from carddash.series import series_for_source
from conftest import PULLED_AT, SCE_MONTHLY_FIXTURE


def test_every_series_is_in_the_crosswalk_and_nothing_else(sce_monthly_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, sce.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in sce_monthly_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 36


def test_release_page_numbers_reproduce(sce_monthly_facts):
    """Press release of 2026-09-08: 'increased by 1.2 percentage points to 13.2%'."""
    s = sce_monthly_facts[(sce_monthly_facts["metric"] == "sce_miss_payment_prob") & (sce_monthly_facts["entity"] == "SCE_ALL")]
    s = s.set_index("period_end")["value"]
    assert abs(s[pd.Timestamp("2026-08-31")] - 13.2) < 0.05
    assert abs(s[pd.Timestamp("2026-07-31")] - 12.0) < 0.05
    assert abs((s[pd.Timestamp("2026-08-31")] - s[pd.Timestamp("2026-07-31")]) - 1.2) < 0.05
    assert s.index.min() == pd.Timestamp("2013-06-30") and len(s) == 159
    assert (s.index == s.index + pd.offsets.MonthEnd(0)).all()


def test_demographics_and_answer_shares(sce_monthly_facts):
    aug = sce_monthly_facts[sce_monthly_facts["period_end"] == pd.Timestamp("2026-08-31")]
    miss = aug[aug["metric"] == "sce_miss_payment_prob"]
    by_income = miss[miss["entity_type"] == "income"].set_index("entity")["value"]
    assert by_income["INCOME:LT50K"] > by_income["INCOME:GT100K"]  # lower income, higher stated probability
    assert set(miss["entity_type"]) == {"aggregate", "age", "education", "income", "numeracy", "region"}
    for prefix in ("sce_credit_year_ago", "sce_credit_year_ahead", "sce_finances_year_ago", "sce_finances_year_ahead"):
        shares = aug[aug["metric"].str.startswith(prefix)]["value"]
        assert len(shares) == 5 and abs(shares.sum() - 100) < 0.5, prefix
    harder = aug[aug["metric"].isin(["sce_credit_year_ago_much_harder", "sce_credit_year_ago_somewhat_harder"])]["value"].sum()
    assert 40 < harder < 50  # August 2026: 15.3 + 30.6


def test_facts_shape(sce_monthly_facts):
    assert sce_monthly_facts["source"].unique().tolist() == [sce.SOURCE]
    assert sce_monthly_facts["period_type"].unique().tolist() == ["M"]
    assert sce_monthly_facts["tier"].unique().tolist() == ["all"]
    assert not sce_monthly_facts.duplicated(["metric", "entity", "period_end"]).any()
    assert sce_monthly_facts["pulled_at"].unique().tolist() == [PULLED_AT]


def _workbook(tmp_path, rows_by_sheet):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in rows_by_sheet.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    path = tmp_path / "frbny-sce-data.xlsx"
    wb.save(path)
    return path


def _single(months):
    return [["Source: SCE"], ["Debt delinquency expectations"], [],
            ["Mean probability of missing minimum debt payment over the next three months"]] + [[m, 10.0] for m in months]


def test_a_missing_month_fails(tmp_path):
    path = _workbook(tmp_path, {"Delinquency expectations": _single([201306, 201307, 201309])})
    with pytest.raises(ValueError, match="months jump"):
        sce.parse_sheet(path, "Delinquency expectations")


def test_a_renamed_title_or_column_fails(tmp_path):
    rows = _single([201306, 201307])
    rows[3] = ["Something else"]
    path = _workbook(tmp_path, {"Delinquency expectations": rows})
    with pytest.raises(ValueError, match="title"):
        sce.parse_sheet(path, "Delinquency expectations")
    demo = [["Source"], ["Debt delinquency expectations"], ["by Demographics"],
            ["Age Under 40", "Age 40-60", "Age Over 60", "Something"], [201306] + [1.0] * 4]
    path = _workbook(tmp_path, {"Delinquency expectations Demo": demo})
    with pytest.raises(ValueError, match="unknown demographic column"):
        sce.parse_sheet(path, "Delinquency expectations Demo")


def test_horizon_header_is_carried_across_its_block(tmp_path):
    answers = ["Much harder", "Somewhat harder", "Equally easy/hard", "Somewhat easier", "Much easier"]
    rows = [["Source"], ["Change in credit availability"], [None, "Year ago", None, None, None, None, "Year ahead"],
            [None] + answers + answers, [201306] + [10.0, 20.0, 40.0, 25.0, 5.0] + [1.0, 2.0, 94.0, 2.0, 1.0]]
    path = _workbook(tmp_path, {"Credit availability": rows})
    df = sce.parse_sheet(path, "Credit availability").set_index("metric")["value"]
    assert df["sce_credit_year_ago_much_harder"] == 10.0 and df["sce_credit_year_ahead_same"] == 94.0
    assert len(df) == 10


def test_not_an_xlsx_is_rejected():
    assert not sce.looks_like_xlsx(b"<!DOCTYPE html>")
    assert sce.looks_like_xlsx(SCE_MONTHLY_FIXTURE.read_bytes()[:4] + b"x")
