"""Command line: carddash refresh | render | check | health."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

import pandas as pd

from .fetchers import FETCHERS
from .golden import check_golden, load_golden
from .http import make_session
from .loader import (
    RED,
    REVISION_COLUMNS,
    SourceHealth,
    append_revisions,
    consume_ack,
    load_acks,
    read_facts,
    refresh_source,
    write_facts,
)
from .paths import REPO_ROOT, Paths
from .render import render
from .series import load_series


def read_health(paths: Paths) -> dict[str, SourceHealth]:
    if not paths.health_json.exists():
        return {}
    doc = json.loads(paths.health_json.read_text(encoding="utf-8"))
    out = {}
    for name, h in doc.get("sources", {}).items():
        out[name] = SourceHealth(**{k: h.get(k) for k in SourceHealth.__dataclass_fields__ if k in h})
        out[name].messages = list(h.get("messages", []))
    return out


def write_health(paths: Paths, health: dict[str, SourceHealth], generated_at: str) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": generated_at, "sources": {k: v.to_dict() for k, v in sorted(health.items())}}
    paths.health_json.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def cmd_refresh(paths: Paths, sources: list[str] | None, do_render: bool) -> int:
    sources = sources or list(FETCHERS)
    unknown = [s for s in sources if s not in FETCHERS]
    if unknown:
        print(f"unknown source(s): {unknown}; known: {list(FETCHERS)}", file=sys.stderr)
        return 2

    facts = read_facts(paths.facts_csv)
    meta = load_series(paths.series_csv)
    session = make_session()
    now = dt.datetime.now(dt.timezone.utc)
    pulled_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = now.date()
    acked = load_acks(paths.ack_yaml)
    health = read_health(paths)
    all_revisions = [pd.DataFrame(columns=REVISION_COLUMNS)]

    for source in sources:
        facts, h, revisions = refresh_source(
            source, facts, meta, FETCHERS[source].fetch, paths.raw / source, session, today, pulled_at, acked
        )
        if source in acked and h.status not in RED:
            consume_ack(paths.ack_yaml, source)
        health[source] = h
        all_revisions.append(revisions)
        print(f"[{source}] {h.status}: {h.rows} rows, {h.n_series} series, latest {h.last_period_end}")
        for m in h.messages:
            print(f"    - {m}")

    for r in check_golden(facts, load_golden(paths.golden_yaml), live_only=True):
        h = health.setdefault(r["source"], SourceHealth(source=r["source"]))
        if not r["ok"]:
            h.worsen("golden_mismatch", "golden check failed: " + r["message"])
            print(f"[{r['source']}] GOLDEN MISMATCH {r['message']}")

    write_facts(facts, paths.facts_csv)
    append_revisions(pd.concat(all_revisions, ignore_index=True), paths.revisions_csv)
    write_health(paths, health, pulled_at)
    if do_render:
        out = render(paths)
        print(f"rendered {out}")
    return 0


def cmd_check(paths: Paths) -> int:
    facts = read_facts(paths.facts_csv)
    results = check_golden(facts, load_golden(paths.golden_yaml), live_only=False)
    bad = 0
    for r in results:
        print(("PASS " if r["ok"] else "FAIL ") + r["message"])
        bad += 0 if r["ok"] else 1
    print(f"{len(results) - bad} passed, {bad} failed")
    return 1 if bad else 0


def cmd_health(paths: Paths, fail_on_red: bool) -> int:
    health = read_health(paths)
    if not health:
        print("no health.json yet")
        return 1 if fail_on_red else 0
    red = 0
    for name, h in sorted(health.items()):
        print(f"{name:12s} {h.status:16s} latest {h.last_period_end} pulled {h.pulled_at}")
        for m in h.messages:
            print(f"    - {m}")
        red += 1 if h.status in RED else 0
    return 1 if (fail_on_red and red) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="carddash")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="fetch every source, validate, load, render")
    r.add_argument("--source", action="append", help="limit to one source (repeatable)")
    r.add_argument("--no-render", action="store_true")
    sub.add_parser("render", help="rebuild docs/index.html from data/")
    sub.add_parser("check", help="run every golden check against data/facts.csv")
    h = sub.add_parser("health", help="print source health")
    h.add_argument("--fail-on-red", action="store_true")
    args = p.parse_args(argv)

    paths = Paths.from_root(REPO_ROOT)
    if args.cmd == "refresh":
        return cmd_refresh(paths, args.source, not args.no_render)
    if args.cmd == "render":
        print(f"rendered {render(paths)}")
        return 0
    if args.cmd == "check":
        return cmd_check(paths)
    if args.cmd == "health":
        return cmd_health(paths, args.fail_on_red)
    return 2


if __name__ == "__main__":
    sys.exit(main())
