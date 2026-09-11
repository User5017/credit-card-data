"""The monthly 8-K credit metrics of four issuers: their layouts, their traps, and the scaling.

The checked-in documents are chosen to be the shapes that broke something, not a sample of typical months:
  months/2026-07  Capital One's current layout, a single 'Domestic' row under 'Credit Card:(4)'
  months/2025-06  Capital One's one month after the Discover acquisition closed, where the section splits
                  into 'Capital One Domestic', 'Discover Domestic' and a 'Domestic Card' total
  months/2024-10  the same data under 'Domestic(5)', a footnote marker on the row label itself
  syf/2026-07     Synchrony's thirteen-month table under its SHORT row names
  syf/2023-04     the same table under the LONG row names it used until late 2024
  bfh/2026-07     Bread's two tables, each the month and the same month a year earlier
  bfh/2023-09     Bread in a QUARTER-END month, where the second column of the loss table is the quarter and
                  not the year-ago month, which is the trap that put a quarterly rate on a monthly series
  axp/2026-07     American Express on its new 'Card balances' basis
  axp/2026-03     American Express on the old 'Card Member loans' basis, the other side of the break that
                  keeps it out of ISSUERS
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from carddash.fetchers import issuer_8k
from carddash.schema import coerce_facts
from conftest import FIXTURES, PULLED_AT, series_for_source

RAW = FIXTURES / "issuer_8k"
MONTHS = RAW / "months"


def _html(folder: str, stem: str) -> str:
    return (RAW / folder / f"{stem}.htm").read_text(encoding="utf-8")


# --- Capital One -----------------------------------------------------------------------------------------

def test_the_current_layout_parses_the_numbers_capital_one_filed():
    """Exhibit 99.1 to the 8-K of 2026-08-17, for the month ended July 31, 2026."""
    period_end, values = issuer_8k.parse_exhibit(_html("months", "2026-07"))
    assert period_end == dt.date(2026, 7, 31)
    assert values["issuer_card_nco_rate"] == 4.12
    assert values["issuer_card_dq30_rate"] == 3.48
    # the exhibit's own units: millions of dollars, unscaled at this point
    assert values["issuer_card_loans_avg"] == 256704.0
    assert values["issuer_card_loans_eop"] == 258940.0
    assert values["issuer_card_nco_amount"] == 881.0
    assert values["issuer_card_dq30_amount"] == 9014.0


def test_the_merger_month_takes_the_combined_total_not_a_component():
    """June 2025 is the only month the exhibit splits Capital One from the newly acquired Discover.

    Taking either component would put a step in the series at the merger and would misstate the book by a
    hundred billion dollars. The total is what the FDIC roll-up also uses for this issuer.
    """
    period_end, values = issuer_8k.parse_exhibit(_html("months", "2025-06"))
    assert period_end == dt.date(2025, 6, 30)
    assert values["issuer_card_nco_rate"] == 4.96      # 'Domestic Card' total
    assert values["issuer_card_nco_rate"] not in (5.29, 4.47)  # NOT Capital One-only or Discover-only
    assert values["issuer_card_loans_avg"] == 250122.0  # 150,686 + 99,436, the total row as filed


def test_a_footnote_on_the_row_label_does_not_hide_the_row():
    """October 2024 files the same row as 'Domestic(5)'. Capital One moves its footnote markers constantly."""
    period_end, values = issuer_8k.parse_exhibit(_html("months", "2024-10"))
    assert period_end == dt.date(2024, 10, 31)
    assert values["issuer_card_nco_rate"] > 0 and values["issuer_card_dq30_rate"] > 0


def test_the_month_comes_from_the_title_block_not_the_file_name():
    """EDGAR truncates a document name at no fixed width, so the name is never trusted to date a row."""
    assert issuer_8k.period_of(_html("months", "2026-07")) == dt.date(2026, 7, 31)
    # the name is only ever a hint for skipping a download
    assert issuer_8k._month_from_name("ex991february2026creditmet.htm") == "2026-02"
    assert issuer_8k._month_from_name("ex991july2026creditmetrics.htm") == "2026-07"
    assert issuer_8k._month_from_name("cof-20260814.htm") is None


def test_a_changed_layout_fails_loudly_rather_than_shifting_a_column():
    """Every header is verified before any value is mapped to a metric."""
    good = _html("months", "2026-07")
    with pytest.raises(ValueError, match="sub-headers changed"):
        issuer_8k.parse_exhibit(good.replace("Amount", "Balance"))
    with pytest.raises(ValueError, match="sub-header row not found"):
        issuer_8k.parse_exhibit(good.replace("Period-End", "Period End"))
    with pytest.raises(ValueError, match="group headers changed"):
        issuer_8k.parse_exhibit(good.replace("Net Charge-Offs", "Charge-Offs, Net"))
    with pytest.raises(ValueError, match="does not state the month"):
        issuer_8k.parse_exhibit(good.replace("month ended", "period ended"))


# --- Synchrony -------------------------------------------------------------------------------------------

def test_synchrony_reads_thirteen_months_from_one_exhibit():
    """The whole point of Synchrony's shape: one download carries thirteen months, so six cover the history."""
    parsed = issuer_8k.parse_syf(_html("syf", "2026-07"))
    assert len(parsed) == 13
    assert max(parsed) == dt.date(2026, 7, 31)
    assert min(parsed) == dt.date(2025, 7, 31)
    july = parsed[dt.date(2026, 7, 31)]
    assert july[(issuer_8k.E_SYF, "issuer_card_nco_rate")] == 4.7
    assert july[(issuer_8k.E_SYF, "issuer_card_dq30_rate")] == 4.2
    assert july[(issuer_8k.E_SYF, "issuer_card_loans_eop")] == 102.6     # BILLIONS in the exhibit
    # the adjusted rate is a different number and must not be mistaken for the comparable one
    assert july[(issuer_8k.E_SYF, "issuer_card_nco_rate_adj")] == 4.9


