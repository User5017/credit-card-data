"""dim_issuer: the crosswalk loads and validates, names match on a normalized form, the unmatched-name report lists
what the crosswalk does not know, and the FDIC institution records agree with valid_from/valid_to/merged_into.

Expected names were read from the checked-in H2 2025 TCCP workbook (column 'Institution Name' where 'Issued by Top
25 Institution' is Yes) and tests/fixtures/fdic/institutions.json by hand on 2026-09-08.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from carddash import issuers as iss
from carddash.fetchers import fdic, tccp
from carddash.paths import Paths
from carddash.render import _connect
from conftest import FDIC_FIXTURE_CERTS, FDIC_FIXTURE_DIR, FIXTURES, REPO

ISSUERS_CSV = REPO / "crosswalks" / "issuers.csv"
H2_2025 = "2025-12-31"
# top-25 TCCP institutions of the H2 2025 file that no charter in issuers.csv covers (credit unions are NCUA, not FDIC)
UNMATCHED_H2_2025 = [
    "America First Federal Credit Union",
    "Boeing Employees' Credit Union",
    "Credit One Bank, National Association",
    "Fifth Third Bank, National Association",
    "Merrick Bank",
    "Navy Federal Credit Union",
    "Pentagon Federal Credit Union",
    "Regions Bank",
    "Stride Bank, National Association",
]


@pytest.fixture(scope="module")
def issuers() -> pd.DataFrame:
    return iss.load_issuers(ISSUERS_CSV)


@pytest.fixture(scope="module")
def h2_2025_products() -> pd.DataFrame:
    return tccp.parse_workbook(FIXTURES / "tccp" / "cfpb_tccp-data_2025-12-31.xlsx", dt.date(2025, 12, 31))


# ---------- the crosswalk ----------


def test_every_charter_has_the_fdic_established_date_and_merger_facts(issuers):
    assert len(issuers) == 30
    assert issuers["valid_from"].notna().all()
    by = issuers.set_index("fdic_cert")
    assert by.loc[4297, "valid_from"] == pd.Timestamp("1933-05-22")  # Hibernia National Bank's charter
    assert by.loc[628, "valid_from"] == pd.Timestamp("1824-01-01")
    assert by.loc[18409, "valid_from"] == pd.Timestamp("1852-01-01") and by.loc[18409, "issuer_id"] == "TD"
    merged = by[by["valid_to"].notna()]
    assert {int(c): (d.date().isoformat(), int(t)) for c, d, t in zip(merged.index, merged["valid_to"], merged["merged_into"])} == {
        5649: ("2025-05-18", 4297),
        33954: ("2022-10-03", 4297),
        23702: ("2019-05-18", 628),
        35328: ("2018-04-01", 27471),
        34351: ("2024-06-01", 32188),
    }
    assert (merged["valid_to"] > merged["valid_from"]).all()


def test_dimension_rolls_merged_charters_up_to_the_survivor(issuers):
    dim = iss.dimension(issuers).set_index("fdic_cert")
    assert dim.loc[5649, "issuer_id"] == "DISCOVER" and dim.loc[5649, "rollup_issuer_id"] == "CAPITAL_ONE"
    assert dim.loc[5649, "rollup_issuer_name"] == "Capital One"
    assert dim.loc[33954, "rollup_issuer_id"] == "CAPITAL_ONE" and dim.loc[23702, "rollup_issuer_id"] == "JPMORGAN"
    assert dim.loc[35328, "rollup_issuer_id"] == "AMEX" and dim.loc[34351, "rollup_issuer_id"] == "USAA"
    active = dim[dim["valid_to"].isna()]
    assert (active["rollup_issuer_id"] == active["issuer_id"]).all()
    assert set(dim.loc[[57570, 27499], "rollup_issuer_id"]) == {"BREAD"}
    assert set(dim.loc[[33947, 18409], "rollup_issuer_id"]) == {"TD"}


def test_dim_issuer_view_matches_the_python_dimension(issuers, fdic_facts):
    con = _connect(fdic_facts, REPO / "sql" / "views.sql", issuers_csv=ISSUERS_CSV)
    try:
        rows = con.execute(
            "SELECT fdic_cert, issuer_id, rollup_issuer_id, valid_from, valid_to, merged_into FROM dim_issuer ORDER BY fdic_cert"
        ).fetchall()
    finally:
        con.close()
    dim = iss.dimension(issuers).set_index("fdic_cert")
    assert len(rows) == len(dim)
    for cert, issuer_id, rollup, valid_from, valid_to, merged_into in rows:
        assert dim.loc[cert, "issuer_id"] == issuer_id and dim.loc[cert, "rollup_issuer_id"] == rollup
        assert pd.Timestamp(valid_from) == dim.loc[cert, "valid_from"]
        assert (valid_to is None) == pd.isna(dim.loc[cert, "valid_to"])
        assert (merged_into is None) == pd.isna(dim.loc[cert, "merged_into"])


# ---------- names ----------


@pytest.mark.parametrize(
    "raw, norm",
    [
        ("Bank Of America, National Association", "bank of america national association"),
        ("Citibank, N.A.", "citibank na"),
        ("American Express National  Bank", "american express national bank"),
        ("American Express Bank,  FSB.", "american express bank fsb"),
        ("Evolve Bank & Trust", "evolve bank and trust"),
        ("EVOLVE BANK&TRUST", "evolve bank and trust"),
        ("  Jpmorgan   Chase Bank, National Association ", "jpmorgan chase bank national association"),
        (None, ""),
    ],
)
def test_normalize_name(raw, norm):
    assert iss.normalize_name(raw) == norm


def test_name_index_covers_bank_names_and_aliases(issuers):
    idx = iss.name_index(issuers)
    assert idx["citibank na"] == ("CITI", 7213) and idx["citibank national association"] == ("CITI", 7213)
    assert idx["synchrony financial"] == ("SYNCHRONY", 27314)
    assert idx["us bancorp"] == ("US_BANK", 6548)
    assert idx["td bank na"] == ("TD", 18409) and idx["td bank usa na"] == ("TD", 33947)
    assert idx["chase"] == ("JPMORGAN", 628)


def test_match_names_is_exact_after_normalization(issuers):
    matched, unmatched = iss.match_names(issuers, ["CITIBANK, N.A.", "Citibank, N.A.", "Citibank NA Ltd", "Discover Bank", "Ally Bank"])
    assert matched == {"CITIBANK, N.A.": "CITI", "Citibank, N.A.": "CITI", "Discover Bank": "DISCOVER"}
    assert unmatched == ["Ally Bank", "Citibank NA Ltd"]


def test_an_alias_claimed_by_two_charters_fails(tmp_path):
    df = pd.read_csv(ISSUERS_CSV, dtype=str, keep_default_na=False)
    df.loc[df["fdic_cert"] == "7213", "aliases"] = df.loc[df["fdic_cert"] == "7213", "aliases"] + "|Synchrony Financial"
    path = tmp_path / "issuers.csv"
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="'Synchrony Financial' is claimed by cert (7213|27314) .* and cert (27314|7213)"):
        iss.load_issuers(path)


# ---------- the report ----------


def test_tccp_top_names_come_from_the_newest_file(h2_2025_products):
    period, names = iss.tccp_top_names(h2_2025_products)
    assert period == H2_2025
    assert len(names) == 28
    assert "First National Bank Of Omaha" in names and "Ally Bank" not in names  # Ally left the top-25 list in H2 2025
    assert names == sorted(names, key=str.lower)


def test_tccp_report_lists_the_top25_names_the_crosswalk_does_not_know(issuers, h2_2025_products):
    (line,) = iss.tccp_report(h2_2025_products, issuers)
    assert line.startswith(iss.REPORT_PREFIX)
    assert f"9 of 28 top-25 institution names in the {H2_2025} TCCP file are not in issuers.csv: " in line
    assert line.endswith(", ".join(UNMATCHED_H2_2025))
    matched, unmatched = iss.match_names(issuers, iss.tccp_top_names(h2_2025_products)[1])
    assert unmatched == UNMATCHED_H2_2025
    assert set(matched.values()) == {
        "AMEX", "BARCLAYS", "BOFA", "BREAD", "CAPITAL_ONE", "CITI", "CITIZENS", "FNBO", "GOLDMAN", "JPMORGAN", "PNC",
        "SYNCHRONY", "TD", "TRUIST", "US_BANK", "USAA", "WELLS_FARGO",
    }


def test_tccp_report_when_everything_matches(issuers):
    products = pd.DataFrame(
        {"period_end": ["2025-12-31", "2025-12-31", "2025-06-30"], "institution": ["Citibank, N.A.", "Discover Bank", "Ally Bank"],
         "top25": ["True", "True", "True"]}
    )
    assert iss.tccp_report(products, issuers) == [f"{iss.REPORT_PREFIX} all 2 top-25 institution names in the 2025-12-31 TCCP file match issuers.csv"]
    none = products.assign(top25="False")
    assert "flags no top-25 institutions" in iss.tccp_report(none, issuers)[0]
    with pytest.raises(ValueError, match="no TCCP products"):
        iss.tccp_top_names(products.iloc[0:0])


def test_fdic_name_report(issuers):
    inst = fdic.parse_institutions_json(FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE)
    sub = issuers[issuers["fdic_cert"].isin(FDIC_FIXTURE_CERTS)]
    assert iss.fdic_name_report(inst, sub) == [f"{iss.REPORT_PREFIX} all 5 charter names match the FDIC institution records"]
    renamed = inst.copy()
    renamed.loc[renamed["CERT"] == 5649, "NAME"] = "Discover Bank of Delaware"
    (line,) = iss.fdic_name_report(renamed, sub)
    assert line == f"{iss.REPORT_PREFIX} cert 5649 is 'Discover Bank of Delaware' at the FDIC, bank_name says 'Discover Bank'"
    (line,) = iss.fdic_name_report(inst[inst["CERT"] != 628], sub)
    assert line == f"{iss.REPORT_PREFIX} cert 628 has no FDIC institution record"


def test_crosswalk_report_reads_the_raw_files(tmp_path, h2_2025_products):
    paths = Paths.from_root(REPO, data=tmp_path / "data", docs=tmp_path / "docs")
    assert iss.crosswalk_report(paths) == {}  # nothing fetched yet, nothing to report
    paths.tccp_products_csv.parent.mkdir(parents=True)
    tccp.write_products(h2_2025_products, paths.tccp_products_csv)
    latest = paths.raw / "fdic" / "latest"
    latest.mkdir(parents=True)
    (latest / fdic.INSTITUTIONS_FILE).write_bytes((FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE).read_bytes())
    report = iss.crosswalk_report(paths)
    assert set(report) == {"tccp", "fdic"}
    assert "9 of 28 top-25 institution names" in report["tccp"][0]
    # the institutions file covers five charters, the crosswalk thirty: the other 25 have no record in it
    assert len(report["fdic"]) == 25 and all("has no FDIC institution record" in line for line in report["fdic"])


# ---------- the FDIC institution records ----------


def test_institutions_fixture_parses():
    inst = fdic.parse_institutions_json(FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE).set_index("CERT")
    assert sorted(inst.index) == sorted(FDIC_FIXTURE_CERTS)
    assert inst.loc[5649, "NAME"] == "Discover Bank" and not inst.loc[5649, "ACTIVE"]
    assert inst.loc[5649, "ESTYMD"] == dt.date(1911, 8, 30) and inst.loc[5649, "ENDEFYMD"] == dt.date(2025, 5, 18)
    assert int(inst.loc[5649, "NEWCERT"]) == 4297 and inst.loc[5649, "PRIORNAME1"] == "Greenwood Trust Company"
    assert inst.loc[4297, "ACTIVE"] and inst.loc[4297, "ENDEFYMD"] is None and pd.isna(inst.loc[4297, "NEWCERT"])
    assert inst.loc[4297, "ESTYMD"] == dt.date(1933, 5, 22) and inst.loc[4297, "NAMEHCR"] == "CAPITAL ONE FINANCIAL CORP"
    assert inst.loc[33954, "ENDEFYMD"] == dt.date(2022, 10, 3) and int(inst.loc[33954, "NEWCERT"]) == 4297
    assert inst.loc[34404, "SPECGRPN"] == "Commercial Lending Specialization"


def _institutions_doc() -> dict:
    return json.loads((FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE).read_text(encoding="utf-8"))


def _write(tmp_path, doc) -> "Path":  # noqa: F821
    path = tmp_path / fdic.INSTITUTIONS_FILE
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _set(doc, cert, **fields):
    for r in doc["data"]:
        if r["data"]["CERT"] == cert:
            r["data"].update(fields)
    return doc


@pytest.mark.parametrize(
    "fn, match",
    [
        (lambda d: {**d, "data": []}, "no institution records"),
        (lambda d: {**d, "data": [{"data": {k: v for k, v in r["data"].items() if k != "NEWCERT"}} for r in d["data"]]}, r"lacks \['NEWCERT'\]"),
        (lambda d: _set(d, 628, ACTIVE=2), "ACTIVE is 2"),
        (lambda d: _set(d, 628, ESTYMD="1824-01-01"), "not a MM/DD/YYYY date"),
        (lambda d: {**d, "data": d["data"] + [d["data"][0]]}, "returned twice"),
    ],
)
def test_institution_file_problems_fail(tmp_path, fn, match):
    with pytest.raises(ValueError, match=match):
        fdic.parse_institutions_json(_write(tmp_path, fn(_institutions_doc())))


def test_check_crosswalk_passes_on_the_fixture(issuers):
    sub = issuers[issuers["fdic_cert"].isin(FDIC_FIXTURE_CERTS)]
    fdic.check_crosswalk(fdic.parse_institutions_json(FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE), sub)


@pytest.mark.parametrize(
    "fn, match",
    [
        (lambda d: _set(d, 5649, ACTIVE=1, ENDEFYMD="12/31/9999", NEWCERT=0), "cert 5649 is active at the FDIC, issuers.csv says merged"),
        (lambda d: _set(d, 628, ACTIVE=0, ENDEFYMD="06/30/2026", NEWCERT=4297), "cert 628 is closed at the FDIC, issuers.csv says active"),
        (lambda d: _set(d, 5649, ENDEFYMD="05/19/2025"), "cert 5649 ended 2025-05-19 at the FDIC, valid_to says 2025-05-18"),
        (lambda d: _set(d, 5649, NEWCERT=628), "cert 5649 continues as 628 at the FDIC, merged_into says 4297"),
        (lambda d: _set(d, 4297, ESTYMD="05/23/1933"), "cert 4297 established 1933-05-23 at the FDIC, valid_from says 1933-05-22"),
        (lambda d: _set(d, 4297, NEWCERT=628), "cert 4297 has an end date None or successor 628 at the FDIC but no valid_to"),
        (lambda d: {**d, "data": [r for r in d["data"] if r["data"]["CERT"] != 628]}, r"certificates missing \[628\]"),
    ],
)
def test_check_crosswalk_fails_when_the_fdic_disagrees(tmp_path, issuers, fn, match):
    sub = issuers[issuers["fdic_cert"].isin(FDIC_FIXTURE_CERTS)]
    inst = fdic.parse_institutions_json(_write(tmp_path, fn(_institutions_doc())))
    with pytest.raises(ValueError, match=match):
        fdic.check_crosswalk(inst, sub)


def test_check_crosswalk_rejects_unlisted_records(tmp_path, issuers):
    sub = issuers[issuers["fdic_cert"].isin(FDIC_FIXTURE_CERTS[:4])]
    inst = fdic.parse_institutions_json(FDIC_FIXTURE_DIR / fdic.INSTITUTIONS_FILE)
    with pytest.raises(ValueError, match=r"not in issuers.csv \[34404\]"):
        fdic.check_crosswalk(inst, sub)
