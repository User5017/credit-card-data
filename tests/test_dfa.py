"""DFA fetcher: the checked-in zip, the Z.1 reconciliation, agreement of the three splits, synthetic failures."""

from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from carddash.fetchers import dfa
from carddash.series import series_for_source
from conftest import DFA_FIXTURE, PULLED_AT


def test_zip_reproduces_the_z1_household_balance_sheet(dfa_facts):
    """Z.1 release of 2026-06-11, table S14.b (Balance sheet of households): consumer credit 4,988.2 (2023), 4,948.1
    (2024), 5,099.4 (2025); deposits are checkable deposits and currency 5,351.7 plus time deposits 8,780.2 (2025)."""
    tot = dfa_facts[dfa_facts["entity"] == "DFA_ALL_HOUSEHOLDS"].pivot(index="period_end", columns="metric", values="value")
    assert abs(tot.loc[pd.Timestamp("2023-12-31"), "dfa_consumer_credit"] - 4988.2) < 0.06
    assert abs(tot.loc[pd.Timestamp("2024-12-31"), "dfa_consumer_credit"] - 4948.1) < 0.06
    assert abs(tot.loc[pd.Timestamp("2025-12-31"), "dfa_consumer_credit"] - 5099.4) < 0.06
    assert abs(tot.loc[pd.Timestamp("2025-12-31"), "dfa_deposits"] - (5351.7 + 8780.2)) < 0.06
    assert abs(tot.loc[pd.Timestamp("2025-12-31"), "dfa_net_worth"] - 174003.5) < 0.06  # S14.b line 27


