"""FRED fetcher: parsing checked-in raw files, period mapping, scaling, missing values."""

from __future__ import annotations

import pandas as pd

from carddash.fetchers import fred
from conftest import FIXTURES, PULLED_AT


def _row(meta, sid):
    return meta[(meta["source"] == "fred") & (meta["source_id"] == sid)].iloc[0]


def test_revolsl_month_end_and_billions(meta):
    obs = fred.parse_fredgraph((FIXTURES / "fred" / "REVOLSL.csv").read_text())
    facts = fred.to_facts(obs, _row(meta, "REVOLSL"), PULLED_AT)
    assert facts["metric"].unique().tolist() == ["revolving_credit_sa"]
    assert facts["period_type"].unique().tolist() == ["M"]
    assert facts["entity_type"].unique().tolist() == ["aggregate"]
    june = facts[facts["period_end"] == pd.Timestamp("2026-06-30")]
    assert len(june) == 1
    # G.19 release table (2026-08-07) shows June 2026 revolving, SA = $1,351.1 bn
    assert abs(june["value"].iloc[0] - 1351.1) < 0.06
    # every period_end is a month end
    assert (facts["period_end"] == facts["period_end"] + pd.offsets.MonthEnd(0)).all()


def test_quarterly_survey_month_maps_to_quarter_end(meta):
    obs = fred.parse_fredgraph((FIXTURES / "fred" / "TERMCBCCALLNS.csv").read_text())
    facts = fred.to_facts(obs, _row(meta, "TERMCBCCALLNS"), PULLED_AT)
    q2 = facts[facts["period_end"] == pd.Timestamp("2026-06-30")]
    assert len(q2) == 1 and abs(q2["value"].iloc[0] - 20.94) < 0.006
    q1 = facts[facts["period_end"] == pd.Timestamp("2026-03-31")]
    assert len(q1) == 1 and abs(q1["value"].iloc[0] - 21.00) < 0.006
    # blank padding rows (non-survey months) must not become facts
    assert not facts.duplicated(["period_end"]).any()


def test_weekly_keeps_its_date(meta):
    obs = fred.parse_fredgraph((FIXTURES / "fred" / "CCLACBW027SBOG.csv").read_text())
    facts = fred.to_facts(obs, _row(meta, "CCLACBW027SBOG"), PULLED_AT)
    assert facts["period_type"].unique().tolist() == ["W"]
    last = facts.sort_values("period_end").iloc[-1]
    assert last["period_end"] == pd.Timestamp("2026-08-26")
    assert abs(last["value"] - 1089.7912) < 1e-6
    assert last["period_end"].day_name() == "Wednesday"


def test_missing_markers_are_dropped():
    text = "observation_date,X\n2020-01-01,1.5\n2020-02-01,.\n2020-03-01,\n2020-04-01,2.5\n"
    obs = fred.parse_fredgraph(text)
    assert obs["value"].tolist() == [1.5, 2.5]


def test_fredgraph_rejects_html():
    try:
        fred.parse_fredgraph("<!DOCTYPE html><html></html>")
    except ValueError:
        return
    raise AssertionError("expected ValueError on HTML input")


def test_api_json_parse():
    text = '{"observations":[{"date":"2020-01-01","value":"1.5"},{"date":"2020-02-01","value":"."}]}'
    obs = fred.parse_api_json(text)
    assert obs["value"].tolist() == [1.5]
    assert obs["date"].iloc[0] == pd.Timestamp("2020-01-01")
