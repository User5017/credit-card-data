"""NY Fed state-level fetcher: the checked-in workbook, the cross-source check that stands in for a golden,
Puerto Rico's trailing gap, and the format changes that must fail."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from openpyxl import Workbook

from carddash.fetchers import nyfed_state as st
from carddash.loader import validate
from carddash.schema import SERIES_KEY, coerce_facts
from carddash.series import series_for_source
from conftest import PULLED_AT, STATE_FIXTURE

YEARS = list(range(2003, 2026))


def test_national_row_tracks_the_quarterly_report(state_facts, hhdc_facts):
    """No release page states these numbers, so this is what stands in for a golden: the state file's own national
    row must follow the Quarterly Report's card delinquency series, which comes from a separate draw of the same
    panel. A transposed read, a shifted column or a unit mix-up all break it by far more than the tolerance."""
    st.check_against_quarterly(state_facts, hhdc_facts)
    mine = state_facts[
        (state_facts["metric"] == "state_card_dq90_rate_balances") & (state_facts["entity"] == "CCP_ALL")
    ].set_index("period_end")["value"]
    theirs = hhdc_facts[
        (hhdc_facts["metric"] == "hhdc_card_dq90_rate_balances") & (hhdc_facts["entity"] == "CCP_ALL")
    ].set_index("period_end")["value"]
    shared = mine.index.intersection(theirs.index)
    assert len(shared) == 23  # Q4 of 2003 through 2025
    assert (mine[shared] - theirs[shared]).abs().max() == pytest.approx(0.342, abs=0.002)


def test_series_shape(state_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, st.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in state_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 106  # 52 areas plus the national row, two metrics
    assert set(state_facts["entity_type"]) == {"aggregate", "state"}
    assert (state_facts["period_type"] == "A").all()
    assert (state_facts["period_end"].dt.month == 12).all() and (state_facts["period_end"].dt.day == 31).all()
    assert state_facts["period_end"].min() == pd.Timestamp("2003-12-31")
    assert state_facts["period_end"].max() == pd.Timestamp("2025-12-31")
    assert state_facts["pulled_at"].unique().tolist() == [PULLED_AT]
    errors, warnings = validate(state_facts, meta[meta["source"] == st.SOURCE])
    assert errors == [] and warnings == []
    assert state_facts.groupby(SERIES_KEY).ngroups == 106


def test_puerto_rico_ends_in_2016_and_every_other_area_is_complete(state_facts):
    pr = state_facts[state_facts["entity"] == "STATE:PR"]
    assert pr["period_end"].max() == pd.Timestamp("2016-12-31")
    assert len(pr) == 2 * 14  # 2003 through 2016, two metrics
    others = state_facts[state_facts["entity"] != "STATE:PR"]
    assert (others.groupby(["metric", "entity"])["period_end"].count() == len(YEARS)).all()


def test_headline_values(state_facts):
    """Read independently from the workbook at fixed positions: AK 2003 and the national row."""
    def v(metric, entity, year):
        sel = state_facts[(state_facts["metric"] == metric) & (state_facts["entity"] == entity)
                          & (state_facts["period_end"] == pd.Timestamp(f"{year}-12-31"))]
        assert len(sel) == 1
        return float(sel["value"].iloc[0])

    assert v("state_card_debt_per_capita", "STATE:AK", 2003) == 4260
    assert v("state_card_dq90_rate_balances", "STATE:AK", 2003) == pytest.approx(5.21)
    assert v("state_card_dq90_rate_balances", "STATE:AL", 2003) == pytest.approx(11.92)
    assert v("state_card_dq90_rate_balances", "CCP_ALL", 2003) == pytest.approx(9.17)
    assert v("state_card_dq90_rate_balances", "CCP_ALL", 2025) == pytest.approx(12.36)
    assert v("state_card_debt_per_capita", "CCP_ALL", 2025) == 4350


def test_the_loss_cycle_was_national_not_concentrated(state_facts):
    """The finding the source was added for: card delinquency rose in every area from 2021 to 2025."""
    d = state_facts[state_facts["metric"] == "state_card_dq90_rate_balances"]
    d = d.pivot(index="period_end", columns="entity", values="value")
    states = [c for c in d.columns if c.startswith("STATE:") and d[c].notna().all()]
    assert len(states) == 51  # Puerto Rico has dropped out by then
    rise = d.loc["2025-12-31", states] - d.loc["2021-12-31", states]
    assert (rise > 0).all() and rise.min() > 2.2
    spread = lambda y: d.loc[f"{y}-12-31", states].max() - d.loc[f"{y}-12-31", states].min()  # noqa: E731
    assert spread(2025) > spread(2019)  # the states diverged as well as all rising


# ---------- format changes fail, on a small workbook in the file's layout ----------


def _rows(areas=None, years=None, blank: dict | None = None, header="state") -> list[list]:
    areas = areas if areas is not None else list(st.AREAS) + [st.NATIONAL_ROW]
    years = years if years is not None else [2003, 2004, 2005]
    blank = blank or {}
    rows = [["NEW YORK FED"], ["Source: New York Fed Consumer Credit Panel / Equifax"],
            [header] + [f"Q4_{y}" for y in years]]
    for k, area in enumerate(areas):
        base = 5.0 if area == st.NATIONAL_ROW else 1.0 + k * 0.1
        rows.append([area] + [None if y in blank.get(area, ()) else base for y in years])
    return rows


def _workbook(tmp_path: Path, rows_by_sheet: dict[str, list[list]]) -> Path:
    wb = Workbook()
    wb.active.title = "Cover Sheet"
    for name, rows in rows_by_sheet.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    path = tmp_path / "area.xlsx"
    wb.save(path)
    return path


def _both(rows) -> dict[str, list[list]]:
    return {sheet: [list(r) for r in rows] for sheet in st.SHEETS}


def test_synthetic_workbook_parses(tmp_path):
    facts = st.parse_workbook(_workbook(tmp_path, _both(_rows())), PULLED_AT)
    assert len(facts) == 106 * 3
    assert set(facts["period_end"].dt.year) == {2003, 2004, 2005}
    national = facts[(facts["entity"] == "CCP_ALL") & (facts["metric"] == "state_card_debt_per_capita")]
    assert national["value"].tolist() == [5.0, 5.0, 5.0]


def test_a_trailing_gap_is_allowed_only_for_an_ended_area(tmp_path):
    facts = st.parse_workbook(_workbook(tmp_path, _both(_rows(blank={"PR": (2004, 2005)}))), PULLED_AT)
    pr = facts[facts["entity"] == "STATE:PR"]
    assert pr["period_end"].dt.year.tolist() == [2003, 2003]  # one row per metric, the rest dropped
    with pytest.raises(ValueError, match="not a trailing run"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(blank={"PR": (2004,)}))), PULLED_AT)
    with pytest.raises(ValueError, match="no readings at all"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(blank={"PR": (2003, 2004, 2005)}))), PULLED_AT)
    with pytest.raises(ValueError, match="expected a number"):  # a state that has not ended may not be blank
        st.parse_workbook(_workbook(tmp_path, _both(_rows(blank={"TX": (2005,)}))), PULLED_AT)


def test_format_changes_fail_loudly(tmp_path):
    with pytest.raises(ValueError, match="no sheet"):
        st.parse_workbook(_workbook(tmp_path, {"creditcard": _rows()}), PULLED_AT)
    with pytest.raises(ValueError, match="no header row with 'state'"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(header="area"))), PULLED_AT)
    with pytest.raises(ValueError, match="not a Q4_YYYY column"):
        rows = _rows()
        rows[2][2] = "Q2_2004"
        st.parse_workbook(_workbook(tmp_path, _both(rows)), PULLED_AT)
    with pytest.raises(ValueError, match="expected 2003 onwards with no gap"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(years=[2003, 2005, 2006]))), PULLED_AT)
    with pytest.raises(ValueError, match="areas are not the expected"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(areas=list(st.AREAS)))), PULLED_AT)  # no national row
    with pytest.raises(ValueError, match="areas are not the expected"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(areas=list(st.AREAS) + ["GU", st.NATIONAL_ROW]))), PULLED_AT)
    with pytest.raises(ValueError, match="appears twice"):
        st.parse_workbook(_workbook(tmp_path, _both(_rows(areas=list(st.AREAS) + ["TX", st.NATIONAL_ROW]))), PULLED_AT)


def test_the_national_row_must_sit_inside_the_range(tmp_path):
    """A transposed or shifted read puts the national row outside the states, which is what this catches."""
    rows = _rows()
    rows[-1] = [st.NATIONAL_ROW, 99.0, 99.0, 99.0]  # above every state
    with pytest.raises(ValueError, match="outside the range across states"):
        st.parse_workbook(_workbook(tmp_path, _both(rows)), PULLED_AT)


def test_cross_check_catches_a_shifted_series(state_facts, hhdc_facts):
    shifted = state_facts.copy()
    mask = (shifted["metric"] == "state_card_dq90_rate_balances") & (shifted["entity"] == "CCP_ALL")
    shifted.loc[mask, "value"] = shifted.loc[mask, "value"] + 1.0
    with pytest.raises(ValueError, match="above the .* point tolerance"):
        st.check_against_quarterly(shifted, hhdc_facts)


def test_fixture_is_the_real_workbook():
    assert st.looks_like_xlsx(STATE_FIXTURE.read_bytes())