def test_synchrony_reads_the_long_row_names_it_used_before_2025():
    """Until late 2024 Synchrony spelled each rate out as its own definition instead of naming it."""
    parsed = issuer_8k.parse_syf(_html("syf", "2023-04"))
    assert max(parsed) == dt.date(2023, 4, 30)
    april = parsed[dt.date(2023, 4, 30)]
    # the same five metrics come out of the long-form labels
    assert {m for _, m in april} == {
        "issuer_card_nco_rate", "issuer_card_nco_rate_adj", "issuer_card_dq30_rate",
        "issuer_card_loans_eop", "issuer_card_loans_avg",
    }
    assert 0 < april[(issuer_8k.E_SYF, "issuer_card_nco_rate")] < 30


def test_synchrony_will_not_read_a_document_that_is_not_its_exhibit():
    with pytest.raises(ValueError, match="not a Synchrony monthly statistics exhibit"):
        issuer_8k.parse_syf(_html("bfh", "2026-07"))


def test_a_dropped_synchrony_row_fails_rather_than_loading_a_short_month():
    good = _html("syf", "2026-07")
    with pytest.raises(ValueError, match="missing metrics this parser requires"):
        issuer_8k.parse_syf(good.replace("Net charge-off rate", "Charge-off rate, net"))


# --- Bread Financial -------------------------------------------------------------------------------------

def test_bread_reads_the_month_and_the_same_month_a_year_earlier():
    parsed = issuer_8k.parse_bfh(_html("bfh", "2026-07"))
    assert sorted(parsed) == [dt.date(2025, 7, 31), dt.date(2026, 7, 31)]
    july = parsed[dt.date(2026, 7, 31)]
    assert july[(issuer_8k.E_BFH, "issuer_card_nco_rate")] == 6.80
    assert july[(issuer_8k.E_BFH, "issuer_card_dq30_rate")] == 5.35
    assert july[(issuer_8k.E_BFH, "issuer_card_loans_eop")] == 18543.0          # MILLIONS in the exhibit
    # the delinquency rate divides by the PRINCIPAL figure, not by the end-of-period one above it
    assert july[(issuer_8k.E_BFH, "issuer_card_loans_eop_principal")] == 16378.0
    assert july[(issuer_8k.E_BFH, "issuer_card_dq30_amount")] == 876.0
    assert 876.0 / 16378.0 * 100 == pytest.approx(5.35, abs=0.01)
    assert 876.0 / 18543.0 * 100 != pytest.approx(5.35, abs=0.01)


