# Credit Card Public-Data Dashboard — Plan (v2, as reviewed)

**Status:** building. Written 2026-09-07 from the original proposal (design/PLAN-v1-original.md) and the
review that changed it (design/PLAN-review.md). Task-level status lives in TASKS.md.

## 0. Context

One person with credit and fintech background, building a portfolio project that also produces a recurring
LinkedIn post. Windows 11, Python, Claude Code writes most code. No budget for paid data or hosting.
Focus: **card P&L from public data**, meaning where balances are growing, what APR is charged, what interchange
is earned and rewards paid, and how credit is performing. Companion inventory of 33 public sources with
automatability grades: design/sources-catalog.html.

## 1. Goal and non-goals

**Goal.** A single static HTML page that refreshes itself on a schedule, joins public sources, shows Growth,
Pricing and Performance panels, and is trustworthy enough to cite publicly. The limit case to design for:
ignored for six months, it is still correct or visibly red. It never shows a stale number as fresh.

**Non-goals for v1.** No account-level modeling or forecasting. No PDF parsing. No server, database service,
or login. No LLM in the numeric path for structured sources. No client-side filters. No spend crosswalk.

## 2. Sources

### v1 (five fetchers)

| Fetcher | Sources | Cadence | Transport | Verified |
|---|---|---|---|---|
| `fred` | Fed G.19 consumer credit and card APR; H.8 bank card loans; charge-off and delinquency rates; SLOOS card standards | M, W, Q | fredgraph.csv (keyless) or FRED API with key | 2026-09-07 |
| `tccp` | CFPB Terms of Credit Card Plans | H | one xlsx per half-year, linked from the survey page (names vary: `cfpb_tccp-data_2025-12-31.xlsx`, `..._2023-07-01_2023_12-31.xlsx`, re-uploads get a suffix), current layout from H1 2023 | 2026-09-07, loaded H1 2023 to H2 2025 |
| `phillyfed` | Philadelphia Fed large-bank credit card data (FR Y-14M) | Q | two CSVs per release (Balances, Originations), each with the full history; URL embeds the quarter, fetcher walks back from the current quarter | 2026-09-07, loaded 2012 Q3 to 2026 Q1 |
| `nyfed_hhdc` | NY Fed Household Debt and Credit (Consumer Credit Panel/Equifax) | Q | one xlsx per release with the full history (`HHD_C_Report_2026Q2.xlsx`); the file name embeds the data quarter, fetcher walks back from the current quarter; data sheets found by title, not sheet name | 2026-09-07, loaded 2003 Q1 to 2026 Q2 (age split from 2000 Q1) |
| `fdic` | FDIC BankFind Suite API financials: card loans, 30-89 days past due, noncurrent (90+ plus nonaccrual) and the FDIC's quarterly charge-offs, recoveries and net charge-offs, per charter (`CERT:<n>`, the 29 charters in crosswalks/issuers.csv) and for all insured institutions (`FDIC_ALL_INSURED`, BKCLASS NC and OI excluded) | Q | one CSV per charter plus one aggregate JSON, no key | 2026-09-08, loaded 1984 Q1 to 2026 Q2 |

Corrections from the review: FFIEC bulk download is an ASP.NET form that returns 403 to scripts, so the FDIC API
replaces it. The CFPB complaint database is not on Socrata; it has an Elasticsearch API with a `trends`
aggregation endpoint (no trailing slash). SLOOS and Fed charge-off rates are on FRED, so three sources collapse
into one fetcher.

### v1.5
BEA PCE detail (API key), Census Monthly Retail Trade (API), NY Fed SCE Credit Access (T), CFPB complaints
(trends endpoint only, never rows). Spend panel.

### v2 and v3
Issuer monthly 8-K credit metrics (COF, SYF, BFC, AXP); 10-Q economics via SEC XBRL with a per-issuer tag map.
Then grade-C PDFs (ABS trust statements, network interchange schedules) through an LLM extraction step that
emits facts rows into the same validation gate.

