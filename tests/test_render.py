"""Rendering: the page is self-contained, embeds the data, and carries the health strip."""

from __future__ import annotations

import json
import shutil

import pandas as pd
from PIL import Image

from carddash.loader import write_facts
from carddash.render import PANELS, _new_periods, _rel_display, render
from carddash.schema import coerce_facts
from conftest import FIXTURES, PULLED_AT

RUN1 = "2026-09-06T00:00:00Z"
RUN2 = "2026-09-07T00:00:00Z"  # the fixtures' pulled_at
POST_CHARTS = {"revolving_level", "card_apr", "card_nco"}


def _health(generated_at: str, sources=("fred",)) -> dict:
    return {
        "generated_at": generated_at,
        "sources": {
            s: {"source": s, "status": "ok", "messages": [], "last_period_end": "2026-08-26", "pulled_at": generated_at,
                "rows": 1, "n_series": 1, "n_revisions": 0}
            for s in sources
        },
    }


def test_render_produces_self_contained_page(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    health = {
        "generated_at": "2026-09-07T00:00:00Z",
        "sources": {
            "fred": {
                "source": "fred", "status": "ok", "messages": ["3 revised value(s) since last load"],
                "last_period_end": "2026-08-26", "pulled_at": "2026-09-07T00:00:00Z",
                "rows": int(len(fixture_facts)), "n_series": 5, "n_revisions": 3,
            }
        },
    }
    tmp_paths.health_json.write_text(json.dumps(health), encoding="utf-8")

    out = render(tmp_paths)
    html = out.read_text(encoding="utf-8")
    assert "window.CARDDASH" in html
    assert "uPlot" in html and "<script src=" not in html  # vendored, no runtime fetches
    for panel in PANELS:
        for c in panel["charts"]:
            assert f'data-chart="{c["id"]}"' in html
    assert "st-ok" in html and "Federal Reserve Board" in html
    assert (tmp_paths.docs / "data" / "facts.csv").exists()

    start = html.index("window.CARDDASH = ") + len("window.CARDDASH = ")
    end = html.index(";</script>", start)
    payload = json.loads(html[start:end])
    by_id = {c["id"]: c for c in payload["charts"]}
    assert by_id["revolving_level"]["n_points"] > 600
    assert by_id["card_apr"]["n_points"] > 100
    assert 400 < by_id["revolving_yoy"]["n_points"] < 700  # v_growth view works and the 'since' cut applies
    assert by_id["card_apr"]["period_type"] == "Q"
    assert "latest period 2026 Q2" in by_id["card_apr"]["footer"]
    # charts with no data in the fixtures still render (empty), never crash
    assert by_id["sloos_cards"]["n_points"] == 0
    # the first cross-source chart: TCCP (semiannual) and G.19 (quarterly) lines plus the as-of spread from the view
    ovp = by_id["offered_vs_paid"]
    assert len(ovp["series"]) == 3 and ovp["n_points"] > 10
    assert any(v is not None for v in ovp["data"][3])
    assert "CFPB Terms of Credit Card Plans" in ovp["footer"] and "Federal Reserve Board" in ovp["footer"]
    # the PNG caption is the footer without the pull date
    assert "pulled" in by_id["card_apr"]["footer"] and "pulled" not in by_id["card_apr"]["caption"]
    assert by_id["card_apr"]["caption"].startswith("Source: Federal Reserve Board, via FRED · quarterly · latest period 2026 Q2")
    assert {c["id"] for c in payload["charts"] if c["post"]} == POST_CHARTS
    assert 'href="img/card_apr.png"' in html and 'href="img/card_dq.png"' not in html
    # the method section credits every loaded source, from health
    assert "Sources: Federal Reserve Board, via FRED." in html


def test_png_export_is_byte_stable_and_carries_no_pull_date(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    render(tmp_paths)
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
    render(tmp_paths)
    second = {p.name: p.read_bytes() for p in img.glob("*.png")}
    assert second == first


def test_what_changed_lists_only_the_latest_run(tmp_paths, fixture_facts):
    write_facts(fixture_facts, tmp_paths.facts_csv)
    shutil.copyfile(FIXTURES / "revisions.csv", tmp_paths.revisions_csv)
    tmp_paths.health_json.write_text(json.dumps(_health(RUN2)), encoding="utf-8")
    html = render(tmp_paths).read_text(encoding="utf-8")
    assert "Latest run 2026-09-07 00:00 UTC" in html
    assert "3 values revised by the source, largest first (2 listed; revisions under 0.1% are only counted)" in html
    assert "Card balances (NY Fed CCP), 2026 Q1: 1252 → 1242 (0.80%)" in html
    assert "Issuers with a card above 30% purchase APR, 2025 H2: 0 → 13 (from 0)" in html  # old value 0: no percentage
    assert "Card APR, all accounts" not in html.split("<h2>Growth</h2>")[0]  # the 0.05% revision is counted, not listed
    assert "Card balances (Y-14 large banks)" not in html.split("<h2>Growth</h2>")[0]  # run 1's revision is not shown
    assert 'href="data/revisions.csv"' in html and (tmp_paths.docs / "data" / "revisions.csv").exists()
    # every fixture row carries RUN2, so every source is a first load: new periods are summarized per cadence
    assert "New periods loaded in this run:" in html
    assert "Federal Reserve Board, via FRED: " in html and " monthly periods through Jun 2026" in html
    # keyed on the run: pointing health at run 1 lists run 1, even though run 2's rows are newer in the file
    tmp_paths.health_json.write_text(json.dumps(_health(RUN1)), encoding="utf-8")
    html = render(tmp_paths).read_text(encoding="utf-8")
    assert "Latest run 2026-09-06 00:00 UTC" in html
    assert "3 values revised by the source, largest first (1 listed" in html
    assert "Card balances (Y-14 large banks), 2026 Q1: 940 → 948.7 (0.93%)" in html
    assert "Card balances (NY Fed CCP)" not in html.split("<h2>Growth</h2>")[0]
    assert "No new periods in this run." in html  # no fixture row carries run 1's stamp
    # no health file yet: the block says so instead of guessing
    tmp_paths.health_json.unlink()
    assert "No refresh run recorded yet." in render(tmp_paths).read_text(encoding="utf-8")


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
