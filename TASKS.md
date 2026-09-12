# Task board

One bounded task per session. Each has a pass condition written before work starts.
Status: `todo` | `doing` | `done YYYY-MM-DD` | `blocked (why)`.

## Handoff 2026-09-12 (tasks 45, 47 and 46: four issuers, and holes drawn as holes)

Three tasks landed, each committed separately and pushed: 45 (Synchrony and Bread join issuer_8k), 47 (a missing
period is drawn as missing), 46 (American Express, carried as two populations). 78 charts, 14 sources, 68
goldens, 383 tests green, 1376 issuer_8k rows in 34 series across four lenders. Their own sections carry the
evidence. Four things matter more than the task list.

TASK 8 IS ONE RUN FROM CLOSING AND THE RUN IS IN FLIGHT. The 2026-09-10 23:57 UTC scheduled run was GREEN with
every source ok. The next one started 2026-09-12 00:04 UTC, pushed its refresh commit (bfc12d2) with all
fourteen sources ok, and was still `in_progress` when this session ended, so `verify_releases.py` could not
grade it and still reports 1 of 2. RUN `uv run python checks/verify_releases.py` FIRST NEXT SESSION: if that run
finished green, v1 closes. Two things about the timing, because they have now misled two handoffs. The cron IS
`0 22 * * *`, 22:00 UTC, exactly as the workflow says; GitHub simply runs scheduled jobs LATE, and the last
three landed at 00:08, 23:57 and 00:04. So do not expect a run at 22:00 and do not conclude the cron changed.
A full run then takes roughly fifty minutes, so the result is not gradable until about 01:00 UTC. Pushing
during one is safe: the workflow sets `concurrency: {group: refresh, cancel-in-progress: false}`, so a push run
queues behind the scheduled run instead of cancelling it.

THREE NUMBERS WERE SILENTLY WRONG AND ALL THREE CAME OUT OF THE SAME KIND OF PLACE: a document that looks
regular and is not. Bread's quarter-end months head two columns with the SAME date, the second being 'For the
three months ended', so assigning by position put the QUARTER's loss rate on the month and every quarter-end
month in the series was wrong (September 2023 at 6.9 when the month was 6.7). Amex printed two months' write-off
rates as '2.5%(b)' and '1.7%(b)', with the footnote marker inside the VALUE cell, so both parsed as nothing and
vanished. And filtering filings on Item 7.01, which is what these disclosures normally are, silently dropped
Synchrony's newest month, because Synchrony filed July 2026 under Item 2.02 instead. Each is now pinned by a
live golden or a test. The lesson that generalises: on EDGAR the item tag is the filer's description rather than
a property of the document, and a table header is not a list of dates.

THE GAP CHECK IS WHAT FOUND THE THIRD ONE, which is the argument for keeping it strict. Task 45 added a rule that
each series is cut back to the unbroken run ending at its newest reading, because the page spans gaps and would
draw through a hole. The trim is deliberately BOUNDED: it only applies before an issuer's `first_month`, where
the source genuinely does not publish monthly, and a hole at or after it raises. Amex's two missing months were
after it, so the run refused to publish and named them. An unbounded trim would have quietly shipped a shorter
series and nobody would have known.

WHAT IS WORTH DOING NEXT, in the order I would do it:
1. Close v1 (verify_releases), assuming the run that was in flight finished green.
2. A third thesis note, or an edit to the second. This is now the biggest gap between what the page knows and
   what the note says. Four issuers spanning the whole credit spectrum turn at the same time and never change
   ORDER over five years (July 2026 30+ delinquency: Amex 1.10, Capital One 3.48, Synchrony 4.20, Bread 5.35),
   which is the strongest evidence yet for leg 1 and matches what the state map said in task 43. The note
   mentions none of it.
3. Not scheduled, suggested: `issuer_card_nco_rate` now exists for four issuers on one metric, so a view that
   ranks lenders at a given month, or charts the SPREAD between the best and worst, is cheap and would say the
   thing the four-line chart only implies.
4. Not scheduled, suggested: Amex's old basis stops at January 2026 rather than March because the planner works
   in months and the filing covering those two months was already redundant by the time the walk reached it. The
   overlap is preserved in the fixtures and asserted in a test, but the PAGE shows two lines that abut without
   overlapping. Planning per entity rather than per month would recover it; it is not obviously worth it.

Costs accepted: data/raw/issuer_8k is 4.8 MB (Capital One 1.6, Bread 1.2, Amex 1.4, Synchrony 0.44, plus the
index cache). The cache is what keeps the nightly run at a handful of SEC requests instead of several hundred,
and the month-driven planner is what keeps Synchrony's whole history at five downloads.

## Handoff 2026-09-11 (late evening, task 45: three issuers on the monthly chart)

One task this session, committed as e4c33fe and pushed: `issuer_8k` went from Capital One alone to Capital One,
Synchrony and Bread Financial, and the Capital-One-specific metric names became entity-keyed ones. 1016 rows in
18 series, 77 charts, 14 sources, 65 goldens, 381 tests green. The v1.14 section carries the evidence. Four
things matter more than the task row.

TASK 8 IS STILL 1 OF 2 AND THIS SESSION DID NOT MOVE IT. The second scheduled run was due about 22:00 UTC on
2026-09-11 and had not happened when this session ended (20:13 UTC). Everything above is push-triggered and does
not count. RUN `uv run python checks/verify_releases.py` FIRST NEXT SESSION and close v1 if it passes. Note that
this session's push changed a source that the scheduled run will exercise, so the 2026-09-11 run is the first to
carry three issuers in issuer_8k.

TWO NUMBERS WERE WRONG ON THE WAY IN AND BOTH LOADED SILENTLY. Bread's quarter-end months put the QUARTER's loss
rate on the month, because its loss table heads two columns with the same date and the second one is 'For the
three months ended'; September 2023 went in at 6.9 when the month was 6.7, and every quarter-end month was wrong
the same way. Separately, filtering filings on Item 7.01 (which is what these disclosures normally are) dropped
Synchrony's newest month entirely, because Synchrony filed July 2026 under Item 2.02: no error, the source would
simply have sat a month behind for ever. Both are fixed, both have a live golden or a test pinning them, and
both are written up in the v1.14 section. If you take one lesson: on EDGAR the item tag is the filer's
description of a filing, not a property of the document.

AMERICAN EXPRESS IS DONE EXCEPT FOR ONE DECISION, which is now task 46. Its parser, both label vocabularies, two
fixtures and five tests are checked in and passing; it is deliberately absent from `ISSUERS` and has no rows in
series.csv. The blocker is real and is not a parsing problem: Amex changed the population it reports in May 2026
(from 'Card Member loans' to 'Card balances', which adds pay-in-full charge-card balances), restated only two
months, and the same month reads $97.5bn on one basis and $110.8bn on the other. Loading it as one series would
put a fake 10 percent step in the balances and a fake improvement in the delinquency rate. Enabling it means
deciding how to carry two populations under one issuer; `test_the_amex_population_break_is_real_and_is_why_it_is_not_loaded`
fails if the two bases ever reconcile, which is the signal that the decision has been made for you.

WHAT IS WORTH DOING NEXT, in the order I would do it:
1. Close v1 (verify_releases), assuming the 22:00 UTC run is green.
2. A third thesis note, or an edit to the second. The three-issuer chart is new evidence for leg 1 and the note
   does not mention it: the ORDER of the three lenders never changes over five years and all three turn at the
   same time, which is what a common national cause looks like and matches what the state map said in task 43.
   Bread at 6.80 percent net loss against Capital One at 4.12 and the industry at 3.82 is the clearest picture
   on the page yet of distress sitting with the weakest borrowers.
3. Task 46, the Amex decision.
4. Still not scheduled, still suggested: the page spans gaps on every chart except nco_by_issuer, and the PNGs
   never do, so the two disagree wherever a series has a hole. The issuer_8k trim now means its own series never
   have holes, which removes one source of that disagreement but not the others.

Costs accepted this session: data/raw/issuer_8k is 3.6 MB (was 1.8), of which Bread is 1.2 MB because its
exhibits carry one month each and Synchrony is 440 KB because its exhibits carry thirteen. The index cache is
what keeps the nightly run at a handful of SEC requests rather than several hundred.

## Handoff 2026-09-11 (end of a long session: tasks 40 to 44)

Five tasks landed, each committed separately and each green on the runner: 40 cursor sync, 41 the value audit and
its fix, 42/31 Capital One's monthly 8-K metrics, 43 the state tile map, 44 the second thesis note. Their own
sections carry the evidence. Four things matter more than the task list.

THE PAGE WAS PUBLISHING A WRONG NUMBER and had been for as long as the issuer chart existed: a 182.9 percent
annualised charge-off rate for Bank of America, with -5,969 percent reachable on charters the page does not draw.
It was a collapsed denominator, it is fixed at source with a materiality floor, and the class of bug is now closed
by VIEW_BOUNDS plus a test that forces every new derived field to be either bounded or exempted with a reason. If
you read one section of this board, read task 41.

SEC EDGAR IS NOT BLOCKED ANY MORE. The 2026-09-08 note said never to plan EDGAR fetches from this machine; that
was an IP-reputation block on the retired ViaSat address and the machine is now on a different one. Task 31 had
been sitting blocked for a reason that had expired. Before believing a 403 is policy, check the public IP.

TASK 8 IS STILL AT 1 OF 2 and this session did not move it. Every run above is push-triggered and
`verify_releases.py` counts only scheduled ones. The second scheduled run is due about 22:00 UTC on 2026-09-11,
which had not happened when this session ended (17:32 UTC). RUN `uv run python checks/verify_releases.py` FIRST
NEXT SESSION and close v1 if it passes. Note that the 2026-09-11 scheduled run will be the first to carry
fourteen sources, so issuer_8k has not yet been through a scheduled run, only push-triggered ones.

WHAT IS WORTH DOING NEXT, in the order I would do it:
1. Close v1 (verify_releases), assuming the scheduled run is green.
2. Synchrony, Bread Financial and American Express monthly 8-Ks. The route is proven and the fetcher is built; each
   needs its own parser because the exhibit layout is per issuer, and whether they still file monthly should be
   checked before promising them. Two issuers would turn task 42's single-lender caveat into an industry read.
3. The thesis note's new open item: nothing reconciles Capital One's managed domestic card book (4.12 percent in
   July) with the FDIC's charter figure for the same issuer (4.83 percent at 2026 Q2). Both are right; the monthly
   series is a good read on direction and a bad one on level, and that limits what it can be used for.
4. Not scheduled, suggested: the page still spans gaps on every chart except nco_by_issuer, and the PNGs never do,
   so the two disagree wherever a series has a hole. Auditing which series actually have holes would say whether
   span_gaps should flip to False by default.

Known cost accepted this session: data/raw/issuer_8k is 1.8 MB (66 exhibits at about 24 KB plus 97 cached filing
indexes). The cache is what keeps the nightly run at two or three SEC requests instead of ninety-seven.

## Handoff 2026-09-11 (late, after the cursor work)

Task 40 is done and is the whole session: the 75 panel charts now share one cursor, so hovering any
chart marks the same date on every other chart and each legend reads its own series at that date.
The evidence is in the task row. Two things in it are worth knowing beyond the feature itself. First,
the guard: a chart only answers for dates it actually holds and is currently showing, so hovering
weekly jobless claims at its newest point leaves 9 of 75 charts reading out and CLEARS the other 66
instead of letting uPlot clamp them to their endpoints and quote a value for a date they do not have.
Second, a correctness fix that sync forced into the open: on step charts the cursor now lands on the
period that CONTAINS the hovered date rather than the nearest period end, which was already wrong on
a single chart (the stepped line is drawn with align -1) and is glaring across cadences, where the
nearest annual period end to 2025-06-14 is 2024-12-31 and the containing one is 2025-12-31.

Task 8 is still at 1 of 2. The 2026-09-10 scheduled run was green; the second is due about 22:00 UTC
on 2026-09-11, which had not happened when this session ran (it was 15:16 UTC). Run
`uv run python checks/verify_releases.py` first next session and close v1 if it passes. This session's
push is a push-triggered run and does not count toward the scheduled pair.

The thesis rewrite is STILL the next substantive task and has still not been started; nothing about it
changed this session. See the previous handoff below for why three v1.8 findings force it.

Also corrected this session: the PNG note in the open items claimed the runner/Windows byte difference
settles once. It does not, it ping-pongs on every machine alternation, so all 75 images show as
modified after any local render and that is noise. Check against the last local render, not HEAD.

