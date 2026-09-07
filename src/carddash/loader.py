"""Loader: validate a fetcher's output, compare it with what we had, replace by source, log revisions.

Failure policy: a source that fails fetch or validation keeps its prior rows and goes red.
A source whose values changed wholesale (restatement detector) is NOT loaded until acknowledged.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .schema import (
    ENTITY_TYPES,
    FACT_COLUMNS,
    KEY_COLUMNS,
    PERIOD_DAYS,
    PERIOD_TYPES,
    SERIES_KEY,
    coerce_facts,
    empty_facts,
)
from .series import series_for_source

AMBER = {"stale", "restated", "golden_mismatch"}
RED = {"failed", "suspect"}
STATUS_RANK = {"ok": 0, "stale": 1, "restated": 1, "golden_mismatch": 1, "failed": 2, "suspect": 2}

REVISION_COLUMNS = [
    "pulled_at",
    "source",
    "metric",
    "entity",
    "tier",
    "period_type",
    "period_end",
    "old_value",
    "new_value",
    "abs_change",
    "rel_change",
]

# Restatement detector: "suspect" when more than this share of overlapping values moved by more than REL_BIG.
REL_BIG = 0.05
SUSPECT_FRACTION = 0.5
MIN_OVERLAP_FOR_DETECTOR = 12


@dataclass
class SourceHealth:
    source: str
    status: str = "ok"
    messages: list[str] = field(default_factory=list)
    last_period_end: str | None = None
    pulled_at: str | None = None
    rows: int = 0
    n_series: int = 0
    n_revisions: int = 0

    def worsen(self, status: str, message: str) -> None:
        if STATUS_RANK[status] > STATUS_RANK[self.status]:
            self.status = status
        self.messages.append(message)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- facts I/O ----------


def read_facts(path: Path) -> pd.DataFrame:
    if not path.exists():
        return empty_facts()
    df = pd.read_csv(path, dtype={c: str for c in FACT_COLUMNS if c != "value"}, keep_default_na=False)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return coerce_facts(df)


def write_facts(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = coerce_facts(df).sort_values(KEY_COLUMNS).reset_index(drop=True)
    out = out.assign(period_end=out["period_end"].dt.strftime("%Y-%m-%d"))
    out.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")


def read_revisions(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=REVISION_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def append_revisions(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[REVISION_COLUMNS].copy()
    out["period_end"] = pd.to_datetime(out["period_end"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, mode="a", header=not path.exists(), index=False, float_format="%.10g", lineterminator="\n")


# ---------- validation ----------


def validate(new: pd.DataFrame, meta: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Any error fails the source."""
    errors: list[str] = []
    warnings: list[str] = []

    missing = [c for c in FACT_COLUMNS if c not in new.columns]
    if missing:
        return [f"missing columns {missing}"], warnings
    if new.empty:
        return ["no rows"], warnings

    for c in KEY_COLUMNS:
        n = int(new[c].isna().sum())
        if n:
            errors.append(f"{n} null values in key column {c}")
    bad_pt = set(new["period_type"].dropna().unique()) - PERIOD_TYPES
    if bad_pt:
        errors.append(f"unknown period_type {sorted(bad_pt)}")
    bad_et = set(new["entity_type"].dropna().unique()) - ENTITY_TYPES
    if bad_et:
        errors.append(f"unknown entity_type {sorted(bad_et)}")
    n_dupes = int(new.duplicated(KEY_COLUMNS).sum())
    if n_dupes:
        errors.append(f"{n_dupes} duplicate keys")
    n_nan = int(new["value"].isna().sum())
    if n_nan:
        errors.append(f"{n_nan} rows with non-numeric or missing value")
    if errors:
        return errors, warnings

    meta_idx = {tuple(r[k] for k in SERIES_KEY): r for _, r in meta.iterrows()}
    for key, grp in new.groupby(SERIES_KEY, sort=False):
        m = meta_idx.get(tuple(key))
        label = f"{key[0]}/{key[1]}/{key[2]}/{key[3]}"
        if m is None:
            errors.append(f"series {label} not in series.csv")
            continue
        vmin, vmax = m["vmin"], m["vmax"]
        if pd.notna(vmin):
            n = int((grp["value"] < vmin).sum())
            if n:
                errors.append(f"{label}: {n} values below vmin {vmin}")
        if pd.notna(vmax):
            n = int((grp["value"] > vmax).sum())
            if n:
                errors.append(f"{label}: {n} values above vmax {vmax}")
        # continuity over the last 24 periods: a gap wider than two cadences is worth a warning
        tail = grp.sort_values("period_end")["period_end"].tail(24)
        if len(tail) > 1:
            gaps = tail.diff().dt.days.dropna()
            limit = 2 * PERIOD_DAYS[key[3]] + 3
            n_gaps = int((gaps > limit).sum())
            if n_gaps:
                errors_or_warn = warnings
                errors_or_warn.append(f"{label}: {n_gaps} gap(s) wider than {limit} days in the last 24 periods")
    # every series the crosswalk expects should be present
    got = set(tuple(k) for k in new[SERIES_KEY].drop_duplicates().itertuples(index=False, name=None))
    for key in meta_idx:
        if key not in got:
            warnings.append(f"expected series {key[0]}/{key[1]}/{key[2]}/{key[3]} missing from fetch")
    return errors, warnings


