# Credit Card Public-Data Dashboard — Design Plan for Review

**Status:** proposal, nothing built yet. Written 2026-09-07.
**Ask of the reviewer:** judge the architecture, data model, and build order. Flag anything that will break at month 3 rather than day 1. Specific questions are in §12.

---

## 0. Context

- One person, currently between jobs, with credit/fintech background. Wants a portfolio project that also produces a recurring LinkedIn post.
- Tooling: Windows 11, Python, Claude Code writes most of the code. No budget for paid data or hosting.
- The user has already rejected two framings: (a) fitting a credit-risk model on a Kaggle dataset, (b) analyzing rewards-category psychology. The focus is **card P&L from public data**: where balances are growing, what APR is charged, what interchange is earned and rewards paid, how credit is performing.
- A companion document (`sources-catalog.html`) inventories 33 public sources with cadence, format, and an automatability grade (A = API/CSV, B = structured but needs a parser, C = PDF).

## 1. Goal and non-goals

**Goal:** a single static HTML dashboard that refreshes itself on a schedule, joins ~10–15 public sources, shows growth / pricing / performance / spend panels, and is trustworthy enough to cite publicly.

**Non-goals (v1):**
- No account-level modeling, no forecasting.
- No PDF parsing (grade-C sources) — those are v3.
- No server, database service, or login. Static file only.
- No LLM in the numeric path for structured sources.

## 2. Sources in v1 (all grade A)

| Source | Cadence | Format | What it contributes |
|---|---|---|---|
| Fed G.19 Consumer Credit | monthly (APR series quarterly) | CSV / FRED API | revolving balances by holder; headline card APR |
| CFPB Terms of Credit Card Plans (TCCP) | semiannual | CSV | offered purchase APR range, fees, per card, per issuer, by target credit tier |
| Philadelphia Fed Large Bank Credit Card Data (from FR Y-14M) | quarterly | Excel | balances, utilization, share revolving vs. paying in full, delinquency — by credit-score bucket |
| NY Fed Household Debt & Credit | quarterly | Excel | card balances by age and state; delinquency transitions |
| FFIEC Call Reports | quarterly | bulk CSV | per-bank card loans, charge-offs, past-due (incl. fintech sponsor banks) |
| Fed charge-off & delinquency rates | quarterly | CSV | industry card NCO/DQ, top-100 banks vs. rest |
| Fed SLOOS | quarterly | CSV/HTML tables | net % of banks tightening card standards |
| NY Fed SCE Credit Access | every 4 months | Excel | card application and rejection rates |
| BEA PCE detail tables | monthly | API | spending by ~200 categories |
| Census Monthly Retail Trade | monthly | API | retail sales by NAICS |
| CFPB Consumer Complaint DB | daily | Socrata API | complaint counts by company / issue / state |

v2 adds grade-B: issuer monthly 8-K credit metrics (COF, SYF, BFC, AXP), and 10-Q economics lines (interchange, rewards, yield) for AXP / COF / SYF / BFC. v3 adds grade-C PDFs (ABS trust statements, Visa/MC interchange schedules, CFPB biennial report).

## 3. Architecture in one paragraph

Each source has one fetcher script. Every fetcher reshapes its source into the same **long-format fact table** (one row per metric × entity × tier × period) and upserts into a single DuckDB file. Four hand-maintained dimension tables (issuers, credit tiers, merchant categories, release calendar) supply the join keys. SQL views compute derived metrics. A render script queries the views and writes one self-contained `index.html` with data embedded as JSON and inline charts. A GitHub Actions cron runs the whole chain daily and publishes the HTML via GitHub Pages.

```
fetchers/*.py ──► validate ──► upsert facts (DuckDB) ──► sql/views.sql ──► build/render.py ──► docs/index.html ──► GitHub Pages
                                    ▲
                     crosswalks/*.csv (issuers, tiers, categories, calendar)
```

## 4. Data model

### 4.1 `facts` — the one table everything becomes

```sql
CREATE TABLE facts (
  metric        VARCHAR NOT NULL,   -- e.g. 'card_balances', 'offered_apr_max', 'nco_rate', 'pce_nominal'
  entity        VARCHAR NOT NULL,   -- issuer_id from dim_issuer, or 'ALL', 'ALL_LARGE', 'PCE:pets', 'NAICS:4471', 'STATE:ME', 'AGE:30-39'
  tier          VARCHAR NOT NULL,   -- CFPB tier code from dim_tier, or 'all'
  period_end    DATE    NOT NULL,   -- last day of the period
  period_type   CHAR(1) NOT NULL,   -- D, M, Q, H, A
  value         DOUBLE,
  unit          VARCHAR,            -- 'usd_bn', 'pct', 'count', 'index'
  source        VARCHAR NOT NULL,   -- fetcher id
  pulled_at     TIMESTAMP NOT NULL,
  PRIMARY KEY (metric, entity, tier, period_end, period_type, source)
);
```

