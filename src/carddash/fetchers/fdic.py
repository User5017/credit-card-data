"""FDIC BankFind Suite API fetcher: Call Report credit card items per charter and for all insured institutions.

Publication facts, verified 2026-09-08 (scouting in design/handoff-2026-09-07.html, section 2):
- Base https://api.fdic.gov/banks/financials, no key, docs at https://api.fdic.gov/banks/docs/. The pipeline's own
  User-Agent is accepted. Filters are Elasticsearch query strings, field names are uppercase, REPDTE is YYYYMMDD.
  The response header says 120 requests per window; one run makes about thirty.
- One request per charter (filters=CERT:<n>, limit=10000, format=csv) returns its full history, one row per
  quarter-end call report, up to 170 rows since 1984-03-31. Values are thousands of dollars. The CSV comes back with
  a quoted header, columns in alphabetical order and nulls as empty cells, so it is parsed by header text. A bank
  with no card book reports 0; null means the item was not collected (P3CRCD before 2001 Q1, P9CRCD, NACRCD and
  NCCRCD before 1991 Q1, the charge-off flows of some thrifts before 1990).
- Silent failures the fetcher guards against: an unknown field name is dropped from the response without error, a
  malformed filter or an unpublished quarter answers 200 with zero rows, and a response is cut at `limit`. So every
  requested field must be in the header, every charter must return rows, and a file at the limit is an error.
- Charge-offs: DRCRCD, CRCRCD and NTCRCD are year-to-date and reset each Q1. The FDIC's own quarterly flows
  DRCRCDQ, CRCRCDQ and NTCRCDQ equal the hand difference in every normal quarter but not in merger quarters
  (Capital One 2022 Q4, JPMorgan 2019 Q2), so the quarterly fields are loaded and the year-to-date ones stay in the
  raw file only. Noncurrent is the FDIC's NCCRCD, which equals P9CRCD + NACRCD on every row (some thrift-lineage
  filers put 90+ day balances in nonaccrual: USAA, First National Bank of Omaha, Citizens). NTCRCDQ equals
  DRCRCDQ - CRCRCDQ on every row. Both identities are asserted per charter. The ratio fields (NTCRCDR, P3CRCDR)
  divide by total assets, not card loans, and are not used.
- Industry total: agg_by=REPDTE with agg_sum_fields over the same items and the filter NOT BKCLASS:(NC OR OI)
  (noninsured institutions and insured US branches of foreign banks excluded) reproduces the Quarterly Banking
  Profile's 'All FDIC-Insured Institutions' credit card line to the million and its institution count exactly.
  A sum is 0, not null, before an item was collected, and a few filers reported 90+ day and nonaccrual card
  balances before 1991, so each item starts at a documented first quarter (AGG_START). The buckets come back in
  descending count order, which is ascending date order because the number of institutions falls every quarter.
- Charters come from crosswalks/issuers.csv (src/carddash/issuers.py), one row per FDIC certificate, with the
  established date, the merger date and the acquiring certificate for charters that no longer file. One call to
  /institutions for the listed certificates (saved as institutions.json) checks the crosswalk every run: ACTIVE
  must agree with valid_to, ESTYMD with valid_from, ENDEFYMD and NEWCERT with valid_to and merged_into. A
  merged-out charter's last call report is the last quarter end before the merger date (Discover Bank: merged
  2025-05-18, last report 2025-03-31). Every active charter ends on the industry total's last quarter, so a charter
  that merges or fails without a crosswalk update turns the source red rather than quietly shortening one line.
  Name drift (the FDIC renames a charter) is reported, not failed: see issuers.crosswalk_report. The issuer
  roll-up (acquirer plus acquired charters by period) is sql/views.sql v_fdic_issuer over dim_issuer.
- Timing: the API carried 2026-06-30 data on 2026-08-19, the QBP followed on 2026-08-25, 55 to 58 days after
  quarter end. Recent quarters can be amended, so only the year-old golden entry is checked live.

Outputs: raw_dir/latest/financials_<cert>.csv per charter, raw_dir/latest/aggregate_by_repdte.json and
raw_dir/latest/institutions.json, all response bodies byte for byte, then facts rows for six series per entity: CERT:<n> for each charter and
FDIC_ALL_INSURED for the industry. Thousands are turned into billions here; nothing else is computed.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
from pathlib import Path

import pandas as pd

from ..issuers import ISSUER_COLUMNS, ISSUER_TYPES, KINDS, load_issuers  # noqa: F401 - re-exported for callers
from ..paths import REPO_ROOT
from ..schema import FACT_COLUMNS, last_day

SOURCE = "fdic"
API_URL = "https://api.fdic.gov/banks/financials"
INSTITUTIONS_URL = "https://api.fdic.gov/banks/institutions"
ENTITY_ALL = "FDIC_ALL_INSURED"
ISSUERS_CSV = REPO_ROOT / "crosswalks" / "issuers.csv"
CERT_FILE = "financials_{cert}.csv"
CERT_GLOB = "financials_*.csv"
AGGREGATE_FILE = "aggregate_by_repdte.json"
INSTITUTIONS_FILE = "institutions.json"
INSTITUTIONS_LIMIT = 500  # the crosswalk lists a few dozen charters
LIMIT = 10000  # the API's maximum rows per call; a charter has at most about 170 quarters
AGG_LIMIT = 1000  # buckets in the industry call, one per quarter since 1984
MIN_QUARTERS_ALL = 100  # the industry series runs from 1984; far fewer buckets means a truncated or filtered answer
EXCLUDED_CLASSES = ("NC", "OI")  # noninsured institutions, insured US branches of foreign banks
THOUSANDS_TO_BILLIONS = 1e-6

# (metric, API field). The source reports thousands of dollars.
METRICS = [
    ("fdic_card_loans", "LNCRCD"),
    ("fdic_card_dq30_89", "P3CRCD"),
    ("fdic_card_noncurrent", "NCCRCD"),
    ("fdic_card_charge_offs_q", "DRCRCDQ"),
    ("fdic_card_recoveries_q", "CRCRCDQ"),
    ("fdic_card_nco_q", "NTCRCDQ"),
]
# Every field requested per charter: identifiers, the six loaded items, their components and the year-to-date flows
# (raw file only, so the quarterly-flow identity can be checked against the release).
FIELDS = [
    "CERT", "NAME", "REPDTE", "BKCLASS",
    "LNCRCD", "P3CRCD", "P9CRCD", "NACRCD", "NCCRCD",
    "DRCRCD", "CRCRCD", "NTCRCD", "DRCRCDQ", "CRCRCDQ", "NTCRCDQ",
]
VALUE_FIELDS = FIELDS[4:]
AGG_FIELDS = VALUE_FIELDS
# /institutions fields: the structural facts the crosswalk must agree with, plus names for the drift report
INSTITUTION_FIELDS = [
    "CERT", "NAME", "ACTIVE", "ESTYMD", "ENDEFYMD", "NEWCERT", "ULTCERT", "CHANGEC1", "PRIORNAME1", "NAMEHCR",
    "SPECGRPN", "INSTCRCD", "BKCLASS", "STALP",
]
INSTITUTION_REQUIRED = ["CERT", "NAME", "ACTIVE", "ESTYMD", "ENDEFYMD", "NEWCERT"]
OPEN_ENDED = "12/31/9999"  # ENDEFYMD of an active institution
# First quarter an item is loaded for the industry total: before it the sum is 0 (item not collected) or partial.
AGG_START = {
    "P3CRCD": dt.date(2001, 3, 31),
    "P9CRCD": dt.date(1991, 3, 31),
    "NACRCD": dt.date(1991, 3, 31),
    "NCCRCD": dt.date(1991, 3, 31),
}

_INT_RE = re.compile(r"-?\d+")

# ---------- dates and ids ----------


def entity_for(cert: int) -> str:
    return f"CERT:{cert}"


def parse_repdte(value) -> dt.date:
    """'20260630' -> 2026-06-30. Anything that is not a quarter end in YYYYMMDD fails."""
    s = str(value).strip()
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"REPDTE {value!r} is not YYYYMMDD")
    d = dt.date(int(s[:4]), int(s[4:6]), int(s[6:]))
    if d.month not in (3, 6, 9, 12) or d != last_day(d.year, d.month):
        raise ValueError(f"REPDTE {s} is not a quarter end")
    return d


def quarter_end_before(date: dt.date) -> dt.date:
    """The last quarter end strictly before `date`: a charter merged on 2025-05-18 filed its last report for 2025-03-31."""
    return dt.date(date.year, ((date.month - 1) // 3) * 3 + 1, 1) - dt.timedelta(days=1)


# ---------- parsing the raw files ----------


def _int_or_null(value: str, field: str, where: str) -> int | None:
    s = value.strip()
    if s == "":
        return None
    if not _INT_RE.fullmatch(s):
        raise ValueError(f"{where}: {field} = {value!r} is not an integer number of thousands")
    return int(s)


def _json_int(value, key: str, where: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{where}: {key} is {value!r}, expected a number")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError(f"{where}: {key} = {value!r} is not an integer number of thousands")


def _check_identities(df: pd.DataFrame, name: str) -> None:
    """NCCRCD = P9CRCD + NACRCD and NTCRCDQ = DRCRCDQ - CRCRCDQ hold on every published row; a break means the
    FDIC changed a definition and the scope notes are wrong."""
    nc = df.dropna(subset=["NCCRCD", "P9CRCD", "NACRCD"])
    bad = nc[nc["NCCRCD"] != nc["P9CRCD"] + nc["NACRCD"]]
    if not bad.empty:
        raise ValueError(f"{name}: NCCRCD is not P9CRCD + NACRCD on {len(bad)} row(s), first {list(bad['REPDTE'].head(3))}")
    nt = df.dropna(subset=["NTCRCDQ", "DRCRCDQ", "CRCRCDQ"])
    bad = nt[nt["NTCRCDQ"] != nt["DRCRCDQ"] - nt["CRCRCDQ"]]
    if not bad.empty:
        raise ValueError(f"{name}: NTCRCDQ is not DRCRCDQ - CRCRCDQ on {len(bad)} row(s), first {list(bad['REPDTE'].head(3))}")


def parse_cert_csv(path: Path, cert: int) -> pd.DataFrame:
    """One charter's CSV -> frame sorted by REPDTE (date) with CERT, NAME, BKCLASS and the value fields as nullable ints."""
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"{path.name}: empty response")
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    missing = [f for f in FIELDS if f not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: fields missing from the response: {missing} (the API drops unknown field names silently)")
    if df.empty:
        raise ValueError(f"{path.name}: no rows for cert {cert} (unknown certificate, or the API answered 200 with nothing)")
    if len(df) >= LIMIT:
        raise ValueError(f"{path.name}: {len(df)} rows, at the API limit of {LIMIT}; the history is truncated")
    other = sorted(set(df["CERT"].str.strip()) - {str(cert)})
    if other:
        raise ValueError(f"{path.name}: rows for cert(s) {other}, expected only {cert}")
    out = pd.DataFrame({"CERT": cert, "NAME": df["NAME"].str.strip(), "BKCLASS": df["BKCLASS"].str.strip()})
    out["REPDTE"] = [parse_repdte(s) for s in df["REPDTE"]]
    for f in VALUE_FIELDS:
        out[f] = pd.array(
            [_int_or_null(v, f, f"{path.name} REPDTE {d}") for v, d in zip(df[f], df["REPDTE"])], dtype="Int64"
        )
    dupes = sorted({d.isoformat() for d in out.loc[out["REPDTE"].duplicated(), "REPDTE"]})
    if dupes:
        raise ValueError(f"{path.name}: duplicate quarters {dupes}")
    out = out.sort_values("REPDTE").reset_index(drop=True)
    _check_identities(out, path.name)
    return out


def parse_aggregate_json(path: Path) -> pd.DataFrame:
    """The industry call -> frame sorted by REPDTE (date) with count and one column per summed field (ints)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path.name}: not JSON ({exc})") from None
    buckets = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(buckets, list) or not buckets:
        raise ValueError(f"{path.name}: no aggregation buckets in the response")
    if len(buckets) >= AGG_LIMIT:
        raise ValueError(f"{path.name}: {len(buckets)} buckets, at agg_limit {AGG_LIMIT}; the history is truncated")
    if len(buckets) < MIN_QUARTERS_ALL:
        raise ValueError(f"{path.name}: only {len(buckets)} quarters, expected the full history since 1984")
    wanted = ["REPDTE", "count"] + [f"sum_{f}" for f in AGG_FIELDS]
    rows = []
    for b in buckets:
        d = b.get("data") if isinstance(b, dict) else None
        if not isinstance(d, dict):
            raise ValueError(f"{path.name}: bucket without a 'data' object: {b!r}")
        missing = [k for k in wanted if k not in d]
        if missing:
            raise ValueError(f"{path.name}: bucket {d.get('REPDTE')!r} lacks {missing} (unknown sum fields are dropped silently)")
        where = f"{path.name} REPDTE {d['REPDTE']}"
        row = {"REPDTE": parse_repdte(d["REPDTE"]), "count": _json_int(d["count"], "count", where)}
        for f in AGG_FIELDS:
            row[f] = _json_int(d[f"sum_{f}"], f"sum_{f}", where)
        rows.append(row)
    out = pd.DataFrame(rows)
    dupes = sorted({d.isoformat() for d in out.loc[out["REPDTE"].duplicated(), "REPDTE"]})
    if dupes:
        raise ValueError(f"{path.name}: duplicate quarters {dupes}")
    return out.sort_values("REPDTE").reset_index(drop=True)


def parse_mdy(value, where: str):
    """'05/18/2025' -> date; the FDIC's open-ended '12/31/9999' -> None."""
    s = str(value).strip()
    if s == OPEN_ENDED:
        return None
    try:
        return dt.datetime.strptime(s, "%m/%d/%Y").date()
    except ValueError:
        raise ValueError(f"{where}: {value!r} is not a MM/DD/YYYY date") from None


def parse_institutions_json(path: Path) -> pd.DataFrame:
    """The /institutions call -> one row per certificate: CERT, NAME, ACTIVE (bool), ESTYMD (date), ENDEFYMD (date or
    None while active), NEWCERT (int or None), plus the descriptive fields as text."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path.name}: not JSON ({exc})") from None
    records = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError(f"{path.name}: no institution records in the response")
    rows = []
    for r in records:
        d = r.get("data") if isinstance(r, dict) else None
        if not isinstance(d, dict):
            raise ValueError(f"{path.name}: record without a 'data' object: {r!r}")
        missing = [k for k in INSTITUTION_REQUIRED if k not in d]
        if missing:
            raise ValueError(f"{path.name}: record {d.get('CERT')!r} lacks {missing} (unknown fields are dropped silently)")
        where = f"{path.name} cert {d['CERT']}"
        active = d["ACTIVE"]
        if active not in (0, 1, "0", "1"):
            raise ValueError(f"{where}: ACTIVE is {active!r}, expected 0 or 1")
        newcert = _json_int(d["NEWCERT"], "NEWCERT", where) if d["NEWCERT"] not in (None, "", "0", 0) else None
        rows.append(
            {
                "CERT": _json_int(d["CERT"], "CERT", where),
                "NAME": str(d["NAME"]).strip(),
                "ACTIVE": int(active) == 1,
                "ESTYMD": parse_mdy(d["ESTYMD"], where),
                "ENDEFYMD": parse_mdy(d["ENDEFYMD"], where),
                "NEWCERT": newcert,
                **{k: (str(d[k]).strip() if d.get(k) is not None else "") for k in INSTITUTION_FIELDS if k not in INSTITUTION_REQUIRED},
            }
        )
    out = pd.DataFrame(rows)
    dupes = sorted(out.loc[out["CERT"].duplicated(), "CERT"])
    if dupes:
        raise ValueError(f"{path.name}: certificate(s) returned twice: {dupes}")
    out["ESTYMD"] = out["ESTYMD"].astype(object)
    out["ENDEFYMD"] = out["ENDEFYMD"].astype(object)
    out["NEWCERT"] = out["NEWCERT"].astype("Int64")
    return out.sort_values("CERT").reset_index(drop=True)


def check_crosswalk(institutions: pd.DataFrame, issuers: pd.DataFrame) -> None:
    """issuers.csv must say what the FDIC says about each charter's life: established, active, merged when and into
    whom. A disagreement is a crosswalk maintenance item and fails the run so it gets done."""
    by_cert = institutions.set_index("CERT")
    listed = [int(c) for c in issuers["fdic_cert"]]
    missing = sorted(set(listed) - set(by_cert.index))
    extra = sorted(set(by_cert.index) - set(listed))
    if missing or extra:
        raise ValueError(f"institutions.json: certificates missing {missing}, not in issuers.csv {extra}")
    problems = []
    for row in issuers.itertuples(index=False):
        cert = int(row.fdic_cert)
        rec = by_cert.loc[cert]
        merged = not pd.isna(row.valid_to)
        if rec["ACTIVE"] == merged:
            problems.append(f"cert {cert} is {'active' if rec['ACTIVE'] else 'closed'} at the FDIC, issuers.csv says {'merged' if merged else 'active'}")
        if rec["ESTYMD"] != row.valid_from.date():
            problems.append(f"cert {cert} established {rec['ESTYMD']} at the FDIC, valid_from says {row.valid_from.date()}")
        if merged:
            if rec["ENDEFYMD"] != row.valid_to.date():
                problems.append(f"cert {cert} ended {rec['ENDEFYMD']} at the FDIC, valid_to says {row.valid_to.date()}")
            if pd.isna(rec["NEWCERT"]) or int(rec["NEWCERT"]) != int(row.merged_into):
                problems.append(f"cert {cert} continues as {rec['NEWCERT']} at the FDIC, merged_into says {int(row.merged_into)}")
        elif not pd.isna(rec["NEWCERT"]) or rec["ENDEFYMD"] is not None:
            problems.append(f"cert {cert} has an end date {rec['ENDEFYMD']} or successor {rec['NEWCERT']} at the FDIC but no valid_to")
    if problems:
        raise ValueError("crosswalks/issuers.csv disagrees with the FDIC: " + "; ".join(problems[:6]))


# ---------- facts ----------


def _facts(period_end, values, metric: str, entity: str, entity_type: str, pulled_at: str) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "metric": metric,
            "entity": entity,
            "entity_type": entity_type,
            "tier": "all",
            "period_end": pd.to_datetime(list(period_end)),
            "period_type": "Q",
            "value": [float(v) * THOUSANDS_TO_BILLIONS for v in values],
            "source": SOURCE,
            "pulled_at": pulled_at,
        }
    )
    return df[FACT_COLUMNS]


def cert_facts(df: pd.DataFrame, cert: int, pulled_at: str) -> pd.DataFrame:
    """Six series for one charter. Quarters where an item is null (not collected) are left out, never zero-filled."""
    frames = []
    for metric, field in METRICS:
        sel = df[df[field].notna()]
        if sel.empty:
            raise ValueError(f"cert {cert}: {field} is null in every quarter")
        frames.append(_facts(sel["REPDTE"], sel[field], metric, entity_for(cert), "bank", pulled_at))
    return pd.concat(frames, ignore_index=True)


def aggregate_facts(df: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """Six series for all insured institutions, each from the first quarter its item was collected."""
    frames = []
    for metric, field in METRICS:
        start = AGG_START.get(field)
        sel = df[df["REPDTE"] >= start] if start else df
        if sel.empty:
            raise ValueError(f"industry total: no quarters from {start} on for {field}")
        if sel[field].iloc[0] <= 0:
            raise ValueError(f"industry total: sum_{field} is {sel[field].iloc[0]} at {sel['REPDTE'].iloc[0]}, its documented first quarter")
        frames.append(_facts(sel["REPDTE"], sel[field], metric, ENTITY_ALL, "aggregate", pulled_at))
    return pd.concat(frames, ignore_index=True)


def parse_release(latest_dir: Path, issuers: pd.DataFrame, pulled_at: str) -> pd.DataFrame:
    """Every file in latest_dir -> facts, with the cross-checks between charters, mergers and the industry total."""
    expected = {CERT_FILE.format(cert=c): int(c) for c in issuers["fdic_cert"]}
    found = {p.name for p in latest_dir.glob(CERT_GLOB)}
    missing, extra = sorted(set(expected) - found), sorted(found - set(expected))
    if missing or extra:
        raise ValueError(f"{latest_dir}: charter files missing {missing}, not in issuers.csv {extra}")
    agg_path = latest_dir / AGGREGATE_FILE
    if not agg_path.exists():
        raise ValueError(f"{latest_dir}: no {AGGREGATE_FILE}")
    inst_path = latest_dir / INSTITUTIONS_FILE
    if not inst_path.exists():
        raise ValueError(f"{latest_dir}: no {INSTITUTIONS_FILE}")
    check_crosswalk(parse_institutions_json(inst_path), issuers)
    agg = parse_aggregate_json(agg_path)
    last_all = agg["REPDTE"].iloc[-1]
    frames = [aggregate_facts(agg, pulled_at)]
    for row in issuers.itertuples(index=False):
        cert = int(row.fdic_cert)
        df = parse_cert_csv(latest_dir / CERT_FILE.format(cert=cert), cert)
        last = df["REPDTE"].iloc[-1]
        if pd.isna(row.valid_to):
            if last != last_all:
                raise ValueError(
                    f"cert {cert} ({row.bank_name}) last reports {last}, the industry total runs to {last_all}: "
                    "merged, failed or late? Update crosswalks/issuers.csv"
                )
        else:
            want = quarter_end_before(row.valid_to.date())
            if last != want:
                raise ValueError(
                    f"cert {cert} ({row.bank_name}) merged {row.valid_to.date()} should end with the {want} call report, "
                    f"last row is {last}"
                )
        frames.append(cert_facts(df, cert, pulled_at))
    return pd.concat(frames, ignore_index=True)


# ---------- download ----------


def cert_params(cert: int) -> dict:
    return {
        "filters": f"CERT:{cert}",
        "fields": ",".join(FIELDS),
        "sort_by": "REPDTE",
        "sort_order": "ASC",
        "limit": LIMIT,
        "format": "csv",
    }


def aggregate_params() -> dict:
    return {
        "filters": f"NOT BKCLASS:({' OR '.join(EXCLUDED_CLASSES)})",
        "agg_by": "REPDTE",
        "agg_sum_fields": ",".join(AGG_FIELDS),
        "agg_limit": AGG_LIMIT,
        "limit": 0,
    }


def institutions_params(certs) -> dict:
    return {
        "filters": "CERT:(" + " OR ".join(str(int(c)) for c in certs) + ")",
        "fields": ",".join(INSTITUTION_FIELDS),
        "limit": INSTITUTIONS_LIMIT,
    }


def download(session, params: dict, dest: Path, marker: bytes, url: str = API_URL) -> None:
    """GET the API with `params`, require `marker` near the top of the body (an error page has neither a CSV
    header nor a data array), save the body byte for byte."""
    resp = session.get(url, params=params)
    resp.raise_for_status()
    if marker not in resp.content[:4000]:
        raise ValueError(f"{dest.name}: unexpected response (starts {resp.content[:80]!r})")
    dest.write_bytes(resp.content)


def fetch(meta: pd.DataFrame, raw_dir: Path, session, pulled_at: str) -> pd.DataFrame:
    """Series come out of METRICS and issuers.csv, not out of meta. The loader rejects any that series.csv does not list."""
    issuers = load_issuers(ISSUERS_CSV)
    latest = raw_dir / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    keep = set()
    for cert in issuers["fdic_cert"]:
        dest = latest / CERT_FILE.format(cert=int(cert))
        download(session, cert_params(int(cert)), dest, b"REPDTE")
        keep.add(dest.name)
    download(session, aggregate_params(), latest / AGGREGATE_FILE, b'"data"')
    download(session, institutions_params(issuers["fdic_cert"]), latest / INSTITUTIONS_FILE, b'"data"', url=INSTITUTIONS_URL)
    for stale in latest.glob(CERT_GLOB):  # latest/ mirrors issuers.csv, as replace-by-source does for facts
        if stale.name not in keep:
            stale.unlink()
    return parse_release(latest, issuers, pulled_at)
