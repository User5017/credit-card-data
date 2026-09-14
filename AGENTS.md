# AGENTS.md — carddash (credit-card-data)

**Read `CLAUDE.md` in this directory before doing anything, and follow it.** It is the working agreement for
this project regardless of which assistant is running: the start/end ritual, the scope rule, the data
contracts, the fetcher contract, and the commands. `PLAN.md` holds the design; `TASKS.md` is the board.

Nothing here replaces CLAUDE.md. These few are repeated because they are the ones that cost a day when missed.

## The ritual

- **Start:** read `TASKS.md`, run `python -m uv run pytest`, report status before doing anything else.
- **One bounded task per session**, with its pass condition written into `TASKS.md` before work starts.
- **End:** tests green, `git commit` with a plain message, `TASKS.md` updated, one-paragraph handoff.

## Scope

v1 scope only, as listed in `TASKS.md`. Do not add charts, sources, filters or features that are not on the
board — suggest them in the handoff instead. The board already carries 30-plus tasks queued past v1; this
rule is what stops the drift.

## Traps that have already cost time

- **Do not push while a scheduled run is in flight.** A push mid-run makes the runner's own `git push` a
  non-fast-forward. On 2026-09-12 that failed the job *after* tests and all fourteen sources had passed, threw
  the refresh commit away, and reset the two-green-runs count. Task 48 made the job rebase and retry, but still
  check `gh run list --limit 3` before pushing.
- **`checks/verify_releases.py` reads the LOCAL clone.** A clone even one commit behind reports
  "no bot commit found", so a green release looks like a failure. This has misled a reading twice.
  Run `git fetch` first, every time.
- **The cron is `0 22 * * *` but GitHub runs scheduled jobs late** — recent ones landed 23:54, 23:57 and 00:08.
  A run is usually not gradable until about 01:00 UTC. Only `schedule` events count; a `workflow_dispatch`
  run cannot substitute. Amber shows as "success" in the Actions UI.
- **A local `carddash render` rewrites all 77 PNGs** because of a Windows/Ubuntu freetype difference that
  ping-pongs. `git status` showing every image modified is noise — leave `docs/` to the runner.
- **The SEC refuses any User-Agent containing a URL in parentheses.** `fetchers/issuer_8k.py` carries its own
  `_sec_headers()` for this reason; the shared `make_session()` default still has the URL form. Any new
  fetcher touching sec.gov must use the URL-free User-Agent.

## GitHub

The remote is `User5017/credit-card-data`, but the active `gh` account is **SamuelJWebber**, which is the only
one holding the `workflow` scope. Editing anything under `.github/workflows/` needs that account. A move of the
repo to SamuelJWebber is planned but deferred — check with the user before attempting it.

After CLAUDE.md, read `HANDOFF.md` for what is true right now — and **rewrite HANDOFF.md before the session ends**, never append to it. It is the only channel Codex and Claude Code share.
