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
