#!/usr/bin/env python3
import duckdb
import os
import sys

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
OUTLIERS_FILE = os.path.join(JOB_DIR, "outliers.md")

def execute_query_to_md(conn, f, header, description, query):
    try:
        results = conn.execute(query).fetchall()
        cols = [d[0] for d in conn.description]
    except Exception as e:
        f.write(f"### {header}\n\nError executing query: {e}\n\n")
        return

    f.write(f"### {header}\n")
    f.write(f"{description}\n\n")
    
    if not results:
        f.write("**Status: PASS (0 rows found)**\n\n")
        return
        
    f.write(f"**Status: FAIL ({len(results)} rows found)**\n\n")
    
    # Write markdown table
    f.write("| " + " | ".join(cols) + " |\n")
    f.write("|" + "|".join(["---"] * len(cols)) + "|\n")
    for r in results:
        f.write("| " + " | ".join([str(x) if x is not None else "" for x in r]) + " |\n")
    f.write("\n")

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)

    # 1. Build the projection view
    conn.execute("""
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
                                    AND r.pricing_type = 'OnDemand'
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
                  ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) * COALESCE(c1.rate_usd, od.rate_usd)
                END AS gcp_cost_1yr_cud,
                CASE m.strategy
                  WHEN 'ignore'      THEN 0
                  WHEN 'passthrough' THEN c.aws_amortized_cost
                  ELSE c.total_usage * COALESCE(m.unit_multiplier, 1) * COALESCE(c3.rate_usd, od.rate_usd)
                END AS gcp_cost_3yr_cud
        FROM    aws_li_catalog c
        LEFT JOIN aws_li_to_gcp_li m ON m.aws_li_key = c.aws_li_key
        LEFT JOIN od_pick od ON od.aws_li_key = m.aws_li_key AND od.gcp_sku_id = m.gcp_sku_id
        LEFT JOIN c1_pick c1 ON c1.aws_li_key = m.aws_li_key AND c1.gcp_sku_id = m.gcp_sku_id
        LEFT JOIN c3_pick c3 ON c3.aws_li_key = m.aws_li_key AND c3.gcp_sku_id = m.gcp_sku_id;
    """)

    with open(OUTLIERS_FILE, "w") as f:
        f.write("# Outlier Triage Report\n\n")
        f.write("Review the following flagged rows and fix the mappings in the database.\n\n")

        execute_query_to_md(conn, f, "A1. Big-dollar deviations", 
            "Meaningful rows where GCP cost has shifted noticeably from AWS.",
            """
            SELECT aws_li_key, product, gcp_service, gcp_sku_id,
                   ROUND(aws_amortized_cost,2) AS aws,
                   ROUND(gcp_projected_cost,2) AS gcp,
                   ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection
            WHERE  strategy NOT IN ('ignore','passthrough')
              AND  aws_amortized_cost > 50
              AND  (
                ( gcp_service IN ('Compute Engine','Cloud SQL','AlloyDB','Cloud Memorystore',
                                  'Cloud Memorystore for Redis','Cloud Memorystore for Memcached')
                  AND ( gcp_projected_cost > aws_amortized_cost * 1.3
                     OR gcp_projected_cost < aws_amortized_cost * 0.77 )
                )
                OR
                ( gcp_service NOT IN ('Compute Engine','Cloud SQL','AlloyDB','Cloud Memorystore',
                                      'Cloud Memorystore for Redis','Cloud Memorystore for Memcached')
                  AND ( gcp_projected_cost > aws_amortized_cost * 1.5
                     OR gcp_projected_cost < aws_amortized_cost * 0.667 )
                )
              );
            """)

        execute_query_to_md(conn, f, "A2. Wildly implausible ratios", 
            "Ratio of 100x or 0.01x is almost always a unit multiplier bug.",
            """
            SELECT aws_li_key, product, gcp_service, gcp_sku_id,
                   ROUND(aws_amortized_cost,2) AS aws,
                   ROUND(gcp_projected_cost,2) AS gcp,
                   ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection
            WHERE  strategy NOT IN ('ignore','passthrough')
              AND  aws_amortized_cost > 1
              AND  ( gcp_projected_cost > aws_amortized_cost * 3
                  OR gcp_projected_cost < aws_amortized_cost * 0.5 );
            """)

        execute_query_to_md(conn, f, "B. Phantom GCP cost", 
            "AWS row had ~0 cost but GCP projection is large. HARD RULE: unit_multiplier bug.",
            """
            SELECT aws_li_key, product, gcp_service, gcp_sku_id,
                   total_usage, ROUND(gcp_projected_cost,2) AS gcp
            FROM   gcp_projection
            WHERE  aws_amortized_cost <= 1
              AND  gcp_projected_cost > 10;
            """)

        execute_query_to_md(conn, f, "C. Zero rate on a billable row", 
            "SKU resolved but rate_usd=0 and the AWS row carries non-trivial cost.",
            """
            SELECT m.aws_li_key, m.gcp_sku_id, c.aws_amortized_cost
            FROM   aws_li_to_gcp_li m
            JOIN   aws_li_catalog c USING (aws_li_key)
            JOIN   gcp_sku_rates  r ON r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'OnDemand'
            WHERE  m.strategy IN ('map','break_down')
              AND  r.rate_usd = 0
              AND  c.aws_amortized_cost > 50;
            """)

        execute_query_to_md(conn, f, "D. Cross-service mismatch", 
            "Mapping declares service X but the resolved SKU is registered under service Y in the Catalog.",
            """
            SELECT m.aws_li_key, m.gcp_service AS mapping_says, r.gcp_service AS sku_actually,
                   m.gcp_sku_id
            FROM   aws_li_to_gcp_li m
            JOIN   gcp_sku_rates    r ON r.gcp_sku_id = m.gcp_sku_id
            WHERE  m.strategy IN ('map','break_down')
              AND  m.gcp_service IS NOT NULL AND r.gcp_service IS NOT NULL
              AND  m.gcp_service != r.gcp_service;
            """)

        execute_query_to_md(conn, f, "E. Missing CUD Alias", 
            "Compute Engine, Cloud SQL, and Memorystore should all have Commit1Yr rows.",
            """
            SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_id
            FROM   aws_li_to_gcp_li m
            WHERE  m.strategy IN ('map','break_down')
              AND  m.gcp_service IN ('Compute Engine','Cloud SQL',
                                     'Cloud Memorystore','Cloud Memorystore for Redis',
                                     'Cloud Memorystore for Memcached')
              AND  m.component IN ('core','ram')
              AND  NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                               WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'Commit1Yr');
            """)

        execute_query_to_md(conn, f, "F. Unit-multiplier sanity check", 
            "For non-trivial AWS rows, the projected OD cost should be within 0.5x-2x of AWS cost.",
            """
            SELECT aws_li_key, product, gcp_service, gcp_sku_id,
                   ROUND(aws_amortized_cost,2) AS aws,
                   ROUND(gcp_projected_cost,2) AS gcp_od,
                   unit_multiplier,
                   ROUND(gcp_projected_cost / NULLIF(aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection
            WHERE  strategy = 'map'
              AND  pricing_model = 'OnDemand'
              AND  line_item_type IN ('Usage')
              AND  aws_amortized_cost > 20
              AND  ( gcp_projected_cost > aws_amortized_cost * 2
                  OR gcp_projected_cost < aws_amortized_cost * 0.5 );
            """)

        execute_query_to_md(conn, f, "G. break_down multiplier verification", 
            "Catches cases where an inferred multiplier doesn't match the catalog.",
            """
            SELECT m.aws_li_key, c.operation, m.component,
                   m.unit_multiplier AS mapped,
                   CASE m.component
                     WHEN 'core' THEN c.instance_vcpus
                     WHEN 'ram'  THEN c.instance_ram_gb
                   END AS spec_value,
                   ABS(m.unit_multiplier - CASE m.component
                     WHEN 'core' THEN c.instance_vcpus
                     WHEN 'ram'  THEN c.instance_ram_gb
                   END) AS delta,
                   ROUND(c.aws_amortized_cost, 2) AS aws_cost
            FROM   aws_li_to_gcp_li m
            JOIN   aws_li_catalog   c USING (aws_li_key)
            WHERE  m.strategy = 'break_down'
              AND  m.component IN ('core','ram')
              AND  c.instance_vcpus IS NOT NULL
              AND  CASE m.component
                     WHEN 'core' THEN ABS(m.unit_multiplier - c.instance_vcpus) > 0.5
                     WHEN 'ram'  THEN ABS(m.unit_multiplier - c.instance_ram_gb) > 1.0
                   END
            ORDER BY delta DESC;
            """)

        execute_query_to_md(conn, f, "H. RI/CUD parity check", 
            "Committed rows where 1yr CUD equals OD rate (missing CUD rate row).",
            """
            SELECT m.aws_li_key, c.product, m.gcp_service, m.gcp_sku_id,
                   ROUND(p.gcp_projected_cost, 2)  AS gcp_od,
                   ROUND(p.gcp_cost_1yr_cud, 2)    AS gcp_1yr,
                   ROUND(p.gcp_cost_3yr_cud, 2)    AS gcp_3yr
            FROM   gcp_projection p
            JOIN   aws_li_to_gcp_li m USING (aws_li_key)
            JOIN   aws_li_catalog   c USING (aws_li_key)
            WHERE  m.strategy IN ('map','break_down')
              AND  m.component IN ('core','ram')
              AND  m.gcp_service IN ('Compute Engine','Cloud SQL','AlloyDB',
                                     'Cloud Memorystore','Cloud Memorystore for Redis',
                                     'Cloud Memorystore for Memcached')
              AND  p.gcp_cost_1yr_cud = p.gcp_projected_cost
              AND  c.aws_amortized_cost > 10;
            """)

        execute_query_to_md(conn, f, "I. NULL projection", 
            "Mapped rows where gcp_projected_cost IS NULL (rate-fill missed this SKU).",
            """
            SELECT m.aws_li_key, c.product, c.gcp_region, m.gcp_service, m.gcp_sku_id,
                   ROUND(c.aws_amortized_cost, 2) AS aws_cost
            FROM   gcp_projection p
            JOIN   aws_li_to_gcp_li m USING (aws_li_key)
            JOIN   aws_li_catalog   c USING (aws_li_key)
            WHERE  m.strategy IN ('map','break_down')
              AND  p.gcp_projected_cost IS NULL
              AND  c.aws_amortized_cost > 1
            ORDER BY c.aws_amortized_cost DESC;
            """)

if __name__ == "__main__":
    main()
