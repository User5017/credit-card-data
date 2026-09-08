"""Render one self-contained docs/index.html from facts.csv, health.json and revisions.csv.

No runtime fetches: the chart library is vendored inline and the data is embedded as JSON,
so the page renders identically from file:// and from GitHub Pages.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import duckdb
import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

from .fetchers import tccp
from .issuers import ISSUER_TYPES
from .loader import read_facts, read_revisions
from .paths import Paths
from .schema import PERIOD_WORDS, SERIES_KEY
from .series import load_series, series_index

VENDOR = Path(__file__).parent / "vendor"

SOURCE_LABELS = {
    "fred": "Federal Reserve Board, via FRED",
    "tccp": "CFPB Terms of Credit Card Plans survey",
    "phillyfed": "Federal Reserve Bank of Philadelphia, Large Bank Credit Card and Mortgage Data (FR Y-14M)",
    "nyfed_hhdc": "Federal Reserve Bank of New York, Quarterly Report on Household Debt and Credit (Consumer Credit Panel/Equifax)",
    "fdic": "FDIC, Call Report data via the BankFind Suite API",
}
STATUS_LABELS = {
    "ok": "OK",
    "stale": "Stale",
    "restated": "Restated",
    "golden_mismatch": "Check mismatch",
    "failed": "Failed",
    "suspect": "Suspect, not loaded",
}
UNIT_LABELS = {
    "usd_bn": "Billions of dollars",
    "usd": "Dollars",
    "millions": "Millions",
    "pct": "Percent",
    "count": "Count",
    "score": "Credit score",
    "index": "Index",
}


def S(metric, entity, label, tier="all", period_type="M", source="fred", view="facts", field="value"):
    return {
        "metric": metric,
        "entity": entity,
        "tier": tier,
        "period_type": period_type,
        "source": source,
        "label": label,
        "view": view,
        "field": field,
    }


# Chart specs. Adding a chart means adding an entry here; the data comes from facts or a view in sql/views.sql.
PANELS = [
    {
        "name": "Growth",
        "blurb": "How much revolving credit is outstanding and how fast it is growing.",
        "charts": [
            {
                "id": "revolving_level",
                "title": "Revolving consumer credit, all holders",
                "unit": "usd_bn",
                "step": False,
                "series": [S("revolving_credit_sa", "ALL_HOLDERS", "Revolving credit, SA")],
            },
            {
                "id": "revolving_yoy",
                "title": "Revolving consumer credit, year-over-year change",
                "unit": "pct",
                "step": False,
                "since": "1980-01-01",  # the 1968-1975 base is tiny and its triple-digit growth hides everything after
                "series": [S("revolving_credit_sa", "ALL_HOLDERS", "YoY change", view="v_growth", field="yoy_pct")],
            },
            {
                "id": "bank_card_loans_weekly",
                "title": "Credit card loans at commercial banks, weekly",
                "unit": "usd_bn",
                "step": False,
                "series": [S("bank_card_loans_sa", "COMBANKS_ALL", "Card loans, SA", period_type="W")],
            },
        ],
    },
    {
        "name": "Pricing",
        "blurb": "What card borrowers are charged.",
        "charts": [
            {
                "id": "card_apr",
                "title": "Commercial bank credit card APR",
                "unit": "pct",
                "step": True,
                "series": [
                    S("card_apr_all_accounts", "COMBANKS_ALL", "All accounts", period_type="Q"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Accounts assessed interest", period_type="Q"),
                ],
            },
            {
                # first cross-source chart: the terms issuers advertise (TCCP) against the rate revolvers pay (G.19)
                "id": "offered_vs_paid",
                "title": "Card APR offered vs APR paid",
                "unit": "pct",
                "step": True,
                "since": "2022-01-01",
                "series": [
                    S("tccp_purchase_apr_max_median", "TCCP_ALL", "Offered: median highest purchase APR (TCCP)",
                      period_type="H", source="tccp"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Paid: APR on accounts assessed interest (G.19)",
                      period_type="Q"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Offered minus paid", period_type="Q",
                      view="v_offered_vs_paid", field="spread_pct_pts"),
                ],
            },
        ],
    },
    {
        "name": "Performance",
        "blurb": "How card credit is performing and whether banks are tightening.",
        "charts": [
            {
                "id": "card_nco",
                "title": "Credit card charge-off rate, annualized",
                "unit": "pct",
                "step": True,
                "series": [
                    S("card_nco_rate_sa", "COMBANKS_ALL", "All commercial banks", period_type="Q"),
                    S("card_nco_rate_sa", "COMBANKS_TOP100", "Top 100 banks", period_type="Q"),
                    S("card_nco_rate_sa", "COMBANKS_OTHER", "Banks outside top 100", period_type="Q"),
                ],
            },
            {
                "id": "card_dq",
                "title": "Credit card delinquency rate, 30+ days past due",
                "unit": "pct",
                "step": True,
                "series": [
                    S("card_dq_rate_sa", "COMBANKS_ALL", "All commercial banks", period_type="Q"),
                    S("card_dq_rate_sa", "COMBANKS_TOP100", "Top 100 banks", period_type="Q"),
                    S("card_dq_rate_sa", "COMBANKS_OTHER", "Banks outside top 100", period_type="Q"),
                ],
            },
            {
                "id": "sloos_cards",
                "title": "Banks tightening credit card standards, net percent",
                "unit": "pct",
                "step": True,
                "series": [S("sloos_card_standards_net_tightening", "SLOOS_DOMESTIC", "Net % tightening", period_type="Q")],
            },
        ],
    },
]


def _connect(
    facts: pd.DataFrame, views_sql: Path, products_csv: Path | None = None, issuers_csv: Path | None = None
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.register("facts_df", facts)
    con.execute("CREATE TABLE facts AS SELECT * FROM facts_df")
    if products_csv is not None and products_csv.exists():
        # the TCCP sub-grain raw table, schema pinned so no view ever depends on CSV type sniffing
        cols = ", ".join(f"'{c}': '{t}'" for c, t in tccp.PRODUCT_TYPES.items())
        con.execute(
            f"CREATE TABLE tccp_products AS SELECT * FROM read_csv(?, header = true, columns = {{{cols}}})",
            [str(products_csv)],
        )
    if issuers_csv is not None and issuers_csv.exists():
        # the charter list behind the FDIC issuer roll-up view, schema pinned the same way
        cols = ", ".join(f"'{c}': '{t}'" for c, t in ISSUER_TYPES.items())
        con.execute(
            f"CREATE TABLE issuers AS SELECT * FROM read_csv(?, header = true, columns = {{{cols}}})",
            [str(issuers_csv)],
        )
    sql = "\n".join(
        line for line in views_sql.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--")
    )
    for stmt in sql.split(";"):
        if stmt.strip():
            con.execute(stmt)
    return con


def _series_rows(con, s: dict, since: str | None = None) -> list[tuple]:
    sql = (
        f"SELECT CAST(period_end AS DATE) AS d, {s['field']} AS v FROM {s['view']} "
        "WHERE metric = ? AND entity = ? AND tier = ? AND period_type = ? AND source = ? "
        f"AND {s['field']} IS NOT NULL"
    )
    params = [s["metric"], s["entity"], s["tier"], s["period_type"], s["source"]]
    if since:
        sql += " AND period_end >= ?"
        params.append(since)
    return con.execute(sql + " ORDER BY d", params).fetchall()


def _epoch(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def period_label(d: dt.date, period_type: str) -> str:
    """Human label for a period_end: 'Jun 2026', '2026 Q2', '2026 H1', '2026', or the ISO date."""
    if period_type == "M":
        return f"{MONTHS[d.month - 1]} {d.year}"
    if period_type == "Q":
        return f"{d.year} Q{(d.month - 1) // 3 + 1}"
    if period_type == "H":
        return f"{d.year} H{1 if d.month <= 6 else 2}"
    if period_type == "A":
        return str(d.year)
    return d.isoformat()


def _chart_payload(con, spec: dict, meta_idx: dict, health: dict) -> dict:
    per_series = []
    all_rows = []
    since = spec.get("since")
    for s in spec["series"]:
        rows = _series_rows(con, s, since)
        all_rows.append(rows)
        m = meta_idx.get((s["metric"], s["entity"], s["tier"], s["period_type"], s["source"]))
        src_health = health.get(s["source"], {})
        per_series.append(
            {
                "label": s["label"],
                "period_type": s["period_type"],
                "cadence": PERIOD_WORDS.get(s["period_type"], s["period_type"]),
                "last_period": period_label(rows[-1][0], s["period_type"]) if rows else None,
                "last_period_iso": rows[-1][0].isoformat() if rows else None,
                "source_label": SOURCE_LABELS.get(s["source"], s["source"]),
                "source_url": (m["source_url"] if m is not None else ""),
                "scope_note": (m["scope_note"] if m is not None else ""),
                "pulled_at": src_health.get("pulled_at"),
            }
        )
    xs = sorted({d for rows in all_rows for d, _ in rows})
    idx = {d: i for i, d in enumerate(xs)}
    data: list[list] = [[_epoch(d) for d in xs]]
    for rows in all_rows:
        ys: list[float | None] = [None] * len(xs)
        for d, v in rows:
            ys[idx[d]] = float(v)
        data.append(ys)

    sources = sorted({p["source_label"] for p in per_series})
    cadences = sorted({p["cadence"] for p in per_series})
    with_data = [p for p in per_series if p["last_period_iso"]]
    last = max(with_data, key=lambda p: p["last_period_iso"])["last_period"] if with_data else None
    pulled = max((p["pulled_at"] for p in per_series if p["pulled_at"]), default=None)
    footer = f"Source: {', '.join(sources)} · {', '.join(cadences)}"
    if last:
        footer += f" · latest period {last}"
    if pulled:
        footer += f" · pulled {pulled[:10]}"
    if since:
        footer += f" · shown from {since[:4]}"
    notes = []
    for p in per_series:
        if p["scope_note"] and p["scope_note"] not in notes:
            notes.append(p["scope_note"])
    period_types = {p["period_type"] for p in per_series}
    return {
        "id": spec["id"],
        "title": spec["title"],
        "unit": spec["unit"],
        "unit_label": UNIT_LABELS.get(spec["unit"], spec["unit"]),
        "step": spec["step"],
        "period_type": period_types.pop() if len(period_types) == 1 else None,
        "series": per_series,
        "data": data,
        "footer": footer,
        "notes": notes,
        "n_points": len(xs),
    }


def _latest_by_source(facts: pd.DataFrame, meta_idx: dict) -> dict[str, str]:
    """source -> 'Jun 2026 (Revolving consumer credit (SA))' for the series with the newest period."""
    out = {}
    if facts.empty:
        return out
    for source, grp in facts.groupby("source"):
        row = grp.loc[grp["period_end"].idxmax()]
        m = meta_idx.get((row["metric"], row["entity"], row["tier"], row["period_type"], row["source"]))
        name = m["display_name"] if m is not None else row["metric"]
        out[str(source)] = f"{period_label(row['period_end'].date(), row['period_type'])} ({name})"
    return out


def _health_rows(health: dict, latest_by_source: dict[str, str]) -> list[dict]:
    rows = []
    for source, h in sorted(health.items()):
        rows.append(
            {
                "source": source,
                "label": SOURCE_LABELS.get(source, source),
                "status": h.get("status", "failed"),
                "status_label": STATUS_LABELS.get(h.get("status", "failed"), h.get("status")),
                "last_period_end": latest_by_source.get(source) or h.get("last_period_end") or "none",
                "pulled_at": (h.get("pulled_at") or "never")[:16].replace("T", " "),
                "rows": h.get("rows", 0),
                "n_series": h.get("n_series", 0),
                "messages": h.get("messages", []),
            }
        )
    return rows


def _recent_revisions(revisions: pd.DataFrame, meta_idx: dict, limit: int = 12) -> list[dict]:
    if revisions.empty:
        return []
    df = revisions.copy()
    df["rel_change"] = pd.to_numeric(df["rel_change"], errors="coerce")
    df["old_value"] = pd.to_numeric(df["old_value"], errors="coerce")
    df["new_value"] = pd.to_numeric(df["new_value"], errors="coerce")
    latest_pull = df["pulled_at"].max()
    df = df[(df["pulled_at"] == latest_pull) & (df["rel_change"] > 0.001)]
    df = df.sort_values("rel_change", ascending=False).head(limit)
    out = []
    for _, r in df.iterrows():
        m = meta_idx.get((r["metric"], r["entity"], r["tier"], r["period_type"], r["source"]))
        out.append(
            {
                "name": m["display_name"] if m is not None else f"{r['metric']} {r['entity']}",
                "period_end": str(r["period_end"])[:10],
                "old": r["old_value"],
                "new": r["new_value"],
                "rel": r["rel_change"] * 100,
            }
        )
    return out


def render(paths: Paths) -> Path:
    facts = read_facts(paths.facts_csv)
    meta = load_series(paths.series_csv)
    meta_idx = series_index(meta)
    health_doc = json.loads(paths.health_json.read_text(encoding="utf-8")) if paths.health_json.exists() else {}
    health = health_doc.get("sources", {})
    revisions = read_revisions(paths.revisions_csv)

    con = _connect(facts, paths.views_sql, paths.tccp_products_csv, paths.issuers_csv)
    panels = []
    for panel in PANELS:
        charts = [_chart_payload(con, spec, meta_idx, health) for spec in panel["charts"]]
        panels.append({"name": panel["name"], "blurb": panel["blurb"], "charts": charts})
    con.close()

    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = {
        "generated_at": generated_at,
        "charts": [c for p in panels for c in p["charts"]],
    }
    payload_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    env = Environment(loader=PackageLoader("carddash", "templates"), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("index.html.j2")
    html = tpl.render(
        generated_at=generated_at,
        health=_health_rows(health, _latest_by_source(facts, meta_idx)),
        health_generated=health_doc.get("generated_at", "never"),
        panels=panels,
        recent_revisions=_recent_revisions(revisions, meta_idx),
        n_facts=len(facts),
        n_series=int(facts.groupby(SERIES_KEY).ngroups) if not facts.empty else 0,
        payload_json=payload_json,
        uplot_js=(VENDOR / "uPlot.iife.min.js").read_text(encoding="utf-8"),
        uplot_css=(VENDOR / "uPlot.min.css").read_text(encoding="utf-8"),
        repo_url="https://github.com/User5017/credit-card-data",
    )
    paths.docs.mkdir(parents=True, exist_ok=True)
    out = paths.docs / "index.html"
    out.write_text(html, encoding="utf-8", newline="\n")
    (paths.docs / "data").mkdir(exist_ok=True)
    if paths.facts_csv.exists():
        shutil.copyfile(paths.facts_csv, paths.docs / "data" / "facts.csv")
    (paths.docs / ".nojekyll").write_text("", encoding="utf-8")
    return out
