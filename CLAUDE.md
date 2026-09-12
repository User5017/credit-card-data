# carddash — working agreement for Claude Code sessions

Self-updating dashboard of US credit card data from public sources. One person, Claude writes most code.
Design: PLAN.md. Reasoning behind the design: design/PLAN-review.md. Task board: TASKS.md.

## Start and end of every session
- Start: read TASKS.md, run `python -m uv run pytest`, report status before doing anything.
- One bounded task per session, with the pass condition written in TASKS.md before work starts.
- End: tests green, `git commit` with a plain message, TASKS.md updated, one-paragraph handoff.

## Scope rule
v1 scope only (TASKS.md). Do not add charts, sources, filters, or features that are not on the board.
Suggest them in the handoff instead. Definition of done for v1: five fetchers green for two consecutive releases.

## Commands (Windows: prefix with `python -m`)
    uv sync                         # install (creates .venv)
    uv run pytest                   # all tests
    uv run carddash refresh         # fetch, validate, load, render everything
    uv run carddash refresh --source fred --no-render
    uv run carddash render          # rebuild docs/index.html from data/
    uv run carddash check           # every golden check against data/facts.csv
    uv run carddash health --fail-on-red
    uv run carddash issuers          # dim_issuer per charter and the unmatched-name report

## Data contracts (do not change without updating PLAN.md and the tests)
- `facts` columns: metric, entity, entity_type, tier, period_end, period_type, value, source, pulled_at.
  Key: (metric, entity, tier, period_end, period_type, source). See src/carddash/schema.py.
- period_end is the LAST day of the period. Quarterly survey taken in May is dated June 30.
- Seasonal adjustment is in the metric name (`_sa` / `_nsa`) whenever the source offers both.
- unit, cadence, display name, scope note, value range, staleness limit, and period offset live in crosswalks/series.csv,
  one row per series. A fetcher may only emit series that exist there; validation rejects the rest. period_offset shifts a
  source's own dating by whole periods (SLOOS -1: the July survey asks about the quarter that just ended), applied in the fetcher.
- Entities: prefixed ids (`ALL_HOLDERS`, `COMBANKS_TOP100`, `CERT:4297`, `FDIC_ALL_INSURED`, `STATE:ME`,
  `ISSUER:SYNCHRONY`), with entity_type set. Views never parse prefixes. An issuer is an ENTITY, never part of a
  metric name: `issuer_card_nco_rate` for every lender, keyed by entity, is what lets one chart draw four of them.
  The first version of issuer_8k used `cof_card_*` and it was renamed on 2026-09-11 before the second issuer landed.
- Sources finer than the fact key (TCCP card products, complaints) keep their own raw table and feed
  facts through a view. Aggregation choices go in SQL, not in fetchers.

