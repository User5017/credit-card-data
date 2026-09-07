# Task board

One bounded task per session. Each has a pass condition written before work starts.
Status: `todo` | `doing` | `done YYYY-MM-DD` | `blocked (why)`.

## v1: five fetchers, three panels, self-updating

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 0 | Golden numbers for FRED series | checks/golden.yaml has ≥8 entries traced to the G.19 release page; test_golden passes on fixtures | done 2026-09-07 |
| 1 | Walking skeleton: scaffold, FRED fetcher, loader, health, render, CI, Pages | Pages URL serves the page with a green health strip and Growth/Pricing/Performance charts; Actions run green; `pytest` green; golden checks pass live | done 2026-09-07 (first Actions run 34149165528 green; bot commit c76ae79; live page stamped from CI) |
| 2 | CFPB TCCP fetcher (semiannual xlsx, per card product) | raw table `tccp_products` + facts view for median max-APR by issuer tier; fixture test; golden entry from the CFPB page; first cross-source chart `v_offered_vs_paid` skeleton | done 2026-09-07 (six workbooks H1 2023 to H2 2025, 82 facts rows in 15 series; `data/raw/tccp/tccp_products.csv` 3,781 products; golden: 15 issuers above 30% in H1 2023 matches the CFPB's Feb 2024 report; `offered_vs_paid` chart live in Pricing; first Actions run 34152591538 green from the runner, bot commit c596df7) |
| 3 | Philly Fed large-bank card CSV fetcher | balances, utilization, delinquency loaded (whole panel; the Fed splits only purchase volume, original credit limit and new-account shares by score bucket) with the bucket-to-tier mapping documented in crosswalks/tiers.csv; fixture test; golden entry | done 2026-09-07 (both 26Q1 CSVs, 2012Q3 to 2026Q1, 2,804 facts rows in 51 series; eight golden numbers from the Q1 2026 Insights Report reproduce; live refresh ok, page rendered; first Actions run 34158322607 green from the runner, bot commit f6d350f) |
| 4 | NY Fed Household Debt & Credit xlsx fetcher | card balances and delinquency transitions loaded; quarter discovery works when a new file appears; fixture test; golden entry | done 2026-09-07 (2026Q2 workbook, 1,200 facts rows in 12 series: card balances, accounts, limits, 90+ share, 30+ and 90+ delinquency flows 2003Q1 to 2026Q2, plus the 90+ flow by six age groups from 2000Q1; three golden numbers from the 2026-08-11 press release reproduce; live refresh ok, page rendered; first Actions run 34164118850 green from the runner, bot commit 0faec59) |
| 5 | FDIC BankFind API fetcher | card loans, charge-offs, past due for the banks in crosswalks/issuers.csv; entity `CERT:<n>`; issuer roll-up view; fixture test; golden entry | todo |
| 6 | `dim_issuer` with merger handling | valid_from/valid_to/merged_into populated for the top card banks and sponsor banks; unmatched-name report in health | todo |
| 7 | Revisions block and PNG export | "What changed" lists revisions; `carddash render` writes docs/img/<chart>.png for three post charts | todo |
| 8 | Two green releases | Health strip all green across two consecutive scheduled releases of every source | todo |

## v1.5 (after two green releases)
- BEA PCE detail (monthly, API key), Census Monthly Retail Trade (API), NY Fed SCE Credit Access (every 4 months), CFPB complaints via the trends endpoint.
- Spend panel.

## v2
- Issuer 8-K monthly credit metrics (COF, SYF, BFC, AXP). 10-Q economics via SEC XBRL with a per-issuer tag map.
- Post-drafting routine after each refresh (human posts).

## Open items and known gaps
- Repo transfer User5017 -> SamuelJWebber: wanted, deferred (2026-09-07). Checklist of references to update is in CLAUDE.md under "GitHub accounts".
- FRED account created 2026-09-07 (credentials in `C:\Users\samwe\code\fred-account.txt`, outside the repo). `FRED_API_KEY` is set in the local `.env` (gitignored) and as an Actions secret; the fetcher uses the official API when the key is present and falls back to keyless fredgraph.csv otherwise.
- Golden entries are re-verified against the G.19 release page itself with `uv run python checks/verify_g19.py` (no FRED, no LLM). Run it whenever golden.yaml changes.
- SLOOS demand series id not yet identified (only standards loaded).
- TCCP loads the 2023 survey layout only (H1 2023 on). The H2 2022 workbook uses the older layout ('Minimum/Median/Maximum APR', 'Poor or Fair/Good/Great Credit', no top-25 flag) and the 1990-2022 archive on data.consumerfinance.gov is a third one. Adding H2 2022 needs a second header map in `fetchers/tccp.py`; the archive is a separate survey.
- TCCP issuer-size split (`TCCP_TOP25` / `TCCP_OTHER`) starts H2 2023, when the public file gained the 'Issued by Top 25 Institution' flag. The CFPB's Feb 2024 large-vs-small medians for H1 2023 therefore cannot be reproduced exactly (see the spot-check log in checks/golden.yaml).
- TCCP re-uploads: the CFPB replaced the H2 2024 file under a suffixed name with an extra 'Institution Type' column. Snapshots use canonical names (`cfpb_tccp-data_<period_end>.xlsx`), so a re-upload shows as a content diff and, if values move, as revisions.
- TCCP timing: H1 2026 is due to the CFPB in September 2026 and files have been posted about six months after period end (H2 2025 appeared June 2026). `max_age_days` is 400, so the source turns stale in early February 2027 if H1 2026 is late.
- TCCP 'Report Date' column is unreliable (H1 2025 says 'Data as of June 30', six rows say December 31). Dating comes from the file name and is checked against the title block.
- Philly Fed discovery: the URL embeds the release quarter (`.../Y14/2026/Q1/26Q1-CreditCardBalances.csv`). The fetcher starts at the calendar quarter of the run date and walks back up to 8 quarters until the Balances file answers; a missing file is an HTML 404 page served with HTTP 200, so the body is checked, not the status. Each release carries the full history from 2012Q3, so only the newest release is downloaded. Older Balances files stay online, older Originations files do not.
- Philly Fed timing: releases land about 3.5 months after quarter end (2026 Q1 on 2026-07-13). `max_age_days` is 230 so the source does not turn stale in the normal gap; if Q2 2026 has not appeared by mid-November 2026 it goes stale.
- Philly Fed score buckets are <660, 660-719, >=720 and apply only to purchase volume per account, median original credit limit, and the sub-660 share of new accounts and commitments. Balances, utilization, delinquency and charge-offs are whole-panel only, so a delinquency-by-tier chart cannot come from this source. `y14_card_utilization_rate` is the one computed series (balances / commitments, the Fed's own definition).
- Philly Fed restatements: the Y-14 panel changes when a bank crosses the $5 billion threshold, and every release re-publishes the full history, so a panel change shows up as a wholesale restatement (suspect, needs an ack). The golden entries are on the newest quarter, so they turn amber on the next release if that quarter is revised.
- Not on the board, suggested for after task 8: a Pricing chart of TCCP median purchase APR by issuer size x credit tier (the six series are already in facts) and the `v_apr_spread_large_small` view from PLAN §6; a download link for `data/raw/tccp/tccp_products.csv` on the page.
- Not on the board, suggested for after task 8: a Performance chart of the HHDC 90+ flow by age group (six series already in facts) and a cross-source delinquency chart (Philly Fed 90+ balance-based rate for Y-14 banks against the HHDC 90+ share for all lenders, with the scope notes explaining the level gap).
- NY Fed HHDC discovery: the URL embeds the data quarter (`.../householdcredit/data/xls/HHD_C_Report_2026Q2.xlsx`). The fetcher starts at the calendar quarter of the run date and walks back up to 8 quarters until a real xlsx answers; a missing quarter redirects to `/errors/404` served with HTTP 200 and `text/html`, so the body's zip signature is checked, not the status. The xlsx downloads directly with the pipeline User-Agent, no terms click (verified 2026-09-07). Data sheets are found by their A1 title because the `Page N Data` sheet names follow the PDF's page numbers.
- NY Fed HHDC timing: releases land five to seven weeks after quarter end (2026 Q2 on 2026-08-11). `max_age_days` is 200, so the source goes stale if 2026 Q3 has not appeared by mid-January 2027.
- NY Fed HHDC revisions: every release re-publishes the full history and revises prior quarters (between the 2026Q1 and 2026Q2 files the 2026Q1 card balance moved by $10bn and limits by $10bn; the flows did not move; nothing moved between the 2025Q2 and 2026Q2 files). So the balance golden entry is fixture-only (`check_live: false`) and the two flow entries are live; the 2026 Q2 flow can turn amber if revised, the 2025 Q2 flow is expected to hold. A panel or methodology change would show as a wholesale restatement (suspect, needs an ack).
- NY Fed HHDC scope: card balances by age and by state are not in the workbook (only total debt is split that way); the age split exists only for the 90+ flow. 'Credit card' is bankcards; retail cards sit in 'Other'. The limit sheet's balance column and the age sheet's 'all' column are near-duplicates of loaded series and are not loaded. The account-count sheet labels 2003Q1 to 2011Q1 with date cells; the parser accepts both label styles and requires consecutive quarters.
- NY Fed SCE download may sit behind a terms click: verify before grading A (the HHDC xlsx does not, verified 2026-09-07).
- FDIC charge-off fields may be year-to-date: de-cumulate by quarter if so (check one bank by hand).
- BEA API row caps on underlying-detail tables: may need per-year requests.
