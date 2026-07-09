#!/usr/bin/env python3
"""
detect_outliers.py — Phase 5 pre-LLM detection pass.

Runs all outlier queries, splits results into:
  structural_outliers.md   (D, E, G, B, C, H, I — deterministic root causes)
  pricing_outliers.md      (A1, A2, F — ratio anomalies)
  outliers_data.json       (machine-readable raw results for auto_triage.py)

Early gate: if total flagged rows > 20, writes outlier_pattern_summary.md
with a product/service breakdown and continues. Never exits with code 1 —
report generation must always complete. The pattern summary surfaces
systematic mapper issues for the next development cycle.
"""
import duckdb
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection_view import create_projection_view

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
STRUCTURAL_FILE = os.path.join(JOB_DIR, "structural_outliers.md")
PRICING_FILE    = os.path.join(JOB_DIR, "pricing_outliers.md")
DATA_FILE       = os.path.join(JOB_DIR, "outliers_data.json")

# Keep backward-compat file so any external tooling that reads outliers.md still works.
LEGACY_FILE = os.path.join(JOB_DIR, "outliers.md")

EARLY_GATE_THRESHOLD = 20


def run_query(conn, query):
    """Return (cols, rows) or (None, None) on error."""
    try:
        rows = conn.execute(query).fetchall()
        cols = [d[0] for d in conn.description]
        return cols, rows
    except Exception as e:
        print(f"  Query error: {e}", file=sys.stderr)
        return None, None


def write_section(f, header, description, cols, rows):
    f.write(f"### {header}\n")
    f.write(f"{description}\n\n")
    if rows is None:
        f.write("**Status: ERROR (query failed)**\n\n")
        return
    if not rows:
        f.write("**Status: PASS (0 rows found)**\n\n")
        return
    f.write(f"**Status: FAIL ({len(rows)} rows found)**\n\n")
    f.write("| " + " | ".join(cols) + " |\n")
    f.write("|" + "|".join(["---"] * len(cols)) + "|\n")
    for r in rows:
        f.write("| " + " | ".join([str(x) if x is not None else "" for x in r]) + " |\n")
    f.write("\n")


def rows_to_dicts(cols, rows):
    if cols is None or rows is None:
        return []
    return [dict(zip(cols, r)) for r in rows]


