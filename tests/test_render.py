"""Rendering: the page is self-contained, embeds the data, opens on the latest readings, and every card carries its
footer, status, notes, checks and PNG where flagged. The numbers asserted come from the checked-in fixtures."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest
from PIL import Image

from carddash.loader import write_facts
from carddash.render import (
    BENCHMARK_LABEL,
    THESIS_NOTE,
    STATE_TILES,
    VIEW_BOUNDS,
    VIEW_BOUNDS_UNCHECKED,
    _check_bounds,
    _connect,
    PANELS,
    _benchmark,
    _missing_periods,
    _new_periods,
    _recessions,
    _rel_display,
    headlines,
    render,
)
from carddash.schema import SERIES_KEY, coerce_facts
from conftest import FIXTURES, PULLED_AT, REPO

RUN1 = "2026-09-06T00:00:00Z"
RUN2 = "2026-09-07T00:00:00Z"  # the fixtures' pulled_at
TODAY = dt.date(2026, 9, 8)
POST_CHARTS = {"revolving_level", "card_apr", "card_access", "card_nco", "hhdc_dq90_by_age", "debt_service", "dfa_credit_by_wealth"}
ALL_SOURCES = ("fred", "tccp", "phillyfed", "nyfed_hhdc", "fdic", "nyfed_sce", "nyfed_sce_monthly", "bea", "census", "ncua",
               "dfa", "cfpb_cct", "nyfed_state", "issuer_8k")


def _health(generated_at: str, sources=("fred",), status="ok") -> dict:
    return {
        "generated_at": generated_at,
        "sources": {
            s: {"source": s, "status": status, "messages": [], "last_period_end": "2026-08-26", "pulled_at": generated_at,
                "rows": 1, "n_series": 1, "n_revisions": 0}
            for s in sources
        },
    }


def _payload(html: str) -> dict:
    start = html.index("window.CARDDASH = ") + len("window.CARDDASH = ")
    return json.loads(html[start:html.index(";</script>", start)])


@pytest.fixture(scope="session")
def page(session_paths, fixture_facts):
    """The rendered page, built once for the whole session: every test that takes it only reads it."""
    write_facts(fixture_facts, session_paths.facts_csv)
    session_paths.health_json.write_text(json.dumps(_health(RUN2, ALL_SOURCES)), encoding="utf-8")
    html = render(session_paths, today=TODAY).read_text(encoding="utf-8")
    return html, _payload(html)


def test_page_is_self_contained_and_carries_every_chart(page):
    html, payload = page
    assert "uPlot" in html and "<script src=" not in html  # vendored, no runtime fetches
    for panel in PANELS:
        for c in panel["charts"]:
            assert f'data-chart="{c["id"]}"' in html
    assert [p["name"] for p in PANELS] == ["Growth", "Pricing", "Access", "Performance", "Borrowers", "Distribution", "Spend", "Context"]
    assert len(payload["charts"]) == sum(len(p["charts"]) for p in PANELS) == 77
    assert html.index("<h2>Access</h2>") > html.index("<h2>Pricing</h2>")
    assert html.index("<h2>Context</h2>") > html.index("<h2>Borrowers</h2>")
    assert payload["default_years"] == 5 and len(payload["recessions"]) == 8
    # the health strip sits below the charts, a one-line summary sits at the top
    assert html.index('<h2 id="health">Source health</h2>') > html.index("<h2>Context</h2>")
    assert "14 sources OK" in html.split('<h2 id="readings">Latest readings</h2>')[0]
    assert "Sources: Federal Reserve Board, via FRED; CFPB Terms of Credit Card Plans survey" in html


def test_fred_charts(page):
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    assert by_id["revolving_level"]["n_points"] > 600
    assert by_id["card_apr"]["n_points"] > 1000 and by_id["card_apr"]["period_type"] is None  # quarterly plus weekly
    assert 400 < by_id["revolving_yoy"]["n_points"] < 700  # v_growth works and the 'since' cut applies
    assert len(by_id["card_apr"]["footer_lines"]) == 3  # two cadences: one line per series
    assert "latest period 2026-09-02" in by_id["card_apr"]["footer"]  # the weekly advertised rate is the newest
    assert by_id["card_apr"]["status"] == "ok" and by_id["card_apr"]["attempted"] is None
    apr_only = by_id["card_nco"]  # one source, one cadence, and it has fixture rows
    assert "data as of 2026-09-07" in apr_only["footer"] and "data as of" not in apr_only["caption"]
    assert apr_only["caption"].startswith("Source: Federal Reserve Board, via FRED · quarterly · latest period 2026 Q2")
    assert apr_only["footer_lines"] == []  # one source, one cadence: nothing to split out
    # SLOOS is dated to the quarter it asks about: the July 2026 survey is 2026 Q2, nothing is in the future
    assert by_id["sloos_cards"]["n_points"] > 100 and "latest period 2026 Q2" in by_id["sloos_cards"]["footer"]


def test_offered_vs_paid_band_and_spread(page):
    html, payload = page
    ovp = {c["id"]: c for c in payload["charts"]}["offered_vs_paid"]
    assert len(ovp["series"]) == 5 and ovp["n_points"] > 10 and ovp["band"] == [1, 2]
    assert [s["unit"] for s in ovp["series"]] == ["pct", "pct", "pct", "pct", "pp"]
    assert ovp["series"][4]["dash"] and not ovp["series"][3]["dash"]
    assert "percentage points" in ovp["series"][4]["label"]
    last = lambda k: max(i for i, v in enumerate(ovp["data"][k]) if v is not None)  # noqa: E731
    assert last(5) <= max(last(1), last(2), last(3))  # the spread stops at the last offered period
    assert last(4) > last(5)  # the paid rate runs on past it
    # the band is the range across the tiers: hi >= mid >= lo wherever all three exist
    for hi, lo, mid in zip(ovp["data"][1], ovp["data"][2], ovp["data"][3]):
        if hi is not None and lo is not None and mid is not None:
            assert hi >= mid >= lo
    assert len(ovp["footer_lines"]) == 5  # two sources and two cadences: one line per series
    assert "CFPB Terms of Credit Card Plans" in ovp["footer"] and "Federal Reserve Board" in ovp["footer"]
    assert any("secured cards" in n for n in ovp["notes"])


def test_new_charts_draw_the_other_sources(page):
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    loans = by_id["card_loans_by_issuer"]
    assert [s["label"] for s in loans["series"]][:2] == ["Capital One (with Discover)", "JPMorgan Chase"]
    assert loans["n_points"] == 170 and any(v is not None for v in loans["data"][1])
    rates = by_id["nco_by_issuer"]
    assert len(rates["series"]) == 7 and rates["series"][6]["dash"]
    assert any(v is not None for v in rates["data"][7])  # the industry rate from the aggregate fixture
    tri = by_id["dq_triangulation"]
    assert len(tri["series"]) == 4 and tri["n_points"] > 80
    assert sum(1 for k in range(1, 5) if any(v is not None for v in tri["data"][k])) == 3  # no Fed 30+ fixture
    assert any("charged-off balances" in n.lower() or "charged-off" in n for n in tri["notes"])
    age = by_id["hhdc_dq90_by_age"]
    assert [s["label"] for s in age["series"]] == ["Ages 18-29", "Ages 30-39", "Ages 40-49", "Ages 50-59", "Ages 60-69", "Ages 70+"]
    assert age["n_points"] == 106 and age["post"]
    pay = by_id["y14_payment_behavior"]
    assert len(pay["series"]) == 4 and pay["series"][3]["benchmark"]  # three shares plus the benchmark
    scores = by_id["y14_origination_scores"]
    assert scores["y_zero"] is False and scores["unit"] == "score" and scores["n_points"] > 50
    assert by_id["card_apr"]["y_zero"] is True
    assert {c["id"] for c in payload["charts"] if c["post"]} == POST_CHARTS
    assert 'href="img/hhdc_dq90_by_age.png"' in html and 'href="img/card_dq.png"' in html  # every chart has a PNG


def test_derived_views_draw_the_context_charts(page):
    """The macro series and the views over them: burden, real balances, spread over prime, payment flows."""
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    # card debt as a share of income: quarterly points only, from a monthly-keyed view
    burden = by_id["card_debt_burden"]
    assert burden["n_points"] > 200 and len([v for v in burden["data"][1] if v is not None]) > 90
    assert burden["series"][1]["benchmark"]
    # nominal and real on one chart, equal at the latest CPI reading by construction
    real = by_id["revolving_real"]
    nom = [v for v in real["data"][1] if v is not None]
    defl = [v for v in real["data"][2] if v is not None]
    assert abs(len(nom) - len(defl)) <= 1 and len(defl) > 600  # the deflated line needs a CPI month to match
    assert abs(nom[-1] - defl[-1]) / nom[-1] < 0.01  # the base period is the latest CPI month
    assert defl[0] > nom[0] * 3  # 1968 dollars restated at today's prices
    # spread over prime
    spread = by_id["apr_over_prime"]
    assert len(spread["series"]) == 3 and spread["series"][2]["unit"] == "pp" and spread["series"][2]["dash"]
    apr, prime, sp = (spread["data"][k] for k in (1, 2, 3))
    pairs = [(a, p, s) for a, p, s in zip(apr, prime, sp) if a is not None and p is not None and s is not None]
    assert len(pairs) > 100 and all(abs((a - p) - s) < 1e-9 for a, p, s in pairs)
    # payment rate and revolver share from the Y-14 identity
    flows = by_id["y14_payment_flows"]
    assert len([v for v in flows["data"][1] if v is not None]) > 50
    assert all(55 < v < 130 for v in flows["data"][1] if v is not None)  # payment rate, percent of opening balance
    assert all(60 < v < 85 for v in flows["data"][2] if v is not None)  # revolving share of balances
    assert max(v for v in flows["data"][1] if v is not None) > 105  # the 2021-22 payment-rate spike clears 100
    # per-account credit: limit above unused above balance, every quarter
    per = by_id["per_account_credit"]
    rows = [(lim, avail, bal) for lim, avail, bal in zip(per["data"][1], per["data"][2], per["data"][3])
            if None not in (lim, avail, bal)]
    assert len(rows) > 90 and all(lim > avail > bal for lim, avail, bal in rows)
    assert all(abs(lim - avail - bal) < 1e-6 for lim, avail, bal in rows)
    # the Context panel
    assert by_id["debt_service"]["n_points"] == 85 and len(by_id["debt_service"]["series"]) == 2
    assert by_id["sentiment"]["y_zero"] is False and by_id["sentiment"]["unit"] == "index"
    assert by_id["consumer_credit_mix"]["n_points"] > 600
    assert by_id["losses_vs_labor"]["n_points"] > 400 and len(by_id["losses_vs_labor"]["series"]) == 2
    assert len(by_id["card_vs_consumer"]["series"]) == 4


def test_access_panel_draws_the_credit_access_survey(page):
    """The survey's own readings, and the gap between score bands that the aggregate hides."""
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    access = by_id["card_access"]
    assert len(access["series"]) == 4 and access["n_points"] == 39
    assert all(s["cadence"] == "every four months" for s in access["series"])
    assert "latest period Jun 2026" in access["footer"]  # a wave is labelled by the month it was fielded in
    rej = by_id["rejection_by_score"]
    labels = [s["label"] for s in rej["series"]]
    assert labels == ["Score under 680", "Score 680 to 760", "Score over 760"]
    sub, mid, top = (next(v for v in reversed(rej["data"][k]) if v is not None) for k in (1, 2, 3))
    assert sub > mid > top and sub > 40 and top < 5  # the June 2026 wave: 46.6, 11.1, 3.3
    # the back door: lender-initiated closures, and the households that never applied
    closures = by_id["lender_closures"]
    assert len(closures["series"]) == 3 and closures["n_points"] == 38  # the score split starts one wave later
    assert any("back door" in n for n in closures["notes"])
    assert any("never appear in any approval" in n for n in by_id["discouraged"]["notes"])
    # every survey chart names its sample-size caveat
    for cid in ("rejection_by_score", "lender_closures", "discouraged"):
        assert any("about 150 respondents" in n for n in by_id[cid]["notes"]), cid
    # the front door, and the size of the line behind it, from the Y-14 series
    mix = by_id["origination_mix"]
    accounts, dollars = (next(v for v in reversed(mix["data"][k]) if v is not None) for k in (1, 2))
    assert accounts > dollars * 3  # subprime is about a fifth of new accounts and a twenty-fifth of new credit lines
    limits = by_id["origination_limits"]
    sub, mid, top = (next(v for v in reversed(limits["data"][k]) if v is not None) for k in (1, 2, 3))
    assert sub < mid < top and limits["unit"] == "usd"


