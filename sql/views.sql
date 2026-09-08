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

-- Offered vs paid: the TCCP median of the highest purchase APR issuers offer (semiannual, every respondent)
-- matched as-of to the G.19 rate on accounts assessed interest (quarterly). Keyed on the G.19 series so
-- render.py can select it like any other, with the offered value and the spread as extra fields.
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
  WHERE metric = 'tccp_purchase_apr_max_median' AND entity = 'TCCP_ALL' AND tier = 'all' AND source = 'tccp'
)
SELECT p.metric, p.entity, p.entity_type, p.tier, p.period_type, p.source, p.period_end,
       p.paid_apr, o.offered_apr, o.period_end AS offered_period_end,
       o.offered_apr - p.paid_apr AS spread_pct_pts
FROM paid p
ASOF JOIN offered o ON p.period_end >= o.period_end;

-- FDIC issuer roll-up: the per-charter series (entity CERT:<n>, source fdic) summed by issuer and period, acquirer
-- plus acquired charters, so an issuer's line does not jump when a book moves between charters (Discover Bank into
-- Capital One, N.A. in 2025 Q2). crosswalks/issuers.csv maps each charter to its issuer and, for charters that
-- merged out, to the surviving charter (merged_into), whose issuer names the roll-up. n_certs says how many charters
-- carried the item that quarter. Keyed like facts (entity ISSUER:<issuer_id>) so render.py can select it.
CREATE OR REPLACE VIEW v_fdic_issuer AS
WITH rollup AS (
  SELECT i.fdic_cert AS cert,
         COALESCE(s.issuer_id, i.issuer_id) AS issuer_id,
         COALESCE(s.issuer_name, i.issuer_name) AS issuer_name
  FROM issuers i
  LEFT JOIN issuers s ON s.fdic_cert = i.merged_into
)
SELECT f.metric,
       'ISSUER:' || r.issuer_id AS entity,
       'issuer' AS entity_type,
       f.tier, f.period_type, f.source,
       CAST(f.period_end AS DATE) AS period_end,
       SUM(f.value) AS value,
       COUNT(*) AS n_certs,
       r.issuer_name
FROM facts f
JOIN rollup r ON f.entity = 'CERT:' || r.cert
WHERE f.source = 'fdic' AND f.entity_type = 'bank'
GROUP BY f.metric, r.issuer_id, r.issuer_name, f.tier, f.period_type, f.source, f.period_end;
