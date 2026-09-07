"""Loader semantics: replace-by-source, revisions, restatement detector, validation, staleness."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from carddash import loader
from carddash.schema import coerce_facts, empty_facts

TODAY = dt.date(2026, 9, 7)
PULL1 = "2026-09-06T00:00:00Z"
PULL2 = "2026-09-07T00:00:00Z"


def _meta(vmin=0, vmax=100, max_age=None):
    return pd.DataFrame(
        [
            {
                "source": "test", "source_id": "T1", "metric": "m", "entity": "E", "entity_type": "aggregate",
                "tier": "all", "period_type": "M", "unit": "pct", "scale": 1.0, "sa": False,
                "display_name": "Test", "scope_note": "", "source_url": "", "vmin": vmin, "vmax": vmax,
                "max_age_days": max_age,
            }
        ]
    )


def _facts(values: dict[str, float], pulled_at=PULL1, metric="m"):
    rows = [
        {"metric": metric, "entity": "E", "entity_type": "aggregate", "tier": "all", "period_end": d,
         "period_type": "M", "value": v, "source": "test", "pulled_at": pulled_at}
        for d, v in values.items()
    ]
    return coerce_facts(pd.DataFrame(rows))


BASE = {f"2026-{m:02d}-28": 10.0 + m for m in range(1, 9)}  # Jan..Aug 2026
TWELVE = {  # Sep 2025..Aug 2026: twelve periods, recent enough not to be stale on TODAY
    **{f"2025-{m:02d}-28": 10.0 + m for m in range(9, 13)},
    **{f"2026-{m:02d}-28": 22.0 + m for m in range(1, 9)},
}


def _run(prev_facts, fetch_result, acked=(), meta=None, today=TODAY):
    meta = _meta() if meta is None else meta

    def fetch_fn(meta_, raw_dir, session, pulled_at):
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    return loader.refresh_source("test", prev_facts, meta, fetch_fn, None, None, today, PULL2, set(acked))


def test_first_load_is_ok():
    facts, h, revs = _run(empty_facts(), _facts(BASE))
    assert h.status == "ok", h.messages
    assert len(facts) == 8 and revs.empty
    assert h.last_period_end == "2026-08-28"


def test_replace_by_source_drops_vanished_rows():
    prev = _facts(BASE)
    smaller = {k: v for k, v in BASE.items() if k != "2026-01-28"}
    facts, h, _ = _run(prev, _facts(smaller, PULL2))
    assert len(facts) == 7
    assert pd.Timestamp("2026-01-28") not in set(facts["period_end"])
    assert h.status == "restated"
    assert any("no longer published" in m for m in h.messages)


def test_revision_is_logged():
    prev = _facts(BASE)
    revised = dict(BASE)
    revised["2026-07-28"] = 17.5  # was 17.0
    facts, h, revs = _run(prev, _facts(revised, PULL2))
    assert h.status == "ok"
    assert len(revs) == 1
    r = revs.iloc[0]
    assert r["old_value"] == 17.0 and r["new_value"] == 17.5 and r["pulled_at"] == PULL2
    assert facts.loc[facts["period_end"] == pd.Timestamp("2026-07-28"), "value"].iloc[0] == 17.5


def test_wholesale_change_is_suspect_and_not_loaded():
    prev = _facts(TWELVE)
    doubled = {k: v * 2 for k, v in TWELVE.items()}
    facts, h, revs = _run(prev, _facts(doubled, PULL2))
    assert h.status == "suspect"
    assert revs.empty
    assert facts["value"].tolist() == prev["value"].tolist()


def test_acknowledged_restatement_loads():
    prev = _facts(TWELVE)
    doubled = {k: v * 2 for k, v in TWELVE.items()}
    facts, h, revs = _run(prev, _facts(doubled, PULL2), acked=["test"])
    assert h.status == "restated", h.messages
    assert len(revs) == 12
    assert facts["value"].max() == 60.0


def test_out_of_range_fails_and_keeps_prior_rows():
    prev = _facts(BASE)
    bad = dict(BASE)
    bad["2026-08-28"] = 500.0
    facts, h, _ = _run(prev, _facts(bad, PULL2))
    assert h.status == "failed"
    assert "vmax" in " ".join(h.messages)
    assert facts["value"].max() == 18.0


def test_unknown_series_fails():
    facts, h, _ = _run(empty_facts(), _facts(BASE, metric="not_in_crosswalk"))
    assert h.status == "failed"
    assert "not in series.csv" in " ".join(h.messages)


def test_fetch_exception_fails_and_keeps_prior_rows():
    prev = _facts(BASE)
    facts, h, _ = _run(prev, RuntimeError("boom"))
    assert h.status == "failed"
    assert "boom" in " ".join(h.messages)
    assert len(facts) == 8


def test_stale_when_latest_period_is_old():
    facts, h, _ = _run(empty_facts(), _facts(BASE), today=dt.date(2027, 3, 1))
    assert h.status == "stale"
    assert "days old" in " ".join(h.messages)


def test_max_age_override_from_series_csv():
    meta = _meta(max_age=5)
    facts, h, _ = _run(empty_facts(), _facts(BASE), meta=meta)
    assert h.status == "stale"


def test_duplicate_keys_fail():
    df = pd.concat([_facts(BASE), _facts({"2026-08-28": 1.0})])
    errors, _ = loader.validate(df, _meta())
    assert any("duplicate" in e for e in errors)


def test_continuity_gap_is_a_warning_not_error():
    gappy = {k: v for k, v in BASE.items() if k not in ("2026-04-28", "2026-05-28", "2026-06-28")}
    errors, warnings = loader.validate(_facts(gappy), _meta())
    assert not errors
    assert any("gap" in w for w in warnings)


def test_facts_roundtrip_csv(tmp_path):
    df = _facts(BASE)
    path = tmp_path / "facts.csv"
    loader.write_facts(df, path)
    back = loader.read_facts(path)
    pd.testing.assert_frame_equal(
        back.sort_values("period_end").reset_index(drop=True),
        df.sort_values("period_end").reset_index(drop=True),
        check_dtype=False,
    )


@pytest.mark.parametrize("period_type,date,expected", [
    ("M", "2026-02-10", "2026-02-28"),
    ("Q", "2026-05-01", "2026-06-30"),
    ("Q", "2026-07-01", "2026-09-30"),
    ("H", "2026-03-15", "2026-06-30"),
    ("T", "2026-05-01", "2026-08-31"),
    ("A", "2026-05-01", "2026-12-31"),
    ("W", "2026-08-26", "2026-08-26"),
])
def test_period_end(period_type, date, expected):
    from carddash.schema import period_end
    assert period_end(dt.date.fromisoformat(date), period_type).isoformat() == expected