def test_benchmark_is_the_2015_2019_mean_of_the_first_series(page, fixture_facts):
    html, payload = page
    nco = {c["id"]: c for c in payload["charts"]}["card_nco"]
    assert len(nco["series"]) == 4 and nco["series"][3]["benchmark"] and nco["series"][3]["dash"]
    assert nco["series"][3]["label"] == f"All commercial banks, {BENCHMARK_LABEL}"
    line = nco["data"][4]
    assert len(set(line)) == 1
    s = fixture_facts[(fixture_facts["metric"] == "card_nco_rate_sa") & (fixture_facts["entity"] == "COMBANKS_ALL")]
    window = s[(s["period_end"] >= "2015-01-01") & (s["period_end"] <= "2019-12-31")]["value"]
    assert len(window) == 20 and line[0] == pytest.approx(window.mean())
    # a chart whose first series has no fixture rows gets no benchmark line
    dq = {c["id"]: c for c in payload["charts"]}["card_dq"]
    assert not any(s["benchmark"] for s in dq["series"])
    assert _benchmark([dt.date(2014, 12, 31), dt.date(2016, 6, 30), dt.date(2019, 12, 31), dt.date(2020, 3, 31)], [9.0, 1.0, 3.0, 9.0]) == 2.0
    assert _benchmark([dt.date(2021, 1, 1)], [1.0]) is None


