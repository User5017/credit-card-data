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
