"""FRED fetcher: parsing checked-in raw files, period mapping, scaling, missing values."""

from __future__ import annotations

import pandas as pd

import datetime as dt

import pytest

from carddash.fetchers import fred
from carddash.schema import shift_period
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


def test_sloos_is_dated_to_the_quarter_it_asks_about(meta):
    """FRED dates the July 2026 survey to 2026-07-01 (Q3); it asks about April to June, so it lands on 2026-06-30."""
    obs = fred.parse_fredgraph((FIXTURES / "fred" / "DRTSCLCC.csv").read_text())
    row = _row(meta, "DRTSCLCC")
    assert int(row["period_offset"]) == -1
    assert obs[obs["date"] == pd.Timestamp("2026-07-01")]["value"].iloc[0] == 6.7
    facts = fred.to_facts(obs, row, PULLED_AT).set_index("period_end")["value"]
    assert facts[pd.Timestamp("2026-06-30")] == 6.7
    assert facts[pd.Timestamp("2026-03-31")] == 2.0
    assert facts[pd.Timestamp("1995-12-31")] == 25.0  # the January 1996 survey describes 1995 Q4
    assert facts.index.max() == pd.Timestamp("2026-06-30")  # nothing dated to a quarter that has not ended
    assert (facts.index == facts.index + pd.offsets.QuarterEnd(0)).all()
    # every other FRED series has no offset and keeps its dating
    assert int((meta.loc[meta["source"] == "fred", "period_offset"] != 0).sum()) == 1


@pytest.mark.parametrize(
    "date, pt, n, expected",
    [
        (dt.date(2026, 7, 1), "Q", -1, dt.date(2026, 6, 30)),
        (dt.date(2026, 9, 30), "Q", -1, dt.date(2026, 6, 30)),
        (dt.date(2026, 1, 15), "Q", -1, dt.date(2025, 12, 31)),
        (dt.date(2026, 1, 15), "Q", 0, dt.date(2026, 3, 31)),
        (dt.date(2026, 1, 15), "Q", 2, dt.date(2026, 9, 30)),
        (dt.date(2026, 11, 3), "M", 2, dt.date(2027, 1, 31)),
        (dt.date(2025, 12, 31), "H", -1, dt.date(2025, 6, 30)),
        (dt.date(2026, 5, 1), "T", 1, dt.date(2026, 12, 31)),
        (dt.date(2026, 5, 1), "A", -2, dt.date(2024, 12, 31)),
        (dt.date(2026, 8, 26), "W", -1, dt.date(2026, 8, 19)),
        (dt.date(2026, 8, 26), "D", 3, dt.date(2026, 8, 29)),
    ],
)
def test_shift_period(date, pt, n, expected):
    assert shift_period(date, pt, n) == expected


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


def test_api_json_snapshot_is_date_independent():
    a = '{"realtime_start":"2026-09-07","realtime_end":"2026-09-07","count":1,"observations":[{"realtime_start":"2026-09-07","realtime_end":"2026-09-07","date":"2020-01-01","value":"1.5"}]}'
    b = a.replace("2026-09-07", "2026-09-08")
    assert fred.normalize_api_json(a) == fred.normalize_api_json(b)
    assert '"date":"2020-01-01","value":"1.5"' in fred.normalize_api_json(a)
    assert fred.parse_api_json(fred.normalize_api_json(a))["value"].tolist() == [1.5]
    # the same number formatted two ways by different FRED servers must snapshot identically
    c = a.replace('"1.5"', '"1247630.0500000000"')
    d = a.replace('"1.5"', '"1247630.05"')
    assert fred.normalize_api_json(c) == fred.normalize_api_json(d)
    assert '"value":"1247630.05"' in fred.normalize_api_json(c)
    assert '"value":"1000"' in fred.normalize_api_json(a.replace('"1.5"', '"1000.000"'))  # no scientific notation
    assert '"value":"."' in fred.normalize_api_json(a.replace('"1.5"', '"."'))


def test_api_json_parse():
    text = '{"observations":[{"date":"2020-01-01","value":"1.5"},{"date":"2020-02-01","value":"."}]}'
    obs = fred.parse_api_json(text)
    assert obs["value"].tolist() == [1.5]
    assert obs["date"].iloc[0] == pd.Timestamp("2020-01-01")
