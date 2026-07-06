#!/usr/bin/env python3
"""
projection_view.py — single source of truth for the gcp_projection VIEW.

The VIEW derives per-line-item GCP cost (OnDemand / 1yr / 3yr CUD) from
aws_li_catalog × aws_li_to_gcp_li × gcp_sku_rates. It is created as soon as
rates exist (end of Phase 4 / apply_rates.py) so the Phase-4 gate and the
validator autofix can query it, and re-created idempotently in Phase 5
(detect_outliers.py). Both callers import create_projection_view() from here so
the SQL never drifts between phases.
"""

_PROJECTION_VIEW_SQL = """
CREATE OR REPLACE VIEW gcp_projection AS
WITH od_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = CASE
                                WHEN c.pricing_model = 'Spot' THEN 'Preemptible'
                                ELSE 'OnDemand'
                              END
  GROUP BY m.aws_li_key, m.gcp_sku_id
),
c1_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'Commit1Yr'
  GROUP BY m.aws_li_key, m.gcp_sku_id
),
c3_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END)
         ) AS rate_usd
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'Commit3Yr'
  GROUP BY m.aws_li_key, m.gcp_sku_id
)
SELECT  c.aws_li_key, c.product, c.aws_region, c.gcp_region,
        c.line_item_type, c.pricing_model, c.is_workload,
        c.total_usage, c.aws_amortized_cost,
        m.strategy, m.gcp_service, m.gcp_sku_id, m.component,
        m.unit_multiplier, m.projection_note,
        -- unit_multiplier is COALESCEd to 1: a NULL multiplier must not
        -- silently null out the whole cost (x*NULL=NULL) and vanish from
        -- SUM(). rate_usd is deliberately NOT coalesced — a NULL rate is a
        -- genuine coverage gap the gate must catch, not paper over.
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) * od.rate_usd
        END AS gcp_projected_cost,
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          -- Spot/Preemptible VMs cannot be combined with CUDs on GCP.
          -- Use the preemptible rate (od.rate_usd) for both CUD columns so the
          -- invariant gcp_od >= gcp_1yr_cud >= gcp_3yr_cud always holds and
          -- the report doesn't show CUD > OD for Spot rows.
          ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) *
               CASE WHEN c.pricing_model = 'Spot'
                    THEN od.rate_usd
                    ELSE COALESCE(c1.rate_usd, od.rate_usd)
               END
        END AS gcp_cost_1yr_cud,
        CASE m.strategy
          WHEN 'ignore'      THEN 0
          WHEN 'passthrough' THEN c.aws_amortized_cost
          ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) *
               CASE WHEN c.pricing_model = 'Spot'
                    THEN od.rate_usd
                    ELSE COALESCE(c3.rate_usd, od.rate_usd)
               END
        END AS gcp_cost_3yr_cud
FROM    aws_li_catalog c
LEFT JOIN aws_li_to_gcp_li m ON m.aws_li_key = c.aws_li_key
LEFT JOIN od_pick od ON od.aws_li_key = m.aws_li_key AND od.gcp_sku_id = m.gcp_sku_id
LEFT JOIN c1_pick c1 ON c1.aws_li_key = m.aws_li_key AND c1.gcp_sku_id = m.gcp_sku_id
LEFT JOIN c3_pick c3 ON c3.aws_li_key = m.aws_li_key AND c3.gcp_sku_id = m.gcp_sku_id;
"""


def create_projection_view(conn):
    """(Re)create the gcp_projection VIEW. Requires aws_li_catalog,
    aws_li_to_gcp_li, and gcp_sku_rates to exist. Idempotent."""
    conn.execute(_PROJECTION_VIEW_SQL)
