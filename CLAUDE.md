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

## Data contracts (do not change without updating PLAN.md and the tests)
- `facts` columns: metric, entity, entity_type, tier, period_end, period_type, value, source, pulled_at.
  Key: (metric, entity, tier, period_end, period_type, source). See src/carddash/schema.py.
- period_end is the LAST day of the period. Quarterly survey taken in May is dated June 30.
- Seasonal adjustment is in the metric name (`_sa` / `_nsa`) whenever the source offers both.
- unit, cadence, display name, scope note, value range, and staleness limit live in crosswalks/series.csv,
  one row per series. A fetcher may only emit series that exist there; validation rejects the rest.
- Entities: prefixed ids (`ALL_HOLDERS`, `COMBANKS_TOP100`, later `CERT:34404`, `STATE:ME`), with
  entity_type set. Views never parse prefixes.
- Sources finer than the fact key (TCCP card products, complaints) keep their own raw table and feed
  facts through a view. Aggregation choices go in SQL, not in fetchers.

## Fetcher contract (src/carddash/fetchers/<source>.py)
- `SOURCE` and `fetch(meta, raw_dir, session, pulled_at) -> DataFrame` in the facts schema.
- Pull the full history every run. Idempotent. Save the raw download under `raw_dir/latest/` before parsing.
- Parse by header text, never by column position. Fail loudly on anything unexpected.
- Never write facts.csv, health.json, or DuckDB. The loader does that.
- Before writing a parser: download the real file, print its headers and first rows, then write the parser.
  Check the raw file in under tests/fixtures/<source>/ and write a test that parses it.
- Every fetcher ships with: a fixture test, at least one golden entry in checks/golden.yaml that traces to
  a release page (not the feed), and its rows in crosswalks/series.csv.

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
- Keep this repo out of OneDrive.

## Rendering
- docs/index.html is fully self-contained: vendored uPlot (src/carddash/vendor), data embedded as JSON.
  No CDN, no runtime fetches. Must render identically from file:// and GitHub Pages.
- Chart specs live in PANELS in src/carddash/render.py. Every chart shows source, cadence, latest period,
  pull date, and the scope notes from series.csv. Step charts for period data; never interpolate.
- Palette and mark rules: thin 2px lines, hairline grid, fixed series colors, legend for 2+ series, table twin.

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
