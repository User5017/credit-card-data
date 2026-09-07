# Review of PLAN.md — credit card public-data dashboard

Reviewer stance: senior data engineer. Written 2026-09-07 against PLAN.md dated the same day.
Five fetchability claims were checked live (FDIC API, CFPB complaint API, Philly Fed, CFPB TCCP, FRED SLOOS). Everything else is judgment.

Verdict up front: the shape is right (long fact table, deterministic fetchers, static HTML, cron). Four things in the data model will hurt by month 3 and are cheap to fix now. One pipeline behaviour (upsert without delete) is a latent bug. Three sources are mis-described in §2. The build order should put CI first, not fourth. Details below.

---

## 1. Will the data model hold at month 3?

Yes, the single long `facts` table holds. FRED itself is series × date × value; you are not doing anything exotic. What will not hold is the set of things you have hung on that table. Fix these before the first fetcher:

**1a. `unit` does not belong on every fact row.** It is a property of the series, not the observation. Denormalised per row it will drift (one fetcher writes `pct`, another `percent`). Move it to a series-metadata table.

**1b. Seasonal adjustment is a missing dimension, not a footnote.** G.19 has SA and NSA. Census retail trade has both. BEA PCE is SAAR. Comparing NSA card balances to SA retail sales is the classic macro-data error and it will get called out publicly. Either bake it into the metric name (`card_balances_nsa`) or add an `sa` column to the series table. Do not leave it implicit.

**1c. Prefixed `entity` strings are fine as ids, bad as the only type information.** Every view will end up doing `WHERE entity LIKE 'STATE:%'`, age bands will sort lexically wrong, PCE lines and NAICS codes are trees and you will want a parent. Add an `entity_type` column to `facts` and a `dim_entity` table (id, type, display name, sort order, parent). Keep the prefix convention for readability.

**1d. Some sources are finer-grained than the fact key and the plan pretends otherwise.** TCCP is one row per card product with a *set* of target tiers, min/max APR, fees. Your fact key (metric, entity=issuer, tier, period) forces an aggregation (median max-APR per issuer per tier) into the fetcher, which is an analytic decision hidden in a parser. Complaints are company × issue × state × product; the fact key can hold any one cut but not the cross. Call reports are per charter, not per issuer.

Recommendation: `facts` is the *interchange* table, not the *only* table. Sources whose native grain is finer keep a raw table (`tccp_products`, `complaints_agg`) and populate `facts` through a view. For call reports, make `entity = 'CERT:…'` (FDIC certificate) and roll up to issuer in a view via `dim_issuer`. This keeps the fetcher dumb and puts the aggregation choice in SQL where it can be reviewed.

**Concrete schema change** (all four fixes together):

```sql
CREATE TABLE dim_series (
  metric, entity, tier, source,          -- same composite as facts minus period
  unit, cadence, sa BOOLEAN,
  display_name, scope_note,              -- "large Y-14 filers only", "Equifax panel, not G.19 scope"
  source_url, PRIMARY KEY (metric, entity, tier, source)
);
ALTER TABLE facts ADD COLUMN entity_type VARCHAR NOT NULL;
ALTER TABLE facts DROP COLUMN unit;
```

`scope_note` matters more than it looks: G.19 revolving, NY Fed card balances (Equifax panel), Philly Fed (Y-14 large banks only), and call-report card loans are four different populations. §11 covers tier mismatch but not scope mismatch, and scope mismatch is the bigger credibility risk.

**1e. Latent bug in §5.1/§5.3: upsert never deletes.** Full re-pull + upsert means a series the source legitimately drops (TCCP loses an issuer; Philly Fed reshuffles buckets) stays in `facts` forever as "latest". Replace-by-source instead: in one transaction, `DELETE FROM facts WHERE source = ?` then insert. On fetch/validation failure the transaction never starts, so §5.3 still holds.

**1f. Add a `period_type` value for "every 4 months"** (SCE) or store as M with gaps. Trivial, but D/M/Q/H/A does not cover it.

---

## 2. The nine decisions in §12

**1. Store — DuckDB, yes, but do not commit the `.duckdb` file.** Reasons: a binary in git bloats daily; DuckDB's storage format has changed across versions and an old file may not open with a newer library; and your repo lives in OneDrive, where DuckDB file locking on cloud-synced folders is a known source of lock errors and corruption. Commit `data/facts.csv` (readable diffs) plus raw snapshots. Rebuild the DuckDB file from those at the start of every run. Side benefit: git history of facts.csv *is* vintage tracking, free (see Q7). Keep the working `.duckdb` in `.gitignore` and, locally, outside OneDrive.