def test_checks_list_the_golden_numbers_of_the_chart(page):
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    checks = by_id["revolving_level"]["checks"]
    assert len(checks) == 5 and all(c["ok"] for c in checks)
    assert checks[0]["period"] == "Jun 2026" and checks[0]["expected"] == 1354.4  # newest first
    assert checks[0]["origin_url"].startswith("https://www.federalreserve.gov/releases/g19/")
    assert "Checked against the release (5)" in html
    assert {c["id"] for c in by_id["card_apr"]["checks"]} == {
        "g19_card_apr_all_2026_q1", "g19_card_apr_all_2026_q2", "g19_card_apr_assessed_2026_q1", "g19_card_apr_assessed_2026_q2",
    }
    assert by_id["bank_card_loans_weekly"]["checks"] == []


def test_recessions_are_month_ranges():
    rec = _recessions()
    assert len(rec) == 8
    first, last = rec[0], rec[-1]
    epoch = dt.date(1970, 1, 1)
    assert epoch + dt.timedelta(seconds=first[0]) == dt.date(1969, 12, 1)
    assert epoch + dt.timedelta(seconds=first[1]) == dt.date(1970, 11, 30)
    assert epoch + dt.timedelta(seconds=last[0]) == dt.date(2020, 2, 1)
    assert epoch + dt.timedelta(seconds=last[1]) == dt.date(2020, 4, 30)


def test_latest_readings_are_computed_from_the_facts(page, session_paths, fixture_facts):
    html, payload = page
    items = headlines(fixture_facts, TODAY)
    labels = [h["label"] for h in items]
    assert labels[0].startswith("Revolving consumer credit") and len(items) == 15  # no 30+ delinquency fixture
    rev = items[0]["text"]
    # the 2026-09-08 G.19 vintage added July 2026 at $1,357bn, above the October 2024 peak, so the record flag fires
    assert rev == "$1,357bn in Jul 2026, +3.6% on the year, highest on record (since Jan 1968)"
    # the flag logic on a synthetic series: a record, a three-year high, and a value with no flag
    base = fixture_facts[(fixture_facts["metric"] == "revolving_credit_sa")].sort_values("period_end").copy()
    record = base.copy()
    record.loc[record["period_end"] == record["period_end"].max(), "value"] = 9999.0
    assert "highest on record (since Jan 1968)" in headlines(coerce_facts(record), TODAY)[0]["text"]
    low = base.copy()
    low.loc[low["period_end"] == low["period_end"].max(), "value"] = 0.5
    assert "lowest on record (since Jan 1968)" in headlines(coerce_facts(low), TODAY)[0]["text"]
    apr = next(h for h in items if h["label"].startswith("APR paid"))["text"]
    assert apr.startswith("22.15% in 2026 Q2, ") and "pp on the year" in apr and BENCHMARK_LABEL in apr
    sloos = next(h for h in items if h["label"].startswith("Banks tightening"))["text"]
    assert sloos.startswith("6.70% in 2026 Q2") and BENCHMARK_LABEL not in sloos  # a net balance has no benchmark
    assert ('<strong><a href="#revolving_level" title="Go to the chart">Revolving consumer credit, all lenders (Fed G.19, SA)</a>:</strong> $1,357bn in Jul 2026' in html)
    text = (session_paths.docs / "latest.txt").read_text(encoding="utf-8")  # written by the page fixture
    assert text.startswith("US credit card data, latest readings (") and "- Revolving consumer credit" in text
    assert 'href="latest.txt"' in html