def test_bread_in_a_quarter_end_month_takes_the_month_not_the_quarter():
    """The trap: in March, June, September and December the second column is the QUARTER, not a year earlier.

    Both columns are headed with the same date, so reading the header as dates alone puts the quarter's
    figure on the month. September 2023 was loaded as 6.9 percent, which is the quarter; the month is 6.7.
    """
    parsed = issuer_8k.parse_bfh(_html("bfh", "2023-09"))
    september = parsed[dt.date(2023, 9, 30)]
    assert september[(issuer_8k.E_BFH, "issuer_card_nco_rate")] == 6.7          # the month
    assert september[(issuer_8k.E_BFH, "issuer_card_nco_rate")] != 6.9          # NOT the three months
    assert september[(issuer_8k.E_BFH, "issuer_card_nco_amount")] == 98.0       # the month, not 304
    # the year-ago month is still read from the delinquency table, which does keep its year-ago column
    assert parsed[dt.date(2022, 9, 30)][(issuer_8k.E_BFH, "issuer_card_dq30_rate")] == 5.7
    # ... and states nothing about that month's losses, so nothing is invented for it
    assert (issuer_8k.E_BFH, "issuer_card_nco_rate") not in parsed[dt.date(2022, 9, 30)]


def test_a_bread_header_that_repeats_a_month_is_an_error_not_a_last_write_wins():
    """If Bread ever prints the same month twice without saying one is an aggregate, stop rather than guess."""
    good = _html("bfh", "2023-09")
    with pytest.raises(ValueError, match="states a month twice"):
        issuer_8k.parse_bfh(good.replace("three months ended", "months ended"))


# --- American Express: parsed, deliberately not loaded ---------------------------------------------------

def test_amex_is_parsed_but_is_not_one_of_the_loaded_issuers():
    """Amex changed the population it reports without restating the history: see AXP_POPULATION_BREAK."""
    assert "axp" not in {i.key for i in issuer_8k.ISSUERS}
    assert "axp" in {i.key for i in issuer_8k.HELD_BACK}
    loaded = {e for i in issuer_8k.ISSUERS for e in i.entities}
    assert issuer_8k.E_AXP_CONSUMER not in loaded
    assert issuer_8k.E_AXP_SMALL_BUSINESS not in loaded


def test_amex_reads_three_months_and_two_segments():
    parsed = issuer_8k.parse_axp(_html("axp", "2026-07"))
    assert sorted(parsed) == [dt.date(2026, 5, 31), dt.date(2026, 6, 30), dt.date(2026, 7, 31)]
    july = parsed[dt.date(2026, 7, 31)]
    assert july[(issuer_8k.E_AXP_CONSUMER, "issuer_card_loans_eop")] == 113.1
    assert july[(issuer_8k.E_AXP_CONSUMER, "issuer_card_dq30_rate")] == 1.1
    assert july[(issuer_8k.E_AXP_CONSUMER, "issuer_card_nco_rate")] == 1.7
    assert july[(issuer_8k.E_AXP_SMALL_BUSINESS, "issuer_card_nco_rate")] == 2.6


def test_amex_does_not_load_the_lending_trust_table_below_its_own():
    """The second dated table is the Credit Account Master Trust: revolve-eligible only, a different rate."""
    parsed = issuer_8k.parse_axp(_html("axp", "2026-07"))
    # the trust's July figures are 24.9 (principal balance) and 1.1 percent (annualized default rate)
    assert all(v != 24.9 for values in parsed.values() for v in values.values())
    assert set(parsed) == {dt.date(2026, 5, 31), dt.date(2026, 6, 30), dt.date(2026, 7, 31)}


def test_amex_reads_the_card_member_loans_labels_it_used_before_may_2026():
    parsed = issuer_8k.parse_axp(_html("axp", "2026-03"))
    march = parsed[dt.date(2026, 3, 31)]
    assert march[(issuer_8k.E_AXP_CONSUMER, "issuer_card_loans_eop")] == 97.5
    assert march[(issuer_8k.E_AXP_CONSUMER, "issuer_card_dq30_rate")] == 1.4


def test_the_amex_population_break_is_real_and_is_why_it_is_not_loaded():
    """March 2026 is reported on both bases, and the two disagree by 14 percent on the same month.

    This is the whole reason Amex is held back: splicing the two would put a step in the balances and a fake
    improvement in the delinquency rate. If this test ever fails because the two agree, the break has been
    closed and Amex can be moved into ISSUERS.
    """
    old = issuer_8k.parse_axp(_html("axp", "2026-03"))[dt.date(2026, 3, 31)]
    new = issuer_8k.parse_axp(_html("axp", "2026-07"))
    assert dt.date(2026, 3, 31) not in new, "the new-basis filing does not restate March"

    older_new_basis = issuer_8k.parse_axp(_html("axp", "2026-07"))[dt.date(2026, 5, 31)]
    on_old = old[(issuer_8k.E_AXP_CONSUMER, "issuer_card_loans_eop")]
    on_new = older_new_basis[(issuer_8k.E_AXP_CONSUMER, "issuer_card_loans_eop")]
    assert on_new / on_old > 1.10, f"the two bases are {on_old} and {on_new}: no longer a break?"


