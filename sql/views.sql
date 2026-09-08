-- Derived metrics over the facts table. Adding a metric means adding a view here.
-- facts is registered by render.py / any DuckDB session from data/facts.csv.

CREATE OR REPLACE VIEW v_latest AS
SELECT * EXCLUDE (rn)
FROM (
  SELECT *,
         row_number() OVER (PARTITION BY metric, entity, tier, period_type, source ORDER BY period_end DESC) AS rn
  FROM facts
)
WHERE rn = 1;

-- Year-over-year change. Exact year shift for period series, 52 weeks for weekly series.
-- (Keep semicolons out of comments: render.py splits statements on them.)
CREATE OR REPLACE VIEW v_growth AS
WITH f AS (
  SELECT metric, entity, entity_type, tier, source, period_type,
         CAST(period_end AS DATE) AS period_end, value
  FROM facts
)
SELECT a.metric, a.entity, a.entity_type, a.tier, a.source, a.period_type, a.period_end,
       a.value,
       b.value AS value_1y_ago,
       100.0 * (a.value / b.value - 1) AS yoy_pct
FROM f a
JOIN f b
  ON  a.metric = b.metric AND a.entity = b.entity AND a.tier = b.tier
  AND a.source = b.source AND a.period_type = b.period_type
  AND b.period_end = CASE
        WHEN a.period_type = 'W' THEN CAST(a.period_end - INTERVAL 364 DAY AS DATE)
        WHEN a.period_type = 'D' THEN CAST(a.period_end - INTERVAL 1 YEAR AS DATE)
        ELSE last_day(CAST(a.period_end - INTERVAL 1 YEAR AS DATE))
      END
WHERE b.value <> 0;

-- Offered vs paid: the TCCP median purchase APR offered to applicants with scores 620 to 719 (semiannual, every
-- respondent, the middle of the three tier bands the chart draws) matched as-of to the G.19 rate on accounts assessed
-- interest (quarterly). Keyed on the G.19 series so render.py can select it like any other, with the offered value
-- and the spread (percentage points) as extra fields. The spread exists only through the last offered period: a
-- quarter after it has no offered reading to compare with, so the line stops instead of carrying the last semiannual
-- value forward.
CREATE OR REPLACE VIEW v_offered_vs_paid AS
WITH paid AS (
  SELECT metric, entity, entity_type, tier, period_type, source,
         CAST(period_end AS DATE) AS period_end, value AS paid_apr
  FROM facts
  WHERE metric = 'card_apr_assessed_interest' AND entity = 'COMBANKS_ALL' AND tier = 'all' AND source = 'fred'
),
offered AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS offered_apr
  FROM facts
  WHERE metric = 'tccp_purchase_apr_median' AND entity = 'TCCP_ALL' AND tier = '620_719' AND source = 'tccp'
)
SELECT p.metric, p.entity, p.entity_type, p.tier, p.period_type, p.source, p.period_end,
       p.paid_apr, o.offered_apr, o.period_end AS offered_period_end,
       o.offered_apr - p.paid_apr AS spread_pct_pts
FROM paid p
ASOF JOIN offered o ON p.period_end >= o.period_end
WHERE p.period_end <= (SELECT max(period_end) FROM offered);

-- FDIC issuer roll-up: the per-charter series (entity CERT:<n>, source fdic) summed by issuer and period, acquirer
-- plus acquired charters, so an issuer's line does not jump when a book moves between charters (Discover Bank into
-- Capital One, N.A. in 2025 Q2). crosswalks/issuers.csv maps each charter to its issuer and, for charters that
-- merged out, to the surviving charter (merged_into), whose issuer names the roll-up (dim_issuer below). n_certs says
-- how many charters carried the item that quarter. Keyed like facts (entity ISSUER:<issuer_id>) so render.py can
-- select it. Rows are bounded by the charter's valid_from and valid_to (a charter cannot file after its merger,
-- so the bound documents the intent more than it filters).
-- dim_issuer: crosswalks/issuers.csv with the issuer each charter rolls up to. A charter that merged out rolls up to
-- the surviving charter's issuer (Discover Bank to Capital One). valid_from is the FDIC established date, valid_to the
-- merger date, both checked against the FDIC every run by the fdic fetcher.
CREATE OR REPLACE VIEW dim_issuer AS
SELECT i.fdic_cert, i.bank_name, i.kind, i.issuer_id, i.issuer_name,
       COALESCE(s.issuer_id, i.issuer_id) AS rollup_issuer_id,
       COALESCE(s.issuer_name, i.issuer_name) AS rollup_issuer_name,
       i.valid_from, i.valid_to, i.merged_into, i.sec_cik
