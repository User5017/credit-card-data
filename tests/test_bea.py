"""BEA NIPA flat-file fetcher: the checked-in subset, scaling, continuity, the release-page changes."""

from __future__ import annotations

import pandas as pd
import pytest

from carddash.fetchers import bea
from carddash.series import series_for_source
from conftest import BEA_FIXTURE, PULLED_AT


def test_subset_reproduces_the_release_changes(bea_facts):
    """Personal Income and Outlays, July 2026: PCE +36.3bn, goods -49.9bn, services +86.2bn; June +77.4bn."""
    def change(metric, month):
        s = bea_facts[bea_facts["metric"] == metric].set_index("period_end")["value"]
        return s[pd.Timestamp(month)] - s[pd.Timestamp(month) - pd.offsets.MonthEnd(1)]
    assert abs(change("pce_total_saar", "2026-07-31") - 36.3) < 0.06
    assert abs(change("pce_goods_saar", "2026-07-31") + 49.9) < 0.06
    assert abs(change("pce_services_saar", "2026-07-31") - 86.2) < 0.06
    assert abs(change("pce_total_saar", "2026-06-30") - 77.4) < 0.06


def test_series_shape(bea_facts, meta):
    cols = ["metric", "entity", "tier", "period_type"]
    listed = {tuple(r) for r in series_for_source(meta, bea.SOURCE)[cols].itertuples(index=False)}
    loaded = {tuple(r) for r in bea_facts[cols].drop_duplicates().itertuples(index=False)}
    assert loaded == listed and len(loaded) == 8
    total = bea_facts[bea_facts["metric"] == "pce_total_saar"]
    assert total["period_end"].min() == pd.Timestamp("1959-01-31") and total["period_end"].max() == pd.Timestamp("2026-07-31")
    assert abs(total["value"].iloc[-1] - 22250.42) < 0.001  # billions, from '22,250,420' millions
    assert (bea_facts["period_end"] == bea_facts["period_end"] + pd.offsets.MonthEnd(0)).all()
    assert bea_facts["pulled_at"].unique().tolist() == [PULLED_AT]
    # goods plus services is total
    g = bea_facts[bea_facts["metric"] == "pce_goods_saar"].set_index("period_end")["value"]
    s = bea_facts[bea_facts["metric"] == "pce_services_saar"].set_index("period_end")["value"]
    t = total.set_index("period_end")["value"]
    assert ((g + s - t).abs() < 0.01).all()


def test_subset_lines_keep_only_wanted_codes_and_check_the_header():
    text = "%SeriesCode,Period,Value\nA001RC,2020M01,\"1\"\nDPCERC,2020M01,\"2,000\"\nDPCERC,2020M02,\"2,100\"\n"
    assert bea.subset_lines(text, {"DPCERC"}) == ["%SeriesCode,Period,Value", 'DPCERC,2020M01,"2,000"', 'DPCERC,2020M02,"2,100"']
    with pytest.raises(ValueError, match="does not start with"):
        bea.subset_lines("<!DOCTYPE html><html>not found</html>", {"DPCERC"})


def test_parse_subset_fails_on_gaps_missing_codes_and_ragged_ends(meta):
    mine = series_for_source(meta, bea.SOURCE)
    two = mine[mine["source_id"].isin(["DPCERC", "DGDSRC"])]
    good = ("%SeriesCode,Period,Value\n" + "DPCERC,2020M01,\"1,000\"\nDPCERC,2020M02,\"1,100\"\n"
            + "DGDSRC,2020M01,\"300\"\nDGDSRC,2020M02,\"310\"\n")
    df = bea.parse_subset(good, two, PULLED_AT)
    assert len(df) == 4 and df["value"].tolist() == [1.0, 1.1, 0.3, 0.31]
    with pytest.raises(ValueError, match="months jump"):
        bea.parse_subset(good.replace("DPCERC,2020M02", "DPCERC,2020M04"), two, PULLED_AT)
    with pytest.raises(ValueError, match="no rows"):
        bea.parse_subset(good.replace("DGDSRC", "XXXXXX"), two, PULLED_AT)
    with pytest.raises(ValueError, match="end on different months"):
        bea.parse_subset(good.replace("DGDSRC,2020M02,\"310\"\n", ""), two, PULLED_AT)
    with pytest.raises(ValueError, match="not a number"):
        bea.parse_subset(good.replace("\"310\"", "\"...\""), two, PULLED_AT)


def test_fixture_is_the_subset_format():
    text = BEA_FIXTURE.read_text(encoding="utf-8")
    assert text.startswith(bea.HEADER + "\n") and "A001RC" not in text
