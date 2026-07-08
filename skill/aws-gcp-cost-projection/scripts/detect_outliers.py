#!/usr/bin/env python3
import duckdb
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection_view import create_projection_view

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

    # 1. Build the projection view (shared definition — single source of truth)
    create_projection_view(conn)

    with open(OUTLIERS_FILE, "w") as f:
        f.write("# Outlier Triage Report\n\n")
        f.write("Review the following flagged rows and fix the mappings in the database.\n\n")

        # A1: Only flag OVER-projection (GCP > AWS) — under-projection (GCP cheaper) is the
        # expected outcome and not a bug. Thresholds: >1.3x for core infra, >1.5x for others.
        # Data Transfer excluded: GCP inter-zone ($0.01/GB) is legitimately 9x cheaper than
        # AWS inter-AZ ($0.09/GB) — flagging those as outliers floods the LLM with false positives.
        # Includes gcp_sku_name and unit_multiplier so LLM can fix without extra SELECTs.
        execute_query_to_md(conn, f, "A1. Over-projection (GCP costs more than AWS)",
            "GCP projected cost is materially higher than AWS — likely a wrong SKU or unit multiplier.",
            """
            SELECT p.aws_li_key, p.product, p.gcp_service, p.gcp_sku_id,
                   m.gcp_sku_name, m.unit_multiplier, m.component,
                   ROUND(p.aws_amortized_cost,2) AS aws,
                   ROUND(p.gcp_projected_cost,2) AS gcp,
                   ROUND(p.gcp_projected_cost / NULLIF(p.aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection p
            JOIN   aws_li_to_gcp_li m USING (aws_li_key)
            WHERE  p.strategy NOT IN ('ignore','passthrough')
              AND  p.aws_amortized_cost > 50
              AND  p.product NOT ILIKE '%Data Transfer%'
              AND  (
                ( p.gcp_service IN ('Compute Engine','Cloud SQL','AlloyDB','Cloud Memorystore',
                                    'Cloud Memorystore for Redis','Cloud Memorystore for Memcached')
                  AND p.gcp_projected_cost > p.aws_amortized_cost * 1.3
                )
                OR
                ( p.gcp_service NOT IN ('Compute Engine','Cloud SQL','AlloyDB','Cloud Memorystore',
                                        'Cloud Memorystore for Redis','Cloud Memorystore for Memcached')
                  AND p.gcp_projected_cost > p.aws_amortized_cost * 1.5
                )
              );
            """)

        # A2: Only flag extreme ratios — >10x over OR <5% of AWS cost.
        # GCP being 15-50% of AWS is EXPECTED for Compute Engine (GCP is cheaper).
        # The old <0.5x threshold caught almost all EC2 rows as false positives.
        # Includes gcp_sku_name, unit_multiplier, component so LLM can fix without extra SELECTs.
        execute_query_to_md(conn, f, "A2. Extreme ratio outliers (>10x over or <5% of AWS)",
            "Ratio >10x almost always means wrong SKU. Ratio <0.05x almost always means unit_multiplier bug.",
            """
            SELECT p.aws_li_key, p.product, p.gcp_service, p.gcp_sku_id,
                   m.gcp_sku_name, m.unit_multiplier, m.component,
                   ROUND(p.aws_amortized_cost,2) AS aws,
                   ROUND(p.gcp_projected_cost,2) AS gcp,
                   ROUND(p.gcp_projected_cost / NULLIF(p.aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection p
            JOIN   aws_li_to_gcp_li m USING (aws_li_key)
            WHERE  p.strategy NOT IN ('ignore','passthrough')
              AND  p.aws_amortized_cost > 1
              AND  ( p.gcp_projected_cost > p.aws_amortized_cost * 10
                  OR p.gcp_projected_cost < p.aws_amortized_cost * 0.05 );
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

        # F: Only flag over-projection (GCP > 2x AWS). GCP being cheaper than AWS is correct.
        # The old <0.5x lower bound matched most Compute Engine rows legitimately.
        execute_query_to_md(conn, f, "F. Unit-multiplier over-projection check",
            "GCP OD cost >2x AWS for a mapped row — likely wrong unit_multiplier or SKU.",
            """
            SELECT p.aws_li_key, p.product, p.gcp_service, p.gcp_sku_id,
                   m.gcp_sku_name, m.unit_multiplier, m.component,
                   ROUND(p.aws_amortized_cost,2) AS aws,
                   ROUND(p.gcp_projected_cost,2) AS gcp_od,
                   ROUND(p.gcp_projected_cost / NULLIF(p.aws_amortized_cost,0), 2) AS ratio
            FROM   gcp_projection p
            JOIN   aws_li_to_gcp_li m USING (aws_li_key)
            WHERE  p.strategy = 'map'
              AND  p.pricing_model = 'OnDemand'
              AND  p.line_item_type IN ('Usage')
              AND  p.aws_amortized_cost > 20
              AND  p.gcp_projected_cost > p.aws_amortized_cost * 2;
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