**2. Full re-pull — yes, with two exceptions.**
- *CFPB complaints*: never pull rows. Use the aggregation endpoint (verified live today): `GET https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/trends?lens=overview&trend_interval=month&product=…` returns monthly bucket counts; `lens=company` and friends give the other cuts. The index has ~17.6M documents; the CSV dump is multi-GB.
- *Call reports*: do not use FFIEC bulk at all (see Q8). The FDIC API returns the full quarterly history for one bank in one call, so full re-pull is fine there.
- Everything else is kilobytes to low megabytes. Full re-pull is correct.

**3. Embed — yes.** Also emit `docs/data/facts.csv` as a sidecar *for readers*, not for the page. A "download the data" link is a portfolio feature and costs nothing.

**4. Charting — inline a small library; do not hand-roll; do not use a CDN.** The plan says "no runtime fetches" and "CDN" in the same section; those conflict when offline. Vendor a minified library into the HTML at build time. uPlot (~45 KB, native stepped lines, fast) or Chart.js (~200 KB, `stepped: true`). Plotly is nicer to hover but 3.5 MB blows the budget. Fifteen hand-rolled SVG charts with client-side filtering is a week of work you do not want to own.

**Missing from §7 entirely:** the LinkedIn post needs a PNG. Static HTML does not produce one. Add a build step that renders the two or three "post charts" with matplotlib to `docs/img/*.png` on each run. Same data, second renderer, half a day.

**5. Granularity — see §1c/1d above.** Prefix overload is fine for ids; add `entity_type`; give sub-grain sources their own raw tables.

**6. Daily cron — yes, but not at 06:00 ET.** Census retail and BEA release at 08:30 ET; G.19 at 15:00 ET; Fed charge-off and SLOOS tables land mid-afternoon. A 06:00 run makes every release a day late. Run at 18:00 ET, or twice daily (09:30 and 18:00). Cost is still nil.

Two GitHub-specific gotchas:
- Scheduled workflows in public repos are disabled after 60 days with no commits. Bot commits count. If nothing changes for 60 days (possible in a quiet stretch) the cron dies silently. Cheapest fix: always commit a `docs/last_run.json` with the timestamp. Also add `workflow_dispatch` so you can kick it manually.
- Scheduled runs can be delayed or dropped under load. Not a problem for you, just do not build anything that assumes exact timing.

**7. Revisions — "latest wins + raw snapshots" is adequate for v1, and you get vintage for free if facts.csv is in git.** But add one cheap table now: `revisions (metric, entity, tier, period_end, source, old_value, new_value, changed_at)`, written by the loader whenever an existing key's value changes (anti-join old vs new before the replace). This is your best recurring LinkedIn content: "the Fed revised July revolving credit by $X bn." Do not add a `vintage` column to `facts`; it complicates every query for a feature nobody will look at.

**8. Fetchability — corrections to §2, verified today:**

| Source | Plan says | Actually |
|---|---|---|
| FFIEC Call Reports | grade A, bulk CSV | The bulk-download page is an ASP.NET form and returns 403 to non-browser clients. **Use the FDIC BankFind API instead**: `https://api.fdic.gov/banks/financials?filters=CERT:34404&fields=REPDTE,LNCRCD,CRCRCD,DRCRCD,P3CRCD,P9CRCD,NACRCD` — no key, JSON, full quarterly history (117 quarters for WebBank), fields for card loans, charge-offs, recoveries, 30–89 and 90+ past due, nonaccrual. All the sponsor banks and the big card banks are FDIC-insured. Grade A, better than the original. |
| CFPB Complaint DB | Socrata API | Not Socrata (retired years ago). Elasticsearch-backed API at `…/consumer-complaints/search/api/v1/` with a `trends` aggregation endpoint (path has **no** trailing slash; with one it 404s). Grade A. |
| Philly Fed Large Bank | Excel | Now direct CSV: `…/Y14/2026/Q1/26Q1-CreditCardBalances.csv` and `…CreditCardOriginations.csv`. Easier. URL embeds the quarter, so the fetcher must discover the latest (try current quarter, walk back). |
| CFPB TCCP | CSV | Now xlsx: `files.consumerfinance.gov/f/documents/cfpb_tccp-data_2025-12-31.xlsx`. Latest period is H2 2025; H1 2026 is not yet posted, so your first "overdue" flag will fire on day one. Fine, that is the feature working. |
| Fed SLOOS | CSV/HTML tables | Use FRED `DRTSCLCC` (net % tightening card standards, quarterly, latest Q3 2026). One line in the FRED fetcher. |
| Fed charge-off/DQ | CSV | Also on FRED (`CORCCACBS`, `DRCCLACBS`, plus top-100 vs other variants). Same fetcher. |

Net effect: G.19 + charge-offs + SLOOS collapse into **one FRED fetcher**. Add a FRED key and a Census key to Actions secrets alongside BEA.

