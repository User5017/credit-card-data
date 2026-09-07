# US credit card data dashboard

A static page that refreshes itself daily from public sources and shows where card balances are growing,
what borrowers are charged, and how card credit is performing. Every number traces to a government release
and the pipeline checks a set of hand-copied headline figures on every run.

Live page: https://user5017.github.io/credit-card-data/

## How it works

    fetchers/*.py -> validate -> replace-by-source into data/facts.csv -> sql/views.sql -> render -> docs/index.html

- One fetcher per source, each producing rows in one long `facts` table (metric x entity x tier x period).
- Series metadata (unit, cadence, scope note, sanity ranges) is hand-maintained in `crosswalks/series.csv`.
- The loader validates, logs revisions to `data/revisions.csv`, and refuses wholesale restatements until acknowledged.
- `checks/golden.yaml` holds numbers copied by hand from release pages; the pipeline must reproduce them.
- GitHub Actions runs the refresh daily and commits `data/` and `docs/`. GitHub Pages serves `docs/`.

## Run it locally

    python -m uv sync
    python -m uv run pytest
    python -m uv run carddash refresh
    start docs/index.html

Design and rationale: `PLAN.md`, `design/PLAN-review.md`. Task board: `TASKS.md`. Working agreement: `CLAUDE.md`.

## Sources in v1

Federal Reserve Board (G.19 consumer credit, H.8 bank credit, charge-off and delinquency rates, SLOOS) via FRED;
CFPB Terms of Credit Card Plans; Philadelphia Fed large-bank credit card data; New York Fed Household Debt and
Credit; FDIC BankFind call-report data. See `TASKS.md` for what is loaded so far.

## License

Code: MIT. Data: as published by the respective agencies; see the notes under each chart.
