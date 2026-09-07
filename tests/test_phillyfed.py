"""Philly Fed large-bank card fetcher: parse the checked-in 2026 Q1 release, discovery walks back, bad input fails."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from carddash.fetchers import phillyfed
from carddash.loader import validate
from carddash.schema import SERIES_KEY
from conftest import FIXTURES, PULLED_AT

FIX = FIXTURES / "phillyfed"
BAL = FIX / "26Q1-CreditCardBalances.csv"
ORIG = FIX / "26Q1-CreditCardOriginations.csv"
HTML_404 = b'\r\n<!DOCTYPE html>\r\n<html lang="en"><head><title>Error - 404</title></head><body></body></html>'


def value(facts: pd.DataFrame, metric: str, quarter: str, tier: str = "all") -> float:
    pe = pd.Timestamp(phillyfed.quarter_end(*phillyfed.parse_quarter(quarter)))
    sel = facts[(facts["metric"] == metric) & (facts["tier"] == tier) & (facts["period_end"] == pe)]
    assert len(sel) == 1, f"{metric}/{tier}/{quarter}: {len(sel)} rows"
    return float(sel["value"].iloc[0])


# ---------- quarters and URLs ----------


@pytest.mark.parametrize(
    "label, end",
    [("2012Q3", dt.date(2012, 9, 30)), ("2026Q1", dt.date(2026, 3, 31)), ("2025Q4", dt.date(2025, 12, 31)),
     ("2024Q2", dt.date(2024, 6, 30))],
)
def test_quarter_labels_map_to_quarter_end(label, end):
    assert phillyfed.quarter_end(*phillyfed.parse_quarter(label)) == end


@pytest.mark.parametrize("bad", ["2026Q5", "26Q1", "2026-Q1", "Source: Philadelphia Fed"])
def test_bad_quarter_label_fails(bad):
    with pytest.raises(ValueError):
        phillyfed.parse_quarter(bad)


def test_file_url_embeds_the_quarter_twice():
    assert phillyfed.file_url(2026, 1, "CreditCardBalances") == (
        "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/Y14/2026/Q1/26Q1-CreditCardBalances.csv"
    )
    assert phillyfed.previous_quarter(2026, 1) == (2025, 4)
    assert phillyfed.quarter_of(dt.date(2026, 9, 7)) == (2026, 3)


# ---------- discovery ----------


class FakeSession:
    """Answers 200 for every URL, as the Fed's site does. The body says whether the file exists."""

    def __init__(self, available: dict[str, bytes]):
        self.available = available
        self.calls: list[str] = []

    def get(self, url):
        self.calls.append(url)
        body = self.available.get(url, HTML_404)

        class R:
            content = body
            status_code = 200

            def raise_for_status(self):
                pass

        return R()


def test_discovery_walks_back_from_the_current_quarter():
    bal = BAL.read_bytes()
    session = FakeSession({phillyfed.file_url(2026, 1, "CreditCardBalances"): bal})
    year, q, content = phillyfed.discover_latest(session, dt.date(2026, 9, 7))
    assert (year, q) == (2026, 1)
    assert content == bal
    assert [u.rsplit("/", 1)[-1] for u in session.calls] == [
        "26Q3-CreditCardBalances.csv", "26Q2-CreditCardBalances.csv", "26Q1-CreditCardBalances.csv"
    ]


def test_discovery_fails_loudly_when_nothing_is_found():
    with pytest.raises(ValueError, match="no CreditCardBalances file found"):
        phillyfed.discover_latest(FakeSession({}), dt.date(2026, 9, 7), max_back=3)


def test_looks_like_release_csv_ignores_bom_and_rejects_html():
    assert phillyfed.looks_like_release_csv(b'\xef\xbb\xbf"YRQTR","Total Balances ($Billions)"')
    assert phillyfed.looks_like_release_csv(b'"YRQTR","New Originations ($Billions)"')
    assert not phillyfed.looks_like_release_csv(HTML_404)


def test_fetch_end_to_end_with_a_fake_site(tmp_path, meta):
    session = FakeSession(
        {
            phillyfed.file_url(2026, 1, "CreditCardBalances"): BAL.read_bytes(),
            phillyfed.file_url(2026, 1, "CreditCardOriginations"): ORIG.read_bytes(),
        }
    )
    raw = tmp_path / "phillyfed"
    facts = phillyfed.fetch(meta, raw, session, "2026-09-07T12:00:00Z")
    assert (raw / "latest" / "CreditCardBalances.csv").read_bytes() == BAL.read_bytes()
    assert (raw / "latest" / "CreditCardOriginations.csv").read_bytes() == ORIG.read_bytes()
    assert facts["period_end"].max() == pd.Timestamp("2026-03-31")


def test_fetch_fails_when_the_originations_file_is_missing(tmp_path, meta):
    session = FakeSession({phillyfed.file_url(2026, 1, "CreditCardBalances"): BAL.read_bytes()})
    with pytest.raises(ValueError, match="CreditCardOriginations for 2026Q1: not a release CSV"):
        phillyfed.fetch(meta, tmp_path / "phillyfed", session, "2026-09-07T12:00:00Z")


# ---------- parsing the real release ----------


