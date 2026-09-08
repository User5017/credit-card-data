"""dim_issuer: the charter-to-issuer crosswalk (crosswalks/issuers.csv) and the name matching around it.

One row per FDIC charter. issuer_id groups the charters of one issuer (Bread has two, Capital One has the survivor
plus two it absorbed); valid_from is the charter's established date and valid_to its merger date, both as the FDIC
records them (/institutions ESTYMD and ENDEFYMD, checked on every FDIC run); merged_into is the surviving
certificate, whose issuer names the roll-up. aliases carries the spellings other sources use for the charter
(the TCCP survey's 'Institution Name', a holding company name), pipe-separated; matching is on a normalized
form (case, punctuation and spacing dropped, '&' read as 'and'), never fuzzy.

The unmatched-name report (crosswalk_report) is what keeps the crosswalk honest: every refresh lists the TCCP
top-25 institution names of the newest file that match no row, and the charters whose FDIC legal name no longer
matches bank_name. The lines go into health.json under the tccp and fdic sources as messages, never as a status
change: a stale crosswalk is a maintenance item, not a data failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ISSUER_COLUMNS = [
    "issuer_id", "issuer_name", "fdic_cert", "bank_name", "kind", "sec_cik",
    "valid_from", "valid_to", "merged_into", "aliases", "note",
]
ISSUER_TYPES = {  # schema for reading issuers.csv into DuckDB (render._connect), no type sniffing
    "issuer_id": "VARCHAR", "issuer_name": "VARCHAR", "fdic_cert": "INTEGER", "bank_name": "VARCHAR", "kind": "VARCHAR",
    "sec_cik": "VARCHAR", "valid_from": "DATE", "valid_to": "DATE", "merged_into": "INTEGER", "aliases": "VARCHAR",
    "note": "VARCHAR",
}
KINDS = {"issuer", "sponsor"}
ALIAS_SEP = "|"
REPORT_PREFIX = "issuer crosswalk:"

_NOT_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


# ---------- the crosswalk ----------


def load_issuers(path: Path) -> pd.DataFrame:
    """crosswalks/issuers.csv -> one typed row per charter. Every inconsistency fails here, not in a view."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in ISSUER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    if df.empty:
        raise ValueError(f"{path.name}: no charters listed")
    df = df.apply(lambda col: col.str.strip())
    for c in ("issuer_id", "issuer_name", "fdic_cert", "bank_name", "kind", "valid_from"):
        n = int((df[c] == "").sum())
        if n:
            raise ValueError(f"{path.name}: {n} row(s) with an empty {c}")
    if not df["fdic_cert"].str.fullmatch(r"\d+").all():
        bad = sorted(df.loc[~df["fdic_cert"].str.fullmatch(r"\d+"), "fdic_cert"])
        raise ValueError(f"{path.name}: fdic_cert must be a number: {bad}")
    df["fdic_cert"] = df["fdic_cert"].astype(int)
    dupes = sorted(df.loc[df["fdic_cert"].duplicated(), "fdic_cert"])
    if dupes:
        raise ValueError(f"{path.name}: certificate(s) listed twice: {dupes}")
    bad_kind = sorted(set(df["kind"]) - KINDS)
    if bad_kind:
        raise ValueError(f"{path.name}: kind must be one of {sorted(KINDS)}, got {bad_kind}")
    for c in ("valid_from", "valid_to"):
        df[c] = pd.to_datetime(df[c].replace("", None), format="%Y-%m-%d")
    if not df["merged_into"].str.fullmatch(r"\d*").all():
        raise ValueError(f"{path.name}: merged_into must be a certificate or empty")
    df["merged_into"] = pd.to_numeric(df["merged_into"].replace("", None)).astype("Int64")
    has_date, has_target = df["valid_to"].notna(), df["merged_into"].notna()
    if (has_date != has_target).any():
        certs = sorted(df.loc[has_date != has_target, "fdic_cert"])
        raise ValueError(f"{path.name}: valid_to and merged_into must be set together: certs {certs}")
    late = df[has_date & (df["valid_to"] <= df["valid_from"])]
    if not late.empty:
        raise ValueError(f"{path.name}: valid_to before valid_from for certs {sorted(late['fdic_cert'])}")
    active = set(df.loc[~has_target, "fdic_cert"])
    for cert, target in df.loc[has_target, ["fdic_cert", "merged_into"]].itertuples(index=False):
        if int(target) not in active:
            raise ValueError(
                f"{path.name}: cert {cert} merged into {int(target)}, which is not listed as an active charter "
                "(chains of mergers are not supported; point at the surviving charter)"
            )
    name_index(df)  # aliases must be unambiguous
    return df.reset_index(drop=True)


def dimension(issuers: pd.DataFrame) -> pd.DataFrame:
    """The dimension as the views see it: one row per charter with the issuer its rows roll up to."""
    survivor = issuers.set_index("fdic_cert")[["issuer_id", "issuer_name"]]
    out = issuers[["fdic_cert", "bank_name", "kind", "issuer_id", "issuer_name", "valid_from", "valid_to", "merged_into"]].copy()
    target = out["merged_into"].astype("object").where(out["merged_into"].notna(), None)
    out["rollup_issuer_id"] = [
        survivor.loc[int(t), "issuer_id"] if t is not None else i for t, i in zip(target, out["issuer_id"])
    ]
    out["rollup_issuer_name"] = [
        survivor.loc[int(t), "issuer_name"] if t is not None else n for t, n in zip(target, out["issuer_name"])
    ]
    return out