def test_an_amex_earnings_filing_is_recognised_without_being_parsed():
    """The content test reads parsed table text, not raw HTML: Amex splits the label across tags."""
    assert issuer_8k.looks_like_axp_metrics(_html("axp", "2026-07"))
    assert not issuer_8k.looks_like_axp_metrics(_html("bfh", "2026-07"))


# --- the shared machinery --------------------------------------------------------------------------------

def test_only_the_unbroken_run_ending_at_the_newest_month_counts_as_coverage():
    """Synchrony's thirteen months are contiguous and all count; Bread's year-ago month must not.

    If Bread's year-ago month counted, the planner would skip the filing that actually reports it and leave a
    year-long hole in the middle of the series.
    """
    syf = issuer_8k.parse_syf(_html("syf", "2026-07"))
    assert issuer_8k._contiguous_tail(list(syf)) == set(syf)

    bfh = issuer_8k.parse_bfh(_html("bfh", "2026-07"))
    assert issuer_8k._contiguous_tail(list(bfh)) == {dt.date(2026, 7, 31)}


def test_a_footnote_marker_is_stripped_from_a_label_but_never_from_a_value():
    """'(5)' after a label is a footnote; '(5)' as a value is minus five. Only labels are stripped."""
    assert issuer_8k._norm("Domestic(5)") == "Domestic"
    assert issuer_8k._norm("Net write-off rate – principal only (a)(b)") == \
        "Net write-off rate - principal only"
    assert issuer_8k._number("(5)") == -5.0
    assert issuer_8k._number("(0.1)") == -0.1
    assert issuer_8k._number("1,234") == 1234.0
    assert issuer_8k._number("—") is None


def test_facts_are_scaled_per_issuer_because_they_do_not_publish_the_same_units(meta):
    """Capital One and Bread print millions, Synchrony prints billions, and facts are billions throughout."""
    df = issuer_8k.facts_from_raw(RAW, series_for_source(meta, issuer_8k.SOURCE), PULLED_AT,
                                  require_consecutive=False)
    df = coerce_facts(df)
    assert set(df["source"]) == {"issuer_8k"}
    assert set(df["period_type"]) == {"M"}
    assert set(df["entity"]) == {"ISSUER:CAPITAL_ONE", "ISSUER:SYNCHRONY", "ISSUER:BREAD_FINANCIAL"}

    def value(entity, metric, period):
        row = df[(df["entity"] == entity) & (df["metric"] == metric)
                 & (df["period_end"] == pd.Timestamp(period))]
        assert len(row) == 1, f"{entity} {metric} {period}: {len(row)} rows"
        return row.iloc[0]["value"]

    # $258,940mn -> billions, and $102.6bn stays as it is
    assert value("ISSUER:CAPITAL_ONE", "issuer_card_loans_eop", "2026-07-31") == pytest.approx(258.940)
    assert value("ISSUER:SYNCHRONY", "issuer_card_loans_eop", "2026-07-31") == pytest.approx(102.6)
    assert value("ISSUER:BREAD_FINANCIAL", "issuer_card_loans_eop", "2026-07-31") == pytest.approx(18.543)
    # the rates are percent everywhere and are not rescaled
    assert value("ISSUER:CAPITAL_ONE", "issuer_card_nco_rate", "2026-07-31") == pytest.approx(4.12)
    assert value("ISSUER:SYNCHRONY", "issuer_card_nco_rate", "2026-07-31") == pytest.approx(4.7)
    assert value("ISSUER:BREAD_FINANCIAL", "issuer_card_nco_rate", "2026-07-31") == pytest.approx(6.80)

    # Capital One's rate is the flow over the average book, annualised, which is how it states it
    avg = value("ISSUER:CAPITAL_ONE", "issuer_card_loans_avg", "2026-07-31")
    flow = value("ISSUER:CAPITAL_ONE", "issuer_card_nco_amount", "2026-07-31")
    assert 12 * flow / avg * 100 == pytest.approx(4.12, abs=0.02)