# ---------- comparison with the previous load ----------


def compare(old: pd.DataFrame, new: pd.DataFrame, pulled_at: str) -> tuple[pd.DataFrame, dict]:
    """Diff previously loaded rows against the fresh fetch for one source."""
    if old.empty:
        stats = {"n_overlap": 0, "n_changed": 0, "n_big": 0, "frac_big": 0.0, "n_dropped": 0, "n_added": len(new)}
        return pd.DataFrame(columns=REVISION_COLUMNS), stats
    m = old[KEY_COLUMNS + ["value"]].merge(
        new[KEY_COLUMNS + ["value"]], on=KEY_COLUMNS, how="outer", suffixes=("_old", "_new"), indicator=True
    )
    both = m[m["_merge"] == "both"].copy()
    diff = (both["value_new"] - both["value_old"]).abs()
    noise = np.maximum(1e-9, 1e-9 * both["value_old"].abs())
    changed = both[diff > noise].copy()
    changed["abs_change"] = changed["value_new"] - changed["value_old"]
    denom = changed["value_old"].abs().replace(0, np.nan)
    changed["rel_change"] = (changed["abs_change"].abs() / denom).fillna(np.inf)
    n_big = int((changed["rel_change"] > REL_BIG).sum())
    n_overlap = int(len(both))
    stats = {
        "n_overlap": n_overlap,
        "n_changed": int(len(changed)),
        "n_big": n_big,
        "frac_big": (n_big / n_overlap) if n_overlap else 0.0,
        "n_dropped": int((m["_merge"] == "left_only").sum()),
        "n_added": int((m["_merge"] == "right_only").sum()),
    }
    revisions = changed.rename(columns={"value_old": "old_value", "value_new": "new_value"})
    revisions["pulled_at"] = pulled_at
    revisions = revisions[REVISION_COLUMNS].reset_index(drop=True)
    return revisions, stats


def staleness(new: pd.DataFrame, meta: pd.DataFrame, today: dt.date) -> list[str]:
    """Series whose latest period is older than allowed (2x cadence + 14 days, or max_age_days)."""
    out = []
    latest = new.groupby(["metric", "entity", "tier", "period_type"])["period_end"].max()
    for _, row in meta.iterrows():
        key = (row["metric"], row["entity"], row["tier"], row["period_type"])
        if key not in latest.index:
            continue
        last = latest[key].date()
        age = (today - last).days
        limit = row["max_age_days"]
        if pd.isna(limit):
            limit = 2 * PERIOD_DAYS[row["period_type"]] + 14
        if age > limit:
            out.append(f"{row['source_id']}: latest period {last.isoformat()} is {age} days old (limit {int(limit)})")
    return out


# ---------- acknowledgements ----------