## Fetcher contract (src/carddash/fetchers/<source>.py)
- `SOURCE` and `fetch(meta, raw_dir, session, pulled_at) -> DataFrame` in the facts schema.
- Pull the full history every run. Idempotent. Save the raw download under `raw_dir/latest/` before parsing.
  Two documented exceptions: the BEA NIPA flat file is 36.7 MB, so `fetchers/bea.py` saves only the lines of the series in
  series.csv (header included, the file's own format) and parses the full file in memory; the NCUA call-report zips are
  about 8 MB for each of 41 quarters, so `fetchers/ncua.py` writes a per-quarter extract (the tracked credit unions plus
  the industry sums) under `raw_dir/quarters/` and downloads only quarters without an extract plus the newest two.
  The third is issuer_8k, which keeps one document per FILING under `raw_dir/<issuer>/<newest month it states>.htm`
  (Capital One's stay at `raw_dir/months/`) and plans downloads in MONTHS: it parses what it already holds, works out
  which months are missing, and only then decides which filings can fill them, so a month already held costs nothing,
  not even an index request. Synchrony's whole history is five downloads because each exhibit carries thirteen months.
- Parse by header text, never by column position. Fail loudly on anything unexpected.
- Never write facts.csv, health.json, or DuckDB. The loader does that.
- Send the pipeline's own User-Agent (src/carddash/http.py). The CFPB's edge blocks browser-like agents sent from
  scripts and accepts ours. Never fake a browser.
  ONE EXCEPTION, issuer_8k: the SEC's edge 403s any User-Agent containing a URL in parentheses, which is exactly the
  shape of ours, with or without a contact appended (verified 2026-09-11 on every SEC host). That fetcher sends
  `carddash/<version> <CARDDASH_CONTACT>` instead, which is the format the SEC asks for. Still honest identification,
  still not a browser string. CARDDASH_CONTACT must be set locally (.env) and as an Actions secret or the source
  fails loudly; it is not defaulted, because the SEC requires a real contact.
- Before writing a parser: download the real file, print its headers and first rows, then write the parser.
  Check the raw file in under tests/fixtures/<source>/ and write a test that parses it.
- Every fetcher ships with: a fixture test, at least one golden entry in checks/golden.yaml that traces to
  a release page (not the feed), and its rows in crosswalks/series.csv. A golden entry may carry `change_from_prior: true`
  when the release states a change rather than a level (BEA).

## Loader behaviour (src/carddash/loader.py)
- Replace-by-source: on success, all prior rows for that source are dropped and the new rows inserted.
- Validation: schema, null keys, duplicates, per-series vmin/vmax, continuity (warning), expected series present.
- Restatement detector: if more than half of overlapping values moved by more than 5%, the source is
  `suspect` and NOT loaded until it is listed in checks/restatement_ack.yaml.
- Every changed value is appended to data/revisions.csv with old and new value.
- Staleness: latest period older than max_age_days (series.csv) or 2x cadence + 14 days -> `stale`.
- Statuses: ok | stale | restated | golden_mismatch (amber) | failed | suspect (red). Red keeps prior rows.

## Storage
- Committed: data/facts.csv, data/revisions.csv, data/health.json, data/raw/<source>/latest/*, docs/.
  Git history of facts.csv is the vintage log.
- Never commit a .duckdb file. DuckDB is rebuilt in memory from facts.csv each run.
- Sub-grain raw tables (TCCP card products) live at data/raw/<source>/<table>.csv, written by the fetcher next to
  latest/, committed, and loaded into the DuckDB session by render._connect so views can use them. Their facts
  come from sql/<source>_facts.sql, which the fetcher runs in memory: aggregation choices stay in SQL.
- Keep this repo out of OneDrive.

## Sources (fourteen as of 2026-09-11; issuer_8k carries three issuers)
fred, tccp, phillyfed, nyfed_hhdc, fdic, nyfed_sce (credit access, four-monthly), nyfed_sce_monthly (core survey),
bea (PCE by type of product plus household interest payments and monthly DPI, keyless flat file), census (Monthly Retail
Trade workbook), ncua (5300 call report quarterly zips, per-quarter extracts), dfa (Fed Distributional Financial Accounts
zip: consumer credit, deposits, liabilities, net worth and household counts by wealth, income and age group, quarterly),
cfpb_cct (CFPB Consumer Credit Trends CSVs: card originations, new credit lines by score tier and age, inquiry and
tightness indexes, monthly), nyfed_state (State Level Household Debt Statistics: card debt per capita and card 90+
delinquency for the 50 states, DC, Puerto Rico and the nation, annual at Q4), issuer_8k (the monthly charge-off and delinquency metrics that Capital One, Synchrony and Bread Financial each
file with an 8-K, the only monthly issuer-level readings on the page: Capital One from February 2021, Synchrony
from March 2021, Bread from July 2022. American Express from November 2022, carried as FOUR entities because it
changed the population it reports in May 2026 without restating the history: 'Card Member loans' (retired, ends
January 2026, max_age_days 36500 so it never turns stale) and 'Card balances' (current, from February 2026), for
each of its U.S. consumer and U.S. small business segments. Nothing hard-codes the changeover date; the section
label in each filing decides. See AXP_POPULATION_BREAK). Each has a typical release lag in
`RELEASE_RHYTHM` (render.py) that the health strip turns
into a 'next expected' date. After each G.19 release run `uv run python checks/verify_g19.py`; a revision that moves a
live golden shows as fred `golden_mismatch` (amber, the job still passes) until the golden is re-based.
Two sources have no golden entry because no release page states their numbers, and both say so in their module
docstring and carry structural checks instead. cfpb_cct: the CFPB's biennial report counts from issuer data and does
not reconcile, so the fetcher requires the score and age files to sum to within 6 percent of the total file every month
and the fixture test pins the dashboard's January 2026 readings. nyfed_state: the fetcher requires the area list to be
exactly the expected 53, the years to run from 2003 with no gap, and the national row to sit inside the range across
areas, and a fixture test (`check_against_quarterly`) requires its national card delinquency row to track the Quarterly
Report's own series to 0.6 points. Do not add a third without a very good reason.
nyfed_hhdc reads the other five loan types (mortgage, home equity revolving, auto, student, other) beside the card
columns, so card distress can be read against the other debts of the same households. Its sheets have four different
first quarters and the student flows start a year late; `leading_gaps` in the sheet spec is the only concession, and it
allows an empty cell before a column's first reading, never after.

## Rendering
- docs/index.html is fully self-contained: vendored uPlot (src/carddash/vendor), data embedded as JSON. That now
  includes every series, for the browser at the foot of the page, so the file is about 1.4 MB.
  No CDN, no runtime fetches. Must render identically from file:// and GitHub Pages.
- Chart specs live in PANELS in src/carddash/render.py. Every chart shows source, cadence, latest period,
  data-as-of, a status badge when its source is not OK, the scope notes from series.csv with source links, and the
  golden numbers it was checked against. Step charts for period data; never interpolate. A period that has not ended
  is never labelled as the latest (render takes `today`). A series may carry its own unit (a spread in percentage
  points on a percent chart); a chart may fill a band between two series, carry a dashed 2015-2019 benchmark of its
  first series, or let its y axis float (`y_zero: False`, credit scores). Every chart opens on the last five years
  with the full history one click away and NBER recessions shaded (RECESSIONS in render.py).
- The latest-readings block and docs/latest.txt are computed from facts in render.py (headlines). No model writes
  anything on the page.
- Palette and mark rules: thin 2px lines, hairline grid, fixed series colors, legend for 2+ series, table twin.
- One chart per panel carries `post: True` and is also written to docs/img/<id>.png by src/carddash/png.py (matplotlib Agg,
  drawn from the same payload dict as the page, steps-pre for period data, no pull date, no Software chunk) so the
  bytes change only when the data does. The 'What changed' block is keyed on health.json generated_at, never on the
  newest row of revisions.csv.

## GitHub accounts (two, on purpose)
- `User5017` owns the repo and the Pages site (https://user5017.github.io/credit-card-data/). Its CLI token has no
  `workflow` scope, so it cannot push files under `.github/workflows/`.
- `SamuelJWebber` (the user's 2020 real-name account) is an admin collaborator and the **active** `gh` account, with
  `workflow` scope. Push everything as this account. `gh auth status` shows both; `gh auth switch --user <name>` flips.
- In Git Bash, prefix `gh api` calls with `MSYS_NO_PATHCONV=1` or omit the leading slash, or the path becomes `C:/Program Files/Git/...`.
- Transfer to SamuelJWebber: wanted, deferred by the user on 2026-09-07. A transfer request was initiated and may still
  be pending in the GitHub UI. When it goes through, update the owner in: src/carddash/http.py, src/carddash/render.py,
  checks/verify_g19.py, README.md, this section, the git remote, and the OneDrive README-moved.md note. Pages URL changes.

## Verification standard
"Should work" is not done. Done means: the test passes, the command ran, and the output was shown,
with the golden number compared against its release page.