def test_a_hole_before_an_issuer_starts_filing_monthly_trims_the_series():
    """Bread's shape: a year of extra delinquency history arrives free, and the loss rows do not.

    Bread's first_month is July 2023. Readings before it are whatever the year-ago columns happened to reach
    back to, so a hole there means the month was never published monthly and the series starts where it is
    whole, rather than being drawn across the hole.
    """
    bfh = issuer_8k.BY_KEY["bfh"]
    readings = {
        ("issuer_card_nco_rate", "ISSUER:BREAD_FINANCIAL", dt.date(2022, 7, 31)): 1.0,   # stranded, pre-floor
        ("issuer_card_nco_rate", "ISSUER:BREAD_FINANCIAL", dt.date(2023, 7, 31)): 2.0,
        ("issuer_card_nco_rate", "ISSUER:BREAD_FINANCIAL", dt.date(2023, 8, 31)): 3.0,
        ("issuer_card_dq30_rate", "ISSUER:BREAD_FINANCIAL", dt.date(2023, 7, 31)): 4.0,
        ("issuer_card_dq30_rate", "ISSUER:BREAD_FINANCIAL", dt.date(2023, 8, 31)): 5.0,
    }
    kept = issuer_8k._trim_to_unbroken_run(bfh, readings)
    assert sorted(p for m, _, p in kept if m == "issuer_card_nco_rate") == \
        [dt.date(2023, 7, 31), dt.date(2023, 8, 31)]
    # the issuer's other series is untouched
    assert len([1 for m, _, _ in kept if m == "issuer_card_dq30_rate"]) == 2


def test_a_missed_filing_raises_rather_than_quietly_shortening_the_series():
    """The trim must never swallow a hole in the months an issuer really does file every one of.

    This is the dangerous case the bound exists for: a run that failed to read one Capital One filing would
    otherwise publish a series starting after the hole and drop years of history without saying anything.
    """
    cof = issuer_8k.BY_KEY["cof"]
    readings = {
        ("issuer_card_nco_rate", "ISSUER:CAPITAL_ONE", dt.date(2021, 3, 31)): 1.0,
        ("issuer_card_nco_rate", "ISSUER:CAPITAL_ONE", dt.date(2026, 5, 31)): 2.0,
        ("issuer_card_nco_rate", "ISSUER:CAPITAL_ONE", dt.date(2026, 7, 31)): 3.0,
    }
    with pytest.raises(ValueError, match="a filing this run failed to read"):
        issuer_8k._trim_to_unbroken_run(cof, readings)


def test_the_capital_one_fixtures_are_not_consecutive_so_the_check_catches_them(meta):
    """The three checked-in Capital One months are 2024-10, 2025-06 and 2026-07 on purpose."""
    with pytest.raises(ValueError, match="a filing this run failed to read"):
        issuer_8k.facts_from_raw(RAW, series_for_source(meta, issuer_8k.SOURCE), PULLED_AT,
                                 issuers=(issuer_8k.BY_KEY["cof"],))


def test_a_raw_file_must_be_named_for_the_newest_month_it_states(tmp_path, meta):
    """The name is checked against the document rather than trusted, for every issuer."""
    folder = tmp_path / "syf"
    folder.mkdir()
    (folder / "2020-01.htm").write_text(_html("syf", "2026-07"), encoding="utf-8")
    with pytest.raises(ValueError, match="which is not its name"):
        issuer_8k._load_local(issuer_8k.BY_KEY["syf"], tmp_path)


def test_a_parse_failure_says_which_document_it_was(tmp_path):
    folder = tmp_path / "bfh"
    folder.mkdir()
    (folder / "2026-07.htm").write_text("<html><body>nothing here</body></html>", encoding="utf-8")
    with pytest.raises(ValueError, match=r"Bread Financial raw file 2026-07\.htm"):
        issuer_8k._load_local(issuer_8k.BY_KEY["bfh"], tmp_path)


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


def test_every_loaded_issuer_declares_its_series_in_the_crosswalk(meta):
    """A fetcher may only emit series that exist in crosswalks/series.csv, per issuer and per metric."""
    declared = {(r["entity"], r["metric"]) for _, r in series_for_source(meta, issuer_8k.SOURCE).iterrows()}
    for issuer in issuer_8k.ISSUERS:
        for entity in issuer.entities:
            assert any(e == entity for e, _ in declared), f"{issuer.name}: {entity} has no series rows"
    # and the held-back issuer has none, so enabling it is a deliberate act
    for issuer in issuer_8k.HELD_BACK:
        for entity in issuer.entities:
            assert not any(e == entity for e, _ in declared), f"{entity} is held back but has series rows"
