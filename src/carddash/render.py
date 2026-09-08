"""Render one self-contained docs/index.html from facts.csv, health.json, revisions.csv and golden.yaml.

No runtime fetches: the chart library is vendored inline and the data is embedded as JSON, so the page renders
identically from file:// and from GitHub Pages. The page is meant to be read on its own: a block of latest readings
(computed here, no model involved), four panels of charts with a five-year default view and the full history one
click away, NBER recession shading, a 2015-2019 benchmark on the rate charts, per-card status and data-as-of, the
golden numbers each chart was checked against, and the source health strip at the bottom.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import shutil
from pathlib import Path

import duckdb
import pandas as pd
from jinja2 import Environment, PackageLoader, select_autoescape

from .fetchers import tccp
from .golden import check_golden, load_golden
from .issuers import ISSUER_TYPES
from .loader import STATUS_RANK, read_facts, read_revisions
from .paths import Paths
from .png import write_png
from .schema import PERIOD_WORDS, SERIES_KEY, shift_period
from .series import load_series, series_index

VENDOR = Path(__file__).parent / "vendor"

SOURCE_LABELS = {
    "fred": "Federal Reserve Board, via FRED",
    "tccp": "CFPB Terms of Credit Card Plans survey",
    "phillyfed": "Federal Reserve Bank of Philadelphia, Large Bank Credit Card and Mortgage Data (FR Y-14M)",
    "nyfed_hhdc": "Federal Reserve Bank of New York, Quarterly Report on Household Debt and Credit (Consumer Credit Panel/Equifax)",
    "fdic": "FDIC, Call Report data via the BankFind Suite API",
    "nyfed_sce": "Federal Reserve Bank of New York, Survey of Consumer Expectations Credit Access Survey",
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
    "pp": "Percentage points",
    "count": "Count",
    "score": "Credit score",
    "index": "Index",
}

# NBER business cycle peak and trough months, shaded on every chart (and in the PNGs). Source: nber.org/research/data/
# us-business-cycle-expansions-and-contractions. A new recession is a one-line edit here.
RECESSIONS = [
    ("1969-12", "1970-11"),
    ("1973-11", "1975-03"),
    ("1980-01", "1980-07"),
    ("1981-07", "1982-11"),
    ("1990-07", "1991-03"),
    ("2001-03", "2001-11"),
    ("2007-12", "2009-06"),
    ("2020-02", "2020-04"),
]
BENCHMARK_WINDOW = (dt.date(2015, 1, 1), dt.date(2019, 12, 31))  # the last full expansion before the pandemic
BENCHMARK_LABEL = "2015-2019 average"
DEFAULT_YEARS = 5  # the page opens on the last five years; the button shows the full history
PERIODS_PER_YEAR = {"D": 365, "W": 52, "M": 12, "Q": 4, "T": 3, "H": 2, "A": 1}


def S(metric, entity, label, tier="all", period_type="M", source="fred", view="facts", field="value", unit=None, dash=False):
    """One series of a chart. `unit` overrides the chart unit for this series (a spread in percentage points on a
    percent chart); `dash` draws it dashed."""
    return {
        "metric": metric,
        "entity": entity,
        "tier": tier,
        "period_type": period_type,
        "source": source,
        "label": label,
        "view": view,
        "field": field,
        "unit": unit,
        "dash": dash,
    }


# The six largest card books at 2026 Q2 by the FDIC roll-up, drawn on the issuer charts. Capital One includes
# Discover Bank and Capital One Bank (USA) for their whole history (crosswalks/issuers.csv).
ISSUER_CHART = [
    ("CAPITAL_ONE", "Capital One (with Discover)"),
    ("JPMORGAN", "JPMorgan Chase"),
    ("CITI", "Citi"),
    ("AMEX", "American Express"),
    ("BOFA", "Bank of America"),
    ("SYNCHRONY", "Synchrony"),
]


def issuer_series(metric: str, view: str, field: str) -> list[dict]:
    return [
        S(metric, f"ISSUER:{issuer_id}", label, period_type="Q", source="fdic", view=view, field=field)
        for issuer_id, label in ISSUER_CHART
    ]


AGE_GROUPS = ("18-29", "30-39", "40-49", "50-59", "60-69", "70+")

FDIC_ROLLUP_NOTE = (
    "FDIC Call Report data per bank charter, rolled up to the issuer (crosswalks/issuers.csv): Capital One is the sum "
    "of Capital One, N.A., Discover Bank and Capital One Bank (USA) for their whole history, so its line does not jump "
    "at the 2025 Discover merger; JPMorgan Chase includes Chase Bank USA; American Express includes American Express "
    "Bank, FSB; Synchrony is Synchrony Bank. Loans held on the bank's own balance sheet, billions, quarter end."
)
FDIC_RATE_NOTE = (
    "Annualized net charge-off rate the way the FDIC computes its own: four times the quarter's net charge-offs "
    "divided by the average of the beginning and end-of-quarter card loans (it reproduces the FDIC's IDNTCRDQR ratio "
    "for Capital One to the rounding). In a merger quarter the acquirer reports the acquired book's charge-offs only "
    "from the merger date, so a roll-up rate dips that quarter: Capital One in 2025 Q2. All FDIC-insured institutions "
    "excludes noninsured institutions and insured US branches of foreign banks."
)
DQ_TRIANGULATION_NOTE = (
    "Four measures of the same stress that cannot agree on the level. The NY Fed measure is the share of card "
    "balances on credit reports that are 90 or more days late: charged-off balances leave lender books but stay on "
    "credit reports for years (about 80 percent are still reported a year later, per the NY Fed's August 2026 "
    "Liberty Street post 'How Distressed Are Consumers?'), so it sits far above the lender-book measures. The Fed "
    "series is 30 or more days past due at commercial banks, seasonally adjusted. The Y-14 series covers the largest "
    "banks only. The FDIC series is 90 or more days past due plus nonaccrual, all insured institutions. Read the "
    "turning points together, not the levels."
)
Y14_NOTE = (
    "FR Y-14M credit card filers only: bank holding companies with card balances above $5 billion or material to "
    "Tier 1 capital, about three quarters of US card balances, not all lenders. Quarterly, dated to quarter end, not "
    "seasonally adjusted."
)

BURDEN_NOTE = (
    "Revolving credit outstanding as a percentage of disposable personal income, which the income series reports at an "
    "annual rate; the ratio therefore reads as card balances per dollar of annual after-tax income. It is the burden "
    "measure the level alone cannot give: balances at a record in dollars can still be a smaller claim on income than "
    "they were in 2008."
)
REAL_NOTE = (
    "The same balances in nominal dollars and restated at the latest CPI price level. Consumer prices rose about 25 "
    "percent between 2020 and 2026, so a flat nominal balance is a falling real one."
)
PRIME_NOTE = (
    "Most variable card APRs are set as the prime rate plus a margin. The prime rate follows the federal funds target, "
    "so the spread over prime is the part of the price the issuer chooses and the level is mostly the part it does not. "
    "A widening spread while prime is flat is issuers repricing risk or rebuilding margin."
)
CONSUMER_NOTE = (
    "Card losses and delinquencies against the same measures for all consumer loans at commercial banks (cards, auto, "
    "personal, student held on bank books). Cards are the largest and worst-performing part of that aggregate, so the "
    "gap says whether card stress is idiosyncratic or the leading edge of a broader consumer credit cycle."
)
LABOR_NOTE = (
    "Card charge-offs against the unemployment rate. Losses are historically a job-loss phenomenon more than an "
    "interest-rate one: every past charge-off peak followed an unemployment peak within a few quarters. Charge-offs "
    "well above what the labour market implies is the signal that underwriting, not the economy, is the cause."
)
FLOWS_NOTE = (
    "Two measures of how cardholders use the product, from the Y-14 series already loaded. The revolving share is the "
    "percentage of balances carrying interest. The payment rate is payments as a percentage of the opening balance, "
    "derived from the accounting identity (closing balance equals opening plus purchases minus payments and "
    "charge-offs); charge-offs are left inside payments because the Y-14 publishes a charge-off rate rather than a "
    "dollar amount, which overstates the payment rate by a few tenths of a point. A rising payment rate with a falling "
    "revolving share means the growth is transactors, not borrowers. " + Y14_NOTE
)
PER_ACCOUNT_NOTE = (
    "Balances, limits and unused credit per open card account on credit reports. Joint accounts are counted twice in "
    "the NY Fed's account series, so these are lower bounds on the per-borrower figures. The gap between the limit and "
    "the balance line is the unused credit the household is carrying."
)
DEBT_SERVICE_NOTE = (
    "The Federal Reserve Board's own estimates of required debt payments as a share of disposable personal income: the "
    "household ratio includes mortgages, the consumer ratio covers cards, auto and student debt. Published about five "
    "months after the quarter, so this is the slowest series on the page and the one that settles whether a record "
    "balance is actually a burden."
)

SCE_NOTE = (
    "New York Fed Survey of Consumer Expectations, Credit Access Survey: about 1,000 household heads, weighted to be "
    "nationally representative, asked every four months in February, June and October about the previous twelve "
    "months. A reading is dated to the month it was fielded in. This is the only source on the page that sees the "
    "demand side: applications, refusals, and the households that needed credit and did not apply at all."
)
SCE_THIN_NOTE = (
    "Read the level, not the wave-to-wave move: the credit score bands are self-reported and the sub-680 band rests "
    "on about 150 respondents a wave, so single-wave swings of several points are noise. The observation counts are "
    "in the data file."
)
CLOSURE_NOTE = (
    "Accounts closed by the lender, not by the borrower. This is the back door of credit supply and no lender-reported "
    "source publishes it: an issuer that is approving more new applicants while closing more existing weak accounts is "
    "managing the book, not loosening. " + SCE_THIN_NOTE
)
DISCOURAGED_NOTE = (
    "Households that needed credit in the past twelve months but did not apply because they expected to be turned "
    "down. They never appear in any approval or rejection statistic, so this is the part of tightening that lender "
    "data cannot see. " + SCE_THIN_NOTE
)

# Chart specs. Adding a chart means adding an entry here; the data comes from facts or a view in sql/views.sql.
# Keys: `post` writes docs/img/<id>.png (one per panel); `band` fills between two series (1-based data indices);
# `benchmark` adds a dashed 2015-2019 average of the first series; `y_zero: False` lets the y axis float (scores);
# `since` cuts the history; `notes` adds chart-level notes to the series' scope notes.
PANELS = [
    {
        "name": "Growth",
        "blurb": "How much card credit is outstanding, how fast it is growing, and who holds it.",
        "charts": [
            {
                "id": "revolving_level",
                "title": "Revolving consumer credit, all holders",
                "unit": "usd_bn",
                "step": False,
                "post": True,
                "series": [S("revolving_credit_sa", "ALL_HOLDERS", "Revolving credit, SA")],
            },
            {
                "id": "revolving_yoy",
                "title": "Revolving consumer credit, year-over-year change",
                "unit": "pct",
                "step": False,
                "since": "1980-01-01",  # the 1968-1975 base is tiny and its triple-digit growth hides everything after
                "benchmark": True,
                "series": [S("revolving_credit_sa", "ALL_HOLDERS", "YoY change", view="v_growth", field="yoy_pct")],
            },
            {
                "id": "bank_card_loans_weekly",
                "title": "Credit card loans at commercial banks, weekly",
                "unit": "usd_bn",
                "step": False,
                "series": [S("bank_card_loans_sa", "COMBANKS_ALL", "Card loans, SA", period_type="W")],
            },
            {
                "id": "card_debt_burden",
                "title": "Card debt as a share of disposable income",
                "unit": "pct",
                "step": False,
                "benchmark": True,
                "notes": [BURDEN_NOTE],
                "series": [
                    S("revolving_credit_sa", "ALL_HOLDERS", "Revolving credit per dollar of annual after-tax income",
                      view="v_card_burden", field="pct_of_disposable_income"),
                ],
            },
            {
                "id": "revolving_real",
                "title": "Revolving credit, nominal and in today's dollars",
                "unit": "usd_bn",
                "step": False,
                "notes": [REAL_NOTE],
                "series": [
                    S("revolving_credit_sa", "ALL_HOLDERS", "Nominal", view="v_card_burden", field="revolving"),
                    S("revolving_credit_sa", "ALL_HOLDERS", "At the latest price level", view="v_card_burden", field="revolving_real"),
                ],
            },
            {
                "id": "card_loans_by_issuer",
                "title": "Card loans on the books of the largest issuers",
                "unit": "usd_bn",
                "step": True,
                "notes": [FDIC_ROLLUP_NOTE],
                "series": issuer_series("fdic_card_loans", "v_fdic_issuer", "value"),
            },
        ],
    },
    {
        "name": "Pricing",
        "blurb": "What card borrowers are charged, and what issuers advertise.",
        "charts": [
            {
                "id": "card_apr",
                "title": "Commercial bank credit card APR",
                "unit": "pct",
                "step": True,
                "post": True,
                "series": [
                    S("card_apr_all_accounts", "COMBANKS_ALL", "All accounts", period_type="Q"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Accounts assessed interest", period_type="Q"),
                    S("card_apr_advertised", "BANKRATE_SURVEY", "Advertised on new offers (Bankrate, weekly)", period_type="W"),
                ],
            },
            {
                "id": "apr_over_prime",
                "title": "Card APR against the prime rate",
                "unit": "pct",
                "step": True,
                "notes": [PRIME_NOTE],
                "series": [
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "APR paid by revolvers", period_type="Q",
                      view="v_apr_spread", field="apr"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Prime rate", period_type="Q",
                      view="v_apr_spread", field="prime"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Spread over prime, percentage points", period_type="Q",
                      view="v_apr_spread", field="spread_over_prime", unit="pp", dash=True),
                ],
            },
            {
                # the cross-source chart: the range of terms issuers advertise (TCCP, the highest and lowest of the
                # three credit-tier medians as a band, the 620-719 median as a line) against the rate revolvers pay
                # (G.19). The spread is the 620-719 median minus paid, in percentage points, and stops at the last
                # offered period (sql/views.sql v_offered_vs_paid).
                "id": "offered_vs_paid",
                "title": "Card APR offered vs APR paid",
                "unit": "pct",
                "step": True,
                "since": "2022-01-01",
                "band": [1, 2],  # fill between the highest and lowest tier medians
                "notes": [
                    "The band is the range across the three credit-tier medians of the CFPB survey (scores 619 or "
                    "less, 620 to 719, 720 and up). The sub-620 median is usually the lowest of the three, not the "
                    "highest: products that target that tier are mostly secured cards with lower APRs. The G.19 rate "
                    "is the average across all accounts that paid interest, so it sits below the terms offered to new "
                    "applicants in the middle tier."
                ],
                "series": [
                    S("tccp_purchase_apr_median", "TCCP_ALL", "Highest of the three tier medians offered (TCCP)",
                      tier="620_719", period_type="H", source="tccp", view="v_tccp_offered_range", field="hi"),
                    S("tccp_purchase_apr_median", "TCCP_ALL", "Lowest of the three tier medians offered (TCCP)",
                      tier="620_719", period_type="H", source="tccp", view="v_tccp_offered_range", field="lo"),
                    S("tccp_purchase_apr_median", "TCCP_ALL", "Offered to scores 620 to 719, median (TCCP)",
                      tier="620_719", period_type="H", source="tccp"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Paid: APR on accounts assessed interest (G.19)",
                      period_type="Q"),
                    S("card_apr_assessed_interest", "COMBANKS_ALL", "Offered to 620-719 minus paid, percentage points",
                      period_type="Q", view="v_offered_vs_paid", field="spread_pct_pts", unit="pp", dash=True),
                ],
            },
        ],
    },
    {
        "name": "Access",
        "blurb": "Who is asking for card credit, who is getting it, and who is being shut out. The demand side, from the only survey that asks households directly.",
        "charts": [
            {
                "id": "card_access",
                "title": "Card applications and refusals",
                "unit": "pct",
                "step": True,
                "post": True,
                "notes": [SCE_NOTE],
                "series": [
                    S("sce_card_application_rate", "SCE_ALL", "Applied for a card", period_type="T", source="nyfed_sce"),
                    S("sce_card_rejection_rate", "SCE_ALL", "Refused a card, of those who applied", period_type="T", source="nyfed_sce"),
                    S("sce_card_limit_request_rate", "SCE_ALL", "Asked for a higher limit", period_type="T", source="nyfed_sce"),
                    S("sce_card_limit_rejection_rate", "SCE_ALL", "Refused a higher limit, of those who asked", period_type="T", source="nyfed_sce"),
                ],
            },
            {
                "id": "rejection_by_score",
                "title": "Rejection rate for any credit, by credit score",
                "unit": "pct",
                "step": True,
                "notes": [SCE_NOTE, SCE_THIN_NOTE],
                "series": [
                    S("sce_any_rejection_rate", "SCORE:LT680", "Score under 680", period_type="T", source="nyfed_sce"),
                    S("sce_any_rejection_rate", "SCORE:680-760", "Score 680 to 760", period_type="T", source="nyfed_sce"),
                    S("sce_any_rejection_rate", "SCORE:GE760", "Score over 760", period_type="T", source="nyfed_sce"),
                ],
            },
            {
                "id": "origination_mix",
                "title": "Who is getting new card accounts",
                "unit": "pct",
                "step": True,
                "notes": [
                    "New accounts opened in the quarter with a credit score below 660, as a share of all new accounts, "
                    "and the share of new credit line dollars going to them. The two lines separating is the point: a "
                    "rising account share against a flat dollar share means lenders are opening more subprime accounts "
                    "on small lines, which is a test, not an expansion. " + Y14_NOTE
                ],
                "series": [
                    S("y14_card_new_accounts_share", "Y14_CARD_FILERS", "Share of new accounts, score below 660",
                      tier="lt660", period_type="Q", source="phillyfed"),
                    S("y14_card_new_commitments_share", "Y14_CARD_FILERS", "Share of new credit lines, score below 660",
                      tier="lt660", period_type="Q", source="phillyfed"),
                ],
            },
            {
                "id": "origination_limits",
                "title": "Credit line on a new account, by credit score",
                "unit": "usd",
                "step": True,
                "notes": [
                    "Median credit limit at origination for accounts opened in the quarter, by the borrower's score "
                    "band. The size of the line is the risk the lender is actually taking, as distinct from the number "
                    "of accounts it opens. " + Y14_NOTE
                ],
                "series": [
                    S("y14_card_orig_credit_limit_median", "Y14_CARD_FILERS", "Score below 660", tier="lt660",
                      period_type="Q", source="phillyfed"),
                    S("y14_card_orig_credit_limit_median", "Y14_CARD_FILERS", "Score 660 to 719", tier="660_719",
                      period_type="Q", source="phillyfed"),
                    S("y14_card_orig_credit_limit_median", "Y14_CARD_FILERS", "Score 720 and up", tier="superprime",
                      period_type="Q", source="phillyfed"),
                ],
            },
            {
                "id": "lender_closures",
                "title": "Accounts closed by the lender, by credit score",
                "unit": "pct",
                "step": True,
                "notes": [CLOSURE_NOTE],
                "series": [
                    S("sce_lender_closed_rate", "SCORE:LT680", "Score under 680", period_type="T", source="nyfed_sce"),
                    S("sce_lender_closed_rate", "SCORE:680-760", "Score 680 to 760", period_type="T", source="nyfed_sce"),
                    S("sce_lender_closed_rate", "SCORE:GE760", "Score over 760", period_type="T", source="nyfed_sce"),
                ],
            },
            {
                "id": "discouraged",
                "title": "Needed credit but did not apply, by credit score",
                "unit": "pct",
                "step": True,
                "notes": [DISCOURAGED_NOTE],
                "series": [
                    S("sce_discouraged_rate", "SCORE:LT680", "Score under 680", period_type="T", source="nyfed_sce"),
                    S("sce_discouraged_rate", "SCORE:680-760", "Score 680 to 760", period_type="T", source="nyfed_sce"),
                    S("sce_discouraged_rate", "SCORE:GE760", "Score over 760", period_type="T", source="nyfed_sce"),
                ],
            },
        ],
    },
    {
        "name": "Performance",
        "blurb": "How card credit on lenders' books is performing, and whether banks are tightening.",
        "charts": [
            {
                "id": "card_nco",
                "title": "Credit card charge-off rate, annualized",
                "unit": "pct",
                "step": True,
                "post": True,
                "benchmark": True,
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
                "benchmark": True,
                "series": [
                    S("card_dq_rate_sa", "COMBANKS_ALL", "All commercial banks", period_type="Q"),
                    S("card_dq_rate_sa", "COMBANKS_TOP100", "Top 100 banks", period_type="Q"),
                    S("card_dq_rate_sa", "COMBANKS_OTHER", "Banks outside top 100", period_type="Q"),
                ],
            },
            {
                "id": "card_vs_consumer",
                "title": "Card losses against all consumer credit",
                "unit": "pct",
                "step": True,
                "notes": [CONSUMER_NOTE],
                "series": [
                    S("card_nco_rate_sa", "COMBANKS_ALL", "Card charge-off rate", period_type="Q"),
                    S("consumer_nco_rate_sa", "COMBANKS_ALL", "All consumer loan charge-off rate", period_type="Q"),
                    S("card_dq_rate_sa", "COMBANKS_ALL", "Card delinquency, 30+ days", period_type="Q"),
                    S("consumer_dq_rate_sa", "COMBANKS_ALL", "All consumer loan delinquency, 30+ days", period_type="Q"),
                ],
            },
            {
                "id": "losses_vs_labor",
                "title": "Card charge-offs against unemployment",
                "unit": "pct",
                "step": True,
                "notes": [LABOR_NOTE],
                "series": [
                    S("card_nco_rate_sa", "COMBANKS_ALL", "Card charge-off rate, annualized", period_type="Q"),
                    S("unemployment_rate_sa", "US_ECONOMY", "Unemployment rate", period_type="M"),
                ],
            },
            {
                "id": "dq_triangulation",
                "title": "Card delinquency, four measures",
                "unit": "pct",
                "step": True,
                "since": "2003-01-01",
                "notes": [DQ_TRIANGULATION_NOTE],
                "series": [
                    S("hhdc_card_dq90_rate_balances", "CCP_ALL", "90+ days late, share of balances on credit reports, all lenders (NY Fed)",
                      period_type="Q", source="nyfed_hhdc"),
                    S("card_dq_rate_sa", "COMBANKS_ALL", "30+ days past due, commercial banks, SA (Fed)", period_type="Q"),
                    S("y14_card_dq90_rate_balances", "Y14_CARD_FILERS", "90+ days past due, share of balances, large banks (Y-14)",
                      period_type="Q", source="phillyfed"),
                    S("fdic_card_nco_q", "FDIC_ALL_INSURED", "Noncurrent (90+ or nonaccrual), share of card loans, all FDIC-insured banks",
                      period_type="Q", source="fdic", view="v_fdic_rates", field="noncurrent_share"),  # v_fdic_rates is keyed on the nco_q series
                ],
            },
            {
                "id": "nco_by_issuer",
                "title": "Card net charge-off rate by issuer, annualized",
                "unit": "pct",
                "step": True,
                "since": "2000-01-01",
                "notes": [FDIC_RATE_NOTE, FDIC_ROLLUP_NOTE],
                "series": issuer_series("fdic_card_nco_q", "v_fdic_rates", "nco_rate_annualized")
                + [
                    S("fdic_card_nco_q", "FDIC_ALL_INSURED", "All FDIC-insured institutions", period_type="Q", source="fdic",
                      view="v_fdic_rates", field="nco_rate_annualized", dash=True),
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
    {
        "name": "Borrowers",
        "blurb": "How households are using and servicing card credit, and who is getting new cards.",
        "charts": [
            {
                "id": "hhdc_dq90_by_age",
                "title": "Flow into serious card delinquency by age of borrower",
                "unit": "pct",
                "step": True,
                "post": True,
                "notes": [
                    "Share of card balances that became 90 or more days late over the previous four quarters, out of "
                    "balances that were current or less than 90 days late, annualized (NY Fed Consumer Credit "
                    "Panel/Equifax, all lenders, by the borrower's age)."
                ],
                "series": [
                    S("hhdc_card_transition_dq90", f"AGE:{g}", f"Ages {g}", period_type="Q", source="nyfed_hhdc")
                    for g in AGE_GROUPS
                ],
            },
            {
                "id": "y14_payment_behavior",
                "title": "How cardholders pay: minimum, partial, in full",
                "unit": "pct",
                "step": True,
                "benchmark": True,
                "notes": [Y14_NOTE],
                "series": [
                    S("y14_card_pay_minimum_share", "Y14_CARD_FILERS", "Paying only the minimum", period_type="Q", source="phillyfed"),
                    S("y14_card_pay_partial_share", "Y14_CARD_FILERS", "Paying more than the minimum, less than full", period_type="Q", source="phillyfed"),
                    S("y14_card_pay_full_share", "Y14_CARD_FILERS", "Paying in full", period_type="Q", source="phillyfed"),
                ],
            },
            {
                "id": "y14_payment_flows",
                "title": "Payment rate and the revolving share of balances",
                "unit": "pct",
                "step": True,
                "notes": [FLOWS_NOTE],
                "series": [
                    S("y14_card_balances", "Y14_CARD_FILERS", "Payments as a share of the opening balance", period_type="Q",
                      source="phillyfed", view="v_y14_flows", field="payment_rate"),
                    S("y14_card_balances", "Y14_CARD_FILERS", "Balances carrying interest, share of total", period_type="Q",
                      source="phillyfed", view="v_y14_flows", field="revolver_share"),
                ],
            },
            {
                "id": "per_account_credit",
                "title": "Balances, limits and unused credit per card account",
                "unit": "usd",
                "step": True,
                "notes": [PER_ACCOUNT_NOTE],
                "series": [
                    S("hhdc_card_balances", "CCP_ALL", "Credit limit per account", period_type="Q", source="nyfed_hhdc",
                      view="v_hhdc_per_account", field="limit_per_account"),
                    S("hhdc_card_balances", "CCP_ALL", "Unused credit per account", period_type="Q", source="nyfed_hhdc",
                      view="v_hhdc_per_account", field="available_per_account"),
                    S("hhdc_card_balances", "CCP_ALL", "Balance per account", period_type="Q", source="nyfed_hhdc",
                      view="v_hhdc_per_account", field="balance_per_account"),
                ],
            },
            {
                "id": "y14_utilization",
                "title": "Card utilization, large banks",
                "unit": "pct",
                "step": True,
                "notes": [Y14_NOTE],
                "series": [
                    S("y14_card_utilization_rate", "Y14_CARD_FILERS", "Balances as a share of credit limits, all accounts", period_type="Q", source="phillyfed"),
                    S("y14_card_utilization_active_p50", "Y14_CARD_FILERS", "Median utilization, active accounts", period_type="Q", source="phillyfed"),
                ],
            },
            {
                "id": "y14_origination_scores",
                "title": "Credit scores of new card accounts",
                "unit": "score",
                "step": True,
                "y_zero": False,
                "notes": [
                    "Credit score at origination of accounts opened in the quarter: the 10th, 25th and 50th percentiles. "
                    "A falling 10th percentile means the largest banks are opening more accounts for lower-score "
                    "applicants. " + Y14_NOTE
                ],
                "series": [
                    S("y14_card_orig_credit_score_p10", "Y14_CARD_FILERS", "10th percentile", period_type="Q", source="phillyfed"),
                    S("y14_card_orig_credit_score_p25", "Y14_CARD_FILERS", "25th percentile", period_type="Q", source="phillyfed"),
                    S("y14_card_orig_credit_score_p50", "Y14_CARD_FILERS", "Median", period_type="Q", source="phillyfed"),
                ],
            },
        ],
    },
    {
        "name": "Context",
        "blurb": "What the card numbers sit inside: the cost of household debt, the buffer behind it, and how people say they feel.",
        "charts": [
            {
                "id": "debt_service",
                "title": "Debt payments as a share of disposable income",
                "unit": "pct",
                "step": False,
                "post": True,
                "notes": [DEBT_SERVICE_NOTE],
                "series": [
                    S("debt_service_ratio_household", "US_HOUSEHOLDS", "All household debt, including mortgages", period_type="Q"),
                    S("debt_service_ratio_consumer", "US_HOUSEHOLDS", "Consumer debt only (cards, auto, student)", period_type="Q"),
                ],
            },
            {
                "id": "household_buffer",
                "title": "Personal saving rate",
                "unit": "pct",
                "step": False,
                "benchmark": True,
                "series": [S("saving_rate", "US_HOUSEHOLDS", "Saving as a share of disposable income", period_type="M")],
            },
            {
                "id": "sentiment",
                "title": "Consumer sentiment",
                "unit": "index",
                "step": False,
                "y_zero": False,
                "benchmark": True,
                "notes": [
                    "University of Michigan index of consumer sentiment, 1966 Q1 = 100. On the page because stated "
                    "sentiment and card behaviour have diverged since 2022: sentiment near historic lows while payment "
                    "rates and full-payer shares set records. One of the two is not describing the median cardholder."
                ],
                "series": [S("consumer_sentiment", "US_ECONOMY", "Index of consumer sentiment", period_type="M")],
            },
            {
                "id": "consumer_credit_mix",
                "title": "Revolving and nonrevolving consumer credit",
                "unit": "usd_bn",
                "step": False,
                "notes": [
                    "The two halves of the Fed's consumer credit measure. Nonrevolving is mostly auto and student "
                    "loans. Revolving growing faster than nonrevolving is a different economy from the reverse."
                ],
                "series": [
                    S("nonrevolving_credit_sa", "ALL_HOLDERS", "Nonrevolving (auto, student)"),
                    S("revolving_credit_sa", "ALL_HOLDERS", "Revolving (mostly cards)"),
                ],
            },
        ],
    },
]


# The thesis watch. design/thesis-2026-09-08.html argues that the 2022-24 loss surge was a vintage event rather than
# a credit cycle, and that the repricing that came with it is structural. It names the readings that would prove that
# wrong. They are evaluated here on every refresh so the claim cannot quietly rot: each test carries the series it
# reads, the direction that keeps the thesis alive, and the threshold. Changing a threshold means rewriting the note.
THESIS_NOTE = "design/thesis-2026-09-08.html"
THESIS_DATE = "2026-09-08"
THESIS_CLAIM = "US card credit is re-segmenting, not cycling: the 2022-24 loss surge was a vintage event, and the repricing that came with it is structural."
THESIS_TESTS = [
    {
        "label": "Flow into 90+ day delinquency stays below 7.5%",
        "series": S("hhdc_card_transition_dq90", "CCP_ALL", "", period_type="Q", source="nyfed_hhdc"),
        "op": "<", "threshold": 7.5, "unit": "pct",
        "why": "A break above this would mean the loosening that began in late 2025 was larger than the small line sizes suggest, and that this is a cycle after all.",
    },
    {
        "label": "Card charge-off rate stays below 4.2%",
        "series": S("card_nco_rate_sa", "COMBANKS_ALL", "", period_type="Q"),
        "op": "<", "threshold": 4.2, "unit": "pct",
        "why": "Losses reaccelerating while unemployment is near 4% would break the argument that the surge was a vintage event that has washed out.",
    },
    {
        "label": "Card APR margin over prime stays above 14 points",
        "series": S("card_apr_assessed_interest", "COMBANKS_ALL", "", period_type="Q", view="v_apr_spread", field="spread_over_prime"),
        "op": ">", "threshold": 14.0, "unit": "pp",
        "why": "The margin falling back toward its 2015-19 level of 10.7 points would mean the repricing was cyclical, not structural.",
    },
]

# The latest-readings block: (series key, label, kind). kind: level (dollar amount, change in percent on the year),
# rate (percent, change in points, compared with the 2015-2019 average), net (a net balance in percent, no benchmark).
HEADLINES = [
    (("revolving_credit_sa", "ALL_HOLDERS", "all", "M", "fred"), "Revolving consumer credit, all lenders (Fed G.19, SA)", "level"),
    (("hhdc_card_balances", "CCP_ALL", "all", "Q", "nyfed_hhdc"), "Card balances on credit reports (NY Fed)", "level"),
    (("card_apr_assessed_interest", "COMBANKS_ALL", "all", "Q", "fred"), "APR paid by revolvers at commercial banks (Fed G.19)", "rate"),
    (("card_dq_rate_sa", "COMBANKS_ALL", "all", "Q", "fred"), "Card delinquency, 30+ days past due, commercial banks (Fed, SA)", "rate"),
    (("card_nco_rate_sa", "COMBANKS_ALL", "all", "Q", "fred"), "Card charge-off rate, annualized, commercial banks (Fed, SA)", "rate"),
    (("hhdc_card_transition_dq90", "CCP_ALL", "all", "Q", "nyfed_hhdc"), "Card balances newly 90+ days late, annualized flow (NY Fed)", "rate"),
    (("y14_card_pay_minimum_share", "Y14_CARD_FILERS", "all", "Q", "phillyfed"), "Accounts paying only the minimum, large banks (Philly Fed)", "rate"),
    (("sce_card_rejection_rate", "SCE_ALL", "all", "T", "nyfed_sce"), "Card applications refused, of those who applied (NY Fed survey)", "rate"),
    (("sce_lender_closed_rate", "SCE_ALL", "all", "T", "nyfed_sce"), "Had an account closed by a lender (NY Fed survey)", "rate"),
    (("debt_service_ratio_consumer", "US_HOUSEHOLDS", "all", "Q", "fred"), "Consumer debt payments as a share of disposable income (Fed)", "rate"),
    (("unemployment_rate_sa", "US_ECONOMY", "all", "M", "fred"), "Unemployment rate (BLS)", "rate"),
    (("sloos_card_standards_net_tightening", "SLOOS_DOMESTIC", "all", "Q", "fred"), "Banks tightening card standards, net (SLOOS)", "net"),
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


def _key(s: dict) -> tuple:
    return (s["metric"], s["entity"], s["tier"], s["period_type"], s["source"])


def _series_rows(con, s: dict, since: str | None = None) -> list[tuple]:
    sql = (
        f"SELECT CAST(period_end AS DATE) AS d, {s['field']} AS v FROM {s['view']} "
        "WHERE metric = ? AND entity = ? AND tier = ? AND period_type = ? AND source = ? "
        f"AND {s['field']} IS NOT NULL"
    )
    params = list(_key(s))
    if since:
        sql += " AND period_end >= ?"
        params.append(since)
    return con.execute(sql + " ORDER BY d", params).fetchall()


def _series_pulled_at(con, s: dict) -> str | None:
    """When the newest value of the series behind `s` was loaded (unchanged values keep their first pulled_at)."""
    row = con.execute(
        "SELECT max(pulled_at) FROM facts WHERE metric = ? AND entity = ? AND tier = ? AND period_type = ? AND source = ?",
        list(_key(s)),
    ).fetchone()
    if row and row[0]:
        return row[0]
    # a roll-up series (ISSUER:<id>) has no facts row of its own: the newest load of its source stands in
    row = con.execute("SELECT max(pulled_at) FROM facts WHERE source = ?", [s["source"]]).fetchone()
    return row[0] if row and row[0] else None


def _epoch(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


def _recessions() -> list[list[int]]:
    out = []
    for start, end in RECESSIONS:
        y0, m0 = (int(x) for x in start.split("-"))
        y1, m1 = (int(x) for x in end.split("-"))
        out.append([_epoch(dt.date(y0, m0, 1)), _epoch(shift_period(dt.date(y1, m1, 1), "M", 0))])
    return out


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def period_label(d: dt.date, period_type: str) -> str:
    """Human label for a period_end: 'Jun 2026', '2026 Q2', '2026 H1', '2026', or the ISO date."""
    if period_type == "M":
        return f"{MONTHS[d.month - 1]} {d.year}"
    if period_type == "Q":
        return f"{d.year} Q{(d.month - 1) // 3 + 1}"
    if period_type == "H":
        return f"{d.year} H{1 if d.month <= 6 else 2}"
    if period_type == "T":  # a survey wave, labelled by the month it was fielded in
        return f"{MONTHS[d.month - 1]} {d.year}"
    if period_type == "A":
        return str(d.year)
    return d.isoformat()


def _golden_index(facts: pd.DataFrame, golden_path: Path) -> dict[tuple, list[dict]]:
    """series key -> the golden checks on it, with the result against these facts, newest period first."""
    entries = load_golden(golden_path)
    results = {r["id"]: r for r in check_golden(facts, entries, live_only=False)} if entries else {}
    out: dict[tuple, list[dict]] = {}
    for e in entries:
        key = (e["metric"], e["entity"], e.get("tier", "all"), e["period_type"], e["source"])
        r = results.get(e["id"], {})
        pe = pd.Timestamp(str(e["period_end"])).date()
        out.setdefault(key, []).append(
            {
                "id": e["id"],
                "period": period_label(pe, e["period_type"]),
                "period_end": pe.isoformat(),
                "expected": float(e["expected"]),
                "actual": r.get("actual"),
                "ok": bool(r.get("ok")),
                "origin": e.get("origin", ""),
                "origin_url": e.get("origin_url", ""),
            }
        )
    for lst in out.values():
        lst.sort(key=lambda c: c["period_end"], reverse=True)
    return out


def _benchmark(xs: list[dt.date], ys: list[float | None]) -> float | None:
    vals = [v for d, v in zip(xs, ys) if v is not None and BENCHMARK_WINDOW[0] <= d <= BENCHMARK_WINDOW[1]]
    return sum(vals) / len(vals) if vals else None


def _chart_payload(con, spec: dict, meta_idx: dict, health: dict, golden: dict, today: dt.date) -> dict:
    per_series = []
    all_rows = []
    checks = []
    since = spec.get("since")
    for s in spec["series"]:
        rows = _series_rows(con, s, since)
        all_rows.append(rows)
        ended = [r for r in rows if r[0] <= today]  # a period that has not ended is never called the latest
        m = meta_idx.get(_key(s))
        src_health = health.get(s["source"], {})
        per_series.append(
            {
                "label": s["label"],
                "unit": s.get("unit") or spec["unit"],
                "dash": bool(s.get("dash")),
                "benchmark": False,
                "source": s["source"],
                "period_type": s["period_type"],
                "cadence": PERIOD_WORDS.get(s["period_type"], s["period_type"]),
                "last_period": period_label(ended[-1][0], s["period_type"]) if ended else None,
                "last_period_iso": ended[-1][0].isoformat() if ended else None,
                "source_label": SOURCE_LABELS.get(s["source"], s["source"]),
                "source_url": (m["source_url"] if m is not None else ""),
                "scope_note": (m["scope_note"] if m is not None else ""),
                "data_as_of": _series_pulled_at(con, s) or src_health.get("pulled_at"),
                "status": src_health.get("status", "failed") if src_health else "failed",
            }
        )
        if s["view"] == "facts":
            for c in golden.get(_key(s), []):
                if c["id"] not in {x["id"] for x in checks}:
                    checks.append({**c, "series_label": s["label"]})
    xs = sorted({d for rows in all_rows for d, _ in rows})
    idx = {d: i for i, d in enumerate(xs)}
    data: list[list] = [[_epoch(d) for d in xs]]
    for rows in all_rows:
        ys: list[float | None] = [None] * len(xs)
        for d, v in rows:
            ys[idx[d]] = float(v)
        data.append(ys)
    if spec.get("benchmark") and data[1:]:
        mean = _benchmark(xs, data[1])
        if mean is not None:
            first = per_series[0]
            per_series.append(
                {
                    **first,
                    "label": f"{first['label']}, {BENCHMARK_LABEL}",
                    "dash": True,
                    "benchmark": True,
                    "scope_note": "",
                    "source_url": "",
                }
            )
            data.append([mean] * len(xs))

    real = [p for p in per_series if not p["benchmark"]]
    sources = sorted({p["source_label"] for p in real})
    cadences = sorted({p["cadence"] for p in real})
    with_data = [p for p in real if p["last_period_iso"]]
    last = max(with_data, key=lambda p: p["last_period_iso"])["last_period"] if with_data else None
    data_as_of = max((p["data_as_of"] for p in real if p["data_as_of"]), default=None)
    status = max((p["status"] for p in real), key=lambda st: STATUS_RANK.get(st, 2), default="ok")
    attempted = max((health.get(p["source"], {}).get("pulled_at") or "" for p in real), default="") or None
    caption = f"Source: {', '.join(sources)} · {', '.join(cadences)}"
    if last:
        caption += f" · latest period {last}"
    footer = caption
    if data_as_of:
        footer += f" · data as of {data_as_of[:10]}"
    if since:
        caption += f" · shown from {since[:4]}"
        footer += f" · shown from {since[:4]}"
    mixed = len(sources) > 1 or len(cadences) > 1
    footer_lines = (
        [f"{p['label']}: {p['source_label']}, {p['cadence']}, latest period {p['last_period'] or 'none'}" for p in real]
        if mixed
        else []
    )
    notes = list(spec.get("notes", []))
    about = []
    seen = set()
    for p in real:
        if p["scope_note"] and p["scope_note"] not in seen:
            seen.add(p["scope_note"])
            about.append({"label": p["label"], "source_label": p["source_label"], "source_url": p["source_url"], "note": p["scope_note"]})
    period_types = {p["period_type"] for p in real}
    return {
        "id": spec["id"],
        "title": spec["title"],
        "unit": spec["unit"],
        "unit_label": UNIT_LABELS.get(spec["unit"], spec["unit"]),
        "step": spec["step"],
        "post": bool(spec.get("post")),
        "band": spec.get("band"),
        "y_zero": bool(spec.get("y_zero", True)),
        "period_type": period_types.pop() if len(period_types) == 1 else None,
        "series": per_series,
        "data": data,
        "footer": footer,
        "footer_lines": footer_lines,
        "caption": caption,  # the footer without the pull date: what the PNG prints
        "notes": notes,
        "about": about,
        "checks": checks,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "attempted": attempted[:10] if attempted and status != "ok" else None,
        "n_points": len(xs),
    }


# ---------- the latest-readings block ----------


def thesis_status(con, today: dt.date) -> list[dict]:
    """Evaluate each falsification test against the newest ended period. No judgement here: a test either holds or
    it does not, and a test whose series has no data says so rather than passing by default."""
    out = []
    for test in THESIS_TESTS:
        rows = [r for r in _series_rows(con, test["series"]) if r[0] <= today]
        if not rows:
            out.append({**{k: test[k] for k in ("label", "op", "threshold", "unit", "why")},
                        "value": None, "period": None, "holds": None, "text": "no data"})
            continue
        period_end, value = rows[-1]
        value = float(value)
        holds = value < test["threshold"] if test["op"] == "<" else value > test["threshold"]
        suffix = " pp" if test["unit"] == "pp" else "%"
        out.append(
            {
                **{k: test[k] for k in ("label", "op", "threshold", "unit", "why")},
                "value": value,
                "period": period_label(period_end, test["series"]["period_type"]),
                "holds": holds,
                "text": f"{value:.2f}{suffix} in {period_label(period_end, test['series']['period_type'])}",
            }
        )
    return out


def _fmt_value(v: float, kind: str) -> str:
    if kind == "level":
        return f"${v:,.0f}bn"
    return f"{v:.2f}%"


def _headline(facts: pd.DataFrame, key: tuple, label: str, kind: str, today: dt.date) -> dict | None:
    metric, entity, tier, pt, source = key
    s = facts[
        (facts["metric"] == metric) & (facts["entity"] == entity) & (facts["tier"] == tier)
        & (facts["period_type"] == pt) & (facts["source"] == source) & (facts["period_end"].dt.date <= today)
    ].sort_values("period_end")
    if s.empty:
        return None
    values = dict(zip(s["period_end"].dt.date, s["value"].astype(float)))
    latest_d = s["period_end"].iloc[-1].date()
    latest = values[latest_d]
    year_ago_d = shift_period(latest_d, pt, -PERIODS_PER_YEAR[pt])
    year_ago = values.get(year_ago_d)
    change = None
    if year_ago is not None:
        if kind == "level":
            change = f"{100 * (latest / year_ago - 1):+.1f}% on the year" if year_ago else None
        else:
            change = f"{latest - year_ago:+.2f} pp on the year"
    bench = None
    if kind == "rate":
        window = [v for d, v in values.items() if BENCHMARK_WINDOW[0] <= d <= BENCHMARK_WINDOW[1]]
        if window:
            bench = f"{BENCHMARK_LABEL} {sum(window) / len(window):.2f}%"
    # highest or lowest since: the most recent earlier period at or beyond the latest value
    earlier = [(d, v) for d, v in values.items() if d < latest_d]
    flag = None
    if earlier:
        hi = [d for d, v in earlier if v >= latest]
        lo = [d for d, v in earlier if v <= latest]
        first_d = min(values)
        if not hi:
            flag = f"highest on record (since {period_label(first_d, pt)})"
        elif not lo:
            flag = f"lowest on record (since {period_label(first_d, pt)})"
        else:
            since_hi, since_lo = max(hi), max(lo)
            years_hi = (latest_d - since_hi).days / 365.25
            years_lo = (latest_d - since_lo).days / 365.25
            if years_hi >= 3 and years_hi >= years_lo:
                flag = f"highest since {period_label(since_hi, pt)}"
            elif years_lo >= 3:
                flag = f"lowest since {period_label(since_lo, pt)}"
    parts = [p for p in (change, bench, flag) if p]
    text = f"{_fmt_value(latest, kind)} in {period_label(latest_d, pt)}" + (", " + ", ".join(parts) if parts else "")
    return {"label": label, "text": text, "source": source, "source_label": SOURCE_LABELS.get(source, source)}


def headlines(facts: pd.DataFrame, today: dt.date) -> list[dict]:
    """The latest-readings block, computed from facts: no model, no adjectives, every number traceable to a series."""
    out = []
    for key, label, kind in HEADLINES:
        h = _headline(facts, key, label, kind, today)
        if h:
            out.append(h)
    return out


def headlines_text(items: list[dict], generated_at: str, thesis: list[dict] | None = None) -> str:
    lines = [f"US credit card data, latest readings ({generated_at})", ""]
    lines += [f"- {h['label']}: {h['text']}" for h in items]
    if thesis:
        state = "holds" if all(t["holds"] for t in thesis if t["holds"] is not None) else "BROKEN"
        lines += ["", f"Thesis of {THESIS_DATE} ({state}): {THESIS_CLAIM}"]
        for t in thesis:
            mark = "ok " if t["holds"] else ("BROKEN" if t["holds"] is False else "no data")
            lines.append(f"- [{mark}] {t['label']} -> {t['text']}")
    lines += ["", "Source: https://user5017.github.io/credit-card-data/ (public data, checked against the releases)"]
    return "\n".join(lines) + "\n"


# ---------- health, revisions, new periods ----------


def _latest_by_source(facts: pd.DataFrame, meta_idx: dict, today: dt.date) -> dict[str, str]:
    """source -> 'Jun 2026 (Revolving consumer credit (SA))' for the series with the newest ended period."""
    out = {}
    facts = facts[facts["period_end"].dt.date <= today] if not facts.empty else facts
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


def _health_summary(health: dict) -> dict:
    statuses = [h.get("status", "failed") for h in health.values()]
    worst = max(statuses, key=lambda st: STATUS_RANK.get(st, 2), default="failed")
    n_ok = sum(1 for st in statuses if st == "ok")
    return {"n": len(statuses), "n_ok": n_ok, "worst": worst, "worst_label": STATUS_LABELS.get(worst, worst)}


REVISION_MIN_REL = 0.001  # revisions smaller than 0.1% are counted but not listed (float noise, rounding)
MAX_LISTED_REVISIONS = 12
MAX_LISTED_PERIODS = 6


def _rel_display(old: float, rel: float) -> str:
    """'0.80%' or, when the old value was 0 (rel_change is inf in revisions.csv), 'from 0'."""
    if old == 0 or rel is None or not math.isfinite(rel):
        return "from 0"
    return f"{rel * 100:.2f}%"


def _run_revisions(revisions: pd.DataFrame, meta_idx: dict, run: str | None) -> dict:
    """The revisions logged by the run stamped `run` (the health file's generated_at), largest first.

    Keyed on the run, not on the newest row in the file: after the first revision ever, the newest row would
    otherwise be listed on every later run."""
    empty = {"n": 0, "listed": []}
    if revisions.empty or not run:
        return empty
    df = revisions[revisions["pulled_at"] == run].copy()
    if df.empty:
        return empty
    df["rel_change"] = pd.to_numeric(df["rel_change"], errors="coerce")
    df["old_value"] = pd.to_numeric(df["old_value"], errors="coerce")
    df["new_value"] = pd.to_numeric(df["new_value"], errors="coerce")
    n = int(len(df))
    df = df[(df["rel_change"] > REVISION_MIN_REL) | (df["old_value"] == 0)]
    df = df.sort_values("rel_change", ascending=False).head(MAX_LISTED_REVISIONS)
    listed = []
    for _, r in df.iterrows():
        m = meta_idx.get((r["metric"], r["entity"], r["tier"], r["period_type"], r["source"]))
        listed.append(
            {
                "name": m["display_name"] if m is not None else f"{r['metric']} {r['entity']}",
                "source_label": SOURCE_LABELS.get(r["source"], r["source"]),
                "period": period_label(pd.Timestamp(r["period_end"]).date(), r["period_type"]),
                "old": r["old_value"],
                "new": r["new_value"],
                "rel": _rel_display(r["old_value"], r["rel_change"]),
            }
        )
    return {"n": n, "listed": listed}


def _new_periods(facts: pd.DataFrame, run: str | None) -> list[dict]:
    """Per source and cadence, the periods the run stamped `run` added beyond what earlier runs had loaded: rows
    carrying the run's pulled_at whose period_end is later than every period_end loaded by an earlier run (unchanged
    values keep the pulled_at of the run that first loaded them). A revised value in an old period is not a new
    period, even when a source re-publishes a whole quarter."""
    if facts.empty or not run:
        return []
    out = []
    for source, grp in facts.groupby("source", sort=True):
        this = grp[grp["pulled_at"] == run]
        if this.empty:
            continue
        prior = grp[grp["pulled_at"] != run]
        items = []
        for pt, sub in this.groupby("period_type", sort=True):
            earlier = prior.loc[prior["period_type"] == pt, "period_end"]
            frontier = earlier.max() if not earlier.empty else None
            new = sorted({d for d in sub["period_end"] if frontier is None or d > frontier})
            if not new:
                continue
            word = PERIOD_WORDS.get(pt, pt)
            if prior.empty or len(new) > MAX_LISTED_PERIODS:
                items.append(f"{len(new)} {word} periods through {period_label(new[-1].date(), pt)}")
            else:
                items.append(", ".join(period_label(d.date(), pt) for d in new) + f" ({word})")
        if items:
            out.append({"source": str(source), "label": SOURCE_LABELS.get(str(source), str(source)), "periods": items})
    return out


# ---------- render ----------


def render(paths: Paths, today: dt.date | None = None) -> Path:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    facts = read_facts(paths.facts_csv)
    meta = load_series(paths.series_csv)
    meta_idx = series_index(meta)
    health_doc = json.loads(paths.health_json.read_text(encoding="utf-8")) if paths.health_json.exists() else {}
    health = health_doc.get("sources", {})
    revisions = read_revisions(paths.revisions_csv)
    golden = _golden_index(facts, paths.golden_yaml)

    con = _connect(facts, paths.views_sql, paths.tccp_products_csv, paths.issuers_csv)
    panels = []
    for panel in PANELS:
        charts = [_chart_payload(con, spec, meta_idx, health, golden, today) for spec in panel["charts"]]
        panels.append({"name": panel["name"], "blurb": panel["blurb"], "charts": charts})
    thesis = thesis_status(con, today)
    con.close()

    run = health_doc.get("generated_at")  # the stamp every row and revision of the latest refresh carries
    generated_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    latest = headlines(facts, today)
    thesis_holds = all(t["holds"] for t in thesis if t["holds"] is not None)
    payload = {
        "generated_at": generated_at,
        "default_years": DEFAULT_YEARS,
        "recessions": _recessions(),
        "charts": [c for p in panels for c in p["charts"]],
    }
    payload_json = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    env = Environment(loader=PackageLoader("carddash", "templates"), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("index.html.j2")
    html = tpl.render(
        generated_at=generated_at,
        health=_health_rows(health, _latest_by_source(facts, meta_idx, today)),
        health_summary=_health_summary(health),
        health_generated=(run or "never")[:16].replace("T", " "),
        panels=panels,
        headlines=latest,
        thesis=thesis,
        thesis_holds=thesis_holds,
        thesis_note=THESIS_NOTE,
        thesis_date=THESIS_DATE,
        thesis_claim=THESIS_CLAIM,
        run=run,
        revisions=_run_revisions(revisions, meta_idx, run),
        new_periods=_new_periods(facts, run),
        has_revisions_csv=paths.revisions_csv.exists(),
        sources=[SOURCE_LABELS[s] for s in SOURCE_LABELS if s in health],
        default_years=DEFAULT_YEARS,
        benchmark_label=BENCHMARK_LABEL,
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
    (paths.docs / "latest.txt").write_text(headlines_text(latest, generated_at[:10], thesis), encoding="utf-8", newline="\n")
    (paths.docs / "data").mkdir(exist_ok=True)
    if paths.facts_csv.exists():
        shutil.copyfile(paths.facts_csv, paths.docs / "data" / "facts.csv")
    if paths.revisions_csv.exists():
        shutil.copyfile(paths.revisions_csv, paths.docs / "data" / "revisions.csv")
    for chart in payload["charts"]:
        if chart["post"]:
            write_png(chart, paths.docs / "img" / f"{chart['id']}.png", recessions=payload["recessions"], years=DEFAULT_YEARS)
    (paths.docs / ".nojekyll").write_text("", encoding="utf-8")
    return out
