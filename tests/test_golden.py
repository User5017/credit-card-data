"""Every golden number must be reproduced from the checked-in fixtures."""

from __future__ import annotations

from carddash.golden import check_golden, load_golden
from conftest import REPO


def test_all_golden_entries_pass_on_fixtures(fixture_facts):
    entries = load_golden(REPO / "checks" / "golden.yaml")
    assert entries, "golden.yaml is empty"
    results = check_golden(fixture_facts, entries, live_only=False)
    failures = [r["message"] for r in results if not r["ok"]]
    assert not failures, "\n".join(failures)


def test_golden_reports_missing_period(fixture_facts):
    entry = {
        "id": "missing", "metric": "revolving_credit_sa", "entity": "ALL_HOLDERS", "tier": "all",
        "period_type": "M", "period_end": "2099-01-31", "source": "fred", "expected": 1.0,
    }
    (r,) = check_golden(fixture_facts, [entry])
    assert not r["ok"] and "no value" in r["message"]