## 3. Architecture

    fetchers/*.py -> validate -> replace-by-source into data/facts.csv -> sql/views.sql -> render.py -> docs/index.html -> Pages
                                       ^
                       crosswalks/series.csv, issuers.csv, tiers.csv

Each fetcher reshapes one source into the long `facts` table. The loader validates, diffs against the previous
load, replaces that source's rows, and writes health. DuckDB is created in memory from facts.csv on every run
for views and rendering; no database file is ever committed. The renderer writes one self-contained HTML file
with a vendored chart library and the data embedded. GitHub Actions runs the chain daily and commits the results.

## 4. Data model

### 4.1 facts

    metric, entity, entity_type, tier, period_end, period_type, value, source, pulled_at
    key = (metric, entity, tier, period_end, period_type, source)

- `period_end` is the last day of the period. `period_type` in D, W, M, Q, T (every four months), H, A.
- Seasonal adjustment is in the metric name (`_sa`, `_nsa`) where the source offers both.
- `entity` is a prefixed id; `entity_type` (aggregate, issuer, bank, state, age, pce, naics) makes the kind
  explicit so views never parse prefixes.
- `unit` is not on the fact row. It lives in series metadata.

### 4.2 dim_series (crosswalks/series.csv)

One row per series, keyed (metric, entity, tier, period_type, source): source_id, unit, scale, sa,
display_name, scope_note, source_url, vmin, vmax, max_age_days. The scope note is what makes cross-source
charts honest: G.19 covers all lenders, H.8 and charge-off series cover commercial banks only, Philly Fed
covers Y-14 filers only, NY Fed is an Equifax panel. Every chart footnote comes from this table.

### 4.3 Other crosswalks

- `issuers.csv` (dim_issuer, src/carddash/issuers.py): one row per FDIC charter: issuer_id, issuer_name, fdic_cert,
  bank_name (the FDIC legal name), kind (issuer or sponsor), sec_cik, valid_from (the FDIC established date),
  valid_to (the merger date), merged_into (the surviving certificate), aliases (spellings other sources use,
  pipe-separated), note. Multi-charter issuers repeat the issuer_id (Bread, Capital One, TD); a charter that merged
  out carries the merger date and its acquirer, and the surviving charter's issuer names the roll-up (Discover into
  Capital One). Chains of mergers are not supported: merged_into must point at an active charter. The fdic fetcher
  checks valid_from, valid_to, merged_into and the active flag against the FDIC's /institutions records on every
  run and fails on a disagreement; a renamed charter is only reported. Names match on a normalized form (case,
  punctuation and spacing dropped), never fuzzy, and the unmatched-name report (`carddash issuers`, and the health
  messages of the tccp and fdic sources after every refresh) lists the TCCP top-25 institution names and the FDIC
  legal names the crosswalk does not know. Lineage before a renaming (Hibernia into Capital One, N.A., Juniper into
  Barclays) is in the note, not modelled: the roll-up carries a charter's whole history. Loaded into DuckDB as
  `issuers`, exposed as the `dim_issuer` view. Hardest artifact; every row that needs one has a note.
- `tiers.csv`: the CFPB five tiers with score bounds. Per-source mappings are documented as sources arrive.

### 4.4 Sub-grain sources

Sources finer than the fact key keep a raw table and feed facts through a view: TCCP is one row per card
product with a set of target tiers (`tccp_products`, committed as data/raw/tccp/tccp_products.csv, rebuilt from the
snapshots every run, facts from sql/tccp_facts.sql); complaints are company x issue x state; call reports
are per charter (`entity = CERT:<n>`, rolled up to issuer in `v_fdic_issuer`, acquirer plus acquired charters summed by period). Aggregation choices live in SQL.

### 4.5 Cadence alignment

Sources are joined as-of, never on exact date, with DuckDB ASOF JOIN. Slower series are step functions,
never interpolated. Charts label the true cadence.

### 4.6 Revisions

Latest value wins. Every changed value is appended to `data/revisions.csv` (old, new, when). Git history of
`facts.csv` is the vintage log. The raw download of each source is committed under `data/raw/<source>/latest/`,
so any vintage can be rebuilt from git. No vintage column in facts.

## 5. Pipeline contracts

### 5.1 Fetcher
`fetch(meta, raw_dir, session, pulled_at) -> DataFrame` in the facts schema. Full history every run.
Idempotent. Raw file saved before parsing. Parse by header text, not position. Never writes facts or DuckDB.
Ships with a fixture test, a golden entry, and rows in series.csv.

### 5.2 Validation (loader, before load)
Schema and null keys; duplicate keys; per-series vmin/vmax; continuity over the last 24 periods (warning);
expected series present (warning); staleness against max_age_days or 2x cadence + 14 days.

### 5.3 Restatement detector
If more than half of the overlapping values moved by more than 5%, the source is `suspect` and not loaded
until listed in `checks/restatement_ack.yaml`. This catches swapped columns that pass range checks.

### 5.4 Golden checks
`checks/golden.yaml` holds numbers copied by hand from release pages. The fixture test runs all of them;
the live run checks the ones marked `check_live` (periods old enough not to be revised). A live mismatch
turns the source amber.

### 5.5 Failure behaviour
A source that fails fetch or validation keeps its prior rows and goes red. The build continues, the page
is published, and then the job exits non-zero so GitHub sends an email. A run never blanks a chart.

## 6. Derived metrics (sql/views.sql)

`v_latest`, `v_growth` (YoY by exact year shift, 52 weeks for weekly), `v_offered_vs_paid` (TCCP median highest
purchase APR matched as-of to the G.19 rate on accounts assessed interest, keyed on the G.19 series). Coming with
their sources: `v_apr_spread_large_small`, `v_revolve_share`, `v_sponsor_bank_growth`.

## 7. Rendering

One `docs/index.html`: inline CSS, vendored uPlot, data embedded as JSON. Health strip, "what changed"
block, three panels, data and method section with a CSV download. Thin lines, hairline grid, fixed series
colors, legend for two or more series, a table twin under each chart. Step charts for period data.
Light and dark via `prefers-color-scheme`. PNG export of three post charts is task 7.

## 8. Scheduling and hosting

GitHub Actions, daily at 22:00 UTC (after the day's releases), plus `workflow_dispatch` and a run on push.
Steps: checkout, uv sync, pytest, refresh, commit data and docs, then fail if any source is red.
`docs/index.html` changes every run (timestamp), so the daily commit also keeps the schedule alive past
GitHub's 60-day inactivity cutoff. Pages serves `docs/` from `main`. Public repo; the only secret is an
optional FRED key.

## 9. Where an LLM is and is not used

Build time: Claude Code writes fetchers, crosswalks, views, render. Not in the refresh path for structured
sources. v3 only: extraction from PDFs, through the same validation gate. Optional post-drafting after a
refresh, with a human between the data and the byline.

## 10. Build order

See TASKS.md. CI and Pages are part of task 1, so every later task is exercised where it will run.
Estimates from the original plan are doubled.

## 11. Risks

- Scope mismatch across sources (bigger than tier mismatch). Mitigation: scope_note on every series, shown
  under every chart.
- Seasonal adjustment mismatch. Mitigation: SA in the metric name; never compared across the line.
- Column swaps that pass range checks. Mitigation: header-text parsing, restatement detector, golden checks.
- Silent format changes in hand-edited Excel (Philly Fed, NY Fed). Mitigation: fixture tests, raw snapshots.
- Crosswalk drift on M&A and name spellings. Mitigation: valid_from/valid_to, unmatched-name report.
- Tier mapping is approximate. Mitigation: documented per source, footnoted.
- As-of joins imply precision they lack. Mitigation: step rendering, cadence labels.
- CFPB output has been unstable. Mitigation: health panel surfaces stalls.
- XBRL custom tags (v2). Mitigation: per-issuer tag map reviewed each 10-K.
- Unpinned dependencies. Mitigation: uv.lock, `--frozen` in CI.
- GitHub cron delays or disablement. Mitigation: daily commit, workflow_dispatch, email on red.
- DuckDB in a cloud-synced folder. Mitigation: repo outside OneDrive; no database file committed.
- Single maintainer. Mitigation: every judgment row has a note; CLAUDE.md carries the contracts.

## 12. Definition of done for v1

- Five fetchers loading on the daily cron with zero manual steps.
- Health strip all green for two consecutive scheduled releases of every source.
- Three panels render identically from file:// and from GitHub Pages.
- Golden checks pass live.
- One chart (`v_offered_vs_paid`) that no single source could have produced.
