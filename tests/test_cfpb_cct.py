"""CFPB Consumer Credit Trends fetcher: the checked-in files, the dashboard readings, group sums, synthetic failures."""

from __future__ import annotations

import pandas as pd
import pytest

from carddash.fetchers import cfpb_cct as cct
from carddash.series import series_for_source
from conftest import PULLED_AT


def test_files_reproduce_the_dashboard_readings(cct_facts):
    """The CFPB dashboard for January 2026: 8.2 million cards opened (8,165,250 unadjusted), $54.2bn of credit lines
    on them, 19.4 percent more cards than a year earlier (seasonally adjusted); the inquiry index runs to May 2026."""
    nsa = cct_facts[cct_facts["metric"] == "cct_card_originations_nsa"].set_index("period_end")["value"]
    assert abs(nsa[pd.Timestamp("2026-01-31")] - 8.16525) < 1e-4
    sa = cct_facts[cct_facts["metric"] == "cct_card_originations_sa"].set_index("period_end")["value"]
    assert 1.15 < sa[pd.Timestamp("2026-01-31")] / sa[pd.Timestamp("2025-01-31")] < 1.25  # the dashboard's 19.4% is its own yoy file's basis
    lines = cct_facts[(cct_facts["metric"] == "cct_card_new_lines_nsa") & (cct_facts["tier"] == "all")
                      & (cct_facts["entity"] == cct.ENTITY_ALL)].set_index("period_end")["value"]
    assert abs(lines[pd.Timestamp("2026-01-31")] - 54.222) < 0.001
    inq = cct_facts[cct_facts["metric"] == "cct_card_inquiry_index_sa"]
    assert inq["period_end"].max() == pd.Timestamp("2026-05-31") and abs(inq["value"].iloc[-1] - 224.30686) < 1e-4


