"""Rendering: the page is self-contained, embeds the data, and carries the health strip."""

from __future__ import annotations

import json

from carddash.loader import write_facts
from carddash.render import PANELS, render


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