Suggested, NOT on the board (scope rule):
- The single best remaining data source is task 31, the monthly issuer 8-K credit metrics, and it is
  marked blocked for a reason that is about this machine rather than about the source. Scouted
  2026-09-11 (through a proxy, NOT from the dev machine, whose ISP is still 403 on every SEC host, so
  the blocker itself is unchanged): Capital One CIK 0000927628 files these as Item 7.01 on a monthly
  rhythm (2026-08-17, 2026-07-21, 2026-06-15, 2026-05-15), self-filed, and the exhibit in the
  2026-08-17 filing is `ex991july2026creditmetrics.htm`, a 24 KB HTML table, so it parses by header
  text like every other fetcher here. The point of it: every issuer-level number on the page today is
  quarterly and can be four and a half months old (Y-14 lands about 3.5 months after quarter end, FDIC
  55 to 58 days), and this lands about two weeks after month end. Scope one session to Capital One
  only and capture the fixture from a runner job; check whether Synchrony and Bread still file the same
  way before promising four issuers.
- The state data (106 series, 53 areas) is drawn as two range bands and a few named lines, which is the
  worst form-to-data match on the page. A tile-grid cartogram with a year slider shows all 53 at once
  and needs no new dependency (SVG rects, no geometry file). The cost is that it is a new mark type and
  so sits outside the PANELS -> uPlot -> png.py pipeline.

## Handoff 2026-09-11 (evening)

v1.8 (tasks 36, 37 and 38) is done on top of v1.7: the NY Fed workbook we already download is now read in full breadth
(68 series from 25: every loan type, debt by age, three by-age transition sheets) and six FRED cross-asset
one-liners were added, and a thirteenth source was added for card debt and delinquency by state, giving 75 charts, 54 goldens, 360 tests and thirteen sources ok, and the suite was taking over ten minutes so the page fixture now renders once per session (task 39, under three minutes). The v1.8 findings bullets hold
the numbers, and three of them change what the thesis note should say: card distress is far worse than every other
debt of the same households (leg 1), and the over-70 puzzle in section 7 is card-specific rather than a
whole-balance-sheet problem, which the note currently lists as unexplained. Rewriting design/thesis-2026-09-08.html
as a dated second note is the obvious next task and has not been started; the three falsification thresholds have
not moved and should not be changed without rewriting the note. Task 8 is at 1 of 2 green scheduled runs (the
2026-09-10 run was green); the 2026-09-11 run at 22:00 UTC is the second, so run
`uv run python checks/verify_releases.py` first next session and close v1 if it passes. Tasks 30 and 31 are now
documented as unavailable with the evidence in the v1.8 section: do not re-scout SEC, FFIEC CDR or the Philly Fed
explorer. The Z.1 for 2026 Q2 lands 2026-09-11 and the DFA about a week later, which will be the first DFA
revision: re-base the dfa 2023 golden if it turns amber.