Rationale: sources have incompatible column layouts and incompatible cadences. Forcing all of them into one narrow table means (a) one upsert path, (b) one query pattern for any pair of sources, (c) adding a source never changes the schema.

### 4.2 Dimension tables (hand-maintained CSVs, loaded into DuckDB)

- **`dim_issuer`** — one row per issuer: `issuer_id`, display name, SEC `cik`, list of bank `rssd_id`s (issuers have multiple charters), ABS trust names, TCCP institution-name variants, CFPB complaint company-name variants. ~30 rows. This is the hardest artifact to get right and the most valuable.
- **`dim_tier`** — maps every source's credit buckets onto the CFPB five: superprime 720+, prime 660–719, near-prime 620–659, subprime 580–619, deep subprime <580. Philly Fed uses FICO buckets, TCCP uses issuer-declared target tiers, bureaus use VantageScore bands. Mapping is approximate and documented per source.
- **`dim_category`** — BEA PCE line ↔ Census NAICS ↔ card-network merchant category group. Approximate by nature (Amazon, Walmart, Costco straddle lines). Tolerance stated, not hidden.
- **`dim_calendar`** — per source: cadence, typical release day, expected lag. Drives the "overdue" flag in the health panel.

### 4.3 Cadence alignment

Sources are joined **as-of**, never on exact date: for a row at `period_end = X`, the joined value from a slower source is the latest one with `period_end <= X`. DuckDB `ASOF JOIN` does this natively.

Example: monthly charge-off rate for Capital One joined to its semiannual offered APR → each month carries the most recent TCCP reading. Two conventions: (1) slower series are always step-functions, never interpolated; (2) charts label the source's true cadence so a step isn't mistaken for a monthly reading.

### 4.4 Revisions

Several sources revise history (G.19 revises prior months; call reports get amended). Policy: `facts` always holds the **latest** value (upsert overwrites). Every fetcher also writes its raw download to `data/raw/<source>/<pulled_at>.<ext>` so any vintage can be rebuilt. Vintage tracking in `facts` itself is out of scope for v1.

## 5. Pipeline contracts

### 5.1 Fetcher contract (one file per source)

Each fetcher exposes `fetch() -> pandas.DataFrame` in the `facts` schema and nothing else. Rules:
- Idempotent: running twice produces the same rows.
- Pulls the full available history on every run (these files are small), not just the delta — simplest correct behaviour and it picks up revisions for free.
- Writes raw download to `data/raw/` before parsing.
- Never writes to DuckDB directly; the loader does.

### 5.2 Validation (loader, before upsert)

- Schema check; no null keys.
- Range checks per metric (e.g. APR in [0, 50]; rates in [0, 100]; balances > 0). Violations fail the source, not the run.
- Continuity: no gaps in `period_end` beyond the cadence for the last 24 periods; warn, don't fail.
- Freshness: latest `period_end` ≥ what `dim_calendar` says should be out by today; otherwise mark `overdue`.

### 5.3 Failure behaviour

A source that fails validation or fetch leaves its prior rows untouched and sets a status flag. The build continues. The health panel shows the source red with the error. The run never blanks a chart.

## 6. Derived metrics (SQL views)

All computed in `sql/views.sql` over `facts`; adding a metric means adding a view.