def load_acks(path: Path) -> set[str]:
    if not path.exists():
        return set()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return set(doc.get("accept_restatement", []) or [])


def consume_ack(path: Path, source: str) -> None:
    if not path.exists():
        return
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lst = [s for s in (doc.get("accept_restatement") or []) if s != source]
    doc["accept_restatement"] = lst
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


# ---------- the per-source refresh ----------


def refresh_source(
    source: str,
    facts: pd.DataFrame,
    meta_all: pd.DataFrame,
    fetch_fn,
    raw_dir: Path,
    session,
    today: dt.date,
    pulled_at: str,
    acked: set[str],
) -> tuple[pd.DataFrame, SourceHealth, pd.DataFrame]:
    """Fetch one source and merge it into facts. Returns (facts, health, revisions)."""
    health = SourceHealth(source=source, pulled_at=pulled_at)
    meta = series_for_source(meta_all, source)
    old = facts[facts["source"] == source]
    no_revisions = pd.DataFrame(columns=REVISION_COLUMNS)

    if meta.empty:
        health.worsen("failed", "no series defined for this source in series.csv")
        return facts, health, no_revisions

    try:
        new = coerce_facts(fetch_fn(meta, raw_dir, session, pulled_at))
    except Exception as exc:  # noqa: BLE001 - any failure keeps prior rows and goes red
        health.worsen("failed", f"fetch error: {type(exc).__name__}: {exc}")
        return facts, health, no_revisions

    errors, warnings = validate(new, meta)
    for w in warnings:
        health.messages.append(f"warning: {w}")
    if errors:
        health.worsen("failed", "validation failed: " + "; ".join(errors[:6]))
        return facts, health, no_revisions

    revisions, stats = compare(old, new, pulled_at)
    if (
        stats["n_overlap"] >= MIN_OVERLAP_FOR_DETECTOR
        and stats["frac_big"] > SUSPECT_FRACTION
        and source not in acked
    ):
        health.worsen(
            "suspect",
            f"{stats['n_big']} of {stats['n_overlap']} overlapping values changed by more than "
            f"{int(REL_BIG * 100)}%. Not loaded. To accept, add '{source}' under accept_restatement "
            "in checks/restatement_ack.yaml.",
        )
        return facts, health, no_revisions

    if stats["n_overlap"] >= MIN_OVERLAP_FOR_DETECTOR and stats["frac_big"] > SUSPECT_FRACTION:
        health.worsen("restated", f"restatement accepted: {stats['n_big']} of {stats['n_overlap']} values changed >5%")
    elif stats["n_changed"]:
        health.messages.append(f"{stats['n_changed']} revised value(s) since last load")
    if stats["n_dropped"]:
        health.worsen("restated", f"{stats['n_dropped']} previously loaded row(s) no longer published; removed")
    if stats["n_added"] and not old.empty:
        health.messages.append(f"{stats['n_added']} new row(s)")

    # Unchanged values keep the pulled_at of the run that first loaded them, so facts.csv only changes
    # when data changes and its git history reads as a vintage log rather than a daily heartbeat.
    if not old.empty:
        prev = old[KEY_COLUMNS + ["value", "pulled_at"]].rename(columns={"value": "_old_value", "pulled_at": "_old_pulled"})
        merged = new.merge(prev, on=KEY_COLUMNS, how="left")
        unchanged = merged["_old_value"].notna() & (
            (merged["value"] - merged["_old_value"]).abs() <= np.maximum(1e-9, 1e-9 * merged["_old_value"].abs())
        )
        merged.loc[unchanged, "pulled_at"] = merged.loc[unchanged, "_old_pulled"]
        new = coerce_facts(merged[FACT_COLUMNS])

    facts = pd.concat([facts[facts["source"] != source], new], ignore_index=True)

    for msg in staleness(new, meta, today):
        health.worsen("stale", msg)

    health.rows = int(len(new))
    health.n_series = int(new.groupby(SERIES_KEY).ngroups)
    health.n_revisions = int(len(revisions))
    health.last_period_end = new["period_end"].max().date().isoformat()
    return facts, health, revisions
