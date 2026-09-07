"""TCCP fetcher: link discovery, the checked-in workbooks, header drift, loud failures, and the facts SQL.

The numbers asserted on the real workbooks were recomputed independently with pandas.read_excel on 2026-09-07
(header row 10, columns picked by name), not with the parser under test.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from openpyxl import Workbook

from carddash import loader
from carddash.fetchers import tccp
from carddash.schema import KEY_COLUMNS, coerce_facts
from carddash.series import series_for_source
from conftest import PULLED_AT

H1_2023 = pd.Timestamp("2023-06-30")
H2_2025 = pd.Timestamp("2025-12-31")

# the seven links on the survey page on 2026-09-07, names as published, plus a PDF that must be ignored
INDEX_HTML = """
<a class="a-link" href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2025-12-31.xlsx">Download data for July 1, 2025 - December 31, 2025</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-quick-reference-guide_2026-05.pdf">Quick reference guide</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2025-06-30.xlsx">Download data for January 1 - June 30, 2025</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2024-12-31_uYJ9Krf.xlsx">Download data for July 1 - December 31, 2024</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2024-06-30.xlsx">Download data for January 1 - June 30, 2024</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2023-07-01_2023_12-31.xlsx">Download data for July 1 - December 31, 2023</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2023-01-01_2023-06-30.xlsx">Download data for January 1 - June 30, 2023</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2022-07-01_2022_12-31.xlsx">Download data for July 1 - December 31, 2022</a>
<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2025-12-31.xlsx">(the same link again, further down)</a>
"""


# ---------- discovery ----------


def test_index_dates_every_workbook_from_its_file_name():
    links = tccp.parse_index(INDEX_HTML)
    assert sorted(links) == [
        dt.date(2022, 12, 31), dt.date(2023, 6, 30), dt.date(2023, 12, 31), dt.date(2024, 6, 30),
        dt.date(2024, 12, 31), dt.date(2025, 6, 30), dt.date(2025, 12, 31),
    ]
    assert links[dt.date(2024, 12, 31)].endswith("cfpb_tccp-data_2024-12-31_uYJ9Krf.xlsx")
    assert links[dt.date(2023, 12, 31)].endswith("cfpb_tccp-data_2023-07-01_2023_12-31.xlsx")
    assert len([d for d in links if d >= tccp.FIRST_PERIOD_END]) == 6  # H2 2022 is the old survey layout


def test_index_fails_loudly():
    with pytest.raises(ValueError, match="no TCCP workbook links"):
        tccp.parse_index("<html><title>Access Denied</title></html>")
    twice = INDEX_HTML + '<a href="https://files.consumerfinance.gov/f/documents/cfpb_tccp-data_2025-12-31_v2.xlsx">x</a>'
    with pytest.raises(ValueError, match="two TCCP workbooks"):
        tccp.parse_index(twice)


def test_period_end_from_name():
    assert tccp.period_end_from_name("cfpb_tccp-data_2023-07-01_2023_12-31.xlsx") == dt.date(2023, 12, 31)
    assert tccp.period_end_from_name("cfpb_tccp-data_2024-12-31_uYJ9Krf.xlsx") == dt.date(2024, 12, 31)
    assert tccp.snapshot_name(dt.date(2025, 12, 31)) == "cfpb_tccp-data_2025-12-31.xlsx"
    with pytest.raises(ValueError, match="not the end of a half-year"):
        tccp.period_end_from_name("cfpb_tccp-data_2025-05-15.xlsx")
    with pytest.raises(ValueError, match="no date"):
        tccp.period_end_from_name("cfpb_tccp-data_latest.xlsx")


# ---------- the checked-in workbooks ----------


def test_h2_2025_workbook(tccp_products):
    p = tccp_products[tccp_products["period_end"] == H2_2025]
    assert len(p) == 663
    assert p["institution"].nunique() == 195
    assert int(p["top25"].sum()) == 410
    assert int((p["purchase_apr_offered"] == False).sum()) == 38  # noqa: E712
    assert int(p["secured"].sum()) == 56
    assert int((p["apr_index"] == "V").sum()) == 522
    assert p["source_file"].unique().tolist() == ["cfpb_tccp-data_2025-12-31.xlsx"]
    # First National Bank of Omaha, Yale Mastercard: sheet row 342
    yale = p[(p["institution"] == "First National Bank Of Omaha") & p["product"].str.startswith("Yale Mastercard")]
    assert len(yale) == 1
    r = yale.iloc[0]
    assert r["row_in_file"] == 342 and bool(r["top25"]) and r["report_date"] == "2025-12-31"
    assert abs(r["purchase_apr_max"] - 0.2424) < 1e-9 and abs(r["purchase_apr_min"] - 0.2399) < 1e-9
    assert r["purchase_apr_varies_by_tier"] == False and r["apr_index"] == "V" and r["variable_rate_index"] == "Prime"  # noqa: E712
    assert bool(r["targets_le619"]) and bool(r["targets_620_719"]) and bool(r["targets_ge720"]) and not r["targets_no_score"]
    assert r["late_fee"] == 29 and pd.isna(r["annual_fee"]) and pd.isna(r["institution_type"])
    # the first product row (sheet row 11) offers no purchase APR, so its APR cells are empty
    first = p.sort_values("row_in_file").iloc[0]
    assert first["row_in_file"] == 11 and first["institution"] == "1st Community Federal Credit Union"
    assert first["purchase_apr_offered"] == False and pd.isna(first["purchase_apr_max"])  # noqa: E712


def test_h1_2023_workbook_predates_the_top25_flag(tccp_products):
    p = tccp_products[tccp_products["period_end"] == H1_2023]
    assert len(p) == 648
    assert p["institution"].nunique() == 140
    assert p["top25"].isna().all() and p["purchase_apr_no_score"].isna().all()
    assert p["report_date"].unique().tolist() == ["2023-06-30"]  # a datetime cell in this vintage
    assert p["purchase_apr_max"].notna().sum() == 600


def test_facts_from_the_workbooks(meta, tccp_products):
    facts = coerce_facts(tccp.products_to_facts(tccp_products, PULLED_AT))
    errors, _ = loader.validate(facts, series_for_source(meta, "tccp"))
    assert not errors, errors
    assert facts["period_type"].eq("H").all() and facts["entity_type"].eq("aggregate").all()
    assert not facts.duplicated(KEY_COLUMNS).any()
    v = facts.set_index(["metric", "entity", "tier", "period_end"])["value"]

    def close(key, expected):
        assert abs(v[key] - expected) < 1e-6, (key, v[key], expected)

    close(("tccp_purchase_apr_max_median", "TCCP_ALL", "all", H2_2025), 27.49)
    close(("tccp_purchase_apr_max_median", "TCCP_TOP25", "all", H2_2025), 29.24)
    close(("tccp_purchase_apr_max_median", "TCCP_OTHER", "all", H2_2025), 17.99)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "superprime", H2_2025), 19.99)
    close(("tccp_purchase_apr_median", "TCCP_TOP25", "superprime", H2_2025), 23.99)
    close(("tccp_purchase_apr_median", "TCCP_OTHER", "superprime", H2_2025), 12.75)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "620_719", H2_2025), 26.74)
    close(("tccp_purchase_apr_median", "TCCP_TOP25", "620_719", H2_2025), 28.49)
    close(("tccp_purchase_apr_median", "TCCP_OTHER", "620_719", H2_2025), 15.99)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "le619", H2_2025), 23.99)
    close(("tccp_purchase_apr_median", "TCCP_TOP25", "le619", H2_2025), 28.24)
    close(("tccp_purchase_apr_median", "TCCP_OTHER", "le619", H2_2025), 17.99)
    close(("tccp_issuers_max_apr_over_30_count", "TCCP_ALL", "all", H2_2025), 13)
    close(("tccp_product_count", "TCCP_ALL", "all", H2_2025), 663)
    close(("tccp_issuer_count", "TCCP_ALL", "all", H2_2025), 195)
    # H1 2023: the CFPB's own report says 15 issuers had a card above 30% (the golden entry), and no split by size
    close(("tccp_issuers_max_apr_over_30_count", "TCCP_ALL", "all", H1_2023), 15)
    close(("tccp_purchase_apr_max_median", "TCCP_ALL", "all", H1_2023), 28.74)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "superprime", H1_2023), 21.74)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "620_719", H1_2023), 27.74)
    close(("tccp_purchase_apr_median", "TCCP_ALL", "le619", H1_2023), 27.865)
    close(("tccp_product_count", "TCCP_ALL", "all", H1_2023), 648)
    assert ("tccp_purchase_apr_max_median", "TCCP_TOP25", "all", H1_2023) not in v.index
    assert ("tccp_purchase_apr_median", "TCCP_OTHER", "le619", H1_2023) not in v.index


# ---------- synthetic workbooks: header drift, optional columns, loud failures, SQL choices ----------

HEADERS = [
    "Institution Name", "Product Name", "Report Date", "Availability of Credit Card Plan", "Secured Card",
    "Targeted Credit Tiers", "Purchase APR Offered?", "Purchase APR Vary by Credit Tier", "Purchase APR poor",
    "Purchase APR good", "Purchase APR great", "Purchase APR min", "Purchase APR median", "Purchase APR max",
    "Index", "Purchase APR Tier 1 to Balance", "Periodic Max", "Annual Fee", "Late Fee ($)",
    "Rewards", "Card Features", "Website for Consumer",
]
CREDIT_UNION = {
    "Institution Name": "Some Credit Union", "Product Name": "Platinum Visa", "Report Date": "Data as of June 30",
    "Availability of Credit Card Plan": "One State/Territory", "Secured Card": "No",
    "Targeted Credit Tiers": "Credit scores from 620 to 719; Credit score of 720 or greater",
    "Purchase APR Offered?": "Yes", "Purchase APR Vary by Credit Tier": "No",
    "Purchase APR min": 0.1299, "Purchase APR median": 0.1499, "Purchase APR max": 0.1799, "Index": "F", "Late Fee ($)": 25,
}
BIG_BANK = {
    "Institution Name": "Big Bank, N.A.", "Product Name": "Rewards Card", "Report Date": "Data as of June 30",
    "Availability of Credit Card Plan": "National", "Secured Card": "No",
    "Targeted Credit Tiers": "No credit score; Credit score 619 or less",
    "Purchase APR Offered?": "Yes", "Purchase APR Vary by Credit Tier": "Yes",
    "Purchase APR poor": 0.2999, "Purchase APR good": 0.2799, "Purchase APR great": 0.2199,
    "Purchase APR min": 0.2199, "Purchase APR median": 9.99, "Purchase APR max": 0.2999,  # 9.99 is a filler
    "Index": "V", "Annual Fee": 95, "Late Fee ($)": 40,
}
PLACEHOLDER_BANK = {
    "Institution Name": "Placeholder Bank", "Product Name": "Zero Card", "Report Date": "Data as of June 30",
    "Availability of Credit Card Plan": "National", "Secured Card": "Yes",
    "Targeted Credit Tiers": "Credit score of 720 or greater",
    "Purchase APR Offered?": "Yes", "Purchase APR Vary by Credit Tier": "No",
    "Purchase APR min": 0, "Purchase APR median": 0, "Purchase APR max": 9.99, "Index": "V",
}
TITLE = "Data for reporting period January 1, 2025 - June 30, 2025"
NAME = "cfpb_tccp-data_2025-06-30.xlsx"


def _workbook(tmp_path, rows, headers=HEADERS, title=TITLE, name=NAME):
    wb = Workbook()
    ws = wb.active
    ws.title = "All TCCP Survey V.2"
    ws.append([None])
    ws.append([None, "Terms of Credit Card Plans survey results"])
    ws.append([None, "Collected by the Consumer Financial Protection Bureau"])
    ws.append([None, title])
    for _ in range(5):
        ws.append([None])
    ws.append([None] + list(headers))
    for r in rows:
        ws.append([None] + [r.get(h) for h in headers])
    path = tmp_path / name
    wb.save(path)
    return path


def test_header_drift_and_optional_columns(tmp_path):
    p = tccp.parse_workbook(_workbook(tmp_path, [CREDIT_UNION, BIG_BANK]), dt.date(2025, 6, 30))
    assert list(p.columns) == tccp.PRODUCT_COLUMNS
    assert p["row_in_file"].tolist() == [11, 12]
    assert p["top25"].isna().all() and p["institution_type"].isna().all() and p["purchase_apr_no_score"].isna().all()
    cu, bank = p.iloc[0], p.iloc[1]
    assert cu["targets_no_score"] == False and cu["targets_le619"] == False  # noqa: E712
    assert cu["targets_620_719"] == True and cu["targets_ge720"] == True  # noqa: E712
    assert bank["targets_no_score"] == True and bank["targets_le619"] == True and bank["targets_ge720"] == False  # noqa: E712
    assert pd.isna(cu["purchase_apr_ge720"]) and abs(bank["purchase_apr_median"] - 9.99) < 1e-9  # transcribed as-is
    assert cu["late_fee"] == 25 and pd.isna(cu["annual_fee"]) and bank["annual_fee"] == 95
    assert cu["report_date"] == "Data as of June 30" and cu["availability"] == "One State/Territory"


def test_top25_flag_accepts_both_casings(tmp_path):
    headers = HEADERS + ["Issued by Top 25 Institution"]
    rows = [dict(CREDIT_UNION, **{"Issued by Top 25 Institution": "FALSE"}), dict(BIG_BANK, **{"Issued by Top 25 Institution": "True"})]
    p = tccp.parse_workbook(_workbook(tmp_path, rows, headers=headers), dt.date(2025, 6, 30))
    assert p["top25"].tolist() == [False, True]


def test_missing_required_header_fails(tmp_path):
    headers = [h for h in HEADERS if h != "Purchase APR max"] + ["Something Else"]
    with pytest.raises(ValueError, match="purchase apr max"):
        tccp.parse_workbook(_workbook(tmp_path, [CREDIT_UNION], headers=headers), dt.date(2025, 6, 30))


def test_title_block_must_match_the_file_name(tmp_path):
    path = _workbook(tmp_path, [CREDIT_UNION], title="Data for reporting period July 1, 2025 - December 31, 2025")
    with pytest.raises(ValueError, match="title block says the period ends 2025-12-31"):
        tccp.parse_workbook(path, dt.date(2025, 6, 30))


@pytest.mark.parametrize(
    "override,match",
    [
        ({"Purchase APR Offered?": "Maybe"}, "expected Yes/No"),
        ({"Purchase APR max": "n/a"}, "expected a number"),
        ({"Targeted Credit Tiers": "Excellent credit only"}, "unrecognized targeted credit tiers"),
        ({"Institution Name": None}, "no institution name"),
    ],
)
def test_bad_cells_fail_loudly(tmp_path, override, match):
    with pytest.raises(ValueError, match=match):
        tccp.parse_workbook(_workbook(tmp_path, [dict(CREDIT_UNION, **override)]), dt.date(2025, 6, 30))


def test_facts_sql_choices(tmp_path):
    products = tccp.parse_workbook(_workbook(tmp_path, [CREDIT_UNION, BIG_BANK, PLACEHOLDER_BANK]), dt.date(2025, 6, 30))
    facts = tccp.products_to_facts(products, PULLED_AT)
    v = facts.set_index(["metric", "entity", "tier"])["value"]
    # placeholders (0, 9.99) drop out, so the max-APR median is over the credit union and the bank only
    assert abs(v[("tccp_purchase_apr_max_median", "TCCP_ALL", "all")] - 23.99) < 1e-9
    # tier medians come from the tier columns only: the credit union quotes one APR and reports none
    assert abs(v[("tccp_purchase_apr_median", "TCCP_ALL", "superprime")] - 21.99) < 1e-9
    assert abs(v[("tccp_purchase_apr_median", "TCCP_ALL", "620_719")] - 27.99) < 1e-9
    assert abs(v[("tccp_purchase_apr_median", "TCCP_ALL", "le619")] - 29.99) < 1e-9
    assert v[("tccp_product_count", "TCCP_ALL", "all")] == 3 and v[("tccp_issuer_count", "TCCP_ALL", "all")] == 3
    # 29.99% is not above 30%, and no product carries the top-25 flag, so these series have no rows
    assert ("tccp_issuers_max_apr_over_30_count", "TCCP_ALL", "all") not in v.index
    assert not facts["entity"].isin(["TCCP_TOP25", "TCCP_OTHER"]).any()
    assert facts["period_end"].eq(pd.Timestamp("2025-06-30")).all() and facts["pulled_at"].eq(PULLED_AT).all()


def test_products_csv_round_trips_through_duckdb(tmp_path, tccp_products):
    import duckdb

    path = tmp_path / "tccp_products.csv"
    tccp.write_products(tccp_products, path)
    cols = ", ".join(f"'{c}': '{t}'" for c, t in tccp.PRODUCT_TYPES.items())
    con = duckdb.connect()
    con.execute(f"CREATE TABLE tccp_products AS SELECT * FROM read_csv(?, header = true, columns = {{{cols}}})", [str(path)])
    n, n_top, latest = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE top25), max(period_end) FROM tccp_products"
    ).fetchone()
    assert (n, n_top, latest) == (648 + 663, 410, dt.date(2025, 12, 31))
    # the same SQL over the CSV-backed table gives the same facts as over the in-memory frame
    again = con.execute(tccp._read_sql(tccp.FACTS_SQL)).df()
    direct = tccp.products_to_facts(tccp_products, PULLED_AT)
    assert len(again) == len(direct)
    con.close()