Two generic fetch hazards: set a real `User-Agent` with a contact email (SEC EDGAR requires it; some Fed hosts 403 the Python default), and expect GitHub's datacenter IPs to be rate-limited or blocked occasionally. Retry with backoff, and treat a 403 as a source failure, not a run failure.

**9. What is missing from §11 — see §3 below.**

---

## 3. Missing risks, over/under-engineering, build order

**Missing from §11:**

- **Scope mismatch across sources** (§1d above). Bigger than tier mismatch.
- **Seasonal adjustment mismatch** (§1b above).
- **Column-swap that passes range checks.** Philly Fed swapping "prime" and "near-prime" columns produces in-range values with no gaps; your validation passes. Two mitigations: parse Excel/CSV by header *text*, never by position; and add a **restatement detector** in the loader: if more than N% of previously loaded values for a source change, fail the source and require a manual ack. This also catches wholesale panel restatements (Philly Fed's Y-14 panel changes when banks cross the threshold).
- **Health green ≠ correct.** Definition of done says "all green for two releases". Add a `checks/golden.yaml` with a dozen headline numbers copied by hand from the source press releases (G.19 revolving for a given month, Philly Fed 90+ DQ for a given quarter) and assert the pipeline reproduces them. Cheapest credibility you can buy.
- **No tests.** Claude Code will write the parsers. Each fetcher needs a test that parses a checked-in raw sample from `data/raw/`. Without it, the first silent format change costs you the day you least expect it.
- **Unpinned dependencies.** A run six months from now on a fresh runner with new pandas/openpyxl/duckdb. Lockfile from day one.
- **Windows dev, Linux CI.** Paths, CRLF, encodings. Not fatal, but the reason to set up Actions on day one, not day ten.
- **OneDrive + DuckDB** (Q1 above).
- **Discover → Capital One** touches every source differently: FDIC CERTs may or may not merge, TCCP names change, complaint company names change, Philly Fed panel shifts. §11 mentions it once; it needs `valid_from` / `valid_to` and a `merged_into` column in `dim_issuer`.
- **Attribution.** Philly Fed and NY Fed Consumer Credit Panel data carry citation requirements. One footer line.

**Over-engineered for one person:**

- `dim_category` (PCE ↔ NAICS ↔ merchant category) is a research project, not a crosswalk. Drop it from v1; show PCE and retail sales as context series, unjoined. Two crosswalks (issuer, tier) for v1, not four.
- `dim_calendar` with typical release day and lag. A rule of "stale if latest `period_end` is older than 2× cadence" needs no calendar and catches the same stalls. Calendar is v2.
- Client-side issuer/tier filters. Fixed charts for v1; filters when there is a reader who asks.
- Eleven sources in the definition of done. With FRED collapsing three into one, v1 should be **five fetchers**: FRED, TCCP, Philly Fed, NY Fed HHDC, FDIC. That covers Growth, Pricing, Performance. Spend (BEA, Census), SCE, and complaints are v1.5, each a single evening once the pattern exists.

**Under-engineered:**

- Tests, lockfile, golden checks, restatement detector (all above).
- `dim_series` with scope notes (above). Every chart footnote should come from that table, not from the render script.
- PNG export for the post (above).
- Failure alerting: GitHub emails on a failed job by default, but a *source* failure inside a green job does not email. Have the run exit non-zero when any source is red, *after* publishing the HTML, so you get the email and the page still updates.

**Build order, revised:**

0. Copy ten headline numbers by hand into `checks/golden.yaml`. *(1 hour)*
1. Scaffold + lockfile + one FRED fetcher + loader + `facts.csv` in git + **GitHub Actions cron + Pages live**, publishing a page with one chart and the health strip. *(1 day)* CI first, so every later step is exercised where it will actually run.
2. TCCP + Philly Fed fetchers with header-based parsing and a fixture test each. `v_offered_vs_paid`. *(2–3 days, not 1–2: parsers with tests take two evenings each)*
3. NY Fed HHDC, FDIC API, `dim_issuer` with merge handling. *(3 days)*
4. Revisions table, restatement detector, PNG export. *(1 day)*
5. v1.5: BEA, Census, SCE, complaints. *(1 evening each)*
6. v2/v3 as planned.

Multiply the plan's estimates by roughly two. That is normal, not a criticism.

---

## 4. Things I did not check

- Whether the NY Fed SCE Credit Access xlsx is behind a terms click. Verify before grading A.
- BEA API row caps on underlying-detail tables (may need per-year requests).
- Whether the FDIC `CRCRCD`/`DRCRCD` fields are year-to-date (call-report schedule RI-B is YTD; de-cumulate by quarter in the fetcher if so). Check one bank by hand.