def test_series_shape(cct_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, cct.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in cct_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 26
    assert set(cct_facts["entity_type"]) == {"aggregate", "age"}
    assert set(cct_facts["tier"]) == {"all", "deep_subprime", "subprime", "near_prime", "prime", "superprime"}
    assert (cct_facts["period_end"] == cct_facts["period_end"] + pd.offsets.MonthEnd(0)).all()
    assert cct_facts["pulled_at"].unique().tolist() == [PULLED_AT]
    first = cct_facts.groupby("metric")["period_end"].min()
    assert first["cct_card_originations_sa"] == pd.Timestamp("2006-01-31")
    assert first["cct_card_inquiry_index_sa"] == pd.Timestamp("2005-01-31")
    by_tier = cct_facts[(cct_facts["metric"] == "cct_card_new_lines_sa") & (cct_facts["tier"] != "all")]
    assert by_tier["period_end"].min() == pd.Timestamp("2007-01-31")


def test_score_and_age_groups_sum_close_to_the_total(cct_facts):
    """The groups are scaled and adjusted separately: within 5 percent of the total for score, 3 for age (SA)."""
    lines = cct_facts[cct_facts["metric"] == "cct_card_new_lines_sa"]
    total = lines[(lines["entity"] == cct.ENTITY_ALL) & (lines["tier"] == "all")].set_index("period_end")["value"]
    tiers = lines[(lines["entity"] == cct.ENTITY_ALL) & (lines["tier"] != "all")].groupby("period_end")["value"].sum()
    ages = lines[lines["entity_type"] == "age"].groupby("period_end")["value"].sum()
    assert ((tiers - total.loc[tiers.index]).abs() / total.loc[tiers.index]).max() < 0.05
    assert ((ages - total.loc[ages.index]).abs() / total.loc[ages.index]).max() < 0.035
    jan = lines[(lines["period_end"] == pd.Timestamp("2026-01-31")) & (lines["tier"] == "superprime")]["value"].iloc[0]
    assert 0.80 < jan / tiers[pd.Timestamp("2026-01-31")] < 0.84  # superprime borrowers get about four fifths of new lines


def _month_rows(months: list[str], value: float, group: str | None = None) -> list[str]:
    out = []
    for m in months:
        y, mo = int(m[:4]), int(m[5:])
        idx = cct.month_index(y, mo)
        out.append(f"{idx},{m},{value},{value * 0.9}" + (f",{group}" if group else ""))
    return out


def _texts(months=("2020-01", "2020-02", "2020-03"), score_total=1000.0, age_total=1000.0, total=1000.0,
           skip_month: str | None = None, extra_group: str | None = None, bad_index: bool = False) -> dict[str, str]:
    months = list(months)
    num = ["month,date,num,num_unadj"] + _month_rows(months, 5.0)
    vol = ["month,date,vol,vol_unadj"] + _month_rows([m for m in months if m != skip_month], total)
    score = ["month,date,vol,vol_unadj,credit_score_group"]
    groups = list(cct.SCORE_GROUPS) + ([extra_group] if extra_group else [])
    for g in groups:
        score += _month_rows(months, score_total / len(cct.SCORE_GROUPS), g)
    age = ["month,date,vol,vol_unadj,age_group"]
    for g in cct.AGE_GROUPS:
        age += _month_rows(months, age_total / len(cct.AGE_GROUPS), g)
    inq = ["month,date,inquiry_index,unadjusted_inquiry_index"] + _month_rows(months, 150.0)
    crt = ["month,date,tightness_index,unadjusted_credit_tightness_index"] + _month_rows(months, 70.0)
    if bad_index:
        crt[1] = "999" + crt[1][crt[1].index(","):]
    return {
        "num_data_CRC.csv": "\n".join(num) + "\n",
        "vol_data_CRC.csv": "\n".join(vol) + "\n",
        "volume_data_Score_Level_CRC.csv": "\n".join(score) + "\n",
        "volume_data_Age_Group_CRC.csv": "\n".join(age) + "\n",
        "inq_data_CRC.csv": "\n".join(inq) + "\n",
        "crt_data_CRC.csv": "\n".join(crt) + "\n",
    }


def test_synthetic_files_scale_and_fail_loudly(meta):
    mine = series_for_source(meta, cct.SOURCE)
    df = cct.parse_files(_texts(), mine, PULLED_AT)
    assert len(df) == 26 * 3
    jan = df[df["period_end"] == pd.Timestamp("2020-01-31")].set_index(["metric", "entity", "tier"])["value"]
    assert jan[("cct_card_originations_sa", cct.ENTITY_ALL, "all")] == pytest.approx(5e-6)  # 5 cards -> millions
    assert jan[("cct_card_originations_nsa", cct.ENTITY_ALL, "all")] == pytest.approx(4.5e-6)
    assert jan[("cct_card_new_lines_sa", cct.ENTITY_ALL, "all")] == pytest.approx(1e-6)  # 1000 dollars -> billions
    assert jan[("cct_card_new_lines_sa", cct.ENTITY_ALL, "prime")] == pytest.approx(0.2e-6)
    assert jan[("cct_card_new_lines_sa", "AGE:LT30", "all")] == pytest.approx(0.25e-6)
    assert jan[("cct_card_tightness_sa", cct.ENTITY_ALL, "all")] == pytest.approx(70.0)
    with pytest.raises(ValueError, match="months jump"):
        cct.parse_files(_texts(months=("2020-01", "2020-03")), mine, PULLED_AT)
    with pytest.raises(ValueError, match="does not match date"):
        cct.parse_files(_texts(bad_index=True), mine, PULLED_AT)
    with pytest.raises(ValueError, match="unknown credit_score_group"):
        cct.parse_files(_texts(extra_group="Ultra-prime"), mine, PULLED_AT)
    with pytest.raises(ValueError, match="groups sum to"):
        cct.parse_files(_texts(score_total=1100.0), mine, PULLED_AT)
    with pytest.raises(ValueError, match="months the total file"):
        cct.parse_files(_texts(skip_month="2020-03"), mine, PULLED_AT)
    with pytest.raises(ValueError, match="missing files"):
        cct.parse_files({k: v for k, v in _texts().items() if k != "inq_data_CRC.csv"}, mine, PULLED_AT)
    with pytest.raises(ValueError, match="header lacks"):
        texts = _texts()
        texts["num_data_CRC.csv"] = texts["num_data_CRC.csv"].replace("num_unadj", "num_nsa")
        cct.parse_files(texts, mine, PULLED_AT)
    with pytest.raises(ValueError, match="not in series.csv"):
        cct.parse_files(_texts(), mine[mine["entity"] != "AGE:65PLUS"], PULLED_AT)


def test_month_helpers():
    assert cct.parse_month("2026-01") == (2026, 1) and cct.month_index(2006, 1) == 72 and cct.month_index(2026, 1) == 312
    assert cct.next_month(2025, 12) == (2026, 1)
    with pytest.raises(ValueError, match="not a YYYY-MM"):
        cct.parse_month("2026-13")