FROM issuers i
LEFT JOIN issuers s ON s.fdic_cert = i.merged_into;

CREATE OR REPLACE VIEW v_fdic_issuer AS
SELECT f.metric,
       'ISSUER:' || d.rollup_issuer_id AS entity,
       'issuer' AS entity_type,
       f.tier, f.period_type, f.source,
       CAST(f.period_end AS DATE) AS period_end,
       SUM(f.value) AS value,
       COUNT(*) AS n_certs,
       d.rollup_issuer_name AS issuer_name
FROM facts f
JOIN dim_issuer d ON f.entity = 'CERT:' || d.fdic_cert
WHERE f.source = 'fdic' AND f.entity_type = 'bank'
  AND CAST(f.period_end AS DATE) >= d.valid_from
  AND (d.valid_to IS NULL OR CAST(f.period_end AS DATE) < d.valid_to)
GROUP BY f.metric, d.rollup_issuer_id, d.rollup_issuer_name, f.tier, f.period_type, f.source, f.period_end;

-- Offered range: the highest, lowest and middle (620-719) of the three TCCP tier medians per half-year, keyed on the
-- 620-719 series so render.py can select the band edges as fields. The sub-620 median is often the lowest of the
-- three (secured-card products dominate that tier), which is why the band is min to max, not sub-620 to superprime.
CREATE OR REPLACE VIEW v_tccp_offered_range AS
SELECT metric, entity, entity_type, '620_719' AS tier, period_type, source, CAST(period_end AS DATE) AS period_end,
       max(value) AS hi, min(value) AS lo,
       max(CASE WHEN tier = '620_719' THEN value END) AS mid
FROM facts
WHERE metric = 'tccp_purchase_apr_median' AND entity = 'TCCP_ALL' AND source = 'tccp'
  AND tier IN ('le619', '620_719', 'superprime')
GROUP BY metric, entity, entity_type, period_type, source, period_end;

