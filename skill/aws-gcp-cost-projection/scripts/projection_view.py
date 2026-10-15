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
import re


def compute_dominant_region(conn) -> str:
    """Return the customer's dominant region (most common non-global gcp_region or aws_region)."""
    try:
        cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
        region_expr = None
        if "gcp_region" in cols and "aws_region" in cols:
            region_expr = "COALESCE(NULLIF(gcp_region, ''), NULLIF(aws_region, ''))"
        elif "gcp_region" in cols:
            region_expr = "NULLIF(gcp_region, '')"
        elif "aws_region" in cols:
            region_expr = "NULLIF(aws_region, '')"

        if region_expr:
            row = conn.execute(f"""
                SELECT {region_expr} AS reg, COUNT(*) as cnt
                FROM aws_li_catalog
                WHERE {region_expr} IS NOT NULL
                  AND lower({region_expr}) NOT IN ('global', 'unknown', '')
                GROUP BY reg
                ORDER BY cnt DESC, reg ASC
                LIMIT 1
            """).fetchone()
            if row and row[0]:
                return row[0].strip()
    except Exception:
        pass
    return "us-central1"



def _build_projection_view_sql(dominant_region: str) -> str:
    dom = re.sub(r'[^a-zA-Z0-9-]', '', str(dominant_region)) or "us-central1"
    return f"""
CREATE OR REPLACE VIEW gcp_projection AS
-- passthrough double-count guard: a passthrough's AWS cost belongs to the LINE ITEM,
-- not each component. If a passthrough row is split into N components (e.g. core+ram),
-- assigning the full aws_amortized_cost to every component and SUMing would N-multiply
-- the cost (confirmed real: SageMaker row AWS $1413.56 -> $2827.12, exactly 2x). Rank
-- components per aws_li_key by rowid; only rn=1 carries the cost, the rest carry $0, so
-- SUM() reconstructs the AWS cost exactly once. (rowid, not component label, keeps the
-- join back 1:1 — two rows can share a component label.)
WITH passthrough_rank AS (
  SELECT m.rowid AS row_id, m.aws_li_key,
         ROW_NUMBER() OVER (PARTITION BY m.aws_li_key ORDER BY m.rowid) AS rn
  FROM aws_li_to_gcp_li m
  WHERE m.strategy = 'passthrough'
),
od_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END),
           MAX(CASE WHEN r.region = '{dom}'      THEN r.rate_usd END)
         ) AS rate_usd,
         MAX(CASE
           WHEN (c.gcp_region = 'global' OR c.gcp_region IS NULL OR c.gcp_region = ''
                 OR c.aws_region = 'global' OR c.aws_region IS NULL OR c.aws_region = '')
            AND NOT EXISTS (
                SELECT 1 FROM gcp_sku_rates r_glob
                WHERE r_glob.gcp_sku_id = m.gcp_sku_id
                  AND r_glob.region = 'global'
                  AND r_glob.pricing_type = 'OnDemand'
            )
            AND EXISTS (
                SELECT 1 FROM gcp_sku_rates r_dom
                WHERE r_dom.gcp_sku_id = m.gcp_sku_id
                  AND r_dom.region = '{dom}'
                  AND r_dom.pricing_type = 'OnDemand'
            )
           THEN 'global charge; priced at {dom} rate'
           WHEN c.gcp_region IS NOT NULL AND c.gcp_region != '' AND c.gcp_region != 'global'
            AND NOT EXISTS (
                SELECT 1 FROM gcp_sku_rates r_reg
                WHERE r_reg.gcp_sku_id = m.gcp_sku_id
                  AND r_reg.region = c.gcp_region
                  AND r_reg.pricing_type = 'OnDemand'
            )
            AND NOT EXISTS (
                SELECT 1 FROM gcp_sku_rates r_glob2
                WHERE r_glob2.gcp_sku_id = m.gcp_sku_id
                  AND r_glob2.region = 'global'
                  AND r_glob2.pricing_type = 'OnDemand'
            )
            AND EXISTS (
                SELECT 1 FROM gcp_sku_rates r_dom2
                WHERE r_dom2.gcp_sku_id = m.gcp_sku_id
                  AND r_dom2.region = '{dom}'
                  AND r_dom2.pricing_type = 'OnDemand'
            )
           THEN 'priced at {dom} rate — no ' || c.gcp_region || ' rate'
           WHEN c.gcp_region IS NOT NULL AND c.gcp_region != ''
            AND NOT EXISTS (
                SELECT 1 FROM gcp_sku_rates r_reg2
                WHERE r_reg2.gcp_sku_id = m.gcp_sku_id
                  AND r_reg2.region IN (c.gcp_region, 'global', '{dom}')
                  AND r_reg2.pricing_type = 'OnDemand'
            )
           THEN 'no rate in ' || c.gcp_region || '/global/{dom}; unpriced'
           ELSE NULL
         END) AS dominant_note
  FROM   aws_li_to_gcp_li m
  JOIN   aws_li_catalog   c USING (aws_li_key)
  LEFT JOIN gcp_sku_rates r ON r.gcp_sku_id = m.gcp_sku_id
                            AND r.pricing_type = 'OnDemand'
  GROUP BY m.aws_li_key, m.gcp_sku_id
),
c1_pick AS (
  SELECT m.aws_li_key, m.gcp_sku_id,
         COALESCE(
           MAX(CASE WHEN r.region = c.gcp_region THEN r.rate_usd END),
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END),
           MAX(CASE WHEN r.region = '{dom}'      THEN r.rate_usd END)
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
           MAX(CASE WHEN r.region = 'global'     THEN r.rate_usd END),
           MAX(CASE WHEN r.region = '{dom}'      THEN r.rate_usd END)
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
        m.unit_multiplier,
        CASE
          WHEN od.dominant_note IS NOT NULL
            THEN CASE
                   WHEN m.projection_note IS NULL OR m.projection_note = '' THEN od.dominant_note
                   WHEN m.projection_note LIKE '%' || od.dominant_note || '%' THEN m.projection_note
                   ELSE m.projection_note || '; ' || od.dominant_note
                 END
          ELSE m.projection_note
        END AS projection_note,
        CASE m.strategy
          WHEN 'ignore'         THEN 0
          WHEN 'outlier_triage' THEN NULL
          WHEN 'passthrough'    THEN CASE WHEN COALESCE(pr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END
          ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) * od.rate_usd
        END AS gcp_projected_cost,
        CASE m.strategy
          WHEN 'ignore'         THEN 0
          WHEN 'outlier_triage' THEN NULL
          WHEN 'passthrough'    THEN CASE WHEN COALESCE(pr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END
          ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) *
               CASE WHEN c.pricing_model = 'Spot'
                    THEN od.rate_usd
                    ELSE COALESCE(c1.rate_usd, od.rate_usd)
               END
        END AS gcp_cost_1yr_cud,
        CASE m.strategy
          WHEN 'ignore'         THEN 0
          WHEN 'outlier_triage' THEN NULL
          WHEN 'passthrough'    THEN CASE WHEN COALESCE(pr.rn, 1) = 1 THEN c.aws_amortized_cost ELSE 0 END
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
LEFT JOIN c3_pick c3 ON c3.aws_li_key = m.aws_li_key AND c3.gcp_sku_id = m.gcp_sku_id
LEFT JOIN passthrough_rank pr ON pr.row_id = m.rowid;
"""

_PROJECTION_VIEW_SQL = _build_projection_view_sql("us-central1")


def create_projection_view(conn, dominant_region=None):
    """(Re)create the gcp_projection VIEW with dominant region fallback.
    Requires aws_li_catalog, aws_li_to_gcp_li, and gcp_sku_rates to exist. Idempotent.
    """
    if dominant_region is None:
        dominant_region = compute_dominant_region(conn)
    clean_reg = re.sub(r'[^a-zA-Z0-9-]', '', str(dominant_region)) or "us-central1"

    conn.execute("""
        SELECT table_type FROM information_schema.tables WHERE table_name = 'gcp_projection'
    """)
    row = conn.fetchone()
    if row and row[0] == "BASE TABLE":
        conn.execute("DROP TABLE gcp_projection")
    conn.execute(_build_projection_view_sql(clean_reg))
