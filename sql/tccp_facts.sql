-- Facts from the TCCP product table (tccp_products: one row per card product per half-year, APRs as published,
-- as fractions). The tccp fetcher runs this in an in-memory DuckDB over the freshly parsed products. Output is percent.
-- Every aggregation choice lives here, not in the parser:
--   Placeholders: an APR of 0 or above 100% counts as not reported (the files carry 9.99 and 0 fillers).
--   Issuer groups: TCCP_TOP25 is 'Issued by Top 25 Institution' true, TCCP_OTHER false, TCCP_ALL every product.
--     The flag exists from H2 2023, so the two split groups start there.
--   tccp_purchase_apr_max_median: median over products offering a purchase APR of the highest APR offered to new accounts.
--   tccp_purchase_apr_median by credit tier: median over products that report a median APR for that tier, the
--     definition behind the CFPB's February 2024 report ('Credit card data: Small issuers offer lower rates').
--   tccp_issuers_max_apr_over_30_count: issuers with at least one product whose highest purchase APR exceeds 30%,
--     the report's headline count (15 issuers in H1 2023).
--   Duplicate product rows stay as published (a handful per file). Medians are unweighted across products.
-- One statement, no semicolons anywhere (the loader splits on them).
WITH p AS (
  SELECT period_end, institution, top25,
         CASE WHEN purchase_apr_max > 0 AND purchase_apr_max <= 1 THEN purchase_apr_max END AS apr_max,
         CASE WHEN purchase_apr_ge720 > 0 AND purchase_apr_ge720 <= 1 THEN purchase_apr_ge720 END AS apr_ge720,
         CASE WHEN purchase_apr_620_719 > 0 AND purchase_apr_620_719 <= 1 THEN purchase_apr_620_719 END AS apr_620_719,
         CASE WHEN purchase_apr_le619 > 0 AND purchase_apr_le619 <= 1 THEN purchase_apr_le619 END AS apr_le619
  FROM tccp_products
  WHERE purchase_apr_offered
),
g AS (
  SELECT 'TCCP_ALL' AS entity, * FROM p
  UNION ALL SELECT 'TCCP_TOP25' AS entity, * FROM p WHERE top25
  UNION ALL SELECT 'TCCP_OTHER' AS entity, * FROM p WHERE NOT top25
),
tiered AS (
  SELECT entity, period_end, 'superprime' AS tier, apr_ge720 AS apr FROM g
  UNION ALL SELECT entity, period_end, '620_719' AS tier, apr_620_719 AS apr FROM g
  UNION ALL SELECT entity, period_end, 'le619' AS tier, apr_le619 AS apr FROM g
)
SELECT 'tccp_purchase_apr_max_median' AS metric, entity, 'all' AS tier, period_end, 100 * median(apr_max) AS value
FROM g WHERE apr_max IS NOT NULL GROUP BY entity, period_end
UNION ALL
SELECT 'tccp_purchase_apr_median' AS metric, entity, tier, period_end, 100 * median(apr) AS value
FROM tiered WHERE apr IS NOT NULL GROUP BY entity, tier, period_end
UNION ALL
SELECT 'tccp_issuers_max_apr_over_30_count' AS metric, 'TCCP_ALL' AS entity, 'all' AS tier, period_end,
       count(DISTINCT institution) AS value
FROM p WHERE apr_max > 0.30 GROUP BY period_end
UNION ALL
SELECT 'tccp_product_count' AS metric, 'TCCP_ALL' AS entity, 'all' AS tier, period_end, count(*) AS value
FROM tccp_products GROUP BY period_end
UNION ALL
SELECT 'tccp_issuer_count' AS metric, 'TCCP_ALL' AS entity, 'all' AS tier, period_end,
       count(DISTINCT institution) AS value
FROM tccp_products GROUP BY period_end