def main():
    if not os.path.exists(DB_PATH):
        print("Database not found.")
        sys.exit(0)

    conn = duckdb.connect(DB_PATH)
    create_projection_view(conn)

    # ------------------------------------------------------------------ #
    # Run all queries                                                       #
    # ------------------------------------------------------------------ #

    queries = {}

    # A1: Over-projection (pricing anomaly)
    queries["A1"] = run_query(conn, """
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
          )
    """)

    # A2: Extreme ratio outliers (pricing anomaly)
    queries["A2"] = run_query(conn, """
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
              OR p.gcp_projected_cost < p.aws_amortized_cost * 0.05 )
    """)

    # B: Phantom GCP cost (structural)
    queries["B"] = run_query(conn, """
        SELECT aws_li_key, product, gcp_service, gcp_sku_id,
               total_usage, ROUND(gcp_projected_cost,2) AS gcp
        FROM   gcp_projection
        WHERE  aws_amortized_cost <= 1
          AND  gcp_projected_cost > 10
    """)

    # C: Zero rate on billable row (structural)
    queries["C"] = run_query(conn, """
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_id, m.gcp_sku_name,
               ROUND(c.aws_amortized_cost,2) AS aws_cost
        FROM   aws_li_to_gcp_li m
        JOIN   aws_li_catalog c USING (aws_li_key)
        JOIN   gcp_sku_rates  r ON r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'OnDemand'
        WHERE  m.strategy IN ('map','break_down')
          AND  r.rate_usd = 0
          AND  c.aws_amortized_cost > 50
    """)

    # D: Cross-service mismatch (structural)
    queries["D"] = run_query(conn, """
        SELECT m.aws_li_key, m.gcp_service AS mapping_says, r.gcp_service AS sku_actually,
               m.gcp_sku_id
        FROM   gcp_projection p
        JOIN   aws_li_to_gcp_li m USING (aws_li_key)
        JOIN   gcp_sku_rates    r ON r.gcp_sku_id = m.gcp_sku_id
        WHERE  m.strategy IN ('map','break_down')
          AND  m.gcp_service IS NOT NULL AND r.gcp_service IS NOT NULL
          AND  m.gcp_service != r.gcp_service
          -- Only flag if it caused a real impact: projected cost is NULL despite being mapped.
          -- Name-only mismatches (e.g. "Cloud KMS" vs "Cloud Key Management Service (KMS)")
          -- are cosmetic — the rate lookup is keyed by SKU ID, not service name.
          -- apply_rates.py normalizes gcp_service for new runs so these disappear going forward.
          AND  p.gcp_projected_cost IS NULL
    """)

    # E: Missing CUD alias (structural)
    queries["E"] = run_query(conn, """
        SELECT m.aws_li_key, m.gcp_service, m.gcp_sku_id
        FROM   aws_li_to_gcp_li m
        WHERE  m.strategy IN ('map','break_down')
          AND  m.gcp_service IN ('Compute Engine','Cloud SQL',
                                 'Cloud Memorystore','Cloud Memorystore for Redis',
                                 'Cloud Memorystore for Memcached')
          AND  m.component IN ('core','ram')
          AND  m.projection_note NOT LIKE '%no-rate-fallback%'
          AND  m.gcp_sku_name NOT ILIKE '%preemptible%'
          AND  m.gcp_sku_name NOT ILIKE '%spot%'
          AND  NOT EXISTS (SELECT 1 FROM gcp_sku_rates r
                           WHERE r.gcp_sku_id = m.gcp_sku_id AND r.pricing_type = 'Commit1Yr')
    """)

    # F: Unit-multiplier over-projection (pricing anomaly)
    queries["F"] = run_query(conn, """
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
          AND  p.gcp_projected_cost > p.aws_amortized_cost * 2
    """)

    # G: break_down multiplier mismatch (structural)
    queries["G"] = run_query(conn, """
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
        ORDER BY delta DESC
    """)

    # H: RI/CUD parity check (structural)
    queries["H"] = run_query(conn, """
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
          AND  c.aws_amortized_cost > 10
    """)

    # I: NULL projection (structural)
    queries["I"] = run_query(conn, """
        SELECT m.aws_li_key, c.product, c.gcp_region, m.gcp_service, m.gcp_sku_id,
               ROUND(c.aws_amortized_cost, 2) AS aws_cost
        FROM   gcp_projection p
        JOIN   aws_li_to_gcp_li m USING (aws_li_key)
        JOIN   aws_li_catalog   c USING (aws_li_key)
        WHERE  m.strategy IN ('map','break_down')
          AND  p.gcp_projected_cost IS NULL
          AND  c.aws_amortized_cost > 1
          AND  m.projection_note NOT LIKE '%no-rate-fallback%'
        ORDER BY c.aws_amortized_cost DESC
    """)

    conn.close()

    # ------------------------------------------------------------------ #
    # Count rows and early gate                                            #
    # ------------------------------------------------------------------ #

    STRUCTURAL_QUERIES = ["B", "C", "D", "E", "G", "H", "I"]
    PRICING_QUERIES    = ["A1", "A2", "F"]

    def row_count(qid):
        _, rows = queries[qid]
        return len(rows) if rows else 0

    total_structural = sum(row_count(q) for q in STRUCTURAL_QUERIES)
    total_pricing    = sum(row_count(q) for q in PRICING_QUERIES)
    total            = total_structural + total_pricing

    # Count by service/product for pattern summary (helps diagnose systematic bugs)
    if total > EARLY_GATE_THRESHOLD:
        # Build pattern summary before failing
        product_counts = defaultdict(int)
        service_counts = defaultdict(int)
        for qid in STRUCTURAL_QUERIES + PRICING_QUERIES:
            cols, rows = queries[qid]
            if not rows:
                continue
            for r in rows:
                row_dict = dict(zip(cols, r))
                prod = row_dict.get("product", "")
                svc  = row_dict.get("gcp_service", "")
                if prod:
                    product_counts[prod] += 1
                if svc:
                    service_counts[svc] += 1

        print(f"OUTLIER WARNING: {total} outlier rows exceed threshold of {EARLY_GATE_THRESHOLD}.",
              file=sys.stderr)
        print("Systematic mapper issue likely — check outlier_pattern_summary.md.",
              file=sys.stderr)
        print("\nTop AWS products with outliers:", file=sys.stderr)
        for prod, cnt in sorted(product_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"  {cnt:3d}x  {prod}", file=sys.stderr)
        print("\nTop GCP services with outliers:", file=sys.stderr)
        for svc, cnt in sorted(service_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"  {cnt:3d}x  {svc}", file=sys.stderr)
        print(f"\nBreakdown: structural={total_structural} pricing={total_pricing}", file=sys.stderr)

        # Write a summary file for the user to inspect, then continue.
        # Do NOT exit(1) — report generation must always proceed.
        with open(os.path.join(JOB_DIR, "outlier_pattern_summary.md"), "w") as f:
            f.write(f"# Outlier Pattern Summary\n\n")
            f.write(f"**WARNING**: {total} outlier rows (threshold: {EARLY_GATE_THRESHOLD})\n\n")
            f.write("Systematic mapper issue detected. Review and fix the mapper between runs.\n\n")
            f.write("## Top AWS Products\n\n")
            for prod, cnt in sorted(product_counts.items(), key=lambda x: -x[1])[:10]:
                f.write(f"- {cnt}x `{prod}`\n")
            f.write("\n## Top GCP Services\n\n")
            for svc, cnt in sorted(service_counts.items(), key=lambda x: -x[1])[:10]:
                f.write(f"- {cnt}x `{svc}`\n")
            f.write(f"\n## Row Counts by Query\n\n")
            for qid in STRUCTURAL_QUERIES + PRICING_QUERIES:
                f.write(f"- {qid}: {row_count(qid)}\n")

    # ------------------------------------------------------------------ #
    # Write structural_outliers.md                                         #
    # ------------------------------------------------------------------ #

    with open(STRUCTURAL_FILE, "w") as f:
        f.write("# Structural Outliers\n\n")
        f.write("Deterministic root causes: wrong service label, missing CUD alias, "
                "multiplier mismatch, phantom cost, zero rate, NULL projection.\n")
        f.write(f"Total: {total_structural} rows\n\n")

        write_section(f, "B. Phantom GCP cost",
            "AWS ~$0 but GCP cost is large — always a unit_multiplier bug.",
            *queries["B"])
        write_section(f, "C. Zero rate on billable row",
            "SKU resolved but rate_usd=0 and the AWS row carries non-trivial cost.",
            *queries["C"])
        write_section(f, "D. Cross-service mismatch",
            "Mapping declares service X but the resolved SKU belongs to service Y in the catalog.",
            *queries["D"])
        write_section(f, "E. Missing CUD alias",
            "Compute Engine / Cloud SQL / Memorystore rows missing Commit1Yr pricing row.",
            *queries["E"])
        write_section(f, "G. break_down multiplier mismatch",
            "Inferred unit_multiplier doesn't match instance spec (vcpus or ram_gb).",
            *queries["G"])
        write_section(f, "H. RI/CUD parity (missing CUD rate row)",
            "Committed rows where 1yr CUD equals OD rate — CUD rate row absent.",
            *queries["H"])
        write_section(f, "I. NULL projection",
            "Mapped rows where gcp_projected_cost IS NULL (rate-fill missed this SKU or region).",
            *queries["I"])

    # ------------------------------------------------------------------ #
    # Write pricing_outliers.md                                            #
    # ------------------------------------------------------------------ #

    with open(PRICING_FILE, "w") as f:
        f.write("# Pricing Outliers\n\n")
        f.write("Ratio anomalies visible only after rates are applied. "
                "Root causes: wrong SKU tier, wrong unit_multiplier, wrong rate lookup.\n")
        f.write(f"Total: {total_pricing} rows\n\n")

        write_section(f, "A1. Over-projection (GCP costs more than AWS)",
            "GCP projected cost materially higher than AWS — likely wrong SKU or unit_multiplier.",
            *queries["A1"])
        write_section(f, "A2. Extreme ratio outliers (>10x over or <5% of AWS)",
            "Ratio >10x → wrong SKU. Ratio <0.05x → unit_multiplier bug.",
            *queries["A2"])
        write_section(f, "F. Unit-multiplier over-projection",
            "GCP OD cost >2x AWS for a mapped row — likely wrong unit_multiplier or SKU tier.",
            *queries["F"])

    # ------------------------------------------------------------------ #
    # Write legacy outliers.md (backward compat)                           #
    # ------------------------------------------------------------------ #

    with open(LEGACY_FILE, "w") as f:
        f.write("# Outlier Triage Report\n\n")
        f.write(f"Structural outliers: {total_structural}  |  Pricing outliers: {total_pricing}\n\n")
        f.write("See `structural_outliers.md` and `pricing_outliers.md` for details.\n")
        f.write("See `triage_suggestions.md` (written by auto_triage.py) for LLM input.\n")

    # ------------------------------------------------------------------ #
    # Write outliers_data.json (machine-readable for auto_triage.py)       #
    # ------------------------------------------------------------------ #

    data = {
        "total": total,
        "total_structural": total_structural,
        "total_pricing": total_pricing,
    }
    for qid in STRUCTURAL_QUERIES + PRICING_QUERIES:
        cols, rows = queries[qid]
        data[qid] = rows_to_dicts(cols, rows)

    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

    print(f"detect_outliers: structural={total_structural} pricing={total_pricing} total={total}")

if __name__ == "__main__":
    main()
