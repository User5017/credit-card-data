"""Golden checks: hand-copied headline numbers the pipeline must reproduce.

checks/golden.yaml entries trace to a public release page, never to the machine-readable feed itself.
The fixture test runs every entry against checked-in raw files (deterministic). The live run only checks
entries with check_live: true, which should be periods old enough not to be revised routinely.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml


def load_golden(path: Path) -> list[dict]:
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return list(doc)


def check_golden(facts: pd.DataFrame, entries: list[dict], live_only: bool = False) -> list[dict]:
    results = []
    for e in entries:
        if live_only and not e.get("check_live", True):
            continue
        sel = facts[
            (facts["metric"] == e["metric"])
            & (facts["entity"] == e["entity"])
            & (facts["tier"] == e.get("tier", "all"))
            & (facts["period_type"] == e["period_type"])
            & (facts["source"] == e["source"])
            & (facts["period_end"] == pd.Timestamp(str(e["period_end"])))
        ]
        expected = float(e["expected"])
        tol = float(e.get("tolerance", 0.0))
        if sel.empty:
            results.append(
                {"id": e["id"], "source": e["source"], "ok": False, "actual": None, "expected": expected,
                 "message": f"{e['id']}: no value for {e['period_end']}"}
            )
            continue
        actual = float(sel["value"].iloc[0])
        ok = abs(actual - expected) <= tol
        msg = f"{e['id']}: got {actual:.6g}, expected {expected:.6g} +/-{tol:g} ({e.get('origin', '')})"
        results.append(
            {"id": e["id"], "source": e["source"], "ok": ok, "actual": actual, "expected": expected, "message": msg}
        )
    return results
