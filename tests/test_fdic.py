"""FDIC fetcher: the checked-in charter CSVs and industry aggregate parse, the Call Report identities hold, the
year-to-date and quarterly charge-off fields agree outside merger quarters, the issuer roll-up sums acquirer and
acquired, and bad input fails loudly.

The numbers asserted on the real files were read independently with pandas.read_csv and json on 2026-09-08, by column
name, not with the parser under test. Thousands in the files, billions in facts.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest
import requests

from carddash.fetchers import fdic
from carddash.loader import validate
from carddash.render import _connect
from carddash.schema import SERIES_KEY
from conftest import FDIC_FIXTURE_CERTS, FDIC_FIXTURE_DIR, PULLED_AT, REPO

Q2_2026 = pd.Timestamp("2026-06-30")
BN = fdic.THOUSANDS_TO_BILLIONS
N_CERT_FACTS = 4083  # non-null cells of the six loaded items over the five checked-in charters
N_AGG_FACTS = 924  # 170 quarters x 4 items, plus 102 quarters of P3CRCD from 2001 Q1 and 142 of NCCRCD from 1991 Q1


def value(facts: pd.DataFrame, metric: str, period_end: str, entity: str) -> float:
    sel = facts[
        (facts["metric"] == metric) & (facts["entity"] == entity) & (facts["period_end"] == pd.Timestamp(period_end))
    ]
    assert len(sel) == 1, f"{metric}/{entity}/{period_end}: {len(sel)} rows"
    return float(sel["value"].iloc[0])


def cert_file(cert: int) -> Path:
    return FDIC_FIXTURE_DIR / fdic.CERT_FILE.format(cert=cert)


# ---------- dates and the charter list ----------


@pytest.mark.parametrize("s, d", [("20260630", dt.date(2026, 6, 30)), ("19840331", dt.date(1984, 3, 31)), (20251231, dt.date(2025, 12, 31))])
def test_parse_repdte(s, d):
    assert fdic.parse_repdte(s) == d


@pytest.mark.parametrize("bad", ["2026-06-30", "20260629", "20260531", "2026063", "", None, "abcdefgh"])
def test_bad_repdte_fails(bad):
    with pytest.raises(ValueError, match="REPDTE"):
        fdic.parse_repdte(bad)


@pytest.mark.parametrize(
    "merger, last_report",
    [
        (dt.date(2025, 5, 18), dt.date(2025, 3, 31)),  # Discover Bank into Capital One
        (dt.date(2022, 10, 3), dt.date(2022, 9, 30)),  # Capital One Bank (USA) into Capital One
        (dt.date(2018, 4, 1), dt.date(2018, 3, 31)),  # AmEx Bank FSB, effective the first day of a quarter
        (dt.date(2020, 9, 30), dt.date(2020, 6, 30)),  # on a quarter end: strictly before
    ],
)
def test_quarter_end_before(merger, last_report):
    assert fdic.quarter_end_before(merger) == last_report


def test_issuers_csv_lists_the_handoff_charters_with_their_mergers():
    issuers = fdic.load_issuers(REPO / "crosswalks" / "issuers.csv")
    assert len(issuers) == 29 and issuers["fdic_cert"].is_unique
    assert set(issuers["kind"]) == {"issuer", "sponsor"}
    assert int((issuers["kind"] == "sponsor").sum()) == 6
    merged = issuers[issuers["valid_to"].notna()].set_index("fdic_cert")
    assert {int(c): (d.date().isoformat(), int(t)) for c, d, t in zip(merged.index, merged["valid_to"], merged["merged_into"])} == {
        5649: ("2025-05-18", 4297),
        33954: ("2022-10-03", 4297),
        23702: ("2019-05-18", 628),
        35328: ("2018-04-01", 27471),
        34351: ("2024-06-01", 32188),
    }
    active = set(issuers.loc[issuers["valid_to"].isna(), "fdic_cert"])
    assert len(active) == 24 and set(merged["merged_into"].astype(int)) <= active
    by_cert = issuers.set_index("fdic_cert")
    assert by_cert.loc[5649, "issuer_id"] == "DISCOVER" and by_cert.loc[33954, "issuer_id"] == "CAPITAL_ONE"
    assert by_cert.loc[34404, "issuer_id"] == "WEBBANK" and by_cert.loc[34404, "kind"] == "sponsor"
    assert by_cert.loc[5649, "sec_cik"] == "1393612" and by_cert.loc[628, "sec_cik"] == "19617"


def test_series_csv_matches_issuers_csv(meta):
    """Six series per charter and six for the industry, nothing else; merged-out charters can never go stale."""
    issuers = fdic.load_issuers(REPO / "crosswalks" / "issuers.csv")
    mine = meta[meta["source"] == "fdic"]
    metrics = {m for m, _ in fdic.METRICS}
    assert set(mine["metric"]) == metrics and len(metrics) == 6
    expected_entities = {fdic.entity_for(int(c)) for c in issuers["fdic_cert"]} | {fdic.ENTITY_ALL}
    assert set(mine["entity"]) == expected_entities
    assert len(mine) == 6 * len(expected_entities) == 180
    assert set(mine.loc[mine["entity"] == fdic.ENTITY_ALL, "entity_type"]) == {"aggregate"}
    assert set(mine.loc[mine["entity"] != fdic.ENTITY_ALL, "entity_type"]) == {"bank"}
    assert (mine["period_type"] == "Q").all() and (mine["tier"] == "all").all() and (mine["unit"] == "usd_bn").all()
    merged = {fdic.entity_for(int(c)) for c in issuers.loc[issuers["valid_to"].notna(), "fdic_cert"]}
    assert set(mine.loc[mine["entity"].isin(merged), "max_age_days"]) == {36500.0}
    assert set(mine.loc[~mine["entity"].isin(merged), "max_age_days"]) == {200.0}


@pytest.mark.parametrize(
    "edit, match",
    [
        (lambda df: df.drop(columns=["kind"]), "missing columns"),
        (lambda df: df.assign(fdic_cert=df["fdic_cert"].replace("5649", "4297")), "listed twice"),
        (lambda df: df.assign(kind=df["kind"].replace("sponsor", "partner")), "kind must be one of"),
        (lambda df: df.assign(merged_into=df["merged_into"].replace("4297", "")), "must be set together"),
        (lambda df: df.assign(merged_into=df["merged_into"].replace("4297", "5649")), "not listed as an active charter"),
        (lambda df: df.assign(merged_into=df["merged_into"].replace("4297", "99999")), "not listed as an active charter"),
        (lambda df: df.assign(fdic_cert=df["fdic_cert"].replace("628", "628a")), "must be a number"),
        (lambda df: df.assign(bank_name=df["bank_name"].where(df["fdic_cert"] != "628", "")), "empty bank_name"),
        (lambda df: df.assign(valid_to=df["valid_to"].replace("2025-05-18", "05/18/2025")), "match format"),
    ],
)
def test_issuers_csv_inconsistencies_fail(tmp_path, edit, match):
    df = pd.read_csv(REPO / "crosswalks" / "issuers.csv", dtype=str, keep_default_na=False)
    path = tmp_path / "issuers.csv"
    edit(df).to_csv(path, index=False)
    with pytest.raises(ValueError, match=match):
        fdic.load_issuers(path)


# ---------- the checked-in charter files ----------


def test_charter_file_shapes():
    shapes = {
        4297: (170, "1984-03-31", "2026-06-30", "CAPITAL ONE NATIONAL ASSN"),
        5649: (165, "1984-03-31", "2025-03-31", "DISCOVER BANK"),
        33954: (112, "1994-12-31", "2022-09-30", "CAPITAL ONE BANK USA NA"),
        628: (170, "1984-03-31", "2026-06-30", "JPMORGAN CHASE BANK NA"),
        34404: (117, "1997-06-30", "2026-06-30", "WEBBANK"),
    }
    for cert, (n, first, last, name) in shapes.items():
        df = fdic.parse_cert_csv(cert_file(cert), cert)
        assert len(df) == n and (df["CERT"] == cert).all()
        assert df["REPDTE"].iloc[0].isoformat() == first and df["REPDTE"].iloc[-1].isoformat() == last
        assert df["NAME"].iloc[-1] == name
        assert df["REPDTE"].is_monotonic_increasing and df["REPDTE"].is_unique
    cap = fdic.parse_cert_csv(cert_file(4297), 4297)
    assert cap["LNCRCD"].notna().all() and cap["DRCRCDQ"].notna().all()
    assert cap.loc[cap["P3CRCD"].notna(), "REPDTE"].iloc[0] == dt.date(2001, 3, 31)  # 30-89 days collected from 2001
    assert cap.loc[cap["NCCRCD"].notna(), "REPDTE"].iloc[0] == dt.date(1991, 3, 31)  # 90+ and nonaccrual from 1991
    assert int(cap["P9CRCD"].isna().sum()) == 28


def test_headline_values(fdic_facts):
    f = fdic_facts
    cap, disc, cobusa, jpm, web = (fdic.entity_for(c) for c in (4297, 5649, 33954, 628, 34404))
    # Capital One, N.A. at 2026-06-30 (thousands in the file: 250,519,000 / 4,904,000 / 4,867,000 / 4,212,000 / 1,211,000 / 3,001,000)
    assert value(f, "fdic_card_loans", "2026-06-30", cap) == pytest.approx(250.519)
    assert value(f, "fdic_card_dq30_89", "2026-06-30", cap) == pytest.approx(4.904)
    assert value(f, "fdic_card_noncurrent", "2026-06-30", cap) == pytest.approx(4.867)  # P9CRCD 4,854,000 + NACRCD 13,000
    assert value(f, "fdic_card_charge_offs_q", "2026-06-30", cap) == pytest.approx(4.212)
    assert value(f, "fdic_card_recoveries_q", "2026-06-30", cap) == pytest.approx(1.211)
    assert value(f, "fdic_card_nco_q", "2026-06-30", cap) == pytest.approx(3.001)
    # Discover's book arriving: Capital One's own line jumps in 2025 Q2, Discover's ends in 2025 Q1
    assert value(f, "fdic_card_loans", "2025-03-31", cap) == pytest.approx(143.440421)
    assert value(f, "fdic_card_loans", "2025-06-30", cap) == pytest.approx(245.827607)
    assert value(f, "fdic_card_loans", "2025-03-31", disc) == pytest.approx(98.890692)
    assert value(f, "fdic_card_nco_q", "2025-03-31", disc) == pytest.approx(1.348255)
    assert f.loc[f["entity"] == disc, "period_end"].max() == pd.Timestamp("2025-03-31")
    # the earlier Capital One consolidation, 2022 Q4
    assert value(f, "fdic_card_loans", "2022-09-30", cobusa) == pytest.approx(98.973973)
    assert value(f, "fdic_card_loans", "2022-09-30", cap) == pytest.approx(16.790130)
    assert value(f, "fdic_card_loans", "2022-12-31", cap) == pytest.approx(126.365021)
    assert f.loc[f["entity"] == cobusa, "period_end"].max() == pd.Timestamp("2022-09-30")
    # JPMorgan Chase Bank at 2026-06-30: nonaccrual is 0, so noncurrent equals 90+ days past due
    assert value(f, "fdic_card_loans", "2026-06-30", jpm) == pytest.approx(216.172)
    assert value(f, "fdic_card_dq30_89", "2026-06-30", jpm) == pytest.approx(1.920)
    assert value(f, "fdic_card_noncurrent", "2026-06-30", jpm) == pytest.approx(2.083)
    assert value(f, "fdic_card_nco_q", "2026-06-30", jpm) == pytest.approx(1.664)
    # WebBank: a sponsor bank, small book, zero flows (0 is a value, not a gap)
    assert value(f, "fdic_card_loans", "2026-06-30", web) == pytest.approx(0.189672)
    assert value(f, "fdic_card_nco_q", "2026-06-30", web) == 0.0
    assert value(f, "fdic_card_noncurrent", "2026-06-30", web) == 0.0


def test_ytd_minus_prior_ytd_equals_the_quarterly_field_outside_merger_quarters():
    """Capital One, N.A. in 2024, a year with no merger: the hand difference of the year-to-date charge-off fields
    reproduces the FDIC's quarterly fields exactly. In 2022 Q4, when cert 33954 was absorbed, it does not, which
    is why the fetcher loads the quarterly fields and never de-cumulates."""
    df = fdic.parse_cert_csv(cert_file(4297), 4297).set_index("REPDTE")
    for ytd, q in (("DRCRCD", "DRCRCDQ"), ("CRCRCD", "CRCRCDQ"), ("NTCRCD", "NTCRCDQ")):
        prior = 0
        for month in (3, 6, 9, 12):
            row = df.loc[dt.date(2024, month, 30 if month in (6, 9) else 31)]
            assert int(row[ytd]) - prior == int(row[q]), (ytd, month)
            prior = int(row[ytd])
    q3, q4 = df.loc[dt.date(2022, 9, 30)], df.loc[dt.date(2022, 12, 31)]
    assert int(q4["NTCRCD"]) - int(q3["NTCRCD"]) == 2_786_384 and int(q4["NTCRCDQ"]) == 1_019_147
    assert int(q4["NTCRCD"]) == 2_926_715 and int(q3["NTCRCD"]) == 140_331


def test_identities_hold_on_every_checked_in_row():
    for cert in FDIC_FIXTURE_CERTS:
        df = fdic.parse_cert_csv(cert_file(cert), cert)
        nc = df.dropna(subset=["NCCRCD", "P9CRCD", "NACRCD"])
        assert len(nc) > 100 and (nc["NCCRCD"] == nc["P9CRCD"] + nc["NACRCD"]).all()
        assert (df["NTCRCDQ"] == df["DRCRCDQ"] - df["CRCRCDQ"]).all()


# ---------- the industry aggregate ----------


def test_aggregate_reproduces_the_quarterly_banking_profile(fdic_facts):
    agg = fdic.parse_aggregate_json(FDIC_FIXTURE_DIR / fdic.AGGREGATE_FILE)
    assert len(agg) == 170 and agg["REPDTE"].iloc[0] == dt.date(1984, 3, 31) and agg["REPDTE"].iloc[-1] == dt.date(2026, 6, 30)
    by = agg.set_index("REPDTE")
    # QBP 2Q 2026 Table II-A: 'Number of institutions reporting' 4,238 / 4,279 / 4,421 and 'Credit cards' in millions
    assert [int(by.loc[d, "count"]) for d in (dt.date(2026, 6, 30), dt.date(2026, 3, 31), dt.date(2025, 6, 30))] == [4238, 4279, 4421]
    assert int(by.loc[dt.date(2025, 6, 30), "LNCRCD"]) == 1_141_109_656
    all_ = fdic.ENTITY_ALL
    assert value(fdic_facts, "fdic_card_loans", "2025-06-30", all_) == pytest.approx(1141.109656)
    assert value(fdic_facts, "fdic_card_loans", "2026-03-31", all_) == pytest.approx(1161.264221)
    assert value(fdic_facts, "fdic_card_loans", "2026-06-30", all_) == pytest.approx(1189.600732)
    # Table V-A 'Credit card loans': 30-89 days 1.37, noncurrent 1.44 (percent of card loans)
    dq = value(fdic_facts, "fdic_card_dq30_89", "2026-06-30", all_) / value(fdic_facts, "fdic_card_loans", "2026-06-30", all_)
    nc = value(fdic_facts, "fdic_card_noncurrent", "2026-06-30", all_) / value(fdic_facts, "fdic_card_loans", "2026-06-30", all_)
    assert round(100 * dq, 2) == 1.37 and round(100 * nc, 2) == 1.44
    assert value(fdic_facts, "fdic_card_nco_q", "2026-06-30", all_) == pytest.approx(11.753098)
    # items start when they were collected: the 0 sums before are not loaded
    sub = fdic_facts[fdic_facts["entity"] == all_]
    first = sub.groupby("metric")["period_end"].min()
    assert first["fdic_card_dq30_89"] == pd.Timestamp("2001-03-31")
    assert first["fdic_card_noncurrent"] == pd.Timestamp("1991-03-31")
    assert first["fdic_card_loans"] == first["fdic_card_nco_q"] == pd.Timestamp("1984-03-31")
    assert int(by.loc[dt.date(2000, 12, 31), "P3CRCD"]) == 0 and int(by.loc[dt.date(2001, 3, 31), "P3CRCD"]) == 5_768_561
    assert len(sub) == N_AGG_FACTS


# ---------- the whole release ----------


def test_facts_cover_every_series_for_the_checked_in_charters_and_nothing_else(meta, fdic_facts):
    mine = meta[meta["source"] == "fdic"]
    entities = {fdic.entity_for(c) for c in FDIC_FIXTURE_CERTS} | {fdic.ENTITY_ALL}
    mine = mine[mine["entity"].isin(entities)]
    expected = set(mine[SERIES_KEY].itertuples(index=False, name=None))
    got = set(fdic_facts[SERIES_KEY].drop_duplicates().itertuples(index=False, name=None))
    assert got == expected, f"missing {expected - got}, extra {got - expected}"
    assert len(expected) == 36
    errors, warnings = validate(fdic_facts, mine)
    assert errors == []
    assert warnings == []
    assert (fdic_facts["period_type"] == "Q").all() and (fdic_facts["tier"] == "all").all()
    assert set(fdic_facts.loc[fdic_facts["entity"] == fdic.ENTITY_ALL, "entity_type"]) == {"aggregate"}
    assert set(fdic_facts.loc[fdic_facts["entity"] != fdic.ENTITY_ALL, "entity_type"]) == {"bank"}
    assert len(fdic_facts) == N_CERT_FACTS + N_AGG_FACTS
    assert fdic_facts["value"].notna().all()


def test_issuer_rollup_sums_acquirer_and_acquired(fdic_facts, fdic_issuers):
    con = _connect(fdic_facts, REPO / "sql" / "views.sql", issuers_csv=REPO / "crosswalks" / "issuers.csv")
    try:
        rows = con.execute(
            "SELECT entity, CAST(period_end AS DATE), value, n_certs, issuer_name FROM v_fdic_issuer "
            "WHERE metric = 'fdic_card_loans' ORDER BY entity, period_end"
        ).fetchall()
    finally:
        con.close()
    by = {(e, d.isoformat()): (v, n, name) for e, d, v, n, name in rows}
    entities = {e for e, *_ in rows}
    assert entities == {"ISSUER:CAPITAL_ONE", "ISSUER:JPMORGAN", "ISSUER:WEBBANK"}  # Discover rolls into Capital One
    cap = lambda d: value(fdic_facts, "fdic_card_loans", d, fdic.entity_for(4297))  # noqa: E731
    # one charter after the Discover merger, two before it, three before the 2022 consolidation
    assert by[("ISSUER:CAPITAL_ONE", "2026-06-30")] == (pytest.approx(250.519), 1, "Capital One")
    assert by[("ISSUER:CAPITAL_ONE", "2025-03-31")] == (pytest.approx(143.440421 + 98.890692), 2, "Capital One")
    three = cap("2022-09-30") + value(fdic_facts, "fdic_card_loans", "2022-09-30", fdic.entity_for(33954)) + value(
        fdic_facts, "fdic_card_loans", "2022-09-30", fdic.entity_for(5649)
    )
    assert by[("ISSUER:CAPITAL_ONE", "2022-09-30")] == (pytest.approx(three), 3, "Capital One")
    # the roll-up moves by a few billion across the merger quarter where the charter alone doubles
    rolled = by[("ISSUER:CAPITAL_ONE", "2025-06-30")][0] - by[("ISSUER:CAPITAL_ONE", "2025-03-31")][0]
    assert abs(rolled) < 10 and cap("2025-06-30") - cap("2025-03-31") > 100
    assert by[("ISSUER:JPMORGAN", "2026-06-30")] == (pytest.approx(216.172), 1, "JPMorgan Chase")
    assert by[("ISSUER:WEBBANK", "2026-06-30")] == (pytest.approx(0.189672), 1, "WebBank")
    # every metric rolls up, and the view never invents periods
    con = _connect(fdic_facts, REPO / "sql" / "views.sql", issuers_csv=REPO / "crosswalks" / "issuers.csv")
    try:
        n_metrics, n_rows = con.execute("SELECT COUNT(DISTINCT metric), COUNT(*) FROM v_fdic_issuer").fetchone()
        n_periods = con.execute(
            "SELECT COUNT(DISTINCT period_end) FROM facts WHERE source = 'fdic' AND entity_type = 'bank'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n_metrics == 6 and n_rows > 0
    assert n_periods == 170


# ---------- fetch end to end, with a fake API ----------


class FakeSession:
    """Serves the fixture files for the charters they cover; other charters get a header-only CSV, as the API does
    for an unknown certificate (200, zero rows)."""

    def __init__(self, status: int = 200, agg_override: bytes | None = None):
        self.status = status
        self.agg_override = agg_override
        self.calls: list[dict] = []

    def get(self, url, params=None):
        assert url == fdic.API_URL
        self.calls.append(params)
        if "agg_by" in params:
            body = self.agg_override if self.agg_override is not None else (FDIC_FIXTURE_DIR / fdic.AGGREGATE_FILE).read_bytes()
        else:
            cert = int(params["filters"].split(":")[1])
            path = cert_file(cert)
            body = path.read_bytes() if path.exists() else b'"BKCLASS","CERT","REPDTE"\n'
        status = self.status

        class R:
            content = body
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise requests.HTTPError(f"{status}")

        return R()


def fixture_issuers_csv(tmp_path: Path, certs=FDIC_FIXTURE_CERTS) -> Path:
    df = pd.read_csv(REPO / "crosswalks" / "issuers.csv", dtype=str, keep_default_na=False)
    path = tmp_path / "issuers.csv"
    df[df["fdic_cert"].astype(int).isin(certs)].to_csv(path, index=False)
    return path


def test_fetch_end_to_end(tmp_path, monkeypatch, meta, fdic_facts):
    monkeypatch.setattr(fdic, "ISSUERS_CSV", fixture_issuers_csv(tmp_path))
    raw = tmp_path / "fdic"
    (raw / "latest").mkdir(parents=True)
    (raw / "latest" / "financials_99999.csv").write_text("stale file from a charter no longer listed\n")
    session = FakeSession()
    facts = fdic.fetch(meta, raw, session, PULLED_AT)
    assert len(session.calls) == len(FDIC_FIXTURE_CERTS) + 1
    assert session.calls[0]["format"] == "csv" and session.calls[0]["limit"] == fdic.LIMIT
    assert session.calls[-1]["filters"] == "NOT BKCLASS:(NC OR OI)" and session.calls[-1]["agg_by"] == "REPDTE"
    assert sorted(p.name for p in (raw / "latest").iterdir()) == sorted(
        [fdic.CERT_FILE.format(cert=c) for c in FDIC_FIXTURE_CERTS] + [fdic.AGGREGATE_FILE]
    )
    assert (raw / "latest" / "financials_4297.csv").read_bytes() == cert_file(4297).read_bytes()
    assert len(facts) == len(fdic_facts)
    assert facts["period_end"].max() == Q2_2026


def test_fetch_fails_on_a_charter_with_no_rows(tmp_path, monkeypatch, meta):
    monkeypatch.setattr(fdic, "ISSUERS_CSV", fixture_issuers_csv(tmp_path, certs=(4297, 5649, 33954, 628, 34404, 7213)))
    with pytest.raises(ValueError, match="fields missing from the response"):
        fdic.fetch(meta, tmp_path / "fdic", FakeSession(), PULLED_AT)


def test_fetch_fails_on_http_errors_and_error_pages(tmp_path, monkeypatch, meta):
    monkeypatch.setattr(fdic, "ISSUERS_CSV", fixture_issuers_csv(tmp_path))
    with pytest.raises(requests.HTTPError):
        fdic.fetch(meta, tmp_path / "fdic", FakeSession(status=503), PULLED_AT)
    with pytest.raises(ValueError, match="unexpected response"):
        fdic.fetch(meta, tmp_path / "fdic", FakeSession(agg_override=b"<html>Access Denied</html>"), PULLED_AT)


# ---------- loud failures on the raw files ----------


def _rewrite(path: Path, fn) -> Path:
    df = pd.read_csv(cert_file(4297), dtype=str, keep_default_na=False)
    df = fn(df)
    df.to_csv(path, index=False)
    return path


@pytest.mark.parametrize(
    "fn, match",
    [
        (lambda df: df.drop(columns=["NTCRCDQ"]), r"fields missing from the response: \['NTCRCDQ'\]"),
        (lambda df: df.iloc[0:0], "no rows for cert 4297"),
        (lambda df: df.assign(CERT=df["CERT"].where(df.index != 5, "5649")), r"rows for cert\(s\) \['5649'\]"),
        (lambda df: df.assign(REPDTE=df["REPDTE"].where(df.index != 5, "20250515")), "not a quarter end"),
        (lambda df: df.assign(REPDTE=df["REPDTE"].where(df.index != 5, df["REPDTE"].iloc[6])), "duplicate quarters"),
        (lambda df: df.assign(LNCRCD=df["LNCRCD"].where(df.index != 5, "1.5e6")), "not an integer number of thousands"),
        (lambda df: df.assign(NCCRCD=df["NCCRCD"].where(df.index != 169, "1")), "NCCRCD is not P9CRCD \\+ NACRCD"),
        (lambda df: df.assign(NTCRCDQ=df["NTCRCDQ"].where(df.index != 169, "1")), "NTCRCDQ is not DRCRCDQ - CRCRCDQ"),
        (lambda df: pd.concat([df] * 59, ignore_index=True).assign(REPDTE=lambda d: d["REPDTE"]), "at the API limit"),
    ],
)
def test_charter_file_problems_fail(tmp_path, fn, match):
    path = _rewrite(tmp_path / "financials_4297.csv", fn)
    with pytest.raises(ValueError, match=match):
        fdic.parse_cert_csv(path, 4297)


def test_empty_charter_file_fails(tmp_path):
    path = tmp_path / "financials_4297.csv"
    path.write_text("")
    with pytest.raises(ValueError, match="empty response"):
        fdic.parse_cert_csv(path, 4297)


def _agg_doc() -> dict:
    return json.loads((FDIC_FIXTURE_DIR / fdic.AGGREGATE_FILE).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "fn, match",
    [
        (lambda d: {**d, "data": []}, "no aggregation buckets"),
        (lambda d: {**d, "data": d["data"][:50]}, "only 50 quarters"),
        (lambda d: {**d, "data": [{"data": {k: v for k, v in b["data"].items() if k != "sum_NCCRCD"}} for b in d["data"]]}, r"lacks \['sum_NCCRCD'\]"),
        (lambda d: {**d, "data": d["data"] + [d["data"][-1]]}, "duplicate quarters"),
        (lambda d: {**d, "data": [{"data": {**b["data"], "sum_LNCRCD": None}} for b in d["data"]]}, "expected a number"),
        (lambda d: {**d, "data": [{"data": {**b["data"], "count": 1.5}} for b in d["data"]]}, "not an integer"),
        (lambda d: {"meta": d["meta"]}, "no aggregation buckets"),
    ],
)
def test_aggregate_problems_fail(tmp_path, fn, match):
    path = tmp_path / fdic.AGGREGATE_FILE
    path.write_text(json.dumps(fn(_agg_doc())), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        fdic.parse_aggregate_json(path)


def test_aggregate_not_json_fails(tmp_path):
    path = tmp_path / fdic.AGGREGATE_FILE
    path.write_text("<html>maintenance</html>", encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON"):
        fdic.parse_aggregate_json(path)


def test_aggregate_item_that_is_zero_at_its_first_quarter_fails(tmp_path):
    doc = _agg_doc()
    for b in doc["data"]:
        if b["data"]["REPDTE"] == "20010331":
            b["data"]["sum_P3CRCD"] = 0
    path = tmp_path / fdic.AGGREGATE_FILE
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="sum_P3CRCD is 0 at 2001-03-31"):
        fdic.aggregate_facts(fdic.parse_aggregate_json(path), PULLED_AT)


# ---------- cross-checks between charters, mergers and the industry total ----------


def _release_dir(tmp_path: Path, drop_last_of: int | None = None, drop_last_bucket: bool = False) -> Path:
    latest = tmp_path / "latest"
    latest.mkdir()
    for cert in FDIC_FIXTURE_CERTS:
        text = cert_file(cert).read_text(encoding="utf-8")
        if cert == drop_last_of:
            text = "\n".join(text.splitlines()[:-1]) + "\n"
        (latest / fdic.CERT_FILE.format(cert=cert)).write_text(text, encoding="utf-8")
    doc = _agg_doc()
    if drop_last_bucket:
        doc["data"] = doc["data"][:-1]
    (latest / fdic.AGGREGATE_FILE).write_text(json.dumps(doc), encoding="utf-8")
    return latest


def test_release_parses_from_a_directory(tmp_path, fdic_issuers, fdic_facts):
    facts = fdic.parse_release(_release_dir(tmp_path), fdic_issuers, PULLED_AT)
    assert len(facts) == len(fdic_facts)


def test_active_charter_that_stops_filing_fails(tmp_path, fdic_issuers):
    with pytest.raises(ValueError, match="cert 628 .* last reports 2026-03-31, the industry total runs to 2026-06-30"):
        fdic.parse_release(_release_dir(tmp_path, drop_last_of=628), fdic_issuers, PULLED_AT)


def test_merged_charter_must_end_the_quarter_before_its_merger(tmp_path, fdic_issuers):
    with pytest.raises(ValueError, match="cert 5649 .* merged 2025-05-18 should end with the 2025-03-31 call report, last row is 2024-12-31"):
        fdic.parse_release(_release_dir(tmp_path, drop_last_of=5649), fdic_issuers, PULLED_AT)


def test_industry_total_behind_the_charters_fails(tmp_path, fdic_issuers):
    with pytest.raises(ValueError, match="the industry total runs to 2026-03-31"):
        fdic.parse_release(_release_dir(tmp_path, drop_last_bucket=True), fdic_issuers, PULLED_AT)


def test_missing_or_unlisted_charter_files_fail(tmp_path, fdic_issuers):
    latest = _release_dir(tmp_path)
    (latest / "financials_4297.csv").unlink()
    (latest / "financials_7213.csv").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing \['financials_4297.csv'\], not in issuers.csv \['financials_7213.csv'\]"):
        fdic.parse_release(latest, fdic_issuers, PULLED_AT)
    (latest / "financials_7213.csv").unlink()
    (latest / "financials_4297.csv").write_bytes(cert_file(4297).read_bytes())
    (latest / fdic.AGGREGATE_FILE).unlink()
    with pytest.raises(ValueError, match="no aggregate_by_repdte.json"):
        fdic.parse_release(latest, fdic_issuers, PULLED_AT)