def test_series_shape_and_split_agreement(dfa_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, dfa.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in dfa_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 80
    assert set(dfa_facts["entity_type"]) == {"aggregate", "wealth", "income", "age"}
    assert dfa_facts["period_end"].min() == pd.Timestamp("1989-09-30")
    assert dfa_facts["period_end"].max() == pd.Timestamp("2026-03-31")
    assert (dfa_facts["period_end"] == dfa_facts["period_end"] + pd.offsets.QuarterEnd(0)).all()
    assert dfa_facts["pulled_at"].unique().tolist() == [PULLED_AT]
    assert dfa_facts.groupby(["metric", "entity"]).size().nunique() == 1  # 147 quarters for every series
    total = dfa_facts[dfa_facts["entity"] == "DFA_ALL_HOUSEHOLDS"].set_index(["metric", "period_end"])["value"]
    for etype in ("wealth", "income", "age"):
        s = dfa_facts[dfa_facts["entity_type"] == etype].groupby(["metric", "period_end"])["value"].sum()
        rel = ((s - total.loc[s.index]).abs() / total.loc[s.index].abs()).max()
        assert rel < dfa.TOTAL_TOLERANCE, etype


def test_bottom_half_reading(dfa_facts):
    """2026 Q1: the bottom half of households by wealth owes $2,626.6bn of consumer credit against $793.2bn of
    deposits and $4,266.4bn of net worth (millions in the file, billions here); 67.6 million households."""
    b = dfa_facts[(dfa_facts["entity"] == "WEALTH:BOTTOM50") & (dfa_facts["period_end"] == pd.Timestamp("2026-03-31"))]
    b = b.set_index("metric")["value"]
    assert abs(b["dfa_consumer_credit"] - 2626.591) < 1e-6
    assert abs(b["dfa_deposits"] - 793.232) < 1e-6
    assert abs(b["dfa_net_worth"] - 4266.359) < 1e-6
    assert abs(b["dfa_households"] - 67.573814) < 1e-6


HEADER = "Date,Category,Consumer credit,Deposits,Liabilities,Net worth,Household count"


def _csv(name: str, quarters: list[str], total: float = 1200.0, skip: tuple[str, str] | None = None,
         extra_group: str | None = None) -> str:
    groups = list(dfa.SPLITS[name]) + ([extra_group] if extra_group else [])
    share = total / len(groups)
    lines = [HEADER]
    for g in groups:
        for q in quarters:
            if skip == (g, q):
                continue
            lines.append(f"{q},{g},{share},{2 * share},{3 * share},{10 * share},{share}")
    return "\n".join(lines) + "\n"


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


QUARTERS = ["1989:Q3", "1989:Q4", "1990:Q1"]


def _files(**overrides) -> dict[str, str]:
    files = {name: _csv(name, QUARTERS) for name in dfa.SPLITS}
    files.update(overrides)
    return files


def test_synthetic_zip_scales_sums_and_fails_loudly(meta):
    mine = series_for_source(meta, dfa.SOURCE)
    df = dfa.parse_zip(_zip(_files()), mine, PULLED_AT)
    assert len(df) == 16 * 5 * 3  # 15 groups plus the sum, five metrics, three quarters
    b50 = df[(df["entity"] == "WEALTH:BOTTOM50") & (df["period_end"] == pd.Timestamp("1989-09-30"))].set_index("metric")["value"]
    assert b50["dfa_consumer_credit"] == pytest.approx(0.24)  # 240 million -> billions
    assert b50["dfa_households"] == pytest.approx(240e-6)  # 240 households -> millions
    tot = df[(df["entity"] == "DFA_ALL_HOUSEHOLDS") & (df["metric"] == "dfa_consumer_credit")]["value"]
    assert tot.tolist() == pytest.approx([1.2, 1.2, 1.2])
    with pytest.raises(ValueError, match="quarters jump"):
        dfa.parse_zip(_zip(_files(**{"dfa-age-levels-detail.csv": _csv("dfa-age-levels-detail.csv", QUARTERS, skip=("age70plus", "1989:Q4"))})), mine, PULLED_AT)
    with pytest.raises(ValueError, match="unknown Category"):
        dfa.parse_zip(_zip(_files(**{"dfa-income-levels-detail.csv": _csv("dfa-income-levels-detail.csv", QUARTERS, extra_group="pct100plus")})), mine, PULLED_AT)
    with pytest.raises(ValueError, match="totals differ"):
        dfa.parse_zip(_zip(_files(**{"dfa-income-levels-detail.csv": _csv("dfa-income-levels-detail.csv", QUARTERS, total=1300.0)})), mine, PULLED_AT)
    with pytest.raises(ValueError, match="end on different quarters"):
        dfa.parse_zip(_zip(_files(**{"dfa-age-levels-detail.csv": _csv("dfa-age-levels-detail.csv", QUARTERS, skip=("age70plus", "1990:Q1"))})), mine, PULLED_AT)
    with pytest.raises(ValueError, match="starts at"):
        dfa.parse_zip(_zip(_files(**{"dfa-age-levels-detail.csv": _csv("dfa-age-levels-detail.csv", QUARTERS[1:])})), mine, PULLED_AT)
    with pytest.raises(ValueError, match="lacks"):
        dfa.parse_zip(_zip({k: v for k, v in _files().items() if "income" not in k}), mine, PULLED_AT)
    with pytest.raises(ValueError, match="not a zip"):
        dfa.parse_zip(b"<!DOCTYPE html>", mine, PULLED_AT)
    with pytest.raises(ValueError, match="header lacks"):
        bad = _csv("dfa-age-levels-detail.csv", QUARTERS).replace("Consumer credit", "Consumer loans")
        dfa.parse_zip(_zip(_files(**{"dfa-age-levels-detail.csv": bad})), mine, PULLED_AT)


def test_a_group_missing_from_the_crosswalk_fails(meta):
    mine = series_for_source(meta, dfa.SOURCE)
    short = mine[mine["entity"] != "AGE:70PLUS"]
    with pytest.raises(ValueError, match="not in series.csv"):
        dfa.parse_zip(_zip(_files()), short, PULLED_AT)


def test_fixture_is_the_real_zip_trimmed():
    with zipfile.ZipFile(DFA_FIXTURE) as z:
        assert set(dfa.SPLITS) <= set(z.namelist())