def test_card_badge_and_last_attempt_when_a_source_is_not_ok(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    health = _health(RUN2, ALL_SOURCES)
    health["sources"]["tccp"]["status"] = "stale"
    health["sources"]["fdic"]["status"] = "failed"
    health["sources"]["fdic"]["pulled_at"] = "2026-09-08T00:00:00Z"
    tmp_paths.health_json.write_text(json.dumps(health), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    by_id = {c["id"]: c for c in _payload(html)["charts"]}
    assert by_id["card_apr"]["status"] == "ok"
    assert by_id["offered_vs_paid"]["status"] == "stale" and by_id["offered_vs_paid"]["status_label"] == "Stale"
    assert by_id["nco_by_issuer"]["status"] == "failed" and by_id["nco_by_issuer"]["attempted"] == "2026-09-08"
    assert "data as of 2026-09-07" in by_id["nco_by_issuer"]["footer"]  # the data on the chart is still the last good load
    assert '<span class="badge st-failed"' in html and "last fetch attempt 2026-09-08" in html
    assert "2 of 14 sources need attention (Failed)" in html


def test_png_export_is_byte_stable_and_carries_no_pull_date(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    render(tmp_paths, today=TODAY)
    img = tmp_paths.docs / "img"
    assert {p.stem for p in img.glob("*.png")} == {c["id"] for panel in PANELS for c in panel["charts"]}
    assert POST_CHARTS <= {p.stem for p in img.glob("*.png")}
    first = {p.name: p.read_bytes() for p in img.glob("*.png")}
    for name, data in first.items():
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert b"Software" not in data and b"2026-09-07" not in data
        with Image.open(img / name) as im:
            assert im.size == (1200, 675)
    # a second render of unchanged data, with a different health stamp, yields identical bytes
    tmp_paths.health_json.write_text(json.dumps(_health("2026-09-08T00:00:00Z")), encoding="utf-8")
    render(tmp_paths, today=TODAY)
    second = {p.name: p.read_bytes() for p in img.glob("*.png")}
    assert second == first


def test_what_changed_lists_only_the_latest_run(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    shutil.copyfile(FIXTURES / "revisions.csv", tmp_paths.revisions_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    assert "Latest run 2026-09-07 00:00 UTC" in html
    assert "3 values revised by the source, largest first (2 listed; revisions under 0.1% are only counted)" in html
    assert "Card balances (NY Fed CCP), 2026 Q1: 1252 → 1242 (0.80%)" in html
    assert "Issuers with a card above 30% purchase APR, 2025 H2: 0 → 13 (from 0)" in html  # old value 0: no percentage
    changed = html.split('<h2 id="changed">What changed</h2>')[1].split('<h2 id="health">')[0]
    assert "Card APR, all accounts" not in changed  # the 0.05% revision is counted, not listed
    assert "Card balances (Y-14 large banks)" not in changed  # run 1's revision is not shown
    assert 'href="data/revisions.csv"' in html and (tmp_paths.docs / "data" / "revisions.csv").exists()
    # every fixture row carries RUN2, so every source is a first load: new periods are summarized per cadence
    assert "New periods loaded in this run:" in html
    assert "Federal Reserve Board, via FRED: " in html and " monthly periods through Aug 2026" in html
    # keyed on the run: pointing health at run 1 lists run 1, even though run 2's rows are newer in the file
    tmp_paths.health_json.write_text(json.dumps(_health(RUN1)), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    assert "Latest run 2026-09-06 00:00 UTC" in html
    assert "3 values revised by the source, largest first (1 listed" in html
    assert "Card balances (Y-14 large banks), 2026 Q1: 940 → 948.7 (0.93%)" in html
    assert "No new periods in this run." in html  # no fixture row carries run 1's stamp
    # no health file yet: the block says so instead of guessing
    tmp_paths.health_json.unlink()
    assert "No refresh run recorded yet." in render(tmp_paths, today=TODAY).read_text(encoding="utf-8")


def test_a_period_that_has_not_ended_is_never_the_latest(tmp_paths, fixture_facts):
    future = fixture_facts[(fixture_facts["metric"] == "sloos_card_standards_net_tightening")].tail(1).copy()
    future["period_end"] = pd.Timestamp("2099-12-31")
    write_facts(pd.concat([fixture_facts, future], ignore_index=True), tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    sloos = next(c for c in _payload(html)["charts"] if c["id"] == "sloos_cards")
    assert sloos["series"][0]["last_period"] == "2026 Q2" and "latest period 2026 Q2" in sloos["footer"]
    assert sloos["data"][0][-1] > 4_000_000_000  # the row itself is still drawn, it is just not called latest
    assert "2099" not in html.split('<h2 id="changed">What changed</h2>')[0]  # neither the readings nor the cards call 2099 latest


def test_thesis_watch_evaluates_every_falsification_test(page, session_paths, fixture_facts):
    """The dated note names thresholds; the page checks them from the data and cannot pass one by default."""
    html, payload = page
    from carddash.render import THESIS_TESTS, _connect, thesis_status

    con = _connect(fixture_facts, REPO / "sql" / "views.sql", REPO / "data" / "raw" / "tccp" / "tccp_products.csv",
                   REPO / "crosswalks" / "issuers.csv")
    try:
        status = thesis_status(con, TODAY)
    finally:
        con.close()
    assert len(status) == len(THESIS_TESTS) == 3
    by_label = {t["label"]: t for t in status}
    flow = by_label["Flow into 90+ day delinquency stays below 7.5%"]
    assert flow["value"] == pytest.approx(6.97) and flow["period"] == "2026 Q2" and flow["holds"] is True
    nco = by_label["Card charge-off rate stays below 4.2%"]
    assert nco["value"] == pytest.approx(3.82) and nco["holds"] is True
    spread = by_label["Card APR margin over prime stays above 14 points"]
    assert spread["value"] == pytest.approx(15.40, abs=0.01) and spread["holds"] is True and spread["unit"] == "pp"
    assert all(t["why"] for t in status)
    # the page and the text file both carry it
    assert "Thesis watch" in html and "st-ok" in html.split("Thesis watch")[1][:400]
    assert THESIS_NOTE in html
    text = (session_paths.docs / "latest.txt").read_text(encoding="utf-8")  # written by the page fixture
    assert "Thesis of 2026-09-08 (holds)" in text and text.count("- [ok ]") == 3


def test_a_broken_thesis_test_says_so(tmp_paths, fixture_facts):
    """Push the charge-off rate above its threshold: the page must report the test broken, not quietly pass."""
    facts = fixture_facts.copy()
    latest = facts[(facts["metric"] == "card_nco_rate_sa") & (facts["entity"] == "COMBANKS_ALL")]["period_end"].max()
    hit = (facts["metric"] == "card_nco_rate_sa") & (facts["entity"] == "COMBANKS_ALL") & (facts["period_end"] == latest)
    facts.loc[hit, "value"] = 5.5
    write_facts(coerce_facts(facts), tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2, ALL_SOURCES)), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    watch = html.split("Thesis watch")[1][:1400]
    assert "st-failed" in watch and "Broken" in watch
    assert "this test has broken" in watch
    text = (tmp_paths.docs / "latest.txt").read_text(encoding="utf-8")
    assert "(BROKEN)" in text and "- [BROKEN]" in text


def test_browse_payload_covers_every_series_and_reconstructs_it(page, fixture_facts):
    """Every series in facts is browsable, and its compact encoding rebuilds the original values exactly."""
    from carddash.render import browse_payload
    from carddash.series import load_series, series_index

    html, payload = page
    b = payload["browse"]
    meta_idx = series_index(load_series(REPO / "crosswalks" / "series.csv"))
    direct = browse_payload(fixture_facts, meta_idx, TODAY)
    keys = {s["key"] for s in b["series"]}
    assert len(b["series"]) == len(direct["series"]) == len(keys)  # keys are unique
    in_facts = {"|".join(str(x) for x in k)
                for k in fixture_facts[SERIES_KEY].drop_duplicates().itertuples(index=False, name=None)}
    assert keys == in_facts  # nothing loaded is missing from the browser
    assert b["grids"] and all(g in b["grids"] for g in {s["grid"] for s in b["series"]})
    # rebuild one series from the grid and compare against facts
    one = next(s for s in b["series"] if s["key"].startswith("y14_card_utilization_active_p50|"))
    grid = b["grids"][one["grid"]]
    xs = grid[one["start"]: one["start"] + len(one["values"])]
    pairs = [(dt.datetime.fromtimestamp(x, dt.timezone.utc).date(), v)
             for x, v in zip(xs, one["values"]) if v is not None]
    src = fixture_facts[(fixture_facts["metric"] == "y14_card_utilization_active_p50")].sort_values("period_end")
    assert len(pairs) == len(src) == one["n"]
    assert [p[0] for p in pairs] == [d.date() for d in src["period_end"]]
    assert [p[1] for p in pairs] == [round(float(v), 6) for v in src["value"]]
    assert one["last_value"] == pytest.approx(float(src["value"].iloc[-1]))
    assert one["cadence"] == "quarterly" and one["unit"] == "pct" and one["note"]
    # the charted flag matches the panels, and most series have no chart
    charted = {s["key"] for s in b["series"] if s["charted"]}
    plotted = {"|".join([x["metric"], x["entity"], x["tier"], x["period_type"], x["source"]])
               for pa in PANELS for c in pa["charts"] for x in c["series"]}
    assert charted == plotted & keys
    assert len(b["series"]) - len(charted) > 100  # the fixtures carry 5 FDIC charters, the live pull carries 35
    # no series is dated into the future
    for s in b["series"]:
        end = b["grids"][s["grid"]][s["start"] + len(s["values"]) - 1]
        assert dt.datetime.fromtimestamp(end, dt.timezone.utc).date() <= TODAY
    # the section is on the page with a working control set
    assert 'id="browse"' in html and 'id="browse-table"' in html and 'id="browse-q"' in html
    assert f'{len(b["series"])} series' in html


def test_browse_payload_is_empty_without_facts(tmp_paths):
    from carddash.render import browse_payload
    assert browse_payload(coerce_facts(pd.DataFrame(columns=SERIES_KEY + ["period_end", "value", "entity_type", "pulled_at"])), {}, TODAY) == {"grids": {}, "series": []}


def test_rel_display_is_capped_when_the_old_value_is_zero():
    assert _rel_display(100.0, 0.0079872) == "0.80%"
    assert _rel_display(0.0, float("inf")) == "from 0"
    assert _rel_display(0.0, 5.0) == "from 0"
    assert _rel_display(2.0, float("nan")) == "from 0"


def test_new_periods_are_the_periods_only_this_run_loaded(fixture_facts):
    facts = fixture_facts.copy()
    facts["pulled_at"] = RUN1
    fred_m = (facts["source"] == "fred") & (facts["period_type"] == "M")
    latest_two = sorted(facts.loc[fred_m, "period_end"].unique())[-2:]
    facts.loc[fred_m & facts["period_end"].isin(latest_two), "pulled_at"] = RUN2  # two new months
    revised = (facts["source"] == "fred") & (facts["period_type"] == "Q") & (facts["period_end"] == pd.Timestamp("2025-12-31"))
    facts.loc[revised, "pulled_at"] = RUN2  # a revised value in an old period is not a new period
    facts.loc[facts["source"] == "tccp", "pulled_at"] = RUN2  # a source loaded for the first time in this run
    out = _new_periods(coerce_facts(facts), RUN2)
    assert [o["source"] for o in out] == ["fred", "tccp"]
    assert out[0]["periods"] == ["Jul 2026, Aug 2026 (monthly)"]
    assert out[1]["periods"] == ["2 semiannual periods through 2025 H2"]  # the two checked-in workbooks
    assert _new_periods(coerce_facts(facts), None) == []
    assert _new_periods(coerce_facts(facts.iloc[0:0]), RUN2) == []


def test_holder_and_sloos_demand_charts(page):
    _, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    holders = ids["revolving_by_holder"]
    assert [s["label"][:10] for s in holders["series"]] == ["Depository", "Credit uni", "Finance co"]
    share = ids["holder_share"]
    last = [ys[-1] for ys in share["data"][1:]]
    assert 5 < last[0] < 9 and 0.5 < last[1] < 3  # credit unions about 6.5%, finance companies about 1% of the NSA total
    sloos = ids["sloos_cards"]
    assert [s["label"] for s in sloos["series"]][1].startswith("Net % of banks reporting stronger")
    assert sloos["series"][1]["last_period"] == "2026 Q2"
    by_size = ids["sloos_demand_by_size"]
    assert by_size["data"][1][-1] == 0.0 and by_size["data"][2][-1] == 3.8


def test_page_carries_link_previews_nav_and_next_release(page, session_paths, fixture_facts):
    html, payload = page
    head = html.split("</head>")[0]
    assert '<meta property="og:image" content="https://user5017.github.io/credit-card-data/img/revolving_level.png">' in head
    assert '<meta name="description" content="Revolving consumer credit, all lenders: $1,357bn in Jul 2026; ' in head
    assert '<meta name="twitter:card" content="summary_large_image">' in head
    assert '<link rel="icon" href="data:image/svg+xml,' in head
    assert '<nav class="nav" aria-label="Sections">' in html and 'href="#panel-performance"' in html
    assert '<section class="panel" id="panel-growth">' in html
    for panel in PANELS:
        for c in panel["charts"]:
            assert f'<button type="button" class="link" data-link="{c["id"]}"' in html
    # the thesis note is served next to the page, not linked to the repository blob view
    assert (session_paths.docs / THESIS_NOTE).exists()
    assert f'href="{THESIS_NOTE}"' in html and "blob/main/design" not in html
    # the note it revises is kept and linked, so the change of mind stays legible
    assert (REPO / "design" / "thesis-2026-09-08.html").exists()
    assert "thesis-2026-09-08.html" in (REPO / THESIS_NOTE).read_text(encoding="utf-8")
    # the thesis tests link to their charts
    assert '<a class="chartlink" href="#card_nco"' in html
    # next expected release per source, from the newest loaded period plus the typical lag
    health = html.split('<h2 id="health">')[1]
    assert "Aug 2026 data expected around 2026-10-08" in health  # July loaded (2026-09-08 vintage): month end plus 38 days
    assert "2026 Q3 data expected around 2026-11-11" in health  # NY Fed HHDC: quarter end plus 42 days
    assert 'id="theme"' in html and '@media (max-width: 640px)' in html


def test_monthly_sce_charts_and_reading(page):
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    miss = ids["miss_payment_expectation"]
    assert miss["series"][0]["last_period"] == "Aug 2026" and abs(miss["data"][1][-1] - 13.16) < 0.01
    assert miss["series"][-1]["benchmark"] and 10 < miss["data"][-1][0] < 14
    income = ids["miss_payment_by_income"]
    assert income["data"][1][-1] > income["data"][3][-1]
    harder = ids["credit_harder"]
    assert abs(harder["data"][1][-1] - (15.306 + 30.606)) < 0.01  # much plus somewhat harder, August 2026
    assert "Households' stated chance of missing a debt payment" in html


def test_hhdc_all_debt_charts(page):
    _, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    stages = ids["debt_by_delinquency_stage"]
    assert len(stages["series"]) == 5 and stages["series"][0]["last_period"] == "2026 Q2"
    assert abs(sum(ys[-1] for ys in stages["data"][1:]) - (100 - 95.27)) < 0.02  # 4.7% in some stage of delinquency
    bk = ids["bankruptcies_foreclosures"]
    assert bk["unit_label"] == "Thousands" and abs(bk["data"][1][-1] - 136.8) < 0.05
    assert abs(ids["collections"]["data"][1][-1] - 4.88) < 0.01
    assert ids["credit_demand_flow"]["unit"] == "millions" and abs(ids["credit_demand_flow"]["data"][1][-1] - 83.021) < 0.01


def test_spend_panel(page):
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    growth = ids["spend_growth"]
    assert growth["series"][0]["last_period"] == "Jul 2026" and 0 < growth["data"][1][-1] < 10
    retail = ids["retail_growth"]
    assert retail["series"][0]["last_period"] == "Jun 2026"
    share = ids["online_share"]
    assert 15 < share["data"][1][-1] < 22  # nonstore about 18% of retail and food services sales in 2026
    cross = ids["card_volume_vs_retail"]
    assert {s["source"] for s in cross["series"]} == {"phillyfed", "census"} and cross["footer_lines"]
    assert '<section class="panel" id="panel-spend">' in html


def test_credit_union_charts(page):
    _, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    loans = ids["credit_union_card_loans"]
    assert loans["series"][0]["last_period"] == "2026 Q1" and abs(loans["data"][1][-1] - 86.039) < 0.001
    rates = ids["credit_union_card_rates"]
    navy = [v for v in rates["data"][2] if v is not None]
    assert abs(navy[-1] - 18.0) < 0.001  # Navy Federal on the 18 percent cap (the grid runs to the bank series' 2026 Q2)
    assert {s["source"] for s in rates["series"]} == {"fred", "ncua"}
    losses = ids["credit_union_card_losses"]
    assert len(losses["series"]) == 4 and losses["series"][3]["dash"]
    assert [v for v in losses["data"][4] if v is not None]  # the bank rate draws; the credit union rates need
    # consecutive quarters, which the fixture extracts (2016, 2021, 2023, 2026) do not give: see test_ncua_rates_view


def test_ncua_rates_view(tmp_paths, meta):
    """De-cumulated year-to-date charge-offs, annualized over average loans, and the 60+ day share."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_ncua import _cus, _zip
    from carddash.fetchers import ncua
    from carddash.series import series_for_source
    q1 = ncua.extract(_zip("3/31/2026 0:00:00", _cus()), 2026, 1)
    cus = _cus()
    for cu in cus:
        cus[cu].update({"396": 100, "680": 10, "681": 2, "045B": 3})
    q2 = ncua.extract(_zip("6/30/2026 0:00:00", cus), 2026, 2)
    facts = coerce_facts(ncua.to_facts([q1, q2], series_for_source(meta, ncua.SOURCE), RUN2))
    con = _connect(facts, tmp_paths.views_sql, tmp_paths.tccp_products_csv, tmp_paths.issuers_csv)
    rows = con.execute("SELECT period_end, nco_q, nco_rate_annualized, dq_share FROM v_ncua_rates WHERE entity = 'NCUA:BECU' ORDER BY period_end").fetchall()
    con.close()
    assert rows[0][1] == pytest.approx(3e-9) and rows[0][2] is None  # Q1: YTD net 4 - 1 = 3, no prior quarter for the average
    assert rows[1][1] == pytest.approx(5e-9)  # Q2: (10 - 4) - (2 - 1) = 5 dollars, in billions
    assert rows[1][2] == pytest.approx(100 * 4 * 5 / 100)  # 20 percent annualized on average loans of 100
    assert rows[1][3] == pytest.approx(3.0)


def test_distribution_panel_draws_the_dfa(page):
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    wealth = ids["dfa_credit_by_wealth"]
    assert wealth["post"] and wealth["series"][0]["last_period"] == "2026 Q1"
    shares = [s[-1] for s in wealth["data"][1:]]
    assert abs(sum(shares) - 100) < 0.01 and 50 < shares[0] < 54  # the bottom half owes about half of all consumer credit
    buffer = ids["dfa_buffer_by_wealth"]
    assert 25 < buffer["data"][1][-1] < 35  # bottom 50%: deposits of about 30 cents per dollar of consumer credit
    ratio = ids["dfa_credit_to_net_worth"]
    assert 55 < ratio["data"][1][-1] < 70  # bottom 50%: consumer credit about 60 percent of net worth
    per_hh = ids["dfa_credit_per_household_age"]
    assert per_hh["unit"] == "usd" and 14000 < per_hh["data"][4][-1] < 18000  # 70 and over: about $16k per household
    income = ids["dfa_credit_by_income"]
    assert abs(sum(s[-1] for s in income["data"][1:]) - 100) < 0.01
    assert '<section class="panel" id="panel-distribution">' in html
    assert "Consumer credit owed by the bottom half of households by wealth" in html


def test_cct_charts(page):
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    orig = ids["cct_originations"]
    assert orig["series"][0]["last_period"] == "Jan 2026" and 7 < orig["data"][1][-1] < 10
    by_score = ids["cct_lines_by_score"]
    assert abs(sum(s[-1] for s in by_score["data"][1:]) - 100) < 0.01 and 78 < by_score["data"][5][-1] < 86
    below = ids["cct_below_prime_share"]
    assert {s["source"] for s in below["series"]} == {"cfpb_cct", "phillyfed"} and below["footer_lines"]
    vals = [v for v in below["data"][1] if v is not None]
    assert 4 < vals[-1] < 8  # sub-660 borrowers get about 6 percent of new credit line dollars
    ages = ids["cct_lines_by_age"]
    assert abs(sum(s[-1] for s in ages["data"][1:]) - 100) < 0.01
    inq = ids["cct_inquiries"]
    assert inq["series"][0]["last_period"] == "May 2026" and 200 < inq["data"][1][-1] < 250
    tight = ids["cct_tightness"]
    assert tight["unit"] == "index" and tight["series"][0]["last_period"] == "Mar 2026" and 70 < tight["data"][1][-1] < 90
    assert "New credit cards opened in the month, all lenders" in html and " million in Jan 2026" in html


def test_interest_and_claims_charts(page):
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    eff = ids["effective_rate"]
    assert {s["source"] for s in eff["series"]} == {"bea", "fred"}
    rate = [v for v in eff["data"][1] if v is not None]
    assert 9 < rate[-1] < 14  # about $600bn of interest on about $5.2tn of consumer credit
    share = ids["interest_share_income"]
    v = [x for x in share["data"][1] if x is not None]
    assert 2 < v[-1] < 3.5  # 604 over 23,858: about 2.5 percent of disposable income
    claims = ids["jobless_claims"]
    assert claims["unit"] == "thousands" and claims["period_type"] == "W"
    assert abs(claims["data"][1][-1] - 206.0) < 1e-9 and abs(claims["data"][2][-2] - 1774.0) < 1e-9
    assert " thousand in " in html  # the claims reading


def test_dfa_and_cct_views(page, fixture_facts, tmp_paths):
    """v_dfa_shares and v_cct_shares reproduce the hand arithmetic on the fixture facts."""
    con = _connect(fixture_facts, tmp_paths.views_sql, tmp_paths.tccp_products_csv, tmp_paths.issuers_csv)
    row = con.execute(
        "SELECT share_pct, deposits_to_credit_pct, credit_per_household, credit_to_net_worth_pct FROM v_dfa_shares "
        "WHERE entity = 'WEALTH:BOTTOM50' AND period_end = DATE '2026-03-31'"
    ).fetchone()
    assert row[0] == pytest.approx(100 * 2626.591 / 5073.031, abs=1e-6)
    assert row[1] == pytest.approx(100 * 793.232 / 2626.591, abs=1e-6)
    assert row[2] == pytest.approx(1000 * 2626.591 / 67.573814, abs=1e-3)
    assert row[3] == pytest.approx(100 * 2626.591 / 4266.359, abs=1e-6)
    tiers = con.execute(
        "SELECT tier, share_pct, below_prime_share_pct FROM v_cct_shares WHERE metric = 'cct_card_new_lines_sa' "
        "AND entity = 'CFPB_CCP_ALL' AND period_end = DATE '2026-01-31' ORDER BY tier"
    ).fetchall()
    assert [t[0] for t in tiers] == ["deep_subprime", "near_prime", "prime", "subprime", "superprime"]
    assert sum(t[1] for t in tiers) == pytest.approx(100.0)
    assert len({round(t[2], 9) for t in tiers}) == 1 and 5 < tiers[0][2] < 7
    ages = con.execute(
        "SELECT sum(share_pct) FROM v_cct_shares WHERE metric = 'cct_card_new_lines_nsa' AND entity_type = 'age' "
        "AND period_end = DATE '2026-01-31'"
    ).fetchone()
    assert ages[0] == pytest.approx(100.0)
    con.close()


def test_loan_type_charts_compare_cards_with_the_other_debts(page):
    """The by-loan-type sheets of the same workbook: card distress against auto, mortgage and student."""
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    stock = ids["dq_by_loan_type"]
    assert len(stock["series"]) == 7 and stock["series"][6]["dash"]
    last = {s["label"]: stock["data"][i + 1][-1] for i, s in enumerate(stock["series"])}
    assert last["Credit card"] > last["Auto loan"] > last["Mortgage"]  # 12.9, 5.5, 1.0 at 2026 Q2
    assert last["Credit card"] > last["All household debt"]
    flow = ids["dq_flow_by_loan_type"]
    assert len(flow["series"]) == 7 and flow["series"][0]["last_period"] == "2026 Q2"
    student = [v for v in flow["data"][2] if v is not None]
    assert len(student) < flow["n_points"]  # student loans start a year late and are not back-filled
    old = ids["age70_by_loan_type"]
    vals = {s["label"]: old["data"][i + 1][-1] for i, s in enumerate(old["series"])}
    assert vals["Credit card"] > vals["Auto loan"] > vals["All debt"]  # the over-70s' problem is card-specific
    banks = ids["bank_losses_by_category"]
    assert len(banks["series"]) == 4 and {s["source"] for s in banks["series"]} == {"fred"}
    card, mortgage = banks["data"][1][-1], banks["data"][4][-1]
    assert card > 10 * mortgage  # card charge-offs are an order of magnitude above mortgage
    by_age = ids["debt_by_age"]
    assert by_age["unit"] == "usd_bn" and len(by_age["series"]) == 6
    assert by_age["n_points"] == 110  # the debt-by-age sheet starts in 1999, a year before the others
    assert ids["student_dq_by_age"]["n_points"] > 80


def test_consumer_loan_rates_chart(page):
    """The G.19 rate survey's three loan types, the test of whether the card margin is a card decision."""
    _, payload = page
    rates = {c["id"]: c for c in payload["charts"]}["consumer_loan_rates"]
    assert len(rates["series"]) == 4 and rates["period_type"] is None  # quarterly plus the monthly prime rate
    card = [v for v in rates["data"][1] if v is not None][-1]
    personal = [v for v in rates["data"][2] if v is not None][-1]
    auto = [v for v in rates["data"][3] if v is not None][-1]
    assert card > personal > auto  # 22.15, 11.86, 7.47
    assert len(rates["footer_lines"]) == 4


def test_state_charts(page):
    """The state-level source: a band from the lowest to the highest state around the national line."""
    html, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    rng = ids["state_card_dq_range"]
    assert rng["band"] == [1, 2] and rng["period_type"] == "A"
    assert rng["series"][0]["last_period"] == "2025"
    hi, lo, med, nat = (rng["data"][k][-1] for k in (1, 2, 3, 4))
    assert hi > med > lo and lo < nat < hi
    assert hi - lo > 8  # the range across states was 8.3 points in 2025
    picked = ids["state_card_dq_selected"]
    labels = [s["label"] for s in picked["series"]]
    assert labels[0] == "National" and "Nevada" in labels and "Wisconsin" in labels
    nv = picked["data"][labels.index("Nevada") + 1][-1]
    wi = picked["data"][labels.index("Wisconsin") + 1][-1]
    assert nv > wi + 5
    debt = ids["state_card_debt_range"]
    assert debt["unit"] == "usd" and debt["data"][4][-1] == 4350  # the national row, dollars per adult
    assert "rose in every one of the 51 areas" in html

def test_the_page_shares_one_cursor_across_the_charts_on_x_only(page):
    """Hovering any chart marks the same date on every other chart.

    The behaviour is a browser matter and was verified in headless Chrome (task 40): hovering one
    chart at a date inside its window made all 75 read out that date, hovering the weekly chart at
    its newest point left only the 9 charts whose data runs that far, a mouseleave cleared all 75,
    and one mousemove cost 2.65 ms. What a test can hold here is the wiring, and the three ways it
    could go quietly wrong: a shared y scale or series focus would carry one chart's unit onto a
    chart in another unit; a chart answering for a date it does not hold would let uPlot clamp to
    an endpoint and read out a value for the wrong period; and on step charts the nearest period
    end is not the period that contains the date.
    """
    html, _ = page
    cursor = html.split("D.charts.forEach")[1].split("---- browse every series ----")[0]
    assert "sync: { key: SYNC_KEY" in cursor
    assert "scales: ['x', null]" in cursor and "setSeries: false" in cursor
    # a chart outside the hovered date clears its cursor instead of clamping to an endpoint
    assert "xv < xs[0] || xv > xs[n - 1] || xv < sc.min || xv > sc.max" in cursor
    assert "u.setCursor({ left: -10, top: -10 })" in cursor
    # only the cursor is shared: zoom, drag and dblclick stay per chart
    assert "type !== 'mousemove' && type !== 'mouseleave'" in cursor
    # period data reads the period that CONTAINS the date; continuous data keeps uPlot's nearest
    assert "dataIdx: c.step ? containingIdx : null" in cursor
    assert "return closestIdx + 1 < xs.length ? closestIdx + 1 : closestIdx;" in html
    # the series browser keeps its own cursor: it is never on screen with a panel chart
    assert "SYNC_KEY" not in html.split("---- browse every series ----")[1]


def test_every_view_field_a_chart_draws_is_bounded_or_explicitly_exempt():
    """A derived field must not reach a chart without someone having decided what it may contain.

    The loader's per-series vmin/vmax validates facts as they load; view fields are computed in SQL
    afterwards and so were never checked by anything. That gap put a 182.9% annualised charge-off rate
    for Bank of America on the issuer chart for months. This test makes the omissions deliberate: a new
    view field is either given bounds or added to the exempt list with its reason.
    """
    used = {(sr["view"], sr["field"]) for panel in PANELS for c in panel["charts"] for sr in c["series"]}
    unaccounted = used - set(VIEW_BOUNDS) - VIEW_BOUNDS_UNCHECKED
    assert not unaccounted, f"view fields with no bound and no stated exemption: {sorted(unaccounted)}"
    assert not (set(VIEW_BOUNDS) & VIEW_BOUNDS_UNCHECKED)  # a field is bounded or exempt, never both


def test_a_view_field_outside_its_bounds_fails_the_render():
    """The backstop fires, and says what to do about it."""
    s = {"view": "v_fdic_rates", "field": "nco_rate_annualized", "entity": "ISSUER:BOFA", "label": "Bank of America"}
    lo, hi = VIEW_BOUNDS[("v_fdic_rates", "nco_rate_annualized")]
    _check_bounds(s, [(dt.date(2020, 3, 31), 5.0), (dt.date(2020, 6, 30), hi - 0.1)])  # inside: fine
    with pytest.raises(ValueError, match="outside its plausible range"):
        _check_bounds(s, [(dt.date(2001, 12, 31), 182.857)])
    with pytest.raises(ValueError, match="collapsed denominator"):
        _check_bounds(s, [(dt.date(2006, 12, 31), lo - 1.0)])


def test_fdic_ratios_need_a_card_book_worth_dividing_by(tmp_paths):
    """A residual book of a few million produces no rate at all, rather than a rate of a few hundred percent.

    Bank of America's own history is the case: its card book on the Bank of America, N.A. charter sat between
    $2mn and $50mn from 2001 to 2013, because the business was at FIA Card Services, and dividing a real
    quarter of charge-offs by it drew 182.9% for 2001 Q4. Negative rates are kept: a quarter whose recoveries
    beat its charge-offs is real (Citi, 2006 Q4, -4.0% on a $37bn book).
    """
    def row(metric, period_end, value, entity="FDIC_ALL_INSURED"):
        return {"metric": metric, "entity": entity, "entity_type": "industry", "tier": "all",
                "period_end": period_end, "period_type": "Q", "value": value, "source": "fdic",
                "pulled_at": PULLED_AT}

    rows = []
    for i, (end, loans, nco) in enumerate([
        ("2020-03-31", 0.020, 0.006),   # $20mn book: no rate, this is the 182.9% case
        ("2020-06-30", 0.020, 0.006),
        ("2020-09-30", 40.000, 0.400),  # $40bn book: a rate, and a small one
        ("2020-12-31", 40.000, -0.400),  # a genuine net recovery quarter stays negative
    ]):
        rows += [row("fdic_card_loans", end, loans), row("fdic_card_nco_q", end, nco)]
    facts = coerce_facts(pd.DataFrame(rows))
    con = _connect(facts, tmp_paths.views_sql, tmp_paths.tccp_products_csv, tmp_paths.issuers_csv)
    got = con.execute(
        "SELECT period_end, nco_rate_annualized FROM v_fdic_rates "
        "WHERE entity = 'FDIC_ALL_INSURED' ORDER BY period_end"
    ).fetchall()
    rates = {str(d): v for d, v in got}
    assert rates["2020-06-30"] is None, "a $20mn book must carry no rate"
    assert rates["2020-09-30"] is not None and 0 < rates["2020-09-30"] < 10
    assert rates["2020-12-31"] is not None and rates["2020-12-31"] < 0, "a real net recovery quarter is kept"


def test_a_hole_is_drawn_as_a_hole_on_every_chart_but_the_one_that_changed_cadence(page):
    """A missing period means the thing was not measured, so no line is drawn across it.

    The page used to span gaps everywhere while the PNG of the same chart never did, so the two disagreed
    wherever a series had a hole. The default is now False and exactly one chart opts back in:
    consumer_sentiment, whose gaps before 1978 are the survey being quarterly rather than months that went
    unmeasured.
    """
    _, payload = page
    ids = {c["id"]: c for c in payload["charts"]}
    assert ids["nco_by_issuer"]["span_gaps"] is False
    assert any("FIA Card Services" in n for n in ids["nco_by_issuer"]["notes"])

    spanning = [c["id"] for c in payload["charts"] if c["span_gaps"]]
    assert spanning == ["sentiment"], f"only a cadence change may span gaps, got {spanning}"
    assert any("QUARTERLY until January 1978" in n for n in ids["sentiment"]["notes"])


def test_the_shutdown_month_missing_from_the_labour_data_is_left_open(page):
    """October 2025 was never collected, so the unemployment line breaks there rather than joining up.

    The flag alone is not enough and this is the part that actually bites: the x grid is built from the dates
    that HAVE data, so a month no series holds is simply absent and the line joins September to November as
    though they were consecutive. The missing period has to be put on the grid as an explicit null first.
    """
    _, payload = page
    chart = {c["id"]: c for c in payload["charts"]}["losses_vs_labor"]
    assert chart["span_gaps"] is False
    assert any("OCTOBER 2025" in n for n in chart["notes"])

    epoch = dt.date(1970, 1, 1)
    unemployment = {epoch + dt.timedelta(seconds=t): v for t, v in zip(chart["data"][0], chart["data"][2])}
    assert dt.date(2025, 10, 31) in unemployment, "the missing month is not even on the grid"
    assert unemployment[dt.date(2025, 10, 31)] is None
    assert unemployment[dt.date(2025, 9, 30)] is not None
    assert unemployment[dt.date(2025, 11, 30)] is not None


def test_a_series_that_merely_starts_late_is_not_treated_as_having_a_hole():
    """Only periods INSIDE a series' own span are filled, or every chart would grow a run of leading nulls."""
    rows = [(dt.date(2026, 1, 31), 1.0), (dt.date(2026, 2, 28), 2.0), (dt.date(2026, 4, 30), 3.0)]
    assert _missing_periods(rows, "M") == [dt.date(2026, 3, 31)]
    assert _missing_periods(rows[:2], "M") == []
    assert _missing_periods([(dt.date(2026, 3, 31), 1.0)], "M") == []
    # quarterly and annual step by their own cadence, and the four-monthly survey waves are left alone
    quarters = [(dt.date(2025, 3, 31), 1.0), (dt.date(2025, 12, 31), 2.0)]
    assert _missing_periods(quarters, "Q") == [dt.date(2025, 6, 30), dt.date(2025, 9, 30)]
    assert _missing_periods(quarters, "T") == []

def test_the_state_map_covers_every_area_on_a_grid_with_no_collisions(page):
    """A tile-grid cartogram: one equal square per area, so land area never stands in for population."""
    _, payload = page
    m = payload["state_map"]
    codes = [t["code"] for t in m["tiles"]]
    assert len(codes) == len(set(codes)) == len(STATE_TILES) == 52   # 50 states, DC, Puerto Rico
    positions = [(t["row"], t["col"]) for t in m["tiles"]]
    assert len(positions) == len(set(positions)), "two areas share a square"
    assert all(0 <= r < m["rows"] and 0 <= c < m["cols"] for r, c in positions)
    assert [x["key"] for x in m["metrics"]] == ["dq", "debt"]
    assert m["years"] == sorted(m["years"]) and len(m["years"]) >= 20

    dq = m["metrics"][0]
    # every square carries a series, and the national row is kept out of the grid
    assert set(dq["values"]) == set(codes)
    assert "US" not in dq["values"] and "CCP_ALL" not in dq["values"]
    assert dq["national"], "the national reference is missing"


def test_the_state_map_ramp_is_one_hue_defined_in_both_themes(page):
    """Magnitude takes a sequential ramp, and dark is a chosen ramp rather than a flipped light one."""
    html, _ = page
    head = html.split("* { box-sizing: border-box; }")[0]
    # the light ramp sits on bare :root so a viewer with no preference still gets a complete palette
    assert head.count("--seq-0:") == 3       # :root, the media query, and [data-theme="dark"]
    assert head.count("--seq-ink-0:") == 3
    light = head.split("--seq-0:")[1].split(";")[0].strip()
    dark = head.split("--seq-0:")[2].split(";")[0].strip()
    assert light != dark, "dark mode must define its own steps, not reuse the light ramp"
    # the ramp never borrows the identity channel
    for i in range(6):
        assert f"--seq-{i}:" in head and f"--seq-ink-{i}:" in head
    assert "--seq-0:#fdf0e6" in head and "--seq-5:#a3400f" in head          # light: light -> dark
    assert "--seq-0:#3a2317" in head and "--seq-5:#f7a35f" in head          # dark: the anchor flips

    # bins are classes, so the browser resolves the ramp live instead of a colour frozen at draw time
    assert ".tile.b0, .sw.b0 { background: var(--seq-0); }" in html
    assert "el.classList.add('b' + b)" in html


def test_the_state_map_numbers_are_the_ones_the_source_published(page, fixture_facts):
    """Spot values straight from the fixtures, so a broken join shows up as a wrong square."""
    _, payload = page
    dq = payload["state_map"]["metrics"][0]
    rows = fixture_facts[
        (fixture_facts["source"] == "nyfed_state")
        & (fixture_facts["metric"] == "state_card_dq90_rate_balances")
    ]
    latest = max(payload["state_map"]["years"])
    for code in ("NV", "WI", "CA"):
        want = rows[(rows["entity"] == f"STATE:{code}") & (rows["period_end"].astype(str).str.startswith(str(latest)))]
        assert dq["values"][code][str(latest)] == pytest.approx(float(want["value"].iloc[0]))
    # Nevada has been the worst area for the whole history, which is why its square never lightens
    assert dq["values"]["NV"][str(latest)] > dq["values"]["WI"][str(latest)]


def test_puerto_rico_is_shown_but_its_series_stops(page):
    """It is in the source and is not a state; the map draws the gap rather than carrying 2016 forward."""
    _, payload = page
    dq = payload["state_map"]["metrics"][0]
    pr_years = sorted(int(y) for y in dq["values"]["PR"])
    assert max(pr_years) < max(payload["state_map"]["years"]), "Puerto Rico should stop before the newest year"