- `v_growth` — YoY and QoQ % change for every balance-type metric.
- `v_apr_spread_large_small` — TCCP median max-APR for top-25 issuers minus the rest, by tier, by half. (Replicates CFPB's 2024 finding and tracks it forward.)
- `v_offered_vs_paid` — TCCP offered APR (by tier) beside G.19 assessed-interest APR and Philly Fed revolving share (by score bucket), as-of joined by quarter.
- `v_revolve_share` — Philly Fed share of accounts revolving vs. transacting, by tier.
- `v_sponsor_bank_growth` — call-report card loans for the fintech sponsor banks, QoQ.
- `v_health` — per source: last period loaded, last pull, expected-by date, status.
- v2: `v_giveback_ratio` — rewards expense ÷ interchange revenue per issuer per quarter (AXP, COF, SYF, BFC).

## 7. Rendering

- `build/render.py` queries the views and writes **one** `docs/index.html`: inline CSS, data embedded as a JSON blob, inline JS charting. No runtime fetches, so it opens from `file://` or from Pages identically.
- Charting: a single small library loaded from a CDN, or hand-rolled SVG for the ~15 charts. Decision open (§12).
- Every chart carries: source name, true cadence, `period_end` of the latest point, and pull date.
- Layout: health strip on top, then four panels — Growth, Pricing, Performance, Spend. Filters (issuer, tier) implemented client-side over the embedded JSON.
- Size budget: < 3 MB total. The full history of all v1 series is on the order of 10⁵ numbers.

## 8. Scheduling and hosting

**Chosen:** GitHub Actions, daily cron (~06:00 ET). Steps: checkout → install → run all fetchers → load → render → commit `data/warehouse.duckdb` + `docs/index.html` if changed → Pages deploys `docs/`. Free tier is ample (a run is minutes).

Why daily when most sources are monthly/quarterly: release dates drift and the cost of checking is nil. `dim_calendar` decides what's overdue; the cron just checks.

**Alternatives considered:**
- Windows Task Scheduler on the user's laptop — fails whenever the laptop is closed. Rejected.
- Claude Code scheduled routines (cloud) — viable, and the natural home for the *post-drafting* step (§9), but a plain cron with deterministic Python is the right tool for the data refresh.

Public repo is fine: every input is public government or SEC data; the code has no secrets except a free BEA API key (stored as an Actions secret).

## 9. Where an LLM is and isn't used

- **Build time:** Claude Code writes fetchers, crosswalks, views, render. Bulk of the labour.
- **Not in the refresh path for grade A/B sources.** Deterministic parsers only.
- **v3 only:** an LLM extraction step for grade-C PDFs (ABS trust monthly statements, network interchange schedules), emitting `facts` rows that pass through the same validation gate as everything else.
- **After each refresh (optional, v2):** a scheduled routine reads `v_health` and the deltas, drafts LinkedIn post text plus a chart reference, and leaves it for the user to edit and post manually. Human between the data and the byline.

## 10. Build order

1. Repo scaffold, DuckDB schema, `dim_issuer` and `dim_tier` crosswalks, loader with validation, health panel rendering. *(~1 day)*
2. Three fetchers: G.19, TCCP, Philly Fed. First real view: `v_offered_vs_paid`. First real chart. *(~1–2 days)*
3. Remaining v1 fetchers, one per evening, each landing as a panel section. *(~1 week)*
4. GitHub Actions cron + Pages. Dashboard now self-updates. *(~half day)*
5. v2: monthly 8-K parsers; 10-Q economics lines via SEC XBRL API (expect issuer-custom tags; per-issuer mapping table). *(~1 week)*
6. v3: PDF layer with LLM extraction. *(open-ended)*

## 11. Known risks and weaknesses

- **Crosswalk drift.** `dim_issuer` breaks on M&A (Discover → Capital One, 2025) and on TCCP name spellings. Mitigation: unmatched-name report in the health panel; quarterly manual review.
- **Source format changes.** Philly Fed and NY Fed publish Excel with human-edited layouts; a moved column breaks a fetcher silently unless validation catches it. Mitigation: range + continuity checks; raw snapshots for diffing.
- **As-of joins can mislead.** A semiannual APR shown beside monthly charge-offs implies precision it doesn't have. Mitigation: step rendering + cadence labels; never interpolate.
- **Tier mapping is approximate.** Comparing TCCP "prime" (issuer-declared) to Philly Fed 660–719 (FICO) is a judgment. Mitigation: mapping documented in `dim_tier`, footnoted on charts.
- **Revisions.** Overwriting with latest is simple but loses the "what did we know then" view. Acceptable for v1; raw snapshots make it recoverable.
- **XBRL custom tags (v2).** Interchange and rewards lines are often issuer-extension tags, not standard US-GAAP. Expect a hand-built tag map per issuer that needs review each 10-K.
- **CFPB continuity.** TCCP and the complaint DB are live as of Sep 2026, but the agency's output has been unstable (complaint narratives ended Aug 14, 2026). Health panel will surface a stall; no other mitigation available.
- **Single-maintainer bus factor.** Crosswalks are judgment, not code. Mitigation: every mapping row has a `note` column explaining why.

## 12. Decisions the reviewer should judge

1. **Store:** DuckDB single file vs. SQLite vs. Parquet-per-source with pandas joins. Leaning DuckDB for `ASOF JOIN` and direct Excel/CSV reads.
2. **Full-history re-pull every run** vs. incremental. Leaning full re-pull for simplicity and revision handling; sizes are small. Is there a source where this is a problem?
3. **Embed all data in the HTML** vs. HTML + sidecar JSON. Leaning embed for `file://` compatibility.
4. **Charting:** one CDN library vs. hand-rolled SVG. ~15 charts, must render identically offline.
5. **Facts granularity:** is a five-key fact table (`metric, entity, tier, period_end, period_type`) enough, or will merchant-category data (which has no issuer or tier) and state/age data (which has neither) want their own tables? Current answer: overload `entity` with a prefix (`PCE:`, `STATE:`, `AGE:`). Is that going to hurt?
6. **Daily cron** vs. release-calendar-triggered runs.
7. **Revision handling:** is "latest wins + raw snapshots" adequate, or should `facts` carry a `vintage` column from day one?
8. **Is anything in §2 not actually fetchable programmatically** (e.g. a source behind a click-through or CAPTCHA) that should be demoted to grade B?
9. What is missing from §11?

## 13. Definition of done for v1

- Eleven sources loading on a daily cron with zero manual steps.
- Health panel shows all green for at least two consecutive scheduled releases.
- Four panels render correctly from `file://` and from GitHub Pages.
- One chart (`v_offered_vs_paid`) that could not have been produced from any single source.
