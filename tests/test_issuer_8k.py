"""Capital One's monthly 8-K credit metrics: the three exhibit layouts, the merger month, and the scaling.

The three checked-in exhibits are not consecutive on purpose. They are the three shapes the filing has taken:
  2026-07  the current layout, a single 'Domestic' row under 'Credit Card:(4)'
  2025-06  the one month after the Discover acquisition closed where the section is split into
           'Capital One Domestic', 'Discover Domestic' and a 'Domestic Card' total
  2024-10  the same data under 'Domestic(5)', a footnote marker on the row label itself
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from carddash.fetchers import issuer_8k
from carddash.schema import coerce_facts
from conftest import FIXTURES, PULLED_AT, series_for_source

MONTHS = FIXTURES / "issuer_8k" / "months"


def _html(stem: str) -> str:
    return (MONTHS / f"{stem}.htm").read_text(encoding="utf-8")


def test_the_current_layout_parses_the_numbers_capital_one_filed():
    """Exhibit 99.1 to the 8-K of 2026-08-17, for the month ended July 31, 2026."""
    period_end, values = issuer_8k.parse_exhibit(_html("2026-07"))
    assert period_end == dt.date(2026, 7, 31)
    assert values["cof_card_nco_rate"] == 4.12
    assert values["cof_card_dq30_rate"] == 3.48
    # the exhibit's own units: millions of dollars, unscaled at this point
    assert values["cof_card_loans_avg"] == 256704.0
    assert values["cof_card_loans_eop"] == 258940.0
    assert values["cof_card_nco_amount"] == 881.0
    assert values["cof_card_dq30_amount"] == 9014.0


def test_the_merger_month_takes_the_combined_total_not_a_component():
    """June 2025 is the only month the exhibit splits Capital One from the newly acquired Discover.

    Taking either component would put a step in the series at the merger and would misstate the book by a
    hundred billion dollars. The total is what the FDIC roll-up also uses for this issuer.
    """
    period_end, values = issuer_8k.parse_exhibit(_html("2025-06"))
    assert period_end == dt.date(2025, 6, 30)
    assert values["cof_card_nco_rate"] == 4.96      # 'Domestic Card' total
    assert values["cof_card_nco_rate"] not in (5.29, 4.47)  # NOT Capital One-only or Discover-only
    assert values["cof_card_loans_avg"] == 250122.0  # 150,686 + 99,436, the total row as filed


def test_a_footnote_on_the_row_label_does_not_hide_the_row():
    """October 2024 files the same row as 'Domestic(5)'. Capital One moves its footnote markers constantly."""
    period_end, values = issuer_8k.parse_exhibit(_html("2024-10"))
    assert period_end == dt.date(2024, 10, 31)
    assert values["cof_card_nco_rate"] > 0 and values["cof_card_dq30_rate"] > 0


def test_the_month_comes_from_the_title_block_not_the_file_name():
    """EDGAR truncates a document name at no fixed width, so the name is never trusted to date a row."""
    assert issuer_8k.period_of(_html("2026-07")) == dt.date(2026, 7, 31)
    # the name is only ever a hint for skipping a download
    assert issuer_8k._month_from_name("ex991february2026creditmet.htm") == "2026-02"
    assert issuer_8k._month_from_name("ex991july2026creditmetrics.htm") == "2026-07"
    assert issuer_8k._month_from_name("cof-20260814.htm") is None


def test_a_changed_layout_fails_loudly_rather_than_shifting_a_column(meta):
    """Every header is verified before any value is mapped to a metric."""
    good = _html("2026-07")
    with pytest.raises(ValueError, match="sub-headers changed"):
        issuer_8k.parse_exhibit(good.replace("Amount", "Balance"))
    with pytest.raises(ValueError, match="sub-header row not found"):
        issuer_8k.parse_exhibit(good.replace("Period-End", "Period End"))
    with pytest.raises(ValueError, match="group headers changed"):
        issuer_8k.parse_exhibit(good.replace("Net Charge-Offs", "Charge-Offs, Net"))
    with pytest.raises(ValueError, match="does not state the month"):
        issuer_8k.parse_exhibit(good.replace("month ended", "period ended"))


def test_facts_are_scaled_by_the_crosswalk_and_only_declared_series_are_emitted(meta):
    """Dollars are millions in the exhibit and billions in facts; the rates are already percent."""
    df = issuer_8k.facts_from_months(
        MONTHS, series_for_source(meta, issuer_8k.SOURCE), PULLED_AT, require_consecutive=False
    )
    df = coerce_facts(df)
    assert set(df["source"]) == {"issuer_8k"}
    assert set(df["entity"]) == {"ISSUER:CAPITAL_ONE"}
    assert set(df["period_type"]) == {"M"}
    assert len(df) == 3 * 6  # three months, six series

    july = df[df["period_end"] == pd.Timestamp(2026, 7, 31)].set_index("metric")["value"]
    assert july["cof_card_nco_rate"] == pytest.approx(4.12)
    assert july["cof_card_loans_eop"] == pytest.approx(258.940)   # $258,940mn -> billions
    assert july["cof_card_nco_amount"] == pytest.approx(0.881)
    # the rate is the flow over the average book, annualised, which is how Capital One states it
    avg = july["cof_card_loans_avg"]
    assert 12 * july["cof_card_nco_amount"] / avg * 100 == pytest.approx(4.12, abs=0.02)


def test_a_missing_month_is_an_error_not_a_gap(meta):
    """Capital One files every month, so a hole means a filing was missed rather than a month not happening."""
    with pytest.raises(ValueError, match="gap in the monthly metrics"):
        issuer_8k.facts_from_months(MONTHS, series_for_source(meta, issuer_8k.SOURCE), PULLED_AT)


def test_the_sec_user_agent_declares_a_contact_and_carries_no_url(monkeypatch):
    """The SEC's edge 403s any User-Agent containing a URL, which is the shape of this pipeline's normal one."""
    monkeypatch.setenv("CARDDASH_CONTACT", "someone@example.com")
    ua = issuer_8k._sec_headers()["User-Agent"]
    assert "someone@example.com" in ua and "http" not in ua

    monkeypatch.setenv("CARDDASH_CONTACT", "https://example.com")
    with pytest.raises(RuntimeError, match="not a URL"):
        issuer_8k._sec_headers()

    monkeypatch.delenv("CARDDASH_CONTACT")
    with pytest.raises(RuntimeError, match="CARDDASH_CONTACT must be set"):
        issuer_8k._sec_headers()
