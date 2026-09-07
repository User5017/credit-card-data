"""Spot-check golden entries against the Fed's own G.19 release page, parsed directly (no FRED, no LLM).

    uv run python checks/verify_g19.py

Reads checks/golden.yaml, fetches https://www.federalreserve.gov/releases/g19/current/default.htm, parses the
HTML tables, maps each golden period to a table column, and prints PASS/FAIL. Exit code 1 on any failure.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sys
from pathlib import Path

import requests
import yaml

G19_URL = "https://www.federalreserve.gov/releases/g19/current/default.htm"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# golden metric -> (row label on the page, which table)
ROW_FOR_METRIC = {
    "revolving_credit_sa": ("Revolving", "level_sa"),
    "card_apr_all_accounts": ("All accounts", "rates"),
    "card_apr_assessed_interest": ("Accounts assessed interest", "rates"),
}


def _cells(row_html: str) -> list[tuple[str, int]]:
    """(text, colspan) for each cell."""
    out = []
    for m in re.finditer(r"<t([dh])([^>]*)>(.*?)</t[dh]>", row_html, flags=re.S):
        attrs, inner = m.group(2), m.group(3)
        span = re.search(r'colspan="?(\d+)', attrs)
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        out.append((text, int(span.group(1)) if span else 1))
    return out


def parse_tables(page: str) -> list[dict[str, dict[str, str]]]:
    """Each table -> {row label: {column key: value}}. Column keys look like '2025', 'Q1 2026', 'Jun 2026'."""
    tables = []
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", page, flags=re.S):
        rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, flags=re.S)]
        rows = [r for r in rows if r]
        # find the header row with period labels (Q1, Apr, 2024 ...) and the year row just above it
        col_keys: list[str] | None = None
        data: dict[str, dict[str, str]] = {}
        for i, r in enumerate(rows):
            labels = [c[0] for c in r]
            if col_keys is None and sum(bool(re.fullmatch(r"(Q[1-4]|[A-Z][a-z]{2}|20\d\d)( [rp])?", x)) for x in labels) >= 6:
                years: list[str] = []
                if i > 0:
                    for text, span in rows[i - 1]:
                        years.extend([text] * span)
                keys = []
                period_cols = [x for x in labels if x != ""]
                # the year row aligns to period columns from the right (label cell is unspanned on the left)
                years = years[-len(period_cols):] if years else [""] * len(period_cols)
                for lab, yr in zip(period_cols, years):
                    lab = re.sub(r" [rp]$", "", lab)
                    keys.append(lab if re.fullmatch(r"20\d\d", lab) else f"{lab} {yr}".strip())
                col_keys = keys
                continue
            if col_keys and len(labels) >= 2:
                name = labels[0]
                vals = labels[1:]
                if len(vals) == len(col_keys) and name:
                    data.setdefault(name, {})
                    # a table can hold several 'Revolving' rows (pct change, flow, level); keep them all, in order
                    key = name if name not in data or not data[name] else f"{name}#{sum(k.startswith(name) for k in data)}"
                    data[key] = dict(zip(col_keys, vals))
        if data:
            tables.append(data)
    return tables


def find_row(tables, label: str, kind: str) -> dict[str, str] | None:
    for t in tables:
        for name, row in t.items():
            if not name.split("#")[0] == label:
                continue
            nums = [_num(v) for v in row.values()]
            nums = [n for n in nums if n is not None]
            if not nums:
                continue
            if kind == "level_sa" and max(nums) > 500:  # levels in $bn, first (SA) table wins
                return row
            if kind == "rates" and max(nums) < 100:
                return row
    return None


def _num(s: str) -> float | None:
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def columns_for(period_end: dt.date, period_type: str) -> list[str]:
    """Candidate column labels, most specific first. A quarter-end month can appear as its month, its
    quarter, or (December) its year; the level tables show the end-of-period value under each."""
    y, m = period_end.year, period_end.month
    if period_type == "M":
        cands = [f"{MONTHS[m - 1]} {y}"]
        if m % 3 == 0:
            cands.append(f"Q{m // 3} {y}")
        if m == 12:
            cands.append(str(y))
        return cands
    if period_type == "Q":
        return [f"Q{(m - 1) // 3 + 1} {y}"]
    raise ValueError(period_type)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    entries = yaml.safe_load((root / "checks" / "golden.yaml").read_text(encoding="utf-8")) or []
    page = requests.get(G19_URL, headers={"User-Agent": "carddash/0.1 (+https://github.com/User5017/credit-card-data)"}, timeout=60).text
    tables = parse_tables(page)
    bad = 0
    for e in entries:
        if "g19" not in e.get("origin_url", ""):
            continue
        label, kind = ROW_FOR_METRIC[e["metric"]]
        row = find_row(tables, label, kind)
        cands = columns_for(dt.date.fromisoformat(str(e["period_end"])), e["period_type"])
        col, got = None, None
        for c in cands:
            got = _num(row.get(c, "")) if row else None
            if got is not None:
                col = c
                break
        if got is None:
            # the page only shows the last three months and quarter/year ends; older months are not checkable here
            print(f"SKIP {e['id']}: none of {cands} on the current page")
            continue
        ok = abs(got - float(e["expected"])) <= float(e.get("tolerance", 0))
        bad += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'} {e['id']}: page {got} vs golden {e['expected']} (column {col})")
    print("all checked entries match the release page" if not bad else f"{bad} mismatch(es)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