## v1: five fetchers, three panels, self-updating

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 0 | Golden numbers for FRED series | checks/golden.yaml has ≥8 entries traced to the G.19 release page; test_golden passes on fixtures | done 2026-09-07 |
| 1 | Walking skeleton: scaffold, FRED fetcher, loader, health, render, CI, Pages | Pages URL serves the page with a green health strip and Growth/Pricing/Performance charts; Actions run green; `pytest` green; golden checks pass live | done 2026-09-07 (first Actions run 34149165528 green; bot commit c76ae79; live page stamped from CI) |
| 2 | CFPB TCCP fetcher (semiannual xlsx, per card product) | raw table `tccp_products` + facts view for median max-APR by issuer tier; fixture test; golden entry from the CFPB page; first cross-source chart `v_offered_vs_paid` skeleton | done 2026-09-07 (six workbooks H1 2023 to H2 2025, 82 facts rows in 15 series; `data/raw/tccp/tccp_products.csv` 3,781 products; golden: 15 issuers above 30% in H1 2023 matches the CFPB's Feb 2024 report; `offered_vs_paid` chart live in Pricing; first Actions run 34152591538 green from the runner, bot commit c596df7) |
| 3 | Philly Fed large-bank card CSV fetcher | balances, utilization, delinquency loaded (whole panel; the Fed splits only purchase volume, original credit limit and new-account shares by score bucket) with the bucket-to-tier mapping documented in crosswalks/tiers.csv; fixture test; golden entry | done 2026-09-07 (both 26Q1 CSVs, 2012Q3 to 2026Q1, 2,804 facts rows in 51 series; eight golden numbers from the Q1 2026 Insights Report reproduce; live refresh ok, page rendered; first Actions run 34158322607 green from the runner, bot commit f6d350f) |
| 4 | NY Fed Household Debt & Credit xlsx fetcher | card balances and delinquency transitions loaded; quarter discovery works when a new file appears; fixture test; golden entry | done 2026-09-07 (2026Q2 workbook, 1,200 facts rows in 12 series: card balances, accounts, limits, 90+ share, 30+ and 90+ delinquency flows 2003Q1 to 2026Q2, plus the 90+ flow by six age groups from 2000Q1; three golden numbers from the 2026-08-11 press release reproduce; live refresh ok, page rendered; first Actions run 34164118850 green from the runner, bot commit 0faec59) |
| 5 | FDIC BankFind API fetcher (no key; scouting in design/handoff-2026-09-07.html §2) | crosswalks/issuers.csv seeded with the handoff cert list (24 active card banks and sponsor banks plus the merged-out certs with merger dates: 5649 Discover -> 4297 on 2025-05-18, 33954 -> 4297 on 2022-10-03, 23702 -> 628 on 2019-05-18, 35328 -> 27471 on 2018-04-01, 34351 -> 32188 on 2024-06-01), so the roll-up means something; per cert, entity `CERT:<n>`: card loans `LNCRCD`, 30-89 past due `P3CRCD`, noncurrent = `P9CRCD` + `NACRCD`, and the FDIC's own QUARTERLY flows `DRCRCDQ`/`CRCRCDQ`/`NTCRCDQ` (never de-cumulate the YTD `DRCRCD`/`CRCRCD`/`NTCRCD` by hand; it breaks in merger quarters); an all-insured-institutions entity `FDIC_ALL_INSURED` from `agg_by=REPDTE` with `BKCLASS` NC (noninsured) and OI (insured foreign branches) excluded; issuer roll-up view sums acquirer plus acquired by period; fetcher asserts every requested field key is present and every cert returned rows (the API answers 200 with zero rows or silently drops unknown fields); test that YTD minus prior YTD equals the `*Q` field for the fixture bank in a non-merger year; fixture test; golden from the Q2 2026 QBP PDF, Table II-A, row Credit cards ($ millions): 2Q 2025 1,141,110 live, 1Q 2026 1,161,264 and 2Q 2026 1,189,601 fixture-only, `origin_url` = the PDF with table, row and column quoted; `max_age_days` 200 | done 2026-09-08 (29 charters and the industry total, 1984 Q1 to 2026 Q2, 23,642 facts rows in 180 series; issuers.csv seeded with the 24 active charters and the 5 merged-out ones with their merger dates; `v_fdic_issuer` roll-up view; the three QBP Table II-A numbers reproduce to the million, 2Q 2025 checked live; year-to-date minus prior year-to-date equals the quarterly field for Capital One in 2024 and not in the 2022 Q4 merger quarter; live refresh ok, page rendered; 59 fixture tests; first Actions run 34189283165 green from the runner, bot commit 6911c11) |
| 6 | `dim_issuer` with merger handling | valid_from/valid_to/merged_into populated for the top card banks and sponsor banks; unmatched-name report in health | done 2026-09-08 (issuers.csv: 30 charters with the FDIC established date as valid_from, merger date and acquirer for the five merged-out charters, aliases for the TCCP spellings; TD Bank, N.A. (cert 18409) added, 186 FDIC series; `src/carddash/issuers.py` = dim_issuer, `dim_issuer` view, `carddash issuers`; the fdic fetcher downloads /institutions every run and fails if ACTIVE, ESTYMD, ENDEFYMD or NEWCERT disagree with the crosswalk; the unmatched-name report is written into the tccp and fdic health messages after every refresh (9 of 28 top-25 TCCP names unmatched in H2 2025, all 30 FDIC names match); 37 new tests, 205 total; live refresh ok, page rendered; first Actions run 34190842424 green from the runner, bot commit fcb4ecb) |
| 7 | Revisions block and PNG export (critique in design/handoff-2026-09-07.html §4) | tests/fixtures carry a revisions.csv with two runs; "What changed" lists only the latest run (keyed on the health file's generated_at, not the newest row in the file), plus new periods per source from rows whose pulled_at equals the run; rel_change display is capped when the old value is 0; revisions.csv is copied under docs/ and linked; one chart spec per panel carries `post: True`, and `carddash render` writes docs/img/<chart>.png for those three via matplotlib (Agg backend, `drawstyle="steps-pre"`, drawn from the same chart payload dict as the page); PNG bytes are identical across two renders of unchanged data (no pull date in the image, `savefig(metadata={"Software": None})`) | done 2026-09-08 (tests/fixtures/revisions.csv carries two runs; the block is keyed on health.json generated_at, counts every revision of that run, lists those above 0.1% plus any from a zero old value ('from 0' instead of a percentage), lists new periods per source and cadence beyond the previously loaded frontier, and links docs/data/revisions.csv; `post: True` on revolving_level, card_apr and card_nco; `src/carddash/png.py` draws docs/img/<id>.png from the chart payload with matplotlib Agg, steps-pre, 1200x675, no pull date, no Software chunk, and a test renders twice and compares bytes; matplotlib added to pyproject and uv.lock; 209 tests; first Actions run 34191419090 green from the runner, bot commit 4c9d20f, which re-rendered the three PNGs once with the runner's fonts) |
| 7b | Chart correctness before the gate (critique findings 1 and 2) | Offered vs paid plots the TCCP purchase-APR tier medians already in facts (superprime, 620-719, <=619) as a band against the G.19 rate paid, instead of the median of each product's maximum APR; the spread stops or dashes after the last offered period instead of carrying H2 2025 into 2026 Q2, and its caption says percentage points, not percent; SLOOS is dated to the quarter the survey asks about (July survey -> June 30) through a per-series period offset in crosswalks/series.csv applied in the FRED fetcher, and render never labels a period that has not ended as latest; tests cover the offset and the spread cut-off; golden checks still pass; page re-rendered and inspected | done 2026-09-08 (offered vs paid now draws the three TCCP tier medians for scores 619 or less, 620 to 719 and 720 and up as a filled band against the G.19 assessed-interest rate, with the spread redefined as the 620-719 median minus paid, dashed, labelled in percentage points, and cut off at the last offered period in `v_offered_vs_paid`; `period_offset` column in series.csv applied by the FRED fetcher through `schema.shift_period`, SLOOS at -1 so the July 2026 survey is 2026 Q2; render takes `today` and never labels a period that has not ended as latest, on chart footers and the health strip; SLOOS rows re-loaded under the new dating without spurious revisions; 24 golden checks pass; 222 tests; page re-rendered and inspected in headless Chrome; first Actions run 34191919986 green from the runner, bot commit 77ccc3f) |
| 8 | Two green releases | Health strip all green across two consecutive scheduled releases of every source | doing (all five push-triggered runs of 2026-09-08 were green with every source ok, ending with 7b's run; the one scheduled run so far, 34172365036 at 2026-09-08 00:08 UTC, was green but predates the FDIC source, so the count starts with the next two scheduled runs, due about 22:00 UTC on 2026-09-08 and 2026-09-09; check with `uv run python checks/verify_releases.py`, which prints PASS when the newest two scheduled runs are all green. Update 2026-09-10: the scheduled runs of 2026-09-09 and 2026-09-10 were amber, fred `golden_mismatch`, because the 2026-09-08 G.19 release revised Q1 2026 revolving from 1338.0 to 1338.2; the workflow still showed 'success' because only red fails it. Goldens and fixtures were re-based on the 2026-09-08 release on 2026-09-10, so the count restarts with the scheduled runs due about 22:00 UTC on 2026-09-10 and 2026-09-11. Update 2026-09-11: the 2026-09-10 scheduled run (34544421985, started 23:57 UTC) was GREEN with all ten sources of that vintage ok, so the count is 1 of 2; the 2026-09-11 run is the second, and it is the first scheduled run to carry the twelve v1.7 sources) |

## v1.1: readable on its own (2026-09-08, the user's call after the adversarial review of the v1 page)

The page must work for someone who looks at nothing else for the credit health of the economy. Scope rule for this
section: charts only from series already in facts, every number on the page computed from those series, no model.

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 9 | Charts from the loaded-but-unshown sources | Growth: card loans by issuer (FDIC roll-up, six largest). Performance: delinquency triangulation (NY Fed 90+ share, Fed 30+, Y-14 90+, FDIC noncurrent share) with the charged-off-balances footnote; net charge-off rate by issuer, annualized the FDIC's way (`v_fdic_rates`, reproduces IDNTCRDQR for Capital One). New Borrowers panel: NY Fed 90+ flow by age, Y-14 payment behavior (minimum, partial, full), utilization, origination credit-score percentiles (floating y axis). Tests on every payload | done 2026-09-08 (15 charts in four panels; `v_fdic_rates` and `v_tccp_offered_range` views) |
| 10 | Offered vs paid band fixed | The band is the range across the three tier medians (min to max per half-year), the 620-719 median a line inside it, since the sub-620 median is usually the lowest; note explains why | done 2026-09-08 |
| 11 | Reading aids on every chart | Default view last 5 years with a full-history button; NBER recession shading (one list in render.py, shared with the PNGs); dashed 2015-2019 average of the first series on the rate charts | done 2026-09-08 |
| 12 | Trust and status on every card | Data-as-of from the plotted rows' pulled_at (source-level fallback for roll-ups); status badge and last fetch attempt when the source is not OK; per-series footer lines when sources or cadences differ; source links in the notes; "Checked against the release" lists the golden numbers and their release pages; health strip moved below the charts with a one-line status at the top | done 2026-09-08 |
| 13 | Latest readings block | Eight headline series computed from facts: latest value, change on the year, 2015-2019 average for rates, highest/lowest since at least three years or on record; also written to docs/latest.txt as a post draft; no model | done 2026-09-08 |
| 14 | Palette | Light-mode series colors at 4.3:1 or better against the card surface, eight series colors, band and recession fills, dark variants | done 2026-09-08 |
| 15 | Five card banks from the crosswalk report | Credit One (25620), Merrick (34519), Fifth Third (6672), Regions (12368), Stride (4091) as charters with established dates from /institutions; the report now lists only the four credit unions | done 2026-09-08 (35 charters, 216 FDIC series) |

## v1.2: context (2026-09-08, the analysis cycle)

Goal: the page explains, not just describes. Same scope rule as v1.1, plus macro series that are one FRED row each.

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 16 | Macro context series | Twelve FRED series verified before writing the crosswalk: TDSP, CDSP, DPI, PSAVERT, CPIAUCSL, UNRATE, UMCSENT, MPRIME, BRMCC01, CORCACBS, DRCLACBS, NONREVSL. Fixtures for all twelve | done 2026-09-08 (26 fred series) |
| 17 | Derived views | `v_y14_flows` (payment rate, revolving share, per-account), `v_hhdc_per_account`, `v_card_burden` (share of disposable income, real balances), `v_apr_spread` (card APR minus prime). All from series already loaded | done 2026-09-08 |
| 18 | Charts that answer the review's questions | Growth: debt as a share of income, nominal vs real. Pricing: advertised rate on the APR chart, spread over prime. Performance: card vs all consumer credit, charge-offs against unemployment. Borrowers: payment rate and revolving share, per-account credit. New Context panel: debt service ratios, saving rate, sentiment, revolving vs nonrevolving | done 2026-09-08 (26 charts in 5 panels, 231 tests) |

## v1.3: the demand side and a thesis (2026-09-08)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 19 | NY Fed Credit Access Survey | Fetcher for the four-monthly credit access workbook; 52 series over seven entities (all respondents, three self-reported credit score bands, three age bands); waves must step by four months; the 'N/A' not-asked marker tolerated only as a leading gap; golden from the survey page's own sentence (any-credit rejection 16.1% June 2026, 23.1% June 2025); fixture test | done 2026-09-08 (2,006 rows from October 2013, Access panel of six charts, 263 tests) |
| 20 | Origination mix charts | Y-14 series already loaded: sub-660 share of new accounts against sub-660 share of new credit line dollars, and median original credit line by score band | done 2026-09-08 |
| 21 | Written thesis | A dated analysis note in design/ that states a position, cites only loaded data, and names what would falsify it | done 2026-09-08 (design/thesis-2026-09-08.html) |

## v1.4: the thesis watch (2026-09-08)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 22 | Thesis watch | The three falsification thresholds named in design/thesis-2026-09-08.html are evaluated from loaded data on every render, shown on the page with a holds/broken state and printed into docs/latest.txt; a test whose series has no data reads 'no data' rather than passing; a test can be seen to break | done 2026-09-08 (265 tests) |

## v1.5a: browse every series (2026-09-08)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 23 | Series browser | Every series in facts is reachable from the page: a searchable table with a source filter and a 'no chart' filter, click a row to plot it with its scope note and source link, deep-linkable by `#series=<key>`. Embedded, so no runtime fetch: dates are shared per source and cadence and a series stores a start index plus values. Tests reconstruct a series from the encoding and assert the browser covers exactly the series in facts | done 2026-09-08 (372 series, 314 with no chart, payload 0.41 MB, page 1.39 MB, 267 tests) |

## v1.6: what was left (2026-09-08, the user's "just do all of this" after the evening assessment)

Scouting for every row was verified live on 2026-09-08 before work started (FRED ids, file URLs, release-page numbers).

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 24 | FRED one-liners: SLOOS card demand, G.19 by holder | DEMCC plus the large/other splits (period offset -1 like standards) and REVOLNDI/REVOLNCU/REVOLNFC (NSA, billions) in series.csv with fixtures; goldens from the July 2026 SLOOS Table 1 (net = stronger minus weaker: 2.2 all, 0.0 large, 3.8 other; standards 6.7) and the G.19 holder table (2025: 1,219.7 / 88.3 / 16.3); charts: revolving credit by holder, holder share (`v_holder_share`), SLOOS standards and demand on one chart, demand by bank size | done 2026-09-08 (32 fred series, 35 charts, 33 golden checks pass live) |
| 25 | Page fixes for sharing and reading | Open Graph, Twitter and description meta with the revolving-credit PNG as the preview image; an inline SVG favicon; the thesis note copied under docs/ so the link renders on Pages instead of showing source on GitHub; a sticky one-line nav with the six panels, browser and health; every latest reading and thesis test links to its chart; the latest value printed at the end of each line; a copy-link button on every card; a PNG for every chart; browser overlay of a second series and a per-series CSV download; a 'next expected' release line per source in the health strip from a per-source typical lag; width media queries for phones; a dark/light toggle that remembers itself | done 2026-09-08 (35 PNGs, thesis served at docs/design/, release rhythm in `RELEASE_RHYTHM` keyed on one series per source with a one-week grace before 'has not appeared', end-of-line labels as HTML spans in a 62 px right margin, browser compare puts a second unit on a right axis, CSV is a Blob link, 279 tests) |
| 26 | NY Fed monthly SCE: delinquency expectations | Fetcher for frbny-sce-data.xlsx (1.2 MB, direct GET, no date in the name): mean probability of missing a minimum debt payment in the next three months, overall and by age, education, income and numeracy; credit availability year-ago and year-ahead shares; household financial situation shares; monthly from June 2013; golden from the SCE release page's own sentence; Performance and Context charts | done 2026-09-08 (`fetchers/nyfed_sce_monthly.py`, 36 series, 5,724 rows from June 2013; goldens 12.0 (July, live) and 13.2 (August) from the 2026-09-08 press release; four charts plus a headline reading; `v_sce_sums`; 7 sources) |
| 27 | HHDC sheets already downloaded | Page 5 (new and closed accounts, inquiries), Page 11 (balance by delinquency status), Page 17 (bankruptcies, foreclosures), Page 18 (third-party collections) loaded from the existing workbook, all-debt scope stated in the notes; goldens from the press release or the report PDF; Access and Context charts | done 2026-09-08 (13 series from four sheets, 25 HHDC series; goldens: 95.3% current from the press release, 137k bankruptcies and 55k foreclosures from the report's page 2; four charts, 43 on the page) |
| 28 | Spend panel: BEA PCE and Census retail | BEA NipaDataM.txt (keyless, 36.7 MB: snapshot only the kept series, a documented deviation) for total PCE, goods, services and the card-heavy lines; Census mrtssales92-present.xlsx (keyless) for retail categories; goldens from the BEA and Census press releases; a Spend panel with 12-month growth | done 2026-09-08 (`fetchers/bea.py` 8 series 1959-2026 as a 165 KB subset snapshot, `fetchers/census.py` 12 series 1992-2026 SA and NSA; goldens: BEA monthly changes 36.3 / -49.9 / 86.2 / 77.4 via the new `change_from_prior` golden option, Census May and June NSA levels from the advance release's Table 1; five Spend charts incl. Y-14 purchase volume vs retail sales and the nonstore share (`v_retail_share`); 9 sources, 48 charts, 45 goldens) |
| 29 | NCUA credit unions | Quarterly call-report zips (direct links) for the four TCCP top-25 credit unions: card loans and delinquency; depends on zip size being workable for a daily full pull | done 2026-09-10 (`fetchers/ncua.py`; the zips are about 8 MB each for 41 quarters, so the raw snapshot is a per-quarter extract under data/raw/ncua/quarters/ (41 files, 172 KB, 2016 Q1 to 2026 Q1) and a run downloads only quarters without an extract plus the newest two; the second documented exception in CLAUDE.md. 24 series: the industry sum over federally insured credit unions (FOICU CU_TYPE 1 and 2) plus Navy Federal, PenFed, BECU and America First, each with card loans, year-to-date charge-offs and recoveries, 60+ day delinquent balances and, per credit union, the reported card interest rate; 984 rows. `v_ncua_rates` de-cumulates the year-to-date accounts to a quarterly annualized net charge-off rate and the 60+ day share. Goldens 86.0 (2026 Q1, fixture-only) and 82.0 (2023 Q4, live) from the NCUA Quarterly Credit Union Data Summary. Three charts: card loans at credit unions (Growth), credit union rates against the bank APR (Pricing; Navy Federal sits on the 18 percent cap), credit union NCO rate against banks (Performance). 10 sources, 51 charts, 47 goldens. Built 2026-09-08 in the session that lost DNS mid-task; first live pull and commit 2026-09-10) |
| 30 | FDIC realized card yield | Find the card interest-and-fee income field in the BankFind financials (Call Report RIAD B485); if it exists, a per-issuer realized yield view and an offered-vs-earned chart | todo |
| 31 | Issuer monthly 8-K credit metrics | Capital One's own Exhibit 99.1 monthly charge-off and delinquency metrics loaded as a source, with a golden traced to the filed exhibit, a fixture test per layout, and charts against the industry | done 2026-09-11, Capital One only (see v1.11 below; the blocker was this machine's IP, not the source) |

## v1.7: what the thesis still needs (2026-09-10 evening, the user's "what more do I need to prove this out, go get it")

Sources scouted live on 2026-09-10 before work started. Rejected with reasons: Philly Fed Consumer Credit Explorer (no
downloads, vendor restriction); FDIC realized card yield (task 30: RIAD B485 is not in the BankFind financials API, see
the open item); CFPB report figures as goldens for the trends files (issuer data, does not reconcile to the panel).

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 32 | Fed Distributional Financial Accounts (leg 2 and section 7 of the thesis: who owes the debt, what stands behind it, the over-70s) | `fetchers/dfa.py` loads the dfa.zip detail files for wealth, income and age: consumer credit, deposits, liabilities, net worth and household count per group plus the sum (80 series, 1989 Q3 on); the three splits must agree on every total; goldens from the Z.1 release PDF table S14.b (consumer credit 2023 live, 2024 and 2025 and 2025 deposits fixture-only); `v_dfa_shares`; a Distribution panel of five charts; a headline reading | done 2026-09-11 (`fetchers/dfa.py`, 80 series 1989 Q3 to 2026 Q1, 11,760 rows; the three splits agree on every total to 0.02 percent; four goldens from the Z.1 PDF table S14.b reproduce to the tenth of a billion; Distribution panel of five charts; reading 'Consumer credit owed by the bottom half'; max_age_days 200 because the DFA lands about 80 days after quarter end) |
| 33 | CFPB Consumer Credit Trends (leg 5 for all lenders, and the vintage story behind leg 1) | `fetchers/cfpb_cct.py` loads six CSVs: cards opened, new credit lines total and by score tier and age, inquiry index, tightness index (both indexes, January 2010 = 100; 26 series, monthly from 2005-2007); group files must sum to within 6 percent of the total; documented no-golden exception; `v_cct_shares`; six Access charts including the all-lender below-prime share against the Y-14's; a headline reading | done 2026-09-11 (`fetchers/cfpb_cct.py`, 26 series, 6,206 rows, originations to January 2026, inquiries to May 2026; both indexes are January 2010 = 100, the tightness one included, which the first live run caught as 17 values above a vmax of 100; six Access charts; reading 'New credit cards opened') |
| 34 | BEA household interest payments (leg 4 in dollars) | B069RC and A067RC added to the bea subset; `v_interest_burden` gives the effective rate on all consumer credit (interest over G.19 total) and the interest share of monthly DPI; a Pricing chart against the card APR and a Context chart against the debt service ratio | done 2026-09-11 (B069RC and A067RC in the subset, 10 bea series; `v_interest_burden`; Pricing chart `effective_rate`, Context chart `interest_share_income`) |
| 35 | Weekly jobless claims (the thesis watch a month earlier) | ICSA and CCSA as fred series with fixtures, a Context chart and a headline reading | done 2026-09-11 (ICSA and CCSA, 34 fred series; Context chart `jobless_claims`; reading 'Initial jobless claims') |

## v1.8: comparison (2026-09-11, the user's "any other data sources we can add?")

Scouted live before work started. Confirmed unavailable, with the evidence, so nobody re-scouts them: SEC EDGAR is
still 403 on every host from this ISP (browse-edgar, data.sec.gov, efts.sec.gov, the Archives), so task 31 stays
blocked; the FFIEC CDR bulk call-report download is an ASPX form behind a login, so RIAD B485 (task 30) has no
keyless route; the Philly Fed Consumer Credit Explorer publishes no files at all by vendor restriction. The Y-14
files were checked column by column and are fully loaded, all 47 series, nothing left on the floor there.

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 36 | The rest of the NY Fed workbook we already download (leg 1: is card distress a card story or a household story?) | The five non-card loan types (mortgage, home equity revolving, auto, student, other) read from the three sheets that publish all of them, plus debt by age, plus the by-age transition sheets for all debt, auto and student loans: 68 hhdc series from 25; four different first quarters handled, including a `leading_gaps` rule for student loans starting a year late and a two-digit year pivot at 90 for the 1999 debt-by-age sheet; goldens from the 2026-08-11 press release's by-loan-type table; six charts | done 2026-09-11 |
| 37 | Cross-asset FRED one-liners (leg 4: is the card margin a card decision?) | TERMCBPER24NS and TERMCBAUTO48NS (the same G.19 survey as the card APR) and DRALACBS, CORALACBS, DRSFRMACBS, CORSFRMACBS with fixtures; a Pricing chart of the three loan rates against prime and a Performance chart of charge-off rates by loan category | done 2026-09-11 |
| 38 | NY Fed state-level card statistics (leg 1: was the loss cycle broad or concentrated?) | New `fetchers/nyfed_state.py` for area_report_by_year.xlsx: card debt per capita and card 90+ delinquency for the 50 states, DC, Puerto Rico and the nation, annual at Q4 from 2003 (106 series); the transposed layout parsed by header, the area list asserted, Puerto Rico's post-2016 trailing gap allowed and only at the end; no golden is possible, so the fetcher checks the national row sits inside the range across areas and a fixture test requires it to track the Quarterly Report to 0.6 points; `v_state_card`; three Distribution charts | done 2026-09-11 |
| 39 | The test suite had become the bottleneck | The `page` fixture renders once per session instead of once per test (a `session_paths` fixture beside `tmp_paths`), since every render draws a PNG per chart through matplotlib and twenty-odd tests took the page; runtime falls from over ten minutes to under three and stops growing with the chart count | done 2026-09-11 |

## v1.9: the charts talk to each other (2026-09-11, the user's "cursor?")

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 40 | Cursor sync across the page | Hovering any chart puts the crosshair on the same date on every other chart and each legend reads its own series at that date. The x scale only: `scales: ['x', null]`, `setSeries: false`, so no chart ever shares a y scale or a series focus with a chart in another unit. A chart whose data or whose current window does not cover the hovered date clears its cursor instead of letting uPlot clamp to an endpoint and read out a value for a date it does not have. On step (period) charts the cursor lands on the period that CONTAINS the hovered date rather than the nearest period end, which is the rule the stepped line is already drawn by (`align: -1`) and which matters across cadences: a date in March 2024 is in the annual period ending 2024-12-31, not the nearer 2023-12-31. The series browser at the foot of the page is deliberately left out: it is a separate tool with its own axis and it is never on screen at the same time as a panel chart. Tests: the panel charts carry the sync key, no chart syncs a y scale, the containing-period rule picks the later index, and the browser carries no sync key. Verified in headless Chrome with no console errors, by publishing a cursor from one chart and reading another chart's `cursor.idx` back. PNGs byte-identical, since this is client-side only | done 2026-09-11 (75 charts in the sync group, 0 sharing a y scale, 0 sharing a series focus. Headless Chrome, driving real mousemove events and reading the legends back: with no cursor 0 of 75 charts read out a period; hovering `card_nco` mid-window put 75 of 75 on 2023 Q4, reading it as '2023 Q4' on the quarterly charts, '2023-12-30' on weekly jobless claims and '2023' on the annual state chart; hovering weekly claims at its newest point left 9 of 75 reading out and cleared the other 66 rather than clamping them to their endpoints; a mouseleave cleared all 75; one mousemove cost 2.65 ms, so no throttling or viewport gating was needed. The containing-period rule was checked directly on the annual state chart: for 2025-06-14 the NEAREST period end is 2024-12-31 and the rule picks 2025-12-31. No console errors. PNGs: all 72 images the last local render produced are byte-identical to this render, so the feature changed no image bytes) |

## v1.10: the numbers on the charts (2026-09-11, the user's "make sure graphs dont have crazy values")

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 41 | Audit every value the page shows, and stop the class of bug that produced them | Every series the page draws (75 charts) and every series it can draw (736 in the browser) checked against plausibility for its unit; each flag either fixed or written down as legitimate with its reason; the mechanism that allowed it closed so it cannot recur silently; tests | done 2026-09-11 (the audit read the rendered payload, so it saw exactly what a reader sees. ONE REAL DEFECT, on a published headline chart: `nco_by_issuer` drew Bank of America at a 182.9% annualised net charge-off rate for 2001 Q4, with six more quarters above 95%, and `v_fdic_rates` also produced a 69.6% 30-89 day share for the same charter. Cause: a collapsed denominator. Bank of America's card book on the Bank of America, N.A. charter falls from $1.8bn in 2000 to between $2mn and $50mn from 2001 to 2013, because the card business sat at FIA Card Services (the former MBNA charter, not in the roll-up), returning at $102bn in 2014; dividing a real quarter of charge-offs by a $23mn book gives 182.9%. On charters that are not drawn it was far worse: Regions 2006 Q4 reads -5,969% and TD Bank 2003 Q3 -850%. Across v_fdic_rates, 22 of 3,700 issuer-quarters were above 30% and 801 sat on a book under $50mn. FIX: every ratio in v_fdic_rates now requires a card book of at least $100mn. The floor is not tuned: $50mn, $100mn and $250mn remove exactly the same quarters and leave the same chart maximum (15.65%, JPMorgan 2009 Q4); only above $500mn does it start cutting real Synchrony history. Bank of America keeps 60 of 106 quarters, and the largest book among the 46 dropped is $49.8mn, so nothing material was lost. Negative rates were deliberately NOT filtered, because a quarter whose recoveries beat its charge-offs is real: Citi 2006 Q4 is -4.0% on a $37bn average book and Synchrony 2007 Q4 is -1.4% on $7.4bn, and both are still drawn. SECOND FIX, found because the first one created it: suppressing those quarters left a 13-year hole in the Bank of America line, and the page drew every series with `spanGaps: true`, so it would have joined 2000 straight to 2014 in one straight line. Charts can now set `span_gaps: False` and `nco_by_issuer` does, with a note explaining the charter history. This also removes a quiet disagreement between the page and the PNGs, which have always broken at nulls because matplotlib breaks at NaN. GUARD: `VIEW_BOUNDS` in render.py bounds the rate-like view fields and `_series_rows` raises rather than drawing a value outside them, since view fields never pass the loader's vmin/vmax (that validates facts; views are computed afterwards and nothing checked them at all). A test asserts every view field a chart draws is either bounded or named in `VIEW_BOUNDS_UNCHECKED` with its reason, so a new derived field cannot reach a chart without someone deciding what it may contain. EVERYTHING ELSE THE AUDIT FLAGGED IS LEGITIMATE and is recorded below so it is not re-investigated) |

Values that look wrong on the page and are not, checked 2026-09-11 so nobody re-opens them:
- 12-month growth rates above 100% and below -50% (`retail_growth`, `spend_categories`, `spend_growth`, `card_volume_vs_retail`, `revolving_yoy`): these are April 2021 against the April 2020 collapse. Restaurants and bars +117.2%, clothing +145.1%, restaurants -50.1% in 2020. Real.
- `dfa_credit_to_net_worth` reaching 599.9% and `dfa_buffer_by_wealth` 402.8%: ratios of one stock to another, not shares. The bottom half owing six times its net worth IS the finding.
- SLOOS net percents as low as -92: a net percent is stronger minus weaker and is negative by construction.
- Charge-off rate on single-family mortgages at -0.04% (FRED CORSFRMACBS): a quarter of net recoveries, and the Fed publishes it that way.
- FDIC per-charter quarterly charge-offs, recoveries and net charge-offs going slightly negative: the FDIC's own `*Q` flow fields carry reversals and restatements. They are dollar amounts, not rates, and are not charted.
- `y14_payment_flows` payment rate at 112.1%: charge-offs sit inside payments because the Y-14 publishes a charge-off rate rather than a dollar amount, which is already in the view's comment and the chart note.
- Utilization at the 90th percentile (95.3%), debt current as a share of balances (97.5%), revolving share (80.3%): all genuinely near their ceilings.
- `Card loans, Capital One, National Association` jumping from a median of $0.2bn to $256bn: the card book moved onto that charter. The roll-up exists precisely so the issuer line does not jump.

## v1.11: the first monthly issuer reading (2026-09-11)

THE BLOCKER ON TASK 31 WAS STALE. SEC EDGAR is reachable from this machine again: www.sec.gov, data.sec.gov,
efts.sec.gov and the Archives all answer 200. The 2026-09-08 block was IP reputation against the ViaSat CGNAT
address 99.196.128.3 and the machine is now on 104.229.10.138, so nothing about SEC policy changed and nothing
needs to run from Actions. Before assuming a 403 is policy, check the public IP (`curl https://api.ipify.org`).

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 42 | Capital One monthly credit metrics (task 31, Capital One only) | `fetchers/issuer_8k.py` loads the Exhibit 99.1 that Capital One files with an 8-K under Item 7.01 each month; goldens traced to the filed exhibit itself; a fixture test per layout; charts against the industry | done 2026-09-11 (66 consecutive months, February 2021 to July 2026, 396 rows in 6 series, no gaps. Net charge-off rate 4.12% and 30+ performing delinquency 3.48% for July 2026, filed 2026-08-17, against the industry's 3.82% and 2.85% at 2026 Q2. This is the ONLY monthly issuer-level reading on the page: the FDIC call report lands 55 to 58 days after quarter end and the Y-14 about 3.5 months, so every other single-lender number here can be four and a half months old, and this one is about two weeks old. Two Performance charts, `cof_monthly_nco` and `cof_monthly_dq`, each against the Fed's all-commercial-bank rate. 77 charts, 14 sources, 58 goldens) |

What the exhibits actually look like, so nobody rediscovers it:
- The metrics exhibit is found through each filing's index.json, never by constructing its name. EDGAR truncates a
  document name but NOT at a fixed width: `ex991april2026creditmetrics.htm` survives at 27 characters while
  `ex991august2025creditmetri.htm` is cut to 26. Match on "credit", not on "metrics", or February and December
  vanish. Filing indexes are cached under data/raw/issuer_8k/index/ because a filing is immutable once filed, so
  after the first run only the one or two new filings a month cost a request instead of 97 every night.
- The exhibit states its own period ("As of and for the month ended July 31, 2026") and that is what dates the
  rows. The file name is only ever a hint for skipping a download.
- FIVE layouts appear across the 55 exhibits from January 2022 to July 2026, and four of the differences are
  Capital One moving footnote markers: 'Domestic' with Rate(1)/(2)/(3) in 43 filings, 'Domestic(5)' in 8,
  'Credit Card:(4)(5)' in 2, one month of Rate(2)/(3)/(4), and one month with a footnote on a GROUP header
  ('30+ Day Performing Delinquencies(5)'). Every label is therefore compared with its footnotes stripped.
- The fifth difference is real: for June 2025, the first month after the Discover acquisition closed, the section
  splits into 'Capital One Domestic' (5.29%), 'Discover Domestic' (4.47%) and a 'Domestic Card' total (4.96%).
  The TOTAL is loaded so the series does not step at the merger, which is the same choice the FDIC roll-up makes
  for this issuer, and a live golden pins that month so a regression to a component fails loudly.
- Scope: managed DOMESTIC CARD, wider than the single FDIC charter, and the delinquency figure counts 30+ day
  PERFORMING balances. Not comparable with the call report level for level; the chart note says so.
- Some months carry a second Item 7.01 8-K with no exhibit (an investor conference), so a filing is identified by
  its exhibit and never by counting one per month.
- USER-AGENT: the SEC's edge 403s any User-Agent containing a URL in parentheses, which is exactly the shape of
  this pipeline's own, with or without a contact appended. Verified all four combinations on 2026-09-11. This
  fetcher sends `carddash/<version> <CARDDASH_CONTACT>` instead. CARDDASH_CONTACT is now a repo secret and is
  passed to the refresh workflow; without it the source fails loudly rather than fetching anonymously.
- Not done: Synchrony, Bread and American Express. Check whether they still file monthly before promising them;
  each would need its own parser because the exhibit layout is per issuer.

## v1.12: the state data gets a shape (2026-09-11, the user's "better viz?")

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 43 | A tile-grid cartogram for the state data | The 53 areas drawn as one equal square each in a rough map of the country, with a year slider over the full history, a measure switch, a per-square hover reading, a legend, and a table twin; a sequential ramp defined separately for light and dark; tests | done 2026-09-11 (52 squares plus the national row kept out of the grid and shown as the reference. Two measures, 90+ delinquency and card debt per person, 2003 to 2025. Replaces nothing: the three range charts stay, since they show the spread over time and the map shows one year across areas) |

Why a tile grid rather than a real map: every area gets the same square, so Rhode Island is as visible as Texas and
land area never stands in for population. It is not a map and the page says so.

Colour decisions, because this is the first thing on the page that is not a line chart:
- Magnitude, so the ramp is SEQUENTIAL: one hue over six steps. It deliberately does not touch --series-1..8,
  which are the identity channel. Six bins because past about seven, adjacent classes stop being tellable apart.
- Bin edges are quantiles of the WHOLE history of the chosen measure, not of the year on screen, so dragging the
  year moves the colours instead of re-cutting the scale under the reader. That is what makes the finding legible:
  drag from 2003 to 2025 and the map darkens almost everywhere, which is the recorded result that card delinquency
  rose in every one of the 51 areas with a full history. The cost is that 2025 is mostly the top bin, and that is
  the point rather than a defect.
- Dark mode is a chosen ramp, not an inverted light one: the anchor flips so the high end is bright against the
  dark surface. Both ramps were checked for monotonic lightness, and every step ships the ink colour that clears
  4.5:1 on it (--seq-ink-N), so a two-letter label is legible on every square in both themes.
- Bins are CSS classes, not inline colours, so the browser resolves the ramp live. The uPlot charts still read
  their colours once at draw time, which is why the theme toggle reloads the page; the map would not need that.

Two bugs caught by rendering it and looking at it, which is the only way they would have been caught:
- The draw loop shadowed `v`, the CSS-variable helper, with a local named `v` holding the reading, so every square
  called a number as a function and no tile rendered at all. The page threw before the probe attached its error
  listener, so the console was empty; the screenshot is what showed it.
- With inline colours the first screenshot came out with the DARK ramp under data-theme="light", because the
  colour was frozen at draw time and the attribute was set afterwards. Real users never hit this (the toggle
  reloads), but it is why the bins are classes now.

## v1.13: the second thesis note (2026-09-11)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 44 | Rewrite the thesis as a dated second note | A second note in design/ that revises the first without editing it, cites only loaded data, keeps the three falsification thresholds unchanged, and says plainly what the new data cost the argument | done 2026-09-11 (design/thesis-2026-09-11.html, "Cards turned down while every other debt turned up". THESIS_NOTE points at it; the first note is untouched, linked from the second, and render now copies EVERY design/thesis-*.html into docs/ so the back-links survive a clean build. The tests follow the constant instead of a hard-coded filename, so the next rewrite does not break them, and they assert the old note still exists and is still linked) |

What the second note actually changed, so the next rewrite knows what moved:
- Leg 1 (the loss cycle is over) is STRONGER and now six weeks fresher. Capital One's monthly series is below its
  year-earlier month in all six months of 2026 that have a 2025 twin: February -1.18, March -1.00, April -0.72,
  May -0.75, June -0.59, July -0.71. The note states explicitly that the series is NOT seasonally adjusted, so the
  5.17 to 4.12 fall within 2026 is partly seasonal and the like-for-like month comparison is the honest one.
- A caveat added inside leg 1: Capital One's 30+ delinquency, the leading number, improved only 0.19 points year
  over year against 0.71 on charge-offs. Charge-offs are working off a 2024 stock; the inflow is improving about a
  quarter as fast. That is the number that decides the argument next, and the note says so.
- Leg 2 (no aggregate debt problem) is STRONGER, and now about people rather than ratios: the bottom half by wealth
  owes 51.8 percent of consumer credit against 57.2 in 2019, holds 30 cents of deposits per dollar against 22, and
  owes it against 62 percent of net worth against 129.
- Leg 4 (the repricing was permanent) got its control: the same G.19 survey at the same banks prices a 24-month
  personal loan at 11.86 percent and a 48-month car loan at 7.47 against the card's 22.15. The margin is a card
  decision, which the first note asserted and could not show.
- THE THIRD LEG WAS CONSTRAINED, which is the point of writing a second note at all. "Re-segmenting" invited a
  reading where distress concentrated somewhere, and the state panel rejects it: card delinquency rose in every one
  of the 51 areas with a full history between 2021 and 2025, smallest rise 2.26 points, median 3.84, and the best
  state in 2025 (8.03) is worse than the median state in 2019 (7.42). The note now says the re-segmentation is
  across BORROWERS, not places, and names the first note's failure to guard that reading.
- NEW and not in the first note: cards are the only consumer debt improving. Between 2024 Q4 and 2026 Q2 the card
  flow fell 7.18 to 6.97 while mortgages went 1.09 to 1.52, home equity 0.56 to 1.15 and all household debt 1.70 to
  2.57. The note is explicit that the secured rises are off a floor (a 0.99 percent mortgage 90+ share against 8.89
  at the financial crisis) and that the student loan move is the payment-pause reporting restart, not distress.
- Section 7's over-70 puzzle is ANSWERED: their flow is 6.34 on cards, 2.45 on auto and 1.80 across all their debt,
  so it is card-specific, not a balance-sheet problem for older households.
- Section 7's timing gap is ANSWERED by task 42, and the note says what that is still worth: one issuer, about a
  fifth of card loans at insured institutions ($250.5bn of $1,189.6bn at 2026 Q2), not an industry.
- A new open item the second note raises: Capital One's own July rate (4.12) and the FDIC's rate for the same
  issuer (4.83) are both correct and not comparable, because the 8-K covers the managed domestic card book and the
  call report covers the bank charter. The monthly series is a good read on DIRECTION and a bad one on level.
- A faster tripwire, added alongside the three unchanged thresholds: three consecutive months of Capital One above
  its year-earlier month. It has not happened once in 2026 and it would fire about two quarters before the NY Fed
  flow could.
- The three falsification thresholds (7.5, 4.2, 14.0) are UNCHANGED and must stay that way unless the note is
  rewritten again; THESIS_TESTS asserts them, so a quiet move fails a test.

Every figure in the note was cross-checked against data/facts.csv before it shipped: 83 distinct numbers, all
traceable. One claim was cut in that check rather than softened, because nothing loaded measured it: a sentence
putting Capital One at "about a third of the large-bank subprime card market". Nothing on this dashboard measures
any issuer's subprime share, and the note now says so where the claim used to be.

## v1.14: three more issuers, so the monthly reading is an industry reading (2026-09-11)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 45 | Synchrony, Bread Financial and American Express monthly 8-K credit metrics | `issuer_8k` carries a per-issuer registry instead of one hard-coded CIK; each issuer has its own parser and its own checked-in fixture; the six `cof_card_*` metrics become entity-keyed `issuer_card_*` so an issuer is an entity and not a metric name; at least one live golden per issuer traced to the filed document; the monthly charge-off and delinquency charts draw every issuer against the industry, with the definitional differences in the chart note; tests green | done 2026-09-11 for Synchrony and Bread, NOT for American Express (task 46). issuer_8k now loads three issuers, 1016 rows in 18 series: Capital One 2021-02 to 2026-07, Synchrony 2021-03 to 2026-07, Bread 2022-07 to 2026-07. 7 new goldens, all live, all passing (65 total). The two issuer charts draw three lenders against the all-commercial-bank rate. 381 tests green |
| 46 | American Express: decide how to carry two populations under one issuer | Amex's readings are loaded without a fake step at the basis change, or the source is recorded as unavailable with the evidence | done 2026-09-11 (four entities, two segments x two bases; 1376 rows in 34 series across four issuers; a dedicated chart draws both bases as separate lines; 3 new live goldens, 68 total; 383 tests green) |

THE DECISION: the basis is part of the POPULATION, not a label on it, so it is part of the entity.
`ISSUER:AMEX_US_CONSUMER` is the current 'Card balances' measure and `ISSUER:AMEX_US_CONSUMER_LOANS` the retired
'Card Member loans' one, and the same for small business. Nothing hard-codes the changeover date: the section
label in each filing says which basis it is on, so `AXP_SECTIONS` maps the wording to the entity and the rows
follow the document. The retired pair carries `max_age_days` 36500 and never turns stale, the treatment the
merged-out FDIC charters already get. They are drawn as separate lines on `amex_prime_end`, so a reader sees the
change of measure instead of a step in one line.

What the chart is for: Amex is the prime end of the same market and the distance is the finding. In July 2026,
1.1 percent of its U.S. consumer card balances were 30 days past due, against 3.48 at Capital One, 4.2 at
Synchrony and 5.35 at Bread Financial, a five-fold range across four lenders in the same month. Part of that is
definitional and the note says so: Amex's denominator includes pay-in-full charge-card balances that are almost
never late, and its write-off rate excludes interest and fees.

The old basis stops at JANUARY 2026 rather than March, so the two lines abut and do not overlap on the page. The
filing that reported February and March 2026 on the old basis was never downloaded, because by the time the walk
reached it those months were already covered by the new-basis filing, and the planner works in months. The
same-month comparison is not lost: both fixtures are checked in and
`test_the_amex_population_break_is_real_and_is_why_there_are_two_entities` asserts March 2026 at $97.5bn on the
old basis against $110.8bn on the new one, so the evidence lives in the tests even though the page shows the
adjacent months (January $97.2bn, February $107.4bn) instead.

A THIRD SILENT DROP, caught by the bounded trim from task 45 rather than by looking. Amex printed its November
and December 2023 consumer write-off rates as `2.5%(b)` and `1.7%(b)`, with the footnote marker inside the VALUE
cell, and `_number` deliberately does not strip footnotes because `(5)` as a value is minus five. So both months
parsed as nothing and vanished, and the only reason anyone knows is that the gap check refused to publish a
series with a hole in it. `_number` now strips a trailing marker when it FOLLOWS a number and leaves a cell that
opens with a parenthesis alone.

WHAT THE THREE ISSUERS SHOW, which is the point of doing it. All three peaked in 2024 and all three have been
falling since, and they are stacked in exactly the order their books would predict: Bread (private-label, the
weakest borrowers) at 6.80 percent net loss and 5.35 percent 30+ delinquency in July 2026, Synchrony
(private-label and co-brand) at 4.7 and 4.2, Capital One (general-purpose) at 4.12 and 3.48, the
all-commercial-bank rate at 3.82 and 2.85. The ORDER never changes across the whole history and the SHAPE is
the same for all three, which is what a common national cause looks like and is consistent with what the state
map said in task 43. Bread's loss rate peaked around 8.9 percent in early 2024 and has given back most of it.

FOUR THINGS WERE FOUND BY DOING THIS THAT NOBODY SHOULD REDISCOVER.

1. THE ITEM TAG IS NOT A FILTER, and filtering on it silently loses the newest month. Synchrony filed its July
   2026 credit statistics on 2026-08-17 under Item 2.02 while every other month of the same exhibit went out
   under Item 7.01. The first version of this fetcher read only Item 7.01 filings and therefore thought
   Synchrony's newest month was June, with no error anywhere: the source would just have sat one month behind
   for ever. Every 8-K is now considered and the document decides. This is worth remembering for any future
   EDGAR work: the item is the filer's description, not a property of the document.

2. BREAD'S QUARTER-END MONTHS PUT A QUARTERLY RATE ON A MONTHLY SERIES, and it loaded silently. In March, June,
   September and December the loss table drops the year-ago column and prints 'For the three months ended
   September 30, 2023' beside 'For the month ended September 30, 2023'. The header states THE SAME DATE TWICE,
   so reading the header as dates alone and assigning values by position puts the quarter's figure on the
   month: September 2023 went in at 6.9 percent when the month was 6.7. Every quarter-end month in the series
   was wrong. Columns headed 'N months ended' are now dropped, a header that still repeats a month raises, and
   `bfh_8k_nco_rate_2023_09_monthly_not_quarterly` is a live golden pinning 6.7 so it cannot come back. Same
   class of bug as task 41: the number was plausible, which is why it needed a check rather than a glance.

3. A TRIM THAT FIXES HOLES CAN HIDE A MISSED FILING, so it is bounded. Bread's exhibits print a month and the
   same month a year earlier, which hands over a free extra year of delinquency history, except in the
   quarter-end months where that column is spent on the quarter instead. So Bread's delinquency series is
   unbroken back to July 2022 while its loss series has four holes before July 2023. The page spans gaps, so a
   line with a hole is drawn straight through it. Each series is therefore cut back to the unbroken run ending
   at its newest reading, which is why Bread's two series start a year apart. The trap is that the same trim
   would quietly swallow a genuinely missed filing and publish a series starting after the hole: a failed read
   of one Capital One exhibit would have dropped five years of history without a word. So the trim only
   applies BEFORE the issuer's `first_month`, where the source really does not publish monthly; a hole at or
   after it raises.

4. THE FOUR ISSUERS DO NOT MEASURE THE SAME THING, and the differences are bigger than most of the movements
   on the chart. Capital One counts 30+ day PERFORMING delinquencies over period-end loans; Synchrony counts
   over-30-day delinquencies over period-end receivables and its monthly charge-off rate saws up and down
   purely because a calendar month holds 25 or 30 charge-off cycle dates; Bread divides delinquency by
   period-end PRINCIPAL loans, a smaller denominator than its own end-of-period loan figure ($16,378mn against
   $18,543mn in July 2026). The chart note says all of this and says to read direction rather than the gaps
   between lines. Synchrony's own adjusted (non-GAAP) charge-off rate is loaded beside the unadjusted one but
   is deliberately NOT the series on the chart, because no other issuer publishes anything like it.

WHY AMERICAN EXPRESS IS PARSED, TESTED AND NOT LOADED (task 46). Between the filings of 2026-04-15 and
2026-05-15 Amex stopped reporting 'Card Member loans' and started reporting 'Card balances', and the population
changed with the words: the new measure includes pay-in-full charge-card balances the old one left out. On the
same month, March 2026, the two bases read $97.5bn and $110.8bn for U.S. Consumer, a step of about 14 percent,
and the delinquency rate moves the other way (1.4 against 1.3) because the added balances are almost never past
due. Amex restated only the two overlapping months, not the history, so the newest statement of January 2026 is
$97.2bn on the old basis and of February 2026 is $107.4bn on the new one: the break cannot be closed from the
filings. Splicing them would put a fake 10 percent jump in the balances and a fake improvement in delinquency,
which is the task 41 class of error; loading only the new basis leaves five months, which is not a series. The
parser handles both vocabularies, the fixtures are checked in for both, and
`test_the_amex_population_break_is_real_and_is_why_it_is_not_loaded` fails if the two bases ever agree, which
is the signal that it can be enabled. Amex also has no separate exhibit at all (the table is in the 8-K body),
so it is found by content, and its content test must read PARSED table text: Amex splits the row label across
tags, so a substring search of the raw HTML answers False on filings that plainly carry the table.

Cost and shape of the fetch, for the next person who worries about request counts: Synchrony's whole history is
FIVE downloads because each exhibit carries thirteen months; Bread needs one per month (37) because its second
column is a year earlier rather than the month before; Capital One's 66 were already there. The planner works
in months, not filings: it parses what is on disk, works out which months are missing, and only then decides
which filings can fill them, so a filing whose month is already held costs nothing, not even an index request.
Raw storage is 1.2 MB for Bread and 440 KB for Synchrony on top of Capital One's 1.6 MB.


Scouted 2026-09-11 before any code was written, from the dev machine (EDGAR answers it again). All three still file
monthly, and no two of them file the same shape, which is most of the work:

- **Synchrony Financial, CIK 0001601712.** One exhibit, always named `creditstatsfinancialtables.htm` (55 of 55
  filings from 2022-01-28 to 2026-07-21 use that exact name, the only issuer here with a stable document name), and
  each one carries **thirteen months** of history in a single table, so the whole series costs about six downloads
  rather than sixty. Rows: period-end loan receivables, loan receivables held for sale, average loan receivables
  including held for sale, 30+ delinquency rate, net charge-off rate, recovery adjustment, adjusted net charge-off
  rate. Dollars are in BILLIONS here, unlike Capital One's millions.
- **Bread Financial Holdings, CIK 0001101215.** The exhibit is named for its month and truncated the same way
  Capital One's is (`july2026creditstatsex991-t.htm`, `august2025creditstatsex991.htm`), so it is matched on
  "creditstats", which also happens to match Synchrony's. Only 36 of its 307 Item 7.01 filings carry one, and the
  earliest is 2023-07-27: before that the numbers were not filed this way, so Bread's history starts mid-2023 and
  that is a property of the source, not a gap to fix. Each exhibit prints the month AND the same month a year
  earlier, which both halves the downloads and gives a free consistency check.
- **American Express, CIK 0000004962.** There is NO separate exhibit: the statistics are in the body of the 8-K
  itself (`axp-<filing date>.htm`), so a filing cannot be identified by a document name and has to be identified by
  its content. Each one carries **three months**, and splits them into U.S. Consumer Card and U.S. Small Business
  Card, which is two entities rather than one.

The definitions are NOT the same across the four, and this is the thing most likely to be read wrong off a chart:
- Capital One: 30+ day PERFORMING delinquencies over period-end loans; charge-offs over average loans.
- Synchrony: over-30-day delinquencies over period-end receivables; charge-offs over average receivables including
  held for sale. Its monthly charge-off rate is saw-toothed because charge-off cycle dates fall differently in each
  calendar month (the exhibit prints the count of cycle dates per month for exactly this reason), which is why
  Synchrony also publishes an "adjusted" rate that spreads recoveries evenly across the quarter. The unadjusted rate
  is what is comparable with the others; the adjusted one is loaded too and labelled as the non-GAAP measure it is.
- Bread: net principal losses over average loans; delinquency over period-end **principal** loans, which is a
  smaller denominator than its own end-of-period loan figure ($16,378mn against $18,543mn in July 2026). Both are
  loaded so the rate can be recomputed from the page's own numbers.
- American Express: net write-off rate **principal only**, explicitly excluding interest and fees, and its card
  balances include pay-in-full charge-card balances. Both push its rates far below everyone else's (1.1 percent 30+
  in July 2026 against Synchrony's 4.2), and neither is a sign of a better book in the way a naive read suggests.

Not loaded, on purpose: the second table in the Amex filing is the American Express Credit Account Master Trust,
which holds only revolve-eligible balances and computes its write-off rate on end-of-period rather than average
balances. Amex's own filing says at length why the two are not comparable, so the trust table is skipped and the
reason recorded here rather than discovered again.

## v1.15: a missing month is drawn as missing (2026-09-11, the open item from the v1.9 handoff)

| # | Task | Pass condition | Status |
|---|------|----------------|--------|
| 47 | Stop the page drawing through holes the PNGs leave open | `span_gaps` defaults to False so a missing period is drawn as a break on the page exactly as it already is in the PNGs; every series whose gaps are a CADENCE CHANGE rather than missing data opts back in explicitly and says so in its chart note; a test pins the one real hole | done 2026-09-11 (default flipped, `sentiment` is the only chart that opts back in, and the missing October 2025 is now an explicit null on the grid rather than an absent row) |

Audited before deciding, because the previous handoff asked whether `span_gaps` should flip by default and the
answer depends entirely on how many series actually have holes. Of 754 loaded series, SIX have a hole inside
their own span, and only two of those are drawn on a chart:

- `unemployment_rate_sa` (charted, losses_vs_labor) is missing OCTOBER 2025, and `cpi_all_urban_sa` (browser
  only) is missing the same month. That is the federal shutdown: the household survey for October 2025 was
  never collected, so BLS published no unemployment rate for it and never will. It is a real hole in a headline
  series at a recent, prominent point on the chart, and the page has been drawing a straight line across it
  while the PNG of the same chart leaves it open.
- `consumer_sentiment` (charted, its own chart) has 92 "holes", and NONE of them are missing data: the
  University of Michigan survey was QUARTERLY until January 1978 and monthly from February 1978. Every gap is
  before 1978-01-31 and every period after it is consecutive.
- The other three are `fdic_card_noncurrent` for three charters, browser only.

So the honest default is False and the exception is narrow. A hole means "not measured", and joining across it
invents a reading; a cadence change means "measured less often", which is a different statement and has to be
declared rather than inferred. `consumer_sentiment` therefore carries `span_gaps: True` and a note saying why,
which is the only series on the page that gets it.

THE FLAG ALONE DID NOTHING, WHICH IS THE PART WORTH REMEMBERING. `span_gaps` only governs how uPlot treats a
NULL, and the x grid is built from the dates that HAVE data (`xs = sorted({d for rows in all_rows for d, _ in
rows})`). A month that no series on the chart holds is therefore not on the grid at all, so there was no null
to span: the unemployment line ran straight from September to November 2025 with the flag set either way, and
nothing about the chart said a month was missing. `_missing_periods` now walks each series' own span at its own
cadence and puts anything skipped on the grid as an explicit null, and only then does span_gaps decide whether
to bridge it. 'T' (the four-monthly SCE waves) is deliberately left out of the cadence table, and a series that
merely starts late is never filled, or every chart would grow a run of leading nulls.

The sentiment chart went from 675 points to 885 because its pre-1978 quarterly era now carries its skipped
months as nulls; it spans them, so it draws exactly as it did before.

## v1.5 (after two green releases)
- Order (from the 2026-09-07 scouting): NY Fed SCE Credit Access first (direct xlsx, no gate, about half a session), then CFPB complaints via the trends endpoint (one session), then BEA PCE detail via the keyless NipaDataM.txt flat file (the API needs a key; half to one session), then Census Monthly Retail Trade via the keyless mrtssales92-present.xlsx (the API needs a key even at low volume; one session). Details, URLs and risks in design/handoff-2026-09-07.html §3.
- Spend panel.

## v2
- Issuer 8-K monthly credit metrics (COF, SYF, BFC, AXP). 10-Q economics via SEC XBRL with a per-issuer tag map.
- Post-drafting routine after each refresh (human posts).

## Open items and known gaps
- Repo transfer User5017 -> SamuelJWebber: wanted, deferred (2026-09-07). Checklist of references to update is in CLAUDE.md under "GitHub accounts".
- G.19 revisions move live goldens (seen 2026-09-08): the September release revised Q1 2026 revolving from 1338.0 to 1338.2 and the May/June levels by about 3.4bn, which turned the fred source amber (`golden_mismatch`) on two scheduled runs while the Actions job stayed 'success' (only red fails it; `checks/verify_releases.py` is what catches amber). The routine after each G.19 release (fifth business day): run `uv run python checks/verify_g19.py` (it now checks the by-holder rows too and skips metrics it cannot map instead of crashing); on a FAIL, re-base the golden on the new release page (say the old value and release date in `origin`), refresh the FRED fixtures with `curl "https://fred.stlouisfed.org/graph/fredgraph.csv?id=<sid>" -o tests/fixtures/fred/<sid>.csv`, and re-pin the reading strings in tests/test_fred.py and tests/test_render.py that quote the newest month. A quarter-end level inside the current year is not safe as a live golden; the year-end columns have held.
- NCUA (task 29): the industry total sums federally insured credit unions only (FOICU CU_TYPE 1 and 2), which is the only filter that reproduces the Quarterly Credit Union Data Summary's $86.0bn; privately insured credit unions are excluded. Dollar amounts are in dollars (scale 1e-9 in series.csv), the interest rate in basis points (scale 0.01). File and column names change case between vintages and are matched case-insensitively. Charge-offs and recoveries are year-to-date and reset each Q1, so the quarterly flow in `v_ncua_rates` needs consecutive quarters. A quarter's zip lands about two months after quarter end (2026 Q2 was not up on 2026-09-10); `RELEASE_RHYTHM` uses 65 days. The 2026 Q1 and 2016 Q1 extracts are byte-identical to the checked-in fixtures. An NCUA outage at home (DNS on 2026-09-08) is what ended the previous session; the runner never had the problem.
- FRED account created 2026-09-07 (credentials in `C:\Users\samwe\code\fred-account.txt`, outside the repo). `FRED_API_KEY` is set in the local `.env` (gitignored) and as an Actions secret; the fetcher uses the official API when the key is present and falls back to keyless fredgraph.csv otherwise.
- Golden entries are re-verified against the G.19 release page itself with `uv run python checks/verify_g19.py` (no FRED, no LLM). Run it whenever golden.yaml changes.
- SLOOS demand series id not yet identified (only standards loaded). SLOOS is dated with period_offset -1 since task 7b: FRED's 2026-07-01 observation (the July survey) is period_end 2026-06-30. The re-dating was loaded by dropping the SLOOS rows from facts.csv before the refresh, so revisions.csv carries no spurious entries from it.
- Offered vs paid (task 7b): the spread is the TCCP 620-719 tier median minus the G.19 assessed-interest rate, in percentage points, and exists only through the last offered period (H2 2025 until the H1 2026 file lands). The band is the range across the three tier medians of all responding issuers; the earlier 'median of each product's highest APR' series stays in facts (tccp_purchase_apr_max_median) but is no longer charted. Two more series colors (--series-4, --series-5) and a band fill were added to the page palette.
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
- Read-only scouting for tasks 5 to 7 and the v1.5 sources (FDIC fields and certs, year-to-date verdict, golden candidates; SCE, complaints, BEA, Census; page critique; landscape scan) is in design/handoff-2026-09-07.html (2026-09-07). Its suggested board changes were applied 2026-09-08: task 5 wording, task 7 pass condition, new task 7b (the offered-vs-paid and SLOOS-dating fixes, scheduled before task 8), v1.5 order recorded above. Not scheduled, kept as suggestions below: chart cards showing data-as-of and a status badge, and the candidate charts.
- Not on the board, suggested for after task 8: a Performance chart of the HHDC 90+ flow by age group (six series already in facts) and a cross-source delinquency chart (Philly Fed 90+ balance-based rate for Y-14 banks against the HHDC 90+ share for all lenders, with the scope notes explaining the level gap).
- Not on the board, suggested for after task 8 (from the 2026-09-07 critique, handoff §4 and §6): chart cards show "data as of" from the plotted rows' pulled_at and a per-card status badge (today a card cannot go red and its pulled date is the last attempt, not the last success); one footer line per series on cross-source charts when sources or cadences differ; the method section credits every loaded source from SOURCE_LABELS; a population column in series.csv printed on the caption line; light-mode contrast and a fourth series color; query step charts from one period earlier so the first reading is not lost; `spanGaps` only for foreign-grid nulls. Candidate charts: delinquency triangulation across HHDC 90+ share, HHDC 90+ flow, Fed 30+ rate and Y-14 90+ rate with a residue footnote; a vintage-diff chart rebuilt from git history of facts.csv; an APR ladder with margin over prime including BRMCC01.
- NY Fed HHDC discovery: the URL embeds the data quarter (`.../householdcredit/data/xls/HHD_C_Report_2026Q2.xlsx`). The fetcher starts at the calendar quarter of the run date and walks back up to 8 quarters until a real xlsx answers; a missing quarter redirects to `/errors/404` served with HTTP 200 and `text/html`, so the body's zip signature is checked, not the status. The xlsx downloads directly with the pipeline User-Agent, no terms click (verified 2026-09-07). Data sheets are found by their A1 title because the `Page N Data` sheet names follow the PDF's page numbers.
- NY Fed HHDC timing: releases land five to seven weeks after quarter end (2026 Q2 on 2026-08-11). `max_age_days` is 200, so the source goes stale if 2026 Q3 has not appeared by mid-January 2027.
- NY Fed HHDC revisions: every release re-publishes the full history and revises prior quarters (between the 2026Q1 and 2026Q2 files the 2026Q1 card balance moved by $10bn and limits by $10bn; the flows did not move; nothing moved between the 2025Q2 and 2026Q2 files). So the balance golden entry is fixture-only (`check_live: false`) and the two flow entries are live; the 2026 Q2 flow can turn amber if revised, the 2025 Q2 flow is expected to hold. A panel or methodology change would show as a wholesale restatement (suspect, needs an ack).
- NY Fed HHDC scope: card balances by age and by state are not in the workbook (only total debt is split that way); the age split exists only for the 90+ flow. 'Credit card' is bankcards; retail cards sit in 'Other'. The limit sheet's balance column and the age sheet's 'all' column are near-duplicates of loaded series and are not loaded. The account-count sheet labels 2003Q1 to 2011Q1 with date cells; the parser accepts both label styles and requires consecutive quarters.
- NY Fed SCE download may sit behind a terms click: verify before grading A (the HHDC xlsx does not, verified 2026-09-07).
- FDIC charge-off fields: resolved 2026-09-07. `DRCRCD`/`CRCRCD`/`NTCRCD` are year-to-date and reset in Q1; the `*Q` fields are the FDIC's quarterly flows and equal hand differences in every normal quarter but not in merger quarters (79 mismatches in 4,588 rows). Task 5 loads the `*Q` fields; the year-to-date fields stay in the raw CSVs and a test checks the identity on Capital One's 2024.
- FDIC timing: the API carried 2026-06-30 data on 2026-08-19 and the QBP followed on 2026-08-25 (55 to 58 days after quarter end). `max_age_days` is 200, so the source goes stale if 2026 Q3 has not appeared in the API by mid-January 2027. The newest quarters can be amended and whether the FDIC re-indexes afterwards is unverified, so only the year-old 2Q 2025 golden number is checked live.
- FDIC merged-out charters (5649, 33954, 23702, 35328, 34351): their series end with the last call report before the merger date, so series.csv gives them `max_age_days` 36500 (never stale) and says so in the scope note. The fetcher asserts that ending, that every active charter ends on the industry total's quarter, and (since task 6) that the /institutions record agrees with valid_from, valid_to and merged_into, so a charter that merges, fails or files late turns the source red until issuers.csv is updated (add valid_to and merged_into, set the six series to 36500). Chains of mergers are not supported: merged_into must be an active charter. USAA Savings Bank's 2024-06-01 date is confirmed by its institution record (ENDEFYMD 06/01/2024, NEWCERT 32188, change code 224); the /history endpoint has no 223 record on that cert.
- FDIC identities asserted per charter on every row: NCCRCD = P9CRCD + NACRCD and NTCRCDQ = DRCRCDQ - CRCRCDQ (0 mismatches in 4,073 rows on 2026-09-08). The industry sums do not satisfy them before 1991 (partial reporting), and a sum is 0 rather than null before an item was collected, so the aggregate items start at documented first quarters (P3CRCD 2001 Q1, P9CRCD/NACRCD/NCCRCD 1991 Q1) and the fetcher checks the first kept quarter is positive.
- FDIC raw files: one CSV per charter (`format=csv`, no volatile metadata, so the file only changes when data does) and one aggregate JSON whose `meta.index` name changes when the FDIC re-indexes; about 560 KB together. facts.csv grew from 9.5k to 33k rows (2.9 MB).
- issuers.csv is one row per charter (issuer_id, issuer_name, fdic_cert, bank_name, kind, sec_cik, valid_from, valid_to, merged_into, aliases, note). valid_from is the FDIC established date (ESTYMD), valid_to the merger date (ENDEFYMD), both checked against /institutions on every FDIC run. sec_cik is filled only for Discover (1393612) and JPMorgan (19617), the two in the handoff's SEC links; the rest are v2 work. Per-charter `source_url` is the BankFind Suite financial reporting landing page; a per-bank deep link was not verified (the site answers 200 for any path).
- Unmatched-name report (task 6): the H2 2025 TCCP file flags 28 top-25 institutions; 9 match no charter: Credit One Bank, Fifth Third Bank, Merrick Bank, Regions Bank, Stride Bank, and four credit unions (Navy Federal, PenFed, BECU, America First; NCUA, never in FDIC data). Adding the five banks means their certs in issuers.csv plus 30 series rows; not done because they were not in the handoff's list and the report is meant to surface them, not to grow the pull silently. Ally Bank left the top-25 flag in H2 2025. TCCP spellings differ by vintage in case only ('FIRST NATIONAL BANK OF OMAHA' in H2 2023), which the normalization absorbs; 'Synchrony Financial' and 'U.S. Bancorp' are holding-company names and sit in aliases. A renamed FDIC charter shows up as a report line, not a failure; a merged one fails the fdic source until issuers.csv is updated.
- Not on the board, suggested for after task 8: a Performance chart from `v_fdic_issuer` (card loans, or an annualized net charge-off rate as 4 x `fdic_card_nco_q` over average card loans) for the largest issuers, and the industry rate against the G.19 charge-off rate; both need only a view over series already in facts. Goldman Sachs Bank USA reports zero card charge-offs in 2026 against $19.5bn of card loans; unexplained. The QBP 'Credit Card Banks' column (SPECGRP) is unverified.
- BEA API row caps on underlying-detail tables: may need per-year requests.
- Series browser (task 23): the whole dataset is embedded, which took docs/index.html from 0.63 MB to 1.39 MB. The encoding is one date grid per (source, period_type) plus a start index and a values array per series, so dates are stored once rather than per series; the naive form was measured first and the compact one is 0.41 MB. If the page ever needs to shrink, the browser payload is the thing to cut, not the charts. Keys are `metric|entity|tier|period_type|source` and are also the deep-link fragment.
- REJECTED 2026-09-08, CFPB complaints trends endpoint. Investigated as a faster distress signal and not shipped. Three reasons, all verified: (1) the product label changed twice, so `product=Credit card` returns 21,064 for 2016, **zero** for 2018 to 2022 and 89,978 for 2025, with `Credit card or prepaid card` carrying the middle years and mixing prepaid in; (2) `sub_product` is silently ignored as a filter, so the two labels cannot be cleanly separated or combined; (3) the database cannot be reconciled to any published figure, since the 2025 Consumer Response Annual Report states approximately 114,100 credit card complaints received in 2025 against 89,978 in the database, a 21 percent gap made of referrals, not-actionable complaints and unpublished ones. No golden is therefore possible, which the fetcher contract requires. Revisit only if the CFPB publishes a database-level count by product. The endpoint itself works fine (`.../search/api/v1/trends`, `lens=overview`, `trend_interval=month`, read `dateRangeBrush`, drop the current month) and per-company monthly series exist for the six largest issuers.
- REJECTED 2026-09-08, FDIC credit-card-bank aggregate. `SPECGRP:1` on the financials aggregate returns 4 institutions and $392bn of card loans at 2026 Q2, with 30-89 day and noncurrent shares of 1.06 and 1.12 percent. The QBP's own 'Credit Card Banks' column says 1.69 and 1.75 percent on about $512bn, so the API's specialization code is not the QBP's asset concentration group and the column cannot be reproduced. Not shipped rather than shipped mislabelled.
- Thesis watch (task 22): the thresholds live in `THESIS_TESTS` in render.py next to the claim and the note's filename. Changing a threshold means rewriting design/thesis-2026-09-08.html, and the tests assert the current values, so a drift in either shows up as a failing test rather than a quietly moved goalpost.
- Credit access survey (task 19): the workbook is re-published at one URL with no Last-Modified or ETag, so a new wave shows only as a content diff. Waves are fielded in February, June and October and a reading covers the twelve months to the survey month, so period_type is T and period_end is the last day of the survey month. `schema.shift_period` treats T without re-bucketing for exactly this reason, and `render.period_label` labels a wave by its month. The credit score bands are self-reported and the sub-680 band is about 150 respondents a wave: quote three-wave averages, never a single wave. Card-specific rejection rates exist only for the whole sample, not by score.
- The thesis (task 21) is at design/thesis-2026-09-08.html and is linked from the page's method list. It is dated and editorial on purpose: the page itself stays mechanical, and the note carries the position, the numbers behind it and the falsification test. Its prediction is that the NY Fed 90+ flow stays below 7.5 percent and the Fed charge-off rate below 4.2 percent through 2027; a break above either is the signal to rewrite it.
- Macro context (task 16): the debt service ratios lag about five months (Q1 2026 published by September 2026), so their `max_age_days` is 260 rather than the quarterly default. Card APR minus prime is the issuer-set margin; prime is monthly and the G.19 card rate quarterly, so `v_apr_spread` joins them at quarter ends. The payment rate in `v_y14_flows` leaves charge-offs inside payments because the Y-14 publishes a charge-off rate, not a dollar amount, which overstates it by a few tenths of a point.
- Findings from the v1.8 data (2026-09-11). Cards are the worst-performing consumer asset by a wide margin and the gap is not close: at 2026 Q2, 12.92 percent of card balances are 90+ days delinquent against 10.60 for student loans, 9.61 for 'other' (retail cards and consumer finance), 5.49 for auto, 0.99 for mortgages and 0.99 for home equity lines, with all household debt at 3.31. In flow terms the card rate (6.97) is beaten only by student loans (7.83, an artefact of the payment-pause restart) and is more than double auto (3.00) and four times mortgage (1.52). This is leg 1's strongest support yet: if the 2022-24 surge had been a macroeconomic event, the secured books would have moved with the cards, and they did not.
- Test runtime (task 39): every `render()` writes one PNG per chart through matplotlib, so the cost of the suite scaled with charts times the number of tests that render. The `page` fixture is now session-scoped and writes into `session_paths` (conftest), which is why three tests read `session_paths.docs` rather than `tmp_paths.docs`: anything that renders its own page must keep using the per-test `tmp_paths`. The 2026-09-11 push run before this change took about 50 minutes on the runner, almost all of it in the Tests step.
- The geography finding (2026-09-11, task 38): card delinquency rose in every one of the 51 areas with a full history between 2021 and 2025, by a median 3.8 points and by at least 2.3 in the state that moved least, so the loss cycle was national, not concentrated in a few places. The range across states widened at the same time, from 6.0 points in 2019 to 8.3 in 2025 (Nevada 16.3, Florida 14.9 and Texas 14.2 at the top; Wisconsin 8.0, Minnesota 8.6 and Vermont 9.0 at the bottom), and the ordering barely moves from year to year. This constrains the thesis rather than confirming it: 'concentrated' is off the table as a description, and what is left is a common national cause, which the note argues was origination standards rather than the labour market. Card debt per person is $4,350 nationally, highest in DC, Alaska and Hawaii.
- The caveat the v1.8 data raises against leg 1, recorded because it cuts the other way (2026-09-11): the card flow into serious delinquency has fallen from its 2024 Q4 peak (7.18 to 6.97) but the secured books have turned up over the same stretch, mortgages from 1.09 to 1.52 and home equity lines from 0.56 to 1.15, and auto is flat to slightly higher (2.96 to 3.00). The levels are still historically tiny (the mortgage 90+ share is 0.99 percent against an 8.89 percent peak in the financial crisis), so this is not yet a housing cycle, but 'every loss measure has turned' is no longer true of every asset and the note should not claim it. Watch the mortgage flow: cards improving while secured credit deteriorates would be the shape of a late-cycle household squeeze rather than a vintage washing out.
- The over-70 question in the thesis note's section 7 now has an answer (2026-09-11): their flow into serious delinquency is 6.34 percent on cards against 2.45 on auto loans and 1.80 on all their debt, so the deterioration is card-specific rather than a whole-balance-sheet problem for older households. Combined with the DFA finding that their consumer credit per household has been flat since 2023, the composition reading is the stronger one, and the note should be rewritten to say so rather than to list it as unexplained.
- Leg 4 has its control (2026-09-11): the same quarterly G.19 survey of the same banks puts the card rate at 22.15 percent, the 24-month personal loan at 11.86 and the 48-month new car loan at 7.47. The card margin is a card decision, not the cost of funds. At banks, the card net charge-off rate also runs an order of magnitude above single-family mortgages, which is the loss side of the same pricing.
- Student loan delinquency is an administrative break, not distress: flat from 2020 to 2023 under the federal payment pause (paused loans could not go delinquent), then jumping from 2024 as reporting restarted. The flow by age rises with age (5.32 for under-30s to 9.86 for the over-50s). Both the chart note and the series.csv scope note say this; never read the 2024 jump as new hardship.
- Findings from the v1.7 data (2026-09-11, recorded so the next session does not rediscover them). DFA: the bottom half of households by wealth owes 51.8 percent of all consumer credit (57.2 in 2019, 52.8 in 2007) and holds deposits worth 30 cents per dollar of it (22 in 2019, 23 in 2007); its consumer credit is 62 percent of its net worth against 129 percent in 2019, so the buffer behind the most exposed half is better than before the pandemic, not worse (leg 2). Consumer credit per household for heads 70 and over went from $14.4k in 2019 to $16.2k in 2023 and has been flat since, while their share of the total rose from 8.1 to 9.2 percent and the under-40s' fell from 40.5 to 34.9; rising over-70 delinquency on flat debt per household reads as composition more than distress (section 7). By income, the top fifth's share of consumer credit rose from 29.4 to 35.1 percent since 2019 and the bottom three fifths' fell from 43.3 to 39.0. CFPB trends: the below-prime (under 660) share of new card credit lines across all lenders was 7.6 percent in 2019, 9.5 in 2021 (the highest since 2007, the vintage behind leg 1), 8.4 in 2022, then 6.6, 5.8, 6.2 and 6.0 in January 2026, while superprime went from 74.8 to 81.6 percent; the large banks' Y-14 figure (4.0 percent) sits below the all-lender one (6.0), so the small subprime lines are written mostly by smaller lenders. Cards opened hit a record 8.6 million a month in January 2026 (+18.7 percent on the year, $54bn of new lines) while the inquiry index fell from 285 in 2023 to 228 and the tightness index to 78.6, its lowest: fewer applications, more of them approved, nearly all of the dollars to superprime (leg 5). BEA: households pay an effective 11.65 percent on all consumer credit (8.39 in 2019, 11.32 in 2007), up 3.3 points against 5.3 points on the card APR, and nonmortgage interest is 2.53 percent of disposable income against 2.15 in 2019 and 2.77 in 2007 (leg 4 in dollars: real, but below the 2007 burden). Initial claims 206k, 20 percent below a year ago. None of the three thesis tests moved.
- FDIC realized card yield (task 30, resolved 2026-09-11): RIAD B485 (interest and fee income on credit card loans) is not in the BankFind financials API. The API returns 161 default fields and silently drops every candidate name (ILNCRCD, ICRCD, IFEE, ILNCON, ILNRE and others), while known off-default fields (P3CRCD, DRCRCDQ) come back when asked, so the field is absent, not hidden. Interest income by loan type is not published there at all. The only public route is the FFIEC CDR bulk call-report zips (tens of MB a quarter), a per-quarter-extract job like NCUA's; not scheduled.
- Rejected 2026-09-10: Philly Fed Consumer Credit Explorer (card balances and delinquency by age, score and neighborhood from the Equifax panel): 'Because of data vendor restrictions, we are not able to provide any series from the tool in spreadsheet format'. No download, no API.
- DFA timing and revisions: the DFA zip is re-published about a week after each Z.1 (2026 Q1: Z.1 2026-06-11, DFA 2026-06-18), and every release revises the whole history, so only the 2023 year-end consumer credit golden is live; 2024, 2025 and the 2025 deposits check are fixture-only. The September Z.1 (2026-09-11) carries the annual revision and the DFA for 2026 Q2 should follow about 2026-09-18; if the 2023 golden then turns amber, re-base it from the new PDF's S14.b table (page number may move) and refresh tests/fixtures/dfa/dfa.zip (three detail files plus the dictionary, built from the real zip).
- cfpb_cct: the group files (score, age) are scaled and seasonally adjusted separately and sum to within 5 and 3 percent of the total file; the fetcher requires 6. Both indexes (inquiry, tightness) are January 2010 = 100. The neighborhood-income file stops in April 2025 and is not loaded. If the CFPB stops updating, the source turns stale at 330 days for originations.
- The page is now 2.9 MB (65 charts, 65 PNGs, 508 series in the browser); the browser payload is still the thing to cut if it must shrink.
- Findings the added data produced (2026-09-08, recorded so the next session does not rediscover them): card APR minus prime is at a record 15.4 points against about 7 in the 1990s and 10.7 in 2015-19; card debt is 5.7 percent of disposable income against a 9.5 percent peak in 2008 and 6.7 percent in 2019; real balances are 15 percent below their 2008 peak; the charge-off rate ran 1.0 to 1.2 points above what unemployment implied through 2024 and that gap is now 0.26 points; the Y-14 payment rate went from 58 percent in 2012 to 97 percent now while the revolving share fell from 78 to 70 percent; sentiment is 55 against a 95 average in 2015-19 while payment rates set records.
- Findings from the credit union data (2026-09-10, task 29): federally insured credit unions hold $86bn of card loans and Navy Federal alone $32.7bn, 38 percent of that; PenFed, BECU and America First are $1bn to $1.7bn each. Navy Federal and PenFed have sat on the 18 percent federal rate cap since 2022 while the bank APR on accounts assessed interest is 22.15 percent; BECU charges 12.5 and America First 15. Credit union card losses now run above banks: the industry's annualized net charge-off rate is 5.3 percent (2026 Q1) against 3.8 for commercial banks (Fed, SA, 2026 Q2), Navy Federal is at 7.1 and PenFed at 8.6 after peaking above 11 around the turn of 2025. The rate cap and member lending make this a read on a different underwriting model, which the chart note says; it is also the first place on the page where a lender group's losses have not turned down, so it belongs in the next thesis revision as a check on claim (1).
- Page (v1.1): the NBER recession list lives in render.py RECESSIONS (peak month to trough month) and is the one place to add a new one. The 2015-2019 benchmark is the mean of the first series over that window. The default five-year window is a client-side setScale; the PNGs draw the same window. The headline block's 'highest/lowest since' needs a gap of at least three years to say anything, so most readings carry only the change on the year. Roll-up series (ISSUER:<id>) have no facts row, so their data-as-of is the source's newest load. The FDIC rate view needs consecutive quarters for the average-loans denominator; a charter with a gap shows no rate that quarter.
- Credit One securitizes most of its receivables, so its on-book card loans ($2.0bn) understate the program; Stride Bank books $2.9bn of sponsor-program card loans with zero charge-offs (losses sit with the partners). Both are in the roll-up data but not on the issuer charts, which show the six largest books.
- Task 8 check: `checks/verify_releases.py` lists the recent runs of the refresh workflow (scheduled by default, `--event push` for the others), matches each to the bot commit it pushed by the health.json generated_at inside the run's window, and prints every source's status; it exits 0 with PASS once the newest two scheduled runs are green with every source ok. The cron is 22:00 UTC; GitHub started the 2026-09-07 one at 00:08 UTC the next day, so expect delays of an hour or two. Needs `gh` and a fetched origin/main.
- PNG export (task 7): the images are drawn with matplotlib's bundled DejaVu Sans on the Agg backend. Bytes are stable across renders on one machine (tested); a different OS or freetype build (the Ubuntu runner versus this Windows machine) shifts a few pixels. CORRECTED 2026-09-11: this is not a one-off that settles on the runner's bytes, it is a ping-pong. Every render alternation rewrites every image, and the byte counts flip between exactly two values per machine (docs/img/y14_utilization.png is 60022 bytes rendered on this Windows machine and 67036 on the runner, and it has flipped on each of the last three commits that changed machine). So `git status` after any local `carddash render` shows all 75 images modified and that is noise, not a change: check it by comparing against the last LOCAL render, not against HEAD. Do not commit local image bytes unless the data actually moved; restore them with `git checkout -- docs/img` and let the scheduled run on the runner own them. The images carry the chart caption (source, cadence, latest period) but never the pull date.
- What changed (task 7): revisions.csv does not exist yet, so the block shows revisions only after the first real revision (likely the next G.19 release). New periods are 'beyond the previously loaded frontier' per source and cadence; a value revised in an old period is never a new period, and a first load prints a count and the latest period rather than a list.
