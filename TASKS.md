# Task board

One bounded task per session. Each has a pass condition written before work starts.
Status: `todo` | `doing` | `done YYYY-MM-DD` | `blocked (why)`.

## v1: five fetchers, three panels, self-updating

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 0 | Golden numbers for FRED series | checks/golden.yaml has ≥8 entries traced to the G.19 release page; test_golden passes on fixtures | done 2026-09-07 |
| 1 | Walking skeleton: scaffold, FRED fetcher, loader, health, render, CI, Pages | Pages URL serves the page with a green health strip and Growth/Pricing/Performance charts; Actions run green; `pytest` green; golden checks pass live | doing: everything live except CI. `.github/workflows/refresh.yml` is written but the GitHub token lacked `workflow` scope; it sits in `.git/info/exclude`. After `gh auth refresh -s workflow`: remove that exclude line, `git add .github`, push, confirm one green Actions run. |
| 2 | CFPB TCCP fetcher (semiannual xlsx, per card product) | raw table `tccp_products` + facts view for median max-APR by issuer tier; fixture test; golden entry from the CFPB page; first cross-source chart `v_offered_vs_paid` skeleton | todo |
| 3 | Philly Fed large-bank card CSV fetcher | balances, utilization, delinquency by score bucket loaded with tier mapping documented in crosswalks/tiers.csv; fixture test; golden entry | todo |
| 4 | NY Fed Household Debt & Credit xlsx fetcher | card balances and delinquency transitions loaded; quarter discovery works when a new file appears; fixture test; golden entry | todo |
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
- FRED runs keyless via fredgraph.csv. A FRED_API_KEY secret switches the fetcher to the official API; obtaining one needs a FRED account created in a browser.
- SLOOS demand series id not yet identified (only standards loaded).
- NY Fed SCE download may sit behind a terms click: verify before grading A.
- FDIC charge-off fields may be year-to-date: de-cumulate by quarter if so (check one bank by hand).
- BEA API row caps on underlying-detail tables: may need per-year requests.
