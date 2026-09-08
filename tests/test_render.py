"""Rendering: the page is self-contained, embeds the data, opens on the latest readings, and every card carries its
footer, status, notes, checks and PNG where flagged. The numbers asserted come from the checked-in fixtures."""

from __future__ import annotations

import datetime as dt
import json
import shutil

import pandas as pd
import pytest
from PIL import Image

from carddash.loader import write_facts
from carddash.render import (
    BENCHMARK_LABEL,
    PANELS,
    _benchmark,
    _new_periods,
    _recessions,
    _rel_display,
    headlines,
    render,
)
from carddash.schema import coerce_facts
from conftest import FIXTURES, PULLED_AT

RUN1 = "2026-09-06T00:00:00Z"
RUN2 = "2026-09-07T00:00:00Z"  # the fixtures' pulled_at
TODAY = dt.date(2026, 9, 8)
POST_CHARTS = {"revolving_level", "card_apr", "card_nco", "hhdc_dq90_by_age"}
ALL_SOURCES = ("fred", "tccp", "phillyfed", "nyfed_hhdc", "fdic")


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


@pytest.fixture
def page(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2, ALL_SOURCES)), encoding="utf-8")
    html = render(tmp_paths, today=TODAY).read_text(encoding="utf-8")
    return html, _payload(html)


def test_page_is_self_contained_and_carries_every_chart(page):
    html, payload = page
    assert "uPlot" in html and "<script src=" not in html  # vendored, no runtime fetches
    for panel in PANELS:
        for c in panel["charts"]:
            assert f'data-chart="{c["id"]}"' in html
    assert [p["name"] for p in PANELS] == ["Growth", "Pricing", "Performance", "Borrowers"]
    assert len(payload["charts"]) == 15
    assert payload["default_years"] == 5 and len(payload["recessions"]) == 8
    # the health strip sits below the charts, a one-line summary sits at the top
    assert html.index('<h2 id="health">Source health</h2>') > html.index("<h2>Borrowers</h2>")
    assert "5 sources OK" in html.split("<h2>Latest readings</h2>")[0]
    assert "Sources: Federal Reserve Board, via FRED; CFPB Terms of Credit Card Plans survey" in html


def test_fred_charts(page):
    html, payload = page
    by_id = {c["id"]: c for c in payload["charts"]}
    assert by_id["revolving_level"]["n_points"] > 600
    assert by_id["card_apr"]["n_points"] > 100 and by_id["card_apr"]["period_type"] == "Q"
    assert 400 < by_id["revolving_yoy"]["n_points"] < 700  # v_growth works and the 'since' cut applies
    assert "latest period 2026 Q2" in by_id["card_apr"]["footer"]
    assert "data as of 2026-09-07" in by_id["card_apr"]["footer"] and "data as of" not in by_id["card_apr"]["caption"]
    assert by_id["card_apr"]["caption"].startswith("Source: Federal Reserve Board, via FRED · quarterly · latest period 2026 Q2")
    assert by_id["card_apr"]["footer_lines"] == []  # one source, one cadence: nothing to split out
    assert by_id["card_apr"]["status"] == "ok" and by_id["card_apr"]["attempted"] is None
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
    assert 'href="img/hhdc_dq90_by_age.png"' in html and 'href="img/card_dq.png"' not in html


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
    assert checks[0]["period"] == "Jun 2026" and checks[0]["expected"] == 1351.1  # newest first
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


def test_latest_readings_are_computed_from_the_facts(page, tmp_paths, fixture_facts):
    html, payload = page
    items = headlines(fixture_facts, TODAY)
    labels = [h["label"] for h in items]
    assert labels[0].startswith("Revolving consumer credit") and len(items) == 7  # no 30+ delinquency fixture
    rev = items[0]["text"]
    assert rev == "$1,351bn in Jun 2026, +3.8% on the year"  # October 2024 was higher, so no 'highest since' flag
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
    assert "<strong>Revolving consumer credit, all lenders (Fed G.19, SA):</strong> $1,351bn in Jun 2026" in html
    text = (tmp_paths.docs / "latest.txt").read_text(encoding="utf-8")
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
    assert "2 of 5 sources need attention (Failed)" in html


def test_png_export_is_byte_stable_and_carries_no_pull_date(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    render(tmp_paths, today=TODAY)
    img = tmp_paths.docs / "img"
    assert {p.stem for p in img.glob("*.png")} == POST_CHARTS
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
    changed = html.split("<h2>What changed</h2>")[1].split('<h2 id="health">')[0]
    assert "Card APR, all accounts" not in changed  # the 0.05% revision is counted, not listed
    assert "Card balances (Y-14 large banks)" not in changed  # run 1's revision is not shown
    assert 'href="data/revisions.csv"' in html and (tmp_paths.docs / "data" / "revisions.csv").exists()
    # every fixture row carries RUN2, so every source is a first load: new periods are summarized per cadence
    assert "New periods loaded in this run:" in html
    assert "Federal Reserve Board, via FRED: " in html and " monthly periods through Jun 2026" in html
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
    assert "2099" not in html.split("<h2>What changed</h2>")[0]  # neither the readings nor the cards call 2099 latest


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
    assert out[0]["periods"] == ["May 2026, Jun 2026 (monthly)"]
    assert out[1]["periods"] == ["2 semiannual periods through 2025 H2"]  # the two checked-in workbooks
    assert _new_periods(coerce_facts(facts), None) == []
    assert _new_periods(coerce_facts(facts.iloc[0:0]), RUN2) == []