def test_release_csv_shapes():
    bal = phillyfed.parse_release_csv(BAL.read_bytes(), BAL.name)
    orig = phillyfed.parse_release_csv(ORIG.read_bytes(), ORIG.name)
    assert list(bal.index[:2]) == ["2012Q3", "2012Q4"] and bal.index[-1] == "2026Q1"
    assert len(bal) == 55 and len(orig) == 55  # 2012Q3 .. 2026Q1
    assert bal.shape[1] == 35 and orig.shape[1] == 15
    assert bal.loc["2012Q3", "Total Balances ($Billions)"] == 571.03
    assert bal.loc["2012Q3", "Current Credit Limit (75th Percentile)"] == 8083.0
    assert bal.loc["2012Q3", "Utilization (Active Accounts Only) (50th percentile)"] == 9.25
    assert pd.isna(bal.loc["2012Q3", "Percentage of Accounts with Credit Line Decrease"])  # 'null' in the file
    assert orig.loc["2026Q1", "Original Credit Score (10th percentile)"] == 619.0


def test_facts_cover_every_series_in_series_csv_and_nothing_else(meta, phillyfed_facts):
    expected = set(
        meta[meta["source"] == "phillyfed"][SERIES_KEY].itertuples(index=False, name=None)
    )
    got = set(phillyfed_facts[SERIES_KEY].drop_duplicates().itertuples(index=False, name=None))
    assert got == expected, f"missing {expected - got}, extra {got - expected}"
    assert len(expected) == 51  # 35 + 15 transcribed columns + the utilization ratio
    errors, warnings = validate(phillyfed_facts, meta[meta["source"] == "phillyfed"])
    assert errors == []
    assert warnings == []
    assert (phillyfed_facts["entity"] == "Y14_CARD_FILERS").all()
    assert (phillyfed_facts["period_type"] == "Q").all()
    # every series has all 55 quarters except the one the Fed left 'null' for 2012Q3
    counts = phillyfed_facts.groupby(["metric", "tier"])["period_end"].count()
    assert counts[("y14_card_line_decrease_share", "all")] == 54
    assert (counts.drop(("y14_card_line_decrease_share", "all")) == 55).all()
    assert len(phillyfed_facts) == 51 * 55 - 1


def test_headline_values_2026_q1(phillyfed_facts):
    f = phillyfed_facts
    assert value(f, "y14_card_balances", "2026Q1") == 948.67
    assert value(f, "y14_card_commitments", "2026Q1") == 4960.42
    assert value(f, "y14_card_utilization_rate", "2026Q1") == pytest.approx(100 * 948.67 / 4960.42)
    assert value(f, "y14_card_dq30_rate_balances", "2026Q1") == 3.31
    assert value(f, "y14_card_dq90_rate_accounts", "2026Q1") == 0.80
    assert value(f, "y14_card_nco_rate", "2026Q1") == 5.20
    assert value(f, "y14_card_purchase_volume_avg", "2026Q1", "lt660") == 529.28
    assert value(f, "y14_card_purchase_volume_avg", "2026Q1", "660_719") == 1139.59
    assert value(f, "y14_card_purchase_volume_avg", "2026Q1", "superprime") == 2363.33
    assert value(f, "y14_card_orig_credit_score_p10", "2026Q1") == 619
    assert value(f, "y14_card_orig_credit_limit_median", "2026Q1", "lt660") == 500.0
    assert value(f, "y14_card_new_accounts_share", "2026Q1", "lt660") == 19.15
    assert value(f, "y14_card_new_commitments_share", "2026Q1", "lt660") == 4.01
    assert value(f, "y14_card_line_increase_share", "2012Q3") == 1.16  # first published value, past the 'null'


# ---------- format changes fail ----------


def _balances_with(replacement: tuple[str, str]) -> bytes:
    text = BAL.read_bytes().decode("utf-8-sig")
    old, new = replacement
    assert old in text
    return text.replace(old, new, 1).encode("utf-8")


@pytest.mark.parametrize(
    "replacement, match",
    [
        (('"YRQTR"', '"Quarter"'), "first header cell should be 'YRQTR'"),
        (('"Total Balances ($Billions)"', '"Total Balances ($Millions)"'), "headers missing"),
        (('"$948.67"', '"n/a"'), "cannot read 'n/a' as a number"),
        (('"Source: Philadelphia Fed FR Y-14M Data"', '"2026Q2","$1.00"'), "unexpected row after the data"),
        (('"Source: Philadelphia Fed FR Y-14M Data"', '"Note: restated"'), "unexpected row after the data"),
    ],
)
def test_format_changes_fail_loudly(tmp_path, replacement, match):
    paths = {"CreditCardBalances": tmp_path / "b.csv", "CreditCardOriginations": ORIG}
    paths["CreditCardBalances"].write_bytes(_balances_with(replacement))
    with pytest.raises(ValueError, match=match):
        phillyfed.parse_release(paths, PULLED_AT)


def test_file_must_end_on_the_quarter_the_url_names(tmp_path):
    paths = {"CreditCardBalances": BAL, "CreditCardOriginations": ORIG}
    with pytest.raises(ValueError, match="last quarter in the file is 2026Q1, the URL says 2026Q2"):
        phillyfed.parse_release(paths, PULLED_AT, expected_quarter=(2026, 2))


def test_duplicate_quarter_fails(tmp_path):
    text = BAL.read_bytes().decode("utf-8-sig")
    line = next(ln for ln in text.splitlines() if ln.startswith('"2026Q1"'))
    dup = text.replace(line, line + "\n" + line, 1).encode("utf-8")
    with pytest.raises(ValueError, match="quarter 2026Q1 appears twice"):
        phillyfed.parse_release_csv(dup, "dup.csv")