-- FDIC rates: per issuer roll-up and for all insured institutions, the delinquency shares and the annualized net
-- charge-off rate the way the FDIC computes its own (4 x the quarter's net charge-offs over the average of the
-- beginning and end-of-quarter card loans, so it reproduces IDNTCRDQR to the rounding). The average needs the
-- previous quarter to be consecutive. Keyed on metric fdic_card_nco_q so render.py can select the rate fields. In a
-- merger quarter the acquirer's quarterly charge-offs cover the acquired book only from the merger date, so a
-- roll-up rate dips that quarter (Capital One 2025 Q2, Discover's April to mid-May losses were never reported).
CREATE OR REPLACE VIEW v_fdic_rates AS
WITH base AS (
  SELECT metric, entity, entity_type, tier, period_type, source, period_end, value, issuer_name FROM v_fdic_issuer
  UNION ALL
  SELECT metric, entity, entity_type, tier, period_type, source, CAST(period_end AS DATE) AS period_end, value,
         'All FDIC-insured institutions' AS issuer_name
  FROM facts WHERE source = 'fdic' AND entity = 'FDIC_ALL_INSURED'
),
w AS (
  SELECT entity, entity_type, tier, period_type, source, period_end, issuer_name,
         max(CASE WHEN metric = 'fdic_card_loans' THEN value END) AS loans,
         max(CASE WHEN metric = 'fdic_card_nco_q' THEN value END) AS nco_q,
         max(CASE WHEN metric = 'fdic_card_dq30_89' THEN value END) AS dq30_89,
         max(CASE WHEN metric = 'fdic_card_noncurrent' THEN value END) AS noncurrent
  FROM base
  GROUP BY entity, entity_type, tier, period_type, source, period_end, issuer_name
),
l AS (
  SELECT *,
         lag(loans) OVER (PARTITION BY entity, tier, period_type, source ORDER BY period_end) AS loans_prev,
         lag(period_end) OVER (PARTITION BY entity, tier, period_type, source ORDER BY period_end) AS period_prev
  FROM w
)
SELECT 'fdic_card_nco_q' AS metric, entity, entity_type, tier, period_type, source, period_end, issuer_name,
       loans, nco_q, dq30_89, noncurrent,
       CASE WHEN loans > 0 THEN 100.0 * dq30_89 / loans END AS dq30_89_share,
       CASE WHEN loans > 0 THEN 100.0 * noncurrent / loans END AS noncurrent_share,
       CASE WHEN loans_prev IS NOT NULL AND period_end = last_day(period_prev + INTERVAL 3 MONTH)
                 AND (loans + loans_prev) > 0
            THEN 400.0 * nco_q / ((loans + loans_prev) / 2.0) END AS nco_rate_annualized
FROM l;

-- Derived measures over series already in facts. Each view is keyed on one of its input series (metric, entity, tier,
-- period_type, source) so render.py can select a field from it exactly like a fact. Nothing here fetches anything.

-- Y-14 flows: what the large banks' cardholders actually do. revolver_share is the share of balances carrying interest.
-- payment_rate is payments as a share of the opening balance, derived as purchases minus the change in balances (the
-- accounting identity closing = opening + purchases - payments - charge-offs, with charge-offs left in payments
-- because the Y-14 charge-off series is a rate, not a dollar amount; the error is a few tenths of a point). It needs
-- consecutive quarters, so a gap in the panel leaves the quarter null. Per-account figures turn billions over
-- millions of accounts into dollars.
CREATE OR REPLACE VIEW v_y14_flows AS
WITH w AS (
  SELECT CAST(period_end AS DATE) AS period_end,
         max(CASE WHEN metric = 'y14_card_balances' THEN value END) AS balances,
         max(CASE WHEN metric = 'y14_card_revolving_balances' THEN value END) AS revolving,
         max(CASE WHEN metric = 'y14_card_purchase_volume' THEN value END) AS purchases,
         max(CASE WHEN metric = 'y14_card_accounts' THEN value END) AS accounts,
         max(CASE WHEN metric = 'y14_card_commitments' THEN value END) AS commitments
  FROM facts
  WHERE source = 'phillyfed' AND entity = 'Y14_CARD_FILERS' AND tier = 'all'
  GROUP BY period_end
),
l AS (
  SELECT *,
         lag(balances) OVER (ORDER BY period_end) AS prev_balances,
         lag(period_end) OVER (ORDER BY period_end) AS prev_period
  FROM w
)
SELECT 'y14_card_balances' AS metric, 'Y14_CARD_FILERS' AS entity, 'aggregate' AS entity_type, 'all' AS tier,
       'Q' AS period_type, 'phillyfed' AS source, period_end,
       balances, revolving, purchases, accounts, commitments,
       CASE WHEN balances > 0 THEN 100.0 * revolving / balances END AS revolver_share,
       CASE WHEN prev_balances > 0 AND period_end = last_day(prev_period + INTERVAL 3 MONTH)
            THEN 100.0 * (purchases - (balances - prev_balances)) / prev_balances END AS payment_rate,
       CASE WHEN accounts > 0 THEN 1000.0 * purchases / accounts END AS purchase_per_account,
       CASE WHEN accounts > 0 THEN 1000.0 * balances / accounts END AS balance_per_account,
       CASE WHEN accounts > 0 THEN 1000.0 * commitments / accounts END AS limit_per_account
FROM l;

-- NY Fed per-account measures: the same arithmetic on the all-lender credit report panel. Joint accounts are counted
-- twice in the account series (the NY Fed's own note), so the per-account dollars are a lower bound.
CREATE OR REPLACE VIEW v_hhdc_per_account AS
WITH w AS (
  SELECT CAST(period_end AS DATE) AS period_end,
         max(CASE WHEN metric = 'hhdc_card_balances' THEN value END) AS balances,
         max(CASE WHEN metric = 'hhdc_card_limit' THEN value END) AS limits,
         max(CASE WHEN metric = 'hhdc_card_accounts' THEN value END) AS accounts
  FROM facts
  WHERE source = 'nyfed_hhdc' AND entity = 'CCP_ALL' AND tier = 'all'
  GROUP BY period_end
)
SELECT 'hhdc_card_balances' AS metric, 'CCP_ALL' AS entity, 'aggregate' AS entity_type, 'all' AS tier,
       'Q' AS period_type, 'nyfed_hhdc' AS source, period_end,
       balances, limits, accounts,
       CASE WHEN limits > 0 THEN 100.0 * balances / limits END AS utilization,
       CASE WHEN accounts > 0 THEN 1000.0 * balances / accounts END AS balance_per_account,
       CASE WHEN accounts > 0 THEN 1000.0 * limits / accounts END AS limit_per_account,
       CASE WHEN accounts > 0 THEN 1000.0 * (limits - balances) / accounts END AS available_per_account
FROM w;

-- Card debt in context: as a share of disposable income (an annual rate, so the ratio reads as balances per dollar of
-- annual after-tax income) and restated in the price level of the latest CPI reading. The income ratio is only
-- defined at quarter ends, where the quarterly income series lands.
CREATE OR REPLACE VIEW v_card_burden AS
WITH rev AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS revolving
  FROM facts WHERE metric = 'revolving_credit_sa' AND entity = 'ALL_HOLDERS' AND source = 'fred'
),
dpi AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS dpi
  FROM facts WHERE metric = 'disposable_income_sa' AND entity = 'US_HOUSEHOLDS' AND source = 'fred'
),
cpi AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS cpi
  FROM facts WHERE metric = 'cpi_all_urban_sa' AND entity = 'US_ECONOMY' AND source = 'fred'
),
base AS (SELECT cpi AS cpi_base FROM cpi ORDER BY period_end DESC LIMIT 1)
SELECT 'revolving_credit_sa' AS metric, 'ALL_HOLDERS' AS entity, 'aggregate' AS entity_type, 'all' AS tier,
       'M' AS period_type, 'fred' AS source, r.period_end,
       r.revolving,
       r.revolving * b.cpi_base / c.cpi AS revolving_real,
       100.0 * r.revolving / d.dpi AS pct_of_disposable_income