# ---------- names ----------


def normalize_name(name) -> str:
    """'Bank Of America, National Association' -> 'bank of america national association', 'Citibank, N.A.' ->
    'citibank na'. Punctuation is dropped, '&' reads as 'and', case and spacing are collapsed."""
    s = str(name if name is not None else "").lower().replace("&", " and ")
    s = _NOT_WORD.sub("", s)
    return _SPACES.sub(" ", s).strip()


def aliases_of(row) -> list[str]:
    raw = row["aliases"] if isinstance(row, dict) else getattr(row, "aliases")
    return [a.strip() for a in str(raw or "").split(ALIAS_SEP) if a.strip()]


def name_index(issuers: pd.DataFrame) -> dict[str, tuple[str, int]]:
    """normalized name -> (issuer_id, cert) over bank_name and aliases. A spelling claimed by two charters fails."""
    out: dict[str, tuple[str, int]] = {}
    claimed: dict[str, str] = {}
    for row in issuers.itertuples(index=False):
        for spelling in [row.bank_name, *aliases_of(row)]:
            key = normalize_name(spelling)
            if not key:
                raise ValueError(f"cert {row.fdic_cert}: empty name or alias")
            if key in out and out[key][1] != row.fdic_cert:
                raise ValueError(
                    f"name {spelling!r} is claimed by cert {out[key][1]} ({claimed[key]!r}) and cert {row.fdic_cert}"
                )
            out[key] = (row.issuer_id, int(row.fdic_cert))
            claimed[key] = spelling
    return out


def match_names(issuers: pd.DataFrame, names) -> tuple[dict[str, str], list[str]]:
    """Source spellings -> {spelling: issuer_id} for the ones the crosswalk knows, plus the unmatched spellings, sorted."""
    idx = name_index(issuers)
    matched, unmatched = {}, []
    for name in dict.fromkeys(names):
        hit = idx.get(normalize_name(name))
        if hit:
            matched[name] = hit[0]
        else:
            unmatched.append(name)
    return matched, sorted(unmatched, key=str.lower)


# ---------- the report ----------


def tccp_top_names(products: pd.DataFrame) -> tuple[str, list[str]]:
    """(period_end, institution names flagged 'Issued by Top 25 Institution') in the newest TCCP file."""
    if products.empty:
        raise ValueError("no TCCP products")
    periods = pd.to_datetime(products["period_end"])
    latest = periods.max()
    sel = products[(periods == latest) & (products["top25"].astype(str) == "True")]
    return latest.date().isoformat(), sorted(sel["institution"].astype(str).unique(), key=str.lower)


def tccp_report(products: pd.DataFrame, issuers: pd.DataFrame) -> list[str]:
    period, names = tccp_top_names(products)
    if not names:
        return [f"{REPORT_PREFIX} the {period} TCCP file flags no top-25 institutions (flag missing?)"]
    matched, unmatched = match_names(issuers, names)
    if unmatched:
        return [
            f"{REPORT_PREFIX} {len(unmatched)} of {len(names)} top-25 institution names in the {period} TCCP file are not "
            f"in issuers.csv: {', '.join(unmatched)}"
        ]
    return [f"{REPORT_PREFIX} all {len(names)} top-25 institution names in the {period} TCCP file match issuers.csv"]


def fdic_name_report(institutions: pd.DataFrame, issuers: pd.DataFrame) -> list[str]:
    """Charters whose FDIC legal name (the /institutions NAME) no longer matches bank_name."""
    fdic_names = institutions.set_index("CERT")["NAME"]
    drift = []
    for row in issuers.itertuples(index=False):
        if int(row.fdic_cert) not in fdic_names.index:
            drift.append(f"cert {row.fdic_cert} has no FDIC institution record")
        elif normalize_name(fdic_names[int(row.fdic_cert)]) != normalize_name(row.bank_name):
            drift.append(f"cert {row.fdic_cert} is {fdic_names[int(row.fdic_cert)]!r} at the FDIC, bank_name says {row.bank_name!r}")
    if drift:
        return [f"{REPORT_PREFIX} {d}" for d in drift]
    return [f"{REPORT_PREFIX} all {len(issuers)} charter names match the FDIC institution records"]


def crosswalk_report(paths) -> dict[str, list[str]]:
    """source -> report lines, from the raw files on disk. Sources whose raw files are absent are left out."""
    from .fetchers import fdic  # local import: fdic imports this module for load_issuers

    issuers = load_issuers(paths.issuers_csv)
    out: dict[str, list[str]] = {}
    if paths.tccp_products_csv.exists():
        products = pd.read_csv(paths.tccp_products_csv, dtype=str, keep_default_na=False)
        out["tccp"] = tccp_report(products, issuers)
    institutions = paths.raw / fdic.SOURCE / "latest" / fdic.INSTITUTIONS_FILE
    if institutions.exists():
        out["fdic"] = fdic_name_report(fdic.parse_institutions_json(institutions), issuers)
    return out