FROM rev r
CROSS JOIN base b
LEFT JOIN cpi c ON c.period_end = r.period_end
LEFT JOIN dpi d ON d.period_end = r.period_end;

-- Card APR against the prime rate. Most variable card APRs are prime plus a margin, so the spread is the part the
-- issuer sets and the level is not. Prime is monthly and the G.19 card rate is quarterly, so they meet at quarter ends.
CREATE OR REPLACE VIEW v_apr_spread AS
WITH apr AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS apr
  FROM facts WHERE metric = 'card_apr_assessed_interest' AND entity = 'COMBANKS_ALL' AND source = 'fred'
),
prime AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS prime
  FROM facts WHERE metric = 'prime_rate' AND entity = 'COMBANKS_ALL' AND source = 'fred'
)
SELECT 'card_apr_assessed_interest' AS metric, 'COMBANKS_ALL' AS entity, 'aggregate' AS entity_type, 'all' AS tier,
       'Q' AS period_type, 'fred' AS source, a.period_end,
       a.apr, p.prime, a.apr - p.prime AS spread_over_prime
FROM apr a
JOIN prime p ON p.period_end = a.period_end;

-- Holder share: each G.19 holder's revolving credit as a share of the sum of the three published holders plus the
-- rest (the NSA total). Keyed on the holder's own series so render.py can select share_pct per entity.
CREATE OR REPLACE VIEW v_holder_share AS
WITH total AS (
  SELECT CAST(period_end AS DATE) AS period_end, value AS total
  FROM facts WHERE metric = 'revolving_credit_nsa' AND entity = 'ALL_HOLDERS' AND source = 'fred'
)
SELECT f.metric, f.entity, f.entity_type, f.tier, f.period_type, f.source, CAST(f.period_end AS DATE) AS period_end,
       f.value, t.total, 100.0 * f.value / t.total AS share_pct
FROM facts f
JOIN total t ON t.period_end = CAST(f.period_end AS DATE)
WHERE f.metric = 'revolving_credit_nsa' AND f.source = 'fred' AND f.entity <> 'ALL_HOLDERS' AND t.total > 0;

-- Monthly SCE answer sums: the share saying credit is harder (much plus somewhat) and the share saying the household
-- is worse off, for each horizon. Keyed on the 'much' series so render.py can select the sum as a field.
CREATE OR REPLACE VIEW v_sce_sums AS
WITH s AS (
  SELECT metric, entity, entity_type, tier, period_type, source, CAST(period_end AS DATE) AS period_end, value
  FROM facts WHERE source = 'nyfed_sce_monthly'
)
SELECT a.metric, a.entity, a.entity_type, a.tier, a.period_type, a.source, a.period_end,
       a.value AS much, b.value AS somewhat, a.value + b.value AS combined
FROM s a
JOIN s b ON b.entity = a.entity AND b.period_end = a.period_end
       AND b.metric = replace(replace(a.metric, '_much_harder', '_somewhat_harder'), '_much_worse', '_somewhat_worse')
WHERE a.metric LIKE '%_much_harder' OR a.metric LIKE '%_much_worse';
